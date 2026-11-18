"""Windows 系统标题栏跟随应用主题（2026-11-17）。

★ 为什么单独一个模块
  Qt 的 QSS 只能画客户区，窗口最上面那条「最小化 / 最大化 / 关闭」是
  Windows 系统（DWM）画的，QSS 碰不到它。于是深色界面顶着一条白条，
  像一张贴错的车贴。这层用 ctypes 直接调 Windows 的
  DwmSetWindowAttribute，把「沉浸式深色模式」开关跟着应用主题走：
  深色主题 → 系统标题栏变深；浅色主题 → 变回白。

★ 为什么不做无边框自绘
  自己画标题栏要补拖动、双击最大化、边缘缩放、贴靠分屏、Win11 圆角阴影，
  还得处理多屏 DPI —— 那是为了一条标题栏拆掉整个窗口系统，不划算。
  系统方案一个接口调用搞定，所有原生行为原样保留。

★ 兜底纪律
  非 Windows / 老系统 / 任何异常一律静默返回 False，绝不让「一条装饰」
  把主窗口拖崩。纯标准库 ctypes，不引任何第三方包，打包零负担。
"""
from __future__ import annotations

import sys

# DWM 窗口属性号：沉浸式深色模式
#  20 = Windows 10 2004(build 19041)+ / Win11，本机 19045 走这条
#  19 = Windows 10 1809~1909 老编号，作为回退
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19

# SetWindowPos 标志：只触发非客户区重绘，不动位置/尺寸/Z 序/激活态
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

# 记录每个 hwnd 上次设过的值：DWM 对同值重复设置偶尔不刷新，
# 而且反复设没必要。窗口销毁后条目残留只是几个整数的内存，可忽略。
_LAST: dict[int, int] = {}


def apply_native_titlebar(win, mode: str = "light") -> bool:
    """让系统标题栏跟随应用主题。返回是否真正生效。

    参数 win 要能给出有效的原生窗口句柄（PySide6 的 QWidget.winId()）。
    必须在窗口 show() 之后调用——show 之前句柄还没落地。
    """
    if sys.platform != "win32":                              # 非 Windows 静默跳过
        return False
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(win.winId())
        if not hwnd:
            return False

        dark = 1 if str(mode).lower() == "dark" else 0
        if _LAST.get(hwnd) == dark:                          # 已经是对的，不折腾
            return True

        value = ctypes.c_int(dark)                           # BOOL/DWORD 都是 4 字节
        dwmapi = ctypes.windll.dwmapi
        hr = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value))
        if hr != 0:                                          # 老系统回退 19 号
            hr = dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
                ctypes.byref(value), ctypes.sizeof(value))
        if hr != 0:
            return False                                     # 不支持就算了，不抛

        _LAST[hwnd] = dark
        # 已经显示出来的窗口要强制刷新非客户区，否则标题栏要等下次
        # 激活才变色——用户点「切换深色」的当下就该看到变化。
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(hwnd), None, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER |
            _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
        return True
    except Exception:                                        # noqa: BLE001
        return False


def read_native_titlebar(win) -> int | None:
    """读回标题栏当前深色开关（仅用于自检/门禁，业务路径不调用）。

    返回 1=深色 / 0=浅色 / None=平台不支持或读取失败。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(win.winId())
        if not hwnd:
            return None
        value = ctypes.c_int(-1)
        dwmapi = ctypes.windll.dwmapi
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE,
                     _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            hr = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), attr,
                ctypes.byref(value), ctypes.sizeof(value))
            if hr == 0:
                return int(value.value)
        return None
    except Exception:                                        # noqa: BLE001
        return None
