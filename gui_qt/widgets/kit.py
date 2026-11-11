"""高阶交互组件：展开式面板 / 刻度尺 / 分步选择 / 联动数据板。

★ 为什么单独开一个文件（2026-10-17 三次改版）
  客户原话是「界面缺乏创新的东西」，并给了四个参考：进度展开、刻度滑动、
  分步选择、数据面板联动。这四件事本质上都是**同一个东西**：
  让静态界面"回应"操作 —— 展开时高度会长出来、拖动时刻度会跟手、
  选够数量按钮才亮、点一下单位整组数字一起换算。

  这些东西 Qt 一个都没有现成的（QSlider 是滚动条长相，QToolBox 是硬切，
  QStackedWidget 没有进度点）。所以自己画。

  参考的实现思路（GitHub 上同类交互的实现范式，Qt 侧重写）：
    · 数字滚动：number-flow（滚动每一位）→ 这里简化为整值插值，中文场景够用
    · 展开面板：Radix / vaul 的 disclosure —— 高度动画 + chevron 旋转
    · 刻度滑动：iOS 相机变焦条 —— 中央固定指针 + 轨道反向拖动 + 松手吸附
    · 分步选择：Typeform / Duolingo 的 step flow —— 顶部进度点 + 门禁按钮

★ 三条硬规则（与 motion.py 同源）
  1. 动效只从 motion 的令牌里取（曲线 out / in_out / soft，时长 T_UI /
     T_PANEL / T_HOVER / T_REVEAL），不在这里写裸数字 —— 2026-10-18 之前
     每个组件各写一套 220/260/760，界面节奏对不上。
  2. 一切可关：TRAFFIC_NO_ANIM=1 时全部退化为瞬变；系统「减弱动画」时
     位移 / 缩放 / 吸附惯性自动瞬变（motion.travel_enabled()）。
  3. 自绘组件不读 QSS，颜色一律从 stat.palette(mode) 取，切主题必须
     apply_theme() —— 漏了就会在深色下出现一块浅色（不报错，只难看）。

组件清单：
  DisclosurePanel  可展开面板：标题行 + chevron 旋转 + 高度动画 + 内容
  NodeTimeline     节点时间轴：三个节点依次点亮，解释"走到哪一步了"
  RulerSlider      刻度尺：中央固定指针，横向拖动，松手吸附
  StepDots         步骤点：当前步是长胶囊，已完成实心，未到空心中空
  ChoiceChips      标签多选：点一下切换选中态，超过上限自动拒绝
  ChoiceFlow       分步选择器：StepDots + 两屏内容 + 门禁按钮
  MetricBoard      联动数据板：数字滚动 + 单位整组换算 + 眼睛统一模糊
"""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPointF, QRectF, QSize, Qt,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QFrame, QGraphicsBlurEffect, QHBoxLayout, QLabel,
                               QLayout, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from .motion import (SmoothValue, Tween, T_COUNT, T_HOVER, T_PANEL, T_REVEAL,
                     T_UI, animations_enabled, count_up, mix)
from .stat import palette, ui_font

# 缓动与时长一律走 motion 的令牌（见 motion.py 文件头两张表）。
# 这里只留三个语义别名，方便看出"这个动画是什么性质"：
#   _K_ENTER  入场 / 出场（眼睛一到场就要动）
#   _K_MOVE   屏内移动（刻度尺吸附、箭头转动）
#   _K_REVEAL 长而安静的揭示（节点依次点亮、数字滚动）
_K_ENTER = "out"
_K_MOVE = "in_out"
_K_REVEAL = "soft"


# ---------------------------------------------------------------------------
# 流式布局（标签换行用）
# ---------------------------------------------------------------------------
class FlowLayout(QLayout):
    """按宽度自动换行的布局。Qt 没有内置，官方示例的最小实现。

    ★ 为什么标签必须用流式布局：兴趣标签有长有短（"口播"两个字 vs
      "测评开箱"四个字），用 QGridLayout 会被拉成等宽格子，长短不一的
      标签排出来像表格，不像"标签"。流式布局让每个标签保持自己的宽度。
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):                                 # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):                                     # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):                                     # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):                           # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):                             # noqa: N802
        return True

    def heightForWidth(self, width):                         # noqa: N802
        return self._layout(QRectF(0, 0, width, 0), test=True)

    def setGeometry(self, rect):                             # noqa: N802
        super().setGeometry(rect)
        self._layout(QRectF(rect), test=False)

    def sizeHint(self):                                      # noqa: N802
        return self.minimumSize()

    def minimumSize(self):                                   # noqa: N802
        size = QSize(0, 0)
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRectF, test: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        line_h = 0.0
        right = rect.right() - m.right()
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width()
            if nx > right and line_h > 0:
                x = rect.x() + m.left()
                y += line_h + self._spacing
                nx = x + hint.width()
                line_h = 0.0
            if not test:
                it.setGeometry(QRectF(x, y, hint.width(), hint.height()).toRect())
            x = nx + self._spacing
            line_h = max(line_h, float(hint.height()))
        return int(y + line_h - rect.y() + m.bottom())


# ---------------------------------------------------------------------------
# 步骤点
# ---------------------------------------------------------------------------
class StepDots(QWidget):
    """顶部进度点：当前步是拉长的胶囊，已完成实心，未到只留一个空心圈。

    ★ 为什么当前步要"拉长"而不是"放大"：放大只改变大小，眼睛要靠对比才
      发现；拉长是形状改变，余光都能看见 —— 这是 Duolingo / Typeform
      一路的做法。
    """

    changed = Signal(int)

    def __init__(self, count: int = 3, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._n = max(1, int(count))
        self._step = 0
        self._pos = 0.0                 # 动画中的当前位置（浮点，用来插值形状）
        self._tw = Tween(self, duration=T_UI, kind=_K_MOVE,
                         on_change=self._on_anim)
        self.setFixedHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(self._n * 22)

    def step(self) -> int:
        return self._step

    def set_step(self, i: int):
        i = max(0, min(self._n - 1, int(i)))
        if i == self._step:
            return
        self._step = i
        self.changed.emit(i)
        # 进度点是在屏内"移动"到新位置（不是入场），按 Emil 的表走 ease-in-out
        self._tw.run(self._pos, float(i), duration=T_UI, kind=_K_MOVE, travel=True)

    def _on_anim(self, v):
        self._pos = float(v)
        self.update()

    def apply_theme(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, _e):                                # noqa: N802
        p = palette(self._mode)
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.setPen(Qt.PenStyle.NoPen)
        gap, dot, active_w, h = 6.0, 7.0, 20.0, 7.0
        x = 0.0
        y = (self.height() - h) / 2.0
        for i in range(self._n):
            # 距当前位置越近，"拉长"得越多 —— 拖动式过渡比整块跳更顺眼
            near = max(0.0, 1.0 - abs(self._pos - i))
            w = dot + (active_w - dot) * near
            # ★ 2026-10-18 实拍修正：未到的点原来用 track（#EDEFF3），
            #   压在白卡上几乎看不见 —— 截出来只剩一条孤零零的黑杠，读者
            #   根本不知道"还有第二步"。三档要各自能看见才叫进度指示：
            #   已完成中灰、当前结构色、未到浅灰（对比度 ~1.9，够认不抢眼）。
            if i < self._step:
                col = QColor(p["title"])
            elif i == self._step:
                col = QColor(p["accent"])
            else:
                col = QColor(p["border_hover"])
            q.setBrush(col)
            q.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
            x += w + gap
        q.end()


# ---------------------------------------------------------------------------
# 标签多选
# ---------------------------------------------------------------------------
class _Chip(QWidget):
    """一颗可选标签。自绘是因为 QCheckBox 改不出胶囊形，QPushButton 的
    :checked 又只能换底色、做不了"选中后左边出现一个小勾"。"""

    clicked = Signal()

    def __init__(self, text: str, mode: str = "light", parent=None):
        super().__init__(parent)
        self._text = text
        self._mode = mode
        self._checked = False
        self._hover = SmoothValue(self, 140, on_change=lambda _v: self.update())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def isChecked(self) -> bool:                             # noqa: N802
        return self._checked

    def setChecked(self, on: bool):                          # noqa: N802
        on = bool(on)
        if on != self._checked:
            self._checked = on
            self.updateGeometry()
            self.update()

    def sizeHint(self):                                      # noqa: N802
        f = ui_font(12, QFont.Weight.Medium)
        w = QFontMetrics(f).horizontalAdvance(self._text)
        # 选中时左边多一颗 6px 的圆点 + 8px 间隙，宽度会变 —— 变化很小，
        # 所以不用预留，让它自然撑开反而有"长出来"的手感
        return QSize(int(w) + (34 if self._checked else 26), 32)

    def enterEvent(self, e):                                 # noqa: N802
        self._hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):                                 # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):                          # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def apply_theme(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, _e):                                # noqa: N802
        p = palette(self._mode)
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        h = r.height()
        if self._checked:
            bg = QColor(p["accent"])
            fg = QColor(p["card"] if self._mode == "light" else "#0B0E14")
            bd = bg
        else:
            bg = QColor(p["card_hover"]) if self._hover.value > 0.5 else QColor(p["card"])
            fg = QColor(p["label"])
            bd = QColor(mix(p["border"], p["border_hover"], self._hover.value))
        q.setPen(QPen(bd, 1))
        q.setBrush(bg)
        q.drawRoundedRect(r, h / 2, h / 2)

        f = ui_font(12, QFont.Weight.Medium)
        q.setFont(f)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(self._text)
        if self._checked:
            # 选中：左边一颗小圆点（点 = "已勾上"，比一个勾更轻）
            dot = 5.0
            x = r.left() + 12
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 200))
            q.drawEllipse(QRectF(x, r.center().y() - dot / 2, dot, dot))
            tx = x + dot + 7
        else:
            tx = r.center().x() - tw / 2
        q.setPen(QPen(fg))
        q.drawText(QRectF(tx, r.top(), tw + 2, h),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self._text)
        q.end()


class ChoiceChips(QWidget):
    """一组可多选的标签，超过 max_sel 时拒绝并发出 rejected 信号。"""

    changed = Signal(list)
    rejected = Signal()

    def __init__(self, options, mode: str = "light", max_sel: int = 3,
                 parent=None):
        super().__init__(parent)
        self._mode = mode
        self._max = int(max_sel)
        self._chips: list[_Chip] = []
        lay = FlowLayout(self, margin=0, spacing=8)
        for text in options:
            c = _Chip(text, mode, self)
            c.clicked.connect(lambda t=text: self._toggle(t))
            lay.addWidget(c)
            self._chips.append(c)
        self.setLayout(lay)

    def selected(self) -> list[str]:
        return [c.text() for c in self._chips if c.isChecked()]

    def _toggle(self, text: str):
        chip = next((c for c in self._chips if c.text() == text), None)
        if chip is None:
            return
        if not chip.isChecked() and len(self.selected()) >= self._max:
            self.rejected.emit()
            return
        chip.setChecked(not chip.isChecked())
        self.changed.emit(self.selected())

    def apply_theme(self, mode: str):
        self._mode = mode
        for c in self._chips:
            c.apply_theme(mode)


# ---------------------------------------------------------------------------
# 刻度尺
# ---------------------------------------------------------------------------
class RulerSlider(QWidget):
    """中央固定指针 + 横向拖动的刻度尺，松手吸附到最近刻度。

    ★ 交互设计（照 iOS 相机变焦条的做法）
      手指不动、尺子动。指针永远在正中，所以"当前值"永远在同一个位置 ——
      眼睛不用跟着数字跑。拖动时尺子连续跟手（不跳格），松手才吸附，
      这样既有"物理感"，又保证最终值是整齐的刻度。

    ★ 为什么不用 QSlider
      QSlider 的视觉语言是"音量/音量条"，圆点跑、轨道不动。刻度尺反过来：
      轨道动、指针不动。两者表达的是不同的事（一个是"占比"，一个是"读刻度"）。
    """

    valueChanged = Signal(int)

    def __init__(self, minimum: int = 0, maximum: int = 100, step: int = 5,
                 mode: str = "light", parent=None, px_per_step: int = 9,
                 suffix: str = "", unit: str = ""):
        super().__init__(parent)
        self._mode = mode
        self._min = int(minimum)
        self._max = int(maximum)
        self._step = max(1, int(step))
        self._px = max(4, int(px_per_step))
        self._suffix = suffix
        self._unit = unit
        # 大刻度（带数字的那一档）每隔几小格出现一次。
        # ★ 不能写死 5：15~90 秒按 5 秒一格时有 15 格，每 5 格标一次就变成
        #   15/40/65 —— 数字之间毫无规律，读起来像坏掉的尺子。
        #   这里按"总共标 4~6 个数"反算，15~90 会得到 15/30/45/60/75/90。
        steps = max(1, (self._max - self._min) // self._step)
        self._major = 1
        for cand in (1, 2, 3, 4, 5, 6, 10, 15, 20):
            if steps / cand <= 6:
                self._major = cand
                break
        else:
            self._major = max(1, steps // 5)
        self._v = float(self._min)      # 连续值（拖动时是小数）
        self._value = self._min         # 取整后的值（对外）
        self._drag_from_x: float | None = None
        self._drag_from_v = 0.0
        self._hover = False
        # 松手吸附：物体在屏内从当前位置滑到最近刻度 → ease-in-out
        self._snap = Tween(self, duration=T_UI, kind=_K_MOVE,
                           on_change=self._on_snap)
        self.setMinimumHeight(94)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ---------------------------------------------------------------- 接口
    def value(self) -> int:
        return self._value

    def set_value(self, v: int, animate: bool = False):
        v = self._snap_to(int(v))
        if v == self._value and not animate:
            self._v = float(v)
            self.update()
            return
        self._value = v
        if not animate or not animations_enabled():
            self._v = float(v)
            self.update()
        else:
            self._snap.run(self._v, float(v), duration=T_UI, kind=_K_MOVE,
                           travel=True)
        self.valueChanged.emit(v)

    def _snap_to(self, v: int) -> int:
        v = max(self._min, min(self._max, v))
        k = round((v - self._min) / self._step)
        return max(self._min, min(self._max, self._min + k * self._step))

    def _on_snap(self, v):
        self._v = float(v)
        self.update()

    # ---------------------------------------------------------------- 鼠标
    def mousePressEvent(self, e):                            # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._snap.stop()
            self._drag_from_x = e.position().x()
            self._drag_from_v = self._v
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):                             # noqa: N802
        if self._drag_from_x is None:
            return
        dx = e.position().x() - self._drag_from_x
        v = self._drag_from_v - dx / float(self._px)
        v = max(float(self._min), min(float(self._max), v))
        self._v = v
        new = self._snap_to(int(round(v)))
        if new != self._value:
            self._value = new
            self.valueChanged.emit(new)
        self.update()

    def mouseReleaseEvent(self, e):                          # noqa: N802
        if self._drag_from_x is None:
            return
        self._drag_from_x = None
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        target = float(self._value)
        if abs(target - self._v) < 0.01 or not animations_enabled():
            self._v = target
            self.update()
            return
        self._snap.stop()
        self._snap.setStartValue(self._v)
        self._snap.setEndValue(target)
        self._snap.start()

    def keyPressEvent(self, e):                              # noqa: N802
        """键盘也要能调（无障碍 + 精准微调）。← → 走一格。"""
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.set_animate(self._value - self._step)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.set_animate(self._value + self._step)
        else:
            super().keyPressEvent(e)

    def set_animate(self, v: int):
        self.set_value(v, animate=True)

    def enterEvent(self, e):                                 # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):                                 # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e):                                 # noqa: N802
        d = e.angleDelta().y()
        if d:
            self.set_animate(self._value + (self._step if d > 0 else -self._step))
            e.accept()

    # ---------------------------------------------------------------- 绘制
    def apply_theme(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, _e):                                # noqa: N802
        p = palette(self._mode)
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        cx = w / 2.0
        base_y = h - 22.0

        # ---- 刻度 ----
        # 刻度位置 x(k) = cx + (k - v) * px：k 是刻度序号对应的值
        first = self._min
        last = self._max
        span = int(cx / self._px) + 2
        k0 = int(self._v) - span
        k1 = int(self._v) + span
        q.setPen(QPen(QColor(p["border_hover"]), 1))
        for k in range(max(first, k0), min(last, k1) + 1):
            if (k - first) % self._step != 0:
                continue
            x = cx + (k - self._v) * self._px
            dist = abs(x - cx) / max(1.0, cx)
            alpha = 1.0 - 0.75 * dist          # 越靠边越淡，暗示"还没到头"
            major = (k - first) % (self._step * self._major) == 0
            hh = 11.0 if major else 6.0
            col = QColor(p["border_hover"] if not major else p["sub"])
            # 刻度越靠边越淡（暗示"还没到头"），但**最低不透明度要留住**：
            # 实测 0.12 在深色面板上等于看不见，尺子只剩中间三根。
            col.setAlphaF(max(0.42 if major else 0.30, alpha))
            q.setPen(QPen(col, 1.4 if major else 1.0))
            q.drawLine(QPointF(x, base_y - hh), QPointF(x, base_y))

            if major:
                f = ui_font(9)
                q.setFont(f)
                q.setPen(QPen(QColor(p["sub"])))
                txt = f"{k}"
                tw = QFontMetrics(f).horizontalAdvance(txt)
                q.drawText(QRectF(x - tw / 2 - 2, base_y + 4, tw + 4, 14),
                           int(Qt.AlignmentFlag.AlignCenter), txt)

        # ---- 中央指针 ----
        accent = QColor(p["accent"])
        q.setPen(QPen(accent, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        q.drawLine(QPointF(cx, base_y - 30), QPointF(cx, base_y + 2))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(accent)
        q.drawEllipse(QRectF(cx - 3.5, base_y - 34, 7, 7))

        # ---- 数值 ----
        f = ui_font(28, QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.6)
        q.setFont(f)
        q.setPen(QPen(QColor(p["value"])))
        val = f"{self._value}"
        unit = self._unit
        fm = QFontMetrics(f)
        vw = fm.horizontalAdvance(val)
        if unit:
            fu = ui_font(12, QFont.Weight.Medium)
            uw = QFontMetrics(fu).horizontalAdvance(unit)
            total = vw + 4 + uw
            q.drawText(QRectF(cx - total / 2, 4, vw + 2, 34),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), val)
            q.setFont(fu)
            q.setPen(QPen(QColor(p["sub"])))
            q.drawText(QRectF(cx - total / 2 + vw + 4, 4, uw + 2, 34),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), unit)
        else:
            q.drawText(QRectF(0, 4, w, 34),
                       int(Qt.AlignmentFlag.AlignCenter), val)
        q.end()


# ---------------------------------------------------------------------------
# 节点时间轴
# ---------------------------------------------------------------------------
class NodeTimeline(QWidget):
    """横向节点轴：三个节点依次点亮。

    state 取值：done（已完成，结构色 + 勾）/ warn（有问题，琥珀）/ bad（红）/
                idle（还没到，浅槽）
    """

    def __init__(self, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._nodes: list[dict] = []
        self._t = 0.0
        # 节点依次点亮属于"揭示"（一次、稍慢、安静），不是交互反馈，
        # 所以允许用到 T_REVEAL 这一档 —— 交互类一律不超过 T_UI。
        self._tw = Tween(self, duration=T_REVEAL, kind=_K_REVEAL,
                         on_change=self._on_t)
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_nodes(self, nodes):
        self._nodes = list(nodes or [])
        self.update()

    def play(self):
        """展开时调用：从头点亮一遍，节点依次亮起。"""
        self._t = 0.0
        self._tw.run(0.0, 1.0, duration=T_REVEAL, kind=_K_REVEAL)

    def _on_t(self, v):
        self._t = float(v)
        self.update()

    def apply_theme(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, _e):                                # noqa: N802
        if not self._nodes:
            return
        p = palette(self._mode)
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        n = len(self._nodes)
        rail_y = 13.0
        pad = 14.0
        step = (w - pad * 2) / max(1, n - 1)
        accent = QColor(p["accent"])

        # 轨道（先画一条浅底，再按进度覆盖）
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(QColor(p["track"]))
        q.drawRoundedRect(QRectF(pad, rail_y - 1, (n - 1) * step, 2), 1, 1)
        prog = max(0.0, min(1.0, self._t)) * (n - 1) * step
        if prog > 0:
            q.setBrush(accent)
            q.drawRoundedRect(QRectF(pad, rail_y - 1, prog, 2), 1, 1)

        for i, node in enumerate(self._nodes):
            cx = pad + i * step
            # 第 i 个节点的点亮时刻：整段进度的 i/(n-1) 附近
            local = 0.0 if n <= 1 else max(0.0, min(1.0, self._t * (n - 1) - i + 1))
            state = node.get("state", "idle")
            base = {
                "done": accent,
                "warn": QColor(p["warn"]),
                "bad": QColor(p["bad"]),
                "idle": QColor(p["track"]),
            }.get(state, QColor(p["track"]))
            # idle 但已经在进度里 = 正在"亮起来"的那一个
            if state == "idle" and local > 0.05:
                base = QColor(mix(p["track"], p["accent"], local * 0.55))

            r = 11.0
            if state != "idle" and local <= 0.02:
                # 还没轮到它：先画成空心，等进度过来再填
                q.setPen(QPen(QColor(p["border"]), 1.4))
                q.setBrush(QColor(p["card"]))
                q.drawEllipse(QRectF(cx - r, rail_y - r, r * 2, r * 2))
            else:
                q.setPen(Qt.PenStyle.NoPen)
                q.setBrush(base)
                q.drawEllipse(QRectF(cx - r, rail_y - r, r * 2, r * 2))
                fg = QColor("#FFFFFF") if state in ("warn", "bad") else \
                    QColor(p["card"] if self._mode == "light" else "#0B0E14")
                q.setPen(QPen(fg, 1.8))
                if state in ("done", "warn", "bad"):
                    # 画一个勾（两笔折线），比一个数字更像"这件事办完了"
                    q.drawPolyline([QPointF(cx - 4.2, rail_y + 0.2),
                                    QPointF(cx - 1.0, rail_y + 3.4),
                                    QPointF(cx + 4.4, rail_y - 3.0)])
                else:
                    f = ui_font(10, QFont.Weight.DemiBold)
                    q.setFont(f)
                    q.drawText(QRectF(cx - r, rail_y - r, r * 2, r * 2),
                               int(Qt.AlignmentFlag.AlignCenter), str(i + 1))

            # 文字：标题 12/600，说明 11 淡一档
            # ★ 首尾两个节点不能"居中画"：节点在 x=14 和 x=w-14，居中会把
            #   一半文字甩到控件外面、被裁成半个字（实拍就是这样）。
            #   规则：第一个左对齐、最后一个右对齐、中间居中。
            ft = ui_font(12, QFont.Weight.DemiBold)
            q.setFont(ft)
            q.setPen(QPen(QColor(p["label"])))
            slot = min(w / max(1, n), 170.0)
            if i == 0:
                tx = cx - 4
                align = Qt.AlignmentFlag.AlignLeft
            elif i == n - 1:
                tx = cx + 4 - slot
                align = Qt.AlignmentFlag.AlignRight
            else:
                tx = cx - slot / 2
                align = Qt.AlignmentFlag.AlignHCenter
            q.drawText(QRectF(tx, rail_y + 20, slot, 17),
                       int(align | Qt.AlignmentFlag.AlignTop),
                       node.get("label", ""))
            note = node.get("note", "")
            if note:
                fs = ui_font(11)
                q.setFont(fs)
                q.setPen(QPen(QColor(p["sub"])))
                q.drawText(QRectF(tx, rail_y + 39, slot, 16),
                           int(align | Qt.AlignmentFlag.AlignTop), note)
        q.end()


class _Chevron(QWidget):
    """会转的箭头：0° 朝右（收起），90° 朝下（展开）。"""

    def __init__(self, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._angle = 0.0
        self._tw = Tween(self, duration=T_UI, kind=_K_MOVE,
                         on_change=self._on_v)
        self.setFixedSize(18, 18)

    def to(self, deg: float):
        self._tw.run(self._angle, float(deg), duration=T_UI, kind=_K_MOVE,
                     travel=True)

    def _on_v(self, v):
        self._angle = float(v)
        self.update()

    def apply_theme(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, _e):                                # noqa: N802
        p = palette(self._mode)
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        q.translate(self.width() / 2, self.height() / 2)
        q.rotate(self._angle)
        q.setPen(QPen(QColor(p["sub"]), 1.5, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        q.drawPolyline([QPointF(-2.4, -4.6), QPointF(2.2, 0), QPointF(-2.4, 4.6)])
        q.end()


# ---------------------------------------------------------------------------
# 可展开面板
# ---------------------------------------------------------------------------
class DisclosurePanel(QFrame):
    """标题行常驻、内容可展开的一块面板。

    ★ 为什么这比"直接铺开"好（客户给的第一个参考就是这个）
      默认只显示摘要，需要看细节才展开 —— 页面不需要一直堆满内容。
      展开是**高度动画**而不是 `setVisible`：硬切会让下面的内容整块跳一下，
      高度长出来的过程本身就在告诉用户"这块东西是从这里出来的"。
    """

    toggled = Signal(bool)

    def __init__(self, title: str, meta: str = "", mode: str = "light",
                 parent=None, on_expand=None):
        super().__init__(parent)
        self._mode = mode
        self._expanded = False
        self._on_expand = on_expand
        self._tw = Tween(self, duration=T_PANEL, kind=_K_ENTER,
                         on_change=self._on_h)
        self._tw.finished.connect(self._on_h_done)
        self.setObjectName("Disclosure")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._head = _Head(title, meta, mode, self)
        self._head.clicked.connect(self.toggle)
        v.addWidget(self._head)

        self._body = QWidget(self)
        self._body.setVisible(False)
        self._body.setMaximumHeight(0)
        bv = QVBoxLayout(self._body)
        bv.setContentsMargins(0, 2, 0, 2)
        bv.setSpacing(0)
        v.addWidget(self._body)
        self.body_layout = bv
        self.apply_theme(mode)

    # ---------------------------------------------------------------- 接口
    def add_widget(self, w: QWidget):
        self.body_layout.addWidget(w)

    def add_layout(self, lay):
        self.body_layout.addLayout(lay)

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self):
        self.set_expanded(not self._expanded)

    def _on_h(self, v):
        self._body.setMaximumHeight(max(0, int(v)))

    def _on_h_done(self):
        """展开结束要松开通配高度，收起结束要真的隐藏。

        ★ 为什么要"松开"：展开后内容可能自己变化（状态刷新），高度要能跟着
          长，不能被这一次动画算出来的固定值卡死。
        """
        if self._expanded:
            self._body.setMaximumHeight(16777215)
        else:
            self._body.setMaximumHeight(0)
            self._body.setVisible(False)

    def set_meta(self, text: str):
        self._head.set_meta(text)

    def set_expanded(self, on: bool, animate: bool = True):
        on = bool(on)
        if on == self._expanded:
            return
        self._expanded = on
        self._head.set_open(on, animate)
        if on and self._on_expand is not None:
            try:
                self._on_expand()
            except Exception:                                # noqa: BLE001
                pass
        self.toggled.emit(on)

        body = self._body
        # 高度动画是唯一允许的"动 layout 属性"例外：手风琴没有等价的
        # transform 写法（两条库都点名了这条豁免）。
        ms = T_PANEL if on else int(T_PANEL * 0.7)   # 收起比展开快（Emil：退出 70%）
        if on:
            body.setVisible(True)
            body.setMaximumHeight(0)
            target = max(1, body.layout().sizeHint().height())
            self._tw.run(0, target, duration=ms, kind=_K_ENTER, travel=True)
        else:
            self._tw.run(body.height(), 0, duration=ms, kind=_K_ENTER, travel=True)

    def apply_theme(self, mode: str):
        self._mode = mode
        p = palette(mode)
        self.setStyleSheet(f"#Disclosure{{background:transparent;border:none;}}")
        self._head.apply_theme(mode)
        _ = p


class _Head(QWidget):
    """可点的一整行标题：左边标题 + 说明，右边 chevron。"""

    clicked = Signal()

    def __init__(self, title: str, meta: str, mode: str, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._hover = SmoothValue(self, 140, on_change=lambda _v: self.update())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(34)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self._dot = QWidget(self)
        self._dot.setFixedSize(6, 6)
        h.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title = QLabel(title, self)
        self._title.setStyleSheet("background:transparent;border:none;")
        self._title.setFont(ui_font(12, QFont.Weight.DemiBold))
        h.addWidget(self._title)
        self._meta = QLabel(meta, self)
        self._meta.setStyleSheet("background:transparent;border:none;")
        self._meta.setFont(ui_font(12))
        h.addWidget(self._meta)
        h.addStretch()
        self._chev = _Chevron(mode, self)
        h.addWidget(self._chev)
        self.apply_theme(mode)

    def set_meta(self, text: str):
        self._meta.setText(text)

    def set_open(self, on: bool, animate: bool = True):
        if animate:
            self._chev.to(90.0 if on else 0.0)
        else:
            self._chev._angle = 90.0 if on else 0.0
            self._chev.update()

    def apply_theme(self, mode: str):
        self._mode = mode
        p = palette(mode)
        self._title.setStyleSheet(
            f"color:{p['label']};background:transparent;border:none;")
        self._meta.setStyleSheet(
            f"color:{p['sub']};background:transparent;border:none;")
        self._dot.setStyleSheet(f"background:{p['ok']};border-radius:3px;")
        self._chev.apply_theme(mode)

    def enterEvent(self, e):                                 # noqa: N802
        self._hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):                                 # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):                          # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


# ---------------------------------------------------------------------------
# 分步选择器
# ---------------------------------------------------------------------------
class ChoiceFlow(QFrame):
    """两步式选择：第一步选标签，选够 n 个才亮「下一步」；第二步选投入。

    ★ 门禁为什么必须"不满足就不亮"，而不是"点了弹错误"
      弹错误是在教用户规则，置灰是在**展示**规则。前者要用户读一句话，
      后者用户看一眼按钮就知道"还差一个"。所以数量提示直接写在按钮左边。
    """

    finished = Signal(dict)

    def __init__(self, step1_title: str, options, step2_title: str,
                 step2_options, need: int = 3, mode: str = "light",
                 parent=None, step1_hint: str = "", step2_hint: str = ""):
        super().__init__(parent)
        self._mode = mode
        self._need = int(need)
        self._step = 0
        self._picked: list[str] = []
        self._level = ""
        self.setObjectName("ChoiceFlow")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self.dots = StepDots(2, mode, self)
        v.addWidget(self.dots, 0, Qt.AlignmentFlag.AlignLeft)

        # ---- 第一屏 ----
        self._p1 = QWidget(self)
        p1 = QVBoxLayout(self._p1)
        p1.setContentsMargins(0, 0, 0, 0)
        p1.setSpacing(10)
        self._t1 = QLabel(step1_title, self._p1)
        self._t1.setFont(ui_font(14, QFont.Weight.DemiBold))
        p1.addWidget(self._t1)
        if step1_hint:
            h1 = QLabel(step1_hint, self._p1)
            h1.setWordWrap(True)
            h1.setFont(ui_font(11))
            h1.setObjectName("FlowHint")
            p1.addWidget(h1)
        self.chips = ChoiceChips(options, mode, self._need, self._p1)
        self.chips.changed.connect(self._on_chips)
        self.chips.rejected.connect(self._on_reject)
        p1.addWidget(self.chips)
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 2, 0, 0)
        row1.setSpacing(10)
        self._count = QLabel("", self._p1)
        self._count.setFont(ui_font(11))
        row1.addWidget(self._count)
        row1.addStretch()
        self.next_btn = QPushButton("下一步", self._p1)
        self.next_btn.setObjectName("Primary")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setFixedHeight(32)
        self.next_btn.clicked.connect(lambda: self.set_step(1))
        row1.addWidget(self.next_btn)
        p1.addLayout(row1)
        v.addWidget(self._p1)

        # ---- 第二屏 ----
        self._p2 = QWidget(self)
        p2 = QVBoxLayout(self._p2)
        p2.setContentsMargins(0, 0, 0, 0)
        p2.setSpacing(10)
        self._t2 = QLabel(step2_title, self._p2)
        self._t2.setFont(ui_font(14, QFont.Weight.DemiBold))
        p2.addWidget(self._t2)
        if step2_hint:
            h2 = QLabel(step2_hint, self._p2)
            h2.setWordWrap(True)
            h2.setFont(ui_font(11))
            h2.setObjectName("FlowHint")
            p2.addWidget(h2)
        self.levels = _LevelRow(step2_options, mode, self._p2)
        self.levels.changed.connect(self._on_level)
        p2.addWidget(self.levels)
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 2, 0, 0)
        row2.setSpacing(10)
        back = QPushButton("上一步", self._p2)
        back.setObjectName("EmptyAction")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.setFixedHeight(32)
        back.clicked.connect(lambda: self.set_step(0))
        row2.addWidget(back)
        row2.addStretch()
        self.done_btn = QPushButton("保存偏好", self._p2)
        self.done_btn.setObjectName("Primary")
        self.done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.done_btn.setFixedHeight(32)
        self.done_btn.setEnabled(False)
        self.done_btn.clicked.connect(self._finish)
        row2.addWidget(self.done_btn)
        p2.addLayout(row2)
        self._p2.setVisible(False)
        v.addWidget(self._p2)

        self._on_chips([])
        self.apply_theme(mode)

    # ---------------------------------------------------------------- 逻辑
    def set_step(self, i: int):
        i = 1 if i >= 1 else 0
        if i == 1 and len(self._picked) < self._need:
            return
        self._step = i
        self.dots.set_step(i)
        self._p1.setVisible(i == 0)
        self._p2.setVisible(i == 1)

    def _on_chips(self, picked: list[str]):
        self._picked = list(picked)
        left = self._need - len(picked)
        if left > 0:
            self._count.setText(f"还差 {left} 个")
        else:
            self._count.setText("选好了")
        self.next_btn.setEnabled(left <= 0)
        self.apply_theme(self._mode)

    def _on_reject(self):
        self._count.setText(f"最多选 {self._need} 个")
        self.apply_theme(self._mode)

    def _on_level(self, name: str):
        self._level = name
        self.done_btn.setEnabled(bool(name))

    def _finish(self):
        self.finished.emit({"tags": list(self._picked), "level": self._level})

    # ---------------------------------------------------------------- 主题
    def apply_theme(self, mode: str):
        self._mode = mode
        p = palette(mode)
        self.setStyleSheet("#ChoiceFlow{background:transparent;}")
        for lab in (self._t1, self._t2):
            lab.setStyleSheet(f"color:{p['value']};background:transparent;")
        for lab in self.findChildren(QLabel, "FlowHint"):
            lab.setStyleSheet(f"color:{p['sub']};background:transparent;")
        # 「还差 N 个」用琥珀色：它不是错误，是"差一点"的提示
        self._count.setStyleSheet(
            f"color:{p['warn'] if self.next_btn.isEnabled() is False else p['sub']};"
            f"background:transparent;")
        self.dots.apply_theme(mode)
        self.chips.apply_theme(mode)
        self.levels.apply_theme(mode)


class _LevelRow(QWidget):
    """一排互斥的"投入程度"按钮（每天 30 分钟 / 1 小时 / 2 小时以上）。"""

    changed = Signal(str)

    def __init__(self, options, mode: str, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._chips: list[_Chip] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for text in options:
            c = _Chip(text, mode, self)
            c.clicked.connect(lambda t=text: self._pick(t))
            row.addWidget(c)
            self._chips.append(c)
        row.addStretch()

    def _pick(self, text: str):
        for c in self._chips:
            c.setChecked(c.text() == text)
        self.changed.emit(text)

    def apply_theme(self, mode: str):
        self._mode = mode
        for c in self._chips:
            c.apply_theme(mode)


# ---------------------------------------------------------------------------
# 联动数据板
# ---------------------------------------------------------------------------
class MetricCell(QWidget):
    """一格数据：小标签 + 大数字 + 一句说明。"""

    def __init__(self, label: str, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._raw = 0
        self._kind = "count"
        self._sub = ""
        self._unit_mode = "raw"
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        self.cap = QLabel(label, self)
        self.cap.setObjectName("MetricTitle")
        self.cap.setFont(ui_font(12, QFont.Weight.DemiBold))
        v.addWidget(self.cap)
        self.val = QLabel("—", self)
        self.val.setObjectName("MetricValue")
        self.val.setFont(ui_font(26, QFont.Weight.DemiBold))
        # ★ 2026-10-27：数字必须定高 + 底部对齐，理由同 stat.py 的 BigNumber ——
        #   一排四格里有一格是 "32 KB"（文本）、其余是计数，字号一不同
        #   下面的说明就会错位成手写表格。
        self.val.setFixedHeight(42)
        self.val.setAlignment(Qt.AlignmentFlag.AlignLeft
                              | Qt.AlignmentFlag.AlignBottom)
        v.addWidget(self.val)
        self.sub = QLabel("", self)
        self.sub.setObjectName("MetricSub")
        self.sub.setFont(ui_font(11))
        v.addWidget(self.sub)
        self.setMinimumHeight(92)
        self.apply_theme(mode)

    def set_value(self, raw, sub: str = "", kind: str = "count"):
        self._kind = kind
        self._sub = sub
        if kind == "text":
            # 文本类（"1.2 MB"）不参与滚动与单位换算 —— 给它插值没有意义，
            # 中间态会是"0.7 MB"这种假数字
            self.val.setText(str(raw))
            self.sub.setText(sub)
            return
        try:
            self._raw = float(raw)
        except (TypeError, ValueError):
            self._raw = 0.0
        text = self._fmt(self._raw)
        if kind == "count":
            count_up(self.val, text, 340)
        else:
            self.val.setText(text)
        self.sub.setText(sub)

    def _fmt(self, v: float) -> str:
        if self._kind != "count":
            return f"{int(v)}"
        if self._unit_mode == "wan":
            if v >= 10000:
                return f"{v / 10000:.1f}万"
            return f"{int(v)}"
        return f"{int(round(v)):,}"

    def set_unit_mode(self, mode: str):
        self._unit_mode = "wan" if mode == "wan" else "raw"
        if self._kind == "count":
            count_up(self.val, self._fmt(self._raw), 300)

    def apply_theme(self, mode: str):
        self._mode = mode
        p = palette(mode)
        # ★ 字号必须写进内联样式（2026-10-27）：QSS 里 `QWidget{font-size:13px}`
        #   会盖掉 setFont 的磅值 —— 这也是"数字一直显不出来"的原因：
        #   代码里写的是 26 磅，屏幕上按 13px 渲染。写死 px 才算数。
        self.cap.setStyleSheet(
            f"color:{p['title']};font-size:12px;background:transparent;"
            f"letter-spacing:0;")
        self.val.setStyleSheet(
            f"color:{p['value']};font-size:32px;font-weight:600;"
            f"letter-spacing:-1px;background:transparent;")
        self.sub.setStyleSheet(
            f"color:{p['sub']};font-size:12px;background:transparent;")


class Segmented(QWidget):
    """两选一的分段控件（原始 / 万）。"""

    changed = Signal(str)

    def __init__(self, options, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._opts = list(options)              # [(text, key), ...]
        self._cur = self._opts[0][1] if self._opts else ""
        self._chips: list[_Chip] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for text, key in self._opts:
            c = _Chip(text, mode, self)
            c.setChecked(key == self._cur)
            c.clicked.connect(lambda k=key: self.set_value(k))
            row.addWidget(c)
            self._chips.append(c)
        self._chips and None

    def value(self) -> str:
        return self._cur

    def set_value(self, key: str):
        if key == self._cur:
            return
        self._cur = key
        for c, (_t, k) in zip(self._chips, self._opts):
            c.setChecked(k == key)
        self.changed.emit(key)

    def apply_theme(self, mode: str):
        self._mode = mode
        for c in self._chips:
            c.apply_theme(mode)


class IconToggle(QWidget):
    """眼睛按钮：点一下所有数值统一模糊。

    ★ 为什么是"模糊"而不是"变成星号"
      星号会让人以为数据没了（客户会以为软件出错）；模糊是"我知道它在那，
      只是现在不想让人看见" —— 开会投屏、给同事看屏幕时用得上。
    """

    toggled = Signal(bool)

    def __init__(self, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._on = False
        self._hover = SmoothValue(self, 140, on_change=lambda _v: self.update())
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("遮住所有数字（投屏时用）")

    def is_masked(self) -> bool:
        return self._on

    def set_masked(self, on: bool):
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self.update()
        self.toggled.emit(on)

    def mouseReleaseEvent(self, e):                          # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.set_masked(not self._on)
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):                                 # noqa: N802
        self._hover.to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):                                 # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(e)

    def apply_theme(self, mode: str):
        self._mode = mode
        self.update()

    def paintEvent(self, _e):                                # noqa: N802
        p = palette(self._mode)
        q = QPainter(self)
        q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover.value > 0.01:
            q.setPen(Qt.PenStyle.NoPen)
            c = QColor(p["card_hover"])
            c.setAlphaF(0.9 * self._hover.value)
            q.setBrush(c)
            q.drawRoundedRect(r, 8, 8)
        col = QColor(p["accent"] if self._on else p["sub"])
        q.setPen(QPen(col, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx, cy = r.center().x(), r.center().y()
        # 眼睛：一段弧 + 中间一个点；关闭时压一条斜线
        path = QPainterPath()
        path.moveTo(cx - 7.5, cy)
        path.quadTo(cx, cy - 6.4, cx + 7.5, cy)
        path.quadTo(cx, cy + 6.4, cx - 7.5, cy)
        q.drawPath(path)
        q.setBrush(col)
        q.drawEllipse(QRectF(cx - 2.4, cy - 2.4, 4.8, 4.8))
        if self._on:
            q.setBrush(Qt.BrushStyle.NoBrush)
            q.drawLine(QPointF(cx - 7, cy + 6.5), QPointF(cx + 7, cy - 6.5))
        q.end()


class MetricBoard(QFrame):
    """一排联动数据：数字滚动 + 单位整组换算 + 眼睛统一模糊。"""

    def __init__(self, mode: str = "light", parent=None, unit_toggle: bool = True,
                 eye: bool = True, gap: int = 22):
        super().__init__(parent)
        self._mode = mode
        self._cells: list[MetricCell] = []
        self._blurs: dict[QLabel, QVariantAnimation] = {}
        self.setObjectName("MetricBoard")

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # 顶部小工具条：左"按天汇总"之类的说明，右"单位 + 眼睛"
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.note = QLabel("", self)
        self.note.setFont(ui_font(12))
        bar.addWidget(self.note)
        bar.addStretch()
        self.unit = None
        if unit_toggle:
            self.unit = Segmented([("原始", "raw"), ("万", "wan")], mode, self)
            self.unit.changed.connect(self._on_unit)
            bar.addWidget(self.unit)
        self.eye = None
        if eye:
            self.eye = IconToggle(mode, self)
            self.eye.toggled.connect(self.set_masked)
            bar.addWidget(self.eye)
        v.addLayout(bar)

        self._row = QHBoxLayout()
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(gap)
        v.addLayout(self._row)
        self.apply_theme(mode)

    # ---------------------------------------------------------------- 接口
    def add_metric(self, label: str) -> MetricCell:
        if self._cells:
            sep = QFrame(self)
            sep.setFixedWidth(1)
            sep.setObjectName("VLine")
            sep.setStyleSheet(f"background:{palette(self._mode)['border']};")
            self._row.addWidget(sep)
        cell = MetricCell(label, self._mode, self)
        self._row.addWidget(cell, 1)
        self._cells.append(cell)
        return cell

    def set_note(self, text: str):
        self.note.setText(text)

    def set_masked(self, on: bool):
        p = palette(self._mode)
        self.note.setText("已遮住数字" if on else "")
        self.note.setStyleSheet(f"color:{p['sub']};background:transparent;")
        for cell in self._cells:
            for lab in (cell.val, cell.sub):
                self._blur(lab, on)

    def _blur(self, lab: QLabel, on: bool):
        old = self._blurs.pop(lab, None)
        if old is not None:
            old.stop()
        eff = lab.graphicsEffect()
        if on:
            if not isinstance(eff, QGraphicsBlurEffect):
                eff = QGraphicsBlurEffect(lab)
                lab.setGraphicsEffect(eff)
            eff.setBlurRadius(0.2)
            start = 0.2
            # 4.2 而不是 6.5：6.5 在 26px 数字上会糊成一坨色块，看着像控件坏了；
            # 4.2 还认得出"这里有一串数字"，只是读不出来 —— 那才是"遮住"的语义。
            end = 4.2
        else:
            if not isinstance(eff, QGraphicsBlurEffect):
                return
            start = float(eff.blurRadius())
            end = 0.0

        def _apply(v, target=lab):
            e = target.graphicsEffect()
            if isinstance(e, QGraphicsBlurEffect):
                e.setBlurRadius(float(v))

        # 遮住是"状态指示"类，走 T_UI；揭开比遮住快一点（退出 70%）
        tw = Tween(lab, duration=T_UI if on else int(T_UI * 0.7),
                   kind=_K_ENTER, on_change=_apply)
        if not on:
            # ★ 模糊效果不摘掉，Qt 会一直走离屏渲染（滚动变卡、字也发虚）
            tw.finished.connect(lambda t=lab: t.setGraphicsEffect(None))
        self._blurs[lab] = tw
        tw.run(start, end)

    def _on_unit(self, key: str):
        for cell in self._cells:
            cell.set_unit_mode(key)

    def apply_theme(self, mode: str):
        self._mode = mode
        p = palette(mode)
        self.setStyleSheet("#MetricBoard{background:transparent;}")
        self.note.setStyleSheet(f"color:{p['sub']};background:transparent;")
        for c in self._cells:
            c.apply_theme(mode)
        if self.unit is not None:
            self.unit.apply_theme(mode)
        if self.eye is not None:
            self.eye.apply_theme(mode)


__all__ = ["FlowLayout", "StepDots", "ChoiceChips", "ChoiceFlow", "RulerSlider",
           "NodeTimeline", "DisclosurePanel", "MetricBoard", "MetricCell",
           "Segmented", "IconToggle"]
