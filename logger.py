"""日志模块 - 轮转日志、结构化输出"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import LOG_BACKUP_COUNT, LOG_DIR, LOG_FILE, LOG_MAX_BYTES

# 阶段标签，便于一眼定位
TAG_MAIN = "MAIN"
TAG_LOGIN = "LOGIN"
TAG_ORDER_LIST = "ORDER_LIST"
TAG_CREATE_ORDER = "CREATE_ORDER"
TAG_DETAIL = "DETAIL"
TAG_ECOMMERCE = "ECOMMERCE"
TAG_PEOPLE = "PEOPLE"


def _setup_logger() -> logging.Logger:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("weixin_jiare")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    fmt = "%(asctime)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, date_fmt))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(fmt, date_fmt))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


_logger = _setup_logger()


def log(tag: str, msg: str, level: int = logging.INFO, **kwargs) -> None:
    """结构化日志：tag | msg | 可选 extra"""
    extra_str = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
    full_msg = f"[{tag}] {msg}" + (f" | {extra_str}" if extra_str else "")
    _logger.log(level, full_msg)


def log_step(tag: str, step: str, status: str = "ok", detail: str = "") -> None:
    """步骤日志：step | status | detail"""
    parts = [f"[{tag}]", step, status]
    if detail:
        parts.append(detail)
    _logger.info(" | ".join(parts))


def log_order(tag: str, idx: int, total: int, pid: str, status: str, detail: str = "") -> None:
    """订单级日志：[idx/total] pid | status | detail"""
    log(tag, f"[{idx}/{total}] {pid} | {status}" + (f" | {detail}" if detail else ""))


def log_error(tag: str, msg: str, exc: Exception | None = None) -> None:
    """错误日志"""
    full = f"{msg}" + (f" | error={exc!r}" if exc else "")
    _logger.error(f"[{tag}] {full}", exc_info=exc is not None)
