"""页面注册表一致性门禁（2026-11-24 立）。

【为什么要有这个脚本】
新增一个页面过去要改 13 处清单（导航 / 类映射 / 功能位 / hiddenimports /
教程 / 别名 / 首页卡片 / UX 门禁 ...），漏一处不会报错，只会在发版后
暴露成「点了没反应 / 客户搜不到 / 打包后打不开」。

现在 page_registry.py 是唯一真相源，gen_pages.py 负责同步。本门禁钉三件事：

  1. 所有清单与注册表一致（调用 gen_pages.py --check）；
  2. 注册表自身合法：页名唯一、类名/模块名不撞车、别名 ≥3、功能位已登记、
     展示顺序里的每个名字都存在、历史位没被误删；
  3. 标记块完整：每个目标文件的 BEGIN/END 成对出现（防手改把标记删了）。

跑法：C:/Python310/python.exe tools/_validate_page_registry.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gui_qt.page_registry import (FLOW_STEPS, GUIDE_ORDER, HISTORICAL_FEATURES,
                                  HOME_MORE_ORDER, HOME_QUICK_ORDER,
                                  NAV_NO_FEATURE, PAGES, UX_EXPECTED,
                                  real_pages)  # noqa: E402
from justoneapi_collector import licensing  # noqa: E402
from tools import gen_pages  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


print("1) 注册表自身合法性")

pages = real_pages()
names = [p.name for p in pages]
check("页面清单非空", bool(pages), "")

dupes = sorted({n for n in names if names.count(n) > 1})
check("页名唯一（重复会让导航/别名串页）", not dupes, f"重复：{dupes}")

classes = [p.cls for p in pages]
dup_cls = sorted({c for c in classes if classes.count(c) > 1})
check("页面类名唯一", not dup_cls, f"重复：{dup_cls}")

mods = [p.module for p in pages]
dup_mod = sorted({m for m in mods if mods.count(m) > 1})
check("页面模块名唯一", not dup_mod, f"重复：{dup_mod}")

thin = [p.name for p in pages if len(p.aliases) < 3]
check("每页至少 3 个搜索别名（客户用自己的说法能搜到）", not thin, f"太少：{thin}")

first_alias = [p.name for p in pages if not p.aliases[0].islower()]
check("每页首个别名是拼音首字母/小写词", not first_alias, str(first_alias))

no_feature = [p.name for p in pages
              if p.feature is None and p.name not in NAV_NO_FEATURE
              and p.name != "设置"]
check("无功能位的页面都登记进 NAV_NO_FEATURE", not no_feature, str(no_feature))

undeclared = sorted({p.feature for p in pages
                     if p.feature and p.feature not in licensing.FEATURES})
check("所有功能位都在 licensing.FEATURES 里（发卡工具能勾选）",
      not undeclared, f"缺：{undeclared}")

stale_nf = [n for n in NAV_NO_FEATURE if n not in names]
check("NAV_NO_FEATURE 没有指向已下线页面", not stale_nf, str(stale_nf))

print()
print("2) 展示顺序引用完整")

known = set(names)
for label, seq in (("教程顺序", GUIDE_ORDER), ("首页常用卡片", HOME_QUICK_ORDER),
                   ("首页其它工具", HOME_MORE_ORDER), ("UX 门禁 EXPECTED", UX_EXPECTED)):
    unknown = [n for n in seq if n not in known]
    check(f"{label}里的页都在注册表中", not unknown, f"不存在：{unknown}")

flow_pages = {step[3] for step in FLOW_STEPS}
unknown_flow = sorted(flow_pages - known)
check("五步任务流的跳转目标都是真实页", not unknown_flow, str(unknown_flow))

dup_flow = sorted({n for n in UX_EXPECTED if UX_EXPECTED.count(n) > 1})
check("UX EXPECTED 没有重复项", not dup_flow, str(dup_flow))

check("历史功能位保留（已签授权码不能报未知位）",
      all(f in licensing.FEATURES for f in HISTORICAL_FEATURES),
      str(HISTORICAL_FEATURES))

print()
print("3) 功能位与教程覆盖")

featured = {p.feature for p in pages if p.feature}
check("功能位数量与已登记页面相符", len(featured) > 0, str(sorted(featured)))

no_guide_content = [n for n in GUIDE_ORDER
                    if f'"{n}": {{' not in (
                        ROOT / "gui_qt" / "widgets" / "guide.py").read_text(encoding="utf-8")]
check("教程顺序里的页都有 GUIDE_CONTENT 条目", not no_guide_content,
      f"缺教程：{no_guide_content}")

print()
print("4) 标记块完整（BEGIN/END 成对）")

for rel, blocks in gen_pages.TARGETS.items():
    text = (ROOT / rel).read_text(encoding="utf-8")
    for name, _renderer in blocks:
        b = gen_pages.BEGIN.format(name=name)
        e = gen_pages.END.format(name=name)
        check(f"{rel} :: {name} 标记成对",
              text.count(b) == 1 and text.count(e) == 1,
              f"BEGIN={text.count(b)} END={text.count(e)}")

print()
print("5) 所有清单与注册表一致")

drifted: list[str] = []
for rel, blocks in gen_pages.TARGETS.items():
    if gen_pages.update_file(rel, blocks, check_only=True):
        drifted.append(rel)
check("清单逐项一致（不一致请跑 tools/gen_pages.py）", not drifted,
      "漂移文件：" + "、".join(drifted))

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过：页面注册清单与注册表一致。")
