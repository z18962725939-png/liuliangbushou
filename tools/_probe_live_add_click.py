# -*- coding: utf-8 -*-
"""临时探针：真点「添加监控」按钮复现客户"无反应"（用完即删）。"""
import io
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\trafficcatcher-src\justoneapi-collector")

from PySide6.QtWidgets import QApplication, QPushButton
from gui_qt.theme import apply_theme
from gui_qt.pages._base import AppContext
from gui_qt.pages.live_page import LivePage

app = QApplication([])
apply_theme(app, "light")


class StubDaemon:
    def __init__(self):
        self.calls = []
    def add_target_by_link(self, url, note=""):
        self.calls.append(("link", url, note))
        return True, "测试主播", "web_rid:123456789"
    def add_target(self, *a, **k):
        self.calls.append(("uid", a, k))
    def check_once(self, sec_uid):
        return None
    def ffmpeg_path(self):
        return "C:/ffmpeg/bin/ffmpeg.exe"
    def is_recording(self, *a):
        return False
    def get_targets(self):
        return []


def pump(seconds=2.0):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


for label, with_daemon in (("有真实引擎", True), ("演示模式(无引擎)", False)):
    ctx = AppContext()
    ctx.daemon = StubDaemon() if with_daemon else None
    if not with_daemon:
        ctx.ensure_monitor = lambda: None
    page = LivePage(ctx)
    page.table.setRowCount(0)
    btns = [b for b in page.findChildren(QPushButton) if b.text() == "添加监控"]
    print(f"=== {label}: 找到按钮={len(btns)} 可用={btns[0].isEnabled() if btns else '-'}")
    if not btns:
        continue
    page.input.setText("https://live.douyin.com/123456789")
    page.note_input.setText("测试备注")
    btns[0].click()
    print("   点击后立刻: 行数=", page.table.rowCount(), "| 状态=", page.status.text()[:50])
    pump(3.0)
    print("   等 3 秒后: 行数=", page.table.rowCount(), "| 状态=", page.status.text()[:60])
    if page.table.rowCount():
        print("   首行=", [page.table.item(0, c).text() if page.table.item(0, c) else "" for c in range(3)])
    print("   引擎调用=", ctx.daemon.calls if ctx.daemon else "-")
    page.close()

print("[done]")
