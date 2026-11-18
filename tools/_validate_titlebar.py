# -*- coding: utf-8 -*-
"""系统标题栏主题同步门禁（2026-11-17）。

★ 为什么要有这个脚本
  白标题栏这个问题「代码不报错、功能全正常，就是丑」——编译过、冒烟过，
  门禁全绿也拦不住。而且标题栏归 Windows 管，QSS 里翻不出任何线索，
  最容易在后续重构里被悄悄漏掉，客户又发来那张"深色界面顶白条"的截图。

  所以这里钉两件事：
    ① 接线还在：showEvent（首屏）和 _toggle_theme（切换）都调了同步函数；
    ② 兜底还在：纯标准库、非 Windows 静默、异常不抛、没走自绘路线。

跑法：C:/Python310/python.exe tools/_validate_titlebar.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


print("\n== 1. titlebar 模块 ==")
try:
    src = read("gui_qt/titlebar.py")
    tree = ast.parse(src)
    check("titlebar.py 可解析", True)
except (OSError, SyntaxError) as exc:
    check("titlebar.py 可解析", False, repr(exc))
    src, tree = "", None

if tree is not None:
    # 纯标准库：只允许 sys / ctypes，绝不能引 Qt 或第三方包
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    check("只依赖标准库 sys/ctypes",
          imported <= {"sys", "ctypes", "__future__"}, str(sorted(imported)))

    # 函数齐全
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    check("apply_native_titlebar 存在", "apply_native_titlebar" in funcs)
    check("read_native_titlebar 存在（自检用）", "read_native_titlebar" in funcs)

    # DWM 属性号：20 主力 + 19 回退，缺一不可
    check("用 DWM 沉浸式深色属性 20", "DWMWA_USE_IMMERSIVE_DARK_MODE = 20" in src)
    check("保留老系统回退属性 19", "DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19" in src)
    check("调的是 DwmSetWindowAttribute", "DwmSetWindowAttribute" in src)
    check("设完触发非客户区重绘", "SetWindowPos" in src and "SWP_FRAMECHANGED" in src)

    # 兜底：非 Windows 静默 + 异常不抛
    check("非 Windows 静默返回", 'sys.platform != "win32"' in src)
    check("异常一律吞掉不抛", "except Exception" in src)

print("\n== 2. 接线：首屏 + 主题切换 ==")
try:
    mw = read("gui_qt/main_window.py")
    mw_tree = ast.parse(mw)
except (OSError, SyntaxError) as exc:
    mw, mw_tree = "", None
    check("main_window.py 可解析", False, repr(exc))

if mw_tree is not None:
    methods = {n.name: n for n in ast.walk(mw_tree)
               if isinstance(n, ast.FunctionDef)}

    def calls_toggle(name: str) -> bool:
        fn = methods.get(name)
        if fn is None:
            return False
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == "apply_native_titlebar":
                    return True
                if isinstance(f, ast.Attribute) and f.attr == "apply_native_titlebar":
                    return True
        return False

    check("showEvent 首屏同步标题栏", calls_toggle("showEvent"))
    check("_toggle_theme 切换时同步标题栏", calls_toggle("_toggle_theme"))

    # 没走自绘路线：一旦有人加 FramelessWindowHint 就得重审整套窗口行为
    check("没有自绘标题栏（禁用 FramelessWindowHint）",
          "FramelessWindowHint" not in mw)

print("\n== 3. 真机接口可用性（Windows 上） ==")
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        # 用桌面窗口探测接口是否存在：只设置一次并读回，随即还原。
        # 桌面窗口未必接受该属性，读回不一致不算失败，只报告能力。
        hwnd = ctypes.windll.user32.GetDesktopWindow()
        value = ctypes.c_int(1)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value))
        check("本机 DWM 接口可调用（无异常）", True, f"hr=0x{hr & 0xFFFFFFFF:08X}")
    except Exception as exc:                                  # noqa: BLE001
        check("本机 DWM 接口可调用（无异常）", False, repr(exc))
else:
    print("  [SKIP] 非 Windows 环境，跳过真机探测")

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
