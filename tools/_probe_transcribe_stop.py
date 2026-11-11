# -*- coding: utf-8 -*-
"""离屏真链路探针：转写引擎下载卡住时，客户的两个「逃生出口」是否真的能点。

为什么要有这个探针（2026-11-10 客户截图）：
    439MB 显卡引擎下载卡在 89%，进度在 85%~91% 来回横跳，而「停止」是灰的，
    客户只能干等或强杀。修完下载器后新增了「下载期间可停止 + 失败后一键切 CPU」，
    但这类问题在本项目里反复以「按钮点了没反应」的面目出现（QWidget 状态栏被
    重复创建、Qt 槽抛异常静默卡死、checkable 按钮 checked 翻转……）。
    静态扫到 `setEnabled(True)` 不等于按钮真能点到，所以必须用真实 click() 走一遍。

全程 offscreen + 演示模式 AppContext；install_engine / bootstrap_engine / QMessageBox
全部替换成假的，不联网、不写用户目录、不弹窗。
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication([])

from gui_qt.theme import apply_theme  # noqa: E402
apply_theme(app, "light")

from gui_qt.pages._base import AppContext  # noqa: E402
from gui_qt.pages import transcribe_page as TP  # noqa: E402

_PASSED = 0
_FAILED = 0


def chk(name: str, cond: bool, why: str = ""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f"OK   {name}")
    else:
        _FAILED += 1
        print(f"FAIL {name}" + (f"  — {why}" if why else ""))


class FakeDL:
    """只实现页面会用到的 cancel()。"""

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


boot_calls: list[str] = []
exec_calls: list = []


def fake_bootstrap(kind: str):
    boot_calls.append(kind)


def fake_exec(box):
    """拦住模态框，记录它，并让 clickedButton() 返回第一个按钮（＝改用 CPU）。"""
    exec_calls.append(box)
    return 0


def fake_clicked(box):
    return box.buttons()[0]


with patch.object(TP, "bootstrap_engine", fake_bootstrap):
    ctx = AppContext()
    page = TP.TranscribePage(ctx)

    chk("页面构造成功", page is not None)
    chk("停止按钮初始为灰", not page.stop_btn.isEnabled())
    chk("设备下拉有 CPU / 显卡两项", page.device.count() == 2,
        f"实际 {page.device.count()}")

    # ── 场景 1：下载期间真点「停止」 ────────────────────────────────
    # 假的 install_engine 卡在 gate 上，模拟 439MB 下不完；期间验证按钮状态，
    # 再用真实 click() 取消，最后放它返回，验证停止后不再弹吓人的失败框。
    gate = threading.Event()
    holder: dict = {}

    def fake_install(kind, progress=None, log=None, on_downloader=None):
        fd = FakeDL()
        holder["dl"] = fd
        if on_downloader:
            on_downloader(fd)
        if progress:
            progress(392 * 1048576, 439 * 1048576)
        gate.wait(5)
        return (False, "已停止下载显卡引擎")

    with patch.object(TP, "install_engine", fake_install):
        page._install_engine_async("cuda")
        for _ in range(300):
            app.processEvents()
            if holder.get("dl") is not None:
                break
            time.sleep(0.01)

        chk("下载器已通过 on_downloader 交给页面", page._engine_dl is holder.get("dl"))
        chk("下载期间「停止」点亮", page.stop_btn.isEnabled())
        chk("下载期间「开始转写」禁用", not page.start_btn.isEnabled())
        chk("状态栏提示了可停止", "停止" in page.status.text(), page.status.text())

        page.stop_btn.click()  # ★ 真实按钮点击，不直接调槽

        chk("真点击确实取消了下载", bool(holder.get("dl") and holder["dl"].cancelled))
        chk("取消后清空 _engine_dl", page._engine_dl is None)
        chk("取消后标记 _engine_stopped", bool(page._engine_stopped))
        chk("取消后「停止」熄灭", not page.stop_btn.isEnabled())
        chk("取消后「开始转写」恢复", page.start_btn.isEnabled())
        chk("状态栏给了 CPU 出口", "CPU" in page.status.text(), page.status.text())
        chk("运行日志给了 CPU 出口", "CPU" in page.log.toPlainText())

        gate.set()
        for _ in range(400):
            app.processEvents()
            if page._install_worker.isFinished():
                break
            time.sleep(0.01)
        for _ in range(20):
            app.processEvents()

        chk("Worker 正常收尾", page._install_worker.isFinished())
        chk("用户自己停止后不再弹失败框", not exec_calls,
            f"弹了 {len(exec_calls)} 次")
        chk("收尾后按钮复位（停止灰 / 开始亮）",
            not page.stop_btn.isEnabled() and page.start_btn.isEnabled())
        chk("收尾后清空 _engine_dl", page._engine_dl is None)

    # ── 场景 2：下载失败弹窗里点「改用 CPU 转写」 ─────────────────────
    exec_calls.clear()
    boot_calls.clear()
    page.device.setCurrentIndex(1)
    page._engine_stopped = False
    with patch.object(QMessageBox, "exec", fake_exec), \
         patch.object(QMessageBox, "clickedButton", fake_clicked):
        page._on_engine_installed("cuda", (False, "镜像都连不上"))

    chk("失败时弹出可操作弹窗", len(exec_calls) == 1, f"实际 {len(exec_calls)}")
    if exec_calls:
        box = exec_calls[0]
        chk("弹窗不再说软件坏了", "可以照常转写" in box.windowTitle(), box.windowTitle())
        chk("弹窗有「改用 CPU 转写」按钮",
            any("CPU" in b.text() for b in box.buttons()),
            str([b.text() for b in box.buttons()]))
    chk("点了 CPU 按钮会释放自带 CPU 引擎", "cpu" in boot_calls, str(boot_calls))
    chk("点了 CPU 按钮会切回 CPU 设备", page.device.currentIndex() == 0,
        f"实际 {page.device.currentIndex()}")
    chk("状态栏确认已切 CPU", "CPU" in page.status.text(), page.status.text())

    # ── 场景 3：不在下载态时，「停止」仍是停止转写 ─────────────────────
    page._engine_dl = None
    page._running = True
    page.stop_btn.setEnabled(True)
    page.stop_btn.click()
    chk("非下载态点停止＝停止转写", page._running is False)
    chk("非下载态状态栏写停止信号", "停止信号" in page.status.text(),
        page.status.text())

    # ── 场景 4：槽里抛异常不能变成「点了没反应」 ──────────────────────
    # 下面会故意抛一次异常，connect_guarded 按设计把 traceback 打到 stderr，
    # 屏幕上出现一段红色栈信息属预期，不是探针崩了。
    print("（下一段 traceback 是故意注入的槽异常，用于验证兜底，属预期）")
    orig_stop = page._stop

    def boom():
        raise RuntimeError("模拟槽内部异常")

    page._stop = boom
    page.stop_btn.setEnabled(True)
    page.stop_btn.click()
    chk("槽抛异常会写进状态栏而非静默卡死", "停止按钮出错" in page.status.text(),
        page.status.text())
    page._stop = orig_stop

    page.hide()

print("=" * 66)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print(f"【失败 {_FAILED} 项】转写引擎逃生出口不可用，先修好再发版。")
    sys.exit(1)
print("转写引擎停止 / CPU 出口离屏链路全绿")
sys.exit(0)
