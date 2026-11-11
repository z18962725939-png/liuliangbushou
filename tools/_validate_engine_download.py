# -*- coding: utf-8 -*-
"""发版门禁：转写引擎下载进度 / 停滞 / 慢源兜底（v1.28.2）。

起因：客户截图「音视频转文本」页下载 439 MB CUDA 引擎卡在 85%~91%，
百分比 90→88→91→89 来回倒退、预计剩余时间反复横跳，永远下不完。

根因（tools/_repro_engine_progress.py 已复现）：
  1. `_fetch_range` 用 `_done += len(chunk)` 累计，某块被镜像掐断重试时
     半块字节被重复计数 → 进度虚高到 91%；换源按 `_done_blocks` 重算
     → 掉回真实值 88%，于是来回震荡。
  2. 停滞检测用 `done != last_done` 刷新计时器，重试让 done 一直变，
     60 秒换源保护被绕过 → 客户对着 89% 干等。
  3. 死镜像探测用 20 秒超时，光探测就白等一分钟。

本脚本用本地假 HTTP 服务覆盖：单调进度、不虚增、停滞能触发、换源不倒退、
整包退化、取消、慢速文案与停止出口。不联网、不写用户目录。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import transcribe as T  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def chk(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"{'OK  ' if cond else 'FAIL'} {name}" + (f"  — {extra}" if extra else ""))


# ---------------------------------------------------------------- 假服务
BLOCK = T.Downloader.BLOCK
_PAT = bytes((i * 7 + 3) % 256 for i in range(65536))
PAYLOAD = (_PAT * ((BLOCK * 9 + 12345) // 65536 + 1))[:BLOCK * 9 + 12345]
TOTAL = len(PAYLOAD)
MD5 = hashlib.md5(PAYLOAD).hexdigest()


class Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    support_range = True
    cut_block = -1          # 该块第一次请求掐断一半
    cut_from = -1           # 块号 >= 此值一律掐断（用于续传场景）
    always_cut = False      # 所有块都只发一半（用于停滞检测）
    hang = False            # 发一点字节后挂住连接（模拟慢镜像卡死）
    drag = 0.0              # 每 64KB 的拖延秒数

    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except Exception:
            pass

    def _hdr(self, n, code=200, crange=None):
        self.send_response(code)
        if crange:
            self.send_header("Content-Range", crange)
        self.send_header("Content-Length", str(n))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        self.end_headers()

    def _write(self, data, limit=None):
        body = data if limit is None else data[:limit]
        try:
            if self.drag:
                for i in range(0, len(body), 65536):
                    self.wfile.write(body[i:i + 65536])
                    self.wfile.flush()
                    time.sleep(self.drag)
            else:
                self.wfile.write(body)
            self.wfile.flush()
        except Exception:
            pass

    def do_HEAD(self):
        self._hdr(TOTAL)

    def do_GET(self):
        rng = self.headers.get("Range")
        if not rng or not self.support_range:
            self._hdr(TOTAL)
            self._write(PAYLOAD)
            return
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        s = int(m.group(1))
        e = int(m.group(2)) if m.group(2) else TOTAL - 1
        e = min(e, TOTAL - 1)
        data = PAYLOAD[s:e + 1]
        idx = s // BLOCK
        cut = (self.always_cut or (self.cut_from >= 0 and idx >= self.cut_from)
               or (idx == self.cut_block and not self.cut_once.get("done")))
        if idx == self.cut_block and not self.cut_once.get("done"):
            self.cut_once["done"] = True
        self._hdr(len(data), 206, f"bytes {s}-{e}/{TOTAL}")
        if cut and len(data) > 1024:
            self._write(data, len(data) // 2)
            self.close_connection = True
            return
        if self.hang:
            # 只吐一点点字节就挂住：客户端线程一直活着、字节偶尔动，
            # 但一个块都完成不了 —— 客户截图里 89% 卡十几分钟就是这么来的。
            self._write(data, 65536)
            time.sleep(30)
            self.close_connection = True
            return
        self._write(data)

    cut_once: dict = {"done": False}


def serve(**kw) -> tuple[ThreadingHTTPServer, str]:
    cls = type("H", (Base,), dict(kw, cut_once={"done": False}))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/engine.zip"


# ---------------------------------------------------------------- 静态判据
src = (ROOT / "justoneapi_collector" / "transcribe.py").read_text(encoding="utf-8")
page = (ROOT / "gui_qt" / "pages" / "transcribe_page.py").read_text(encoding="utf-8")

# 只看真正的代码行，别把注释里解释「老实现」的文字当成残留
code_lines = [ln for ln in src.splitlines()
              if ln.strip() and not ln.lstrip().startswith("#")]
code = "\n".join(code_lines)
chk("不再用 _done += 累计写入字节", "self._done +=" not in src)
chk("进度有唯一真相 _progress_bytes", "def _progress_bytes" in src)
chk("分块重试记账用覆盖式 _inflight", "self._inflight[block_idx] = pos - offset" in src)
chk("上报进度有 _peak 单调保护", "done = max(done, self._peak)" in src)
chk("停滞检测只看已完成块数", "nblocks = len(self._done_blocks)" in src
    and "if nblocks != self._stall_last_blocks:" in src)
chk("停滞计时跨轮保持（不会被每轮重置）", "_stall_last_t" in src
    and "self._stall_count += 1" in src)
chk("探测用短超时 probe_timeout", "timeout=self.probe_timeout" in src
    and "probe_timeout: float = 6.0" in src)
chk("速度改用窗口实测而非平均值", "speed = max(0.0, (done - state[\"last_done\"]) / span)" in src)
chk("install_engine 可交出 downloader 供取消", "on_downloader=None" in src
    and "on_downloader(dl)" in src)
chk("取消下载日志引导改用 CPU 引擎", "切到「CPU 转录」" in src)
chk("页面接线了停止下载", "_engine_dl" in page and "_stop_engine_download" in page)
chk("页面下载中启用停止按钮", "stop_btn.setEnabled(True)" in page)

# ---------------------------------------------------------------- 动态判据
_TMP = Path(tempfile.mkdtemp(prefix="jo_engdl_"))


def _content_ok(dest: Path) -> bool:
    return dest.exists() and hashlib.md5(dest.read_bytes()).hexdigest() == MD5


# 1) 某块被掐断重试：进度不得虚高、不得倒退、内容必须正确
srv1, url1 = serve(cut_block=3)
seen1: list[int] = []
d1 = T.Downloader(threads=4, stall_timeout=30.0)
ok1, err1 = d1.download([url1], _TMP / "a.zip", progress=lambda d, t: seen1.append(d))
srv1.shutdown()
chk("掐断重试后下载成功", ok1, err1)
chk("掐断重试后内容正确", _content_ok(_TMP / "a.zip"))
chk("进度不超过总量", all(0 <= d <= TOTAL for d in seen1), str(max(seen1, default=0)))
chk("进度序列无倒退", all(b >= a for a, b in zip(seen1, seen1[1:])))
chk("最终记账等于真实大小", d1._done == TOTAL, f"{d1._done} vs {TOTAL}")
chk("掐断重试不虚增字节", d1._progress_bytes(TOTAL) == TOTAL,
    str(d1._progress_bytes(TOTAL)))
chk("多次进度上报", len(seen1) >= 3, f"{len(seen1)} 次")

# 2) 换源：源 A 某块永远拿不全，源 B 完整 —— 进度不许倒退
srvA, urlA = serve(always_cut=True, drag=0.01)
srvB, urlB = serve()
seen2: list[int] = []
d2 = T.Downloader(threads=4, stall_timeout=4.0)
ok2, err2 = d2.download([urlA, urlB], _TMP / "b.zip",
                        progress=lambda d, t: seen2.append(d))
srvA.shutdown()
srvB.shutdown()
chk("换源后下载成功", ok2, err2)
chk("换源后内容正确", _content_ok(_TMP / "b.zip"))
chk("换源进度不倒退（客户截图症状）",
    all(b >= a for a, b in zip(seen2, seen2[1:])),
    f"{len(seen2)} 次上报")
chk("换源进度不超总量", all(d <= TOTAL for d in seen2))

# 3) 停滞检测：连接挂住、线程还活着、字节偶尔动，但一个块都完成不了。
#    download() 换源时会复位 _stalled，所以看累计触发次数 _stall_count。
#    用 daemon 服务线程，shutdown() 不必等挂住的连接（那些 30 秒 sleep）。
srvS, urlS = serve(hang=True)
d3 = T.Downloader(threads=4, stall_timeout=2.0, timeout=20.0)
t0 = time.time()
ok3, _ = d3.download([urlS], _TMP / "stall.zip")
elapsed = time.time() - t0
srvS.shutdown()
chk("连接挂住、无块完成仍能判定停滞", d3._stall_count >= 1,
    f"触发 {d3._stall_count} 次，耗时 {elapsed:.1f}s")
chk("停滞在阈值附近就换源，不拖十几分钟", elapsed < 20, f"耗时 {elapsed:.1f}s")
chk("停滞源不会假报成功", ok3 is False)

# 4) 服务端不支持 Range 时整包退化仍正确
srvN, urlN = serve(support_range=False)
d4 = T.Downloader(threads=4)
ok4, err4 = d4.download([urlN], _TMP / "norange.zip")
srvN.shutdown()
chk("不支持 Range 也能下载成功", ok4, err4)
chk("整包退化内容正确", _content_ok(_TMP / "norange.zip"))

# 5) 取消仍然有效
srvC, urlC = serve(drag=0.05)
d5 = T.Downloader(threads=4)
res: dict = {}


def _run_cancel():
    res["ok"], res["err"] = d5.download([urlC], _TMP / "cancel.zip")


th = threading.Thread(target=_run_cancel, daemon=True)
th.start()
time.sleep(0.8)
d5.cancel()
th.join(timeout=20)
srvC.shutdown()
chk("取消后返回失败", res.get("ok") is False, str(res.get("err")))
chk("取消后 downloader 标记已取消", d5.cancelled is True)

# 6) 断点续传：前半段能下、后半段始终失败 → 失败要保留 .part + .blocks，
#    再次点安装时从已完成块接着补，内容最终正确。
srvR1, urlR1 = serve(cut_from=4)
resume_dest = _TMP / "resume.zip"
part = resume_dest.with_suffix(resume_dest.suffix + ".part")
d6 = T.Downloader(threads=4, stall_timeout=60.0, timeout=3.0)
ok6, msg6 = d6.download([urlR1], resume_dest)
srvR1.shutdown()
chk("半途失败不会假报成功", ok6 is False)
chk("失败时保留已下载的 .part", part.exists())
chk("失败时写出块清单 .blocks", T.Downloader._blocks_file(part).exists())
chk("失败原因说明可以从断点接着下", "接着下" in msg6, msg6)
chk("确实存下了已完成块", len(d6._done_blocks) >= 4,
    f"{len(d6._done_blocks)} 块")

# 同一个目标文件、换个健康源重下 —— 应自动恢复并补缺的块
srvR2, urlR2 = serve()
d7 = T.Downloader(threads=4, stall_timeout=60.0)
ok7, err7 = d7.download([urlR2], resume_dest)
srvR2.shutdown()
chk("续传后下载成功", ok7, err7)
chk("续传后内容正确（块没有错位）", _content_ok(resume_dest))
chk("成功后清掉块清单，不留垃圾", not T.Downloader._blocks_file(part).exists())
chk("成功后不留 .part", not part.exists())

# ---------------------------------------------------------------- 汇总
print("=" * 68)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    for f in FAIL:
        print(f"  未通过：{f}")
    sys.exit(1)
print("引擎下载门禁全绿")
