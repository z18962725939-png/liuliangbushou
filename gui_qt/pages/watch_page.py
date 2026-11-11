"""复盘监控页（PySide6）。

功能：监控作者列表 + 定期快照（粉丝/作品/获赞变化）。复用 projects.ProjectManager。
无真实数据时降级为演示。
"""
from PySide6.QtWidgets import (QHeaderView, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ._base import (StatusLine, confirm_destructive, danger_button, guide_banner,
                    page_title, platform_label, require_feature, vertical_card)


class WatchPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("复盘监控"))
        root.addWidget(guide_banner("复盘监控", self))

        top, top_l = vertical_card("添加监控作者（自动定期记录粉丝/作品/获赞变化）", self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("作者主页链接 / sec_uid："))
        self.input = QLineEdit(); self.input.setPlaceholderText("粘贴抖音主页链接或直接贴 sec_uid")
        r1.addWidget(self.input, 1)
        b_add = QPushButton("加入监控"); b_add.setObjectName("Primary")
        b_add.clicked.connect(self._add)
        r1.addWidget(b_add)
        top_l.addLayout(r1)
        r2 = QHBoxLayout()
        b_refresh = QPushButton("立即刷新快照"); b_refresh.clicked.connect(self._refresh)
        b_rm = QPushButton("移除选中"); b_rm.clicked.connect(self._remove)
        # 清空整张监控表：破坏性动作，样式弱化 + 二次确认
        self.clear_btn = danger_button("清空列表")
        self.clear_btn.setToolTip("清空本页监控列表（不删除已积累的历史快照）")
        self.clear_btn.clicked.connect(self._clear)
        r2.addWidget(b_refresh); r2.addWidget(b_rm); r2.addWidget(self.clear_btn)
        r2.addStretch()
        top_l.addLayout(r2)
        root.addWidget(top)

        tb, tb_l = vertical_card("监控列表", self)
        cols = ["作者", "平台", "粉丝数", "作品数", "获赞总数", "最近更新", "状态"]
        self.table = QTableWidget(0, len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        tb_l.addWidget(self.table, 1)
        root.addWidget(tb, 1)

        self.status = StatusLine("就绪"); self.status.mark("idle")
        root.addWidget(self.status)

        if self.ctx.demo:
            self._demo()
        self._sync_actions()

    def _demo(self):
        for a in [("示例博主A", "douyin", "12.3万", "320", "98.6万", "今天", "正常"),
                  ("示例博主B", "xiaohongshu", "5.1万", "210", "33.2万", "昨天", "正常")]:
            self._add_row(*a)
        self.status.setText("演示模式：展示示例数据，配置项目后可实时复盘")

    def _add_row(self, name, plat, fans, posts, likes, updated, state):
        r = self.table.rowCount()
        self.table.insertRow(r)
        # 平台列显示中文（存储/接口仍是内部键，只在显示这一层翻译）
        for c, v in enumerate((name, platform_label(plat), fans, posts,
                               likes, updated, state)):
            self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self._sync_actions()

    def _sync_actions(self):
        """空列表时禁用「清空列表」，避免无效点击。"""
        self.clear_btn.setEnabled(self.table.rowCount() > 0)

    def _add(self):
        # ★ 付费闸门（v1.21.0）：加入监控后会定期拉账号数据，属于采集类功能
        if not require_feature(self, "analyze", "加入复盘监控"):
            return
        text = self.input.text().strip()
        if not text:
            self.status.setText("请先输入作者链接或 sec_uid")
            return
        pm = self.ctx.project_manager
        if pm is None:
            self._add_row(text, "douyin", "-", "-", "-", "待采集", "新增")
            self.status.setText("演示模式：已加入示例监控")
            return
        # 真实环境：交给 ProjectManager 记录
        try:
            pm.add_watch_target(text)
            self._add_row(text, "douyin", "-", "-", "-", "待采集", "新增")
            self.status.setText(f"已加入监控：{text}")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"加入失败：{e}")

    def _refresh(self):
        # ★ 付费闸门（v1.21.0）：刷新快照 = 逐个拉账号最新数据，接口成本在这里
        if not require_feature(self, "analyze", "刷新监控快照"):
            return
        pm = self.ctx.project_manager
        if pm is None:
            self.status.setText("演示模式：刷新需连接真实项目数据")
            return
        self.status.setText("正在刷新快照…")
        try:
            rows = pm.snapshot_all()
            self.table.setRowCount(0)
            for r in rows:
                self._add_row(r.get("name", ""), r.get("platform", ""),
                              r.get("fans", ""), r.get("posts", ""),
                              r.get("likes", ""), r.get("updated", ""), "正常")
            self._sync_actions()
            self.status.setText(f"刷新完成：{len(rows)} 个作者")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"刷新失败：{e}")

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            self.status.setText("请先在表格里选中要移除的作者")
            return
        for r in rows:
            self.table.removeRow(r)
        self._sync_actions()
        self.status.setText(f"已移除 {len(rows)} 个作者")

    def _clear(self):
        """清空监控列表：二次确认 → 清空 → 明确反馈。"""
        n = self.table.rowCount()
        if n == 0:
            self.status.setText("监控列表已经是空的")
            return
        if not confirm_destructive(
            self, "清空监控列表",
            f"确定清空当前 {n} 个监控作者吗？",
            ok_text="确认清空",
            detail="只清空本页列表；已积累的历史快照仍保留在项目数据里。",
        ):
            self.status.setText("已取消，监控列表未做改动")
            return
        self.table.setRowCount(0)
        self._sync_actions()
        self.status.setText(f"已清空 {n} 个监控作者")
