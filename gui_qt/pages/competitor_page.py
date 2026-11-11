"""竞品账号体检页（PySide6）。

功能：粘贴抖音链接/主页/ sec_uid（每行一个）批量体检，开始体检（competitor_check），
结果表格展示，导出 Excel。无真实 client 时降级为演示模式。
"""
import re

from PySide6.QtWidgets import (QHeaderView, QHBoxLayout, QLabel, 
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import (StatusLine, Worker, confirm_destructive, danger_button,
                    guide_banner, page_title, vertical_card)

from justoneapi_collector.competitor_check import (COMPETITOR_COLUMNS,
                                                  check_accounts,
                                                  export_competitor_excel)


class CompetitorPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.rows = []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("竞品账号体检（抖音）"))
        root.addWidget(guide_banner("竞品体检", self))

        intro, il = vertical_card("批量粘贴抖音分享短链 / 主页链接 / 直接 sec_uid", self)
        il.addWidget(QLabel("每行一个，支持批量体检"))
        self.targets = _TextEdit()
        self.targets.setMaximumHeight(110)
        il.addWidget(self.targets)

        ctrl = QHBoxLayout()
        b_start = QPushButton("开始体检"); b_start.setObjectName("Primary")
        b_start.clicked.connect(self._run)
        self.export_btn = QPushButton("导出Excel"); self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        ctrl.addWidget(b_start); ctrl.addWidget(self.export_btn)
        # ★ 这行说明有 30 多个字，QLabel 默认不换行也不压缩，尺寸提示 650px，
        #   加上左边三个按钮就把整页顶宽 113px（视口 950），右侧表格列被切。
        #   允许换行 + 给一个最小宽度就够了，一句话就不再决定整页宽度。
        #
        #   ★★ 不要再设成横向 Ignored：Ignored + wordWrap 会让这个标签的宽度
        #   被压到 0，于是「指标：粉丝数…」逐字换行，撑出一块 265px 高的空白，
        #   输入框和按钮之间出现一大片莫名其妙的留白（2026-10-17 踩过）。
        metrics_hint = QLabel("指标：粉丝数 / 作品数 / 获赞总数 / 平均互动 / 赞粉比 / 近30天更新 / 爆款Top5")
        metrics_hint.setWordWrap(True)
        metrics_hint.setMinimumWidth(220)
        metrics_hint.setSizePolicy(QSizePolicy.Policy.Preferred,
                                   QSizePolicy.Policy.Preferred)
        ctrl.addWidget(metrics_hint)
        ctrl.addStretch()
        il.addLayout(ctrl)
        root.addWidget(intro)

        # ★ v1.14.4：进度条与状态上移到结果卡之前（原来在表格下方，会被顶出视口）
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = StatusLine("就绪")
        root.addWidget(self.status)

        tb, tb_l = vertical_card("体检结果", self)
        labels = [label for _, label in COMPETITOR_COLUMNS]
        self.table = QTableWidget(0, len(labels))
        self.table.setHorizontalHeaderLabels(labels)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        tb_l.addWidget(self.table, 1)
        root.addWidget(tb, 1)


    def _run(self):
        # ★ 付费闸门放在最前面（v1.21.0）：竞品体检要拉账号作品列表，有接口成本
        if not require_feature(self, "analyze", "竞品账号体检"):
            return
        text = self.targets.toPlainText().strip()
        targets = [t.strip() for t in re.split(r"[\n,，;；]+", text) if t.strip()]
        if not targets:
            self.status.setText("请输入至少一个抖音链接或 sec_uid")
            return
        if self.ctx.demo:
            self.status.setText("演示模式：未连接 API，无法体检")
            return
        self.export_btn.setEnabled(False)
        self.table.setRowCount(0)
        self.progress.setValue(10)
        self.status.setText(f"体检中：共 {len(targets)} 个目标 …")

        def run(worker):
            return check_accounts(self.ctx.client, targets)
        w = Worker(run)
        w.done.connect(lambda r: self._on_done(r, targets))
        w.error.connect(lambda e: self.status.setText(f"体检出错：{e}"))
        w.start()

    def _on_done(self, result, targets):
        rows, errors = result
        self.progress.setValue(100)
        self.rows = rows
        keys = [k for k, _ in COMPETITOR_COLUMNS]
        self.table.setRowCount(len(rows))
        ok_n = 0
        for i, r in enumerate(rows):
            for c, k in enumerate(keys):
                self.table.setItem(i, c, QTableWidgetItem(str(r.get(k, ""))))
            name = str(r.get("nickname", ""))
            if name and not name.startswith(("解析失败", "资料失败")):
                ok_n += 1
        self.status.setText(f"体检完成：成功 {ok_n}/{len(rows)}，失败 {len(errors)}")
        if rows:
            self.export_btn.setEnabled(True)
        token_dead = bool(getattr(self.ctx.client, "token_invalid", False))
        if token_dead:
            self.status.setText("⚠ 体检失败：API Token 无效或已失效，请到「设置」页更新")
        elif errors:
            self.status.setText(f"部分失败：{errors[0]}")

    def _export(self):
        if not self.rows:
            self.status.setText("暂无体检结果")
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "导出竞品体检", "竞品体检.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_competitor_excel(self.rows, path)
            self.status.setText(f"已导出：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"导出失败：{e}")


# 用一个普通 QPlainTextEdit 包成 text area 即可（PySide6 没有 Tk 的 Text）
from PySide6.QtWidgets import QPlainTextEdit  # noqa: E402

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.progress import FlowProgressBar
from PySide6.QtWidgets import QSizePolicy
from ._base import require_feature


class _TextEdit(QPlainTextEdit):
    def toPlainText(self):  # 与 Tk Text 同名方法保持一致
        return super().toPlainText()
