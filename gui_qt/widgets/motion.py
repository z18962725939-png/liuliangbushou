"""轻量 UI 动效：淡入、悬浮过渡、数值增长。

★ 为什么自己写而不引动画库：
  这里要的只是「切页时不硬切」「鼠标移上去不硬变」——用 Qt 自带的
  QPropertyAnimation / QVariantAnimation 十几行就够，多一个依赖就多一份
  打包体积和启动时间（见 stat.py 头部同样的取舍）。

★ 2026-10-18 动效令牌化（依据 Emil Kowalski《animate》与 tasteskill《motion》）
  改版前这个文件里散着 4 处 `QEasingCurve.Type.OutCubic`、时长从 120ms 到
  760ms 各写各的：曲线是 Qt 内置的弱 ease-out、时长是拍脑袋的整数。看起来
  都在"能动"的范围里，但整套界面没有统一节奏 —— 有的地方干脆、有的地方拖。

  现在全部收进两张表，任何新动效只能从表里取，不许再手写数字：

    曲线（与 CSS 同名同值，浏览器/官网那边是同一套）
      out     0.22,1,0.36,1   入场 / 出场 / 揭示：眼睛一到场就开始动
      in_out  0.76,0,0.24,1   屏内移动：从一个位置到另一个位置
      soft    0.33,1,0.68,1   长时间安静的揭示、数值滚动
      linear                   匀速：进度条、跑马灯

    时长
      T_PRESS  140   按下反馈
      T_HOVER  180   悬浮 / 颜色过渡
      T_UI     240   面板、菜单、切页
      T_PANEL  260   展开 / 折叠（手风琴）
      T_COUNT  520   数字滚动
      T_REVEAL 800   入场揭示上限

  ★ 为什么不用 Qt 内置的 OutCubic：它就是 CSS 的 `ease-out` 关键字那一级，
    起步不够快，200ms 里前 3 帧几乎没动。两条库都明确点名"内置关键字曲线
    太弱"，要用上面这种强 ease-out。

  ★★ 为什么曲线由我们自己算、不交给 QEasingCurve.setCustomType
    实测（tools/_probe_motion_tokens.py）：setCustomType 收一个 Python
    callable，动画**跑起来**的时候会直接段错误 —— 不是报错，是进程当场没了。
    客户那边就是"软件突然消失"。所以动画一律跑线性进度（Linear），
    曲线在回调里由 ease() 施加。效果分毫不差，而且全程在自己的掌控之内。
    以后谁想"优化"回 setCustomType，先看这个注释再动手。

★ 三条硬规则（沿用至今，仍然有效）：
  1. 只做「解释状态变化」的动画。切页淡入解释「你到了另一个地方」，
     悬浮过渡解释「这个能点」，额度条增长解释「额度在变」。
     没有弹跳、没有旋转、没有循环装饰 —— 那是干扰，不是质感。
  2. 交互类动效一律 ≤ 300ms；只有"揭示"（数字滚动、图表生长）可以到 800ms。
  3. 一律可关：环境变量 TRAFFIC_NO_ANIM=1 全部瞬变；系统开了「减弱动画」
     （Windows SPI_GETCLIENTAREAANIMATION）自动降到 reduced —— 那种模式下
     只保留透明度与颜色变化，位移 / 缩放 / 吸附惯性一律瞬变。

★ 一个必须知道的坑：QGraphicsOpacityEffect 会让控件走离屏渲染，
  动画结束后必须立刻 setGraphicsEffect(None) 摘掉，否则整页滚动会变卡。
  fade_in() 已在 finished 里做了这件事，别自己再叠一层 effect。
"""
from __future__ import annotations

import itertools
import os

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractButton, QGraphicsOpacityEffect,
                               QWidget)

# 单调递增的动画令牌：切页很快时，前一个动画的 finished 不能把新页面的
# effect 摘掉（否则新页面会突然「跳」成全不透明，或者干脆停在半透明）。
_TOKEN = itertools.count(1)


# ---------------------------------------------------------------------------
# 曲线令牌
# ---------------------------------------------------------------------------
def _cubic_bezier(x1: float, y1: float, x2: float, y2: float):
    """CSS cubic-bezier(x1,y1,x2,y2) 求值（输入 0~1 进度，输出 0~1 缓动量）。

    先二分反解出参数 t（20 次迭代 ≈ 1e-6 精度），再回代 y。每帧只算一次，
    开销可忽略 —— Qt 那边每帧才调一次 Python 回调。
    """

    def _bez(t: float, a: float, b: float) -> float:
        mt = 1.0 - t
        return 3.0 * a * mt * mt * t + 3.0 * b * mt * t * t + t * t * t

    def _fn(progress: float) -> float:
        p = 0.0 if progress <= 0 else (1.0 if progress >= 1 else float(progress))
        lo, hi = 0.0, 1.0
        for _ in range(20):
            mid = (lo + hi) * 0.5
            if _bez(mid, x1, x2) < p:
                lo = mid
            else:
                hi = mid
        return _bez((lo + hi) * 0.5, y1, y2)

    return _fn


_EASE_FUNCS = {
    "out": _cubic_bezier(0.22, 1.0, 0.36, 1.0),        # 入场 / 出场 / 揭示
    "in_out": _cubic_bezier(0.76, 0.0, 0.24, 1.0),     # 屏内移动
    "soft": _cubic_bezier(0.33, 1.0, 0.68, 1.0),       # 长而安静的揭示
    "linear": lambda p: p,                              # 匀速（进度条）
}


def ease(kind: str = "out", progress: float = 0.0) -> float:
    """取缓动值：0~1 的线性进度 → 0~1 的缓动后进度。"""
    return _EASE_FUNCS.get(kind, _EASE_FUNCS["out"])(progress)


# ---------------------------------------------------------------------------
# 时长令牌
# ---------------------------------------------------------------------------
T_PRESS = 140      # 按下反馈
T_HOVER = 180      # 悬浮 / 颜色过渡
T_UI = 240         # 面板、菜单、切页
T_PANEL = 260      # 展开 / 折叠
T_COUNT = 520      # 数字滚动
T_REVEAL = 800     # 入场揭示上限


# ---------------------------------------------------------------------------
# 动效门控：环境变量（用户手动关）+ 系统「减弱动画」（自动降档）
# ---------------------------------------------------------------------------
def _system_reduce_motion() -> bool:
    """读 Windows 的「减弱动画」设置（SPI_GETCLIENTAREAANIMATION）。

    非 Windows 或读不到一律返回 False —— 宁可按默认给动效，也不要因为一次
    系统调用失败就把整界面变成静态的。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        val = ctypes.c_bool()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(val), 0)
        return bool(ok) and not bool(val.value)
    except Exception:                                          # noqa: BLE001
        return False


def motion_level() -> str:
    """当前动效档位：'off'（全关）/ 'reduced'（只留透明度与颜色）/ 'full'。"""
    if os.environ.get("TRAFFIC_NO_ANIM", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return "off"
    return "reduced" if _system_reduce_motion() else "full"


def animations_enabled() -> bool:
    """动效总开关。默认开；TRAFFIC_NO_ANIM=1/true/yes/on 关闭。"""
    return motion_level() != "off"


def travel_enabled() -> bool:
    """位移 / 缩放 / 吸附惯性这类"移动"允许吗（reduced 模式下关掉）。

    ★ 依据：两条库都写明了 —— 减弱动效不等于零动效，要"更少、更轻"，
      保留能帮助理解的（透明度、颜色），去掉纯粹的移动。
    """
    return motion_level() == "full"


def scaled(duration: int, kind: str = "opacity") -> int:
    """按当前档位重算时长。

    kind='opacity'：reduced 下缩短到 60%（保留但更轻）；
    kind='travel' ：reduced 下直接归零 —— 调用方拿到 0 要瞬变。
    """
    ms = int(duration)
    level = motion_level()
    if level == "off":
        return 0
    if level == "reduced":
        return 0 if kind == "travel" else max(60, int(ms * 0.6))
    return ms


class Tween(QObject):
    """把「一个数值」从 a 平滑推到 b（所有自绘动效的统一驱动）。

    用法：
        self._tw = Tween(self, duration=T_UI, kind="out", on_change=self._apply)
        self._tw.run(self._value, target)

    ★ 为什么动画跑 Linear、曲线由我们施加：见文件头"★★"那一段 ——
      setCustomType 会在动画运行期段错误。这里动画只负责给 0→1 的进度，
      ease() 把它弯成我们要的形状，再插值出真实数值。

    ★ run() 是"从当前值出发"的：连续调用（比如鼠标快速进出）不会跳回起点，
      这正是 Emil 要求的「可打断」—— transition 从当前值续，keyframes 才重启。
    """

    finished = Signal()

    def __init__(self, host: QObject, duration: int = T_UI, kind: str = "out",
                 on_change=None):
        super().__init__(host)
        self._a = 0.0
        self._b = 1.0
        self._kind = kind
        self._duration = int(duration)
        self._on_change = on_change
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        # 线性进度 + 自己施加曲线（原因见文件头）
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self.finished)

    def run(self, a: float, b: float, duration: int | None = None,
            kind: str | None = None, travel: bool = False) -> bool:
        """平滑从 a 到 b。返回是否真的跑了动画（False = 已直接落到 b）。"""
        self._anim.stop()
        self._a = float(a)
        self._b = float(b)
        if kind:
            self._kind = kind
        ms = scaled(duration or self._duration,
                    "travel" if travel else "opacity")
        if ms <= 0 or abs(b - a) < 1e-9:
            self._emit(float(b))
            self.finished.emit()
            return False
        self._anim.setDuration(ms)
        self._anim.start()
        return True

    def jump(self, v: float) -> None:
        """直接落到某个值，不播动画（主题切换、首次赋值用）。"""
        self._anim.stop()
        self._emit(float(v))

    def stop(self) -> None:
        self._anim.stop()

    def _tick(self, p) -> None:
        self._emit(self._a + (self._b - self._a) * ease(self._kind, float(p)))

    def _emit(self, v: float) -> None:
        if self._on_change is not None:
            self._on_change(v)


def fade_in(widget: QWidget | None, duration: int = 200, delay: int = 0) -> None:
    """让控件从透明淡入到不透明（切页用）。

    ★ 为什么只淡入、不上移：浏览器里"上移 16px + 淡入"是最常见的入场，
      但在 Qt 里做位移就得改 geometry / pos —— 那等价于 CSS 里动 width/top，
      是 Emil 明令禁止的（每帧触发 layout）。桌面软件还是稳字当头，
      层次感改用 stagger（见 stagger()）来做。

    delay > 0 时可以错开多个控件，做出「依次浮现」的层次。
    """
    if widget is None:
        return
    ms = scaled(duration, "opacity")
    if ms <= 0:
        return
    # 超大控件做透明度会生成同等尺寸的离屏位图（长表格页可到几千像素高），
    # 换来的观感收益抵不上切页卡顿的风险 —— 直接不淡入。
    if widget.width() * widget.height() > 4_000_000:
        return
    token = next(_TOKEN)
    widget.setProperty("_motion_token", token)

    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)

    anim = QVariantAnimation(widget)
    anim.setDuration(max(60, ms))
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.Linear)

    def _on_value(p):
        # 令牌不匹配 = 期间又切了页，旧动画还在跑但 effect 已被新动画替换并销毁。
        # 此时 eff 是已删除的 C++ 对象，直接 setOpacity 会抛 RuntimeError，
        # 必须跳过（否则快速连切页面时控制台刷一堆 "already deleted" 报错，
        # 且理论上可能干扰新页面的显示）。
        if widget.property("_motion_token") != token:
            return
        try:
            eff.setOpacity(ease("out", float(p)))
        except RuntimeError:                        # eff 已被销毁（极端竞态）
            pass

    anim.valueChanged.connect(_on_value)

    def _done():
        # 令牌不匹配 = 期间又切了页，这个 effect 已经不属于当前页面，别动它
        if widget.property("_motion_token") != token:
            return
        widget.setGraphicsEffect(None)

    anim.finished.connect(_done)
    if delay > 0:
        QTimer.singleShot(int(delay), anim.start)
    else:
        anim.start()


def mix(c1: str, c2: str, t: float) -> str:
    """两个颜色按 t（0~1）线性混合，返回 #RRGGBB。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else float(t))
    a, b = QColor(c1), QColor(c2)
    if not a.isValid():
        return c2
    if not b.isValid():
        return c1
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    ).name()


class SmoothValue(QObject):
    """把宿主控件上的一个 0~1 浮点值平滑推到目标值（悬浮态过渡用）。

    用法：
        self._hover = SmoothValue(self, on_change=self._repaint)
        self._hover.to(1.0)      # 鼠标进入
        self._hover.to(0.0)      # 鼠标离开

    ★ 为什么不直接用 QSS 的 :hover：
      QSS 的悬停态是**瞬变**的，白色卡片瞬间跳到灰白，眼睛看到的是「闪一下」。
      平滑插值才有「浮起来」的感觉。改样式表每帧重解析代价偏高，
      所以这里只维护一个浮点数，由控件在 paintEvent 里自己算颜色。
    """

    def __init__(self, host: QWidget, duration: int = T_HOVER, on_change=None,
                 kind: str = "soft"):
        super().__init__(host)
        self._value = 0.0
        self._duration = int(duration)
        self._on_change = on_change
        self._tween = Tween(self, duration=self._duration, kind=kind,
                            on_change=self._apply)

    @property
    def value(self) -> float:
        return self._value

    def to(self, target: float) -> None:
        target = 0.0 if target < 0 else (1.0 if target > 1 else float(target))
        if abs(target - self._value) < 1e-4:
            self._tween.jump(target)
            return
        self._tween.run(self._value, target, duration=self._duration)

    def reset(self, value: float = 0.0) -> None:
        """主题切换等场景直接落到某个值，不要动画。"""
        self._tween.jump(value)

    def _apply(self, v) -> None:
        self._value = float(v)
        if self._on_change is not None:
            self._on_change(self._value)


class RatioAnimation:
    """给自绘控件用的比例动画（额度条那种「从 0 涨上去」）。

    宿主需要提供 `_ratio` 属性；每次变化会调用宿主 `update()`。
    """

    def __init__(self, host: QWidget, duration: int = 320):
        self._host = host
        self._value = 0.0
        self._duration = int(duration)
        self._tween = Tween(host, duration=self._duration, kind="out",
                            on_change=self._apply)

    @property
    def value(self) -> float:
        return self._value

    def to(self, target: float) -> None:
        target = float(target)
        if abs(target - self._value) < 1e-3:
            return                     # 值没变就别重播，否则每次切回页面都涨一遍
        self._tween.run(self._value, target, duration=self._duration)

    def reset(self, value: float = 0.0) -> None:
        self._tween.jump(value)

    def _apply(self, v) -> None:
        self._value = float(v)
        self._host.update()


def stagger(widgets, duration: int = 200, step: int = 70) -> None:
    """一组控件依次淡入（编排式入场）。

    ★ step 默认 70ms 的依据：tasteskill 给的是 60~90ms —— 比这更慢会变成
      "一个一个往外蹦"，比这更快就退化成整块一起亮，看不出先后。
      最多编排 6 个：再多的队列入场就是 AI 味最重的那种"瀑布流"。
    """
    if not animations_enabled():
        return
    for i, w in enumerate(widgets[:6]):
        fade_in(w, duration=duration, delay=i * step)


def fade_in_all(widgets, duration: int = 200, step: int = 70) -> None:
    """stagger() 的旧名，行为一致（保留给已接入的页面）。"""
    stagger(widgets, duration=duration, step=step)


# 数字 + 可选单位/后缀：'12 次' / '不限量' / '—' / '1,280'
_NUM_RE = None


def count_up(label, new_text: str, duration: int = T_COUNT) -> bool:
    """把一个数字标签从「当前显示值」滚到新值，返回是否真的做了动画。

    ★ 为什么要这个（2026-10-17 视觉改版）
      指标卡的数字是这个软件里客户最在意的东西（还剩多少额度、还有几天）。
      直接 setText 换掉，数字"啪"一下变了，看不出"它是从多少变过来的"；
      滚一下才知道刚才发生了多少变化。这是仪表盘最典型的质感来源。
      （两条库都把它列为"揭示"类：一次、稍慢、允许到 800ms。）

    ★ 三条克制规则（与文件头一致）：
      1. 非数字（"不限量"、"已激活"、"—"）不做动画，直接换文本 ——
         给文字做插值是假的，会变成乱码闪现；
      2. 单位不同（"12 次" → "5 天"）也不做，因为中间态没有意义；
      3. 改标点/分隔符（"1,280" → "1280"）沿用新文本自己的格式，
         只对数字部分插值。

    参数 label：任意有 setText(str) 的控件（QLabel 就行）。
    """
    global _NUM_RE
    if _NUM_RE is None:
        import re
        _NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)(.*)$", re.S)

    old_text = label.text()
    m_old = _NUM_RE.match(old_text.strip())
    m_new = _NUM_RE.match(str(new_text).strip())
    if not m_new:
        label.setText(str(new_text))
        return False
    if not m_old or m_old.group(2) != m_new.group(2) or not animations_enabled():
        label.setText(str(new_text))
        return False

    try:
        start = float(m_old.group(1))
        end = float(m_new.group(1))
    except (TypeError, ValueError):
        label.setText(str(new_text))
        return False
    if abs(end - start) < 1e-9:
        label.setText(str(new_text))
        return False

    suffix = m_new.group(2)
    # 小数位数跟目标值走：目标写 12.5 就保留 1 位，避免滚到一半变成 12.0
    raw_new = m_new.group(1)
    dec = len(raw_new.split(".")[1]) if "." in raw_new else 0
    plain = "." not in raw_new and "," not in raw_new

    def fmt(v: float) -> str:
        s = f"{v:.{dec}f}" if dec else f"{int(round(v))}"
        if plain:
            return s + suffix
        return f"{v:,.{dec}f}" + suffix

    ms = scaled(duration, "opacity")
    if ms <= 0:
        label.setText(str(new_text))
        return False

    anim = QVariantAnimation(label)
    anim.setDuration(max(80, ms))
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.Type.Linear)
    anim.valueChanged.connect(
        lambda p: label.setText(fmt(start + (end - start) * ease("soft", float(p)))))
    # 用属性挂住引用：局部变量被回收时动画会连同定时器一起消失（数字停在半路）
    label.setProperty("_count_anim", anim)
    anim.finished.connect(lambda: label.setText(str(new_text)))
    anim.start()
    return True


# ---------------------------------------------------------------------------
# 按钮悬停过渡（2026-10-27 新增）
# ---------------------------------------------------------------------------
# Qt 的样式表**没有任何 transition / animation 能力** —— `:hover` 是瞬时切换，
# 鼠标一进一出像开关灯。CSS 里一句 `transition: background .18s` 的效果，
# 在 Qt 里只能自己补：把背景从常态色到 hover 色切成 4 档写进 QSS 的属性
# 选择器（见 styles/*.qss 里 [tint="n"] 那几组），这里用 Tween 推进 tint 值。
#
# ★ 为什么是「属性 + unpolish/polish」而不是每帧 setStyleSheet
#   setStyleSheet 要把整串样式重新编译一遍，60fps 下反复编译既慢，还会把
#   页面里自己写的 inline 样式冲掉；polish 只是让 Qt 重新套一次已编译的规则，
#   而且**只在档位真的跳变时才 polish**（4 档 ⇒ 一次悬停最多 4 次），
#   不是每帧都重绘。
#
# ★ 为什么不在启动时遍历整棵控件树给每个按钮预装
#   改用 app 级事件过滤 + 惰性建对象：只有鼠标真的划过的按钮才会生成一个
#   Tween，页面上哪怕有几十个按钮，静止时开销是零。
#
# ★ 遵守本文件头的三条硬规则
#   ① 只做「解释状态变化」的颜色渐变 —— 没有位移、缩放、弹跳；
#   ② T_HOVER=180ms，远低于 300ms 上限；
#   ③ animations_enabled() 为假时根本不安装（TRAFFIC_NO_ANIM=1 完全静态）。
# ---------------------------------------------------------------------------
_TINT_PROP = "tint"
_TINT_STEPS = 4          # 与 QSS 里写的档数必须一致，改一处要改两处


class _BtnTint(QObject):
    """挂在一个按钮上的「色阶推进器」。生命周期跟随按钮（parent=按钮）。"""

    _NAME = "_hoverTint"

    def __init__(self, w):
        super().__init__(w)
        self.setObjectName(_BtnTint._NAME)
        self._w = w
        self._cur = 0.0
        self._lvl = -1
        self._tw = Tween(self, duration=T_HOVER, kind="out",
                         on_change=self._apply)

    @staticmethod
    def get(w):
        """取（或建）这个按钮上已有的推进器。

        ★ 存在 Qt 的子对象树里、不用 Python dict：dict 要么泄漏（按钮删了
          键还在），要么 key 用 id() 会被后来的控件顶掉。findChild 天然
          跟着按钮销毁，也能避免同一按钮重复建多个动画互相打架。
        """
        found = w.findChild(QObject, _BtnTint._NAME)
        return found if isinstance(found, _BtnTint) else _BtnTint(w)

    def to(self, target: float) -> None:
        self._tw.run(self._cur, float(target), duration=T_HOVER, kind="out")

    def _apply(self, v: float) -> None:
        self._cur = float(v)
        w = self._w
        if w is None:
            return
        lvl = int(round(v * _TINT_STEPS))
        if lvl == self._lvl:
            return                      # 同一档不必 polish —— 省掉大部分帧
        self._lvl = lvl
        try:
            st = w.style()
            w.setProperty(_TINT_PROP, max(0, lvl))
            st.unpolish(w)
            st.polish(w)
            # ★ polish 之后必须显式 update：实测 polish 只让样式缓存失效，
            #   重绘要等下一个事件循环 —— 中间那一帧画面还是上一档的颜色，
            #   快速进出时肉眼看就是"回不到原色"。
            w.update()
        except RuntimeError:            # 按钮已被 Qt 销毁（切页 / 关闭）
            self._w = None


class _ButtonMotionFilter(QObject):
    """应用级事件过滤：把 Enter / Leave 转成色阶推进。"""

    def eventFilter(self, obj, ev):                       # noqa: N802
        try:
            t = ev.type()
            if t == QEvent.Type.Enter or t == QEvent.Type.Leave:
                # 只管按钮；禁用的按钮不该有-hover 反馈（点了没反应已经够糟，
                # 再加一点颜色暗示更像是"可以点"）
                if isinstance(obj, QAbstractButton) and obj.isEnabled():
                    _BtnTint.get(obj).to(
                        1.0 if t == QEvent.Type.Enter else 0.0)
        except RuntimeError:
            pass                        # 事件里拿到的对象可能刚好被销毁
        return False


def install_button_motion(app) -> bool:
    """给全程序按钮挂上悬停过渡。返回是否真的启用。

    ★ 家规「一律可关」在这里落地：TRAFFIC_NO_ANIM=1 时**根本不装**过滤器，
      而不是装了再让每段动画都是 0ms —— 后者等于给每个鼠标移动事件都白
      套一层 Python 调用。
    """
    if not animations_enabled():
        return False
    try:
        f = _ButtonMotionFilter(app)
        app.installEventFilter(f)
        return True
    except Exception:                                      # noqa: BLE001
        return False


__all__ = ["SmoothValue", "RatioAnimation", "Tween", "animations_enabled",
           "motion_level", "travel_enabled", "scaled", "ease",
           "T_PRESS", "T_HOVER", "T_UI", "T_PANEL", "T_COUNT", "T_REVEAL",
           "fade_in", "fade_in_all", "stagger", "mix", "count_up",
           "install_button_motion"]
