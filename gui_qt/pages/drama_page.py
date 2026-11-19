"""短剧成片页（PySide6）：把一部剧丢进去，出一面墙能直接发的竖屏成片。

★ 为什么剪辑引擎是「按需下载」而不是随包
-------------------------------------------
剪辑组件实测 590MB 量级（ML 服务 252MB + 内置 ffmpeg 194MB + 语音模型 145MB）。主包
44MB，直接塞进去体积变十几倍，而绝大多数客户用不到短剧剪辑。
所以主包不动，客户第一次打开本页才下载（drama_bundle）。

★ 线程模型（这页的长任务特别多，别在主线程干）
-----------------------------------------------
  · 下载/解压/分析/导出 一律走 Worker（QThread）；
  · 剪辑服务进程推来的进度与日志，发生在 IPC 读线程上，
    不能直接碰控件 —— 统一经 _Signals 转成 Qt 信号，由 Qt 排队回主线程。
    PySide6 的 Signal.emit 跨线程是安全的，会自动排队到接收者所在线程。
  · 所有槽一律 connect_guarded 接（项目铁律：槽里抛异常 = 打包后静默卡死）。

★ 长任务节奏（与服务端对齐，别自创）
-------------------------------------
  提交 → 拿 job_id → 轮询 jobs.get 到终态。终态是 done/failed/cancelled。
  中途的进度由服务端主动推 progress.update，不是我们猜的。
"""
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import (StatusLine, Worker, append_log, connect_guarded, guide_banner,
                    log_panel, page_title, require_feature, row_widget, vertical_card)

# ★ 进度条必须用项目统一的 FlowProgressBar：它自带百分比、走到一半不会变细、
#   进度也绝不倒退。发版自检会扫 gui_qt 下的 QProgressBar 残留，
#   残留一处就中止发版 —— 别图省事 new 一个原生的。
from gui_qt.widgets.kit import DisclosurePanel, Segmented
from gui_qt.widgets.progress import FlowProgressBar

from justoneapi_collector import drama_bundle, drama_bridge
from justoneapi_collector.drama_bridge import DramaBridge, DramaBridgeError

# 组件包下载源（与流量捕手共用 COS 桶）
BUNDLE_URL = ("https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com"
              "/trafficcatcher/drama_clip.zip")

_EPISODE_HEADERS = ("#", "集名", "时长", "状态")
_PROJECT_HEADERS = ("剧名", "素材目录", "集数", "状态")


class _Signals(QObject):
    """把 IPC 读线程的回调转成 Qt 信号（跨线程安全）。"""

    progress = Signal(dict)
    logline = Signal(dict)
    dead = Signal()


class DramaPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._bridge: DramaBridge | None = None
        self._sig = _Signals()
        self._projects: list[dict] = []
        self._episodes: list[dict] = []
        self._pid: str | None = None
        self._busy = False
        self._build_ui()
        connect_guarded(self._sig.progress, self._on_progress,
                        label="剪辑进度")
        connect_guarded(self._sig.logline, self._on_logline, label="剪辑日志")
        connect_guarded(self._sig.dead, self._on_dead, label="剪辑服务断开")
        # 构造期不碰网络、不起进程：留到事件循环起来之后，避免构造期开线程
        QTimer.singleShot(0, self._check_bundle)

    # ── 界面 ──────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("短剧成片"))
        root.addWidget(guide_banner("短剧成片", self))

        # ★ 2026-10-27：1541px → 分两档「剧集 / 成片」。
        #   这一页是三张表叠着铺（项目 150 + 集 150 + 成片 130），加上引擎和
        #   日志，一屏根本放不下。但客户的动作是线性的：先建剧集、扫集数，
        #   再分析、再看成片 —— 两件事不必同时占屏。
        # ★ 同转写页：setVisible 切换，不用 QStackedWidget（高度会被最高子页撑住）。
        seg_row = QHBoxLayout()
        seg_row.setSpacing(8)
        self.step_seg = Segmented(
            [("剧集与分集", "ep"), ("成片与日志", "out")],
            mode="light", parent=self)
        self.step_seg.changed.connect(self._sync_step)
        seg_row.addWidget(self.step_seg)
        seg_row.addStretch()
        root.addLayout(seg_row)

        self._view_ep = QWidget(self)
        ep_v = QVBoxLayout(self._view_ep)
        ep_v.setContentsMargins(0, 0, 0, 0)
        ep_v.setSpacing(14)
        self._view_out = QWidget(self)
        out_v = QVBoxLayout(self._view_out)
        out_v.setContentsMargins(0, 0, 0, 0)
        out_v.setSpacing(14)
        self._view_out.setVisible(False)

        # ① 剪辑引擎（组件状态 + 下载）
        eng, eng_l = vertical_card("", self)
        self.engine_state = QLabel("正在检查…")
        self.engine_state.setStyleSheet("color:#6B7280;font-size:12px;")
        eng_l.addWidget(self.engine_state)

        self.dl_bar = FlowProgressBar()
        self.dl_bar.setRange(0, 100)
        self.dl_bar.setValue(0)
        self.dl_bar.setVisible(False)
        eng_l.addWidget(self.dl_bar)

        btn_row = QHBoxLayout()
        # ★ 按钮上写的是**下载量** 420MB，不是解压后占盘的 590MB。
        #   客户真正等的是下载，把 450 写在按钮上会让人以为要下 450，
        #   结果进度条跑得比预期快 —— 这种"惊喜"是拿信息不准换来的，不要。
        self.dl_btn = QPushButton("下载剪辑组件（约 420MB，只下一次）")
        self.dl_btn.clicked.connect(self._download_bundle)
        self.dl_btn.setVisible(False)
        btn_row.addWidget(self.dl_btn)
        self.open_dir_btn = QPushButton("打开素材输出目录")
        self.open_dir_btn.clicked.connect(self._open_outputs)
        btn_row.addWidget(self.open_dir_btn)
        btn_row.addStretch()
        eng_l.addLayout(btn_row)
        root.addWidget(eng)

        # ② 项目
        proj, proj_l = vertical_card("剧集项目", self)
        self.proj_table = QTableWidget(0, len(_PROJECT_HEADERS))
        self.proj_table.setHorizontalHeaderLabels(_PROJECT_HEADERS)
        self.proj_table.verticalHeader().setVisible(False)
        self.proj_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.proj_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.proj_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.proj_table.horizontalHeader().setStretchLastSection(True)
        self.proj_table.setMinimumHeight(150)
        proj_l.addWidget(self.proj_table)
        # 选中哪部剧，下面就跟着显示哪部剧的集（不接这个信号，点行毫无反应）
        connect_guarded(self.proj_table.itemSelectionChanged,
                        self._on_pick_project, label="切换剧集")

        pb = QHBoxLayout()
        self.new_btn = QPushButton("新增剧集（选素材文件夹）")
        self.new_btn.clicked.connect(self._new_project)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._refresh_projects)
        self.del_btn = QPushButton("删除剧集")
        self.del_btn.clicked.connect(self._delete_project)
        for b in (self.new_btn, self.refresh_btn, self.del_btn):
            pb.addWidget(b)
        pb.addStretch()
        proj_l.addLayout(pb)
        root.addWidget(proj)

        # ③ 集
        ep, ep_l = vertical_card("集（选中剧集后显示）", self)
        self.ep_table = QTableWidget(0, len(_EPISODE_HEADERS))
        self.ep_table.setHorizontalHeaderLabels(_EPISODE_HEADERS)
        self.ep_table.verticalHeader().setVisible(False)
        self.ep_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ep_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ep_table.horizontalHeader().setStretchLastSection(True)
        self.ep_table.setMinimumHeight(150)
        ep_l.addWidget(self.ep_table)

        eb = QHBoxLayout()
        self.analyze_btn = QPushButton("分析选中剧集（转写 + 场景 + 字幕）")
        self.analyze_btn.clicked.connect(self._analyze)
        self.scan_btn = QPushButton("重新扫描")
        self.scan_btn.clicked.connect(self._scan)
        for b in (self.analyze_btn, self.scan_btn):
            eb.addWidget(b)
        eb.addStretch()
        ep_l.addLayout(eb)
        root.addWidget(ep)

        # ④ 成片
        out, out_l = vertical_card("成片", self)
        self.work_table = QTableWidget(0, 3)
        self.work_table.setHorizontalHeaderLabels(("成片", "模式", "状态"))
        self.work_table.verticalHeader().setVisible(False)
        self.work_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.work_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.work_table.horizontalHeader().setStretchLastSection(True)
        self.work_table.setMinimumHeight(130)
        out_l.addWidget(self.work_table)

        ob = QHBoxLayout()
        self.export_btn = QPushButton("生成成片并导出")
        self.export_btn.clicked.connect(self._export)
        self.open_out_btn = QPushButton("打开成片目录")
        self.open_out_btn.clicked.connect(self._open_outputs)
        for b in (self.export_btn, self.open_out_btn):
            ob.addWidget(b)
        ob.addStretch()
        out_l.addLayout(ob)
        root.addWidget(out)

        # ⑤ 状态与日志
        self.status = StatusLine()
        root.addWidget(self.status)
        self.log = log_panel()
        self.log.setMinimumHeight(120)
        root.addWidget(self.log)

        self._set_operational(False)

    # ── 分档 ──────────────────────────────────────────────────
    def _sync_step(self, key: str):
        """两档互斥显示。key 取 'ep' / 'out'。"""
        self._view_ep.setVisible(key != "out")
        self._view_out.setVisible(key == "out")

    def apply_theme(self, mode: str):
        """分段控件与折叠条都是自绘的，切主题必须转交。"""
        self._mode = mode
        for w in (getattr(self, "step_seg", None),
                  getattr(self, "eng_fold", None),
                  getattr(self, "log_fold", None)):
            if w is not None:
                w.apply_theme(mode)

    # ── 组件包 ────────────────────────────────────────────────

    def _check_bundle(self):
        """看组件在不在，在就顺手把服务起起来。

        ★ 演示模式（发版冒烟 / 自检）下既不下组件也不起进程。
          这条是被发版冒烟逼出来的：本机构建 17 个页面时，剪辑组件早就装好了，
          页面一构造就把剪辑服务真的拉起来（子进程 + IPC 读线程），
          冒烟跑完这些没停干净，进程退出码 3221226505（0xC0000409）
          直接把整条发版卡死在"冒烟没过"上 —— 而它其实全部构建通过了。
          自检也不该顺手起别人的服务进程，更不该真去下 420MB。
        """
        if getattr(self.ctx, "demo", False):
            self.dl_btn.setVisible(False)
            self.engine_state.setText("演示模式：不下载组件、不启动剪辑服务")
            self._set_operational(False)
            return
        if drama_bundle.is_installed():
            self.dl_btn.setVisible(False)
            self.dl_bar.setVisible(False)
            self.engine_state.setText("剪辑组件已就绪，正在启动服务…")
            self._start_bridge()
        else:
            self.dl_btn.setVisible(True)
            self.engine_state.setText(
                "首次使用需要下载剪辑组件（下载约 420MB，装完占 590MB 磁盘）。"
                "语音模型已打在包里，装完不用再等它下模型。"
                "不用的话不用下，流量捕手其余功能不受影响。")
            self._set_operational(False)

    def _download_bundle(self):
        # ★ 按钮不禁用，重复点要给话说（家规：禁用按钮=烂门禁，点了没反应像坏了）
        if self._busy:
            self.status.setText("组件正在下载中，不用重复点。")
            return
        self._busy = True
        self.dl_bar.setVisible(True)
        self.dl_bar.setValue(0)

        def job(w):
            dest = drama_bundle.zip_path()
            # 先清半成品：换源/升级后旧的记账本会让新包永远下不完整
            drama_bundle.clear_partials()
            drama_bundle.download(
                BUNDLE_URL, dest,
                on_progress=lambda pct, msg: w.emit_progress(pct, msg))
            w.emit_progress(100.0, "下载完成，正在解压…")
            drama_bundle.install(
                on_progress=lambda pct, msg: w.emit_progress(pct, msg))
            return str(drama_bundle.bundle_dir())

        w = Worker(job)
        connect_guarded(w.progress, self._on_dl_progress, label="组件下载进度")
        connect_guarded(w.done, self._on_dl_done, label="组件下载完成")
        connect_guarded(w.error, self._on_dl_error, label="组件下载失败")
        w.start()

    def _on_dl_progress(self, pct, msg):
        self.dl_bar.setValue(int(pct))
        self.engine_state.setText(f"下载中：{msg}")

    def _on_dl_done(self, _ret):
        self._busy = False
        self.dl_bar.setVisible(False)
        self.dl_btn.setVisible(False)
        self.engine_state.setText("剪辑组件已就绪，正在启动服务…")
        append_log(self.log, "剪辑组件安装完成")
        self._start_bridge()

    def _on_dl_error(self, msg):
        self._busy = False
        self.dl_bar.setVisible(False)
        self.engine_state.setText(f"下载失败：{msg}")
        append_log(self.log, f"组件下载失败：{msg}")
        # 失败要能重试，且半成品清掉（留着会让下次从错误的块继续）
        drama_bundle.clear_partials()

    # ── 服务进程 ──────────────────────────────────────────────

    def _start_bridge(self):
        def job(w):
            data_dir = drama_bundle.data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            br = DramaBridge(
                exe=drama_bundle.service_exe(),
                resources_dir=drama_bundle.resources_dir(),
                data_dir=data_dir,
                on_progress=lambda p: self._sig.progress.emit(p),
                on_log=lambda p: self._sig.logline.emit(p),
                on_disconnect=lambda: self._sig.dead.emit(),
            )
            br.start(timeout=40.0)
            # 组件包里带的是 base 档，不钉住的话默认档 small 会让分析一上来就报缺模型
            drama_bridge.apply_defaults(br)
            w.emit_progress(100.0, "服务已就绪")
            return br

        w = Worker(job)
        connect_guarded(w.done, self._on_bridge_up, label="剪辑服务启动完成")
        connect_guarded(w.error, self._on_bridge_fail, label="剪辑服务启动失败")
        w.start()

    def _on_bridge_up(self, br):
        self._bridge = br
        self.engine_state.setText("剪辑服务已就绪")
        append_log(self.log, "剪辑服务已启动，可以开始分析")
        self._set_operational(True)
        self._refresh_projects()

    def _on_bridge_fail(self, msg):
        self.engine_state.setText(f"剪辑服务启动失败：{msg}")
        append_log(self.log, f"服务启动失败：{msg}")
        self._set_operational(False)

    def _on_dead(self):
        self._bridge = None
        self.engine_state.setText("剪辑服务已断开，请重新打开本页")
        append_log(self.log, "剪辑服务连接断开")
        self._set_operational(False)

    def _bridge_or_warn(self):
        """按钮点了才判定，并按缺什么说不同的话（灰着不动是甩锅，不是门禁）。"""
        if self._bridge is not None and self._bridge.running:
            return self._bridge
        if not drama_bundle.is_installed():
            QMessageBox.information(
                self, "先装一下剪辑组件",
                "短剧成片需要一套剪辑组件，第一次用才下：下载约 420MB，"
                "装完占 590MB 磁盘。语音模型已打在包里，\n"
                "装完就能直接转写，不用再等它现下模型。\n\n"
                "点上面那个「下载剪辑组件」按钮，下完就能用了。\n"
                "不用这个功能的话不用下，流量捕手其余功能不受影响。")
        else:
            QMessageBox.information(
                self, "剪辑服务还没就绪",
                "剪辑组件装好了，但服务进程还没起来。\n\n"
                "一般是刚打开软件还在启动，稍等几秒再点一次就行；"
                "一直这样就把软件重开一下。")
        return None

    def _set_operational(self, ok: bool):
        """★ 项目铁律：禁用按钮 = 烂门禁。点了没反应，客户只会以为软件坏了。

        所以这里刻意**不做** setEnabled(False) —— 本页所有按钮永远可点。
        组件没装 / 服务没起来时，点了由 _bridge_or_warn 弹窗说明情况并给下一步，
        那才叫门禁；灰着不动是甩锅。
        """
        # 只记状态给文案用，不动按钮的可点性
        self._engine_ok = bool(ok)
        self.new_btn.setToolTip("选一个装着整部剧的文件夹")
        self.analyze_btn.setToolTip("逐集转写台词、切场景、读画面字幕，并给场景打冲突分")
        self.export_btn.setToolTip("先出几条角度不同的方案，再渲染成 9:16 竖屏成片")

    # ── 项目 ──────────────────────────────────────────────────

    def _refresh_projects(self):
        br = self._bridge_or_warn()
        if br is None:
            return

        def job(w):
            return br.call("project.list", {}, timeout=30)

        w = Worker(job)
        connect_guarded(w.done, self._fill_projects, label="刷新剧集列表")
        connect_guarded(w.error, self._rpc_error, label="刷新剧集失败")
        w.start()

    def _fill_projects(self, rows):
        self._projects = list(rows or [])
        t = self.proj_table
        t.setRowCount(0)
        for row in self._projects:
            n = t.rowCount()
            t.insertRow(n)
            t.setItem(n, 0, QTableWidgetItem(str(row.get("name") or "")))
            t.setItem(n, 1, QTableWidgetItem(str(row.get("source_path") or "")))
            t.setItem(n, 2, QTableWidgetItem(str(row.get("episode_count") or 0)))
            t.setItem(n, 3, QTableWidgetItem(str(row.get("status") or "")))
        if not self._projects:
            self.status.setText("还没有剧集项目。点「新增剧集」，"
                            "选一个装着整部剧的文件夹（文件夹名就是剧名）。")
        else:
            self.status.setText(f"共 {len(self._projects)} 部剧")
        # 默认选第一行，集列表跟着亮
        if self._projects:
            t.selectRow(0)
            self._on_pick_project()

    def _on_pick_project(self):
        row = self.proj_table.currentRow()
        if row < 0 or row >= len(self._projects):
            return
        self._pid = self._projects[row].get("id")
        self._load_episodes()

    def _new_project(self):
        if not require_feature(self, "drama", "短剧成片"):
            return
        d = QFileDialog.getExistingDirectory(
            self, "选择装着整部剧的文件夹（文件夹名就是剧名）")
        if not d:
            return
        src = Path(d)

        def job(w):
            br = self._bridge
            # source_path 在库里是 UNIQUE，同一目录不能建两个项目
            for old in br.call("project.list", {}, timeout=30) or []:
                if old.get("source_path") == str(src):
                    return {"dupe": True, "name": old.get("name")}
            proj = br.call("project.create",
                           {"name": src.name, "source_path": str(src)},
                           timeout=60)
            w.emit_progress(50.0, "正在扫描集…")
            br.call("project.scan_episodes",
                    {"project_id": proj.get("id")}, timeout=600)
            return {"dupe": False, "project": proj}

        w = Worker(job)
        connect_guarded(w.done, self._on_new_done, label="新增剧集完成")
        connect_guarded(w.error, self._rpc_error, label="新增剧集失败")
        self.status.setText("正在扫描剧集…")
        w.start()

    def _on_new_done(self, ret):
        if ret.get("dupe"):
            self.status.setText(f"这个文件夹已经建过剧集了（剧名：{ret.get('name')}）")
            append_log(self.log, "重复建项已跳过")
        else:
            self.status.setText("剧集已建好，集也扫完了")
            append_log(self.log, "新增剧集完成")
        self._refresh_projects()

    def _delete_project(self):
        br = self._bridge_or_warn()
        if br is None:
            return
        row = self.proj_table.currentRow()
        if row < 0 or row >= len(self._projects):
            return
        proj = self._projects[row]
        if QMessageBox.question(
                self, "删除剧集",
                f"删除《{proj.get('name')}》？\n"
                "这台机器上扫出来的转写与场景结果会一起删掉，素材文件不受影响。",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        def job(w):
            br.call("project.delete", {"project_id": proj.get("id")},
                    timeout=60)
            return True

        w = Worker(job)
        connect_guarded(w.done, lambda _r: self._refresh_projects(),
                        label="删除剧集完成")
        connect_guarded(w.error, self._rpc_error, label="删除剧集失败")
        w.start()

    # ── 集 ────────────────────────────────────────────────────

    def _load_episodes(self):
        br = self._bridge_or_warn()
        if br is None or not self._pid:
            return

        def job(w):
            return br.call("project.get", {"project_id": self._pid}, timeout=60)

        w = Worker(job)
        connect_guarded(w.done, self._fill_episodes, label="载入集列表")
        connect_guarded(w.error, self._rpc_error, label="载入集失败")
        w.start()

    def _fill_episodes(self, ret):
        eps = (ret or {}).get("episodes") or []
        self._episodes = eps
        t = self.ep_table
        t.setRowCount(0)
        for e in eps:
            n = t.rowCount()
            t.insertRow(n)
            t.setItem(n, 0, QTableWidgetItem(str(e.get("episode_number") or "")))
            t.setItem(n, 1, QTableWidgetItem(str(e.get("name") or "")))
            dur = e.get("duration") or 0
            t.setItem(n, 2, QTableWidgetItem(f"{dur:.1f}s" if dur else ""))
            t.setItem(n, 3, QTableWidgetItem(str(e.get("status") or "")))

    def _scan(self):
        br = self._bridge_or_warn()
        if br is None or not self._pid:
            return

        def job(w):
            return br.call("project.scan_episodes",
                           {"project_id": self._pid}, timeout=900)

        w = Worker(job)
        connect_guarded(w.done, lambda _r: self._load_episodes(),
                        label="扫描完成")
        connect_guarded(w.error, self._rpc_error, label="扫描失败")
        self.status.setText("正在扫描集…")
        w.start()

    # ── 分析 ──────────────────────────────────────────────────

    def _analyze(self):
        if not require_feature(self, "drama", "短剧成片"):
            return
        br = self._bridge_or_warn()
        if br is None or not self._pid:
            return

        pid = self._pid

        def job(w):
            # ★ 扫描完服务会自动起分析。客户再点一次会撞 -32205「已有进行中的任务」，
            #   把那条报成失败，客户看到的就是「分析失败」，可活儿其实正在跑。
            #   所以先看有没有现成的：有就跟着它，没有才提交。
            job_id = drama_bridge.find_active_job(br, "analysis")
            if job_id:
                w.emit_progress(1.0, "扫描完已自动开始分析，正在接着跑…")
            else:
                job_id = br.submit_job("analysis.start", {"project_id": pid},
                                       timeout=60)
                w.emit_progress(1.0, "分析已开始")
            deadline = time.time() + 7200
            last = -1
            while time.time() < deadline:
                st = br.job_state(job_id, timeout=30)
                state = st.get("status")
                pct = st.get("percent") or 0
                if isinstance(pct, (int, float)) and int(pct) != last:
                    last = int(pct)
                    w.emit_progress(float(pct), str(st.get("message") or ""))
                if drama_bridge.is_terminal(state):
                    return {"state": state, "job": st}
                time.sleep(2.0)
            return {"state": "timeout", "job": {}}

        w = Worker(job)
        connect_guarded(w.progress, self._on_job_progress, label="分析进度")
        connect_guarded(w.done, self._on_analyze_done, label="分析完成")
        connect_guarded(w.error, self._rpc_error, label="分析失败")
        self.status.setText("正在分析…先转写，再找场景和冲突点，几十集会比较久")
        w.start()

    def _on_analyze_done(self, ret):
        state = (ret or {}).get("state")
        # 成功态是 completed（服务端 infra/jobs.py 的字面量），不是 done
        if state == "completed":
            self.status.setText("分析完成，可以生成成片了")
            append_log(self.log, "分析完成")
            self._load_episodes()
            self._load_works()
        elif state == "timeout":
            self.status.setText("分析超时了（超过 2 小时）。可以分几集重跑一次")
            append_log(self.log, "分析超时")
        else:
            job = (ret or {}).get("job") or {}
            self.status.setText(f"分析没跑完：{job.get('message') or state}")
            append_log(self.log, f"分析失败：{job.get('message') or state}")
        self._load_episodes()

    # ── 成片 ──────────────────────────────────────────────────

    def _load_works(self):
        br = self._bridge_or_warn()
        if br is None or not self._pid:
            return

        def job(w):
            return br.call("export.list_works", {"project_id": self._pid},
                           timeout=60)

        w = Worker(job)
        connect_guarded(w.done, self._fill_works, label="载入成片列表")
        connect_guarded(w.error, self._rpc_error, label="载入成片失败")
        w.start()

    def _fill_works(self, rows):
        t = self.work_table
        t.setRowCount(0)
        for wk in (rows or []):
            n = t.rowCount()
            t.insertRow(n)
            t.setItem(n, 0, QTableWidgetItem(str(wk.get("title") or wk.get("id") or "")))
            t.setItem(n, 1, QTableWidgetItem(str(wk.get("mode") or "")))
            t.setItem(n, 2, QTableWidgetItem(str(wk.get("status") or "")))

    def _export(self):
        if not require_feature(self, "drama", "短剧成片"):
            return
        br = self._bridge_or_warn()
        if br is None or not self._pid:
            return
        # 导出要先用 LLM 出方案；没配 LLM 的剧这里会拿到明确原因，不静默
        pid = self._pid

        def job(w):
            w.emit_progress(2.0, "正在出方案…")
            try:
                plan_job = br.submit_job("narration.plan_variants",
                                         {"project_id": pid}, timeout=120)
                deadline = time.time() + 1800
                while time.time() < deadline:
                    st = br.job_state(plan_job, timeout=30)
                    if drama_bridge.is_terminal(st.get("status")):
                        break
                    time.sleep(2.0)
            except DramaBridgeError as exc:
                return {"ok": False, "reason": str(exc)}
            w.emit_progress(30.0, "方案好了，开始渲染成片…")
            try:
                job_id = br.submit_job("export.submit", {"project_id": pid},
                                       timeout=120)
            except DramaBridgeError as exc:
                return {"ok": False, "reason": str(exc)}
            deadline = time.time() + 7200
            last = -1
            while time.time() < deadline:
                st = br.job_state(job_id, timeout=30)
                state = st.get("status")
                pct = st.get("percent") or 0
                if isinstance(pct, (int, float)) and int(pct) != last:
                    last = int(pct)
                    w.emit_progress(30.0 + float(pct) * 0.7,
                                    str(st.get("message") or ""))
                if drama_bridge.is_terminal(state):
                    return {"ok": state == "completed", "state": state, "job": st}
                time.sleep(2.0)
            return {"ok": False, "state": "timeout", "job": {}}

        w = Worker(job)
        connect_guarded(w.progress, self._on_job_progress, label="导出进度")
        connect_guarded(w.done, self._on_export_done, label="导出完成")
        connect_guarded(w.error, self._rpc_error, label="导出失败")
        self.status.setText("正在出成片…")
        w.start()

    def _on_export_done(self, ret):
        ret = ret or {}
        if ret.get("ok"):
            self.status.setText("成片已生成，点「打开成片目录」看成品")
            append_log(self.log, "成片导出完成")
            self._load_works()
        else:
            why = ret.get("reason") or (ret.get("job") or {}).get("message") \
                or ret.get("state")
            self.status.setText(f"成片没出来：{why}")
            append_log(self.log, f"导出失败：{why}")

    # ── 通用回调 ──────────────────────────────────────────────

    def _on_job_progress(self, pct, msg):
        self.status.setText(f"{msg or '处理中'}（{int(pct)}%）")

    def _on_progress(self, payload):
        pct = payload.get("percent")
        msg = str(payload.get("message") or "")
        if pct is not None:
            self.status.setText(f"{msg}（{int(pct)}%）" if msg else f"{int(pct)}%")

    def _on_logline(self, payload):
        lv = payload.get("level")
        msg = str(payload.get("message") or "")
        if lv in ("warn", "error"):
            append_log(self.log, f"[{lv}] {msg}")

    def _rpc_error(self, msg):
        self.status.setText(f"操作失败：{msg}")
        append_log(self.log, f"失败：{msg}")

    def _open_outputs(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        d = drama_bundle.data_dir() / "outputs"
        if not d.is_dir():
            d = drama_bundle.data_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
