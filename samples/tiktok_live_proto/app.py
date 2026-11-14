"""TikTok 直播监控原型 · 图形界面（独立运行，不接主程序）。

  python samples/tiktok_live_proto/app.py

界面只做一件事：粘贴主播号/直播间链接 → 开始监控 → 开播自动录、下播自动停。
代理地址可改（自动探测后填进输入框，绝不写死），没有海外出口时给出明确提示。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # samples/

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, QUrl  # noqa: E402
from PySide6.QtGui import QDesktopServices, QFont, QIntValidator  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from tiktok_live_proto.config import (  # noqa: E402
    ProtoConfig, default_ffmpeg, detect_proxy,
)
from tiktok_live_proto.core import normalize_handle  # noqa: E402
from tiktok_live_proto.monitor import ProtoMonitor  # noqa: E402

# ---------------------------------------------------------------- 样式
LIGHT = {
    "bg": "#F5F7FA", "card": "#FFFFFF", "text": "#1B2330", "muted": "#6B7686",
    "line": "#E4E9F0", "input": "#F8FAFC", "accent": "#16A34A",
    "accent_soft": "#E8F7EE", "danger": "#DC2626", "shadow": "#DDE4EC",
    "disabled_bg": "#E9EDF3", "disabled_text": "#9AA6B5",
}
DARK = {
    "bg": "#0F141B", "card": "#1A222D", "text": "#E8EDF4", "muted": "#94A3B8",
    "line": "#2A3543", "input": "#141C26", "accent": "#4ADE80",
    "accent_soft": "#1C3527", "danger": "#F87171", "shadow": "#0A0F15",
    "disabled_bg": "#263140", "disabled_text": "#728096",
}


def build_qss(c: dict) -> str:
    return f"""
    QWidget {{ background: {c['bg']}; color: {c['text']};
               font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif;
               font-size: 13px; }}
    QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
    #Card {{ background: {c['card']}; border: 1px solid {c['line']};
            border-radius: 12px; }}
    #Title {{ font-size: 20px; font-weight: 700; }}
    #Sub {{ color: {c['muted']}; font-size: 12px; }}
    #Section {{ font-size: 13px; font-weight: 700; }}
    #Hint {{ color: {c['muted']}; font-size: 11px; }}
    #Big {{ font-size: 22px; font-weight: 700; }}
    #Dot {{ border-radius: 7px; background: {c['muted']}; }}
    QLineEdit, QComboBox, QPlainTextEdit, QListWidget {{
        background: {c['input']}; color: {c['text']};
        border: 1px solid {c['line']}; border-radius: 8px;
        padding: 7px 9px; selection-background-color: {c['accent_soft']};
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
    QComboBox::drop-down {{ border: 0; width: 18px; }}
    QComboBox::down-arrow {{ image: none; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {c['muted']};
        margin-right: 8px; }}
    QPushButton {{ background: {c['input']}; color: {c['text']};
        border: 1px solid {c['line']}; border-radius: 8px; padding: 8px 14px; }}
    QPushButton:hover {{ border-color: {c['accent']}; }}
    QPushButton:disabled {{ color: {c['disabled_text']}; }}
    #Primary {{ background: {c['accent']}; color: #FFFFFF; border: 0;
        font-weight: 700; padding: 10px 16px; }}
    #Danger {{ background: {c['danger']}; color: #FFFFFF; border: 0;
        font-weight: 700; padding: 10px 16px; }}
    #Primary:disabled, #Danger:disabled {{
        background: {c['disabled_bg']}; color: {c['disabled_text']}; border: 0; }}
    QPlainTextEdit, QListWidget {{ font-family: Consolas, 'Microsoft YaHei UI', monospace;
        font-size: 12px; }}
    """


LOG_COLOR = {
    "info": ("#6B7686", "#B2BFD0"), "warn": ("#D97706", "#FBBF24"),
    "error": ("#DC2626", "#F87171"), "record": ("#16A34A", "#4ADE80"),
    "done": ("#2563EB", "#60A5FA"),
}


def _int_value(edit: QLineEdit, default: int, low: int, high: int) -> int:
    """把数值输入框安全地读成整数，越界自动夹住。"""
    try:
        value = int((edit.text() or "").strip())
    except ValueError:
        return default
    return max(low, min(high, value))


# ---------------------------------------------------------------- 后台线程
class Bridge(QObject):
    log = Signal(str, str, str)      # kind, text, data_json


class MonitorWorker(QThread):
    """监控循环跑在线程里；所有回调只发信号，绝不直接碰界面控件。"""

    def __init__(self, cfg: ProtoConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.bridge = Bridge()
        self.mon: ProtoMonitor | None = None

    def run(self):                                       # noqa: D102
        try:
            mon = ProtoMonitor(self.cfg,
                               emit=lambda k, t, d: self.bridge.log.emit(
                                   k, t, json.dumps(d, ensure_ascii=False)))
            self.mon = mon
            mon.run_forever()
        except Exception as exc:                         # noqa: BLE001
            self.bridge.log.emit("error", f"监控线程异常：{type(exc).__name__}: {exc}", "{}")

    def stop(self) -> None:
        if self.mon:
            self.mon.request_stop()
        self.wait(4000)
        if self.isRunning():
            self.terminate()


# ---------------------------------------------------------------- 主窗口
class ProtoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = ProtoConfig.load()
        self.worker: MonitorWorker | None = None
        self.dark = False
        self.setWindowTitle("TikTok 直播监控 · 独立原型")
        self.resize(1040, 720)
        self._build_ui()
        self._apply_theme()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_state)
        self.timer.start(800)
        self.file_timer = QTimer(self)
        self.file_timer.timeout.connect(self._refresh_files)
        self.file_timer.start(3000)
        self._refresh_files()

    # -- 构建 ---------------------------------------------------------
    def _card(self) -> QFrame:
        f = QFrame()
        f.setObjectName("Card")
        return f

    def _section(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("Section")
        return lb

    def _hint(self, text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("Hint")
        lb.setWordWrap(True)
        return lb

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        # 顶部
        head = QHBoxLayout()
        titles = QVBoxLayout()
        t = QLabel("TikTok 直播监控")
        t.setObjectName("Title")
        sub = QLabel("独立验证原型 · 不接主程序 · 不参与发版　|　粘贴 @handle → 开播自动录 → 下播自动停")
        sub.setObjectName("Sub")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addLayout(titles)
        head.addStretch(1)
        self.theme_btn = QPushButton("切换深色")
        self.theme_btn.setFixedWidth(110)
        self.theme_btn.clicked.connect(self._toggle_theme)
        head.addWidget(self.theme_btn)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_left(), 0)
        body.addWidget(self._build_right(), 1)
        root.addLayout(body, 1)

        foot = QLabel("提示：本原型需要可用的海外网络出口；代理地址自动探测后仍可手动修改。"
                      "TikTok 的浏览器校验必须由真实浏览器会话完成，普通 HTTP 请求拿不到房间号。")
        foot.setObjectName("Hint")
        foot.setWordWrap(True)
        root.addWidget(foot)

    def _build_left(self) -> QWidget:
        card = self._card()
        card.setFixedWidth(340)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(self._section("监控设置"))

        v.addWidget(QLabel("主播号 / 直播间链接"))
        self.handle_edit = QLineEdit(self.cfg.handle)
        self.handle_edit.setPlaceholderText("@ofdwex9 或粘贴完整 live 链接")
        v.addWidget(self.handle_edit)

        v.addWidget(QLabel("海外代理（HTTP）"))
        proxy_row = QHBoxLayout()
        self.proxy_edit = QLineEdit(self.cfg.proxy)
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7897")
        proxy_row.addWidget(self.proxy_edit, 1)
        detect = QPushButton("探测")
        detect.setFixedWidth(58)
        detect.clicked.connect(self._detect_proxy)
        proxy_row.addWidget(detect)
        v.addLayout(proxy_row)
        v.addWidget(self._hint("留空表示直连；国内直连 TikTok 会被重置。"))

        grid = QGridLayout()
        grid.setSpacing(8)
        grid.addWidget(QLabel("轮询间隔（秒）"), 0, 0)
        self.interval_edit = QLineEdit(str(int(self.cfg.poll_interval)))
        self.interval_edit.setValidator(QIntValidator(5, 600))
        grid.addWidget(self.interval_edit, 0, 1)
        grid.addWidget(QLabel("单段时长（秒）"), 1, 0)
        self.duration_edit = QLineEdit(str(int(self.cfg.record_duration)))
        self.duration_edit.setValidator(QIntValidator(0, 7200))
        grid.addWidget(self.duration_edit, 1, 1)
        v.addLayout(grid)
        v.addWidget(self._hint("单段时长 0 = 一直录到主播下播；>0 用于快速验证。"))

        v.addWidget(QLabel("保存目录"))
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.cfg.save_dir)
        self.dir_edit.setToolTip(self.cfg.save_dir)     # 长路径看不全时悬停可见
        self.dir_edit.setCursorPosition(0)
        dir_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("浏览")
        browse.setFixedWidth(58)
        browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse)
        v.addLayout(dir_row)

        v.addWidget(QLabel("ffmpeg 路径"))
        self.ffmpeg_edit = QLineEdit(self.cfg.ffmpeg or default_ffmpeg())
        self.ffmpeg_edit.setReadOnly(True)
        self.ffmpeg_edit.setToolTip(self.ffmpeg_edit.text())
        self.ffmpeg_edit.setCursorPosition(0)           # 显示盘符开头，而不是被裁的尾部
        v.addWidget(self.ffmpeg_edit)

        v.addStretch(1)
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("▶ 开始监控")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn = QPushButton("■ 停止")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.start_btn, 1)
        btn_row.addWidget(self.stop_btn, 1)
        v.addLayout(btn_row)
        v.addWidget(self._hint("配置文件保存在原型目录下的 config.json，录制文件不会进版本库。"))
        return card

    def _build_right(self) -> QWidget:
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        # 状态卡
        card = self._card()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 16, 18, 16)
        cv.setSpacing(10)
        top = QHBoxLayout()
        self.dot = QLabel()
        self.dot.setObjectName("Dot")
        self.dot.setFixedSize(14, 14)
        top.addWidget(self.dot)
        self.phase_label = QLabel("未开始")
        self.phase_label.setObjectName("Big")
        top.addWidget(self.phase_label)
        top.addStretch(1)
        self.seg_label = QLabel("已录 0 段")
        self.seg_label.setObjectName("Hint")
        top.addWidget(self.seg_label)
        cv.addLayout(top)

        info = QGridLayout()
        info.setSpacing(8)
        info.setColumnStretch(1, 1)
        self.lb_nick = QLabel("—")
        self.lb_room = QLabel("—")
        self.lb_title = QLabel("—")
        self.lb_check = QLabel("—")
        self.lb_file = QLabel("—")
        for row, (key, widget) in enumerate((
                ("主播", self.lb_nick), ("房间号", self.lb_room),
                ("直播标题", self.lb_title), ("最近检查", self.lb_check),
                ("当前文件", self.lb_file))):
            k = QLabel(key)
            k.setObjectName("Hint")
            info.addWidget(k, row, 0)
            widget.setWordWrap(True)
            info.addWidget(widget, row, 1)
        cv.addLayout(info)
        v.addWidget(card)

        # 日志
        log_head = QHBoxLayout()
        log_head.addWidget(self._section("运行日志"))
        log_head.addStretch(1)
        clear = QPushButton("清空")
        clear.setFixedWidth(64)
        clear.clicked.connect(lambda *_: self.log_view.clear())
        log_head.addWidget(clear)
        v.addLayout(log_head)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(200)
        v.addWidget(self.log_view, 1)

        # 成片
        file_head = QHBoxLayout()
        file_head.addWidget(self._section("录制文件"))
        file_head.addStretch(1)
        open_dir = QPushButton("打开目录")
        open_dir.setFixedWidth(90)
        open_dir.clicked.connect(self._open_dir)
        file_head.addWidget(open_dir)
        v.addLayout(file_head)
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(120)
        v.addWidget(self.file_list)
        return wrap

    # -- 交互 ---------------------------------------------------------
    def _toggle_theme(self) -> None:
        try:
            self.dark = not self.dark
            self._apply_theme()
        except Exception as exc:                         # noqa: BLE001
            self._append_log("error", f"切换主题失败：{exc}")

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_qss(DARK if self.dark else LIGHT))
        self.theme_btn.setText("切换浅色" if self.dark else "切换深色")
        self._refresh_state()

    def _detect_proxy(self) -> None:
        try:
            found = detect_proxy()
            self.proxy_edit.setText(found)
            self._append_log("info", f"代理探测：{found or '没找到本机代理，可手动填写'}")
        except Exception as exc:                         # noqa: BLE001
            self._append_log("error", f"代理探测失败：{exc}")

    def _browse_dir(self) -> None:
        try:
            d = QFileDialog.getExistingDirectory(self, "选择录制保存目录", self.dir_edit.text())
            if d:
                self.dir_edit.setText(d)
        except Exception as exc:                         # noqa: BLE001
            self._append_log("error", f"选择目录失败：{exc}")

    def _open_dir(self) -> None:
        try:
            p = Path(self.dir_edit.text() or self.cfg.save_dir)
            p.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        except Exception as exc:                         # noqa: BLE001
            self._append_log("error", f"打开目录失败：{exc}")

    def _collect_cfg(self) -> ProtoConfig | None:
        handle = normalize_handle(self.handle_edit.text())
        if not handle:
            self._append_log("error", "请填主播号，或粘贴完整的 TikTok 直播间链接")
            return None
        cfg = ProtoConfig.load(
            handle=handle, proxy=self.proxy_edit.text().strip(),
            save_dir=self.dir_edit.text().strip(),
            ffmpeg=self.ffmpeg_edit.text().strip() or default_ffmpeg(),
            poll_interval=_int_value(self.interval_edit, 20, 5, 600),
            record_duration=_int_value(self.duration_edit, 0, 0, 7200),
        )
        if not cfg.ffmpeg or not Path(cfg.ffmpeg).is_file():
            self._append_log("error", "没找到 ffmpeg.exe，请检查工程 assets/ffmpeg 目录")
            return None
        return cfg

    def _on_start(self) -> None:
        try:
            if self.worker and self.worker.isRunning():
                self._append_log("warn", "监控已经在运行")
                return
            cfg = self._collect_cfg()
            if not cfg:
                return
            cfg.save()
            self.cfg = cfg
            self.worker = MonitorWorker(cfg, self)
            self.worker.bridge.log.connect(self._on_log)
            self.worker.finished.connect(self._on_finished)
            self.worker.start()
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self._append_log("info", f"启动监控 @{cfg.handle}｜代理 {cfg.proxy or '(未配置)'}")
        except Exception as exc:                         # noqa: BLE001
            self._append_log("error", f"启动失败：{type(exc).__name__}: {exc}")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def _on_stop(self) -> None:
        try:
            if self.worker:
                self.worker.stop()
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        except Exception as exc:                         # noqa: BLE001
            self._append_log("error", f"停止失败：{exc}")

    def _on_finished(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_log(self, kind: str, text: str, _data: str) -> None:
        self._append_log(kind, text)

    def _append_log(self, kind: str, text: str) -> None:
        try:
            color = LOG_COLOR.get(kind, LOG_COLOR["info"])[1 if self.dark else 0]
            import time                                  # noqa: PLC0415
            stamp = time.strftime("%H:%M:%S")
            self.log_view.appendHtml(
                f'<span style="color:{color}">[{stamp}] {text}</span>')
            self.log_view.verticalScrollBar().setValue(
                self.log_view.verticalScrollBar().maximum())
        except Exception:                               # noqa: BLE001
            pass

    # -- 定时刷新 -----------------------------------------------------
    def _refresh_state(self) -> None:
        try:
            st = self.worker.mon.state if self.worker and self.worker.mon else None
            phase = st.phase if st else "未开始"
            self.phase_label.setText(phase)
            color = (LIGHT if not self.dark else DARK)["muted"]
            if phase == "录制中":
                color = (LIGHT if not self.dark else DARK)["danger"]
            elif phase == "等待开播":
                color = (LIGHT if not self.dark else DARK)["accent"]
            elif phase in ("出错", "状态未知"):
                color = "#D97706"
            self.dot.setStyleSheet(f"border-radius: 7px; background: {color};")
            if st:
                self.lb_nick.setText(f"@{st.handle}" + (f" · {st.nickname}" if st.nickname else ""))
                self.lb_room.setText(st.room_id or "—")
                self.lb_title.setText(st.title or "—")
                self.lb_check.setText(f"{st.last_status} · {st.last_check or '—'}")
                self.lb_file.setText(Path(st.recording_file).name if st.recording_file else "—")
                self.seg_label.setText(f"已录 {st.segments} 段")
        except Exception:                               # noqa: BLE001
            pass

    def _refresh_files(self) -> None:
        try:
            d = Path(self.dir_edit.text() if hasattr(self, "dir_edit") else self.cfg.save_dir)
            if not d.is_dir():
                return
            files = sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
            if self.file_list.count() == len(files) and (not files or
                    self.file_list.item(0).text().startswith(files[0].name)):
                return
            self.file_list.clear()
            for p in files:
                mb = p.stat().st_size / 1024 / 1024
                self.file_list.addItem(f"{p.name}　·　{mb:.2f} MB")
        except Exception:                               # noqa: BLE001
            pass

    def closeEvent(self, event):                        # noqa: N802,D102
        try:
            if self.worker and self.worker.isRunning():
                self.worker.stop()
        except Exception:                               # noqa: BLE001
            pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    win = ProtoWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
