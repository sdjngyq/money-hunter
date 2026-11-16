"""GitHub REST API 客户端（只用到搜索接口，附带限流与重试处理）。"""

from __future__ import annotations

import time
from typing import Any

import requests

from . import __version__

API_ROOT = "https://api.github.com"
SEARCH_URL = f"{API_ROOT}/search/repositories"
GRAPHQL_URL = f"{API_ROOT}/graphql"

# GraphQL 一次最多查多少个仓库（用别名拼接，避免依赖 GraphQL node id）
GRAPHQL_BATCH = 100


class GitHubError(RuntimeError):
    pass


class RateLimitError(GitHubError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: float = 25.0) -> None:
        self.timeout = timeout
        self.token = (token or "").strip() or None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"GitHubMoneyHunter/{__version__}",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.search_remaining: int | None = None
        self.search_reset: int | None = None

    # ---------- 内部工具 ----------

    def _track_rate_limit(self, resp: requests.Response) -> None:
        remaining = resp.headers.get("x-ratelimit-remaining")
        reset = resp.headers.get("x-ratelimit-reset")
        if remaining is not None:
            try:
                self.search_remaining = int(remaining)
            except ValueError:
                pass
        if reset is not None:
            try:
                self.search_reset = int(reset)
            except ValueError:
                pass

    def _wait_seconds(self, resp: requests.Response) -> float:
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                return max(1.0, float(retry_after) + 1.0)
            except ValueError:
                pass
        if self.search_reset:
            wait = self.search_reset - int(time.time()) + 2
            return float(min(max(wait, 5), 180))
        return 15.0

    # ---------- 公共接口 ----------

    def search_repositories(
        self,
        query: str,
        *,
        sort: str = "stars",
        order: str = "desc",
        per_page: int = 50,
        page: int = 1,
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        params = {
            "q": query,
            "sort": sort,
            "order": order,
            "per_page": max(1, min(100, per_page)),
            "page": page,
        }
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.get(SEARCH_URL, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < max_attempts:
                    time.sleep(2.0 * attempt)
                    continue
                raise GitHubError(f"网络请求失败: {exc}") from exc

            self._track_rate_limit(resp)

            if resp.status_code == 200:
                return resp.json()

            body = ""
            try:
                body = (resp.json() or {}).get("message", "")
            except ValueError:
                body = resp.text[:200]

            if resp.status_code in (403, 429):
                wait = self._wait_seconds(resp)
                if attempt < max_attempts:
                    time.sleep(wait)
                    continue
                raise RateLimitError(f"触发 GitHub 限流（{resp.status_code}）：{body}")

            if 500 <= resp.status_code < 600 and attempt < max_attempts:
                time.sleep(2.0 * attempt)
                last_error = f"{resp.status_code} {body}"
                continue

            if resp.status_code == 422:
                raise GitHubError(f"搜索语法无效：{query}（{body}）")

            raise GitHubError(f"GitHub 返回 {resp.status_code}：{body}")

        raise GitHubError(f"请求多次失败：{last_error}")

    def rate_limit(self) -> dict[str, Any]:
        resp = self.session.get(f"{API_ROOT}/rate_limit", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("resources", {})

    # ---------- GraphQL：批量刷新存量仓库 ----------

    @property
    def can_graphql(self) -> bool:
        return bool(self.token)

    def graphql(self, query: str, variables: dict | None = None, max_attempts: int = 3) -> dict[str, Any]:
        if not self.token:
            raise GitHubError("GraphQL 接口需要 GitHub Token")
        payload = {"query": query, "variables": variables or {}}
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.post(GRAPHQL_URL, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < max_attempts:
                    time.sleep(2.0 * attempt)
                    continue
                raise GitHubError(f"GraphQL 网络请求失败: {exc}") from exc

            self._track_rate_limit(resp)

            if resp.status_code == 200:
                body = resp.json()
                if body.get("errors"):
                    message = "; ".join(
                        str(err.get("message", err)) for err in body["errors"]
                    )
                    raise GitHubError(f"GraphQL 返回错误：{message[:500]}")
                return body.get("data") or {}

            if resp.status_code == 401:
                raise GitHubError("GitHub Token 无效或已过期，请检查 GITHUB_TOKEN")

            if resp.status_code in (403, 429):
                wait = self._wait_seconds(resp)
                if attempt < max_attempts:
                    time.sleep(wait)
                    continue
                raise RateLimitError(f"GraphQL 触发限流（{resp.status_code}）")

            if 500 <= resp.status_code < 600 and attempt < max_attempts:
                time.sleep(2.0 * attempt)
                last_error = f"{resp.status_code}"
                continue

            raise GitHubError(f"GraphQL 返回 {resp.status_code}")

        raise GitHubError(f"GraphQL 请求多次失败：{last_error}")

    def fetch_repo_stats(self, full_names: list[str]) -> dict[str, tuple[int, int]]:
        """用 GraphQL 别名一次查询最多 100 个仓库的 Star / Fork。"""
        names = [n for n in full_names if "/" in n][:GRAPHQL_BATCH]
        if not names:
            return {}
        lines = []
        for index, full_name in enumerate(names):
            owner, name = full_name.split("/", 1)
            owner = owner.replace("\\", "").replace('"', "")
            name = name.replace("\\", "").replace('"', "")
            lines.append(
                f'  r{index}: repository(owner: "{owner}", name: "{name}") '
                "{ stargazerCount forkCount }"
            )
        query = "query {\n" + "\n".join(lines) + "\n}"
        data = self.graphql(query)
        result: dict[str, tuple[int, int]] = {}
        for index, full_name in enumerate(names):
            node = data.get(f"r{index}")
            if not node:
                continue
            result[full_name] = (
                int(node.get("stargazerCount") or 0),
                int(node.get("forkCount") or 0),
            )
        return result

    def fetch_repo_stats_rest(self, repo_id: int) -> tuple[int, int] | None:
        """无 Token 时的降级方案：用 REST 单个仓库接口（占用核心额度 60 次/小时）。"""
        url = f"{API_ROOT}/repositories/{repo_id}"
        for attempt in range(1, 3):
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt < 2:
                    time.sleep(2.0)
                    continue
                raise GitHubError(f"网络请求失败: {exc}") from exc

            self._track_rate_limit(resp)
            if resp.status_code == 200:
                item = resp.json()
                return int(item.get("stargazers_count") or 0), int(item.get("forks_count") or 0)
            if resp.status_code == 404:
                return None
            if resp.status_code in (403, 429):
                if resp.headers.get("x-ratelimit-remaining") == "0":
                    raise RateLimitError(
                        "REST 核心接口额度已用完（未配置 Token 时 60 次/小时），跳过本轮刷新"
                    )
                if attempt < 2:
                    time.sleep(self._wait_seconds(resp))
                    continue
                raise RateLimitError("REST 核心接口触发限流（未配置 Token 时约 60 次/小时）")
            if 500 <= resp.status_code < 600 and attempt < 2:
                time.sleep(2.0)
                continue
            raise GitHubError(f"刷新仓库失败：HTTP {resp.status_code}")
        return None
