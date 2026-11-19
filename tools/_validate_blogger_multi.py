# -*- coding: utf-8 -*-
"""发版门禁：多博主批量采集（2026-11-19 客户问「能不能同时添加多位博主采集」）。

客户端现象：博主拉取只有单行输入框，一次一位；手上有一整串主页链接时
得一位一位粘。底层浏览器登录会话只有一个，不能并发 —— 所以用「一个
Worker 串行逐个拉」实现。编译/冒烟抓不到这类静默退化，本门禁钉：

1. 串行 task 存在，且复用现有单次拉取函数（不另起一套解析逻辑）；
2. video_id 去重、某位失败不毁整批、进度 0→100 单调不回退；
3. UI 接线：多行输入框 + 按钮 + 独立 worker + 结果入表 + 按钮恢复；
4. 付费闸门：免费档不能白嫖采集接口。

不联网、不写真配置、不起真浏览器；下载器/Worker 全用假对象。
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TRAFFIC_NO_ANIM"] = "1"

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


print("== 1. 源码结构 ==")
PAGE = ROOT / "gui_qt" / "pages" / "video_download_page.py"
src = PAGE.read_text(encoding="utf-8")
try:
    tree = ast.parse(src)
except SyntaxError as exc:
    chk("页面源码可解析", False, repr(exc))
    tree = None

if tree is not None:
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    chk("串行任务函数存在", "_batch_bloggers_task" in funcs)
    chk("进度切片转发器存在", "_SegmentWorker" in classes)
    chk("批量入口槽存在", "_batch_fetch_bloggers" in funcs)
    chk("批量结果槽存在", "_on_batch_bloggers_done" in funcs)
    chk("批量收尾槽存在", "_on_batch_bloggers_finished" in funcs)

task_src = ast.get_source_segment(src, funcs["_batch_bloggers_task"]) if tree and "_batch_bloggers_task" in funcs else ""
chk("复用抖音单次拉取函数（不另造解析）", "_fetch_list_task(" in task_src)
chk("复用快手单次拉取函数（不另造解析）", "_fetch_ks_list_task(" in task_src)
chk("单博主异常被隔离（try/except 在循环内）",
    "except Exception" in task_src and "for i," in task_src)
chk("按 video_id 去重", 'getattr(v, "video_id"' in task_src and "seen" in task_src)
chk("有独立批量 worker 字段", "_batch_bloggers_worker" in src)
chk("多行输入框存在", "batch_urls_edit = QPlainTextEdit" in src)
chk("批量按钮接线走 connect_guarded",
    "connect_guarded(self.batch_fetch_btn.clicked" in src)

# 单次入口不能被改坏（门禁 _validate_blogger_batch 依赖它，这里再钉一层）
fetch_src = ast.get_source_segment(src, funcs["_fetch_list"]) if tree and "_fetch_list" in funcs else ""
chk("单次拉取入口仍在（原有用户不受影响）", bool(fetch_src))
chk("单次入口仍用 list_url_edit", "list_url_edit" in fetch_src)
chk("单次入口仍是 QLineEdit（不破坏既有门禁）", "list_url_edit = QLineEdit" in src)

print("\n== 2. 串行任务真实行为 ==")
from PySide6.QtWidgets import QApplication  # noqa: E402
from gui_qt.pages._base import AppContext  # noqa: E402
import gui_qt.pages.video_download_page as vdp  # noqa: E402
from justoneapi_collector.video_downloader import VideoInfo  # noqa: E402

app = QApplication.instance() or QApplication([])


class FakeDL:
    def resolve_sec_uid(self, text, allow_network=False):
        return ("sid_" + text[-3:], "")

    def list_user_list(self, kind, sec_uid, use_browser=False, max_pages=30,
                       on_progress=None, profile_url=""):
        if on_progress:
            on_progress(50)
            on_progress(100)
        if "bad" in (sec_uid or ""):
            return [], "模拟接口报错"
        return ([VideoInfo(author="A", desc="共通作品", video_id="v1"),
                 VideoInfo(author="A", desc="专属", video_id="uid_" + sec_uid)], "")


class P:
    def __init__(self):
        self.p = []

    def emit_progress(self, pct, msg=""):
        self.p.append((float(pct), msg))


p = P()
urls = ["https://douyin.com/user/bad", "https://douyin.com/user/good",
        "https://douyin.com/user/good2"]
vids, fails, ok, total = vdp._batch_bloggers_task(
    p, FakeDL(), urls, "post", "douyin", True)

chk("总数统计正确", total == 3, str(total))
chk("成功数统计正确（失败 1 位不影响其他）", ok == 2, str(ok))
chk("失败项带回原因", len(fails) == 1 and "模拟接口报错" in fails[0][1], str(fails))
chk("去重后作品数正确", len(vids) == 3, str([v.video_id for v in vids]))
chk("重复 video_id 只留一条",
    [v.video_id for v in vids].count("v1") == 1, str([v.video_id for v in vids]))
chk("进度 0→100", p.p[0][0] == 0 and p.p[-1][0] == 100, str(p.p[:1] + p.p[-1:]))
chk("进度单调不回退", all(p.p[i][0] <= p.p[i + 1][0]
                     for i in range(len(p.p) - 1)))
chk("进度不越界", all(0 <= x <= 100 for x, _ in p.p))

# 空输入 / 全失败的边界
v2, f2, o2, t2 = vdp._batch_bloggers_task(p, FakeDL(), ["", "   "], "post", "douyin", True)
chk("空输入安全返回", (v2, f2, o2, t2) == ([], [], 0, 0))

print("\n== 3. UI 接线真实行为 ==")


from PySide6.QtCore import QObject, Signal as QSignal  # noqa: E402


class FakeW(QObject):
    """带真 Qt 信号的假 Worker：connect_guarded 能正常接线。"""
    done = QSignal(object)
    progress = QSignal(float, str)
    error = QSignal(str)
    finished = QSignal()

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args, self._run = fn, args, False

    def isRunning(self):
        return self._run

    def emit_progress(self, pct, msg=""):
        self.progress.emit(float(pct), str(msg))

    def start(self):
        self._run = True
        res = self.fn(self, *self.args)
        self.done.emit(res)
        self.finished.emit()
        self._run = False


class UIDL(FakeDL):
    def list_user_list(self, kind, sec_uid, **kw):
        return [VideoInfo(author="A", desc="作品" + str(sec_uid),
                          video_id="uv" + str(sec_uid))], ""


ui = vdp.VideoDownloadPage(AppContext())
ui._ensure_real_downloader = lambda: True
ui.downloader = UIDL()
ui.batch_urls_edit.setPlainText("https://douyin.com/user/aa\n\nhttps://douyin.com/user/bb\n")
before = ui.table.rowCount()
with mock.patch.object(vdp, "Worker", FakeW):
    ui._batch_fetch_bloggers()
app.processEvents()
chk("结果入表（两位博主各一条）", ui.table.rowCount() - before == 2,
    str(ui.table.rowCount() - before))
chk("状态栏汇总成功数", "2/2 位博主成功" in ui.status.text(), ui.status.text())
chk("状态栏提示去重合并", "去重" in ui.status.text())
chk("按钮采集完恢复可点", ui.batch_fetch_btn.isEnabled()
    and ui.batch_fetch_btn.text() == "批量采集博主")

# 空输入：不 Worker、给可操作提示（spy 计数，避免 patch.new 没有 called）
ui.batch_urls_edit.clear()
ui.status.clear()
started = []

class SpyW(FakeW):
    def start(self):
        started.append(1)
        super().start()

with mock.patch.object(vdp, "Worker", SpyW):
    ui._batch_fetch_bloggers()
chk("空输入不启动任务", not started)
chk("空输入给可操作提示", "一行" in ui.status.text(), ui.status.text())

print("\n== 4. 付费闸门 ==")
gate = ast.get_source_segment(src, funcs["_batch_fetch_bloggers"]) if tree and "_batch_fetch_bloggers" in funcs else ""
chk("入口先确保真实下载器", "_ensure_real_downloader()" in gate)
chk("免费档提示付费而非白嫖", "_prompt_paid_feature" in gate)
chk("付费位复用既有「博主主页作品批量入库」",
    "博主主页作品批量入库" in gate)
ui.hide()

print("=" * 62)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print(f"【失败 {_FAILED} 项】多博主批量采集退化，先修好再发版。")
    sys.exit(1)
print("多博主批量采集门禁全绿")
sys.exit(0)
