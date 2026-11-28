"""GEO 页：AI 可见度监测（品牌在 AI 回答里的提及 / 引用 / 竞品对比）。

干什么：
    把「客户真实会问 AI 的问题」批量丢给 DeepSeek / Kimi，把回答抓回来，
    算出你的品牌被提到了多少、排在多靠前、AI 参考了哪些网站、竞品比你怎么
    样，最后给一份能直接交给老板的报告。

怎么用：
    1. 填品牌名（+ 别名）+ 竞品名
    2. 填提问词（20~50 个为宜，一行一个，可从 Excel 导入）
    3. 勾引擎、填接口 Key（设置页也能填，会存到本地）
    4. 点「开始体检」→ 看概览、建议 → 导出 Excel 报告

三条必须让客户知道的口径（界面上也写了，别删）：
    · AI 回答有随机性 → 本页按「采样次数」多次提问取比例，不是单次结论
    · 接口通道不带联网搜索 → 测的是模型既有认知里的品牌信息
    · 见效以月为单位 → 只能承诺趋势，不能承诺排名
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)
from gui_qt.widgets.progress import FlowProgressBar

from ._base import connect_guarded
from ._base import (StatusLine, Worker, append_log, form_row, guide_banner, log_panel,
                    page_title, vertical_card)

from justoneapi_collector import geo_monitor as gm
from justoneapi_collector import licensing

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.kit import DisclosurePanel, Segmented
from ._base import require_feature

RESULT_COLUMNS = ["提问词", "引擎", "状态", "采样次数", "提及次数", "提及率",
                  "首次位次", "竞品提及", "情感", "引用信源"]

# 单次提问耗时实测值（2026-10-12 真机），只用于给用户估时间，不是计费依据
_ENGINE_SEC_PER_ASK = {"deepseek": 2.0, "kimi": 6.0}

# 通用提问模板：帮客户起步用，不是结果数据。客户必须按自己行业改。
_QUERY_TEMPLATES = [
    "{行业}哪个品牌好",
    "{行业}十大品牌排名",
    "{行业}怎么选，有什么推荐",
    "{行业}性价比高的品牌有哪些",
    "{行业}靠谱的厂家有哪些",
    "买{行业}要注意什么，哪个牌子不容易踩坑",
    "{行业}品牌对比，哪个好",
    "国内{行业}做得好的公司有哪些",
]


def _split_lines(text: str) -> list[str]:
    """一行一个，也兼容中英文逗号 / 分号，去重保序。"""
    out: list[str] = []
    raw = re.split(r"[\r\n,，;；]+", str(text or ""))
    for s in raw:
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out


class GeoPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._stop = False
        self._worker = None
        self._report = None
        self._engine_boxes: dict[str, QCheckBox] = {}
        self._key_edits: dict[str, QLineEdit] = {}
        self._model_edits: dict[str, QLineEdit] = {}
        self._build_ui()
        self._refresh_gate()

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("AI 可见度监测（GEO）"))
        root.addWidget(guide_banner("GEO 监测", self))

        self.gate_hint = QLabel("")
        self.gate_hint.setWordWrap(True)
        self.gate_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        root.addWidget(self.gate_hint)

        # ★ 2026-10-27 改版：六张卡 2100+ px，客户从「填品牌」滚到「看结果」
        #   要走过引擎、采样、四个 key、两份模型名…… GEO 天然是三段式，
        #   按 "填什么 → 用什么跑 → 跑出什么" 切开，每段一屏装得下。
        self.step_seg = Segmented(
            [("① 品牌与问题", "brand"), ("② 引擎与 Key", "engine"),
             ("③ 体检结果", "result")], "light", self)
        self.step_seg.changed.connect(self._sync_step)
        seg_row = QHBoxLayout()
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.addWidget(self.step_seg)
        seg_row.addStretch()
        root.addLayout(seg_row)

        self._view_brand = QWidget(self)
        self._view_engine = QWidget(self)
        self._view_result = QWidget(self)
        self._lay_brand = QVBoxLayout(self._view_brand)
        self._lay_engine = QVBoxLayout(self._view_engine)
        self._lay_result = QVBoxLayout(self._view_result)
        for lay in (self._lay_brand, self._lay_engine, self._lay_result):
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
        self._view_engine.setVisible(False)
        self._view_result.setVisible(False)
        root.addWidget(self._view_brand)
        root.addWidget(self._view_engine)
        root.addWidget(self._view_result)

        # ---------------- 品牌与竞品 ----------------
        c1, l1 = vertical_card("品牌与竞品", self)
        self.brand = QLineEdit()
        self.brand.setPlaceholderText("客户品牌名，例如：某某按摩棒")
        l1.addLayout(form_row("品牌名", self.brand))
        self.alias = QLineEdit()
        self.alias.setPlaceholderText("全称 / 简称 / 英文名 / 官网域名，用逗号分隔（漏了会统计漏）")
        l1.addLayout(form_row("品牌别名", self.alias))
        r1 = QHBoxLayout()
        r1.setSpacing(10)
        clab = QLabel("竞品")
        clab.setMinimumWidth(96)
        clab.setStyleSheet("color:#374151;")
        r1.addWidget(clab, 0, Qt.AlignmentFlag.AlignTop)
        self.competitors = QPlainTextEdit()
        self.competitors.setFixedHeight(58)
        self.competitors.setTabChangesFocus(True)
        self.competitors.setPlaceholderText("一行一个竞品名（也支持逗号），留空则不对比")
        r1.addWidget(self.competitors, 1)
        l1.addLayout(r1)
        self._lay_brand.addWidget(c1)

        # ---------------- 提问词库 ----------------
        c2, l2 = vertical_card("提问词库（客户真实会问 AI 的问题）", self)
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        qlab = QLabel("提问词")
        qlab.setMinimumWidth(96)
        qlab.setStyleSheet("color:#374151;")
        r2.addWidget(qlab, 0, Qt.AlignmentFlag.AlignTop)
        self.queries = QPlainTextEdit()
        self.queries.setFixedHeight(150)
        self.queries.setTabChangesFocus(True)
        self.queries.setPlaceholderText(
            "一行一个问题，建议 20~50 个，要覆盖：品牌词 / 行业词 / 选型词 / 预算词 / 对比词。\n"
            "例：按摩棒哪个牌子好、筋膜枪和按摩棒哪个好、预算500买什么按摩棒")
        r2.addWidget(self.queries, 1)
        l2.addLayout(r2)

        r2b = QHBoxLayout()
        self.import_btn = QPushButton("从 Excel 导入提问词")
        self.import_btn.setToolTip("读取表格第一列的文本作为提问词")
        connect_guarded(self.import_btn.clicked, lambda *_: self._import_queries(), on_error=lambda msg: self.status.setText(msg), label="import queries")
        r2b.addWidget(self.import_btn)
        self.tpl_btn = QPushButton("插入通用提问模板")
        self.tpl_btn.setToolTip("插入 8 条通用选型类问法，请按客户行业替换「{行业}」")
        connect_guarded(self.tpl_btn.clicked, lambda *_: self._insert_template(), on_error=lambda msg: self.status.setText(msg), label="insert template")
        r2b.addWidget(self.tpl_btn)
        r2b.addStretch()
        self.count_hint = QLabel("")
        self.count_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        r2b.addWidget(self.count_hint)
        l2.addLayout(r2b)
        self.queries.textChanged.connect(self._refresh_count)
        self._lay_brand.addWidget(c2)

        # ---------------- 引擎与采样 ----------------
        c3, l3 = vertical_card("引擎与采样", self)
        r3 = QHBoxLayout()
        r3.setSpacing(14)
        for key, spec in gm.ENGINE_SPECS.items():
            box = QCheckBox(spec.label)
            box.setToolTip(spec.note)
            if spec.kind == "cdp":
                box.setEnabled(False)
                box.setToolTip(spec.note + "（当前版本未开放）")
            else:
                box.setChecked(True)
            box.stateChanged.connect(self._refresh_estimate)
            self._engine_boxes[key] = box
            r3.addWidget(box)
        r3.addStretch()
        l3.addLayout(r3)

        r3b = QHBoxLayout()
        self.sample_box = QComboBox()
        self.sample_box.addItems(["1 次（最快，只做粗略摸底）",
                                  "3 次（推荐，抵消 AI 随机性）",
                                  "5 次（最稳，耗时与成本翻倍）"])
        self.sample_box.setCurrentIndex(1)
        self.sample_box.setFixedWidth(260)
        self.sample_box.currentIndexChanged.connect(self._refresh_estimate)
        r3b.addLayout(form_row("每词采样", self.sample_box))
        r3b.addStretch()
        l3.addLayout(r3b)

        self.estimate = QLabel("")
        self.estimate.setStyleSheet("color:#6B7280;font-size:12px;")
        self.estimate.setWordWrap(True)
        l3.addWidget(self.estimate)
        self.queries.textChanged.connect(self._refresh_estimate)
        self._lay_engine.addWidget(c3)

        # ---------------- 接口 Key ----------------
        c4, l4 = vertical_card("AI 接口 Key（存在本机，不外传）", self)
        saved_models = self.ctx.get_setting("geo_models", {}) or {}
        for key, spec in gm.ENGINE_SPECS.items():
            if spec.kind != "api":
                continue
            ed = QLineEdit()
            ed.setEchoMode(QLineEdit.EchoMode.Password)
            ed.setPlaceholderText(f"{spec.label} 的接口 Key（{spec.note}）")
            saved = str(self.ctx.get_setting("geo_api_keys", {}).get(key, "") or "")
            ed.setText(saved)
            self._key_edits[key] = ed
            l4.addLayout(form_row(f"{spec.label} Key", ed))

            med = QLineEdit()
            med.setPlaceholderText(f"默认 {spec.model}（模型名会下线，报错时可在这里换）")
            med.setText(str(saved_models.get(key, "") or ""))
            med.setToolTip("留空就用内置默认模型。官方下线旧模型名导致报错时，"
                           "在这里填新的模型名即可，不用等软件更新。")
            self._model_edits[key] = med
            l4.addLayout(form_row(f"{spec.label} 模型名", med))
        r4 = QHBoxLayout()
        self.save_key_btn = QPushButton("保存 Key 与模型名")
        connect_guarded(self.save_key_btn.clicked, lambda *_: self._save_keys(), on_error=lambda msg: self.status.setText(msg), label="save keys")
        r4.addWidget(self.save_key_btn)
        self.key_hint = QLabel("Key 只写进本机配置，不会随报告导出")
        self.key_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        r4.addWidget(self.key_hint)
        r4.addStretch()
        l4.addLayout(r4)
        self._lay_engine.addWidget(c4)

        # ---------------- 运行 ----------------
        c5, l5 = vertical_card("体检", self)
        r5 = QHBoxLayout()
        self.run_btn = QPushButton("开始体检")
        self.run_btn.setObjectName("Primary")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_guarded(self.run_btn.clicked, lambda *_: self._run(), on_error=lambda msg: self.status.setText(msg), label="run")
        r5.addWidget(self.run_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        connect_guarded(self.stop_btn.clicked, lambda *_: self._stop_run(), on_error=lambda msg: self.status.setText(msg), label="stop run")
        r5.addWidget(self.stop_btn)
        self.export_btn = QPushButton("导出 Excel 报告")
        self.export_btn.setEnabled(False)
        connect_guarded(self.export_btn.clicked, lambda *_: self._export(), on_error=lambda msg: self.status.setText(msg), label="export")
        r5.addWidget(self.export_btn)
        self.trend_btn = QPushButton("查看历史趋势")
        connect_guarded(self.trend_btn.clicked, lambda *_: self._show_trend(), on_error=lambda msg: self.status.setText(msg), label="show trend")
        r5.addWidget(self.trend_btn)
        r5.addStretch()
        l5.addLayout(r5)

        self.progress = FlowProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        l5.addWidget(self.progress)
        self.status = StatusLine("")
        self.status.mark("idle")
        self.status.setWordWrap(True)
        l5.addWidget(self.status)
        self._lay_result.addWidget(c5)

        # ---------------- 结果 ----------------
        c6, l6 = vertical_card("体检结果", self)
        self.summary = QLabel("还没有跑过体检")
        self.summary.setStyleSheet("color:#374151;font-size:13px;font-weight:600;")
        self.summary.setWordWrap(True)
        l6.addWidget(self.summary)

        self.table = QTableWidget(0, len(RESULT_COLUMNS))
        self.table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(240)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        l6.addWidget(self.table)

        self.tips = QPlainTextEdit()
        self.tips.setReadOnly(True)
        self.tips.setFixedHeight(150)
        self.tips.setPlaceholderText("跑完体检后，这里给出可执行的优化清单")
        l6.addWidget(self.tips)
        self._lay_result.addWidget(c6)

        # 日志收起：跑完后只看表格和优化清单就够，日志留给"报错了"的时候。
        self.log = log_panel()
        self.log_box = DisclosurePanel("运行日志", meta="出错时才需要看",
                                       mode="light", parent=self)
        self.log_box.add_widget(self.log)
        self._lay_result.addWidget(self.log_box)
        # 每档末尾垫弹簧（原因见 script_page 同处注释）
        for _lay in (self._lay_brand, self._lay_engine, self._lay_result):
            _lay.addStretch(1)
        self._refresh_count()
        self._refresh_estimate()

    # ---------------------------------------------------- 分档 / 主题
    def _sync_step(self, key: str):
        self._view_brand.setVisible(key == "brand")
        self._view_engine.setVisible(key == "engine")
        self._view_result.setVisible(key == "result")

    def apply_theme(self, mode: str):
        """主窗口切主题时广播到这里（用了 kit 自绘控件就必须实现）。"""
        self.step_seg.apply_theme(mode)
        self.log_box.apply_theme(mode)

    # ------------------------------------------------------------ 小工具
    def _refresh_gate(self):
        """功能门禁提示：没买 GEO 就明确告知，不静默失败。

        ★ v1.21.0：按钮不再禁用。禁用按钮 = 免费用户点了没反应，只会觉得
          「这软件坏了」；保持可点，由 _run() 弹统一的「付费须知」。
        """
        ok, why = licensing.check("geo")
        if ok:
            self.gate_hint.setText("")
            return
        self.gate_hint.setStyleSheet("color:#B45309;font-size:12px;font-weight:600;")
        self.gate_hint.setText(
            f"⚠ {why}。GEO 为独立模块，需单独开通后使用（已购采集套餐的客户不受影响）。")

    def _refresh_count(self):
        n = len(_split_lines(self.queries.toPlainText()))
        self.count_hint.setText(f"已填 {n} 个提问词" + ("（建议 20~50 个）" if n < 20 else ""))

    def _selected_engines(self) -> list[str]:
        return [k for k, b in self._engine_boxes.items()
                if b.isEnabled() and b.isChecked()]

    def _samples(self) -> int:
        return [1, 3, 5][max(0, min(self.sample_box.currentIndex(), 2))]

    def _refresh_estimate(self):
        n = len(_split_lines(self.queries.toPlainText()))
        sel = self._selected_engines()
        eng = len(sel)
        samples = self._samples()
        total = n * eng * samples
        if total <= 0:
            self.estimate.setText("填好提问词并勾选引擎后，这里会显示本次要问多少次")
            return
        # 耗时按实测均值估算（2026-10-12 真机：DeepSeek ≈2s/次，Kimi ≈6s/次）
        secs = 0.0
        for e in sel:
            spec = gm.ENGINE_SPECS.get(e)
            if spec is None:
                continue
            per = _ENGINE_SEC_PER_ASK.get(e, 4.0)
            conc = max(1, min(spec.concurrency, 4))
            secs = max(secs, n * samples * per / conc)
        if secs < 60:
            t_txt = f"预计约 {int(secs)} 秒"
        else:
            t_txt = f"预计约 {secs / 60:.0f} 分钟，限流重试时更久"
        self.estimate.setText(
            f"本次将提问 {total} 次（{n} 个词 × {eng} 个引擎 × {samples} 次采样），{t_txt}。"
            "按各平台官方价约几分钱到几毛钱，直接从你填的 Key 里扣，本软件不加价。"
            "失败的提问会自动重试并单独标红，不会被算作「AI 没提到你」。")

    def _save_keys(self):
        keys = dict(self.ctx.get_setting("geo_api_keys", {}) or {})
        models = dict(self.ctx.get_setting("geo_models", {}) or {})
        for k, ed in self._key_edits.items():
            keys[k] = ed.text().strip()
        for k, ed in self._model_edits.items():
            models[k] = ed.text().strip()
        self.ctx.settings["geo_api_keys"] = keys
        self.ctx.settings["geo_models"] = models
        try:
            self.ctx.save_settings()
            self.key_hint.setText("Key 已保存到本机")
            self.key_hint.setStyleSheet("color:#2E7D32;font-size:12px;")
        except Exception as e:                                   # noqa: BLE001
            self.key_hint.setText(f"保存失败：{e}")
            self.key_hint.setStyleSheet("color:#DC2626;font-size:12px;")

    def _api_keys(self) -> dict[str, str]:
        keys = dict(self.ctx.get_setting("geo_api_keys", {}) or {})
        for k, ed in self._key_edits.items():
            v = ed.text().strip()
            if v:
                keys[k] = v
        return keys

    def _models(self) -> dict[str, str]:
        """界面里填的模型名（留空的项不传，交给引擎表默认值）。"""
        out: dict[str, str] = {}
        for k, ed in self._model_edits.items():
            v = ed.text().strip()
            if v:
                out[k] = v
        return out

    def _import_queries(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入提问词", "", "表格文件 (*.xlsx *.xls *.csv)")
        if not path:
            return
        try:
            rows = self._read_first_column(path)
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, "导入失败", f"读不了这个文件：{e}")
            return
        if not rows:
            QMessageBox.information(self, "没有读到内容", "表格第一列里没找到可用的提问词。")
            return
        cur = _split_lines(self.queries.toPlainText())
        for r in rows:
            if r not in cur:
                cur.append(r)
        self.queries.setPlainText("\n".join(cur))
        self.status.setText(f"已导入 {len(rows)} 个提问词，当前共 {len(cur)} 个")

    @staticmethod
    def _read_first_column(path: str) -> list[str]:
        """读表格第一列（xlsx 用 openpyxl，csv 用标准库）。"""
        p = str(path).lower()
        out: list[str] = []
        if p.endswith(".csv"):
            import csv
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.reader(f):
                    if row and row[0].strip():
                        out.append(row[0].strip())
            return out
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        for row in ws.iter_rows(values_only=True):
            if row and row[0]:
                s = str(row[0]).strip()
                if s and s.lower() != "none":
                    out.append(s)
        return out

    def _insert_template(self):
        cur = _split_lines(self.queries.toPlainText())
        for t in _QUERY_TEMPLATES:
            if t not in cur:
                cur.append(t)
        self.queries.setPlainText("\n".join(cur))
        self.status.setText("已插入通用模板，请把「{行业}」替换成客户的行业再跑")

    # ------------------------------------------------------------ 运行
    def _run(self):
        # ★ 付费闸门（v1.21.0）：GEO 是独立售卖的模块，弹窗里补一句说明
        if not require_feature(
            self, "geo", "AI 可见度体检",
            extra="GEO 为独立模块，不随采集套餐附送；已购采集套餐的客户可单独补开。"):
            return
        brand = self.brand.text().strip()
        queries = _split_lines(self.queries.toPlainText())
        engines = self._selected_engines()
        if not brand:
            self.status.setText("请先填品牌名")
            return
        if not queries:
            self.status.setText("请先填至少一个提问词")
            return
        if not engines:
            self.status.setText("请至少勾选一个引擎")
            return
        missing = [gm.ENGINE_SPECS[e].label for e in engines
                   if not (self._api_keys().get(e) or "").strip()]
        if missing:
            QMessageBox.warning(
                self, "缺少接口 Key",
                "这些引擎还没填 Key：" + "、".join(missing)
                + "\n\n请在上面「AI 接口 Key」里填写并保存后再跑，"
                  "否则一个问题也问不出去。")
            return

        self._stop = False
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.progress.setValue(3)
        self.status.mark("idle")
        self.status.setText("体检中…")
        append_log(self.log, f"[GEO] 开始：品牌={brand}，{len(queries)} 词 × {len(engines)} 引擎")

        samples = self._samples()
        aliases = _split_lines(self.alias.text())
        comps = _split_lines(self.competitors.toPlainText())
        keys = self._api_keys()
        models = self._models()      # 必须在主线程取完再进线程，别在子线程碰控件

        def task(worker):
            def on_prog(done, total, text):
                pct = (done / total * 100.0) if total else 0.0
                worker.emit_progress(pct, f"已提问 {done}/{total}：{text}")

            return gm.scan(brand=brand, queries=queries, engines=engines,
                           aliases=aliases, competitors=comps, samples=samples,
                           api_keys=keys, models=models, progress=on_prog,
                           should_stop=lambda: self._stop)

        w = Worker(task)
        self._worker = w
        w.progress.connect(self._on_progress)
        w.log.connect(lambda m: append_log(self.log, m) if m else None)
        w.done.connect(self._on_done)
        w.error.connect(lambda e: self.status.setText(f"体检出错：{e}"))
        w.finished.connect(self._on_finished)
        w.start()

    def _on_progress(self, pct, msg):
        self.progress.setValue(int(pct))
        if msg:
            self.status.setText(msg)

    def _stop_run(self):
        self._stop = True
        self.stop_btn.setEnabled(False)
        self.status.setText("正在停止…（已发出的提问会跑完）")

    def _on_finished(self):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setValue(100)
        self._refresh_gate()

    def _on_done(self, report):
        self._report = report
        # ★ 分档后必须自己把客户带到结果档：体检一次要几十秒，跑完不切过去
        #   就等于"结束了但没给结论"。成功失败都切 —— 失败原因写在结果上方。
        self.step_seg.set_value("result")
        rows = report.items
        self.table.setRowCount(len(rows))
        for i, item in enumerate(rows):
            r = item.to_row()
            for c, col in enumerate(RESULT_COLUMNS):
                cell = QTableWidgetItem(str(r.get(col, "")))
                # 失败的提问必须一眼看出来：否则客户会把「接口挂了」
                # 读成「AI 不知道我的品牌」，据此做错误决策
                if item.failed:
                    cell.setForeground(QBrush(QColor("#DC2626")))
                    if col == "状态":
                        f = cell.font()
                        f.setBold(True)
                        cell.setFont(f)
                self.table.setItem(i, c, cell)

        if report.total_answers == 0:
            self.export_btn.setEnabled(False)
            self.summary.setStyleSheet("color:#DC2626;font-size:13px;font-weight:600;")
            err = (report.errors[0] if report.errors else "未知原因")
            self.summary.setText(f"一个回答都没拿到：{err}")
            self.status.setText("体检失败：" + err)
            self.tips.setPlainText(
                "排查顺序：1) Key 是否有效、有没有余额；2) 网络/代理能不能连到接口；"
                "3) 引擎是否限流。换一个引擎先跑 1 个词试试。")
            return

        self.export_btn.setEnabled(True)
        rate = report.mention_rate
        color = "#2E7D32" if rate >= 0.3 else ("#B45309" if rate > 0 else "#DC2626")
        self.summary.setStyleSheet(f"color:{color};font-size:13px;font-weight:600;")
        per_engine = "，".join(
            f"{gm.ENGINE_SPECS.get(k, gm.EngineSpec(k, k, '')).label} {v * 100:.0f}%"
            for k, v in report.by_engine().items())
        comp_txt = "；".join(f"{n} {r * 100:.0f}%"
                             for n, _h, r in report.competitor_ranking()[:3])
        warn = (f"　⚠ {report.failed_count} 个提问失败（多为限流），结论不完整"
                if report.failed_count else "")
        self.summary.setText(
            f"整体提及率 {rate * 100:.1f}%（{report.total_mentions}/{report.total_answers} 个回答提到）"
            + (f"　·　各引擎：{per_engine}" if per_engine else "")
            + (f"　·　竞品：{comp_txt}" if comp_txt else "")
            + warn)
        self.status.mark("ok")
        self.status.setText(
            f"体检完成：{len(rows)} 条记录，已存一份快照，可在「查看历史趋势」里比对")
        self.tips.setPlainText("\n".join(f"{i + 1}. {t}"
                                         for i, t in enumerate(report.suggestions())))
        try:
            gm.save_snapshot(report)
        except Exception as e:                                   # noqa: BLE001
            append_log(self.log, f"[GEO] 快照保存失败：{e}")

    # ------------------------------------------------------------ 导出 / 趋势
    def _export(self):
        if self._report is None or self._report.total_answers == 0:
            self.status.setText("还没有可导出的结果")
            return
        safe = re.sub(r'[\\/:*?"<>|]+', "_", self._report.brand).strip() or "品牌"
        name = f"AI可见度报告_{safe}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出体检报告", name, "Excel (*.xlsx)")
        if not path:
            return
        try:
            gm.export_report_excel(self._report, path)
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.status.mark("ok")
        self.status.setText(f"报告已导出：{path}")
        try:
            from justoneapi_collector.support import open_path
            open_path(path)
        except Exception:                                        # noqa: BLE001
            pass

    def _show_trend(self):
        snaps = gm.load_snapshots(limit=20)
        if not snaps:
            QMessageBox.information(self, "历史趋势",
                                    "还没有历史快照。跑完第一次体检后会自动存一份，"
                                    "以后每月复测就能看到提及率的变化曲线。")
            return
        lines = []
        prev = None
        for s in snaps:
            t = str(s.get("started_at") or "")[:16]
            r = float(s.get("mention_rate") or 0)
            delta = ""
            if prev is not None:
                d = (r - prev) * 100
                delta = f"（{'+' if d >= 0 else ''}{d:.1f} 个百分点）"
            lines.append(f"{t}　{s.get('brand', '')}　提及率 {r * 100:.1f}%{delta}")
            prev = r
        QMessageBox.information(
            self, "历史趋势（最近 %d 次）" % len(snaps),
            "\n".join(lines) + "\n\n提示：AI 收录内容以月为单位，请按月复测看趋势，"
                               "单次波动不代表效果。")
