# -*- coding: utf-8 -*-
"""临时探针：微信视频号在 v1.24.6 里的真实表现（解析 → 下载 → 落盘）。

跑完即删。用 NullClient（免费档、没有采集账号的场景）跑，
确认不花客户接口额度也能走通。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui_qt.pages._base import NullClient, platform_label          # noqa: E402
from justoneapi_collector.video_downloader import (                # noqa: E402
    VideoDownloader, platform_from_url,
)
from justoneapi_collector.video_link_import import resolve_links   # noqa: E402

LINKS = [
    "https://weixin.qq.com/sph/AM94sIUqvX",
]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tc_sph_"))
    dl = VideoDownloader(NullClient(), save_dir=tmp)
    print(f"保存目录：{tmp}")

    print("\n=== 1. 平台识别（现有代码认不认 weixin.qq.com）===")
    for u in LINKS:
        got = platform_from_url(u)
        print(f"  video_downloader.platform_from_url → {got!r}")
    print(f"  _base.platform_label('weixin_channels') → "
          f"{platform_label('weixin_channels')!r}")

    print("\n=== 2. 生产入口 resolve_links（批量那条路）===")
    t0 = time.time()
    videos, failures = resolve_links(dl, list(LINKS))
    print(f"  耗时 {time.time() - t0:.1f}s　成功 {len(videos)}　失败 {len(failures)}")
    for u, why in failures:
        print(f"  ✗ 解析失败：{why[:140]}")
    for info in videos:
        print(f"  ✓ 平台={getattr(info, 'platform', '?')}｜"
              f"作者={info.author!r}｜id={info.video_id!r}")
        print(f"    标题={info.desc[:44]!r}")
        print(f"    直链={(info.no_watermark_url or '')[:90]}…")
        print(f"    图集={len(getattr(info, 'image_list', []) or [])} 张")

    print("\n=== 3. 单条入口 resolve_share_link_with_reason ===")
    info, reason = dl.resolve_share_link_with_reason(LINKS[0])
    if info is None:
        print(f"  ✗ {reason[:160]}")
        return 1
    print(f"  ✓ 平台={getattr(info, 'platform', '?')}")

    print("\n=== 4. 真实下载落盘 ===")
    if not info.no_watermark_url:
        print("  没有视频地址（图集作品）")
    else:
        t1 = time.time()
        path, err = dl.download_video(info)
        cost = time.time() - t1
        if path is None:
            print(f"  ✗ 下载失败 {cost:.1f}s：{err[:160]}")
            return 1
        size = Path(path).stat().st_size
        print(f"  ✓ 下载 {cost:.1f}s　{size/1024:.0f} KB")
        print(f"    文件名：{Path(path).name}")

    print(f"\n落盘目录：{tmp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
