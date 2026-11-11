# -*- coding: utf-8 -*-
"""GEO 模块离线自测（不联网、不花钱）。

覆盖：品牌变体、信源识别、首次位次、情感判定、接口解析、采样汇总、
      竞品对比、建议生成、快照存取、Excel 导出、授权功能位。

用法：
    C:/Python310/python.exe tools/_validate_geo.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from justoneapi_collector import geo_monitor as gm  # noqa: E402
from justoneapi_collector import licensing  # noqa: E402

# 提前留存真实的 _post_json：后面要 mock 掉它做假接口，但重试逻辑住在真身里
_REAL_POST_JSON = gm._post_json

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASSED.append(name)
    else:
        FAILED.append(f"{name}｜{extra}")
        print(f"  ✗ {name}｜{extra}")


# ---------------------------------------------------------------- 1 品牌变体
print("1) 品牌变体")
v = gm.brand_variants("斯堪尼亚", ["Scania", "斯堪尼亚卡车有限公司"])
check("原名在变体里", "斯堪尼亚" in v, str(v))
check("英文名在变体里", "Scania" in v, str(v))
check("英文名小写也匹配", "scania" in v, str(v))
check("公司后缀被剥出字号", "斯堪尼亚卡车" in v, str(v))
check("变体去空", "" not in v, str(v))

# ---------------------------------------------------------------- 2 信源识别
print("2) 引用信源识别")
src = gm.extract_sources("参见 https://www.zhihu.com/p/123 与 "
                         "https://www.xiaohongshu.com/x/y，还有 "
                         "https://baijiahao.baidu.com/a?b=1，自家 https://brand.com/")
check("知乎", "知乎" in src, str(src))
check("小红书", "小红书" in src, str(src))
check("百家号", "百家号" in src, str(src))
check("未知域名保留域名本身", "brand.com" in src, str(src))
check("信源去重", len(src) == len(set(src)), str(src))
check("空文本不报错", gm.extract_sources("") == [], str(gm.extract_sources("")))

# ---------------------------------------------------------------- 3 首次位次
print("3) 首次提及位次")
_sample_text = "x" * 30 + "斯堪尼亚" + "y" * 30
pos = gm.first_position(_sample_text, ["斯堪尼亚"])
check("位置在 0~1 之间", 0.0 <= pos <= 1.0, str(pos))
check("位置 = 首次出现下标 ÷ 全文长度",
      abs(pos - 30 / len(_sample_text)) < 1e-6, f"{pos} vs {30 / len(_sample_text)}")
check("未提及返回 -1", gm.first_position("随便写点什么", ["斯堪尼亚"]) == -1.0)

# ---------------------------------------------------------------- 4 情感
print("4) 情感倾向")
check("正面词识别", gm.detect_sentiment("斯堪尼亚很靠谱，值得推荐", ["斯堪尼亚"]) == "正面")
check("负面词识别", gm.detect_sentiment("斯堪尼亚不建议买，容易踩坑", ["斯堪尼亚"]) == "负面")
check("无情感词为中性", gm.detect_sentiment("斯堪尼亚成立于1891年", ["斯堪尼亚"]) == "中性")
check("未提及时为中性", gm.detect_sentiment("奔驰不错", ["斯堪尼亚"]) == "中性")

# ---------------------------------------------------------------- 5 接口解析
print("5) 接口通道")
r0 = gm.ask_engine(gm.ENGINE_SPECS["deepseek"], "测试", api_keys={})
check("没填 Key 时明确报错", (not r0.ok) and "Key" in r0.error, r0.error)
check("没填 Key 不抛异常", isinstance(r0.error, str))

ANSWERS = {
    "重型卡车哪个品牌好": "推荐斯堪尼亚（Scania），可靠性领先，参考 https://www.zhihu.com/p/1 。奔驰也不错。",
    "进口卡车怎么选": "沃尔沃和奔驰都值得考虑，参考 https://baijiahao.baidu.com/x 。",
}
_calls = {"n": 0}


def fake_post(url, body, api_key, timeout=60.0, **_kw):
    _calls["n"] += 1
    q = (body.get("messages") or [{}])[1].get("content", "")
    text = ANSWERS.get(q, "不知道，没有相关信息。")
    return True, json.dumps({"choices": [{"message": {"content": text}}]}, ensure_ascii=False)


gm._post_json = fake_post
r1 = gm.ask_engine(gm.ENGINE_SPECS["deepseek"], "重型卡车哪个品牌好",
                   api_keys={"deepseek": "sk-test"})
check("接口正常返回", r1.ok and "斯堪尼亚" in r1.text, r1.error or r1.text[:40])
check("抽出信源", "知乎" in r1.sources, str(r1.sources))
check("耗时被记录", r1.elapsed >= 0)

r2 = gm.ask_engine(gm.ENGINE_SPECS["doubao"], "进口卡车怎么选", api_keys={})
check("豆包 CDP 通道给出明确说明", (not r2.ok) and "尚未接通" in r2.error, r2.error)

# 模型名：默认不能是已下线的旧名，且要能被界面覆盖（各家模型名下线很快）
check("Kimi 默认模型不是已下线的 kimi-latest",
      gm.ENGINE_SPECS["kimi"].model != "kimi-latest", gm.ENGINE_SPECS["kimi"].model)
check("Kimi 默认模型不是已下线的 moonshot-v1 系列",
      not gm.ENGINE_SPECS["kimi"].model.startswith("moonshot-v1"),
      gm.ENGINE_SPECS["kimi"].model)
_seen_models: list[str] = []


def fake_post_model(url, body, api_key, timeout=60.0, **_kw):
    _seen_models.append(body.get("model", ""))
    return True, json.dumps({"choices": [{"message": {"content": "没有相关信息"}}]},
                            ensure_ascii=False)


gm._post_json = fake_post_model
gm.ask_engine(gm.ENGINE_SPECS["kimi"], "测试", api_keys={"kimi": "k"},
              models={"kimi": "my-custom-model"})
check("模型名可被覆盖", _seen_models[-1] == "my-custom-model", str(_seen_models[-1]))
gm.ask_engine(gm.ENGINE_SPECS["kimi"], "测试", api_keys={"kimi": "k"})
check("不传模型名时用引擎表默认值",
      _seen_models[-1] == gm.ENGINE_SPECS["kimi"].model, str(_seen_models[-1]))
gm._post_json = fake_post      # 还原，后面的整轮体检用例还要用它

# 温度兼容：Kimi 只允许 temperature=1.0，传别的直接 400（2026-10-12 真机实测）
check("Kimi 默认温度是 1.0（否则接口直接 400）",
      gm.ENGINE_SPECS["kimi"].temperature == 1.0, str(gm.ENGINE_SPECS["kimi"].temperature))
check("DeepSeek 默认温度是 0.6",
      gm.ENGINE_SPECS["deepseek"].temperature == 0.6,
      str(gm.ENGINE_SPECS["deepseek"].temperature))

_temp_seen: list = []


def fake_post_temp(url, body, api_key, timeout=60.0, **_kw):
    _temp_seen.append(body.get("temperature", "<none>"))
    if body.get("temperature") not in (None, 1.0):
        return False, ('接口返回 400：{"error":{"message":'
                       '"invalid temperature: only 1 is allowed for this model"}}')
    return True, json.dumps({"choices": [{"message": {"content": "斯堪尼亚不错"}}]},
                            ensure_ascii=False)


gm._post_json = fake_post_temp
_bad = gm.EngineSpec(key="ds", label="DS", kind="api",
                     base_url="https://x.invalid/v1", model="m", temperature=0.6)
r3 = gm.ask_engine(_bad, "测试", api_keys={"ds": "k"})
check("温度不被接受时自动降级重试成功", r3.ok and "斯堪尼亚" in r3.text, r3.error)
check("降级顺序是 0.6 -> 1.0", _temp_seen == [0.6, 1.0], str(_temp_seen))
gm._post_json = fake_post

# 限流 / 服务端抖动要自动重试，Key 错不能傻重试（真机：Kimi 有组织级限流）
_real_urlopen = urllib.request.urlopen
_real_sleep = gm.time.sleep
gm.time.sleep = lambda _s: None      # 退避等待在测试里跳过，不然要跑好几秒
_hits = {"n": 0}


def _fake_urlopen_factory(fail_code: int, fail_times: int, ok_body: str):
    def _f(req, timeout=None, context=None):
        _hits["n"] += 1
        if _hits["n"] <= fail_times:
            raise urllib.error.HTTPError(getattr(req, "full_url", "u"), fail_code,
                                         "err", {}, None)

        class _R:
            def read(self):
                return ok_body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False
        return _R()
    return _f


_ok_body = json.dumps({"choices": [{"message": {"content": "斯堪尼亚"}}]}, ensure_ascii=False)
_hits["n"] = 0
urllib.request.urlopen = _fake_urlopen_factory(429, 2, _ok_body)
ok429, txt429 = gm._post_json("https://x.invalid/v1/chat/completions",
                              {"model": "m"}, "k", timeout=5)
check("429 限流会自动重试并最终成功", ok429 and "斯堪尼亚" in txt429, txt429[:60])
check("429 确实重试了（调用次数 > 1）", _hits["n"] > 1, str(_hits["n"]))

_hits["n"] = 0
urllib.request.urlopen = _fake_urlopen_factory(500, 1, _ok_body)
ok500, _t5 = gm._post_json("https://x.invalid/v1/chat/completions",
                           {"model": "m"}, "k", timeout=5)
check("服务端 5xx 会自动重试", ok500, _t5[:60])

_hits["n"] = 0
urllib.request.urlopen = _fake_urlopen_factory(401, 9, _ok_body)
ok401, txt401 = gm._post_json("https://x.invalid/v1/chat/completions",
                              {"model": "m"}, "k", timeout=5)
check("401 Key 错立即失败不重试", (not ok401) and "401" in txt401, txt401[:60])
check("401 只请求了 1 次（没傻等）", _hits["n"] == 1, str(_hits["n"]))

urllib.request.urlopen = _real_urlopen
gm.time.sleep = _real_sleep

# ---------------------------------------------------------------- 6 整轮体检
print("6) 一轮体检汇总")
_calls["n"] = 0          # 上面手动调过一次接口，这里重新计数
report = gm.scan(
    brand="斯堪尼亚",
    queries=["重型卡车哪个品牌好", "进口卡车怎么选"],
    engines=["deepseek"],
    aliases=["Scania"],
    competitors=["奔驰", "沃尔沃"],
    samples=2,
    api_keys={"deepseek": "sk-test"},
    workers=2,
)
check("任务数 = 词 × 引擎 × 采样", _calls["n"] == 4, str(_calls["n"]))
check("有效回答数 4", report.total_answers == 4, str(report.total_answers))
check("提及次数 2", report.total_mentions == 2, str(report.total_mentions))
check("整体提及率 50%", abs(report.mention_rate - 0.5) < 1e-6, str(report.mention_rate))
check("按引擎统计正确", abs(report.by_engine().get("deepseek", 0) - 0.5) < 1e-6,
      str(report.by_engine()))
cr = dict((n, h) for n, h, _ in report.competitor_ranking())
# 「奔驰」在两个问题的回答里都出现了（4 个回答全覆盖），「沃尔沃」只在进口卡车那条
check("竞品奔驰被提 4 次", cr.get("奔驰") == 4, str(cr))
check("竞品沃尔沃被提 2 次", cr.get("沃尔沃") == 2, str(cr))
sr = dict(report.source_ranking())
check("信源含知乎", "知乎" in sr, str(sr))
check("信源含百家号", "百家号" in sr, str(sr))
check("明细条数 = 词 × 引擎", len(report.items) == 2, str(len(report.items)))
hot = [i for i in report.items if i.query == "重型卡车哪个品牌好"][0]
check("命中词提及率 100%", abs(hot.mention_rate - 1.0) < 1e-6, str(hot.mention_rate))
cold = [i for i in report.items if i.query == "进口卡车怎么选"][0]
check("未命中词提及率 0", cold.mention_rate == 0.0, str(cold.mention_rate))
check("未命中词位次为 -1", cold.first_pos == -1.0, str(cold.first_pos))
check("命中词有位次", 0.0 <= hot.first_pos <= 1.0, str(hot.first_pos))
check("命中词情感为正面", hot.sentiment == "正面", hot.sentiment)
check("回答样本被保留", "斯堪尼亚" in hot.answer_sample, hot.answer_sample[:40])

# ---------------------------------------------------------------- 7 建议
print("7) 优化建议")
tips = report.suggestions()
check("建议不少于 3 条", len(tips) >= 3, str(len(tips)))
check("给出补内容方向", any("补内容" in t for t in tips), str(tips))
check("给出竞品压制提醒", any("竞品压制" in t for t in tips), str(tips))
check("提醒按月复测", any("复测" in t for t in tips), str(tips))
check("建议里带品牌名", any("斯堪尼亚" in t or "提及率" in t for t in tips), str(tips[:1]))

empty = gm.ScanReport(brand="某品牌", queries=["a"], engines=[], samples=1)
check("无回答时给出可操作提示", "没有拿到任何有效回答" in empty.suggestions()[0],
      empty.suggestions()[0])

# ---------------------------------------------------------------- 8 快照
print("8) 快照与趋势")
tmp = tempfile.mkdtemp(prefix="geo_test_")
gm.snapshot_dir = lambda: Path(tmp)
p1 = gm.save_snapshot(report)
check("快照文件已生成", p1.is_file(), str(p1))
snaps = gm.load_snapshots()
check("快照可读回", len(snaps) == 1, str(len(snaps)))
check("快照含整体提及率", abs(float(snaps[0]["mention_rate"]) - 0.5) < 1e-6,
      str(snaps[0].get("mention_rate")))
check("快照含逐条明细", len(snaps[0]["items"]) == 2, str(len(snaps[0]["items"])))
check("快照含品牌名", snaps[0]["brand"] == "斯堪尼亚", snaps[0]["brand"])
gm.save_snapshot(report)
check("多次快照按时间累积", len(gm.load_snapshots()) == 2, str(len(gm.load_snapshots())))

# ---------------------------------------------------------------- 9 导出
print("9) Excel 报告导出")
try:
    import openpyxl  # noqa: F401
    xlsx = Path(tmp) / "报告.xlsx"
    out = gm.export_report_excel(report, xlsx)
    check("导出文件存在", out.is_file(), str(out))
    wb = openpyxl.load_workbook(out)
    check("含概览表", "概览" in wb.sheetnames, str(wb.sheetnames))
    check("含明细表", "明细" in wb.sheetnames, str(wb.sheetnames))
    check("含信源表", "引用信源" in wb.sheetnames, str(wb.sheetnames))
    check("含建议表", "优化建议" in wb.sheetnames, str(wb.sheetnames))
    ws = wb["概览"]
    text_all = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
    check("概览含整体提及率", "整体提及率" in text_all, text_all[:80])
    check("概览含口径说明", "口径说明" in text_all, text_all[:80])
    check("明细表有表头", wb["明细"].cell(row=1, column=1).value == "提问词")
except ImportError:
    check("openpyxl 可用（否则跳过导出用例）", True, "本机未装 openpyxl，已跳过")

# ---------------------------------------------------------------- 10 授权
print("10) 授权功能位")
check("功能清单已含 geo", "geo" in licensing.FEATURES, str(list(licensing.FEATURES)))
check("ALL_FEATURES 含 geo", "geo" in licensing.ALL_FEATURES, str(licensing.ALL_FEATURES))
check("geo 有中文名", bool(licensing.FEATURES.get("geo")), str(licensing.FEATURES.get("geo")))
ok, why = licensing.check("geo")
check("check('geo') 返回二元组", isinstance(ok, bool) and isinstance(why, str), f"{ok},{why}")

# ------------------------------------------- 11 失败 ≠ 未提及（真机踩出来的）
print("11) 失败不能被当成“未提及”")
rep_fail = gm.scan(brand="某品牌", queries=["q1", "q2"], engines=["deepseek"],
                   api_keys={}, samples=1)
check("没 Key 时所有条目都标记为失败",
      len(rep_fail.items) == 2 and all(i.failed for i in rep_fail.items),
      str([i.failed for i in rep_fail.items]))
check("失败条目状态=失败", rep_fail.items[0].to_row()["状态"] == "失败",
      str(rep_fail.items[0].to_row().get("状态")))
check("失败条目提及率显示 - 而不是 0%",
      rep_fail.items[0].to_row()["提及率"] == "-", str(rep_fail.items[0].to_row()["提及率"]))
check("失败条目采样次数 0/1", rep_fail.items[0].to_row()["采样次数"] == "0/1",
      str(rep_fail.items[0].to_row()["采样次数"]))
check("失败条目在回答样本里写明不能当未提及",
      "不能当作" in rep_fail.items[0].to_row()["回答样本"],
      rep_fail.items[0].to_row()["回答样本"][:40])
check("全部失败时无有效回答", rep_fail.total_answers == 0, str(rep_fail.total_answers))
check("失败被记进 errors", any("失败" in e for e in rep_fail.errors), str(rep_fail.errors[:1]))
check("全部失败时建议提示限流", any("限流" in t for t in rep_fail.suggestions()),
      str(rep_fail.suggestions()[:1]))

# 部分失败：成功率和失败率必须分开统计，不能互相污染
def fake_post_partial(url, body, api_key, timeout=60.0, **_kw):
    q = (body.get("messages") or [{}])[1].get("content", "")
    if q == "会失败的提问":
        return False, "调用太频繁被限流（429），已自动重试仍未成功"
    return True, json.dumps({"choices": [{"message": {"content": "斯堪尼亚不错"}}]},
                            ensure_ascii=False)


gm._post_json = fake_post_partial
rep_mix = gm.scan(brand="斯堪尼亚", queries=["正常提问", "会失败的提问"],
                  engines=["deepseek"], api_keys={"deepseek": "k"}, samples=1)
check("部分失败：失败数=1", rep_mix.failed_count == 1, str(rep_mix.failed_count))
check("部分失败：有效回答=1", rep_mix.total_answers == 1, str(rep_mix.total_answers))
check("部分失败：提及率只按成功回答算=100%", abs(rep_mix.mention_rate - 1.0) < 1e-6,
      str(rep_mix.mention_rate))
check("部分失败：建议里有数据不全警告",
      any("数据不全" in t for t in rep_mix.suggestions()), str(rep_mix.suggestions()[:1]))
gm._post_json = fake_post

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    for f in FAILED:
        print("  ✗ " + f)
    sys.exit(1)
print("GEO 模块自测全部通过 ✔")
