# -*- coding: utf-8 -*-
"""内置浏览器登录模块单测：Cookie 处理 + 自研 WebSocket 编解码。

不依赖真实浏览器：WebSocket 部分用本地假服务端喂帧，
所以这套测试在无网络、无浏览器的机器上也能跑。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justoneapi_collector.browser_login import (  # noqa: E402
    _WSClient, CDPSession, cookies_to_header, cookie_summary, extract_ttwid,
    find_browser, find_cookie_value, free_port, is_logged_in, LOGIN_COOKIE_NAMES,
)

PASS, FAIL = [], []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✔' if cond else '✘'} {name}" + (f"  — {extra}" if extra else ""))


# ======================================================================
#  1) Cookie 处理
# ======================================================================

DOUYIN_COOKIES = [
    {"name": "ttwid", "value": "1%7Cabc", "domain": ".douyin.com", "path": "/"},
    {"name": "sessionid", "value": "sess123", "domain": ".douyin.com", "path": "/"},
    {"name": "s_v_web_id", "value": "verify_x", "domain": "www.douyin.com", "path": "/"},
    {"name": "udid", "value": "should_be_filtered", "domain": ".toutiao.com", "path": "/"},
    {"name": "empty_val", "value": None, "domain": ".douyin.com", "path": "/"},
]

hdr = cookies_to_header(DOUYIN_COOKIES)
chk("Cookie 头包含 ttwid", "ttwid=1%7Cabc" in hdr, hdr[:80])
chk("Cookie 头包含 sessionid", "sessionid=sess123" in hdr)
chk("非抖音域名被过滤", "udid" not in hdr)
chk("空值 Cookie 被跳过", "empty_val" not in hdr)
chk("同一域名下都保留", "s_v_web_id=verify_x" in hdr)

chk("识别已登录", is_logged_in(DOUYIN_COOKIES) is True)
chk("无登录 Cookie 判为未登录", is_logged_in(
    [{"name": "ttwid", "value": "x", "domain": ".douyin.com"}]) is False)
chk("空列表判为未登录", is_logged_in([]) is False)
chk("find_cookie_value 命中", find_cookie_value(DOUYIN_COOKIES, "sessionid") == "sess123")
chk("find_cookie_value 未命中返回空串", find_cookie_value(DOUYIN_COOKIES, "nope") == "")

chk("extract_ttwid 正确", extract_ttwid(hdr) == "ttwid=1%7Cabc",
    extract_ttwid(hdr))
chk("extract_ttwid 无则空串", extract_ttwid("sessionid=1; a=b") == "")

summ = cookie_summary(hdr)
chk("摘要含 Cookie 总数", "项 Cookie" in summ, summ)
chk("摘要标注登录态", "含登录态" in summ, summ)
chk("摘要标注 ttwid", "ttwid" in summ, summ)
chk("空 Cookie 摘要", cookie_summary("") == "无 Cookie")

# 同名 Cookie 后者覆盖前者（同域）
dup = cookies_to_header([
    {"name": "a", "value": "1", "domain": ".douyin.com"},
    {"name": "a", "value": "2", "domain": ".douyin.com"},
])
chk("同域同名去重（保留最后一个）", dup == "a=2", dup)


# ======================================================================
#  2) 端口工具
# ======================================================================

p1, p2 = free_port(), free_port()
chk("free_port 返回合法端口", 1024 < p1 < 65536 and 1024 < p2 < 65536, f"{p1},{p2}")
chk("find_browser 返回值合法",
    find_browser() is None or (isinstance(find_browser(), tuple)
                               and len(find_browser()) == 2),
    str(find_browser()))


# ======================================================================
#  3) WebSocket 编解码（本地假服务端）
# ======================================================================

def b64_accept(key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()


def server_send_text(conn, text, fragments=None):
    """按 fragments 切分发文本帧（fragments=None 表示单帧）。"""
    payload = text.encode("utf-8")
    parts = [payload] if not fragments else fragments

    def frame(opcode, data, fin):
        head = bytearray([(0x80 if fin else 0x00) | opcode])
        n = len(data)
        if n < 126:
            head.append(n)
        elif n < 65536:
            head.append(126)
            head += struct.pack(">H", n)
        else:
            head.append(127)
            head += struct.pack(">Q", n)
        conn.sendall(bytes(head) + data)

    if len(parts) == 1:
        frame(0x1, parts[0], True)
        return
    for i, part in enumerate(parts):
        frame(0x1 if i == 0 else 0x0, part, i == len(parts) - 1)


def run_fake_server(script):
    """script(conn) 在握手完成后执行。返回端口。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def handle():
        try:
            conn, _ = srv.accept()
            buf = b""
            while b"\r\n\r\n" not in buf:
                c = conn.recv(1024)
                if not c:
                    return
                buf += c
            text = buf.decode("latin-1", "replace")
            key = ""
            for line in text.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {b64_accept(key)}\r\n\r\n"
            ).encode())
            script(conn)
        except Exception:                  # noqa: BLE001
            pass
        finally:
            try:
                conn.close()
            except Exception:              # noqa: BLE001
                pass
            srv.close()

    threading.Thread(target=handle, daemon=True).start()
    return port


def read_client_frame(conn):
    """从客户端读一帧（客户端必须带掩码），返回 (opcode, payload 原文)。"""
    def recv_exact(n):
        b = b""
        while len(b) < n:
            c = conn.recv(n - len(b))
            if not c:
                raise ConnectionError("closed")
            b += c
        return b

    b1, b2 = recv_exact(2)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(8))[0]
    mask = recv_exact(4) if masked else b""
    data = recv_exact(length) if length else b""
    if masked and data:
        data = bytes(x ^ mask[i % 4] for i, x in enumerate(data))
    return opcode, data


# --- 3.1 单帧文本 ---
captured = {}


def s_single(conn):
    op, data = read_client_frame(conn)
    captured["op"] = op
    captured["sent"] = json.loads(data.decode("utf-8"))
    server_send_text(conn, json.dumps({"id": 1, "result": {"cookies": []}}))


port = run_fake_server(s_single)
ws = _WSClient(f"ws://127.0.0.1:{port}/devtools/browser/x", timeout=5)
ws.connect()
chk("WebSocket 握手成功（101）", True)
ws.send_text(json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}}))
got = json.loads(ws.recv_text())
chk("客户端发出的帧带掩码且 opcode=1", captured["op"] == 0x1, str(captured["op"]))
chk("客户端发出的 JSON 正确", captured["sent"]["method"] == "Storage.getCookies")
chk("能正确读到服务端响应", got["result"] == {"cookies": []})
ws.close()

# --- 3.2 分片消息（大 Cookie 列表会分片，必须拼回来）---
big = json.dumps({"id": 2, "result": {"blob": "A" * 200 + "B" * 150}})


def s_frag(conn):
    read_client_frame(conn)
    payload = big.encode("utf-8")
    server_send_text(conn, big, fragments=[payload[:100], payload[100:210], payload[210:]])


port = run_fake_server(s_frag)
ws = _WSClient(f"ws://127.0.0.1:{port}/x", timeout=5)
ws.connect()
ws.send_text("{}")
msg = ws.recv_text()
chk("分片消息被完整拼接（FIN 位处理正确）", msg == big,
    f"len={len(msg)} expect={len(big)}")
ws.close()

# --- 3.3 ping 帧要被 pong 掉，且不能干扰文本消息 ---
pong_seen = {}


def s_ping(conn):
    read_client_frame(conn)
    conn.sendall(b"\x89\x04ping")          # ping
    time.sleep(0.1)
    server_send_text(conn, '{"id":3,"result":{"ok":true}}')
    op, data = read_client_frame(conn)     # 期待收到 pong
    pong_seen["op"] = op
    pong_seen["data"] = data


port = run_fake_server(s_ping)
ws = _WSClient(f"ws://127.0.0.1:{port}/x", timeout=5)
ws.connect()
ws.send_text("{}")
msg = ws.recv_text()
chk("ping 不影响消息读取", json.loads(msg)["result"] == {"ok": True}, msg)
time.sleep(0.4)
chk("收到 ping 会自动回 pong", pong_seen.get("op") == 0xA and pong_seen.get("data") == b"ping",
    str(pong_seen))
ws.close()

# --- 3.4 非 ws:// 地址要明确报错 ---
try:
    _WSClient("http://127.0.0.1:1/x")
    chk("非 ws 地址应抛错", False)
except ValueError:
    chk("非 ws 地址应抛错", True)


# ======================================================================
#  4) CDPSession：call 要跳过事件消息，只认自己的 id
# ======================================================================

def s_cdp(conn):
    read_client_frame(conn)
    # 先发两条事件（没有 id），再发真正的响应
    server_send_text(conn, json.dumps({"method": "Target.targetCreated",
                                       "params": {"x": 1}}))
    server_send_text(conn, json.dumps({"method": "Target.targetInfoChanged",
                                       "params": {"x": 2}}))
    server_send_text(conn, json.dumps({"id": 1, "result": {"cookies": DOUYIN_COOKIES}}))


port = run_fake_server(s_cdp)
sess = CDPSession(port, timeout=5)
sess.ws = _WSClient(f"ws://127.0.0.1:{port}/x", timeout=5)
sess.ws.connect()
res = sess.call("Storage.getCookies", {})
chk("call 跳过事件消息只取 id 匹配的响应",
    len(res.get("cookies") or []) == len(DOUYIN_COOKIES), str(res)[:80])
sess.close()


# ======================================================================
#  5) 端到端：假 CDP 服务端 + DouyinLoginSession.poll 判定
# ======================================================================

from justoneapi_collector.browser_login import (  # noqa: E402
    DouyinLoginSession, default_profile_dir,
)


class _FakeSess:
    """替身：只实现 poll 依赖的 get_all_cookies。"""

    def __init__(self, cookies):
        self._c = cookies
        self.closed = False

    def get_all_cookies(self):
        return self._c

    def close(self):
        self.closed = True


import tempfile  # noqa: E402
import shutil  # noqa: E402

_TMP_DIRS: list[str] = []


def tmpdir(prefix: str) -> str:
    """创建临时目录并登记，测试结束统一清理（浏览器 profile 动辄几十 MB）。"""
    d = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(d)
    return d


dl = DouyinLoginSession(default_profile_dir(tmpdir("jo_prof_")))
dl.session = _FakeSess([{"name": "ttwid", "value": "t", "domain": ".douyin.com"}])
st, ck, info = dl.poll()
chk("未登录时 poll 返回 waiting", st == "waiting", st)

dl.session = _FakeSess(DOUYIN_COOKIES)
st, ck, info = dl.poll()
chk("已登录时 poll 返回 logged_in", st == "logged_in", st)
chk("poll 返回的 Cookie 头可用", "sessionid=sess123" in ck, ck[:60])
chk("poll 返回 ttwid", info.get("ttwid") == "ttwid=1%7Cabc", str(info))


class _BrokenSess:
    def get_all_cookies(self):
        raise ConnectionError("boom")


dl.session = _BrokenSess()
st, _ck, info = dl.poll()
chk("底层出错时 poll 返回 dead", st == "dead", st)
chk("dead 时带回错误原因", "boom" in (info.get("error") or ""), str(info))

dl.session = None
st, _ck, _info = dl.poll()
chk("无会话时 poll 返回 dead", st == "dead", st)

chk("default_profile_dir 指向 browser_profile",
    default_profile_dir("C:/x").name == "browser_profile")


for d in _TMP_DIRS:
    shutil.rmtree(d, ignore_errors=True)

print()
print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项: " + ", ".join(FAIL))
    sys.exit(1)
