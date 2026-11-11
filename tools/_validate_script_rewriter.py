"""短视频文案仿写自检（离线，不联网、不启动界面）。

跑法：C:/Python310/python.exe tools/_validate_script_rewriter.py

守住这几条底线（都是踩过或差点踩的坑）：
  · 分类必须齐：口播 / 剧情 / Vlog 是客户点名要的，不能漏
  · 每类的段落占比要加起来 ≈ 1，否则提示词里的秒数分配是自相矛盾的
  · 合规扫描要真命中极限词，同时不能把「第一次」误判成「第一」
  · 模型不按格式输出时必须兜底，且要标记出来，不能假装解析成功
  · 生成失败就是失败：没 Key / 走 CDP 通道必须明确报错，绝不能塞模板文字
  · 新功能位与导航、教程、图标必须同步（功能位漏登记会直接引发客户投诉）
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import licensing                       # noqa: E402
from justoneapi_collector import script_rewriter as sr           # noqa: E402
from justoneapi_collector import geo_monitor as gm               # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


print("1) 体裁分类（客户点名的口播 / 剧情 / Vlog 必须在）")
for key, label in (("talk", "口播"), ("story", "剧情"), ("vlog", "Vlog")):
    check(f"包含体裁「{label}」", key in sr.GENRES, f"缺失 {key}")
check("体裁总数 ≥ 8", len(sr.GENRES) >= 8, str(len(sr.GENRES)))
check("GENRE_ORDER 与 GENRES 一致",
      set(sr.GENRE_ORDER) == set(sr.GENRES),
      f"{set(sr.GENRE_ORDER) ^ set(sr.GENRES)}")
check("每个体裁都有段落骨架 / 钩子 / 要点",
      all(g.structure and g.hooks and g.tips for g in sr.GENRES.values()), "")

print()
print("2) 骨架自洽（占比之和 ≈ 1，语速在合理区间）")
for key, g in sr.GENRES.items():
    total = sum(r for _n, _d, r in g.structure)
    check(f"「{g.label}」段落占比之和 ≈ 1", abs(total - 1.0) < 0.02, f"实际 {total:.3f}")
    check(f"「{g.label}」语速在 3.5~5.5 字/秒", 3.5 <= g.wps <= 5.5, f"{g.wps}")
check("时长档位含 15/30/60/90", set(sr.DURATION_CHOICES) >= {15, 30, 60, 90}, "")
spec = sr.RewriteSpec(genre="talk", seconds=30)
check("目标字数 = 时长 × 语速", spec.target_chars == int(30 * sr.GENRES["talk"].wps),
      str(spec.target_chars))

print()
print("3) 合规扫描（真命中 + 不误伤）")
hit = sr.check_compliance("这是我们家最好用的按摩棒，全网最低价")
words = [h["词"] for h in hit]
check("命中极限词「最好」", "最好" in words, str(words))
check("命中极限词「全网最低」", "全网最低" in words, str(words))
check("命中医疗功效词", any(h["类别"] == "医疗功效"
                            for h in sr.check_compliance("三天根治颈椎病")))
check("命中站外引流词", any(h["类别"] == "站外引流"
                            for h in sr.check_compliance("想要链接的加微信")))
check("量词豁免：「第一次」不算极限词",
      not any(h["词"] == "第一" for h in sr.check_compliance("我第一次用就爱上了")))
check("干净文案零命中", sr.check_compliance("这款用下来挺顺手，噪音也不大") == [], "")
check("自定义禁用词生效",
      any(h["词"] == "代理" for h in sr.check_compliance("招代理", ["代理"])))
check("违规项带替换建议",
      all(h.get("建议") for h in sr.check_compliance("最好用")), "")

print()
print("4) 原文拆解")
sample = ("90% 的人都不知道，按摩棒买错了等于白买。"
          "我踩过三次坑，后来才发现关键在电机。"
          "但是便宜的那几款，用的都是有刷电机，三个月就废。"
          "想要不踩坑的，评论区扣 1，我把清单发你。")
a = sr.analyze_source(sample, 4.5)
check("字数统计正确", a.chars == len(re.sub(r"\s+", "", sample)), f"{a.chars}")
check("句数 > 0", a.sentences >= 3, str(a.sentences))
check("识别出开场钩子", bool(a.hook_hits), str(a.hook_hits))
check("识别出转折", bool(a.turn_hits), str(a.turn_hits))
check("识别出结尾引导", bool(a.cta_hits), str(a.cta_hits))
check("估算时长 > 0", a.est_seconds > 0, str(a.est_seconds))
check("summary 可直接展示", "字" in a.summary() and "秒" in a.summary(), a.summary())

print()
print("5) 输出解析（标准 / 变体 / 完全跑偏）")
raw_ok = (
    "【标题】按 moll 棒别乱买｜三招挑对｜踩坑清单\n"
    "【钩子】90% 的人按摩棒都买错了\n"
    "【分镜】0-3秒｜画面：主播举着三根不同价位的按摩棒｜口播：买错等于白买\n"
    "3-12秒｜画面：特写电机拆解｜口播：关键在电机，不在档位\n"
    "12-25秒｜画面：对比噪音分贝仪｜口播：这款只有 45 分贝\n"
    "25-30秒｜画面：主播指评论区｜口播：想要清单的扣 1\n"
    "【结尾】评论区扣 1，我把清单发你\n"
    "【话题】#按摩棒 #避坑 #好物分享\n")
p = sr.parse_script(raw_ok, spec)
check("解析出 3 个标题", len(p["标题"]) == 3, str(p["标题"]))
check("解析出钩子", "买错" in p["钩子"], p["钩子"])
check("解析出 4 条分镜", len(p["分镜"]) == 4, str(len(p["分镜"])))
check("分镜含时间轴", p["分镜"][0]["时间轴"] == "0-3秒", str(p["分镜"][0]))
check("分镜含画面", "电机" in p["分镜"][1]["画面"], str(p["分镜"][1]))
check("分镜含口播", "分贝" in p["分镜"][2]["口播"], str(p["分镜"][2]))
check("解析出结尾", "扣 1" in p["结尾"], p["结尾"])
check("解析出话题", len(p["话题"]) >= 3, str(p["话题"]))

raw_var = ("**标题**：A｜B\n**钩子**：别再乱买了\n"
           "分镜：0-5秒｜画面：出镜｜口播：说清楚痛点\n"
           "结尾：点个收藏\n话题：#a #b\n")
pv = sr.parse_script(raw_var, spec)
check("变体格式（**标题** / 冒号）也能解析",
      bool(pv["标题"]) and bool(pv["分镜"]) and bool(pv["结尾"]),
      f"{len(pv['标题'])}/{len(pv['分镜'])}/{pv['结尾']!r}")

raw_bad = "好的，以下是我为您创作的短视频脚本：第一段先讲痛点，第二段讲卖点，最后引导下单。"
pb = sr.parse_script(raw_bad, spec)
check("跑偏时兜底切分出分镜", len(pb["分镜"]) > 0, str(len(pb["分镜"])))
check("跑偏时标记为未识别格式", pb.get("_fallback") is True, str(pb.get("_fallback")))

print()
print("6) 质检评分")
good = sr.ScriptDraft(
    index=1, genre="talk", genre_label="口播", seconds=30,
    title_options=["a"], hook="90% 的人按摩棒都买错了",
    shots=[{"时间轴": "0-3秒", "画面": "x", "口播": "买错等于白买，关键在电机"},
           {"时间轴": "3-12秒", "画面": "y", "口播": "有刷电机三个月就废"},
           {"时间轴": "12-30秒", "画面": "z", "口播": "选无刷的，噪音还低"}],
    cta="评论区扣 1", hashtags=["#a"])
good.chars = len(re.sub(r"\s+", "", good.spoken))
good.est_seconds = good.chars / sr.GENRES["talk"].wps
good.violations = []
score, detail = sr.score_draft(good, spec)
check("合格稿得分 ≥ 80", score >= 80, f"{score}｜{'；'.join(detail)}")
check("评分明细可读（含各项得分）", any("/30" in d or "/25" in d for d in detail), str(detail))

bad = sr.ScriptDraft(index=2, genre="talk", genre_label="口播", seconds=30,
                     shots=[{"时间轴": "-", "画面": "", "口播": "这是最好的产品，全网最低价"}])
bad.chars = len(bad.spoken)
bad.est_seconds = bad.chars / sr.GENRES["talk"].wps
bad.violations = sr.check_compliance(bad.spoken)
bscore, bdetail = sr.score_draft(bad, spec)
check("缺钩子缺结尾的稿低分", bscore < score, f"{bscore} vs {score}")
check("违规稿扣掉合规分并写明后果",
      any("广告法" in d for d in bdetail) and bscore <= 60, str(bdetail))

check("读出模型自标的时间轴总时长",
      sr.claimed_seconds([{"时间轴": "0-3秒"}, {"时间轴": "25-30秒"}]) == 30.0,
      str(sr.claimed_seconds([{"时间轴": "0-3秒"}, {"时间轴": "25-30秒"}])))
check("小数时间轴也能读（0.0-4.0 / 28.0-30.0）",
      sr.claimed_seconds([{"时间轴": "0.0-4.0"}, {"时间轴": "28.0-30.0"}]) == 30.0, "")

pain = sr.ScriptDraft(index=1, genre="talk", genre_label="口播", seconds=30,
                      hook="久坐一天，腰背硬得抬不起手",
                      shots=[{"时间轴": "0-3秒", "口播": "a"},
                             {"时间轴": "3-15秒", "口播": "b"},
                             {"时间轴": "15-30秒", "口播": "c"}],
                      cta="评论区扣 1")
pain.chars = len(pain.spoken)
pain.est_seconds = pain.chars / sr.GENRES["talk"].wps
pscore, pdetail = sr.score_draft(pain, spec)
check("痛点型开场也算强钩子", any("25/25" in d for d in pdetail), str(pdetail))

print()
print("7) 文案拼接与导出")
check("spoken 含钩子 + 各分镜口播 + 结尾",
      good.spoken.startswith("90%") and "扣 1" in good.spoken, good.spoken[:40])
dup = sr.ScriptDraft(index=1, hook="钩子", shots=[{"口播": "正文"}], cta="正文")
check("结尾与正文重复时不重复拼接", dup.spoken.count("正文") == 1, dup.spoken)

md = sr.to_markdown(good, spec)
check("Markdown 含分镜表格", "| 时间轴 |" in md and "口播" in md, "")
check("Markdown 含标题与话题", "标题备选" in md or "话题" in md, "")

out = ROOT / "_tmp_script_out"
out.mkdir(exist_ok=True)
res = sr.RewriteResult(spec=spec, engine="deepseek", source=sample,
                       analysis=a, items=[good, bad], started_at="2026-10-12 12:00")
res.items[1].error = "调用太频繁被限流（429）"
xlsx = out / "t.xlsx"
sr.export_excel(res, xlsx)
check("Excel 文件已生成", xlsx.exists() and xlsx.stat().st_size > 0, "")
try:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx)
    names = wb.sheetnames
    check("Excel 含四张表", len(names) >= 4, str(names))
    check("分镜表只写成功的版本",
          all(r[0] != "第 2 版" for r in wb["分镜脚本"].iter_rows(values_only=True)),
          str([r[0] for r in wb["分镜脚本"].iter_rows(values_only=True)]))
    check("质检表记录了失败原因",
          any("限流" in str(r[2]) for r in wb["质检明细"].iter_rows(values_only=True)), "")
except ImportError:
    check("openpyxl 可用（用于导出验证）", False, "未安装 openpyxl")

mdp = out / "t.md"
sr.export_markdown(res, mdp)
check("Markdown 导出成功", mdp.exists() and "生成失败" in mdp.read_text(encoding="utf-8"), "")

print()
print("8) 提示词")
sys_txt, user_txt = sr.build_prompt(spec, sample, a)
check("提示词写明「不许照搬原句」", "禁止照搬原句" in user_txt, "")
check("提示词带体裁骨架", "结构骨架" in user_txt and "钩子" in user_txt, "")
check("提示词带目标字数", str(spec.target_chars) in user_txt, "")
check("提示词带违禁词清单", "全网最低" in user_txt, "")
check("提示词要求分镜格式", "时间轴" in user_txt and "口播" in user_txt, "")
check("原文为空时不出现空引用",
      "未提供对标原文" in sr.build_prompt(spec, "", None)[1], "")

print()
print("9) 失败必须显式失败（不联网）")
r_nokey = sr.rewrite(sample, sr.RewriteSpec(), api_keys={})
check("没 Key 时返回失败而不是空稿",
      r_nokey.ok_count == 0 and bool(r_nokey.errors), str(r_nokey.errors))
check("没 Key 时错误说明指到 Key 上",
      "Key" in (r_nokey.errors[0] if r_nokey.errors else ""), str(r_nokey.errors))
r_cdp = sr.rewrite(sample, sr.RewriteSpec(engine="doubao"), api_keys={"doubao": "x"})
check("CDP 通道明确告知未开放",
      r_cdp.ok_count == 0 and "未开放" in (r_cdp.errors[0] if r_cdp.errors else ""),
      str(r_cdp.errors))
check("引擎层已提供通用 chat_completion", hasattr(gm, "chat_completion"), "")
check("chat_completion 缺 Key 时返回失败",
      gm.chat_completion(gm.ENGINE_SPECS["deepseek"], "s", "u", api_keys={})[0] is False, "")

print()
print("10) 历史与成本估算")
h = sr.save_history(res)
check("历史文件已写入", h.exists(), "")
check("历史可回读", any(i.get("started_at") == "2026-10-12 12:00"
                        for i in sr.load_history(limit=5)), "")
h.unlink(missing_ok=True)
check("成本估算 = 版本数 × 单次耗时",
      sr.estimate_cost(sr.RewriteSpec(versions=3))["calls"] == 3, "")

print()
print("11) 功能位 / 导航 / 教程 / 图标 同步")
check("licensing.FEATURES 登记了文案仿写", "script" in licensing.FEATURES, "")
check("老授权码策略已定（auto = 不收回已售功能）",
      licensing.FEATURE_BACKFILL.get("script") == "auto",
      str(licensing.FEATURE_BACKFILL.get("script")))

mw = (ROOT / "gui_qt" / "main_window.py").read_text(encoding="utf-8")
check("导航里有「文案仿写」", '"文案仿写"' in mw, "")
check("页面映射里有 ScriptPage", '"文案仿写": ScriptPage' in mw, "")
check("页面导入了 ScriptPage", "from gui_qt.pages.script_page import ScriptPage" in mw, "")
gd = (ROOT / "gui_qt" / "widgets" / "guide.py").read_text(encoding="utf-8")
check("教程文案里有「文案仿写」", '"文案仿写"' in gd, "")
check("教程写明了「仿的是结构不是措辞」", "仿的是结构与节奏" in gd, "")
ic = (ROOT / "gui_qt" / "icons.py").read_text(encoding="utf-8")
check("导航图标里有「文案仿写」", '"文案仿写"' in ic, "")
sp_spec = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
check("打包 spec 里加了 script_rewriter",
      "justoneapi_collector.script_rewriter" in sp_spec, "")

print()
print("12) 卖点命中判定（不能让讲到了的卖点白扣 10 分）")
_spoken = "汇报只说结果，请示带方案A和B，总结拎关键，规划讲标准"
check("换个说法讲到也算命中",
      sr._point_hit("汇报工作说结果", _spoken) is True)
check("完全没提到的卖点不刷分",
      sr._point_hit("三档力度", _spoken) is False)
check("纯数字卖点按原样比对", sr._point_hit("45", "静音45分贝") is True)

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过：文案仿写模块可用。")
