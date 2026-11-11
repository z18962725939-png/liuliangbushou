"""进度条组件：粗体百分比 + 实时流光动画（v1.14.4）。

★ 为什么自己画，而不是继续用 QProgressBar + QSS：
  1. QSS 没有 animation，进度条只能静止 —— 下载大文件时条不动，用户分不清
     是「在跑」还是「卡死」；
  2. 原 QSS 里 `QProgressBar { max-height: 6px; text: none; }`，条只有 6px 高、
     还不显示百分比，站在两步之外根本看不见走到哪；
  3. 这里要的只是「一条圆角填充 + 斜向流光 + 右侧数字」，几十行 QPainter
     就够，不需要引动画库。

组件：
  FlowProgressBar   进度条容器（自绘的条 + 右侧粗体百分比，可选说明文字）

行为：
  - 0%           灰色数字，条不流动
  - 1%~99%       靛蓝渐变 + 斜向流光持续流动（每秒约 30 帧）
  - 100%         转为绿色并停住，数字显示 100%
  - setRange(0,0) 忙碌态（事先不知道总量）：条上来回扫光，数字显示「处理中」

接口与 QProgressBar 兼容（setValue / setRange / value / minimum / maximum /
reset / setVisible / setTextVisible / setText），所以各页面只需把
`QProgressBar()` 换成 `FlowProgressBar()`，其余代码不用动。

颜色跟随浅色 / 深色主题（读 QApplication 的 theme_mode 属性），集中在本文件
顶部的调色板里。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QProgressBar,
                               QSizePolicy, QWidget)

# ---------------------------------------------------------------- 调色板

_LIGHT = {
    "track": "#E9EBF0",
    "edge": "#DCDFE6",
    # 渐变按品牌墨：起点略浅、终点纯墨，条本身在浅灰槽上对比极强
    "bar1": "#3A4250",
    "bar2": "#0B0E14",
    "done1": "#34D399",     # 完成：绿（"做完了"是唯一该用绿色的语义）
    "done2": "#0F9960",
    "stripe": (255, 255, 255, 46),   # 流光条纹（半透明白）—— 墨底上很清楚
    "idle": "#5B6472",      # 0% 时的数字色（别太浅，否则等于没显示）
    "run": "#0B0E14",       # 进行中的数字色
    "done": "#0F9960",      # 完成的数字色
    "note": "#5B6472",      # 说明文字
}

_DARK = {
    "track": "#272C36",
    "edge": "#333A47",
    # 深色主题进度条用柠檬（深底上唯一的亮色）
    "bar1": "#E8FFA8",
    "bar2": "#C8F751",
    "done1": "#34D399",
    "done2": "#0F9960",
    # ★ 条纹必须是深色：改版前是半透明白，现在底变成更亮的柠檬，
    #   白条纹落在上面几乎看不见 —— 流光动画会等于白做。
    "stripe": (11, 14, 20, 46),
    "idle": "#8B93A1",
    "run": "#C8F751",
    "done": "#34D399",
    "note": "#9AA4B2",
}

_PALETTE = {"light": _LIGHT, "dark": _DARK}

# ---------------------------------------------------------------- 动画参数

_FRAME_MS = 33          # 约 30fps：再快只是白烧 CPU，肉眼看不出差别
_STRIPE_W = 11          # 流光条纹宽度
_STRIPE_GAP = 15        # 两条条纹之间的间隔
_STEP = _STRIPE_W + _STRIPE_GAP
_SPEED = 1.6            # 每帧位移（px）：太快会晃眼，太慢看不出在动

_BAR_H = 14             # 条的厚度（原来是 6px，翻倍才看得见）
# 百分比数字区的兜底宽度。真正取多宽按字体度量算（见 _pct_width）——
# 实测 12pt 下 "100%" 就要 64px，写死 58px 会把百分号切掉，
# 而 Windows 常见 125% / 150% 缩放会让它更宽。
_PCT_W = 68


def _theme() -> str:
    """当前主题模式；读不到就按浅色处理（不抛异常，界面优先）。"""
    try:
        app = QApplication.instance()
        mode = str(app.property("theme_mode") or "") if app is not None else ""
    except Exception:  # noqa: BLE001
        mode = ""
    return "dark" if mode == "dark" else "light"


# ---------------------------------------------------------------- 自绘的条

class _FlowBar(QProgressBar):
    """只有条本身：轨道 + 渐变填充 + 斜向流光；不画文字（文字在容器上）。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 100)
        self.setValue(0)
        self.setFixedHeight(_BAR_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 覆盖全局 QSS 里 `QProgressBar { max-height: 6px }` 的限制：
        # 用 ID 选择器（优先级高于类型选择器），不动 QSS 文件，避免波及其它条。
        self.setObjectName("flowbar")
        self.setStyleSheet(
            "#flowbar { border: none; background: transparent; "
            "min-height: %dpx; max-height: %dpx; }" % (_BAR_H, _BAR_H))

        self._phase = 0.0
        self._busy = False
        self._done = False
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    # -- 状态开关 ------------------------------------------------------
    def set_busy(self, on: bool) -> None:
        on = bool(on)
        if on != self._busy:
            self._busy = on
            self.update()

    def set_done(self, on: bool) -> None:
        on = bool(on)
        if on != self._done:
            self._done = on
            self.update()

    def set_animating(self, on: bool) -> None:
        """启停流光定时器。空闲时停掉，别让十几个页面同时空转烧 CPU。"""
        on = bool(on)
        if on and not self._timer.isActive():
            self._timer.start()
        elif not on and self._timer.isActive():
            self._timer.stop()

    def animating(self) -> bool:
        return bool(self._timer.isActive())

    # -- 绘制 ----------------------------------------------------------
    def _tick(self):
        self._phase += _SPEED
        if self._phase > 100000:      # 长时间运行也不让浮点越滚越大
            self._phase = 0.0
        self.update()

    def paintEvent(self, _ev):        # noqa: N802（Qt 的命名约定）
        pal = _PALETTE[_theme()]
        w, h = float(self.width()), float(self.height())
        if w <= 0 or h <= 0:
            return
        rad = h / 2.0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)

        # 轨道
        p.setBrush(QColor(pal["track"]))
        p.drawRoundedRect(QRectF(0.0, 0.0, w, h), rad, rad)

        # 填充区间：忙碌态是一段来回扫的光块，否则按百分比
        if self._busy:
            span = max(w * 0.34, 36.0)
            travel = max(w - span, 1.0)
            cycle = travel * 2.0
            pos = self._phase % cycle
            x = pos if pos <= travel else (cycle - pos)
            fill = QRectF(x, 0.0, span, h)
        else:
            lo, hi = float(self.minimum()), float(self.maximum())
            v = float(self.value())
            frac = 0.0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
            fill = QRectF(0.0, 0.0, w * frac, h)

        if fill.width() > 0.6:
            # 渐变填充（裁剪在圆角内，避免直角溢出圆角）
            grad = QLinearGradient(fill.left(), 0.0, fill.right(), 0.0)
            grad.setColorAt(0.0, QColor(pal["done1"] if self._done else pal["bar1"]))
            grad.setColorAt(1.0, QColor(pal["done2"] if self._done else pal["bar2"]))
            clip = QPainterPath()
            clip.addRoundedRect(fill, rad, rad)
            p.save()
            p.setClipPath(clip)
            p.fillRect(fill, grad)

            # 斜向流光条纹：只在动画中画，静止时保持干净
            if self._timer.isActive():
                sr, sg, sb, sa = pal["stripe"]
                p.setBrush(QColor(sr, sg, sb, sa))
                x = -(h + _STEP) + (self._phase % _STEP)
                while x < fill.right() + _STEP:
                    poly = QPolygonF([
                        QPointF(x, 0.0),
                        QPointF(x + _STRIPE_W, 0.0),
                        QPointF(x + _STRIPE_W - h, h),
                        QPointF(x - h, h),
                    ])
                    p.drawPolygon(poly)
                    x += _STEP
            p.restore()

        # 极淡的内描边：浅色轨道上也能看清条的轮廓
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(pal["edge"]), 1.0))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), rad, rad)
        p.end()


# ---------------------------------------------------------------- 对外组件

class FlowProgressBar(QWidget):
    """进度条 + 粗体百分比（带流光动画）。

    用法与 QProgressBar 完全一致::

        self.progress = FlowProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(45)         # 数字显示「45%」，条纹开始流动
        self.progress.setRange(0, 0)       # 忙碌态：扫光 + 「处理中」
        self.progress.setText("3/10 条")   # 可选：百分比左侧的小字说明
    """

    def __init__(self, parent: QWidget | None = None,
                 note: str = "", bar_height: int = _BAR_H):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # 容器要留出字号的高度：12pt 文字在 125%/150% 缩放下会更高，
        # 只按条厚给高度会把「100%」的上下切掉。
        self.setMinimumHeight(max(int(bar_height) + 6, 22))

        self._bar = _FlowBar(self)
        if bar_height and bar_height != _BAR_H:
            self._bar.setFixedHeight(int(bar_height))
            self._bar.setStyleSheet(
                "#flowbar { border: none; background: transparent; "
                "min-height: %dpx; max-height: %dpx; }" % (bar_height, bar_height))
        lay.addWidget(self._bar, 1)

        self._note = QLabel("")
        self._note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._note, 0)

        self._pct = QLabel("0%")
        self._pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        f = QFont(self.font())
        f.setPointSize(12)
        f.setBold(True)
        self._pct.setFont(f)
        self._pct.setFixedWidth(self._pct_width())
        lay.addWidget(self._pct, 0)

        self._tone = ""
        self.set_note(note)
        self._refresh()

    # -- 兼容 QProgressBar 的接口 --------------------------------------
    def setValue(self, v: int) -> None:
        self._bar.setValue(int(v))
        self._refresh()

    def setRange(self, lo: int, hi: int) -> None:
        self._bar.setRange(int(lo), int(hi))
        self._refresh()

    def value(self) -> int:
        return self._bar.value()

    def minimum(self) -> int:
        return self._bar.minimum()

    def maximum(self) -> int:
        return self._bar.maximum()

    def reset(self) -> None:
        self._bar.reset()
        self._refresh()

    def setTextVisible(self, _on: bool) -> None:
        """百分比始终显示（老代码里的 setTextVisible(True) 无需再管）。"""

    def setText(self, text: str) -> None:
        """设置百分比左侧的说明文字；传空串则隐藏。"""
        self.set_note(text or "")

    def set_note(self, text: str) -> None:
        self._note.setText(text or "")
        self._note.setVisible(bool(text))

    def note(self) -> str:
        return self._note.text()

    # -- 内部 ----------------------------------------------------------
    def _pct_width(self) -> int:
        """按当前字体量出「100%」要多宽 —— 缩放设置变了也不会切字。"""
        try:
            fm = QFontMetrics(self._pct.font())
            return max(_PCT_W, fm.horizontalAdvance("100%") + 10)
        except Exception:  # noqa: BLE001
            return _PCT_W

    def changeEvent(self, ev):        # noqa: N802（Qt 的命名约定）
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.FontChange:
            self._pct.setFixedWidth(self._pct_width())

    def _set_tone(self, tone: str) -> None:
        """只在状态切换时改颜色：setStyleSheet 会触发重排，别每帧调。"""
        if tone == self._tone:
            return
        self._tone = tone
        pal = _PALETTE[_theme()]
        self._pct.setStyleSheet(f"QLabel {{ color: {pal[tone]}; }}")

    def _refresh(self) -> None:
        lo, hi = self._bar.minimum(), self._bar.maximum()
        if lo == 0 and hi == 0:                       # 忙碌态
            self._bar.set_busy(True)
            self._bar.set_done(False)
            self._bar.set_animating(True)
            self._pct.setText("处理中")
            self._set_tone("run")
            return

        self._bar.set_busy(False)
        frac = 0.0 if hi <= lo else max(0.0, min(1.0, (self._bar.value() - lo) / float(hi - lo)))
        pct = int(round(frac * 100))
        self._pct.setText(f"{pct}%")
        done = pct >= 100
        self._bar.set_done(done)
        # 只有「走在路上」才流动：0% 和 100% 停住，省 CPU 也不晃眼
        self._bar.set_animating(0 < pct < 100)
        self._set_tone("done" if done else ("run" if pct > 0 else "idle"))
        self._bar.update()
