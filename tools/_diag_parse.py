# -*- coding: utf-8 -*-
"""诊断：为什么「作品链接」解析不出来。逐层打印，定位断点。"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 手动载入 .env（不依赖 python-dotenv）
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

LINK = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/iFIK4dwHNpo/"
SHARE_TEXT = f"户 # 电影解说 # 抖音精选计划 {LINK} 复制此链接，打开Dou音搜索，直接观看视频！"

print("=" * 70)
print("输入链接:", LINK)
print("=" * 70)

# ── 1. 纯 httpx 跟随短链（不经过任何业务代码）──────────────────────────
import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
print("\n[1] httpx 直接跟随短链")
for proxy_mode, kwargs in (("trust_env=False(直连)", {"trust_env": False}),
                           ("trust_env=True(走系统代理)", {"trust_env": True})):
    try:
        with httpx.Client(follow_redirects=True, timeout=15, **kwargs) as cx:
            r = cx.get(LINK, headers={"User-Agent": UA})
            print(f"    {proxy_mode}: HTTP {r.status_code} → {r.url}")
    except Exception as e:  # noqa: BLE001
        print(f"    {proxy_mode}: 失败 {type(e).__name__}: {e}")

# ── 2. 分类 ────────────────────────────────────────────────────────────
from justoneapi_collector.client import JustOneAPICollector  # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

client = JustOneAPICollector()
dl = VideoDownloader(client)

print("\n[2] extract_urls / classify_url_kind")
print("    extract_urls(分享文案) =", dl.extract_urls(SHARE_TEXT))
print("    is_short_link =", VideoDownloader.is_short_link(LINK))
try:
    print("    classify_url_kind =", dl.classify_url_kind(LINK))
except Exception as e:  # noqa: BLE001
    print("    分类异常:", type(e).__name__, e)

print("\n[3] classify_input(分享文案)")
try:
    plan = VideoDownloader.classify_input(SHARE_TEXT, resolver=dl.classify_url_kind)
    print("    videos   =", plan["videos"])
    print("    bloggers =", plan["bloggers"])
    print("    texts    =", plan["texts"])
except Exception as e:  # noqa: BLE001
    print("    异常:", type(e).__name__, e)
    traceback.print_exc()

print("\n[4] resolve_short_link（本地跟随重定向）")
try:
    final = dl.resolve_short_link(LINK)
    print("    final =", final)
    if final:
        print("    parse_share_url =", VideoDownloader.parse_share_url(final))
except Exception as e:  # noqa: BLE001
    print("    异常:", type(e).__name__, e)

print("\n[5] 完整解析 resolve_share_link_with_reason")
try:
    v, reason = dl.resolve_share_link_with_reason(SHARE_TEXT)
    print("    结果:", v)
    print("    原因:", reason)
except Exception as e:  # noqa: BLE001
    print("    异常:", type(e).__name__, e)
    traceback.print_exc()

print("\n[6] Token 直连测试（video-details-v2，用一个公开视频ID）")
try:
    resp = client.request("douyin-tiktok-china", "share-link-resolution-v1",
                          {"shareUrl": LINK})
    print("    success =", resp.success, "code =", resp.code,
          "message =", getattr(resp, "message", ""))
    print("    data =", str(getattr(resp, "data", None))[:400])
    print("    token_invalid =", getattr(client, "token_invalid", None),
          "token_error =", getattr(client, "token_error", ""))
except Exception as e:  # noqa: BLE001
    print("    异常:", type(e).__name__, e)
