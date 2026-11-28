"""微信聊天整理页（PySide6）。

把微信「合并转发」保存的 ZIP 归档，一键解析成结构化聊天记录，并导出成
Obsidian 笔记 / 普通文件夹 Markdown / 剪贴板纯文本。全程本地处理，不联网、
不读微信数据库、不注入微信进程。

用法：微信里多选聊天记录 →「合并转发」→ 转发给「文件传输助手」或保存到
电脑 → 得到 .zip → 在本页选择它 → 解析预览 → 选出口。
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ._base import connect_guarded
from ._base import (
    StatusLine, Worker, connect_guarded, guide_banner, page_title,
    require_feature, vertical_card,
)
from gui_qt.widgets.progress import FlowProgressBar
from justoneapi_collector import wechat_archive as wa


class WechatChatPage(QWidget):
    PAGE_NAME = "微信聊天整理"

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._items: list[wa.Transcript] = []
        self._default_out = self._default_dir()
        self.status = StatusLine("选择微信合并转发的 ZIP 归档开始", parent=self)
        self._build_ui()

    # ------------------------------------------------------------------
    #  构造
    # ------------------------------------------------------------------
    @staticmethod
    def _default_dir() -> str:
        try:
            from justoneapi_collector import branding
            d = branding.app_data_dir() / "微信聊天整理"
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
        except Exception:                                   # noqa: BLE001
            return str(Path.home() / "Downloads")

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_title("微信聊天整理", self))
        root.addWidget(guide_banner("微信聊天整理", self))

        # 卡片一：选择归档 + 解析
        card1, cl1 = vertical_card("聊天归档", self)
        cl1.setSpacing(10)
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("微信合并转发的 .zip 文件（可多选后一次性解析）")
        self.path_edit.setReadOnly(True)
        row1.addWidget(self.path_edit, 1)
        pick = QPushButton("选择 ZIP…", self)
        connect_guarded(pick.clicked, lambda *_: self._pick_files(), on_error=lambda msg: self.status.setText(msg), label="pick files")   # guard: 信号带参
        row1.addWidget(pick)
        parse_btn = QPushButton("开始解析", self)
        parse_btn.setObjectName("Primary")
        connect_guarded(parse_btn.clicked, lambda *_: self._parse(), on_error=lambda msg: self.status.setText(msg), label="parse")
        row1.addWidget(parse_btn)
        cl1.addLayout(row1)
        hint = QLabel(
            "微信里多选聊天记录 → 合并转发 → 保存到电脑得到 .zip；本页只处理你选的文件，不联网、不读微信数据库。",
            self,
        )
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        cl1.addWidget(hint)
        self.progress = FlowProgressBar(self)
        cl1.addWidget(self.progress)
        root.addWidget(card1)

        # 卡片二：预览表格
        card2, cl2 = vertical_card("解析预览", self)
        cl2.setSpacing(8)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["时间", "发言人", "消息内容", "来源归档"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        # 第 2 列（消息内容）吃掉剩余空间，不用 setStretchLastSection
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        cl2.addWidget(self.table)
        root.addWidget(card2)

        # 卡片三：导出出口
        card3, cl3 = vertical_card("导出", self)
        cl3.setSpacing(10)
        out_row = QHBoxLayout()
        out_row.setSpacing(10)
        out_lbl = QLabel("保存目录", self)
        out_row.addWidget(out_lbl)
        self.out_edit = QLineEdit(self._default_out, self)
        out_row.addWidget(self.out_edit, 1)
        out_pick = QPushButton("选择目录…", self)
        connect_guarded(out_pick.clicked, lambda *_: self._pick_out(), on_error=lambda msg: self.status.setText(msg), label="pick out")
        out_row.addWidget(out_pick)
        cl3.addLayout(out_row)
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_row.addWidget(QLabel("群名 / 场景（可选）", self))
        self.chat_edit = QLineEdit(self)
        self.chat_edit.setPlaceholderText("用于笔记标题，留空则按发言人推断")
        name_row.addWidget(self.chat_edit, 1)
        self.scene_edit = QLineEdit(self)
        self.scene_edit.setPlaceholderText("场景备注（可选）")
        name_row.addWidget(self.scene_edit, 1)
        cl3.addLayout(name_row)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.copy_btn = QPushButton("复制到剪贴板", self)
        connect_guarded(self.copy_btn.clicked, lambda *_: self._copy(), on_error=lambda msg: self.status.setText(msg), label="copy")
        btn_row.addWidget(self.copy_btn)
        self.md_btn = QPushButton("导出 Markdown 文件夹", self)
        connect_guarded(self.md_btn.clicked, lambda *_: self._export(), on_error=lambda msg: self.status.setText(msg), label="export")
        btn_row.addWidget(self.md_btn)
        self.obs_btn = QPushButton("导出到 Obsidian 库", self)
        connect_guarded(self.obs_btn.clicked, lambda *_: self._export(obsidian=True), on_error=lambda msg: self.status.setText(msg), label="export")
        btn_row.addWidget(self.obs_btn)
        btn_row.addStretch()
        cl3.addLayout(btn_row)
        root.addWidget(card3)
        root.addWidget(self.status)

    # ------------------------------------------------------------------
    #  状态栏
    # ------------------------------------------------------------------
    def _say(self, msg: str, level: str = ""):
        self.status.setText(msg)
        if level in ("error", "ok"):
            try:
                self.status.mark(level)
            except Exception:                               # noqa: BLE001
                pass

    def _wire_worker(self, w, done_slot, err_prefix: str, label: str) -> None:
        connect_guarded(w.done, done_slot,
                        on_error=lambda m: self._say(m, "error"), label=label)
        connect_guarded(w.error, lambda m: self._say(f"{err_prefix}：{m}", "error"),
                        on_error=lambda m: self._say(m, "error"), label=label + "异常")

    # ------------------------------------------------------------------
    #  交互
    # ------------------------------------------------------------------
    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择微信合并转发 ZIP", "", "ZIP 归档 (*.zip)")
        if paths:
            self.path_edit.setText(" | ".join(paths))

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def _selected_paths(self) -> list[str]:
        raw = self.path_edit.text().strip()
        return [p.strip() for p in raw.split("|") if p.strip()] if raw else []

    def _parse(self):
        if not require_feature(self, "wechat_chat", "微信聊天整理"):
            return
        paths = self._selected_paths()
        if not paths:
            self._say("请先选择微信合并转发的 ZIP 文件", "error")
            return
        self.progress.setValue(0)
        self._say(f"正在解析 {len(paths)} 份归档…")
        w = Worker(self._parse_task, paths)
        self._wire_worker(w, self._on_parsed, "解析失败", "聊天归档解析")
        w.start()

    def _parse_task(self, worker, paths: list[str]):
        results = []
        total = len(paths)
        for i, p in enumerate(paths):
            try:
                results.append(wa.read_archive(p))
            except wa.ArchiveError as exc:
                worker.emit_progress(0, f"第 {i + 1}/{total} 份失败：{exc}")
                continue
            worker.emit_progress(int((i + 1) / total * 100), f"已解析 {i + 1}/{total} 份")
        return results

    def _on_parsed(self, results: list):
        self._items = results
        self._fill_table()
        if not results:
            self._say("没有解析出可用归档，请确认选的是微信合并转发的 ZIP", "error")
            return
        stat = wa.summarize(results)
        self._say(
            f"解析完成：{stat['archives']} 份归档 / {stat['messages']} 条消息 / "
            f"{stat['senders']} 位发言人" + (f"（{stat['failed']} 份格式未识别）" if stat['failed'] else ""),
            "ok",
        )
        self.progress.setValue(100)

    def _fill_table(self):
        self.table.setRowCount(0)
        for t in self._items:
            for m in t.messages:
                r = self.table.rowCount()
                self.table.insertRow(r)
                self.table.setItem(r, 0, QTableWidgetItem(m.at.strftime("%Y-%m-%d %H:%M")))
                self.table.setItem(r, 1, QTableWidgetItem(m.sender))
                self.table.setItem(r, 2, QTableWidgetItem(m.text))
                self.table.setItem(r, 3, QTableWidgetItem(Path(t.source).name))
        self._tune_tables()

    def _tune_tables(self):
        """列宽统一走这里，禁止 setStretchLastSection。"""
        self.table.resizeRowsToContents()

    def _ensure_ready(self) -> bool:
        if not require_feature(self, "wechat_chat", "导出聊天记录"):
            return False
        if not self._items:
            self._say("请先解析至少一份归档", "error")
            return False
        return True

    def _copy(self):
        if not self._ensure_ready():
            return
        text = "\n\n".join(wa.transcript_to_text(t) for t in self._items)
        QApplication.clipboard().setText(text)
        self._say(f"已复制 {len(self._items)} 份归档的聊天记录到剪贴板，可直接粘贴给 AI", "ok")

    def _export(self, obsidian: bool = False):
        if not self._ensure_ready():
            return
        out = self.out_edit.text().strip()
        if not out:
            self._say("请先选择保存目录", "error")
            return
        chat = self.chat_edit.text().strip()
        scene = self.scene_edit.text().strip()
        # Obsidian 库：用户选的是 vault 根，笔记落到固定子目录
        if obsidian:
            out = str(Path(out) / "微信聊天整理")
        w = Worker(self._export_task, out, chat, scene)
        self._wire_worker(w, self._on_exported, "导出失败", "聊天记录导出")
        self._say("正在导出笔记与附件…")
        w.start()

    def _export_task(self, worker, out: str, chat: str, scene: str):
        written = []
        total = len(self._items)
        for i, t in enumerate(self._items):
            written.append(wa.export_note(t, out, chat_name=chat, scene_name=scene))
            worker.emit_progress(int((i + 1) / total * 100), f"已导出 {i + 1}/{total} 份")
        return [str(p) for p in written]

    def _on_exported(self, paths: list):
        self._say(f"导出完成：{len(paths)} 篇笔记已保存到 {self.out_edit.text().strip()}", "ok")
        self.progress.setValue(100)
