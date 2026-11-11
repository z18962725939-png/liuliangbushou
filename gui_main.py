"""
Entry point for the Just One API data collection GUI application.

Launches the PySide6 desktop interface for operations staff.
Compatible with both development mode and PyInstaller-frozen (EXE) mode.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _setup_frozen_env():
    """Adjust environment for PyInstaller frozen builds."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys._MEIPASS to the temp extraction dir.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            os.environ.setdefault("JUSTONEAPI_BUNDLE_DIR", meipass)
        # Onedir mode: the exe's parent dir is where the user runs from.
        exe_dir = Path(sys.executable).parent
        os.environ.setdefault("JUSTONEAPI_EXE_DIR", str(exe_dir))


def main():
    _setup_frozen_env()
    # ★ 打包后没有控制台，未捕获异常（含 Qt 槽里抛的）会彻底消失：
    #   客户只看到「一直解析中」，我们这边连堆栈都没有。
    #   2026-10-18 的「一直显示解析中」就是这种情况。这里把日志与崩溃钩子
    #   真正装上（以前只写了函数，没人调用，app.log 一直是 0 字节）。
    try:
        from justoneapi_collector.logger import install_crash_hooks, setup_logging
        setup_logging()
        install_crash_hooks()
    except Exception:                                # noqa: BLE001
        pass

    from gui_qt.app import run

    run()


if __name__ == "__main__":
    main()
