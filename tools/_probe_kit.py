"""交互组件目视探针：把展开态 / 分步态 / 刻度尺各抓一张图。

用法：
    SHOT_THEME=dark  python tools/_probe_kit.py
    SHOT_THEME=light python tools/_probe_kit.py

★ 为什么要单独一个探针：这三处交互的"好看"全在**动完之后**那一帧 ——
  时间轴节点有没有对齐、分步选择第二步长什么样、刻度尺拖动后的指针与数字
  是否对得上。默认截图抓到的是收起态，等于没验证。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    _fd = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_fd):
        os.environ.setdefault("QT_QPA_FONTDIR", _fd)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QScrollArea   # noqa: E402

from gui_qt.main_window import MainWindow                 # noqa: E402
from gui_qt.pages._base import AppContext                 # noqa: E402
from gui_qt.theme import apply_theme                      # noqa: E402
from gui_qt.widgets.kit import ChoiceFlow                 # noqa: E402

OUT = ROOT / "_tmp_shots"


def settle(app, ms=600):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    mode = os.environ.get("SHOT_THEME", "dark").strip().lower()
    app = QApplication([])
    apply_theme(app, mode)
    win = MainWindow(AppContext())
    if mode == "dark":
        win._theme_mode = "light"
        win._toggle_theme()
    win.resize(1180, 940)
    win.show()
    settle(app)
    OUT.mkdir(exist_ok=True)

    # ① 工作台：展开「上手检查」，三个节点依次点亮后的那一帧
    win.switch_to("工作台")
    settle(app)
    win._pages["工作台"].checker.set_expanded(True)
    settle(app, 1300)
    win.grab().save(str(OUT / f"probe_home_expand_{mode}.png"), "PNG")
    print("① 展开面板 ok")

    # ② 设置：选满 3 个 → 进第二步
    win.switch_to("设置")
    settle(app)
    flow = win._pages["设置"]._pref_host.findChild(ChoiceFlow)
    if flow is not None:
        for t in ("口播", "剧情", "Vlog"):
            flow.chips._toggle(t)
        settle(app, 400)
        flow.set_step(1)
        settle(app, 400)
    win.grab().save(str(OUT / f"probe_pref_{mode}.png"), "PNG")
    print("② 分步选择 ok")

    # ③ 文案仿写：把刻度尺拖到 55 秒之后
    win.switch_to("文案仿写")
    settle(app)
    page = win._pages["文案仿写"]
    page.dur_ruler.set_value(55, animate=True)
    # 刻度尺在页面下半部分，默认视口看不到 —— 直接把窗口拉高再抓
    win.resize(1180, 1500)
    settle(app, 700)
    win.grab().save(str(OUT / f"probe_ruler_{mode}.png"), "PNG")
    win.resize(1180, 940)
    settle(app, 300)
    print("③ 刻度尺 ok")

    # ④ 数据趋势：切到「万」+ 点眼睛（整组模糊）
    win.switch_to("数据趋势")
    settle(app)
    page = win._pages["数据趋势"]
    page.board.unit.set_value("wan")
    page.board.eye.set_masked(True)
    settle(app, 700)
    win.grab().save(str(OUT / f"probe_board_{mode}.png"), "PNG")
    print("④ 数据板 ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
