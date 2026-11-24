"""一次性：给现有清单套上 PAGE-REGISTRY 标记块（幂等，可重复跑）。

只在首次把注册表接入时跑；以后清单由 tools/gen_pages.py 维护。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BEGIN = "# >>> PAGE-REGISTRY BEGIN {name} >>>"
END = "# >>> PAGE-REGISTRY END {name} <<<"

# (文件, 块名, 原文首行, 结束标识)  —— 用首行+结束行唯一定位赋值语句
JOBS = [
    ("gui_qt/main_window.py", "page_imports",
     "from gui_qt.pages.comment_page import CommentPage", None),
    ("gui_qt/main_window.py", "nav_groups", "NAV_GROUPS = [", "\n]\n"),
    ("gui_qt/main_window.py", "free_nav", "FREE_NAV_GROUPS = [", "\n]\n"),
    ("gui_qt/main_window.py", "page_class", "_PAGE_CLASS = {", "\n}\n"),
    ("gui_qt/icons.py", "nav_inner", "_NAV_INNER = {", "\n}\n"),
    ("gui_qt/widgets/guide.py", "guide_order", "GUIDE_ORDER = [", "\n]\n"),
    ("gui_qt/widgets/command_palette.py", "aliases", "_ALIASES = {", "\n}\n"),
    ("gui_qt/pages/home_page.py", "quick_actions", "QUICK_ACTIONS = [", "\n]\n"),
    ("gui_qt/pages/home_page.py", "main_flow", "MAIN_FLOW = [", "\n]\n"),
    ("gui_qt/pages/home_page.py", "more_tools", "MORE_TOOLS = [", "\n]\n"),
    ("justoneapi_collector.spec", "spec_hidden",
     '    "gui_qt.pages._base",', None),
    ("tools/_validate_feature_sync.py", "feature_sync",
     "PAGE_FEATURE: dict[str, tuple[str, str]] = {", None),
    ("tools/_validate_ux.py", "ux_expected", "EXPECTED = [", "\n]\n"),
]

# 特殊块的精确边界（用起止字符串切片）
SPECIAL = {
    ("gui_qt/main_window.py", "page_imports"): (
        "from gui_qt.pages.comment_page import CommentPage",
        "from gui_qt.subwindow import SubWindow",
    ),
    ("justoneapi_collector.spec", "spec_hidden"): (
        '    "gui_qt.pages._base",',
        '    "justoneapi_collector.comment_mining",',
    ),
    ("tools/_validate_feature_sync.py", "feature_sync"): (
        "PAGE_FEATURE: dict[str, tuple[str, str]] = {",
        "\nPASSED: list[str] = []",
    ),
}


def wrap(text: str, name: str, body: str) -> str:
    return (BEGIN.format(name=name) + "\n" + body.rstrip() + "\n"
            + END.format(name=name) + "\n")


def main() -> int:
    for rel, name, first, _end in JOBS:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if BEGIN.format(name=name) in text:
            print(f"[已有标记] {rel} :: {name}")
            continue
        key = (rel, name)
        if key in SPECIAL:
            start_s, stop_s = SPECIAL[key]
            i = text.find(start_s)
            if i < 0:
                print(f"[失败] {rel} :: {name} 找不到起点")
                return 1
            # 终点取起点的行首，保证整段被包住
            j = text.find(stop_s, i)
            if j < 0:
                print(f"[失败] {rel} :: {name} 找不到终点")
                return 1
            if name == "page_imports":
                # 含终点行本身
                j = text.find("\n", j) + 1
            elif name == "spec_hidden":
                j = text.find("\n", j) + 1
            else:  # feature_sync：终点标记另起，回到 it 之前的行尾
                j = j + 1
            body = text[i:j]
            text = text[:i] + wrap(text, name, body) + text[j:]
        else:
            i = text.find(first)
            if i < 0:
                print(f"[失败] {rel} :: {name} 找不到起点")
                return 1
            # 退路：从首行找配对的 ] 或 }（简单计数，忽略字符串内的括号）
            open_ch = "[" if first.endswith("[") else "{"
            close_ch = "]" if open_ch == "[" else "}"
            depth = 0
            j = i
            while j < len(text):
                c = text[j]
                if c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        if j < len(text) and text[j] == "\n":
                            j += 1
                        break
                j += 1
            body = text[i:j]
            text = text[:i] + wrap(text, name, body) + text[j:]
        path.write_text(text, encoding="utf-8")
        print(f"[已加标记] {rel} :: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
