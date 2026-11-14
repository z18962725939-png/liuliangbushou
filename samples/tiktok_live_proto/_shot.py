"""离屏渲染原型界面效果图（浅色 / 深色两张），不联网、不启监控。

  QT_QPA_PLATFORM=offscreen python samples/tiktok_live_proto/_shot.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # samples/

from PySide6.QtCore import QTimer                                # noqa: E402
from PySide6.QtWidgets import QApplication                       # noqa: E402

from tiktok_live_proto.app import ProtoWindow                    # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1]        # samples/


def settle(app: QApplication, rounds: int = 12) -> None:
    for _ in range(rounds):
        app.processEvents()
        QTimer.singleShot(30, app.quit)
        app.exec()


def fill_demo(win: ProtoWindow) -> None:
    """只在截图时注入演示态，不影响真实运行。"""
    win.log_view.clear()
    win.file_list.clear()
    win.handle_edit.setText("@ofdwex9")
    win.proxy_edit.setText("http://127.0.0.1:7897")
    win.phase_label.setText("录制中")
    win.lb_nick.setText("@ofdwex9 · Vibe-A1")
    win.lb_room.setText("7708019193639521076")
    win.lb_title.setText("Lets Go LIVE!")
    win.lb_check.setText("直播中 · 23:10:42")
    win.lb_file.setText("20261114_231042_ofdwex9_01.mp4")
    win.seg_label.setText("已录 1 段")
    win.dot.setStyleSheet("border-radius: 7px; background: #DC2626;")
    win.file_list.addItem("20261114_231042_ofdwex9_01.mp4　·　2.51 MB")
    win.file_list.addItem("TikTok直播_端到端验证_10秒.mp4　·　2.51 MB")
    for kind, text in (
        ("info", "开始监控 @ofdwex9｜代理：http://127.0.0.1:7897｜轮询 20s"),
        ("info", "拿到房间号 7708019193639521076（SIGI_STATE.LiveRoom.liveRoomUserInfo.user，页面 337727 字节）"),
        ("info", "状态：直播中｜Lets Go LIVE!｜流地址 3 路"),
        ("record", "● 开始录制：20261114_231042_ofdwex9_01.mp4"),
        ("info", "流地址复查：未变化，继续录制"),
        ("warn", "! 提示：本原型需要可用的海外网络出口"),
    ):
        win._append_log(kind, text)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = ProtoWindow()
    win.resize(1040, 720)
    win.show()
    fill_demo(win)
    settle(app)
    light = OUT_DIR / "TikTok直播监控原型_浅色.png"
    win.grab().save(str(light))

    win.dark = True
    win._apply_theme()
    fill_demo(win)
    settle(app)
    dark = OUT_DIR / "TikTok直播监控原型_深色.png"
    win.grab().save(str(dark))

    print(f"浅色：{light} ({light.stat().st_size} 字节)")
    print(f"深色：{dark} ({dark.stat().st_size} 字节)")
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
