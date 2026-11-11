# -*- coding: utf-8 -*-
"""回归验证：作品链接解析（含 HTML 提取路径）是否还会崩。"""
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

print("=" * 68)
print("[A] VideoInfo 字段自检")
from justoneapi_collector.video_downloader import BloggerInfo, VideoInfo  # noqa: E402

v = VideoInfo(video_id="1", desc="d", author="a", cover_url="c", no_watermark_url="u",
              share_url="s", create_time=0, digg_count=0, comment_count=0, share_count=0)
print("    ✅ HTML 提取路径的构造参数不再报错:", v.video_id, v.comment_count, v.share_count)
print("    ✅ 最小构造可用的字段:", VideoInfo().video_id == "")
print("    ✅ BloggerInfo 最小构造:",
      BloggerInfo(sec_uid="x").nickname == "" and BloggerInfo(sec_uid="x").follower_count == 0)

print("\n[B] is_douyin_home 判定")
for u, expect in (("https://www.douyin.com", True),
                  ("https://www.douyin.com/", True),
                  ("https://www.douyin.com/?recommend=1", True),
                  ("https://www.douyin.com/video/7664911070490126501", False),
                  ("https://www.douyin.com/user/MS4wLjABxxx", False),
                  ("https://v.douyin.com/abc/", False)):
    got = VideoDownloader.is_douyin_home(u)
    print(f"    {'✅' if got == expect else '❌'} {u} → {got}")

print("\n[C] 失效短链的失败原因（应当是可操作提示，而不是崩）")
import httpx  # noqa: E402

link = "https://v.douyin.com/iFIK4dwHNpo/"
try:
    v, reason = dl.resolve_share_link_with_reason(link)
    print("    结果:", v)
    print("    原因:", reason)
except Exception as e:  # noqa: BLE001
    print("    ❌ 仍然抛异常:", type(e).__name__, e)

print("\n[D] HTML 提取路径（真实分享页，修复前必崩）")
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
for host in ("https://www.iesdouyin.com/share/video/7664911070490126501/",
             "https://m.douyin.com/share/video/7664911070490126501"):
    try:
        with httpx.Client(follow_redirects=True, timeout=20,
                          headers={"User-Agent": UA, "Referer": "https://www.douyin.com/",
                                   "Cookie": cookie}) as cx:
            r = cx.get(host)
        info = dl._extract_video_from_html(r.text)
        print(f"    {host[:56]}")
        print(f"        HTTP {r.status_code} / {len(r.text)} 字节 / 最终 {str(r.url)[:58]}")
        if info:
            print(f"        ✅ 提取成功 video_id={info.video_id} 作者={info.author[:12]}")
            print(f"           播放地址={(info.no_watermark_url or '<空>')[:70]}")
        else:
            print("        ⚠️ 未提取到（不崩，交给 API 兜底）")
    except Exception as e:  # noqa: BLE001
        print(f"    ❌ {host[:56]} 异常: {type(e).__name__}: {e}")

print("\n[E] 端到端：真实作品链接（长链）")
try:
    v, reason = dl.resolve_share_link_with_reason(
        "https://www.douyin.com/video/7664911070490126501")
    print("    ✅ 解析成功:", v.video_id, v.author[:12], "|", (v.desc or "")[:24])
    print("       下载地址:", (v.no_watermark_url or "<空>")[:70])
except Exception as e:  # noqa: BLE001
    print("    ❌ 异常:", type(e).__name__, e)
