# -*- coding: utf-8 -*-
"""平台适配层单测：用构造的三平台风格响应验证解析（不依赖 Token）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from justoneapi_collector.platforms.adapter import (  # noqa: E402
    PlatformAdapter, scan_post_dicts, scan_comment_dicts, parse_post,
    parse_comment, normalize_platform, _to_int, _deep_get, PLATFORM_DISPLAY,
)

PASS, FAIL = [], []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✔' if cond else '✘'} {name}" + (f"  — {extra}" if extra else ""))


# ---- 三种平台风格的真实感样本 ----
DOUYIN = {
    "business_data": [
        {"type": 1, "data": {"aweme_info": {
            "aweme_id": "730001", "desc": "三分钟看懂汽车保养",
            "create_time": 1700000000,
            "author": {"sec_uid": "MS4wLjABAAAA_x", "nickname": "老司机说车",
                       "follower_count": 120000},
            "statistics": {"digg_count": 88000, "comment_count": 3200,
                           "collect_count": 5100, "share_count": 1500},
            "video": {"duration": 185000,
                      "cover": {"url_list": ["https://p.img/cover1.jpg"]}},
            "share_url": "https://www.douyin.com/video/730001",
        }}},
        {"type": 1, "data": {"aweme_info": {
            "aweme_id": "730002", "desc": "新手上路必看",
            "create_time": 1700001000,
            "author": {"sec_uid": "MS4wLjABAAAA_y", "nickname": "小白学车",
                       "follower_count": 3000},
            "statistics": {"digg_count": 120000, "comment_count": 8000,
                           "collect_count": 9000, "share_count": 4000},
            "video": {"duration": 95000},
        }}},
        {"type": 3, "data": {"aweme_info": {"aweme_id": "", "desc": "关联词卡片"}}},
    ]
}

KUAISHOU = {
    "videos": [
        {"video_id": "ks9001", "caption": "农村自建房全过程",
         "timestamp": 1700002000,
         "user": {"id": "ksu001", "name": "包工头阿强", "follower_count": 45000},
         "stats": {"likeCount": 23000, "commentCount": 1200, "shareCount": 800},
         "duration": 240,
         "cover": "https://ks.img/c1.jpg"},
        {"video_id": "ks9002", "caption": "装修避坑指南",
         "timestamp": 1700003000,
         "user": {"id": "ksu002", "name": "设计师小林", "follower_count": 2100},
         "stats": {"likeCount": 156000, "commentCount": 9800},
         "duration": 132},
    ]
}
