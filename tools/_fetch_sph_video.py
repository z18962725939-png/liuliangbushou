# -*- coding: utf-8 -*-
"""临时：把用户给的那条视频号作品下下来，供转写用。跑完即删。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from justoneapi_collector.media_parser import parse  # noqa: E402

LINK = "https://weixin.qq.com/sph/AM94sIUqvX"
OUT_DIR = Path(r"D:\workbuddy\2026-10-03-23-06-16\_视频号素材")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    data, reason = parse(LINK)
    if data is None:
        print(f"解析失败：{reason}")
        return 1
    print(f"解析 {time.time() - t0:.1f}s")
    print(f"  标题：{data['title'][:60]!r}")
    print(f"  封面：{(data['cover_url'] or '')[:70]}")

    url = data["video_url"]
    mp4 = OUT_DIR / "sph_ai_ui_skill.mp4"
    t1 = time.time()
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0),
                      follow_redirects=True, trust_env=False) as c:
        with c.stream("GET", url, headers={"User-Agent": "Mozilla/5.0"}) as r:
            r.raise_for_status()
            with open(mp4, "wb") as f:
                for chunk in r.iter_bytes(65536):
                    f.write(chunk)
    size = mp4.stat().st_size
    print(f"视频 {time.time() - t1:.1f}s　{size / 1024 / 1024:.2f} MB → {mp4}")

    # 封面也存一份（做封面分析时会用到）
    if data["cover_url"]:
        cov = OUT_DIR / "sph_ai_ui_skill_cover.jpg"
        try:
            with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0),
                              trust_env=False) as c:
                r = c.get(data["cover_url"], headers={"User-Agent": "Mozilla/5.0"})
                cov.write_bytes(r.content)
            print(f"封面 {cov.stat().st_size / 1024:.0f} KB → {cov}")
        except Exception as e:  # noqa: BLE001
            print(f"封面没拿到：{type(e).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
