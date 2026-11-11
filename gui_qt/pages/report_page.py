"""周报页（PySide6）。

功能：基于「复盘监控」积累的快照数据生成周期报告（Excel / HTML）。
复用 justoneapi_collector.reporter。无真实项目数据时降级为演示提示。
"""
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                               QWidget)

from ._base import danger_button, guide_banner, page_title, vertical_card, StatusLine

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from ._base import require_feature


class ReportPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._data = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("运营周报"))
        root.addWidget(guide_banner("报告", self))

        top, top_l = vertical_card("报告参数", self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("统计周期(天)："))
        self.days = QLineEdit("7"); self.days.setFixedWidth(60)
        r1.addWidget(self.days)
        r1.addWidget(QLabel("（基于「复盘监控」积累的快照数据）"))
        r1.addStretch()
        top_l.addLayout(r1)

        r2 = QHBoxLayout()
        b_gen = QPushButton("生成报告"); b_gen.setObjectName("Primary")
        b_gen.clicked.connect(self._generate)
        b_xlsx = QPushButton("导出 Excel"); b_xlsx.clicked.connect(self._export_xlsx)
        b_html = QPushButton("导出 HTML"); b_html.clicked.connect(self._export_html)
        b_open = QPushButton("打开报告目录")
        r2.addWidget(b_gen); r2.addWidget(b_xlsx); r2.addWidget(b_html); r2.addWidget(b_open)
        r2.addStretch()
        top_l.addLayout(r2)
        root.addWidget(top)

        body, body_l = vertical_card("报告结论", self)
        self.conclusion = QLabel("先点「生成报告」。报告会基于复盘监控的快照，给出增长亮点、最佳发布时段、待优化项。")
        self.conclusion.setStyleSheet("color:#374151;font-size:13px;line-height:1.6;")
        self.conclusion.setWordWrap(True)
        body_l.addWidget(self.conclusion)
        body_l.addStretch()
        root.addWidget(body, 1)

        self.status = StatusLine("就绪")
        root.addWidget(self.status)

    def _generate(self):
        # ★ 付费闸门（v1.21.0）：报告是付费功能。导出按钮自带「请先生成报告」
        #   兜底，所以只拦生成这一个入口就够了。
        if not require_feature(self, "report", "生成数据报告"):
            return
        pm = self.ctx.project_manager
        if pm is None:
            self.status.setText("演示模式：周报需先创建项目并完成复盘监控积累数据")
            self.conclusion.setText(
                "演示模式说明：\n"
                "· 在真实环境里，本页会读取「复盘监控」里各作者的粉丝/作品/获赞快照，\n"
                "· 自动计算增长亮点、最佳发布时段（analyze_best_hours）、待优化项，\n"
                "· 一键导出 Excel 与 HTML 报告发给客户或团队。\n"
                "请先配置项目与数据后再生成。")
            return
        try:
            days = max(1, int(self.days.text() or 7))
        except ValueError:
            days = 7
        try:
            project = pm.current_project()
            tower = pm.watch_tower(project)
            from justoneapi_collector.reporter import build_report
            data = build_report(project, tower, period_days=days)
            self._data = data
            self.conclusion.setText("\n".join(build_highlights(data)) if (build_highlights := _highlights()) else "报告已生成")
            self.status.setText(f"已生成 {data.period_text} 报告")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"生成失败：{e}")

    def _export_xlsx(self):
        if self._data is None:
            self.status.setText("请先生成报告")
            return
        from PySide6.QtWidgets import QFileDialog
        from justoneapi_collector.reporter import export_excel_report
        path, _ = QFileDialog.getSaveFileName(self, "导出报告", "运营周报.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_excel_report(self._data, path)
            self.status.setText(f"已导出：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"导出失败：{e}")

    def _export_html(self):
        if self._data is None:
            self.status.setText("请先生成报告")
            return
        from PySide6.QtWidgets import QFileDialog
        from justoneapi_collector.reporter import export_html_report
        path, _ = QFileDialog.getSaveFileName(self, "导出报告", "运营周报.html", "HTML (*.html)")
        if not path:
            return
        try:
            export_html_report(self._data, path)
            self.status.setText(f"已导出：{path}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"导出失败：{e}")


def _highlights():
    from justoneapi_collector.reporter import build_highlights
    return build_highlights
