"""数据质量标记：awesome 列表、疑似刷星、久未更新。

这些标记在每轮采集结束后统一重算，榜单据此过滤或加提示，
避免“链接合集”“刷星项目”“停更项目”污染结果。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import analytics
from .config import Config
from .db import iso


def is_awesome_list(full_name: str, topics: list[str], description: str) -> bool:
    name = (full_name.split("/")[-1] or "").lower()
    desc = (description or "").lower()
    if name == "awesome":
        return True
    if name.startswith("awesome-") or name.startswith("awesome_"):
        return True
    if name.endswith("-awesome") or name.endswith("_awesome"):
        return True
    if any(str(t).lower() == "awesome" for t in topics):
        return True
    if "awesome list" in desc or "awesome-list" in desc or "curated list" in desc:
        return True
    return False


def recompute(conn: sqlite3.Connection, cfg: Config) -> dict[str, int]:
    """重算所有仓库的质量标记，返回各标记的命中数量。"""
    now = datetime.now(timezone.utc)
    inactive_cutoff = iso(now - timedelta(days=cfg.inactive_days))
    growth = analytics.growth_map(conn, 7)

    awesome = inactive = suspect = 0
    rows = conn.execute(
        "SELECT id, full_name, topics, description, pushed_at, stars, forks FROM repos"
    ).fetchall()

    for row in rows:
        try:
            topics = json.loads(row["topics"] or "[]")
        except (TypeError, ValueError):
            topics = []

        awesome_flag = 1 if is_awesome_list(
            row["full_name"], topics, row["description"] or ""
        ) else 0

        pushed_at = row["pushed_at"] or ""
        inactive_flag = 1 if (pushed_at and pushed_at < inactive_cutoff) else 0

        info = growth.get(row["id"])
        per_day = float(info["per_day"] or 0.0) if info else 0.0
        stars = int(row["stars"] or 0)
        forks = int(row["forks"] or 0)
        fork_ratio = (forks / stars) if stars else 1.0
        suspect_flag = 1 if (
            stars >= 1000
            and per_day >= cfg.suspect_daily_stars
            and fork_ratio <= cfg.suspect_fork_ratio
        ) else 0

        conn.execute(
            "UPDATE repos SET is_awesome=?, is_inactive=?, star_suspect=? WHERE id=?",
            (awesome_flag, inactive_flag, suspect_flag, row["id"]),
        )
        awesome += awesome_flag
        inactive += inactive_flag
        suspect += suspect_flag

    conn.commit()
    return {"awesome": awesome, "inactive": inactive, "suspect": suspect}
