# -*- coding: utf-8 -*-
"""自测：直播监控「添加即检查、在播即录」+ 添加返回值字段不再错位。

用桩引擎覆盖 6 条分支，不需要真实 API：
  rec_ok / offline / live_no_auto / live_no_ffmpeg / unknown / error
并单独校验 _on_add 把 sec_uid（而不是昵称）写进了表格 UserRole。

用法：python tools/_validate_live_probe.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_fail = []


def check(cond, msg):
    print(("  [OK]   " if cond else "  [FAIL] ") + msg)
    if not cond:
        _fail.append(msg)


def pump(app, seconds=2.5):
    """转动事件循环，等待 Worker 线程回主线程。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


class StubDaemon:
    """最小直播引擎替身：只实现 LivePage 会用到的方法。"""

    def __init__(self):
        self.st = None
        self.ffmpeg = "C:/ffmpeg/bin/ffmpeg.exe"
        self.record_result = (True, "started")
        self.record_calls = []
        self.raise_on_check = False

    def check_once(self, sec_uid):
        if self.raise_on_check:
            raise RuntimeError("模拟网络异常")
        return self.st

    def ffmpeg_path(self):
        return self.ffmpeg

    def auto_record(self, sec_uid):
        self.record_calls.append(sec_uid)
        return self.record_result

    # LivePage 的每秒 tick 会用到
    def is_recording(self):
        return False

    def get_targets(self):
        return []

    def add_target(self, *a, **k):
        return None

    def remove_target(self, *a, **k):
        return None


def main():
    from PySide6.QtWidgets import QApplication

    from gui_qt.theme import apply_theme
    from gui_qt.pages._base import AppContext
    from gui_qt.pages.live_page import LivePage
    from justoneapi_collector.live_monitor import LiveStatus

    app = QApplication(sys.argv)
    apply_theme(app, "light")

    ctx = AppContext()
    stub = StubDaemon()
    ctx.daemon = stub
    page = LivePage(ctx)

    def make_status(live: bool, nick="老桃带你玩", sec="web_rid:7695002154"):
        return LiveStatus(sec_uid=sec, nickname=nick, is_live=live,
                          live_status=1 if live else 0,
                          room_id="7695002154" if live else "0",
                          avatar_url="", follower_count=0, input_type="链接")

    print("=== 1) _on_add 字段不再错位（sec_uid 必须进 UserRole） ===")
    page.table.setRowCount(0)   # 清掉演示模式预置的示例行
    page._on_add({"ok": True, "nick": "老桃带你玩", "sec_uid": "web_rid:7695002154",
                  "src": "链接", "error": ""})
    pump(app, 2.5)
    it = page.table.item(0, 0)
    stored = str(it.data(0x0100) or "") if it is not None else ""  # Qt.UserRole == 0x0100
    check(stored == "web_rid:7695002154",
          f"表格 UserRole 存的是 sec_uid（实际：{stored!r}）")
    check(page.table.item(0, 1).text() == "链接", "来源列为「链接」")

    print("\n=== 2) 分支：在播 + 自动录制开 + ffmpeg 就绪 → 直接开录 ===")
    page.table.setRowCount(0)
    stub.st = make_status(True)
    page.auto_rec.setChecked(True)
    page._add_row("直播间7695002154", "链接", "检查中…", "web_rid:7695002154", "-", "-",
                  sec_uid="web_rid:7695002154")
    page._probe_after_add("web_rid:7695002154", "直播间7695002154")
    pump(app, 3.0)
    check(stub.record_calls == ["web_rid:7695002154"], "引擎收到了 auto_record 调用")
    check(page.table.item(0, 2).text() == "直播中", "直播状态刷新为「直播中」")
    check(page.table.item(0, 4).text() == "录制中", "录制状态刷新为「录制中」")
    check(page.table.item(0, 0).text() == "老桃带你玩", "真实昵称回填成功")
    check("已自动开始录制" in page.status.text(), f"状态提示正确：{page.status.text()}")

    print("\n=== 3) 分支：未开播 ===")
    stub.st = make_status(False)
    stub.record_calls.clear()
    page._probe_after_add("web_rid:7695002154")
    pump(app, 3.0)
    check(not stub.record_calls, "未开播时不会调用录制")
    check(page.table.item(0, 2).text() == "未开播", "直播状态为「未开播」")
    check("未开播" in page.status.text(), f"状态提示：{page.status.text()}")

    print("\n=== 4) 分支：在播但未勾「开播自动录制」 ===")
    stub.st = make_status(True)
    stub.record_calls.clear()
    page.auto_rec.setChecked(False)
    page._probe_after_add("web_rid:7695002154")
    pump(app, 3.0)
    check(not stub.record_calls, "未勾选时不自动录（需尊重用户开关）")
    check("未勾选" in page.status.text(), f"状态提示：{page.status.text()}")

    print("\n=== 5) 分支：在播 + 勾选了但缺 ffmpeg ===")
    stub.st = make_status(True)
    stub.ffmpeg = ""
    stub.record_calls.clear()
    page.auto_rec.setChecked(True)
    page._probe_after_add("web_rid:7695002154")
    pump(app, 3.0)
    check(not stub.record_calls, "缺 ffmpeg 时不尝试录制（避免必然失败）")
    check("ffmpeg" in page.status.text(), f"状态提示：{page.status.text()}")

    print("\n=== 6) 分支：接口返回 None / 抛异常 ===")
    stub.ffmpeg = "C:/ffmpeg/bin/ffmpeg.exe"
    stub.st = None
    page._probe_after_add("web_rid:7695002154")
    pump(app, 2.5)
    check("重试" in page.status.text(), f"未知状态给可操作提示：{page.status.text()}")

    stub.raise_on_check = True
    page._probe_after_add("web_rid:7695002154")
    pump(app, 2.5)
    check("检查失败" in page.status.text() or "失败" in page.status.text(),
          f"异常不吞掉，明确报错：{page.status.text()}")
    stub.raise_on_check = False

    print("\n" + ("全部通过 ✅" if not _fail else f"存在失败项 ❌ {len(_fail)} 条"))
    for f in _fail:
        print("  -", f)
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
