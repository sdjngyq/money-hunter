"""演示数据：生成一个带 45 天星标历史的本地数据库，方便直接预览仪表盘。

所有数据均为合成的演示数据，仅用于展示界面效果。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, quality, scoring
from .config import Config

HISTORY_DAYS = 45

# (full_name, stars, language, created_days, 近7天增长比例, topics, description
#  [, fork/star 比例, 距今天数内没有 push])
DEMO_REPOS: list[tuple] = [
    # ---- 人工智能 / LLM / Agent ----
    ("langchain-ai/langchain", 118000, "Python", 1200, 0.012,
     ["llm", "agents", "rag"], "Build context-aware reasoning applications with LLMs."),
    ("Significant-Gravitas/AutoGPT", 172000, "Python", 1300, 0.006,
     ["autonomous-agents", "ai", "gpt-4"], "AutoGPT is the vision of accessible AI for everyone."),
    ("n8n-io/n8n", 82000, "TypeScript", 1700, 0.02,
     ["automation", "workflow", "no-code", "ai"], "Fair-code workflow automation platform with native AI capabilities."),
    ("langgenius/dify", 88000, "TypeScript", 900, 0.035,
     ["llm", "agent", "workflow", "low-code"], "Production-ready platform for agentic workflow development."),
    ("open-webui/open-webui", 98000, "Python", 800, 0.03,
     ["llm", "chat", "self-hosted"], "User-friendly AI interface (supports Ollama, OpenAI API, ...)."),
    ("infiniflow/ragflow", 66000, "Python", 750, 0.045,
     ["rag", "agents", "llm"], "RAG engine fused with agent capabilities to build a superior context layer for LLMs."),
    ("browser-use/browser-use", 58000, "Python", 380, 0.06,
     ["browser", "agents", "automation"], "Make websites accessible for AI agents."),
    ("All-Hands-AI/OpenHands", 50000, "Python", 600, 0.04,
     ["agents", "coding", "llm"], "Platform for AI software developers as generalist agents."),
    ("ollama/ollama", 132000, "Go", 900, 0.025,
     ["llm", "local", "inference"], "Get up and running with large language models locally."),
    ("comfyanonymous/ComfyUI", 74000, "Python", 1100, 0.022,
     ["stable-diffusion", "image-generation"], "The most powerful and modular diffusion model GUI and backend."),
    ("microsoft/autogen", 44000, "Python", 700, 0.03,
     ["agents", "llm", "multi-agent"], "A programming framework for agentic AI."),
    ("crewAIInc/crewAI", 34000, "Python", 500, 0.05,
     ["agents", "multi-agent", "automation"], "Framework for orchestrating role-playing autonomous AI agents."),
    ("firecrawl/firecrawl", 40000, "TypeScript", 400, 0.06,
     ["scraping", "crawler", "ai"], "The web data API for AI - turn any website into LLM-ready data."),
    ("unclecode/crawl4ai", 44000, "Python", 420, 0.05,
     ["scraping", "llm", "crawler"], "Open-source LLM-friendly web crawler and scraper."),
    ("huggingface/transformers", 142000, "Python", 1900, 0.012,
     ["nlp", "llm", "deep-learning"], "State-of-the-art machine learning for JAX, PyTorch and TensorFlow."),
    # ---- SaaS / 电商 / 营销 ----
    ("calcom/cal.com", 39000, "TypeScript", 1500, 0.02,
     ["scheduling", "saas", "open-source"], "Scheduling infrastructure for absolutely everyone."),
    ("twentyhq/twenty", 28000, "TypeScript", 700, 0.04,
     ["crm", "saas", "sales"], "The open alternative to Salesforce, designed for AI."),
    ("nocodb/nocodb", 54000, "TypeScript", 1500, 0.018,
     ["database", "no-code", "airtable-alternative"], "Open source Airtable alternative."),
    ("appsmithorg/appsmith", 38000, "TypeScript", 1300, 0.018,
     ["low-code", "internal-tools", "admin-panel"], "Platform to build admin panels, internal tools and dashboards."),
    ("medusajs/medusa", 28000, "TypeScript", 1400, 0.022,
     ["ecommerce", "headless", "commerce"], "The world's most flexible commerce platform."),
    ("saleor/saleor", 22000, "Python", 1900, 0.01,
     ["ecommerce", "graphql", "headless"], "Saleor Core: the high performance, composable, headless commerce API."),
    ("bagisto/bagisto", 19000, "PHP", 1900, 0.012,
     ["ecommerce", "laravel", "shop"], "Free and open source laravel eCommerce platform."),
    ("tryghost/ghost", 50000, "JavaScript", 2000, 0.012,
     ["cms", "blogging", "publishing"], "Independent technology for modern publishing, memberships and subscriptions."),
    ("knadh/listmonk", 19000, "Go", 1200, 0.025,
     ["newsletter", "email-marketing", "mailing-list"], "High performance, self-hosted newsletter and mailing list manager."),
    ("chatwoot/chatwoot", 25000, "Ruby", 1300, 0.03,
     ["customer-support", "crm", "chat"], "Open-source live-chat, email support and omni-channel desk."),
    ("PostHog/posthog", 27000, "Python", 1200, 0.03,
     ["analytics", "product-analytics"], "How developers build successful products - product analytics and session replay."),
    ("gitroomhq/postiz-app", 21000, "TypeScript", 450, 0.065,
     ["social-media", "scheduling", "saas", "ai"], "The ultimate social media scheduling tool, with a bunch of AI."),
    ("matomo-org/matomo", 20000, "PHP", 2500, 0.008,
     ["analytics", "privacy"], "Leading open source alternative to Google Analytics."),
    # ---- 量化 / 交易 ----
    ("freqtrade/freqtrade", 41000, "Python", 1800, 0.02,
     ["trading", "crypto", "bot", "backtesting"], "Free, open source crypto trading bot."),
    ("hummingbot/hummingbot", 13000, "Python", 1600, 0.02,
     ["trading", "market-making", "crypto"], "Open source software to create and deploy high-frequency crypto trading bots."),
    ("vnpy/vnpy", 31000, "Python", 2000, 0.012,
     ["trading", "quant", "investment"], "Python-based open source quantitative trading system development framework."),
    ("microsoft/qlib", 23000, "Python", 1200, 0.018,
     ["quant", "machine-learning", "investment"], "Qlib is an AI-oriented quantitative investment platform."),
    # ---- 硬件 / IoT ----
    ("home-assistant/core", 80000, "Python", 2200, 0.012,
     ["home-automation", "iot", "smart-home"], "Open source home automation that puts local control and privacy first."),
    ("esphome/esphome", 30000, "C++", 1200, 0.022,
     ["esp32", "esp8266", "iot", "home-automation"], "Control your ESP8266/ESP32 with simple yet powerful configuration files."),
    ("espressif/arduino-esp32", 15000, "C", 1200, 0.014,
     ["esp32", "arduino", "iot"], "Arduino core for the ESP32."),
    ("platformio/platformio-core", 8000, "Python", 2000, 0.01,
     ["embedded", "iot", "tooling"], "Your gateway to embedded software development."),
    # ---- 最新高潜（演示用，创建时间较短）----
    ("openai/openai-agents-python", 16000, "Python", 45, 0.28,
     ["agents", "openai", "automation"], "A lightweight, powerful framework for multi-agent workflows."),
    ("modelcontextprotocol/servers", 44000, "TypeScript", 55, 0.22,
     ["mcp", "agents", "llm"], "Model Context Protocol servers - connect AI models to tools and data."),
    ("deepseek-ai/DeepSeek-R1", 90000, "Python", 35, 0.18,
     ["llm", "reasoning", "ai"], "DeepSeek-R1: incentivizing reasoning capability in LLMs via RL."),
    ("browserbase/stagehand", 14000, "TypeScript", 30, 0.25,
     ["browser", "agents", "automation"], "An AI web browsing framework for developers."),
    ("agent0ai/agent-zero", 12000, "Python", 50, 0.2,
     ["agents", "automation", "framework"], "Agent Zero - a personal, organic agentic framework."),
    # ---- 质量标记演示（精选列表 / 久未更新 / 疑似刷星）----
    ("sindresorhus/awesome", 390000, "", 3800, 0.004,
     ["awesome", "lists", "resources"],
     "Awesome lists about all kinds of interesting topics.", 0.34, None),
    ("legacyco/old-crm", 15000, "PHP", 3000, 0.0008,
     ["crm", "php", "self-hosted"], "Legacy self-hosted CRM, no longer maintained.",
     0.2, 420),
    ("starfarm/fake-saas-kit", 9000, "JavaScript", 60, 0.45,
     ["saas", "boilerplate", "stripe"], "The ultimate SaaS boilerplate to make money online fast.",
     0.001, 3),
]


def _build_history(stars: int, week_growth: float, days: int) -> list[int]:
    delta7 = max(1, int(stars * week_growth))
    values: list[int] = []
    for ago in range(days, -1, -1):
        if ago <= 7:
            delta = delta7 * ago / 7
        else:
            delta = delta7 + (ago - 7) * delta7 * 0.45 / 7
        delta = min(delta, stars * 0.78)
        values.append(max(0, int(round(stars - delta))))
    values[-1] = stars
    return values


def _reset_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def seed(db_path: str | Path) -> dict:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reset_db(path)
    conn = db.connect(path)
    db.init_db(conn)

    rng = random.Random(20261115)
    now = datetime.now(timezone.utc)
    rank_stats: list[tuple[int, int, int]] = []

    for index, spec in enumerate(DEMO_REPOS):
        full_name, stars, language, created_days, week_growth, topics, description = spec[:7]
        fork_ratio = float(spec[7]) if len(spec) > 7 else rng.uniform(0.12, 0.22)
        pushed_days = spec[8] if len(spec) > 8 else None
        repo_id = 900001 + index
        created_at = now - timedelta(days=created_days)
        history_days = min(HISTORY_DAYS, created_days)
        values = _build_history(stars, week_growth, history_days)

        oldest = now - timedelta(days=history_days)
        money = scoring.money_score(
            {"name": full_name.split("/")[-1], "description": description, "topics": topics}
        )
        pushed_at = (
            now - timedelta(days=pushed_days)
            if pushed_days is not None
            else now - timedelta(hours=rng.randint(1, 96))
        )
        repo = {
            "id": repo_id,
            "full_name": full_name,
            "name": full_name.split("/")[-1],
            "owner": full_name.split("/")[0],
            "html_url": f"https://github.com/{full_name}",
            "description": description,
            "language": language,
            "topics": topics,
            "stars": stars,
            "forks": int(stars * fork_ratio),
            "open_issues": rng.randint(20, 900),
            "license": "MIT",
            "archived": False,
            "created_at": db.iso(created_at),
            "updated_at": db.iso(now - timedelta(hours=rng.randint(1, 60))),
            "pushed_at": db.iso(pushed_at),
        }
        repo_id_value, _ = db.upsert_repo(conn, repo, money)
        conn.execute(
            "UPDATE repos SET first_seen_at=? WHERE id=?", (db.iso(oldest), repo_id_value)
        )

        for offset, value in enumerate(values):
            ts = (now - timedelta(days=history_days - offset)).replace(
                hour=3, minute=0, second=0, microsecond=0
            )
            db.insert_snapshot(
                conn,
                repo_id_value,
                value,
                int(value * fork_ratio),
                captured_at=db.iso(ts),
            )

        g_today = values[-1] - values[-7] if len(values) >= 7 else values[-1] - values[0]
        g_prev = values[-2] - values[-8] if len(values) >= 8 else g_today
        rank_stats.append((repo_id_value, max(0, g_today), max(0, g_prev)))

    quality.recompute(conn, Config())

    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    top_n = 20
    today_sorted = sorted(rank_stats, key=lambda s: s[1], reverse=True)[:top_n]
    prev_sorted = sorted(rank_stats, key=lambda s: s[2], reverse=True)[:top_n]
    db.save_daily_rank(
        conn, "growth", today, [(rid, i + 1) for i, (rid, _, _) in enumerate(today_sorted)]
    )
    db.save_daily_rank(
        conn, "growth", yesterday, [(rid, i + 1) for i, (rid, _, _) in enumerate(prev_sorted)]
    )

    for days_ago, seen, new, refreshed in (
        (HISTORY_DAYS, 34, 34, 34),
        (30, 40, 8, 40),
        (1, 44, 4, 44),
    ):
        started = (now - timedelta(days=days_ago)).strftime(db.TS_FMT)
        finished = (now - timedelta(days=days_ago, minutes=-2)).strftime(db.TS_FMT)
        conn.execute(
            """INSERT INTO runs (started_at, finished_at, status, queries_total, queries_ok,
                                 repos_seen, repos_new, repos_refreshed, message)
               VALUES (?, ?, 'ok', 25, 25, ?, ?, ?, '')""",
            (started, finished, seen, new, refreshed),
        )

    conn.commit()
    conn.close()
    return {"repos": len(DEMO_REPOS), "path": str(path)}
