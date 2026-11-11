# -*- coding: utf-8 -*-
"""探针：验证导航选中高亮 + 复选框勾号是否真的画出来了。

用法：QT_QPA_PLATFORM=offscreen python tools/_probe_checkbox.py
输出：tools/_probe_checkbox.png（放大 3 倍便于肉眼判断）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel,  # noqa: E402
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from gui_qt.theme import load_stylesheet  # noqa: E402


def main():
    app = QApplication(sys.argv)
    qss = load_stylesheet("light")
    app.setStyleSheet(qss)

    # 1) 打印注入结果，确认占位符被替换成绝对 file URL
    print("--- QSS 中 image 相关行 ---")
    for line in qss.splitlines():
        if "image:" in line:
            print(repr(line.strip()))

    # 2) 单独校验图片文件可被 Qt 加载
    from gui_qt.theme import _resolve_asset_url
    url = _resolve_asset_url("check.png")
    print("asset url:", url)
    pm = QPixmap(url)
    print("pixmap loaded:", (not pm.isNull()), pm.width(), "x", pm.height())

    root = QWidget()
    v = QVBoxLayout(root)

    # 复选框：未勾选 / 已勾选
    row = QHBoxLayout()
    for text, checked in [("未勾选", False), ("已勾选", True)]:
        cb = QCheckBox(text)
        cb.setChecked(checked)
        row.addWidget(cb)
    row.addStretch()
    v.addLayout(row)

    # 导航项：选中 / 未选中（用 QFrame#NavBar 包一层，模拟真实层级）
    from PySide6.QtWidgets import QFrame
    nav = QFrame()
    nav.setObjectName("NavBar")
    nav.setFixedWidth(220)
    nv = QVBoxLayout(nav)
    for text, checked in [("视频下载(选中)", True), ("数据采集(未选)", False)]:
        b = QPushButton(text)
        b.setObjectName("NavItem")
        b.setCheckable(True)
        b.setChecked(checked)
        nv.addWidget(b)
    v.addWidget(nav)

    # 表格行内勾选：1 行勾选、1 行未勾选
    tb = QTableWidget(2, 2)
    tb.setHorizontalHeaderLabels(["选择", "标题"])
    for r, st in enumerate([Qt.CheckState.Checked, Qt.CheckState.Unchecked]):
        it = QTableWidgetItem("")
        it.setCheckState(st)
        tb.setItem(r, 0, it)
        tb.setItem(r, 1, QTableWidgetItem(f"第{r}行"))
    tb.setFixedHeight(110)
    v.addWidget(tb)

    v.addWidget(QLabel("说明：上=复选框，中=导航，下=表格行"))
    root.resize(520, 330)
    root.show()
    app.processEvents()
    for _ in range(3):
        app.processEvents()

    img = root.grab().toImage()
    big = img.scaled(img.width() * 2, img.height() * 2,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe_checkbox.png")
    big.save(out)
    print("saved:", out)


if __name__ == "__main__":
    main()
