# -*- coding: utf-8 -*-
"""界面观感自检：叶控件底色 / 深色可读性 / 交互动效是否还在。

★ 为什么要有这个脚本：
  这三类问题的共同点是「代码不报错，界面悄悄变丑」——
  - QLabel 继承了 `QWidget{background}` 的灰底：编译通过、功能正常，
    只是每张白卡下面多一条灰杠，客户看到的是"阴影画得不一致"；
  - 深色主题下内联写死的 #111827 没跟着换：标题直接黑底黑字消失；
  - 动效被"顺手改回去"：不会报错，只是又变成硬切。

  所以这里不测逻辑，只测**像素**和**关键调用是否还在**。

跑法：C:/Python310/python.exe tools/_validate_ui_polish.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 必须在导入 Qt 之前决定平台：需要真实字体才能判断文字亮不亮，
# 离屏环境没有中文字体（量出来全是方块，断言会失真）。
os.environ.pop("QT_QPA_PLATFORM", None)

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


# ---------------------------------------------------------------- 1. 样式层
print("\n== 1. 叶控件底色（灰杠根因） ==")
for qss in ("gui_qt/styles/light.qss", "gui_qt/styles/dark.qss"):
    src = read(qss)
    has_rule = re.search(
        r"QLabel\s*,\s*QCheckBox\s*,\s*QRadioButton\s*\{[^}]*background\s*:\s*transparent",
        src, re.S)
    check(f"{qss.split('/')[-1]} 声明 QLabel 透明底", bool(has_rule))
    check(f"{qss.split('/')[-1]} 没有给 QLabel 单独设背景",
          "QLabel {" not in src.replace("QLabel, QCheckBox", ""))

for qss in ("gui_qt/styles/light.qss", "gui_qt/styles/dark.qss"):
    src = read(qss)
    _m = re.search(r"QFrame#Card\s*\{[^}]*border-radius:\s*(\d+)px", src, re.S)
    _card = int(_m.group(1)) if _m else 0
    _p = re.search(r"QFrame#Panel\s*\{[^}]*border-radius:\s*(\d+)px", src, re.S)
    _panel = int(_p.group(1)) if _p else 0
    # ★ 2026-10-27：这里原来写死 16px。客户第三次提"边角能不能更圆润"，
    #   一并 Card 16→18 / Panel 18→20，写死的值就变成了一枚"不许进步"的锁。
    #   真正要拦的是三件事：
    #     ① 不低于 16（14px 落在 60px 行上还是方盒观感 —— 客户第二轮的反馈）
    #     ② 内层 Card 不能比外层 Panel 更圆（否则一块大面包里塞了个小圆饼）
    #     ③ 两套主题同一个数（切主题不能有"卡变方了"的错觉）
    check(f"{qss.split('/')[-1]} 卡片圆角不小于 16px（当前 {_card}px）",
          _card >= 16)
    check(f"{qss.split('/')[-1]} 外层面板比卡片更圆（{_panel} ≥ {_card}）",
          _panel >= _card)
    globals().setdefault("_CARD_R", {})[qss] = _card

# 两套主题同一个数（上面循环里存下来的）
check("明暗两套卡片圆角一致",
      len(set(globals().get("_CARD_R", {}).values())) == 1)


# ---------------------------------------------------------------- 2. 动效层
print("\n== 2. 动效模块 ==")
motion_src = read("gui_qt/widgets/motion.py")
for name in ("def fade_in", "class SmoothValue", "class RatioAnimation",
             "def animations_enabled", "def mix"):
    check(f"motion.py 提供 {name}", name in motion_src)

check("动效可一键关闭（TRAFFIC_NO_ANIM）", "TRAFFIC_NO_ANIM" in motion_src)
check("淡入结束会摘掉 opacity effect（否则滚动变卡）",
      "setGraphicsEffect(None)" in motion_src)
check("淡入有令牌防竞态",
      "_motion_token" in motion_src and "token" in motion_src)

mw = read("gui_qt/main_window.py")
check("切页调用 fade_in", "fade_in(" in mw and "def _switch" in mw)
check("切主题调用 restyle_tree（深色文字置换）", "restyle_tree(" in mw)

stat_src = read("gui_qt/widgets/stat.py")
check("ActionCard 自绘背景（不再靠 QSS 瞬变）",
      "def paintEvent" in stat_src and "_hover_s" in stat_src)
check("ActionCard 保留 clicked 信号", "clicked = Signal()" in stat_src
      and "self.clicked.emit()" in stat_src)
check("UsageBar 用 RatioAnimation 增长",
      "RatioAnimation(" in stat_src and "showEvent" in stat_src)
check("StatCard 数值变化才淡入", "fade_in(self.value_lab" in stat_src)

theme_src = read("gui_qt/theme.py")
check("theme.py 提供 restyle_tree", "def restyle_tree" in theme_src)
check("深色置换表覆盖主标题色 #111827", '"#111827"' in theme_src)
check("深色置换表覆盖错误红 #dc2626", '"#dc2626"' in theme_src)


# ---------------------------------------------------------------- 3. 渲染层
print("\n== 3. 真实渲染（像素级） ==")
from PySide6.QtCore import QEventLoop, QTimer                   # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel                 # noqa: E402

from gui_qt.main_window import MainWindow                        # noqa: E402
from gui_qt.pages._base import AppContext                        # noqa: E402
from gui_qt.theme import apply_theme                             # noqa: E402
from gui_qt.widgets.stat import ActionCard, BigNumber, StatCard  # noqa: E402

app = QApplication.instance() or QApplication([])


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def count_color(img, rect, hex_color: str, tol: int = 3) -> int:
    """统计 rect 内接近某色的像素数。"""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    x0, y0, x1, y1 = rect
    n = 0
    for y in range(y0, y1, 1):
        for x in range(x0, x1, 1):
            c = img.pixelColor(x, y)
            if (abs(c.red() - r) <= tol and abs(c.green() - g) <= tol
                    and abs(c.blue() - b) <= tol):
                n += 1
    return n


apply_theme(app, "light")
win = MainWindow(AppContext())
win.resize(1180, 940)
win.show()
wait(120)
win.switch_to("工作台")
wait(260)
page = win.page("工作台")
img = win.grab().toImage()

# 3.0 切页淡入结束后必须摘掉 opacity effect，否则整页长期走离屏渲染（滚动变卡）
content = win._scrolls.get("工作台")
content = content.widget() if content is not None else None
check("切页淡入结束后已摘掉 effect", content is not None and content.graphicsEffect() is None)

# 3.1 三张指标卡内部不应有 #F7F8FA 灰底
#
# ★ 2026-10-27：工作台的三格从 StatCard 换成了 BigNumber（大号数字）。
#   这条断言原来只找 StatCard，于是"一张都没找到"→ area=0 → 比例按 100%
#   判失败。**空集当失败是这条断言的正确行为**（它本来就在防"卡片被塞了
#   灰底"），所以修法不是放宽阈值，而是把两类指标卡都算进来、并且要求
#   至少存在一个 —— 一张都找不到时它仍然应该红。
gray_hits = 0
area = 0
cards = list(page.findChildren(StatCard)) + list(page.findChildren(BigNumber))
for card in cards:
    tl = card.mapTo(win, card.rect().topLeft())
    x0, y0 = tl.x() + 8, tl.y() + 8
    x1, y1 = tl.x() + card.width() - 8, tl.y() + card.height() - 8
    if x1 <= x0 or y1 <= y0:
        continue
    gray_hits += count_color(img, (x0, y0, x1, y1), "#F7F8FA")
    area += (x1 - x0) * (y1 - y0)
ratio = (gray_hits / area * 100) if area else 100
check(f"指标卡内部无灰底（{len(cards)} 张，灰像素占比 {ratio:.1f}%）",
      bool(cards) and ratio < 3.0,
      f"修复前约 20%，现在应接近 0")

# 3.2 功能卡内部同理
gray2 = 0
area2 = 0
for card in page.findChildren(ActionCard)[:6]:
    tl = card.mapTo(win, card.rect().topLeft())
    x0, y0 = tl.x() + 8, tl.y() + 8
    x1, y1 = tl.x() + card.width() - 8, tl.y() + card.height() - 8
    if x1 <= x0 or y1 <= y0:
        continue
    gray2 += count_color(img, (x0, y0, x1, y1), "#F7F8FA")
    area2 += (x1 - x0) * (y1 - y0)
ratio2 = (gray2 / area2 * 100) if area2 else 100
check(f"功能卡内部无灰底（灰像素占比 {ratio2:.1f}%）", ratio2 < 3.0)

# 3.3 深色主题：页面标题必须能被看见
win._toggle_theme()
wait(420)
img_dark = win.grab().toImage()
# ★ 用 findChildren 而不是 children()：2026-10-17 二次改版后，首页为了做
#   「内容最大宽度居中」在外面包了一层容器，标题不再是 page 的直接子控件 ——
#   按直接子对象找会一个都找不到，自检就变成"没找到标题控件"的假失败。
titles = page.findChildren(QLabel)
tl = None
for lab in titles:
    if lab.text() == "工作台":
        tl = lab.mapTo(win, lab.rect().topLeft())
        rect_w, rect_h = lab.width(), lab.height()
        break
if tl is not None:
    bright = 0
    for y in range(tl.y(), tl.y() + rect_h):
        for x in range(tl.x(), tl.x() + min(rect_w, 200)):
            if img_dark.pixelColor(x, y).lightness() > 150:
                bright += 1
    check(f"深色下页面标题可读（亮像素 {bright}）", bright > 60,
          "修复前标题是 #111827，在 #0F1115 上几乎不可见")
else:
    check("深色下页面标题可读", False, "没找到标题控件")

# 3.4 悬浮是「渐变」而不是瞬变：中间帧的插值必须落在 0~1 之间
card0 = page.findChildren(ActionCard)[0]
edge = card0.mapTo(win, card0.rect().topLeft())
bx, by = edge.x() + card0.width() // 2, edge.y() + 1
before = img_dark.pixelColor(bx, by)
card0._hover_s.to(1.0)
wait(60)
mid = card0._hover_s.value
img_hover = win.grab().toImage()
after = img_hover.pixelColor(bx, by)
check(f"悬浮是渐变不是瞬变（60ms 时 t={mid:.2f}）", 0.0 < mid < 1.0)
check(f"悬浮后边框颜色确实变了（{before.name()} → {after.name()}）",
      before.name() != after.name())

win._toggle_theme()
wait(200)
win.close()


# ---------------------------------------------------------------- 4. 动效开关
print("\n== 4. 关闭开关真的生效 ==")
from gui_qt.widgets.motion import SmoothValue, animations_enabled, fade_in  # noqa: E402
from PySide6.QtWidgets import QWidget                                     # noqa: E402

old = os.environ.get("TRAFFIC_NO_ANIM")
os.environ["TRAFFIC_NO_ANIM"] = "1"
check("TRAFFIC_NO_ANIM=1 时 animations_enabled() 为 False", animations_enabled() is False)

w = QWidget()
fade_in(w, duration=100)
check("关闭后 fade_in 不挂 effect", w.graphicsEffect() is None)

sv = SmoothValue(w, on_change=None)
sv.to(1.0)
check("关闭后 SmoothValue 直接落到目标值", abs(sv.value - 1.0) < 1e-6)
w.close()

if old is None:
    os.environ.pop("TRAFFIC_NO_ANIM", None)
else:
    os.environ["TRAFFIC_NO_ANIM"] = old
check("恢复开关后动画可用", animations_enabled() is True)


# ---------------------------------------------------------------- 5. 交互组件
#
# 2026-10-17 四次改版新增：客户要「组件交互」，落了四个自绘组件。
# 这组检查守两件事：① 组件真的被页面用上了（不是写了没用）；② 组件不许
# 绕过动效总开关、不许把颜色写死（深色下会变白块）。
print("\n== 5. 交互组件（展开 / 刻度 / 分步 / 数据联动） ==")
kit_src = read("gui_qt/widgets/kit.py")
for cls in ("class DisclosurePanel", "class NodeTimeline", "class RulerSlider",
            "class ChoiceFlow", "class MetricBoard", "class StepDots",
            "class ChoiceChips"):
    check(f"kit.py 提供 {cls.split()[1]}", cls in kit_src)
check("kit 的动画统一受 TRAFFIC_NO_ANIM 约束",
      "animations_enabled" in kit_src and "from .motion import" in kit_src)

home_src = read("gui_qt/pages/home_page.py")
check("工作台状态区改成可展开面板", "DisclosurePanel(" in home_src
      and "NodeTimeline(" in home_src)
check("展开时才播放节点动画（不是常驻闪）",
      "def _play_check" in home_src and "on_expand=" in home_src)

script_src = read("gui_qt/pages/script_page.py")
check("文案仿写的目标时长是刻度尺", "RulerSlider(" in script_src)
check("时长不再用下拉框（dur_box 已移除）", "dur_box" not in script_src)
check("拖动刻度尺时字数跟着变", "dur_ruler.valueChanged" in script_src
      and "dur_live" in script_src)

settings_src = read("gui_qt/pages/settings_page.py")
check("设置页有分步选择（选够数量才放行）", "ChoiceFlow(" in settings_src
      and "need=3" in settings_src)

trend_src = read("gui_qt/pages/trend_page.py")
check("数据趋势用联动数据板（单位换算 + 眼睛）", "MetricBoard(" in trend_src
      and "unit_toggle=True" in trend_src and "eye=True" in trend_src)

mw_src = read("gui_qt/main_window.py")
check("切主题时统一给自绘交互组件重上色",
      "from gui_qt.widgets import kit" in mw_src and "kit.RulerSlider" in mw_src)

for qss in ("gui_qt/styles/light.qss", "gui_qt/styles/dark.qss"):
    src = read(qss)
    check(f"{qss.split('/')[-1]} 定义了跟手数值胶囊（不靠内联写死浅色）",
          "QLabel#LiveChip" in src)

font_dir = Path("gui_qt/styles/fonts")
have = sorted(p.name for p in font_dir.glob("*.ttf"))
# ★ 2026-10-27：三档 → 四档。这条原来写死"等于这三个文件名"，
#   补上 Semibold 之后它就变成一枚"不许进步"的锁（圆角那次踩过同款）。
#   真正要保证的是：四档都在，且**必须有 600 这一档** ——
#   界面里满屏 font-weight:600，缺了它 Qt 会把 600 顶到 700，
#   标题于是又重又糊（2026-10-27 换字体时实测出来的）。
_need_fonts = ["TrafficSans-Bold.ttf", "TrafficSans-Medium.ttf",
               "TrafficSans-Regular.ttf", "TrafficSans-Semibold.ttf"]
check("内嵌字体四档齐全（Regular/Medium/Semibold/Bold）",
      all(n in have for n in _need_fonts), "实际：" + "、".join(have))

# ---- 行为级：刻度吸附 + 分步门禁 ----
from gui_qt.widgets.kit import ChoiceFlow, RulerSlider   # noqa: E402

ruler = RulerSlider(15, 90, 5, "dark", unit="秒")
ruler.set_value(53)
check(f"刻度尺吸附到最近格（53 → {ruler.value()}）", ruler.value() == 55)
ruler.set_value(200)
check(f"刻度尺夹在上限内（200 → {ruler.value()}）", ruler.value() == 90)
ruler.set_value(-5)
check(f"刻度尺夹在下限内（-5 → {ruler.value()}）", ruler.value() == 15)

flow = ChoiceFlow("选三个", ["甲", "乙", "丙", "丁"], "投入", ["少", "多"], need=3)
check("没选够时「下一步」是灰的", flow.next_btn.isEnabled() is False)
for t in ("甲", "乙", "丙"):
    flow.chips._toggle(t)
check("选满三个后「下一步」才亮", flow.next_btn.isEnabled() is True)
flow.set_step(1)
check("进第二步后进度点跟着走", flow.dots.step() == 1)
check("第二步没选投入时不能保存", flow.done_btn.isEnabled() is False)
flow._on_level("少")
check("选了投入才能保存", flow.done_btn.isEnabled() is True)


print(f"\n{'='*60}")
print(f"界面观感自检：{PASS} 项通过，{FAIL} 项失败")
sys.exit(0 if FAIL == 0 else 1)
