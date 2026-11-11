# -*- coding: utf-8 -*-
"""诊断：关键词抓取导出的链接，能否被视频下载器直接解析？

结论决定「导入 Excel」功能怎么做：
  · 若能解析  → 导入后可直接批量解析成 VideoInfo → 加入下载列表
  · 若不能    → 需要先把链接转成作品 ID / 走另一条解析路径
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from justoneapi_collector.client import JustOneAPICollector              # noqa: E402
from justoneapi_collector import keyword_search as ks                    # noqa: E402
from justoneapi_collector.video_downloader import (VideoDownloader,      # noqa: E402
                                                   platform_from_url)

TOKEN = "kPa7Fxdepu2GAuxU"
BASE = "https://api.justoneapi.com"

cl = JustOneAPICollector(token=TOKEN, base_url=BASE, cache_enabled=False)
try:
    kw = sys.argv[1] if len(sys.argv) > 1 else "牙刷"
    r = ks.search_keyword(cl, "douyin", kw, depth_label="快速（2~3 次请求）")
    print(f"[搜索] 「{kw}」共 {r.count} 条，请求 {r.requests_used} 次")
    for p in r.posts[:5]:
        print(f"   链接: {p.url!r}")
        print(f"        platform_from_url -> {platform_from_url(p.url)}")

    dl = VideoDownloader(cl, save_dir=None)
    for p in r.posts[:3]:
        info, reason = dl.resolve_share_link_with_reason(p.url)
        ok = info is not None
        print(f"[解析] {'OK ' if ok else 'FAIL'} {p.url[:70]}")
        print(f"       reason={reason!r}")
        if ok:
            print(f"       标题={info.desc[:30]!r} 时长={info.duration} 无水印={'有' if info.no_watermark_url else '无'}")
finally:
    cl.close()
