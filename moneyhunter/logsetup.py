"""日志轮转配置：采集与 Web 日志写入 data/logs，按天轮转。

调用示例（run.py 启动入口中）：
    from moneyhunter.logsetup import setup_logging
    setup_logging(cfg)
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import Config

LOGGER_NAME = "moneyhunter"


def default_log_dir(cfg: Config) -> Path:
    """日志目录：与数据库同级的 data/logs 目录。"""
    return Path(cfg.db_path).parent / "logs"


def setup_logging(cfg: Config, level: int = logging.INFO) -> Path:
    """初始化按天轮转的文件日志，返回日志文件路径。

    多次调用不会重复添加同一文件 handler；INFO 级别写入文件，并把
    WARNING 级以上也镜像到控制台（便于直接运行时看到实时输出）。
    """
    log_dir = default_log_dir(cfg)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "money_hunter.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)

    # 避免重复添加同一文件 handler（run 入口或测试可能多次调用）
    already = any(
        isinstance(h, logging.handlers.TimedRotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")) == log_file
        for h in logger.handlers
    )
    if not already:
        handler = logging.handlers.TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=max(1, cfg.log_backup_days),
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    # 控制台镜像错误与警告，供直接前台运行时观察
    if not any(
        isinstance(h, logging.StreamHandler)
        and getattr(h.stream, "name", None) == "<stderr>"
        for h in logger.handlers
    ):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(console_handler)

    logging.getLogger("flask").setLevel(logging.WARNING)
    logging.getLogger("waitress").setLevel(logging.WARNING)

    return log_file


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)
