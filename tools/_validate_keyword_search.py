# -*- coding: utf-8 -*-
"""离线门禁：关键词抓取的「多角度组合 → 去重 → 排序 → 过滤」真实逻辑。

为什么不再联网：原来的版本硬编码了一枚早已过期的 token，真机房门禁环境里
拿不到有效凭证，真实抓取恒为 0 条 —— 6 项断言全红，测的是 token 而不是功能。
这里改用假 client 产出确定性的作品数据，全程不联网、不花额度，但断言的仍是
产品真实链路（PlatformAdapter → search_keyword / mine_low_fan_viral）。

用法：C:/Python310/python.exe tools/_validate_keyword_search.py [关键词]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector.models import ApiResponse                    # noqa: E402
from justoneapi_collector import keyword_search as ks                   # noqa: E402
from justoneapi_collector.topic_mining import mine_low_fan_viral          # noqa: E402

KW = sys.argv[1] if len(sys.argv) > 1 else "减肥"
# 与 ks.DEPTH_PLANS / ks._PAGES_NO_FILTER 的真实键逐字对齐（旧标签「约 2 次 /
# 约 12 次」早就落默认值，测的根本不是快速/深度档）
FAST = "快速（2~3 次请求）"
STANDARD = "标准（6~9 次请求）"
DEEP = "深度（10~18 次请求）"
SORT = "数据最好（点赞最多）"
TIME = "不限时间"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = ""):
    (PASSED if cond else FAILED).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + " " + extra)


class _FakeSearchClient:
    """离线假 client：每次请求返回一条固定爆款 + 一条随请求序号变化的作品。

    固定项在多次请求里重复出现，用来真实压测去重；变化项的点赞逐次递减，
    用来同时压测排序与 min_digg 过滤。没有网络、没有 token、没有额度消耗。
    """

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, platform, endpoint, params=None):
        params = dict(params or {})
        self.calls.append((platform, endpoint, params))
        n = len(self.calls)
        if platform == "xiaohongshu":
            posts = [
                {"note_id": "note-fixed", "title": "固定爆款笔记",
                 "user": {"nickname": "作者甲", "follower_count": 30000},
                 "interact_info": {"liked_count": 600000, "play_count": 1200000}},
                {"note_id": f"note-{n}", "title": f"笔记{n}",
                 "user": {"nickname": f"作者{n}", "follower_count": 20000},
                 "interact_info": {"liked_count": 900000 - (n % 12) * 50000,
                                   "play_count": 900000}},
            ]
        else:
            posts = [
                {"aweme_id": "7000000000000000001", "desc": "固定爆款",
                 "author": {"nickname": "作者甲", "follower_count": 30000},
                 "statistics": {"digg_count": 600000, "play_count": 1200000}},
                {"aweme_id": f"7000000000000000{n:02d}", "desc": f"作品{n}",
                 "author": {"nickname": f"作者{n}", "follower_count": 20000},
                 "statistics": {"digg_count": 900000 - (n % 12) * 50000,
                                "play_count": 900000}},
            ]
        return ApiResponse(success=True, code=0, message="",
                           data={"aweme_list": posts, "notes": posts},
                           raw_json={"code": 0, "data": {"aweme_list": posts}})

    def close(self):
        pass


print("=" * 72)
print("关键词抓取能力验证（离线）｜关键词：", KW)
print("=" * 72)

# 1) 计划规模 / 档位标签
print("\n[1] 请求计划规模")
for depth in ks.DEPTH_PLANS:
    n = len(ks.build_plan(SORT, TIME, depth))
    print(f"   {depth:<22} → {n} 次请求")
check("深度档标签与产品键一致",
      all(k in ks.DEPTH_PLANS for k in (FAST, STANDARD, DEEP)),
      f"（{FAST} / {STANDARD} / {DEEP}）")
n_fast = len(ks.build_plan(SORT, TIME, FAST))
n_std = len(ks.build_plan(SORT, TIME, STANDARD))
n_deep = len(ks.build_plan(SORT, TIME, DEEP))
check("快速档 2 次 / 标准档 9 次 / 深度档 18 次",
      (n_fast, n_std, n_deep) == (2, 9, 18),
      f"（{n_fast}/{n_std}/{n_deep}）")
check("深度越深请求越多", n_fast < n_std < n_deep)

# 非抖音平台应退化成纯翻页（不传 sort/publish_time）
plan_xhs = ks.build_plan(SORT, TIME, STANDARD, "xiaohongshu")
check("小红书不传排序/时间参数", all(s is None and t is None for s, t, _ in plan_xhs),
      f"→ {len(plan_xhs)} 次")

cl = _FakeSearchClient()

# 2) 对照：选题挖掘（旧路径）能出几条
print("\n[2] 对照：选题挖掘（低粉高赞过滤后）")
ranked = mine_low_fan_viral(cl, "douyin", KW, pages=2)
print(f"   选题挖掘结果：{len(ranked)} 条")

# 3) 新功能：快速 / 标准
print("\n[3] 关键词抓取（新功能）")
res_fast = ks.search_keyword(cl, "douyin", KW, sort_label=SORT,
                             time_label=TIME, depth_label=FAST)
print(f"   快速：{res_fast.count} 条，请求 {res_fast.requests_used} 次")
res_std = ks.search_keyword(cl, "douyin", KW, sort_label=SORT,
                            time_label=TIME, depth_label=STANDARD)
print(f"   标准：{res_std.count} 条，请求 {res_std.requests_used} 次")

print("\n   标准模式 Top8（按点赞）:")
for i, p in enumerate(res_std.posts[:8], 1):
    print(f"     {i}. 赞{p.digg:>8} 播{p.play:>9} {p.publish_text[:10]} "
          f"{(p.title[:26] or '(无标题)')} @{p.author_name}")

check("快速模式确实只请求 2 次", res_fast.requests_used == 2,
      f"（{res_fast.requests_used} 次）")
check("标准模式确实请求 9 次", res_std.requests_used == 9,
      f"（{res_std.requests_used} 次）")
check("标准模式结果多于快速模式", res_std.count > res_fast.count,
      f"（{res_std.count} > {res_fast.count}）")
check("标准模式明显多于选题挖掘", res_std.count > len(ranked),
      f"（{res_std.count} > {len(ranked)}）")
check("结果按点赞降序",
      all(res_std.posts[i].digg >= res_std.posts[i + 1].digg
          for i in range(len(res_std.posts) - 1)) if res_std.posts else False)
ids = [p.post_id for p in res_std.posts]
check("结果无重复（重复固定项被去重）", len(ids) == len(set(ids)),
      f"（{len(ids)} 条）")

# 4) 行数据与表头对齐 + 过滤参数
print("\n[4] 行数据 / 过滤参数")
rows = ks.rows_from_results([res_std])
check("每行列数与表头一致",
      all(len(r) == len(ks.KEYWORD_COLUMNS) for r in rows),
      f"（表头 {len(ks.KEYWORD_COLUMNS)} 列，共 {len(rows)} 行）")
if rows:
    print("   示例行：", rows[0][:6], "…")
    check("行内包含播放量列", rows[0][9] is not None)

res_flt = ks.search_keyword(cl, "douyin", KW, sort_label=SORT, time_label=TIME,
                            depth_label=STANDARD, min_digg=500000)
check("最低点赞过滤生效（不是空结果假通过）",
      0 < res_flt.count < res_std.count
      and all(p.digg >= 500000 for p in res_flt.posts),
      f"（过滤后 {res_flt.count} / {res_std.count} 条）")

# 5) 小红书路径（不传排序参数也能出结果）
print("\n[5] 其它平台")
res_xhs = ks.search_keyword(cl, "xiaohongshu", KW, sort_label=SORT,
                            time_label=TIME, depth_label=FAST)
check("小红书可抓取", res_xhs.count > 0,
      f"（{res_xhs.count} 条，请求 {res_xhs.requests_used} 次）")

cl.close()
print("\n" + "=" * 72)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    for name in FAILED:
        print("  ✗ " + name)
print("离线结论：", "全部通过 ✔" if not FAILED else "存在失败 ✘")
sys.exit(1 if FAILED else 0)
