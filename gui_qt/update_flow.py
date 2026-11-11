# -*- coding: utf-8 -*-
"""自动更新流程（PySide6）。

═══════════════════════════════════════════════════════════════════════════
为什么要有这个模块
═══════════════════════════════════════════════════════════════════════════
设置页里很早就有一个「启动时自动检查更新」的开关，但**启动时根本没有任何
代码去检查**——开关只是存了个布尔值，从来没被读过。结果就是：作者发布了
新版本，客户机器上永远静悄悄，只有用户自己想到设置页点「立即检查更新」
才会知道。客户以为软件不会更新，作者以为客户都用上了最新版，两边脱节
（2026-10-11 客户反馈后定位）。

这里把完整链路补齐：

  启动 → 后台联网查 version.json → 有新版且未被跳过 → 弹出更新说明
       → 用户点「立即更新」→ 后台下载（多线程 + 进度条）→ SHA256 校验
       → 拉起安装包静默升级（/SILENT /CLOSEAPPLICATIONS）→ 关闭本程序

设计要点
  · 检查在子线程做，绝不卡界面；失败一律静默（启动时不打扰用户）。
  · 下载复用 transcribe 的 8 线程分块下载器，快且能断点续传。
  · 校验不通过会删掉半截文件并报错，**绝不执行来路不明的安装包**。
  · 安装走 Inno Setup 静默参数；因为安装包需要管理员权限，Windows 会弹
    一次系统安全提示（UAC），界面上已提前告知用户点「是」。
  · 用户可「稍后提醒我」或「跳过此版本」，跳过记录写在 config.json。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPlainTextEdit, 
                               QPushButton, QVBoxLayout)

import justoneapi_collector.branding as branding
import justoneapi_collector.updater as updater
from gui_qt.widgets.progress import FlowProgressBar

# 运行中的线程 / 非模态弹窗的强引用池。
# QThread 若在运行时被 GC 会直接 abort（0xC0000409），弹窗同理会瞬间消失，
# 所以两者都必须持有引用直到结束。
_LIVE_THREADS: set = set()
_ACTIVE_DIALOGS: set = set()


def _human(n) -> str:
    try:
        from justoneapi_collector.transcribe import human_size
        return human_size(int(n or 0))
    except Exception:                                      # noqa: BLE001
        try:
            return f"{int(n or 0) / 1048576:.1f} MB"
        except Exception:                                  # noqa: BLE001
            return "未知大小"


class _CheckWorker(QThread):
    """后台检查更新，结果通过 checked(UpdateInfo) 回传。"""

    checked = Signal(object)

    def __init__(self, feed_url: str, parent=None):
        super().__init__(parent)
        self.feed_url = feed_url

    def run(self):
        try:
            info = updater.check_for_update(self.feed_url)
        except Exception as e:                             # noqa: BLE001
            info = updater.UpdateInfo()
            info.error = f"检查更新失败：{e}"
        self.checked.emit(info)


class _DownloadWorker(QThread):
    """后台下载安装包并校验。progressed(pct, 文案)，pct<0 表示只是日志。"""

    progressed = Signal(float, str)
    finished_dl = Signal(bool, str)     # (ok, 本地路径或错误原因)

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self):
        def cb(done, total):
            try:
                pct = (float(done) / float(total) * 100.0) if total else 0.0
            except Exception:                              # noqa: BLE001
                pct = 0.0
            self.progressed.emit(min(pct, 100.0),
                                 f"{_human(done)} / {_human(total) if total else '未知'}")

        def log(text):
            self.progressed.emit(-1.0, str(text))

        try:
            ok, res = updater.download_update(self.info, progress=cb, log=log)
        except Exception as e:                             # noqa: BLE001
            ok, res = False, f"下载异常：{e}"
        self.finished_dl.emit(bool(ok), str(res))


class UpdateDialog(QDialog):
    """更新提示窗：展示说明、下载进度、一键安装。非模态，不打断当前操作。"""

    def __init__(self, info, parent=None, ctx=None):
        super().__init__(parent)
        self.info = info
        self.ctx = ctx
        self._worker = None
        self._install_path = ""

        self.setWindowTitle(f"发现新版本 v{info.version}")
        self.setModal(False)
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel(f"发现新版本：v{info.version}")
        title.setStyleSheet("font-size:17px;font-weight:700;color:#111827;")
        root.addWidget(title)

        sub = QLabel(f"当前版本：v{branding.APP_VERSION}"
                     + (f"　·　发布于 {info.published}" if info.published else "")
                     + f"　·　安装包 {info.size_text()}")
        sub.setStyleSheet("color:#6B7280;font-size:12px;")
        root.addWidget(sub)

        if info.mandatory:
            warn = QLabel("本次为重要更新，建议立即升级后再继续使用。")
            warn.setStyleSheet("color:#B91C1C;font-size:12px;font-weight:600;")
            root.addWidget(warn)

        root.addWidget(QLabel("更新内容："))
        notes = QPlainTextEdit()
        notes.setPlainText(info.notes_text())
        notes.setReadOnly(True)
        notes.setFixedHeight(150)
        notes.setStyleSheet("font-size:12px;")
        root.addWidget(notes)

        self._bar = FlowProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setVisible(False)
        root.addWidget(self._bar)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#1565c0;font-size:12px;")
        self._status.setVisible(False)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_update = QPushButton("⬇ 立即更新")
        self._btn_update.setObjectName("Primary")
        self._btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_update.clicked.connect(self._start_download)
        self._btn_later = QPushButton("稍后提醒我")
        self._btn_later.clicked.connect(self._later)
        self._btn_skip = QPushButton("跳过此版本")
        self._btn_skip.clicked.connect(self._skip)
        btn_row.addWidget(self._btn_update)
        btn_row.addWidget(self._btn_later)
        btn_row.addWidget(self._btn_skip)
        btn_row.addStretch()
        root.addLayout(btn_row)

        if info.mandatory:
            self._btn_later.setEnabled(False)
            self._btn_skip.setEnabled(False)

    # ---------------------------------------------------------------- 下载
    def _start_download(self):
        self._btn_update.setEnabled(False)
        self._btn_later.setEnabled(False)
        self._btn_skip.setEnabled(False)
        self._bar.setVisible(True)
        self._bar.setValue(0)
        self._status.setVisible(True)
        self._status.setStyleSheet("color:#1565c0;font-size:12px;")
        self._status.setText("正在准备下载…")

        w = _DownloadWorker(self.info)
        w.progressed.connect(self._on_progress)
        w.finished_dl.connect(self._on_downloaded)
        _LIVE_THREADS.add(w)
        w.finished.connect(lambda: _LIVE_THREADS.discard(w))
        self._worker = w
        w.start()

    def _on_progress(self, pct: float, text: str):
        if pct >= 0:
            self._bar.setValue(int(pct))
        if text:
            self._status.setText(("正在下载更新包：" if pct >= 0 else "") + text)

    def _on_downloaded(self, ok: bool, res: str):
        self._worker = None
        if not ok:
            self._bar.setVisible(False)
            self._btn_update.setEnabled(True)
            self._btn_later.setEnabled(True)
            self._btn_skip.setEnabled(True)
            self._status.setStyleSheet("color:#B91C1C;font-size:12px;")
            self._status.setText(f"更新失败：{res}\n可点「立即更新」重试，或到设置页手动检查。")
            return
        self._install_path = res
        self._bar.setValue(100)
        self._status.setStyleSheet("color:#2E7D32;font-size:12px;")
        self._status.setText(
            "下载并校验完成，正在启动安装程序…\n"
            "若系统弹出「是否允许此应用对设备进行更改」的安全提示，请点「是」；\n"
            "安装完成后程序会自动关闭，稍后重新打开即为新版本。")
        # 给界面 600ms 把上面这段话画出来，再拉起安装器并退出本程序
        QTimer.singleShot(600, self._do_install)

    def _do_install(self):
        ok, err = updater.launch_installer(self._install_path, silent=True)
        if not ok:
            QMessageBox.warning(
                self, "无法自动安装",
                f"{err}\n\n可以手动双击下面这个文件完成升级：\n{self._install_path}")
            try:
                updater.open_containing_folder(self._install_path)
            except Exception:                              # noqa: BLE001
                pass
            self._btn_update.setEnabled(True)
            return
        # 安装程序会自己关闭本程序（/CLOSEAPPLICATIONS）；这里主动退出，
        # 避免文件占用导致覆盖失败。
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ---------------------------------------------------------------- 按钮
    def _later(self):
        if self.ctx is not None:
            updater.mark_prompted(self.ctx.settings)
            try:
                self.ctx.save_settings()
            except Exception:                              # noqa: BLE001
                pass
        self.close()

    def _skip(self):
        if self.ctx is not None:
            updater.mark_skipped(self.ctx.settings, self.info.version)
            try:
                self.ctx.save_settings()
            except Exception:                              # noqa: BLE001
                pass
        self.close()


def _show_dialog(info, parent, ctx):
    dlg = UpdateDialog(info, parent, ctx)
    _ACTIVE_DIALOGS.add(dlg)
    dlg.finished.connect(lambda _r=0, d=dlg: _ACTIVE_DIALOGS.discard(d))
    dlg.show()
    dlg.raise_()
    return dlg


def maybe_check_update(parent, ctx, manual: bool = False, interval_days: float = 1.0,
                       on_message=None):
    """检查更新并在需要时弹出提示。

    manual=True：设置页「立即检查更新」——无论有没有新版都给用户明确回话。
    manual=False：启动自动检查——尊重「启动时自动检查更新」开关与跳过/节流
                  记录，没新版或刚提醒过时完全静默。
    on_message：可选回调，用于把结果同步到调用方的状态标签上。
    """
    if ctx is None:
        return None

    def say(text: str):
        if on_message:
            try:
                on_message(str(text))
            except Exception:                              # noqa: BLE001
                pass

    feed = str(ctx.get_setting("update_feed_url", "") or
               branding.UPDATE_FEED_URL or "").strip()
    if not manual and not bool(ctx.get_setting("update_auto_check", True)):
        return None
    if not feed:
        if manual:
            say("尚未填写更新地址")
            QMessageBox.information(
                parent, "软件更新",
                "还没有填写更新地址。请在上方「更新地址」里填入 version.json 的地址后重试。")
        return None
    if manual:
        # 手动点击：清掉跳过/节流记录，保证用户一定能看到真实结果
        updater.clear_skipped(ctx.settings)

    def _after(info):
        if info is None:
            return
        if not getattr(info, "available", False):
            if manual:
                extra = f"\n\n（{info.error}）" if getattr(info, "error", "") else ""
                say(f"已是最新版本 v{branding.APP_VERSION}" +
                    (f"（{info.error}）" if getattr(info, "error", "") else ""))
                QMessageBox.information(
                    parent, "软件更新",
                    f"当前已是最新版本 v{branding.APP_VERSION}{extra}")
            return
        if not manual:
            if not updater.should_prompt(ctx.settings, info.version,
                                         interval_days=interval_days):
                return
            updater.mark_prompted(ctx.settings)
            try:
                ctx.save_settings()
            except Exception:                              # noqa: BLE001
                pass
        say(f"发现新版本 v{info.version}，已弹出更新窗口")
        _show_dialog(info, parent, ctx)

    w = _CheckWorker(feed)
    w.checked.connect(_after)
    _LIVE_THREADS.add(w)
    w.finished.connect(lambda: _LIVE_THREADS.discard(w))
    w.start()
    if manual:
        _LIVE_THREADS.add(w)     # 幂等，保持引用直到结束
    return w
