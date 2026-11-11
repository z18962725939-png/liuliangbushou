# -*- coding: utf-8 -*-
"""真实联网验证：关键词抓取到底能比「选题挖掘」多抓多少条。

用法：C:/Python310/python.exe tools/_validate_keyword_search.py [关键词]
会真实消耗几次额度（快速 2 次 + 标准 6 次），跑前确认额度够用。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector.client import JustOneAPICollector              # noqa: E402
from justoneapi_collector import keyword_search as ks                     # noqa: E402
from justoneapi_collector.topic_mining import mine_low_fan_viral          # noqa: E402

TOKEN = "kPa7Fxdepu2GAuxU"
BASE = "https://api.justoneapi.com"
KW = sys.argv[1] if len(sys.argv) > 1 else "减肥"

ok_all = True


def check(name: str, cond: bool, extra: str = ""):
    global ok_all
    print(("  ✔ " if cond else "  ✘ ") + name + " " + extra)
    if not cond:
        ok_all = False


print("=" * 72)
print("关键词抓取能力验证｜关键词：", KW)
print("=" * 72)

# 1) 计划规模
print("\n[1] 请求计划规模")
for depth in ks.DEPTH_PLANS:
    n = len(ks.build_plan("数据最好（点赞最多）", "不限时间", depth))
    print(f"   {depth:<22} → {n} 次请求")
check("深度越深请求越多",
      len(ks.build_plan("数据最好（点赞最多）", "不限时间", "快速（约 2 次请求）"))
      < len(ks.build_plan("数据最好（点赞最多）", "不限时间", "深度（约 12 次请求）")))

# 非抖音平台应退化成纯翻页（不传 sort/publish_time）
plan_xhs = ks.build_plan("数据最好（点赞最多）", "不限时间", "标准（约 6 次请求）",
                         "xiaohongshu")
check("小红书不传排序/时间参数", all(s is None and t is None for s, t, _ in plan_xhs),
      f"→ {len(plan_xhs)} 次")

cl = JustOneAPICollector(token=TOKEN, base_url=BASE, cache_enabled=False)

# 2) 对照：选题挖掘（旧路径）能出几条
print("\n[2] 对照：选题挖掘（低粉高赞过滤后）")
try:
    ranked = mine_low_fan_viral(cl, "douyin", KW, pages=2)
    print(f"   选题挖掘结果：{len(ranked)} 条")
except Exception as e:                                      # noqa: BLE001
    ranked = []
    print(f"   选题挖掘异常：{e}")

# 3) 新功能：快速 / 标准
print("\n[3] 关键词抓取（新功能）")
res_fast = ks.search_keyword(cl, "douyin", KW,
                             sort_label="数据最好（点赞最多）",
                             time_label="不限时间",
                             depth_label="快速（约 2 次请求）")
print(f"   快速：{res_fast.count} 条，请求 {res_fast.requests_used} 次")
res_std = ks.search_keyword(cl, "douyin", KW,
                            sort_label="数据最好（点赞最多）",
                            time_label="不限时间",
                            depth_label="标准（约 6 次请求）")
print(f"   标准：{res_std.count} 条，请求 {res_std.requests_used} 次")

print("\n   标准模式 Top8（按点赞）:")
for i, p in enumerate(res_std.posts[:8], 1):
    print(f"     {i}. 赞{p.digg:>8} 播{p.play:>9} {p.publish_text[:10]} "
          f"{(p.title[:26] or '(无标题)')} @{p.author_name}")

check("标准模式结果多于快速模式", res_std.count > res_fast.count,
      f"（{res_std.count} > {res_fast.count}）")
check("标准模式明显多于选题挖掘", res_std.count > len(ranked),
      f"（{res_std.count} > {len(ranked)}）")
check("结果按点赞降序",
      all(res_std.posts[i].digg >= res_std.posts[i + 1].digg
          for i in range(len(res_std.posts) - 1)) if res_std.posts else False)
ids = [p.post_id for p in res_std.posts]
check("结果无重复", len(ids) == len(set(ids)), f"（{len(ids)} 条）")

# 4) 行数据与表头对齐 + 过滤参数
print("\n[4] 行数据 / 过滤参数")
rows = ks.rows_from_results([res_std])
check("每行列数与表头一致",
      all(len(r) == len(ks.KEYWORD_COLUMNS) for r in rows),
      f"（表头 {len(ks.KEYWORD_COLUMNS)} 列，共 {len(rows)} 行）")
if rows:
    print("   示例行：", rows[0][:6], "…")
    check("行内包含播放量列", rows[0][9] is not None)

res_flt = ks.search_keyword(cl, "douyin", KW, depth_label="快速（约 2 次请求）",
                            min_digg=500000)
check("最低点赞过滤生效",
      all(p.digg >= 500000 for p in res_flt.posts) if res_flt.posts else True,
      f"（过滤后 {res_flt.count} 条）")

# 5) 小红书路径（不传排序参数也能出结果）
print("\n[5] 其它平台")
res_xhs = ks.search_keyword(cl, "xiaohongshu", KW, depth_label="快速（约 2 次请求）")
check("小红书可抓取", res_xhs.count > 0, f"（{res_xhs.count} 条，请求 {res_xhs.requests_used} 次）")

cl.close()
print("\n结论：", "全部通过 ✔" if ok_all else "存在失败 ✘")
sys.exit(0 if ok_all else 1)
