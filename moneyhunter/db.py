from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id            INTEGER PRIMARY KEY,
    full_name     TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    owner         TEXT NOT NULL,
    html_url      TEXT NOT NULL,
    description   TEXT,
    language      TEXT,
    topics        TEXT,
    stars         INTEGER NOT NULL DEFAULT 0,
    forks         INTEGER NOT NULL DEFAULT 0,
    open_issues   INTEGER NOT NULL DEFAULT 0,
    license       TEXT,
    archived      INTEGER NOT NULL DEFAULT 0,
    is_awesome    INTEGER NOT NULL DEFAULT 0,
    is_inactive   INTEGER NOT NULL DEFAULT 0,
    star_suspect  INTEGER NOT NULL DEFAULT 0,
    money_score   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT,
    updated_at    TEXT,
    pushed_at     TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    stars       INTEGER NOT NULL,
    forks       INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'search',
    UNIQUE (repo_id, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_repo_time ON snapshots(repo_id, captured_at);

CREATE TABLE IF NOT EXISTS daily_rank (
    board   TEXT NOT NULL,
    day     TEXT NOT NULL,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    rank    INTEGER NOT NULL,
    PRIMARY KEY (board, day, repo_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_rank_board_day ON daily_rank(board, day);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    queries_total INTEGER NOT NULL DEFAULT 0,
    queries_ok    INTEGER NOT NULL DEFAULT 0,
    repos_seen    INTEGER NOT NULL DEFAULT 0,
    repos_new     INTEGER NOT NULL DEFAULT 0,
    repos_refreshed INTEGER NOT NULL DEFAULT 0,
    message       TEXT
);
"""

# 旧版本数据库升级：新增列（ALTER TABLE 不会影响已有数据）
MIGRATIONS: list[tuple[str, str, str]] = [
    ("repos", "is_awesome", "is_awesome INTEGER NOT NULL DEFAULT 0"),
    ("repos", "is_inactive", "is_inactive INTEGER NOT NULL DEFAULT 0"),
    ("repos", "star_suspect", "star_suspect INTEGER NOT NULL DEFAULT 0"),
    ("snapshots", "source", "source TEXT NOT NULL DEFAULT 'search'"),
    ("runs", "repos_refreshed", "repos_refreshed INTEGER NOT NULL DEFAULT 0"),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime(TS_FMT)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FMT).replace(tzinfo=timezone.utc)


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        _ensure_column(conn, table, column, ddl)
    conn.commit()


def start_run(conn: sqlite3.Connection, queries_total: int) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, status, queries_total) VALUES (?, 'running', ?)",
        (utcnow(), queries_total),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    queries_ok: int,
    repos_seen: int,
    repos_new: int,
    repos_refreshed: int = 0,
    message: str = "",
) -> None:
    conn.execute(
        """UPDATE runs
           SET finished_at=?, status=?, queries_ok=?, repos_seen=?, repos_new=?,
               repos_refreshed=?, message=?
           WHERE id=?""",
        (
            utcnow(),
            status,
            queries_ok,
            repos_seen,
            repos_new,
            repos_refreshed,
            message[:2000],
            run_id,
        ),
    )
    conn.commit()


def upsert_repo(conn: sqlite3.Connection, repo: dict, money_score: int) -> tuple[int, bool]:
    """插入或更新仓库，返回 (repo_id, 是否新仓库)。"""
    now = utcnow()
    repo_id = int(repo["id"])
    row = conn.execute("SELECT id FROM repos WHERE id = ?", (repo_id,)).fetchone()
    is_new = row is None
    topics = json.dumps(repo.get("topics") or [], ensure_ascii=False)
    values = (
        repo["full_name"],
        repo.get("name") or repo["full_name"].split("/")[-1],
        repo.get("owner") or repo["full_name"].split("/")[0],
        repo.get("html_url") or f"https://github.com/{repo['full_name']}",
        (repo.get("description") or "")[:1000],
        repo.get("language") or "",
        topics,
        int(repo.get("stars") or 0),
        int(repo.get("forks") or 0),
        int(repo.get("open_issues") or 0),
        repo.get("license") or "",
        int(bool(repo.get("archived"))),
        int(money_score),
        repo.get("created_at") or "",
        repo.get("updated_at") or "",
        repo.get("pushed_at") or "",
    )
    try:
        if is_new:
            conn.execute(
                """INSERT INTO repos (
                       full_name, name, owner, html_url, description, language, topics,
                       stars, forks, open_issues, license, archived, money_score,
                       created_at, updated_at, pushed_at, first_seen_at, last_seen_at, id
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values + (now, now, repo_id),
            )
        else:
            conn.execute(
                """UPDATE repos SET
                       full_name=?, name=?, owner=?, html_url=?, description=?, language=?,
                       topics=?, stars=?, forks=?, open_issues=?, license=?, archived=?,
                       money_score=?, created_at=?, updated_at=?, pushed_at=?, last_seen_at=?
                   WHERE id=?""",
                values + (now, repo_id),
            )
    except sqlite3.IntegrityError:
        # 仓库改名后 full_name 撞车等极端情况：按 full_name 兜底更新
        conn.execute(
            """UPDATE repos SET
                   stars=?, forks=?, open_issues=?, description=?, language=?, topics=?,
                   archived=?, money_score=?, updated_at=?, pushed_at=?, last_seen_at=?
               WHERE full_name=?""",
            (
                int(repo.get("stars") or 0),
                int(repo.get("forks") or 0),
                int(repo.get("open_issues") or 0),
                (repo.get("description") or "")[:1000],
                repo.get("language") or "",
                topics,
                int(bool(repo.get("archived"))),
                int(money_score),
                repo.get("updated_at") or "",
                repo.get("pushed_at") or "",
                now,
                repo["full_name"],
            ),
        )
        row = conn.execute(
            "SELECT id FROM repos WHERE full_name = ?", (repo["full_name"],)
        ).fetchone()
        is_new = False
        repo_id = int(row["id"]) if row else repo_id
    return repo_id, is_new


def insert_snapshot(
    conn: sqlite3.Connection,
    repo_id: int,
    stars: int,
    forks: int,
    captured_at: str | None = None,
    source: str = "search",
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO snapshots (repo_id, captured_at, stars, forks, source)
           VALUES (?,?,?,?,?)""",
        (repo_id, captured_at or utcnow(), int(stars), int(forks), source),
    )


def update_repo_stats(conn: sqlite3.Connection, repo_id: int, stars: int, forks: int) -> None:
    """批量刷新时更新仓库的 Star / Fork（不改动其它字段）。"""
    conn.execute(
        "UPDATE repos SET stars=?, forks=?, last_seen_at=? WHERE id=?",
        (int(stars), int(forks), utcnow(), repo_id),
    )


# ---------------- 每日榜单排名快照（用于新晋 / 掉榜判定） ----------------

def save_daily_rank(
    conn: sqlite3.Connection,
    board: str,
    day: str,
    rows: list[tuple[int, int]],
) -> None:
    """保存某榜单某天的 Top N 排名，覆盖写入（同一天重复采集会刷新）。"""
    conn.execute(
        "DELETE FROM daily_rank WHERE board=? AND day=?", (board, day)
    )
    conn.executemany(
        "INSERT OR REPLACE INTO daily_rank (board, day, repo_id, rank) VALUES (?,?,?,?)",
        [(board, day, repo_id, rank) for repo_id, rank in rows],
    )
    conn.commit()


def get_rank_map(conn: sqlite3.Connection, board: str, day: str) -> dict[int, int]:
    """返回 {repo_id: rank}。"""
    return {
        row["repo_id"]: row["rank"]
        for row in conn.execute(
            "SELECT repo_id, rank FROM daily_rank WHERE board=? AND day=? ORDER BY rank",
            (board, day),
        ).fetchall()
    }


def rank_days(conn: sqlite3.Connection, board: str, limit: int = 5) -> list[str]:
    """返回该榜单有记录的日期（降序），最近 limit 个。"""
    return [
        row["day"]
        for row in conn.execute(
            """SELECT DISTINCT day FROM daily_rank WHERE board=?
               ORDER BY day DESC LIMIT ?""",
            (board, limit),
        ).fetchall()
    ]
