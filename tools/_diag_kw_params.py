# -*- coding: utf-8 -*-
"""诊断：抖音 video-search-v4 到底支持哪些排序 / 时间范围参数。

要做的「关键词抓取」功能，核心诉求是"搜一个词，把数据最好的相关视频都抓出来"，
而选题挖掘默认只给很少几条。所以必须实测清楚：
  · sort_type（0综合 / 1最多点赞 / 2最新发布）有没有效果
  · publish_time（0不限 / 1一天 / 7一周 / 180半年）是否被接口接受
  · 翻页（offset）能翻多少、每页最多多少条

用法：C:/Python310/python.exe tools/_diag_kw_params.py [关键词]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector.client import JustOneAPICollector            # noqa: E402
from justoneapi_collector.platforms.adapter import (                   # noqa: E402
    PlatformAdapter, scan_post_dicts, parse_post)

TOKEN = "kPa7Fxdepu2GAuxU"
BASE = "https://api.justoneapi.com"
KW = sys.argv[1] if len(sys.argv) > 1 else "减肥"


def probe(cl, label, extra):
    resp = cl.request("douyin-tiktok-china", "video-search-v4",
                      {"keyword": KW, "count": 20, "offset": 0, **extra})
    ok = resp.success
    items = []
    if ok and resp.data is not None:
        for d in scan_post_dicts(resp.data):
            it = parse_post("douyin-tiktok-china", d)
            if it.post_id:
                items.append(it)
    diggs = [i.digg for i in items]
    dates = [i.publish_text[:10] for i in items if i.publish_ts]
    print(f"\n[{label}] params={extra}")
    print(f"   success={ok} code={resp.code} msg={resp.message!r:.90}")
    print(f"   条数={len(items)}  点赞区间=({min(diggs) if diggs else '-'} ~ {max(diggs) if diggs else '-'})")
    print(f"   发布日期区间: {min(dates) if dates else '-'} ~ {max(dates) if dates else '-'}")
    if items:
        top = sorted(items, key=lambda x: x.digg, reverse=True)[:3]
        for t in top:
            print(f"     · 赞{t.digg:>7} 粉{t.author_fans:>9} {t.publish_text[:10]} "
                  f"{t.title[:30] or '(无标题)'} @{t.author_name}")
    return ok, len(items)


def main() -> int:
    cl = JustOneAPICollector(token=TOKEN, base_url=BASE, cache_enabled=False)
    print("=" * 72)
    print("关键词：", KW, " （诊断排序 / 时间范围参数）")
    print("=" * 72)

    probe(cl, "基线（不传排序/时间）", {})
    probe(cl, "sort_type=0 综合", {"sort_type": 0})
    probe(cl, "sort_type=1 最多点赞", {"sort_type": 1})
    probe(cl, "sort_type=2 最新发布", {"sort_type": 2})
    probe(cl, "publish_time=0 不限", {"publish_time": 0})
    probe(cl, "publish_time=7 一周", {"publish_time": 7})
    probe(cl, "publish_time=180 半年", {"publish_time": 180})
    probe(cl, "sort_type=1 + publish_time=0", {"sort_type": 1, "publish_time": 0})
    probe(cl, "sort_type=1 + publish_time=7", {"sort_type": 1, "publish_time": 7})

    # 翻页能力：offset 0 / 20 / 40 / 60，看能拿多少不重复条目
    print("\n" + "=" * 72)
    print("翻页能力（sort_type=1, publish_time=0）")
    print("=" * 72)
    seen = {}
    for page in range(5):
        resp = cl.request("douyin-tiktok-china", "video-search-v4",
                          {"keyword": KW, "count": 20, "offset": page * 20,
                           "sort_type": 1, "publish_time": 0})
        if not resp.success:
            print(f"  offset={page*20} 失败: {resp.message!r:.80}")
            break
        n_new = 0
        for d in scan_post_dicts(resp.data or {}):
            it = parse_post("douyin-tiktok-china", d)
            if it.post_id and it.post_id not in seen:
                seen[it.post_id] = it
                n_new += 1
        print(f"  offset={page*20:>3}  新条目 {n_new:>2}  累计 {len(seen)}")
        if n_new == 0:
            break

    print(f"\n5 页累计去重 {len(seen)} 条；按点赞 Top5：")
    for i, it in enumerate(sorted(seen.values(), key=lambda x: x.digg, reverse=True)[:5], 1):
        print(f"  {i}. 赞{it.digg:>8} 评{it.comment:>6} 藏{it.collect:>6} "
              f"{it.publish_text[:10]} {it.title[:28] or '(无标题)'} @{it.author_name}")

    cl.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
