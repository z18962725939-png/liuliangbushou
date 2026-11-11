"""强制登录门：未登录抖音时先扫码，登录成功才允许使用软件。

★ 为什么要有这道门（2026-10-11 需求）
   客户登录后，作品列表抓取走 CDP 浏览器直连、视频解析走页面直取，
   这两条路都不调用 JustOneAPI —— 不登录就只能走付费接口，额度烧得飞快
   （一次批量导入 20 条 ≈ 2 元）。所以从产品角度必须把登录做成默认动作。

★ 三条安全设计（缺一不可，否则会把客户锁死在门外）
   1. **本地开关**：设置页「要求先登录抖音再使用」，客户自己可关。
   2. **云端保险丝**：启动时拉一次 gate.json，作者把 require_login 改成
      false 即可全量远程关闭 —— 万一登录链路在某个环境大面积失败，
      不用等发版就能救回来。拉不到（断网/超时）就按本地开关走，绝不拦人。
   3. **失败兜底入口**：扫码连续失败 2 次后，对话框才出现「暂不登录，
      先进入」—— 浏览器打不开这类环境问题时，客户不至于完全用不了，
      进入后主界面顶部常驻提示条，随时可以补登。
"""
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

import justoneapi_collector.browser_login as bl

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
# 云端保险丝：与官网同域（COS 静态网站），与 version.json 同级目录。
# 内容形如 {"require_login": true}；作者改这个文件即可全量开关强制登录。
REMOTE_GATE_URL = ("https://liuliangbushou-1258850027.cos-website."
                   "ap-guangzhou.myqcloud.com/gate.json")
REMOTE_TIMEOUT = 3.0          # 秒：宁可超时放行，也不能拖慢启动

PLATFORM = "douyin"
SETTING_KEY = "require_login"          # 本地开关键（默认开，见 _base 默认值）
REMOTE_CACHE_KEY = "_gate_remote_at"   # 上次成功拉到云端开关的时间（仅调试用）

_COOKIE_NAMES = bl.LOGIN_PLATFORMS.get(PLATFORM, {}).get(
    "cookie_names", bl.LOGIN_COOKIE_NAMES)


def cookie_key(platform: str) -> str:
    """该平台登录 Cookie 在 config.json 里的键名。

    抖音保留历史键名 `douyin_cookie`（老用户配置里已存的值要能继续读到），
    其余平台用 `<平台>_cookie`。
    """
    return "douyin_cookie" if platform == "douyin" else f"{platform}_cookie"


def is_logged_in(settings: dict, platform: str = PLATFORM) -> bool:
    """判断本机是否已保存该平台的有效登录态。

    只看 Cookie 里有没有登录态关键字段（sessionid / sid_tt …）。
    不联网校验：抖音网页 Cookie 有效期以周计，为一次启动去发请求反而
    容易撞风控；真失效时功能会自然回落接口，不会崩。
    """
    header = str(settings.get(cookie_key(platform)) or "").strip()
    if not header:
        return False
    names = {p.split("=", 1)[0].strip() for p in header.split(";") if "=" in p}
    return bool(names & set(_COOKIE_NAMES))


def remote_override() -> bool | None:
    """拉取云端保险丝；返回 True/False 表示云端明确要求，None 表示没拉到。

    任何异常都返回 None —— 断网客户绝不能被这道门挡住。
    """
    try:
        import httpx
        with httpx.Client(timeout=REMOTE_TIMEOUT, follow_redirects=True) as c:
            r = c.get(REMOTE_GATE_URL)
        if r.status_code != 200:
            return None
        data = json.loads(r.text)
        if not isinstance(data, dict):
            return None
        v = data.get("require_login")
        if isinstance(v, bool):
            return v
    except Exception:                                   # noqa: BLE001
        return None
    return None


def require_login_enabled(ctx, use_remote: bool = True) -> bool:
    """是否要求先登录才能使用。

    优先级：云端保险丝（明确值时覆盖）> 本机设置 > 默认 True。
    """
    settings = getattr(ctx, "settings", None) or {}
    local = bool(settings.get(SETTING_KEY, True))
    if not use_remote:
        return local
    remote = remote_override()
    if remote is None:
        return local
    return remote


# --------------------------------------------------------------------------
# 扫码登录后台线程（设置页与登录门共用）
# --------------------------------------------------------------------------
class BrowserLoginWorker(QThread):
    """后台执行浏览器扫码登录，边轮询边回报状态。

    ★ 2026-10-11 修复（客户反馈「浏览器里明明登录成功了，软件却一直不显示
      已登录」）：旧代码写的是 `ok, header = session.poll()`，而 poll() 返回
      的是**三元组** (state, cookie_header, info)，解包成两个变量会直接抛
      ValueError: too many values to unpack；异常发生在 QThread 的 run() 里
      且没有 except，线程随即静默死亡 —— 界面永远停在「等待扫码…」，扫码
      成功也毫无反应，且日志里看不到任何报错（极难自查）。
      顺带修掉另外两处隐患：
        · `if ok:` 对 "waiting" / "dead" 这类非空字符串同样为真，会误判成功；
        · session.start() 的返回值没检查，浏览器没拉起来也白等 5 分钟。
    """
    status = Signal(str)
    done = Signal(bool, str)   # (ok, cookie_header_or_reason)

    def __init__(self, platform, app_data_dir, port=18932, max_wait: int = 300):
        super().__init__()
        self.platform = platform
        self.app_data_dir = app_data_dir
        self.port = port
        self.max_wait = max_wait
        self._stop = False

    def run(self):
        cfg = bl.LOGIN_PLATFORMS.get(self.platform)
        if not cfg:
            self.done.emit(False, "未知平台")
            return
        if cfg.get("domestic_blocked"):
            self.done.emit(False, f"{cfg['label']} 国内网络不可用，已禁用")
            return
        try:
            session = bl.create_login_session(self.platform, self.app_data_dir, self.port)
        except Exception as e:                           # noqa: BLE001
            self.done.emit(False, f"打开浏览器失败：{e}")
            return

        ok, msg = session.start()
        if not ok:
            self.done.emit(False, msg or "浏览器启动失败，请确认已安装 Edge 或 Chrome")
            return

        try:
            dead_hits = 0
            for _ in range(self.max_wait):     # 默认最多等 5 分钟（扫码 + 手机确认有时很慢）
                if self._stop:
                    self.done.emit(False, "已取消登录")
                    return
                try:
                    state, header, _info = session.poll()
                except Exception as e:                   # noqa: BLE001
                    dead_hits += 1
                    if dead_hits >= 5:
                        self.done.emit(False, f"读取登录状态失败：{e}")
                        return
                    time.sleep(1)
                    continue

                if state == "logged_in":
                    self.status.emit(f"{cfg['label']} 登录成功")
                    self.done.emit(True, header or "")
                    return
                if state == "dead":
                    dead_hits += 1
                    if dead_hits >= 5:
                        self.done.emit(False, "浏览器窗口已关闭，登录取消")
                        return
                else:                        # waiting：正常等待扫码，清零容错计数
                    dead_hits = 0
                time.sleep(1)
            self.done.emit(False, f"登录超时（{self.max_wait // 60} 分钟内未完成扫码），请重新点击扫码登录")
        finally:
            try:
                session.stop()
            except Exception:                            # noqa: BLE001
                pass

    def stop(self):
        self._stop = True


def _save_cookie(ctx, platform: str, header: str) -> None:
    """登录成功后落盘 Cookie 并注入下载器 / 直播监控。"""
    ctx.settings[cookie_key(platform)] = header
    ctx.settings[f"{platform}_cookie_ts"] = time.strftime("%m-%d %H:%M")
    try:
        ctx.save_settings()
    except Exception:                                    # noqa: BLE001
        pass

    if getattr(ctx, "downloader", None) is None:
        try:
            ctx.ensure_downloader()
        except Exception:                                # noqa: BLE001
            pass
    if getattr(ctx, "downloader", None) is not None:
        try:
            ctx.downloader.set_cookie_for(platform, header)
        except Exception:                                # noqa: BLE001
            pass
    if platform == "douyin" and getattr(ctx, "daemon", None) is not None:
        try:
            ctx.daemon.set_douyin_cookie(header)
        except Exception:                                # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# 登录门对话框
# --------------------------------------------------------------------------
class LoginGateDialog(QDialog):
    """启动时的强制登录窗口：未登录不允许进入软件。

    出口只有三个：
      · 登录成功      → accept()，放行
      · 退出软件      → reject()，调用方据此终止启动
      · 连续失败后「暂不登录，先进入」（仅环境异常时的逃生通道）→ accept()
    """

    def __init__(self, ctx, parent=None, skip_after_fail: int = 2):
        super().__init__(parent)
        self.ctx = ctx
        self._skip_after_fail = skip_after_fail
        self._fails = 0
        self._logged_in = False
        self._skipped = False
        self._worker = None
        self._app_data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "流量捕手"
        self.setWindowTitle("流量捕手 · 登录")
        self.setModal(True)
        self.setFixedSize(540, 400)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self._build_ui()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 22)
        root.setSpacing(12)

        title = QLabel("请先登录抖音账号")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#111827;")
        root.addWidget(title)

        sub = QLabel("用你自己的抖音账号登录后，抓作品、解析视频都走账号直连通道，"
                     "不消耗接口额度，成功率也更高。")
        sub.setWordWrap(True)
        sub.setStyleSheet("font-size:13px;color:#4B5563;")
        root.addWidget(sub)

        for line in ("· 一次登录长期有效，下次打开软件不用重复扫码",
                     "· 登录信息只保存在你本机，用于采集与下载",
                     "· 点下方按钮会打开浏览器（Edge / Chrome），用抖音 App 扫码即可"):
            lab = QLabel(line)
            lab.setWordWrap(True)
            lab.setStyleSheet("font-size:12px;color:#6B7280;")
            root.addWidget(lab)

        root.addSpacing(6)

        self.login_btn = QPushButton("扫码登录抖音")
        self.login_btn.setObjectName("Primary")
        self.login_btn.setMinimumHeight(42)
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.clicked.connect(self._on_login_btn)
        root.addWidget(self.login_btn)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size:12px;color:#1565C0;")
        root.addWidget(self.status)

        # 逃生通道：默认隐藏，连续失败后才出现（浏览器打不开等环境问题）
        self.skip_btn = QPushButton("暂不登录，先进入（部分功能受限）")
        self.skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.skip_btn.setStyleSheet("color:#6B7280;font-size:12px;")
        self.skip_btn.setVisible(False)
        self.skip_btn.clicked.connect(self._skip)
        root.addWidget(self.skip_btn)

        root.addStretch()

        bottom = QHBoxLayout()
        help_btn = QPushButton("登录遇到问题？")
        help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        help_btn.setStyleSheet("color:#9CA3AF;font-size:12px;")
        help_btn.clicked.connect(self._show_help)
        bottom.addWidget(help_btn)
        bottom.addStretch()
        quit_btn = QPushButton("退出软件")
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet("color:#6B7280;font-size:12px;")
        quit_btn.clicked.connect(self.reject)
        bottom.addWidget(quit_btn)
        root.addLayout(bottom)

    # ------------------------------------------------------------ 交互
    def _on_login_btn(self):
        """登录中再点一次 = 取消登录。"""
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self.login_btn.setEnabled(False)
            self.status.setText("正在取消…")
            return
        self._start_login()

    def _start_login(self):
        self.login_btn.setText("取消登录")
        self.status.setStyleSheet("font-size:12px;color:#1565C0;")
        self.status.setText("正在打开浏览器…（如未弹出，请检查是否安装了 Edge 或 Chrome）")
        self._worker = BrowserLoginWorker(PLATFORM, self._app_data_dir)
        self._worker.status.connect(self._on_status)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_status(self, msg):
        self.status.setText(msg)

    def _on_done(self, ok, payload):
        self.login_btn.setText("扫码登录抖音")
        self.login_btn.setEnabled(True)
        if ok and payload:
            _save_cookie(self.ctx, PLATFORM, payload)
            self._logged_in = True
            self.status.setStyleSheet("font-size:12px;color:#2E7D32;font-weight:600;")
            self.status.setText("登录成功，正在进入软件…")
            self.accept()
            return
        if ok:
            self.status.setStyleSheet("font-size:12px;color:#C62828;")
            self.status.setText("登录成功但未读到 Cookie，请重新扫码")
        else:
            self.status.setStyleSheet("font-size:12px;color:#C62828;")
            self.status.setText(f"未完成登录：{payload}")
        self._fails += 1
        if self._fails >= self._skip_after_fail:
            self.skip_btn.setVisible(True)

    def _skip(self):
        self._skipped = True
        self.accept()

    def _show_help(self):
        QMessageBox.information(
            self, "登录帮助",
            "1. 需要电脑上装有 Edge 或 Chrome（Windows 10/11 自带 Edge）；\n"
            "2. 点「扫码登录抖音」后会自动打开抖音网页版，用抖音 App 扫页面上的二维码；\n"
            "3. 手机上点确认后，本窗口会自动变成「登录成功」；\n"
            "4. 若始终打不开浏览器，可点「暂不登录，先进入」——"
            "未登录时抓取与解析会走付费接口，额度消耗更快；\n"
            "5. 仍有问题，加作者微信：807108536。")

    # ---------------------------------------------------------- 关闭行为
    def closeEvent(self, event):                    # noqa: N802
        """右上角 X：未登录时给出选择，而不是默默放行或默默退出。"""
        if self._logged_in or self._skipped:
            event.accept()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("未登录")
        box.setText("还没有登录抖音。关闭将退出软件，确定吗？")
        cont = box.addButton("继续登录", QMessageBox.ButtonRole.AcceptRole)
        quit_ = box.addButton("退出软件", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cont)
        box.exec()
        if box.clickedButton() is quit_:
            self.reject()
            event.accept()
        else:
            event.ignore()

    # ------------------------------------------------------------ 结果
    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def skipped(self) -> bool:
        return self._skipped


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def ensure_login(parent, ctx, window=None, force: bool = False) -> bool:
    """保证「可以使用软件」之前已登录；返回 False 表示应当终止启动。

    parent  —— 对话框父窗口（启动阶段可为 None）
    ctx     —— AppContext
    window  —— MainWindow，登录成功后同步刷新设置页的登录态显示
    force   —— True 时跳过「是否已登录」判断，直接弹门（用于退出登录后）
    """
    try:
        if not force and not require_login_enabled(ctx):
            return True
        if not force and is_logged_in(ctx.settings):
            return True
    except Exception:                                    # noqa: BLE001
        return True                                      # 判定出错一律放行，绝不拦人

    try:
        dlg = LoginGateDialog(ctx, parent)
        res = dlg.exec()
    except Exception as exc:                             # noqa: BLE001
        print(f"[流量捕手] 登录门异常，已放行：{exc}")
        return True

    if dlg.logged_in and window is not None:
        # 同步设置页显示 + 让新 Cookie 立刻生效
        try:
            page = window._pages.get("设置")
            if page is not None:
                page._restore_saved_logins()
        except Exception:                                # noqa: BLE001
            pass
    if res == QDialog.DialogCode.Rejected and not dlg.logged_in and not dlg.skipped:
        return False
    return True


def apply_skip_banner(window) -> None:
    """跳过登录后在主界面顶部挂一条常驻提示（客户可随时补登）。

    只在 window 暴露 show_login_warning() 时生效，缺失则静默跳过。
    """
    try:
        window.show_login_warning()
    except Exception:                                    # noqa: BLE001
        pass
