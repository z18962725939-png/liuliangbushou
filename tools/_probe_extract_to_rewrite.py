# -*- coding: utf-8 -*-
"""端到端：已提取的口播稿 → 按结构仿写（走真实 DeepSeek Key）。

跑法：C:/Python310/python.exe tools/_probe_extract_to_rewrite.py

验证「链接提取」和「文案仿写」两段是不是真接得上：
带上时间轴之后，提示词里会多出「原片真实节奏」，模型才知道该写到多长。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import script_rewriter as sr               # noqa: E402
from justoneapi_collector import video_script_extract as vse         # noqa: E402

VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else "6577936098192788749"


def main():
    cache = vse.cache_dir()
    txt = cache / f"{VIDEO_ID}.txt"
    srt = cache / f"{VIDEO_ID}.srt"
    if not txt.exists():
        print(f"× 没有这份提取结果：{txt}\n先跑 tools/_probe_link_extract.py 提取一次")
        return 1

    cfg_p = Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "config.json"
    cfg = json.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
    keys = cfg.get("geo_api_keys") or {}
    if not keys.get("deepseek"):
        print("× 本机没保存 DeepSeek Key，先在软件里「文案仿写」页填一次")
        return 1

    source = txt.read_text(encoding="utf-8", errors="ignore").strip()
    segs = vse.parse_srt(srt.read_text(encoding="utf-8", errors="ignore")) if srt.exists() else []

    genre = sr.guess_genre(source, "")
    real = max((s.end for s in segs), default=0.0)
    near = min(sr.DURATION_CHOICES, key=lambda c: abs(c - real)) if real else 30
    print(f"提取稿：{len(source)} 字 / {real:.0f} 秒 / 体裁建议 {genre} / 目标 {near} 秒")

    spec = sr.RewriteSpec(
        genre=genre, seconds=near, tone="pro",
        audience="刚进职场的新人", product="（演示：职场沟通课程）",
        points=["汇报工作说结果", "请示工作说方案", "总结工作说关键点", "规划工作说标准"],
        keywords=[], forbidden_extra=[], versions=2, engine="deepseek",
        temperature="lively",
    )

    res = sr.rewrite(source=source, spec=spec, api_keys=keys, timeout=180.0,
                     progress=lambda d, t, m: print(f"  [{d}/{t}] {m}"),
                     segments=segs)
    print("=" * 68)
    print(f"成功 {res.ok_count} / {len(res.items)}，失败原因：{res.errors}")
    for d in res.items:
        if not d.ok:
            print(f"  第{d.index}版 失败：{d.error}")
            continue
        tt = d.title_options[0] if d.title_options else "—"
        print(f"  第{d.index}版：{d.score} 分 / {d.chars} 字 / 约 {d.est_seconds:.0f} 秒 / "
              f"标题「{tt}」/ 违规词 {len(d.violations)}")
        for line in (d.score_detail or [])[:6]:
            print(f"      · {line}")
    best = res.best()
    if best and best.ok:
        print("-" * 68)
        print(best.detail_text() if hasattr(best, "detail_text") else sr.to_markdown(best, spec))
        out = ROOT / "_tmp_script_out"
        out.mkdir(exist_ok=True)
        p = sr.export_excel(res, out / "链接提取到仿写验证.xlsx")
        print(f"已导出：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
