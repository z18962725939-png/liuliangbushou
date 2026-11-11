# -*- coding: utf-8 -*-
"""格式符与回调链自检（防「一直解析中」这类静默卡死）。

背景：2026-10-18 客户现场「一直显示解析中」，真因是 `_fmt_duration` 里
`f"{m:02D}"` —— `compile()` 能过、pyc 也对，但一执行就
`ValueError: Unknown format code 'D'`；异常发生在「解析成功」的回调里，
没人接，界面就永远停在「正在解析，请稍候…」。

本脚本做两件事：
  A. 静态：用 AST 找出所有 f-string 的格式说明符，逐个 `format()` 试跑，
     非法的直接报错（大小写写错、多余字符都在这里露馅）。
  B. 动态：离屏构造视频下载页，真跑一遍解析/下载的回调链，
     任何一步抛异常都算失败。

跑法：python tools/_validate_format_specs.py
"""
from __future__ import annotations

import ast
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP_PARTS = {"build", "dist", "_tmp_script_out", "_archive",
              "_douyin_live_recorder_src", ".venv", "__pycache__",
              "node_modules", "installer_output"}

# ★ 明确点名的弃用文件（不走目录排除，避免把整个目录都豁免掉）。
#   gui.py 是最早那版 tkinter 界面，已经被 gui_qt（PySide6）取代：
#     · 没进 PyInstaller spec，不参与打包；
#     · 运行时没有任何路径 import 它（只有两个 tkinter 冒烟测试引用，
#       而当前环境根本装不了 tkinter）；
#   2026-10-29 源码重建时它是按行号硬拼出来的，中段已经错乱（方法重复、
#   文档串没收尾），语法层面修不回来，也不值得修 —— 留着只是给后人看旧版
#   长什么样。等确认不再需要时整个删掉即可。
SKIP_FILES = {"justoneapi_collector/gui.py"}

passed: list[str] = []
failed: list[str] = []


def ok(msg: str):
    passed.append(msg)
    print(f"  [ OK ] {msg}")


def bad(msg: str):
    failed.append(msg)
    print(f"  [FAIL] {msg}")


# ---------------------------------------------------------------- A. 静态
def _iter_sources():
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_PARTS for part in p.parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES:
            continue
        yield p


def _spec_is_valid(spec: str) -> bool:
    """格式说明符是否合法。

    数字和字符串各试一次：`s` 只对字符串合法，`d` 只对数字合法，
    任一个成功就算写法没问题 —— 本检查只抓「写法非法」，
    不抓「类型不匹配」（那需要运行时类型信息，静态判不准）。
    """
    for probe in (0, 0.0, "0"):
        try:
            format(probe, spec)
            return True
        except (ValueError, TypeError):
            continue
    return False


def check_format_specs():
    """扫描 f-string 里的格式说明符。"""
    n_specs = 0
    for src_path in _iter_sources():
        try:
            tree = ast.parse(src_path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            bad(f"{src_path.relative_to(ROOT)} 语法错误：{e}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            for part in node.values:
                if not isinstance(part, ast.FormattedValue):
                    continue
                spec_node = part.format_spec
                if not isinstance(spec_node, ast.JoinedStr):
                    continue
                # 只校验不含嵌套占位符的常量说明符（嵌套的动态拼不出来）
                consts = [v.value for v in spec_node.values
                          if isinstance(v, ast.Constant)]
                if len(consts) != len(spec_node.values):
                    continue
                spec = "".join(str(c) for c in consts)
                if not spec:
                    continue
                # 含 % 的是 datetime 的 strftime 方言（f"{dt:%Y-%m-%d}"），
                # 由 __format__ 转交 strftime，不是 format() 的说明符
                if "%" in spec:
                    continue
                n_specs += 1
                if not _spec_is_valid(spec):
                    line = getattr(node, "lineno", 0)
                    bad(f"{src_path.relative_to(ROOT)}:{line} 非法格式符 "
                        f"':{spec}'（f-string）")
    if not failed:
        ok(f"f-string 格式符全部合法（共检查 {n_specs} 个）")


def check_strftime():
    """扫 strftime 格式串里的非法指令（%D 在 Windows 上不成立）。"""
    import re

    bad_directives = {"%D", "%e", "%k", "%l", "%P"}
    hits = 0
    pat = re.compile(r"""(?:strftime|strptime)\s*\(\s*([fr]?["'][^"']{0,80}["'])""")
    for src_path in _iter_sources():
        text = src_path.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            for m in pat.finditer(line):
                lit = m.group(1)
                for d in bad_directives:
                    if d in lit:
                        hits += 1
                        bad(f"{src_path.relative_to(ROOT)}:{i} strftime 里出现 "
                            f"'{d}'（Windows 不支持）：{lit}")
    if not hits:
        ok("strftime 指令无可疑项（%D / %e / %k / %l / %P）")


# ---------------------------------------------------------------- B. 动态
def check_callback_chain():
    """离屏真跑下载页的回调链。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    from gui_qt.pages.video_download_page import VideoDownloadPage, _fmt_duration

    # 1) 时长格式化：这就是事故点，各种边界都过一遍
    cases = [(0, "—"), (None, "—"), (32000, "00:32"), (192000, "03:12"),
             (3723000, "01:02:03"), ("", "—"), ("abc", "—")]
    for raw, want in cases:
        got = _fmt_duration(raw)
        if got == want:
            ok(f"_fmt_duration({raw!r}) → {got}")
        else:
            bad(f"_fmt_duration({raw!r}) = {got!r}，应为 {want!r}")

    # 2) 页面构造 + 解析回调
    page = VideoDownloadPage(None)
    info = SimpleNamespace(platform="douyin", desc="自检标题", duration=32000,
                           video_id="7345678901234567890")
    try:
        page._on_resolved((info, ""))
        if page.table.rowCount() >= 1:
            ok("解析成功回调能填进列表")
        else:
            bad("解析成功回调后列表仍为空")
        if "解析成功" in page.status.text():
            ok("解析成功回调会更新状态栏")
        else:
            bad(f"解析成功回调未更新状态栏：{page.status.text()!r}")
    except Exception:                                   # noqa: BLE001
        bad("解析成功回调抛异常：\n" + traceback.format_exc())

    try:
        page._on_resolved((None, "页面HTTP 403"))
        if "解析失败" in page.status.text():
            ok("解析失败回调会写明原因")
        else:
            bad(f"解析失败回调文案不对：{page.status.text()!r}")
    except Exception:                                   # noqa: BLE001
        bad("解析失败回调抛异常：\n" + traceback.format_exc())

    try:
        page._on_resolve_finished()
        if page.progress.maximum() == 100:
            ok("解析收尾会把进度条从忙碌态复位")
        else:
            bad("解析收尾未复位进度条")
    except Exception:                                   # noqa: BLE001
        bad("解析收尾抛异常：\n" + traceback.format_exc())

    # 3) 下载回调（成功/失败两条分支）
    try:
        page.downloader.last_save_dir_used = Path.cwd() / "_tmp_script_out" / "probe"
        page.downloader.last_dir_note = ""
        page._on_downloaded([(info, Path("x.mp4"), "")])
        if "下载完成" in page.status.text():
            ok("下载成功回调会更新状态栏")
        else:
            bad(f"下载成功回调文案不对：{page.status.text()!r}")
    except Exception:                                   # noqa: BLE001
        bad("下载成功回调抛异常：\n" + traceback.format_exc())

    # 4) 槽异常必须被兜住（guard 生效才会走到 _slot_failed）
    from gui_qt.pages._base import connect_guarded
    from PySide6.QtCore import QObject, Signal

    class _Emit(QObject):
        sig = Signal(object)

    seen = {}
    holder = _Emit()
    connect_guarded(holder.sig, lambda v: (_ for _ in ()).throw(RuntimeError("boom")),
                    on_error=lambda m: seen.setdefault("msg", m), label="自检")
    holder.sig.emit(None)
    if "自检出错" in seen.get("msg", ""):
        ok("connect_guarded 能兜住槽内异常并回传原因")
    else:
        bad(f"connect_guarded 未兜住异常：{seen}")


def main():
    print("== A. f-string 格式符 ==")
    check_format_specs()
    print("== A2. strftime 指令 ==")
    check_strftime()
    print("== B. 下载页回调链 ==")
    try:
        check_callback_chain()
    except Exception:                                   # noqa: BLE001
        bad("回调链自检无法完成：\n" + traceback.format_exc())

    print(f"\n通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        for f in failed:
            print(" -", f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
