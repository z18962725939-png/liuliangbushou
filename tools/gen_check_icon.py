# -*- coding: utf-8 -*-
"""生成 QSS 需要的两张位图：复选框「对勾」与下拉框「箭头」。

为什么需要它们：
    项目在 QSS 里自定义了 QCheckBox::indicator 的 background/border。
    一旦自定义了 indicator 的盒模型，Qt 就不再使用系统主题绘制对勾，
    勾选后只剩一个纯色方块 —— 用户完全看不出「勾没勾」。
    QSS 的 image: url(...) 又不支持 data URI，所以必须落一张真实图片。

    ★ 下拉箭头是同一类坑：QSS 里一旦写了 QComboBox::drop-down（哪怕只改
      border 和 width），Qt 就不再用系统主题画那个小三角，结果是下拉框
      变成一根「只有边框、没有箭头」的输入框 —— 客户根本看不出能点。
      必须自己给 ::down-arrow 一张图。2026-10-17 改版时踩到，截图放大才看见。

用法：
    python tools/gen_check_icon.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# 画布尺寸：取 2 倍于 indicator（16px），兼顾普通屏与 125%/150% 缩放屏的清晰度
CANVAS = 32


def build_pixmap(size: int = CANVAS, color: str = "#FFFFFF") -> QPixmap:
    """画一条居中、留边距的对勾，返回透明底 QPixmap。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    u = size / 32.0  # 以 32 为设计基准做等比缩放
    path = QPainterPath(QPointF(8.0 * u, 17.0 * u))
    path.lineTo(QPointF(13.5 * u, 22.5 * u))
    path.lineTo(QPointF(24.0 * u, 10.5 * u))

    pen = QPen(QColor(color))
    pen.setWidthF(3.6 * u)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.end()
    return pm


def build_chevron(size: int = CANVAS, color: str = "#5B6472") -> QPixmap:
    """画一个居中的下箭头（chevron），返回透明底 QPixmap。

    线宽 / 端点与 icons.py 里的 SVG 图标保持同一套语言（圆头、圆角、细线），
    这样下拉框的箭头不会看着像从别的软件里抠来的。
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    u = size / 32.0
    path = QPainterPath(QPointF(9.0 * u, 12.5 * u))
    path.lineTo(QPointF(16.0 * u, 19.5 * u))
    path.lineTo(QPointF(23.0 * u, 12.5 * u))

    pen = QPen(QColor(color))
    pen.setWidthF(3.2 * u)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.end()
    return pm


def main():
    app = QApplication(sys.argv)  # noqa: F841  QPixmap 需要 QGuiApplication 实例
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "gui_qt", "styles")
    os.makedirs(out_dir, exist_ok=True)

    # 白勾：配「墨色」勾选底（浅色主题的强调色）
    for name, size in [("check.png", 32), ("check@2x.png", 64)]:
        path = os.path.join(out_dir, name)
        build_pixmap(size).save(path, "PNG")
        print("written:", path)

    # 墨勾：配「柠檬」勾选底（深色主题的强调色 #C8F751 是高亮黄绿，
    #   白勾落在上面几乎看不见，必须用深色勾 —— 与 check.png 成对。）
    for name, size in [("check_dark.png", 32), ("check_dark@2x.png", 64)]:
        path = os.path.join(out_dir, name)
        build_pixmap(size, "#0B0E14").save(path, "PNG")
        print("written:", path)

    # 下拉箭头：浅色用中性灰（跟导航未选中文字同色），深色用浅灰。
    #   两边都不用纯黑/纯白 —— 箭头是「可点」的暗示，不该抢正文的注意力。
    for name, size, color in [("arrow_down.png", 32, "#5B6472"),
                              ("arrow_down@2x.png", 64, "#5B6472"),
                              ("arrow_down_dark.png", 32, "#9AA4B2"),
                              ("arrow_down_dark@2x.png", 64, "#9AA4B2")]:
        path = os.path.join(out_dir, name)
        build_chevron(size, color).save(path, "PNG")
        print("written:", path)


if __name__ == "__main__":
    main()
