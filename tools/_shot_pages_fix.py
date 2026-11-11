# -*- coding: utf-8 -*-
"""对比截图：验证「长页面被压缩重叠」是否已被滚动区修复。

输出（tools/ 下）：
    _shot_settings_before.png   直接放内容区（无滚动）→ 应看到卡片被压缩、控件重叠
    _shot_settings_after.png    包进 QScrollArea → 应看到完整卡片 + 右侧滚动条
    _shot_topic_after.png       选题挖掘页在未连接服务时的提示状态

用法：C:/Python310/python.exe -u tools/_shot_pages_fix.py
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt                                          # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase                         # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea        # noqa: E402

for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"):
    if os.path.isfile(fp):
        QFontDatabase.addApplicationFont(fp)

from gui_qt.pages._base import AppContext                              # noqa: E402
from gui_qt.pages.settings_page import SettingsPage                    # noqa: E402
from gui_qt.pages.topic_page import TopicPage                          # noqa: E402
from gui_qt.theme import apply_theme                                   # noqa: E402

OUT = ROOT / "tools"
CW, CH = 960, 712        # 客户窗口 1180x760 去掉左侧 220 导航后的内容区


def wrap(widget):
    """与 gui_qt.main_window._scroll_wrap 同构（此处内联，避免加载主窗口）。"""
    sa = QScrollArea()
    sa.setObjectName("PageScroll")
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sa.setWidget(widget)
    return sa


def shoot(widget, name):
    widget.resize(CW, CH)
    widget.show()
    for _ in range(5):
        APP.processEvents()
    img = widget.grab().toImage()
    img.save(str(OUT / name))
    print("saved %-30s %dx%d" % (name, img.width(), img.height()), flush=True)
    widget.hide()
    APP.processEvents()


def main() -> int:
    global APP
    APP = QApplication(sys.argv)
    APP.setFont(QFont("Microsoft YaHei", 9))
    apply_theme(APP, "light")
    ctx = AppContext()                      # 演示模式

    p1 = SettingsPage(ctx)
    shoot(p1, "_shot_settings_before.png")

    p2 = SettingsPage(ctx)
    s2 = wrap(p2)
    shoot(s2, "_shot_settings_after.png")

    p3 = TopicPage(ctx)
    s3 = wrap(p3)
    shoot(s3, "_shot_topic_after.png")

    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
