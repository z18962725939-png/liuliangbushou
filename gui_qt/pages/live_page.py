"""直播监控页（PySide6）。

功能：昵称/链接添加监控、博主搜索、轮询间隔/自动录制、录制保存目录、录制选中/停止、
ffmpeg 状态与「指定 ffmpeg」、目标表格、实时录制指示灯。业务逻辑复用
live_monitor.LiveMonitor（经 AppContext.ensure_monitor 注入）。

无真实 client 时自动降级为演示模式（展示示例数据）；ffmpeg 缺失时给出明确引导，
而不是静默失败。
"""
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, 
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ._base import (StatusLine, Worker, _feature_ok, confirm_destructive, connect_guarded,
                    danger_button, guide_banner, page_title, vertical_card)
# ★ 2026-10-31 重建补回：这行 import 在源码重建时丢失，页面一打开就
#   NameError（进度条全项目统一用 FlowProgressBar，见 widgets/progress.py）。
from gui_qt.widgets.progress import FlowProgressBar
# ★ 2026-11-03：屏幕区域录制 —— 拖拽选矩形录屏（与直播拉流录制互补）。
from gui_qt.widgets.region_picker import RegionPicker


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
            "粘贴直播间链接 → 点「添加监控」；正在直播时会自动开始录制", self)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("昵称/链接："))
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "粘贴直播间链接（如 https://live.douyin.com/123456789），或直接贴 sec_uid")
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

    def _add_row(self, nick, src, st, rid, rec, dur, sec_uid="", note=""):
        """插入一行；sec_uid 挂在昵称单元格的 UserRole 上（表格操作一律以它为准）。

        note 是用户备注（赛道/用途），显示在第 7 列，可双击直接改。
        """
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((nick, src, st, rid, rec, dur, note)):
            item = QTableWidgetItem(v)
            if c == 2:
                item.setForeground(self._color_for_status(st))
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
                if text.startswith("http") or "douyin.com" in text:
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

    def _set_cell(self, row: int, col: int, text: str):
        it = self.table.item(row, col)
        if it is not None:
            it.setText(text)

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
        self.status.setText(f"已添加「{name}」，正在检查是否直播中…")

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
                self._set_cell(row, 2, label)
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
            self.status.setText(f"检测到正在直播，但录制未启动：{msg}")
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
        dm.start_polling(interval=interval)
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
            )
        self._sync_actions()
        msg = f"检查完成：{len(statuses)} 个目标"
        if unknown_n:
            msg += (f"，其中 {unknown_n} 个状态未知（不是未开播，可能是接口风控）；"
                    "可点「立即检查一次」重试")
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
        ok_n = sum(1 for _, ok, _ in results if ok)
        parts = []
        for uid, ok, msg in results:
            short = uid[:14] + ("…" if len(uid) > 14 else "")
            parts.append(f"{'✔' if ok else '✘'} {short}：{'已开始录制' if ok else msg}")
        self.status.setText(f"录制启动 {ok_n}/{len(results)} —— " + "；".join(parts))

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
        self._wire_worker(w, lambda n: self.status.setText(
            f"停止失败：{n}" if isinstance(n, Exception) else f"已停止 {n} 个录制"),
            "停止失败", "停止录制结果")
        w.start()

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
        self._wire_worker(w, lambda n: self.status.setText(
            f"停止失败：{n}" if isinstance(n, Exception) else f"已停止全部录制（{n} 个）"),
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
