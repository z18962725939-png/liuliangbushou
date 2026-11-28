# -*- coding: utf-8 -*-
"""UI 文案一致性门禁（2026-11-24，UI 布局评审 P0「话术漂移」专项）。

★ 为什么要有这个脚本
  这几个 P0 全是「不报错、不崩溃、编译和冒烟都拦不住」的工程债：
    · 额度重置时间点一处写「每天 0 点」、另一处写「明天 16:00」，
      客户按 16:00 白等一整天，谁也不会想到是文案写错；
    · GEO 页引导条传了官网产品名「GEO 优化」，教程字典里却只有
      「GEO 监测」。GuideBanner 用 dict.get(key, {}) 静默降级成通用话术，
      页面看着一切正常，本页教程内容其实全丢了；
    · 选题页把「未连接采集服务」写死成「请先激活授权码或填写 API Token」，
      已经激活但码里没带采集凭证、或没填 okdata Key 的客户被反复引去激活，
      等于把人带到死路。

  话术漂移只能靠静态门禁钉住：把「合法教程键集合」「0 点口径」
  「统一诊断调用」写成断言，谁改回去当场报红并给出修复提示。
  纯标准库（ast + 文本扫描）：不引 Qt、不联网、不实例化窗口。

跑法：C:/Python310/python.exe tools/_validate_ui_copy.py
"""
from __future__ import annotations

import ast
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0

# 过期话术：把「没连接」笼统归因为「没激活」，已激活的客户会反复空转。
# ★ 2026-11-24 第 2 轮加固：QA 把 _build_ui 分支改回备份里的历史原文
#   「请先在「设置」激活授权码…」，精确子串没拦住；_mine_comments 里还有
#   「评论挖掘需要先激活授权码…」这个变体。改成「同一行内两个关键片段相邻、
#   中间允许有限字符」的正则，覆盖所有同类措辞，不再靠逐条枚举。
STALE_PATTERN = re.compile(r"激活授权码[^\n]{0,12}?填写\s*API\s*Token")


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


def _const_str(node: ast.AST) -> str | None:
    """取常量字符串节点的值；非常量返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _stale_hits(text: str) -> list[str]:
    """逐行找过期话术变体，返回「第 N 行：原文」；纯注释行不算命中。"""
    hits: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if STALE_PATTERN.search(line):
            hits.append(f"第 {i} 行：{stripped}")
    return hits


def _call_named(node: ast.AST, name: str) -> bool:
    """判断是不是指定名字的**真实调用节点**；注释与源码文本一律不算。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return ((isinstance(func, ast.Name) and func.id == name)
            or (isinstance(func, ast.Attribute) and func.attr == name))


def _is_demo_if(node: ast.If) -> bool:
    """If 条件里是否引用了 ctx.demo。"""
    return any(isinstance(n, ast.Attribute) and n.attr == "demo"
               for n in ast.walk(node.test))


def _branch_shows_not_connected_error(node: ast.If) -> bool:
    """分支体是否在给用户报「未连接」错误态，或写了去激活旧话术。

    ★ 只挑「直接把错误摆到状态栏」的分支。别的页面用 _warn_not_connected /
      _notify_not_connected / _demo 委托诊断，或只写「演示模式」提示，
      这些分支一律跳过，避免为了门禁去硬改其它页面源码。
    """
    for stmt in node.body:
        for n in ast.walk(stmt):
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            attr = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else "")
            if attr == "mark" and n.args and _const_str(n.args[0]) == "error":
                return True
            if attr in ("setText", "_say") and n.args:
                for arg in n.args:
                    val = _const_str(arg)
                    if val and ("未连接采集服务" in val or "激活授权码" in val):
                        return True
    return False


def _page_files() -> list[str]:
    """列出 gui_qt/pages/ 下的页面源码（只取 .py，备份文件天然被排除）。"""
    return sorted(glob.glob(os.path.join(ROOT, "gui_qt", "pages", "*.py")))


def _gui_qt_py_files() -> list[str]:
    """递归列出 gui_qt/ 下全部 .py 源码，跳过缓存目录。"""
    out: list[str] = []
    for base, dirs, files in os.walk(os.path.join(ROOT, "gui_qt")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        out.extend(os.path.join(base, fn) for fn in files if fn.endswith(".py"))
    return out


def _live_page_modules() -> set[str]:
    """从页面注册表 PAGES 取仍在册的页面模块名（纯 AST，不 import 模块）。

    PAGES 在注册表里是带类型标注的赋值（AnnAssign），普通 Assign 两种都兼容，
    免得以后有人去掉 / 加上类型标注就让整条门禁静默失效。
    """
    tree = ast.parse(read("gui_qt/page_registry.py"))
    mods: set[str] = set()
    for node in tree.body:
        value: ast.AST | None = None
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PAGES" for t in node.targets):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "PAGES":
            value = node.value
        if value is None:
            continue
        for call in ast.walk(value):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                    and call.func.id == "PageSpec":
                for kw in call.keywords:
                    if kw.arg == "module":
                        val = _const_str(kw.value)
                        if val:
                            mods.add(val)
    return mods


def _guide_banner_calls() -> list[tuple[str, int, str | None]]:
    """扫描所有 guide_banner(...) 的第一个参数。

    返回 (文件名, 行号, 常量键或 None)；None 表示参数不是常量字符串，
    这种写法没法静态校验，一律按失败处理。
    """
    out: list[tuple[str, int, str | None]] = []
    for path in _page_files():
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        tree = ast.parse(read(rel))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_call = ((isinstance(func, ast.Name) and func.id == "guide_banner")
                       or (isinstance(func, ast.Attribute)
                           and func.attr == "guide_banner"))
            if not is_call:
                continue
            key = _const_str(node.args[0]) if node.args else None
            out.append((os.path.basename(path), node.lineno, key))
    return out


# ── 1. 页面引导条的教程键 ────────────────────────────────────────────────
print("\n== 1. 页面引导条教程键 ==")
try:
    guide_src = read("gui_qt/widgets/guide.py")
    guide_tree = ast.parse(guide_src)
    check("guide.py 可解析", True)
except (OSError, SyntaxError) as exc:
    check("guide.py 可解析", False, f"【修】恢复 gui_qt/widgets/guide.py：{exc!r}")
    guide_src, guide_tree = "", None

legal: set[str] = set()
order: list[str] = []
content_keys: set[str] = set()
if guide_tree is not None:
    for node in guide_tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                continue
            if name == "GUIDE_ORDER" and isinstance(value, list):
                order = [str(x) for x in value]
            elif name == "GUIDE_CONTENT" and isinstance(value, dict):
                content_keys = {str(k) for k in value}
    legal = set(order) | content_keys
    check("教程合法键集合可读（GUIDE_ORDER + GUIDE_CONTENT）", len(legal) >= 18,
          "【修】GUIDE_ORDER / GUIDE_CONTENT 必须是模块级字面量，别改成运行时拼接。")
    check("GUIDE_ORDER 每个键都有教程内容", set(order) <= content_keys,
          "【修】补齐 GUIDE_CONTENT 缺的键："
          + "、".join(sorted(set(order) - content_keys))
          + "；否则引导条会静默降级成通用话术。")

live = _live_page_modules()
check("页面注册表可读（在线页面模块清单）", len(live) >= 15,
      "【修】gui_qt/page_registry.py 的 PAGES 必须保留 module= 字段。")

calls = _guide_banner_calls()
check("gui_qt/pages 下扫到 guide_banner 调用", len(calls) >= 15,
      "【修】页面引导条要么被误删，要么换了工厂函数名，请同步更新本门禁。")

bad: list[str] = []
for fname, lineno, key in calls:
    if fname[:-3] not in live:
        continue                       # 已下线残留页不渲染引导条，见下方跳过说明
    if key is None:
        bad.append(f"{fname}:{lineno} 第一个参数不是常量字符串")
    elif key not in legal:
        bad.append(f"{fname}:{lineno} 错误键「{key}」")
check("在线页面的 guide_banner 常量键全部合法", not bad,
      "【修】把错键改成 gui_qt/widgets/guide.py 的合法键"
      "（如「GEO 优化」→「GEO 监测」）；官网产品名不许带进 GUI。命中："
      + "；".join(bad))

geo_keys = [k for f, _l, k in calls if f == "geo_page.py"]
check("geo_page.py 引导条键是「GEO 监测」",
      "GEO 监测" in geo_keys and "GEO 优化" not in geo_keys,
      "【修】geo_page.py 的 guide_banner 参数改回「GEO 监测」，当前："
      + "、".join(str(k) for k in geo_keys))

geo_opt_files = [os.path.basename(p) for p in _page_files()
                 if "GEO 优化" in read(os.path.relpath(p, ROOT).replace("\\", "/"))]
check("gui_qt/pages 下不再出现官网产品名「GEO 优化」", not geo_opt_files,
      "【修】GUI 侧统一用「GEO 监测」；官网产品名只留在 tools/ 的站点资料里。命中："
      + "、".join(geo_opt_files))

mw_src = read("gui_qt/main_window.py")
skipped = sorted({f for f, _l, _k in calls if f[:-3] not in live})
reimported = [f[:-3] for f in skipped
              if f"from gui_qt.pages.{f[:-3]} import" in mw_src]
check("下线残留页未被主窗口 import（跳过键检查的前提成立）", not reimported,
      "【修】这些页面已重新启用，必须先给 guide_banner 换合法键再纳入注册表："
      + "、".join(reimported))
if skipped:
    print(f"  [说明] 已下线残留页不渲染引导条，跳过键检查：{'、'.join(skipped)}")


# ── 2. 额度重置时间点口径 ────────────────────────────────────────────────
print("\n== 2. 额度重置时间点口径 ==")
home_src = read("gui_qt/pages/home_page.py")
check("home_page.py 额度文案含「0 点」", "0 点" in home_src,
      "【修】额度真实规则是每天 0 点重置，把文案里的时间点全改成「0 点」。")
check("额度用完提示写「明天 0 点自动重置」", "明天 0 点自动重置" in home_src,
      "【修】home_page.py 的 hints.append 改成「今日额度已用完，明天 0 点自动重置。」")
check("额度卡片仍是「每天 0 点重置」（卡片口径别动）", "每天 0 点重置" in home_src,
      "【修】额度卡片 sub 文案保持「今日已用 N 次 · 每天 0 点重置」。")

expired_literal: list[str] = []
expired_line: list[str] = []
for path in _gui_qt_py_files():
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if "16:00 重置" in text or "16:00 自动重置" in text:
        expired_literal.append(rel)
    for i, line in enumerate(text.splitlines(), 1):
        if "16:00" in line and "重置" in line:
            expired_line.append(f"{rel}:{i}")
check("gui_qt/ 下无「16:00 重置」/「16:00 自动重置」", not expired_literal,
      "【修】过期时间点全部改成 0 点；命中：" + "、".join(expired_literal))
check("gui_qt/ 下没有把 16:00 和「重置」写在一起的过期时间点", not expired_line,
      "【修】逐行把这些额度话术改成「0 点」；命中：" + "、".join(expired_line))


# ── 3. 未连接服务话术 ────────────────────────────────────────────────────
print("\n== 3. 未连接服务话术 ==")
stale_hits: list[str] = []
undiagnosed: list[str] = []
branch_offenders: list[str] = []
branch_seen: list[tuple[str, int]] = []
for path in _page_files():
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    text = read(rel)
    fname = os.path.basename(path)
    for hit in _stale_hits(text):
        stale_hits.append(f"{fname}:{hit}")
    # 文件级兜底：涉及「未连接服务」的页面至少要在某处用到统一诊断
    if ("未连接服务" in text or "未连接采集服务" in text) \
            and "connection_diagnosis(" not in text:
        undiagnosed.append(fname)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _is_demo_if(node):
            continue
        if not _branch_shows_not_connected_error(node):
            continue
        branch_seen.append((fname, node.lineno))
        # ★ 分支级：只看这个分支体内有没有真实调用节点，文件别处的调用不顶替
        has_call = any(_call_named(n, "connection_diagnosis")
                       for stmt in node.body for n in ast.walk(stmt))
        if not has_call:
            branch_offenders.append(f"{fname}:{node.lineno}")

check("gui_qt/pages 下无「激活授权码 … 填写 API Token」过期话术（含变体）",
      not stale_hits,
      "【修】改走 connection_diagnosis()，只显示诊断标题，别写死去激活；命中："
      + "；".join(stale_hits))
check("涉及「未连接服务」的页面都调 connection_diagnosis()（文件级兜底）",
      not undiagnosed,
      "【修】参照 gui_qt/pages/settings_page.py:791 用 title, hint = "
      "connection_diagnosis() 做统一诊断；命中：" + "、".join(undiagnosed))
check("每个「未连接服务 / demo」分支内都有真实 connection_diagnosis() 调用",
      not branch_offenders,
      "【修】分支体内必须直接调 connection_diagnosis()（文件别处的调用不能顶替，"
      "注释里写函数名也不算）；命中：" + "、".join(branch_offenders))
check("分支级检查至少覆盖 topic_page.py 的 _build_ui 与 _mine_comments 两处",
      sum(1 for f, _l in branch_seen if f == "topic_page.py") >= 2,
      '【修】这两处 ctx.demo 分支必须保留 status.mark("error") 或旧话术痕迹，'
      "否则分支选择器会漏掉它们；当前识别到："
      + "、".join(f"{f}:{l}" for f, l in branch_seen))

topic_src = read("gui_qt/pages/topic_page.py")
topic_tree = ast.parse(topic_src)
build = next((n for n in ast.walk(topic_tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_build_ui"), None)
build_seg = ast.get_source_segment(topic_src, build) if build is not None else ""
check("topic_page.py 的 _build_ui 可定位", bool(build_seg),
      "【修】_build_ui 被改名或删除了，请同步更新本门禁。")
# ★ AST 调用级：真实 Call 节点才算，注释里出现函数名不算数
build_calls = [n for n in ast.walk(build) if _call_named(n, "connection_diagnosis")] \
    if build is not None else []
check("topic_page.py 常驻提示走 connection_diagnosis()（AST 调用级）",
      bool(build_calls),
      "【修】if self.ctx.demo: 分支里必须有真实的 connection_diagnosis() "
      "调用节点；只在注释里写函数名不算数。")
build_demo_branches = [n for n in ast.walk(build)
                       if isinstance(n, ast.If) and _is_demo_if(n)] \
    if build is not None else []
check("topic_page.py 的 _build_ui 的 ctx.demo 分支内有真实诊断调用",
      bool(build_demo_branches)
      and all(any(_call_named(x, "connection_diagnosis")
                  for stmt in n.body for x in ast.walk(stmt))
              for n in build_demo_branches),
      "【修】_build_ui 的 if self.ctx.demo: 分支体内要真调 connection_diagnosis()，"
      "别让 _notify_not_connected() 或注释顶替。")
build_stale: list[str] = []
if build is not None:
    for n in ast.walk(build):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        attr = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        if attr == "setText" and n.args:
            for arg in n.args:
                val = _const_str(arg)
                if val and STALE_PATTERN.search(val):
                    build_stale.append(f"第 {n.lineno} 行：{val}")
check("topic_page.py 常驻提示不再写死去激活", not build_stale,
      "【修】删掉把「未连接」归因为「没激活」的写死文案，改用诊断标题；命中："
      + "；".join(build_stale))
check('topic_page.py 常驻提示保留 if self.ctx.demo: 与 status.mark("error")',
      bool(build_demo_branches)
      and all(any(isinstance(x, ast.Call) and _call_named(x, "mark")
                  and x.args and _const_str(x.args[0]) == "error"
                  for stmt in n.body for x in ast.walk(stmt))
              for n in build_demo_branches),
      "【修】条件仍是 self.ctx.demo（未连接服务的判据），错误态标记不许删。")


# ── 4. 门禁自身已挂进发版链 ──────────────────────────────────────────────
print("\n== 4. 门禁自身已挂进发版链 ==")
rel_src = read("tools/release_all.py")
check("_validate_ui_copy.py 已挂进 tools/release_all.py",
      "_validate_ui_copy.py" in rel_src,
      "【修】在「小红书笔记」门禁之后、升版本号之前补一段 run(...) 挂载。")
pos = rel_src.find("_validate_ui_copy.py")
mark = rel_src.find("if not args.no_pack:")
check("挂载位置在升版本号之前（属于门禁段）",
      0 < pos < mark,
      "【修】门禁必须在 if not args.no_pack: 之前，否则 --no-pack 会绕过它。")

print(f"\n结果：{PASS} 通过 / {FAIL} 失败  →  {PASS}/{PASS + FAIL}")
sys.exit(1 if FAIL else 0)
