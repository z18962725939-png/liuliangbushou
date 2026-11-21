# -*- coding: utf-8 -*-
"""视频号解析页（2026-11-20）。

粘贴微信视频号分享链接 → 一键解析出「作者 / 标题 / 视频 ID / 封面 / 直链」，
可复制、可在浏览器打开、可批量下载成无水印 mp4。

★ 边界铁律（与 media_parser.py 顶部注释一致，不许改）
  · 只走免费解析服务 video.zacao.top（作者自己的 key，不从客户身上扣）；
  · 不调 JustOneAPI、不接付费闸门（require_feature）——它是软件自带能力；
  · 界面上不许出现「额度 / 次数 / 欠费」这类字眼跟这条链路挂钩；
  · 互动数据（点赞/评论/转发）本页不展示 —— 那要走付费接口，当前链路拿不到，
    宁可不显示也不编造假数据。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ._base import (
    StatusLine, Worker, connect_guarded, guide_banner, page_title,
    vertical_card,
)
from gui_qt.widgets.progress import FlowProgressBar
from justoneapi_collector import media_parser
from justoneapi_collector.video_downloader import VideoInfo


# 解析结果列（表头名进 main_window._TABLE_STRETCH 的「标题」会自动拉伸）
_COLS = ["作者", "标题", "视频ID", "平台", "封面", "直链"]
_COL_AUTHOR, _COL_TITLE, _COL_ID, _COL_PLAT, _COL_COVER, _COL_URL = range(6)


def _parse_task(worker, links: list[str]):
    """后台串行解析：一条一条来，逐条上报，失败隔离不连累整批。

    media_parser.parse 已保证失败返回 (None, 人话原因)、不抛异常；这里仍
    逐个 try 包一层，杜绝任何一条畸形链接把整个 Worker 带崩。
    """
    total = len(links)
    ok = 0
    for idx, link in enumerate(links, 1):
        worker.emit_progress(idx * 100.0 / max(total, 1),
                             f"正在解析 {idx}/{total} 条")
        row = {"link": link}
        try:
            data, reason = media_parser.parse(link)
        except Exception as exc:                          # noqa: BLE001
            data, reason = None, f"{type(exc).__name__}: {exc}"
        if data:
            row.update({
                "author": str(data.get("author") or ""),
                "title": str(data.get("title") or ""),
                "video_id": str(data.get("video_id") or ""),
                "platform_name": str(data.get("platform_name") or
                                     data.get("platform") or ""),
                "cover_url": str(data.get("cover_url") or ""),
                "video_url": str(data.get("video_url") or ""),
            })
            ok += 1
        else:
            row["reason"] = reason or "解析失败"
        worker.emit_item(row)
    return {"total": total, "ok": ok, "fail": total - ok}


def _download_task(worker, downloader, videos: list[VideoInfo]):
    """下载选中项：复用视频下载页同一套 downloader，进度逐条回传。

    ★ 保存目录必须在 GUI 线程就转成 Path —— 下载器内部全是 `save_dir / 文件名`，
      传字符串进来每条都会抛 TypeError，界面却只剩「失败 N 条」（2026-11-21
      客户截图就是这个坑）。
    """
    def on_progress(done, total, video, path, err):
        worker.emit_progress(done * 100.0 / max(total, 1),
                             f"已下载 {done}/{total} 条")
    return downloader.download_videos(videos, on_progress=on_progress)


class WechatChannelsPage(QWidget):
    PAGE_NAME = "视频号解析"

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._rows: list[dict] = []
        self._downloader = None
        self.status = StatusLine("粘贴视频号链接，点「开始解析」即可", parent=self)
        self._build()
        self.apply_theme("light")

    # ------------------------------------------------------------------
    #  guarded 接线（★ 禁止裸 connect、禁止 status.set）
    # ------------------------------------------------------------------
    def _wire_btn(self, btn, slot, label: str) -> None:
        connect_guarded(btn.clicked, lambda *_: slot(),
                        on_error=lambda m: self._say(m, "error"), label=label)

    def _wire_worker(self, w, done_slot, err_prefix: str, label: str) -> None:
        connect_guarded(w.done, done_slot,
                        on_error=lambda m: self._say(m, "error"), label=label)
        connect_guarded(w.error, lambda m: self._say(f"{err_prefix}：{m}", "error"),
                        on_error=lambda m: self._say(m, "error"), label=label + "异常")
        connect_guarded(w.progress, lambda pct, msg: self.progress.setValue(int(pct)),
                        on_error=lambda m: self._say(m, "error"), label=label + "进度")

    def _say(self, text: str, level: str = "idle") -> None:
        self.status.setText(text)
        self.status.mark(level)

    # ------------------------------------------------------------------
    #  布局
    # ------------------------------------------------------------------
    @staticmethod
    def _default_dir() -> str:
        try:
            from justoneapi_collector import branding
            d = branding.app_data_dir() / "视频号解析"
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
        except Exception:                                  # noqa: BLE001
            return str(Path.home() / "Downloads")

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        root.addWidget(page_title("视频号解析", self))
        root.addWidget(guide_banner("视频号解析", self))

        # 卡片一：输入 + 解析
        card1, cl1 = vertical_card("解析链接", self)
        cl1.setSpacing(10)
        self.links_edit = QPlainTextEdit(self)
        self.links_edit.setPlaceholderText(
            "每行一条视频号分享链接，支持「分享文案 + 链接」混排；\n"
            "例如：https://weixin.qq.com/sph/AsFrSACTaL")
        self.links_edit.setMinimumHeight(84)
        self.links_edit.setMaximumHeight(130)
        cl1.addWidget(self.links_edit)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.dir_edit = QLineEdit(self)
        self.dir_edit.setPlaceholderText("保存目录（下载用）")
        self.dir_edit.setText(self._default_dir())
        self.dir_edit.setReadOnly(True)
        row.addWidget(self.dir_edit, 1)
        pick = QPushButton("浏览…", self)
        self._wire_btn(pick, self._pick_dir, "选择目录")
        row.addWidget(pick)
        clear = QPushButton("清空", self)
        self._wire_btn(clear, self._clear, "清空")
        row.addWidget(clear)
        parse_btn = QPushButton("开始解析", self)
        parse_btn.setObjectName("Primary")
        self._wire_btn(parse_btn, self._parse, "解析")
        row.addWidget(parse_btn)
        cl1.addLayout(row)

        hint = QLabel(
            "解析走软件自带的免费链路，不消耗采集额度；作者、标题、封面、直链会列在下方。",
            self)
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        cl1.addWidget(hint)
        self.progress = FlowProgressBar(self)
        cl1.addWidget(self.progress)
        root.addWidget(card1)

        # 卡片二：结果表 + 操作
        card2, cl2 = vertical_card("解析结果", self)
        cl2.setSpacing(8)
        self.table = QTableWidget(0, len(_COLS), self)
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # ★ 不开启交替行色：QSS 没定义 alternate 色，Qt 会用系统默认白，
        #   深色主题下就变成白底浅字看不清（全工程其它页也都不开）。
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        connect_guarded(self.table.customContextMenuRequested, self._context_menu,
                        on_error=lambda m: self._say(m, "error"), label="右键菜单")
        connect_guarded(self.table.doubleClicked, self._open_row,
                        on_error=lambda m: self._say(m, "error"), label="打开直链")
        cl2.addWidget(self.table)

        ops = QHBoxLayout()
        ops.setSpacing(10)
        copy = QPushButton("复制选中直链", self)
        self._wire_btn(copy, self._copy_selected, "复制直链")
        ops.addWidget(copy)
        open_btn = QPushButton("打开选中", self)
        self._wire_btn(open_btn, self._open_selected, "打开链接")
        ops.addWidget(open_btn)
        dl = QPushButton("下载选中", self)
        dl.setObjectName("Primary")
        self._wire_btn(dl, self._download_selected, "下载")
        ops.addWidget(dl)
        ops.addStretch(1)
        cl2.addLayout(ops)
        root.addWidget(card2)

        root.addStretch(1)
        root.addWidget(self.status)

    # ------------------------------------------------------------------
    #  行为
    # ------------------------------------------------------------------
    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    def _clear(self):
        self.links_edit.clear()
        self._rows.clear()
        self.table.setRowCount(0)
        self.progress.setValue(0)
        self._say("已清空", "idle")

    def _fill_demo(self):
        """空态引导按钮：填入一条示例链接，客户点「开始解析」即见效果。"""
        self.links_edit.setPlainText("https://weixin.qq.com/sph/AsFrSACTaL")
        self._say("已填入示例链接，点「开始解析」试试", "idle")

    def _parse(self):
        raw = self.links_edit.toPlainText()
        links = [ln.strip() for ln in raw.splitlines()
                 if ln.strip() and ("://" in ln or "sph" in ln)]
        if not links:
            self._say("请先粘贴至少一条视频号链接", "warn")
            return
        self._rows.clear()
        self.table.setRowCount(0)
        self.progress.setValue(0)
        self._say(f"正在解析 {len(links)} 条…", "idle")
        w = Worker(_parse_task, links)
        connect_guarded(w.item_done, self._on_item,
                        on_error=lambda m: self._say(m, "error"), label="逐条解析")
        self._wire_worker(w, self._on_parsed, "解析失败", "解析")
        w.start()

    def _on_item(self, row: dict):
        """逐条上表：解析成功出字段，失败出原因（不编造数据）。"""
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._rows.append(row)
        cover = row.get("cover_url", "")
        url = row.get("video_url", "")
        # 封面/直链是超长 URL，表格里塞完整串只会显示 https://... 还占宽；
        # 显示简短标记，完整地址放 tooltip，复制/打开功能照用真实值。
        vals = [
            row.get("author", ""),
            row.get("title", ""),
            row.get("video_id", ""),
            row.get("platform_name", ""),
            ("有封面" if cover else "—"),
            ("可打开" if url else "—"),
        ]
        tips = [
            row.get("author", ""), row.get("title", ""),
            row.get("video_id", ""), row.get("platform_name", ""),
            cover, url,
        ]
        for col, (val, tip) in enumerate(zip(vals, tips)):
            item = QTableWidgetItem(val)
            if tip:
                item.setToolTip(tip)
            self.table.setItem(r, col, item)
        if not row.get("video_url"):
            # 失败行：把原因写进标题列，作者列标“解析失败”，绝不填假直链
            self.table.item(r, _COL_AUTHOR).setText("解析失败")
            self.table.item(r, _COL_TITLE).setText(row.get("reason", "")[:120])

    def _on_parsed(self, result):
        ok = result.get("ok", 0) if isinstance(result, dict) else 0
        total = result.get("total", 0) if isinstance(result, dict) else 0
        fail = total - ok
        self.progress.setValue(100)
        level = "ok" if ok and not fail else ("warn" if ok else "error")
        self._say(f"解析完成：成功 {ok} 条，失败 {fail} 条", level)

    def _selected_row(self) -> int:
        return self.table.currentRow()

    def _selected_url(self) -> str:
        r = self._selected_row()
        if r < 0 or r >= len(self._rows):
            return ""
        return self._rows[r].get("video_url", "")

    def _copy_selected(self):
        url = self._selected_url()
        if not url:
            self._say("先选中一条有直链的解析结果", "warn")
            return
        QApplication.clipboard().setText(url)
        self._say("直链已复制到剪贴板", "ok")

    def _open_one(self, url: str):
        if not url:
            self._say("这条没有可打开的直链", "warn")
            return
        QDesktopServices.openUrl(QUrl(url))

    def _open_selected(self):
        self._open_one(self._selected_url())

    def _open_row(self, index):
        row = index.row()
        if 0 <= row < len(self._rows):
            self._open_one(self._rows[row].get("video_url", ""))

    def _context_menu(self, pos):
        r = self.table.rowAt(pos.y())
        if r < 0:
            return
        self.table.selectRow(r)
        menu = QMenu(self)
        ca = menu.addAction("复制直链")
        cb = menu.addAction("在浏览器打开")
        picked = menu.exec(self.table.viewport().mapToGlobal(pos))
        if picked == ca:
            self._copy_selected()
        elif picked == cb:
            self._open_selected()

    def _download_selected(self):
        picked_idx = sorted({i.row() for i in self.table.selectedIndexes()})
        vids: list[VideoInfo] = []
        for i in picked_idx:
            if i >= len(self._rows):
                continue
            row = self._rows[i]
            url = row.get("video_url", "")
            if not url:
                continue
            vids.append(VideoInfo(
                video_id=row.get("video_id", ""),
                desc=row.get("title", ""),
                author=row.get("author", ""),
                cover_url=row.get("cover_url", ""),
                no_watermark_url=url,
                share_url=row.get("link", ""),
                platform="weixin_channels",
            ))
        if not vids:
            self._say("先勾选至少一条有直链的结果", "warn")
            return
        try:
            downloader = self.ctx.ensure_downloader()
        except Exception as exc:                          # noqa: BLE001
            self._say(f"下载器初始化失败：{exc}", "error")
            return
        self._downloader = downloader
        save_text = self.dir_edit.text().strip()
        save_dir: Path | None = None
        if save_text:
            try:
                save_dir = Path(save_text).expanduser()
                save_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._say(f"保存目录不可用：{exc}", "error")
                return
        if save_dir is not None:
            downloader.save_dir = save_dir
        self.progress.setValue(0)
        self._say(f"正在下载 {len(vids)} 条…", "idle")
        w = Worker(_download_task, downloader, vids)
        self._wire_worker(w, self._on_downloaded, "下载失败", "下载")
        w.start()

    def _on_downloaded(self, results):
        results = results or []
        ok = sum(1 for _v, p, _e in results if p)
        fail = len(results) - ok
        self.progress.setValue(100)
        level = "ok" if ok and not fail else ("warn" if ok else "error")
        msg = f"下载完成：成功 {ok} 条，失败 {fail} 条"
        # ★ 失败必须带原因：客户只看到「失败 1 条」等于没说，也没法反馈。
        reasons: list[str] = []
        for _v, _p, err in results:
            text = str(err or "").strip()
            if text and text not in reasons:
                reasons.append(text)
        if reasons:
            msg += "；失败原因：" + "；".join(reasons)
        note = str(getattr(self._downloader, "last_dir_note", "") or "").strip()
        if note and note not in msg:
            msg += f"（{note}）"
        self._say(msg[:400], level)

    # ------------------------------------------------------------------
    #  主题
    # ------------------------------------------------------------------
    def apply_theme(self, mode: str) -> None:
        try:
            self.status.apply_theme(mode)
        except Exception:                                  # noqa: BLE001
            pass
