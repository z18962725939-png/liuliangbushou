"""通用次级窗口（2026-11-18）。

背景：客户反馈「右侧区域内容过多会拉长整个窗口」。页面本身都套了滚动区
（见 main_window._scroll_wrap），真正缺的是窗口尺寸上限 + 一种把复杂内容
「抽出去单独看」的手段。本模块提供后者：

  · SubWindow —— 标题栏 + 可滚动内容区 + 可选底栏的独立窗口；
    非模态、可缩放、Esc 关闭；parent 挂主窗口，随主窗口最小化/关闭，
    也被主题切换的 restyle_tree 覆盖到。
  · 支持嵌套：把另一个 SubWindow 的 parent 设为当前窗口，即为「第二级弹窗」，
    层数不限（Qt 原生支持）。open_child() 是便捷封装。

约定（照项目规矩）：
  · 内容区是 QScrollArea —— 页面被弹出来后内容再长也只滚、不撑大窗口。
  · set_content / take_content 成对：把页面 widget 从主窗口「借」出来、
    关闭时「还」回去（切换父控件由 Qt 自动处理）。
  · 不写死任何颜色：QSS 设在 QApplication 上是全局的，弹窗自动跟随主题；
    内联样式由 main_window 切主题时的 restyle_tree(self) 顺着父子链置换。
  · 纯 Qt、无业务逻辑 —— 便于门禁单测。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

# 独立窗口默认 / 最小尺寸：比主窗口小一圈，适合「抽出来盯一块内容」
DEFAULT_SIZE = (980, 720)
MIN_SIZE = (640, 440)


class SubWindow(QDialog):
    """可复用的次级窗口。

    用法：
        w = SubWindow("录制历史 · 独立窗口", parent=main_window)
        w.set_content(page_widget)
        w.show()
        # 关闭时把内容取回归位：
        page_widget = w.take_content()

    多级：
        child = w.open_child("详情", some_widget)  # parent=w，第二级
    """

    def __init__(self, title: str, parent: QWidget | None = None,
                 size: tuple[int, int] = DEFAULT_SIZE,
                 on_closed=None):
        super().__init__(parent)
        # 关闭回调：主窗口用它把「借」出来的页面 widget 还回原滚动容器。
        # 签名 on_closed(window)，调用方从 window.take_content() 取回 widget。
        self._on_closed = on_closed
        self.setObjectName("SubWindow")
        self.setWindowTitle(title)
        # 非模态：用户能同时开着主窗口和多个独立窗口对照看
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumSize(*MIN_SIZE)
        self.resize(*size)
        self._title = title

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏：标题 + 右侧关闭。用 objectName 走 QSS，不内联颜色。
        head = QFrame()
        head.setObjectName("SubWindowHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        hl.setSpacing(10)
        lab = QLabel(title)
        lab.setObjectName("SubWindowTitle")
        hl.addWidget(lab, 1)
        close = QPushButton("✕")
        close.setObjectName("SubWindowClose")
        close.setToolTip("关闭此窗口（内容会回到主界面）")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.close)
        hl.addWidget(close)
        root.addWidget(head)

        # 内容区：滚动承载，页面再长也只滚不撑大窗口
        self._scroll = QScrollArea()
        self._scroll.setObjectName("SubWindowScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        root.addWidget(self._scroll, 1)

        self._content = None

    # ------------------------------------------------------------ 内容装载
    def set_content(self, widget: QWidget | None) -> None:
        """把 widget 放进滚动内容区（会从它原来的父控件移过来）。"""
        if self._content is widget:
            return
        if self._content is not None:
            self._scroll.takeWidget()
        self._content = widget
        if widget is not None:
            self._scroll.setWidget(widget)

    def take_content(self) -> QWidget | None:
        """把内容 widget 摘出来交还调用方（归位用）。"""
        w = self._content
        if w is not None and self._scroll.widget() is w:
            self._scroll.takeWidget()
        self._content = None
        return w

    def content(self) -> QWidget | None:
        return self._content

    # ------------------------------------------------------------ 多级弹窗
    def open_child(self, title: str, widget: QWidget | None = None,
                   size: tuple[int, int] = (760, 560)) -> "SubWindow":
        """从本窗口再弹出一级窗口（parent=self → Qt 原生层级管理）。"""
        child = SubWindow(title, parent=self, size=size)
        if widget is not None:
            child.set_content(widget)
        child.show()
        return child

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, e):                                  # noqa: N802
        # WA_DeleteOnClose 不用：内容 widget 要先被 take_content 归还，
        # 否则 Qt 销毁弹窗时会把页面 widget 一起带走。这里不自动 delete。
        try:
            if self._on_closed is not None:
                self._on_closed(self)
        except Exception:                                      # noqa: BLE001
            import traceback
            traceback.print_exc()
        super().closeEvent(e)
