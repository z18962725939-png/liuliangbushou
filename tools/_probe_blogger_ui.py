# -*- coding: utf-8 -*-
"""离屏验证：点「解析」贴主页链接 → 是否自动转成「拉取列表」。

不启动真浏览器、不弹任何窗（_fetch_list 被替换成记录器）。
"""
import os, sys, io
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac100ef13e78468d54cea96\justoneapi-collector")

from PySide6.QtWidgets import QApplication
from gui_qt.pages.video_download_page import VideoDownloadPage

SEC = "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp"
SHORT = "长按复制此条消息，打开抖音搜索，查看TA的更多作品。 https://v.douyin.com/GfePy6r3aYY/"

app = QApplication.instance() or QApplication(sys.argv)
page = VideoDownloadPage(ctx=None)

calls = []
page._fetch_list = lambda: calls.append("fetch_list")
page._prompt_paid_feature = lambda *a, **k: calls.append(("paid_prompt", a[:1]))

print("=" * 62)

# ---- 场景 1：完整版（非免费档）贴主页链接 ----
page._free_mode = lambda: False
page.downloader._last_blogger_sec_uid = SEC
page.url_edit.setText(SHORT)
page._on_resolved((None, "这是博主主页链接…"))
print("[1] 解析回调后 pending =", repr(page._pending_blogger_url[:50]))
print("    状态栏 =", page.status.text())
page._on_resolve_finished()
print("    list_url_edit =", page.list_url_edit.text())
print("    自动拉列表 =", "✅ 已触发" if "fetch_list" in calls else "❌ 未触发")
print("    列表类型 =", page.list_type_combo.currentData(),
      "| 浏览器抓取 =", page.use_browser_chk.isChecked())

# ---- 场景 2：免费版（不能自动开跑，但必须把链接填好并说明） ----
calls.clear()
page._free_mode = lambda: True
page.downloader._last_blogger_sec_uid = SEC
page.url_edit.setText(SHORT)
page._pending_blogger_url = ""
page._on_resolved((None, "这是博主主页链接…"))
page._on_resolve_finished()
print("[2] 免费版 自动拉列表 =", "❌ 不该触发" if "fetch_list" in calls else "✅ 未触发")
print("    弹付费窗 =", "❌ 不该弹" if any(isinstance(c, tuple) for c in calls) else "✅ 未弹")
print("    list_url_edit =", page.list_url_edit.text())
print("    状态栏 =", page.status.text())

# ---- 场景 3：正常作品链接的失败，仍走原来的报错（不能误转） ----
calls.clear()
page._free_mode = lambda: False
page.downloader._last_blogger_sec_uid = ""
page._pending_blogger_url = ""
page.url_edit.setText("https://www.douyin.com/video/7696734238402222720")
page._on_resolved((None, "这条链接打不开了 —— 请在抖音里重新复制一次"))
page._on_resolve_finished()
print("[3] 作品链接失败 自动拉列表 =", "❌ 误转" if "fetch_list" in calls else "✅ 未误转")
print("    状态栏 =", page.status.text())

print("=" * 62)
page.close()
