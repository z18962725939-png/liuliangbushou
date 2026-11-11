# -*- coding: utf-8 -*-
"""GEO 端到端真机体检：真实 Key + 真实接口，跑完一整轮并导出 Excel 报告。

用法：
    set GEO_DS_KEY=sk-xxxx
    set GEO_KIMI_KEY=sk-yyyy
    python tools/_probe_geo_e2e.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector import geo_monitor as gm          # noqa: E402

BRAND = "斯堪尼亚"
ALIASES = ["Scania"]
COMPETITORS = ["沃尔沃", "奔驰", "曼恩"]
QUERIES = [
    "进口重卡品牌怎么选？",
    "长途干线物流买什么卡车好？",
    "国产重卡和进口重卡差距大吗，值得多花钱吗？",
    "斯堪尼亚卡车口碑怎么样？",
    "30万到50万预算买什么牵引车？",
]
ENGINES = ["deepseek", "kimi"]
SAMPLES = 1


def main():
    keys = {
        "deepseek": os.environ.get("GEO_DS_KEY", "").strip(),
        "kimi": os.environ.get("GEO_KIMI_KEY", "").strip(),
    }
    keys = {k: v for k, v in keys.items() if v}
    if not keys:
        print("没有可用的 Key，退出")
        return 1
    print(f"引擎：{list(keys)}   提问词 {len(QUERIES)} 个   每词采样 {SAMPLES} 次")

    t0 = time.time()

    def on_prog(done, total, text):
        if done % 2 == 0 or done == total:
            print(f"  [{done}/{total}] {text}")

    rep = gm.scan(brand=BRAND, queries=QUERIES, engines=list(keys),
                  aliases=ALIASES, competitors=COMPETITORS,
                  samples=SAMPLES, api_keys=keys, workers=3,
                  progress=on_prog)
    el = time.time() - t0

    print(f"\n{'=' * 66}\n结果（耗时 {el:.1f}s）\n{'=' * 66}")
    print(f"品牌            ：{rep.brand}")
    print(f"有效回答        ：{rep.total_answers}")
    print(f"整体提及率      ：{rep.mention_rate * 100:.1f}%")
    print(f"各引擎提及率    ：{ {k: f'{v * 100:.0f}%' for k, v in rep.by_engine().items()} }")
    print(f"引用信源 TOP    ：{rep.source_ranking()[:6] or '无'}")
    print(f"竞品对比        ：{[(n, h, f'{r * 100:.0f}%') for n, h, r in rep.competitor_ranking()]}")
    print(f"异常            ：{rep.errors or '无'}")
    print("\n明细：")
    for i in rep.items:
        row = i.to_row()
        print(f"  [{row['引擎']}] {row['提问词'][:20]:<22} 提及率={row['提及率']:<5} "
              f"位次={row['首次位次']:<5} 情感={row['情感']} 竞品={row['竞品提及']}")
    print("\n优化建议：")
    for n, t in enumerate(rep.suggestions(), 1):
        print(f"  {n}. {t}")

    out = Path(os.environ.get("TEMP", ".")) / "_geo_e2e_report.xlsx"
    p = gm.export_report_excel(rep, out)
    print(f"\nExcel 报告已导出：{p}  （{p.stat().st_size:,} 字节）")

    snap = gm.save_snapshot(rep)
    print(f"历史快照        ：{snap.name}")
    print(f"快照总数        ：{len(gm.load_snapshots())}")

    ok = rep.total_answers > 0 and p.exists()
    print(f"\n端到端结论：{'✔ 通过' if ok else '✗ 失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
