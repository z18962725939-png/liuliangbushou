# -*- coding: utf-8 -*-
"""v1.19.1 端到端：真起一个本地 HTTP 服务，走完整下载链路。

覆盖三种客户现场：
  A 正常目录                       → 落到原名
  B 目标文件被杀软/播放器占成只读   → 自动换短名，仍然成功
  C 整个目录没有写入权限            → 自动改存到备用目录，仍然成功

跑法：python tools/_validate_download_flow.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector.video_downloader import (   # noqa: E402
    VideoDownloader, VideoInfo, writable_problem,
)

PASS, FAIL = [], []
PAYLOAD = b"FAKE-MP4-" + b"x" * 4096


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if not ok else ""))


class H(BaseHTTPRequestHandler):
    def do_GET(self):                                   # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):                          # 静音
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{port}/v.mp4"

tmp = Path(tempfile.mkdtemp(prefix="tc_flow_"))
video = VideoInfo(video_id="7697306010482491752",
                  desc="#护膝 #抖音入手的好东西",
                  author="杰哥外贸品牌清货",
                  no_watermark_url=URL)
expect_name = "杰哥外贸品牌清货_#护膝 #抖音入手的好东西_7697306010482491752.mp4"

print("=" * 62)
print("v1.19.1 下载链路端到端（本地 HTTP，不联网、不耗额度）")
print("=" * 62)


def new_dl(save_dir: Path) -> VideoDownloader:
    d = VideoDownloader(None, save_dir=save_dir)
    d.save_dir = save_dir
    d.last_save_dir_used = save_dir
    d.last_dir_note = ""
    d._probe_cache.clear()
    return d


# ---------------- A 正常目录 ----------------
a_dir = tmp / "A正常"
a_dir.mkdir(parents=True)
dl = new_dl(a_dir)
path, err = dl.download_video(video)
check("A 正常目录下载成功", path is not None and not err, str(err))
check("A 落盘字节数一致", path is not None and path.stat().st_size == len(PAYLOAD))
check("A 用的是原名", path is not None and path.name == expect_name,
      path.name if path else "")
check("A 无需改存提示", dl.last_dir_note == "", dl.last_dir_note)

# ---------------- B 目标文件被占成只读 ----------------
b_dir = tmp / "B只读文件"
b_dir.mkdir(parents=True)
target = b_dir / expect_name
target.write_bytes(b"")
os.chmod(target, 0o444)
try:
    subprocess.run(["attrib", "+R", str(target)], capture_output=True)
except OSError:
    pass
dl = new_dl(b_dir)
path, err = dl.download_video(video)
print("      B 结果：", path, "|", err)
if err == "" and path is not None:
    check("B 只读目标文件被绕过（改成短名后成功）",
          path.name == "7697306010482491752.mp4", path.name)
    check("B 落盘字节数一致", path.stat().st_size == len(PAYLOAD))
    check("B 有换名说明", "写不进去" in dl.last_dir_note, dl.last_dir_note)
else:
    check("B 只读目标文件：要么绕过要么报清原因", "写入失败" in err, err)

# ---------------- C 目录无写入权限 ----------------
c_dir = tmp / "C无权限"
c_dir.mkdir(parents=True)
user = os.environ.get("USERNAME") or "Administrator"
subprocess.run(["icacls", str(c_dir), "/deny", f"{user}:(W)"],
               capture_output=True)
blocked = writable_problem(c_dir) != ""
if blocked:
    dl = new_dl(c_dir)
    path, err = dl.download_video(video)
    print("      C 结果：", path, "|", err)
    check("C 目录无权限时下载仍成功（自动改存）", path is not None and not err, str(err))
    check("C 没落在原目录", path is not None and c_dir not in path.parents, str(path))
    check("C 提示里写明原目录与去向",
          str(c_dir) in dl.last_dir_note and str(path.parent) in dl.last_dir_note
          if path else False, dl.last_dir_note)
    check("C 落盘字节数一致", path is not None and path.stat().st_size == len(PAYLOAD))
    check("C last_save_dir_used 指向新目录",
          dl.last_save_dir_used == (path.parent if path else None))
    # 清掉备用目录里刚落下的文件
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
else:
    print("  [SKIP] icacls 未能拦住写入，跳过 C 用例")

# ---------------- 清理 ----------------
subprocess.run(["icacls", str(c_dir), "/remove:d", user], capture_output=True)
for p in sorted(tmp.rglob("*"), key=lambda x: len(str(x)), reverse=True):
    try:
        os.chmod(p, 0o666)
    except OSError:
        pass
    try:
        p.unlink() if p.is_file() else p.rmdir()
    except OSError:
        pass
try:
    tmp.rmdir()
except OSError:
    pass
srv.shutdown()

print("=" * 62)
print(f"下载链路端到端：{len(PASS)} 项通过，{len(FAIL)} 项失败")
if FAIL:
    for f in FAIL:
        print("  失败：", f)
    sys.exit(1)
