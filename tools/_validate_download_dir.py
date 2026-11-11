# -*- coding: utf-8 -*-
"""v1.19.1 落盘链路自检：可写性预检 / 自动换目录 / 错误码人话化。

背景：客户截图只有一句「保存目录不可写(PermissionError)」，四条全失败，
界面给的三条建议一条都不沾边。这里把新逻辑逐条钉死。

跑法：python tools/_validate_download_dir.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector.video_downloader import (      # noqa: E402
    VideoDownloader, VideoInfo, _bad_windows_name, _oserror_reason,
    fallback_save_dirs, writable_problem,
)

_prepare_path = VideoDownloader._prepare_path

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASS.append(name)
        print(f"  [OK ] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _run_icacls(*args: str) -> bool:
    """icacls 的输出在中文系统是 GBK，直接 text=True 会 UnicodeDecodeError。"""
    r = subprocess.run(["icacls", *args], capture_output=True,
                       encoding="gbk", errors="replace")
    return r.returncode == 0


def _deny_write(d: Path) -> bool:
    """用 icacls 给当前用户加一条 Deny(W)，模拟"目录没有写入权限"。"""
    user = os.environ.get("USERNAME") or "Administrator"
    return _run_icacls(str(d), "/deny", f"{user}:(W)")


def _allow_write(d: Path) -> None:
    user = os.environ.get("USERNAME") or "Administrator"
    _run_icacls(str(d), "/remove:d", user)


print("=" * 62)
print("v1.19.1 落盘链路自检")
print("=" * 62)

tmp = Path(tempfile.mkdtemp(prefix="tc_dl_"))
blocked = tmp / "只读目录"
blocked.mkdir()

# ---- 1. 可写目录判对 ----
check("可写目录探测为「没问题」", writable_problem(tmp) == "")

# ---- 2. 建不出来的目录：说人话 ----
bad_parent = tmp / "a.txt"
bad_parent.write_text("x", encoding="utf-8")
r = writable_problem(bad_parent / "sub")
check("父级是文件时能报出原因（不是空字符串）", bool(r), r)
check("原因里带完整路径", str(bad_parent) in r or "路径" in r, r)

# ---- 3. 真·无写入权限：靠 icacls 拒绝写 ----
denied_ok = _deny_write(blocked)
if denied_ok:
    r = writable_problem(blocked)
    # 管理员账户可能绕过 Deny(W)（SE_BACKUP 等），绕过时这条判为跳过
    if r == "":
        print("  [SKIP] 当前会话权限过高，Deny(W) 被绕过，跳过「无权限」用例")
    else:
        check("无写入权限的目录被拦住", True)
        check("原因写明「没有写入权限」", "没有写入权限" in r, r)
        check("原因带错误码", "代码" in r, r)
else:
    print("  [SKIP] icacls 不可用，跳过无权限用例")

# ---- 4. OSError → 中文（按错误码分流）----
e = PermissionError(13, "denied")
e.winerror = 5
check("WinError 5 → 没有写入权限", "没有写入权限" in _oserror_reason(e, "D:/x"))
e2 = PermissionError(10013, "sock")
e2.winerror = 10013
check("WinError 10013 → 网络被拦（不再赖磁盘）",
      "网络被系统拦下" in _oserror_reason(e2, "D:/x"))
e3 = OSError(28, "full")
e3.winerror = 112
check("WinError 112 → 磁盘空间不足", "空间不足" in _oserror_reason(e3, "D:/x"))
e4 = OSError(206, "long")
e4.winerror = 206
check("WinError 206 → 路径过长", "路径过长" in _oserror_reason(e4, "D:/x"))
check("原因恒以「写入失败」开头（界面据此判断）",
      _oserror_reason(e, "D:/x").startswith("写入失败"))

# ---- 5. 文件名预检 ----
check("CON.mp4 判为保留名", _bad_windows_name("CON.mp4") == "是 Windows 保留名")
check("结尾是点判为非法", _bad_windows_name("abc.") != "")
check("正常名字判为合法", _bad_windows_name("正常_名字_123.mp4") == "")

long_name = "长" * 240 + ".mp4"
p, note = _prepare_path(tmp, tmp / long_name, "7697306010482491752")
check("超长文件名被换成短名", p.name == "7697306010482491752.mp4", p.name)
check("换名有说明", bool(note), note)
p2, note2 = _prepare_path(tmp, tmp / "正常.mp4", "1")
check("正常文件名原样保留", p2.name == "正常.mp4" and note2 == "")

# ---- 6. 备用目录都在用户可写区 ----
dirs = fallback_save_dirs()
check("备用目录不为空", len(dirs) >= 2, str(dirs))
check("备用目录都在用户主目录或 LOCALAPPDATA 下",
      all(str(d).lower().startswith(
          (str(Path.home()).lower(),
           (os.environ.get("LOCALAPPDATA") or "%%%").lower())) for d in dirs),
      str(dirs))

# ---- 7. 目录不可写时自动改存 ----
dl = VideoDownloader.__new__(VideoDownloader)      # 不跑 __init__（会建目录）
dl.save_dir = blocked
dl.last_dir_note = ""
dl.last_save_dir_used = blocked
dl._probe_cache = {}

if denied_ok and writable_problem(blocked):
    got, note = dl._resolve_save_dir("")
    check("原目录不可写时换到了别的目录", got != blocked, str(got))
    check("换目录后确实可写", writable_problem(got) == "", str(got))
    check("换目录有说明且写明原目录", str(blocked) in note, note)
    check("last_save_dir_used 指向新目录", dl.last_save_dir_used == got)
else:
    got, note = dl._resolve_save_dir("")
    check("可写目录不做改动", got == blocked and note == "", f"{got} / {note}")

# ---- 8. 探测结果有缓存，不重复建临时文件 ----
dl._probe_cache.clear()
dl._probe(tmp)
before = len(dl._probe_cache)
dl._probe(tmp)
check("同一目录只探测一次（有缓存）", len(dl._probe_cache) == before)

# ---- 清理 ----
if denied_ok:
    _allow_write(blocked)
for p in sorted(tmp.rglob("*"), key=lambda x: len(str(x)), reverse=True):
    try:
        p.unlink() if p.is_file() else p.rmdir()
    except OSError:
        pass
try:
    tmp.rmdir()
except OSError:
    pass

print("=" * 62)
print(f"落盘链路自检：{len(PASS)} 项通过，{len(FAIL)} 项失败")
if FAIL:
    for f in FAIL:
        print("  失败：", f)
    sys.exit(1)
