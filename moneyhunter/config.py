from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.yaml"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "money_hunter.db"

DEFAULT_QUERIES = [
    "topic:ai-agents stars:>500",
    "topic:llm stars:>3000",
    "topic:chatgpt stars:>3000",
    "topic:stable-diffusion stars:>3000",
    "topic:rag stars:>1000",
    # 新模型发布 / 本地部署 / 推理
    "topic:llm-inference stars:>200",
    "topic:model-serving stars:>200",
    "topic:quantization stars:>300",
    "topic:gguf stars:>200",
    "topic:local-llm stars:>300",
    "topic:automation stars:>1500",
    "topic:side-project stars:>500",
    "saas boilerplate stars:>500",
    "topic:ecommerce stars:>1500",
    "topic:shopify stars:>500",
    "topic:marketing stars:>800",
    "topic:web-scraping stars:>1000",
    "topic:nocode stars:>300",
    "topic:trading-bot stars:>1000",
    "topic:quantitative-trading stars:>500",
    # 硬件 / IoT
    "topic:home-automation stars:>1000",
    "topic:esp32 stars:>500",
    "topic:stm32 stars:>300",
    "topic:raspberry-pi stars:>1500",
    # 开源硬件 / PCB / DIY 创客
    "topic:open-hardware stars:>200",
    "topic:pcb stars:>500",
    "topic:kicad stars:>300",
    "topic:diy stars:>1000",
    "topic:3d-printing stars:>1000",
    "topic:home-lab stars:>50",
    # 逆向工程 / 安全研究（合法开源工具）
    "topic:reverse-engineering stars:>1000",
    "topic:decompiler stars:>300",
    "topic:ctf stars:>500",
]

DEFAULT_NEW_QUERIES = [
    "topic:ai created:>{since} stars:>50",
    "topic:llm created:>{since} stars:>50",
    "topic:agents created:>{since} stars:>30",
    "topic:inference created:>{since} stars:>20",
    "topic:saas created:>{since} stars:>20",
    "topic:automation created:>{since} stars:>20",
    "topic:hardware created:>{since} stars:>20",
    "topic:open-hardware created:>{since} stars:>10",
    "topic:esp32 created:>{since} stars:>10",
    "topic:stm32 created:>{since} stars:>10",
    "topic:diy created:>{since} stars:>20",
    "topic:reverse-engineering created:>{since} stars:>20",
    "created:>{since} stars:>200",
]


@dataclass
class Config:
    """运行配置。所有路径都是绝对路径。"""

    token: str | None = None
    db_path: Path = DEFAULT_DB_PATH
    min_stars: int = 50
    per_query: int = 50
    request_delay: float | None = None
    interval_minutes: int = 360
    growth_window_days: int = 7
    new_within_days: int = 60
    top_limit: int = 50
    queries: list[str] = field(default_factory=lambda: list(DEFAULT_QUERIES))
    new_queries: list[str] = field(default_factory=lambda: list(DEFAULT_NEW_QUERIES))
    # 存量仓库刷新（保证增长数据准确）
    refresh_existing: bool = True
    refresh_limit: int = 2000          # 有 Token（GraphQL）时每轮最多刷新多少个
    refresh_limit_no_token: int = 30   # 无 Token 时用 REST 降级，核心额度有限
    refresh_batch_size: int = 100      # GraphQL 每批查询数量（最大 100）
    # 质量过滤阈值
    inactive_days: int = 180
    suspect_daily_stars: int = 250
    suspect_fork_ratio: float = 0.01
    # 日报 / 推送
    notify_type: str = ""
    notify_webhook: str = ""
    notify_chat_id: str = ""
    notify_spike_threshold: int = 300
    digest_window_days: int = 1
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_to: str = ""
    # Web 服务（可选访问口令 + 日志轮转）
    web_auth_user: str = ""
    web_auth_password: str = ""
    log_backup_days: int = 7

    @property
    def web_auth_enabled(self) -> bool:
        return bool(self.web_auth_user and self.web_auth_password)

    @property
    def notify_enabled(self) -> bool:
        return bool(self.notify_type.strip())

    @property
    def effective_delay(self) -> float:
        """两次请求之间的等待秒数：显式配置优先，否则按是否有 token 自动取值。"""
        if self.request_delay is not None:
            try:
                return max(0.0, float(self.request_delay))
            except (TypeError, ValueError):
                pass
        return 2.0 if self.token else 7.0

    def new_queries_formatted(self, since: str) -> list[str]:
        return [q.replace("{since}", since) for q in self.new_queries]


def _resolve_path(value: str | Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def ensure_config_file() -> tuple[Path, bool]:
    """确保 config.yaml 存在。返回 (路径, 是否新建)。"""
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH, False
    if EXAMPLE_CONFIG_PATH.exists():
        DEFAULT_CONFIG_PATH.write_text(
            EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        return DEFAULT_CONFIG_PATH, True
    return DEFAULT_CONFIG_PATH, False


def load_config(path: str | Path | None = None, db_override: str | Path | None = None) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    elif EXAMPLE_CONFIG_PATH.exists():
        with EXAMPLE_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    token = (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or data.get("token")
        or ""
    )
    token = str(token).strip() if token else None

    notify = data.get("notify") or {}
    if not isinstance(notify, dict):
        notify = {}

    web = data.get("web") or {}
    if not isinstance(web, dict):
        web = {}

    def _web_int(key: str, default: int) -> int:
        try:
            return max(1, int(web.get(key, default) or default))
        except (TypeError, ValueError):
            return default

    def _notify_int(key: str, default: int) -> int:
        try:
            return max(1, int(notify.get(key, default) or default))
        except (TypeError, ValueError):
            return default

    cfg = Config(
        token=token or None,
        db_path=_resolve_path(db_override or data.get("db_path") or DEFAULT_DB_PATH),
        min_stars=max(0, int(data.get("min_stars", 50) or 0)),
        per_query=min(100, max(1, int(data.get("per_query", 50) or 50))),
        request_delay=data.get("request_delay"),
        interval_minutes=max(1, int(data.get("interval_minutes", 360) or 360)),
        growth_window_days=max(1, int(data.get("growth_window_days", 7) or 7)),
        new_within_days=max(1, int(data.get("new_within_days", 60) or 60)),
        top_limit=min(500, max(1, int(data.get("top_limit", 50) or 50))),
        queries=[str(q) for q in (data.get("queries") or DEFAULT_QUERIES) if str(q).strip()],
        new_queries=[
            str(q) for q in (data.get("new_queries") or DEFAULT_NEW_QUERIES) if str(q).strip()
        ],
        refresh_existing=bool(data.get("refresh_existing", True)),
        refresh_limit=max(0, int(data.get("refresh_limit", 2000) or 0)),
        refresh_limit_no_token=max(0, int(data.get("refresh_limit_no_token", 30) or 0)),
        refresh_batch_size=min(100, max(1, int(data.get("refresh_batch_size", 100) or 100))),
        inactive_days=max(1, int(data.get("inactive_days", 180) or 180)),
        suspect_daily_stars=max(1, int(data.get("suspect_daily_stars", 250) or 250)),
        suspect_fork_ratio=max(0.0, float(data.get("suspect_fork_ratio", 0.01) or 0.01)),
        notify_type=str(
            os.environ.get("GHMH_NOTIFY_TYPE") or notify.get("type") or ""
        ).strip().lower(),
        notify_webhook=str(
            os.environ.get("GHMH_NOTIFY_WEBHOOK") or notify.get("webhook") or ""
        ).strip(),
        notify_chat_id=str(notify.get("chat_id") or "").strip(),
        notify_spike_threshold=_notify_int("spike_threshold", 300),
        digest_window_days=_notify_int("digest_window_days", 1),
        smtp_host=str(notify.get("smtp_host") or "").strip(),
        smtp_port=_notify_int("smtp_port", 465),
        smtp_user=str(notify.get("smtp_user") or "").strip(),
        smtp_password=str(notify.get("smtp_password") or ""),
        smtp_to=str(notify.get("to") or "").strip(),
        web_auth_user=str(
            os.environ.get("GHMH_WEB_USER") or web.get("auth_user") or ""
        ).strip(),
        web_auth_password=str(
            os.environ.get("GHMH_WEB_PASSWORD") or web.get("auth_password") or ""
        ),
        log_backup_days=_web_int("log_backup_days", 7),
    )
    return cfg
