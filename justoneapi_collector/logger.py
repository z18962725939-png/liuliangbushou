# -*- coding: utf-8 -*-
"""日志系统：控制台输出 + 轮转文件（%APPDATA%\\<品牌>\\logs\\app.log）。

为什么要强制落文件
------------------
打包成 exe（--noconsole）后 stdout 是空的，只挂 StreamHandler 等于把日志
扔进黑洞 —— 客户一报错，我们手里什么都没有。所以这里【永远】写文件，并做
大小轮转：既不撑爆磁盘，又保证「一键发日志」能拿到最近的现场。

设计约束
--------
· 只能依赖标准库 + branding（branding 本身只依赖标准库，不会循环导入）。
· setup_logging 保持在首次调用时生效（幂等），签名向后兼容：
      setup_logging(level="INFO", log_file=None)
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "justoneapi_collector"
_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

# 单个文件 5 MB，最多留 5 份历史（够定位问题，又不会越积越多）
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5

LOG_DIR_NAME = "logs"
LOG_FILENAME = "app.log"

_configured = False
_active_file: Path | None = None


# ----------------------------------------------------------------------
#  路径
# ----------------------------------------------------------------------

def log_dir() -> Path:
    """日志目录：优先环境变量 JUSTONEAPI_LOG_DIR，否则 %APPDATA%\\<品牌>\\logs。"""
    env = os.environ.get("JUSTONEAPI_LOG_DIR")
    if env:
        return Path(env)
    try:
        from . import branding                     # 延迟导入，规避循环导入
        return Path(branding.app_data_dir()) / LOG_DIR_NAME
    except Exception:                              # noqa: BLE001
        return Path.home() / "TrafficCatcher" / LOG_DIR_NAME


def log_file_path() -> Path:
    """日志文件完整路径（环境变量可覆盖，测试用得上）。"""
    env = os.environ.get("JUSTONEAPI_LOG_FILE")
    if env:
        return Path(env)
    return log_dir() / LOG_FILENAME


def active_log_file() -> Path | None:
    """当前真正在写的日志文件（没配成功时为 None）。"""
    return _active_file


# ----------------------------------------------------------------------
#  配置
# ----------------------------------------------------------------------

def _has_console() -> bool:
    """是否存在可用的控制台（exe 无控制台时不加 StreamHandler）。"""
    if getattr(sys, "frozen", False):
        return False
    try:
        return sys.stderr is not None
    except Exception:                              # noqa: BLE001
        return False


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """配置根日志。多次调用安全（只有第一次生效）。"""
    global _configured, _active_file
    if _configured:
        return

    root = logging.getLogger(_LOGGER_NAME)
    root.setLevel(getattr(logging, str(level or "INFO").upper(), logging.INFO))
    root.propagate = False
    fmt = logging.Formatter(_DEFAULT_FORMAT)

    # 1) 文件处理器 —— 永远挂（exe 里就靠它）
    target = Path(log_file) if log_file else log_file_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            target, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
        _active_file = target
    except Exception:                              # noqa: BLE001
        _active_file = None

    # 2) 控制台处理器 —— 只有真控制台才加
    if _has_console():
        try:
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            root.addHandler(sh)
        except Exception:                          # noqa: BLE001
            pass

    _configured = True


def get_logger(name: str = "justoneapi_collector") -> logging.Logger:
    if not _configured:
        setup_logging()
    return logging.getLogger(name)


# ----------------------------------------------------------------------
#  崩溃捕获（让未处理异常也进日志）
# ----------------------------------------------------------------------

def install_crash_hooks(on_error=None) -> None:
    """把未捕获异常写进日志。

    on_error(exc_type, exc_value, exc_tb)：可选回调（比如弹窗提示用户
    「已记录，请点一键发日志」）。
    """
    log = get_logger("crash")

    def _hook(exc_type, exc_value, exc_tb):
        try:
            log.error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
        except Exception:                          # noqa: BLE001
            pass
        if on_error is not None:
            try:
                on_error(exc_type, exc_value, exc_tb)
            except Exception:                      # noqa: BLE001
                pass

    try:
        sys.excepthook = _hook
    except Exception:                              # noqa: BLE001
        pass
