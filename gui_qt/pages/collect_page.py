"""数据采集页（PySide6）。

功能：平台分类 → 平台 → 接口 三级联动，动态参数行，执行采集（client.request），
结果自动扁平化到表格，支持 JSON / CSV / Excel 导出。业务逻辑复用 justoneapi_collector。
"""
import json
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHeaderView, QLabel, QLineEdit,
                               QHBoxLayout, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ._base import connect_guarded
from ._base import (StatusLine, Worker, append_log, card, form_row, guide_banner,
                    log_panel, page_title, row_widget, vertical_card)

from justoneapi_collector.platforms import list_categories, list_platforms
from justoneapi_collector.platforms.registry import get_platform_meta

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.progress import FlowProgressBar
from ._base import require_feature


class CollectPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.param_rows = []        # [(key_edit, value_edit)]
        self.current_results = []
        self._build_ui()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("数据采集"))
        root.addWidget(guide_banner("数据采集", self))

        # 顶部：三级联动 + 接口说明
        top, top_l = vertical_card("接口选择", self)
        sel = QHBoxLayout() if False else QVBoxLayout()
        sel.setSpacing(8)

        # 分类 / 平台 / 接口 一行
        combo_row = QHBoxLayout()
        combo_row.setSpacing(10)
        self.category_combo = QComboBox(); self.category_combo.setMinimumWidth(160)
        self.platform_combo = QComboBox(); self.platform_combo.setMinimumWidth(180)
        self.endpoint_combo = QComboBox(); self.endpoint_combo.setMinimumWidth(260)
        combo_row.addWidget(QLabel("分类")); combo_row.addWidget(self.category_combo, 1)
        combo_row.addWidget(QLabel("平台")); combo_row.addWidget(self.platform_combo, 1)
        combo_row.addWidget(QLabel("接口")); combo_row.addWidget(self.endpoint_combo, 2)
        sel.addLayout(combo_row)

        self.desc_label = QLabel("请选择接口")
        self.desc_label.setStyleSheet("color:#6B7280;font-size:12px;")
        sel.addWidget(self.desc_label)
        top_l.addLayout(sel)

        # 参数区
        pf = QVBoxLayout()
        pf.setSpacing(6)
        self.params_container = QVBoxLayout()
        self.params_container.setSpacing(6)
        pf.addLayout(self.params_container)
        pbtns = QHBoxLayout()
        pbtns.setSpacing(8)
        b_add = QPushButton("添加参数"); connect_guarded(b_add.clicked, lambda *_: self._add_param_row(), on_error=lambda msg: self.status.setText(msg), label="add param row")
        b_clr = QPushButton("清空"); connect_guarded(b_clr.clicked, lambda *_: self._clear_params(), on_error=lambda msg: self.status.setText(msg), label="clear params")
        pbtns.addWidget(b_add); pbtns.addWidget(b_clr); pbtns.addStretch()
        pf.addLayout(pbtns)
        top_l.addLayout(pf)

        self.execute_btn = QPushButton("执行采集")
        self.execute_btn.setObjectName("Primary")
        connect_guarded(self.execute_btn.clicked, lambda *_: self._execute(), on_error=lambda msg: self.status.setText(msg), label="execute")
        top_l.addWidget(self.execute_btn)
        root.addWidget(top)

        # ★ v1.14.4：进度条与状态从页面最底部移到结果卡上方 ——
        #   表格撑高后它们会被顶出视口，客户点完「开始采集」看不到进度。
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = StatusLine("就绪")
        root.addWidget(self.status)

        # 结果区
        res, res_l = vertical_card("采集结果", self)
        self.table = QTableWidget(0, 0)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        res_l.addWidget(self.table, 1)

        ex = QHBoxLayout(); ex.setSpacing(8)
        b_json = QPushButton("导出 JSON"); connect_guarded(b_json.clicked, lambda *_: self._export("json"), on_error=lambda msg: self.status.setText(msg), label="export")
        b_csv = QPushButton("导出 CSV"); connect_guarded(b_csv.clicked, lambda *_: self._export("csv"), on_error=lambda msg: self.status.setText(msg), label="export")
        b_xlsx = QPushButton("导出 Excel"); connect_guarded(b_xlsx.clicked, lambda *_: self._export("xlsx"), on_error=lambda msg: self.status.setText(msg), label="export")
        ex.addWidget(b_json); ex.addWidget(b_csv); ex.addWidget(b_xlsx); ex.addStretch()
        res_l.addLayout(ex)
        root.addWidget(res, 1)


        self._init_combos()
        self._add_param_row()

    def _init_combos(self):
        cats = list_categories()
        self.category_combo.addItems([c for c in cats])
        self.category_combo.currentTextChanged.connect(self._on_category)
        self.platform_combo.currentTextChanged.connect(self._on_platform)
        self.endpoint_combo.currentTextChanged.connect(self._on_endpoint)
        if self.category_combo.count():
            self._on_category(self.category_combo.currentText())

    def _platforms_in_category(self, cat):
        return [p for p in list_platforms() if p["category"] == cat]

    def _on_category(self, cat):
        self.platform_combo.clear()
        for p in self._platforms_in_category(cat):
            self.platform_combo.addItem(f"{p['name']} ({p['slug']})", p["slug"])
        if self.platform_combo.count():
            self._on_platform(self.platform_combo.currentData())

    def _on_platform(self, _slug):
        slug = self.platform_combo.currentData()
        self.endpoint_combo.clear()
        if not slug:
            return
        meta = get_platform_meta(slug) or {}
        for ep in meta.get("endpoints", []):
            path, method, desc = (list(ep) + ["", "", ""])[:3]
            self.endpoint_combo.addItem(f"{path}  [{method}]", path)
            # 用 itemData 存描述不太好，这里直接展示当前项描述
        if self.endpoint_combo.count():
            self._on_endpoint(self.endpoint_combo.currentData())

    def _on_endpoint(self, _path):
        slug = self.platform_combo.currentData()
        path = self.endpoint_combo.currentData()
        if not slug or not path:
            self.desc_label.setText("请选择接口")
            return
        meta = get_platform_meta(slug) or {}
        for ep in meta.get("endpoints", []):
            if ep[0] == path and len(ep) > 2:
                self.desc_label.setText(str(ep[2]))
                return
        self.desc_label.setText(path)

    # --------------------------------------------------- 参数行
    def _add_param_row(self):
        h = QHBoxLayout(); h.setSpacing(8)
        k = QLineEdit(); k.setPlaceholderText("参数名")
        v = QLineEdit(); v.setPlaceholderText("参数值")
        rm = QPushButton("✕"); rm.setFixedWidth(32)
        h.addWidget(k, 1); h.addWidget(v, 2); h.addWidget(rm)
        self.params_container.addLayout(h)
        self.param_rows.append((k, v, h, rm))

        def _remove():
            self._clear_layout(h)
            self.param_rows = [r for r in self.param_rows if r[2] is not h]
        connect_guarded(rm.clicked, lambda *_: _remove(), on_error=lambda msg: self.status.setText(msg), label="remove")

    def _clear_params(self):
        for (_k, _v, h, _rm) in self.param_rows:
            self._clear_layout(h)
        self.param_rows = []
        self._add_param_row()

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                CollectPage._clear_layout(item.layout())

    def _get_params(self) -> dict:
        params = {}
        for (k, v, _h, _rm) in self.param_rows:
            key = k.text().strip()
            val = v.text().strip()
            if not key:
                continue
            if val.isdigit():
                params[key] = int(val)
            else:
                try:
                    params[key] = float(val)
                except ValueError:
                    if val.lower() in ("true", "false"):
                        params[key] = val.lower() == "true"
                    else:
                        params[key] = val
        return params

    # --------------------------------------------------- 执行
    def _execute(self):
        # ★ 付费闸门必须放在最前面（v1.21.0）：免费档点「执行采集」要先看到
        #   付费须知，而不是跑起来之后收一堆失败 —— 那是引流入口变差评的来源。
        if not require_feature(self, "collect", "执行采集"):
            return
        if self.ctx.demo:
            self.status.setText("演示模式：未连接 API，请先配置 Token（设置页）")
            return
        slug = self.platform_combo.currentData()
        path = self.endpoint_combo.currentData()
        if not slug or not path:
            self.status.setText("请先选择平台和接口")
            return
        params = self._get_params()
        # 记下当前接口，供「批量采集」页「取采集页接口」使用
        self.ctx.collect_current = (slug, path, params)
        self.execute_btn.setEnabled(False)
        self.progress.setValue(10)
        self.status.setText(f"正在采集：{slug}/{path} …")

        def run(worker):
            resp = self.ctx.client.request(slug, path, params)
            return resp
        w = Worker(run)
        w.done.connect(lambda resp: self._on_done(resp, slug, path, params))
        w.error.connect(lambda e: self._on_error(e))
        w.start()
        self._worker = w

    def _on_done(self, resp, slug, path, params):
        self.execute_btn.setEnabled(True)
        self.progress.setValue(100)
        if getattr(resp, "success", False):
            self.status.setText(f"采集成功：{slug}/{path}")
            self._display(resp.data, slug, path)
        else:
            self.status.setText(f"采集失败：[{getattr(resp,'code','?')}] {getattr(resp,'message','')}")

    def _on_error(self, e):
        self.execute_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText(f"错误：{e}")

    # --------------------------------------------------- 结果展示
    @staticmethod
    def _extract_items(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "list", "data", "results", "cards", "feeds",
                        "realtime", "notes", "aweme_list"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
        return [{"value": data}]

    @staticmethod
    def _flatten(data, prefix=""):
        flat = {}
        if isinstance(data, dict):
            for k, v in data.items():
                key = f"{prefix}.{k}" if prefix else k
                flat.update(CollectPage._flatten(v, key))
        elif isinstance(data, list):
            if not data:
                flat[prefix] = "[]"
            elif len(data) <= 3:
                for i, item in enumerate(data):
                    flat.update(CollectPage._flatten(item, f"{prefix}[{i}]"))
            else:
                flat[prefix] = f"[{len(data)} items]"
        else:
            flat[prefix] = data
        return flat

    def _display(self, data, slug, path):
        items = self._extract_items(data)
        flat_items = []
        all_keys = set()
        for it in items:
            f = self._flatten(it)
            flat_items.append(f)
            all_keys.update(f.keys())
        cols = ["序号", "平台", "接口"] + sorted(all_keys)
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(flat_items))
        for i, f in enumerate(flat_items):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(slug))
            self.table.setItem(i, 2, QTableWidgetItem(path))
            for c, key in enumerate(cols[3:], start=3):
                val = f.get(key, "")
                self.table.setItem(i, c, QTableWidgetItem("" if val is None else str(val)[:500]))
        self.current_results = flat_items
        self.status.setText(f"显示 {len(flat_items)} 条结果")

    # --------------------------------------------------- 导出
    def _export(self, kind):
        if not self.current_results:
            self.status.setText("暂无结果可导出")
            return
        if self.ctx.demo:
            self.status.setText("演示模式：导出功能需连接 API 后使用")
            return
        try:
            from PySide6.QtWidgets import QFileDialog
            ext = {"json": "json", "csv": "csv", "xlsx": "xlsx"}[kind]
            path, _ = QFileDialog.getSaveFileName(self, "导出结果", f"采集结果.{ext}",
                                                  f"*.{ext}")
            if not path:
                return
            if kind == "json":
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(self.current_results, fh, ensure_ascii=False, indent=2)
            elif kind == "csv":
                import csv
                keys = ["序号", "平台", "接口"] + sorted(
                    {k for r in self.current_results for k in r})
                with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=keys)
                    w.writeheader()
                    for r in self.current_results:
                        w.writerow(r)
            else:
                self._export_xlsx(path)
            self.status.setText(f"已导出：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"导出失败：{e}")

    def _export_xlsx(self, path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        keys = ["序号", "平台", "接口"] + sorted({k for r in self.current_results for k in r})
        ws.append(keys)
        for r in self.current_results:
            ws.append([r.get(k, "") for k in keys])
        wb.save(path)
