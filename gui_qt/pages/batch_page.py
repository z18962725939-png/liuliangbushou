"""批量采集页（PySide6）。

功能：任务表格（平台/接口/参数/状态/消息），新建任务、从采集页取当前接口、
生成示例任务、加载/保存 JSON 任务文件、执行批量采集（带进度）、导出全部结果。
"""
import json
import sys

from PySide6.QtWidgets import (QHeaderView, QHBoxLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import Worker, guide_banner, page_title, require_feature, row_widget, vertical_card
from ._base import StatusLine

from gui_qt.widgets.kit import Segmented
from gui_qt.widgets.progress import FlowProgressBar


class BatchPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.tasks = []  # list[dict]
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("批量采集"))
        root.addWidget(guide_banner("批量采集", self))

        # ★ 2026-10-26 改版（客户反馈「一屏全是按钮，不知道该先点哪个」）
        #   原来这里是两排共 9 颗按钮平铺：新建/取接口/示例/加载/保存
        #   ┃ 执行/导出/移除/清空，右边的提示文字被挤到换行。
        #   9 颗同等重量的灰块 = 没有主次，客户得逐个读标题才知道哪颗是"开工"。
        #   现在：动作按「手动添加 / 文件导入 / 导出与清理」三档收起（页面内
        #   第三级），一屏只露当前档的 2~3 颗；卡片头上只留一颗柠檬主按钮。
        card_, cl = vertical_card("", self)
        cl.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(12)
        t = QLabel("任务队列")
        t.setObjectName("H2")
        head.addWidget(t)
        sub = QLabel("按顺序一次跑完 · 可导出 JSON 改完再导入")
        sub.setObjectName("Caption")
        head.addWidget(sub)
        head.addStretch()
        self.exec_btn = QPushButton("▶ 执行批量采集")
        self.exec_btn.setObjectName("Primary")
        self.exec_btn.clicked.connect(self._execute)
        head.addWidget(self.exec_btn)
        cl.addLayout(head)

        self.mode_seg = Segmented([("手动添加", "manual"),
                                   ("文件导入", "file"),
                                   ("导出与清理", "export")])
        self.mode_seg.changed.connect(self._switch_mode)
        seg_row = QHBoxLayout()
        seg_row.setSpacing(10)
        seg_row.addWidget(self.mode_seg)
        seg_row.addStretch()
        cl.addLayout(seg_row)

        self._mode_boxes = {}
        self._mode_box(cl, "manual", [
            ("新建任务", self._new_task),
            ("生成示例任务", self._sample),
            ("取采集页接口", self._add_current),
        ], "手动档：新建后直接在表格里改 JSON 参数")
        self._mode_box(cl, "file", [
            ("加载任务文件", self._load),
            ("保存任务文件", self._save),
        ], "支持 .json（任务列表，或 {\"tasks\": [...]}）")
        self._mode_box(cl, "export", [
            ("导出全部结果", self._export),
            ("移除选中", self._remove_selected),
            ("清空任务", self._clear),
        ], "导出会连同执行状态一起写进 JSON")

        # 进度与状态同一行：原来各占一行，而状态多数时候只有两个字「就绪」，
        # 单独占一整行纯属浪费首屏高度。
        prog = QHBoxLayout()
        prog.setSpacing(12)
        self.progress = FlowProgressBar()
        self.progress.setValue(0)
        prog.addWidget(self.progress, 1)
        self.status = StatusLine("请新建任务，或从文件导入")
        self.status.mark("idle")
        prog.addWidget(self.status)
        cl.addLayout(prog)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["平台", "接口", "参数(JSON)", "状态", "消息"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        cl.addWidget(self.table, 1)
        root.addWidget(card_, 1)

    # ---------------------------------------------------------------- 三档切换
    def _mode_box(self, cl, key: str, specs, tip: str) -> QWidget:
        """建一个「档」：一排动作 + 一句说明。同一时刻只显示一档。"""
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for text, slot in specs:
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        lab = QLabel(tip)
        lab.setObjectName("Caption")
        h.addWidget(lab)
        h.addStretch()
        box.setVisible(key == "manual")
        cl.addWidget(box)
        self._mode_boxes[key] = box
        return box

    def _switch_mode(self, key: str):
        for k, box in self._mode_boxes.items():
            box.setVisible(k == key)

    def apply_theme(self, mode: str):
        """主题切换时把档位控件（自绘 QSS 之外的那部分）也刷一遍。"""
        try:
            self.mode_seg.apply_theme(mode)
        except Exception:                                   # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 任务维护
    def _add_task(self, platform="", endpoint="", params=None, status="待执行", message=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(platform))
        self.table.setItem(r, 1, QTableWidgetItem(endpoint))
        self.table.setItem(r, 2, QTableWidgetItem(json.dumps(params or {}, ensure_ascii=False)))
        self.table.setItem(r, 3, QTableWidgetItem(status))
        self.table.setItem(r, 4, QTableWidgetItem(message))
        self.tasks.append({"platform": platform, "endpoint": endpoint,
                           "params": params or {}, "status": status, "message": message})

    def _sync_tasks_from_table(self):
        self.tasks = []
        for r in range(self.table.rowCount()):
            def g(c):
                it = self.table.item(r, c)
                return it.text() if it else ""
            plat, ep, pj = g(0), g(1), g(2)
            try:
                params = json.loads(pj) if pj.strip() else {}
            except Exception:
                params = {"_raw": pj}
            self.tasks.append({"platform": plat, "endpoint": ep, "params": params})

    def _new_task(self):
        self._add_task()

    def _add_current(self):
        cur = getattr(self.ctx, "collect_current", None)
        if not cur:
            self.status.setText("采集页还没有选中的接口，请先去「数据采集」页选好接口并执行一次")
            return
        slug, path, params = cur
        self._add_task(slug, path, params, "待执行", "来自采集页")

    def _sample(self):
        samples = [
            ("douyin", "aweme-post-v1", {"aweme_id": "7300000000000000000"}),
            ("xiaohongshu", "note-search-v2", {"keyword": "美妆", "page": 1}),
            ("kuaishou", "video-details-v2", {"video_id": "3xexample"}),
        ]
        for s in samples:
            self._add_task(*s, status="待执行", message="示例")

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self._sync_tasks_from_table()

    def _clear(self):
        self.table.setRowCount(0)
        self.tasks = []

    def _load(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "加载任务文件", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            items = data if isinstance(data, list) else data.get("tasks", [])
            self._clear()
            for it in items:
                self._add_task(
                    it.get("platform", ""), it.get("endpoint", ""),
                    it.get("params", {}), it.get("status", "待执行"),
                    it.get("message", ""))
            self.status.setText(f"已加载 {len(items)} 个任务：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"加载失败：{e}")

    def _save(self):
        from PySide6.QtWidgets import QFileDialog
        self._sync_tasks_from_table()
        path, _ = QFileDialog.getSaveFileName(self, "保存任务文件", "批量任务.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.tasks, fh, ensure_ascii=False, indent=2)
            self.status.setText(f"已保存：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"保存失败：{e}")

    # ---------------------------------------------------------------- 执行
    def _execute(self):
        # ★ 付费闸门放在最前面（v1.21.0）：免费档点「执行批量采集」要先看到
        #   付费须知，而不是等表格里刷出一片失败。
        if not require_feature(self, "batch", "执行批量采集"):
            return
        self._sync_tasks_from_table()
        if not self.tasks:
            self.status.setText("没有可执行任务")
            return
        if self.ctx.demo:
            self.status.setText("演示模式：未连接 API，无法执行批量采集")
            return

        total = len(self.tasks)
        done = 0

        def run(worker):
            results = []
            for idx, t in enumerate(self.tasks):
                try:
                    resp = self.ctx.client.request(
                        t["platform"], t["endpoint"], t.get("params", {}))
                    ok = getattr(resp, "success", False)
                    msg = "" if ok else getattr(resp, "message", "")
                    results.append({"ok": ok, "message": msg})
                except Exception as e:  # noqa: BLE001
                    results.append({"ok": False, "message": str(e)})
                done = idx + 1
                worker.emit_progress(done / total * 100, f"进度 {done}/{total}")
            return results

        def on_progress(pct, msg):
            self.progress.setValue(int(pct))
            if msg:
                self.status.setText(msg)

        def on_done(results):
            self.exec_btn.setEnabled(True)
            for r in range(self.table.rowCount()):
                res = results[r] if r < len(results) else {"ok": False, "message": ""}
                self.table.setItem(r, 3, QTableWidgetItem("成功" if res["ok"] else "失败"))
                self.table.setItem(r, 4, QTableWidgetItem(res["message"]))
            ok_n = sum(1 for x in results if x["ok"])
            self.status.setText(f"批量完成：成功 {ok_n}/{total}")

        def on_error(e):
            self.exec_btn.setEnabled(True)
            self.status.setText(f"执行出错：{e}")

        self.exec_btn.setEnabled(False)
        w = Worker(run)
        w.progress.connect(on_progress)
        w.done.connect(on_done)
        w.error.connect(on_error)
        w.start()
        self._worker = w

    def _export(self):
        self._sync_tasks_from_table()
        if not self.tasks:
            self.status.setText("暂无任务可导出")
            return
        if self.ctx.demo:
            self.status.setText("演示模式：导出需连接 API 后使用")
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "导出全部结果", "批量结果.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.tasks, fh, ensure_ascii=False, indent=2)
            self.status.setText(f"已导出：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"导出失败：{e}")
