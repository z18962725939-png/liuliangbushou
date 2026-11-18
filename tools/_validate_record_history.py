# -*- coding: utf-8 -*-
"""录制历史 / 回放门禁（2026-11-18）。

★ 为什么要有这个脚本
  「录完就撒手不管」是纯体验缺口：录制链路全绿、文件确实生成了，但软件
  认不出它，客户想回看时找不到入口。这类问题编译、冒烟、功能门禁都抓不到。
  这里钉两件事：
    ① 扫描模块行为正确（排除 .part / 空目录容错 / 只读 / 按时间倒序）；
    ② UI 接线没断（历史表存在、双击打开、停止后刷新、空态登记、系统播放器）。

跑法：C:/Python310/python.exe tools/_validate_record_history.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time
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


print("\n== 1. 扫描模块行为 ==")
from justoneapi_collector.record_history import (  # noqa: E402
    format_size, scan_recordings, latest_recording)

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "主播A.mp4").write_bytes(b"x" * 1000)
    time.sleep(0.02)
    (d / "主播B.mp4").write_bytes(b"y" * 2000)
    (d / "残片.mp4.part").write_bytes(b"z" * 50)     # 录制中残片，必须排除
    (d / "封面.jpg").write_bytes(b"img")             # 非视频，必须排除
    (d / "子目录").mkdir()

    items = scan_recordings(d)
    check("只列 mp4，排除 .part/图片/目录",
          len(items) == 2 and {i.name for i in items} == {"主播A.mp4", "主播B.mp4"},
          str([i.name for i in items]))
    check("按修改时间倒序（最新在前）",
          bool(items) and items[0].name == "主播B.mp4",
          items[0].name if items else "空")
    check("记录带绝对路径且文件真实存在",
          all(Path(i.path).is_file() for i in items))
    check("带人类可读体积", bool(items) and items[0].size_text.endswith("KB"),
          items[0].size_text if items else "空")
    check("latest_recording 取最新一条",
          latest_recording(d) is not None and latest_recording(d).name == "主播B.mp4")

    # 只读性：扫描前后目录内容不变
    before = sorted(p.name for p in d.iterdir())
    scan_recordings(d)
    after = sorted(p.name for p in d.iterdir())
    check("扫描只读，不改动任何文件", before == after)

check("空目录返回空列表", scan_recordings(tempfile.mkdtemp()) == [])
check("不存在的目录返回空列表", scan_recordings("/__no_such_dir__/xyz") == [])
check("None 输入返回空列表", scan_recordings(None) == [])
check("limit 生效", len(scan_recordings(
    d if 'd' in dir() else tempfile.mkdtemp(), limit=1)) <= 1)
check("体积格式化：B/KB/MB",
      format_size(0) == "0 B" and format_size(2048).endswith("KB")
      and format_size(1048576).endswith("MB"))

print("\n== 2. 纯标准库（不引 Qt/网络） ==")
try:
    tree = ast.parse(read("justoneapi_collector/record_history.py"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    check("仅依赖标准库",
          imported <= {"os", "sys", "dataclasses", "datetime", "pathlib", "__future__"},
          str(sorted(imported)))
    check("无 Qt / 无网络导入",
          not any(x in imported for x in ("PySide6", "requests", "httpx", "urllib")))
except (OSError, SyntaxError) as exc:
    check("record_history.py 可解析", False, repr(exc))

print("\n== 3. UI 接线 ==")
try:
    lp = read("gui_qt/pages/live_page.py")
    lp_tree = ast.parse(lp)
    check("live_page.py 可解析", True)
except (OSError, SyntaxError) as exc:
    check("live_page.py 可解析", False, repr(exc))
    lp = ""

if lp:
    check("有录制历史表 hist_table", "self.hist_table = QTableWidget" in lp)
    check("历史表表名含「录制历史」", "录制历史" in lp)
    check("扫描模块被调用", "scan_recordings" in lp)
    check("双击历史表打开回放",
          "hist_table.doubleClicked" in lp and "_open_history_selected" in lp)
    check("用系统默认播放器打开",
          "QDesktopServices.openUrl" in lp and "QUrl.fromLocalFile" in lp)
    check("有「打开保存目录」出口", "_open_save_dir" in lp)
    check("停止录制后刷新历史", "_refresh_history" in lp and "_on_stop_done" in lp)
    check("读 last_stopped 提示产物", "last_stopped" in lp)
    # 新按钮必须走 connect_guarded（工程铁律：禁裸 connect）
    check("历史区按钮走 connect_guarded",
          lp.count("connect_guarded") >= 10, str(lp.count("connect_guarded")))

print("\n== 4. 空态引导登记（防止历史表套用监控表文案） ==")
try:
    mw = read("gui_qt/main_window.py")
    check("历史表首列「文件名」有专属空态引导",
          '"文件名", "还没有录制的回放"' in mw)
    check("空态出口挂到打开保存目录",
          '"打开保存目录", "_open_save_dir"' in mw)
    check("列宽表登记录制时间列", '"录制时间"' in mw)
except OSError as exc:
    check("main_window.py 可读", False, repr(exc))

print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
