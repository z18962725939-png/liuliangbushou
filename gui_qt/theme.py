"""流量捕手 PySide6 主题管理：加载明暗 QSS、设置全局字体。"""
import os
import re
import sys
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

_HERE = os.path.dirname(os.path.abspath(__file__))
_STYLES_DIR = os.path.join(_HERE, "styles")

_DARK = "dark.qss"
_LIGHT = "light.qss"

# QSS 里图片资源的占位符：QSS 是纯文本读入后再 setStyleSheet 的，
# 相对 url() 会以「进程工作目录」为基准解析（而不是 QSS 文件所在目录），
# 打包后必然找不到 —— 所以统一在读取时替换成解析好的绝对 file:// URL。
# ---------------------------------------------------------------------------
# ★ 品牌色（2026-10-17 视觉改版）
#
# 改版前整套界面是 Tailwind 的 indigo 家族（#4F46E5 / #6366F1），那是"AI 默认
# 生成的紫色"，与官网 #0B0E14 + #C8F751 完全是两套视觉 —— 客户从官网下载、
# 打开软件，品牌资产等于白做。
#
# 分工（这是整套改版最重要的一条纪律）：
#   · 墨 #0B0E14 = 结构色：标题、主按钮底、导航选中、焦点框、图表主线。
#                  它承担"这是什么 / 我现在在哪"，要求稳、要求对比强。
#   · 柠檬 #C8F751 = 能量色：**只**用在"发生了什么"的地方 —— 进度、激活、
#                  新功能标、深色主题的强调。用多了会累，也就不值钱了。
#
# 浅色主题结构色用墨、能量色用柠檬；深色主题反过来让大面积用柠檬（深底上
# 柠檬是唯一的亮色，识别度最高）—— 两套主题都能保证选中态一眼可见。
# ---------------------------------------------------------------------------
BRAND_INK = "#0B0E14"
BRAND_ACID = "#C8F751"
# ★ v2（2026-11-16）：浅色主题的能量色单独调深一档。
#   白底上 #C8F751 太亮、描边和 hover 都发虚；#C4F13E 更实。
#   深色主题与官网仍用 BRAND_ACID，两者不可互串。
BRAND_ACID_LIGHT = "#C4F13E"

_ACCENT_BY_MODE = {
    "light": BRAND_INK,
    "dark": BRAND_ACID,
}

# 导航项（未选中）的图标/文字色：墨系里偏冷的中性灰，不用纯灰（更耐看）
_NAV_TEXT_BY_MODE = {
    "light": "#566070",
    "dark": "#9AA4B2",
}

# 导航选中项背景色（= 上面的 accent），图标色要跟背景反着来
# 2026-10-17 二次改版：选中态从「实心块」改成「淡填充 + 指示条」，
# 所以图标色不再需要反白/反黑，直接跟结构色走（浅色墨、深色柠檬）。
_NAV_ACTIVE_ICON_BY_MODE = {
    "light": "#0B0E14",
    "dark": BRAND_ACID,
}


_CHECK_ICON_TOKEN = "__CHECK_ICON__"
# 深色主题勾选底是柠檬（高亮黄绿），白勾落在上面看不清 —— 用同一支线的墨色勾
_CHECK_ICON_DARK_TOKEN = "__CHECK_ICON_DARK__"
# 下拉箭头：一旦 QSS 碰了 ::drop-down，Qt 就不再画系统那个小三角，
#   必须自己喂一张图，否则下拉框看着跟只读输入框一样（客户看不出能点）。
_ARROW_DOWN_TOKEN = "__ARROW_DOWN__"
# 界面字族：QSS 里写占位符，运行时换成「内嵌字体 or 回退雅黑」的实际家族名
_APP_FONT_TOKEN = "__APP_FONT__"
_ARROW_DOWN_DARK_TOKEN = "__ARROW_DOWN_DARK__"


def _candidate_style_dirs():
    """QSS 目录候选路径（按优先级从高到低）。

    打包（PyInstaller）后 __file__ 不再可靠，数据文件落在 sys._MEIPASS 下，
    故优先从 _MEIPASS 解析；开发态回退到 __file__ 相对路径。
    """
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(os.path.join(meipass, "gui_qt", "styles"))
        dirs.append(os.path.join(meipass, "styles"))
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        dirs.append(os.path.join(exe_dir, "_internal", "gui_qt", "styles"))
        dirs.append(os.path.join(exe_dir, "gui_qt", "styles"))
    dirs.append(os.path.join(_HERE, "styles"))
    return dirs


def _resolve_stylesheet(filename):
    """返回第一个真实存在的 QSS 路径；全部不存在则返回 None。"""
    for d in _candidate_style_dirs():
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p
    return None


# QSS 全部缺失时的最小兜底样式：宁可界面朴素，也不要启动崩溃
_FALLBACK_QSS = """
QWidget { font-family: "__APP_FONT__"; font-size: 10pt; }
QPushButton#NavItem { padding: 8px 12px; border: none; border-radius: 8px; }
"""

def _resolve_asset_url(filename: str) -> str:
    """把 styles/ 下的图片解析成 QSS 可用的绝对路径（正斜杠；找不到返回空串）。

    两个实测结论（见 tools/_probe_checkbox.py 的对照实验）：
      1. 必须用绝对路径 —— QSS 里的相对路径以「进程工作目录」为基准，
         打包后是用户双击 exe 的位置，必然找不到。
      2. 必须写成**纯文件路径**，不能写 file:/// URL —— QSS 内部按文件名
         加载（QPixmap 不认 URL），file:/// 形式一律加载失败。
    路径里的空格与中文不影响加载（Qt 直接当文件名用）。
    """
    for d in _candidate_style_dirs():
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p.replace("\\", "/")
    return ""


APP_FONT_FAMILY = "TrafficCatcher Sans"
_FALLBACK_FONT_FAMILY = "Microsoft YaHei UI"
# 由 tools/build_app_font.py 生成；加载不到就回退系统雅黑（不抛、不白屏）
# 2026-10-17 四次改版：源字体换成 Zen Maru Gothic SC（圆体），三档
# Regular/Medium/Bold 对应 400/500/700。标题走 700（圆体的 Bold 本来就轻），
# 正文 400，深色下次级强调用 500。
_APP_FONT_FILES = ("fonts/TrafficSans-Regular.ttf",
                   "fonts/TrafficSans-Medium.ttf",
                   "fonts/TrafficSans-Semibold.ttf",
                   "fonts/TrafficSans-Bold.ttf")
_app_font_ready = False

# ---------------------------------------------------------------------------
# ★ 负字距（Apple 的「紧排」）
#
# 苹果每个 17px 以上的标题都带 -0.12 ~ -0.374px 的收紧，这是「看起来像大厂
# 软件」最省力的一招：同样的字体、同样的大小，字距一松就是「网页表单」，
# 一紧就是「产品」。
#
# ★ 为什么不用 QSS 写：Qt 的样式表**没有 letter-spacing 属性**（写了不报错、
#   也不生效）。只能在设完 QSS 之后再用 QFont.setLetterSpacing 补一遍 ——
#   注意顺序，QSS 会重置字体，所以必须放在 setStyleSheet 之后。
_TRACKING = {
    "H1": -0.5,          # 页面大标题
    "PageTitle": -0.5,
    "H2": -0.25,         # 小节标题
    "StatValue": -0.6,   # 大数字
    "MetricValue": -0.6,
    "NavBrand": 0.2,
    # ★ 2026-10-18：删掉了原来的 "SectionLabel": +0.6。
    #   「小号 + 拉开字距」就是 tasteskill 点名的 tiny tracked caps ——
    #   生成式页面最爱用的装饰，高分站 16%、低分站 57%。分区标题回到
    #   正常小号正常字距，靠字重与色阶跟正文区分（见 QSS 里的注释）。
}

# 需要「表格数字」的控件（objectName）：数字滚动 / 实时刷新时，每一位宽度
# 必须固定，否则数字会在滚动中左右抖 —— 这是 number ticker 的硬性前提
# （Emil 的动效词汇表：Tabular numbers — essential for tickers, counters）。
_TABULAR_NAMES = ("StatValue", "MetricValue", "LiveChip", "StatDelta",
                  "MetricSub")

# 深色下要加一档字重的小字号：浅字压深底会显得比实际更细（impeccable
# typeset 的深色补偿三条：行高、字距、字重各补一点），小字号最明显。
_DARK_WEIGHT_BUMP = ("Caption", "Meta", "SectionLabel")

_TNUM_TAG = "tnum"


def apply_tracking(root, mode: str = "light") -> None:
    """给标题补字距、给数字开等宽、给深色下的小字加字重。

    ★ 三件事都只能走 QFont：QSS 没有 letter-spacing，也没有 font-feature，
      更没有"深色下变重"这回事。所以 QSS 设完之后，由这里统一收尾。
      调用顺序必须在 setStyleSheet 之后（QSS 会重置字体）。
    """
    try:
        from PySide6.QtWidgets import QWidget
    except Exception:                                        # noqa: BLE001
        return
    if not isinstance(root, QWidget):
        return
    dark = (mode == "dark")
    targets = [root] + root.findChildren(QWidget)
    for w in targets:
        name = w.objectName()
        px = _TRACKING.get(name)
        need_tabular = name in _TABULAR_NAMES
        need_bump = dark and name in _DARK_WEIGHT_BUMP
        if px is None and not need_tabular and not need_bump:
            continue
        f = w.font()
        if px is not None:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, px)
        if need_bump and f.weight() < QFont.Weight.Medium:
            f.setWeight(QFont.Weight.Medium)
        if need_tabular:
            try:
                f.setFeature(_TNUM_TAG, 1)
            except Exception:                                # noqa: BLE001
                pass          # 字体没有 tnum 表就静静跳过，不影响其它排版
        w.setFont(f)


def install_app_fonts() -> bool:
    """把内嵌字体注册进 QFontDatabase。幂等，失败返回 False。

    ★ 为什么要这一步（2026-10-17 UI 诊断 P3）
      官网用的是思源（Noto Sans SC），软件却跑雅黑 —— 客户点开软件的一瞬间
      会觉得是「另一个产品」。现在把同一套字体的 GB2312 子集（约 5 MB，
      由 tools/build_app_font.py 生成）随包发出并在启动时注册，
      桌面端和官网才算同一种气质。

    ★ 注意 addApplicationFont 必须在 QGuiApplication 构造之后调用，
      返回负数表示这个 ttf 没被认出来（常见的坑是成品仍是 woff2）。
    """
    global _app_font_ready
    if _app_font_ready:
        return True
    app = QApplication.instance()
    if app is None:
        return False
    try:
        from PySide6.QtGui import QFontDatabase
        db = QFontDatabase()
        loaded = 0
        for rel in _APP_FONT_FILES:
            # ★ 必须走 _resolve_asset_url（底层是 _candidate_style_dirs），
            #   不能直接拼 _STYLES_DIR：_STYLES_DIR 是 __file__ 相对路径，
            #   只在「数据恰好落在同名目录」时成立（2026-10-17 实测过是成立的，
            #   但它依赖打包布局，改一下就静默退回雅黑、不报错、不看不出来）。
            #   _candidate_style_dirs 依次试 _MEIPASS / exe 同目录 / __file__，
            #   与 QSS 图标走同一条解析链，改动互不牵连。
            path = _resolve_asset_url(rel)
            if path and os.path.isfile(path) and db.addApplicationFont(path) >= 0:
                loaded += 1
        _app_font_ready = loaded > 0
    except Exception as exc:                                  # noqa: BLE001
        print(f"[流量捕手] 内嵌字体加载失败，回退系统字体：{exc}")
        _app_font_ready = False
    return _app_font_ready


def app_font_family() -> str:
    """当前该用的界面字族名（没装成功就是雅黑）。"""
    if install_app_fonts():
        return APP_FONT_FAMILY
    return _FALLBACK_FONT_FAMILY


def _inject_assets(text: str) -> str:
    """把 QSS 中的占位符替换为真实值。资源缺失时降级为空串（不抛异常）。

    长名字先替换（__X_DARK__ 在 __X__ 之前）—— 看起来多余，但两个占位符
    是「短的是长的前缀」的关系，顺序写反了将来改名字时很容易踩到。
    """
    if _CHECK_ICON_DARK_TOKEN in text:
        text = text.replace(_CHECK_ICON_DARK_TOKEN,
                            _resolve_asset_url("check_dark.png"))
    if _CHECK_ICON_TOKEN in text:
        text = text.replace(_CHECK_ICON_TOKEN, _resolve_asset_url("check.png"))
    if _ARROW_DOWN_DARK_TOKEN in text:
        text = text.replace(_ARROW_DOWN_DARK_TOKEN,
                            _resolve_asset_url("arrow_down_dark.png"))
    if _ARROW_DOWN_TOKEN in text:
        text = text.replace(_ARROW_DOWN_TOKEN, _resolve_asset_url("arrow_down.png"))
    if _APP_FONT_TOKEN in text:
        text = text.replace(_APP_FONT_TOKEN, app_font_family())
    return text


def load_stylesheet(mode: str) -> str:
    """读取对应模式的 QSS 文本。mode 非 'dark' 时默认浅色。

    找不到 QSS 时返回最小兜底样式（而不是抛异常）——打包后若资源缺失，
    表现为界面朴素但功能可用，远优于"双击没反应"的静默崩溃。
    """
    fn = _DARK if mode == "dark" else _LIGHT
    path = _resolve_stylesheet(fn)
    if path is None:
        return _FALLBACK_QSS
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _inject_assets(fh.read())
    except OSError:
        return _FALLBACK_QSS


def apply_theme(app: QApplication, mode: str = "light") -> None:
    """给 QApplication 套用主题样式与字体。"""
    app.setStyleSheet(load_stylesheet(mode))
    # ★ 字体走占位符解析后的家族名：装了内嵌字体就用思源，没装就雅黑。
    #   不能写死 "Microsoft YaHei UI" —— 那样 P3 等于没做；
    #   也不能写死新字体名 —— 打包万一漏了资源就是全局豆腐块。
    family = app_font_family()
    font = QFont(family, 10)
    font.setWeight(QFont.Weight.Normal)
    app.setFont(font)
    app.setProperty("theme_mode", mode)
    app.setProperty("app_font", family)
    app.setProperty("accent", _ACCENT_BY_MODE.get(mode, BRAND_INK))


def accent_color(mode: str = "light") -> str:
    return _ACCENT_BY_MODE.get(mode, BRAND_INK)


def nav_text_color(mode: str = "light") -> str:
    """导航项未选中时的图标/文字色。"""
    return _NAV_TEXT_BY_MODE.get(mode, "#5B6472")


def nav_active_icon_color(mode: str = "light") -> str:
    """导航项选中时的图标色 —— 必须与选中态背景反着来。

    改版后两种主题的选中底色不同（浅色墨底 / 深色柠檬底），所以图标色也
    不再是常量：墨底配白图标，柠檬底配墨图标。
    """
    return _NAV_ACTIVE_ICON_BY_MODE.get(mode, "#FFFFFF")


def ink_or_acid(mode: str = "light") -> str:
    """取当前主题的「结构色」：浅色用墨、深色用柠檬。

    自绘组件（指标卡、图表…）不能用 QSS 伪状态，需要显式拿到这个色值。
    """
    return BRAND_ACID if mode == "dark" else BRAND_INK


# ---------------------------------------------------------------------------
# 深色适配：内联样式的色值置换
#
# ★ 为什么需要这一层（2026-10-17 客户反馈「色系板块有点问题」时发现）：
#   14 个页面里有 130 多处 `setStyleSheet("color:#6B7280;...")`，都是浅色改版
#   时写死的。内联样式**优先级高于 QSS**，切深色时整片文字仍是深灰/近黑 ——
#   页面标题「工作台」直接变成黑底黑字，卡片标题整行消失。
#
#   逐个改 130 处调用点不现实（容易漏，且以后新代码还会犯）。改成切主题时
#   统一置换：首次见到某控件时把它当前的样式记为「浅色基线」，深色时按语义
#   表换成对应深色值，切回浅色则原样还原 —— 基线是可逆的，来回切不会漂移。
# ---------------------------------------------------------------------------

_SS_BASE = "_theme_base_ss"
_SS_APPLIED = "_theme_applied_ss"

# 文字色：浅色值 → 深色值（成对可逆，别只放单向，否则切回浅色会残留深色）
_TEXT_SWAP = {
    "#111827": "#F9FAFB",     # 主标题（近黑 → 近白）
    "#1f2937": "#E5E7EB",     # 正文
    "#374151": "#D1D5DB",     # 正文次强
    "#4b5563": "#9CA3AF",
    "#6b7280": "#9CA3AF",     # 次要说明（全软件出现最多的一档）
    "#9ca3af": "#6B7280",     # 更次要：深色下"更淡"= 更接近背景
    "#9d174d": "#F472B6",
    "#1565c0": "#60A5FA",
    "#2e7d32": "#34D399",     # 成功绿：深底上必须提亮，否则糊成一片
    "#dc2626": "#F87171",     # 错误红
    "#c62828": "#F87171",
    "#d32f2f": "#F87171",
    "#b45309": "#FBBF24",     # 警告黄
    "#ef6c00": "#FB923C",     # 试用橙（深底上提亮，避免发暗）
    # —— 品牌改版后新增的字面色（2026-10-17）——
    "#0b0e14": "#F2F4F8",     # 墨：做标题/正文时深底要反白
    "#5b6472": "#9AA4B2",     # 中性次要
    "#8b93a1": "#6B7480",     # 中性弱化
    "#c8f751": "#C8F751",     # 柠檬：本身就是亮色，两套主题通用
    # —— v2 浅色色板新增的字面色（2026-11-16）——
    # 内联样式由 stat.py 的 palette() 生成（自绘组件不读 QSS），切深色时
    # 也必须能反白。漏掉任一条 = 深色下该处仍是浅色深字，整片看不见。
    "#12161f": "#F2F4F8",     # 主文（近黑 → 近白）
    "#566070": "#9AA4B2",     # 次级说明
    "#98a1af": "#6B7480",     # 纯装饰弱化档
    "#c4f13e": "#C8F751",     # 浅色柠檬 → 深色亮柠檬
    "#b7e731": "#D9FA7F",     # 柠檬 hover
    "#afdf2c": "#B4E243",     # 柠檬 pressed
    "#18a05e": "#5EE39A",     # 成功绿
    "#c98a12": "#F5C044",     # 警告黄
}

# 背景色：浅色值 → 深色值（只有内联写死的几处）
_BG_SWAP = {
    "#ffffff": "#141821",
    "#f9fafb": "#10131A",
    "#f3f4f6": "#1A1F28",
    "#fef3c7": "#3A2E12",
    "#eef2ff": "#242A4A",
    # —— 品牌改版后新增 ——
    # 墨做「按钮/标签底」时，深色主题下要换成柠檬（深底上唯一的亮色）
    "#0b0e14": "#C8F751",
    "#f1f3f5": "#10131A",
    "#f2fcd9": "#1B2410",     # 柠檬提示条浅底 → 深柠檬底
    "#f6f7f9": "#0B0E14",     # 页面底 → 官网 hero 同色
    # —— v2 浅色色板新增 ——
    "#f5f6f8": "#0B0E14",     # 新页面底
    "#c4f13e": "#C8F751",     # 浅色柠檬底（按钮/徽标）→ 深色亮柠檬底
    "#b7e731": "#D9FA7F",
    "#afdf2c": "#B4E243",
}

_COLOR_TOKEN = re.compile(r"(color|background)\s*:\s*(#[0-9A-Fa-f]{6})")


def _swap_dark(ss: str) -> str:
    """把一段内联样式里的浅色字面量换成深色对应值（单次遍历，不做链式替换）。"""

    def repl(m):
        prop, val = m.group(1), m.group(2)
        table = _BG_SWAP if prop.startswith("background") else _TEXT_SWAP
        hit = table.get(val.lower())
        return f"{prop}:{hit}" if hit else m.group(0)

    return _COLOR_TOKEN.sub(repl, ss)


def restyle_tree(root: QWidget | None, mode: str = "light") -> int:
    """按主题重算整棵控件树里的内联样式，返回实际改动的控件数。

    调用时机：**先**调它，**再**调各页面的 apply_theme() ——
    页面自己用 palette 生成的样式优先级更高，会覆盖这里的粗粒度映射。
    """
    if root is None:
        return 0
    changed = 0
    widgets = [root]
    widgets.extend(root.findChildren(QWidget))
    for w in widgets:
        try:
            cur = w.styleSheet()
        except Exception:                                    # noqa: BLE001
            continue
        base = w.property(_SS_BASE)
        applied = w.property(_SS_APPLIED)
        # 代码在本轮主题下主动改过样式（状态文字变色等）→ 以它为新基线
        if base is None or (cur and cur != applied):
            base = cur
            w.setProperty(_SS_BASE, base)
        if not base:
            continue
        new = _swap_dark(base) if mode == "dark" else base
        if new != cur:
            w.setStyleSheet(new)
            changed += 1
        w.setProperty(_SS_APPLIED, new)
    return changed
