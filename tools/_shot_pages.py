"""把界面页面渲染成图片，用来目视检查排版（不启动窗口）。

用法：python tools/_shot_pages.py [页面名 ...]
默认输出：_tmp_shots/<页面名>.png

★ 为什么要这个：界面改动只看"能不能构建"是不够的 —— 控件重叠、文字挤出、
卡片歪斜这类问题，编译和冒烟都不报错，只有真正渲染出来才看得见。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ★ 离屏平台默认拿不到系统字体（QFontDatabase.families() 返回 0 个），
#   后果是整张截图上的中文全变成「□□□」方块 —— 看着像"界面崩了"，
#   其实是截图环境缺字体，代码一行都没错。2026-10-17 踩到，浪费一轮排查。
#   offscreen 插件走 freetype，必须显式告诉它去哪找字体。
if sys.platform.startswith("win"):
    _fontdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_fontdir):
        os.environ.setdefault("QT_QPA_FONTDIR", _fontdir)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication          # noqa: E402

from gui_qt.main_window import MainWindow           # noqa: E402
from gui_qt.pages._base import AppContext           # noqa: E402
from gui_qt.theme import apply_theme                # noqa: E402

OUT = ROOT / "_tmp_shots"


def _settle(app, ms: int = 450):
    """把事件循环空转 ms 毫秒再截图。

    ★ 为什么不能只 processEvents() 一次：界面上有 120~320ms 的入场动效
      （统计数字滚动、状态条淡入、卡片悬浮）。刚切完页就抓图，
      抓到的是动效第 0 帧 —— 数字还是空的、正片还在透明里，截图看着像
      "整页没渲染出来"。必须先让动画跑完。
    """
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    names = sys.argv[1:] or ["工作台", "选题挖掘", "关键词抓取", "文案仿写"]
    # SHOT_THEME=dark 出深色截图（文件名带 _dark 后缀，不覆盖浅色那张）。
    # ★ 必须能截深色：改主题时最容易漏的就是深色那一半 —— 浅色看着好，
    #   切过去才发现某处还是白底白字，而这种情况不会被任何自检抓到。
    mode = os.environ.get("SHOT_THEME", "light").strip().lower()
    app = QApplication([])
    apply_theme(app, mode)
    ctx = AppContext()
    win = MainWindow(ctx)
    if mode == "dark":
        win._theme_mode = "light"      # 让 _toggle_theme 正好翻到 dark
        win._toggle_theme()
    win.resize(1180, int(os.environ.get("SHOT_HEIGHT", "940") or 940))
    win.show()
    _settle(app, 600)
    OUT.mkdir(exist_ok=True)
    suffix = "_dark" if mode == "dark" else ""
    for n in names:
        win.switch_to(n)
        _settle(app)
        # ★ SHOT_SCROLL=<像素>：长页面下方的区块（比如文案仿写的刻度尺）默认
        #   在视口外，抓不到就等于没验证。给它一个滚动量，截当前视口。
        scroll = os.environ.get("SHOT_SCROLL", "").strip()
        if scroll:
            from PySide6.QtWidgets import QScrollArea
            area = win.findChild(QScrollArea)
            if area is not None:
                bar = area.verticalScrollBar()
                bar.setValue(int(scroll))
                _settle(app, 300)
        # 页面在滚动区里，直接抓整个窗口即可看到内容区
        pix = win.grab()
        path = OUT / f"{n}{suffix}.png"
        pix.save(str(path), "PNG")
        print(f"已保存 {path}（{pix.width()}x{pix.height()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
