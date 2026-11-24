# -*- coding: utf-8 -*-
"""公众号运营页（2026-11-23）。

按 okdata 官方技术文档接入微信公众号能力，四块工作流：
    ① 找公众号：关键词搜号 → 原始 ID（gh_…）
    ② 历史文章：按 gh_ ID / 微信号翻页取文章 → 导出 CSV
    ③ 文章诊断：正文 + 互动数据 + 精选评论（可单查可一次全查）
    ④ 搜一搜选题：关键词找公众号文章 / 视频号视频 → 导出 CSV

★ 边界（改动前必读）
  · 本页是**付费采集**，不碰 video.zacao.top 免费直链，也不碰直播录制；
  · 主动作必须先过 require_feature("wechat_mp") 闸门，免费版导航根本没有本页；
  · okdata 的请求一律由 okdata_client 发出（Key 只在 Authorization 头）；
  · 文档写明「查无结果 / 数据源出错也计费」，所以界面显式提示成本，
    并且失败原因必须原样上屏，不许伪装成空结果或成功。
  · 参数名沿用老工程 endpoint 的命名（keyword/id/url…），由
    okdata_client.PARAM_ALIAS 转成官方 query/id/url，不要在本页直接拼官方路径。
"""
from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ._base import (
    StatusLine, Worker, connect_guarded, guide_banner, page_title,
    require_feature, vertical_card,
)
from gui_qt.widgets.progress import FlowProgressBar

# 老工程平台 slug；okdata_client 会转成 wechat_mp
PLATFORM = "wechat-official-accounts"

# 老工程 endpoint 名 -> okdata capability（映射表在 okdata_client.py）
EP_ACCOUNT = "account-search-v1"
EP_ARTICLES = "account-historical-articles-v2"
EP_DETAIL = "article-details-v1"
EP_METRICS = "article-metrics-v1"
EP_COMMENTS = "article-comments-v1"
EP_SEARCH = "article-search-v1"

# 表头（首列名用于 main_window._EMPTY_HINTS 识别多表页面）
COLS_ACCOUNT = ["公众号", "微信号", "原始ID", "认证", "文章数", "简介"]
COLS_ARTICLE = ["发布时间", "标题", "作者", "阅读", "点赞", "链接"]
COLS_COMMENT = ["评论者", "内容", "点赞", "时间", "IP 属地"]
COLS_SEARCH = ["类型", "发布时间", "标题", "来源账号", "阅读档位", "链接"]

_COST_HINT = (
    "按 okdata 官方价：搜公众号 $0.058/条（单次最多 $0.58），其余每次 $0.02；"
    "查无结果也照常计费。请先在「设置 → 数据服务」填好自己的 okd_ Key 并确认余额。"
    "「一次全查」会串行调用正文 + 互动 + 评论共 3 次，预估 $0.06，请确认余额充足再点。"
)

# 空结果也照常计费，明示一次，免得客户以为没花钱。
_BILLED_EMPTY = "（本次查询已计费）"


def _key_hint(ctx) -> str:
    """按当前数据服务商给出凭证文案：okdata 填 Key，JustOneAPI 填 Token。"""
    try:
        provider = str((getattr(ctx, "settings", {}) or {}).get(
            "api_provider", "okdata") or "okdata").strip().lower()
    except Exception:                                  # noqa: BLE001
        provider = "okdata"
    return "okd_ 开头的 Key" if provider != "justoneapi" else "API Token"


def _payload(worker, client, endpoint: str, params: dict) -> dict:
    """通用单接口任务：Worker 线程里只做一次付费调用，进度与结果都回传。"""
    worker.emit_progress(40, "正在请求接口…")
    resp = client.request(PLATFORM, endpoint, params)
    worker.emit_progress(100, "完成")
    return {"resp": resp, "endpoint": endpoint, "params": dict(params or {})}


def _diag_task(worker, client, url: str, kinds: tuple[str, ...]) -> dict:
    """文章诊断：串行调用所选能力，失败隔离，结果按 kind 汇总。"""
    steps = []
    if "detail" in kinds:
        steps.append(("detail", EP_DETAIL))
    if "metrics" in kinds:
        steps.append(("metrics", EP_METRICS))
    if "comments" in kinds:
        steps.append(("comments", EP_COMMENTS))
    out: dict[str, object] = {}
    for i, (kind, endpoint) in enumerate(steps, 1):
        worker.emit_progress(i * 100.0 / max(len(steps), 1), f"正在查询 {kind} {i}/{len(steps)}")
        try:
            out[kind] = client.request(PLATFORM, endpoint, {"url": url})
        except Exception as exc:                          # noqa: BLE001
            out[kind + "_error"] = f"{type(exc).__name__}: {exc}"
    worker.emit_progress(100, "完成")
    out["url"] = url
    return out


def _items(data: object) -> list[dict]:
    """okdata 统一 items；兼容 JustOneAPI 通道常见的几个列表键。"""
    if not isinstance(data, dict):
        return []
    for key in ("items", "feeds", "notes", "list", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if key == "data" and isinstance(val, dict):
            for k2 in ("items", "list", "data"):
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


class WechatMpPage(QWidget):
    PAGE_NAME = "公众号运营"

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._worker = None
        self._rows: dict[str, list[dict]] = {
            "account": [], "article": [], "comment": [], "search": [],
        }
        self._cursor = {"article": "", "search": ""}
        self._detail_text = ""
        self.status = StatusLine("选一块工作区开始：找号 → 看文章 → 查数据", parent=self)
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
        if not require_feature(self, "wechat_mp", "公众号运营"):
            return
        client = getattr(self.ctx, "client", None)
        if client is None:
            self._say("数据服务未连接，请到「设置 → 数据服务」填 " + _key_hint(self.ctx), "error")
            return
        elif getattr(client, "provider", "") != "okdata":
            self._say("这两个功能仅支持 okdata 通道，请在 设置 → 数据服务 切换数据服务商为 okdata", "error")
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
        root.addWidget(page_title("公众号运营", self))
        root.addWidget(guide_banner("公众号运营", self))

        cost = QLabel(_COST_HINT, self)
        cost.setObjectName("Caption")
        cost.setWordWrap(True)
        root.addWidget(cost)

        # 用四个按钮 + QStackedWidget 切换：QSS 没给 QTabBar 配色，
        # 深色主题下系统原生标签栏会发白；按钮走已有 Primary 样式两主题都稳。
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._mode_btns: list[QPushButton] = []
        for text, slot in (
            ("① 找公众号", self._show_accounts),
            ("② 历史文章", self._show_articles),
            ("③ 文章诊断", self._show_article),
            ("④ 搜一搜选题", self._show_search),
        ):
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            self._wire_btn(btn, slot, "切换工作区")
            self._mode_btns.append(btn)
            nav.addWidget(btn)
        nav.addStretch(1)
        root.addLayout(nav)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_accounts())
        self.stack.addWidget(self._build_articles())
        self.stack.addWidget(self._build_article())
        self.stack.addWidget(self._build_search())
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

    def _show_accounts(self): self._select_mode(0)
    def _show_articles(self): self._select_mode(1)
    def _show_article(self): self._select_mode(2)
    def _show_search(self): self._select_mode(3)

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

    # ---------------------------- ① 找公众号 ----------------------------
    def _build_accounts(self) -> QWidget:
        host, lay = vertical_card("按昵称或关键词找公众号", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.account_kw = QLineEdit(self)
        self.account_kw.setPlaceholderText("公众号名称或关键词，例如：36氪 / 健身")
        row.addWidget(self.account_kw, 1)
        go = QPushButton("搜索公众号", self)
        go.setObjectName("Primary")
        self._wire_btn(go, self._search_accounts, "搜索公众号")
        row.addWidget(go)
        lay.addLayout(row)

        self.account_table = self._table(COLS_ACCOUNT, self)
        lay.addWidget(self.account_table)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        to_art = QPushButton("看这个号的文章 →", self)
        self._wire_btn(to_art, self._account_to_articles, "查看历史文章")
        ops.addWidget(to_art)
        exp = QPushButton("导出 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.account_table, COLS_ACCOUNT, "公众号搜索结果.csv"), "导出公众号")
        ops.addWidget(exp)
        ops.addStretch(1)
        lay.addLayout(ops)
        return host

    def _search_accounts(self):
        kw = self.account_kw.text().strip()
        if not kw:
            self._say("请先填公众号名称或关键词", "warn")
            return
        self._start(EP_ACCOUNT, {"keyword": kw}, self._on_accounts, "搜索公众号")

    def _on_accounts(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"搜索失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        rows = _items(getattr(resp, "data", None))
        self._rows["account"] = rows
        self._fill(self.account_table, rows, [
            lambda x: x.get("name") or x.get("nickname"),
            lambda x: x.get("username"),
            lambda x: x.get("id"),
            lambda x: "已认证" if x.get("verified") else "—",
            lambda x: _num(x.get("posts")),
            lambda x: x.get("bio"),
        ])
        self._say(f"找到 {len(rows)} 个公众号（搜索按返回条数计费，翻页请再次搜索）"
                  + ("" if rows else _BILLED_EMPTY),
                  "ok" if rows else "warn")

    def _account_to_articles(self):
        r = self.account_table.currentRow()
        if r < 0 or r >= len(self._rows["account"]):
            self._say("先选中一个公众号", "warn")
            return
        acc = self._rows["account"][r]
        self.article_id.setText(str(acc.get("id") or acc.get("username") or ""))
        self._select_mode(1)
        self._load_articles()

    # ---------------------------- ② 历史文章 ----------------------------
    def _build_articles(self) -> QWidget:
        host, lay = vertical_card("按公众号 ID 列出已发布文章", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.article_id = QLineEdit(self)
        self.article_id.setPlaceholderText("公众号原始 ID（gh_…）或微信号")
        row.addWidget(self.article_id, 2)
        self.article_tab = QComboBox(self)
        self.article_tab.addItem("文章栏", "articles")
        self.article_tab.addItem("视频栏", "videos")
        self.article_tab.addItem("贴图栏", "images")
        row.addWidget(self.article_tab)
        go = QPushButton("查历史文章", self)
        go.setObjectName("Primary")
        self._wire_btn(go, self._load_articles, "查询历史文章")
        row.addWidget(go)
        lay.addLayout(row)

        self.article_table = self._table(COLS_ARTICLE, self)
        lay.addWidget(self.article_table)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        nxt = QPushButton("下一页 →", self)
        self._wire_btn(nxt, self._next_articles, "翻页文章")
        self.next_article_btn = nxt
        nxt.setEnabled(False)
        ops.addWidget(nxt)
        diag = QPushButton("查互动数据 →", self)
        self._wire_btn(diag, self._article_to_diag, "查互动")
        ops.addWidget(diag)
        body = QPushButton("读正文 →", self)
        self._wire_btn(body, self._article_to_body, "读正文")
        ops.addWidget(body)
        exp = QPushButton("导出 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.article_table, COLS_ARTICLE, "公众号历史文章.csv"), "导出文章")
        ops.addWidget(exp)
        ops.addStretch(1)
        lay.addLayout(ops)
        return host

    def _article_params(self, cursor: str = "") -> dict:
        return {
            "id": self.article_id.text().strip(),
            "tab": self.article_tab.currentData() or "articles",
            "cursor": cursor,
            "view": "standard",
        }

    def _load_articles(self):
        if not self.article_id.text().strip():
            self._say("请先填公众号原始 ID 或微信号", "warn")
            return
        self._cursor["article"] = ""
        self.next_article_btn.setEnabled(False)
        self._start(EP_ARTICLES, self._article_params(), self._on_articles, "查询历史文章")

    def _next_articles(self):
        if not self._cursor["article"]:
            self._say("没有下一页了", "warn")
            return
        self._start(EP_ARTICLES, self._article_params(self._cursor["article"]),
                    self._on_articles, "翻页历史文章")

    def _on_articles(self, payload: dict):
        resp = payload.get("resp")
        if resp is None or not resp.success:
            self._say(f"查询失败：{getattr(resp, 'message', '未知错误')}", "error")
            return
        rows = _items(getattr(resp, "data", None))
        if payload.get("params", {}).get("cursor"):
            self._rows["article"].extend(rows)
        else:
            self._rows["article"] = rows
        self._fill(self.article_table, self._rows["article"], [
            lambda x: str(x.get("published_at") or "")[:19].replace("T", " "),
            lambda x: x.get("title"),
            lambda x: _author_name(x),
            lambda x: _num(_metrics(x).get("views")),
            lambda x: _num(_metrics(x).get("likes")),
            lambda x: x.get("url"),
        ])
        cursor, more = _page_info(getattr(resp, "data", None))
        self._cursor["article"] = cursor if more else ""
        self.next_article_btn.setEnabled(bool(cursor and more))
        self._say(f"列出 {len(rows)} 篇文章，累计 {len(self._rows['article'])} 篇"
                  "（历史文章列表通常不含阅读数，点「查互动数据」另查）"
                  + ("" if rows else _BILLED_EMPTY),
                  "ok" if rows else "warn")

    def _picked_article_url(self) -> str:
        r = self.article_table.currentRow()
        if r < 0 or r >= len(self._rows["article"]):
            return ""
        return str(self._rows["article"][r].get("url") or "")

    def _article_to_diag(self):
        url = self._picked_article_url()
        if not url:
            self._say("先在历史文章里选中一条", "warn")
            return
        self._select_mode(2)
        self.article_url.setText(url)
        self._diagnose(("metrics",))

    def _article_to_body(self):
        url = self._picked_article_url()
        if not url:
            self._say("先在历史文章里选中一条", "warn")
            return
        self._select_mode(2)
        self.article_url.setText(url)
        self._diagnose(("detail",))

    # ---------------------------- ③ 文章诊断 ----------------------------
    def _build_article(self) -> QWidget:
        host, lay = vertical_card("文章正文 / 互动数据 / 精选评论", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.article_url = QLineEdit(self)
        self.article_url.setPlaceholderText("mp.weixin.qq.com 文章链接（短链 / 长链都行）")
        row.addWidget(self.article_url, 1)
        all_btn = QPushButton("一次全查", self)
        all_btn.setObjectName("Primary")
        self._wire_btn(all_btn, lambda: self._diagnose(("detail", "metrics", "comments")),
                       "一次全查文章")
        row.addWidget(all_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        for text, kind, label in (
            ("仅正文", "detail", "读取正文"),
            ("仅互动", "metrics", "查询互动"),
            ("仅评论", "comments", "查询评论"),
        ):
            b = QPushButton(text, self)
            self._wire_btn(b, lambda k=kind, l=label: self._diagnose((k,)), label)
            row2.addWidget(b)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.metric_label = QLabel("互动数据：尚未查询", self)
        self.metric_label.setObjectName("Caption")
        self.metric_label.setWordWrap(True)
        lay.addWidget(self.metric_label)

        self.body_edit = QPlainTextEdit(self)
        self.body_edit.setPlaceholderText("正文会显示在这里，可保存为 Markdown 文件")
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
            self.comment_table, COLS_COMMENT, "公众号文章评论.csv"), "导出评论")
        row3.addWidget(exp)
        row3.addStretch(1)
        lay.addLayout(row3)

        self.comment_table = self._table(COLS_COMMENT, self)
        lay.addWidget(self.comment_table)
        return host

    def _diagnose(self, kinds: tuple[str, ...]):
        if not require_feature(self, "wechat_mp", "公众号运营"):
            return
        url = self.article_url.text().strip()
        if "mp.weixin.qq.com" not in url:
            self._say("请粘贴 mp.weixin.qq.com 的公众号文章链接", "warn")
            return
        client = getattr(self.ctx, "client", None)
        if client is None:
            self._say("数据服务未连接，请到「设置 → 数据服务」填 " + _key_hint(self.ctx), "error")
            return
        elif getattr(client, "provider", "") != "okdata":
            self._say("这两个功能仅支持 okdata 通道，请在 设置 → 数据服务 切换数据服务商为 okdata", "error")
            return
        self.progress.setValue(0)
        self._say("正在查询文章数据…", "idle")
        w = Worker(_diag_task, client, url, tuple(kinds))
        self._wire_worker(w, self._on_diag, "文章诊断")
        self._worker = w
        w.start()

    def _on_diag(self, result: dict):
        if not isinstance(result, dict):
            self._say("文章诊断返回异常", "error")
            return
        notes: list[str] = []
        for kind, label in (("detail", "正文"), ("metrics", "互动"), ("comments", "评论")):
            err = result.get(kind + "_error")
            if err:
                notes.append(f"{label}失败：{err}")
                continue
            resp = result.get(kind)
            if resp is None:
                continue
            if not resp.success:
                notes.append(f"{label}失败：{resp.message}")
                continue
            data = getattr(resp, "data", None)
            if kind == "detail":
                one = _items(data)
                item = one[0] if one else {}
                self._detail_text = str(item.get("text") or "")
                self.body_edit.setPlainText(
                    f"# {item.get('title') or '（无标题）'}\n\n"
                    f"> 作者：{_author_name(item)}　时间：{item.get('published_at') or '—'}\n"
                    f"> 原文：{item.get('url') or result.get('url', '')}\n\n"
                    + self._detail_text[:20000])
            elif kind == "metrics":
                one = _items(data)
                m = _metrics(one[0]) if one else {}
                self.metric_label.setText(
                    "互动数据：阅读 {v}　点赞 {l}　在看 {r}　分享 {s}　收藏 {f}　评论 {c}".format(
                        v=_num(m.get("views")), l=_num(m.get("likes")),
                        r=_num(m.get("recommends")), s=_num(m.get("shares")),
                        f=_num(m.get("favorites")), c=_num(m.get("comments"))))
            else:
                rows = _items(data)
                self._rows["comment"] = rows
                self._fill(self.comment_table, rows, [
                    lambda x: (x.get("author") or {}).get("name") if isinstance(x.get("author"), dict)
                              else x.get("nickname"),
                    lambda x: x.get("text") or x.get("content"),
                    lambda x: _num(_metrics(x).get("likes")),
                    lambda x: str(x.get("created_at") or "")[:19].replace("T", " "),
                    lambda x: x.get("ip_location") or x.get("ip"),
                ])
        self._say("；".join(notes) if notes else "文章数据查询完成" + _BILLED_EMPTY,
                  "error" if notes else "ok")

    def _save_markdown(self):
        text = self.body_edit.toPlainText().strip()
        if not text:
            self._say("先查出正文再保存", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存公众号正文", "公众号正文.md",
                                              "Markdown 文件 (*.md)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            self._say(f"保存失败：{exc}", "error")
            return
        self._say(f"已保存到 {path}", "ok")

    # ---------------------------- ④ 搜一搜选题 ----------------------------
    def _build_search(self) -> QWidget:
        host, lay = vertical_card("微信搜一搜：找文章与视频号选题", self)
        lay.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_kw = QLineEdit(self)
        self.search_kw.setPlaceholderText("行业 / 产品 / 选题关键词")
        row.addWidget(self.search_kw, 2)
        self.search_sort = QComboBox(self)
        for label, value in (("按相关", "relevance"), ("按最新", "latest"), ("按最热", "popular")):
            self.search_sort.addItem(label, value)
        row.addWidget(self.search_sort)
        self.search_time = QComboBox(self)
        for label, value in (("不限时间", ""), ("一天内", "day"),
                             ("一周内", "week"), ("半年内", "half_year")):
            self.search_time.addItem(label, value)
        row.addWidget(self.search_time)
        self.search_type = QComboBox(self)
        for label, value in (("文章 + 视频", ""), ("只看文章", "article"),
                             ("只看视频", "video")):
            self.search_type.addItem(label, value)
        row.addWidget(self.search_type)
        go = QPushButton("搜一搜", self)
        go.setObjectName("Primary")
        self._wire_btn(go, self._search_posts, "搜一搜")
        row.addWidget(go)
        lay.addLayout(row)

        self.search_table = self._table(COLS_SEARCH, self)
        lay.addWidget(self.search_table)

        ops = QHBoxLayout()
        ops.setSpacing(8)
        nxt = QPushButton("下一页 →", self)
        self._wire_btn(nxt, self._next_search, "翻页搜一搜")
        self.next_search_btn = nxt
        nxt.setEnabled(False)
        ops.addWidget(nxt)
        diag = QPushButton("查这篇文章互动 →", self)
        self._wire_btn(diag, self._search_to_diag, "查互动")
        ops.addWidget(diag)
        exp = QPushButton("导出 CSV", self)
        self._wire_btn(exp, lambda: self._export_csv(
            self.search_table, COLS_SEARCH, "搜一搜选题.csv"), "导出选题")
        ops.addWidget(exp)
        ops.addStretch(1)
        lay.addLayout(ops)
        return host

    def _search_params(self, cursor: str = "") -> dict:
        out = {
            "keyword": self.search_kw.text().strip(),
            "sort": self.search_sort.currentData() or "relevance",
            "cursor": cursor,
            "view": "standard",
        }
        time_value = self.search_time.currentData()
        type_value = self.search_type.currentData()
        if time_value:
            out["published_within"] = time_value
        if type_value:
            out["type"] = type_value
        return out

    def _search_posts(self):
        if not self.search_kw.text().strip():
            self._say("请先填关键词", "warn")
            return
        self._cursor["search"] = ""
        self.next_search_btn.setEnabled(False)
        self._start(EP_SEARCH, self._search_params(), self._on_search, "搜一搜")

    def _next_search(self):
        if not self._cursor["search"]:
            self._say("没有下一页了", "warn")
            return
        self._start(EP_SEARCH, self._search_params(self._cursor["search"]),
                    self._on_search, "翻页搜一搜")

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
            lambda x: "视频" if str(x.get("type") or "") in ("video", "channels") else "文章",
            lambda x: str(x.get("published_at") or "")[:19].replace("T", " "),
            lambda x: x.get("title"),
            lambda x: _author_name(x),
            lambda x: str((x.get("ext") or {}).get("read_count_range")
                          or _num(_metrics(x).get("views"))),
            lambda x: x.get("url"),
        ])
        cursor, more = _page_info(getattr(resp, "data", None))
        self._cursor["search"] = cursor if more else ""
        self.next_search_btn.setEnabled(bool(cursor and more))
        self._say(f"找到 {len(rows)} 条结果，累计 {len(self._rows['search'])} 条"
                  + ("" if rows else _BILLED_EMPTY),
                  "ok" if rows else "warn")

    def _search_to_diag(self):
        r = self.search_table.currentRow()
        if r < 0 or r >= len(self._rows["search"]):
            self._say("先选中一条文章", "warn")
            return
        url = str(self._rows["search"][r].get("url") or "")
        typ = str(self._rows["search"][r].get("type") or "")
        if "mp.weixin.qq.com" not in url or typ == "video":
            self._say("这条不是公众号文章，不能查文章互动数据", "warn")
            return
        self._select_mode(2)
        self.article_url.setText(url)
        self._diagnose(("metrics",))

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

    def _fill_demo(self):
        """空态引导按钮：只把关键词送进输入框，不替用户发起付费请求。"""
        self._select_mode(0)
        self.account_kw.setText("36氪")
        self.account_kw.setFocus()
        self._say("已填入示例关键词，点「搜索公众号」即可", "idle")

    def _fill_search_demo(self):
        self._select_mode(3)
        self.search_kw.setText("筋膜枪")
        self.search_kw.setFocus()
        self._say("已填入示例关键词，点「搜一搜」即可", "idle")

    def _goto_accounts(self):
        self._select_mode(0)
        self.account_kw.setFocus()
        self._say("先搜到公众号，再点「看这个号的文章」", "idle")

    def _goto_article(self):
        self._select_mode(2)
        self.article_url.setFocus()
        self._say("粘贴公众号文章链接，再点「仅评论」或「一次全查」", "idle")

    # ------------------------------------------------------------------
    #  主题
    # ------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        try:
            self.status.apply_theme(mode)
        except Exception:                                  # noqa: BLE001
            pass
