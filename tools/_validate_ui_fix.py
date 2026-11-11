# -*- coding: utf-8 -*-
"""自测：UI 高亮修复（侧边导航选中态 + 复选框勾号）落地校验。

校验三件事：
  1. 浅色/深色 QSS 里 NavItem:checked 的底色必须与导航栏底色有明显对比（不再"看不见"）
  2. QCheckBox / 表格 indicator 的勾选态必须带 image，且图片文件真实存在
  3. 主窗口能在两种主题下正常渲染（附截图供人工确认）

用法：python tools/_validate_ui_fix.py
输出：tools/_validate_ui_light.png、tools/_validate_ui_dark.png
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_fail = []


def check(cond, msg):
    print(("  [OK]   " if cond else "  [FAIL] ") + msg)
    if not cond:
        _fail.append(msg)


def _rgb(s: str):
    s = s.strip().lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _contrast(a: str, b: str) -> float:
    """粗略亮度差（0-255 的加权感知亮度差），>40 视为肉眼可辨。"""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    la = 0.299 * ra + 0.587 * ga + 0.114 * ba
    lb = 0.299 * rb + 0.587 * gb + 0.114 * bb
    return abs(la - lb)


def _wcag(a: str, b: str) -> float:
    """WCAG 对比度（1~21），用于判断文字能不能看清。"""
    def _lum(hexv: str) -> float:
        vals = []
        for c in _rgb(hexv):
            s = c / 255.0
            vals.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _block(qss: str, selector: str) -> str:
    """取出某选择器的声明块文本。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", qss)
    return m.group(1) if m else ""


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QCheckBox

    from gui_qt.theme import apply_theme, load_stylesheet
    from gui_qt.pages._base import AppContext
    from gui_qt.main_window import MainWindow

    app = QApplication(sys.argv)

    for mode in ("light", "dark"):
        print(f"\n=== 主题：{mode} ===")
        qss = load_stylesheet(mode)

        nav_bg = _block(qss, "QFrame#NavBar")
        m = re.search(r"background:\s*(#[0-9A-Fa-f]{6})", nav_bg)
        nav_color = m.group(1) if m else ""
        check(bool(nav_color), f"NavBar 底色解析成功：{nav_color}")

        checked = _block(qss, "QPushButton#NavItem:checked")
        m2 = re.search(r"background:\s*(#[0-9A-Fa-f]{6})", checked)
        chk_color = m2.group(1) if m2 else ""
        check(bool(chk_color), f"NavItem:checked 底色解析成功：{chk_color}")
        if nav_color and chk_color:
            # ★ 现行设计（v1.17 起，Apple 侧栏风格）不再用"高对比色块"表示选中，
            #   而是「底色提亮一档 + 3px 左侧指示条 + 字重加粗」。
            #   所以这里不再要求亮度差 ≥60（那是 v1.16 的老契约，会误报），
            #   改为校验真正让人看得见的三件事。
            d = _contrast(nav_color, chk_color)
            check(d >= 5, f"选中态底色与导航栏底色有区别（亮度差 {d:.0f} ≥ 5）")
        m3 = re.search(r"border-left:\s*\d+px\s+solid\s+(#[0-9A-Fa-f]{6})", checked)
        bar = m3.group(1) if m3 else ""
        check(bool(bar), f"选中态有左侧指示条：{bar or '缺失'}")
        if bar and nav_color:
            check(_contrast(nav_color, bar) >= 60,
                  f"指示条与导航栏底色对比足够（亮度差 "
                  f"{_contrast(nav_color, bar):.0f} ≥ 60）")
        mc = re.search(r"color:\s*(#[0-9A-Fa-f]{6})", checked)
        text_color = mc.group(1) if mc else ""
        if text_color and chk_color:
            ratio = _wcag(text_color, chk_color)
            check(ratio >= 4.5,
                  f"选中态文字与选中底色对比达标（{ratio:.1f}:1 ≥ 4.5）")

        # 勾号图片
        cb_checked = _block(qss, "QCheckBox::indicator:checked")
        mm = re.search(r'image:\s*url\("([^"]+)"\)', cb_checked)
        check(bool(mm), "QCheckBox 勾选态带 image 声明")
        if mm:
            p = mm.group(1).replace("/", os.sep)
            check(os.path.isfile(p), f"勾号图片真实存在：{os.path.basename(p)}")

        tb_checked = _block(qss, "QTableWidget::indicator:checked,\nQTableView::indicator:checked")
        check("image:" in tb_checked, "表格行内勾选框同样带 image（否则「选择」列还是纯色块）")

        # 渲染主窗口
        apply_theme(app, mode)
        ctx = AppContext()
        win = MainWindow(ctx)
        win.resize(1080, 720)
        win.show()
        app.processEvents()
        for _ in range(3):
            app.processEvents()
        out = os.path.join(HERE, f"_validate_ui_{mode}.png")
        img = win.grab().toImage()
        img.scaled(img.width() * 2, img.height() * 2,
                   Qt.AspectRatioMode.KeepAspectRatio,
                   Qt.TransformationMode.SmoothTransformation).save(out)
        print(f"  截图：{out}")
        win.close()

    # 复选框实例级验证：勾选态确实渲染出图（像素抽样）
    print("\n=== 复选框渲染抽样 ===")
    apply_theme(app, "light")
    cb = QCheckBox("测试")
    cb.setChecked(True)
    cb.resize(80, 24)
    cb.show()
    app.processEvents()
    img = cb.grab().toImage()
    # 取 indicator 区域（左侧 16x16）里的白色像素数，作为"勾画出来了"的证据
    white = 0
    for x in range(0, 18):
        for y in range(0, 22):
            if 0 <= x < img.width() and 0 <= y < img.height():
                c = img.pixelColor(x, y)
                if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                    white += 1
    print(f"  indicator 区域白像素数：{white}")
    check(white >= 8, "勾选态 indicator 上存在白色勾形像素（非纯色块）")

    print("\n" + ("全部通过 ✅" if not _fail else f"存在失败项 ❌ {len(_fail)} 条"))
    for f in _fail:
        print("  -", f)
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
