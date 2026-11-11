"""文案仿写真机探针：用本机 config.json 里的 Key 真跑一次，看输出能不能用。

跑法：C:/Python310/python.exe tools/_probe_script_live.py

为什么必须真跑：
    GEO 那次已经吃过亏 —— 网上查来的模型名（kimi-k3）根本不存在，
    照着写完就是 100% 失败。仿写的提示词更长、输出结构更复杂，
    不真跑一遍根本不知道模型会不会按「分镜」格式输出。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import script_rewriter as sr          # noqa: E402

CFG = Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "config.json"
keys = (json.loads(CFG.read_text(encoding="utf-8")).get("geo_api_keys") or {}) \
    if CFG.exists() else {}
if not keys.get("deepseek"):
    print("本机没有保存 DeepSeek Key（config.json 的 geo_api_keys），跳过真机探针")
    sys.exit(0)

# 一段真实感的对标原文（仿写对象）
SOURCE = (
    "90% 的人买按摩棒都买错了，不是力度越大越好。"
    "我前后买过五个，踩了三次坑才搞明白。"
    "第一个坑是有刷电机，用三个月就开始咔咔响；第二个坑是电池虚标，"
    "标称续航六小时，实际一个半小时就没电；第三个坑最隐蔽，噪音大到你不敢在办公室用。"
    "后来我只认三个参数：无刷电机、Type-C 快充、45 分贝以下。"
    "按这个标准挑，三百块以内的也有能打的。"
    "想要清单的评论区扣 1，我一个个回。"
)


def run(genre: str, seconds: int, tone: str = "pro"):
    spec = sr.RewriteSpec(genre=genre, seconds=seconds, tone=tone,
                          audience="久坐上班族", product="某某按摩棒",
                          points=["无刷电机", "Type-C 快充", "45 分贝以下"],
                          keywords=["按摩棒"], versions=1,
                          engine="deepseek", temperature="lively")
    print("=" * 70)
    print(f"体裁：{spec.genre_spec.label}　时长：{seconds} 秒　语气：{tone}")
    res = sr.rewrite(SOURCE, spec, api_keys=keys,
                     progress=lambda d, t, m: print("   ·", m))
    if res.ok_count == 0:
        print("  ✗ 生成失败：", res.errors)
        return False
    d = res.items[0]
    print(f"  评分 {d.score} 分　{d.chars} 字　约 {d.est_seconds:.0f} 秒"
          f"（目标 {seconds} 秒）　分镜 {len(d.shots)} 条")
    print("  标题：", " | ".join(d.title_options))
    print("  钩子：", d.hook)
    for s in d.shots[:8]:
        print(f"    [{s.get('时间轴')}] 画面：{s.get('画面')} ｜ 口播：{s.get('口播')}")
    print("  结尾：", d.cta)
    print("  话题：", " ".join(d.hashtags))
    print("  违规：", d.violations or "无")
    for t in d.score_detail:
        print("    -", t)
    return True


ok1 = run("sell", 30)
ok2 = run("story", 30, "fun")

# 顺带验一次导出
if ok1:
    spec = sr.RewriteSpec(genre="sell", seconds=30, product="某某按摩棒")
    res = sr.rewrite(SOURCE, spec, api_keys=keys)
    out = ROOT / "_tmp_script_out" / "真机验证.xlsx"
    sr.export_excel(res, out)
    print("=" * 70)
    print("导出：", out, out.stat().st_size, "字节")

print("=" * 70)
print("真机结果：", "可用" if (ok1 and ok2) else "有体裁生成失败，需排查")
