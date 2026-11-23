# -*- coding: utf-8 -*-
"""小红书笔记内容提取页（2026-11-23）。

运营人真正要的是「把平台上的文字内容批量拿出来做二次创作/选题」，
按 okdata 官方文档接小红书能力，四块工作流：
    ① 搜笔记：关键词搜笔记 → 列表（标题/正文摘要/作者/赞藏评）→ 导出 CSV
    ② 博主笔记：按博主 ID 翻页取TA的笔记 → 导出 CSV
    ③ 笔记正文：粘笔记 ID / 分享链接 → 全文正文 + 互动 + 评论，正文存 .md
    ④ 选题辅助：话题页热门笔记 + 搜索联想词（选题/关键词灵感）

★ 边界（改动前必读）
  · 本页是**付费采集**，走客户自己的 okdata Key，不碰 video.zacao.top
    免费直链，也不碰直播录制；三条链路不许混。
  · 主动作第一行必须过 require_feature("xhs_notes") 闸门，免费版无本页入口。
  · 请求一律由 okdata_client 发出；笔记详情刻意用独立 endpoint
    note-content-v1 → xiaohongshu.notes.get（拿正文文本），**不复用**
    下载器的 note-details-v6（那个是拿播放直链，映射过去白花钱还下不了）。
  · 文档写明「查无结果 / 数据源出错也计费」，失败原因必须原样上屏，
    不许伪装成空结果或成功；界面显式提示成本。
  · 参数名沿用老工程命名（keyword/id/url…），由 okdata_client.PARAM_ALIAS
    转成官方 query/id/url，不要在本页直接拼官方路径。
"""
from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ._base import (
    StatusLine, Worker, connect_guarded, guide_banner, page_title,
    require_feature, vertical_card,
)
from gui_qt.widgets.progress import FlowProgressBar

# 老工程平台 slug；okdata_client 会转成 xiaohongshu
PLATFORM = "xiaohongshu-rednote"

# 老工程 endpoint 名 -> okdata capability（映射表在 okdata_client.py）
EP_SEARCH = "note-search-v2"          # → xiaohongshu.notes.search
EP_USER_NOTES = "user-published-notes-v4"  # → xiaohongshu.users.notes.list
EP_CONTENT = "note-content-v1"        # → xiaohongshu.notes.get（正文，独立 endpoint）
EP_COMMENTS = "note-comments-v2"      # → xiaohongshu.notes.comments.list
EP_TOPIC_NOTES = "topic-notes-v1"     # → xiaohongshu.topics.notes.list
EP_KEYWORDS = "keyword-related-v1"    # → xiaohongshu.keywords.related

# 表头（首列名用于 main_window._EMPTY_HINTS 识别多表页面）
COLS_SEARCH = ["发布时间", "标题", "作者", "点赞", "收藏", "评论", "链接"]
COLS_USER_NOTES = ["发布", "标题", "点赞", "收藏", "评论", "链接"]
COLS_COMMENT = ["评论者", "内容", "点赞", "时间", "IP 属地"]
COLS_TOPIC = ["标题", "作者", "点赞", "收藏", "链接"]
COLS_KEYWORD = ["名次", "联想词"]

_COST_HINT = (
    "按 okdata 官方价：小红书笔记每个接口 $0.02/次（正文、评论、搜笔记、话题笔记、联想词均同价）；"
    "查无结果也照常计费，参数错误不扣费。请先在「设置 → 数据服务」填好自己的 okd_ Key 并确认余额。"
)


def _payload(worker, client, endpoint: str, params: dict) -> dict:
    """通用单接口任务：Worker 线程里只做一次付费调用，进度与结果都回传。"""
    worker.emit_progress(40, "正在请求接口…")
    resp = client.request(PLATFORM, endpoint, params)
    worker.emit_progress(100, "完成")
    return {"resp": resp, "endpoint": endpoint, "params": dict(params or {})}


def _content_task(worker, client, note_id: str, with_comments: bool) -> dict:
    """笔记正文（+可选评论）：串行调用，失败隔离，结果按 kind 汇总。"""
    out: dict[str, object] = {"id": note_id}
    worker.emit_progress(30, "正在读取笔记正文…")
    try:
        out["content"] = client.request(PLATFORM, EP_CONTENT, {"id": note_id, "view": "full"})
    except Exception as exc:                              # noqa: BLE001
        out["content_error"] = f"{type(exc).__name__}: {exc}"
    if with_comments:
        worker.emit_progress(70, "正在读取笔记评论…")
        try:
            out["comments"] = client.request(PLATFORM, EP_COMMENTS, {"id": note_id})
        except Exception as exc:                          # noqa: BLE001
            out["comments_error"] = f"{type(exc).__name__}: {exc}"
    worker.emit_progress(100, "完成")
    return out


def _items(data: object) -> list[dict]:
    """okdata 统一 items；兼容 JustOneAPI 通道常见的几个列表键。"""
    if not isinstance(data, dict):
        return []
    for key in ("items", "notes", "feeds", "list", "comments", "replies", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if key == "data" and isinstance(val, dict):
            for k2 in ("items", "notes", "list", "data"):
                v2 = val.get(k2)
                if isinstance(v2, list):
                    return [x for x in v2 if isinstance(x, dict)]
    one = data.get("item")
    return [one] if isinstance(one, dict) else []


def _page_info(data: object) -> tuple[str, bool]:
    if not isinstance(data, dict):
        return "", False
    page = data.get("page")
    if not isinstance(page, dict):
        page = data
    return (str(page.get("next_cursor") or data.get("next_cursor") or ""),
            bool(page.get("has_more", data.get("has_more", False))))


def _num(value) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _author_name(item: dict) -> str:
    author = item.get("author")
    if isinstance(author, dict):
        return str(author.get("name") or author.get("username") or "")
    return str(item.get("nickname") or item.get("nick_name") or "")


def _metrics(item: dict) -> dict:
    m = item.get("metrics")
    return m if isinstance(m, dict) else {}


def _favorites(m: dict) -> str:
    """小红书收藏字段在 okdata 里可能是 collects 或 favorites。"""
    return _num(m.get("collects") if m.get("collects") is not None else m.get("favorites"))


class XhsNotesPage(QWidget):
    PAGE_NAME = "小红书笔记"

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._worker = None
        self._rows: dict[str, list[dict]] = {
            "search": [], "user_notes": [], "comment": [], "topic": [], "keyword": [],
        }
        self._cursor = {"search": "", "user_notes": ""}
        self._has_more = {"search": False, "user_notes": False}
        self._detail_text = ""
        self.status = StatusLine("选一块工作区开始：搜笔记 → 看博主 → 提取正文", parent=self)
        self._build()
        self.apply_theme("light")

    # ------------------------------------------------------------------
    #  guarded 接线（禁止裸 connect、禁止 status.set）
    # ------------------------------------------------------------------
    def _say(self, text: str, level: str = "idle") -> None:
        self.status.setText(str(text)[:400])
        self.status.mark(level)

    def _wire_btn(self, btn, slot, label: str) -> None:
        connect_guarded(btn.clicked, lambda *_: slot(),
                        on_error=lambda m: self._say(m, "error"), label=label)

    def _wire_worker(self, w, done_slot, label: str) -> None:
        connect_guarded(w.done, done_slot,
                        on_error=lambda m: self._say(m, "error"), label=label)
        connect_guarded(w.error, lambda m: self._say(f"{label}失败：{m}", "error"),
                        on_error=lambda m: self._say(m, "error"), label=label + "异常")
        connect_guarded(w.progress, lambda pct, msg: self.progress.setValue(int(pct)),
                        on_error=lambda m: self._say(m, "error"), label=label + "进度")

    def _start(self, endpoint: str, params: dict, done_slot, label: str) -> None:
        """所有付费动作的汇聚点；闸门必须在第一行（门禁会静态检查）。"""
        if not require_feature(self, "xhs_notes", "小红书笔记"):
            return
        client = getattr(self.ctx, "client", None)
        if client is None:
            self._say("数据服务未连接，请到「设置 → 数据服务」填好 okd_ Key", "error")
            return
        self.progress.setValue(0)
        self._say(f"正在{label}…", "idle")
        w = Worker(_payload, client, endpoint, params)
        self._wire_worker(w, done_slot, label)
        self._worker = w                       # 强引用，防止 GC 导致「点了没反应」
        w.start()

    # ------------------------------------------------------------------
    #  布局
    # ------------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_title("小红书笔记", self))
        root.addWidget(guide_banner("小红书笔记", self))

        cost = QLabel(_COST_HINT, self)
        cost.setObjectName("Caption")
        cost.setWordWrap(True)
        root.addWidget(cost)

        # 四个按钮 + QStackedWidget 切换：QSS 没给 QTabBar 配色，
        # 深色主题下系统原生标签栏会发白；按钮走已有 Primary 样式两主题都稳。
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._mode_btns: list[QPushButton] = []
        for text, slot in (
            ("① 搜笔记", self._show_search),
            ("② 博主笔记", self._show_user_notes),
            ("③ 笔记正文", self._show_content),
            ("④ 选题辅助", self._show_topic),
        ):
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            self._wire_btn(btn, slot, "切换工作区")
            self._mode_btns.append(btn)
            nav.addWidget(btn)
        nav.addStretch(1)
        root.addLayout(nav)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_search())
        self.stack.addWidget(self._build_user_notes())
        self.stack.addWidget(self._build_content())
        self.stack.addWidget(self._build_topic())
        root.addWidget(self.stack, 1)

        self.progress = FlowProgressBar(self)
        root.addWidget(self.progress)
        root.addWidget(self.status)
        self._select_mode(0)

    def _select_mode(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, btn in enumerate(self._mode_btns):
            btn.setObjectName("Primary" if i == index else "")
            btn.setChecked(i == index)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _show_search(self): self._select_mode(0)
    def _show_user_notes(self): self._select_mode(1)
    def _show_content(self): self._select_mode(2)
    def _show_topic(self): self._select_mode(3)

    @staticmethod
    def _table(cols: list[str], parent) -> QTableWidget:
        table = QTableWidget(0, len(cols), parent)
        table.setHorizontalHeaderLabels(cols)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(False)   # QSS 无 alternate 色，深色下会白底浅字
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        return table

    @staticmethod
    def _fill(table: QTableWidget, rows: list[dict], getters: list) -> None:
        table.setRowCount(0)
        for item in rows:
            r = table.rowCount()
            table.insertRow(r)
            for c, getter in enumerate(getters):
                cell = QTableWidgetItem(str(getter(item) or ""))
                cell.setToolTip(str(getter(item) or ""))
                table.setItem(r, c, cell)

    # ---------------------------- ① 搜笔记 ----------------------------
    def _build_search(self) -> QWidget:
        host, lay = vertical_card("按关键词搜小红书笔记", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_kw = QLineEdit(self)
        self.search_kw.setPlaceholderText("行业 / 产品 / 选题关键词，例如：防晒霜 推荐")
        row.addWidget(self.search_kw, 2)
        self.search_sort = QComboBox(self)
        for label, value in (("默认排序", ""), ("最新", "latest"), ("最热", "popular")):
            self.search_sort.addItem(label, value)
        row.addWidget(self.search_sort)
        go = QPushButton("搜索笔记", self)
        go.setObjectName("Primary")
        self._wire_btn(go, self._search_notes, "搜索笔记")
        row.addWidget(go)
        lay.addLayout(row)

        self.search_table = self._table(COLS_SEARCH, self)
        lay.addWidget(self.search_table)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        nxt = QPushButton("下一页 →", self)
        self._wire_btn(nxt, self._next_search, "翻页笔记")
        self.next_search_btn = nxt
        nxt.setEnabled(False)
        ops.addWidget(nxt)
        body = QPushButton("读这篇正文 →", self)
        self._wire_btn(body, self._search_to_content, "读正文")
        ops.addWidget(body)
        exp = QPushButton("导出 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.search_table, COLS_SEARCH, "小红书搜笔记.csv"), "导出搜笔记")
        ops.addWidget(exp)
        ops.addStretch(1)
        lay.addLayout(ops)
        return host

    def _search_params(self, cursor: str = "") -> dict:
        out = {
            "keyword": self.search_kw.text().strip(),
            "cursor": cursor,
            "view": "brief",
        }
        sort = self.search_sort.currentData()
        if sort:
            out["sort"] = sort
        return out

    def _search_notes(self):
        if not self.search_kw.text().strip():
            self._say("请先填关键词", "warn")
            return
        self._cursor["search"] = ""
        self.next_search_btn.setEnabled(False)
        self._start(EP_SEARCH, self._search_params(), self._on_search, "搜索笔记")

    def _next_search(self):
        if not self._cursor["search"]:
            self._say("没有下一页了", "warn")
            return
        self._start(EP_SEARCH, self._search_params(self._cursor["search"]),
                    self._on_search, "翻页搜笔记")

    def _on_search(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"搜索失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        rows = _items(getattr(resp, "data", None))
        if payload.get("params", {}).get("cursor"):
            self._rows["search"].extend(rows)
        else:
            self._rows["search"] = rows
        self._fill(self.search_table, self._rows["search"], [
            lambda x: str(x.get("published_at") or "")[:19].replace("T", " "),
            lambda x: x.get("title"),
            lambda x: _author_name(x),
            lambda x: _num(_metrics(x).get("likes")),
            lambda x: _favorites(_metrics(x)),
            lambda x: _num(_metrics(x).get("comments")),
            lambda x: x.get("url"),
        ])
        cursor, more = _page_info(getattr(resp, "data", None))
        self._cursor["search"] = cursor if more else ""
        self.next_search_btn.setEnabled(bool(cursor and more))
        self._say(f"找到 {len(rows)} 篇笔记，累计 {len(self._rows['search'])} 篇"
                  "（列表是摘要，点「读这篇正文」取全文）", "ok" if rows else "warn")

    def _search_to_content(self):
        r = self.search_table.currentRow()
        if r < 0 or r >= len(self._rows["search"]):
            self._say("先选中一篇笔记", "warn")
            return
        note_id = str(self._rows["search"][r].get("id") or "")
        self._select_mode(2)
        self.note_input.setText(note_id)
        self._read_content(with_comments=False)

    # ---------------------------- ② 博主笔记 ----------------------------
    def _build_user_notes(self) -> QWidget:
        host, lay = vertical_card("按博主 ID 列出TA发布的笔记", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.user_id = QLineEdit(self)
        self.user_id.setPlaceholderText("小红书博主 ID（24 位十六进制）")
        row.addWidget(self.user_id, 2)
        go = QPushButton("查博主笔记", self)
        go.setObjectName("Primary")
        self._wire_btn(go, self._load_user_notes, "查博主笔记")
        row.addWidget(go)
        lay.addLayout(row)

        self.user_notes_table = self._table(COLS_USER_NOTES, self)
        lay.addWidget(self.user_notes_table)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        nxt = QPushButton("下一页 →", self)
        self._wire_btn(nxt, self._next_user_notes, "翻页博主笔记")
        self.next_user_notes_btn = nxt
        nxt.setEnabled(False)
        ops.addWidget(nxt)
        body = QPushButton("读这篇正文 →", self)
        self._wire_btn(body, self._user_notes_to_content, "读正文")
        ops.addWidget(body)
        exp = QPushButton("导出 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.user_notes_table, COLS_USER_NOTES, "小红书博主笔记.csv"), "导出博主笔记")
        ops.addWidget(exp)
        ops.addStretch(1)
        lay.addLayout(ops)
        return host

    def _user_notes_params(self, cursor: str = "") -> dict:
        return {
            "id": self.user_id.text().strip(),
            "cursor": cursor,
            "view": "brief",
        }

    def _load_user_notes(self):
        if not self.user_id.text().strip():
            self._say("请先填博主 ID", "warn")
            return
        self._cursor["user_notes"] = ""
        self.next_user_notes_btn.setEnabled(False)
        self._start(EP_USER_NOTES, self._user_notes_params(),
                    self._on_user_notes, "查博主笔记")

    def _next_user_notes(self):
        if not self._cursor["user_notes"]:
            self._say("没有下一页了", "warn")
            return
        self._start(EP_USER_NOTES, self._user_notes_params(self._cursor["user_notes"]),
                    self._on_user_notes, "翻页博主笔记")

    def _on_user_notes(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"查询失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        rows = _items(getattr(resp, "data", None))
        if payload.get("params", {}).get("cursor"):
            self._rows["user_notes"].extend(rows)
        else:
            self._rows["user_notes"] = rows
        self._fill(self.user_notes_table, self._rows["user_notes"], [
            lambda x: str(x.get("published_at") or "")[:19].replace("T", " "),
            lambda x: x.get("title"),
            lambda x: _num(_metrics(x).get("likes")),
            lambda x: _favorites(_metrics(x)),
            lambda x: _num(_metrics(x).get("comments")),
            lambda x: x.get("url"),
        ])
        cursor, more = _page_info(getattr(resp, "data", None))
        self._cursor["user_notes"] = cursor if more else ""
        self.next_user_notes_btn.setEnabled(bool(cursor and more))
        self._say(f"列出 {len(rows)} 篇笔记，累计 {len(self._rows['user_notes'])} 篇",
                  "ok" if rows else "warn")

    def _user_notes_to_content(self):
        r = self.user_notes_table.currentRow()
        if r < 0 or r >= len(self._rows["user_notes"]):
            self._say("先选中一篇笔记", "warn")
            return
        note_id = str(self._rows["user_notes"][r].get("id") or "")
        self._select_mode(2)
        self.note_input.setText(note_id)
        self._read_content(with_comments=False)

    # ---------------------------- ③ 笔记正文 ----------------------------
    def _build_content(self) -> QWidget:
        host, lay = vertical_card("笔记全文 / 互动数据 / 评论（核心提取区）", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.note_input = QLineEdit(self)
        self.note_input.setPlaceholderText("笔记 ID 或小红书分享链接（会自动解析出 ID）")
        row.addWidget(self.note_input, 1)
        full = QPushButton("正文 + 评论", self)
        full.setObjectName("Primary")
        self._wire_btn(full, lambda: self._read_content(with_comments=True), "读正文和评论")
        row.addWidget(full)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        body = QPushButton("仅正文", self)
        self._wire_btn(body, lambda: self._read_content(with_comments=False), "读正文")
        row2.addWidget(body)
        cmt = QPushButton("仅评论", self)
        self._wire_btn(cmt, self._read_comments_only, "读评论")
        row2.addWidget(cmt)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.metric_label = QLabel("互动数据：尚未查询", self)
        self.metric_label.setObjectName("Caption")
        self.metric_label.setWordWrap(True)
        lay.addWidget(self.metric_label)

        self.body_edit = QPlainTextEdit(self)
        self.body_edit.setPlaceholderText("笔记全文会显示在这里，可保存为 Markdown 文件")
        self.body_edit.setReadOnly(True)
        self.body_edit.setMinimumHeight(130)
        lay.addWidget(self.body_edit)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        save = QPushButton("保存正文 .md", self)
        self._wire_btn(save, self._save_markdown, "保存正文")
        row3.addWidget(save)
        exp = QPushButton("导出评论 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.comment_table, COLS_COMMENT, "小红书笔记评论.csv"), "导出评论")
        row3.addWidget(exp)
        row3.addStretch(1)
        lay.addLayout(row3)

        self.comment_table = self._table(COLS_COMMENT, self)
        lay.addWidget(self.comment_table)
        return host

    @staticmethod
    def _note_id_from(text: str) -> str:
        """从 ID 或分享链接里取出笔记 ID（24 位十六进制优先）。"""
        import re
        s = (text or "").strip()
        m = re.search(r"[0-9a-fA-F]{24}", s)
        return m.group(0) if m else s

    def _read_content(self, with_comments: bool):
        if not require_feature(self, "xhs_notes", "小红书笔记"):
            return
        note = self._note_id_from(self.note_input.text())
        if not note:
            self._say("请先填笔记 ID 或粘贴分享链接", "warn")
            return
        client = getattr(self.ctx, "client", None)
        if client is None:
            self._say("数据服务未连接，请到「设置 → 数据服务」填好 okd_ Key", "error")
            return
        self.progress.setValue(0)
        self._say("正在读取笔记…", "idle")
        w = Worker(_content_task, client, note, bool(with_comments))
        self._wire_worker(w, self._on_content, "读取笔记")
        self._worker = w
        w.start()

    def _read_comments_only(self):
        """仅评论：复用单接口汇聚点（第一行照样过闸门）。"""
        note = self._note_id_from(self.note_input.text())
        if not note:
            self._say("请先填笔记 ID 或粘贴分享链接", "warn")
            return
        self._start(EP_COMMENTS, {"id": note}, self._on_comments_only, "读取评论")

    def _on_content(self, result: dict):
        if not isinstance(result, dict):
            self._say("笔记读取返回异常", "error")
            return
        notes: list[str] = []
        content_err = result.get("content_error")
        if content_err:
            notes.append(f"正文失败：{content_err}")
        else:
            resp = result.get("content")
            if resp is not None:
                if not resp.success:
                    notes.append(f"正文失败：{resp.message}")
                else:
                    one = _items(getattr(resp, "data", None))
                    item = one[0] if one else {}
                    self._detail_text = str(item.get("text") or "")
                    self.body_edit.setPlainText(
                        f"# {item.get('title') or '（无标题）'}\n\n"
                        f"> 作者：{_author_name(item)}　时间：{item.get('published_at') or '—'}\n"
                        f"> 原文：{item.get('url') or ''}\n\n"
                        + self._detail_text[:20000])
                    m = _metrics(item)
                    self.metric_label.setText(
                        "互动数据：点赞 {l}　收藏 {f}　评论 {c}　分享 {s}　浏览 {v}".format(
                            l=_num(m.get("likes")), f=_favorites(m),
                            c=_num(m.get("comments")), s=_num(m.get("shares")),
                            v=_num(m.get("views"))))
        cmt_err = result.get("comments_error")
        if cmt_err:
            notes.append(f"评论失败：{cmt_err}")
        elif result.get("comments") is not None:
            self._apply_comments(result["comments"])
        self._say("；".join(notes) if notes else "笔记内容读取完成",
                  "error" if notes else "ok")

    def _on_comments_only(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"读取评论失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        self._apply_comments(resp)
        self._say("评论读取完成", "ok")

    def _apply_comments(self, resp) -> None:
        if not resp.success:
            self._say(f"评论失败：{resp.message}", "error")
            return
        rows = _items(getattr(resp, "data", None))
        self._rows["comment"] = rows
        self._fill(self.comment_table, rows, [
            lambda x: _author_name(x),
            lambda x: x.get("text") or x.get("content"),
            lambda x: _num(_metrics(x).get("likes")),
            lambda x: str(x.get("created_at") or "")[:19].replace("T", " "),
            lambda x: x.get("ip_location") or x.get("ip"),
        ])

    def _save_markdown(self):
        text = self.body_edit.toPlainText().strip()
        if not text:
            self._say("先读出正文再保存", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存小红书笔记正文", "小红书笔记.md",
                                              "Markdown 文件 (*.md)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._say(f"保存失败：{exc}", "error")
            return
        self._say(f"已保存到 {path}", "ok")

    # ---------------------------- ④ 选题辅助 ----------------------------
    def _build_topic(self) -> QWidget:
        host, lay = vertical_card("话题页热门笔记 + 搜索联想词（选题灵感）", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.topic_id = QLineEdit(self)
        self.topic_id.setPlaceholderText("话题页 ID（24 位十六进制）")
        row.addWidget(self.topic_id, 2)
        topic_btn = QPushButton("查话题笔记", self)
        topic_btn.setObjectName("Primary")
        self._wire_btn(topic_btn, self._load_topic_notes, "查话题笔记")
        row.addWidget(topic_btn)
        lay.addLayout(row)

        self.topic_table = self._table(COLS_TOPIC, self)
        lay.addWidget(self.topic_table)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        body = QPushButton("读这篇正文 →", self)
        self._wire_btn(body, self._topic_to_content, "读正文")
        ops.addWidget(body)
        exp = QPushButton("导出 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.topic_table, COLS_TOPIC, "小红书话题笔记.csv"), "导出话题笔记")
        ops.addWidget(exp)
        ops.addStretch(1)
        lay.addLayout(ops)

        lay.addWidget(QLabel("搜索联想词（选题灵感，一次约 10 个）", self))
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.keyword_input = QLineEdit(self)
        self.keyword_input.setPlaceholderText("种子关键词，例如：露营")
        row2.addWidget(self.keyword_input, 2)
        kw_btn = QPushButton("查联想词", self)
        self._wire_btn(kw_btn, self._load_keywords, "查联想词")
        row2.addWidget(kw_btn)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.keyword_table = self._table(COLS_KEYWORD, self)
        lay.addWidget(self.keyword_table)
        return host

    def _load_topic_notes(self):
        if not self.topic_id.text().strip():
            self._say("请先填话题页 ID", "warn")
            return
        self._start(EP_TOPIC_NOTES, {"id": self.topic_id.text().strip(), "view": "brief"},
                    self._on_topic_notes, "查话题笔记")

    def _on_topic_notes(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"查询失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        rows = _items(getattr(resp, "data", None))
        self._rows["topic"] = rows
        self._fill(self.topic_table, rows, [
            lambda x: x.get("title"),
            lambda x: _author_name(x),
            lambda x: _num(_metrics(x).get("likes")),
            lambda x: _favorites(_metrics(x)),
            lambda x: x.get("url"),
        ])
        self._say(f"话题下找到 {len(rows)} 篇热门笔记", "ok" if rows else "warn")

    def _topic_to_content(self):
        r = self.topic_table.currentRow()
        if r < 0 or r >= len(self._rows["topic"]):
            self._say("先选中一篇笔记", "warn")
            return
        note_id = str(self._rows["topic"][r].get("id") or "")
        self._select_mode(2)
        self.note_input.setText(note_id)
        self._read_content(with_comments=False)

    def _load_keywords(self):
        if not self.keyword_input.text().strip():
            self._say("请先填种子关键词", "warn")
            return
        self._start(EP_KEYWORDS, {"keyword": self.keyword_input.text().strip()},
                    self._on_keywords, "查联想词")

    def _on_keywords(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"查询失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        # 联想词接口返回的可能是 items 字符串列表，也可能是对象列表。
        data = getattr(resp, "data", None)
        raw: list = []
        if isinstance(data, dict):
            for key in ("items", "keywords", "words", "list"):
                val = data.get(key)
                if isinstance(val, list):
                    raw = val
                    break
        rows: list[dict] = []
        for i, word in enumerate(raw, 1):
            if isinstance(word, dict):
                rows.append({"rank": word.get("rank") or i,
                             "word": word.get("word") or word.get("text") or word.get("keyword") or ""})
            else:
                rows.append({"rank": i, "word": str(word)})
        self._rows["keyword"] = rows
        self._fill(self.keyword_table, rows, [
            lambda x: x.get("rank"),
            lambda x: x.get("word"),
        ])
        self._say(f"找到 {len(rows)} 个联想词（可拿去做选题/标题）", "ok" if rows else "warn")

    # ------------------------------------------------------------------
    #  导出
    # ------------------------------------------------------------------
    def _export_csv(self, table: QTableWidget, cols: list[str], name: str) -> None:
        if table.rowCount() <= 0:
            self._say("表里还没有数据", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", name, "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fp:
                writer = csv.writer(fp)
                writer.writerow(cols)
                for r in range(table.rowCount()):
                    writer.writerow([
                        (table.item(r, c).text() if table.item(r, c) else "")
                        for c in range(len(cols))
                    ])
        except OSError as exc:
            self._say(f"导出失败：{exc}", "error")
            return
        self._say(f"已导出 {table.rowCount()} 行到 {path}", "ok")

    def _fill_search_demo(self):
        """空态引导按钮：只把关键词送进输入框，不替用户发起付费请求。"""
        self._select_mode(0)
        self.search_kw.setText("防晒霜 推荐")
        self.search_kw.setFocus()
        self._say("已填入示例关键词，点「搜索笔记」即可", "idle")

    def _goto_user(self):
        self._select_mode(1)
        self.user_id.setFocus()
        self._say("填博主 24 位 ID，点「查博主笔记」", "idle")

    def _goto_content(self):
        self._select_mode(2)
        self.note_input.setFocus()
        self._say("粘贴笔记 ID 或分享链接，点「正文 + 评论」", "idle")

    def _fill_topic_demo(self):
        self._select_mode(3)
        self.keyword_input.setText("露营")
        self.keyword_input.setFocus()
        self._say("已填入示例关键词，点「查联想词」即可", "idle")

    # ------------------------------------------------------------------
    #  主题
    # ------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        try:
            self.status.apply_theme(mode)
        except Exception:                                  # noqa: BLE001
            pass
