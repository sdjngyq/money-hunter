from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from moneyhunter import db, notifier
from moneyhunter.config import Config
from moneyhunter.web import create_app


def iso_days_ago(days: float) -> str:
    return db.iso(datetime.now(timezone.utc) - timedelta(days=days))


def seed_repo(conn, repo_id: int, name: str, stars_now: int, stars_then: int, *, days_ago: float = 10) -> int:
    repo = {
        "id": repo_id,
        "full_name": f"owner/{name}",
        "name": name,
        "owner": "owner",
        "html_url": f"https://github.com/owner/{name}",
        "description": f"{name} description",
        "language": "Python",
        "topics": ["saas"],
        "stars": stars_now,
        "forks": 20,
        "open_issues": 2,
        "license": "MIT",
        "archived": False,
        "created_at": iso_days_ago(200),
        "updated_at": iso_days_ago(0),
        "pushed_at": iso_days_ago(0),
    }
    repo_id_value, _ = db.upsert_repo(conn, repo, 35)
    db.insert_snapshot(conn, repo_id_value, stars_then, 15, iso_days_ago(days_ago))
    db.insert_snapshot(conn, repo_id_value, stars_now, 20, iso_days_ago(0))
    conn.commit()
    return repo_id_value


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)
        self.repo_a = seed_repo(self.conn, 1, "saas-kit", 500, 100)
        self.repo_b = seed_repo(self.conn, 2, "tool-lib", 300, 250)
        self.cfg = Config(
            db_path=self.db_path,
            digest_window_days=7,
            notify_type="feishu",
            notify_webhook="https://example.com/hook",
            notify_chat_id="42",
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_build_digest_and_render(self):
        digest = notifier.build_digest(self.cfg, self.conn, window_days=7)
        self.assertEqual(digest["summary"]["repos"], 2)
        names = [r["full_name"] for r in digest["growth"]]
        self.assertIn("owner/saas-kit", names)
        self.assertGreater(digest["summary"]["window_growth"], 0)

        title = notifier.render_title(digest)
        self.assertIn("GitHub 雷达日报", title)
        body = notifier.render_body(digest)
        self.assertIn("owner/saas-kit", body)
        self.assertIn("https://github.com/owner/saas-kit", body)

    def test_http_payloads(self):
        url, kwargs = notifier._build_http_request(self.cfg, "feishu", "标题", "正文")
        self.assertEqual(url, "https://example.com/hook")
        self.assertIn("标题", kwargs["json"]["content"]["text"])

        url, kwargs = notifier._build_http_request(self.cfg, "dingtalk", "标题", "正文")
        self.assertIn("正文", kwargs["json"]["text"]["content"])

        url, kwargs = notifier._build_http_request(self.cfg, "serverchan", "标题", "正文")
        self.assertEqual(kwargs["data"]["desp"], "正文")

        tg_cfg = Config(notify_type="telegram", notify_webhook="123:abc", notify_chat_id="42")
        url, kwargs = notifier._build_http_request(tg_cfg, "telegram", "标题", "正文")
        self.assertEqual(url, "https://api.telegram.org/bot123:abc/sendMessage")
        self.assertEqual(kwargs["json"]["chat_id"], "42")

        url, kwargs = notifier._build_http_request(self.cfg, "webhook", "标题", "正文")
        self.assertEqual(kwargs["json"]["title"], "标题")

    def test_send_with_fake_poster(self):
        calls: list[tuple] = []

        class Resp:
            status_code = 200

        def poster(url, **kwargs):
            calls.append((url, kwargs))
            return Resp()

        ok, detail = notifier.send(self.cfg, "T", "B", poster=poster)
        self.assertTrue(ok)
        self.assertIn("200", detail)
        self.assertEqual(calls[0][0], "https://example.com/hook")

    def test_send_without_config(self):
        cfg = Config(db_path=self.db_path)
        ok, detail = notifier.send(cfg, "T", "B")
        self.assertFalse(ok)
        self.assertIn("notify.type", detail)

    def test_serverchan_requires_webhook(self):
        cfg = Config(db_path=self.db_path, notify_type="serverchan")
        ok, detail = notifier.send(cfg, "T", "B")
        self.assertFalse(ok)
        self.assertIn("notify.webhook", detail)

    def test_email_requires_smtp(self):
        cfg = Config(db_path=self.db_path, notify_type="email")
        ok, detail = notifier.send(cfg, "T", "B")
        self.assertFalse(ok)
        self.assertIn("smtp_host", detail)

    def test_dry_run(self):
        ok, detail = notifier.notify_digest(self.cfg, self.conn, dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(detail, "dry-run")


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        conn = db.connect(self.db_path)
        db.init_db(conn)
        self.repo_a = seed_repo(conn, 1, "saas-kit", 500, 100)
        self.repo_b = seed_repo(conn, 2, "tool-lib", 300, 250)
        conn.close()
        self.cfg = Config(db_path=self.db_path)
        self.app = create_app(self.cfg)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_detail_page_and_api(self):
        resp = self.client.get(f"/repo/{self.repo_a}")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/api/repos/{self.repo_a}/detail")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["full_name"], "owner/saas-kit")
        self.assertEqual(len(data["history"]), 2)
        self.assertIn("7", data["windows"])
        self.assertEqual(data["windows"]["7"]["growth"], 400)
        self.assertIsNotNone(data["total"])

    def test_detail_missing_returns_404(self):
        self.assertEqual(self.client.get("/repo/9999").status_code, 404)
        self.assertEqual(self.client.get("/api/repos/9999/detail").status_code, 404)

    def test_compare_api_and_page(self):
        resp = self.client.get(f"/api/compare?ids={self.repo_a},{self.repo_b}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["repos"][0]["series"]), 2)
        self.assertEqual(data["repos"][0]["growth"], 400)

        page = self.client.get(f"/compare?ids={self.repo_a},{self.repo_b}")
        self.assertEqual(page.status_code, 200)

    def test_compare_with_days_filter(self):
        resp = self.client.get(f"/api/compare?ids={self.repo_a}&days=3")
        data = resp.get_json()
        # 10 天前与今天的快照：3 天窗口内只剩今天的 1 个点
        self.assertEqual(len(data["repos"][0]["series"]), 1)

    def test_breakdown_and_changes_endpoints(self):
        resp = self.client.get("/api/breakdown?dim=language")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["dim"], "language")
        self.assertGreaterEqual(body["count"], 1)
        self.assertIn("rows", body)

        topic = self.client.get("/api/breakdown?dim=topic&limit=8").get_json()
        self.assertGreaterEqual(topic["count"], 1)
        self.assertIn("rows", topic)

        changes = self.client.get("/api/changes?board=growth").get_json()
        self.assertEqual(changes["board"], "growth")
        self.assertEqual(changes["day"], "")
        self.assertEqual(changes["prev_day"], "")
        self.assertIsInstance(changes["items"], list)

        self.assertEqual(self.client.get("/api/breakdown?dim=bad").status_code, 404)

        pct = self.client.get("/api/leaderboard?board=growth&mode=pct").get_json()
        self.assertEqual(pct["mode"], "pct")
        self.assertTrue(all("growth_pct" in r for r in pct["rows"]))


class ConfigWebAuthTest(unittest.TestCase):
    def test_web_auth_from_fields(self):
        cfg = Config(
            db_path=":memory:",
            web_auth_user="admin",
            web_auth_password="pw",
        )
        self.assertTrue(cfg.web_auth_enabled)

    def test_web_auth_not_enabled_without_password(self):
        cfg = Config(db_path=":memory:", web_auth_user="admin", web_auth_password="")
        self.assertFalse(cfg.web_auth_enabled)

    def test_log_backup_days_default(self):
        cfg = Config(db_path=":memory:")
        self.assertEqual(cfg.log_backup_days, 7)


class LogSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "data" / "test.db"
        self.cfg = Config(db_path=self.db_path, log_backup_days=3)

    def tearDown(self):
        logger = logging.getLogger("moneyhunter")
        for handler in list(logger.handlers):
            base = getattr(handler, "baseFilename", "")
            if base and str(base).startswith(self.tmp.name):
                handler.close()
                logger.removeHandler(handler)
        self.tmp.cleanup()

    def setup_logging(self):
        from moneyhunter.logsetup import setup_logging

        return setup_logging(self.cfg)

    def test_creates_rotating_handler_and_log_file(self):
        import logging.handlers

        log_file = self.setup_logging()
        self.assertTrue(log_file.parent.exists())
        self.assertEqual(log_file.parent.name, "logs")
        handlers = logging.getLogger("moneyhunter").handlers
        self.assertTrue(any(isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in handlers))

    def test_writes_log_to_file(self):
        self.setup_logging()
        log_file = Path(self.tmp.name) / "data" / "logs" / "money_hunter.log"
        logging.getLogger("moneyhunter").info("轮转日志测试条目")
        # handler 默认 delay=True，写入后才会真正打开文件；再次 emit 保证落盘
        logging.getLogger("moneyhunter").info("第二条")
        self.assertTrue(log_file.exists())
        content = log_file.read_text(encoding="utf-8")
        self.assertIn("轮转日志测试条目", content)


class WebAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = db.connect(self.db_path)
        db.init_db(self.conn)
        seed_repo(self.conn, 1, "saas-kit", 500, 100)

        self.cfg_auth = Config(
            db_path=self.db_path,
            web_auth_user="admin",
            web_auth_password="secret",
        )
        self.cfg_no_auth = Config(db_path=self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _basic_header(self, user: str, pwd: str) -> dict[str, str]:
        import base64

        token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def test_health_no_auth(self):
        app = create_app(self.cfg_auth)
        client = app.test_client()
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_api_requires_basic_auth(self):
        app = create_app(self.cfg_auth)
        client = app.test_client()
        resp = client.get("/api/summary")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Basic", resp.headers.get("WWW-Authenticate", ""))

    def test_wrong_auth_rejected(self):
        app = create_app(self.cfg_auth)
        client = app.test_client()
        resp = client.get("/api/summary", headers=self._basic_header("admin", "wrong"))
        self.assertEqual(resp.status_code, 401)

    def test_correct_auth_ok(self):
        app = create_app(self.cfg_auth)
        client = app.test_client()
        resp = client.get("/api/summary", headers=self._basic_header("admin", "secret"))
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["repos"], 1)

    def test_page_requires_auth(self):
        app = create_app(self.cfg_auth)
        client = app.test_client()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 401)

    def test_no_auth_cfg_allows_anonymous(self):
        app = create_app(self.cfg_no_auth)
        client = app.test_client()
        resp = client.get("/api/summary")
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
