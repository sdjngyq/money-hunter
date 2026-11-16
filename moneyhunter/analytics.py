"""分析层：Star 榜 / 增长榜 / 新项目榜 / 变现榜。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3

from .db import TS_FMT, iso, parse_ts
from . import db

MAX_SPARK_POINTS = 30


# ---------------------------------------------------------------- 工具函数


def _decode_topics(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return [str(t) for t in value] if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _serialize(row: sqlite3.Row) -> dict[str, Any]:
    flags: list[str] = []
    if row["archived"]:
        flags.append("archived")
    if row["is_awesome"]:
        flags.append("awesome")
    if row["is_inactive"]:
        flags.append("inactive")
    if row["star_suspect"]:
        flags.append("suspect")
    if not (row["description"] or "").strip():
        flags.append("no_desc")

    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "name": row["name"],
        "owner": row["owner"],
        "url": row["html_url"],
        "description": row["description"] or "",
        "language": row["language"] or "",
        "topics": _decode_topics(row["topics"]),
        "stars": row["stars"],
        "forks": row["forks"],
        "open_issues": row["open_issues"],
        "money_score": row["money_score"],
        "archived": bool(row["archived"]),
        "license": row["license"] or "",
        "flags": flags,
        "created_at": (row["created_at"] or "")[:10],
        "updated_at": row["updated_at"] or "",
        "pushed_at": row["pushed_at"] or "",
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
    }


def _select_repos(conn: sqlite3.Connection, q: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM repos"
    args: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        sql += " WHERE (full_name LIKE ? OR description LIKE ? OR topics LIKE ? OR language LIKE ?)"
        args = [like, like, like, like]
    return conn.execute(sql, args).fetchall()


def _attach_sparks(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return items
    ids = [item["id"] for item in items]
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT repo_id, stars FROM snapshots WHERE repo_id IN ({marks}) "
        "ORDER BY repo_id, captured_at",
        ids,
    )
    history: dict[int, list[int]] = {}
    for row in rows:
        history.setdefault(row["repo_id"], []).append(row["stars"])
    for item in items:
        values = history.get(item["id"], [])
        item["spark"] = _downsample(values)
    return items


def _downsample(values: list[int], points: int = MAX_SPARK_POINTS) -> list[int]:
    if len(values) <= points:
        return values
    step = (len(values) - 1) / (points - 1)
    return [values[round(i * step)] for i in range(points)]


def growth_map(
    conn: sqlite3.Connection, window_days: int = 7
) -> dict[int, dict[str, Any]]:
    """计算每个仓库在窗口期内的星标增长。

    基线 = 窗口起点之前最近的一条快照；若窗口内才开始追踪，
    则退回到最早的一条快照（此时统计的是“已观测到的增长”）。
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=window_days)).strftime(TS_FMT)

    baseline: dict[int, tuple[int, str]] = {}
    for row in conn.execute(
        """SELECT repo_id, stars, captured_at,
                  ROW_NUMBER() OVER (PARTITION BY repo_id ORDER BY captured_at DESC) AS rn
           FROM snapshots
           WHERE captured_at <= ?""",
        (cutoff,),
    ):
        if row["rn"] == 1:
            baseline[row["repo_id"]] = (row["stars"], row["captured_at"])

    for row in conn.execute(
        """SELECT repo_id, stars, captured_at,
                  ROW_NUMBER() OVER (PARTITION BY repo_id ORDER BY captured_at ASC) AS rn
           FROM snapshots"""
    ):
        if row["rn"] == 1 and row["repo_id"] not in baseline:
            baseline[row["repo_id"]] = (row["stars"], row["captured_at"])

    result: dict[int, dict[str, Any]] = {}
    for repo in conn.execute("SELECT id, stars FROM repos"):
        base = baseline.get(repo["id"])
        if not base:
            continue
        stars0, ts0 = base
        try:
            elapsed_days = (now - parse_ts(ts0)).total_seconds() / 86400
        except ValueError:
            continue
        days = max(elapsed_days, 0.04)
        growth = repo["stars"] - stars0
        result[repo["id"]] = {
            "growth": growth,
            "baseline_days": round(days, 1),
            # 观测不足 1 天时不做按天外推，避免出现夸张的“日均”
            "per_day": round(growth / days, 1) if days >= 1 else None,
            "baseline_stars": stars0,
            "since": ts0[:10],
        }
    return result


def acceleration_map(
    conn: sqlite3.Connection, window_days: int = 7
) -> dict[int, dict[str, Any]]:
    """计算“加速度”：近 N 天日均增速 减去 前 N 天日均增速。

    需要仓库分别拥有「最近一次快照 / 中点前最近快照 / 起点前最近快照」三条数据，
    缺失则不参与，避免短期波动误判。
    """
    now = datetime.now(timezone.utc)
    mid_ts = (now - timedelta(days=window_days)).strftime(TS_FMT)
    start_ts = (now - timedelta(days=window_days * 2)).strftime(TS_FMT)

    latest: dict[int, tuple[int, str]] = {}
    mid_point: dict[int, tuple[int, str]] = {}
    start_point: dict[int, tuple[int, str]] = {}
    for row in conn.execute(
        "SELECT repo_id, stars, captured_at FROM snapshots ORDER BY repo_id, captured_at"
    ):
        rid = row["repo_id"]
        latest[rid] = (row["stars"], row["captured_at"])
        captured = row["captured_at"]
        if captured <= mid_ts:
            mid_point[rid] = (row["stars"], captured)
        if captured <= start_ts:
            start_point[rid] = (row["stars"], captured)

    result: dict[int, dict[str, Any]] = {}
    for rid, (stars_now, ts_now) in latest.items():
        m = mid_point.get(rid)
        s = start_point.get(rid)
        if not m or not s:
            continue
        stars_mid, ts_mid = m
        stars_start, ts_start = s
        try:
            recent_days = (parse_ts(ts_now) - parse_ts(ts_mid)).total_seconds() / 86400
            prev_days = (parse_ts(ts_mid) - parse_ts(ts_start)).total_seconds() / 86400
        except ValueError:
            continue
        if recent_days < 0.2 or prev_days < 0.2:
            continue
        recent_per_day = (stars_now - stars_mid) / recent_days
        prev_per_day = (stars_mid - stars_start) / prev_days
        result[rid] = {
            "recent_per_day": round(recent_per_day, 1),
            "prev_per_day": round(prev_per_day, 1),
            "accel": round(recent_per_day - prev_per_day, 1),
        }
    return result


# ---------------------------------------------------------------- 榜单


def growth_rows(
    conn: sqlite3.Connection,
    *,
    window_days: int = 7,
    limit: int = 50,
    q: str | None = None,
    mode: str = "total",
    include_negative: bool = False,
    exclude_awesome: bool = True,
    exclude_archived: bool = True,
    exclude_inactive: bool = True,
    with_accel: bool = True,
) -> list[dict[str, Any]]:
    gm = growth_map(conn, window_days)
    accel_map = acceleration_map(conn, window_days) if with_accel else {}
    items: list[dict[str, Any]] = []
    for row in _select_repos(conn, q):
        if exclude_awesome and row["is_awesome"]:
            continue
        if exclude_archived and row["archived"]:
            continue
        if exclude_inactive and row["is_inactive"]:
            continue
        info = gm.get(row["id"])
        if not info:
            continue
        if info["growth"] <= 0 and not include_negative:
            continue
        item = _serialize(row)
        item.update(info)
        item["window_days"] = window_days
        item["growth_pct"] = (
            round(info["growth"] / info["baseline_stars"] * 100, 1)
            if info["baseline_stars"]
            else None
        )
        acc = accel_map.get(row["id"])
        if acc:
            item.update(acc)
        items.append(item)
    if mode == "pct":
        key = "growth_pct"
    else:
        key = "per_day" if mode == "perday" else "growth"
    items.sort(key=lambda x: (x.get(key) or 0, x["growth"], x["stars"]), reverse=True)
    return _attach_sparks(conn, items[: max(1, limit)])


def stars_rows(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    q: str | None = None,
    archived: bool | None = False,
    window_days: int = 7,
) -> list[dict[str, Any]]:
    gm = growth_map(conn, window_days)
    rows = _select_repos(conn, q)
    rows = [r for r in rows if archived is None or bool(r["archived"]) == archived]
    rows.sort(key=lambda r: (r["stars"], r["forks"]), reverse=True)
    items: list[dict[str, Any]] = []
    for row in rows[: max(1, limit)]:
        item = _serialize(row)
        info = gm.get(row["id"])
        if info:
            item.update(info)
        items.append(item)
    return _attach_sparks(conn, items)


def new_rows(
    conn: sqlite3.Connection,
    *,
    within_days: int = 60,
    limit: int = 50,
    q: str | None = None,
    exclude_awesome: bool = True,
    exclude_inactive: bool = True,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=within_days)).strftime(TS_FMT)
    items: list[dict[str, Any]] = []
    for row in _select_repos(conn, q):
        if exclude_awesome and row["is_awesome"]:
            continue
        if exclude_inactive and row["is_inactive"]:
            continue
        if row["archived"]:
            continue
        created = row["created_at"] or ""
        if not created or created < cutoff:
            continue
        try:
            age_days = (now - parse_ts(created)).total_seconds() / 86400
        except ValueError:
            continue
        age = max(age_days, 0.5)
        item = _serialize(row)
        item["age_days"] = round(age, 1)
        item["per_day"] = round(row["stars"] / age, 1)
        item["window_days"] = within_days
        items.append(item)
    items.sort(key=lambda x: (x["per_day"], x["stars"]), reverse=True)
    return _attach_sparks(conn, items[: max(1, limit)])


def money_rows(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    q: str | None = None,
    min_score: int = 15,
    window_days: int = 7,
    exclude_awesome: bool = True,
    exclude_inactive: bool = True,
) -> list[dict[str, Any]]:
    gm = growth_map(conn, window_days)
    rows = [
        r
        for r in _select_repos(conn, q)
        if r["money_score"] >= min_score
        and not r["archived"]
        and not (exclude_awesome and r["is_awesome"])
        and not (exclude_inactive and r["is_inactive"])
    ]
    rows.sort(key=lambda r: (r["money_score"], r["stars"]), reverse=True)
    items: list[dict[str, Any]] = []
    for row in rows[: max(1, limit)]:
        item = _serialize(row)
        info = gm.get(row["id"])
        if info:
            item.update(info)
        items.append(item)
    return _attach_sparks(conn, items)


def all_rows(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    q: str | None = None,
    sort: str = "stars",
    window_days: int = 7,
) -> list[dict[str, Any]]:
    gm = growth_map(conn, window_days)
    items: list[dict[str, Any]] = []
    for row in _select_repos(conn, q):
        item = _serialize(row)
        info = gm.get(row["id"])
        if info:
            item.update(info)
        items.append(item)

    sorters = {
        "stars": lambda x: (x["stars"], x["forks"]),
        "money": lambda x: (x["money_score"], x["stars"]),
        "growth": lambda x: (x.get("growth") or 0, x["stars"]),
        "updated": lambda x: x["updated_at"] or "",
        "created": lambda x: x["created_at"] or "",
    }
    items.sort(key=sorters.get(sort, sorters["stars"]), reverse=True)
    return _attach_sparks(conn, items[: max(1, limit)])


# ---------------------------------------------------------------- 汇总


def summary(conn: sqlite3.Connection, window_days: int = 7) -> dict[str, Any]:
    repo_stats = conn.execute(
        "SELECT COUNT(*) AS repos, COALESCE(SUM(stars), 0) AS stars FROM repos"
    ).fetchone()
    snapshot_count = conn.execute("SELECT COUNT(*) AS c FROM snapshots").fetchone()["c"]
    run_count = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
    last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    cutoff_24h = iso(datetime.now(timezone.utc) - timedelta(hours=24))
    new_repos_24h = conn.execute(
        "SELECT COUNT(*) AS c FROM repos WHERE first_seen_at >= ?", (cutoff_24h,)
    ).fetchone()["c"]

    gm = growth_map(conn, window_days)
    window_growth = sum(max(0, info["growth"]) for info in gm.values())

    quality_row = conn.execute(
        """SELECT COALESCE(SUM(is_awesome), 0) AS awesome,
                  COALESCE(SUM(is_inactive), 0) AS inactive,
                  COALESCE(SUM(star_suspect), 0) AS suspect
           FROM repos"""
    ).fetchone()

    return {
        "repos": repo_stats["repos"],
        "total_stars": repo_stats["stars"],
        "snapshots": snapshot_count,
        "runs": run_count,
        "new_repos_24h": new_repos_24h,
        "window_days": window_days,
        "window_growth": window_growth,
        "flagged_awesome": quality_row["awesome"],
        "flagged_inactive": quality_row["inactive"],
        "flagged_suspect": quality_row["suspect"],
        "last_run_at": (last_run["finished_at"] or last_run["started_at"]) if last_run else "",
        "last_run_status": last_run["status"] if last_run else "",
        "last_run_message": (last_run["message"] or "") if last_run else "",
        "has_data": repo_stats["repos"] > 0,
    }


def runs_history(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"] or "",
                "status": row["status"],
                "queries_total": row["queries_total"],
                "queries_ok": row["queries_ok"],
                "repos_seen": row["repos_seen"],
                "repos_new": row["repos_new"],
                "repos_refreshed": row["repos_refreshed"],
                "message": row["message"] or "",
            }
        )
    return out


def repo_history(conn: sqlite3.Connection, repo_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT captured_at, stars, forks FROM snapshots WHERE repo_id=? ORDER BY captured_at",
        (repo_id,),
    ).fetchall()
    return [{"captured_at": r["captured_at"], "stars": r["stars"], "forks": r["forks"]} for r in rows]


def repo_detail(conn: sqlite3.Connection, repo_id: int) -> dict[str, Any] | None:
    """单个项目详情：基础信息 + 全部历史 + 各窗口增长。"""
    row = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
    if not row:
        return None
    item = _serialize(row)
    history = repo_history(conn, repo_id)
    item["history"] = history
    item["windows"] = {
        str(window): growth_map(conn, window).get(repo_id)
        for window in (1, 7, 30, 90)
    }

    if len(history) >= 2:
        first, last = history[0], history[-1]
        try:
            days = max(
                (parse_ts(last["captured_at"]) - parse_ts(first["captured_at"])).total_seconds()
                / 86400,
                0.5,
            )
        except ValueError:
            days = 0.5
        growth = last["stars"] - first["stars"]
        item["total"] = {
            "growth": growth,
            "days": round(days, 1),
            "per_day": round(growth / days, 1),
            "since": first["captured_at"][:10],
        }
    else:
        item["total"] = None
    return item


def compare_series(
    conn: sqlite3.Connection,
    repo_ids: list[int],
    days: int | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """多项目对比数据：每个项目返回统一格式的星标序列。"""
    cutoff = None
    if days:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        ).strftime(TS_FMT)

    out: list[dict[str, Any]] = []
    for repo_id in repo_ids[:limit]:
        row = conn.execute(
            """SELECT id, full_name, html_url, stars, forks, money_score, language
               FROM repos WHERE id=?""",
            (repo_id,),
        ).fetchone()
        if not row:
            continue
        history = repo_history(conn, repo_id)
        if cutoff:
            history = [h for h in history if h["captured_at"] >= cutoff]
        if not history:
            continue
        first, last = history[0], history[-1]
        growth = last["stars"] - first["stars"]
        out.append(
            {
                "id": row["id"],
                "full_name": row["full_name"],
                "url": row["html_url"],
                "stars": row["stars"],
                "forks": row["forks"],
                "language": row["language"] or "",
                "money_score": row["money_score"],
                "series": [{"t": h["captured_at"], "v": h["stars"]} for h in history],
                "first": first["stars"],
                "growth": growth,
                "growth_pct": round(growth / first["stars"] * 100, 2) if first["stars"] else 0.0,
            }
        )
    return out


# ---------------------------------------------------------------- 聚合与变动


def language_stats(
    conn: sqlite3.Connection, *, window_days: int = 7, limit: int = 12
) -> list[dict[str, Any]]:
    """按编程语言聚合：仓库数、总 Star、窗口增长与涨幅。"""
    gm = growth_map(conn, window_days)
    per_lang: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """SELECT id, language, stars FROM repos
           WHERE COALESCE(language,'') <> '' AND archived=0"""
    ):
        lang = row["language"] or ""
        entry = per_lang.get(lang)
        if entry is None:
            entry = per_lang[lang] = {
                "language": lang,
                "repos": 0,
                "stars": 0,
                "growth": 0,
            }
        entry["repos"] += 1
        entry["stars"] += int(row["stars"] or 0)
        entry["growth"] += max(0, int((gm.get(row["id"]) or {}).get("growth") or 0))
    items = sorted(
        per_lang.values(), key=lambda x: (x["stars"], x["repos"], x["growth"]), reverse=True
    )
    for item in items:
        base = item["stars"] - item["growth"]
        item["growth_pct"] = round(item["growth"] / base * 100, 1) if base > 0 else 0.0
    return items[: max(1, limit)]


def topic_stats(
    conn: sqlite3.Connection,
    *,
    window_days: int = 7,
    limit: int = 15,
    exclude_awesome: bool = True,
) -> list[dict[str, Any]]:
    """按话题聚合：仓库数、总 Star、窗口增长与涨幅。"""
    gm = growth_map(conn, window_days)
    per_topic: dict[str, dict[str, Any]] = {}
    rows = conn.execute("SELECT id, stars, topics, is_awesome FROM repos WHERE archived=0").fetchall()
    for row in rows:
        if exclude_awesome and row["is_awesome"]:
            continue
        growth = max(0, int((gm.get(row["id"]) or {}).get("growth") or 0))
        stars = int(row["stars"] or 0)
        for t in _decode_topics(row["topics"]):
            key = t.lower().strip()
            if not key:
                continue
            entry = per_topic.get(key)
            if entry is None:
                entry = per_topic[key] = {"topic": key, "repos": 0, "stars": 0, "growth": 0}
            entry["repos"] += 1
            entry["stars"] += stars
            entry["growth"] += growth
    items = sorted(
        per_topic.values(),
        key=lambda x: (x["repos"], x["stars"], x["growth"]),
        reverse=True,
    )
    for item in items:
        base = item["stars"] - item["growth"]
        item["growth_pct"] = round(item["growth"] / base * 100, 1) if base > 0 else 0.0
    return items[: max(1, limit)]


def daily_changes(
    conn: sqlite3.Connection, *, board: str = "growth", limit: int = 30
) -> dict[str, Any]:
    """今日 vs 昨日榜单的变动：新晋、上升、下降、掉榜。"""
    days = db.rank_days(conn, board, limit=2)
    if not days:
        return {"board": board, "day": "", "prev_day": "", "items": []}
    day = days[0]
    prev_day = days[1] if len(days) > 1 else ""
    now_map = db.get_rank_map(conn, board, day)
    prev_map = db.get_rank_map(conn, board, prev_day) if prev_day else {}
    all_repos = {row["id"]: row for row in _select_repos(conn)}
    gm = growth_map(conn, 7)

    groups: dict[str, list] = {
        "new": [],
        "up": [],
        "down": [],
        "same": [],
        "out": [],
    }

    def _item(rid: int) -> dict[str, Any]:
        row = all_repos.get(rid)
        if row is None:
            return {}
        item = _serialize(row)
        item.update(gm.get(rid) or {})
        return item

    for rid, rank_now in now_map.items():
        item = _item(rid)
        if not item:
            continue
        item["rank_now"] = rank_now
        if prev_day and rid not in prev_map:
            item["change"] = "new"
            item["rank_prev"] = None
            item["delta"] = None
            groups["new"].append((rank_now, item))
        else:
            rank_prev = prev_map.get(rid) if prev_day else None
            item["rank_prev"] = rank_prev
            delta = None if rank_prev is None else rank_prev - rank_now
            item["delta"] = delta
            if rank_prev is None:
                item["change"] = "new"
                groups["new"].append((rank_now, item))
            elif delta > 0:
                item["change"] = "up"
                groups["up"].append((-delta, rank_now, item))
            elif delta < 0:
                item["change"] = "down"
                groups["down"].append((delta, rank_now, item))
            else:
                item["change"] = "same"
                groups["same"].append((rank_now, item))

    # 掉榜：昨天上榜，今天不在
    if prev_day:
        today_ids = set(now_map)
        for rid, rank_prev in prev_map.items():
            if rid not in today_ids:
                item = _item(rid)
                if not item:
                    continue
                item["change"] = "out"
                item["rank_now"] = None
                item["rank_prev"] = rank_prev
                item["delta"] = None
                groups["out"].append((rank_prev, item))

    flat: list[dict[str, Any]] = []
    for key in ("new", "up", "down", "same", "out"):
        groups[key].sort(key=lambda x: x[0])
        flat.extend(item for _, *_, item in groups[key])

    return {
        "board": board,
        "day": day,
        "prev_day": prev_day,
        "items": flat[: max(1, limit)],
    }
