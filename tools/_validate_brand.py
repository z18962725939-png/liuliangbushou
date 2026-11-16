# -*- coding: utf-8 -*-
"""界面品牌一致性自检 —— 拦「改回旧配色 / 漏做一处」这类静默退化。

为什么单独写一个
──────────────────────────────────────────────────────────────────
2026-10-17 视觉改版把整套界面从 Tailwind indigo 家族（#4F46E5 / #6366F1）
换到官网那套「墨 + 柠檬」（#0B0E14 / #C8F751）。

这类改动的失败方式**不会报错**：
  · QSS 里某条规则漏改，那一处就永远是紫的 —— 页面照样打开、控制台干净；
  · 自绘组件（指标卡 / 进度条 / 图表）不读 QSS，改 QSS 不会影响它 ——
    于是出现「一半墨一半紫」的撕裂，只有截图才看得见；
  · 深色主题的勾选底变成柠檬后，白勾落在上面看不见 —— 除非专门查；
  · 空 badge 因为新加了 padding 变成一个小色块 —— 截图才发现。

五组检查：
  A 旧色清零 —— 两套 QSS 与自绘调色板里都不许再出现靛蓝家族
  B 品牌落地 —— 墨与柠檬必须在两套主题里各自承担正确角色
  C 弹层补齐 —— QMenu / 下拉列表必须有定制（改版前是全软件唯一系统原生接缝）
  D 层级与字体 —— 六档字号齐全，统计数字必须等宽
  E 一致性 —— 状态行组件覆盖全部页面，深色勾图资源在位
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
GUI = ROOT / "gui_qt"
LIGHT = GUI / "styles" / "light.qss"
DARK = GUI / "styles" / "dark.qss"

# 改版前的 Tailwind indigo 家族。这些值一旦在样式或自绘调色板里出现，
# 就说明有人把某处改回去了（或者新写的代码照旧抄了旧色值）。
LEGACY_INDIGO = ["#4F46E5", "#6366F1", "#EEF2FF", "#818CF8", "#4338CA",
                 "#A5B4FC", "#C7D2FE", "#2E2F5A", "#242A4A"]

passed = failed = 0


def chk(ok: bool, good: str, bad: str, detail: str = "") -> bool:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✔ {good}")
    else:
        failed += 1
        print(f"  ✗ {bad}" + (f"　—— {detail}" if detail else ""))
    return ok


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def strip_qss_comments(text: str) -> str:
    """去掉 /* … */ 注释。

    ★ 必须剥注释再判旧色：改版说明里到处都要写「改版前是 #4F46E5」，
      不剥就会把"提到"当成"用着"，自检永远红。
    """
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def strip_swap_tables(text: str) -> str:
    """从 theme.py 里摘掉 _TEXT_SWAP / _BG_SWAP 两张置换表，并跳过整行注释。

    ★ 这两张表**必须**保留旧色值：14 个页面里还有上百处内联写死的旧灰/旧紫，
      深色主题靠它们逐字面量翻译。表里的旧色是"字典键"，不是"在用旧配色"。
      所以判违规时先把这两块整体摘出去。
    ★ 整行 `#` 注释也要跳过 —— 改版说明里必然要写「改版前是 #4F46E5」，
      不跳过就会把"提到"当成"用着"，自检永远红。
    """
    out, in_swap, depth = [], False, 0
    for line in text.splitlines():
        stripped = line.strip()
        if in_swap:
            depth += stripped.count("{") - stripped.count("}")
            if depth <= 0:
                in_swap = False
            continue
        if re.match(r"^_(?:TEXT|BG)_SWAP\s*=\s*\{", stripped):
            in_swap = True
            depth = stripped.count("{") - stripped.count("}")
            if depth <= 0:
                in_swap = False
            continue
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def section(name: str) -> None:
    print(f"\n{name}")


light = read(LIGHT)
dark = read(DARK)


# ---------------------------------------------------------------- A 旧色清零
def check_no_legacy() -> None:
    section("A. 旧配色必须清零（改回去就是视觉撕裂，但不会报错）")
    for label, text in (("light.qss", light), ("dark.qss", dark)):
        clean = strip_qss_comments(text).lower()
        hits = [c for c in LEGACY_INDIGO if c.lower() in clean]
        chk(not hits, f"{label} 无旧靛蓝色值", f"{label} 仍有旧靛蓝",
            "、".join(hits))

    for name in ("widgets/stat.py", "widgets/progress.py"):
        t = read(GUI / name).lower()
        hits = [c for c in LEGACY_INDIGO if c.lower() in t]
        chk(not hits, f"{name} 无旧靛蓝", f"{name} 仍有旧靛蓝", "、".join(hits))

    # theme.py：摘掉两张置换表之后，剩余部分不许再有旧色
    th = strip_swap_tables(read(GUI / "theme.py"))
    hits = [c for c in LEGACY_INDIGO if c.lower() in th.lower()]
    chk(not hits, "theme.py 令牌区无旧靛蓝（置换表除外）",
        "theme.py 仍在用旧靛蓝", "、".join(hits))

    # home_page 里曾把图标色写死成 #4F46E5，改主题后图标不变色
    hp = read(GUI / "pages" / "home_page.py").lower()
    chk("#4f46e5" not in hp, "工作台图标色跟随主题（未写死旧色）",
        "工作台图标仍写死 #4F46E5")


# ---------------------------------------------------------------- B 品牌落地
def check_brand_roles() -> None:
    section("B. 品牌色必须各就各位（墨=结构、柠檬=能量）")
    INK, ACID = "#0b0e14", "#c8f751"
    # ★ v2 界面重构（2026-11-16）：浅色柠檬调深一档，白底上更实。
    #   官网 / 深色主题仍是 #C8F751，浅色客户端专用 #C4F13E，不许互串。
    ACID_LIGHT = "#c4f13e"
    chk(INK in light.lower(), "浅色主题用墨做结构色", "浅色主题缺墨色 #0B0E14")
    chk(ACID_LIGHT in light.lower(), "浅色主题有专用柠檬 #C4F13E（能量点）",
        "浅色主题缺浅色柠檬 #C4F13E")
    chk(ACID in dark.lower(), "深色主题用柠檬做结构色", "深色主题缺柠檬 #C8F751")

    # 页面底：深色主题必须直接用官网 hero 的墨色，形成品牌闭环
    chk(re.search(r"QWidget\s*\{[^}]*background:\s*#0B0E14", dark, re.I) is not None,
        "深色页面底 = 官网 hero 墨色 #0B0E14",
        "深色页面底不是 #0B0E14（品牌闭环断了）")

    # 主按钮：两套主题都必须是柠檬底 + 墨字（各自的柠檬档）
    for label, text, acid in (("浅色", light, ACID_LIGHT),
                              ("深色", dark, ACID)):
        m = re.search(r"QPushButton#Primary\s*\{([^}]*)\}", text, re.I)
        body = (m.group(1) if m else "").lower()
        ok = acid in body and INK in body and "font-weight" in body
        chk(ok, f"{label}主题主按钮 = 柠檬底墨字",
            f"{label}主题主按钮不是柠檬底墨字", body.strip()[:60])

    # 导航选中态：浅色墨底+柠檬竖条，深色柠檬底
    m = re.search(r"QPushButton#NavItem:checked\s*\{([^}]*)\}", light, re.I)
    body = (m.group(1) if m else "").lower()
    chk(INK in body and "border-left" in body,
        "浅色导航选中 = 墨底 + 左侧柠檬指示条",
        "浅色导航选中态缺墨底或指示条", body.strip()[:60])
    m = re.search(r"QPushButton#NavItem:checked\s*\{([^}]*)\}", dark, re.I)
    body = (m.group(1) if m else "").lower()
    chk(ACID in body, "深色导航选中 = 柠檬底",
        "深色导航选中态不是柠檬底", body.strip()[:60])

    # theme.py 的令牌
    th = read(GUI / "theme.py")
    chk('BRAND_INK = "#0B0E14"' in th and 'BRAND_ACID = "#C8F751"' in th,
        "主题令牌已集中定义（BRAND_INK / BRAND_ACID）",
        "theme.py 缺品牌常量")
    chk('BRAND_ACID_LIGHT = "#C4F13E"' in th,
        "浅色柠檬有独立令牌 BRAND_ACID_LIGHT",
        "theme.py 缺 BRAND_ACID_LIGHT（v2 浅色色板）")
    chk(re.search(r'_ACCENT_BY_MODE\s*=\s*\{[^}]*"light":\s*BRAND_INK', th) is not None,
        "浅色 accent = 墨", "浅色 accent 不是墨")
    chk(re.search(r'_ACCENT_BY_MODE\s*=\s*\{[^}]*"dark":\s*BRAND_ACID', th) is not None,
        "深色 accent = 柠檬", "深色 accent 不是柠檬")
    chk("_NAV_ACTIVE_ICON_BY_MODE" in th,
        "导航选中图标色按主题分别定义（墨底白图标 / 柠檬底墨图标）",
        "导航选中图标色仍是单一常量 —— 深色下会看不见")


# ---------------------------------------------------------------- C 弹层补齐
def check_popups() -> None:
    section("C. 弹出菜单必须有定制（改版前唯一的系统原生接缝）")
    for label, text in (("浅色", light), ("深色", dark)):
        for sel, human in (
            (r"QMenu\s*\{", "QMenu 本体"),
            (r"QMenu::item\s*\{", "菜单项"),
            (r"QMenu::item:selected", "菜单项选中态"),
            (r"QMenu::separator", "菜单分隔线"),
        ):
            chk(re.search(sel, text, re.I) is not None,
                f"{label}主题有 {human}", f"{label}主题缺 {human}")

    # 下拉列表是 QAbstractItemView，不继承 QMenu，必须单独写
    for label, text in (("浅色", light), ("深色", dark)):
        chk("QComboBox QAbstractItemView" in text,
            f"{label}主题定制了下拉列表", f"{label}主题下拉列表仍是系统原生")
        chk("QComboBox::drop-down" in text,
            f"{label}主题定制了下拉箭头区", f"{label}主题缺下拉箭头区样式")


# ---------------------------------------------------------------- D 层级字体
def check_typography() -> None:
    section("D. 字号分层与数值字体（否则标题和正文一样大）")
    sizes = set(re.findall(r"font-size:\s*(\d+)px",
                           light, re.I))
    # 2026-10-18：字号从 8 档（11/12/13/14/15/16/20/26）收敛到 5 档。
    #   依据 tasteskill 的「一页五种字号是上限」—— 档位太多时相邻两档只差
    #   1px，眼睛分不出谁更重要，层级等于没有。
    #   12 次要 / 13 正文 / 16 小标题 / 20 引导页标题 / 26 页面标题与数值
    # ★ 别再加回 11px：圆体在 11px 下圆弧只剩一个像素，反而更糊
    #   （2026-10-17 已经把 Meta 从 11 提到 12，这次把剩余 11 全部并掉）。
    for need in ("12", "13", "16", "26"):
        chk(need in sizes, f"字号 {need}px 在用", f"字号 {need}px 缺失",
            f"现有 {sorted(sizes, key=int)}")
    chk(len(sizes) <= 5, f"字号档位不超过 5 档（现有 {len(sizes)} 档）",
        f"字号档位 {len(sizes)} 档 —— 超过 5 档层级就糊了",
        f"现有 {sorted(sizes, key=int)}")
    chk(not ({"11", "14", "15"} & sizes),
        "没有 11/14/15 这些中间档",
        "仍存在贴着主档的中间字号，层级会互相打架",
        f"现有 {sorted(sizes, key=int)}")
    # 两套主题的档位必须一致（否则深色下层级会变一套）
    sizes_dark = set(re.findall(r"font-size:\s*(\d+)px", dark, re.I))
    chk(sizes == sizes_dark, "明暗两套主题字号档位完全一致",
        f"两套主题字号不一致：浅 {sorted(sizes, key=int)} / 深 {sorted(sizes_dark, key=int)}")

    for cls in ("H1", "H2", "Body", "Caption", "Meta", "StatValue", "Badge"):
        chk(re.search(rf"QLabel#{cls}\s*\{{", light) is not None,
            f"语义字号类 {cls} 已定义", f"缺语义字号类 {cls}")

    m = re.search(r"QLabel#StatValue\s*\{([^}]*)\}", light, re.I)
    body = (m.group(1) if m else "")
    # ★ 2026-10-17 二次改版：数值字体从 Consolas 换回品牌字体
    #   旧规则是为了「数字不抖」，但代价是同一张卡里 128 是 Consolas、
    #   "不限量"是雅黑，两种字形拼在一起就是「不是同一个产品」的观感 ——
    #   而这正是客户说的"字体不好看"。防抖改由「等宽栅格 + 左对齐」解决。
    chk("__APP_FONT__" in body and "Consolas" not in body,
        "数值与正文同一字族（不再混排等宽字体）",
        "数值仍用 Consolas 等宽 —— 和中文混排会出现两种字形")
    chk("26px" in body, "数值 26px（与页面标题同档）", "数值不是 26px")

    st = read(GUI / "widgets" / "stat.py")
    chk("_num_font" in st, "指标卡数字走统一的 _num_font",
        "指标卡数字没有统一封装（换字体时会漏掉自绘组件）")
    # 判"还在用"要看构造调用，不能搜字符串 —— _num_font 的注释里就写着
    # 「旧版用 Consolas」，按字样判会永远红。
    chk('QFont("Consolas"' not in st and "QFont('Consolas'" not in st,
        "_num_font 里没有残留 Consolas",
        "_num_font 仍返回 Consolas（数值会和中文两种字形）")

    # 负字距：苹果每个大字号标题都收紧字距，这是"像产品"最省力的一招。
    # Qt 的 QSS **没有 letter-spacing**（写了不生效），只能走 QFont，
    # 所以这里查 theme 里有没有那张字距表 + 应用函数。
    th = read(GUI / "theme.py")
    chk("_TRACKING" in th and "def apply_tracking" in th,
        "标题带负字距（Apple 的紧排）",
        "没有字距表 —— 标题会松成网页表单")


# ---------------------------------------------------------------- E 一致性
def check_consistency() -> None:
    section("E. 组件覆盖与资源完整性（漏一处就有一处是旧的）")

    # 叶控件透明 —— 这两行是「卡片灰条」事故的唯一解，绝不能删
    for label, text in (("浅色", light), ("深色", dark)):
        chk(re.search(r"QLabel,\s*QCheckBox,\s*QRadioButton\s*\{[^}]*transparent",
                      text) is not None,
            f"{label}主题保留叶控件透明规则",
            f"{label}主题丢了叶控件透明规则 —— 卡片会重新出现灰条")

    # 深色勾选底是柠檬，必须用墨色勾图
    chk("__CHECK_ICON_DARK__" in dark,
        "深色主题用墨色勾图（柠檬底配白勾会看不见）",
        "深色主题没换墨色勾图")
    chk(re.search(r'image:\s*url\("__CHECK_ICON__"\)', light) is not None,
        "浅色主题仍用白勾", "浅色主题勾图占位符丢了")
    chk((GUI / "styles" / "check_dark.png").is_file(),
        "墨色勾图文件在位", "缺 check_dark.png（跑 tools/gen_check_icon.py）")

    # 提示条组件与状态行
    chk((GUI / "widgets" / "alert.py").is_file(),
        "AlertBar 提示条组件在位", "缺 widgets/alert.py")
    for lv in ("warn", "danger", "info"):
        chk(f'QFrame#AlertBar[level="{lv}"]' in light
            and f'QFrame#AlertBar[level="{lv}"]' in dark,
            f"提示条 {lv} 档在两套主题都有配色",
            f"提示条 {lv} 档缺配色")

    base = read(GUI / "pages" / "_base.py")
    chk("class StatusLine" in base, "状态行组件 StatusLine 在位",
        "缺 StatusLine —— 状态会退回裸色字")

    pages = sorted((GUI / "pages").glob("*_page.py"))
    using = [p.name for p in pages if "StatusLine" in read(p)]
    chk(len(using) >= 14,
        f"状态行已覆盖 {len(using)} 个页面", "有页面还在用裸 QLabel 当状态行",
        f"仅 {len(using)} 个")

    # 状态行的主题刷新钩子（写死内联样式，切主题不会自动变）
    mw = read(GUI / "main_window.py")
    chk("StatusLine" in mw, "切主题时会刷新状态行配色",
        "切主题不刷新 StatusLine —— 深色下会残留浅色底")

    # 空徽标必须隐藏（新加的 padding 会让空标签变成小色块）
    st = read(GUI / "widgets" / "stat.py")
    chk("self.badge_lab.setVisible(bool(badge))" in st,
        "空徽标已隐藏（不会冒出小色块）", "空徽标没隐藏 —— 卡片标题旁会有色块")

    # 标题工厂必须走 QSS 语义类
    chk('setObjectName("H1")' in base and 'setObjectName("H2")' in base,
        "页面/卡片标题走 QSS 语义类", "标题还在内联写死字号")


def check_layout_overflow() -> None:
    """逐个页面量「最小宽度 vs 视口宽度」，拦横向滚动条。

    ★ 为什么必须真构建窗口才知道
      横向滚动条是控件 minimumSizeHint 累积的结果，纯文本检查看不出来。
      实测过的几个来源（都是改版时才发现）：
        · QLabel 里塞完整文件路径（60~80 字符，sizeHint 直接 600+）
        · QComboBox 的某个 item 文案特别长 —— 而且 **setFixedWidth 挡不住**，
          它的 minimumSizeHint 仍按最长 item 算
        · 一行塞 7 个按钮（直播监控），总宽刚好越过视口
      客户看到的是「右边被切掉 / 底下多一条滚动条」，会直接说"排版乱"。
    """
    section("F. 页面不许出现横向滚动条（右侧被切 = 客户眼里的排版崩）")
    import os
    import warnings
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("TRAFFIC_NO_ANIM", "1")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from PySide6.QtWidgets import QApplication
        from gui_qt.main_window import MainWindow
        from gui_qt.pages._base import AppContext
        from gui_qt.theme import apply_theme
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "能导入界面模块", "导入界面模块失败", str(exc)[:70])
        return

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    try:
        win = MainWindow(AppContext())
        win.resize(1180, 940)          # 客户最常见的窗口大小
        win.show()
        app.processEvents()
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "主窗口能构建", "主窗口构建失败", str(exc)[:70])
        return

    over = []
    for name, scroll in win._scrolls.items():
        win.switch_to(name)
        app.processEvents()
        page = scroll.widget()
        if page is None:
            continue
        gap = page.minimumSizeHint().width() - scroll.viewport().width()
        if gap > 0:
            over.append((name, gap))

    chk(not over, f"全部 {len(win._scrolls)} 个页面宽度都装得下",
        "有页面超出窗口宽度（会出现横向滚动条）",
        "；".join(f"{n} 超 {g}px" for n, g in over[:5]))
    try:
        win.close()
    except Exception:                                        # noqa: BLE001
        pass


def check_widget_seams() -> None:
    """拦「控件接缝」类退化：下拉框丢箭头、表格不 fits、内部键外露。

    ★ 这三类都不会报错，只能在截图里肉眼发现，所以必须钉住：
      1. QSS 一旦写了 QComboBox::drop-down，Qt 就不再画系统那个小三角。
         没补 ::down-arrow 的话，下拉框长得跟只读输入框一模一样。
      2. QTableWidget 每列默认 100px，9 列就超视口 → 默认窗口下就挂横向滚动条。
         _tune_tables 收窄短字段后，列数不算多的表必须能整列装下。
      3. 界面文案里不许出现 douyin / xiaohongshu 这种内部键。
    """
    section("G. 控件接缝（下拉箭头 / 表格装得下 / 不露内部键）")

    # ---- 1. 下拉箭头 ----
    chk('QComboBox::down-arrow' in light and '__ARROW_DOWN__' in light,
        "浅色主题给下拉箭头喂了图",
        "浅色主题没给 ::down-arrow 配图（下拉框会变成没箭头的输入框）")
    chk('QComboBox::down-arrow' in dark and '__ARROW_DOWN_DARK__' in dark,
        "深色主题给下拉箭头喂了图",
        "深色主题没给 ::down-arrow 配图（浅灰底色上用浅色箭头会看不见）")
    styles_dir = GUI / "styles"
    missing = [n for n in ("arrow_down.png", "arrow_down_dark.png",
                           "check.png", "check_dark.png")
               if not (styles_dir / n).exists()]
    chk(not missing, "箭头与勾图资源都在位", "资源缺文件", "、".join(missing))
    theme = read(GUI / "theme.py")
    chk("_ARROW_DOWN_TOKEN" in theme and "_ARROW_DOWN_DARK_TOKEN" in theme,
        "theme.py 注册了箭头占位符",
        "theme.py 没注册箭头占位符（QSS 里会留着一串 __ARROW_DOWN__）")

    # ---- 2. 表格定宽接线 ----
    mw = read(GUI / "main_window.py")
    chk("def _tune_tables" in mw, "_tune_tables 在位", "_tune_tables 不见了")
    chk("_tune_tables(page)" in mw,
        "建页后调用了 _tune_tables",
        "建页后没调用 _tune_tables（表格会退回每列 100px）")
    chk("setDefaultAlignment" in mw,
        "表头左对齐走 API",
        "表头没设 setDefaultAlignment（QSS 的 text-align 对表头无效）")

    # ---- 3. 页面不许把内部键当文案 ----
    bad_combo = []
    for p in sorted((GUI / "pages").glob("*.py")):
        text = p.read_text(encoding="utf-8", errors="replace")
        if 'addItems(["douyin"' in text or "addItems(['douyin'" in text:
            bad_combo.append(p.name)
    chk(not bad_combo,
        "平台下拉都显示中文名",
        "有下拉直接把 douyin 当文案显示",
        "、".join(bad_combo))

    # ---- 4. 真构建窗口：列数不多的表必须整列装得下 ----
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("TRAFFIC_NO_ANIM", "1")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from PySide6.QtWidgets import QApplication, QTableView
        from gui_qt.main_window import MainWindow
        from gui_qt.pages._base import AppContext
        from gui_qt.theme import apply_theme
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "能导入界面模块", "导入界面模块失败", str(exc)[:70])
        return

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    try:
        win = MainWindow(AppContext())
        win.resize(1180, 940)
        win.show()
        for _ in range(40):
            app.processEvents()
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "主窗口能构建", "主窗口构建失败", str(exc)[:70])
        return

    overflowing = []
    for name, scroll in win._scrolls.items():
        win.switch_to(name)
        for _ in range(20):
            app.processEvents()
        page = scroll.widget()
        if page is None:
            continue
        for t in page.findChildren(QTableView):
            # ★ 2026-10-27：看不见的表格不算数。按步骤分档的页面（文案仿写 /
            #   GEO）把结果表放在没切到的那一档里，那种状态下 Qt 给的 viewport
            #   宽度是假的（实测 638 vs 显示时的 866），照它算必然"超"。
            #   而这一条要拦的是「客户切到那一档之后右边被切掉」—— 真切换过去
            #   时 Qt 会重算列宽（tools/_probe_pages_height 同路径实测 差=0），
            #   所以这里跳过隐藏的才是对的口径，不是放水。
            if not t.isVisible():
                continue
            hdr = t.horizontalHeader()
            cols = hdr.count()
            if cols <= 0 or cols > 9:
                # 列数本来就不止 9 个的表（关键词抓取 13 列等）放不进 866px
                # 是数据密度问题，允许它滚 —— 只钉「本该装得下却没装下」的。
                continue
            total = sum(hdr.sectionSize(i) for i in range(cols))
            gap = total - t.viewport().width()
            if gap > 0:
                overflowing.append(f"{name}（{cols} 列，超 {gap}px）")
    chk(not overflowing,
        "列数 ≤9 的表格都整列装得下",
        "有表格在默认窗口下挂横向滚动条",
        "；".join(overflowing[:4]))

    try:
        win.close()
    except Exception:                                        # noqa: BLE001
        pass


def _build_win(fresh_nav: bool = False):
    """构建一个离屏主窗口（G/H/I 组共用，省三次冷启动）。

    ★ 两个必须做干净的副作用（2026-10-17 被自己坑过）
      1. `fresh_nav=True` 时清掉 nav_collapsed：折叠状态存在用户配置里，
         本机跑过一次折叠后，自检再跑就永远测不到「默认折叠」——
         于是「折叠功能坏了」会被当成通过，或者反过来。
      2. 把 save_settings 换成空操作：自检不该写用户的真实配置文件。
    """
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("TRAFFIC_NO_ANIM", "1")
    if sys.platform.startswith("win"):
        os.environ.setdefault(
            "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from PySide6.QtWidgets import QApplication
    from gui_qt.main_window import MainWindow
    from gui_qt.pages._base import AppContext
    from gui_qt.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "light")
    ctx = AppContext()
    if fresh_nav:
        ctx.settings.pop("nav_collapsed", None)
    ctx.save_settings = lambda: True        # noqa: E731  自检不落盘
    win = MainWindow(ctx)
    win.resize(1180, 940)
    win.show()
    for _ in range(40):
        app.processEvents()
    return app, win


def check_empty_states() -> None:
    """G. 空态引导：每张表都有、都能点、显隐时机对。

    ★ 为什么值得单独钉一组
      空态是「不出错就没存在感」的东西：某天有人加了张新表忘了挂空态，
      或者把某个按钮的回调改名了，界面看起来完全正常 —— 只有客户在空表格前
      发呆时才暴露。所以三条都要机器验：数量、回调、显隐。
    """
    section("G. 空表格必须给「下一步点哪」的出口")
    try:
        from PySide6.QtWidgets import QPushButton, QTableView

        from gui_qt.widgets.empty import EmptyHint
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "能导入空态组件", "导入空态组件失败", str(exc)[:70])
        return
    try:
        app, win = _build_win()
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "主窗口能构建", "主窗口构建失败", str(exc)[:70])
        return

    tables = hints = with_btn = 0
    cold = []
    for name in win._scrolls:
        win.switch_to(name)
        app.processEvents()
        page = win._scrolls[name].widget()
        if page is None:
            continue
        ts = page.findChildren(QTableView)
        hs = page.findChildren(EmptyHint)
        tables += len(ts)
        hints += len(hs)
        if len(hs) < len(ts):
            cold.append(name)
        for h in hs:
            if h.findChild(QPushButton) is not None:
                with_btn += 1

    chk(tables > 0 and hints == tables,
        f"全部 {tables} 张表都挂了空态",
        "有表格没挂空态（空着就是一片白）",
        "未覆盖：" + "、".join(cold[:5]))
    chk(with_btn == hints and hints > 0,
        f"全部 {hints} 个空态都带可点按钮",
        "有空态只写了文案、没有出口按钮",
        f"{hints - with_btn} 个缺按钮")

    # 显隐：清空→出现；写一行→隐藏
    try:
        from PySide6.QtWidgets import QTableWidgetItem

        win.switch_to("关键词抓取")
        app.processEvents()
        page = win._scrolls["关键词抓取"].widget()
        table = page.findChildren(QTableView)[0]
        hint = page.findChildren(EmptyHint)[0]
        table.setRowCount(0)
        app.processEvents()
        shown_when_empty = hint.isVisible()
        table.insertRow(0)
        table.setItem(0, 0, QTableWidgetItem("x"))
        app.processEvents()
        hidden_when_data = not hint.isVisible()
        chk(shown_when_empty and hidden_when_data,
            "空态跟着行数自动显隐（清空出现 / 有数据消失）",
            "空态显隐时机不对（会盖住真实数据，或该出现时不出现）",
            f"空时={shown_when_empty} 有数据时隐藏={hidden_when_data}")
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "空态显隐可测", "空态显隐测试异常", str(exc)[:70])

    try:
        win.close()
    except Exception:                                        # noqa: BLE001
        pass


def check_nav_collapse() -> None:
    """H. 侧栏折叠：功能一个不少、跳转能自动展开、状态能记住。"""
    section("H. 侧栏折叠不许把功能藏没")
    try:
        # fresh_nav：从「第一次安装」的干净状态测，否则本机跑过一次折叠后
        # 这项永远测不到默认值
        app, win = _build_win(fresh_nav=True)
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "主窗口能构建", "主窗口构建失败", str(exc)[:70])
        return

    from gui_qt.main_window import NAV_GROUPS

    total = sum(len(items) for _g, items in NAV_GROUPS)
    chk(len(win._nav_buttons) == total,
        f"导航项一个没少（{total} 个）",
        "导航项数量对不上（折叠不该删功能）",
        f"实际 {len(win._nav_buttons)} / 期望 {total}")

    visible = [n for n, b in win._nav_buttons if b.isVisible()]
    chk(0 < len(visible) < total,
        f"默认只露常用项（{len(visible)}/{total}），低频项收起来了",
        "折叠没生效（还是全部平铺）或过头（全藏了）",
        f"可见={visible}")

    # 跳到一个被折叠的页面，侧栏必须自动展开那一组
    hidden_item = next((n for n, b in win._nav_buttons if not b.isVisible()), None)
    if hidden_item:
        before = dict(win.ctx.settings.get("nav_collapsed") or {})
        win.switch_to(hidden_item)
        app.processEvents()
        chk(dict(win._nav_buttons)[hidden_item].isVisible(),
            f"Ctrl+K 跳到「{hidden_item}」时侧栏自动展开该组",
            "跳过去后侧栏还收着（客户看不到自己被跳到哪，会以为点错了）")
        # ★ 程序跳转不许写盘：写了的话客户访问过一次某组就再也收不回去，
        #   用几天侧栏会自己变回全展开，折叠等于白做
        after = dict(win.ctx.settings.get("nav_collapsed") or {})
        chk(before == after,
            "程序跳转只临时展开、不改用户设定的折叠状态",
            "程序跳转把折叠状态写死了（客户访问过就再也收不回去）",
            f"{before} -> {after}"[:70])
    else:
        chk(False, "存在可折叠分组", "没有默认折叠的分组，折叠功能等于没做")

    # 折叠状态要写回配置 —— 否则每次启动都重置，等于每天推乱一次
    key = "nav_collapsed"
    try:
        win._set_group_collapsed("分析", True)      # 主动折叠一次，看有没有落配置
        app.processEvents()
    except Exception:                                        # noqa: BLE001
        pass
    saved = win.ctx.settings.get(key)
    chk(isinstance(saved, dict) and bool(saved),
        "折叠状态会写进配置（下次启动保持）",
        "折叠状态没持久化（每次启动都被重置）",
        f"settings[{key}]={saved!r}"[:60])

    # 分组标题必须有箭头图标，否则看不出能点
    heads_with_icon = sum(1 for g, b in win._group_heads.items()
                          if g not in ("开始", "设置") and not b.icon().isNull())
    chk(heads_with_icon >= 1,
        f"{heads_with_icon} 个分组标题带展开箭头",
        "分组标题没有箭头（看着就是一行灰字，没人知道能点）")
    try:
        win.close()
    except Exception:                                        # noqa: BLE001
        pass


def check_command_palette() -> None:
    """I. Ctrl+K：每个页面都搜得到，别名真的能命中。"""
    section("I. Ctrl+K 能否按客户的说法找到功能")
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from gui_qt.main_window import NAV_GROUPS
        from gui_qt.widgets.command_palette import (_ALIASES, _match_score,
                                                    build_commands)
    except Exception as exc:                                 # noqa: BLE001
        chk(False, "能导入命令面板", "导入命令面板失败", str(exc)[:70])
        return

    cmds = build_commands(NAV_GROUPS)
    names = [c[0] for c in cmds]
    pages = [n for _g, items in NAV_GROUPS for n in items]
    missing = [p for p in pages if p not in names]
    chk(not missing, f"全部 {len(pages)} 个页面都在搜索清单里",
        "有页面搜不到（折叠后就真的找不回来了）", "、".join(missing[:5]))

    # 每个页面至少 3 个别名：少于这个数，客户脑子里的词八成对不上
    thin = [p for p in pages if len(_ALIASES.get(p, [])) < 3]
    chk(not thin, "每个页面都配了 ≥3 个搜索别名",
        "有页面别名太少（客户用自己的说法搜不到）", "、".join(thin[:5]))

    # 真实说法命中测试：这些词是客户嘴里会说的，不是页面名
    cases = {"爆款": "选题挖掘", "录直播": "直播监控", "whisper": "音视频转文本",
             "额度": "工作台", "去水印": "视频下载", "问价": "评论区截流",
             "周报": "报告", "竞品": "竞品体检", "字幕": "音视频转文本"}
    bad = []
    for q, want in cases.items():
        best = max(cmds, key=lambda c: _match_score(q, c[0], c[2]))
        if _match_score(q, best[0], best[2]) <= 0 or best[0] != want:
            bad.append(f"{q}→{best[0]}(期望{want})")
    chk(not bad, f"客户口语 {len(cases)} 条都能对到正确页面",
        "有口语搜不到对的功能", "；".join(bad[:4]))

    spec = read(ROOT / "justoneapi_collector.spec")
    chk("gui_qt.widgets.command_palette" in spec,
        "命令面板已进打包 hiddenimports",
        "命令面板没进 hiddenimports（打包后 Ctrl+K 毫无反应，且不报错）")
    chk("Ctrl+K" in read(GUI / "main_window.py"),
        "Ctrl+K 快捷键已注册",
        "Ctrl+K 没注册（折叠后没有按名字直达的出口）")


def check_app_font() -> None:
    """J. 字体：内嵌可用、QSS 已换成占位符、自绘组件同源。"""
    section("J. 界面字体是否真的换成了随包字体")
    fonts_dir = GUI / "styles" / "fonts"
    want = ["TrafficSans-Regular.ttf", "TrafficSans-Medium.ttf",
            "TrafficSans-Bold.ttf"]
    missing = [f for f in want if not (fonts_dir / f).is_file()]
    chk(not missing, "三档字体文件都在（Regular / Medium / Bold）",
        "字体文件缺失（会静默回退雅黑，界面气质白改）", "、".join(missing))

    if not missing:
        total_kb = sum((fonts_dir / f).stat().st_size for f in want) / 1024
        chk(total_kb < 9000, f"字体体积可控（{total_kb:.0f} KB）",
            "字体体积超预期（安装包会明显变大）", f"{total_kb:.0f} KB")

    # QSS 里必须走占位符，不能残留写死的雅黑 —— 写死就等于 P3 没做
    for label, text in (("浅色", strip_qss_comments(light)),
                        ("深色", strip_qss_comments(dark))):
        chk("__APP_FONT__" in text,
            f"{label} QSS 用占位符引用界面字体",
            f"{label} QSS 没引用界面字体（可能写死回系统字体了）")

    theme_src = read(GUI / "theme.py")
    chk("install_app_fonts" in theme_src and "addApplicationFont" in theme_src,
        "theme 里注册了内嵌字体",
        "theme 没有注册内嵌字体（字体文件在位也没人用）")
    chk("_FALLBACK_FONT_FAMILY" in theme_src,
        "字体加载失败有回退分支",
        "字体加载没有回退（打包漏资源会全局豆腐块）")

    # ★ 资源解析必须走 _resolve_asset_url（底层 _candidate_style_dirs）
    #   不能直接拿 _STYLES_DIR 拼路径。_STYLES_DIR 是 __file__ 相对路径，
    #   2026-10-17 实测在 onedir 打包下刚好成立 —— 但它依赖「数据落在同名
    #   目录」这个巧合，挪一次就静默退回雅黑（不报错、截图才看得出）。
    # 判「有没有用」只看代码本身：注释里提到 _STYLES_DIR 不算，
    # 也不该用字符串搜索（会把注释误判成调用）。直接走 AST，天然剥掉注释与 docstring。
    import ast
    tree = ast.parse(theme_src)
    font_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "install_app_fonts"), None)
    used_names = set()
    if font_fn is not None:
        for node in ast.walk(font_fn):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                used_names.add(node.attr)
    chk(font_fn is not None and "_resolve_asset_url" in used_names
        and "_STYLES_DIR" not in used_names,
        "字体走通用资源解析链（打包布局变了也不失效）",
        "字体仍用 __file__ 相对路径拼 resources（挪资源会静默退回系统字体）")

    # ---- 运行时：文件在位 ≠ 注册成功。三档字重必须真的进 QFontDatabase ----
    # Cell A 要求：QApplication 之后才能 addApplicationFont；这里可以复用前面
    # 已经建好的实例（没有就自己建一个）。
    import os
    import warnings
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFontDatabase
        app = QApplication.instance() or QApplication([])
        from gui_qt import theme as _theme
        with warnings.catch_warnings():
            # Qt6 里 QFontDatabase 的构造已标 Deprecation，这里只是读库，不必告警
            warnings.simplefilter("ignore")
            ok = _theme.install_app_fonts()
            fam = _theme.app_font_family()
            chk(ok, f"运行时注册成功（当前字族 {fam}）",
                "运行时没注册上（文件在位也会静默退回雅黑）")
            fams = set(QFontDatabase().families())
            chk(fam in fams, f"字体家族「{_theme.APP_FONT_FAMILY}」在 QFontDatabase 里",
                "字体家族不在库里（Qt 没认这个 ttf，常见于成品还是 woff2）")
            if fam in fams:
                weights = QFontDatabase().styles(fam)
                chk(len(weights) >= 3,
                    f"三档字重齐全（{ '/'.join(weights) }）",
                    "字重不全（标题的半粗会掉到相邻字重，层级看着就糊了）",
                    "/".join(weights))
    except Exception as exc:                                       # noqa: BLE001
        chk(False, "", f"字体运行时自检跑不起来：{exc}")

    stat_src = read(GUI / "widgets" / "stat.py")
    chk("ui_font(" in stat_src and '"Microsoft YaHei UI"' not in stat_src,
        "自绘组件也走同一个字体入口",
        "自绘组件仍写死雅黑（会和其他控件两种字体）")


def check_browser_surfaces() -> None:
    """K. 你没画的那部分（impeccable craft-floor 的 browser surfaces）。

    文字选中、滚动条、焦点环、表格数字这些地方，Qt 自带一套跟设计系统毫无
    关系的默认值。客户说不出哪里不对，但就是觉得"像没做完"。最容易漏的是
    **键盘焦点环**：鼠标点的人一辈子看不见，用 Tab 走一遍就露馅 —— 而按钮
    的 :focus 一旦漏掉，加新按钮时也不会有人想起来补。所以钉成自检。
    """
    print("\nK. 浏览器表面（选中 / 滚动条 / 焦点环 / 表格数字）")
    for name, src in (("浅色", light), ("深色", dark)):
        chk("selection-background-color" in src and "selection-color" in src,
            f"{name}主题的文字选中色已接管",
            f"{name}主题还在用系统蓝选中色", name)
        chk("QScrollBar::handle" in src,
            f"{name}主题的滚动条滑块已接管",
            f"{name}主题的滚动条还是系统默认", name)
        btns = [ln.strip()[:-2].strip() for ln in src.splitlines()
                if ln.strip().startswith("QPushButton") and ln.strip().endswith("{")
                and ":" not in ln.strip()[:-2]]
        miss = [b for b in btns if f"{b}:focus" not in src
                and "border: none" not in _block_body(src, b)]
        chk(not miss,
            f"{name}主题 {len(btns)} 类按钮都有键盘焦点环",
            f"{name}主题这些按钮缺 :focus，Tab 过去看不出来：{miss[:4]}", name)
    chk('setFeature(_TNUM_TAG' in read(GUI / "theme.py") or
        'setFeature("tnum"' in read(GUI / "theme.py"),
        "数字类控件开了等宽数字（滚动时不左右抖）",
        "数字没开 tnum：滚动/实时刷新时位数一变就抖")


def _block_body(src: str, selector: str) -> str:
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == selector + " {":
            j = i + 1
            buf = []
            while j < len(lines) and lines[j].strip() != "}":
                buf.append(lines[j])
                j += 1
            return "\n".join(buf)
    return ""


def main() -> int:
    print("界面品牌一致性自检")
    print("=" * 62)
    if not light or not dark:
        print("✗ 找不到 QSS 文件")
        return 2
    check_no_legacy()
    check_brand_roles()
    check_popups()
    check_typography()
    check_consistency()
    check_layout_overflow()
    check_widget_seams()
    check_empty_states()
    check_nav_collapse()
    check_command_palette()
    check_app_font()
    check_browser_surfaces()
    print("\n" + "=" * 62)
    print(f"通过 {passed} 项，失败 {failed} 项")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
