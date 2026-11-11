# -*- coding: utf-8 -*-
"""给官网批量生成界面截图（必须带中文字体）。

踩过坑：offscreen 平台默认拿不到中文字体，截出来全是"豆腐块"。
解决办法：用 QFontDatabase.addApplicationFont 显式注册微软雅黑，再 setFont。

用法：python tools/shoot_site_shots.py [主题 light|dark]
输出：官网素材/ui/<页面名>.png
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_FONT_FILES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\msyhl.ttc",     # 微软雅黑 Light
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体（兜底）
]
_FONT_FAMILIES = ["Microsoft YaHei", "微软雅黑", "Microsoft YaHei UI", "SimHei", "黑体"]


def install_cjk_font(app) -> str:
    """注册本机中文字体并设为默认。返回最终采用的字体族名。"""
    try:
        from PySide6.QtGui import QFont as _QF, QFontDatabase as DB
    except Exception:  # noqa: BLE001
        return ""

    registered: dict[str, list[str]] = {}
    for path in _FONT_FILES:
        if not os.path.isfile(path):
            continue
        fid = DB.addApplicationFont(path)
        if fid >= 0:
            registered[os.path.basename(path)] = list(DB.applicationFontFamilies(fid))

    print(f"  已注册字体文件：{list(registered) or '无'}")
    for fn, fams in registered.items():
        if fams:
            print(f"  {fn} 内含族名：{fams}")

    available = set(DB.families())
    for fam in _FONT_FAMILIES:
        if fam in available:
            app.setFont(_QF(fam, 9))
            print(f"  采用字体族：{fam}")
            return fam

    print("  [警告] 没找到中文字体族，截图可能缺字")
    return ""


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]", "", s.replace(" ", "")) or "page"


def main():
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    mode = sys.argv[1] if len(sys.argv) > 1 else "light"

    app = QApplication(sys.argv)
    print("=== 安装中文字体 ===")
    install_cjk_font(app)

    from gui_qt.theme import apply_theme
    from gui_qt.pages._base import AppContext
    from gui_qt.main_window import MainWindow

    apply_theme(app, mode)

    # 官网用的版式比例。某些页面内容很长会把窗口撑高，
    # 所以每次切换后都强制拉回目标尺寸，并按矩形截取左上角视图区。
    VIEW_W, VIEW_H = 1460, 920

    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(VIEW_W, VIEW_H)
    win.show()
    for _ in range(6):
        app.processEvents()

    out_dir = os.path.join(ROOT, "官网素材", "ui")
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n=== 逐页截图（主题 {mode}）===")
    for name in win._pages:
        win._switch(name)
        win.resize(VIEW_W, VIEW_H)
        for _ in range(6):
            app.processEvents()
        img = win.grab(QRect(0, 0, VIEW_W, VIEW_H)).toImage()
        img = img.scaled(
            img.width() * 2, img.height() * 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        path = os.path.join(out_dir, f"{safe_name(name)}.png")
        img.save(path)
        print(f"  {name:<12} → {os.path.basename(path)}  ({img.width()}x{img.height()})")

    win.close()


if __name__ == "__main__":
    main()
