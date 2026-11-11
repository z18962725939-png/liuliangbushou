# -*- coding: utf-8 -*-
"""探针：摸清 JustOneAPI 评论接口的真实参数名、返回结构与单页条数。

会联网，成本约 0.3~0.5 元（搜索 2 次 + 评论 1~2 次）。只在设计「评论区截流」
功能前跑一次，摸清结构后即可固定到代码里。

用法：
    python tools/_probe_comments.py 按摩棒
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import keyword_search as ks                 # noqa: E402
from justoneapi_collector.client import JustOneAPICollector           # noqa: E402
from justoneapi_collector.platforms.adapter import (                  # noqa: E402
    scan_comment_dicts, parse_comment)

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "按摩棒"
TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "kPa7Fxdepu2GAuxU")

client = JustOneAPICollector(token=TOKEN, base_url="https://api.justoneapi.com",
                             cache_enabled=False)

print(f"【1】搜索「{KEYWORD}」拿真实 aweme_id…")
res = ks.search_keywords(client, "douyin", [KEYWORD],
                         depth_label="快速（2~3 次请求）")
rows = ks.rows_from_results(res)
print(f"    搜到 {len(rows)} 条")
ids = []
for r in rows[:5]:
    print("    行:", [str(x)[:28] for x in r])
if not rows:
    print("没搜到，退出")
    sys.exit(1)

# 找作品 ID：行结构里通常有一列是链接，末尾带 aweme_id
import re                                                            # noqa: E402
for r in rows[:8]:
    for cell in r:
        m = re.search(r"/video/(\d{15,})", str(cell))
        if m:
            ids.append(m.group(1))
ids = list(dict.fromkeys(ids))[:3]
print("    取到作品ID:", ids)

if not ids:
    print("没能从结果里抠出作品 ID，改用已知老 ID 试")
    ids = ["7166170264458382606"]

aweme = ids[0]
print(f"\n【2】调 video-comments-v1，作品 {aweme}")
for params in ({"awemeId": aweme, "cursor": 0, "count": 20},
               {"aweme_id": aweme, "cursor": 0, "count": 20}):
    key = list(params)[0]
    try:
        resp = client.request("douyin-tiktok-china", "video-comments-v1", params)
        print(f"\n    -- 参数名 {key} --")
        print("       success:", resp.success, "code:", resp.code,
              "msg:", getattr(resp, "message", ""))
        data = getattr(resp, "data", None)
        raw = getattr(resp, "raw_json", None) or {}
        if not resp.success:
            print("       raw:", json.dumps(raw, ensure_ascii=False)[:300])
            continue
        print("       data 类型:", type(data).__name__)
        print("       raw 顶层键:", list(raw)[:12])
        dicts = scan_comment_dicts(raw)
        print("       扫描到的评论字典数:", len(dicts))
        if dicts:
            d0 = dicts[0]
            print("       首条字段:", sorted(d0)[:30])
            print("       首条内容:", json.dumps(d0, ensure_ascii=False)[:600])
            c = parse_comment("douyin", d0, aweme)
            print("       parse_comment →", c)
        print("       raw 片段:", json.dumps(raw, ensure_ascii=False)[:800])
        break
    except Exception as e:                                            # noqa: BLE001
        print(f"    {key} 异常:", type(e).__name__, e)

print("\n【3】翻页测试（cursor=20）")
try:
    resp = client.request("douyin-tiktok-china", "video-comments-v1",
                          {"awemeId": aweme, "cursor": 20, "count": 20})
    dicts = scan_comment_dicts(getattr(resp, "raw_json", None) or {})
    print("    cursor=20 条数:", len(dicts))
except Exception as e:                                                # noqa: BLE001
    print("    异常:", e)

print("\n【4】子评论接口 comment-replies-v1（拿第一条评论的 cid）")
try:
    resp = client.request("douyin-tiktok-china", "video-comments-v1",
                          {"awemeId": aweme, "cursor": 0, "count": 20})
    dicts = scan_comment_dicts(getattr(resp, "raw_json", None) or {})
    if dicts:
        cid = str(dicts[0].get("cid") or dicts[0].get("comment_id")
                  or dicts[0].get("id") or "")
        print("    首条 cid:", cid)
        if cid:
            r2 = client.request("douyin-tiktok-china", "comment-replies-v1",
                                {"awemeId": aweme, "commentId": cid,
                                 "cursor": 0, "count": 20})
            print("    replies success:", r2.success, "code:", r2.code)
            print("    replies raw:",
                  json.dumps(getattr(r2, "raw_json", {}), ensure_ascii=False)[:600])
except Exception as e:                                                # noqa: BLE001
    print("    异常:", type(e).__name__, e)
