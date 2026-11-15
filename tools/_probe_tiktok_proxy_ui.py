"""离屏真点「自动探测」+ 校验状态格悬浮原因（一次性排查脚本，不入发版链）。

用途：客户反馈「能取到直播间但状态未知」，根因在代理探测。这里真点按钮，
确认 ① 点击不卡主线程 ② 探测失败时给的是可操作文案 ③ 状态格带 tooltip。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    _fd = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_fd):
        os.environ.setdefault("QT_QPA_FONTDIR", _fd)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication          # noqa: E402

from gui_qt.main_window import MainWindow           # noqa: E402
from gui_qt.pages._base import AppContext           # noqa: E402
from gui_qt.theme import apply_theme                # noqa: E402

OUT = ROOT / "samples"


def settle(app, ms=600):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def run(mode="light", click=True):
    app = QApplication.instance() or QApplication([])
    apply_theme(app, mode)
    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(1270, 900)
    win.show()
    settle(app, 700)

    # 切到直播监控页
    win.switch_to("直播监控")
    settle(app, 800)
    page = win.page("直播监控")
    assert page is not None, "没找到直播监控页"

    out = {}
    if click:
        t0 = time.time()
        page.detect_proxy_btn.click()               # 真点
        out["click_ms"] = round((time.time() - t0) * 1000)
        out["btn_disabled_right_after_click"] = not page.detect_proxy_btn.isEnabled()
        settle(app, 9000)                           # 等后台探测回来
        out["status"] = page.status.text()
        out["edit"] = page.tiktok_proxy_edit.text()
        out["btn_enabled_after"] = page.detect_proxy_btn.isEnabled()

    # 状态格 tooltip：造一行「状态未知」看能否带原因
    page._add_row("测试主播", "链接", "状态未知", "7708236...", "-", "-",
                  sec_uid="tiktok:test",
                  tip="状态检查失败：连不上 TikTok（本机没找到可用代理）")
    page._add_row("正常主播", "链接", "未开播", "123", "-", "-",
                  sec_uid="tiktok:ok2")
    settle(app, 500)
    r0, r1 = page.table.rowCount() - 2, page.table.rowCount() - 1
    out["tip_unknown"] = page.table.item(r0, 2).toolTip()
    out["tip_offline"] = page.table.item(r1, 2).toolTip()

    shot = OUT / f"直播监控页_TikTok代理探测_{'深色' if mode == 'dark' else '浅色'}.png"
    win.grab().save(str(shot))
    out["shot"] = str(shot)
    out["size"] = (win.width(), win.height())
    return out


if __name__ == "__main__":
    mode = os.environ.get("SHOT_THEME", "light")
    res = run(mode)
    for k, v in res.items():
        print(f"{k} = {v!r}")
