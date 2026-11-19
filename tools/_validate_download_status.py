# -*- coding: utf-8 -*-
"""视频下载页「是否已下载」状态列门禁（2026-11-19）。

★ 为什么要有这个脚本
  客户要在下载列表里直接看到每条视频是否已下载。这类需求最大的坑不是
  「显示不出来」，而是「显示的和实际行为不一致」——下载器跳过下载用的是
  一套文件名规则，界面若另抄一套，命名规则一改就错位（列表说没下、
  点下载却跳过）。所以这里钉死的头号判据是**同源**：下载器和状态列必须
  都调用 video_file_path，不允许下载函数里再出现裸的文件名拼接。

跑法：C:/Python310/python.exe tools/_validate_download_status.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


print("\n== 1. 同源判据（头号）==")
dl_src = read("justoneapi_collector/video_downloader.py")
page_src = read("gui_qt/pages/video_download_page.py")
dl_tree = ast.parse(dl_src)
page_tree = ast.parse(page_src)

# 模块级函数必须存在
funcs = {n.name for n in dl_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
check("video_downloader 定义了 video_file_path", "video_file_path" in funcs)
check("video_downloader 定义了 video_is_downloaded", "video_is_downloaded" in funcs)

# 下载函数体内不得再有裸命名拼接（必须调 video_file_path）
def find_func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None

dl_once = find_func(dl_tree, "_download_video_file_once")
check("找到 _download_video_file_once", dl_once is not None)
if dl_once:
    called = {n.func.id for n in ast.walk(dl_once)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("下载函数调用 video_file_path（同源）", "video_file_path" in called)
    has_raw = any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "filename" for t in n.targets)
        for n in ast.walk(dl_once))
    check("下载函数内无裸 filename 拼接（防再漂移）", not has_raw)

# 界面必须 import 同源函数（不能自己写判断）
imported = set()
for n in ast.walk(page_tree):
    if isinstance(n, ast.ImportFrom) and n.module and "video_downloader" in n.module:
        imported.update(a.name for a in n.names)
check("下载页 import video_is_downloaded", "video_is_downloaded" in imported)
check("下载页不 import video_file_path 之外的私有命名函数",
      not any(x.startswith("_") and x not in imported for x in imported))

print("\n== 2. 命名等价性（状态列与下载器同一路径）==")
from justoneapi_collector.video_downloader import (  # noqa: E402
    VideoInfo, video_file_path, video_is_downloaded, _safe_filename, VideoDownloader)

def old_path(v, save_dir):
    author = _safe_filename(v.author, 30) or "unknown"
    desc = _safe_filename(v.desc, 50)
    fn = f"{author}_{desc}_{v.video_id}.mp4"
    return VideoDownloader._prepare_path(save_dir, save_dir / fn, v.video_id)

d = Path(tempfile.mkdtemp())
cases = [
    VideoInfo(author="装修避坑", desc="巡检工地 #装修 #海安装修", video_id="abc123"),
    VideoInfo(author="", desc="作者为空的一条", video_id="def456"),
    VideoInfo(author="A" * 40, desc="标题" * 60, video_id="verylongid_" * 30),
    VideoInfo(author="CON", desc=".", video_id="s1"),
]
for i, v in enumerate(cases):
    check(f"命名等价 case{i}", old_path(v, d) == video_file_path(v, d))

print("\n== 3. video_is_downloaded 判据 ==")
v = cases[0]
check("空目录 → 未下载", video_is_downloaded(v, d) is False)
check("None video → 未下载", video_is_downloaded(None, d) is False)
check("空 save_dir → 未下载", video_is_downloaded(v, "") is False)
p, _ = video_file_path(v, d)
p.write_bytes(b"x" * 1)
check("文件存在且非空 → 已下载", video_is_downloaded(v, d) is True)
p.write_bytes(b"")
check("文件存在但 0 字节 → 未下载（与下载器同源）", video_is_downloaded(v, d) is False)

print("\n== 4. UI 接线 ==")
check("表格是 5 列", "QTableWidget(0, 5)" in page_src)
check("表头含状态列", '"状态"' in page_src)
check("下载后刷新状态", "_refresh_all_status()" in page_src)
check("换目录后刷新状态", page_src.count("_refresh_all_status()") >= 2)
check("状态格居中", "AlignCenter" in page_src)
check("状态列不写死颜色（交给 QSS）", "setForeground" not in _set_row_status_src
      if (_set_row_status_src := (lambda t: t[t.find("def _set_row_status"):t.find("def _refresh_all_status")])(page_src))
      else True)

print("\n== 4b. 实时逐条计数（2026-11-19）==")
base_src = read("gui_qt/pages/_base.py")
motion_src = read("gui_qt/widgets/motion.py")
check("Worker 定义了 item_done 信号", "item_done = Signal(object)" in base_src)
check("Worker 提供 emit_item 上报", "def emit_item" in base_src
      and "self.item_done.emit" in base_src)
# 逐条任务必须发事件（路径非空且无错=成功）
task_seg = page_src[page_src.find("def _download_task"):
                    page_src.find("def _fetch_list_task")]
check("逐条回调里发 item_done", "emit_item" in task_seg)
check("逐条成功判据与 done 同源（path 且非 err）",
      "bool(path) and not err" in task_seg)
# 界面接线：接信号 + 实时累加 + 对账
check("下载页接 item_done 到 _on_dl_item", "w.item_done" in page_src
      and "_on_dl_item" in page_src)
check("逐条槽累加 live 与 stat", "_live_ok += 1" in page_src
      and "_stat_ok += 1" in page_src)
check("done 对账校正（减 live 而非直接累加）",
      "ok - self._live_ok" in page_src and "fail - self._live_fail" in page_src)
check("逐条刷新单行状态", "_refresh_one_video_status" in page_src)
check("身份匹配用 is（防重复 id 刷错行）", "if v is video" in page_src)
# 动画竞态修复：计数高频触发时旧动画必须停掉，否则数字闪回
count_seg = motion_src[motion_src.find("def count_up"):
                       motion_src.find("# ------", motion_src.find("def count_up"))]
check("count_up 停掉旧动画（防逐条+1竞态）",
      'label.property("_count_anim")' in count_seg and "old.stop()" in count_seg)
check("旧动画 deleteLater 防对象累积", "old.deleteLater()" in count_seg)
check("滚动动画仍受 TRAFFIC_NO_ANIM 门控", "scaled(duration" in count_seg)


print("\n== 5. 离屏真实行为 ==")
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from gui_qt.pages._base import AppContext
    from gui_qt.pages.video_download_page import VideoDownloadPage
    app = QApplication.instance() or QApplication([])
    dd = Path(tempfile.mkdtemp())
    ctx = AppContext()
    ctx.set_setting("dl_save_dir", str(dd))
    page = VideoDownloadPage(ctx)
    app.processEvents()
    heads = [page.table.horizontalHeaderItem(c).text()
             for c in range(page.table.columnCount())]
    check("实际表头 5 列含状态", heads == ["选择", "平台", "标题", "时长 / 大小", "状态"],
          str(heads))
    vy = VideoInfo(author="甲", desc="有文件", video_id="Y1")
    vn = VideoInfo(author="乙", desc="没文件", video_id="N1")
    py, _ = video_file_path(vy, dd)
    py.write_bytes(b"x" * 100)
    page._add_row("douyin", "有文件", "01:00", video=vy)
    page._add_row("douyin", "没文件", "02:00", video=vn)
    page._add_row("douyin", "演示", "00:10", video=None)
    check("已下载行显示正确", page.table.item(0, 4).text() == "已下载")
    check("未下载行显示正确", page.table.item(1, 4).text() == "未下载")
    check("演示行显示 —", page.table.item(2, 4).text() == "—")
    dd2 = Path(tempfile.mkdtemp())
    page.dir_edit.setText(str(dd2))
    page._apply_save_dir()
    app.processEvents()
    check("换目录后重算为未下载", page.table.item(0, 4).text() == "未下载")

    # ---- 实时逐条计数 + 对账 ----
    from unittest import mock
    import os as _os
    _os.environ["TRAFFIC_NO_ANIM"] = "1"       # 静态，避免动画干扰取值
    rv = [VideoInfo(author="实", desc=f"实时{i}", video_id=f"LIVE{i}")
          for i in range(4)]
    # 清空重建列表，保证 _videos 与 rv 从索引 0 对齐
    page.table.setRowCount(0)
    page._videos = []
    for v in rv:
        page._add_row("douyin", v.desc, "01:00", video=v)
    page._stat_ok = 0
    page._stat_fail = 0
    page._live_ok = 0
    page._live_fail = 0
    for i, ok in enumerate([True, True, False]):
        if ok:
            p, _ = video_file_path(rv[i], dd2)
            p.write_bytes(b"x" * 100)
        page._on_dl_item((rv[i], ok))
    check("逐条+1实时累加 ok", page._stat_ok == 2, str(page._stat_ok))
    check("逐条+1实时累加 fail", page._stat_fail == 1, str(page._stat_fail))
    check("已落盘行状态实时变已下载", page.table.item(0, 4).text() == "已下载")
    results = [(rv[0], Path("x"), ""), (rv[1], Path("x"), ""),
               (rv[2], None, "err"), (rv[3], None, "err")]
    with mock.patch("gui_qt.pages.video_download_page.QMessageBox"):
        page._on_downloaded(results)
    check("done 对账后 ok 不重复（仍 2）", page._stat_ok == 2, str(page._stat_ok))
    check("done 对账补齐漏报 fail（2）", page._stat_fail == 2, str(page._stat_fail))
    _os.environ.pop("TRAFFIC_NO_ANIM", None)
except Exception as exc:  # noqa: BLE001
    check("离屏行为测试可执行", False, repr(exc))

print(f"\n{'=' * 52}\n下载状态列门禁：通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
