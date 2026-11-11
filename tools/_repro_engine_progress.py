# -*- coding: utf-8 -*-
"""复现 / 验收：转写引擎下载「进度倒退 / 卡在 89%」。

模拟一个慢镜像：某个分块第一次请求只发一半就被掐断，客户端重试才拿到完整块。
对照截图里 90%→88%→91%→89% 的来回震荡。

修复前用来复现病征，修复后作为验收脚本：进度不许虚高 / 倒退、文件必须完整。
任一断言不满足时退出码为 1。不联网、不改用户目录，纯本地假 HTTP 服务。
"""
import hashlib
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from justoneapi_collector import transcribe as T  # noqa: E402

BLOCK = 2 * 1024 * 1024
# ★ 必须 >16MB：_try_one 里 total > 16MB 才开 8 线程分块并发，
#   小于这个阈值走单线程顺序分支，复现不到客户那种多源并发震荡。
_PAT = bytes((i * 7 + 3) % 256 for i in range(65536))
PAYLOAD = (_PAT * ((BLOCK * 9 + 12345) // 65536 + 1))[:BLOCK * 9 + 12345]
TOTAL = len(PAYLOAD)
MD5 = hashlib.md5(PAYLOAD).hexdigest()

# 第 3 块（0-based）第一次请求掐断
CUT_BLOCK = 3
CUT_ONCE = {"done": False}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_HEAD(self):
        self._hdr(TOTAL)

    def do_GET(self):
        rng = self.headers.get("Range")
        if not rng:
            self._hdr(TOTAL)
            try:
                self.wfile.write(PAYLOAD)
            except Exception:
                pass
            return
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        s = int(m.group(1))
        e = int(m.group(2)) if m.group(2) else TOTAL - 1
        e = min(e, TOTAL - 1)
        data = PAYLOAD[s:e + 1]
        idx = s // BLOCK
        cut = (idx == CUT_BLOCK and not CUT_ONCE["done"] and len(data) > 1024)
        if cut:
            CUT_ONCE["done"] = True
            half = len(data) // 2
            # 声明完整长度，只发一半后主动断连 —— 模拟镜像中途把连接掐了
            self._hdr(len(data), 206, f"bytes {s}-{e}/{TOTAL}")
            try:
                self.wfile.write(data[:half])
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True
            return
        self._hdr(len(data), 206, f"bytes {s}-{e}/{TOTAL}")
        try:
            self.wfile.write(data)
        except Exception:
            pass


def run():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/engine.zip"
    tmp = Path(tempfile.mkdtemp(prefix="jo_repro_"))
    dest = tmp / "engine.zip"

    seen = []
    dl = T.Downloader(threads=4, stall_timeout=2.0)
    ok, err = dl.download([url], dest, progress=lambda d, t: seen.append(d))
    srv.shutdown()

    over = [d for d in seen if d > TOTAL]
    drops = [(a, b) for a, b in zip(seen, seen[1:]) if b < a]
    print("=" * 66)
    print(f"下载结果 ok={ok} err={err!r}")
    print(f"文件正确 = {dest.exists() and hashlib.md5(dest.read_bytes()).hexdigest() == MD5}")
    print(f"总量 TOTAL = {TOTAL}  最终 _done = {dl._done}  超出 = {dl._done - TOTAL}")
    print(f"进度回调 {len(seen)} 次，最小 {min(seen) if seen else '-'}，最大 {max(seen) if seen else '-'}")
    print(f"进度 > 总量 的次数 = {len(over)}  {over[:6]}")
    print(f"进度倒退次数 = {len(drops)}  {drops[:6]}")
    print("=" * 66)
    good = bool(ok) and dest.exists() and \
        hashlib.md5(dest.read_bytes()).hexdigest() == MD5 and \
        dl._done == TOTAL and not over and not drops
    if dl._done > TOTAL:
        print("【未通过】重试时 _done 重复累加 → 进度虚高；换源按已完成块重算 → 倒退")
    if drops:
        print("【未通过】进度序列出现倒退")
    # 本场景只掐断一次、重试即补完，本来就不该触发停滞换源；
    # 停滞检测是否按块数判定，由 _validate_engine_download.py 的挂连接场景覆盖。
    print(f"停滞检测触发 = {bool(dl._stalled)}（本场景重试能补完，不触发属正常）")
    print(f"[场景 1] {'通过' if good else '未通过'}")
    return good


def run_switch():
    """场景 2：源 A 有一块始终拿不全 → 换源 B。观察进度是否倒退、停滞是否失灵。"""
    class HB(H):
        """源 B：完整、慢速发数据。"""
        def do_GET(self):
            rng = self.headers.get("Range")
            if not rng:
                self._hdr(TOTAL)
                try:
                    self.wfile.write(PAYLOAD)
                except Exception:
                    pass
                return
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            s = int(m.group(1))
            e = int(m.group(2)) if m.group(2) else TOTAL - 1
            e = min(e, TOTAL - 1)
            data = PAYLOAD[s:e + 1]
            self._hdr(len(data), 206, f"bytes {s}-{e}/{TOTAL}")
            try:
                for i in range(0, len(data), 65536):
                    self.wfile.write(data[i:i + 65536])
                    self.wfile.flush()
            except Exception:
                pass

    class HA(H):
        """源 A：第 3 块永远只发一半，其它块慢速正常。"""
        def do_GET(self):
            rng = self.headers.get("Range")
            if not rng:
                self._hdr(TOTAL)
                return
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            s = int(m.group(1))
            e = int(m.group(2)) if m.group(2) else TOTAL - 1
            e = min(e, TOTAL - 1)
            data = PAYLOAD[s:e + 1]
            if s // BLOCK == CUT_BLOCK:
                half = len(data) // 2
                self._hdr(len(data), 206, f"bytes {s}-{e}/{TOTAL}")
                try:
                    self.wfile.write(data[:half])
                    self.wfile.flush()
                except Exception:
                    pass
                self.close_connection = True
                return
            self._hdr(len(data), 206, f"bytes {s}-{e}/{TOTAL}")
            try:
                for i in range(0, len(data), 65536):
                    self.wfile.write(data[i:i + 65536])
                    self.wfile.flush()
            except Exception:
                pass

    sa = ThreadingHTTPServer(("127.0.0.1", 0), HA)
    sb = ThreadingHTTPServer(("127.0.0.1", 0), HB)
    threading.Thread(target=sa.serve_forever, daemon=True).start()
    threading.Thread(target=sb.serve_forever, daemon=True).start()
    ua = f"http://127.0.0.1:{sa.server_port}/engine.zip"
    ub = f"http://127.0.0.1:{sb.server_port}/engine.zip"
    tmp = Path(tempfile.mkdtemp(prefix="jo_repro2_"))
    dest = tmp / "engine.zip"

    seen = []
    dl = T.Downloader(threads=4, stall_timeout=3.0)
    t0 = __import__("time").time()
    ok, err = dl.download([ua, ub], dest, progress=lambda d, t: seen.append(d))
    elapsed = __import__("time").time() - t0
    sa.shutdown()
    sb.shutdown()

    drops = [(a, b) for a, b in zip(seen, seen[1:]) if b < a]
    over = [d for d in seen if d > TOTAL]
    print("=" * 66)
    print(f"[换源场景] ok={ok} err={err!r} 耗时={elapsed:.1f}s")
    print(f"文件正确 = {dest.exists() and hashlib.md5(dest.read_bytes()).hexdigest() == MD5}")
    print(f"进度回调 {len(seen)} 次  最大 {max(seen) if seen else '-'} / 总量 {TOTAL}")
    print(f"进度 > 总量 = {len(over)} 次 {over[:5]}")
    print(f"进度倒退 = {len(drops)} 次 {drops[:5]}")
    print(f"停滞检测触发 = {dl._stalled}")
    print("=" * 66)
    good = bool(ok) and dest.exists() and \
        hashlib.md5(dest.read_bytes()).hexdigest() == MD5 and \
        not over and not drops
    if drops:
        print("【未通过】换源时进度倒退 → 客户看到 90%→88%→91%→89%")
    if over:
        print("【未通过】进度超过 100% → _pump 会提前 return，界面直接卡住")
    # 源 B 能补完就不必触发停滞；真正「连接挂住仍要换源」由新门禁覆盖。
    print(f"[场景 2] {'通过' if good else '未通过'}")
    return good


if __name__ == "__main__":
    a = run()
    b = run_switch()
    print("=" * 66)
    print(f"修复后验收：{'通过（进度单调、无虚高、文件完整）' if a and b else '未通过'}")
    sys.exit(0 if a and b else 1)
