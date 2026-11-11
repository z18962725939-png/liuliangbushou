# -*- coding: utf-8 -*-
"""生成应用图标 build/app_icon.ico（打包 + 安装包共用）。

为什么要有这个脚本：
    installer.iss 里写着 SetupIconFile=build\\app_icon.ico，spec 也从同一位置取
    图标。但这个 ico 一直是手工放进 build/ 的 —— build/ 一旦被清掉（清构建产物
    时很容易顺手删掉），PyInstaller 和 Inno Setup 就会双双失败，且报错信息只是
    「系统找不到指定的文件」，很难一眼看出是图标丢了。

设计：与界面左上角品牌 logo 保持同一套图形（同心圆 + 四向刻度 = 靶心），
      靛蓝圆角方底 + 白色线条，多尺寸内嵌 PNG（Vista+ 的标准做法）。

用法：python tools/gen_app_icon.py
"""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# ICO 各尺寸：16/24/32 用于任务栏与列表，48/64 用于桌面，128/256 用于大图标与商店
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

BG = "#4F46E5"      # 品牌靛蓝
FG = "#FFFFFF"

# 靶心图形（与 gui_qt/icons.py 的 _LOGO_INNER 同源）
_CIRCLES = '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>'
_TICKS = ('<path d="M12 3v3"/><path d="M12 18v3"/>'
          '<path d="M3 12h3"/><path d="M18 12h3"/>')


def _svg(stroke_width: float, with_ticks: bool) -> str:
    inner = _CIRCLES + (_TICKS if with_ticks else "")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="%s" stroke-width="%.2f" '
        'stroke-linecap="round" stroke-linejoin="round">%s</svg>'
        % (FG, stroke_width, inner)
    )


def render(size: int) -> QImage:
    """画一枚图标：靛蓝圆角方底 + 白色靶心。

    小尺寸单独调粗线宽、去掉四向刻度 —— 16px 下细线加刻度只会糊成一团。
    """
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 圆角方底（圆角半径约为边长的 22%，接近 Win11 图标观感）
    radius = size * 0.22
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BG))
    p.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # 靶心
    if size <= 20:
        sw, ticks = 3.2, False
    elif size <= 40:
        sw, ticks = 2.4, True
    else:
        sw, ticks = 1.9, True
    renderer = QSvgRenderer(QByteArray(_svg(sw, ticks).encode("utf-8")))
    inset = size * 0.19
    renderer.render(p, QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    p.end()
    return img


def _png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return data


def build_ico(path: str, sizes=None) -> str:
    """把多张 PNG 打包成单个 .ico（Vista+ 支持内嵌 PNG）。"""
    sizes = sizes or SIZES
    pngs = [(s, _png_bytes(render(s))) for s in sizes]

    out = bytearray()
    out += struct.pack("<HHH", 0, 1, len(pngs))          # ICONDIR
    offset = 6 + 16 * len(pngs)
    for s, data in pngs:
        w = 0 if s >= 256 else s                          # 256 记作 0
        out += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _s, data in pngs:
        out += data

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(bytes(out))
    return path


def main():
    app = QApplication(sys.argv)  # noqa: F841
    out = os.path.join(ROOT, "build", "app_icon.ico")
    build_ico(out)
    print("written:", out, os.path.getsize(out), "bytes")
    # 同时导出一张 256 PNG，便于人工核对观感
    png = os.path.join(ROOT, "build", "app_icon_preview.png")
    render(256).save(png, "PNG")
    print("preview:", png)


if __name__ == "__main__":
    main()
