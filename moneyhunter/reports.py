"""报告输出：终端表格 + Markdown / HTML 文件。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import analytics, console
from .config import PROJECT_ROOT, Config
from .db import connect, init_db

REPORTS_DIR = PROJECT_ROOT / "reports"

FLAG_LABELS = {
    "awesome": "精选列表",
    "suspect": "疑似刷星",
    "inactive": "久未更新",
    "archived": "已归档",
}


def _flag_labels(repo: dict) -> list[str]:
    flags = repo.get("flags") or []
    return [FLAG_LABELS[f] for f in flags if f in FLAG_LABELS]


def _flag_markup(repo: dict) -> str:
    labels = _flag_labels(repo)
    return f" [dim]{'·'.join(labels)}[/dim]" if labels else ""


def _flag_text(repo: dict) -> str:
    labels = _flag_labels(repo)
    return f"（{'、'.join(labels)}）" if labels else ""


def _fmt(n: Any) -> str:
    if n is None:
        return "—"
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_delta(n: Any) -> str:
    if n is None:
        return "—"
    try:
        value = int(n)
    except (TypeError, ValueError):
        return str(n)
    return f"+{value:,}" if value > 0 else f"{value:,}"


def collect_sections(cfg: Config, conn, *, limit: int | None = None) -> dict[str, list[dict]]:
    n = limit or cfg.top_limit
    return {
        "growth": analytics.growth_rows(
            conn, window_days=cfg.growth_window_days, limit=n
        ),
        "stars": analytics.stars_rows(
            conn, limit=n, window_days=cfg.growth_window_days
        ),
        "new": analytics.new_rows(conn, within_days=cfg.new_within_days, limit=n),
        "money": analytics.money_rows(
            conn, limit=n, window_days=cfg.growth_window_days
        ),
    }


# ------------------------------------------------------------------ 终端


def print_console(cfg: Config, conn, limit: int | None = None) -> None:
    try:
        from rich import box
        from rich.markup import escape
        from rich.table import Table
    except ImportError:  # pragma: no cover
        _print_console_plain(cfg, conn, limit)
        return

    rich_console = console.console()
    sections = collect_sections(cfg, conn, limit=limit or 10)

    titles = {
        "growth": f"🚀 增长最快（近 {cfg.growth_window_days} 天）",
        "stars": "⭐ Star 最多",
        "new": f"🆕 最新高潜（{cfg.new_within_days} 天内创建）",
        "money": "💎 变现潜力榜",
    }

    for key, title in titles.items():
        table = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", pad_edge=False)
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("项目", overflow="fold", max_width=46)
        table.add_column("Star", justify="right", style="bold yellow")
        if key == "new":
            table.add_column("星/天", justify="right", style="green")
            table.add_column("年龄", justify="right", style="dim")
        else:
            table.add_column("增长", justify="right", style="green")
            table.add_column("日均", justify="right", style="green")
        table.add_column("变现", justify="right")
        table.add_column("语言", style="dim")

        rows = sections[key]
        if not rows:
            table.add_row("—", "暂无数据", "", "", "", "", "")
        for i, repo in enumerate(rows, start=1):
            name = f"[link={repo['url']}]{escape(repo['full_name'])}[/link]{_flag_markup(repo)}"
            if key == "new":
                table.add_row(
                    str(i),
                    name,
                    _fmt(repo["stars"]),
                    _fmt_delta(repo.get("per_day")),
                    f"{round(repo.get('age_days') or 0)} 天",
                    str(repo["money_score"]),
                    repo["language"] or "—",
                )
            else:
                table.add_row(
                    str(i),
                    name,
                    _fmt(repo["stars"]),
                    _fmt_delta(repo.get("growth")),
                    _fmt_delta(repo.get("per_day")),
                    str(repo["money_score"]),
                    repo["language"] or "—",
                )
        rich_console.print(table)
        rich_console.print()

    info = analytics.summary(conn, window_days=cfg.growth_window_days)
    console.say(
        f"共收录 {info['repos']} 个仓库 · 快照 {info['snapshots']} 条 · "
        f"上次采集 {info['last_run_at'] or '从未'}"
    )


def _print_console_plain(cfg: Config, conn, limit: int | None = None) -> None:  # pragma: no cover
    sections = collect_sections(cfg, conn, limit=limit or 10)
    for key in ("growth", "stars", "new", "money"):
        print(f"\n== {key} ==")
        for i, repo in enumerate(sections[key], start=1):
            print(
                f"{i:>2}. {repo['full_name']}  ★{_fmt(repo['stars'])}  "
                f"增长 {_fmt_delta(repo.get('growth'))}  变现 {repo['money_score']}"
            )


# ------------------------------------------------------------------ Markdown


def _md_row(repo: dict, rank: int, with_growth: bool = True) -> str:
    name = f"[{repo['full_name']}]({repo['url']}){_flag_text(repo)}"
    stars = _fmt(repo["stars"])
    growth = _fmt_delta(repo.get("growth"))
    per_day = _fmt_delta(repo.get("per_day"))
    lang = repo.get("language") or "—"
    if with_growth:
        return f"| {rank} | {name} | {stars} | {growth} | {per_day}/天 | {repo['money_score']} | {lang} |"
    return f"| {rank} | {name} | {stars} | {repo['money_score']} | {lang} |"


def build_markdown(cfg: Config, conn) -> str:
    sections = collect_sections(cfg, conn)
    info = analytics.summary(conn, window_days=cfg.growth_window_days)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# GitHub 赚钱项目雷达 · 项目报告",
        "",
        f"> 生成时间：{now}　|　收录仓库：{info['repos']} 个　|　"
        f"追踪 Star 总量：{_fmt(info['total_stars'])}　|　"
        f"近 {cfg.growth_window_days} 天新增：{_fmt(info['window_growth'])}",
        "",
        f"## 🚀 增长最快（近 {cfg.growth_window_days} 天）",
        "",
        "| # | 项目 | Star | 增长 | 日均 | 变现分 | 语言 |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    lines += [_md_row(r, i) for i, r in enumerate(sections["growth"], 1)]
    lines += [
        "",
        "## ⭐ Star 最多",
        "",
        "| # | 项目 | Star | 增长 | 日均 | 变现分 | 语言 |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    lines += [_md_row(r, i) for i, r in enumerate(sections["stars"], 1)]
    lines += [
        "",
        f"## 🆕 最新高潜（{cfg.new_within_days} 天内创建）",
        "",
        "| # | 项目 | Star | 星/天 | 变现分 | 语言 |",
        "|---:|---|---:|---:|---:|---|",
    ]
    for i, repo in enumerate(sections["new"], 1):
        lines.append(
            f"| {i} | [{repo['full_name']}]({repo['url']}){_flag_text(repo)} | {_fmt(repo['stars'])} | "
            f"{_fmt_delta(repo.get('per_day'))}/天 | {repo['money_score']} | {repo.get('language') or '—'} |"
        )
    lines += [
        "",
        "## 💎 变现潜力榜",
        "",
        "| # | 项目 | Star | 变现分 | 语言 |",
        "|---:|---|---:|---:|---|",
    ]
    lines += [_md_row(r, i, with_growth=False) for i, r in enumerate(sections["money"], 1)]
    lines += [
        "",
        "---",
        "",
        "*变现分由名称 / 描述 / 话题标签的关键词启发式估算，仅供参考。数据来源：GitHub REST API。*",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML

_HTML_CSS = """
:root { --bg:#070b12; --panel:#0f1626; --border:rgba(148,163,184,.16); --text:#e6edf7; --muted:#93a2b8; --green:#34d399; --amber:#fbbf24; }
* { box-sizing: border-box; }
body { margin:0; padding:32px 20px 60px; background:radial-gradient(900px 450px at 10% -10%, rgba(56,189,248,.13), transparent 60%), var(--bg);
  color:var(--text); font-family:"Segoe UI","PingFang SC","Microsoft YaHei",system-ui,sans-serif; font-size:14px; line-height:1.55; }
.wrap { max-width: 1080px; margin: 0 auto; }
h1 { font-size:25px; margin:0 0 6px; background:linear-gradient(90deg,#e6edf7,#7dd3fc,#c4b5fd);
  -webkit-background-clip:text; background-clip:text; color:transparent; }
.meta { color:var(--muted); font-size:13px; margin-bottom:26px; }
h2 { font-size:17px; margin:34px 0 12px; }
table { width:100%; border-collapse:collapse; border:1px solid var(--border); border-radius:12px; overflow:hidden; background:rgba(15,22,38,.85); }
th { text-align:left; font-size:12px; letter-spacing:.5px; color:var(--muted); padding:11px 12px; border-bottom:1px solid var(--border); background:rgba(148,163,184,.05); }
td { padding:11px 12px; border-bottom:1px solid rgba(148,163,184,.07); vertical-align:top; }
tr:last-child td { border-bottom:none; }
a { color:#7dd3fc; text-decoration:none; }
a:hover { text-decoration:underline; }
.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.up { color:var(--green); font-weight:600; }
.star { color:#fde68a; font-weight:600; }
.dim { color:var(--muted); }
.score { color:var(--amber); font-weight:600; }
footer { margin-top:38px; color:#64748b; font-size:12px; }
"""


def _html_row(repo: dict, rank: int, *, growth: bool = True, extra: str = "") -> str:
    desc = (repo.get("description") or "").replace("<", "&lt;").replace(">", "&gt;")
    labels = _flag_labels(repo)
    flag_html = (
        f' <span class="dim" style="font-size:11px">{"·".join(labels)}</span>'
        if labels
        else ""
    )
    name = (
        f'<a href="{repo["url"]}" target="_blank" rel="noopener">{repo["full_name"]}</a>'
        f"{flag_html}"
        f'<div class="dim" style="font-size:12px">{desc[:160]}</div>'
    )
    cells = [
        f'<td class="num dim">{rank}</td>',
        f"<td>{name}</td>",
        f'<td class="num star">{_fmt(repo["stars"])}</td>',
    ]
    if growth:
        cells.append(f'<td class="num up">{_fmt_delta(repo.get("growth"))}</td>')
        cells.append(f'<td class="num up">{_fmt_delta(repo.get("per_day"))}/天</td>')
    cells.append(f'<td class="num score">{repo["money_score"]}</td>')
    cells.append(f'<td class="dim">{repo.get("language") or "—"}</td>')
    return "<tr>" + "".join(cells) + "</tr>"


def build_html(cfg: Config, conn) -> str:
    sections = collect_sections(cfg, conn)
    info = analytics.summary(conn, window_days=cfg.growth_window_days)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def table(rows: list[dict], *, growth: bool = True) -> str:
        head = ["#", "项目", "Star"]
        if growth:
            head += ["增长", "日均"]
        head += ["变现分", "语言"]
        thead = "".join(
            f'<th class="{"num" if h not in ("项目",) else ""}">{h}</th>' for h in head
        )
        body = "".join(_html_row(r, i, growth=growth) for i, r in enumerate(rows, 1))
        if not body:
            body = f'<tr><td colspan="{len(head)}" class="dim" style="text-align:center;padding:30px">暂无数据</td></tr>'
        return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GitHub 赚钱项目雷达 · {now}</title>
<style>{_HTML_CSS}</style></head>
<body><div class="wrap">
<h1>💰 GitHub 赚钱项目雷达 · 项目报告</h1>
<div class="meta">{now}　·　收录仓库 {info['repos']} 个　·　追踪 Star 总量 {_fmt(info['total_stars'])}　·　
近 {cfg.growth_window_days} 天新增 {_fmt(info['window_growth'])}</div>

<h2>🚀 增长最快（近 {cfg.growth_window_days} 天）</h2>
{table(sections['growth'])}

<h2>⭐ Star 最多</h2>
{table(sections['stars'])}

<h2>🆕 最新高潜（{cfg.new_within_days} 天内创建）</h2>
{table(sections['new'])}

<h2>💎 变现潜力榜</h2>
{table(sections['money'], growth=False)}

<footer>变现分为关键词启发式评分，仅供参考。数据来源：GitHub REST API。</footer>
</div></body></html>"""


def export(cfg: Config, conn, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    target = Path(out_dir) if out_dir else REPORTS_DIR
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    md_path = target / f"report_{stamp}.md"
    html_path = target / f"report_{stamp}.html"
    md_path.write_text(build_markdown(cfg, conn), encoding="utf-8")
    html_path.write_text(build_html(cfg, conn), encoding="utf-8")
    return md_path, html_path


def export_from_db(cfg: Config, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    conn = connect(cfg.db_path)
    try:
        init_db(conn)
        return export(cfg, conn, out_dir=out_dir)
    finally:
        conn.close()
