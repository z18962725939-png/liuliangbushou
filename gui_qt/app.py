"""流量捕手 PySide6 应用入口。

业务模块（justoneapi_collector.*）完全复用，本文件只负责启动界面层。
用法：
    cd <项目根>
    python -m gui_qt.app            # 真实运行（需显示器）
    QT_QPA_PLATFORM=offscreen python -m gui_qt.app   # 无头构建校验
"""
import os
import sys

# 确保项目根在 sys.path，便于 import justoneapi_collector 与 gui_qt。
# 冻结（PyInstaller）模式下**绝不能**插：此时 __file__ 指向 _MEIPASS/_internal，
# 把它插到 sys.path[0] 会污染模块搜索路径——例如让 `import PySide6` 去解析
# _internal\PySide6\（只有 .pyd/.dll，无包结构）而非冻结归档里的正规模块，
# 导致启动崩溃。冻结时导入由 PyInstaller 自己的 finder 负责，无需干预。
if not getattr(sys, "frozen", False):
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui_qt.pages._base import AppContext  # noqa: E402
from gui_qt.theme import apply_theme  # noqa: E402
from gui_qt.main_window import MainWindow  # noqa: E402
from gui_qt.update_flow import maybe_check_update  # noqa: E402
from justoneapi_collector.projects import ProjectManager  # noqa: E402


def _write_crash_log(exc: BaseException) -> str:
    """把启动期异常写入用户目录日志文件。

    打包为 console=False 的 exe 后，启动崩溃表现为"双击没反应"、无任何提示，
    没有日志将完全无从排查。故在此落盘一份 traceback。
    """
    import traceback
    from datetime import datetime
    try:
        log_dir = os.path.join(os.path.expanduser("~"), ".TrafficCatcher")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "startup_crash.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("流量捕手 启动失败\n")
            f.write(f"时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
            f.write(f"Python: {sys.version}\n\n")
            f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        # P0：启动崩溃是最严重、也最容易静默丢失的故障，记一条脱敏摘要。
        # 只传异常类名，绝不把 traceback / 路径 / 用户信息塞进事件。
        # 函数体内 import 已登记在 spec 的 hiddenimports（gui_qt.diagnostics）。
        try:
            from gui_qt import diagnostics
            diagnostics.record_event(None, "startup_crash", step="boot",
                                     result="fail", code=type(exc).__name__)
        except Exception:
            pass
        return path
    except Exception:
        return ""


def _make_splash():
    """用纯代码绘制启动画面（墨底 + 柠檬色标题 + 版本 + 加载提示）。

    返回一个 QSplashScreen；失败返回 None 由调用方降级。刻意不引入新模块、
    不读外部图片，避免改 hiddenimports / datas 带来的打包回归风险。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QSplashScreen

    w, h = 460, 260
    pix = QPixmap(w, h)
    pix.fill(QColor("#0B0E14"))                 # 官网同一墨色
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 顶部柠檬色能量条
        p.fillRect(0, 0, w, 6, QColor("#C8F751"))
        # 主标题
        f = QFont()
        f.setPointSize(28)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#C8F751"))
        p.drawText(0, 74, w, 50, Qt.AlignmentFlag.AlignCenter, "流量捕手")
        # 副标题
        f2 = QFont()
        f2.setPointSize(12)
        p.setFont(f2)
        p.setPen(QColor("#98A1AF"))
        try:
            from justoneapi_collector.branding import APP_DISPLAY_NAME, APP_VERSION
            sub = f"{APP_DISPLAY_NAME}  v{APP_VERSION}"
        except Exception:                       # noqa: BLE001
            sub = "流量捕手"
        p.drawText(0, 128, w, 30, Qt.AlignmentFlag.AlignCenter, sub)
        # 加载提示
        f3 = QFont()
        f3.setPointSize(11)
        p.setFont(f3)
        p.setPen(QColor("#C8F751"))
        p.drawText(0, 196, w, 30, Qt.AlignmentFlag.AlignCenter, "正在启动，请稍候…")
    finally:
        p.end()
    sp = QSplashScreen(pix)
    sp.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    return sp


def build_context() -> AppContext:
    """构造应用上下文；在此创建 client / downloader 等共享业务资源并注入。

    接入真实采集服务的两条途径（缺一不可）：
      1) 已激活带内嵌 Token 的授权码；
      2) 设置页里填写了 API Token（旧版使用方式）。
    任一满足即构造真实 client，并进一步构造视频下载器；否则保持演示模式
    （client/downloader 均为 None），保证界面在无真实环境时也能构建、不崩溃。
    """
    ctx = AppContext()
    ctx.connect_real_client()
    # 客户端就绪后再构造下载器 / 直播监控引擎
    # （无 client 时 ensure_* 均直接返回 None，页面自动切演示模式）
    ctx.ensure_downloader()
    ctx.ensure_monitor()
    # 复盘监控 / 报告等页要往项目里落盘。仅在真实模式注入：演示模式保持 None，
    # 各页原有的演示分支（示例数据 + 演示提示）一行都不变。
    # ensure_default() 让新安装也有可写的默认项目（GUI 没有创建项目的入口）。
    if not ctx.demo:
        try:
            pm = ProjectManager()
            pm.ensure_default()
            ctx.project_manager = pm
        except Exception:                                       # noqa: BLE001
            ctx.project_manager = None
    return ctx


def run() -> None:
    # ★ 先把日志真正打开（2026-10-19）：此前 setup_logging 写了却没有任何地方调用，
    #   只有个别模块懒加载 get_logger 时才顺带初始化 —— 启动早期出故障时
    #   日志文件要么不存在、要么是 0 字节，客户把日志发过来也是空的。
    #   现在进 run() 第一件事就是装日志，之后任何故障都有迹可循。
    try:
        from justoneapi_collector.logger import setup_logging
        setup_logging()
    except Exception:                                           # noqa: BLE001
        pass                                                   # 日志装不上不能拦住启动

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("流量捕手")
        app.setApplicationDisplayName("流量捕手")
        apply_theme(app, "light")

        # ★ 启动画面（2026-11-19）：把「import 页面 + 构造业务资源 + 登录门」
        #   这段 (~1~4 秒) 的等待用一个稳定的品牌画面盖住。修复前客户看到的是
        #   「双击后空等几秒 → 闪一个 136x57 的默认小窗 → 再等几秒 → 主窗口」，
        #   像卡死/闪屏；现在全程是一张静止的启动图直到主窗口就绪。
        #   纯代码绘制（QPainter），不依赖外部图片，打包无需加 datas。
        #   任何异常都吞掉 —— 启动画面绝不能拦住软件启动。
        splash = None
        try:
            splash = _make_splash()
            if splash is not None:
                splash.show()
                app.processEvents()          # 立刻画出来，否则仍是空白等
        except Exception:                    # noqa: BLE001
            splash = None

        # 按钮悬停过渡（2026-10-27）：QSS 没有 transition，这里的接管让
        # 鼠标划过按钮时背景色是 180ms 渐变而不是开关式跳变。
        # ★ 装在 QApplication 上而不是逐个按钮：任何窗口/对话框里的按钮
        #   都自动生效，以后新页面也不用记得来挂一遍。TRAFFIC_NO_ANIM=1
        #   时函数返回 False，等于完全不接管（见 motion.py 的说明）。
        try:
            from gui_qt.widgets.motion import install_button_motion
            install_button_motion(app)
        except Exception:                                       # noqa: BLE001
            pass                        # 动效装不上只能降级为瞬时 hover

        ctx = build_context()
        window = MainWindow(ctx)
        window.resize(1180, 760)

        # 强制登录门（2026-10-11 新增）：要求先登录抖音再使用。
        # 登录态让作品抓取 / 视频解析走账号直连通道，不烧接口额度。
        # 返回 False = 客户在登录窗里点了「退出软件」→ 终止启动。
        # 任何异常（判定失败 / 对话框构造失败）都按「放行」处理，绝不拦人。
        try:
            from gui_qt.login_gate import ensure_login, is_logged_in, require_login_enabled
            gate_on = require_login_enabled(ctx)
        except Exception:                                   # noqa: BLE001
            gate_on = False
        if gate_on:
            # 登录门是模态框：先关掉启动画面再弹，避免两者叠在一起。
            if splash is not None:
                try:
                    splash.hide()
                except Exception:                           # noqa: BLE001
                    pass
            if not ensure_login(None, ctx, window=window):
                return                                      # 用户主动退出，正常收尾
            try:
                if not is_logged_in(ctx.settings):
                    window.show_login_warning(True)         # 跳过了登录 → 常驻提示
            except Exception:                               # noqa: BLE001
                pass

        window.show()
        # 主窗口真正显示后再收启动画面：中间不留空窗闪烁。
        if splash is not None:
            try:
                splash.finish(window)
            except Exception:                               # noqa: BLE001
                try:
                    splash.hide()
                except Exception:                           # noqa: BLE001
                    pass
            splash = None

        # 启动后自动检查新版本（后台线程，无新版则完全静默）。
        # 延迟 1.5 秒：先让主界面画出来，避免联网检查把启动过程拖慢。
        # 未激活授权的用户也会检查 —— 授权过期的客户同样要能收到修复更新。
        QTimer.singleShot(1500, lambda: maybe_check_update(window, ctx))

        sys.exit(app.exec())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 启动期兜底，需捕获一切
        path = _write_crash_log(exc)
        # 异常退出也要收掉启动画面，别让它盖在报错框上不肯走。
        try:
            _sp = locals().get("splash")
            if _sp is not None:
                _sp.hide()
        except Exception:                                    # noqa: BLE001
            pass
        # 尽力弹窗告知用户；若 Qt 尚不可用则静默退出（日志已落盘）
        try:
            import traceback as _tb
            from PySide6.QtWidgets import QMessageBox
            _app = QApplication.instance() or QApplication(sys.argv)
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("流量捕手 启动失败")
            box.setText(f"程序启动出错：{exc}")
            # ★ 版本号必须出现在弹窗里（2026-10-19 客户报障后补）：
            #   客户截图只拍了这句话，我们无法判断他装的是哪一版，
            #   只能靠反查安装包逐个排除。侧栏底部那一行在启动失败时根本看不到。
            try:
                from justoneapi_collector.branding import APP_VERSION
                _ver = f"v{APP_VERSION}"
            except Exception:                                   # noqa: BLE001
                _ver = "未知版本"
            box.setInformativeText(
                f"版本：{_ver}\n\n"
                "可以试试关掉软件重新打开；如果还是这样，把下面这个日志文件"
                "发给服务方即可定位：\n{path or '（日志写入失败）'}")
            # 完整 traceback 收进「显示详情」：正文只留一句人话，
            # 但排障需要的那部分一键就能复制走。
            box.setDetailedText("".join(
                _tb.format_exception(type(exc), exc, exc.__traceback__)))
            box.exec()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    run()
