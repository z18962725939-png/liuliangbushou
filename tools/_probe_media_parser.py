# -*- coding: utf-8 -*-
"""新解析服务多平台实测（2026-10-25）。

目的：**先证实再接线**。抖音一条跑通不能代表小红书/快手也能跑 ——
之前就是因为没验证，才承诺了「暂不支持」这种话说出口。

做法：用 JustOneAPI 的搜索接口捞几条**真实存在**的小红书/快手链接，
再丢给新的解析服务，看能不能拿到无水印直链。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justoneapi_collector.client import JustOneAPICollector   # noqa: E402
from justoneapi_collector.media_parser import parse           # noqa: E402

TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "kPa7Fxdepu2GAuxU")
_CLIENT = JustOneAPICollector(token=TOKEN, cache_enabled=False)


def call(slug: str, endpoint: str, params: dict) -> dict:
    """走真实客户端请求（路径拼法以 client.py 为准，别自己造轮子）。"""
    try:
        resp = _CLIENT.request(slug, endpoint, params)
    except Exception as e:                                      # noqa: BLE001
        return {"success": False, "code": -1, "message": f"{type(e).__name__}: {e}"}
    return {"success": resp.success, "code": resp.code,
            "message": resp.message, "data": resp.data}


def pick_links(slug: str, endpoint: str, params: dict, keys: list[str],
               want: int = 3) -> list[str]:
    """从搜索结果里捞 want 条真实链接。"""
    try:
        body = call(slug, endpoint, params)
    except Exception as e:                                      # noqa: BLE001
        print(f"  搜索失败：{type(e).__name__}: {e}")
        return []
    if not body.get("success"):
        print(f"  搜索返回失败：code={body.get('code')} {body.get('message')}")
        return []
    found: list[str] = []
    for item in (body.get("data") or {}).get("business_data") or []:
        d = item.get("data") if isinstance(item, dict) else None
        if not isinstance(d, dict):
            continue
        obj = d
        if isinstance(d.get("raw_data"), str):
            try:
                obj = json.loads(d["raw_data"])
            except Exception:                                   # noqa: BLE001
                pass
        for k in keys:
            for holder in (obj, d):
                if not isinstance(holder, dict):
                    continue
                v = holder.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    if v not in found:
                        found.append(v)
                    break
        if len(found) >= want:
            break
    return found[:want]


def main() -> int:
    cases = [
        ("抖音", "https://www.douyin.com/video/7644834211292280293"),
    ]
    # 小红书：note-search-v2
    cases += [("小红书", u) for u in pick_links(
        "xiaohongshu-rednote", "note-search-v2",
        {"keyword": "美食", "page": 1},
        ["share_url", "note_url", "url", "shareUrl", "link"], 3)]
    # 快手：video-search-v2
    cases += [("快手", u) for u in pick_links(
        "kuaishou", "video-search-v2",
        {"keyword": "美食", "page": 1},
        ["share_url", "photo_url", "url", "shareUrl", "link"], 3)]
    # B站等额外平台：暂用固定链接测一下不认识的域名会被怎么处理

    print(f"共 {len(cases)} 条待测\n" + "-" * 70)
    ok_n = 0
    for label, url in cases:
        t0 = time.time()
        data, reason = parse(url)
        cost = time.time() - t0
        if data is None:
            print(f"[{label}] ✗ {cost:5.1f}s  {reason}")
            print(f"         {url[:90]}")
            continue
        ok_n += 1
        print(f"[{label}] ✓ {cost:5.1f}s  {data['platform_name']}｜"
              f"作者={data['author'][:12]}｜id={data['video_id'][:20]}")
        print(f"         标题：{data['title'][:38]}")
        print(f"         视频：{data['video_url'][:78]}…")
        print(f"         图片：{len(data['images'])}　多视频：{len(data['videos'])}")
    print("-" * 70)
    print(f"成功 {ok_n}/{len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
