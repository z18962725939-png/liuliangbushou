"""提示条 AlertBar：把「⚠️ 一句话」升级成「说得清 + 有出口」的一条横幅。

为什么要有它（2026-10-17 视觉改版）
------------------------------------
改版前，页面里的异常状态是这么写的：

    QLabel("⚠️ 授权码里缺少采集凭证")          ← 纯橙字 + emoji
    layout.addWidget(lab)

没有容器、没有图标位、没有按钮，裸贴在两张卡片之间。客户看到会慌，但不知道
该点哪里 —— 这正是"提示语让人更焦虑"的典型：它指出了问题，却没给出路。

AlertBar 统一三件事：
  1. 有容器 —— 浅底 + hairline，明确它是一条"系统消息"，不是正文的一部分；
  2. 有语义 —— warn / danger / info 三档，颜色和图标符号对应；
  3. 有出口 —— 右侧可选一个操作按钮（去授权、去登录、重新检测…）。

★ 设计纪律：一律「浅底 + 深字 + 细边」，不用大红大紫的实心块。
  实心色块会把整页的视觉重心抢过去，而提示条通常不是当前页面的主角。

用法
-----
    bar = AlertBar(level="warn", title="授权码里缺少采集凭证",
                   body="现在只能下载，抓取类功能用不了",
                   action_text="去授权", on_action=self._open_license)
    bar.apply_theme(mode)     # 主题切换时要跟着换
    bar.setVisible(False)     # 正常情况下藏起来
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout)

from .motion import fade_in

# 每档：图标符号 / 浅底 / 边框 / 标题字色 / 正文字色
_LEVELS = {
    "warn": {
        "light": ("!", "#FFFBEB", "#FDE68A", "#92400E", "#78350F"),
        "dark": ("!", "#2A2110", "#4A3A12", "#FBBF24", "#CBB47E"),
    },
    "danger": {
        "light": ("!", "#FEF2F2", "#FECACA", "#991B1B", "#7F1D1D"),
        "dark": ("!", "#2A1618", "#4A2226", "#F87171", "#DBA5A5"),
    },
    "info": {
        "light": ("i", "#F2FCD9", "#DCF3A0", "#0B0E14", "#374151"),
        "dark": ("i", "#1B2410", "#33471A", "#C8F751", "#C3CAD6"),
    },
}

_KIND_ALIAS = {
    "warning": "warn",
    "error": "danger",
    "critical": "danger",
    "success": "info",
    "tip": "info",
    "ok": "info",
}


def _normalize(level: str) -> str:
    """把各种叫法收敛到三档。未知值一律当 info —— 宁可温和，不要错报成红。"""
    key = (level or "info").strip().lower()
    key = _KIND_ALIAS.get(key, key)
    return key if key in _LEVELS else "info"


class AlertBar(QFrame):
    """一条语义化提示：图标 + 标题 + 说明 + 可选操作按钮。"""

    def __init__(self, level: str = "info", title: str = "", body: str = "",
                 action_text: str = "", on_action=None, mode: str = "light",
                 parent=None):
        super().__init__(parent)
        self._mode = mode
        self._level = _normalize(level)
        self.setObjectName("AlertBar")
        self.setProperty("level", self._level)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)

        # 图标：圆底 + 单个符号。不用 emoji —— emoji 在不同 Windows 版本上
        # 字形差很大，有的还渲染成彩色表情，和专业界面不搭。
        self.icon_lab = QLabel()
        self.icon_lab.setFixedSize(20, 20)
        self.icon_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.icon_lab, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(2)
        self.title_lab = QLabel(title)
        self.title_lab.setObjectName("AlertTitle")
        self.title_lab.setWordWrap(True)
        texts.addWidget(self.title_lab)
        self.body_lab = QLabel(body)
        self.body_lab.setObjectName("AlertBody")
        self.body_lab.setWordWrap(True)
        self.body_lab.setVisible(bool(body))
        texts.addWidget(self.body_lab)
        row.addLayout(texts, 1)

        self.action_btn = QPushButton(action_text)
        self.action_btn.setObjectName("AlertAction")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.setVisible(bool(action_text))
        if on_action is not None:
            self.action_btn.clicked.connect(on_action)
        row.addWidget(self.action_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme(mode)

    # ---------------------------------------------------------------- 内容

    def set_content(self, title: str = "", body: str = "") -> None:
        """改文案。内容真的变了才淡入 —— 每次刷新都闪一下会很吵。"""
        changed = self.title_lab.text() != str(title)
        self.title_lab.setText(str(title))
        self.body_lab.setText(str(body))
        self.body_lab.setVisible(bool(body))
        if changed and self.isVisible():
            fade_in(self, duration=120)

    def set_level(self, level: str) -> None:
        """换语义档（浅色主题下 Property 选择器要重新 polish 才生效）。"""
        new = _normalize(level)
        if new == self._level:
            return
        self._level = new
        self.setProperty("level", new)
        self.apply_theme(self._mode)

    def set_action(self, text: str, on_action=None) -> None:
        self.action_btn.setText(str(text))
        self.action_btn.setVisible(bool(text))
        if on_action is not None:
            try:
                self.action_btn.clicked.disconnect()
            except RuntimeError:
                pass
            self.action_btn.clicked.connect(on_action)

    # ---------------------------------------------------------------- 主题

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        sym, bg, line, c_title, c_body = _LEVELS[self._level][
            "dark" if mode == "dark" else "light"]
        # Property 选择器（QSS 里的 [level="warn"]）改了属性不会自动重算，
        # 必须 unpolish + polish 走一遍，否则颜色停在上一档。
        self.style().unpolish(self)
        self.style().polish(self)
        self.setStyleSheet(
            f"#AlertBar{{background:{bg};border:1px solid {line};"
            f"border-radius:10px;}}")
        self.icon_lab.setText(sym)
        self.icon_lab.setStyleSheet(
            f"background:{c_title};color:{bg};border-radius:10px;"
            f"font-size:12px;font-weight:700;")
        self.title_lab.setStyleSheet(
            f"color:{c_title};font-size:13px;font-weight:600;line-height:1.55;")
        self.body_lab.setStyleSheet(
            f"color:{c_body};font-size:12px;line-height:1.6;")
