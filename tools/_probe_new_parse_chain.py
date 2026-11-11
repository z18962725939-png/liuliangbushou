# -*- coding: utf-8 -*-
"""端到端验证「服务端解析 → 下载落盘」整条链（v1.24.6）。

先验证再接线是本项目规矩 —— 抖音一条跑通不代表小红书能下完：
实测就抓出了「小红书 CDN 因为抖音 Referer 直接 403」这个坑。

本脚本用 NullClient（=免费档那种没有采集账号的真实场景）跑解析 + 下载，
确认**不花任何接口额度**也能全链路走通。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui_qt.pages._base import NullClient                       # noqa: E402
from justoneapi_collector.client import JustOneAPICollector     # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

FREE_CLIENT = NullClient()
TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "kPa7Fxdepu2GAuxU")


def _sample_links() -> list[tuple[str, str]]:
    """捞几条真实链接（抖音 / 小红书 / 快手各来几条）。"""
    out: list[tuple[str, str]] = []
    c = JustOneAPICollector(token=TOKEN, cache_enabled=False)

    r = c.request("douyin-tiktok-china", "video-search-v4", {"keyword": "美食"})
    bd = (r.data or {}).get("business_data") or []
    for it in bd[:3]:
        d = it.get("data") if isinstance(it, dict) else None
        if not isinstance(d, dict):
            continue
        if isinstance(d.get("raw_data"), str):
            import json
            try:
                d = json.loads(d["raw_data"])
            except Exception:                                   # noqa: BLE001
                pass
        aw = None
        for holder in (d, (d or {}).get("aweme_info") or {}):
            if isinstance(holder, dict) and holder.get("aweme_id"):
                aw = holder
                break
        if aw:
            out.append(("抖音", f"https://www.douyin.com/video/{aw['aweme_id']}"))

    r = c.request("xiaohongshu-rednote", "note-search-v2", {"keyword": "美食"})
    for it in ((r.data or {}).get("items") or [])[:2]:
        n = it.get("note") or {}
        if n.get("id") and n.get("xsec_token"):
            out.append(("小红书", "https://www.xiaohongshu.com/explore/"
                        f"{n['id']}?xsec_token={n['xsec_token']}"))

    r = c.request("kuaishou", "video-search-v2", {"keyword": "美食"})
    for f in ((r.data or {}).get("feeds") or [])[:2]:
        pid = f.get("photo_id")
        if pid:
            out.append(("快手", f"https://www.kuaishou.com/short-video/{pid}"))
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tc_probe_"))
    dl = VideoDownloader(FREE_CLIENT, save_dir=tmp)
    print(f"保存目录：{tmp}\n" + "-" * 72)

    cases = _sample_links()
    if not cases:
        print("没捞到任何样本链接（搜索接口异常？）")
        return 1

    ok_n = 0
    for label, url in cases:
        t0 = time.time()
        info, reason = dl.resolve_share_link_with_reason(url)
        cost = time.time() - t0
        if info is None:
            print(f"[{label}] ✗ 解析 {cost:5.1f}s　{reason[:90]}")
            continue
        plat = getattr(info, "platform", "?")
        print(f"[{label}] ✓ 解析 {cost:5.1f}s　平台={plat}｜"
              f"作者={info.author[:12]}｜id={info.video_id[:18]}")
        if not info.no_watermark_url:
            print("        图集（无视频地址），跳过下载")
            ok_n += 1
            continue
        t1 = time.time()
        path, err = dl.download_video(info)
        cost2 = time.time() - t1
        if path is None:
            print(f"        ✗ 下载 {cost2:5.1f}s　{err[:90]}")
            continue
        size = Path(path).stat().st_size
        print(f"        ✓ 下载 {cost2:5.1f}s　{size/1024:.0f} KB　{Path(path).name[:44]}")
        ok_n += 1

    print("-" * 72)
    print(f"全链路成功 {ok_n}/{len(cases)}")
    print(f"落盘目录：{tmp}")
    return 0 if ok_n else 1


if __name__ == "__main__":
    raise SystemExit(main())
