"""选题挖掘页（PySide6）。

功能：按关键词批量挖掘「以小博大」爆款（低粉高赞）；评论区痛点挖掘（用户在问什么）。
复用 justoneapi_collector.topic_mining。无真实 client 时降级为演示。
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import (StatusLine, Worker, append_log, cell_num, connect_guarded,
                    confirm_destructive, connection_diagnosis, danger_button,
                    fill_platform_box, guide_banner, log_panel, page_title,
                    require_feature, selected_row_links, send_to_script,
                    vertical_card)

from gui_qt.widgets.stat import StatCard, TopBars, _fmt_num, stat_row

from justoneapi_collector.topic_mining import (COMMENT_EXPORT_COLUMNS,
                                              RANKED_POST_COLUMNS, analyze_comments,
                                              fetch_comments, mine_keywords_batch)

# RANKED_POST_COLUMNS 的索引（概览统计要按列取值，写死下标容易错位，这里集中定义）
# 列顺序见 RANKED_POST_COLUMNS：0爆粉比 1平台 2标题 3作者 4作者粉丝 …
# 11链接
_C_RATIO, _C_TITLE, _C_AUTHOR, _C_FANS, _C_DIGG, _C_LINK = 0, 2, 3, 4, 5, 11


class TopicPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("选题挖掘"))
        root.addWidget(guide_banner("选题挖掘", self))

        # 爆款挖掘
        top, top_l = vertical_card("爆款选题挖掘（低粉高赞 = 以小博大）", self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("平台："))
        self.platform = QComboBox(); fill_platform_box(self.platform)
        # ★ 下拉显示中文、真值挂 itemData（见 _base.fill_platform_box 的说明）
        r1.addWidget(self.platform)
        r1.addWidget(QLabel("关键词(逗号分隔)："))
        self.keywords = QLineEdit("美妆,穿搭,减肥")
        r1.addWidget(self.keywords, 1)
        b_mine = QPushButton("开始挖掘"); b_mine.setObjectName("Primary")
        connect_guarded(b_mine.clicked, lambda *_: self._mine(), on_error=lambda msg: self.status.setText(msg), label="mine")
        r1.addWidget(b_mine)
        top_l.addLayout(r1)
        r2 = QHBoxLayout()
        self.export_btn = QPushButton("导出Excel"); self.export_btn.setEnabled(False)
        connect_guarded(self.export_btn.clicked, lambda *_: self._export(), on_error=lambda msg: self.status.setText(msg), label="export")
        b_copy = QPushButton("复制链接")
        b_copy.setToolTip("复制选中作品链接到剪贴板（可多条）")
        connect_guarded(b_copy.clicked, lambda *_: self._copy_links(),
                        on_error=lambda msg: self.status.setText(msg), label="复制链接")
        b_watch = QPushButton("作者加入监控")
        b_watch.setToolTip("把选中作品的作者链接加入「复盘监控」")
        connect_guarded(b_watch.clicked, lambda *_: self._watch_authors(),
                        on_error=lambda msg: self.status.setText(msg), label="作者加入监控")
        # 清空爆款结果：破坏性动作，样式弱化 + 二次确认
        self.clear_post_btn = danger_button("清空结果")
        self.clear_post_btn.setEnabled(False)
        self.clear_post_btn.setToolTip("清空下方爆款挖掘结果表格")
        connect_guarded(self.clear_post_btn.clicked, lambda *_: self._clear_posts(), on_error=lambda msg: self.status.setText(msg), label="clear posts")
        self.script_btn = QPushButton("写同款文案")
        self.script_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.script_btn.setToolTip("把选中作品的链接带到「文案仿写」，直接扒口播稿再仿写")
        connect_guarded(self.script_btn.clicked, lambda *_: self._to_script(), on_error=lambda msg: self.status.setText(msg), label="to script")
        r2.addWidget(self.export_btn); r2.addWidget(b_copy); r2.addWidget(b_watch)
        r2.addWidget(self.script_btn)
        r2.addWidget(self.clear_post_btn)
        r2.addStretch()
        top_l.addLayout(r2)

        # 结果概览：表格要逐行比数字，条形榜扫一眼就知道谁最猛
        self.ov_count = StatCard("挖到", "—", "条以小博大作品")
        self.ov_ratio = StatCard("最猛爆粉比", "—", "点赞 ÷ 粉丝，越高越以小博大")
        self.ov_digg = StatCard("平均点赞", "—", "这批作品的中位水平")
        top_l.addLayout(stat_row([self.ov_count, self.ov_ratio, self.ov_digg]))
        self.ov_bars = TopBars()
        top_l.addWidget(self.ov_bars)

        cols = list(RANKED_POST_COLUMNS)
        self.post_table = QTableWidget(0, len(cols))
        self.post_table.setHorizontalHeaderLabels(cols)
        self.post_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.post_table.horizontalHeader().setStretchLastSection(True)
        top_l.addWidget(self.post_table, 1)
        root.addWidget(top, 1)

        # 评论痛点
        # ★ v1.14.4：进度条与状态放在两块结果卡之间 —— 原来排在评论表之后，
        #   两张大表撑起来后进度条几乎贴着视口底部，看不到。
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = StatusLine("就绪")
        root.addWidget(self.status)

        cm, cm_l = vertical_card("评论区痛点挖掘（用户在问什么、骂什么、想要什么 = 免费选题库）", self)
        r3 = QHBoxLayout()
        r3.addWidget(QLabel("作品链接/ID（每行一个）："))
        self.post_ids = _MiniText()
        self.post_ids.setMaximumHeight(60)
        r3.addWidget(self.post_ids, 1)
        b_cmt = QPushButton("挖掘评论"); b_cmt.setObjectName("Primary")
        connect_guarded(b_cmt.clicked, lambda *_: self._mine_comments(), on_error=lambda msg: self.status.setText(msg), label="mine comments")
        self.cmt_export = QPushButton("导出评论"); self.cmt_export.setEnabled(False)
        self.clear_cmt_btn = danger_button("清空评论")
        self.clear_cmt_btn.setEnabled(False)
        self.clear_cmt_btn.setToolTip("清空下方评论洞察结果表格")
        connect_guarded(self.clear_cmt_btn.clicked, lambda *_: self._clear_comments(), on_error=lambda msg: self.status.setText(msg), label="clear comments")
        r3.addWidget(b_cmt); r3.addWidget(self.cmt_export)
        r3.addWidget(self.clear_cmt_btn)
        cm_l.addLayout(r3)
        ccols = list(COMMENT_EXPORT_COLUMNS)
        self.cmt_table = QTableWidget(0, len(ccols))
        self.cmt_table.setHorizontalHeaderLabels(ccols)
        self.cmt_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.cmt_table.horizontalHeader().setStretchLastSection(True)
        cm_l.addWidget(self.cmt_table, 1)
        root.addWidget(cm, 1)

        self.log = log_panel()
        root.addWidget(self.log, 1)
        self._sync_actions()
        self._update_overview()
        # 未连接服务时，进入页面就常驻提示，不必等用户点了"开始挖掘"才知道。
        # ★ 2026-11-24 P0：统一走 connection_diagnosis()，别再写死「去激活」——
        #   真实原因可能是授权码里没带采集凭证、或 okdata Key 没填，
        #   已经激活的客户照着这句只会反复激活，等于把人引到死路。
        #   状态栏只放诊断标题 + 压成一句的说明，细节交给点击后的弹窗。
        if self.ctx.demo:
            title, hint = connection_diagnosis()
            first = hint.split("\n\n")[0].replace("\n", " ")
            self.status.setText(f"⚠ {title}：{first}")
            self.status.mark("error")

    def _sync_actions(self):
        """结果为空时禁用对应的「清空」按钮，避免无效点击。"""
        self.clear_post_btn.setEnabled(self.post_table.rowCount() > 0)
        self.clear_cmt_btn.setEnabled(self.cmt_table.rowCount() > 0)
        self.script_btn.setEnabled(self.post_table.rowCount() > 0)

    # --------------------------------------------------- 爆款挖掘
    def _mine(self):
        # ★ 付费闸门放在最前面（v1.21.0）
        if not require_feature(self, "analyze", "爆款选题挖掘"):
            return
        kws = [k.strip() for k in re.split(r"[,，]", self.keywords.text()) if k.strip()]
        if not kws:
            self.status.setText("请填写至少一个关键词")
            return
        if self.ctx.demo:
            # ★ 绝不展示示例数据（见 _notify_not_connected 的说明）
            self._notify_not_connected()
            return
        plat = self.platform.currentData() or self.platform.currentText()
        self.export_btn.setEnabled(False)
        self.progress.setValue(10)
        self.status.mark("idle")
        self.status.setText(f"挖掘中：{kws} …")

        def run(worker):
            return mine_keywords_batch(self.ctx.client, plat, kws,
                                      on_progress=lambda m: worker.emit_log(m))
        w = Worker(run)
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(self._on_mine)
        w.error.connect(lambda e: self.status.setText(f"挖掘出错：{e}"))
        w.start()

    def _notify_not_connected(self):
        """未连接采集服务时，明确告知，且【绝不】往表格里填示例数据。

        ★ 2026-10-11 客户反馈修复：
          此前演示模式会往结果表里填两条写死的示例（「新手化妆3个误区」
          「平价穿搭分享」）。客户搜「减肥」看到这两条，完全对不上，
          于是反馈"搜不到相关视频、关联性极低" —— 其实是软件根本没联网。
          假数据比空结果更糟：它让人以为功能坏了，而不是以为没配置。
        """
        self.post_table.setRowCount(0)          # 清掉可能残留的旧结果
        self.export_btn.setEnabled(False)
        self._sync_actions()
        title, hint = connection_diagnosis()
        self.status.setText(f"⚠ {title}，未做任何挖掘")
        self.status.mark("error")
        append_log(self.log, "[选题挖掘] 未连接采集服务，已停止挖掘，结果表保持为空。")
        append_log(self.log, hint.replace("\n", " "))
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(self, title, hint)

    def _on_mine(self, ranked):
        self.progress.setValue(100)
        self.status.mark("idle")
        cols = list(RANKED_POST_COLUMNS)
        self.post_table.setRowCount(len(ranked))
        for i, r in enumerate(ranked):
            # ★ RankedPost.row() 返回 [爆粉比, 平台, 标题, …]，与表头一一对应。
            #   旧代码调的是不存在的 r.to_row()，hasattr 判否后走 {}，
            #   于是即便真实挖掘成功，每一格也都是空白 —— 第二个隐藏 bug。
            for c, val in enumerate(r.row()):
                if c < len(cols):
                    self.post_table.setItem(i, c, QTableWidgetItem(str(val)))
        self._sync_actions()
        self._update_overview(self.post_table)
        self.status.setText(f"挖掘完成：{len(ranked)} 条以小博大作品")
        if ranked:
            self.export_btn.setEnabled(True)
        elif getattr(self.ctx.client, "token_invalid", False):
            # 有 client 但凭证失效：这不是"没筛出"，而是压根没查成。
            # 必须说清楚，否则用户会以为"关键词搜不到东西"。
            self.status.setText("⚠ 采集服务未授权或已到期：请到「设置」重新激活 / 更新 Token")
            self.status.mark("error")
            append_log(self.log, "[选题挖掘] 采集服务返回未授权，未取到任何数据。"
                                 "请到「设置 → 授权与激活」检查授权状态。")
        else:
            append_log(self.log, "未筛出符合条件的作品：可放宽粉丝上限 / 点赞下限，"
                                 "或换更具体的行业关键词（如「减脂餐」而非「减肥」）。")

    def _update_overview(self, table=None):
        """把结果表算成一眼能懂的三个数 + 一张条形榜。

        表格适合查明细，但不适合回答"这批里哪个最值钱"。概览就是干这个的。
        """
        t = table if table is not None else self.post_table
        if t.rowCount() == 0:
            self.ov_count.set_value("—", "还没跑，先点「开始挖掘」")
            self.ov_ratio.set_value("—", "")
            self.ov_digg.set_value("—", "")
            self.ov_bars.clear()
            return

        def cell(r, c):
            it = t.item(r, c)
            return it.text().strip() if it is not None else ""

        rows = []
        for r in range(t.rowCount()):
            digg = cell_num(cell(r, _C_DIGG))
            rows.append((cell(r, _C_TITLE) or "（无标题）", digg,
                         cell_num(cell(r, _C_RATIO))))
        diggs = [d for _t, d, _x in rows if d > 0]
        ratios = [x for _t, _d, x in rows if x > 0]

        self.ov_count.set_value(f"{len(rows)}", "条以小博大作品", "ok")
        self.ov_ratio.set_value(f"{max(ratios):.1f}" if ratios else "—",
                                "点赞 ÷ 粉丝，越高越以小博大")
        self.ov_digg.set_value(_fmt_num(sum(diggs) / len(diggs)) if diggs else "—",
                               "这批作品的平均点赞")

        top = sorted(rows, key=lambda kv: kv[1], reverse=True)[:5]
        self.ov_bars.set_items([(t, d, _fmt_num(d)) for t, d, _x in top], "点赞")

    def _to_script(self):
        """把选中作品送进「文案仿写」——找完对标直接扒文案，不用手动复制。"""
        links = selected_row_links(self.post_table, -1, limit=3)
        if not links:
            self.status.setText("先在结果表里点中一条作品，再点「写同款文案」")
            return
        ok, msg = send_to_script(self.ctx, links, self)
        self.status.setText(msg)
        if not ok:
            self.status.mark("error")

    def _copy_links(self):
        """把选中作品链接复制到剪贴板——客户拿到链接才能去扒同款/投流。"""
        links = selected_row_links(self.post_table, -1, limit=20)
        if not links:
            self.status.setText("先在结果表里点中一条作品，再点「复制链接」")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(links))
        self.status.setText(f"已复制 {len(links)} 条链接到剪贴板")

    def _watch_authors(self):
        """把选中作品的作者链接送进「复盘监控」——监控其粉丝/作品变化。"""
        if not require_feature(self, "analyze", "作者加入监控"):
            return
        rows = set()
        for idx in self.post_table.selectionModel().selectedRows():
            row = idx.row()
            link_it = self.post_table.item(row, _C_LINK)
            author_it = self.post_table.item(row, _C_AUTHOR)
            if link_it is None or not link_it.text().strip():
                continue
            rows.add((link_it.text().strip(),
                      author_it.text().strip() if author_it else ""))
        if not rows:
            self.status.setText("先在结果表里点中一条作品，再点「作者加入监控」")
            return
        pm = getattr(self.ctx, "project_manager", None)
        if pm is None:
            # 真实模式由 build_context() 注入 ProjectManager；这里拿不到就如实提示，
            # 绝不假装已监控（演示模式走的是更早的 ctx.demo 分支）。
            self.status.setText(
                f"已选中 {len(rows)} 位作者，但当前工作区未就绪；"
                "请先在「设置」创建/打开项目后再加入监控")
            self.status.mark("warn")
            return
        # projects.py:263-267：current 是 @property（不是方法！），
        # 写成 pm.current() 会 TypeError: 'Project' object is not callable。
        # 旧的 current_project() 更是不存在，callable 保护只是不崩却永远 fallback 到 None。
        cur = getattr(pm, "current", None)
        project = cur() if callable(cur) else cur
        if project is None:
            ensure = getattr(pm, "ensure_default", None)
            project = ensure() if callable(ensure) else None
        if project is None:
            self.status.setText("请先创建或打开一个项目，再加入监控")
            self.status.mark("warn")
            return
        added, failed = 0, 0
        for link, author in rows:
            try:
                # Project.add_watch_author 才是真实存在的方法（projects.py:86），
                # 旧代码里的 pm.add_watch_target 根本不存在。
                if project.add_watch_author("douyin", link, author):
                    added += 1
                else:
                    failed += 1            # 已在清单里
            except Exception:                              # noqa: BLE001
                failed += 1
        save = getattr(pm, "save", None)
        if callable(save):
            try:
                save()
            except Exception:                              # noqa: BLE001
                pass
        msg = f"已加入监控 {added} 位作者"
        if failed:
            msg += f"，未新增 {failed} 位（可能已存在）"
        self.status.setText(msg)

    def apply_theme(self, mode: str):
        """主题切换：概览区的自绘组件要跟着换色。"""
        for c in (self.ov_count, self.ov_ratio, self.ov_digg):
            c.apply_theme(mode)
        self.ov_bars.apply_theme(mode)

    def _clear_posts(self):
        """清空爆款挖掘结果。"""
        n = self.post_table.rowCount()
        if n == 0:
            self.status.setText("爆款结果已经是空的")
            return
        if not confirm_destructive(
            self, "清空爆款结果",
            f"确定清空当前 {n} 条挖掘结果吗？",
            ok_text="确认清空",
            detail="只清空结果表格；关键词等输入内容会保留。",
        ):
            self.status.setText("已取消，结果未做改动")
            return
        self.post_table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self._sync_actions()
        self._update_overview()
        self.status.setText(f"已清空 {n} 条爆款结果")

    def _export(self):
        if self.post_table.rowCount() == 0:
            self.status.setText("暂无数据可导出")
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "导出选题", "选题挖掘.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active
            cols = list(RANKED_POST_COLUMNS)
            ws.append(cols)
            for r in range(self.post_table.rowCount()):
                ws.append([self.post_table.item(r, c).text() if self.post_table.item(r, c) else ""
                           for c in range(len(cols))])
            wb.save(path)
            self.status.setText(f"已导出：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"导出失败：{e}")

    # --------------------------------------------------- 评论挖掘
    def _mine_comments(self):
        # ★ 付费闸门放在最前面（v1.21.0）：评论区痛点挖掘同样走采集接口
        if not require_feature(self, "analyze", "评论区痛点挖掘"):
            return
        ids = [t.strip() for t in re.split(r"[\n]+", self.post_ids.toPlainText()) if t.strip()]
        if not ids:
            self.status.setText("请至少填一个作品链接/ID")
            return
        if self.ctx.demo:
            # 与 _build_ui 的常驻提示同一口径：真实原因可能是授权码里没带
            # 采集凭证、或 okdata Key 没填，统一走诊断，别把已激活的客户
            # 引去反复激活；状态栏只放标题 + 压成一句的说明。
            title, hint = connection_diagnosis()
            first = hint.split("\n\n")[0].replace("\n", " ")
            self.status.setText(f"⚠ {title}：{first}")
            self.status.mark("error")
            return
        plat = self.platform.currentData() or self.platform.currentText()

        def run(worker):
            comments = fetch_comments(self.ctx.client, plat, ids,
                                     on_progress=lambda m: worker.emit_log(m))
            return analyze_comments(comments)
        w = Worker(run)
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(self._on_comments)
        w.error.connect(lambda e: self.status.setText(f"评论挖掘出错：{e}"))
        w.start()

    def _on_comments(self, insight):
        # insight_to_rows 已在模块底部直接导入，无需再用 globals() 探测
        rows = insight_to_rows(insight) if insight else []
        cols = list(COMMENT_EXPORT_COLUMNS)
        self.cmt_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, k in enumerate(cols):
                val = row[c] if c < len(row) else ""
                self.cmt_table.setItem(i, c, QTableWidgetItem(str(val)))
        self._sync_actions()
        self.status.mark("idle")
        self.status.setText(f"评论分析完成：{len(rows)} 条洞察")
        if rows:
            self.cmt_export.setEnabled(True)

    def _clear_comments(self):
        """清空评论洞察结果。"""
        n = self.cmt_table.rowCount()
        if n == 0:
            self.status.setText("评论结果已经是空的")
            return
        if not confirm_destructive(
            self, "清空评论结果",
            f"确定清空当前 {n} 条评论洞察吗？",
            ok_text="确认清空",
            detail="只清空结果表格；作品链接输入框的内容会保留。",
        ):
            self.status.setText("已取消，结果未做改动")
            return
        self.cmt_table.setRowCount(0)
        self.cmt_export.setEnabled(False)
        self._sync_actions()
        self.status.setText(f"已清空 {n} 条评论洞察")


from PySide6.QtWidgets import QPlainTextEdit  # noqa: E402


class _MiniText(QPlainTextEdit):
    def toPlainText(self):
        return super().toPlainText()


# 延迟导入，避免循环
from justoneapi_collector.topic_mining import insight_to_rows  # noqa: E402

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.progress import FlowProgressBar
from ._base import require_feature
