"""变现潜力评分：根据仓库的名称 / 描述 / 话题标签估算“能不能靠它赚钱”。

评分是启发式的，仅供排序参考，不代表真实商业价值。
"""

from __future__ import annotations

import re

# 关键词 -> 权重。命中即加分，总分上限 100。
KEYWORDS: dict[str, int] = {
    # 商业模式
    "saas": 12,
    "monetiz": 12,
    "revenue": 10,
    "income": 10,
    "profit": 9,
    "business": 7,
    "startup": 6,
    "boilerplate": 10,
    "starter": 7,
    "template": 5,
    "indie": 7,
    "side-hustle": 10,
    "side project": 9,
    "passive": 9,
    "freelance": 8,
    "agency": 7,
    # 电商 / 支付 / 营销
    "ecommerce": 10,
    "e-commerce": 10,
    "shopify": 10,
    "stripe": 8,
    "payment": 6,
    "checkout": 6,
    "marketing": 9,
    "seo": 8,
    "affiliate": 9,
    "newsletter": 7,
    "email-marketing": 10,
    "crm": 8,
    "invoice": 6,
    "booking": 6,
    "analytics": 6,
    "advertis": 6,
    "copywriting": 8,
    "content-creation": 7,
    # 生产力 / 自动化
    "automation": 8,
    "workflow": 6,
    "nocode": 8,
    "no-code": 8,
    "low-code": 7,
    "internal-tools": 7,
    "dashboard": 5,
    "scraping": 8,
    "scraper": 8,
    "crawler": 5,
    # AI
    "ai": 4,
    "agents": 7,
    "agent": 5,
    "llm": 5,
    "gpt": 4,
    "chatgpt": 5,
    "rag": 5,
    "stable-diffusion": 4,
    "image-generation": 4,
    "voice": 4,
    "tts": 4,
    "transcri": 4,
    "chatbot": 7,
    "copilot": 5,
    # 模型部署 / 本地推理
    "inference": 4,
    "quantiz": 4,
    "gguf": 4,
    "model-serving": 4,
    "local-llm": 4,
    # 交易 / 金融
    "trading": 8,
    "quant": 7,
    "backtest": 7,
    "portfolio": 5,
    "market-making": 7,
    # 硬件 / IoT
    "iot": 6,
    "home-automation": 7,
    "esp32": 5,
    "stm32": 5,
    "raspberry-pi": 5,
    "arduino": 5,
    "embedded": 4,
    "hardware": 5,
    "robot": 5,
    "3d-print": 5,
    # 开源硬件 / PCB / 创客 DIY
    "open-hardware": 5,
    "pcb": 4,
    "kicad": 4,
    "diy": 4,
    "home-lab": 4,
    "self-hosted": 5,
    # 逆向工程 / 安全研究
    "reverse-engineering": 4,
    "decompiler": 4,
    "ctf": 3,
}

# 太短的关键词必须整词匹配，避免 "ai" 命中 "email" 之类的误伤。
_SHORT_LEN = 4


def _has(text: str, keyword: str) -> bool:
    kw = keyword.lower()
    if len(kw) <= _SHORT_LEN and kw.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text) is not None
    return kw in text


def score_text(text: str) -> int:
    haystack = (text or "").lower()
    total = 0
    for keyword, weight in KEYWORDS.items():
        if _has(haystack, keyword):
            total += weight
    return min(100, total)


def money_score(repo: dict) -> int:
    """对 GitHub API 原始仓库字典或本地仓库字典打分。"""
    name = repo.get("name") or repo.get("full_name") or ""
    description = repo.get("description") or ""
    topics = repo.get("topics") or []
    if isinstance(topics, str):
        topic_text = topics.replace('"', " ")
    else:
        topic_text = " ".join(str(t) for t in topics)
    return score_text(" ".join([name, description, topic_text]))
