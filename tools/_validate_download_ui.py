# -*- coding: utf-8 -*-
"""v1.19.1 界面自检：下载失败弹窗与保存目录提示。

客户截图里的弹窗只有一句「保存目录不可写(PermissionError)」，
下面三条建议（链接过期/没登录/代理）全是错的。这里钉死新文案：
  · 弹窗必须显示**实际落盘**的目录，而不是用户填的那个
  · 出现「写入失败」时必须多给一条「怎么换目录」的建议
  · 目录不可写时，点下载之前就要在状态栏提示

跑法：QT_QPA_PLATFORM=offscreen python tools/_validate_download_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication, QMessageBox      # noqa: E402

from gui_qt.pages.video_download_page import VideoDownloadPage   # noqa: E402
from justoneapi_collector.video_downloader import VideoInfo      # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if not ok else ""))


# 拦下弹窗，别让 offscreen 无头进程卡在模态框上
CAPTURED: list[tuple[str, str, str]] = []


def _fake_warning(parent, title, text, *a, **kw):
    CAPTURED.append(("warning", title, text))
    return QMessageBox.StandardButton.Ok


def _fake_info(parent, title, text, *a, **kw):
    CAPTURED.append(("info", title, text))
    return QMessageBox.StandardButton.Ok


QMessageBox.warning = staticmethod(_fake_warning)
QMessageBox.information = staticmethod(_fake_info)

app = QApplication.instance() or QApplication([])

print("=" * 62)
print("v1.19.1 下载页界面自检")
print("=" * 62)

page = VideoDownloadPage(ctx=None)

# ---- 1. 目录不可写：点下载前就提示 ----
tmp = Path(tempfile.mkdtemp(prefix="tc_ui_"))
page.dir_edit.setText(str(tmp))
page._apply_save_dir()
status = page.status.text()
check("目录可写时不误报", "⚠" not in status, status)

# ---- 2. 失败结果里有「写入失败」→ 弹窗给换目录建议 ----
CAPTURED.clear()
v = VideoInfo(video_id="7697306010482491752", desc="#护膝 #抖音入手的好东西")
page.downloader.last_save_dir_used = Path(r"D:\流量捕手\下载")
page.downloader.last_dir_note = ""
page._on_downloaded([(v, None, "写入失败：没有写入权限(代码 5)｜路径 D:\\流量捕手\\下载\\a.mp4")])
kind, title, text = CAPTURED[-1]
check("失败弹窗标题正确", title == "下载未完成", title)
check("弹窗带实际落盘目录", r"D:\流量捕手\下载" in text)
check("弹窗带原始错误码", "代码 5" in text)
check("写入失败时多一条「怎么换目录」", "写入失败怎么办" in text, text[-260:])
check("不再只给三条无关建议", "① 播放地址已过期" in text)

# ---- 3. 自动改存后：弹窗要说明换到哪儿了 ----
CAPTURED.clear()
page.downloader.last_save_dir_used = Path(r"C:\Users\Administrator\Documents\流量捕手下载")
page.downloader.last_dir_note = (r"原保存目录 D:\流量捕手\下载 写不进去（写入失败：没有写入权限"
                                 r"(代码 5)），已自动改存到 C:\Users\Administrator\Documents\流量捕手下载")
page._on_downloaded([(v, None, "写入失败：没有写入权限(代码 5)")])
kind, title, text = CAPTURED[-1]
check("弹窗显示改存后的目录", "Documents" in text, text[:120])
check("弹窗说明为何改存", "已自动改存" in text)

# ---- 4. 全部成功但有改存提示 → 单独告知 ----
CAPTURED.clear()
page._on_downloaded([(v, Path(r"C:\Users\Administrator\Documents\流量捕手下载\x.mp4"), "")])
kind, title, text = CAPTURED[-1]
check("成功但改存过 → 弹提示", kind == "info" and "自动改存" in text, text[:120])
check("成功状态栏显示新目录", "Documents" in page.status.text(), page.status.text())

# ---- 5. 全部成功且没改存 → 不弹窗 ----
CAPTURED.clear()
page.downloader.last_dir_note = ""
page._on_downloaded([(v, Path(r"D:\流量捕手\下载\x.mp4"), "")])
check("正常成功不弹窗", CAPTURED == [], str(CAPTURED))

# ---- 6. 链接类失败不该出现「写入失败怎么办」----
CAPTURED.clear()
page.downloader.last_save_dir_used = Path(r"D:\流量捕手\下载")
page._on_downloaded([(v, None, "无水印直连：HTTP 403 被拒绝(403，多半是链接失效或需要登录)")])
kind, title, text = CAPTURED[-1]
check("链接失败时不扯磁盘", "写入失败怎么办" not in text, text[-200:])

# ---- 清理 ----
try:
    tmp.rmdir()
except OSError:
    pass

print("=" * 62)
print(f"下载页界面自检：{len(PASS)} 项通过，{len(FAIL)} 项失败")
if FAIL:
    for f in FAIL:
        print("  失败：", f)
    sys.exit(1)
