# -*- coding: utf-8 -*-
"""短剧解析页（合规护栏版，2026-11-02）。

★ 用途边界（与 drama_api.py 一致，不许改）：
  仅用于「客户自有 / 已授权」短剧的二创素材获取，或「找选题 / 预览」参考。
  不提供默认全量批量抓取；下载前必须一次性弹窗确认授权。

为什么单独成页、不并进「短剧成片」：
  短剧成片是二创剪辑引擎（本地组件 + 转写）；本页是「内容获取」侧入口，
  二者上下游但不耦合。客户要先在这里把授权的短剧拿到本地，再进成片页二创。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
)

from ._base import StatusLine, Worker, connect_guarded
from gui_qt.widgets.progress import FlowProgressBar
from justoneapi_collector import drama_api

_COMPLIANCE_HINT = (
    "本功能仅用于「自有 / 已授权」短剧的二创素材获取，或找选题 / 预览参考。"
    "批量搬运未授权内容属侵权，本工具不提供该能力。"
)


class DramaParsePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._series: list[dict] = []     # 当前搜索 / 热榜结果（归一化）
        self._eps: list[dict] = []        # 当前选中剧集的集列表（归一化）
        self._authorized = False          # 会话内授权确认标记
        self._save_dir = self._default_save_dir()
        self._build()
        self.apply_theme("light")

    # ------------------------------------------------------------------
    #  布局
    # ------------------------------------------------------------------
    def _default_save_dir(self) -> str:
        try:
            from justoneapi_collector import branding
            d = branding.app_data_dir() / "短剧解析"
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
        except Exception:  # noqa: BLE001
            return str(Path.home() / "Downloads")

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("短剧解析")
        title.setObjectName("H1")
        root.addWidget(title)

        self._hint = QLabel(_COMPLIANCE_HINT)
        self._hint.setWordWrap(True)
        self._hint.setObjectName("ComplianceHint")
        root.addWidget(self._hint)

        # 搜索行
        row = QHBoxLayout()
        self._q = QLineEdit()
        self._q.setPlaceholderText("输入剧名关键词，如「逆袭」「甜宠」")
        self._q.returnPressed.connect(self._do_search)
        row.addWidget(self._q, 4)
        b_search = QPushButton("搜索")
        b_search.clicked.connect(self._do_search)
        row.addWidget(b_search, 1)
        b_hot = QPushButton("看热榜")
        b_hot.clicked.connect(self._do_rank)
        row.addWidget(b_hot, 1)
        root.addLayout(row)

        # 结果列表
        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_series_clicked)
        root.addWidget(self._list, 3)

        # 选集区
        self._detail_label = QLabel("选择上方一部剧，可查看集数并下载")
        self._detail_label.setWordWrap(True)
        root.addWidget(self._detail_label)
        self._eps_list = QListWidget()
        self._eps_list.itemClicked.connect(self._on_ep_clicked)
        root.addWidget(self._eps_list, 2)

        # 下载控制
        dl = QHBoxLayout()
        self._dl_btn = QPushButton("下载选中集")
        self._dl_btn.clicked.connect(self._on_dl_clicked)
        dl.addWidget(self._dl_btn, 3)
        self._dir_btn = QPushButton("保存目录")
        self._dir_btn.clicked.connect(self._choose_dir)
        dl.addWidget(self._dir_btn, 1)
        root.addLayout(dl)
        self._dir_label = QLabel(f"保存到：{self._save_dir}")
        self._dir_label.setWordWrap(True)
        root.addWidget(self._dir_label)

        self._bar = FlowProgressBar()
        root.addWidget(self._bar)

        self._status = StatusLine()
        root.addWidget(self._status)
        self._say("输入剧名或点「看热榜」开始")

    def apply_theme(self, mode: str) -> None:
        """切换主题时刷新合规横幅（主动跟随 mode，避免写死色导致切换发暗）。"""
        if mode == "dark":
            self._hint.setStyleSheet(
                "background:#1A212B;border:1px solid #2E3A47;border-radius:10px;"
                "padding:10px 12px;color:#FBBF24;font-size:12px;"
            )
        else:
            self._hint.setStyleSheet(
                "background:#FEF3C7;border:1px solid #FDE68A;border-radius:10px;"
                "padding:10px 12px;color:#92400E;font-size:12px;"
            )

    def _say(self, msg: str, level: str = "idle") -> None:
        """状态行：setText + mark 语义档（StatusLine 接口）。"""
        self._status.setText(msg)
        self._status.mark(level)

    # ------------------------------------------------------------------
    #  搜索 / 热榜
    # ------------------------------------------------------------------
    def _do_search(self):
        kw = self._q.text().strip()
        if not kw:
            self._say("请先输入剧名关键词")
            return
        self._say(f"正在搜索「{kw}」…")
        w = Worker(self._run_search, kw)
        connect_guarded(w.done, self._on_search_done, on_error=self._status.set)
        connect_guarded(w.error, lambda m: self._say(f"搜索失败：{m}", "error"))
        w.start()

    def _run_search(self, worker, kw: str):
        data, err = drama_api.search(kw)
        return ("search", data, err)

    def _do_rank(self):
        self._say("正在拉取热榜…")
        w = Worker(self._run_rank)
        connect_guarded(w.done, self._on_search_done, on_error=self._status.set)
        connect_guarded(w.error, lambda m: self._say(f"拉取失败：{m}", "error"))
        w.start()

    def _run_rank(self, worker):
        data, err = drama_api.rank()
        return ("rank", data, err)

    def _on_search_done(self, payload):
        kind, data, err = payload
        if err:
            self._say(err, "error")
            return
        results = []
        if kind == "search" and isinstance(data, dict):
            results = data.get("results") or []
        elif kind == "rank" and isinstance(data, dict):
            results = data.get("results") or []
        if not results:
            self._say("没有找到相关短剧")
            self._list.clear()
            return
        self._series = [drama_api.normalize_series(r) for r in results]
        self._list.clear()
        for s in self._series:
            item = QListWidgetItem(f"{s['name']}  ｜ {s['score'] or '—'}分  ｜ {s['heat'] or '—'}热度")
            item.setData(Qt.UserRole, s["series_id"])
            self._list.addItem(item)
        self._say(f"找到 {len(self._series)} 部短剧，点击查看集数")

    # ------------------------------------------------------------------
    #  选剧 → 加载集列表
    # ------------------------------------------------------------------
    def _on_series_clicked(self, item: QListWidgetItem):
        sid = item.data(Qt.UserRole)
        if not sid:
            return
        self._say("正在加载剧集列表…")
        w = Worker(self._run_detail, sid)
        connect_guarded(w.done, self._on_detail_done, on_error=self._status.set)
        connect_guarded(w.error, lambda m: self._say(f"加载失败：{m}", "error"))
        w.start()

    def _run_detail(self, worker, sid: str):
        data, err = drama_api.detail(sid)
        return (data, err)

    def _on_detail_done(self, payload):
        data, err = payload
        if err:
            self._say(err, "error")
            return
        if not isinstance(data, dict):
            self._say("剧集详情返回异常")
            return
        name = data.get("name") or ""
        episodes = data.get("episodes") or []
        self._detail_label.setText(
            f"《{name}》共 {len(episodes)} 集 —— 点选一集后下载（仅限自有/已授权内容）"
        )
        self._eps = [drama_api.normalize_episode(e) for e in episodes]
        self._eps_list.clear()
        for e in self._eps:
            item = QListWidgetItem(f"第 {e['ep']} 集  ｜ {e['quality'] or '—'}p  ｜ {e['source'] or '—'}")
            item.setData(Qt.UserRole, e["ep"])
            self._eps_list.addItem(item)
        self._say(f"《{name}》加载完成，共 {len(self._eps)} 集")

    def _on_ep_clicked(self, item: QListWidgetItem):
        ep = item.data(Qt.UserRole)
        self._say(f"已选中第 {ep} 集，点「下载选中集」保存到本地")

    # ------------------------------------------------------------------
    #  下载（合规护栏：首次弹授权确认）
    # ------------------------------------------------------------------
    def _on_dl_clicked(self):
        item = self._eps_list.currentItem()
        if item is None:
            self._say("请先在上方选一集")
            return
        ep = item.data(Qt.UserRole)
        if not self._authorized:
            ok = QMessageBox.question(
                self, "确认内容授权",
                "你确认要下载的短剧内容已获得授权，或属于你/客户自有素材吗？\n\n"
                "本功能仅用于二创素材获取与选题参考，不支持批量搬运未授权内容。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                self._say("已取消下载（未确认授权）")
                return
            self._authorized = True

        # 拿这一集的播放地址
        sel = next((e for e in self._eps if e["ep"] == ep), None)
        if sel is None or not sel["url"]:
            self._say("该集没有可用的播放地址")
            return
        safe_name = (self._detail_label.text().split("》")[0].lstrip("《")
                     if "》" in self._detail_label.text() else "短剧")
        dest = Path(self._save_dir) / f"{safe_name}_第{ep}集.mp4"
        self._say(f"正在下载第 {ep} 集…")
        self._bar.set_value(5)
        w = Worker(self._run_download, sel["url"], str(dest))
        connect_guarded(w.done, self._on_dl_done, on_error=self._status.set)
        connect_guarded(w.error, lambda m: self._say(f"下载失败：{m}"))
        w.start()

    def _run_download(self, worker, url: str, dest: str):
        ok, msg = drama_api.download_episode(url, dest, authorized=True)
        return (ok, msg)

    def _on_dl_done(self, payload):
        ok, msg = payload
        if ok:
            self._bar.set_value(100)
            self._say(f"下载完成：{msg}")
        else:
            self._bar.set_value(0)
            self._say(f"下载失败：{msg}", "error")

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self._save_dir)
        if d:
            self._save_dir = d
            self._dir_label.setText(f"保存到：{self._save_dir}")
