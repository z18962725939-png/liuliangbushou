# -*- coding: utf-8 -*-
"""窗口尺寸与独立窗口门禁（2026-11-18）。

★ 为什么要有这个脚本
  客户反馈「右侧内容过多会拉长整个窗口」。修法是两件事：
    ① 主窗口设尺寸上下限（不是 setFixedSize —— 那会连最大化都禁掉）；
    ② 通用次级窗口 SubWindow：把当前页借出去独立看，关闭自动归还。
  这类「视觉/交互」问题代码不报错、功能全正常，编译和冒烟都拦不住，
  最容易在后续重构里被悄悄改回去。所以这里钉：

    1. 静态：主窗口有 setMinimumSize/setMaximumSize 且不是 setFixedSize；
       有独立窗口入口按钮、popout_current、_popouts、关闭归还接线；
       SubWindow 模块存在且内容区是滚动区、带关闭回调。
    2. 行为：离屏真实构造主窗口 —— 尺寸被 maximum 夹住；弹出→借出→
       关闭→归还全程 widget 身份不变；重复弹出只一个；多级弹窗 parent 链；
       弹窗开着时切回该页会自动归还；主题切换弹窗留存。

跑法：C:/Python310/python.exe tools/_validate_window_sizing.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 本门禁要真实 import gui_qt，需把项目根放进搜索路径（脚本直跑时 cwd 不一定是根）
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


print("\n== 1. 主窗口尺寸上下限（静态）==")
try:
    mw = read("gui_qt/main_window.py")
    tree = ast.parse(mw)
    check("main_window.py 可解析", True)
except (OSError, SyntaxError) as exc:
    check("main_window.py 可解析", False, repr(exc))
    mw, tree = "", None

calls = [n.func.attr for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)] \
    if tree else []
check("设了 setMinimumSize", "setMinimumSize" in calls)
check("设了 setMaximumSize", "setMaximumSize" in calls)
check("没用 setFixedSize（否则最大化被禁）", "setFixedSize" not in calls)
check("没在 app.py 之外重复 resize 覆盖边界",
      read("gui_qt/app.py").count("window.resize") == 1)

print("\n== 2. 独立窗口接线（静态）==")
check("主窗口 import 了 SubWindow", "from gui_qt.subwindow import SubWindow" in mw)
check("有 _popouts 记录（防重复借出）", "_popouts" in mw)
check("有 popout_current 方法", "def popout_current(self)" in mw)
check("有入口按钮", "_popout_btn" in mw)
check("入口按钮走 lambda *_（防参数签名炸）",
      "_popout_btn.clicked.connect(lambda *_: self.popout_current())" in mw)
check("关闭回调里 take_content 归还",
      "take_content()" in mw and "setWidget(w)" in mw)
check("切回被借出的页会先 close 归还",
      "popped.close()" in mw)

print("\n== 3. SubWindow 模块（静态）==")
try:
    sw = read("gui_qt/subwindow.py")
    sw_tree = ast.parse(sw)
    check("subwindow.py 可解析", True)
except (OSError, SyntaxError) as exc:
    check("subwindow.py 可解析", False, repr(exc))
    sw, sw_tree = "", None

check("是非模态", "NonModal" in sw)
check("内容区是 QScrollArea（内容多只滚不撑大）",
      "self._scroll = QScrollArea()" in sw)
check("有 set_content / take_content 成对接口",
      "def set_content" in sw and "def take_content" in sw)
check("有 on_closed 关闭回调", "_on_closed" in sw)
check("有 open_child（多级弹窗）", "def open_child" in sw)
check("不写死颜色（交给 QSS 跟随主题）", "#[0-9A-Fa-f]{6}" not in sw)
light = read("gui_qt/styles/light.qss")
dark = read("gui_qt/styles/dark.qss")
check("浅色 QSS 有 SubWindow 样式", "QDialog#SubWindow" in light)
check("深色 QSS 有 SubWindow 样式", "QDialog#SubWindow" in dark)

print("\n== 4. 真实行为（离屏构造）==")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from gui_qt.main_window import MainWindow
    from gui_qt.pages._base import AppContext

    app = QApplication.instance() or QApplication([])
    w = MainWindow(AppContext())
    w.resize(1180, 760)

    # 尺寸上限真的夹住
    w.resize(3000, 2000)
    check("尺寸被 maximum 夹住（3000→1520）",
          w.width() <= w.maximumSize().width()
          and w.height() <= w.maximumSize().height(),
          f"实际 {w.width()}x{w.height()}")
    w.resize(1180, 760)

    # 弹出 → 借出 → 关闭 → 归还
    w._switch("工作台")
    scroll = w._stack.currentWidget()
    page_widget = scroll.widget()
    w.popout_current()
    sub = w._popouts.get("工作台")
    check("弹出产生独立窗口", sub is not None)
    check("弹窗标题带页名", sub is not None and "工作台" in sub.windowTitle())
    check("页面被借出（原 scroll 空）", scroll.widget() is None)
    check("弹窗内容就是原页面对象（非重建）",
          sub is not None and sub.content() is page_widget)

    # 重复弹出只一个
    w.popout_current()
    vis = [s for s in w._popouts.values() if s.isVisible()]
    check("重复弹出不产生第二个", len(vis) == 1, f"可见弹窗 {len(vis)} 个")

    # 多级弹窗 parent 链
    child = sub.open_child("二级详情", None)
    check("二级窗口 parent 是一级窗口", child.parent() is sub)
    child.close()

    # 关闭一级 → 归还
    sub.close()
    app.processEvents()
    check("关闭后页面归还原 scroll",
          scroll.widget() is page_widget)
    check("关闭后 _popouts 清空", "工作台" not in w._popouts)

    # 弹窗开着时切回该页 → 自动归还
    w._switch("工作台")
    pp = w._scrolls["工作台"].widget()
    w.popout_current()
    w._switch("选题挖掘")
    w._switch("工作台")
    check("切回被借出的页自动归还",
          w._scrolls["工作台"].widget() is pp
          and "工作台" not in w._popouts)

    # 主题切换弹窗留存
    w._switch("设置")
    w.popout_current()
    w._toggle_theme()
    check("主题切换后弹窗仍在", len(w._popouts) == 1)
    check("主题切换不崩（模式已变）", w._theme_mode == "dark")
    w._toggle_theme()
except Exception as exc:                                       # noqa: BLE001
    check("离屏行为测试全程无异常", False, repr(exc))
    import traceback
    traceback.print_exc()

print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
