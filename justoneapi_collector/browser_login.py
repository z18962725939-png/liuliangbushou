# -*- coding: utf-8 -*-
"""内置浏览器 + 抖音扫码登录（Cookie 获取）。

为什么要这个东西
----------------
抖音对"没带 Cookie 的请求"风控很严：分享短链解析、视频详情、直播流地址
都可能因为缺 Cookie 而被拦。让用户在浏览器里登录一次抖音、程序把 Cookie
带进后续请求，成功率会明显提升。

实现思路（为什么不是"真·内嵌浏览器"）
------------------------------------
把 Chromium 内嵌进 tkinter 需要 CEF / WebView2 宿主进程，体积和复杂度
都不划算。这里用【专用 profile + Chrome DevTools Protocol】实现等效体验：

  1. 用独立的 user-data-dir 启动本机 Edge/Chrome（不碰用户自己的浏览器数据）
     → 相当于本工具自带一个浏览器，登录状态独立保存
  2. 通过 --remote-debugging-port 打开 CDP，用纯标准库实现的 WebSocket
     调 Storage.getCookies 读取 Cookie
  3. 轮询发现"已登录"的关键 Cookie 后自动保存，用户只需扫码，其余全自动

依赖：只用标准库（socket / struct / hashlib / base64 / json / subprocess）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ----------------------------------------------------------------------
#  常量
# ----------------------------------------------------------------------

# 抖音（向后兼容别名）
DOUYIN_HOME = "https://www.douyin.com/"
DOUYIN_LOGIN = "https://www.douyin.com/?recommend=1"

# 各平台"已登录"关键 Cookie 与域名（命中任意一个关键 Cookie 即视为已登录）。
# 名称取自各平台网页版实测，后续可按需补充。
LOGIN_COOKIE_NAMES = ("sessionid", "sessionid_ss", "sid_tt", "uid_tt", "passport_assist_user")

# 客户端需要带上的 Cookie 域名后缀（其余分析/风控类 Cookie 也一并带上，越像真浏览器越好）
COOKIE_DOMAIN_SUFFIX = "douyin.com"

# 多平台登录配置：扫码/手机登录页、登录态判定 Cookie、目标域名、是否国内被屏蔽。
# TikTok 在国内被墙，仅供海外网络环境的用户；按钮会标注「国内用户禁用」。
LOGIN_PLATFORMS: dict[str, dict] = {
    "douyin": {
        "label": "抖音",
        "home": "https://www.douyin.com/",
        "login": "https://www.douyin.com/?recommend=1",
        "cookie_names": ("sessionid", "sessionid_ss", "sid_tt", "uid_tt", "passport_assist_user"),
        "domain_suffix": "douyin.com",
        "domestic_blocked": False,
    },
    "xiaohongshu": {
        "label": "小红书",
        "home": "https://www.xiaohongshu.com/",
        "login": "https://www.xiaohongshu.com/login",
        "cookie_names": ("web_session", "a1", "webid", "customer-sso-sid"),
        "domain_suffix": "xiaohongshu.com",
        "domestic_blocked": False,
    },
    "kuaishou": {
        "label": "快手",
        "home": "https://www.kuaishou.com/",
        "login": "https://www.kuaishou.com/",
        "cookie_names": ("did", "kuaishou_webid", "userId", "kuaishou.server.web"),
        "domain_suffix": "kuaishou.com",
        "domestic_blocked": False,
    },
    "tiktok": {
        "label": "TikTok",
        "home": "https://www.tiktok.com/",
        "login": "https://www.tiktok.com/login",
        "cookie_names": ("sessionid", "sid_tt", "uid_tt", "sessionid_ss"),
        "domain_suffix": "tiktok.com",
        "domestic_blocked": True,   # 国内被屏蔽，仅供海外用户
    },
}


def platform_config(platform: str) -> dict:
    """返回某平台的登录配置，未知平台回退到抖音。"""
    return LOGIN_PLATFORMS.get(platform, LOGIN_PLATFORMS["douyin"])


# ----------------------------------------------------------------------
#  工具
# ----------------------------------------------------------------------


def free_port() -> int:
    """让系统分一个当前空闲的端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def port_busy(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _registry_exe(app_name: str) -> str | None:
    """从 Windows 注册表 App Paths 找 exe（覆盖非默认安装位置）。"""
    if os.name != "nt":
        return None
    try:
        import winreg                     # noqa: PLC0415
    except Exception:                     # noqa: BLE001
        return None
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_name}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as k:
                    path, _ = winreg.QueryValueEx(k, "")
                    if path and Path(path).is_file():
                        return path
            except OSError:
                continue
    return None


def _candidate_browsers() -> list[tuple[str, str]]:
    """返回 [(显示名, exe 路径候选)]，顺序即优先级：Edge 优先（Win10/11 必装）。"""
    pfs = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    return [
        ("Microsoft Edge", rf"{pf86}\Microsoft\Edge\Application\msedge.exe"),
        ("Microsoft Edge", rf"{pfs}\Microsoft\Edge\Application\msedge.exe"),
        ("Google Chrome", rf"{pfs}\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", rf"{pf86}\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", rf"{local}\Google\Chrome\Application\chrome.exe"),
    ]


def find_browser() -> tuple[str, str] | None:
    """找到可用的 Chromium 系浏览器。返回 (路径, 显示名) 或 None。"""
    if os.name == "nt":
        for app in ("msedge.exe", "chrome.exe"):
            p = _registry_exe(app)
            if p:
                return p, ("Microsoft Edge" if "edge" in p.lower() else "Google Chrome")
    for name, path in _candidate_browsers():
        try:
            if path and Path(path).is_file():
                return path, name
        except OSError:
            continue
    return None


def cookies_to_header(cookies: list[dict], domain_suffix: str = COOKIE_DOMAIN_SUFFIX) -> str:
    """把 CDP 返回的 Cookie 列表拼成 HTTP Cookie 头。

    只保留目标域名（抖音）的 Cookie；同名同域只保留一个（后出现的覆盖前者）。
    """
    picked: dict[tuple[str, str], str] = {}
    for c in cookies or []:
        try:
            name = c.get("name") or ""
            value = c.get("value")
            dom = (c.get("domain") or "").lstrip(".").lower()
        except AttributeError:
            continue
        if not name or value is None:
            continue
        if domain_suffix and domain_suffix not in dom:
            continue
        picked[(dom, name)] = value
    return "; ".join(f"{n}={v}" for (_d, n), v in picked.items())


def find_cookie_value(cookies: list[dict], name: str) -> str:
    for c in cookies or []:
        if (c.get("name") or "") == name:
            return c.get("value") or ""
    return ""


def is_logged_in(cookies: list[dict], cookie_names: tuple = LOGIN_COOKIE_NAMES) -> bool:
    """是否已登录：命中任一关键 Cookie 且值非空。

    cookie_names 按平台传入（见 LOGIN_PLATFORMS），默认抖音。
    """
    for nm in cookie_names:
        if find_cookie_value(cookies, nm):
            return True
    return False


def extract_ttwid(cookie_header: str) -> str:
    """从 Cookie 串里取 ttwid=xxx（直播流解析要用）。取不到返回空串。"""
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if part.startswith("ttwid="):
            return part
    return ""


# ----------------------------------------------------------------------
#  极简 WebSocket 客户端（仅用于本机 CDP，够用即可）
# ----------------------------------------------------------------------


class _WSClient:
    """能收发文本帧的最小 WebSocket 客户端。

    只服务本机 127.0.0.1 的 CDP，不做 TLS、不做压缩扩展。
    """

    def __init__(self, url: str, timeout: float = 10.0):
        # ws://127.0.0.1:9222/devtools/browser/xxxx
        if not url.startswith("ws://"):
            raise ValueError(f"只支持 ws:// 地址：{url}")
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.host = host or "127.0.0.1"
        self.port = int(port or 80)
        self.path = "/" + path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buf = b""

    # -- 连接 ---------------------------------------------------------

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode())
        header = self._read_until(b"\r\n\r\n")
        first = header.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if "101" not in first:
            raise ConnectionError(f"WebSocket 握手失败：{first}")
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if accept.lower().encode() not in header.lower():
            # 校验失败不阻断（部分 Chromium 版本的头部大小写/顺序差异），仅记录
            pass

    def _read_until(self, sep: bytes) -> bytes:
        assert self.sock is not None
        while sep not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("连接被关闭")
            self._buf += chunk
        head, _, rest = self._buf.partition(sep)
        self._buf = rest
        return head + sep

    def _recv_exact(self, n: int) -> bytes:
        assert self.sock is not None
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("连接被关闭")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    # -- 收发帧 -------------------------------------------------------

    def send_text(self, text: str):
        assert self.sock is not None
        payload = text.encode("utf-8")
        header = bytearray([0x81])          # FIN + text
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        """返回 (FIN, opcode, payload)。"""
        b1, b2 = self._recv_exact(2)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        data = self._recv_exact(length) if length else b""
        if masked and data:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return fin, opcode, data

    def recv_text(self) -> str:
        """读一条完整文本消息，自动处理分片 / ping / close。

        分片处理必须看 FIN 位而不是"收到数据就当结束"——
        Cookie 列表较大时 Chromium 会分片发送，误判会截断 JSON。
        """
        chunks: list[bytes] = []
        in_message = False
        while True:
            fin, opcode, data = self._recv_frame()
            if opcode == 0x8:               # close
                raise ConnectionError("服务端关闭了 WebSocket")
            if opcode == 0x9:               # ping → pong
                self._send_pong(data)
                continue
            if opcode == 0xA:               # pong
                continue
            if opcode in (0x1, 0x2):        # text / binary：消息起始帧
                chunks = [data]
                in_message = True
            elif opcode == 0x0 and in_message:   # continuation
                chunks.append(data)
            else:
                continue
            if fin:
                break
        return b"".join(chunks).decode("utf-8", "replace")

    def _send_pong(self, payload: bytes):
        assert self.sock is not None
        mask = os.urandom(4)
        header = bytearray([0x8A])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        else:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:               # noqa: BLE001
                pass
            self.sock = None


def _frame_is_final(_data: bytes) -> bool:
    """已废弃：分片判断统一看 FIN 位（见 _WSClient.recv_text）。保留以免外部引用报错。"""
    return True


# ----------------------------------------------------------------------
#  CDP 会话
# ----------------------------------------------------------------------


class CDPError(RuntimeError):
    pass


class CDPSession:
    """与一个浏览器实例的 CDP 会话（浏览器级 endpoint）。"""

    def __init__(self, port: int, timeout: float = 15.0):
        self.port = port
        self.timeout = timeout
        self.ws: _WSClient | None = None
        self._id = 0

    # -- 发现 ---------------------------------------------------------

    def _http_json(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def wait_ready(self, timeout: float = 25.0) -> bool:
        """等调试端口就绪。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                info = self._http_json("/json/version")
                if info.get("webSocketDebuggerUrl"):
                    return True
            except Exception:               # noqa: BLE001
                time.sleep(0.4)
        return False

    def connect(self):
        ver = self._http_json("/json/version")
        url = ver.get("webSocketDebuggerUrl")
        if not url:
            raise CDPError("调试端口已开但拿不到 WebSocket 地址")
        self.ws = _WSClient(url, timeout=self.timeout)
        self.ws.connect()

    # -- 调用 ---------------------------------------------------------

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0):
        if not self.ws:
            raise CDPError("尚未连接")
        self._id += 1
        mid = self._id
        self.ws.send_text(json.dumps({"id": mid, "method": method,
                                      "params": params or {}}, ensure_ascii=False))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv_text())
            if msg.get("id") != mid:
                continue                     # 事件消息，跳过
            if "error" in msg:
                raise CDPError(f"{method} 失败：{msg['error']}")
            return msg.get("result") or {}
        raise CDPError(f"{method} 超时")

    def get_all_cookies(self) -> list[dict]:
        res = self.call("Storage.getCookies", {})
        return res.get("cookies") or []

    def eval_js(self, expression: str, timeout: float = 15.0):
        """在任意一个 page target 里执行 JS（用于活跃度探测/打开页面）。"""
        targets = self._http_json("/json/list")
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page or not page.get("webSocketDebuggerUrl"):
            return None
        ws = _WSClient(page["webSocketDebuggerUrl"], timeout=timeout)
        try:
            ws.connect()
            ws.send_text(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }))
            deadline = time.time() + timeout
            while time.time() < deadline:
                msg = json.loads(ws.recv_text())
                if msg.get("id") == 1:
                    return (msg.get("result") or {}).get("result", {}).get("value")
        except Exception:                    # noqa: BLE001
            return None
        finally:
            ws.close()
        return None

    def open_url(self, url: str):
        try:
            self.call("Target.createTarget", {"url": url}, timeout=8)
        except Exception:                    # noqa: BLE001
            pass

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None


# ----------------------------------------------------------------------
#  登录会话
# ----------------------------------------------------------------------


class BrowserLoginSession:
    """启动内置浏览器、打开指定平台登录页、轮询读取登录 Cookie。

    多平台通用：通过 platform 选择抖音 / 小红书 / 快手 / TikTok（见 LOGIN_PLATFORMS）。
    各平台的登录页、登录态判定 Cookie、目标域名都从配置表取，逻辑完全一致。

    典型用法：
        sess = BrowserLoginSession("xiaohongshu", profile_dir, port=0)
        ok, msg = sess.start()            # 拉起浏览器（会立刻返回）
        while True:                        # 由调用方在后台线程里轮询
            state = sess.poll()            # -> ("waiting"/"logged_in"/"dead", cookie, info)
    """

    def __init__(self, platform: str, profile_dir: Path, port: int | None = None,
                 open_url: str | None = None):
        cfg = platform_config(platform)
        self.platform = platform
        self.label = cfg["label"]
        self.home_url = cfg["home"]
        self.open_url = open_url or cfg["login"]
        self.login_cookie_names = cfg["cookie_names"]
        self.domain_suffix = cfg["domain_suffix"]
        self.domestic_blocked = cfg.get("domestic_blocked", False)

        self.profile_dir = Path(profile_dir)
        self.port = int(port or 0)
        self.proc: subprocess.Popen | None = None
        self.session: CDPSession | None = None
        self.browser_name = ""
        self.browser_path = ""
        self.reused = False

    # -- 启动 ---------------------------------------------------------

    def start(self) -> tuple[bool, str]:
        # 国内被屏蔽的平台（如 TikTok）直接拦截，给出清晰说明，避免用户白等
        if self.domestic_blocked:
            return False, (
                f"{self.label} 在国内被屏蔽、无法访问。此功能仅面向海外网络环境的用户；"
                "国内用户请使用抖音 / 小红书 / 快手。"
            )

        found = find_browser()
        if not found:
            return False, ("未找到本机浏览器。本功能需要 Microsoft Edge 或 Google Chrome，"
                           "请先安装其中任意一个。")
        self.browser_path, self.browser_name = found

        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # 端口：优先复用上次的（浏览器可能还活着），否则取空闲端口
        if not self.port:
            self.port = free_port()
        elif port_busy(self.port):
            # 端口被占用：先看是不是上次那个浏览器还开着
            probe = CDPSession(self.port)
            if probe.wait_ready(timeout=1.0):
                self.reused = True
                self.session = probe
            else:
                self.port = free_port()

        args = [
            self.browser_path,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--disable-features=Translate,msEdgeSidebar,msEdgeIdentityFeatures",
            "--window-size=1024,760",
            "--window-position=220,80",
            self.open_url,
        ]
        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as e:              # noqa: BLE001
            return False, f"启动浏览器失败：{e}"

        if self.session is None:
            self.session = CDPSession(self.port)
            if not self.session.wait_ready(timeout=25.0):
                return False, ("浏览器已启动，但调试端口未就绪（可能有安全软件拦截）。"
                               "请关闭刚打开的窗口后重试。")
        try:
            self.session.connect()
        except Exception as e:              # noqa: BLE001
            return False, f"连接调试端口失败：{e}"
        return True, f"{self.browser_name} 已打开，请在窗口中扫码登录{self.label}"

    # -- 轮询 ---------------------------------------------------------

    def poll(self) -> tuple[str, str, dict]:
        """返回 (状态, cookie_header, info)。

        状态取值：logged_in / waiting / dead
        """
        if self.session is None:
            return "dead", "", {}
        try:
            cookies = self.session.get_all_cookies()
        except Exception as e:              # noqa: BLE001
            return "dead", "", {"error": str(e)}
        if is_logged_in(cookies, self.login_cookie_names):
            header = cookies_to_header(cookies, self.domain_suffix)
            return "logged_in", header, {
                "count": len([c for c in cookies
                              if self.domain_suffix in (c.get("domain") or "")]),
                "ttwid": extract_ttwid(header),
            }
        return "waiting", "", {"count": len(cookies)}

    def stop(self):
        if self.session:
            self.session.close()
            self.session = None
        # 不主动杀浏览器：用户可能还想在里面浏览，关窗口即可


class DouyinLoginSession(BrowserLoginSession):
    """抖音登录会话（向后兼容别名，旧代码 / GUI 仍可用此类名）。"""

    def __init__(self, profile_dir: Path, port: int | None = None,
                 open_url: str | None = None):
        super().__init__("douyin", profile_dir, port=port, open_url=open_url)


# ----------------------------------------------------------------------
#  便捷函数（给 GUI 用）
# ----------------------------------------------------------------------


def default_profile_dir(app_data_dir: Path, platform: str = "douyin") -> Path:
    """本工具专用浏览器 profile 目录（与用户自己的浏览器完全隔离）。

    按平台分目录：各平台登录态独立保存，互不干扰。
    抖音沿用历史目录名 browser_profile，避免老用户已保存的登录态失效；
    其它平台用带后缀的目录名。
    """
    if platform == "douyin":
        return Path(app_data_dir) / "browser_profile"
    return Path(app_data_dir) / f"browser_profile_{platform}"


def create_login_session(platform: str, app_data_dir: Path,
                         port: int | None = None,
                         open_url: str | None = None) -> BrowserLoginSession:
    """按平台创建一个登录会话（便捷入口）。"""
    if platform not in LOGIN_PLATFORMS:
        platform = "douyin"
    return BrowserLoginSession(
        platform, default_profile_dir(app_data_dir, platform),
        port=port, open_url=open_url,
    )


def cookie_summary(cookie_header: str,
                   cookie_names: tuple = LOGIN_COOKIE_NAMES) -> str:
    """给 UI 显示的一行摘要：登录态关键 Cookie 是否齐全。

    cookie_names 按平台传入（见 LOGIN_PLATFORMS），默认抖音。
    """
    if not cookie_header:
        return "无 Cookie"
    names = {p.split("=", 1)[0].strip() for p in cookie_header.split(";") if "=" in p}
    key = [n for n in cookie_names if n in names]
    bits = [f"共 {len(names)} 项 Cookie"]
    bits.append("含登录态 " + "/".join(key) if key else "未含登录态")
    if "ttwid" in names:
        bits.append("含 ttwid")
    return "，".join(bits)


if __name__ == "__main__":                  # pragma: no cover - 手工调试用
    from . import branding
    p, n = find_browser() or ("", "")
    print("browser:", n, p)
    sess = DouyinLoginSession(default_profile_dir(branding.app_data_dir()))
    ok, msg = sess.start()
    print(ok, msg)
    if ok:
        for _ in range(600):
            st, ck, info = sess.poll()
            print(st, info)
            if st == "logged_in":
                print(cookie_summary(ck))
                break
            time.sleep(2)
    sys.exit(0)
