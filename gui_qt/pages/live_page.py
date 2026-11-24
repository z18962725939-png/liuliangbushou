"""直播监控页（PySide6）。

功能：昵称/链接添加监控、博主搜索、轮询间隔/自动录制、录制保存目录、录制选中/停止、
ffmpeg 状态与「指定 ffmpeg」、目标表格、实时录制指示灯。业务逻辑复用
live_monitor.LiveMonitor（经 AppContext.ensure_monitor 注入）。

无真实 client 时自动降级为演示模式（展示示例数据）；ffmpeg 缺失时给出明确引导，
而不是静默失败。
"""
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QMetaObject, Q_ARG, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import (StatusLine, Worker, _feature_ok, confirm_destructive, connect_guarded,
                    danger_button, guide_banner, page_title, vertical_card)
# ★ 2026-10-31 重建补回：这行 import 在源码重建时丢失，页面一打开就
#   NameError（进度条全项目统一用 FlowProgressBar，见 widgets/progress.py）。
from gui_qt.widgets.progress import FlowProgressBar
# ★ 2026-11-03：屏幕区域录制 —— 拖拽选矩形录屏（与直播拉流录制互补）。
from gui_qt.widgets.region_picker import RegionPicker
# TikTok 直播适配层（代理探测 / handle 判别）；打包 spec 已登记该模块
from justoneapi_collector import tiktok_live


def _short_fail_reason(msg: str) -> str:
    """把录制失败原因压成一句能塞进状态栏的结论。

    ★ 状态栏是单行、不换行、超宽直接被窗口边缘裁掉（实测 60 字就切）。
      ffmpeg 的失败原因往往是好几十字的多行文本，只能在这里压短，
      完整内容走 tooltip 与「显示详细信息」弹窗。
    """
    t = (msg or "").strip()
    if not t:
        return "录制未启动"
    low = t.lower()
    if "403" in t or "401" in t or "forbidden" in low:
        return "CDN 拒绝（403），看详情"
    if "404" in t or "not found" in low:
        return "地址已失效（404），看详情"
    if "timed out" in low or "timeout" in low or "超时" in t:
        return "连不上 CDN（超时），看详情"
    if "没有写出任何数据" in t:
        return "无数据写出，看详情"
    if "立即退出" in t:
        return "ffmpeg 启动即退出，看详情"
    first = (t.splitlines() or [""])[0]
    return (first[:28] + "…") if len(first) > 28 else (first or "录制未启动")


class LivePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._blink = True
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._refresh_ffmpeg()

    def _build_ui(self):
        self._region_keys = []          # 进行中的区域录制 key 列表（停止用）
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(12)
        root.addWidget(page_title("直播监控"))

        # 页面使用引导条（替代原先藏在卡片底部的一行小字提示）
        root.addWidget(guide_banner("直播监控", self))

        # 顶部输入
        top, top_l = vertical_card(
            "粘贴抖音 / TikTok 直播间链接 → 点「添加监控」；正在直播时会自动开始录制", self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("昵称/链接："))
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "抖音直播间链接 / sec_uid；TikTok 直播间链接或 @主播号（如 @dannkansai）")
        connect_guarded(self.input.returnPressed, self._add,
                        on_error=self._slot_failed, label="添加监控")
        r1.addWidget(self.input, 1)
        b_add = QPushButton("添加监控"); b_add.setObjectName("Primary")
        b_add.setToolTip("添加后会立刻检查一次是否正在直播，在播则自动开始录制")
        # ★ 2026-11-05：主动作也走 connect_guarded —— 打包版没有控制台，
        #   槽里一个 TypeError 就会变成客户嘴里的「按钮点了没反应」。
        #   QPushButton.clicked 会带一个 checked 布尔，用 lambda 吃掉它。
        connect_guarded(b_add.clicked, lambda *_: self._add(),
                        on_error=self._slot_failed, label="添加监控")
        b_search = QPushButton("搜索博主"); b_search.clicked.connect(self._search)
        r1.addWidget(b_add); r1.addWidget(b_search)
        # ★ v1.26.2：备注（记赛道/用途）。监控目标一多，光看昵称根本想不起来
        #   这个号是干嘛的 —— 添加时顺手记一笔，之后双击表格也能改。
        r1.addWidget(QLabel("备注："))
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("选填，如「美妆-护肤」")
        self.note_input.setToolTip("写清楚这个对标号的赛道/用途，监控多了不会认错")
        self.note_input.setFixedWidth(170)
        connect_guarded(self.note_input.returnPressed, self._add,
                        on_error=self._slot_failed, label="添加监控")
        r1.addWidget(self.note_input)
        top_l.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel("搜索结果："))
        self.blogger_combo = QComboBox(); self.blogger_combo.setMinimumWidth(260)
        r2.addWidget(self.blogger_combo, 1)
        b_sel = QPushButton("选中并添加"); b_sel.clicked.connect(self._add_from_combo)
        r2.addWidget(b_sel)
        top_l.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel("轮询间隔(秒)："))
        self.interval = QLineEdit("60"); self.interval.setFixedWidth(60)
        r3.addWidget(self.interval)
        self.auto_rec = QCheckBox("开播自动录制")
        self.auto_rec.setChecked(bool(self.ctx.get_setting("lm_auto_record", True)))
        r3.addWidget(self.auto_rec)
        b_start = QPushButton("开始监控"); b_start.setObjectName("Primary")
        b_start.clicked.connect(self._start_polling)
        b_stop = QPushButton("停止监控"); b_stop.clicked.connect(self._stop_polling)
        r3.addWidget(b_start); r3.addWidget(b_stop)
        b_now = QPushButton("立即检查一次"); b_now.clicked.connect(self._check_now)
        r3.addWidget(b_now)
        r3.addStretch()
        top_l.addLayout(r3)

        # ★ TikTok 直播（2026-11-15）：客户机都装了代理，但端口各家不同。
        #   自动探测后填进输入框、允许手改并落盘；页面/状态/CDN 全走这个出口。
        r4 = QHBoxLayout()
        r4.addWidget(QLabel("TikTok 代理："))
        self.tiktok_proxy_edit = QLineEdit()
        saved = self.ctx.get_setting("tiktok_live_proxy")
        if saved is None:                       # 从未配置过 → 取候选（毫秒级，不联网）
            saved = tiktok_live.quick_proxy()
        self.tiktok_proxy_edit.setText(str(saved or ""))
        self.tiktok_proxy_edit.setPlaceholderText(
            "如 http://127.0.0.1:7897 或 127.0.0.1:10808；"
            "留空=自动（系统代理 / 直连，开 TUN 的可留空）")
        r4.addWidget(self.tiktok_proxy_edit, 1)
        self.detect_proxy_btn = QPushButton("自动探测")
        self.detect_proxy_btn.setToolTip(
            "逐个试连 TikTok，只填验证通过的出口（不会自动保存）")
        connect_guarded(self.detect_proxy_btn.clicked,
                        lambda *_: self._detect_tiktok_proxy(),
                        on_error=self._slot_failed, label="探测 TikTok 代理")
        self.save_proxy_btn = QPushButton("保存代理")
        self.save_proxy_btn.setToolTip("保存后立即对监控引擎生效，重启也不会丢")
        connect_guarded(self.save_proxy_btn.clicked, lambda *_: self._save_tiktok_proxy(),
                        on_error=self._slot_failed, label="保存 TikTok 代理")
        r4.addWidget(self.detect_proxy_btn); r4.addWidget(self.save_proxy_btn)
        top_l.addLayout(r4)
        root.addWidget(top)

        # 保存目录 + 录制控制
        mid, mid_l = vertical_card("录制", self)
        sdir_row = QHBoxLayout()
        sdir_row.addWidget(QLabel("录制保存目录："))
        self.save_dir = QLineEdit(str(self._default_dir()))
        sdir_row.addWidget(self.save_dir, 1)
        b_browse = QPushButton("浏览..."); b_browse.clicked.connect(self._choose_dir)
        sdir_row.addWidget(b_browse)
        mid_l.addLayout(sdir_row)

        rec_row = QHBoxLayout()
        b_rec = QPushButton("● 录制选中主播"); b_rec.clicked.connect(self._record_selected)
        b_stop_rec = QPushButton("■ 停止选中录制"); b_stop_rec.clicked.connect(self._stop_selected)
        b_stop_all = QPushButton("停止全部录制"); b_stop_all.clicked.connect(self._stop_all)
        b_rm = QPushButton("移除选中"); b_rm.clicked.connect(self._remove_selected)
        # 清空整张监控表：破坏性动作，样式弱化 + 二次确认
        self.clear_btn = danger_button("清空列表")
        self.clear_btn.setToolTip("清空监控目标列表（不会停止正在进行的录制）")
        self.clear_btn.clicked.connect(self._clear_targets)
        self.ffmpeg_label = QLabel("ffmpeg: 检测中...")
        self.ffmpeg_label.setStyleSheet("color:#6B7280;font-size:12px;")
        b_ff = QPushButton("指定 ffmpeg...")
        b_ff.setToolTip("选择本机 ffmpeg.exe（录制直播必需）")
        b_ff.clicked.connect(self._choose_ffmpeg)
        rec_row.addWidget(b_rec); rec_row.addWidget(b_stop_rec)
        rec_row.addWidget(b_stop_all); rec_row.addWidget(b_rm)
        rec_row.addWidget(self.clear_btn)
        rec_row.addStretch()
        mid_l.addLayout(rec_row)

        # ★ ffmpeg 状态单独一行（2026-10-17）：原来 7 个控件挤在同一行，
        #   总宽 950px 刚好顶到视口边，再加上「指定 ffmpeg...」就超 72px，
        #   整页出现横向滚动条。拆开后两行都宽松，也更容易读。
        ff_row = QHBoxLayout()
        ff_row.addWidget(self.ffmpeg_label)
        ff_row.addWidget(b_ff)
        ff_row.addStretch()
        mid_l.addLayout(ff_row)

        # ★ 2026-11-03：屏幕区域录制（拖拽选矩形录屏，与直播拉流录制互补）
        region_row = QHBoxLayout()
        region_row.addWidget(QLabel("屏幕区域："))
        b_region = QPushButton("区域录制")
        b_region.setToolTip("在屏幕上拖拽选一个矩形区域，只录那一块（直播窗口/任意窗口/游戏都行）")
        b_region.clicked.connect(self._start_region)
        b_stop_region = QPushButton("停止区域录制")
        b_stop_region.clicked.connect(self._stop_region)
        region_row.addWidget(b_region)
        region_row.addWidget(b_stop_region)
        region_row.addStretch()
        mid_l.addLayout(region_row)

        root.addWidget(mid)

        # ★ v1.14.4：状态灯 / 进度条 / 状态文案跟在录制卡下面。
        #   原来排在「监控目标」表格之后，目标一多表格撑高，进度条就掉到视口外。
        self.rec_light = QLabel("")
        self.rec_light.setStyleSheet("color:#d32f2f;font-weight:bold;font-size:13px;")
        root.addWidget(self.rec_light)
        self.progress = FlowProgressBar(); self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = StatusLine(
            "① 粘贴直播间链接 →「添加监控」（会自动检查一次，在播即录）；"
            "② 点「开始监控」可持续轮询，开播自动录、下播自动停")
        self.status.mark("idle")
        root.addWidget(self.status)

        # ★ 2026-11-18 录制历史：录完的文件不再「撒手不管」。
        #   录制链路只管录，停手后文件躺在保存目录里，软件重启就认不出来了。
        #   这里扫目录列成表，双击/按钮用系统默认播放器打开，补上「回放」这半条路。
        #   ★ 放在「监控目标」上方：回放是客户点进这页的高频目的（我录的东西
        #     哪去了），监控目标属配置区、低频。放下面会被空态撑到首屏外。
        hist, hist_l = vertical_card("录制历史（双击用系统播放器打开）", self)
        self.hist_table = QTableWidget(0, 3)
        self.hist_table.setHorizontalHeaderLabels(["文件名", "大小", "录制时间"])
        self.hist_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.hist_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # ★ 不能设 maximumHeight：空态引导会给空表设 200px 的 minimumHeight，
        #   一旦 maximum < minimum，Qt 布局行为未定义，会露出半截空态覆盖层。
        # 双击 = 打开回放；连接走 connect_guarded，槽里抛异常也能看见原因
        connect_guarded(self.hist_table.doubleClicked,
                        lambda *_: self._open_history_selected(),
                        on_error=self._slot_failed, label="打开回放")
        hist_l.addWidget(self.hist_table)
        hist_row = QHBoxLayout()
        b_open = QPushButton("打开选中回放")
        b_open.setToolTip("用电脑默认播放器打开选中的录制文件")
        connect_guarded(b_open.clicked, lambda *_: self._open_history_selected(),
                        on_error=self._slot_failed, label="打开回放")
        b_folder = QPushButton("打开保存目录")
        b_folder.setToolTip("在文件管理器里打开录制保存目录")
        connect_guarded(b_folder.clicked, lambda *_: self._open_save_dir(),
                        on_error=self._slot_failed, label="打开保存目录")
        b_refresh = QPushButton("刷新列表")
        connect_guarded(b_refresh.clicked, lambda *_: self._refresh_history(),
                        on_error=self._slot_failed, label="刷新录制历史")
        hist_row.addWidget(b_open); hist_row.addWidget(b_folder)
        hist_row.addWidget(b_refresh); hist_row.addStretch()
        hist_l.addLayout(hist_row)
        root.addWidget(hist)

        # 目标表格
        tb, tb_l = vertical_card("监控目标", self)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["博主昵称", "来源", "直播状态", "房间ID", "录制状态", "录制时长", "备注"])
        # 备注列直接在表格里改，改完立刻写回引擎并落盘
        self.table.itemChanged.connect(self._on_item_changed)
        tb_l.addWidget(self.table, 1)
        root.addWidget(tb, 1)

        # ★ 2026-11-05 客户现场「点添加监控无反应」根因：这里曾重复创建了
        #   第二套 rec_light / progress / status，并**覆盖**了 self.status。
        #   离屏实测（1280×760）：第一套在 y=569 可见，第二套在 y=781
        #   已经掉出窗口，而本页没有 QScrollArea —— 于是所有状态文字都
        #   写进了一条客户永远看不见的标签，表格还被挤到只剩 62px。
        #   状态/进度条在上面（录制卡正下方）已加过一次，这里绝不能再加。
        # ----------------------------------------------------------------

        # ★ v1.14.2：演示行只在「真的用不了」时才铺。
        #   免费档虽然没 Token，但「粘贴直播间链接 → 自动录」走的是抖音
        #   webcast 接口（零成本），本来就该能用 —— 以前按 ctx.demo 判断，
        #   免费用户一进来就是三条假目标，还以为软件能跑。
        # ★ v1.26.2：先把上次保存的目标（含备注）铺回表格，再做演示兜底。
        self._restore_rows()
        if self.ctx.demo and self._ensure_daemon() is None:
            self._load_demo_targets()
        # 首次进入就把已经录好的文件列出来（录制历史不随软件重启丢失）
        self._refresh_history()

    # ----------------------------------------------------------------
    def _default_dir(self):
        """录制保存目录：优先用设置里保存的，否则用桌面默认目录。"""
        saved = str(self.ctx.get_setting("lm_save_dir") or "").strip()
        if saved:
            try:
                Path(saved).mkdir(parents=True, exist_ok=True)
                return Path(saved)
            except OSError:
                pass
        root = Path.home() / "Desktop" / "流量捕手直播"
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return str(root)

    # ------------------------------------------- 录制历史 / 回放（2026-11-18）
    def _history_dir(self) -> Path:
        """历史表扫描的目录：以界面上当前填的保存目录为准（用户可能刚改过）。"""
        raw = str(self.save_dir.text() if hasattr(self, "save_dir") else "").strip()
        return Path(raw) if raw else Path(self._default_dir())

    def _refresh_history(self):
        """扫保存目录，把可回放的 mp4 列进历史表。只读，不动任何文件。"""
        from justoneapi_collector.record_history import scan_recordings
        items = scan_recordings(self._history_dir())
        self.hist_table.setRowCount(0)
        for rec in items:
            r = self.hist_table.rowCount()
            self.hist_table.insertRow(r)
            for c, val in enumerate((rec.name, rec.size_text, rec.time_text)):
                cell = QTableWidgetItem(val)
                # 绝对路径挂在首行 UserRole：双击时据此打开，显示仍是短文件名
                if c == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, rec.path)
                    cell.setToolTip(rec.path)
                self.hist_table.setItem(r, c, cell)
        if items:
            self.status.setText(f"录制历史：{len(items)} 个可回放文件（双击打开）")
            self.status.mark("ok")

    def _open_history_selected(self):
        """用系统默认播放器打开选中的录制；没选中就提示，绝不静默。"""
        rows = sorted({i.row() for i in self.hist_table.selectedIndexes()})
        if not rows:
            self.status.setText("请先在「录制历史」里选中一个文件")
            self.status.mark("warn")
            return
        opened = 0
        for r in rows:
            cell = self.hist_table.item(r, 0)
            path = cell.data(Qt.ItemDataRole.UserRole) if cell else ""
            if not path or not Path(path).is_file():
                continue
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            opened += 1
        if opened:
            self.status.setText(f"已用系统播放器打开 {opened} 个录制文件")
            self.status.mark("ok")
        else:
            self.status.setText("选中的文件已不存在（可能被移到别处或删除）")
            self.status.mark("warn")

    def _open_save_dir(self):
        """在文件管理器里打开保存目录；目录不存在就先建，失败给明话。"""
        d = self._history_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:                              # noqa: BLE001
            self.status.setText(f"保存目录打不开：{e}")
            self.status.mark("error")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        self.status.setText(f"已打开保存目录：{d}")

    def _daemon(self):
        return self.ctx.daemon

    def _ensure_daemon(self):
        """返回可用的监控引擎；缺失时尝试按需拉起（client 就绪才有）。"""
        dm = self.ctx.daemon
        if dm is None:
            try:
                dm = self.ctx.ensure_monitor()
            except Exception:  # noqa: BLE001
                dm = None
        return dm

    # ------------------------------------------- 后台任务的异常兜底（2026-11-05）
    # 工程判据：PySide6 槽里抛异常只把 traceback 打到 stderr，打包成 exe 后
    # 没有控制台 ⇒ 客户看到的就是「按钮点了没反应、状态栏永远停在正在…」。
    # 本页 9 处 Worker 回调 + 主按钮统一走 connect_guarded，异常至少能变成
    # 一条看得见的状态文字。
    def _slot_failed(self, msg: str):
        try:
            self.status.setText(f"{msg}　·　已记录到运行日志，可截图发给服务方")
            self.status.mark("error")
        except Exception:                       # noqa: BLE001
            pass

    def _worker_failed(self, prefix: str, msg: str):
        try:
            self.status.setText(f"{prefix}：{msg}")
            self.status.mark("error")
        except Exception:                       # noqa: BLE001
            pass

    def _wire_worker(self, w, done_slot, prefix: str, label: str):
        """把 Worker 的 done / error 统一接进带兜底的槽。"""
        connect_guarded(w.done, done_slot, on_error=self._slot_failed, label=label)
        connect_guarded(w.error, lambda e: self._worker_failed(prefix, e),
                        on_error=self._slot_failed, label=label)

    # ---------------------------------------------------------- ffmpeg 状态
    def _refresh_ffmpeg(self):
        """刷新 ffmpeg 状态标签：就绪（绿）/ 未找到（红，引导指定）/ 演示模式（灰）。"""
        dm = self._ensure_daemon()
        if dm is None:
            self.ffmpeg_label.setText("ffmpeg: 演示模式（未接入采集服务）")
            self.ffmpeg_label.setStyleSheet("color:#6B7280;font-size:12px;")
            self.ffmpeg_label.setToolTip("请在「设置」页填写 API Token，接入采集服务后即可录制")
            return
        try:
            p = dm.ffmpeg_path()
        except Exception:  # noqa: BLE001
            p = ""
        if p:
            self.ffmpeg_label.setText("ffmpeg: 已就绪")
            self.ffmpeg_label.setStyleSheet("color:#2E7D32;font-size:12px;")
            self.ffmpeg_label.setToolTip(p)
        else:
            self.ffmpeg_label.setText("ffmpeg: 未找到（点右侧「指定 ffmpeg...」）")
            self.ffmpeg_label.setStyleSheet("color:#C62828;font-size:12px;")
            self.ffmpeg_label.setToolTip("录制直播需要 ffmpeg，请点「指定 ffmpeg...」选择本机 ffmpeg.exe")

    def _choose_ffmpeg(self):
        """手动指定 ffmpeg.exe：持久化到配置并立即对当前监控引擎生效。"""
        dm = self._ensure_daemon()
        if dm is None:
            QMessageBox.warning(
                self, "尚未接入采集服务",
                "请先到「设置 → API 配置」填写 API Token 并保存，\n"
                "接入采集服务后再指定 ffmpeg。",
            )
            self.status.setText("演示模式：请先在「设置」填写 API Token")
            return
        init = str(self.ctx.get_setting("ffmpeg_path") or "")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg 可执行文件（ffmpeg.exe）",
            str(Path(init).parent) if init else "",
            "ffmpeg 可执行文件 (ffmpeg.exe);;所有文件 (*.*)",
        )
        if not path:
            return
        resolved = None
        try:
            resolved = dm.set_ffmpeg_path(path)
        except Exception:  # noqa: BLE001
            resolved = None
        self.ctx.set_setting("ffmpeg_path", path)
        self.ctx.save_settings()
        self._refresh_ffmpeg()
        if resolved:
            self.status.setText(f"ffmpeg 已就绪：{resolved}")
            QMessageBox.information(
                self, "ffmpeg 已就绪",
                f"已指定 ffmpeg：\n{path}\n\n现在可对选中主播一键录制。",
            )
        else:
            self.status.setText(f"已保存路径，但未能识别 ffmpeg：{path}")
            QMessageBox.warning(
                self, "未识别 ffmpeg",
                f"已保存路径，但该文件无法执行：\n{path}\n\n请确认所选文件是有效的 ffmpeg.exe。",
            )

    # ---------------------------------------------------------- 演示数据
    def _load_demo_targets(self):
        for nick, src, st, rid, rec, dur in [
            ("示例主播A", "昵称", "直播中", "123456", "录制中", "00:00:32"),
            ("示例主播B", "链接", "未开播", "-", "-", "-"),
        ]:
            self._add_row(nick, src, st, rid, rec, dur, sec_uid=f"demo_{rid}")

    def _add_row(self, nick, src, st, rid, rec, dur, sec_uid="", note="", tip=""):
        """插入一行；sec_uid 挂在昵称单元格的 UserRole 上（表格操作一律以它为准）。

        note 是用户备注（赛道/用途），显示在第 7 列，可双击直接改。
        tip 给「状态未知」用：悬浮显示具体失败原因（代理不通 / 风控 / 页面改版）。
        """
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((nick, src, st, rid, rec, dur, note)):
            item = QTableWidgetItem(v)
            if c == 2:
                item.setForeground(self._color_for_status(st))
                if tip:
                    item.setToolTip(tip)
            if c == 0:
                item.setData(Qt.UserRole, sec_uid or rid)
            if c == 6:
                item.setToolTip("双击可改：写清楚赛道/用途，比如「美妆-护肤」")
            self.table.setItem(r, c, item)
        self._sync_actions()

    # ------------------------------------------------- 备注列的编辑与回写
    def _on_item_changed(self, item):
        """表格里改了备注 → 写回引擎并落盘（其它列不处理）。

        ★ 只认第 6 列（备注）。录制状态/时长每秒会被 _sync_recording_rows 重写，
          如果在这里无差别回写，等于每秒存一次文件。
        """
        if item is None or item.column() != 6:
            return
        if getattr(self, "_loading_rows", False):
            return                                  # 恢复列表时不要触发写盘
        it = self.table.item(item.row(), 0)
        uid = (it.data(Qt.UserRole) if it is not None else "") or ""
        if not uid or str(uid).startswith("demo_"):
            return
        dm = self._daemon()
        if dm is None:
            return
        try:
            dm.set_note(str(uid), item.text().strip())
        except Exception:                            # noqa: BLE001
            pass

    def _restore_rows(self):
        """把上次保存的监控目标（含备注）铺回表格 —— 重启软件列表还在。

        以前目标只存在内存里：关掉软件再打开，加了十几个对标号全没了，
        备注更无从谈起。现在名单跟备注一起存在 live_targets.json。
        """
        dm = self._ensure_daemon()
        if dm is None:
            return
        try:
            targets = dm.get_targets()
        except Exception:                            # noqa: BLE001
            return
        if not targets:
            return
        self._loading_rows = True
        try:
            for t in targets:
                self._add_row(t.nickname or t.sec_uid, t.input_type or "链接",
                              "未开播", t.room_id or "-", "-", "-",
                              sec_uid=t.sec_uid, note=getattr(t, "note", "") or "")
        finally:
            self._loading_rows = False
        self.status.setText(
            f"已恢复上次监控的 {len(targets)} 个目标（备注一并保留）")

    def _sync_actions(self):
        """按列表状态同步控件可用性：空列表时禁用「清空列表」。"""
        self.clear_btn.setEnabled(self.table.rowCount() > 0)

    @staticmethod
    def _color_for_status(st):
        from PySide6.QtGui import QColor
        if st == "直播中":
            return QColor("#c62828")
        if st == "录制中":
            return QColor("#1565c0")
        if st == "状态未知":
            return QColor("#b26a00")   # 橙：不是没开播，是这次没查到
        return QColor("#666666")

    # --------------------------------------------------- 添加目标
    def _detect_tiktok_proxy(self):
        """探测并**逐个试连验证**，只填验证通过的出口，不落盘。

        ★ 只扫端口远远不够：本机上开着端口却连不上 TikTok 的出口（开发工具
          注入的临时代理、别的软件占用）会选出一整片「状态未知」。所以这里
          必须真发请求，验证不通过的绝不填。
        """
        self.status.setText("正在探测代理：逐个试连 TikTok，约几秒…")
        self.detect_proxy_btn.setEnabled(False)

        def run(_worker):
            cands = tiktok_live.proxy_candidates()
            proxy, mode = tiktok_live.detect_egress()
            return {"good": proxy, "mode": mode, "count": len(cands)}

        w = Worker(run)
        self._wire_worker(w, self._on_detect_proxy_done, "代理探测失败", "代理探测")
        w.start()

    def _on_detect_proxy_done(self, p):
        p = p if isinstance(p, dict) else {}
        good = str(p.get("good") or "")
        mode = str(p.get("mode") or "none")
        self.detect_proxy_btn.setEnabled(True)
        if good:
            self.tiktok_proxy_edit.setText(good)
            self.status.setText(
                f"已找到可用代理：{good}（试连 TikTok 成功）；点「保存代理」生效")
            return
        if mode == "direct":
            # ★ TUN / 全局模式：进程不带代理也能出网（ffmpeg 也吃路由表），
            #   留空就是最优解，别硬塞一个端口给它。
            self.tiktok_proxy_edit.setText("")
            self.status.setText(
                "检测到本机可直连 TikTok（TUN / 全局模式）：代理留空即可，"
                "点「保存代理」确认")
            return
        self.tiktok_proxy_edit.setText("")
        self.status.setText(
            "代理和直连都连不上 TikTok。请确认代理软件已开启「系统代理」或「TUN」，"
            "再点「自动探测」；手填一般是 127.0.0.1:7897（Verge）"
            "或 127.0.0.1:10808（mixed 端口）")

    def _save_tiktok_proxy(self):
        """落盘 + 立即让监控引擎换出口（会重建 CDP 会话），并异步验证可用性。"""
        raw = self.tiktok_proxy_edit.text().strip()
        val = tiktok_live.normalize_proxy(raw) if raw else ""
        self.ctx.set_setting("tiktok_live_proxy", val)
        self.ctx.save_settings()
        dm = self._daemon()
        if dm is not None:
            dm.set_tiktok_proxy(val)
        if not val:
            self.status.setText(
                "已留空：按「系统代理 / 直连」自动尝试。开了 TUN 模式的客户可以这样用")
            return
        self.tiktok_proxy_edit.setText(val)
        self.save_proxy_btn.setEnabled(False)
        self.status.setText(f"代理已保存：{val}，正在试连 TikTok…")

        def run(_worker):
            return tiktok_live.proxy_works(val, timeout=6.0)

        w = Worker(run)
        self._wire_worker(w, lambda ok: self._on_proxy_checked(val, ok),
                          "代理校验失败", "代理校验")
        w.start()

    def _on_proxy_checked(self, val: str, ok):
        self.save_proxy_btn.setEnabled(True)
        if ok:
            self.status.setText(
                f"代理已保存且验证可用：{val}；之前「状态未知」的目标点「立即检查一次」即可")
        else:
            self.status.setText(
                f"代理已保存，但试连 TikTok 失败：{val}。请核对端口"
                "（Clash Verge 默认 7897）并确认代理软件正在运行")

    def _add(self):
        text = self.input.text().strip()
        if not text:
            self.status.setText("请先粘贴直播间链接，或输入 sec_uid")
            return
        dm = self._ensure_daemon()
        if dm is None:
            self._add_row(text, "手动", "未开播", "-", "-", "-", sec_uid=text,
                          note=self.note_input.text().strip())
            self.status.setText("演示模式：已加入示例目标（在「设置」填写 API Token 后可真实监控）")
            return
        self.status.setText("正在添加…")
        note = self.note_input.text().strip()

        def run(worker):
            # 返回值统一用 dict，别再用位置元组 —— 之前按位置解包时把
            # add_target_by_link 的「昵称」当成了 sec_uid 存进表格，
            # 导致后续录制/停止/移除全都找不到目标。
            try:
                if (text.startswith("http") or "douyin.com" in text
                        or tiktok_live.is_tiktok_input(text)):
                    ok, nick_or_err, sec_uid = dm.add_target_by_link(text, note=note)
                    if not ok:
                        return {"ok": False, "error": nick_or_err, "src": "链接",
                                "nick": "", "sec_uid": ""}
                    return {"ok": True, "error": "", "src": "链接",
                            "nick": nick_or_err or sec_uid, "sec_uid": sec_uid,
                            "note": note}
                dm.add_target(text, nickname=text, note=note)
                return {"ok": True, "error": "", "src": "sec_uid",
                        "nick": text, "sec_uid": text, "note": note}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": str(e), "src": "链接",
                        "nick": "", "sec_uid": ""}

        w = Worker(run)
        self._wire_worker(w, self._on_add, "添加失败", "添加监控结果")
        w.start()

    def _on_add(self, r):
        if not r.get("ok"):
            self.status.setText(f"添加失败：{r.get('error') or '未知原因'}")
            return
        nick = r.get("nick") or r.get("sec_uid") or ""
        sec_uid = r.get("sec_uid") or ""
        self._add_row(nick, r.get("src") or "链接", "检查中…", sec_uid, "-", "-",
                      sec_uid=sec_uid, note=r.get("note") or "")
        # 备注已经跟着目标存下来了，输入框清空方便加下一个
        self.note_input.clear()
        self.status.setText(
            f"已添加：{nick}" + (f"（备注：{r.get('note')}）" if r.get("note") else ""))
        # 用户要求的体验：添加后立刻检查一次是否在播，在播就直接开录
        self._probe_after_add(sec_uid, nick)

    # ------------------------------------------- 添加后首次检查（在播即录）
    def _row_for(self, sec_uid: str) -> int:
        """按 sec_uid 定位表格行（sec_uid 存在昵称列的 UserRole 上）；找不到返回 -1。"""
        if not sec_uid:
            return -1
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None and str(it.data(Qt.UserRole) or "") == str(sec_uid):
                return r
        return -1

    def _set_cell(self, row: int, col: int, text: str, tip: str = ""):
        it = self.table.item(row, col)
        if it is not None:
            it.setText(text)
            it.setToolTip(tip)

    def _probe_after_add(self, sec_uid: str, display_name: str = ""):
        """添加监控后立即检查一次；若正在直播且已开「开播自动录制」，直接开录。

        把原来的「添加 → 等轮询 → 手动点录制」三步压缩成一步：用户粘链接、
        点添加，软件自己判断并在播就录。
        """
        dm = self._ensure_daemon()
        if dm is None or not sec_uid or str(sec_uid) == "-":
            return
        # Qt 控件只在主线程读，先取值再传给后台线程
        auto_on = bool(self.auto_rec.isChecked())
        name = display_name or sec_uid
        # TikTok 首次要真浏览器过 WAF 挑战，耗时明显长于抖音，先说清楚
        tip = ("（TikTok 首次需过浏览器校验，约 10~20 秒）"
               if str(sec_uid).startswith("tiktok:") else "")
        self.status.setText(f"已添加「{name}」，正在检查是否直播中…{tip}")

        def run(worker):
            try:
                st = dm.check_once(sec_uid)
            except Exception as e:  # noqa: BLE001
                return {"kind": "error", "msg": str(e), "st": None}
            if st is None:
                return {"kind": "unknown", "msg": "接口未返回状态", "st": None}
            # ★ 状态检查失败 ≠ 未开播：解析异常/风控时后端会写这个前缀，
            #   此时保留上次状态、提示重试，别让客户以为人真的没播。
            err = (getattr(st, "stream_error", "") or "")
            if err.startswith("状态检查失败"):
                return {"kind": "unknown", "msg": err, "st": st}
            if not getattr(st, "is_live", False):
                return {"kind": "offline", "msg": "当前未开播", "st": st}
            if not auto_on:
                return {"kind": "live_no_auto",
                        "msg": "未勾选「开播自动录制」，未开始录制", "st": st}
            try:
                has_ffmpeg = bool(dm.ffmpeg_path())
            except Exception:  # noqa: BLE001
                has_ffmpeg = False
            if not has_ffmpeg:
                return {"kind": "live_no_ffmpeg",
                        "msg": "未检测到 ffmpeg，无法录制", "st": st}
            try:
                ok, msg = dm.auto_record(sec_uid)
            except Exception as e:  # noqa: BLE001
                return {"kind": "rec_fail", "msg": f"启动录制异常：{e}", "st": st}
            return {"kind": "rec_ok" if ok else "rec_fail", "msg": msg, "st": st}

        w = Worker(run)
        self._wire_worker(w, self._on_probe_done, "首次检查失败", "首次检查结果")
        w.start()

    def _on_probe_done(self, p):
        """首次检查回来：刷新该行状态，并按结果给出可操作的提示。"""
        st = p.get("st")
        kind = p.get("kind") or ""
        msg = p.get("msg") or ""
        if st is not None:
            sec_uid = getattr(st, "sec_uid", "") or ""
            row = self._row_for(sec_uid)
            if row >= 0:
                real_nick = getattr(st, "nickname", "") or ""
                # 回填真实昵称（链接方式添加时初始名是「直播间123456」这种兜底名）
                if real_nick:
                    it = self.table.item(row, 0)
                    if it is not None:
                        it.setText(real_nick)
                live = bool(getattr(st, "is_live", False))
                unknown = (getattr(st, "stream_error", "") or "").startswith("状态检查失败")
                # 这次没查到就别展示陈旧状态，明确标「状态未知」让客户重试
                label = "状态未知" if unknown else ("直播中" if live else "未开播")
                self._set_cell(row, 2, label,
                               (getattr(st, "stream_error", "") or "") if unknown else "")
                it2 = self.table.item(row, 2)
                if it2 is not None:
                    it2.setForeground(self._color_for_status(label))
                self._set_cell(row, 3, getattr(st, "room_id", "") or "-")
                if kind == "rec_ok":
                    self._set_cell(row, 4, "录制中")
                    self._set_cell(row, 5, "00:00:00")
                    self.table.selectRow(row)

        if kind == "rec_ok":
            self.status.setText(
                "检测到正在直播，已自动开始录制（点「开始监控」可让下播时自动停止）")
        elif kind == "live_no_auto":
            self.status.setText(
                f"检测到正在直播：{msg}；勾选「开播自动录制」或选中该行点「● 录制选中主播」")
        elif kind == "live_no_ffmpeg":
            self.status.setText(
                f"检测到正在直播，但{msg}；点「指定 ffmpeg...」选好 ffmpeg 后即可录制")
        elif kind == "rec_fail":
            # 录制失败的完整原因常有多行（ffmpeg 报错 + 出口实测诊断），
            # 状态栏放不下 —— 只放结论短句，全文进悬浮提示和可复制的详情窗。
            text = (msg or "").strip()
            self.status.setText(
                f"检测到正在直播，但录制未启动：{_short_fail_reason(text)}")
            try:
                self.status.setToolTip(text)
            except Exception:                   # noqa: BLE001
                pass
            if len(text.splitlines()) > 1:
                self._fail_box = self._open_fail_dialog(
                    "录制没有启动起来",
                    "检测到正在直播，但录制没能启动。",
                    text)
        elif kind == "offline":
            self.status.setText("已添加，当前未开播；点「开始监控」后开播会自动录制")
        elif kind == "unknown":
            self.status.setText(f"已添加，但{msg}；可点「立即检查一次」重试")
        else:
            self.status.setText(f"已添加，但检查失败：{msg}")

    def _search(self):
        if self.ctx.demo:
            # 免费档：昵称搜索走的是付费采集接口，得说清"这一项要激活"，
            # 同时把免费能用的替代路径摆出来（粘贴直播间链接即可添加）
            if not _feature_ok("collect"):
                self.status.setText(
                    "免费版不含昵称搜索（要调采集接口）：把直播间链接粘到左边，"
                    "点「添加监控」就能用，开播自动录")
            else:
                self.status.setText("演示模式：搜索需先接入采集服务（设置 → API Token）")
            return
        name = self.input.text().strip()
        if not name:
            self.status.setText("请先输入要搜索的昵称")
            return
        self.status.setText(f"搜索中：{name} …")

        def run(worker):
            try:
                return self.ctx.client.request("douyin", "user-search-v1", {"keyword": name})
            except Exception as e:  # noqa: BLE001
                return e

        w = Worker(run)
        self._wire_worker(w, self._on_search, "搜索失败", "搜索博主结果")
        w.start()

    def _on_search(self, resp):
        self.blogger_combo.clear()
        if isinstance(resp, Exception):
            self.status.setText(f"搜索失败：{resp}")
            return
        data = getattr(resp, "data", None) or {}
        if not isinstance(data, dict):
            data = {}
        items = data.get("user_list") or data.get("items") or data.get("list") or []
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            nick = it.get("nickname") or it.get("nick_name") or it.get("name") or "未知"
            uid = it.get("sec_uid") or it.get("uid") or ""
            if uid:
                self.blogger_combo.addItem(f"{nick} ({uid[:12]}…)", uid)
        self.status.setText(f"搜索到 {self.blogger_combo.count()} 个结果")

    def _add_from_combo(self):
        uid = self.blogger_combo.currentData()
        nick = self.blogger_combo.currentText().split(" (")[0]
        if not uid:
            self.status.setText("请先搜索并选择一个博主")
            return
        dm = self._ensure_daemon()
        if dm is None:
            self._add_row(nick, "搜索", "未开播", uid, "-", "-", sec_uid=uid)
            self.status.setText("演示模式：已加入表格（未接入采集服务）")
            return
        note = self.note_input.text().strip()
        try:
            dm.add_target(uid, nickname=nick, note=note)
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"添加失败：{e}")
            return
        self._add_row(nick, "搜索", "检查中…", uid, "-", "-",
                      sec_uid=uid, note=note)
        self.note_input.clear()
        self.status.setText(
            f"已添加目标：{nick}" + (f"（备注：{note}）" if note else ""))
        # 与「粘贴链接添加」保持一致：加完立刻查一次，在播就录
        self._probe_after_add(uid, nick)

    # --------------------------------------------------- 轮询 / 检查
    def _start_polling(self):
        dm = self._ensure_daemon()
        if dm is None:
            self.status.setText("演示模式：请先在「设置」填写 API Token 接入采集服务")
            return
        try:
            interval = max(5, int(self.interval.text() or 60))
        except ValueError:
            interval = 60
        dm.auto_record_enabled = self.auto_rec.isChecked()
        sdir = self.save_dir.text().strip()
        try:
            if sdir:
                dm.save_dir = Path(sdir)
        except Exception:  # noqa: BLE001
            pass
        # 持久化用户选择（下次启动沿用）
        self.ctx.set_setting("lm_auto_record", self.auto_rec.isChecked())
        if sdir:
            self.ctx.set_setting("lm_save_dir", sdir)
        self.ctx.save_settings()
        dm.start_polling(
            interval=interval,
            on_status_change=self._poll_status_change,
            on_tick=self._poll_tick,
            on_auto_record=self._poll_auto_record,
        )
        if not dm.ffmpeg_path():
            self.status.setText(f"监控中（每 {interval} 秒）—— 注意：未找到 ffmpeg，开播自动录制不会生效")
        else:
            self.status.setText(f"监控中（每 {interval} 秒）")

    def _stop_polling(self):
        dm = self._daemon()
        if dm is not None:
            try:
                dm.stop_polling()
            except Exception:  # noqa: BLE001
                pass
        self.status.setText("已停止监控")

    # ------------------------------------------- 轮询回调（后台线程 → 主线程）
    # ★ live_monitor 的轮询 / 自动录制跑在后台线程，不能直接碰 Qt 控件。
    #   下面三个 _poll_* 是「投递器」：只把数据 marshalling 回主线程，
    #   真正的界面刷新由 @Slot 槽在主线程做，绝不跨线程读写控件。
    def _poll_status_change(self, st):
        QMetaObject.invokeMethod(
            self, "_on_poll_status_change", Qt.QueuedConnection,
            Q_ARG(str, str(getattr(st, "sec_uid", "") or "")))

    def _poll_tick(self, checked, total):
        QMetaObject.invokeMethod(
            self, "_on_poll_tick", Qt.QueuedConnection,
            Q_ARG(int, int(checked or 0)), Q_ARG(int, int(total or 0)))

    def _poll_auto_record(self, uid, status, msg):
        QMetaObject.invokeMethod(
            self, "_on_auto_record", Qt.QueuedConnection,
            Q_ARG(str, str(uid or "")), Q_ARG(str, str(status or "")),
            Q_ARG(str, str(msg or "")))

    @Slot(str)
    def _on_poll_status_change(self, uid: str):
        """轮询期间开播/下播翻转：刷新状态列与房间号（主线程）。"""
        dm = self._daemon()
        if dm is None:
            return
        row = self._row_for(uid)
        if row < 0:
            return
        try:
            st = next((t for t in dm.get_targets()
                       if str(getattr(t, "sec_uid", "") or "") == uid), None)
        except Exception:  # noqa: BLE001
            st = None
        if st is None:
            return
        unknown = (getattr(st, "stream_error", "") or "").startswith("状态检查失败")
        label = ("状态未知" if unknown
                 else ("直播中" if getattr(st, "is_live", False) else "未开播"))
        self._set_cell(row, 2, label,
                       (getattr(st, "stream_error", "") or "") if unknown else "")
        it = self.table.item(row, 2)
        if it is not None:
            it.setForeground(self._color_for_status(label))
        self._set_cell(row, 3, getattr(st, "room_id", "") or "-")

    @Slot(int, int)
    def _on_poll_tick(self, checked: int, total: int):
        """每轮扫完同步录制列；状态栏故意不写，免得盖掉刚上屏的失败结论。"""
        dm = self._daemon()
        if dm is not None:
            self._sync_recording_rows(dm)

    @Slot(str, str, str)
    def _on_auto_record(self, uid: str, status: str, msg: str):
        """轮询自动录制结果上屏（主线程）：fail 显示原因 / start 刷新录制列 / stop 复位。

        ★ 这是「检测到开播却静默失败」的最后一环：live_monitor 里精心构造的
          on_auto_record 回调过去没有消费者。状态栏是单行，只放短结论；
          完整原因进 tooltip 和可复制的详情窗（与手动录制失败同一套呈现）。
        """
        row = self._row_for(uid)
        if status == "fail":
            text = (msg or "").strip()
            if row >= 0:
                self._set_cell(row, 2, _short_fail_reason(text), text)
            self.status.setText(
                f"检测到正在直播，但录制未启动：{_short_fail_reason(text)}")
            try:
                self.status.setToolTip(text)
            except Exception:  # noqa: BLE001
                pass
            if len(text.splitlines()) > 1:
                self._fail_box = self._open_fail_dialog(
                    "录制没有启动起来",
                    "轮询中检测到开播，但自动录制没能启动。",
                    text)
        elif status == "start":
            self.status.setText("检测到正在直播，已自动开始录制")
            dm = self._daemon()
            if dm is not None:
                self._sync_recording_rows(dm)
            if row >= 0:
                self.table.selectRow(row)
        elif status == "stop":
            if row >= 0:
                self._set_cell(row, 2, "未开播")
                self._set_cell(row, 4, "-")
                self._set_cell(row, 5, "-")
            self.status.setText("博主已下播，录制已停止")

    def _check_now(self):
        dm = self._ensure_daemon()
        if dm is None:
            self.status.setText("演示模式：请先接入采集服务")
            return
        self.status.setText("正在检查所有目标…")

        def run(worker):
            return dm.check_all()

        w = Worker(run)
        self._wire_worker(w, self._on_check_all, "检查失败", "检查结果")
        w.start()

    def _on_check_all(self, statuses):
        self.table.setRowCount(0)
        unknown_n = 0
        for s in statuses:
            unknown = (getattr(s, "stream_error", "") or "").startswith("状态检查失败")
            if unknown:
                unknown_n += 1
            label = "状态未知" if unknown else ("直播中" if s.is_live else "未开播")
            self._add_row(
                s.nickname, s.input_type, label,
                s.room_id or "-",
                "录制中" if s.is_recording else "-",
                self._fmt_elapsed(s),
                sec_uid=s.sec_uid,
                tip=(getattr(s, "stream_error", "") or "") if unknown else "",
            )
        self._sync_actions()
        msg = f"检查完成：{len(statuses)} 个目标"
        if unknown_n:
            msg += (f"，其中 {unknown_n} 个状态未知（不是未开播）。"
                    "把鼠标放到「状态未知」上能看到原因；TikTok 多数是代理连不上，"
                    "到上方「TikTok 代理」点「自动探测」再保存即可")
        self.status.setText(msg)

    # --------------------------------------------------- 录制
    def _selected_sec_uids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        out = []
        for r in rows:
            it = self.table.item(r, 0)          # sec_uid 挂在昵称单元格 UserRole
            uid = (it.data(Qt.UserRole) if it else "") or ""
            if uid and uid != "-":
                out.append(str(uid))
        return out

    def _record_selected(self):
        dm = self._ensure_daemon()
        if dm is None:
            self.status.setText("演示模式：请先在「设置」填写 API Token 接入采集服务")
            return
        try:
            has_ffmpeg = bool(dm.ffmpeg_path())
        except Exception:  # noqa: BLE001
            has_ffmpeg = False
        if not has_ffmpeg:
            self.status.setText("未找到 ffmpeg：录制直播需要 ffmpeg，请点「指定 ffmpeg...」")
            if QMessageBox.question(
                self, "未找到 ffmpeg",
                "录制直播需要 ffmpeg，但当前未检测到。\n\n"
                "是否现在指定本机 ffmpeg.exe 的位置？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            ) == QMessageBox.Yes:
                self._choose_ffmpeg()
            return
        uids = self._selected_sec_uids()
        if not uids:
            self.status.setText("请先在表格选中一个主播")
            return
        self.status.setText(f"正在解析 {len(uids)} 位主播的直播流地址…")

        def run(worker):
            results = []
            for uid in uids:
                try:
                    ok, msg = dm.auto_record(uid)
                except Exception as e:  # noqa: BLE001
                    ok, msg = False, f"异常：{e}"
                results.append((uid, ok, msg))
            return results

        w = Worker(run)
        self._wire_worker(w, self._on_record_done, "录制失败", "录制结果")
        w.start()

    def _on_record_done(self, results):
        """录制结果：状态栏只放一行摘要，完整原因进悬浮提示，全失败再弹可复制的详情。

        ★ TikTok 的失败原因会有好几行（ffmpeg 报错 + 出口实测诊断），
          直接拼进状态栏会被窗口宽度截断，客户只看到半句话、没法照做。
        """
        ok_n = sum(1 for _, ok, _ in results if ok)
        parts, details = [], []
        for uid, ok, msg in results:
            short = uid[:14] + ("…" if len(uid) > 14 else "")
            text = (msg or "").strip()
            if ok:
                parts.append(f"✔ {short}：已开始录制")
                continue
            parts.append(f"✘ {short}：{_short_fail_reason(text)}")
            if text:
                details.append(f"【{short}】\n{text}")
        line = f"录制启动 {ok_n}/{len(results)} —— " + "；".join(parts)
        self.status.setText(line)
        try:
            self.status.setToolTip("\n\n".join(details))
        except Exception:                       # noqa: BLE001
            pass
        if details and ok_n == 0:
            self._fail_box = self._open_fail_dialog(
                "录制没有启动起来",
                f"录制启动 {ok_n}/{len(results)}，全部失败。",
                "\n\n".join(details))

    # --------------------------------------------------- 失败详情窗
    def _open_fail_dialog(self, title: str, summary: str, detail: str):
        """录制失败详情窗：结论 + 可复制的完整原因 +「📋 一键导出日志」。

        ★ 2026-11-15 换成自绘 LogDialog：原来用 QMessageBox 时
          「Show Details...」是 Qt 硬编码英文（客户看不懂），窗里也没有
          「确定」，而且导出按钮在部分布局下会被挤到正文上。现在按钮中文、
          详情可全选复制、出口就在窗里 —— 客户遇到问题的第一现场。
        """
        try:
            from gui_qt.widgets.log_export import show_failure_dialog
            return show_failure_dialog(
                self, title=title, summary=summary,
                body="完整原因点「显示详细信息」查看，可全选复制发给我们；"
                     "也可以直接点「一键导出日志」，把日志和环境信息一起打包。",
                detail=detail, kind="录制失败",
                note="作者微信见「设置 → 系统档 → 反馈与日志」。")
        except Exception:                       # noqa: BLE001
            return None

    # --------------------------------------------------- 屏幕区域录制
    def _start_region(self):
        """弹区域选择浮层 → 拿矩形 → 后台启动屏幕区域录制。"""
        dm = self._ensure_daemon()
        if dm is None:
            self.status.setText("演示模式：请先在「设置」填写 API Token 接入采集服务")
            return
        try:
            has_ffmpeg = bool(dm.ffmpeg_path())
        except Exception:  # noqa: BLE001
            has_ffmpeg = False
        if not has_ffmpeg:
            self.status.setText("未找到 ffmpeg：区域录制需要 ffmpeg，请点「指定 ffmpeg...」")
            if QMessageBox.question(
                self, "未找到 ffmpeg",
                "区域录制需要 ffmpeg，但当前未检测到。\n\n"
                "是否现在指定本机 ffmpeg.exe 的位置？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            ) == QMessageBox.Yes:
                self._choose_ffmpeg()
            return
        rect = RegionPicker.pick(self)
        if rect is None:
            self.status.setText("已取消区域录制")
            return
        name = f"区域录制_{time.strftime('%Y%m%d_%H%M%S')}"
        self.status.setText(
            f"正在启动区域录制（{rect.width()}×{rect.height()} @ {rect.x()},{rect.y()}）…")

        def run(worker):
            return dm.start_region_recording(
                rect.x(), rect.y(), rect.width(), rect.height(), name,
                duration=0, startup_check=True)

        w = Worker(run)
        self._wire_worker(w, self._on_region_start, "区域录制启动失败", "区域录制结果")
        w.start()

    def _on_region_start(self, res):
        ok, msg, key = res if isinstance(res, tuple) else (False, str(res), "")
        if not ok:
            self.status.setText(f"区域录制未启动：{msg}")
            return
        self._region_keys.append(key)
        self.status.setText("区域录制已启动，点「停止区域录制」结束（成片在「录制保存目录」）")

    def _stop_region(self):
        """停止所有进行中的区域录制（优雅写 q → 校验 → .part 改名 .mp4）。"""
        dm = self._daemon()
        if dm is None or not self._region_keys:
            self.status.setText("当前没有进行中的区域录制")
            return
        keys = list(self._region_keys)
        self.status.setText("正在停止区域录制…")

        def run(worker):
            total = 0
            for k in keys:
                try:
                    total += dm.stop_recording(k)
                except Exception:  # noqa: BLE001
                    pass
            return total

        def _done(n):
            self._region_keys.clear()
            self.status.setText(f"已停止区域录制（{n} 个），成片已落盘")

        w = Worker(run)
        self._wire_worker(w, _done, "停止区域录制失败", "停止区域录制结果")
        w.start()

    def _stop_selected(self):
        dm = self._daemon()
        uids = self._selected_sec_uids()
        if dm is None:
            self.status.setText("演示模式：无录制可停止")
            return
        if not uids:
            self.status.setText("请先在表格选中要停止录制的主播")
            return
        self.status.setText("正在停止选中录制…")

        def run(worker):
            try:
                return dm.stop_recording(uids[0]) if len(uids) == 1 else dm.stop_all_recordings()
            except Exception as e:  # noqa: BLE001
                return e

        w = Worker(run)
        self._wire_worker(w, lambda n: self._on_stop_done(n),
                          "停止失败", "停止录制结果")
        w.start()

    def _on_stop_done(self, n):
        """停止录制结果：提示产物落哪 + 刷新历史表（录完那一刻就能找到文件）。"""
        if isinstance(n, Exception):
            self.status.setText(f"停止失败：{n}")
            self.status.mark("error")
            return
        self.status.setText(f"已停止 {n} 个录制")
        # dm.last_stopped 里有本次每个文件的结果（path/size/ok），界面此前从不读。
        dm = self._daemon()
        reports = []
        if dm is not None:
            try:
                reports = list(getattr(dm, "last_stopped", []) or [])
            except Exception:                              # noqa: BLE001
                reports = []
        ok_files = [r for r in reports if r.get("ok") and r.get("path")]
        if ok_files:
            newest = ok_files[-1]
            name = Path(str(newest.get("path"))).name
            self.status.setText(f"录制已保存：{name}　·　共 {len(ok_files)} 个，见下方「录制历史」")
            self.status.mark("ok")
        self._refresh_history()

    def _stop_all(self):
        dm = self._daemon()
        if dm is None:
            self.status.setText("演示模式：无录制可停止")
            return
        self.status.setText("正在停止全部录制…")

        def run(worker):
            try:
                return dm.stop_all_recordings()
            except Exception as e:  # noqa: BLE001
                return e

        w = Worker(run)
        self._wire_worker(w, self._on_stop_done,
                          "停止失败", "停止全部录制结果")
        w.start()

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        dm = self._daemon()
        for r in rows:
            it = self.table.item(r, 0)
            uid = (it.data(Qt.UserRole) if it else "") or ""
            if dm is not None and uid and uid != "-" and not str(uid).startswith("demo_"):
                try:
                    dm.remove_target(str(uid))
                except Exception:  # noqa: BLE001
                    pass
            self.table.removeRow(r)
        self._sync_actions()
        if rows:
            self.status.setText(f"已移除 {len(rows)} 个监控目标")
        else:
            self.status.setText("请先在表格里选中要移除的主播")

    def _clear_targets(self):
        """清空监控列表：二次确认 → 逐个移除监控目标 → 明确反馈。"""
        n = self.table.rowCount()
        if n == 0:
            self.status.setText("监控列表已经是空的")
            return
        dm = self._daemon()
        recording = bool(dm is not None and dm.is_recording())
        detail = "只清空本页的监控列表，不会删除已经录制到本地的视频。"
        if recording:
            detail = ("当前仍有主播在录制中。清空列表不会自动停止录制，"
                      "建议先点「停止全部录制」。")
        if not confirm_destructive(
            self, "清空监控列表",
            f"确定清空当前 {n} 个监控目标吗？",
            ok_text="确认清空", detail=detail,
        ):
            self.status.setText("已取消，监控列表未做改动")
            return
        for r in range(n):
            it = self.table.item(r, 0)
            uid = (it.data(Qt.UserRole) if it else "") or ""
            if dm is not None and uid and uid != "-" and not str(uid).startswith("demo_"):
                try:
                    dm.remove_target(str(uid))
                except Exception:  # noqa: BLE001
                    pass
        self.table.setRowCount(0)
        self._sync_actions()
        self.status.setText(f"已清空 {n} 个监控目标")

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择录制保存目录")
        if d:
            self.save_dir.setText(d)
            self.ctx.set_setting("lm_save_dir", d)
            self.ctx.save_settings()
            dm = self._daemon()
            if dm is not None:
                try:
                    dm.save_dir = Path(d)
                except Exception:  # noqa: BLE001
                    pass
        # 换了目录，历史表要重新扫，否则还列着上一个目录的文件
        self._refresh_history()

    # --------------------------------------------------- 指示灯 / 表格同步
    @staticmethod
    def _fmt_elapsed(st) -> str:
        """把 LiveStatus 的开始时间格式化成 HH:MM:SS（仅录制中有意义）。"""
        if not getattr(st, "is_recording", False):
            return "-"
        started = getattr(st, "record_start_time", 0) or 0
        if not started:
            return "00:00:00"
        secs = max(0, int(time.time() - started))
        return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"

    def _sync_recording_rows(self, dm):
        """把引擎里真实的录制状态 / 时长同步到表格（每秒一次，全程容错）。"""
        try:
            targets = {t.sec_uid: t for t in dm.get_targets()}
        except Exception:  # noqa: BLE001
            return
        if not targets:
            return
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            uid = (it.data(Qt.UserRole) if it else "") or ""
            st = targets.get(str(uid))
            if st is None:
                continue
            rec_cell = self.table.item(r, 4)
            if rec_cell is not None:
                rec_cell.setText("录制中" if getattr(st, "is_recording", False) else "-")
            dur_cell = self.table.item(r, 5)
            if dur_cell is None:
                continue
            secs = 0
            fn = getattr(dm, "recording_elapsed", None)
            if callable(fn):
                try:
                    secs = int(fn(str(uid)) or 0)
                except Exception:  # noqa: BLE001
                    secs = 0
            if not secs:
                started = getattr(st, "record_start_time", 0) or 0
                secs = max(0, int(time.time() - started)) if started else 0
            dur_cell.setText("-" if not getattr(st, "is_recording", False)
                             else f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}")

    def _tick(self):
        self._blink = not self._blink
        dm = self._daemon()
        recording = False
        if dm is not None:
            try:
                recording = dm.is_recording()
            except Exception:  # noqa: BLE001
                recording = False
            self._sync_recording_rows(dm)
        if recording:
            self.rec_light.setText("● 录制中" if self._blink else "○ 录制中")
        else:
            self.rec_light.setText("")
