# -*- coding: utf-8 -*-
"""免费 / 试用无 Token 的可用性探针（v1.14.2）。

背景：v1.14.0 免费版装完点解析就弹「请填 API Token」；v1.14.1 只修了
「免费档」这一种状态，客户第二次反馈**还是不行** —— 因为他的机器是
**试用期（state=trial）且没配 Token**，按 is_free_tier 判断依旧被拦。

本探针把三种状态都跑一遍：
  A 免费档（未激活、非试用）
  B 试用期 + 无 Token（客户实际状态）
  C 改系统时间作弊（tamper>=3）—— 这个必须仍然拦住

跑法：C:/Python310/python.exe tools/_probe_free_tier.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from justoneapi_collector import licensing        # noqa: E402

_ORIG = licensing.status


def fake(state: str, licensed: bool = False, tamper: int = 0) -> None:
    licensing.status = lambda *a, **k: {
        "state": state, "label": "", "licensed": licensed, "days_left": 0,
        "customer": "", "expires_at": 0, "machine_id": "AAAA-BBBB-CCCC-DDDD",
        "features": [], "embedded_token": "", "tamper": tamper,
        "trial_start": 0.0, "message": "",
        "daily_quota": licensing.FREE_DAILY_QUOTA, "plan": "",
    }


from PySide6.QtWidgets import QApplication            # noqa: E402
from gui_qt.pages._base import AppContext             # noqa: E402
from gui_qt.pages.video_download_page import VideoDownloadPage   # noqa: E402
from gui_qt.pages.live_page import LivePage           # noqa: E402

app = QApplication.instance() or QApplication([])

for label, kwargs in [
    ("A 免费档", dict(state="free")),
    ("B 试用期 + 无 Token", dict(state="trial")),
    ("C 改过系统时间（tamper=3）", dict(state="expired", tamper=3)),
]:
    fake(**kwargs)
    ctx = AppContext()          # client 恒为 None：模拟没有任何 Token 的机器
    ctx.client = None
    dl = ctx.ensure_downloader()
    dm = ctx.ensure_monitor()
    page = VideoDownloadPage(ctx=ctx)
    live = LivePage(ctx=ctx) if dm is not None else None
    print(f"\n== {label} ==")
    print("   授权档           :", licensing.status()["state"])
    print("   ensure_downloader:", type(dl).__name__ if dl else "None",
          "| client =", type(getattr(dl, 'client', None)).__name__ if dl else "-")
    print("   ensure_monitor   :", type(dm).__name__ if dm else "None")
    print("   _free_mode()     :", page._free_mode())
    print("   _ensure_ready()  :", page._ensure_ready())
    print("   下载表格行数      :", page.table.rowCount(), "(0 = 不铺演示行)")
    print("   状态栏           :", page.status.text())
    if live is not None:
        print("   直播表格行数      :", live.table.rowCount(), "(0 = 不铺假目标)")

print("\n== 抖音链接实测（用试用态，模拟客户机器）==")
fake(state="trial")
ctx = AppContext()
ctx.client = None
dl = ctx.ensure_downloader()
T0 = time.time()
info, reason = dl.resolve_share_link_with_reason("https://v.douyin.com/RHuqE7uZNJo/")
cost = time.time() - T0
if info:
    print(f"   [OK] {cost:.1f}s 解析成功：{(info.desc or '')[:36]}  id={info.video_id}")
    print(f"        无水印播放地址：{'有' if info.no_watermark_url else '无'}")
else:
    print(f"   [未命中] {cost:.1f}s  原因：{reason}")

licensing.status = _ORIG
print("\n完成。")
