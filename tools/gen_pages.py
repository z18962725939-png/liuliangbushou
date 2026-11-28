"""页面清单生成器：从 gui_qt/page_registry.py 渲染到各文件的标记块。

用法：
    C:/Python310/python.exe tools/gen_pages.py            # 生成（写文件）
    C:/Python310/python.exe tools/gen_pages.py --check    # 只校验，不写

★ 为什么需要生成器（而不是运行时从注册表 import）：
  6 个老门禁用 AST / 字符串切源码字面量读 NAV_GROUPS、_PAGE_CLASS 等清单，
  改成运行时派生会让它们全部失效。所以保留各文件里的字面量，由本脚本
  按标记块同步 —— 新增页面只改注册表 + 跑一次本脚本，全站清单自动对齐。

标记格式（成对出现，生成器只替换标记之间的内容）：
    # >>> PAGE-REGISTRY BEGIN block_name >>>
    ... 生成内容 ...
    # >>> PAGE-REGISTRY END block_name <<<
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gui_qt.page_registry import (FLOW_STEPS, GUIDE_ORDER, HOME_MORE_ORDER,
                                  HOME_QUICK_ORDER, NAV_NO_FEATURE,
                                  UX_EXPECTED, real_pages)  # noqa: E402

BEGIN = "# >>> PAGE-REGISTRY BEGIN {name} >>>"
END = "# >>> PAGE-REGISTRY END {name} <<<"


def py_str(s: str) -> str:
    """用双引号 + 转义输出 Python 字符串字面量。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def block(name: str, body: str) -> str:
    # ★ 末尾必须带换行：否则 END 标记会和紧随其后的代码黏在同一行，
    #   把 `missing = ...` 这类语句注释掉（首次接入时踩过，门禁当场抓到）。
    return f"{BEGIN.format(name=name)}\n{body.rstrip()}\n{END.format(name=name)}\n"


def indent(lines: list[str], n: int = 4) -> str:
    pad = " " * n
    return "\n".join(pad + ln if ln else ln for ln in lines)


# ---------------------------------------------------------------------------
# 各标记块的渲染函数
# ---------------------------------------------------------------------------

def render_page_imports() -> str:
    lines = []
    for p in real_pages():
        lines.append(f"from gui_qt.pages.{p.module} import {p.cls}")
    return "\n".join(lines)


def render_nav_groups() -> str:
    order = ["开始", "常用", "素材与短剧", "平台内容", "分析", "更多工具", "设置"]
    lines = ["NAV_GROUPS = ["]
    for group in order:
        members = [p for p in real_pages() if p.group == group]
        if not members:
            continue
        lines.append(f'    ("{group}", [')
        for p in members:
            if p.note:
                lines.append(f"        # {p.note}")
            lines.append(f'        "{p.name}",')
        lines.append("    ]),")
    lines.append("]")
    return "\n".join(lines)


def render_free_nav() -> str:
    lines = ["FREE_NAV_GROUPS = ["]
    seen: dict[str, list[str]] = {}
    for p in real_pages():
        if p.free_group:
            seen.setdefault(p.free_group, []).append(p.name)
    for group, names in seen.items():
        lines.append(f'    ("{group}", {names!r}),')
    lines.append("]")
    return "\n".join(lines)


def render_page_class() -> str:
    lines = ["_PAGE_CLASS = {"]
    for p in real_pages():
        lines.append(f'    "{p.name}": {p.cls},')
    lines.append("}")
    return "\n".join(lines)


def render_nav_inner() -> str:
    lines = ["_NAV_INNER = {"]
    for p in real_pages():
        if not p.icon:
            continue
        if p.icon_note:
            lines.append(f"    # {p.name}：{p.icon_note}")
        # 图标是拼接的 SVG 片段字面量；用 repr 会带单引号，门禁按内容取值不敏感
        lines.append(f"    {py_str(p.name)}: {p.icon!r},")
    lines.append("}")
    return "\n".join(lines)


def render_guide_order() -> str:
    lines = ["GUIDE_ORDER = ["]
    for name in GUIDE_ORDER:
        lines.append(f"    {py_str(name)},")
    lines.append("]")
    return "\n".join(lines)


def render_aliases() -> str:
    lines = ["_ALIASES = {"]
    for p in real_pages():
        vals = ", ".join(py_str(a) for a in p.aliases)
        lines.append(f"    {py_str(p.name)}: [{vals}],")
    lines.append("}")
    return "\n".join(lines)


def render_quick_actions() -> str:
    by_name = {p.name: p for p in real_pages()}
    lines = ["QUICK_ACTIONS = ["]
    for name in HOME_QUICK_ORDER:
        p = by_name.get(name)
        if not p or not p.quick:
            continue
        desc, badge = p.quick
        lines.append(f"    ({py_str(name)}, {py_str(desc)}, {py_str(badge)}),")
    lines.append("]")
    return "\n".join(lines)


def render_more_tools() -> str:
    names = list(HOME_MORE_ORDER)
    lines = ["MORE_TOOLS = ["]
    lines.append("    " + ", ".join(py_str(n) for n in names) + ",")
    lines.append("]")
    return "\n".join(lines)


def render_main_flow() -> str:
    lines = ["MAIN_FLOW = ["]
    for no, action, desc, target in FLOW_STEPS:
        lines.append(f"    ({py_str(no)}, {py_str(action)}, {py_str(desc)}, "
                     f"{py_str(target)}),")
    lines.append("]")
    return "\n".join(lines)


def render_spec_hidden() -> str:
    lines = []
    for p in real_pages():
        lines.append(f'    "gui_qt.pages.{p.module}",')
        for extra in p.spec_extra:
            lines.append(f'    "{extra}",')
    return "\n".join(lines)


def render_feature_sync() -> str:
    lines = ["PAGE_FEATURE: dict[str, tuple[str, str]] = {"]
    for p in real_pages():
        if p.feature is None:
            continue
        lines.append(f'    "{p.name}": ("{p.feature}", "{p.module}"),')
    lines.append("}")
    lines.append("")
    lines.append("PAGE_FEATURE_EXTRA: dict[str, str] = {")
    lines.append('    "export": "各页面的「导出」按钮共用，无独立页面",')
    for f in ("collect", "batch"):
        lines.append(f'    "{f}": "历史授权位：对应页面已下线，保留兼容已发授权码",')
    lines.append("}")
    lines.append("")
    lines.append("NAV_NO_FEATURE: set[str] = {")
    for name in NAV_NO_FEATURE:
        lines.append(f'    "{name}",')
    lines.append("}")
    return "\n".join(lines)


def render_ux_expected() -> str:
    names = list(UX_EXPECTED)
    lines = ["EXPECTED = ["]
    for i in range(0, len(names), 4):
        chunk = ", ".join(py_str(n) for n in names[i:i + 4])
        lines.append(f"    {chunk},")
    lines.append("]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 目标文件 -> [(块名, 渲染函数), ...]
# ---------------------------------------------------------------------------
TARGETS: dict[str, list[tuple[str, object]]] = {
    "gui_qt/main_window.py": [
        ("page_imports", render_page_imports),
        ("nav_groups", render_nav_groups),
        ("free_nav", render_free_nav),
        ("page_class", render_page_class),
    ],
    "gui_qt/icons.py": [
        ("nav_inner", render_nav_inner),
    ],
    "gui_qt/widgets/guide.py": [
        ("guide_order", render_guide_order),
    ],
    "gui_qt/widgets/command_palette.py": [
        ("aliases", render_aliases),
    ],
    "gui_qt/pages/home_page.py": [
        ("quick_actions", render_quick_actions),
        ("main_flow", render_main_flow),
        ("more_tools", render_more_tools),
    ],
    "justoneapi_collector.spec": [
        ("spec_hidden", render_spec_hidden),
    ],
    "tools/_validate_feature_sync.py": [
        ("feature_sync", render_feature_sync),
    ],
    "tools/_validate_ux.py": [
        ("ux_expected", render_ux_expected),
    ],
}


def update_file(rel: str, blocks: list[tuple[str, object]], check_only: bool) -> bool:
    """返回 True 表示有变更（或校验时发现漂移）。"""
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    orig = text
    for name, renderer in blocks:
        want = block(name, renderer())
        pat = re.compile(
            re.escape(BEGIN.format(name=name))
            + r".*?"
            + re.escape(END.format(name=name))
            # ★ 吞掉 END 之后到下一行内容之前的空白：block() 自带 1 个末尾换行，
            #   不吞的话每次生成都会多留一个空行 → 幂等性被破坏、门禁永远报漂移。
            + r"[ \t]*(?:\r?\n[ \t]*)*",
            re.S,
        )
        if pat.search(text):
            text = pat.sub(lambda _m: want, text, count=1)
        else:
            print(f"  [警告] {rel} 找不到标记块 {name}，跳过（请手动补标记）")
    if text == orig:
        return False
    if check_only:
        return True
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    check_only = "--check" in sys.argv
    drifted = []
    for rel, blocks in TARGETS.items():
        changed = update_file(rel, blocks, check_only)
        if changed:
            drifted.append(rel)
            print(f"{'[漂移]' if check_only else '[更新]'} {rel}")
        else:
            print(f"[一致] {rel}")
    if check_only:
        if drifted:
            print("\n以上文件与页面注册表不一致，请跑：")
            print("  C:/Python310/python.exe tools/gen_pages.py")
            return 1
        print("\n全部清单与页面注册表一致。")
    else:
        print(f"\n已同步 {len(drifted)} 个文件；请跑门禁 _validate_page_registry.py 复核。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
