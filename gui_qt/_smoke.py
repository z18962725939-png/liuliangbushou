"""无头构建校验：在 offscreen 平台下构建完整主窗口（全部页面），确认不崩溃。

页面数不写死，直接从 NAV_GROUPS 推导 —— 新增功能页时这里自动跟着变，
不会出现"加了页面忘了改断言"导致的假失败。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QStackedWidget  # noqa: E402

app = QApplication(sys.argv)

from gui_qt.theme import apply_theme  # noqa: E402
apply_theme(app, "light")

# ★ 见舟工作台在页面构造期会 _boot() 起同进程 uvicorn 守护线程 + 加载
#   QWebEngineView。无头冒烟只验证「21 个页面都能构建、不抛异常」，不需要
#   真起服务——服务的真起停由 _validate_jianzhou_embed.py 单独验证。
#   若在冒烟里起服务，daemon 线程与 WebEngine 子进程会在解释器收尾阶段互抢，
#   随机触发 0xC0000409 栈溢出（发版被假失败误拦）。这里在导入主窗口前
#   先把见舟页的 _boot 置空，绕开服务启动，保留界面构建校验。
try:
    import gui_qt.pages.jianzhou_page as _jz_page
    _jz_page.JianzhouPage._boot = lambda self: None
except Exception:                                          # noqa: BLE001
    pass

from gui_qt.pages._base import AppContext  # noqa: E402
from gui_qt.main_window import MainWindow, NAV_GROUPS  # noqa: E402

ctx = AppContext()  # 演示模式（无真实 client），验证界面层在所有页面下都能构建
window = MainWindow(ctx)
window.show()

stack = window.findChild(QStackedWidget)
assert stack is not None, "缺少堆叠内容区"
_expected = sum(len(items) for _g, items in NAV_GROUPS)
assert stack.count() == _expected, f"页面数异常: {stack.count()}（应为 {_expected}）"
assert len(window.findChildren(QStackedWidget)) >= 1, "导航页缺失"

# 依次切换每个页面，确认都能 setCurrentWidget 且不抛异常
for i in range(stack.count()):
    stack.setCurrentIndex(i)

# 切换深色主题，确认 QSS 重加载不报错
window._toggle_theme()

print(f"OFFSCREEN_BUILD_OK: 主窗口 + 侧边导航 + 全部 {stack.count()} 个页面构建通过（演示模式）")

# ★ 见舟工作台内嵌了同进程 uvicorn 守护线程 + QWebEngineView。
#   无头冒烟构造了全部页面（含见舟页），它在演示模式下也会 _boot 起服务。
#   进程退出前必须停掉服务并让窗口析构，否则 daemon 线程与 WebEngine 子进程
#   在解释器收尾阶段互抢，触发 0xC0000409 栈溢出（冒烟假失败、发版被误拦）。
try:
    from justoneapi_collector import jianzhou_host
    jianzhou_host.shutdown()
except Exception:                                          # noqa: BLE001
    pass
window.close()
app.processEvents()
sys.exit(0)
