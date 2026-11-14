# -*- coding: utf-8 -*-
"""离屏渲染「短剧解析 · 免凭证公开热榜」效果图（浅色 + 深色）。

★ 只放演示数据到内存，不写真实配置、不联网、不下载、不碰播放直链。
  截图里的榜单是刻意造的演示行，用于目视检查降级后的状态栏与列表。

用法：
  /d/tcvenv/Scripts/python.exe tools/_shot_drama_public_rank.py
输出：samples/短剧解析_免凭证公开热榜_浅色.png / _深色.png
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    fontdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(fontdir):
        os.environ.setdefault("QT_QPA_FONTDIR", fontdir)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from gui_qt.theme import apply_theme                     # noqa: E402
from gui_qt.pages._base import AppContext                # noqa: E402
from gui_qt.main_window import MainWindow                # noqa: E402
from justoneapi_collector import drama_api               # noqa: E402
from justoneapi_collector import drama_public_rank       # noqa: E402

OUT = ROOT / "samples"

_DEMO = [
    ("7702763145030290457", "万妖图录传第十四季", 1, ["玄幻", "穿越", "战斗", "全202集"]),
    ("7698294694886788121", "和离后我带女儿横扫魔域", 2, ["玄幻", "系统", "异界", "全1000集"]),
    ("7697459724635098174", "全球死考：刚下刑场你让我去考试一季", 3, ["脑洞", "穿越", "求生", "全786集"]),
    ("7701010101010101010", "凡人百世书第九季", 4, ["玄幻", "重生", "系统", "全117集"]),
    ("7702020202020202020", "小镇是非多之同床异梦", 5, ["都市", "家庭", "全63集"]),
    ("7703030303030303030", "母凭子贵后被皇家宠上天3", 6, ["古装", "逆袭", "全71集"]),
    ("7704040404040404040", "十八岁太奶奶驾到，重整家族荣耀第五部", 7, ["都市", "爽剧", "全102集"]),
    ("7705050505050505050", "分你七套房，我调去新疆", 8, ["都市", "现实", "全70集"]),
]


def settle(app, ms: int = 500) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def demo_rows():
    return [
        {
            "series_id": sid, "name": name, "cover": "", "url": "",
            "tags": tags, "intro": f"热播短剧 · 热榜第 {rank} 名",
            "heat": f"热榜第 {rank} 名", "score": "", "favorite": "", "like": "",
        }
        for sid, name, rank, tags in _DEMO
    ]


def main() -> int:
    app = QApplication([])
    apply_theme(app, "light")
    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(1180, 900)
    win.show()
    settle(app, 600)
    win.switch_to("短剧解析")
    settle(app, 350)
    page = win._pages["短剧解析"]

    btn = next((b for b in page.findChildren(QPushButton)
                if b.text() == "看热榜"), None)
    if btn is None:
        print("[中止] 没找到「看热榜」按钮")
        return 1

    # 无凭证 + 公开源返回演示数据 ⇒ 走真实的降级链路，不是手工塞列表
    with mock.patch.object(drama_api, "credentials_ready", return_value=False), \
         mock.patch.object(drama_public_rank, "fetch_rank",
                           return_value=(demo_rows(), "")):
        btn.click()
        settle(app, 1000)

    print("状态栏：", page._status.text())
    print("列表条数：", page._list.count())
    if page._list.count() == 0:
        print("[中止] 降级链路没出数据，不生成误导效果图")
        return 1

    OUT.mkdir(exist_ok=True)
    win.grab().save(str(OUT / "短剧解析_免凭证公开热榜_浅色.png"), "PNG")
    win._toggle_theme()
    settle(app, 500)
    win.grab().save(str(OUT / "短剧解析_免凭证公开热榜_深色.png"), "PNG")
    print("已保存两张效果图到 samples/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
