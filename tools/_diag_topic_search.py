# -*- coding: utf-8 -*-
"""诊断：选题挖掘的搜索结果到底长什么样。

目的：
1. 看抖音 video-search-v4 的原始返回结构，确认有没有「推荐位/关联词」混入；
2. 对比 scan_post_dicts 扫出来的条目与真实搜索结果；
3. 检查关键词参数是否真的生效。

用法：C:/Python310/python.exe tools/_diag_topic_search.py [关键词]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector.client import JustOneAPICollector          # noqa: E402
from justoneapi_collector.platforms.adapter import (                 # noqa: E402
    PlatformAdapter, parse_post, scan_post_dicts)

TOKEN = "kPa7Fxdepu2GAuxU"
BASE = "https://api.justoneapi.com"
KW = sys.argv[1] if len(sys.argv) > 1 else "减肥"


def main() -> int:
    cl = JustOneAPICollector(token=TOKEN, base_url=BASE, cache_enabled=False)
    print("=" * 70)
    print("关键词：", KW)
    print("=" * 70)

    resp = cl.request("douyin-tiktok-china", "video-search-v4",
                      {"keyword": KW, "count": 20, "offset": 0})
    print("success =", resp.success, " code =", resp.code, " msg =", repr(resp.message)[:120])
    data = resp.data
    print("data 类型:", type(data).__name__)
    if isinstance(data, dict):
        print("data 顶层键：")
        for k, v in data.items():
            if isinstance(v, list):
                print("   %-28s list[%d]" % (k, len(v)))
            elif isinstance(v, dict):
                print("   %-28s dict{%s}" % (k, ",".join(list(v.keys())[:6])))
            else:
                print("   %-28s %s" % (k, str(v)[:60]))
    elif isinstance(data, list):
        print("   list[%d]" % len(data))

    dicts = scan_post_dicts(data) if data is not None else []
    print()
    print("scan_post_dicts 扫到 %d 条：" % len(dicts))
    for i, d in enumerate(dicts, 1):
        it = parse_post("douyin-tiktok-china", d)
        # 记录它在原始 json 里的顶层归属键，便于定位来源
        print("  %2d. id=%-22s 赞=%-9d 粉=%-9d %s | 作者:%s"
              % (i, it.post_id[:22], it.digg, it.author_fans,
                 it.title[:34] or "(无标题)", it.author_name or "-"))

    # 用适配器正式路径再取一次（带过滤），确认界面实际会显示什么
    print()
    ad = PlatformAdapter(cl, "douyin-tiktok-china")
    items = ad.search_posts(KW, page=1, count=20)
    print("adapter.search_posts 返回 %d 条：" % len(items))
    for i, it in enumerate(items, 1):
        print("  %2d. %s | %s" % (i, it.title[:40] or "(无标题)", it.author_name))
    if ad.last_error:
        print("  last_error:", ad.last_error)

    cl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
