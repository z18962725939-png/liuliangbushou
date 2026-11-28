"""关键词视频抓取页（PySide6）。

与「选题挖掘」的分工：
  · 选题挖掘 = 找「低粉高赞」的可复制爆款，会按点赞/粉丝门槛过滤，结果少而精；
  · 本页      = 就一个关键词，把该词下数据最好的相关视频尽量抓全，不做以小博大过滤。

客户反馈「搜一个词只能用选题挖掘，但搜出来的视频太少」—— 本页就是为这个需求做的
（2026-10-11）。复用 justoneapi_collector.keyword_search。未连接服务时不展示示例数据。
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHeaderView, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ._base import connect_guarded
from ._base import (StatusLine, Worker, append_log, confirm_destructive, danger_button,
                    form_row, guide_banner, log_panel, page_title, vertical_card)

from justoneapi_collector.keyword_search import (DEPTH_PLANS, KEYWORD_COLUMNS,
                                                SORT_OPTIONS, TIME_OPTIONS,
                                                build_plan, rows_from_results,
                                                search_keywords)

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.progress import FlowProgressBar
from gui_qt.widgets.stat import StatCard, TopBars, _fmt_num, stat_row
from ._base import cell_num, connection_diagnosis, fill_platform_box, require_feature, selected_row_links, send_to_script
from justoneapi_collector import licensing

# RANKED_POST_COLUMNS 的索引（概览统计要按列取值，写死下标容易错位，这里集中定义）
_C_RATIO, _C_TITLE, _C_FANS, _C_DIGG = 0, 2, 4, 5


def _safe_name(text: str) -> str:
    """把关键词转成合法文件名（去掉 Windows 不允许的字符）。"""
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text)).strip() or "关键词"


class KeywordPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._stop = False
        self._worker = None
        self._build_ui()
        if self.ctx.demo:
            self._warn_not_connected(silent=True)
        self._refresh_gate()

    def _refresh_gate(self):
        """功能门禁：授权里不含「关键词抓取」就明确告知，不静默失败。

        注意：run_btn 的可用状态在抓取/停止流程里也会被改写，
        所以 _run() 开头还会再判一次 —— 门禁不能只在构建时生效一次。
        """
        ok, why = licensing.check("keyword")
        # ★ 不许拿「禁用按钮」当门禁（点了没反应＝客户以为软件坏了）。
        #   按钮始终可点，点下去由 _run() 里的 require_feature 弹统一付费须知。
        if ok:
            return
        self.status.setText(f"⚠ {why}")
        self.status.mark("warn")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("关键词抓取"))
        root.addWidget(guide_banner("关键词抓取", self))

        # ---------------- 抓取设置 ----------------
        top, tl = vertical_card("关键词视频抓取（支持多个关键词一次抓全）", self)
        r1 = QHBoxLayout()
        self.platform = QComboBox()
        # 显示中文、值仍是接口要的英文键（详见 _base.fill_platform_box 的说明）
        fill_platform_box(self.platform)
        r1.addLayout(form_row("平台", self.platform))
        r1.addStretch()
        tl.addLayout(r1)

        # 关键词改成多行输入：一行一个最直观，也兼容原来的逗号分隔写法
        krow = QHBoxLayout()
        krow.setSpacing(10)
        klab = QLabel("关键词")
        klab.setMinimumWidth(96)
        klab.setStyleSheet("color:#374151;")
        krow.addWidget(klab, 0, Qt.AlignmentFlag.AlignTop)
        kw_box = QVBoxLayout()
        kw_box.setSpacing(4)
        self.keywords = QPlainTextEdit()
        self.keywords.setFixedHeight(74)
        self.keywords.setTabChangesFocus(True)
        self.keywords.setPlaceholderText(
            "一行一个关键词（也支持逗号分隔），例如：\n减脂餐\n减肥食谱\n瘦肚子")
        kw_box.addWidget(self.keywords)
        self.kw_count = QLabel("")
        self.kw_count.setStyleSheet("color:#6B7280;font-size:12px;")
        kw_box.addWidget(self.kw_count)
        krow.addLayout(kw_box, 1)
        tl.addLayout(krow)

        r2 = QHBoxLayout()
        self.sort_box = QComboBox()
        self.sort_box.addItems(list(SORT_OPTIONS.keys()))
        self.sort_box.setFixedWidth(180)
        r2.addLayout(form_row("排序", self.sort_box))
        self.time_box = QComboBox()
        self.time_box.addItems(list(TIME_OPTIONS.keys()))
        self.time_box.setFixedWidth(130)
        r2.addLayout(form_row("时间范围", self.time_box))
        self.depth_box = QComboBox()
        self.depth_box.addItems(list(DEPTH_PLANS.keys()))
        # 默认「快速」：体验版客户一天只有 10 次额度，默认就开 9 次请求太狠，
        # 想要更多由用户自己往上调（按钮旁会实时显示预计消耗与剩余额度）。
        self.depth_box.setCurrentIndex(0)
        self.depth_box.setFixedWidth(170)
        r2.addLayout(form_row("抓取深度", self.depth_box))
        r2.addStretch()
        tl.addLayout(r2)

        r3 = QHBoxLayout()
        self.min_digg = QLineEdit("0")
        self.min_digg.setFixedWidth(110)
        self.min_digg.setToolTip("只保留点赞数 ≥ 该值的视频；0 表示不过滤")
        r3.addLayout(form_row("最低点赞", self.min_digg))
        self.max_fans = QLineEdit("0")
        self.max_fans.setFixedWidth(110)
        self.max_fans.setToolTip("只保留作者粉丝 ≤ 该值的视频；0 表示不限。"
                                 "注意小红书/快手搜索不返回粉丝数，填了也不生效")
        r3.addLayout(form_row("粉丝上限", self.max_fans))
        self.quota_hint = QLabel("")
        self.quota_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        r3.addWidget(self.quota_hint)
        r3.addStretch()
        tl.addLayout(r3)

        r4 = QHBoxLayout()
        self.run_btn = QPushButton("开始抓取"); self.run_btn.setObjectName("Primary")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_guarded(self.run_btn.clicked, lambda *_: self._run(), on_error=lambda msg: self.status.setText(msg), label="run")
        self.stop_btn = QPushButton("停止"); self.stop_btn.setEnabled(False)
        connect_guarded(self.stop_btn.clicked, lambda *_: self._stop_run(), on_error=lambda msg: self.status.setText(msg), label="stop run")
        self.export_btn = QPushButton("导出Excel"); self.export_btn.setEnabled(False)
        connect_guarded(self.export_btn.clicked, lambda *_: self._export(), on_error=lambda msg: self.status.setText(msg), label="export")
        self.copy_btn = QPushButton("复制链接"); self.copy_btn.setEnabled(False)
        connect_guarded(self.copy_btn.clicked, lambda *_: self._copy_links(), on_error=lambda msg: self.status.setText(msg), label="copy links")
        self.clear_btn = danger_button("清空结果"); self.clear_btn.setEnabled(False)
        connect_guarded(self.clear_btn.clicked, lambda *_: self._clear(), on_error=lambda msg: self.status.setText(msg), label="clear")
        self.script_btn = QPushButton("写同款文案")
        self.script_btn.setEnabled(False)          # 有结果才可用，避免空点
        self.script_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.script_btn.setToolTip("把选中作品的链接带到「文案仿写」，直接扒口播稿再仿写")
        connect_guarded(self.script_btn.clicked, lambda *_: self._to_script(), on_error=lambda msg: self.status.setText(msg), label="to script")
        for b in (self.run_btn, self.stop_btn, self.export_btn, self.copy_btn,
                  self.script_btn, self.clear_btn):
            r4.addWidget(b)
        r4.addStretch()
        tl.addLayout(r4)
        root.addWidget(top)

        # ---------------- 结果表 ----------------
        # ★ v1.14.4：进度条与状态上移到结果卡之前（原来在表格和日志下方，会被顶出视口）
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = StatusLine("就绪")
        self.status.mark("idle")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        res, rl = vertical_card("抓取结果（按点赞从高到低）", self)

        # 概览：先给结论（多少条、最猛多少赞），再给明细表
        self.ov_count = StatCard("抓到", "—", "条相关视频")
        self.ov_top = StatCard("最高点赞", "—", "这批里数据最猛的一条")
        self.ov_avg = StatCard("平均点赞", "—", "判断这个词的竞争强度")
        rl.addLayout(stat_row([self.ov_count, self.ov_top, self.ov_avg]))
        self.ov_bars = TopBars()
        rl.addWidget(self.ov_bars)

        cols = list(KEYWORD_COLUMNS)
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(2, 260)          # 标题列给宽一点
        rl.addWidget(self.table, 1)
        root.addWidget(res, 1)

        self.log = log_panel()
        root.addWidget(self.log, 1)

        # 选项变化时刷新额度预估
        for box in (self.platform, self.sort_box, self.time_box, self.depth_box):
            box.currentTextChanged.connect(lambda _t="": self._refresh_quota_hint())
        self.keywords.textChanged.connect(lambda _t="": self._refresh_quota_hint())
        self._refresh_quota_hint()

    # ------------------------------------------------------------ 额度预估
    def _platform_key(self) -> str:
        """取平台的真实键（douyin / xiaohongshu / kuaishou）。

        下拉里显示的是中文，真值挂在 itemData 上；currentText() 只是兜底，
        防止将来有人手工 addItem 忘了带 data。
        """
        return self.platform.currentData() or self.platform.currentText()

    def _refresh_quota_hint(self):
        kws = self._split_keywords()
        per = len(build_plan(self.sort_box.currentText(), self.time_box.currentText(),
                             self.depth_box.currentText(), self._platform_key()))
        total = per * max(1, len(kws))

        if len(kws) > 1:
            shown = "、".join(kws[:6]) + ("…" if len(kws) > 6 else "")
            self.kw_count.setText(
                f"已识别 {len(kws)} 个关键词：{shown}（每个词约 {per} 次请求，"
                f"按顺序逐个抓取）")
        elif len(kws) == 1:
            self.kw_count.setText(f"已识别 1 个关键词（约 {per} 次请求）")
        else:
            self.kw_count.setText("")

        try:
            from justoneapi_collector import quota
            q = quota.summary()
        except Exception:                                   # noqa: BLE001
            q = None
        if q and q.get("unlimited"):
            self.quota_hint.setText(f"预计消耗 {total} 次调用（当前套餐不限量）")
        elif q and q.get("limit", 0) > 0:
            left = q.get("remaining", 0)
            txt = f"预计消耗 {total} 次调用，今日剩余 {left} 次"
            if total > left:
                txt += "（额度不够，建议改用「快速」或减少关键词）"
                self.quota_hint.setStyleSheet("color:#DC2626;font-size:12px;font-weight:600;")
            else:
                self.quota_hint.setStyleSheet("color:#6B7280;font-size:12px;")
            self.quota_hint.setText(txt)
        else:
            self.quota_hint.setText(f"预计消耗 {total} 次调用")

    def _split_keywords(self) -> list[str]:
        """拆关键词：一行一个，也兼容逗号（中英文）分隔；去重保序。"""
        raw = [k.strip() for k in re.split(r"[,，\n]", self.keywords.toPlainText())]
        out: list[str] = []
        for k in raw:
            if k and k not in out:
                out.append(k)
        return out

    # ------------------------------------------------------------ 抓取
    def _run(self):
        # ★ 付费闸门（v1.21.0）：统一走付费须知
        if not require_feature(self, "keyword", "关键词视频抓取"):
            return
        kws = self._split_keywords()
        if not kws:
            self.status.setText("请填写至少一个关键词")
            return
        if self.ctx.demo:
            self._warn_not_connected()
            return
        try:
            min_digg = int(self.min_digg.text().strip() or 0)
            max_fans = int(self.max_fans.text().strip() or 0)
        except ValueError:
            self.status.setText("「最低点赞」「粉丝上限」只能填数字（0 表示不过滤）")
            return

        self._stop = False
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.progress.setValue(5)
        self.status.mark("idle")
        if len(kws) > 1:
            self.status.setText(f"抓取中：共 {len(kws)} 个关键词，逐个抓取…")
        else:
            self.status.setText(f"抓取中：{kws[0]} …")
        append_log(self.log, f"[关键词抓取] 开始：{kws}")

        plat = self._platform_key()

        def task(worker):
            def on_kw(idx, total, kw):
                # 每换一个关键词推进一次进度；最后一个词跑完由 _on_finished 收尾到 100%
                pct = ((idx - 1) / total * 100.0) if total else 0.0
                worker.emit_progress(pct, f"正在抓第 {idx}/{total} 个关键词：{kw}")

            return search_keywords(
                self.ctx.client, plat, kws,
                sort_label=self.sort_box.currentText(),
                time_label=self.time_box.currentText(),
                depth_label=self.depth_box.currentText(),
                min_digg=min_digg, max_fans=max_fans,
                on_progress=lambda m: worker.emit_log(m),
                should_stop=lambda: self._stop,
                on_keyword=on_kw,
            )

        w = Worker(task)
        self._worker = w
        w.log.connect(lambda m: append_log(self.log, m) if m else None)
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.error.connect(lambda e: self.status.setText(f"抓取出错：{e}"))
        w.finished.connect(self._on_finished)
        w.start()

    def _on_progress(self, pct, msg):
        self.progress.setValue(int(pct))
        if msg:
            self.status.setText(msg)

    def _stop_run(self):
        self._stop = True
        self.stop_btn.setEnabled(False)
        self.status.setText("正在停止…（已发出的请求会跑完）")

    def _on_finished(self):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setValue(100)

    def _on_done(self, results):
        rows = rows_from_results(results or [])
        cols = list(KEYWORD_COLUMNS)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c in range(len(cols)):
                val = row[c] if c < len(row) else ""
                self.table.setItem(i, c, QTableWidgetItem(str(val)))
        self.export_btn.setEnabled(bool(rows))
        self.copy_btn.setEnabled(bool(rows))
        self.clear_btn.setEnabled(bool(rows))
        self.script_btn.setEnabled(bool(rows))
        self._update_overview()

        total_req = sum(getattr(r, "requests_used", 0) for r in (results or []))
        errs: list[str] = []
        for r in (results or []):
            errs.extend(getattr(r, "errors", []) or [])

        if rows:
            self.status.mark("ok")
            self.status.setText(
                f"抓取完成：{len(rows)} 条相关视频（已去重，按点赞排序），共请求 {total_req} 次")
        else:
            self.status.mark("error")
            if getattr(self.ctx.client, "token_invalid", False):
                self.status.setText("⚠ 采集服务未授权或已到期：请到「设置 → 授权与激活」重新激活")
            elif errs:
                self.status.setText(f"未取到数据：{errs[0]}")
            else:
                self.status.setText("未取到数据：可换更具体的关键词，或把「最低点赞」改成 0 后重试")
            append_log(self.log, f"[关键词抓取] 失败详情：{errs[:3]}")

    def _update_overview(self):
        """结果概览：条数 / 最高点赞 / 平均点赞 + Top5 条形榜。"""
        t = self.table
        if t.rowCount() == 0:
            self.ov_count.set_value("—", "还没跑，先点「开始抓取」")
            self.ov_top.set_value("—", "")
            self.ov_avg.set_value("—", "")
            self.ov_bars.clear()
            return

        def cell(r, c):
            it = t.item(r, c)
            return it.text().strip() if it is not None else ""

        rows = []
        for r in range(t.rowCount()):
            rows.append((cell(r, _C_TITLE) or "（无标题）", cell_num(cell(r, _C_DIGG))))
        diggs = [d for _t, d in rows if d > 0]
        self.ov_count.set_value(f"{len(rows)}", "条相关视频", "ok")
        self.ov_top.set_value(_fmt_num(max(diggs)) if diggs else "—",
                              "这批里数据最猛的一条")
        self.ov_avg.set_value(_fmt_num(sum(diggs) / len(diggs)) if diggs else "—",
                              "越高说明这个词竞争越激烈")
        top = sorted(rows, key=lambda kv: kv[1], reverse=True)[:5]
        self.ov_bars.set_items([(t2, d, _fmt_num(d)) for t2, d in top], "点赞")

    def _to_script(self):
        """把选中作品送进「文案仿写」：抓完直接扒文案，不用复制链接再切页。"""
        links = selected_row_links(self.table, KEYWORD_COLUMNS.index("链接"), limit=3)
        if not links:
            self.status.setText("先在结果表里点中一条作品，再点「写同款文案」")
            return
        ok, msg = send_to_script(self.ctx, links, self)
        self.status.setText(msg)
        if not ok:
            self.status.mark("error")

    def apply_theme(self, mode: str):
        """主题切换：概览区自绘组件跟着换色。"""
        for c in (self.ov_count, self.ov_top, self.ov_avg):
            c.apply_theme(mode)
        self.ov_bars.apply_theme(mode)

    def _warn_not_connected(self, silent: bool = False):
        """未连接采集服务：说清真实原因，且【绝不】往表里填示例数据。"""
        self.table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.script_btn.setEnabled(False)
        self._update_overview()
        title, detail = connection_diagnosis()
        self.status.setText(f"⚠ {title}")
        self.status.mark("error")
        if silent:
            return
        QMessageBox.warning(self, title, detail)

    # ------------------------------------------------------------ 结果操作
    def _export(self):
        if self.table.rowCount() == 0:
            self.status.setText("暂无数据可导出")
            return
        kws = self._split_keywords()
        if len(kws) == 1:
            default_name = f"{_safe_name(kws[0])}相关视频.xlsx"
        elif kws:
            default_name = f"{_safe_name(kws[0])}等{len(kws)}个关键词视频.xlsx"
        else:
            default_name = "关键词视频.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出关键词视频",
                                              default_name, "Excel (*.xlsx)")
        if not path:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active
            cols = list(KEYWORD_COLUMNS)
            ws.append(cols)
            for r in range(self.table.rowCount()):
                ws.append([self.table.item(r, c).text() if self.table.item(r, c) else ""
                           for c in range(len(cols))])
            wb.save(path)
            self.status.setText(
                f"已导出：{path} —— 可在「视频下载」页点「导入「关键词抓取」Excel」批量下载")
        except Exception as e:                              # noqa: BLE001
            self.status.setText(f"导出失败：{e}")

    def _copy_links(self):
        col = KEYWORD_COLUMNS.index("链接")
        links = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, col)
            if it and it.text().strip():
                links.append(it.text().strip())
        if not links:
            self.status.setText("结果里没有可复制的链接")
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText("\n".join(links))
        self.status.setText(f"已复制 {len(links)} 条链接到剪贴板")

    def _clear(self):
        n = self.table.rowCount()
        if n == 0:
            self.status.setText("结果已经是空的")
            return
        if not confirm_destructive(self, "清空抓取结果",
                                   f"确定清空当前 {n} 条结果吗？",
                                   ok_text="确认清空",
                                   detail="只清空结果表格，关键词与筛选条件会保留。"):
            self.status.setText("已取消，结果未做改动")
            return
        self.table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.script_btn.setEnabled(False)
        self._update_overview()
        self.status.setText(f"已清空 {n} 条结果")
