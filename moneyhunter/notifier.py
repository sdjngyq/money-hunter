"""日报生成与推送：Server酱 / 飞书 / 钉钉 / Telegram / 邮件 / 通用 webhook。"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Callable

import requests

from . import analytics, console
from .config import Config
from .db import connect, init_db

HTTP_KINDS = {"serverchan", "feishu", "dingtalk", "telegram", "webhook", "generic"}


def _fmt(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def _signed(n: Any) -> str:
    try:
        value = int(n)
    except (TypeError, ValueError):
        return "—"
    return f"+{value:,}" if value >= 0 else f"{value:,}"


def build_digest(cfg: Config, conn=None, window_days: int | None = None) -> dict[str, Any]:
    window = max(1, int(window_days or cfg.digest_window_days))
    own_conn = conn is None
    if own_conn:
        conn = connect(cfg.db_path)
        init_db(conn)
    try:
        info = analytics.summary(conn, window_days=window)
        growth = analytics.growth_rows(conn, window_days=window, limit=5)
        per_day_rows = analytics.growth_rows(
            conn, window_days=window, limit=200, mode="perday"
        )
        spikes = [
            r
            for r in per_day_rows
            if (r.get("per_day") or 0) >= cfg.notify_spike_threshold
            and r["id"] not in {g["id"] for g in growth}
        ][:5]
        new_repos = analytics.new_rows(conn, within_days=cfg.new_within_days, limit=3)
    finally:
        if own_conn:
            conn.close()

    return {
        "generated_at": datetime.now(),
        "window_days": window,
        "summary": info,
        "growth": growth,
        "spikes": spikes,
        "new": new_repos,
        "spike_threshold": cfg.notify_spike_threshold,
        "new_within_days": cfg.new_within_days,
    }


def render_title(digest: dict[str, Any]) -> str:
    info = digest["summary"]
    day = digest["generated_at"].strftime("%Y-%m-%d")
    return (
        f"GitHub 雷达日报 | {day} | 近 {digest['window_days']} 天 "
        f"{_signed(info['window_growth'])}★ · 新收录 {info['new_repos_24h']}"
    )


def _repo_line(
    index: int,
    repo: dict[str, Any],
    *,
    mode: str = "growth",
) -> list[str]:
    tags = []
    flags = repo.get("flags") or []
    if "suspect" in flags:
        tags.append("疑似刷星")
    if "inactive" in flags:
        tags.append("久未更新")
    suffix = f"（{'、'.join(tags)}）" if tags else ""
    stars = f"★{_fmt(repo['stars'])}"
    per_day = (
        f" / {_signed(repo.get('per_day'))}/天"
        if repo.get("per_day") is not None
        else ""
    )
    if mode == "new":
        head = f"{index}. {repo['full_name']} {stars} · 每天 {_signed(repo.get('per_day'))}{suffix}"
        detail = f"   创建于 {repo.get('created_at') or '—'} · 变现分 {repo.get('money_score', 0)}"
        return [head, detail, f"   {repo['url']}"]

    growth = _signed(repo.get("growth"))
    return [
        f"{index}. {repo['full_name']} {stars} {growth}{per_day}{suffix}",
        f"   {repo['url']}",
    ]


def render_body(digest: dict[str, Any]) -> str:
    info = digest["summary"]
    day = digest["generated_at"].strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"GitHub 赚钱项目雷达 · 日报 {day}",
        f"收录 {info['repos']} 个仓库 · 近 {digest['window_days']} 天新增 ★{_fmt(info['window_growth'])}"
        f" · 24h 新收录 {info['new_repos_24h']} · 快照 {_fmt(info['snapshots'])} 条",
    ]

    if digest["growth"]:
        lines += ["", f"🚀 增长最快（近 {digest['window_days']} 天）"]
        for index, repo in enumerate(digest["growth"], start=1):
            lines += _repo_line(index, repo)

    if digest["spikes"]:
        lines += ["", f"⚡ 星标异动（日均 ≥ {digest['spike_threshold']}）"]
        for index, repo in enumerate(digest["spikes"], start=1):
            lines += _repo_line(index, repo)

    if digest["new"]:
        lines += ["", f"🆕 新项目高潜（{digest['new_within_days']} 天内创建）"]
        for index, repo in enumerate(digest["new"], start=1):
            lines += _repo_line(index, repo, mode="new")

    if not (digest["growth"] or digest["spikes"] or digest["new"]):
        lines += ["", "暂无增长数据：再采集一次后即可生成增长榜单。"]

    lines += ["", "—— GitHub Money Hunter 自动生成"]
    return "\n".join(lines)


def _build_http_request(cfg: Config, kind: str, title: str, body: str):
    combined = f"{title}\n\n{body}"
    if kind == "serverchan":
        key = cfg.notify_webhook
        url = key if key.startswith("http") else f"https://sctapi.ftqq.com/{key}.send"
        return url, {"data": {"title": title, "desp": body}}
    if kind == "feishu":
        return cfg.notify_webhook, {
            "json": {"msg_type": "text", "content": {"text": combined}}
        }
    if kind == "dingtalk":
        return cfg.notify_webhook, {
            "json": {"msgtype": "text", "text": {"content": combined}}
        }
    if kind == "telegram":
        url = cfg.notify_webhook
        if not url.startswith("http"):
            url = f"https://api.telegram.org/bot{url}/sendMessage"
        return url, {
            "json": {
                "chat_id": cfg.notify_chat_id,
                "text": f"*{title}*\n\n{body}",
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
        }
    return cfg.notify_webhook, {"json": {"title": title, "text": body}}


def _send_email(cfg: Config, title: str, body: str) -> tuple[bool, str]:
    if not cfg.smtp_host or not cfg.smtp_to:
        return False, "邮件推送需要配置 notify.smtp_host 与 notify.to"
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = cfg.smtp_user or "moneyhunter@localhost"
    message["To"] = cfg.smtp_to
    message.set_content(body)
    context = ssl.create_default_context()
    port = cfg.smtp_port or 465
    if port == 465:
        with smtplib.SMTP_SSL(cfg.smtp_host, port, context=context, timeout=20) as server:
            if cfg.smtp_user:
                server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(cfg.smtp_host, port, timeout=20) as server:
            server.starttls(context=context)
            if cfg.smtp_user:
                server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(message)
    return True, f"邮件已发送至 {cfg.smtp_to}"


def send(
    cfg: Config,
    title: str,
    body: str,
    *,
    poster: Callable | None = None,
) -> tuple[bool, str]:
    kind = (cfg.notify_type or "").strip().lower()
    if not kind:
        return False, "未配置 notify.type，跳过推送"
    try:
        if kind == "email":
            return _send_email(cfg, title, body)
        if kind in HTTP_KINDS:
            if not cfg.notify_webhook:
                return False, f"{kind} 需要配置 notify.webhook"
            url, kwargs = _build_http_request(cfg, kind, title, body)
            post = poster or requests.post
            resp = post(url, timeout=15, **kwargs)
            ok = 200 <= resp.status_code < 300
            return ok, f"HTTP {resp.status_code}"
        return False, f"未知的 notify.type：{kind}"
    except Exception as exc:  # noqa: BLE001 - 推送失败不应影响采集
        return False, f"{type(exc).__name__}: {exc}"


def notify_digest(
    cfg: Config,
    conn=None,
    *,
    dry_run: bool = False,
    window_days: int | None = None,
    poster: Callable | None = None,
) -> tuple[bool, str]:
    digest = build_digest(cfg, conn, window_days=window_days)
    if not digest["summary"]["repos"]:
        return False, "数据库还没有数据，先运行 python run.py collect"
    title = render_title(digest)
    body = render_body(digest)
    if dry_run:
        console.say(title)
        console.say()
        for line in body.splitlines():
            console.say(line)
        return True, "dry-run"
    ok, detail = send(cfg, title, body, poster=poster)
    return ok, detail
