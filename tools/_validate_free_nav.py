# -*- coding: utf-8 -*-
"""免费版导航入口可见性门禁（2026-11-24，UI 布局评审 P0-4 专项）。

★ 为什么要有这个脚本
  免费版只有「下载 / 工具 / 设置」三组、每组一个功能，本来就该三个入口
  常驻。但侧栏手风琴是给完整版 17 个功能做的：启动时会跑一次
  _collapse_others(None)，把同时摊开的多个组压到只剩一组 —— 免费版里
  「工具」（＝直播监控，第二个真功能）就这么被静默收起来了。
  更隐蔽的是 _NAV_DEFAULT_COLLAPSED = ("分析", "更多工具") 在免费版里
  根本没有对应组，等于一个失效的默认值；编译、冒烟、界面都不报错，
  客户只看到「装了软件却找不到直播录制」。

  所以这里钉三件事：免费版组结构不许变、免费版不参与自动折叠、
  完整版的手风琴 / Ctrl+K / 分组展开逻辑一个都不许被顺手删掉。
  纯标准库（ast + 文本扫描）：不引 Qt、不联网、不实例化窗口。

跑法：C:/Python310/python.exe tools/_validate_free_nav.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    """累计一条断言结果；失败时把修复提示一起打出来。"""
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def read(rel: str) -> str:
    """按 UTF-8 读取工程内的相对路径文件。"""
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _literal(tree: ast.Module, name: str) -> object | None:
    """取模块级常量的字面值；取不到返回 None（绝不硬编码行号）。"""
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            try:
                return ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                return None
    return None


print("\n== 1. 免费版导航结构（AST 读取，不硬编码行号） ==")
try:
    mw = read("gui_qt/main_window.py")
    tree = ast.parse(mw)
    check("main_window.py 可解析", True)
except (OSError, SyntaxError) as exc:
    check("main_window.py 可解析", False,
          f"【修】恢复 gui_qt/main_window.py：{exc!r}")
    mw, tree = "", None

funcs: dict[str, ast.FunctionDef] = {}
free_groups: list[tuple[str, list[str]]] = []
if tree is not None:
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    raw = _literal(tree, "FREE_NAV_GROUPS")
    if isinstance(raw, list):
        free_groups = [(str(g), [str(i) for i in items])
                       for g, items in raw if isinstance(items, (list, tuple))]

check("FREE_NAV_GROUPS 是模块级字面量且可被 AST 读取", bool(free_groups),
      "【修】FREE_NAV_GROUPS 位于 gen_pages.py 的生成块内，必须保持字面量；"
      "要改内容先跑 tools/gen_pages.py 同步注册表。")

free_names = [g for g, _items in free_groups]
free_items = [i for _g, items in free_groups for i in items]
for item in ("视频下载", "直播监控", "设置"):
    check(f"免费版入口含「{item}」", item in free_items,
          "【修】免费版三个入口一个都不许删；要调整请改 page_registry.py 的 "
          "free_group 字段并跑 tools/gen_pages.py。")
check("免费版分组数 ≤ 3（折叠对它没有意义）", 0 < len(free_groups) <= 3,
      "【修】免费版组数涨上去就说明被塞了付费页，回到「只有三个入口」的契约。")
check("免费版每组恰好 1 个功能", all(len(items) == 1 for _g, items in free_groups),
      "【修】每组只留一个功能；一组多页时请重新评估是否该恢复折叠。")

pinned = _literal(tree, "_NAV_PINNED") if tree is not None else None
collapsed = _literal(tree, "_NAV_DEFAULT_COLLAPSED") if tree is not None else None
check("_NAV_PINNED 原值仍是（开始, 设置）", tuple(pinned or ()) == ("开始", "设置"),
      "【修】_NAV_PINNED 不许改，完整版「开始 / 设置」始终常驻。")
check("_NAV_DEFAULT_COLLAPSED 原值仍是（分析, 更多工具）",
      tuple(collapsed or ()) == ("分析", "更多工具"),
      "【修】完整版默认收起的两组不许改，别拿它来修免费版。")
check("_NAV_DEFAULT_COLLAPSED 不含免费版任何组名",
      not (set(tuple(collapsed or ())) & set(free_names)),
      "【修】免费版「下载 / 工具 / 设置」不能被默认收起；命中："
      + "、".join(sorted(set(tuple(collapsed or ())) & set(free_names))))


print("\n== 2. 免费版分组常驻、不参与折叠 ==")
fn = funcs.get("_collapsible_groups")
seg = ast.get_source_segment(mw, fn) if fn is not None and mw else ""
check("_collapsible_groups() 仍在且可定位", bool(seg),
      "【修】恢复 gui_qt/main_window.py 的 _collapsible_groups() 方法。")

free_branch = False
if fn is not None and mw:
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(mw, node.test) or ""
        if "APP_IS_FREE" not in test_src:
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.List) \
                    and not stmt.value.elts:
                free_branch = True
check("_collapsible_groups() 有 APP_IS_FREE 分支并返回空列表", free_branch,
      "【修】在 _collapsible_groups() 开头加：if branding.APP_IS_FREE: return []；"
      "免费版三组都要常驻，不能被启动手风琴压掉。")
check("_collapsible_groups() 完整版仍按 NAV_GROUPS / _NAV_PINNED 过滤",
      "NAV_GROUPS" in seg and "_NAV_PINNED" in seg,
      "【修】完整版的列表推导 return [g for g, _items in NAV_GROUPS "
      "if g not in _NAV_PINNED] 不许删。")
check("main_window.py 顶部已 import branding（APP_IS_FREE 分支能生效）",
      "from justoneapi_collector import branding" in mw,
      "【修】补回 from justoneapi_collector import branding，否则分支取不到免费版标志。")
check("启动时仍执行一次 self._collapse_others(None)（完整版行为保留）",
      "self._collapse_others(None)" in mw,
      "【修】完整版手风琴的启动收敛不许删；免费版靠空列表自然空转即可。")


print("\n== 3. 完整版手风琴 / Ctrl+K 逻辑不许退化 ==")
for name in ("_collapse_others", "_ensure_group_expanded", "_set_group_collapsed",
             "_on_root_clicked", "_open_palette"):
    check(f"完整版方法 {name}() 仍在", name in funcs,
          f"【修】恢复 {name}()；免费版改动不该动到完整版逻辑。")

co_seg = ast.get_source_segment(mw, funcs["_collapse_others"]) \
    if "_collapse_others" in funcs and mw else ""
check("_collapse_others() 仍调用 self._collapsible_groups()",
      "self._collapsible_groups()" in co_seg,
      "【修】手风琴必须基于可折叠组列表收起，别改成写死组名。")
check("_collapse_others() 仍把其余组收起（persist=False）",
      "_set_group_collapsed(g, True" in co_seg and "persist=False" in co_seg,
      "【修】保留 for 循环里的 _set_group_collapsed(g, True, persist=False)。")

en_seg = ast.get_source_segment(mw, funcs["_ensure_group_expanded"]) \
    if "_ensure_group_expanded" in funcs and mw else ""
check("_ensure_group_expanded() 仍先跑 _collapse_others(keep)",
      "_collapse_others(" in en_seg,
      "【修】Ctrl+K 跳页时仍要先压平其它组，否则侧栏看不到选中项。")
check("Ctrl+K 接线仍在（install_shortcut + _open_palette）",
      "install_shortcut(" in mw and "_open_palette" in mw,
      "【修】折叠必须配 Ctrl+K，两个是打包交付的，不许拆开。")
check("_set_group_collapsed() 仍负责落盘与重绘",
      "_apply_group_collapsed" in (ast.get_source_segment(mw, funcs["_set_group_collapsed"])
                                   if "_set_group_collapsed" in funcs and mw else ""),
      "【修】保留 _set_group_collapsed() 里的 _apply_group_collapsed 调用。")


print("\n== 4. 门禁自身已挂进发版链 ==")
rel_src = read("tools/release_all.py")
for script in ("_validate_ui_copy.py", "_validate_free_nav.py"):
    check(f"{script} 已挂进 tools/release_all.py", script in rel_src,
          "【修】在「小红书笔记」门禁之后、升版本号之前补一段 run(...) 挂载。")
mark = rel_src.find("if not args.no_pack:")
pos_ui = rel_src.find("_validate_ui_copy.py")
pos_free = rel_src.find("_validate_free_nav.py")
check("两条门禁都挂在升版本号之前（属于门禁段）",
      mark > 0 and 0 < pos_ui < mark and 0 < pos_free < mark,
      "【修】门禁必须在 if not args.no_pack: 之前，否则 --no-pack 会绕过它。")

print(f"\n结果：{PASS} 通过 / {FAIL} 失败  →  {PASS}/{PASS + FAIL}")
sys.exit(1 if FAIL else 0)
