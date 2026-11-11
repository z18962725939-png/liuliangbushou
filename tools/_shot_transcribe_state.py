"""离屏渲染「转录页有数据」效果图（浅色 + 深色），用来目视检查五态徽标。

★ 只放演示文件到临时目录，不写真实配置、不启动转录、不联网、不下载。
  截图里的六行状态是刻意造的全覆盖样例，不是客户目录里的真实文件。

用法：
  /d/tcvenv/Scripts/python.exe tools/_shot_transcribe_state.py
输出：samples/转录页_状态卡片_浅色.png / _深色.png
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    fontdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(fontdir):
        os.environ.setdefault("QT_QPA_FONTDIR", fontdir)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402
from gui_qt.theme import apply_theme         # noqa: E402
from gui_qt.pages._base import AppContext    # noqa: E402
from gui_qt.main_window import MainWindow     # noqa: E402

OUT = ROOT / "samples"


def settle(app, ms=500):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def make_demo_files():
    """造六行覆盖五态的样例：已转录 / 转录中 / 源文件已更新 / 同名冲突×2 / 待转录。"""
    tmp = Path(tempfile.mkdtemp(prefix="tc_state_shot_"))
    out = tmp / "转录输出"
    out.mkdir()
    a, b = tmp / "课程A", tmp / "课程B"
    a.mkdir(); b.mkdir()

    done = tmp / "课程_第01集.mp4"
    done.write_bytes(b"d" * 4096)
    (out / "课程_第01集.txt").write_text("这是已经转好的文稿", encoding="utf-8")

    running = tmp / "访谈_第03期.m4a"
    running.write_bytes(b"r" * 2048)

    stale = tmp / "直播回放_1110.mp4"
    stale.write_bytes(b"s" * 8192)
    old = out / "直播回放_1110.txt"
    old.write_text("上一版源文件的旧稿", encoding="utf-8")
    os.utime(old, (old.stat().st_atime, time.time() - 3600))

    dup_a = a / "[1]--视频_1.mp4"
    dup_a.write_bytes(b"a" * 1024)
    dup_b = b / "[1]--视频_1.mp4"
    dup_b.write_bytes(b"b" * 2048)

    pending = tmp / "口播稿_01.mp3"
    pending.write_bytes(b"p" * 512)

    return tmp, out, [done, running, stale, dup_a, dup_b, pending], running


def main() -> int:
    app = QApplication([])
    apply_theme(app, "light")
    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(1180, 1120)
    win.show()
    settle(app, 600)
    win.switch_to("音视频转文本")
    settle(app, 350)

    page = win._pages["音视频转文本"]
    tmp, out, files, running = make_demo_files()
    page.out_dir.setText(str(out))
    for p in files:
        page._add_file(p, refresh=False)
    page._current_file = running
    page._sync_rows()
    page._set_phase(1)
    page.task_file.setText("当前任务：访谈_第03期.m4a · 62%")
    page.progress.setValue(62)
    page.status.setText("转写中 (2/6)：访谈_第03期.m4a · 62%")
    settle(app, 400)

    OUT.mkdir(exist_ok=True)
    labels = [page.file_list.item(i, 0).text() for i in range(page.file_list.rowCount())]
    print("演示状态：", labels)
    win.grab().save(str(OUT / "转录页_状态卡片_浅色.png"), "PNG")

    # ★ 切主题必须真调主窗口的 _toggle_theme()，只 apply_theme 骗不过徽标重刷
    win._toggle_theme()
    settle(app, 500)
    win.grab().save(str(OUT / "转录页_状态卡片_深色.png"), "PNG")
    print("已保存两张效果图到 samples/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
