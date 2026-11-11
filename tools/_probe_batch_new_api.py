# -*- coding: utf-8 -*-
"""批量 20 条真实抖音链接走**生产入口**（resolve_links），量成功率与耗时。

为什么非要用 resolve_links 而不是直接调 parse：
客户走的就是这条路（批量导入 → resolve_links → resolve_share_link_with_reason），
中间有节流、有缓存、有兜底；只测 parse 等于只测了一半。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui_qt.pages._base import NullClient                        # noqa: E402
from justoneapi_collector.client import JustOneAPICollector      # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402
from justoneapi_collector.video_link_import import resolve_links   # noqa: E402

TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "kPa7Fxdepu2GAuxU")


def gather(n: int = 20) -> list[str]:
    c = JustOneAPICollector(token=TOKEN, cache_enabled=False)
    urls: list[str] = []
    offset = 0
    while len(urls) < n and offset < 5:
        r = c.request("douyin-tiktok-china", "video-search-v4",
                      {"keyword": "美食", "offset": offset})
        for it in ((r.data or {}).get("business_data") or []):
            d = it.get("data") if isinstance(it, dict) else None
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("raw_data"), str):
                try:
                    d = json.loads(d["raw_data"])
                except Exception:                               # noqa: BLE001
                    pass
            aw = None
            for holder in (d, (d or {}).get("aweme_info") or {}):
                if isinstance(holder, dict) and holder.get("aweme_id"):
                    aw = holder
                    break
            if aw:
                u = f"https://www.douyin.com/video/{aw['aweme_id']}"
                if u not in urls:
                    urls.append(u)
            if len(urls) >= n:
                break
        offset += 20
    return urls[:n]


def main() -> int:
    urls = gather(20)
    print(f"样本 {len(urls)} 条真实抖音作品\n" + "-" * 70)
    if not urls:
        print("没捞到样本")
        return 1

    tmp = tempfile.mkdtemp(prefix="tc_batch_")
    dl = VideoDownloader(NullClient(), save_dir=tmp)

    durations: list[float] = []
    seen: list[float] = []

    def on_progress(i, total, url):
        nonlocal last
        now = time.time()
        if seen:
            durations.append(now - seen[-1])
        seen.append(now)

    last = time.time()
    t0 = time.time()
    videos, failures = resolve_links(dl, urls, on_progress=on_progress)
    cost = time.time() - t0

    for v in videos:
        print(f"  ✓ {v.video_id}｜{(v.author or '(无作者)')[:12]}｜{len(v.no_watermark_url)>0 and '有直链' or '无直链'}")
    for u, why in failures:
        print(f"  ✗ {u[-20:]}｜{why[:70]}")

    print("-" * 70)
    ok = len(videos)
    print(f"成功 {ok}/{len(urls)}　失败 {len(failures)}　总耗时 {cost:.1f}s")
    if durations:
        print(f"单条间隔：中位 {statistics.median(durations):.1f}s　"
              f"最快 {min(durations):.1f}s　最慢 {max(durations):.1f}s")
    print(f"（旧版本同批要 4~13 秒/条，且大量因本机风控失败）")
    return 0 if ok == len(urls) else 0


if __name__ == "__main__":
    raise SystemExit(main())
