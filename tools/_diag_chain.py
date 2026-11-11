# -*- coding: utf-8 -*-
"""验证「拿到 aweme_id 之后」的链路是否健康：API 详情 / 分享页 HTML 提取 / 短链兜底。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

CFG = Path(r"C:\Users\Administrator\AppData\Roaming\TrafficCatcher\config.json")
cookie = (json.loads(CFG.read_text(encoding="utf-8")).get("douyin_cookie") or "").strip()

from justoneapi_collector.client import JustOneAPICollector  # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

client = JustOneAPICollector()
dl = VideoDownloader(client, cookie=cookie)

# ── 1. 用 API 搜一个真实作品，拿 aweme_id ─────────────────────────────
print("[1] API 搜索「美食」拿真实作品")
vids = dl.search_videos("美食", count=3)
for v in vids:
    print(f"    {v.video_id}  {v.title[:28]}  作者={getattr(v, 'author_name', '?')}")
if not vids:
    print("    搜索失败，终止")
    sys.exit(1)
vid = vids[0].video_id
print(f"    选中 video_id = {vid}")

# ── 2. API 详情接口 ───────────────────────────────────────────────────
print("\n[2] get_video_detail(API)")
d = dl.get_video_detail(vid)
print("    结果:", "✅ 成功" if d else "❌ 失败")
if d:
    print("    标题:", (d.title or "")[:40])
    print("    播放地址:", (d.no_watermark_url or "")[:80] or "<空>")
    print("    时长:", getattr(d, "duration", "?"))

# ── 3. 分享页 HTML 提取（iesdouyin 分享链）────────────────────────────
print("\n[3] 分享页 HTML 提取 https://www.iesdouyin.com/share/video/{id}/")
import httpx  # noqa: E402

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
for host in (f"https://www.iesdouyin.com/share/video/{vid}/",
             f"https://www.douyin.com/video/{vid}"):
    try:
        with httpx.Client(follow_redirects=True, timeout=20,
                          headers={"User-Agent": UA, "Referer": "https://www.douyin.com/",
                                   "Cookie": cookie}) as cx:
            r = cx.get(host)
        print(f"    {host[:52]} → HTTP {r.status_code}, {len(r.text)} 字节, 最终 {str(r.url)[:60]}")
        got = dl._extract_video_from_html(r.text)
        print("        HTML 提取:", "✅" if got else "❌",
              (got.no_watermark_url or "")[:70] if got else "")
    except Exception as e:  # noqa: BLE001
        print(f"    {host[:52]} → 异常 {type(e).__name__}: {e}")

# ── 4. CDP 浏览器兜底能力探测 ─────────────────────────────────────────
print("\n[4] CDP 浏览器兜底（探测浏览器是否可用）")
try:
    from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler, find_browser  # noqa: E402

    found = find_browser()
    print("    找到浏览器:", found)
    if found:
        crawler = DouyinCDPCrawler(
            profile_dir=str(Path(os.environ.get("APPDATA", "")) / "TrafficCatcher" / "browser_profile"),
            headless=False, timeout=30)
        ok, msg = crawler.start()
        print("    启动:", ok, msg)
        if ok:
            url = crawler._page.call("Runtime.evaluate", {
                "expression": "JSON.stringify({h: location.href})",
                "returnByValue": True}, timeout=8)
            print("    当前页面:", val := (url.get("result") or {}).get("value"))
            crawler.close()
except Exception as e:  # noqa: BLE001
    print("    CDP 异常:", type(e).__name__, e)
