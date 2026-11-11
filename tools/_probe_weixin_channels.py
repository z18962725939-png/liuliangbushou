# -*- coding: utf-8 -*-
"""端到端验证「微信视频号」整条链：识别 → 解析 → 下载 → 落盘（v1.24.7）。

为什么单独留一个探针：
    视频号链接（https://weixin.qq.com/sph/xxxx）是客户直接丢过来的，
    它跟抖音/小红书/快手有三个不一样的地方，任何一个退化都不会报错，
    只会让客户觉得"这个平台支持得糙"：
      ① 平台列会显示英文内部键 weixin_channels（违反界面契约）；
      ② 标题带换行 + 一长串 #话题标签，直接进文件名和表格；
      ③ 服务端偶尔不回 platform，得靠域名兜底认出来。
    每一条都有对应的断言在 _validate_feature_gate.py 第 14 节盯着，
    但那些是静态检查 —— 这个脚本是真的把链接跑一遍。

用法：
    C:\\Python310\\python.exe tools\\_probe_weixin_channels.py

用 NullClient（= 免费档那种没有采集账号的真实场景）跑，
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
from justoneapi_collector.media_parser import (                    # noqa: E402
    platform_from_url as svc_platform_from_url,
)
from justoneapi_collector.video_downloader import (                # noqa: E402
    VideoDownloader, platform_from_url,
)
from justoneapi_collector.video_link_import import resolve_links   # noqa: E402

# 一条真实存在的微信视频号短链
LINKS = ["https://weixin.qq.com/sph/AM94sIUqvX"]

FAILED: list[str] = []


def _fail(msg: str) -> None:
    FAILED.append(msg)
    print(f"  ✗ {msg}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tc_wxch_"))
    print(f"保存目录：{tmp}\n" + "-" * 70)

    print("1) 平台识别与中文名")
    for u in LINKS + ["https://mp.weixin.qq.com/s/abc123"]:
        print(f"  {u:44s} → {platform_from_url(u)!r}")
        if svc_platform_from_url(u) != platform_from_url(u):
            _fail(f"两个模块的识别结果不一致：{u}")
    if platform_from_url(LINKS[0]) != "weixin_channels":
        _fail("按域名认不出视频号短链")
    if svc_platform_from_url("https://mp.weixin.qq.com/s/abc123") != "weixin_mp":
        _fail("按域名认不出公众号文章")
    lbl = platform_label("weixin_channels")
    print(f"  界面显示：{lbl!r}")
    if lbl == "weixin_channels":
        _fail("界面把英文内部键直接显示给了客户")

    dl = VideoDownloader(NullClient(), save_dir=tmp)

    print("\n2) 批量入口 resolve_links")
    t0 = time.time()
    videos, failures = resolve_links(dl, list(LINKS))
    print(f"  耗时 {time.time() - t0:.1f}s　成功 {len(videos)}　失败 {len(failures)}")
    for _u, why in failures:
        _fail(f"解析失败：{why[:120]}")
    if not videos:
        print("  没有解析结果，后续步骤跳过")
    else:
        info = videos[0]
        print(f"  平台={info.platform}｜作者={info.author!r}｜id={info.video_id!r}")
        print(f"  标题={info.desc[:50]!r}")

        print("\n3) 落盘（文件名不许夹带话题标签）")
        if not info.no_watermark_url:
            print("  这条是图集作品，没有视频地址")
        else:
            t1 = time.time()
            path, err = dl.download_video(info)
            cost = time.time() - t1
            if path is None:
                _fail(f"下载失败：{err[:120]}")
            else:
                name = Path(path).name
                size = Path(path).stat().st_size
                print(f"  ✓ {cost:.1f}s　{size/1024:.0f} KB")
                print(f"  文件名（{len(name)} 字符）：{name}")
                if "#" in name:
                    _fail("文件名里混进了 #话题标签")
                if "\n" in name or "\r" in name:
                    _fail("文件名里混进了换行")
                if len(name) > 90:
                    _fail(f"文件名过长（{len(name)} 字符），深层目录会写不进去")

    print("\n" + "-" * 70)
    if FAILED:
        print(f"视频号链路有 {len(FAILED)} 处问题：")
        for f in FAILED:
            print("  ✗", f)
        return 1
    print("WEIXIN_CHANNELS_OK：视频号整条链走得通")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
