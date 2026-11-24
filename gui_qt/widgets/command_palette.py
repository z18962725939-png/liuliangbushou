"""命令面板 / 功能搜索（Ctrl+K）。

为什么要它（2026-10-17 UI 诊断 P2）：
    侧栏把低频功能折叠之后，16 个功能不再全屏平铺 —— 找功能如果仍然只能
    靠逐字读侧栏，折叠就从「简化」变成了「藏东西」。
    所以折叠必须配一个「按名字直达」的出口：Ctrl+K 唤起，打两个字，
    回车即到。这也是所有专业桌面工具（VS Code / Figma / Notion）的通行做法。

设计纪律：
- **搜得到比好看重要**：匹配必须同时支持中文名、别名和拼音首字母。
  客户脑子里的词是「爆款」「下载」「额度」，不是系统的页面名 ——
  别名表不是锦上添花，是这个功能的本体。
- **自带操作盘**：每次列出全部候选（按匹配度排），不做「输到 3 个字才显示」，
  因为空面板会让人以为软件卡了。
- **键盘闭环**：↑↓ 选、Enter 打开、Esc 关闭，全程不用碰鼠标。
- 颜色走 QSS 语义类，不内联写死。
"""
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QKeySequence,
                           QPainter, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QStyle, QStyledItemDelegate, QVBoxLayout,
                               QWidget)

from gui_qt.icons import nav_icon


def _current_mode() -> str:
    app = QApplication.instance()
    if app is None:
        return "light"
    try:
        return app.property("theme_mode") or "light"
    except Exception:                                           # noqa: BLE001
        return "light"


def _glyph_color(mode: str) -> str:
    """条目图标色：跟随主题的"正文级"灰，不用强调色 —— 20 个条目全用柠檬，
    整屏就糊成一片绿。"""
    return "#9AA4B2" if mode == "dark" else "#566070"


def _search_glyph(mode: str) -> QIcon:
    """输入框左侧放大镜（v2 原型 .cmdk-input 的那支线性图标）。

    ★ 自己画而不是找资源图：图标要跟主题变灰阶，而 nav_icon 里那支
      「关键词抓取」是放大镜 + 关键词，语义不对、尺寸也不合适。
    """
    color = QColor("#6B7280" if mode != "dark" else "#9AA4B2")
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    q.setPen(QPen(color, 2.2))
    q.drawEllipse(QRectF(5, 5, 15, 15))
    q.drawLine(QPointF(16.2, 16.2), QPointF(26, 26))
    q.end()
    return QIcon(pm)


def _keycap_icon(mode: str, text: str = "Esc") -> QIcon:
    """输入框右侧键帽：细边框 + 小圆角，本身就是「这里有个快捷键」的图示。"""
    light = mode != "dark"
    # ★ 浅色键帽不能用白底 + 极淡描边：输入框本身就是 #F4F5F7，
    #   那样只剩一条若有若无的弧线（离屏截图复核才发现）。描边压深一档，
    #   文字用正文级灰，键帽才读得出是一颗「键」。
    fg = QColor("#566070" if light else "#9AA4B2")
    bd = QColor("#B4BAC4" if light else "#2E3846")
    bg = QColor("#FFFFFF" if light else "#1A212B")
    pm = QPixmap(44, 26)
    pm.fill(Qt.GlobalColor.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    q.setPen(QPen(bd, 1.2))
    q.setBrush(bg)
    q.drawRoundedRect(QRectF(1, 1, 42, 24), 6, 6)
    q.setPen(fg)
    f = QFont()
    f.setPixelSize(13)
    f.setWeight(QFont.Weight.DemiBold)
    q.setFont(f)
    q.drawText(QRectF(1, 1, 42, 24), Qt.AlignmentFlag.AlignCenter, text)
    q.end()
    return QIcon(pm)


# --------------------------------------------------------------------------
# 搜索别名：客户的叫法 → 系统页面名
# --------------------------------------------------------------------------
# ★ 三条录入规则
#   1. 写客户嘴里那个词（「爆款」「录屏」「行话」），不写开发术语；
#   2. 拼音首字母必须有（xtwj → 选题挖掘）—— 中文输入法还没切过来时也能敲；
#   3. 每个页面至少 3 个别名，否则这一项等于没建索引。
# >>> PAGE-REGISTRY BEGIN aliases >>>
_ALIASES = {
    "工作台": ["gzt", "首页", "home", "总览", "额度", "授权", "概览", "看板"],
    "选题挖掘": ["xtwj", "选题", "挖掘", "爆款", "低粉高赞", "痛点", "爆粉比", "找选题", "抄结构"],
    "关键词抓取": ["gjcq", "关键词", "抓取", "搜索", "搜词", "找视频", "竞品词", "长尾词"],
    "文案仿写": ["wafx", "文案", "仿写", "改写", "脚本", "分镜", "提词", "ai写作", "伪原创", "口播稿", "标题"],
    "视频下载": ["spxz", "下载", "视频", "去水印", "保存", "素材", "搬运"],
    "视频号解析": ["sphjx", "视频号", "微信视频号", "weixin", "channels", "sph", "视频号链接", "解析视频号"],
    "短剧解析": ["djjx", "拆解", "分镜解析", "镜头解析", "剧本拆解", "分析短剧", "拉片", "剧本题"],
    "短剧成片": ["djcp", "短剧", "成片", "剪辑", "切片", "混剪", "竖屏", "自动剪辑", "剧集", "解说", "卡点"],
    "公众号运营": ["gzhyy", "公众号", "微信公众", "wechat mp", "mp.weixin", "原创文章", "历史文章", "阅读数", "搜一搜", "gh_"],
    "小红书笔记": ["xhsbj", "小红书", "rednote", "xiaohongshu", "xhs", "笔记提取", "笔记正文", "种草笔记", "全文", "联想词"],
    "竞品体检": ["jptj", "竞品", "体检", "对标", "同行", "对比", "看对手"],
    "评论区截流": ["plqjl", "评论", "截流", "挖客户", "意向", "问价", "私信", "求链接", "评论区", "留资"],
    "复盘监控": ["fpjk", "复盘", "监控", "涨粉", "掉粉", "粉丝曲线", "跟踪账号"],
    "数据趋势": ["sjqs", "趋势", "曲线", "历史", "涨跌", "看过的数据", "记录"],
    "直播监控": ["zbjk", "直播", "录屏", "录制", "直播间", "挂机", "录直播"],
    "GEO 监测": ["geo", "geo监测", "ai搜索", "deepseek", "豆包", "kimi", "ai提及", "可见度", "ai推荐", "品牌监测"],
    "报告": ["bg", "报告", "周报", "月报", "汇报", "总结", "excel报告"],
    "音视频转文本": ["ypzzwb", "转写", "转录", "字幕", "语音转文字", "whisper", "识别", "模型", "显卡"],
    "微信聊天整理": ["wxlc", "微信", "聊天记录", "合并转发", "聊天归档", "微信流", "wechat", "obsidian", "转笔记", "聊天导出"],
    "设置": ["sz", "设置", "授权码", "激活", "token", "密钥", "更新", "版本", "登录", "反馈", "日志"],
}
# >>> PAGE-REGISTRY END aliases <<<
_EXTRA_COMMANDS = {
    "使用教程": ["syjc", "教程", "帮助", "怎么用", "说明", "引导", "help", "上手"],
    "切换深色 / 浅色": ["qhss", "主题", "深色", "暗色", "夜间", "换肤", "浅色"],
}


def _match_score(query: str, name: str, aliases: list[str]) -> int:
    """给「查询词 vs 一个候选」打分。0 表示不匹配，越大越靠前。"""
    q = (query or "").strip().lower().replace(" ", "")
    if not q:
        return 1                       # 空查询：全列出，按原顺序
    n = name.strip().lower().replace(" ", "")
    if q == n:
        return 100
    if n.startswith(q):
        return 90
    if q in n:
        return 70
    for i, a in enumerate(aliases):
        a = (a or "").strip().lower().replace(" ", "")
        if not a:
            continue
        if q == a:
            return 80 - min(i, 9)      # 别名全等：越靠前的别名越准
        if a.startswith(q):
            return 60 - min(i, 9)
        if q in a:
            return 45 - min(i, 9)
    return 0


class _RowDelegate(QStyledItemDelegate):
    """列表行自绘：图标 + 页面名 + 右侧淡灰的分组名。

    ★ 为什么不用 setItemWidget（2026-10-17 踩过）
      QListWidget 放自定义 widget 时，行高由 item 的 sizeHint 决定；而为了
      让宽度跟随列表，sizeHint 的宽度通常写 0 —— 结果是在某些渲染路径下
      （离屏抓图就是）widget 完全不布局：列表里有 18 行、每行都是空白，
      但 count() 一切正常，属于"看着没坏其实全空"的典型。
      自绘 delegate 没有这层不确定性，也省掉 18 个 QWidget 的开销。

    QSS 对 delegate 不生效（paint 是我们自己画的），所以颜色按主题取字典值，
    和 widgets/empty.py 的做法一致。
    """

    # ★ v2（2026-11-16）：浅色档与 light.qss 对齐（主文 #12161F、
    #   装饰组名 #98A1AF、图标 #566070）；深色档保持原值。
    _PALETTE = {
        "light": {"sel": "#F2FCD9", "title": "#12161F", "group": "#98A1AF",
                  "glyph": "#566070", "glyph_sel": "#0B0E14"},
        "dark": {"sel": "#1B2410", "title": "#E6E9EF", "group": "#6B7480",
                 "glyph": "#9AA4B2", "glyph_sel": "#C8F751"},
    }

    def __init__(self, mode_fn, parent=None):
        super().__init__(parent)
        self._mode_fn = mode_fn

    def sizeHint(self, option, index):                         # noqa: N802
        return QSize(0, 36)

    def paint(self, painter, option, index):                   # noqa: N802
        mode = self._mode_fn()
        pal = self._PALETTE.get(mode, self._PALETTE["light"])
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect

        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, True)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(pal["sel"]))
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 8, 8)

        # 图标
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        x = rect.left() + 12
        if isinstance(icon, QIcon):
            icon.paint(painter, QRect(x, rect.top() + 9, 18, 18))
        x += 18 + 10

        # 右侧分组名（先量宽度，给标题留空间）
        group = index.data(Qt.ItemDataRole.UserRole + 1) or ""
        right_pad = rect.right() - 12
        if group:
            gf = QFont(self.parent().font() if self.parent() else QFont())
            gf.setPixelSize(11)
            painter.setFont(gf)
            painter.setPen(QColor(pal["group"]))
            gw = painter.fontMetrics().horizontalAdvance(group)
            painter.drawText(QRect(right_pad - gw, rect.top(), gw, rect.height()),
                             Qt.AlignmentFlag.AlignRight |
                             Qt.AlignmentFlag.AlignVCenter, group)
            right_pad -= gw + 12

        # 标题
        tf = QFont(self.parent().font() if self.parent() else QFont())
        tf.setPixelSize(13)
        painter.setFont(tf)
        painter.setPen(QColor(pal["title"]))
        text_rect = QRect(x, rect.top(), max(0, right_pad - x), rect.height())
        elided = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
            Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect,
                         Qt.AlignmentFlag.AlignLeft |
                         Qt.AlignmentFlag.AlignVCenter, elided)
        painter.restore()


class CommandPalette(QDialog):
    """按名字直达任意功能页的搜索面板。"""

    def __init__(self, parent, commands, on_pick):
        """
        commands: [(显示名, 所属分组, 别名列表, 图标名)]
        on_pick:  选中后回调，参数为显示名
        """
        super().__init__(parent)
        self._commands = list(commands)
        self._on_pick = on_pick
        self.setWindowTitle("功能搜索")
        self.setObjectName("CmdPalette")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(560)     # v2 原型面板宽 560（原 480 偏窄，右侧分组名挤）
        self._build_ui()
        self.apply_theme(_current_mode())

    # ------------------------------------------------------------ 构建
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        panel = QFrame()
        panel.setObjectName("CmdPanel")
        col = QVBoxLayout(panel)
        col.setContentsMargins(10, 10, 10, 8)
        col.setSpacing(8)

        self._edit = QLineEdit()
        self._edit.setObjectName("CmdInput")
        self._edit.setPlaceholderText("搜功能、跳页面……  试试「爆款」「下载」「geo」「sz」")
        # ★ v2（2026-11-16）：输入框左侧放大镜、右侧 Esc 键帽，跟原型一致。
        #   用 QLineEdit 的 leading / trailing action，不套一层容器 —— 那样
        #   CmdInput 的 QSS 边框、焦点态、选区色都得重写一遍。
        self._act_search = QAction(self)
        self._act_esc = QAction(self)
        self._edit.addAction(self._act_search, QLineEdit.ActionPosition.LeadingPosition)
        self._edit.addAction(self._act_esc, QLineEdit.ActionPosition.TrailingPosition)
        self._edit.textChanged.connect(self._refilter)
        self._edit.returnPressed.connect(self._pick_current)
        col.addWidget(self._edit)

        self._list = QListWidget()
        self._list.setObjectName("CmdList")
        self._list.setItemDelegate(_RowDelegate(_current_mode, self._list))
        self._list.itemClicked.connect(lambda it: self._pick(it))
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 行高交给 delegate 的 sizeHint；关掉交替行色，选中态由 delegate 自己画
        self._list.setUniformItemSizes(True)
        self._list.setAlternatingRowColors(False)
        col.addWidget(self._list, 1)

        foot = QLabel("↑↓ 选择　　回车 跳转　　Esc 关闭")
        foot.setObjectName("CmdFoot")
        col.addWidget(foot)

        outer.addWidget(panel)
        self._refilter()

    # ------------------------------------------------------------ 过滤
    def _refilter(self, *_a):
        query = self._edit.text()
        rows = []
        for idx, (name, group, aliases, icon_name) in enumerate(self._commands):
            score = _match_score(query, name, aliases)
            if score:
                # 第四位保留原始序号：空查询时按页面原顺序列（工作台在最前），
                # 不能按 score 之后的中文排序 —— 那会让「GEO 监测」跑到第一行，
                # 客户打开面板看到的第一条不是工作台，很奇怪。
                rows.append((score, idx, name, group, icon_name))
        rows.sort(key=lambda r: (-r[0], r[1]))

        self._list.clear()
        mode = _current_mode()
        for _score, _idx, name, group, icon_name in rows[:40]:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setData(Qt.ItemDataRole.UserRole + 1, group)
            item.setSizeHint(QSize(0, 36))
            try:
                item.setIcon(nav_icon(icon_name, _glyph_color(mode), 18))
            except Exception:                                   # noqa: BLE001
                pass
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    # ------------------------------------------------------------ 行为
    def keyPressEvent(self, event):                            # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            step = 1 if key == Qt.Key.Key_Down else -1
            cur = self._list.currentRow()
            n = self._list.count()
            if n:
                self._list.setCurrentRow((cur + step) % n)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def _pick_current(self):
        it = self._list.currentItem()
        if it is not None:
            self._pick(it)

    def _pick(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        try:
            if callable(self._on_pick) and name:
                self._on_pick(name)
        except Exception as exc:                                # noqa: BLE001
            print(f"[流量捕手] 命令面板跳转失败：{exc}")

    def apply_theme(self, mode: str = "light"):
        """主题切换后重建条目与图标（都是位图，不会自动变色）。"""
        self._act_search.setIcon(_search_glyph(mode))
        self._act_esc.setIcon(_keycap_icon(mode))
        self._refilter()

    def exec_at(self, window):
        """贴到窗口上方弹出（不是屏幕正中 —— 那样视线要跨一次全屏）。"""
        self.adjustSize()
        self.resize(max(self.width(), 560), min(self.height(), 460))
        if window is not None:
            geo = window.frameGeometry()
            x = geo.x() + max(0, (geo.width() - self.width()) // 2)
            y = geo.y() + max(60, int(geo.height() * 0.12))
            self.move(x, y)
        # 先把焦点给输入框（exec 之后焦点可能被抢占）
        QTimer.singleShot(0, self._edit.setFocus)
        self._edit.selectAll()
        return self.exec()


def build_commands(nav_groups, extra=None):
    """把 NAV_GROUPS 展开成命令面板需要的扁平列表。"""
    out = []
    for group, items in nav_groups:
        for name in items:
            out.append((name, group, _ALIASES.get(name, []), name))
    for name, aliases in (extra or _EXTRA_COMMANDS).items():
        # ★ v2：快捷操作也要带分组名 —— 原来传空串，deletage 右侧什么都不画，
        #   客户分不清这两条是「页面」还是「动作」。
        out.append((name, "快捷操作", aliases, "设置"))
    return out


def install_shortcut(window, palette_factory):
    """挂上 Ctrl+K（Mac 习惯下 Cmd+K 也走同一个组合）。"""
    sc = QShortcut(QKeySequence("Ctrl+K"), window)
    sc.setContext(Qt.ShortcutContext.WindowShortcut)
    sc.activated.connect(lambda: palette_factory())
    return sc


__all__ = ["CommandPalette", "build_commands", "install_shortcut",
           "_ALIASES", "_match_score"]
