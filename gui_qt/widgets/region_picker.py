"""屏幕区域选择浮层（PySide6）。

提供 RegionPicker.pick() 类方法：弹出半透明全屏遮罩，用户用鼠标拖拽出
一个矩形选区，松手后在选区旁浮出「录制 / 重选 / 取消」工具条。点「录制」
返回 QRect（已按设备像素比 DPR 换算，直接喂给 ffmpeg gdigrab），取消返回 None。

★ 必须在真正启动 ffmpeg 之前把自己隐藏掉 —— 否则录出来的画面里会带着这层
  遮罩和选区边框。pick() 在返回前先 hide() 再 accept()，保证时序正确。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QPoint, QRect, QSize, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QPushButton,
                               QWidget)

# 与品牌一致：深墨遮罩 + 柠檬强调色
_DIM = QColor(11, 14, 20, 150)        # #0B0E14 半透明
_ACID = "#C8F751"


class RegionPicker(QDialog):
    region_selected = Signal(QRect)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._start = QPoint()
        self._end = QPoint()
        self._dragging = False
        self._rect = QRect()
        self._toolbar: QWidget | None = None
        self._result_rect: QRect | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)
        self.setModal(True)

        screen = QApplication.primaryScreen()
        self._dpr = screen.devicePixelRatio() if screen else 1.0
        geo = screen.geometry() if screen else QRect(0, 0, 1280, 720)
        self.setGeometry(geo)

    # ------------------------------------------------------------------
    @staticmethod
    def pick(parent=None) -> QRect | None:
        """模态弹出选区；返回设备像素的 QRect，或取消时返回 None。"""
        app = QApplication.instance() or QApplication(sys.argv)
        dlg = RegionPicker(parent)
        dlg.show()
        dlg.activateWindow()
        app.processEvents()
        if dlg.exec() == QDialog.Accepted:
            return dlg._result_rect
        return None

    # ------------------------------------------------------------------
    def _norm_rect(self) -> QRect:
        return QRect(self._start, self._end).normalized()

    def _build_toolbar(self) -> QWidget:
        tb = QWidget(self)
        tb.setObjectName("RegionToolbar")
        layout = QHBoxLayout(tb)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        b_ok = QPushButton("录制")
        b_ok.setObjectName("Primary")
        b_ok.clicked.connect(self._accept)
        b_redo = QPushButton("重选")
        b_redo.clicked.connect(self._redo)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self._cancel)

        layout.addWidget(b_ok)
        layout.addWidget(b_redo)
        layout.addWidget(b_cancel)
        tb.adjustSize()
        tb.setStyleSheet(
            "QWidget#RegionToolbar{background:#161B22;border:1px solid #2A313C;"
            "border-radius:12px;}"
        )
        return tb

    def _show_toolbar(self):
        if self._toolbar is None:
            self._toolbar = self._build_toolbar()
        tb = self._toolbar
        r = self._rect
        # 默认放在选区右下角外侧；超出屏幕底边则翻到上方
        x = r.right() - tb.width()
        y = r.bottom() + 8
        if y + tb.height() > self.height():
            y = r.top() - tb.height() - 8
        x = max(4, min(x, self.width() - tb.width() - 4))
        y = max(4, y)
        tb.move(x, y)
        tb.show()
        tb.raise_()

    def _hide_toolbar(self):
        if self._toolbar is not None:
            self._toolbar.hide()

    # ------------------------------------------------------------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._hide_toolbar()
            self._start = ev.pos()
            self._end = ev.pos()
            self._dragging = True
            self.update()

    def mouseMoveEvent(self, ev):
        if self._dragging:
            self._end = ev.pos()
            self._rect = self._norm_rect()
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._dragging and ev.button() == Qt.LeftButton:
            self._dragging = False
            self._rect = self._norm_rect()
            if self._rect.width() > 4 and self._rect.height() > 4:
                self._show_toolbar()
            self.update()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(ev)

    # ------------------------------------------------------------------
    def _accept(self):
        # ★ 用起止点的跨度算宽高，避开 QRect 闭区间（QRect((0,0),(400,300))
        #   实际是 401×301 像素）的 off-by-one —— 录屏 video_size 要的是真实像素数。
        x0, y0 = self._start.x(), self._start.y()
        x1, y1 = self._end.x(), self._end.y()
        x = min(x0, x1)
        y = min(y0, y1)
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        if w <= 0 or h <= 0:
            return
        dpr = self._dpr
        self._result_rect = QRect(
            int(x * dpr), int(y * dpr),
            int(w * dpr), int(h * dpr),
        )
        self.region_selected.emit(self._result_rect)
        self.hide()                 # ★ 先隐藏自己，避免被录进画面
        self.accept()

    def _redo(self):
        self._hide_toolbar()
        self._rect = QRect()
        self._start = QPoint()
        self._end = QPoint()
        self.update()

    def _cancel(self):
        self.cancelled.emit()
        self.reject()

    # ------------------------------------------------------------------
    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_DIM)
        painter.drawRect(self.rect())            # 整屏压暗

        if not self._rect.isNull() and self._rect.isValid():
            # 选区内部清成透明，露出底下真实桌面
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.setBrush(Qt.transparent)
            painter.drawRect(self._rect)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            # 柠檬边框
            pen = QPen(QColor(_ACID))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._rect)
            # 尺寸标签
            label = f"{self._rect.width()} × {self._rect.height()}"
            font = QFont()
            font.setPointSize(11)
            painter.setFont(font)
            painter.setPen(QColor(_ACID))
            painter.drawText(self._rect.x(), max(2, self._rect.y() - 8), label)
