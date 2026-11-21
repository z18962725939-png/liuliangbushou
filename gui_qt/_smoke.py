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

window.close()
app.processEvents()
sys.exit(0)
