from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from moneyhunter import analytics, db, quality, scoring
from moneyhunter.collector import Collector
from moneyhunter.config import Config


def make_repo(
    repo_id: int,
    name: str,
    stars: int,
    *,
    created_days: int = 300,
    description: str = "",
    topics: list[str] | None = None,
    forks: int = 10,
    pushed_days: int = 0,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": repo_id,
        "full_name": f"owner/{name}",
        "name": name,
        "owner": "owner",
        "html_url": f"https://github.com/owner/{name}",
        "description": description,
        "language": "Python",
        "topics": topics or [],
        "stars": stars,
        "forks": forks,
        "open_issues": 1,
        "license": "MIT",
        "archived": False,
        "created_at": db.iso(now - timedelta(days=created_days)),
        "updated_at": db.iso(now),
        "pushed_at": db.iso(now - timedelta(days=pushed_days)),
    }


def ts_days_ago(days: float) -> str:
    return db.iso(datetime.now(timezone.utc) - timedelta(days=days))


class ScoringTest(unittest.TestCase):
    def test_saas_keywords_score_high(self):
        score = scoring.money_score(
            {
                "name": "saas-starter",
                "description": "SaaS boilerplate with Stripe payments and subscription billing",
                "topics": ["saas", "stripe", "template"],
            }
        )
        self.assertGreaterEqual(score, 20)

    def test_hardware_keywords(self):
        for topic, name in (("esp32", "esp32-sensor-kit"), ("stm32", "stm32-hal-lib")):
            score = scoring.money_score(
                {"name": name, "description": "firmware for microcontroller", "topics": [topic]}
            )
            self.assertGreaterEqual(score, 5)

    def test_deployment_and_diy_keywords(self):
        deploy = scoring.money_score(
            {
                "name": "local-llm-inference",
                "description": "Run GGUF models with quantization on your own GPU",
                "topics": ["inference", "gguf", "self-hosted"],
            }
        )
        self.assertGreaterEqual(deploy, 15)
        diy = scoring.money_score(
            {
                "name": "open-hardware-pcb",
                "description": "DIY KiCad boards for home lab",
                "topics": ["open-hardware", "kicad", "diy"],
            }
        )
        self.assertGreaterEqual(diy, 15)

    def test_short_keyword_needs_word_boundary(self):
        # "email" 里的 "ai" 不应被当成 AI 关键词
        score_email = scoring.score_text("send email with attachments")
        self.assertEqual(score_email, 0)

    def test_ai_word_matches(self):
        self.assertGreater(scoring.score_text("ai agent framework"), 0)

    def test_score_capped_at_100(self):
        text = " ".join(scoring.KEYWORDS.keys())
        self.assertLessEqual(scoring.score_text(text), 100)


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_upsert_and_update(self):
        repo = make_repo(1, "demo", 100)
        repo_id, is_new = db.upsert_repo(self.conn, repo, 30)
        self.assertTrue(is_new)
        repo["stars"] = 250
        repo_id2, is_new2 = db.upsert_repo(self.conn, repo, 30)
        self.assertEqual(repo_id, repo_id2)
        self.assertFalse(is_new2)
        row = self.conn.execute("SELECT stars, money_score FROM repos WHERE id=1").fetchone()
        self.assertEqual(row["stars"], 250)
        self.assertEqual(row["money_score"], 30)

    def test_snapshot_unique(self):
        repo = make_repo(1, "demo", 100)
        db.upsert_repo(self.conn, repo, 0)
        captured = ts_days_ago(1)
        db.insert_snapshot(self.conn, 1, 100, 5, captured)
        db.insert_snapshot(self.conn, 1, 120, 5, captured)
        count = self.conn.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"]
        self.assertEqual(count, 1)
        stars = self.conn.execute("SELECT stars FROM snapshots").fetchone()["stars"]
        self.assertEqual(stars, 120)

    def test_migrations_on_legacy_db(self):
        path = Path(self.tmp.name) / "legacy.db"
        raw = sqlite3.connect(path)
        raw.executescript(
            """
            CREATE TABLE repos (
                id INTEGER PRIMARY KEY, full_name TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                owner TEXT NOT NULL, html_url TEXT NOT NULL, description TEXT, language TEXT,
                topics TEXT, stars INTEGER NOT NULL DEFAULT 0, forks INTEGER NOT NULL DEFAULT 0,
                open_issues INTEGER NOT NULL DEFAULT 0, license TEXT,
                archived INTEGER NOT NULL DEFAULT 0, money_score INTEGER NOT NULL DEFAULT 0,
                created_at TEXT, updated_at TEXT, pushed_at TEXT,
                first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
            );
            CREATE TABLE snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, repo_id INTEGER NOT NULL,
                captured_at TEXT NOT NULL, stars INTEGER NOT NULL,
                forks INTEGER NOT NULL DEFAULT 0, UNIQUE(repo_id, captured_at)
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL DEFAULT 'running',
                queries_total INTEGER NOT NULL DEFAULT 0, queries_ok INTEGER NOT NULL DEFAULT 0,
                repos_seen INTEGER NOT NULL DEFAULT 0, repos_new INTEGER NOT NULL DEFAULT 0,
                message TEXT
            );
            INSERT INTO repos (id, full_name, name, owner, html_url, first_seen_at, last_seen_at)
            VALUES (1, 'a/b', 'b', 'a', 'https://github.com/a/b', 'x', 'x');
            """
        )
        raw.commit()
        raw.close()

        conn = db.connect(path)
        db.init_db(conn)
        repo_cols = {row["name"] for row in conn.execute("PRAGMA table_info(repos)")}
        snap_cols = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
        run_cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        self.assertTrue({"is_awesome", "is_inactive", "star_suspect"} <= repo_cols)
        self.assertIn("source", snap_cols)
        self.assertIn("repos_refreshed", run_cols)
        self.assertEqual(conn.execute("SELECT COUNT(*) c FROM repos").fetchone()["c"], 1)
        conn.close()


class GrowthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self):
        repo = make_repo(1, "riser", 180)
        db.upsert_repo(self.conn, repo, 10)
        db.insert_snapshot(self.conn, 1, 100, 10, ts_days_ago(10))
        db.insert_snapshot(self.conn, 1, 180, 10, ts_days_ago(0))
        self.conn.commit()

    def test_growth_uses_baseline_before_window(self):
        self._seed()
        rows = analytics.growth_rows(self.conn, window_days=7, limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["growth"], 80)
        self.assertGreaterEqual(row["baseline_days"], 9.9)
        self.assertGreaterEqual(row["per_day"], 7.5)
        self.assertEqual(row["spark"], [100, 180])

    def test_growth_window_2_days(self):
        self._seed()
        rows = analytics.growth_rows(self.conn, window_days=2, limit=10)
        self.assertEqual(rows[0]["growth"], 80)  # 仍以 10 天前的快照为基线

    def test_no_growth_repo_filtered(self):
        repo = make_repo(2, "flat", 50)
        db.upsert_repo(self.conn, repo, 0)
        db.insert_snapshot(self.conn, 2, 50, 1, ts_days_ago(3))
        db.insert_snapshot(self.conn, 2, 50, 1, ts_days_ago(0))
        self.conn.commit()
        self.assertEqual(analytics.growth_rows(self.conn, window_days=7), [])

    def test_new_rows_velocity(self):
        repo = make_repo(3, "newbie", 2000, created_days=20)
        db.upsert_repo(self.conn, repo, 0)
        db.insert_snapshot(self.conn, 3, 2000, 1, ts_days_ago(0))
        rows = analytics.new_rows(self.conn, within_days=60, limit=10)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["per_day"], 100.0, delta=1.0)

    def test_new_rows_excludes_old(self):
        repo = make_repo(4, "old", 2000, created_days=300)
        db.upsert_repo(self.conn, repo, 0)
        self.assertEqual(analytics.new_rows(self.conn, within_days=60), [])

    def test_money_rows_order(self):
        high = make_repo(5, "saas-kit", 1000, description="SaaS boilerplate with Stripe billing")
        low = make_repo(6, "misc", 5000, description="random utility")
        db.upsert_repo(self.conn, high, scoring.money_score(high))
        db.upsert_repo(self.conn, low, scoring.money_score(low))
        rows = analytics.money_rows(self.conn, min_score=15, limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["id"], 5)

    def test_summary(self):
        self._seed()
        info = analytics.summary(self.conn, window_days=7)
        self.assertEqual(info["repos"], 1)
        self.assertEqual(info["snapshots"], 2)
        self.assertGreaterEqual(info["window_growth"], 80)
        self.assertTrue(info["has_data"])


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        db.init_db(self.conn)
        self.cfg = Config()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_awesome_detection(self):
        self.assertTrue(quality.is_awesome_list("sindresorhus/awesome", [], ""))
        self.assertTrue(quality.is_awesome_list("owner/awesome-python", [], ""))
        self.assertTrue(quality.is_awesome_list("owner/rust-awesome", [], ""))
        self.assertTrue(quality.is_awesome_list("owner/notes", ["awesome"], ""))
        self.assertTrue(quality.is_awesome_list("owner/lists", [], "A curated list of tools"))
        self.assertFalse(quality.is_awesome_list("owner/awesomewm", [], "window manager"))
        self.assertFalse(quality.is_awesome_list("owner/saas-kit", ["saas"], "Billing"))

    def test_recompute_flags(self):
        awesome = make_repo(1, "awesome-python", 5000, topics=["awesome"])
        inactive = make_repo(2, "old-crm", 3000, pushed_days=400)
        suspect = make_repo(3, "fake-saas", 5000, forks=5)
        normal = make_repo(4, "real-project", 800, forks=80)

        for repo in (awesome, inactive, suspect, normal):
            db.upsert_repo(self.conn, repo, 0)

        # 7 天前 1000 星 -> 现在 5000 星，日均约 570，fork 比例 0.1%
        db.insert_snapshot(self.conn, 3, 1000, 1, ts_days_ago(7))
        db.insert_snapshot(self.conn, 3, 5000, 5, ts_days_ago(0))
        self.conn.commit()

        flags = quality.recompute(self.conn, self.cfg)
        self.assertGreaterEqual(flags["awesome"], 1)
        self.assertGreaterEqual(flags["inactive"], 1)
        self.assertGreaterEqual(flags["suspect"], 1)

        rows = {r["id"]: r for r in self.conn.execute("SELECT * FROM repos")}
        self.assertEqual(rows[1]["is_awesome"], 1)
        self.assertEqual(rows[2]["is_inactive"], 1)
        self.assertEqual(rows[3]["star_suspect"], 1)
        self.assertEqual(rows[4]["is_awesome"], 0)
        self.assertEqual(rows[4]["star_suspect"], 0)

    def test_recompute_handles_single_snapshot(self):
        # 只有一次快照（观测不足 1 天）时 per_day 为 None，不应报错
        repo = make_repo(1, "fresh-repo", 5000, forks=5)
        db.upsert_repo(self.conn, repo, 0)
        db.insert_snapshot(self.conn, 1, 5000, 5, ts_days_ago(0))
        self.conn.commit()
        flags = quality.recompute(self.conn, self.cfg)
        self.assertEqual(flags["suspect"], 0)

    def test_flags_filter_boards(self):
        awesome = make_repo(1, "awesome-python", 5000, topics=["awesome"])
        inactive = make_repo(2, "old-crm", 3000, pushed_days=400)
        normal = make_repo(3, "real", 800)
        for repo in (awesome, inactive, normal):
            db.upsert_repo(self.conn, repo, 0)
        for repo_id in (1, 2, 3):
            db.insert_snapshot(self.conn, repo_id, 100, 1, ts_days_ago(10))
            db.insert_snapshot(self.conn, repo_id, 500, 5, ts_days_ago(0))
        self.conn.commit()
        quality.recompute(self.conn, self.cfg)

        growth_ids = [r["id"] for r in analytics.growth_rows(self.conn)]
        self.assertEqual(growth_ids, [3])
        all_growth = [r["id"] for r in analytics.growth_rows(self.conn, exclude_awesome=False)]
        self.assertIn(1, all_growth)
        self.assertIn(1, [r["id"] for r in analytics.growth_rows(self.conn, exclude_archived=False, exclude_awesome=False)])

        money_ids = [r["id"] for r in analytics.money_rows(self.conn, min_score=0)]
        self.assertNotIn(2, money_ids)

        new_ids = [r["id"] for r in analytics.new_rows(self.conn, within_days=60)]
        self.assertNotIn(1, new_ids)
        self.assertNotIn(2, new_ids)


class FakeGraphQLClient:
    def __init__(self, token: str | None = "fake-token"):
        self.token = token
        self.calls: list[list[str]] = []

    @property
    def can_graphql(self) -> bool:
        return bool(self.token)

    def fetch_repo_stats(self, names):
        self.calls.append(list(names))
        return {name: (777, 11) for name in names}

    def fetch_repo_stats_rest(self, repo_id):
        self.calls.append([f"rest:{repo_id}"])
        return (555, 9)

    def search_repositories(self, query, per_page=50):
        return {
            "items": [
                {
                    "id": 1001,
                    "full_name": "acme/agent-kit",
                    "name": "agent-kit",
                    "owner": {"login": "acme"},
                    "html_url": "https://github.com/acme/agent-kit",
                    "description": "AI agent starter kit",
                    "language": "Python",
                    "topics": ["ai", "agents", "automation"],
                    "stargazers_count": 5000,
                    "forks_count": 100,
                    "open_issues_count": 3,
                    "license": {"spdx_id": "MIT"},
                    "archived": False,
                    "fork": False,
                    "created_at": ts_days_ago(100),
                    "updated_at": ts_days_ago(0),
                    "pushed_at": ts_days_ago(0),
                }
            ]
        }


class RefreshExistingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, count: int = 3):
        for index in range(1, count + 1):
            db.upsert_repo(self.conn, make_repo(index, f"repo{index}", index * 100), 0)
        self.conn.commit()

    def test_graphql_refresh_chunks(self):
        self._seed(3)
        cfg = Config(db_path=self.db_path, refresh_batch_size=2)
        client = FakeGraphQLClient()
        collector = Collector(cfg, client=client)

        refreshed = collector.refresh_existing(self.conn, db.utcnow(), verbose=False)
        self.assertEqual(refreshed, 3)
        self.assertEqual([len(c) for c in client.calls], [2, 1])
        stars = [r["stars"] for r in self.conn.execute("SELECT stars FROM repos")]
        self.assertEqual(stars, [777, 777, 777])
        sources = {
            r["source"]
            for r in self.conn.execute("SELECT source FROM snapshots")
        }
        self.assertEqual(sources, {"refresh"})

    def test_rest_fallback_without_token(self):
        self._seed(2)
        cfg = Config(db_path=self.db_path, refresh_limit_no_token=10)
        client = FakeGraphQLClient(token=None)
        collector = Collector(cfg, client=client)

        refreshed = collector.refresh_existing(self.conn, db.utcnow(), verbose=False)
        self.assertEqual(refreshed, 2)
        self.assertTrue(all(str(c[0]).startswith("rest:") for c in client.calls))
        stars = [r["stars"] for r in self.conn.execute("SELECT stars FROM repos")]
        self.assertEqual(stars, [555, 555])

    def test_refresh_respects_limit(self):
        self._seed(5)
        cfg = Config(db_path=self.db_path, refresh_limit_no_token=2)
        client = FakeGraphQLClient(token=None)
        collector = Collector(cfg, client=client)
        refreshed = collector.refresh_existing(self.conn, db.utcnow(), verbose=False)
        self.assertEqual(refreshed, 2)


class ConfigDefaultsTest(unittest.TestCase):
    def test_hardware_queries_present(self):
        from moneyhunter.config import DEFAULT_NEW_QUERIES, DEFAULT_QUERIES

        joined = " ".join(DEFAULT_QUERIES)
        for topic in (
            "topic:esp32",
            "topic:stm32",
            "topic:llm-inference",
            "topic:open-hardware",
            "topic:diy",
            "topic:reverse-engineering",
        ):
            self.assertIn(topic, joined)
        new_joined = " ".join(DEFAULT_NEW_QUERIES)
        for topic in (
            "topic:esp32",
            "topic:stm32",
            "topic:inference",
            "topic:open-hardware",
            "topic:diy",
        ):
            self.assertIn(topic, new_joined)


class InsightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_growth_pct_and_acceleration(self):
        db.upsert_repo(
            self.conn, make_repo(1, "fast-grower", 1600, topics=["ai", "agent"]), 0
        )
        db.insert_snapshot(self.conn, 1, 1000, 5, ts_days_ago(14))
        db.insert_snapshot(self.conn, 1, 1200, 7, ts_days_ago(7))
        db.insert_snapshot(self.conn, 1, 1600, 9, ts_days_ago(0))
        self.conn.commit()

        rows = analytics.growth_rows(self.conn, window_days=7, limit=10, mode="total")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["growth"], 400)
        self.assertEqual(row["growth_pct"], 33.3)

        rows_pct = analytics.growth_rows(
            self.conn, window_days=7, limit=10, mode="pct"
        )
        self.assertEqual(rows_pct[0]["growth_pct"], 33.3)

        accel_map = analytics.acceleration_map(self.conn, window_days=7)
        self.assertAlmostEqual(accel_map[1]["accel"], 28.6, places=1)

    def test_daily_changes(self):
        for i, stars in ((1, 100), (2, 200), (3, 300), (4, 400)):
            db.upsert_repo(
                self.conn, make_repo(i, f"repo{i}", stars, topics=["ai"]), 0
            )
        self.conn.commit()
        db.save_daily_rank(self.conn, "growth", "2026-11-14", [(1, 1), (2, 2), (3, 3)])
        db.save_daily_rank(self.conn, "growth", "2026-11-15", [(2, 1), (3, 2), (4, 3)])
        data = analytics.daily_changes(self.conn, board="growth", limit=10)
        self.assertEqual(data["day"], "2026-11-15")
        self.assertEqual(data["prev_day"], "2026-11-14")
        changes = {item["id"]: item["change"] for item in data["items"]}
        self.assertEqual(changes[4], "new")
        self.assertEqual(changes[1], "out")
        self.assertEqual(changes[2], "up")
        self.assertEqual(changes[3], "up")
        self.assertEqual(data["items"][0]["id"], 4)

    def test_language_and_topic_stats(self):
        db.upsert_repo(
            self.conn, make_repo(1, "a", 500, topics=["ai", "agents"]), 0
        )
        db.upsert_repo(
            self.conn, make_repo(2, "b", 300, topics=["ai"]), 0
        )
        db.insert_snapshot(self.conn, 1, 400, 5, ts_days_ago(3))
        db.insert_snapshot(self.conn, 1, 500, 6, ts_days_ago(0))
        db.insert_snapshot(self.conn, 2, 200, 4, ts_days_ago(3))
        db.insert_snapshot(self.conn, 2, 300, 5, ts_days_ago(0))
        self.conn.commit()

        langs = analytics.language_stats(self.conn, window_days=7)
        python = next(x for x in langs if x["language"] == "Python")
        self.assertEqual(python["repos"], 2)
        self.assertEqual(python["stars"], 800)
        self.assertEqual(python["growth"], 200)
        self.assertAlmostEqual(python["growth_pct"], 33.3, places=1)

        topics = analytics.topic_stats(self.conn, window_days=7)
        ai = next(x for x in topics if x["topic"] == "ai")
        self.assertEqual(ai["repos"], 2)
        self.assertEqual(ai["stars"], 800)
        self.assertAlmostEqual(ai["growth_pct"], 33.3, places=1)


class CollectorRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_run_saves_daily_rank(self):
        cfg = Config(
            db_path=self.db_path,
            queries=["topic:ai stars:>10"],
            new_queries=[],
            refresh_existing=False,
            per_query=5,
        )
        client = FakeGraphQLClient()
        collector = Collector(cfg, client=client)
        result = collector.run(max_queries=1, include_new=False, verbose=False)
        self.assertTrue(result.ok)
        days = db.rank_days(self.conn, "stars")
        self.assertEqual(len(days), 1)
        ranks = db.get_rank_map(self.conn, "stars", days[0])
        self.assertEqual(len(ranks), 1)
        self.assertEqual(list(ranks.values())[0], 1)


if __name__ == "__main__":
    unittest.main()
