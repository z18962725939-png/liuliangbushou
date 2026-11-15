"""TikTok 直播监控适配层（独立数据源，绝不混用 JustOneAPI 采集接口）。

实测判据（2026-11-14 / 11-15）：
  * 直播间页有 SlardarWAF 的 JS 挑战，curl / 指纹模拟库 / --dump-dom 一律
    只拿到 706~1462B 的「Please wait…」；**唯一可行的是真浏览器 CDP 会话**，
    导航后**真实等待 8 秒**让挑战脚本跑完。禁止 --virtual-time-budget。
  * roomId 真路径 = SIGI_STATE.LiveRoom.liveRoomUserInfo.user.roomId
    （不是 __UNIVERSAL_DATA_FOR_REHYDRATION__，那是主播主页型备用）。
  * 状态/流地址 = webcast.tiktok.com/webcast/room/info/（**尾斜杠必须**），
    aid=1988&room_id=<roomId>；data.status 2=在播 / 4=结束，与抖音同构。
    httpx 即可调，不需要额外指纹库，但**必须走海外出口**。
  * 下播后 /live 页仍给 roomId 且标题仍写「正在直播」⇒ 状态只认接口。
  * 下播后旧流地址即 404（sign/expire 没过期也没用）⇒ 开录前必须重取。
  * ffmpeg 需 -http_proxy + Referer + 浏览器 UA；直连 CDN 会被重置。

本模块不产生任何 JustOneAPI 调用、不进付费闸门；免费版同样可用。
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from .browser_login import CDPSession, find_browser, free_port
except Exception as _exc:                                    # noqa: BLE001
    CDPSession = None                                        # type: ignore[assignment]
    find_browser = None                                      # type: ignore[assignment]
    free_port = None                                         # type: ignore[assignment]
    _CDP_IMPORT_ERROR = _exc
else:
    _CDP_IMPORT_ERROR = None

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIKTOK_REFERER = "https://www.tiktok.com/"
WEBCAST_URL = "https://webcast.tiktok.com/webcast/room/info/"
AID = "1988"

STATUS_LIVE = 2
STATUS_OFFLINE = 4
STATUS_UNKNOWN = 0
ROOM_INVALID = 10011           # 换了一场直播，需要重新解析 roomId

# 只用于「自动探测后填进输入框」，绝不写死为唯一出口
_COMMON_PROXY_PORTS = (7897, 7890, 7891, 10809, 10808, 1080, 2080, 20171)

_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9._]{2,24})$")
_ROOM_ID_RE = re.compile(r'"roomId"\s*:\s*"?(\d{10,})"?')
_SCRIPT_RE = re.compile(r'<script[^>]+id="([^"]+)"[^>]*>(.*?)</script>',
                        re.S | re.I)


# ---------------------------------------------------------------- handle
def normalize_handle(text: str) -> str:
    """把各种粘贴形式归一化成裸 handle（不带 @），失败返回空串。"""
    s = (text or "").strip()
    if not s:
        return ""
    if "://" in s or s.startswith("//") or "tiktok.com" in s.lower():
        if s.startswith("//"):
            s = "https:" + s
        if "://" not in s:
            s = "https://" + s
        try:
            parts = [p for p in urlparse(s).path.split("/") if p]
        except ValueError:
            return ""
        for i, part in enumerate(parts):
            if part.startswith("@") and len(part) > 1:
                m = _HANDLE_RE.match(part)
                return m.group(1) if m else ""
            if part in ("live", "video", "photo") and i > 0:
                m = _HANDLE_RE.match(parts[i - 1])
                return m.group(1) if m else ""
        return ""
    m = _HANDLE_RE.match(s)
    return m.group(1) if m else ""


def live_url(handle: str) -> str:
    """直播间页地址。不带跟踪参数，减少风控变量。"""
    h = normalize_handle(handle)
    return f"https://www.tiktok.com/@{h}/live" if h else ""


def is_tiktok_input(text: str) -> bool:
    """只认显式 TikTok 链接或以 @ 开头的主播号。

    ★ 刻意不认裸 handle：抖音的 web_rid 也是一串字母数字，自动吞掉会把
      抖音直播间错判成 TikTok。带 @ 是 TikTok 独有的写法，可安全区分。
    """
    s = (text or "").strip()
    low = s.lower()
    if not s:
        return False
    if "tiktok.com" in low or low.startswith("//www.tiktok"):
        return True
    return s.startswith("@") and bool(_HANDLE_RE.match(s))


def is_waf_page(html: str) -> bool:
    """TikTok 的 SlardarWAF 是 JS 挑战页（约 0.7~1.5KB），不是正常直播页。"""
    h = html or ""
    if len(h) < 8000:
        return True
    low = h.lower()
    markers = ("slardarwaf", "please wait", "you are unauthorized",
               "未获授权", "verify you are human", "captcha")
    return any(m in low for m in markers) and len(h) < 60000


# ---------------------------------------------------------------- 代理
def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def normalize_proxy(value: str) -> str:
    """把各种写法统一成 `http://host:port`。

    认这几种：`127.0.0.1:7897`、`http://127.0.0.1:7897`、
    以及 Windows 的分段写法 `http=127.0.0.1:7897;https=127.0.0.1:7897`。
    socks5 原样保留（Chrome 直接吃，httpx 需装 socks 支持）。
    """
    s = (value or "").strip()
    if not s:
        return ""
    if "=" in s:
        picked = ""
        for part in s.split(";"):
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            key, val = key.strip().lower(), val.strip()
            if not val:
                continue
            if key.startswith("https"):
                picked = val
                break
            if key == "http":
                picked = picked or val
        s = picked or s
    if "://" in s:
        return s
    return f"http://{s}"


def system_proxy() -> str:
    """读 Windows 系统代理（Clash Verge / v2rayN 的「系统代理」就写这里）。

    ★ 客户机上最可靠的一条线索：浏览器能打开 TikTok 直播间，通常就是
      因为它跟随了系统代理，而我们的 httpx 默认不读这一项。
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except Exception:                           # noqa: BLE001
        return ""
    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return ""
    try:
        try:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
        except FileNotFoundError:
            return ""
        if not enabled:
            return ""
        try:
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
        except FileNotFoundError:
            return ""
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:                       # noqa: BLE001
            pass
    return normalize_proxy(str(server or ""))


def env_proxy() -> str:
    """环境变量里的代理；IDE / 企业软件常注入这种东西，未必能出海。"""
    for key in ("TIKTOK_LIVE_PROXY", "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy"):
        val = normalize_proxy(os.environ.get(key) or "")
        if val:
            return val
    return ""


def proxy_candidates() -> list[str]:
    """按「最可能可用」排序的候选出口，已去重。

    顺序：显式 TIKTOK_LIVE_PROXY → Windows 系统代理 → 本机常见端口 → 环境变量。
    ★ 环境变量排最后：它可能是开发工具注入的临时端口，连 TikTok 必 502。
    """
    out: list[str] = []
    explicit = normalize_proxy(os.environ.get("TIKTOK_LIVE_PROXY") or "")
    if explicit:
        out.append(explicit)
    sys_p = system_proxy()
    if sys_p:
        out.append(sys_p)
    for port in _COMMON_PROXY_PORTS:
        if _port_open(port):
            out.append(f"http://127.0.0.1:{port}")
    env_p = env_proxy()
    if env_p:
        out.append(env_p)
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def proxy_works(proxy: str, timeout: float = 4.0) -> bool:
    """真发一次请求，确认这个代理能把我们带到 TikTok。

    ★ 只测端口是否 listening 远远不够：本机随便一个软件、开发工具注入的
      临时端口都会「开着」，但连 TikTok 一律 502 / CONNECT 被拒。选中这种
      出口，界面就会整片「状态未知」。
    """
    p = normalize_proxy(proxy)
    if not p:
        return False
    try:
        with httpx.Client(proxy=p, timeout=timeout, follow_redirects=True,
                          trust_env=False) as c:
            r = c.get(f"{WEBCAST_URL}?aid={AID}&room_id=1",
                      headers={"User-Agent": CHROME_UA,
                               "Accept": "application/json, text/plain, */*"})
        return r.status_code < 500 and r.status_code != 407
    except Exception:                           # noqa: BLE001
        return False


def quick_proxy() -> str:
    """毫秒级取一个出口候选，供启动/设置注入用（不联网验证）。

    只看「显式配置 → 系统代理 → 常见端口」，**不看环境变量**：那是 IDE
    注入的临时端口，填进界面只会误导客户。真正的可用性由 fetch_room 的
    多出口回退来兜底，所以这里取错也不会造成「状态未知」。
    """
    explicit = normalize_proxy(os.environ.get("TIKTOK_LIVE_PROXY") or "")
    if explicit:
        return explicit
    sys_p = system_proxy()
    if sys_p:
        return sys_p
    for port in _COMMON_PROXY_PORTS:
        if _port_open(port):
            return f"http://127.0.0.1:{port}"
    return ""


def detect_proxy(verify: bool = True) -> str:
    """探测一个**真能连上 TikTok** 的代理；没有就返回空串。

    顺序同 proxy_candidates；verify=True 时逐个真发请求验证，只认验证通过的
    那个（界面「自动探测」按钮用这条）。全部不通返回 ""，让请求走直连 ——
    客户开 TUN 模式时直连本来就通。
    """
    cands = proxy_candidates()
    if not verify:
        return cands[0] if cands else ""
    for p in cands:
        if proxy_works(p):
            return p
    return ""


def proxy_env(proxy: str) -> dict:
    """给 ffmpeg 子进程用的代理环境变量（部分构建只认环境变量）。"""
    if not proxy:
        return {}
    return {k: proxy for k in ("http_proxy", "https_proxy",
                               "HTTP_PROXY", "HTTPS_PROXY")}


def ffmpeg_input_args(proxy: str = "", user_agent: str = CHROME_UA) -> list[str]:
    """TikTok 拉流比抖音多出来的 ffmpeg 输入参数（放在 -i 之前）。"""
    out: list[str] = []
    if proxy:
        out += ["-http_proxy", proxy]
    headers = f"Referer: {TIKTOK_REFERER}\r\nUser-Agent: {user_agent}\r\n"
    out += ["-headers", headers]
    return out


# ---------------------------------------------------------------- 页面解析
def _script_json(html: str, script_id: str) -> dict | None:
    """取指定 id 的 <script> 内容并用 raw_decode 解析（后面常跟 JS）。"""
    for sid, body in _SCRIPT_RE.findall(html or ""):
        if sid == script_id:
            try:
                obj, _ = json.JSONDecoder().raw_decode(body.strip())
                return obj if isinstance(obj, dict) else None
            except (ValueError, TypeError):
                return None
    return None


def _walk_find_key(obj: Any, keys: tuple[str, ...], depth: int = 0,
                   limit: int = 9) -> list[Any]:
    found: list[Any] = []
    if depth > limit or not isinstance(obj, (dict, list)):
        return found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in keys and isinstance(v, (str, int)):
                if str(v).strip():
                    found.append(v)
            found.extend(_walk_find_key(v, keys, depth + 1, limit))
    else:
        for item in obj[:12]:
            found.extend(_walk_find_key(item, keys, depth + 1, limit))
    return found


@dataclass
class PageInfo:
    """从直播间真实 HTML 里解析出来的结果。"""

    room_id: str = ""
    unique_id: str = ""
    nickname: str = ""
    page_title: str = ""
    source: str = ""
    html_size: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.room_id)


def extract_page_info(html: str) -> PageInfo:
    """多路回退解析 roomId：SIGI_STATE → UNIVERSAL → 递归扫描 → 全页正则。"""
    h = html or ""
    info = PageInfo(html_size=len(h))
    m = re.search(r"<title[^>]*>([^<]*)</title>", h, re.I | re.S)
    if m:
        info.page_title = m.group(1).strip()

    # ① 主流：直播间页 SIGI_STATE.LiveRoom.liveRoomUserInfo.user.roomId
    sigi = _script_json(h, "SIGI_STATE") or {}
    lr = sigi.get("LiveRoom") or {}
    user = ((lr.get("liveRoomUserInfo") or {}).get("user") or {})
    if isinstance(user, dict):
        rid = str(user.get("roomId") or "").strip()
        if rid.isdigit() and len(rid) >= 10:
            info.room_id = rid
            info.unique_id = str(user.get("uniqueId") or "")
            info.nickname = str(user.get("nickname") or "")
            info.source = "SIGI_STATE.LiveRoom.liveRoomUserInfo.user"
            return info
    for container in (lr.get("liveRoomStatus"), sigi.get("CurrentRoom")):
        if isinstance(container, dict):
            rid = str(container.get("roomId") or "").strip()
            if rid.isdigit() and len(rid) >= 10:
                info.room_id, info.source = rid, "SIGI_STATE.CurrentRoom"
                return info

    # ② 主播主页型 __UNIVERSAL_DATA_FOR_REHYDRATION__（yt-dlp 走的路径）
    uni = _script_json(h, "__UNIVERSAL_DATA_FOR_REHYDRATION__") or {}
    detail = (uni.get("__DEFAULT_SCOPE__") or {}).get("webapp.user-detail") or {}
    ud = ((detail.get("userInfo") or {}).get("user") or {})
    if isinstance(ud, dict):
        rid = str(ud.get("roomId") or "").strip()
        if rid.isdigit() and len(rid) >= 10:
            info.room_id = rid
            info.unique_id = str(ud.get("uniqueId") or "")
            info.nickname = str(ud.get("nickname") or "")
            info.source = "UNIVERSAL.webapp.user-detail"
            return info

    for val in _walk_find_key(uni, ("roomid", "room_id")):
        rid = str(val).strip()
        if rid.isdigit() and len(rid) >= 10:
            info.room_id, info.source = rid, "UNIVERSAL 递归扫描"
            return info

    hit = _ROOM_ID_RE.search(h)
    if hit:
        info.room_id, info.source = hit.group(1), "全页正则兜底"
        near = h[max(0, hit.start() - 400):hit.end()]
        mu = re.search(r'"uniqueId"\s*:\s*"([A-Za-z0-9._]{2,24})"', near)
        mn = re.search(r'"nickname"\s*:\s*"([^"]{1,60})"', near)
        if mu:
            info.unique_id = mu.group(1)
        if mn:
            info.nickname = mn.group(1)
    return info


# ---------------------------------------------------------------- 房间状态
@dataclass
class RoomSnapshot:
    """webcast/room/info/ 的解析结果；任何异常都变成「状态未知」，绝不抛出。"""

    status_code: int = -1
    raw_status: int = STATUS_UNKNOWN
    status: int = STATUS_UNKNOWN
    title: str = ""
    owner: str = ""
    user_count: int = 0
    stream_url: str = ""
    stream_urls: list[str] = field(default_factory=list)
    expires_at: float = 0.0
    error: str = ""
    payload_size: int = 0
    via: str = ""               # 本次实际走通的出口（代理 URL / 「直连」）

    @property
    def is_live(self) -> bool:
        return self.status == STATUS_LIVE

    @property
    def is_offline(self) -> bool:
        return self.status == STATUS_OFFLINE

    @property
    def status_text(self) -> str:
        return {STATUS_LIVE: "直播中", STATUS_OFFLINE: "已下播",
                STATUS_UNKNOWN: "状态未知"}.get(self.status, "状态未知")


def _expire_of(url: str) -> float:
    m = re.search(r"[?&]expire=(\d{9,})", url or "")
    return float(m.group(1)) if m else 0.0


def _sdk_streams(sdk: dict) -> list[str]:
    raw = (sdk.get("pull_data") or {}).get("stream_data")
    if not raw:
        return []
    try:
        inner = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    out: list[str] = []
    qualities = (inner.get("data") or {}) if isinstance(inner, dict) else {}
    for item in qualities.values():
        if not isinstance(item, dict):
            continue
        main = item.get("main") or {}
        for key in ("hls", "flv"):
            u = main.get(key)
            if isinstance(u, str) and u.startswith("http"):
                out.append(u)
    return out


def collect_stream_urls(stream_url: dict) -> list[str]:
    """HLS 优先 → SDK 档位 → FLV。只收 http(s)：rtmp:// 不吃 -http_proxy。"""
    su = stream_url or {}
    out: list[str] = []

    def add(u: Any):
        if isinstance(u, str) and u.startswith("http") and u not in out:
            out.append(u)

    add(su.get("hls_pull_url"))
    sdk = su.get("live_core_sdk_data") or {}
    if isinstance(sdk, dict):
        for u in _sdk_streams(sdk):
            add(u)
    flv = su.get("flv_pull_url")
    if isinstance(flv, dict):
        for u in flv.values():
            add(u)
    else:
        add(flv)
    return out


def parse_room(payload: dict | str | bytes) -> RoomSnapshot:
    """解析 webcast 房间接口返回。"""
    snap = RoomSnapshot()
    try:
        if isinstance(payload, (str, bytes)):
            snap.payload_size = len(payload)
            d = json.loads(payload)
        else:
            d = payload
            snap.payload_size = len(json.dumps(d, ensure_ascii=False))
    except Exception as exc:                                # noqa: BLE001
        snap.error = f"返回不是 JSON：{type(exc).__name__}"
        return snap
    if not isinstance(d, dict):
        snap.error = "返回结构不是对象"
        return snap
    try:
        snap.status_code = int(d.get("status_code", -1))
    except (TypeError, ValueError):
        snap.status_code = -1
    if snap.status_code != 0:
        snap.error = f"接口返回 status_code={snap.status_code}"
        return snap
    data = d.get("data") or {}
    if not isinstance(data, dict):
        snap.error = "data 为空或结构异常"
        return snap
    try:
        snap.raw_status = int(data.get("status", STATUS_UNKNOWN))
    except (TypeError, ValueError):
        snap.raw_status = STATUS_UNKNOWN
    snap.status = (snap.raw_status if snap.raw_status in (STATUS_LIVE, STATUS_OFFLINE)
                   else STATUS_UNKNOWN)
    snap.title = str(data.get("title") or "")
    snap.owner = str(((data.get("owner") or {}).get("display_id") or ""))
    try:
        snap.user_count = int(data.get("user_count") or 0)
    except (TypeError, ValueError):
        snap.user_count = 0
    # ★ 实测 2026-11-15：下播后 webcast 仍会返回带 sign/expire 的流地址，
    #   但 CDN 立刻 404。任何调用方都不该拿到「已下播的流地址」——这里
    #   直接不给，避免自动开录撞上下播瞬间拉到一个死链。
    if snap.status == STATUS_LIVE:
        su = data.get("stream_url") or {}
        snap.stream_urls = collect_stream_urls(su if isinstance(su, dict) else {})
        if snap.stream_urls:
            snap.stream_url = snap.stream_urls[0]
            snap.expires_at = _expire_of(snap.stream_url)
    return snap


def _room_headers(room_id: str, user_agent: str = CHROME_UA) -> dict:
    return {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.tiktok.com/@/live?room_id={room_id}",
    }


def _outage_hint(given: str, reasons: list[str]) -> str:
    """把「多个出口全失败」翻译成客户看得懂、能动手的一句话。"""
    if given:
        head = (f"连不上 TikTok（当前代理 {given} 不通）。"
                "请到「TikTok 代理」改成 Clash Verge 的端口，"
                "通常是 127.0.0.1:7897；改完点「保存代理」")
    else:
        head = ("连不上 TikTok（本机没找到可用代理）。"
                "请先打开代理软件并开启「系统代理」，"
                "再到「TikTok 代理」点「自动探测」后保存"
                "（Clash Verge 默认 127.0.0.1:7897）")
    tail = "；".join(reasons[:2])
    return f"{head}｜{tail}" if tail else head


def fetch_room(room_id: str, proxy: str = "", user_agent: str = CHROME_UA,
               timeout: float = 10.0) -> RoomSnapshot:
    """查房间状态 + 流地址。永远返回 RoomSnapshot，不把异常抛给轮询线程。

    ★ 出口按「给定代理 → 系统代理 → 直连」依次退让。客户机最常见的是
      Clash 的**系统代理**（浏览器能开直播间就是靠它），也有 TUN 模式的
      （直连即可出网）。只有全部失败才判「状态未知」，并把每个出口的
      失败原因写进 error，让界面显示可操作的提示。
    """
    snap = RoomSnapshot()
    rid = str(room_id or "").strip()
    if not rid.isdigit():
        snap.error = "room_id 为空或不是数字，需要先过浏览器取直播间页"
        return snap
    url = f"{WEBCAST_URL}?aid={AID}&room_id={rid}"
    given = normalize_proxy(proxy)
    tries: list[str] = []
    for p in (given, system_proxy()):
        if p and p not in tries:
            tries.append(p)
    tries.append("")                                   # 直连：TUN / 全局模式
    reasons: list[str] = []
    for p in tries:
        try:
            kwargs: dict = dict(headers=_room_headers(rid, user_agent),
                                timeout=timeout, follow_redirects=True,
                                trust_env=False)
            if p:
                kwargs["proxy"] = p
            with httpx.Client(**kwargs) as c:
                r = c.get(url)
            if r.status_code == 407:
                raise RuntimeError("代理需要认证（407）")
            if r.status_code >= 500:
                raise RuntimeError(f"服务端 HTTP {r.status_code}")
            out = parse_room(r.json())
            out.via = p or "直连"
            return out
        except Exception as exc:                       # noqa: BLE001
            reasons.append(f"{p or '直连'}：{type(exc).__name__} {str(exc)[:70]}")
    snap.error = _outage_hint(given, reasons)
    return snap


# ---------------------------------------------------------------- CDP 取页
class BrowserError(RuntimeError):
    """浏览器会话或 WAF 挑战失败。"""


class TikTokLiveBrowser:
    """可复用的无头浏览器会话；轮询期间保持不关，避免反复过 WAF。"""

    def __init__(self, proxy: str = "", wait: float = 8.0, rounds: int = 3):
        self.proxy = (proxy or "").strip()
        self.wait = max(3.0, float(wait))
        self.rounds = max(1, int(rounds))
        self.proc: subprocess.Popen | None = None
        self.session = None
        self._port = 0

    def start(self) -> None:
        if self.proc:
            return
        if _CDP_IMPORT_ERROR is not None:
            raise BrowserError(f"无法复用主工程 CDP 通道：{_CDP_IMPORT_ERROR}")
        found = find_browser()
        if not found:
            raise BrowserError("本机没找到 Edge / Chrome，无法过 TikTok 的浏览器校验")
        self._port = free_port()
        profile = str(Path(tempfile.gettempdir()) / f"tc_tiktok_live_{self._port}")
        args = [
            found[0], "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-sync",
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile}", "--ignore-certificate-errors",
            "--disable-blink-features=AutomationControlled",
        ]
        if self.proxy:
            args.append(f"--proxy-server={self.proxy}")
        # 刻意不加 --virtual-time-budget：虚拟时间会让挑战脚本跑废
        args.append("about:blank")
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 if os.name == "nt" else 0)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=flags)
        s = CDPSession(self._port)
        if not s.wait_ready(25):
            self.close()
            raise BrowserError("浏览器调试端口没起来")
        try:
            s.connect()
        except Exception as exc:                            # noqa: BLE001
            self.close()
            raise BrowserError(f"CDP 连接失败：{exc}") from exc
        self.session = s

    def close(self) -> None:
        try:
            if self.session:
                self.session.close()
        except Exception:                                   # noqa: BLE001
            pass
        self.session = None
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:                               # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:                           # noqa: BLE001
                    pass
        self.proc = None

    def _navigate(self, url: str) -> None:
        """在启动时唯一的 about:blank tab 上导航（避免 Edge 引导页抢占 target）。"""
        try:
            self.session.eval_js(f"location.href={url!r}", timeout=6)
        except Exception:                                   # noqa: BLE001
            try:
                self.session.open_url(url)
            except Exception:                               # noqa: BLE001
                pass

    def _read_html(self) -> str:
        expr = "JSON.stringify({h:document.documentElement.outerHTML})"
        raw = self.session.eval_js(expr, timeout=30)
        if not raw:
            return ""
        try:
            return str((json.loads(raw) or {}).get("h") or "")
        except (ValueError, TypeError):
            return ""

    def fetch_live_page(self, handle: str) -> str:
        """返回过完 WAF 挑战的完整页面 HTML；失败抛 BrowserError。

        跨页导航偶尔读回 0 字节（tab 被抢占），一轮全失败就重建会话再试一次。
        """
        url = live_url(handle)
        if not url:
            raise BrowserError("handle 格式不对，认不出 TikTok 主播号")
        last_size = 0
        for attempt in range(2):
            self.start()
            self._navigate(url)
            for _round in range(self.rounds):
                time.sleep(self.wait)                       # 必须是真实时间
                html = self._read_html()
                last_size = len(html)
                if html and not is_waf_page(html):
                    return html
            if attempt == 0:
                self.close()
        if not self.proxy:
            raise BrowserError(
                f"只拿到 {last_size} 字节的校验页；本机没配置海外出口，"
                "TikTok 页面在国内直连会被重置，请先在直播监控页填代理")
        if not proxy_works(self.proxy, 4.0):
            raise BrowserError(
                f"代理 {self.proxy} 连不上 TikTok（端口开着但出不去）。"
                "请核对端口（Clash Verge 默认 127.0.0.1:7897），"
                "或到「TikTok 代理」点「自动探测」后保存")
        raise BrowserError(
            f"等了 {self.rounds} 轮仍是 {last_size} 字节的校验页；"
            "可能是代理 IP 被风控，或该直播间需要登录/年龄验证")
