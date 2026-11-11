"""「使用教程 · 快速上手」对话框（PySide6）。

左侧模块列表，右侧按「用途 → 操作步骤 → 注意事项」展示 GUIDE_CONTENT，
与各页面顶部的引导条共用同一份文案。

入口：主窗口左侧导航底部的「使用教程」。
"""
from PySide6.QtCore import QSize
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QPushButton,
                               QTextBrowser, QVBoxLayout)

from gui_qt.icons import book_icon
from gui_qt.widgets.guide import GUIDE_CONTENT, GUIDE_ORDER
from gui_qt.theme import accent_color


def _palette() -> dict:
    """按当前主题给出教程正文用色（QTextBrowser 渲染 HTML 需要显式颜色）。"""
    app = QApplication.instance()
    mode = "light"
    if app is not None:
        try:
            mode = app.property("theme_mode") or "light"
        except Exception:  # noqa: BLE001
            mode = "light"
    if mode == "dark":
        return {
            "title": "#E5E7EB",
            "body": "#C7CBD3",
            "muted": "#9CA3AF",
            "accent": accent_color("dark"),
        }
    return {
        "title": "#111827",
        "body": "#374151",
        "muted": "#6B7280",
        "accent": accent_color("light"),
    }


class GuideDialog(QDialog):
    """快速上手对话框；current_key 用于直接定位到某个模块。"""

    def __init__(self, parent=None, current_key: str | None = None):
        super().__init__(parent)
        self.setObjectName("GuideDialog")
        self.setWindowTitle("使用教程 · 快速上手")
        self.resize(840, 580)
        self._build_ui()
        self._select(current_key)

    # ------------------------------------------------------------ 构建
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(
            book_icon(accent_color("light"), 20).pixmap(QSize(20, 20))
        )
        head.addWidget(icon)
        title = QLabel("快速上手")
        title.setObjectName("TutorialTitle")
        head.addWidget(title)
        head.addStretch()
        hint = QLabel("按左侧模块查看该功能的操作步骤")
        hint.setObjectName("TutorialSub")
        head.addWidget(hint)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(12)

        self.nav = QListWidget()
        self.nav.setObjectName("TutorialNav")
        self.nav.setFixedWidth(196)
        for key in GUIDE_ORDER:
            QListWidgetItem(key, self.nav)
        self.nav.currentRowChanged.connect(self._on_row)
        body.addWidget(self.nav)

        self.doc = QTextBrowser()
        self.doc.setObjectName("TutorialBody")
        self.doc.setOpenExternalLinks(True)
        body.addWidget(self.doc, 1)
        root.addLayout(body, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        ok = QPushButton("知道了")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        foot.addWidget(ok)
        root.addLayout(foot)

    # ------------------------------------------------------------ 行为
    def _select(self, key: str | None):
        row = 0
        if key and key in GUIDE_ORDER:
            row = GUIDE_ORDER.index(key)
        self.nav.setCurrentRow(row)

    def _on_row(self, row: int):
        if row < 0 or row >= len(GUIDE_ORDER):
            return
        self.doc.setHtml(self._render(GUIDE_ORDER[row]))

    @staticmethod
    def _render(key: str) -> str:
        c = _palette()
        data = GUIDE_CONTENT.get(key, {})
        steps = "".join(f"<li>{s}</li>" for s in data.get("steps", []))
        tips = "".join(f"<li class='tip'>{t}</li>" for t in data.get("tips", []))
        html = [
            "<style>",
            f"h2 {{ color:{c['title']}; font-size:17px; margin:0 0 2px 0; }}",
            f"p.purpose {{ color:{c['body']}; font-size:13px; line-height:1.75;"
            f" margin:0 0 4px 0; }}",
            f"h3 {{ color:{c['muted']}; font-size:12px; margin:16px 0 6px 0;"
            " font-weight:600; }",
            f"li {{ color:{c['body']}; font-size:13px; line-height:1.8; }}",
            f"li.tip {{ color:{c['muted']}; }}",
            "ol, ul { margin-left:18px; }",
            "</style>",
        ]
        html.append(f"<h2>{key}</h2>")
        html.append(f"<p class='purpose'>{data.get('summary', '')}</p>")
        if steps:
            html.append("<h3>操作步骤</h3><ol>" + steps + "</ol>")
        if tips:
            html.append("<h3>注意事项</h3><ul>" + tips + "</ul>")
        return "".join(html)


def open_guide(parent=None, current_key: str | None = None) -> None:
    """便捷入口：非模态打开教程对话框，便于对照操作。"""
    dlg = GuideDialog(parent, current_key)
    dlg.exec()
