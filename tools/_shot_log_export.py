# -*- coding: utf-8 -*-
"""一键导出日志 UI 目检截图（2026-11-15）：
1) 主窗口侧栏底部「导出日志」常驻按钮（浅色）；
2) 直播页录制失败详情窗上的「📋 一键导出日志」按钮。

用法：python tools/_shot_log_export.py
输出：samples/一键导出日志_侧栏_浅色.png、samples/一键导出日志_失败窗_浅色.png
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    _fontdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_fontdir):
        os.environ.setdefault("QT_QPA_FONTDIR", _fontdir)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui_qt.main_window import MainWindow  # noqa: E402
from gui_qt.pages._base import AppContext  # noqa: E402
from gui_qt.theme import apply_theme  # noqa: E402

OUT = ROOT / "samples"


def _settle(app, ms=500):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    mode = os.environ.get("SHOT_THEME", "light").strip().lower()
    suffix = "_dark" if mode == "dark" else "_light"
    app = QApplication([])
    apply_theme(app, mode)
    ctx = AppContext()
    win = MainWindow(ctx)
    if mode == "dark":
        win._theme_mode = "light"      # 让 _toggle_theme 正好翻到 dark
        win._toggle_theme()
    win.resize(1180, 940)
    win.show()
    _settle(app, 700)
    OUT.mkdir(exist_ok=True)

    # 1) 侧栏常驻按钮（工作台页即可看到侧栏底部）
    win.grab().save(str(OUT / f"一键导出日志_侧栏{suffix}.png"))

    # 2) 直播页录制失败详情窗（走 _on_record_done 全失败分支）
    win.switch_to("直播监控")
    _settle(app, 500)
    live = win.page("直播监控")
    live._on_record_done([
        ("tiktok.com/@seller2024", False,
         "ffmpeg 报错：Connection timed out\n"
         "出口实测：给定代理 http://127.0.0.1:7897 拉流地址 2KB 读取失败（超时）\n"
         "判定：该节点对 tiktokcdn.com 分流规则为 DIRECT，拉流走直连被拒"),
        ("live.douyin.com/123456", False, "ffmpeg 报错：403 Forbidden"),
    ])
    _settle(app, 500)
    dlg = getattr(live, "_fail_box", None)
    if dlg is not None:
        dlg.grab().save(str(OUT / f"一键导出日志_失败窗_弹窗本体{suffix}.png"))
        # 展开详情后再截一张（验证详情区与按钮不重叠）
        try:
            dlg._toggle_detail()
            _settle(app, 300)
            dlg.grab().save(str(OUT / f"一键导出日志_失败窗_展开详情{suffix}.png"))
        except Exception:
            pass

    # 3) 成功导出弹窗（直接调 show_result_dialog，桩路径）
    from gui_qt.widgets.log_export import show_result_dialog
    ok = show_result_dialog(
        win, Path(r"C:\Users\客户\Desktop\流量捕手_诊断日志_日志_20261115_140755.zip"))
    _settle(app, 400)
    if ok is not None:
        ok.grab().save(str(OUT / f"一键导出日志_成功弹窗{suffix}.png"))

    print("DONE")


if __name__ == "__main__":
    main()
