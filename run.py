#!/usr/bin/env python
"""GitHub 赚钱项目雷达 · 命令行入口。

用法示例：
    python run.py collect            # 采集一次
    python run.py watch              # 定时采集（默认每 6 小时）
    python run.py top                # 终端查看榜单
    python run.py digest             # 生成并推送日报
    python run.py report             # 导出 Markdown / HTML 报告
    python run.py web                # 启动 Web 仪表盘
    python run.py demo               # 用演示数据启动仪表盘
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from moneyhunter import __version__, analytics, console, db, notifier, reports
from moneyhunter.collector import Collector
from moneyhunter.config import (
    PROJECT_ROOT,
    Config,
    ensure_config_file,
    load_config,
)
from moneyhunter.logsetup import get_logger, setup_logging


def _connect(cfg: Config):
    conn = db.connect(cfg.db_path)
    db.init_db(conn)
    return conn


def _print_quick_top(cfg: Config, limit: int = 8) -> None:
    conn = _connect(cfg)
    try:
        rows = analytics.growth_rows(conn, window_days=cfg.growth_window_days, limit=limit)
        if not rows:
            console.info("暂无增长数据：需要至少两次不同时间的采集才能计算增长，过段时间再采集一次即可。")
            return
        rich_console = console.console()
        try:
            from rich.table import Table
        except ImportError:  # pragma: no cover
            Table = None  # type: ignore[assignment]
        if rich_console is None or Table is None:  # pragma: no cover
            for i, repo in enumerate(rows, 1):
                console.say(f"{i}. {repo['full_name']}  +{repo['growth']} 星")
            return
        table = Table(
            title=f"近 {cfg.growth_window_days} 天增长最快（Top {limit}）",
            title_justify="left",
            pad_edge=False,
        )
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("项目")
        table.add_column("Star", justify="right", style="bold yellow")
        table.add_column("增长", justify="right", style="green")
        table.add_column("变现", justify="right")
        for i, repo in enumerate(rows, 1):
            table.add_row(
                str(i),
                f"[link={repo['url']}]{repo['full_name']}[/link]",
                f"{repo['stars']:,}",
                f"+{repo['growth']:,}",
                str(repo["money_score"]),
            )
        rich_console.print(table)
    finally:
        conn.close()


# ---------------------------------------------------------------- 子命令


def cmd_collect(args, cfg: Config) -> int:
    collector = Collector(cfg)
    try:
        result = collector.run(max_queries=args.max_queries, include_new=not args.no_new)
    except KeyboardInterrupt:
        console.warn("已中断采集。")
        return 130
    if not result.ok:
        return 1
    console.say()
    _print_quick_top(cfg, limit=args.top)
    if getattr(args, "notify", False):
        if not cfg.notify_enabled:
            console.warn("未配置 notify（config.yaml 的 notify.type），无法推送日报。")
        else:
            ok, detail = notifier.notify_digest(cfg)
            (console.ok if ok else console.error)(f"日报推送：{detail}")
    return 0


def cmd_watch(args, cfg: Config) -> int:
    interval_minutes = args.interval or cfg.interval_minutes
    console.info(
        f"定时采集已启动：每 {interval_minutes} 分钟运行一次（Ctrl+C 退出），数据库 {cfg.db_path}"
    )
    while True:
        started = time.monotonic()
        try:
            result = Collector(cfg).run(max_queries=args.max_queries, include_new=not args.no_new)
            if result.ok and cfg.notify_enabled:
                ok, detail = notifier.notify_digest(cfg)
                (console.ok if ok else console.warn)(f"日报推送：{detail}")
        except KeyboardInterrupt:
            console.warn("已停止定时采集。")
            return 0
        except Exception as exc:  # noqa: BLE001 - 定时任务不能因单次失败退出
            console.error(f"本轮采集失败：{type(exc).__name__}: {exc}")

        next_run = datetime.now() + timedelta(minutes=interval_minutes)
        console.info(f"下一轮采集时间：{next_run.strftime('%Y-%m-%d %H:%M')}")

        elapsed = time.monotonic() - started
        remaining = max(1.0, interval_minutes * 60 - elapsed)
        try:
            # 分段休眠，方便 Ctrl+C 及时响应
            while remaining > 0:
                step = min(5.0, remaining)
                time.sleep(step)
                remaining -= step
        except KeyboardInterrupt:
            console.warn("已停止定时采集。")
            return 0


def cmd_digest(args, cfg: Config) -> int:
    window = args.days or cfg.digest_window_days
    if args.dry_run or not cfg.notify_enabled:
        ok, detail = notifier.notify_digest(cfg, dry_run=True, window_days=window)
        if not cfg.notify_enabled:
            console.info("未配置 notify.type（config.yaml），以上内容仅打印，未推送。")
        return 0 if ok else 1
    ok, detail = notifier.notify_digest(cfg, window_days=window)
    (console.ok if ok else console.error)(f"日报推送（{cfg.notify_type}）：{detail}")
    return 0 if ok else 1


def cmd_top(args, cfg: Config) -> int:
    conn = _connect(cfg)
    try:
        reports.print_console(cfg, conn, limit=args.limit)
    finally:
        conn.close()
    return 0


def cmd_report(args, cfg: Config) -> int:
    md_path, html_path = reports.export_from_db(cfg)
    console.ok(f"报告已生成:\n  {md_path}\n  {html_path}")
    return 0


def cmd_stats(args, cfg: Config) -> int:
    conn = _connect(cfg)
    try:
        info = analytics.summary(conn, window_days=cfg.growth_window_days)
        console.say(f"数据库: {cfg.db_path}")
        console.say(f"收录仓库: {info['repos']}    快照: {info['snapshots']}    采集次数: {info['runs']}")
        console.say(f"追踪 Star 总量: {info['total_stars']:,}")
        console.say(
            f"近 {info['window_days']} 天新增 Star: {info['window_growth']:,}"
            f"    24h 新收录: {info['new_repos_24h']}"
        )
        console.say(
            f"质量标记: 精选列表 {info['flagged_awesome']} · "
            f"久未更新 {info['flagged_inactive']} · "
            f"疑似刷星 {info['flagged_suspect']}"
        )
        console.say(f"上次采集: {info['last_run_at'] or '从未'}（{info['last_run_status'] or '-'}）")
        if info["last_run_message"]:
            console.warn(f"上次采集信息: {info['last_run_message'][:300]}")
        console.say()
        console.info("最近 5 次采集记录：")
        for run in analytics.runs_history(conn, limit=5):
            console.say(
                f"  #{run['id']}  {run['started_at']}  {run['status']}  "
                f"条件 {run['queries_ok']}/{run['queries_total']}  "
                f"收录 {run['repos_seen']}（新增 {run['repos_new']}）"
            )
    finally:
        conn.close()
    return 0


def cmd_web(args, cfg: Config) -> int:
    from moneyhunter.web import serve

    serve(cfg, host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def cmd_demo(args, cfg: Config) -> int:
    from moneyhunter import demo
    from moneyhunter.web import serve

    demo_db = Path(args.db) if args.db else (PROJECT_ROOT / "data" / "demo.db")
    info = demo.seed(demo_db)
    console.ok(f"已生成演示数据：{info['repos']} 个仓库 -> {info['path']}")
    console.info("演示数据为合成数据，仅用于预览界面。")
    demo_cfg = dataclasses.replace(cfg, db_path=demo_db)
    if args.seed_only:
        return 0
    serve(demo_cfg, host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


# ---------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python run.py",
        description="GitHub 赚钱项目雷达：自动搜集 AI / 软件 / 硬件领域最值得关注的开源项目。",
    )
    parser.add_argument("--version", action="version", version=f"GitHub Money Hunter {__version__}")
    parser.add_argument("--config", help="配置文件路径（默认 config.yaml）")
    parser.add_argument("--db", help="数据库路径（默认取配置文件中的 db_path）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="创建配置文件")
    p.set_defaults(func=None)

    p = sub.add_parser("collect", help="采集一次数据")
    p.add_argument("--max-queries", type=int, default=None, help="最多执行多少个搜索条件（调试用）")
    p.add_argument("--no-new", action="store_true", help="跳过“最新项目”搜索")
    p.add_argument("--top", type=int, default=8, help="采集完成后展示的增长榜条数")
    p.add_argument("--notify", action="store_true", help="采集完成后推送日报（需配置 notify）")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("watch", help="定时采集（默认每 interval_minutes 分钟一次）")
    p.add_argument("--interval", type=int, default=None, help="采集间隔（分钟）")
    p.add_argument("--max-queries", type=int, default=None)
    p.add_argument("--no-new", action="store_true")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("top", help="在终端查看榜单")
    p.add_argument("--limit", type=int, default=10, help="每个榜单展示条数")
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("report", help="导出 Markdown / HTML 报告")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("digest", help="生成日报并推送（未配置 notify 时仅打印）")
    p.add_argument("--days", type=int, default=None, help="统计窗口天数（默认 notify.digest_window_days）")
    p.add_argument("--dry-run", action="store_true", help="只打印内容，不推送")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("stats", help="查看数据库统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("web", help="启动 Web 仪表盘")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("demo", help="用演示数据启动 Web 仪表盘")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--seed-only", action="store_true", help="只生成演示数据，不启动服务")
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        path, created = ensure_config_file()
        if created:
            console.ok(f"已创建配置文件: {path}")
            console.info("请填入 GitHub token（或设置环境变量 GITHUB_TOKEN），然后运行 python run.py collect")
        else:
            console.info(f"配置文件已存在: {path}")
        return 0

    ensure_config_file()
    cfg = load_config(args.config, db_override=args.db)
    log_path = setup_logging(cfg)
    logger = get_logger()
    logger.info("command=%s db=%s", args.command, cfg.db_path)
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        console.warn("已退出。")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.exception("command %s failed", args.command)
        console.error(f"出错了：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
