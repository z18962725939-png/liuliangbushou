# -*- coding: utf-8 -*-
"""离屏探针：解析回调链是否抛异常导致状态卡在「正在解析」。

现象：界面一直显示「正在解析，请稍候…」，进度条 0%。
怀疑：done 槽（_on_resolved）内部抛异常 —— PySide6 只把 traceback 打到
stderr（打包后看不见），finished 仍会触发，于是按钮恢复、文案不更新。
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from gui_qt.pages.video_download_page import VideoDownloadPage  # noqa: E402

fails = []


def check(label, fn, *a, **kw):
    try:
        fn(*a, **kw)
        print(f"  [ OK ] {label}")
        return True
    except Exception:                                       # noqa: BLE001
        print(f"  [FAIL] {label}")
        traceback.print_exc()
        fails.append(label)
        return False


print("== 构造页面 ==")
page = VideoDownloadPage(None)

print("== _apply_save_dir ==")
page.dir_edit.setText(str(Path.cwd() / "_tmp_script_out" / "probe_ui_dl"))
check("_apply_save_dir", page._apply_save_dir)

print("== 解析成功回调 ==")
info = SimpleNamespace(platform="douyin", desc="探针标题", duration=32,
                       video_id="7345678901234567890")
check("_on_resolved(成功)", page._on_resolved, (info, ""))
print("    状态栏:", page.status.text())
print("    表格行数:", page.table.rowCount())

print("== 解析失败回调 ==")
check("_on_resolved(失败)", page._on_resolved, (None, "页面HTTP 403"))
print("    状态栏:", page.status.text())

print("== 收尾回调 ==")
check("_on_resolve_finished", page._on_resolve_finished)
print("    按钮:", page.resolve_btn.text(), "enabled=", page.resolve_btn.isEnabled())
print("    进度:", page.progress.minimum(), page.progress.maximum(),
      page.progress.value())

print("\n结果：", "全部通过" if not fails else f"{len(fails)} 项失败 -> {fails}")
sys.exit(1 if fails else 0)
