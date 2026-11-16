"""采集器：按配置的搜索条件抓取仓库，并记录星标快照。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import analytics, console, db, quality, scoring
from .config import Config
from .github_api import GitHubClient, GitHubError, RateLimitError
from .logsetup import get_logger


@dataclass
class RunResult:
    run_id: int
    queries_total: int = 0
    queries_ok: int = 0
    repos_seen: int = 0
    repos_new: int = 0
    repos_refreshed: int = 0
    snapshots_added: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.queries_ok > 0


def normalize(item: dict) -> dict:
    """把 GitHub API 的仓库对象压缩成内部结构。"""
    license_info = item.get("license") or {}
    return {
        "id": int(item["id"]),
        "full_name": item.get("full_name") or "",
        "name": item.get("name") or "",
        "owner": (item.get("owner") or {}).get("login") or "",
        "html_url": item.get("html_url") or "",
        "description": item.get("description") or "",
        "language": item.get("language") or "",
        "topics": item.get("topics") or [],
        "stars": int(item.get("stargazers_count") or 0),
        "forks": int(item.get("forks_count") or 0),
        "open_issues": int(item.get("open_issues_count") or 0),
        "license": license_info.get("spdx_id") or "",
        "archived": bool(item.get("archived")),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
        "pushed_at": item.get("pushed_at") or "",
    }


class Collector:
    def __init__(self, cfg: Config, client: GitHubClient | None = None) -> None:
        self.cfg = cfg
        self.client = client or GitHubClient(cfg.token)

    # ---------- 主流程 ----------

    def build_queries(self, include_new: bool = True) -> list[str]:
        queries = list(self.cfg.queries)
        if include_new:
            since = (date.today() - timedelta(days=self.cfg.new_within_days)).isoformat()
            queries.extend(self.cfg.new_queries_formatted(since))
        return queries

    def run(
        self,
        *,
        max_queries: int | None = None,
        include_new: bool = True,
        verbose: bool = True,
    ) -> RunResult:
        cfg = self.cfg
        conn = db.connect(cfg.db_path)
        db.init_db(conn)

        queries = self.build_queries(include_new=include_new)
        if max_queries:
            queries = queries[: max(1, max_queries)]
        result = RunResult(run_id=db.start_run(conn, len(queries)), queries_total=len(queries))

        if verbose:
            console.info(
                f"开始采集：{len(queries)} 个搜索条件，数据库 {cfg.db_path}"
            )
            if not cfg.token:
                console.warn(
                    "未配置 GITHUB_TOKEN，搜索接口约 10 次/分钟，采集会比较慢。"
                    "建议设置环境变量 GITHUB_TOKEN。"
                )

        collected: dict[int, dict] = {}
        try:
            for index, query in enumerate(queries, start=1):
                if verbose:
                    console.info(f"[{index}/{len(queries)}] 搜索: {query}")
                try:
                    payload = self.client.search_repositories(
                        query, per_page=cfg.per_query
                    )
                except RateLimitError as exc:
                    result.errors.append(str(exc))
                    console.error(str(exc))
                    break
                except GitHubError as exc:
                    result.errors.append(f"{query} -> {exc}")
                    console.warn(f"跳过该条件：{exc}")
                    continue

                items = payload.get("items") or []
                result.queries_ok += 1
                kept = 0
                for item in items:
                    if int(item.get("stargazers_count") or 0) < cfg.min_stars:
                        continue
                    if item.get("fork"):
                        continue
                    repo = normalize(item)
                    if repo["id"] and repo["full_name"]:
                        collected[repo["id"]] = repo
                        kept += 1
                if verbose:
                    console.say(f"    返回 {len(items)} 个，保留 {kept} 个")

                if index < len(queries):
                    time.sleep(cfg.effective_delay)

            # 统一写库：同一轮采集所有快照时间一致，便于比较
            captured_at = db.utcnow()
            for repo in collected.values():
                score = scoring.money_score(repo)
                repo_id, is_new = db.upsert_repo(conn, repo, score)
                db.insert_snapshot(conn, repo_id, repo["stars"], repo["forks"], captured_at)
                result.snapshots_added += 1
                if is_new:
                    result.repos_new += 1
            result.repos_seen = len(collected)
            conn.commit()

            # 批量刷新库里所有仓库的星标，保证“增长”不依赖搜索排名
            if cfg.refresh_existing:
                if verbose:
                    console.info("批量刷新存量仓库星标（GraphQL 批量查询 / 无 Token 时降级 REST）…")
                result.repos_refreshed = self.refresh_existing(conn, captured_at, verbose=verbose)

            # 重算质量标记（awesome 列表 / 疑似刷星 / 久未更新）
            flags = quality.recompute(conn, cfg)
            if verbose:
                console.say(
                    f"质量标记：精选列表 {flags['awesome']} 个，"
                    f"久未更新 {flags['inactive']} 个，疑似刷星 {flags['suspect']} 个"
                )

            # 保存当日榜单排名（用于新晋 / 掉榜判定）
            day = captured_at[:10]
            try:
                top = max(1, int(cfg.top_limit))
                rank_specs = [
                    ("growth", analytics.growth_rows(conn, window_days=cfg.growth_window_days, limit=top)),
                    ("stars", analytics.stars_rows(conn, limit=top)),
                    ("money", analytics.money_rows(conn, limit=top, window_days=cfg.growth_window_days)),
                ]
                for board, rows in rank_specs:
                    db.save_daily_rank(conn, board, day, [(r["id"], i + 1) for i, r in enumerate(rows)])
            except Exception as exc:  # 排名快照不应影响采集主流程
                if verbose:
                    console.warn(f"保存每日排名快照失败：{type(exc).__name__}: {exc}")

            status = "ok" if result.ok else "failed"
            message = "; ".join(result.errors)[:2000]
            db.finish_run(
                conn,
                result.run_id,
                status=status,
                queries_ok=result.queries_ok,
                repos_seen=result.repos_seen,
                repos_new=result.repos_new,
                repos_refreshed=result.repos_refreshed,
                message=message,
            )
            get_logger().info(
                "collect run=%d status=%s queries_ok=%d/%d repos_seen=%d repos_new=%d "
                "refreshed=%d snapshots=%d",
                result.run_id,
                status,
                result.queries_ok,
                result.queries_total,
                result.repos_seen,
                result.repos_new,
                result.repos_refreshed,
                result.snapshots_added,
            )
        except Exception as exc:  # noqa: BLE001 - 保证运行记录不会卡在 running
            db.finish_run(
                conn,
                result.run_id,
                status="failed",
                queries_ok=result.queries_ok,
                repos_seen=result.repos_seen,
                repos_new=result.repos_new,
                repos_refreshed=result.repos_refreshed,
                message=f"{type(exc).__name__}: {exc}",
            )
            conn.commit()
            raise
        finally:
            conn.close()

        if verbose:
            if result.ok:
                console.ok(
                    f"采集完成：{result.queries_ok}/{result.queries_total} 个条件成功，"
                    f"收录 {result.repos_seen} 个仓库（新增 {result.repos_new}），"
                    f"刷新存量 {result.repos_refreshed} 个，"
                    f"写入 {result.snapshots_added} 条快照"
                )
            else:
                console.error("采集失败，请查看上方错误信息。")
        return result

    # ---------- 存量仓库刷新 ----------

    def refresh_existing(
        self,
        conn,
        captured_at: str,
        *,
        verbose: bool = True,
    ) -> int:
        """批量刷新数据库里已有仓库的 Star / Fork，并写入刷新快照。

        - 有 Token：走 GraphQL，一次查询 batch_size 个仓库，可覆盖全部存量；
        - 无 Token：GraphQL 不可用，降级为 REST 单个查询，只刷新 Top N（核心额度有限）。
        """
        cfg = self.cfg
        use_graphql = self.client.can_graphql
        limit = cfg.refresh_limit if use_graphql else cfg.refresh_limit_no_token
        if limit <= 0:
            return 0

        rows = conn.execute(
            "SELECT id, full_name, stars, forks FROM repos ORDER BY stars DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return 0

        refreshed = 0
        failed = 0

        if use_graphql:
            batch_size = min(100, max(1, cfg.refresh_batch_size))
            for start in range(0, len(rows), batch_size):
                chunk = rows[start : start + batch_size]
                try:
                    stats = self.client.fetch_repo_stats([row["full_name"] for row in chunk])
                except RateLimitError as exc:
                    if verbose:
                        console.warn(f"刷新中止（GraphQL 限流）：{exc}")
                    break
                except GitHubError as exc:
                    if verbose:
                        console.warn(f"本批刷新失败，跳过：{exc}")
                    failed += len(chunk)
                    continue
                for row in chunk:
                    item = stats.get(row["full_name"])
                    if not item:
                        failed += 1
                        continue
                    stars, forks = item
                    db.update_repo_stats(conn, row["id"], stars, forks)
                    db.insert_snapshot(
                        conn, row["id"], stars, forks, captured_at, source="refresh"
                    )
                    refreshed += 1
                if start + batch_size < len(rows):
                    time.sleep(0.6)
            conn.commit()
        else:
            for row in rows:
                try:
                    stats = self.client.fetch_repo_stats_rest(row["id"])
                except RateLimitError as exc:
                    if verbose:
                        console.warn(f"刷新中止（REST 核心额度用完）：{exc}")
                    break
                except GitHubError as exc:
                    failed += 1
                    if verbose:
                        console.warn(f"刷新 {row['full_name']} 失败：{exc}")
                    continue
                if not stats:
                    failed += 1
                    continue
                stars, forks = stats
                db.update_repo_stats(conn, row["id"], stars, forks)
                db.insert_snapshot(
                    conn, row["id"], stars, forks, captured_at, source="refresh"
                )
                refreshed += 1
            conn.commit()

        if verbose:
            mode = "GraphQL" if use_graphql else "REST 降级（无 Token，仅 Top {}）".format(limit)
            console.say(
                f"    刷新完成：成功 {refreshed} 个"
                + (f"，失败 {failed} 个" if failed else "")
                + f"（{mode}）"
            )
        return refreshed
