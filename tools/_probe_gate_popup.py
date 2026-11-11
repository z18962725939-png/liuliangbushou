# -*- coding: utf-8 -*-
"""付费闸门功能探针（v1.21.0）：免费档点付费动作 → 必须弹须知且不继续干活。

跑法：C:/Python310/python.exe tools/_probe_gate_popup.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from justoneapi_collector import licensing                      # noqa: E402
import gui_qt.pages._base as base                               # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK' if cond else 'FAIL'} {name}" + (f"   [{extra}]" if extra else ""))
    return cond


# ---- 打桩：授权态 = 免费档 ----
_orig_status = licensing.status
licensing.status = lambda *a, **k: {
    "state": "free", "label": "免费版", "licensed": False, "days_left": -1,
    "customer": "", "expires_at": 0, "machine_id": "AAAA-BBBB-CCCC-DDDD",
    "features": list(licensing.FREE_FEATURES), "embedded_token": "",
    "tamper": 0, "trial_start": 0.0, "message": "", "daily_quota": 0,
    "plan": "免费版",
}

# ---- 打桩：QMessageBox 不真弹，只记录 ----
popped: list[dict] = []
_orig_box = base.QMessageBox


class _FakeButton:
    def __init__(self, role, text):
        self.role = role
        self.text = text


class _FakeIcon:
    Information = "info"


class _FakeRole:
    AcceptRole = "accept"
    RejectRole = "reject"
    DestructiveRole = "destructive"


class _FakeBox:
    Icon = _FakeIcon
    ButtonRole = _FakeRole

    def __init__(self, parent):
        self.parent = parent
        self.icon = None
        self.title = ""
        self.text = ""
        self.detail = ""
        self.buttons: list[_FakeButton] = []
        self.default = None
        self.clicked = None

    def setIcon(self, v):
        self.icon = v

    def setWindowTitle(self, t):
        self.title = t

    def setText(self, t):
        self.text = t

    def setDetailedText(self, t):
        self.detail = t

    def addButton(self, t, role):
        b = _FakeButton(role, t)
        self.buttons.append(b)
        return b

    def setDefaultButton(self, b):
        self.default = b

    def exec(self):
        popped.append({"parent": self.parent, "title": self.title,
                       "text": self.text, "detail": self.detail,
                       "buttons": [b.text for b in self.buttons]})
        self.clicked = self.default          # 模拟用户点了默认按钮
        return 0

    def clickedButton(self):
        return self.clicked


base.QMessageBox = _FakeBox

print("1) require_feature 对免费档的判罚")
_ok = base.require_feature(object(), "collect", "执行采集")
check("免费档：collect 返回 False（拦住）", _ok is False)
check("拦住时弹了一次须知", len(popped) == 1, f"{len(popped)} 次")
_p = popped[0]
check("弹窗标题是「需要激活授权码」", _p["title"] == "需要激活授权码", _p["title"])
check("弹窗第一行带出用户刚点的动作", "「执行采集」" in _p["text"])
check("弹窗写明免费版能一直用的两项",
      "抖音视频下载" in _p["text"] and "直播录制" in _p["text"])
check("弹窗列全三档价格",
      all(x in _p["text"] for x in ("19.9", "29.9", "59.9")))
check("弹窗说明到期不锁死", "到期不会锁死软件" in _p["text"])
check("详情区带机器码（客户截图不用再问）", "机器码" in _p["detail"])
check("有「去激活授权码」和「知道了」两个按钮",
      set(_p["buttons"]) == {"去激活授权码", "知道了"}, str(_p["buttons"]))

print()
print("2) 免费档的两个免费功能一根手指都不碰")
check("download 放行", base.require_feature(object(), "download", "解析") is True)
check("live 放行", base.require_feature(object(), "live", "开始录制") is True)
check("放行时没有再弹窗", len(popped) == 1, f"{len(popped)} 次")

print()
print("3) 「去激活授权码」按钮跳转到设置页并滚到授权区")
_jumps: list[str] = []


class _FakePage:
    def focus_activation(self):
        _jumps.append("focus")


class _FakeWin:
    def switch_to(self, name):
        _jumps.append(f"switch:{name}")

    def page(self, name):
        return _FakePage()


class _FakeCtx:
    main_window = _FakeWin()


class _FakeParent:
    ctx = _FakeCtx()


base.require_feature(_FakeParent(), "geo", "AI 可见度体检")
check("点了默认按钮后跳转到「设置」", _jumps[:1] == ["switch:设置"], str(_jumps))
check("并且滚到 / 聚焦授权输入框", "focus" in _jumps, str(_jumps))
check("拿不到主窗口时也不炸（安静失败）",
      base.require_feature(object(), "report", "生成报告") is False)

print()
print("4) 读不到授权态时不拦（宁可放过一次，也不误伤付费用户）")
licensing.status = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("授权文件损坏"))
check("授权态异常时返回 True（不拦）",
      base.require_feature(object(), "collect", "执行采集") is True)
check("异常时也不弹窗打扰", len(popped) == 3, f"{len(popped)} 次")

print()
print("5) extra 附加说明会出现在弹窗里（GEO 独立模块用）")
licensing.status = lambda *a, **k: {
    "state": "free", "label": "免费版", "licensed": False, "days_left": -1,
    "customer": "", "expires_at": 0, "machine_id": "AAAA",
    "features": list(licensing.FREE_FEATURES), "embedded_token": "",
    "tamper": 0, "trial_start": 0.0, "message": "", "daily_quota": 0, "plan": "免费版"}
base.require_feature(object(), "geo", "AI 可见度体检",
                     extra="GEO 为独立模块，不随采集套餐附送。")
check("extra 文案进了弹窗正文", "独立模块" in popped[-1]["text"])

# ---- 还原 ----
base.QMessageBox = _orig_box
licensing.status = _orig_status

print()
print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  x", f)
    sys.exit(1)
print("付费闸门功能探针全部通过。")
