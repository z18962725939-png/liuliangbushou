"""评论区截流页（PySide6）。

干什么：把对标视频评论区里「想买、问价、求链接、要私信」的人挖出来，
导出成表格交给运营去私信/加微 —— 也就是代运营行话里的「评论区截流」。

怎么用：
    1. 粘贴对标视频链接（一行一个），或从「关键词抓取」导出的 Excel 直接导入
    2. 选抓多少页（每页 20 条评论）
    3. 点开始 → 结果表里只留下命中「意图词」的评论
    4. 导出 Excel：昵称 / 抖音号 / 主页链接 / 评论内容 / 点赞 / 时间

成本：评论接口 0.1 元/次，一个视频 1 页 = 0.1 元。按钮旁会实时算预计消耗。
未连接采集服务时绝不填示例数据（2026-10-11 定下的规矩）。
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QHeaderView, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ._base import (StatusLine, Worker, append_log, confirm_destructive,
                    connection_diagnosis, danger_button, fill_platform_box,
                    form_row, guide_banner, log_panel, page_title, vertical_card)

from justoneapi_collector import licensing
from justoneapi_collector.comment_mining import (COMMENT_COLUMNS,
                                                 DEFAULT_INTENT_WORDS, PAGE_PLANS,
                                                 dedupe_leads, estimate_requests,
                                                 extract_post_id, mine_comments,
                                                 split_post_inputs)

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.kit import DisclosurePanel
from gui_qt.widgets.progress import FlowProgressBar
from ._base import require_feature


def _safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text)).strip() or "评论"


class CommentPage(QWidget):
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
        """功能门禁：授权里不含「评论区截流」就明确告知，不静默失败。

        run_btn 的可用状态在抓取/停止流程里也会被改写，故 _run() 开头还会再判一次。
        """
        ok, why = licensing.check("comment")
        # ★ 不许拿「禁用按钮」当门禁（点了没反应＝客户以为软件坏了）。
        #   按钮始终可点，点下去由 _run() 里的 require_feature 弹统一付费须知。
        if ok:
            return
        self.status.setText(f"⚠ {why}")
        self.status.mark("warn")

    # ------------------------------------------------------------ 界面
    def apply_theme(self, mode: str):
        """折叠面板是自绘的（QSS 管不到），切主题必须转交。

        ★ 主窗口 _refresh_guide_banners 会遍历各页调 apply_theme；页面没有这个
          方法时，深色下这两块会孤零零留在浅色配色上 —— 下载页踩过同一个坑。
        """
        for w in (getattr(self, "adv", None), getattr(self, "log_box", None)):
            if w is not None:
                w.apply_theme(mode)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("评论区截流"))
        root.addWidget(guide_banner("评论区截流", self))

        # ---------------- 输入 ----------------
        top, tl = vertical_card("对标视频（支持多个，一行一个）", self)
        r0 = QHBoxLayout()
        r0.setSpacing(10)
        lab = QLabel("视频链接")
        lab.setMinimumWidth(96)
        lab.setStyleSheet("color:#374151;")
        r0.addWidget(lab, 0, Qt.AlignmentFlag.AlignTop)
        box = QVBoxLayout()
        box.setSpacing(4)
        self.links = QPlainTextEdit()
        self.links.setFixedHeight(96)
        self.links.setTabChangesFocus(True)
        self.links.setPlaceholderText(
            "一行一个视频链接或作品ID（也支持逗号分隔），例如：\n"
            "https://www.douyin.com/video/7494166718776986914\n"
            "https://v.douyin.com/xxxxxx/")
        box.addWidget(self.links)
        # ★ 2026-10-26 改版：「导入 Excel」原来单独占一整行，只为挂一颗按钮。
        #   现在与「已识别 N 个视频」同一行 —— 两者本来就是同一件事的两端
        #   （读进来 / 读到了几个），分两行反而让客户以为它们是两码事。
        under = QHBoxLayout()
        under.setSpacing(10)
        self.import_btn = QPushButton("导入「关键词抓取」Excel")
        self.import_btn.setToolTip("直接读取关键词抓取导出的 Excel 里的链接列，"
                                   "不用手工复制粘贴")
        self.import_btn.clicked.connect(self._import_excel)
        under.addWidget(self.import_btn)
        self.id_count = QLabel("")
        self.id_count.setStyleSheet("color:#6B7280;font-size:12px;")
        under.addWidget(self.id_count)
        under.addStretch()
        box.addLayout(under)
        r0.addLayout(box, 1)
        tl.addLayout(r0)
        root.addWidget(top)

        # ---------------- 抓取设置 ----------------
        mid, ml = vertical_card("抓取设置", self)
        r1 = QHBoxLayout()
        self.platform = QComboBox()
        # ★ 下拉显示中文，真值挂 itemData（见 _base.fill_platform_box 的说明）
        fill_platform_box(self.platform)
        r1.addLayout(form_row("平台", self.platform))
        self.pages_box = QComboBox()
        self.pages_box.addItems(list(PAGE_PLANS.keys()))
        # 默认「快速」：体验版一天只有 10 次额度，默认开 3 页会把额度一次吃光
        self.pages_box.setCurrentIndex(0)
        self.pages_box.setFixedWidth(240)
        r1.addLayout(form_row("抓取页数", self.pages_box))
        r1.addStretch()
        # 额度预估挪到这一行右侧：它算的是「平台 × 页数 × 视频数」，跟这两个
        # 下拉框是同一屏决策；原来挤在五颗按钮的末尾，离它要解释的参数最远。
        self.quota_hint = QLabel("")
        self.quota_hint.setObjectName("Caption")
        r1.addWidget(self.quota_hint)
        ml.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(10)
        klab = QLabel("意图关键词")
        klab.setMinimumWidth(96)
        klab.setStyleSheet("color:#374151;")
        r2.addWidget(klab, 0, Qt.AlignmentFlag.AlignTop)
        kbox = QVBoxLayout()
        kbox.setSpacing(4)
        self.words = QPlainTextEdit()
        self.words.setFixedHeight(64)
        self.words.setTabChangesFocus(True)
        self.words.setPlaceholderText(
            "留空 = 用内置词库（怎么买/多少钱/求链接/私信/微信/批发…）；\n"
            "填了就按你填的词筛（一行一个，也支持逗号分隔）")
        kbox.addWidget(self.words)
        self.use_default = QCheckBox("同时保留内置词库（推荐）")
        self.use_default.setChecked(True)
        self.use_default.setToolTip(
            f"内置 {len(DEFAULT_INTENT_WORDS)} 个词："
            + "、".join(DEFAULT_INTENT_WORDS[:12]) + "…")
        kbox.addWidget(self.use_default)
        r2.addLayout(kbox, 1)

        r3 = QHBoxLayout()
        # 三个勾选项拉开 20px：默认间距下「排除视频作者本人」与「同一用户只留一条」
        # 的勾选框几乎贴在前一项文字尾巴上，读起来像一串连着的字。
        r3.setSpacing(20)
        self.reply_chk = QCheckBox("抓子评论（回复）")
        self.reply_chk.setToolTip("回复里常有「已私你」「加个微」这类关键话术；"
                                  "但每条命中的评论要 1 次请求，会明显多花额度")
        self.reply_chk.stateChanged.connect(lambda _s: self._refresh_quota_hint())
        r3.addWidget(self.reply_chk)
        self.author_chk = QCheckBox("排除视频作者本人")
        self.author_chk.setToolTip("每个视频多花 1 次请求去取作者ID，"
                                   "用来剔除作者自己回复的评论")
        self.author_chk.stateChanged.connect(lambda _s: self._refresh_quota_hint())
        r3.addWidget(self.author_chk)
        self.dedupe_chk = QCheckBox("同一用户只留一条")
        self.dedupe_chk.setChecked(True)
        self.dedupe_chk.setToolTip("同一人留点赞最高的那条，表更干净")
        r3.addWidget(self.dedupe_chk)
        r3.addStretch()

        # ★ 2026-10-26 改版：意图词 + 三个勾选项全是「可选微调」，原来平铺着
        #   占了设置卡一半高度。第一次用的客户真正要做的只有三步：
        #   粘链接 → 选页数 → 点开始。收进折叠区，标题里写清「默认用内置词库」，
        #   让人放心不展开。
        self.adv = DisclosurePanel("意图关键词与抓取选项",
                                   meta="可选 · 默认用内置词库",
                                   mode="light", parent=self)
        # ★ 展开区内部必须自己包一层带间距的容器：DisclosurePanel 的
        #   body_layout 是 spacing=0 的（它只想贴紧标题），直接 add_layout 两次，
        #   「同时保留内置词库」的勾选框会和下面那排三个勾选框叠在一起（实测截图上
        #   像一串连着的字）。外面这层给 10px 行距。
        adv_body = QWidget()
        av = QVBoxLayout(adv_body)
        av.setContentsMargins(0, 6, 0, 4)
        av.setSpacing(10)
        av.addLayout(r2)
        av.addLayout(r3)
        self.adv.add_widget(adv_body)
        ml.addWidget(self.adv)

        # 动作行只留「开始 / 停止」（2026-10-26）。
        # 导出 / 复制 / 清空 三颗挪到结果卡头部 —— 它们作用于结果表，留在页面
        # 中段的设置卡里，等于让客户跑到上面去处理下面的表格。
        self.run_btn = QPushButton("开始截流"); self.run_btn.setObjectName("Primary")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("停止"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_run)
        self.export_btn = QPushButton("导出Excel"); self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        self.copy_btn = QPushButton("复制抖音号"); self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_ids)
        self.clear_btn = danger_button("清空结果"); self.clear_btn.setEnabled(False)
        self.clear_btn.clicked.connect(self._clear)

        r4 = QHBoxLayout()
        r4.setSpacing(8)
        r4.addWidget(self.run_btn)
        r4.addWidget(self.stop_btn)
        r4.addStretch()
        ml.addLayout(r4)
        root.addWidget(mid)

        # ★ v1.14.4：进度条与状态上移到结果卡之前（原来在表格和日志下方，
        #   页面一长就被顶出视口，采集时看不到走到哪了）。
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = StatusLine("就绪")
        self.status.mark("idle")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        # ---------------- 结果 ----------------
        res, rl = vertical_card("意向评论（按点赞从高到低）", self)
        cols = list(COMMENT_COLUMNS)
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 220)     # 评论内容
        self.table.setColumnWidth(2, 120)     # 抖音号
        rl.addWidget(self.table, 1)
        root.addWidget(res, 1)

        self.log = log_panel()
        root.addWidget(self.log, 1)

        for box in (self.platform, self.pages_box):
            box.currentTextChanged.connect(lambda _t="": self._refresh_quota_hint())
        self.links.textChanged.connect(lambda: self._refresh_quota_hint())
        self.words.textChanged.connect(lambda: self._refresh_quota_hint())
        self._refresh_quota_hint()

    # ------------------------------------------------------------ 输入解析
    def _post_ids(self) -> list[str]:
        return split_post_inputs(self.links.toPlainText())

    def _custom_words(self) -> list[str]:
        raw = [w.strip() for w in re.split(r"[,，\n]", self.words.toPlainText())]
        return [w for w in raw if w]

    def _effective_words(self):
        custom = self._custom_words()
        if not custom:
            return None                       # None = 用内置词库
        if self.use_default.isChecked():
            merged = list(DEFAULT_INTENT_WORDS)
            for w in custom:
                if w not in merged:
                    merged.append(w)
            return merged
        return custom

    def _import_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择关键词抓取导出的 Excel",
            "", "表格文件 (*.xlsx *.xlsm *.csv *.txt)")
        if not path:
            return
        try:
            from justoneapi_collector.video_link_import import read_links_from_file
            links = read_links_from_file(path)
        except Exception as e:                                   # noqa: BLE001
            self.status.setText(f"读取失败：{e}")
            return
        if not links:
            self.status.setText("这个文件里没找到视频链接")
            return
        ids: list[str] = []
        for u in links:
            pid = extract_post_id(u)
            if pid and pid not in ids:
                ids.append(pid)
        if not ids:
            self.status.setText(
                f"读了 {len(links)} 条链接，但都没解析出作品ID（小红书链接可能不行）")
            return
        self.links.setPlainText("\n".join(ids))
        self.status.setText(f"已导入 {len(ids)} 个视频（共读到 {len(links)} 条链接）")

    # ------------------------------------------------------------ 额度预估
    def _refresh_quota_hint(self):
        ids = self._post_ids()
        self.id_count.setText(
            f"已识别 {len(ids)} 个视频" if ids else "")
        pages = PAGE_PLANS.get(self.pages_box.currentText(), 1)
        total = estimate_requests(len(ids), pages,
                                  exclude_author=self.author_chk.isChecked(),
                                  include_replies=self.reply_chk.isChecked())
        if not ids:
            self.quota_hint.setText("")
            self.quota_hint.setStyleSheet("color:#6B7280;font-size:12px;")
            return
        try:
            from justoneapi_collector import quota
            q = quota.summary()
        except Exception:                                        # noqa: BLE001
            q = None
        suffix = "（含子评论粗估）" if self.reply_chk.isChecked() else ""
        if q and q.get("unlimited"):
            self.quota_hint.setText(f"预计消耗 {total} 次调用{suffix}（不限量）")
            self.quota_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        elif q and q.get("limit", 0) > 0:
            left = q.get("remaining", 0)
            txt = f"预计消耗 {total} 次调用{suffix}，今日剩余 {left} 次"
            if total > left:
                txt += "（额度不够，建议减少视频数或改用「快速」）"
                self.quota_hint.setStyleSheet(
                    "color:#DC2626;font-size:12px;font-weight:600;")
            else:
                self.quota_hint.setStyleSheet("color:#6B7280;font-size:12px;")
            self.quota_hint.setText(txt)
        else:
            self.quota_hint.setText(f"预计消耗 {total} 次调用{suffix}")

    # ------------------------------------------------------------ 运行
    def _run(self):
        # ★ 付费闸门（v1.21.0）：统一走付费须知，文案与价格各页一致
        if not require_feature(self, "comment", "评论区截流"):
            return
        ids = self._post_ids()
        if not ids:
            self.status.setText("请先粘贴至少一个视频链接或作品ID")
            return
        if self.ctx.demo:
            self._warn_not_connected()
            return
        if self._worker is not None and self._worker.isRunning():
            self.status.setText("已有任务在跑，请等它结束或点「停止」")
            return

        # 额度先报账（v1.5.0 起所有批量入口的规矩）
        pages = PAGE_PLANS.get(self.pages_box.currentText(), 1)
        need = estimate_requests(len(ids), pages,
                                 exclude_author=self.author_chk.isChecked(),
                                 include_replies=self.reply_chk.isChecked())
        try:
            from justoneapi_collector import quota
            q = quota.summary()
        except Exception:                                        # noqa: BLE001
            q = None
        if q and not q.get("unlimited") and q.get("limit", 0) > 0:
            left = q.get("remaining", 0)
            if need > left:
                ret = QMessageBox.question(
                    self, "额度可能不够",
                    f"本次预计需要 {need} 次调用，你今天只剩 {left} 次。\n\n"
                    f"额度用完的部分会抓不到（不会报错）。\n"
                    f"建议减少视频数量、或把「抓取页数」改成「快速」。\n\n"
                    f"仍要继续吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ret != QMessageBox.Yes:
                    self.status.setText("已取消，未消耗额度")
                    return

        self._stop = False
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.progress.setValue(5)
        self.status.mark("idle")
        self.status.setText(f"截流中：共 {len(ids)} 个视频…")
        append_log(self.log, f"[评论区截流] 开始：{len(ids)} 个视频")

        plat = self.platform.currentData() or self.platform.currentText()
        words = self._effective_words()
        replies = self.reply_chk.isChecked()
        excl = self.author_chk.isChecked()
        dedupe = self.dedupe_chk.isChecked()

        def task(worker):
            def on_post(idx, total, pid):
                pct = ((idx - 1) / total * 100.0) if total else 0.0
                worker.emit_progress(pct, f"正在抓第 {idx}/{total} 个视频的评论…")

            res = mine_comments(
                self.ctx.client, plat, ids, pages=pages, keywords=words,
                include_replies=replies, exclude_author=excl, dedupe=dedupe,
                should_stop=lambda: self._stop,
                on_log=lambda m: worker.emit_log(m),
                on_post=on_post)
            if not dedupe:
                res.leads = dedupe_leads(res.leads)
            return res

        w = Worker(task)
        self._worker = w
        w.log.connect(lambda m: append_log(self.log, m) if m else None)
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.error.connect(lambda e: self.status.setText(f"截流出错：{e}"))
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

    def _on_done(self, res):
        leads = getattr(res, "leads", []) or []
        cols = list(COMMENT_COLUMNS)
        self.table.setRowCount(len(leads))
        for i, ld in enumerate(leads):
            row = ld.as_row()
            for c in range(len(cols)):
                val = row[c] if c < len(row) else ""
                self.table.setItem(i, c, QTableWidgetItem(str(val)))
        has = bool(leads)
        self.export_btn.setEnabled(has)
        self.copy_btn.setEnabled(has)
        self.clear_btn.setEnabled(has)

        scanned = getattr(res, "scanned", 0)
        used = getattr(res, "requests", 0)
        if has:
            self.status.mark("ok")
            self.status.setText(
                f"截流完成：扫描 {scanned} 条评论，命中 {len(leads)} 个意向用户"
                f"（请求 {used} 次）")
        else:
            self.status.mark("error")
            if getattr(self.ctx.client, "token_invalid", False):
                self.status.setText("⚠ 采集服务未授权或已到期：请到「设置 → 授权与激活」重新激活")
            else:
                errs = getattr(res, "errors", []) or []
                if errs:
                    self.status.setText(f"未取到数据：{errs[0]}")
                else:
                    self.status.setText(
                        f"扫了 {scanned} 条评论但没命中意图词：可换更热的视频，"
                        f"或在「意图关键词」里加上你行业的行话")
                append_log(self.log, f"[评论区截流] 详情：{errs[:3]}")

    def _warn_not_connected(self, silent: bool = False):
        self.table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
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
        default_name = "评论区意向客户.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出意向客户",
                                             default_name, "Excel (*.xlsx)")
        if not path:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook(); ws = wb.active
            cols = list(COMMENT_COLUMNS)
            ws.append(cols)
            for r in range(self.table.rowCount()):
                ws.append([self.table.item(r, c).text() if self.table.item(r, c) else ""
                           for c in range(len(cols))])
            wb.save(path)
            self.status.setText(
                f"已导出 {self.table.rowCount()} 个意向客户：{path}")
        except Exception as e:                                   # noqa: BLE001
            self.status.setText(f"导出失败：{e}")

    def _copy_ids(self):
        col = COMMENT_COLUMNS.index("抖音号")
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, col)
            if it and it.text().strip():
                out.append(it.text().strip())
        if not out:
            self.status.setText("结果里没有可复制的抖音号")
            return
        QApplication.clipboard().setText("\n".join(out))
        self.status.setText(f"已复制 {len(out)} 个抖音号到剪贴板")

    def _clear(self):
        n = self.table.rowCount()
        if n == 0:
            self.status.setText("结果已经是空的")
            return
        if not confirm_destructive(self, "清空截流结果",
                                   f"确定清空当前 {n} 条意向评论吗？",
                                   ok_text="确认清空",
                                   detail="只清空结果表格，视频链接与筛选条件会保留。"):
            self.status.setText("已取消，结果未做改动")
            return
        self.table.setRowCount(0)
        self.export_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.status.setText(f"已清空 {n} 条结果")
