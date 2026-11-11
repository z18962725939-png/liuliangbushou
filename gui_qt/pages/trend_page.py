"""数据趋势：把历次抓到的数据攒成本机时间序列，看「涨了还是跌了」。

★ 为什么要有这一页（2026-10-14，对标蝉妈妈 / 飞瓜之后立的）

    那两家真正值钱的不是"能查到数据"，而是"能查到三个月前的数据"——
    判断一个号是在起量还是在掉，靠的从来不是某一帧，而是两帧之间的差。
    本工具过去每次抓完就丢，因此永远只能当"取款机"，当不了"账本"。

    从 v1.12.0 起，所有抓取结果都会自动在本机留一份快照
    （写入口在 platforms/adapter.py，设计说明见 history_store.py），
    这一页就是把那本账翻出来看：

      · 账号粉丝曲线  —— 对标账号是在涨粉还是在掉粉
      · 作品互动曲线  —— 一条作品发出去之后各项互动怎么变
      · 积累的爆款库  —— 过去抓到过的所有高数据作品，可以直接复用

    ★ 所有数据留在本机数据库，不上传任何服务器。

★ 为什么这一页不设付费闸门（v1.21.0 评审时专门想过，别改回去）

    本页的每个动作都只读本机 history_store，一次采集接口都不调 ——
    拦它我们没有一分钱收益，反而有两个坏处：
      1) 到期客户回免费版后，按承诺「下载和录制照样能用」，自己的历史
         账本也该能翻。闸了就是把自己卖出去的数据锁给客户看。
      2) 免费用户本地没数据时，页面显示的是 0 而不是一片失败，
         不会误以为「软件坏了」—— 那才是要拦的场景（见 _base 里的注释）。
    真正该拦的是「产生数据」的入口：采集、批量、选题、竞品、监控、报告、转写。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import (confirm_destructive, danger_button, page_title, section_header)
from gui_qt.widgets.kit import DisclosurePanel, MetricBoard
from gui_qt.widgets.stat import TrendChart, palette

# 只列真正该看的列：观察天数是可信度，必须显示（只看过一天的结论不算结论）
TABLE_COLUMNS = ["平台", "标题", "作者", "点赞", "评论", "收藏", "分享",
                 "最近记录", "观察天数"]

RANGE_CHOICES = [("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90),
                 ("近一年", 365)]

ORDER_CHOICES = [("按点赞", "digg"), ("按评论", "comment"), ("按收藏", "collect"),
                 ("按分享", "share")]


def _fmt_size(n: int) -> str:
    """体积显示成人话。"""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{int(n)} B"


def _retune(page) -> None:
    """折叠面板展开后重算表格列宽（延迟导入避免与 _base 循环）。"""
    try:
        from ._base import retune_tables_later
        retune_tables_later(page)
    except Exception:                                        # noqa: BLE001
        pass


class TrendPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._mode = "light"
        self._rows: list[dict] = []
        self._authors: list[dict] = []
        self._posts: list[dict] = []
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        # 14 → 11：这一页的卡片多，间隙一累加就是 40px 的体感差距
        root.setSpacing(11)
        root.addWidget(page_title("数据趋势"))

        self.sub = QLabel(
            "每次抓取都会自动在本机留一份记录（同一天只留最新一次），"
            "攒下来就能看出谁在涨、谁在掉。数据只存在你自己的电脑上。")
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet("color:#6B7280;font-size:12px;")
        root.addWidget(self.sub)

        # ---------- ① 库的规模 ----------
        top, top_l = self._card("本地记录的规模")
        # ★ 2026-10-17 三次改版：四张指标卡换成一块联动数据板。
        #   这块板能做的事：数字滚动、右键切换「原始 / 万」整组换算、
        #   点眼睛全部糊掉（给同事看屏幕或投屏时用）。四张独立的卡做不到
        #   "整组联动"—— 一张一张改，反而像四个不同的东西。
        self.board = MetricBoard(self._mode, unit_toggle=True, eye=True, gap=20)
        self.board.set_note("点右上角单位可整组换算")
        self.c_posts = self.board.add_metric("记录过的作品")
        self.c_authors = self.board.add_metric("记录过的账号")
        self.c_days = self.board.add_metric("覆盖天数")
        self.c_size = self.board.add_metric("占用空间")
        top_l.addWidget(self.board)
        self.db_hint = QLabel("")
        self.db_hint.setWordWrap(True)
        self.db_hint.setStyleSheet("font-size:12px;")
        top_l.addWidget(self.db_hint)

        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        self.auto_chk = QCheckBox("抓取时自动记录")
        self.auto_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_chk.toggled.connect(self._on_auto_toggled)
        ctl.addWidget(self.auto_chk)
        ctl.addStretch()
        self.prune_btn = QPushButton("清理一年前的记录")
        self.prune_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prune_btn.clicked.connect(self._on_prune)
        ctl.addWidget(self.prune_btn)
        self.clear_btn = danger_button("清空全部记录")
        self.clear_btn.clicked.connect(self._on_clear)
        ctl.addWidget(self.clear_btn)
        top_l.addLayout(ctl)
        root.addWidget(top)

        # ---------- ② 账号粉丝曲线 ----------
        acc, acc_l = self._card("账号粉丝曲线：他是在涨粉还是在掉粉")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("看哪个账号"))
        self.author_box = QComboBox()
        self.author_box.setMinimumWidth(280)
        self.author_box.currentIndexChanged.connect(self._on_author_changed)
        row.addWidget(self.author_box, 1)
        row.addWidget(QLabel("时间范围"))
        self.author_range = QComboBox()
        for label, d in RANGE_CHOICES:
            self.author_range.addItem(label, d)
        self.author_range.setCurrentIndex(1)
        self.author_range.currentIndexChanged.connect(self._on_author_changed)
        row.addWidget(self.author_range)
        acc_l.addLayout(row)

        self.author_chart = TrendChart(self._mode)
        acc_l.addWidget(self.author_chart)
        self.author_note = QLabel("")
        self.author_note.setWordWrap(True)
        self.author_note.setStyleSheet("font-size:12px;")
        acc_l.addWidget(self.author_note)
        root.addWidget(acc)

        # ---------- ③ 作品互动曲线 ----------
        # ★ 2026-10-27：整块收进折叠面板（标题挪到折叠条上）。
        #   本页原来 1382px，一屏放不下：上面是规模、中间两条曲线、
        #   下面还有一张 210px 的表格。但客户一次只看一件事 ——
        #   要么看账号在涨还是在掉，要么翻爆款库。
        #   ★ 图表类不能用 Segmented 硬切（隐藏时宽度为 0，切回来曲线会
        #     按错尺寸重绘），所以这里统一用"高度动画折叠"：展开时控件
        #     是真实摊开的，图表能拿到正确宽度。
        post, post_l = self._card("")
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        row2.addWidget(QLabel("看哪条作品"))
        self.post_box = QComboBox()
        self.post_box.setMinimumWidth(280)
        self.post_box.currentIndexChanged.connect(self._on_post_changed)
        row2.addWidget(self.post_box, 1)
        row2.addWidget(QLabel("时间范围"))
        self.post_range = QComboBox()
        for label, d in RANGE_CHOICES:
            self.post_range.addItem(label, d)
        self.post_range.setCurrentIndex(3)
        self.post_range.currentIndexChanged.connect(self._on_post_changed)
        row2.addWidget(self.post_range)
        post_l.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(14)
        row3.addWidget(QLabel("显示"))
        self.metric_boxes: dict[str, QCheckBox] = {}
        for name, on in (("点赞", True), ("评论", False), ("收藏", False),
                         ("分享", False)):
            cb = QCheckBox(name)
            cb.setChecked(on)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.toggled.connect(self._draw_post_chart)
            row3.addWidget(cb)
            self.metric_boxes[name] = cb
        row3.addStretch()
        post_l.addLayout(row3)

        self.post_chart = TrendChart(self._mode)
        post_l.addWidget(self.post_chart)
        self.post_fold = DisclosurePanel(
            "作品曲线：这条作品发出去之后怎么走的",
            meta="默认收起 · 点开看单条作品走势",
            mode=self._mode, parent=self)
        self.post_fold.add_widget(post)
        root.addWidget(self.post_fold)
        self.author_chart.setMinimumHeight(150)
        self.post_chart.setMinimumHeight(150)

        # ---------- ④ 积累的爆款库 ----------
        lib, lib_l = self._card("")
        row4 = QHBoxLayout()
        row4.setSpacing(10)
        row4.addWidget(QLabel("范围"))
        self.lib_range = QComboBox()
        for label, d in RANGE_CHOICES:
            self.lib_range.addItem(label, d)
        self.lib_range.setCurrentIndex(3)
        self.lib_range.currentIndexChanged.connect(self._reload_library)
        row4.addWidget(self.lib_range)
        row4.addWidget(QLabel("排序"))
        self.lib_order = QComboBox()
        for label, key in ORDER_CHOICES:
            self.lib_order.addItem(label, key)
        self.lib_order.currentIndexChanged.connect(self._reload_library)
        row4.addWidget(self.lib_order)
        row4.addStretch()
        self.copy_btn = QPushButton("复制选中链接")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_link)
        row4.addWidget(self.copy_btn)
        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self._export)
        row4.addWidget(self.export_btn)
        lib_l.addLayout(row4)

        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(210)
        self.table.verticalHeader().setVisible(False)
        lib_l.addWidget(self.table)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#6B7280;font-size:12px;")
        lib_l.addWidget(self.status)
        root.addWidget(lib)

        root.addStretch()

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        f = QFrame()
        f.setObjectName("Card")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        if title:
            lay.addWidget(section_header(title))
        return f, lay

    # ---------------------------------------------------------------- 数据
    def refresh(self):
        """重新读库并刷新整页。每次切回本页都会跑一次。"""
        p = palette(self._mode)
        from justoneapi_collector import history_store as hs

        self.auto_chk.blockSignals(True)
        self.auto_chk.setChecked(bool(self.ctx.get_setting("history_enabled", True)))
        self.auto_chk.blockSignals(False)

        st = hs.stats()
        self.c_posts.set_value(st["posts"], f"共 {st['rows']} 条每日快照")
        self.c_authors.set_value(st["authors"], "挖过作品或查过主页的号")
        if st["first_day"]:
            self.c_days.set_value(st["days"], f"{st['first_day']} 起")
        else:
            self.c_days.set_value(0, "还没开始记录")
        self.c_size.set_value(_fmt_size(st["size_bytes"]), "本机数据库，不上传",
                              kind="text")

        if not hs.available():
            self.db_hint.setText("这台电脑的 Python 缺少数据库组件，记录功能已自动关闭"
                                 "（不影响抓取和其它功能）。")
            self.db_hint.setStyleSheet(f"color:{p['warn']};font-size:12px;")
        elif not st["rows"]:
            self.db_hint.setText("还没有任何记录。去「选题挖掘」或「关键词抓取」跑一次，"
                                 "结果就会自动存到这里；同一个账号隔几天再抓一次，"
                                 "曲线就出来了。")
            self.db_hint.setStyleSheet(f"color:{p['title']};font-size:12px;")
        elif st["days"] < 2:
            self.db_hint.setText("目前只有 1 天的记录。趋势至少需要两天的数据才能画出来，"
                                 "明天再抓一次同样的账号就有对比了。")
            self.db_hint.setStyleSheet(f"color:{p['warn']};font-size:12px;")
        else:
            self.db_hint.setText("记录正常。同一天多次抓取只保留最新一次，"
                                 "所以反复抓同一个账号不会把库撑大。")
            self.db_hint.setStyleSheet(f"color:{p['sub']};font-size:12px;")

        self._reload_authors()
        self._reload_library()

    def _reload_authors(self):
        from justoneapi_collector import history_store as hs
        keep = self.author_box.currentData()
        self._authors = hs.tracked_authors(days=365)

        self.author_box.blockSignals(True)
        self.author_box.clear()
        for a in self._authors:
            label = f"{a['nickname']}　{a['fans']} 粉　({a['snap_days']} 天记录)"
            self.author_box.addItem(label, (a["platform"], a["author_id"]))
        if self._authors:
            idx = 0
            if keep:
                for i, a in enumerate(self._authors):
                    if (a["platform"], a["author_id"]) == tuple(keep):
                        idx = i
                        break
            self.author_box.setCurrentIndex(idx)
        self.author_box.blockSignals(False)
        self._on_author_changed()

    def _on_author_changed(self, *_a):
        """账号换了 → 重画粉丝曲线，并刷新该账号的作品下拉。"""
        from justoneapi_collector import history_store as hs
        data = self.author_box.currentData()
        if not data:
            self.author_chart.clear()
            self.post_box.clear()
            self.post_chart.clear()
            self.author_note.setText("还没有记录过任何账号。")
            return
        platform, author_id = data
        days = int(self.author_range.currentData() or 30)
        pts = hs.author_trend(platform, author_id, days=days)
        self.author_chart.set_series({"粉丝": [(r["day"], r["fans"]) for r in pts]})

        info = next((a for a in self._authors
                     if a["platform"] == platform and a["author_id"] == author_id), {})
        if len(pts) < 2:
            self.author_note.setText("只记录过一天，还看不出涨跌 —— 隔几天再抓一次这个号。")
            self.author_note.setStyleSheet(f"color:{palette(self._mode)['warn']};font-size:12px;")
        else:
            delta = pts[-1]["fans"] - pts[0]["fans"]
            arrow = "涨" if delta > 0 else ("掉" if delta < 0 else "持平")
            self.author_note.setText(
                f"记录的 {len(pts)} 天里粉丝{arrow} {abs(delta)}　"
                f"（{pts[0]['day']} 的 {pts[0]['fans']} → {pts[-1]['day']} 的 {pts[-1]['fans']}）")
            self.author_note.setStyleSheet(
                f"color:{palette(self._mode)['ok' if delta >= 0 else 'bad']};font-size:12px;")

        self._posts = hs.author_posts(platform, author_id, days=365)
        self.post_box.blockSignals(True)
        self.post_box.clear()
        for r in self._posts:
            title = (r["title"] or "（无标题）")[:34]
            self.post_box.addItem(f"{title}　{r['digg']} 赞", (r["platform"], r["post_id"]))
        self.post_box.blockSignals(False)
        self._draw_post_chart()

    def _on_post_changed(self, *_a):
        self._draw_post_chart()

    def _draw_post_chart(self, *_a):
        from justoneapi_collector import history_store as hs
        data = self.post_box.currentData()
        if not data:
            self.post_chart.clear()
            return
        platform, post_id = data
        days = int(self.post_range.currentData() or 365)
        pts = hs.post_trend(platform, post_id, days=days)
        key_of = {"点赞": "digg", "评论": "comment", "收藏": "collect", "分享": "share"}
        series = {}
        for name, cb in self.metric_boxes.items():
            if cb.isChecked():
                series[name] = [(r["day"], r[key_of[name]]) for r in pts]
        self.post_chart.set_series(series)
        self.post_chart.apply_theme(self._mode)

    def _reload_library(self, *_a):
        from justoneapi_collector import history_store as hs
        days = int(self.lib_range.currentData() or 365)
        order = self.lib_order.currentData() or "digg"
        self._rows = hs.top_posts(days=days, order_by=order, limit=200)

        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            vals = [r["platform"], (r["title"] or "（无标题）")[:30], r["author"],
                    r["digg"], r["comment"], r["collect"], r["share"],
                    r["last_day"], r["snap_days"]]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c >= 3 and c <= 6:
                    it.setTextAlignment(int(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter))
                self.table.setItem(i, c, it)
        self.export_btn.setEnabled(bool(self._rows))
        self.copy_btn.setEnabled(bool(self._rows))
        if self._rows:
            self.status.setText(f"共 {len(self._rows)} 条（该范围内记录过的作品，"
                                f"按{self.lib_order.currentText()}排）")
        else:
            self.status.setText("这个范围内还没有记录过的作品。")

    # ---------------------------------------------------------------- 操作
    def _on_auto_toggled(self, on: bool):
        """开关只控制"以后还记不记"，已有数据一条不动。"""
        self.ctx.set_setting("history_enabled", bool(on))
        self.ctx.save_settings()
        try:
            from justoneapi_collector import history_store as hs
            hs.set_enabled(bool(on))
        except Exception:                                    # noqa: BLE001
            pass
        self.status.setText("已开启自动记录：以后每次抓取都会存到本机。"
                            if on else
                            "已关闭自动记录：不再写入新数据，已有的记录仍然可以看。")

    def _on_prune(self):
        if not confirm_destructive(
                self, "清理旧记录",
                "确定要删除一年前的历史记录吗？",
                ok_text="确认清理",
                detail="只删一年前的快照，近一年的曲线不受影响。此操作不可撤销。"):
            return
        from justoneapi_collector import history_store as hs
        n = hs.prune(days=365)
        self.status.setText(f"已清理 {n} 条旧快照。")
        self.refresh()

    def _on_clear(self):
        if not confirm_destructive(
                self, "清空全部记录",
                "确定要清空本机所有历史记录吗？",
                ok_text="确认清空",
                detail="账号曲线、作品曲线和爆款库都会一起清空，"
                       "以后再抓才重新开始积累。此操作不可撤销。"):
            return
        from justoneapi_collector import history_store as hs
        n = hs.clear_all()
        self.status.setText(f"已清空 {n} 条记录。")
        self.refresh()

    def _copy_link(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            self.status.setText("先在上面的表格里点一行，再复制链接。")
            return
        url = self._rows[row].get("url") or ""
        if not url:
            self.status.setText("这条记录里没有链接（接口没返回）。")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        self.status.setText(f"已复制：{url}")

    def _export(self):
        if not self._rows:
            self.status.setText("暂无数据可导出")
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "导出历史数据",
                                              "数据趋势.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            import openpyxl
            from justoneapi_collector import history_store as hs
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(list(hs.EXPORT_COLUMNS))
            for r in hs.export_rows(self._rows):
                ws.append(r)
            wb.save(path)
            self.status.setText(f"已导出：{path}")
        except Exception as e:                               # noqa: BLE001
            self.status.setText(f"导出失败：{e}")

    # ---------------------------------------------------------------- 主题
    def apply_theme(self, mode: str):
        self._mode = mode
        self.board.apply_theme(mode)
        self.author_chart.apply_theme(mode)
        self.post_chart.apply_theme(mode)
        # 折叠面板是自绘的（不读 QSS），切主题必须转交，否则深色下
        # 两个折叠条会孤零零留在浅色配色上
        for fold in (getattr(self, "post_fold", None),
                     getattr(self, "lib_fold", None)):
            if fold is not None:
                fold.apply_theme(mode)
        self.refresh()

    def showEvent(self, e):                                  # noqa: N802
        """切回本页时重新读库（别的时候抓的数据要看得到）。"""
        super().showEvent(e)
        self.refresh()
