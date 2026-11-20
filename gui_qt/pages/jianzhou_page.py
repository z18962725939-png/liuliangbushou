"""见舟工作台 · 深度内嵌页（PySide6 + QtWebEngine）。

见舟整套（FastAPI 后端 + Vue3 前端）随流量捕手一起分发：
  * ``jianzhou_host`` 在同进程启动见舟服务，数据存进流量捕手自己的 AppData；
  * 本页用 QWebEngineView 把见舟界面**嵌进软件窗口**，不跳外部浏览器；
  * 主题跟随软件明暗切换；软件退出时服务自动停止。

页面顶部保留服务状态栏和「推送线索到内容雷达」面板（把流量捕手采集的
结果送进见舟），下方是完整的见舟工作台界面。
"""
import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ._base import (
    StatusLine, Worker, connect_guarded, guide_banner, page_title,
    require_feature, vertical_card,
)
from gui_qt.widgets.progress import FlowProgressBar
from justoneapi_collector import jianzhou_bridge as jz
from justoneapi_collector import jianzhou_host as hostmod

# 主题注入脚本：见舟页面用 data-theme 属性切主题，跟软件一致。
_JS_SET_THEME = (
    "(function(mode){"
    "var d=document.documentElement;"
    "if(d){d.setAttribute('data-theme',mode);}"
    "var b=document.body;if(b){b.setAttribute('data-theme',mode);}"
    "return true;})("
)


class _SilentPage(QWebEnginePage):
    """拦掉见舟页面里指向外部地址的新窗口请求，避免弹浏览器。"""

    def createWindow(self, _type):                            # noqa: N802
        return None


class JianzhouPage(QWidget):
    PAGE_NAME = "见舟工作台"

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._theme = "light"
        self.status = StatusLine("见舟工作台随软件启动，首次加载需要几秒", parent=self)
        self._build_ui()
        self._boot()

    # ------------------------------------------------------------------
    #  构造
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(10)
        root.addWidget(page_title("见舟工作台", self))
        root.addWidget(guide_banner("见舟工作台", self))

        # 卡片一：内嵌服务状态 + 控制
        card1, cl1 = vertical_card("内嵌工作台", self)
        cl1.setSpacing(8)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.state_label = QLabel("正在启动见舟服务…", self)
        self.state_label.setObjectName("Caption")
        self.state_label.setWordWrap(True)
        row1.addWidget(self.state_label, 1)
        refresh = QPushButton("重新加载", self)
        refresh.clicked.connect(lambda *_: self._reload())
        row1.addWidget(refresh)
        ext = QPushButton("在浏览器打开", self)
        ext.clicked.connect(lambda *_: self._open_external())
        row1.addWidget(ext)
        cl1.addLayout(row1)
        self.progress = FlowProgressBar(self)
        cl1.addWidget(self.progress)
        root.addWidget(card1, 0)

        # 卡片二：内嵌浏览器（占满剩余空间）
        card2, cl2 = vertical_card("工作台界面", self)
        cl2.setContentsMargins(6, 6, 6, 6)
        self.view = QWebEngineView(self)
        self.view.setPage(_SilentPage(self.view))
        self.view.setMinimumHeight(420)
        self.view.urlChanged.connect(lambda *_: self._on_url())
        cl2.addWidget(self.view, 1)
        root.addWidget(card2, 1)

        # 卡片三：把流量捕手的结果送进见舟
        card3, cl3 = vertical_card("推送线索到内容雷达", self)
        cl3.setSpacing(8)
        tip = QLabel(
            "每行一条：平台|标题|链接|备注（链接和备注可省）；采集到的作品、下载记录都可以这样送进见舟继续做选题。",
            self,
        )
        tip.setObjectName("Caption")
        tip.setWordWrap(True)
        cl3.addWidget(tip)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.rows_edit = QPlainTextEdit(self)
        self.rows_edit.setPlaceholderText(
            "抖音|某按摩棒工厂的车间实拍|https://www.douyin.com/video/xxx|客户看完问了交期"
        )
        self.rows_edit.setFixedHeight(74)
        row.addWidget(self.rows_edit, 1)
        btns = QVBoxLayout()
        btns.setSpacing(6)
        push = QPushButton("推送到见舟", self)
        push.setObjectName("Primary")
        push.clicked.connect(lambda *_: self._push())
        btns.addWidget(push)
        from_hist = QPushButton("导入采集记录", self)
        from_hist.clicked.connect(lambda *_: self._import_history())
        btns.addWidget(from_hist)
        demo = QPushButton("填入示例", self)
        demo.clicked.connect(lambda *_: self._fill_demo())
        btns.addWidget(demo)
        row.addLayout(btns)
        cl3.addLayout(row)
        self.results = QTableWidget(0, 3, self)
        self.results.setHorizontalHeaderLabels(["标题", "结果", "说明"])
        self.results.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.results.verticalHeader().setVisible(False)
        rh = self.results.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        rh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        rh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.results.setMinimumHeight(70)
        cl3.addWidget(self.results)
        root.addWidget(card3, 0)
        root.addWidget(self.status, 0)

    # ------------------------------------------------------------------
    #  通用
    # ------------------------------------------------------------------
    def _say(self, msg: str, level: str = ""):
        self.status.setText(msg)
        if level in ("error", "ok"):
            try:
                self.status.mark(level)
            except Exception:                                   # noqa: BLE001
                pass

    def _set_progress(self, value: int, note: str = "") -> None:
        self.progress.setValue(int(value))
        self.progress.setText(note)

    def _wire_worker(self, w, done_slot, err_prefix: str, label: str) -> None:
        connect_guarded(w.done, done_slot,
                        on_error=lambda m: self._say(m, "error"), label=label)
        connect_guarded(w.error, lambda m: self._say(f"{err_prefix}：{m}", "error"),
                        on_error=lambda m: self._say(m, "error"), label=label + "异常")
        connect_guarded(w.progress,
                        lambda pct, msg: self._set_progress(int(pct), str(msg)),
                        on_error=lambda m: self._say(m, "error"), label=label + "进度")

    # ------------------------------------------------------------------
    #  内嵌服务
    # ------------------------------------------------------------------
    def _boot(self):
        if not require_feature(self, "jianzhou_bridge", "见舟工作台桥接"):
            return
        # ★ 无头（离屏门禁/冒烟）只验证界面能构建，不真起同进程 uvicorn
        #   守护线程 + 加载 WebEngine。否则进程退出收尾阶段 daemon 线程与
        #   WebEngine 子进程互抢，随机触发 0xC0000409 栈溢出，把发版门禁
        #   假失败。真实软件不设 QT_QPA_PLATFORM=offscreen，完全不受影响。
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            return
        self._say("正在启动内嵌的见舟工作台…")
        self._set_progress(20, "启动服务")
        w = Worker(self._start_task)
        self._wire_worker(w, self._on_started, "启动失败", "见舟启动")
        w.start()

    @staticmethod
    def _start_task(w) -> tuple[str, dict]:
        w.emit_progress(50, "初始化数据库")
        url = hostmod.host().start()
        return url, hostmod.host().status()

    def _on_started(self, result):
        url, state = result if isinstance(result, tuple) else ("", {})
        if url:
            self.state_label.setText(f"内嵌服务已启动 · {url}")
            self._say("见舟工作台已就绪，可直接在下方使用", "ok")
            self.view.setUrl(QUrl(url))
            self._set_progress(100, "已就绪")
        else:
            msg = (state.get("error") or hostmod.host().error or "无法启动内嵌服务")
            self.state_label.setText(msg)
            self._say(msg + "；可联系作者获取支持", "error")
            self._set_progress(0, "未启动")

    def _reload(self):
        if not require_feature(self, "jianzhou_bridge", "见舟工作台桥接"):
            return
        url = hostmod.host().url
        if not url:
            self._boot()
            return
        self.view.setUrl(QUrl(url))
        self._say("已重新加载见舟工作台")

    def _open_external(self):
        if not require_feature(self, "jianzhou_bridge", "见舟工作台桥接"):
            return
        url = hostmod.host().url or "https://github.com/tangchengjiao/jianzhou-media-workbench"
        QDesktopServices.openUrl(QUrl(url))

    def _on_url(self):
        url = self.view.url().toString()
        if url and not url.startswith("http://127.0.0.1:"):
            self._say("见舟页面试图跳转到外部地址，已在本窗口拦截")

    def apply_theme(self, mode: str) -> None:
        """主窗口切主题时会自动调用（约定名 apply_theme）。"""
        self._theme = "dark" if mode == "dark" else "light"
        try:
            self.view.page().runJavaScript(_JS_SET_THEME + '"' + self._theme + '")')
        except Exception:                                      # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    #  数据链路
    # ------------------------------------------------------------------
    def _import_history(self):
        if not require_feature(self, "jianzhou_bridge", "见舟工作台桥接"):
            return
        items = jz.from_history(limit=20)
        if not items:
            self._say("历史库里还没有采集记录；先去「数据采集 / 选题挖掘」跑一批", "error")
            return
        self.rows_edit.setPlainText(jz.to_rows_text(items))
        self._say(f"已导入 {len(items)} 条采集记录，确认后点「推送到见舟」", "ok")

    def _fill_demo(self):
        self.rows_edit.setPlainText(
            "抖音|按摩棒工厂车间实拍：客户最在意的三个细节|https://www.douyin.com/video/demo|客户看完问了交期\n"
            "小红书|竞品爆款封面拆解（个人成长赛道）|https://www.xiaohongshu.com/explore/demo"
        )

    def _push(self):
        if not require_feature(self, "jianzhou_bridge", "见舟工作台桥接"):
            return
        rows, errors = jz.parse_rows(self.rows_edit.toPlainText())
        if errors:
            self._say("；".join(errors), "error")
            return
        if not rows:
            self._say("先填至少一行线索，或点「填入示例」看看格式", "error")
            return
        base = hostmod.host().url
        if not base:
            self._say("见舟服务还没起来，等它就绪后再推送", "error")
            return
        self.results.setRowCount(0)
        self._say(f"正在推送 {len(rows)} 条线索到见舟…")
        self._set_progress(10, "推送中")
        w = Worker(self._push_task, base.rstrip("/"), rows)
        self._wire_worker(w, self._on_push, "推送失败", "见舟推送")
        w.start()

    @staticmethod
    def _push_task(w, base: str, rows: list[dict]) -> list[dict]:
        out = []
        for index, item in enumerate(rows, start=1):
            w.emit_progress(10 + 85 * index / max(1, len(rows)), f"第 {index}/{len(rows)} 条")
            out.append(jz.push_signal(base, item))
        return out

    def _on_push(self, results: list[dict]):
        if not isinstance(results, list):
            self._say("推送结果格式异常", "error")
            return
        ok_count = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            good = bool(item.get("ok"))
            ok_count += int(good)
            r = self.results.rowCount()
            self.results.insertRow(r)
            self.results.setItem(r, 0, QTableWidgetItem(str(item.get("title") or "—")))
            self.results.setItem(r, 1, QTableWidgetItem(
                "已存在" if item.get("existing") else ("已写入" if good else "失败")))
            self.results.setItem(r, 2, QTableWidgetItem(
                str(item.get("note") or item.get("error") or "")))
        self._set_progress(100 if ok_count else 0, "完成")
        self._say(f"推送完成：成功 {ok_count}/{len(results)} 条", "ok" if ok_count else "error")
