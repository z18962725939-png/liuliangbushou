"""工作台首页：一打开就知道现在什么状态、下一步该干什么。

★ 为什么要有这一页（2026-10-14 体验重构）：
  改版前打开软件默认停在通用接口调试页——那是给开发者用的页面
  （选分类 → 选平台 → 选接口 → 手填参数），客户第一眼看到的是一堆
  自己看不懂的下拉框，第一印象就是"这软件好难"。

★ 2026-11-23 四次改版（不换框架的视觉语言精修）
  客户反馈「界面太丑」。查证结论：瓶颈不是缺组件库（现有 QSS 已有完整
  双主题体系），而是视觉语言——首页像功能清单、字号层级偏平、选中态发灰。
  这次只改能一眼看出的四处：页头加场景标签、三步链扩成完整五步运营任务流、
  新功能卡挂「新」角标、浅色导航选中改柠檬指示条。**不装新库、不动页面结构。**

★ 2026-11-16 三次改版（按分享链接里的《v2 界面重构方案》落地）
  方案的核心判断：首页不该是一条「设置项一样的长列表」。现在把高频功能区
  改成 3 列 × 2 行功能宫格，每块卡自带图标块 + 标题 + 说明，hover 时边框
  转柠檬；上方的状态面板（驾驶舱）与三步引导保留 —— 一屏里三种信息形状
  （数据 / 流程 / 入口）各司其职，眼睛有落点。配色同步换成 v2 浅色对比度体系。

★ 2026-10-17 二次改版（客户反馈「首页看起来怪怪的、不够整洁」）
  问题不在配色，在**结构**：原版是「三张指标卡 + 三张步骤卡 + 六张功能卡」，
  九块一样大、一样白、一样圆角的卡堆在一起，眼睛没有落点；侧栏还是实心高亮条，
  整屏像被荧光笔划过。现在按苹果系统 / App Store 的信息层级重排：

    1. 页面标题（26px）＋ 一句问候，右上角一个「搜索功能」胶囊
    2. 一块状态面板：三格数据 + 一条额度进度（不再是三张独立卡）
    3. 三步流程：序号圆点 + 两行字，放在一块面板里，用 hairline 分列
    4. 天天要用的功能：**一行一条**的列表（图标 + 名称 + 说明 + 箭头）
    5. 其它工具：一排淡胶囊按钮
  一条原则：一屏里只允许「一块面」有边框，其余靠留白和 hairline 分。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ._base import connect_guarded, page_title
from gui_qt.icons import nav_icon
from gui_qt.widgets.kit import DisclosurePanel, NodeTimeline
from gui_qt.widgets.stat import (ActionCard, BigNumber, GlowPanel, StatCard,
                                 StepFlow, UsageBar, palette)

# 常用功能（工作台主区）：名称 → (一句话说明, 角标)
QUICK_ACTIONS = [
    ("选题挖掘", "找低粉高赞的爆款，今天发什么看它", ""),
    ("关键词抓取", "一个词，把这个赛道数据最好的视频抓全", ""),
    ("文案仿写", "粘链接就能扒出文案，按体裁仿写成能拍的分镜", "最常用"),
    ("视频下载", "批量下载无水印视频，留着做素材", ""),
    ("视频号解析", "粘视频号链接，一键出作者和直链", ""),
    ("竞品体检", "看同行最近在发什么、哪条跑起来了", ""),
    ("评论区截流", "挖同行评论区里正在问怎么买的人", ""),
    ("公众号运营", "搜公众号、拉历史文章，查阅读点赞和选题", "新"),
    ("小红书笔记", "搜笔记、读全文正文，导出 Markdown 做选题", "新"),
]

# 五步运营任务流：从"不知道发什么"到"手里有一条能发的成片"
# ★ 2026-11-23：三步链扩成完整链路（找选题 → 扒文案 → 仿写 → 下载 → 成片），
#   客户一眼能看到「这条软件是干什么的、我现在走到哪一步」。
# ★ 说明文字 ≤10 汉字：五步时每格只有约 200px，扣掉序号 44px 内边距，
#   正文只剩 ~140px；超过就会掉字，第二步和第三步高低不齐，首页又变"不整洁"。
# ★ 免费版没有工作台页，这五步的跳转目标对免费版零影响。
MAIN_FLOW = [
    ("01", "找选题", "低粉高赞爆款", "选题挖掘"),
    ("02", "扒文案", "粘链接出口播稿", "文案仿写"),
    ("03", "写同款", "选体裁出分镜", "文案仿写"),
    ("04", "存素材", "原画质进本地", "视频下载"),
    ("05", "剪成片", "自动分集出片", "短剧成片"),
]

# 次要功能：收在这里，不再占用主导航的位置
MORE_TOOLS = [
    "直播监控", "GEO 监测", "复盘监控", "数据趋势",
    "报告", "音视频转文本", "设置",
]

# 内容最大宽度：苹果的正文区会被约束在 ~980–1100px，超宽屏靠留白吸收。
# 不约束的话，21:9 显示器上一行说明会拉成 1800px，读起来很累、也不像产品。
_MAX_CONTENT_WIDTH = 1040


def _hsep() -> QFrame:
    f = QFrame()
    f.setObjectName("HSep")
    f.setFixedHeight(1)
    return f


def _vsep() -> QFrame:
    f = QFrame()
    f.setObjectName("VLine")
    f.setFixedWidth(1)
    return f


def _section(text: str) -> QLabel:
    """分区标题：16px/600（H2）。比卡片标题大一档，比页面标题小一档。"""
    t = QLabel(text)
    t.setObjectName("H2")
    return t


class _SearchPill(QFrame):
    """右上角「搜索功能 + Ctrl K 键帽」胶囊。

    ★ 为什么不用一个纯文字按钮（2026-10-17 二次改版）
      原版就是一行 "搜索功能    Ctrl K"，中间用四个空格对齐 —— 截图里两个词
      挨在一起、Ctrl K 还比标题淡一档，看着像写错的内测字样。
      现在的做法跟 Raycast / Linear 一致：左边功能名，右边一个**键帽**
      （细边框 + 小圆角，本身就是"这是个快捷键"的通用图示）。
    """

    clicked = Signal()

    def __init__(self, mode: str = "light", parent=None):
        super().__init__(parent)
        self._mode = mode
        self._hover = False
        self.setObjectName("SearchPill")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("按 Ctrl+K 也可以随时唤起")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 8, 6)
        h.setSpacing(8)
        self._text = QLabel("搜索功能")
        self._key = QLabel("Ctrl K")
        self._key.setObjectName("KeyCap")
        h.addWidget(self._text)
        h.addWidget(self._key)
        self.apply_theme(mode)

    def mouseReleaseEvent(self, e):                          # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def enterEvent(self, e):                                 # noqa: N802
        self._hover = True
        self.apply_theme(self._mode)
        super().enterEvent(e)

    def leaveEvent(self, e):                                 # noqa: N802
        self._hover = False
        self.apply_theme(self._mode)
        super().leaveEvent(e)

    def apply_theme(self, mode: str):
        self._mode = mode
        p = palette(mode)
        bg = p["card_hover"] if self._hover else "transparent"
        bd = p["border_hover"] if self._hover else p["border"]
        self.setStyleSheet(
            f"#SearchPill{{background:{bg};border:1px solid {bd};"
            f"border-radius:15px;}}")
        self._text.setStyleSheet(
            f"color:{p['sub']};font-size:11px;background:transparent;border:none;")
        # 键帽：底比胶囊再深一档、边框淡一档，让它像一颗真的键
        cap_bg = p["track"]
        self._key.setStyleSheet(
            f"color:{p['sub']};font-size:10px;background:{cap_bg};"
            f"border:1px solid {p['border']};border-radius:4px;"
            f"padding:1px 5px;")


class HomePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._mode = "light"
        self._cards: list[ActionCard] = []
        self._card_pairs: list[tuple[ActionCard, str]] = []   # 换主题时按名字重取图标
        self._stat_cards: list[StatCard] = []
        # ★ 2026-11-16 P2：轻量作业簿。读失败会降级为空簿，绝不让作业簿坏掉
        #   拖垮首页构建（_base 的 AppContext 也可能在演示模式下没有数据目录）。
        from gui_qt import taskflow
        self._tf = taskflow
        self._book = taskflow.load()
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 30, 32, 36)
        outer.setSpacing(0)
        self._outer = outer

        # ★ 内容宽度约束：不用两侧 stretch 包夹（那样内容只会拿到 sizeHint 宽度，
        #   截图里实测只有 679px，右边空一大片）。改成 resizeEvent 里按可用宽度
        #   算左右留白 —— 窄屏时贴边铺满，宽屏时最多 1040 并居中。
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Preferred)
        self._content = content
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)

        # ---------- ① 页头 ----------
        head = QHBoxLayout()
        head.setSpacing(16)
        title_col = QVBoxLayout()
        title_col.setSpacing(6)
        # ★ 场景标签（v3）：12px 淡字放在 26px 标题上方，先给眼睛一个落点，
        #   再读标题——和概念稿一致。不新增字号档位，用的还是既有 Meta 语义类。
        self.scene_lab = QLabel("工作台 · 今日概览")
        self.scene_lab.setObjectName("Meta")
        title_col.addWidget(self.scene_lab)
        self.title_lab = page_title("工作台")
        title_col.addWidget(self.title_lab)
        self.sub = QLabel("")
        self.sub.setObjectName("Caption")
        self.sub.setWordWrap(True)
        title_col.addWidget(self.sub)
        head.addLayout(title_col, 1)
        head.addWidget(self._search_pill(), 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        # ---------- ② 状态面板 ----------
        # ★ 2026-10-27：换成 GlowPanel（自绘 + 右上角柠檬微光）。
        #   它是全页唯一的"主角面"，其余面板保持纯白 —— 全靠对比，不靠堆装饰。
        panel = GlowPanel(self._mode)
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(22, 18, 22, 16)
        pv.setSpacing(14)

        # ★ 2026-10-27：三格从 StatCard 换成 BigNumber（数字 32px）。
        #   额度格带一条近 7 天的迷你趋势线 —— 这是「卡片灵动感」的落点
        #   （见 stat.py 里 SparkLine 的说明）。授权/登录是文本状态，
        #   BigNumber 会自动把非数字降级到 20px，不会出现 32px 的"已激活"。
        metrics = QHBoxLayout()
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setSpacing(22)
        self.lic_card = BigNumber("授权状态", "—", mode=self._mode)
        self.quota_card = BigNumber("今日额度", "—", mode=self._mode, spark=[])
        self.login_card = BigNumber("抖音登录", "—", mode=self._mode)
        self._stat_cards = [self.lic_card, self.quota_card, self.login_card]
        for i, c in enumerate(self._stat_cards):
            if i:
                metrics.addWidget(_vsep())
            metrics.addWidget(c, 1)
        pv.addLayout(metrics)
        pv.addWidget(_hsep())

        self.usage = UsageBar()
        pv.addWidget(self.usage)
        pv.addWidget(_hsep())

        # ---------- 上手检查（可展开） ----------
        # ★ 2026-10-17 三次改版：原来这里是常驻的一行提示文字，
        #   一切正常时也占着一行、出了问题时又只有一句干巴巴的警告。
        #   现在改成「默认只显示结论，点开才看三条检查项」——
        #   和寄件页「点追踪才展开路线」是同一个思路：页面不必一直堆满内容，
        #   但信息一直都在，只是折叠起来了。展开时三个节点依次亮起，
        #   客户能看见"我离能用还差哪一步"。
        self.checker = DisclosurePanel("上手检查", "全部正常", self._mode, on_expand=self._play_check)
        self.timeline = NodeTimeline(self._mode)
        self.checker.add_widget(self.timeline)
        pv.addWidget(self.checker)
        root.addWidget(panel)
        self._panel_status = panel

        # ---------- ③ 主流程三步 ----------
        root.addSpacing(4)
        root.addWidget(_section("运营任务流 · 一条链路走完"))
        flow_panel = QFrame()
        flow_panel.setObjectName("Panel")
        fv = QVBoxLayout(flow_panel)
        fv.setContentsMargins(8, 6, 8, 6)
        fv.setSpacing(0)
        self.flow = StepFlow(
            [(n, t, d, (lambda page=p: self._goto(page))) for n, t, d, p in MAIN_FLOW],
            self._mode)
        fv.addWidget(self.flow)
        root.addWidget(flow_panel)
        self._panel_flow = flow_panel

        # ---------- ③b 当前作业（P2 任务流） ----------
        # ★ 为什么放在三步引导正下方：三步是「第一次用怎么办」，作业卡是
        #   「我上次做到哪了」。两者都是回答"现在该干什么"，挨在一起才顺；
        #   又都在功能宫格上面，保证一屏里先看到流程、再看到入口。
        #   不新增导航页：它只是工作台里的一块面板，免费版没有工作台页，
        #   所以这里对免费版零影响。
        root.addSpacing(2)
        job_panel = QFrame()
        job_panel.setObjectName("Panel")
        jv = QVBoxLayout(job_panel)
        jv.setContentsMargins(16, 12, 16, 12)
        jv.setSpacing(9)

        jhead = QHBoxLayout()
        jhead.setSpacing(10)
        jt = QLabel("当前作业")
        jt.setObjectName("SectionLabel")
        jhead.addWidget(jt)
        self.job_progress = QLabel("尚无作业")
        self.job_progress.setObjectName("Meta")
        jhead.addWidget(self.job_progress)
        jhead.addStretch()
        jv.addLayout(jhead)

        self.job_title = QLabel("把一件视频从选题做到成片，进度记在这里，关掉软件也不丢。")
        self.job_title.setObjectName("Caption")
        self.job_title.setWordWrap(True)
        jv.addWidget(self.job_title)

        # 六步进度点：用等宽字符拼，靠颜色区分完成/未完成，不引入新 QSS 选择器
        self.job_dots = QLabel("")
        self.job_dots.setObjectName("Meta")
        jv.addWidget(self.job_dots)

        jbtns = QHBoxLayout()
        jbtns.setSpacing(8)
        self.job_next_btn = QPushButton("新建作业")
        self.job_next_btn.setObjectName("Primary")
        self.job_next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_guarded(self.job_next_btn.clicked,
                        lambda *_: self._on_job_primary(), label="作业主操作")
        jbtns.addWidget(self.job_next_btn)

        self.job_done_btn = QPushButton("完成本步")
        self.job_done_btn.setObjectName("ToolPill")
        self.job_done_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_guarded(self.job_done_btn.clicked,
                        lambda *_: self._on_job_done(), label="完成本步")
        jbtns.addWidget(self.job_done_btn)

        for text, slot in (("归位产物", self._on_job_collect),
                           ("切换", self._on_job_switch),
                           ("清单", self._on_job_copy),
                           ("删除", self._on_job_delete)):
            b = QPushButton(text)
            b.setObjectName("ToolPill")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            connect_guarded(b.clicked, lambda *_cb, s=slot: s(), label=text)
            jbtns.addWidget(b)
        jbtns.addStretch()
        jv.addLayout(jbtns)
        root.addWidget(job_panel)
        self._panel_job = job_panel

        # ---------- ④ 天天要用的功能（v2：3 列功能宫格） ----------
        root.addSpacing(4)
        root.addWidget(_section("天天要用的功能"))
        # ★ 2026-11-16 v2 重构：一行一条的长列表 → 3 列 2 行宫格。
        #   列表把 6 个高频功能压成 6 条细行，一屏读下来像设置项；
        #   宫格让每个功能有一块「面」，图标 + 说明一眼可达，也和上面的
        #   状态面板、三步引导拉开信息形状。原型里 hover 是上浮 3px，
        #   Qt 里动位置会挤动布局，改为「柠檬描边 + 底色微提亮」等价表达。
        grid = QWidget()
        grid.setStyleSheet("background:transparent;")
        grid.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(14)
        for i, (name, desc, badge) in enumerate(QUICK_ACTIONS):
            # 图标染成能量色（浅色 #C4F13E / 深色 #C8F751），落在柠檬浅底上
            card = ActionCard(nav_icon(name, palette(self._mode)["acid"], 24),
                              name, desc, self._mode, badge, vertical=True)
            card.clicked.connect(lambda _c=False, n=name: self._goto(n))
            gl.addWidget(card, i // 3, i % 3)
            self._cards.append(card)
            self._card_pairs.append((card, name))
        for col in range(3):
            gl.setColumnStretch(col, 1)
        root.addWidget(grid)
        self._panel_grid = grid

        # ---------- ⑤ 其它工具 ----------
        root.addSpacing(4)
        tools_head = QHBoxLayout()
        tools_head.setSpacing(10)
        lab = QLabel("其它工具")
        lab.setObjectName("Meta")
        tools_head.addWidget(lab)
        tools_head.addStretch()
        root.addLayout(tools_head)

        self.more_row = QHBoxLayout()
        self.more_row.setSpacing(8)
        for name in MORE_TOOLS:
            b = QPushButton(name)
            b.setObjectName("ToolPill")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, n=name: self._goto(n))
            self.more_row.addWidget(b)
        self.more_row.addStretch()
        root.addLayout(self.more_row)

        root.addStretch()
        outer.addWidget(content)

    def resizeEvent(self, e):                                # noqa: N802
        """宽屏时把内容居中：可用宽度超过 1040 就把差值的一半变成左右留白。"""
        super().resizeEvent(e)
        avail = max(0, self.width())
        side = 32 if avail <= _MAX_CONTENT_WIDTH + 64 \
            else int((avail - _MAX_CONTENT_WIDTH) / 2)
        m = self._outer.contentsMargins()
        if m.left() != side:
            self._outer.setContentsMargins(side, m.top(), side, m.bottom())

    def _search_pill(self) -> _SearchPill:
        """右上角「搜索功能」胶囊：和 Ctrl+K 走同一个入口。

        ★ 为什么要放一个可见的入口：Ctrl+K 只有知道的人才会按。
          客户不会读更新日志，但会看见右上角有个能点的东西。
        """
        pill = _SearchPill(self._mode)
        pill.clicked.connect(self._open_palette)
        self._pill = pill
        return pill

    def _open_palette(self):
        win = getattr(self.ctx, "main_window", None)
        opener = getattr(win, "_open_palette", None)
        if callable(opener):
            try:
                opener()
            except Exception:                                # noqa: BLE001
                pass

    # ---------------------------------------------------------------- 跳转
    def _goto(self, name: str):
        """切到指定页面。拿不到主窗口时静默失败（不该因为跳转崩首页）。"""
        win = getattr(self.ctx, "main_window", None)
        if win is not None and hasattr(win, "switch_to"):
            win.switch_to(name)

    # ---------------------------------------------------------------- 作业
    def _save_book(self):
        if not self._tf.save(self._book):
            self.job_title.setText("作业保存失败（磁盘不可写？本次进度只在内存里）。")

    def _refresh_job(self):
        """把作业簿状态画到卡片上。无作业 / 有作业两条路径都不抛异常。"""
        tf = self._tf
        job = tf.active_job(self._book)
        p = palette(self._mode)
        if job is None:
            self.job_title.setText(
                "把一件视频从选题做到成片，进度记在这里，关掉软件也不丢。")
            self.job_progress.setText("尚无作业")
            self.job_dots.setText("")
            self.job_next_btn.setText("＋ 新建作业")
            self.job_done_btn.setEnabled(False)
            return

        done, total, pct = tf.progress(job)
        self.job_title.setText(job.get("title") or "未命名作业")
        self.job_progress.setText(f"{done}/{total} · {pct}%")
        # 已完成用能量色，未完成用弱化灰；两段内联样式随主题重刷
        on = "●" * done
        off = "○" * (total - done)
        self.job_dots.setText(f"{on}{off}")
        self.job_dots.setStyleSheet(
            f"color:{p['accent']};font-size:15px;letter-spacing:3px;")
        nxt = tf.next_step(job)
        self.job_next_btn.setText(
            f"下一步：{nxt['title']}" if nxt else "六步已完成")
        self.job_done_btn.setEnabled(bool(nxt))

    def _on_job_primary(self):
        """主按钮：有未完成步骤→跳到该步页面；全完成或没作业→新建。"""
        tf = self._tf
        job = tf.active_job(self._book)
        nxt = tf.next_step(job)
        if job is not None and nxt is None:
            self._new_job()
            return
        if nxt is None:
            self._new_job()
            return
        self._goto(nxt["page"])

    def _new_job(self):
        title, ok = QInputDialog.getText(
            self, "新建作业", "这次要做什么？（例：按摩棒工厂 11 月选题）")
        if not ok:
            return
        job = self._tf.create(self._book, title)
        # 新建时拍一份当前各页保存目录的快照，供日后切回来复用
        self._tf.snapshot_dirs(self._book, job["id"], self._ctx_dirs())
        self._save_book()
        self._refresh_job()

    def _ctx_dirs(self) -> dict:
        """从 AppContext 现成的设置键取各页保存目录（不碰任何页面控件）。"""
        s = getattr(self.ctx, "settings", {}) or {}
        return {k: s.get(k, "") for k in self._tf.DIR_ROLE}

    def _on_job_collect(self):
        """把各页保存目录里最近新增的产物，按角色归位到当前作业对应步骤。"""
        job = self._tf.active_job(self._book)
        if not job:
            self.job_title.setText("先新建一个作业，再归位产物。")
            return
        ctx_dirs = self._ctx_dirs()
        # 也把下载器当前实际用的目录算进去（转录页的扫描就认这个）
        dl = getattr(getattr(self.ctx, "downloader", None), "save_dir", None)
        if dl is not None:
            ctx_dirs["dl_save_dir"] = str(dl) or ctx_dirs.get("dl_save_dir", "")
        r = self._tf.scan_artifacts(self._book, job["id"], ctx_dirs)
        self._tf.snapshot_dirs(self._book, job["id"], ctx_dirs)
        self._save_book()
        self._refresh_job()
        if r["attached"]:
            self.job_title.setText(
                f"已归位 {r['attached']} 个产物到对应步骤（原文件没动）。")
        else:
            self.job_title.setText(
                "最近 48 小时没有新产物可归位；下载或转录后点这里即可。")

    def _on_job_done(self):
        job = self._tf.active_job(self._book)
        nxt = self._tf.next_step(job)
        if not job or not nxt:
            return
        self._tf.mark_step(self._book, job["id"], nxt["key"], done=True)
        self._save_book()
        self._refresh_job()

    def _on_job_switch(self):
        jobs = self._book.get("jobs", [])
        if not jobs:
            self._new_job()
            return
        names = [f"{j.get('title')}  ({self._tf.progress(j)[0]}/6)" for j in jobs]
        picked, ok = QInputDialog.getItem(
            self, "切换作业", "选一个作业：", names, 0, False)
        if not ok:
            return
        idx = names.index(picked) if picked in names else -1
        if idx >= 0:
            self._tf.set_active(self._book, jobs[idx]["id"])
            self._apply_job_dirs(jobs[idx]["id"])
            self._save_book()
            self._refresh_job()

    def _apply_job_dirs(self, job_id: str):
        """把作业的目录快照写回 ctx，让下游页的保存/扫描指到本次作业。

        ★ 只写「当前没有值」的键：客户在设置页手填的目录优先，作业快照
          只在空着时补位，避免切换作业把客户刚改的设置冲掉。
        ★ 转录页的 _scan() 读 ctx.downloader.save_dir，所以下载目录快照
          也同步到下载器实例上，这条跨页链路才算真通。
        """
        dirs = self._tf.apply_dirs(self._book, job_id)
        if not dirs:
            return
        s = getattr(self.ctx, "settings", None)
        if s is None:
            return
        for k, v in dirs.items():
            if not str(s.get(k) or "").strip():
                s[k] = v
        # 下载器实例（若已就绪）也跟着指过去
        dl = getattr(self.ctx, "downloader", None)
        dl_dir = dirs.get("dl_save_dir")
        if dl is not None and dl_dir:
            try:
                from pathlib import Path
                dl.save_dir = Path(dl_dir)
            except Exception:                                  # noqa: BLE001
                pass

    def _on_job_copy(self):
        job = self._tf.active_job(self._book)
        text = self._tf.render_summary(job)
        try:
            QApplication.clipboard().setText(text)
            self.job_title.setText("作业清单已复制，可直接发给同事或贴进笔记。")
        except Exception:                                      # noqa: BLE001
            self.job_title.setText(text.splitlines()[0] if text else "")

    def _on_job_delete(self):
        job = self._tf.active_job(self._book)
        if not job:
            return
        self._tf.delete_job(self._book, job["id"])
        self._save_book()
        self._refresh_job()

    # ---------------------------------------------------------------- 入场
    def entrance_order(self):
        """切到本页时按阅读顺序依次浮现的几块（编排式入场）。

        ★ 依据 tasteskill《motion》：入场要"编排"，不是一整块一起亮 ——
          先标题、再状态、再流程、最后列表，间隔 70ms（它给的区间是
          60~90ms：再快就看成同时亮，再慢就变成一个一个往外蹦）。
        ★ 只排 4 个：再多的队列就是那种 AI 味的瀑布流入场。
        ★ 其它页面没有这个方法时，主窗口退回整页淡入。
        """
        return [w for w in (getattr(self, "title_lab", None),
                            getattr(self, "_panel_status", None),
                            getattr(self, "_panel_flow", None),
                            getattr(self, "_panel_grid", None)) if w is not None]

    # ---------------------------------------------------------------- 刷新
    def refresh(self):
        """刷新授权 / 额度 / 登录状态。每次切回本页都会重跑。"""
        p = palette(self._mode)
        self.sub.setText(self._greeting())

        # ---- 授权 ----
        st = {}
        try:
            from justoneapi_collector import licensing
            st = licensing.status() or {}
        except Exception:                                    # noqa: BLE001
            st = {}
        state = st.get("state") or "none"
        if state == "licensed":
            days = st.get("days_left")
            tone = "normal" if (days is None or days > 3) else "warn"
            self.lic_card.set_value(
                "已激活", sub=st.get("label") or "授权有效", tone=tone)
        elif state == "trial":
            days = st.get("days_left")
            # ★ 2026-11-16 v2：BigNumber.set_value 的位置参数是
            #   (value, unit, sub, tone)。这里原来把「剩 N 天」当 unit、
            #   把 "warn" 当 sub —— 截图里授权格直接冒出一行 "normal"，
            #   客户会以为是报错。授权状态没有单位，说明一律走 sub。
            self.lic_card.set_value(
                "试用期",
                sub=f"剩 {days:.0f} 天 · 到期后需激活" if days else "试用中",
                tone="warn")
        elif state == "free":
            # 免费版是一个能正常干活的档位 —— 用 ok 色，别标成"未激活"吓走人。
            # 副标题直接写清可用边界，避免客户点进采集页才发现用不了。
            self.lic_card.set_value(
                "免费版", sub="下载 · 录制 永久可用，其余需激活", tone="normal")
        elif state == "expired":
            self.lic_card.set_value(
                "已过期", sub=f"{st.get('label') or '授权已到期'} · 请联系服务方续费",
                tone="bad")
        else:
            self.lic_card.set_value(
                "未激活", sub="需激活授权码 · 到「设置 → 授权与激活」粘贴",
                tone="bad")

        # ---- 额度 ----
        used = limit = 0
        unlimited = False
        try:
            from justoneapi_collector import quota
            s = quota.summary() or {}
            used = int(s.get("used") or 0)
            limit = int(s.get("limit") or 0)
            unlimited = bool(s.get("unlimited"))
        except Exception:                                    # noqa: BLE001
            pass
        # 近 7 天用量（正序，末项是今天）—— 额度格的迷你趋势线用它
        try:
            hist = list((s or {}).get("history") or [])
        except Exception:                                    # noqa: BLE001
            hist = []
        # ★ 迷你线要"够形状"才画：至少 3 天有量、且数值有起伏。
        #   第一天客户的 7 天历史是 [0,0,0,0,0,0,3]，画出来是一条贴底的
        #   直线加末端陡升 —— 那不像"趋势"，像加载失败的折线图。
        nonzero = sum(1 for v in hist if v)
        flat = (not hist) or (max(hist) - min(hist) == 0)
        spark = hist if (nonzero >= 3 and not flat) else None
        # ★ 2026-10-27：「不限量」这三个字也要让位给数字。
        #   原来这一格在不限量档一直写着「不限量」—— 一枚 32px 的位置被
        #   三个汉字占着，客户天天看也看不出今天到底用了多少。
        #   现在同一格改叫「今日已用」，数字是真实的次数。
        if state == "free":
            # 免费功能走零成本链路、不经过额度统计，显示次数反而误导。
            # ★ 必须传 unlimited=True：传 (0, 0, False) 会走「额度为 0」分支，
            #   免费用户一打开首页就看到一条**满格红条 + 今日额度为 0**，
            #   明明能正常下载却像欠费了。
            # ★ v1.20.0：文案从「不计额度」改成「不限量」—— 免费档现在就是
            #   「下载 + 录制随便用」，说「不计额度」反而像在含糊其辞。
            free_used = 0
            try:
                free_used = int(quota.free_used_today())
            except Exception:                                # noqa: BLE001
                free_used = 0
            self.quota_card.set_title("今日已用")
            self.quota_card.set_value(
                f"{free_used}", unit="次",
                sub="下载与录制免费不限次数", tone="normal", spark=spark)
            self.usage.set_usage(0, 0, True, "免费版",
                                 note="下载 · 录制 不限量")
            self.usage.apply_theme(self._mode)
        elif unlimited:
            self.quota_card.set_title("今日已用")
            self.quota_card.set_value(
                f"{used}", unit="次", sub="不限量 · 随便用",
                tone="normal", spark=spark)
        else:
            left = max(0, limit - used)
            tone = "normal" if left > limit * 0.3 else "warn" if left > 0 else "bad"
            self.quota_card.set_title("今日额度")
            self.quota_card.set_value(
                f"{left}", unit="次",
                sub=f"今日已用 {used} 次 · 每天 0 点重置",
                tone=tone, spark=spark)
        if state != "free":      # 免费版上面已单独渲染，别再被覆盖回去
            self.usage.set_usage(used, limit, unlimited, "今日额度")
            self.usage.apply_theme(self._mode)

        # ---- 登录 ----
        cookie = str(self.ctx.settings.get("douyin_cookie") or "")
        logged = ("sessionid" in cookie) or ("sid_tt" in cookie)
        if logged:
            self.login_card.set_value(
                "已登录", sub="抓取走免费通道，额度消耗更慢", tone="normal")
        else:
            self.login_card.set_value(
                "未登录", sub="抓取走付费接口，建议登录后用", tone="warn")

        # ---- 上手检查（三节点） ----
        # 三个节点分别回答「能不能用 / 抓得贵不贵 / 今天还剩多少」。
        # 顺序不能换：先授权（否则什么都干不了），再登录（决定成本），
        # 最后额度（决定今天能跑多少）—— 这也是客户上手时的真实顺序。
        nodes = []
        if state == "licensed":
            nodes.append({"label": "授权", "note": "已激活", "state": "done"})
        elif state == "trial":
            days = st.get("days_left")
            nodes.append({"label": "授权",
                          "note": f"试用剩 {days:.0f} 天" if days else "试用中",
                          "state": "warn"})
        elif state == "free":
            nodes.append({"label": "授权", "note": "免费版可用", "state": "done"})
        else:
            nodes.append({"label": "授权", "note": "未激活", "state": "bad"})

        if logged:
            nodes.append({"label": "抖音登录", "note": "走免费通道", "state": "done"})
        else:
            nodes.append({"label": "抖音登录", "note": "走付费接口", "state": "warn"})

        if state == "free":
            nodes.append({"label": "今日额度", "note": "不计额度", "state": "done"})
        elif unlimited:
            nodes.append({"label": "今日额度", "note": f"已用 {used} 次", "state": "done"})
        elif limit > 0 and used < limit:
            left = limit - used
            nodes.append({"label": "今日额度", "note": f"剩 {left} 次",
                          "state": "done" if left > limit * 0.3 else "warn"})
        else:
            nodes.append({"label": "今日额度", "note": "已用完", "state": "bad"})

        self.timeline.set_nodes(nodes)
        pending = sum(1 for n in nodes if n["state"] not in ("done",))
        self.checker.set_meta("全部正常" if pending == 0 else f"{pending} 项待处理")

        hints = []
        if state == "free":
            # 先说能用的，再说要激活的 —— 上来就一句"用不了"会让客户以为软件坏了
            hints.append("免费版：视频下载和直播录制可以一直用、不限次数；"
                         "采集、截流、仿写需激活授权码。")
        elif state in ("none", "expired"):
            hints.append("还没激活授权码，采集功能暂时用不了：到「设置 → 授权与激活」粘贴。")
        if not logged and state == "licensed":
            hints.append("没登录抖音：作品抓取会走付费接口，同样的数据多花额度。")
        if not unlimited and limit > 0 and used >= limit:
            hints.append("今日额度已用完，明天 16:00 自动重置。")
        self.checker.setToolTip(" · ".join(hints) if hints else "三项都正常，直接点下面的功能开工。")
        self._style_check_pill(pending)
        # P2：作业卡每次刷新都重画（含切主题后的进度点颜色）
        if hasattr(self, "job_dots"):
            self._refresh_job()

    def _style_check_pill(self, pending: int):
        """v2：把「上手检查」的结论做成状态胶囊（绿=全正常 / 琥珀=待处理）。

        ★ 为什么不改 kit.py：DisclosurePanel 被多个页面共用，而「结论胶囊」
          是工作台驾驶舱的语义特例。这里只接管本页这一颗 meta 标签，
          取不到就静默退回原来的纯文字，不牵动其它页面。
        ★ apply_theme() 末尾会重跑 refresh()，所以切主题后 kit 重刷过
          内联样式也会被这里重新清掉，胶囊不会「切一次深色就消失」。
        """
        head = getattr(self.checker, "_head", None)
        meta = getattr(head, "_meta", None)
        if meta is None:
            return
        meta.setObjectName("StatusPill")
        meta.setProperty("tone", "ok" if pending == 0 else "warn")
        meta.setStyleSheet("")          # 清掉 kit 的内联色，交给 QSS 语义类
        st = meta.style()
        st.unpolish(meta)               # 属性选择器改了 tone 必须重新 polish
        st.polish(meta)
        meta.update()

    def _play_check(self):
        """展开「上手检查」时让三个节点依次亮起。

        ★ 只在展开时播：如果常驻播放，页面每刷一次额度就闪一遍，
          那是干扰。动画应该由"用户刚做了个动作"触发，而不是由状态轮询触发。
        """
        self.timeline.play()

    @staticmethod
    def _greeting() -> str:
        """根据时间给一句人话开场，比"就绪"两个字有温度。"""
        from datetime import datetime
        h = datetime.now().hour
        if h < 6:
            return "这么晚还在忙？先看一眼额度和授权，再开工。"
        if h < 11:
            return "早上好。先找选题，再扒文案，一天的内容就有了。"
        if h < 14:
            return "中午好。数据最好的时段通常在傍晚，现在备稿正好。"
        if h < 18:
            return "下午好。扒两条对标爆款，晚上发正好赶上流量高峰。"
        return "晚上好。看看今天的选题，顺手把明天的稿子也备出来。"

    # ---------------------------------------------------------------- 主题
    def apply_theme(self, mode: str):
        self._mode = mode
        acid = palette(mode)["acid"]
        if getattr(self, "_pill", None) is not None:
            self._pill.apply_theme(mode)
        # ★ 2026-11-10 客户「切深色，工作台右侧颜色不一致」：
        #   这里原来读的是 _PANEL_STATUS（全大写），而 __init__ 里存的是
        #   _panel_status（小写）⇒ getattr 永远拿到 None ⇒ 整块 GlowPanel
        #   从来没刷过主题，深色下仍是构造时的白底，子控件却已切成浅色字，
        #   于是变成一块「白底白字」的瞎眼面板。属性名必须和构造处一致。
        for panel in (getattr(self, "_panel_status", None),):
            if panel is not None and hasattr(panel, "apply_theme"):
                panel.apply_theme(mode)
        for c in self._stat_cards:
            c.apply_theme(mode)
        for c, name in self._card_pairs:
            c.set_icon(nav_icon(name, acid, 24))     # 图标是位图，得手动换色
            c.apply_theme(mode)
        self.usage.apply_theme(mode)
        self.flow.apply_theme(mode)
        self.checker.apply_theme(mode)
        self.timeline.apply_theme(mode)
        # 分隔线是内联色（QSS 的 #HSep 只在首次套主题时生效，
        # 页面内联样式优先级更高），跟着主题重刷一遍
        for f in self.findChildren(QFrame):
            name = f.objectName()
            if name == "HSep":
                f.setStyleSheet(f"background:{palette(mode)['border']};border:none;")
            elif name == "VLine":
                f.setStyleSheet(f"background:{palette(mode)['border']};border:none;")
        self.refresh()

    def showEvent(self, e):                                  # noqa: N802
        """切回本页时刷新状态（额度会随抓取实时变化）。"""
        super().showEvent(e)
        self.refresh()
