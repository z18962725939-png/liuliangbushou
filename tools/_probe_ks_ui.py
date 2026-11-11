# -*- coding: utf-8 -*-
"""离屏验证：快手账号作品批量采集的 UI 行为（2026-11-05）。

场景：
  1) 平台切快手 → 喜欢/收藏灰掉、浏览器会话灰掉；切回抖音恢复
  2) 快手主页链接贴「解析」框 → 自动转交「拉取列表」且平台切到快手
  3) 快手单条作品链接失败 → 不误转
  4) 免费版快手主页 → 只填链接不开跑、不弹付费窗
  5) 快手「拉取列表」真跑一条假下载器链路 → 表格出现行、状态栏带 note
"""
import io
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\trafficcatcher-src\justoneapi-collector")

from PySide6.QtWidgets import QApplication

from gui_qt.pages.video_download_page import VideoDownloadPage
from justoneapi_collector.video_downloader import VideoInfo

app = QApplication.instance() or QApplication(sys.argv)
page = VideoDownloadPage(ctx=None)

KS_PROFILE = "https://v.kuaishou.com/JTs3J28b"
KS_VIDEO = "https://www.kuaishou.com/f/X-9cx7YglxFt11o9"

print("=" * 62)

# ---- 场景 1：平台切换的选项联动 ----
ks_idx = next(i for i in range(page.blogger_platform_combo.count())
              if page.blogger_platform_combo.itemData(i) == "kuaishou")
page.blogger_platform_combo.setCurrentIndex(ks_idx)
model = page.list_type_combo.model()
fav_ok = model.item(1).isEnabled()
col_ok = model.item(2).isEnabled()
print("[1] 切快手: 列表类型=", page.list_type_combo.currentData(),
      "| 喜欢可选=", "❌" if fav_ok else "✅灰",
      "| 收藏可选=", "❌" if col_ok else "✅灰",
      "| 浏览器勾选框=", "✅灰" if not page.use_browser_chk.isEnabled() else "❌还开着")
page.blogger_platform_combo.setCurrentIndex(0)
print("    切回抖音: 喜欢=", "✅恢复" if model.item(1).isEnabled() else "❌",
      "| 浏览器=", "✅恢复" if page.use_browser_chk.isEnabled() else "❌")

# ---- 场景 2：快手主页链接 → 自动转交 ----
calls = []
page._fetch_list = lambda: calls.append("fetch_list")
page._prompt_paid_feature = lambda *a, **k: calls.append(("paid", a[:1]))
page._free_mode = lambda: False
page.downloader._last_blogger_sec_uid = ""
page._pending_blogger_url = ""
page.url_edit.setText(KS_PROFILE)
page._on_resolved((None, "链接不可访问"))
page._on_resolve_finished()
print("[2] 主页转交: fetch=", "✅" if "fetch_list" in calls else "❌",
      "| 平台自动切快手=", "✅" if page._blogger_platform() == "kuaishou" else "❌",
      "| list_url=", page.list_url_edit.text()[:40])

# ---- 场景 3：快手单条作品链接失败 → 不误转 ----
calls.clear()
page._pending_blogger_url = ""
page.url_edit.setText(KS_VIDEO)
page._on_resolved((None, "链接不可访问"))
page._on_resolve_finished()
print("[3] 作品链接失败: fetch=", "✅未误转" if "fetch_list" not in calls else "❌误转",
      "| 状态栏=", page.status.text()[:36])

# ---- 场景 4：免费版快手主页 ----
calls.clear()
page._free_mode = lambda: True
page.url_edit.setText(KS_PROFILE)
page._pending_blogger_url = ""
page._on_resolved((None, "链接不可访问"))
page._on_resolve_finished()
print("[4] 免费版: fetch=", "✅未开跑" if "fetch_list" not in calls else "❌",
      "| 弹付费窗=", "✅未弹" if not any(isinstance(c, tuple) for c in calls) else "❌",
      "| list_url已填=", "✅" if page.list_url_edit.text() else "❌")
page._free_mode = lambda: False

# ---- 场景 5：快手「拉取列表」真跑（假下载器，验证线程与表格） ----
class _StubDL:
    client = object()          # 非 _MockClient ⇒ _ensure_real_downloader 通过
    save_dir = r"D:\流量捕手\下载"
    last_save_dir_used = r"D:\流量捕手\下载"
    last_dir_note = ""
    _last_blogger_sec_uid = ""
    _last_list_note = "快手作品 2 条"
    def list_kuaishou_blogger_videos(self, url, max_pages=30, on_progress=None):
        if on_progress:
            on_progress(2)
        v1 = VideoInfo(video_id="5227553480423460795", desc="快手测试视频一",
                       author="佳佳子。", no_watermark_url="https://x/a.mp4",
                       share_url="https://www.kuaishou.com/short-video/1", platform="kuaishou")
        v2 = VideoInfo(video_id="5227553480423460796", desc="快手测试视频二",
                       author="佳佳子。", no_watermark_url="",
                       share_url="https://www.kuaishou.com/short-video/2", platform="kuaishou")
        return [v1, v2], ""

page._fetch_list = type(page)._fetch_list.__get__(page)   # 还原真方法
page.downloader = _StubDL()
page._free_mode = lambda: False
page.blogger_platform_combo.setCurrentIndex(ks_idx)
page.list_url_edit.setText(KS_PROFILE)
page._fetch_list()
t0 = time.time()
# QThread 启动有短暂延迟，isRunning() 可能在 start() 之后仍为 False ——
# 以「收尾回调把按钮文字改回『拉取列表』」为准，别用 isRunning() 当条件。
while time.time() - t0 < 12:
    app.processEvents()
    if page.fetch_list_btn.text() == "拉取列表":
        break
    time.sleep(0.05)
print("    worker=", "running" if page._list_worker.isRunning() else "finished",
      "| 耗时=%.2fs" % (time.time() - t0))
rows = page.table.rowCount()
print(f"[5] 假链路跑完: 表格行数={rows}",
      "| 状态栏=", page.status.text()[:44],
      "| 按钮恢复=", page.fetch_list_btn.text())
plat_col = [page.table.item(r, 1).text() if page.table.item(r, 1) else "?" for r in range(rows)]
print("    平台列 =", plat_col)

print("=" * 62)
page.close()
