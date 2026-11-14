"""TikTok 直播监控原型 · 纯逻辑层（不联网、不启进程，便于离线自测）。

四步链路里的「解析」全部集中在这里：
  handle → 直播间页 → roomId → webcast 状态/流地址 → ffmpeg 命令
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import CHROME_UA

# webcast 房间状态码：2=在播，4=已结束；其它一律当「状态未知」
STATUS_LIVE = 2
STATUS_OFFLINE = 4
STATUS_UNKNOWN = 0

_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9._]{2,24})$")
_ROOM_ID_RE = re.compile(r'"roomId"\s*:\s*"?(\d{10,})"?')
_SCRIPT_RE = re.compile(
    r'<script[^>]+id="([^"]+)"[^>]*>(.*?)</script>', re.S | re.I
)


# ---------------------------------------------------------------- handle
def normalize_handle(text: str) -> str:
    """把各种粘贴形式归一化成 @handle，失败返回空串。

    支持：
      https://www.tiktok.com/@ofdwex9/live?_r=1&...
      https://www.tiktok.com/@ofdwex9
      @ofdwex9 / ofdwex9
    """
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
    """直播间页地址。不带任何跟踪参数，减少风控变量。"""
    h = normalize_handle(handle)
    return f"https://www.tiktok.com/@{h}/live" if h else ""


# ---------------------------------------------------------------- 页面判定
def is_waf_page(html: str) -> bool:
    """TikTok 的 SlardarWAF 是 JS 挑战页（约 0.7~1.5KB），不是正常直播页。"""
    h = html or ""
    if len(h) < 8000:
        return True
    low = h.lower()
    markers = ("slardarwaf", "please wait", "you are unauthorized",
               "未获授权", "verify you are human", "captcha")
    if any(m in low for m in markers) and len(h) < 60000:
        return True
    return False


def _script_json(html: str, script_id: str) -> dict | None:
    """取指定 id 的 <script> 内容并用 raw_decode 解析。

    页面 script 后面常跟别的内容，json.loads 整体会炸，必须 raw_decode。
    """
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
    """在嵌套 dict/list 里按不区分大小写的键名找值（带深度上限）。"""
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
    source: str = ""          # 命中哪条解析路径，便于排查
    html_size: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.room_id)


def extract_page_info(html: str) -> PageInfo:
    """多路回退解析 roomId。

    实测（2026-11-14，@ofdwex9 在播页 337KB）：
      主路径 = SIGI_STATE.LiveRoom.liveRoomUserInfo.user.roomId
    备用：主页型 __UNIVERSAL_DATA_FOR_REHYDRATION__ 里的 webapp.user-detail、
      以及全页正则。任一路径命中即可，避免 TikTok 改版就全废。
    """
    h = html or ""
    info = PageInfo(html_size=len(h))
    m = re.search(r"<title[^>]*>([^<]*)</title>", h, re.I | re.S)
    if m:
        info.page_title = m.group(1).strip()

    # ① 主流：直播间页 SIGI_STATE
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
                info.room_id = rid
                info.source = "SIGI_STATE.CurrentRoom"
                return info

    # ② 主页型 __UNIVERSAL_DATA_FOR_REHYDRATION__（yt-dlp 走的路径）
    uni = _script_json(h, "__UNIVERSAL_DATA_FOR_REHYDRATION__") or {}
    scope = uni.get("__DEFAULT_SCOPE__") or {}
    detail = scope.get("webapp.user-detail") or {}
    ud = ((detail.get("userInfo") or {}).get("user") or {})
    if isinstance(ud, dict):
        rid = str(ud.get("roomId") or "").strip()
        if rid.isdigit() and len(rid) >= 10:
            info.room_id = rid
            info.unique_id = str(ud.get("uniqueId") or "")
            info.nickname = str(ud.get("nickname") or "")
            info.source = "UNIVERSAL.webapp.user-detail"
            return info

    # ③ 整个 UNIVERSAL / 全页 script 里扫一遍
    for val in _walk_find_key(uni, ("roomid", "room_id")):
        rid = str(val).strip()
        if rid.isdigit() and len(rid) >= 10:
            info.room_id, info.source = rid, "UNIVERSAL 递归扫描"
            return info

    # ④ 全页正则兜底
    hit = _ROOM_ID_RE.search(h)
    if hit:
        # 只接受和页面里主播信息相邻的 roomId，避免抓到推荐位其它主播
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
    """webcast/room/info/ 的解析结果。"""

    status_code: int = -1              # 接口自己的 0=成功，10011=房间无效
    raw_status: int = STATUS_UNKNOWN   # data.status
    status: int = STATUS_UNKNOWN       # 归一到 2 / 4 / 0
    title: str = ""
    owner: str = ""
    user_count: int = 0
    stream_url: str = ""
    stream_urls: list[str] = field(default_factory=list)
    expires_at: float = 0.0
    error: str = ""
    payload_size: int = 0

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
    """live_core_sdk_data.pull_data.stream_data 是嵌套 JSON 字符串。"""
    out: list[str] = []
    raw = ((sdk.get("pull_data") or {}).get("stream_data"))
    if not raw:
        return out
    try:
        inner = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return out
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
    """按可用性排序收集流地址：HLS 优先，再 SDK 档位，再 FLV。

    只收 http(s)：rtmp:// 不吃 ffmpeg 的 -http_proxy，走代理时会连不上，
    因此原型里刻意不把它列为候选。
    """
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
    add(su.get("rtmp_pull_url"))
    return out


def parse_room(payload: dict | str | bytes) -> RoomSnapshot:
    """解析 webcast 房间接口返回。任何异常都变成「状态未知」，绝不抛出。"""
    snap = RoomSnapshot()
    try:
        if isinstance(payload, (str, bytes)):
            snap.payload_size = len(payload)
            d = json.loads(payload)
        else:
            d = payload
            snap.payload_size = len(json.dumps(d, ensure_ascii=False))
    except Exception as exc:                            # noqa: BLE001
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
    su = data.get("stream_url") or {}
    snap.stream_urls = collect_stream_urls(su if isinstance(su, dict) else {})
    if snap.stream_urls:
        snap.stream_url = snap.stream_urls[0]
        snap.expires_at = _expire_of(snap.stream_url)
    return snap


# ---------------------------------------------------------------- 录制命令
def safe_filename(text: str, max_len: int = 40) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (text or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:max_len] or "live"


def new_record_path(handle: str, save_dir: str | Path, seq: int = 1,
                    now: datetime | None = None) -> Path:
    """一段录制一个文件，避免重启录制时覆盖成片。"""
    d = Path(save_dir)
    d.mkdir(parents=True, exist_ok=True)
    t = now or datetime.now()
    name = (f"{t:%Y%m%d_%H%M%S}_{safe_filename(normalize_handle(handle) or 'live')}"
            f"_{seq:02d}.mp4")
    return d / name


def build_ffmpeg_cmd(ffmpeg: str, stream_url: str, out_path: str | Path,
                     proxy: str = "", user_agent: str = CHROME_UA,
                     duration: float = 0.0,
                     referer: str = "https://www.tiktok.com/") -> list[str]:
    """构造录制命令。

    与现有抖音链路的差别（实测 2026-11-14 必需）：
      - -http_proxy：CDN 在国内直连会被重置
      - -headers：浏览器 UA + Referer，裸请求会被拒
      - 不传 -nostdin：留着 stdin 才能发 q 优雅收尾、写出完整 mp4
    """
    cmd = [ffmpeg, "-y", "-loglevel", "warning"]
    if proxy:
        cmd += ["-http_proxy", proxy]
    headers = f"Referer: {referer}\r\nUser-Agent: {user_agent}\r\n"
    cmd += ["-headers", headers, "-i", stream_url]
    if duration and duration > 0:
        cmd += ["-t", str(int(duration))]
    cmd += ["-c", "copy", "-bsf:a", "aac_adtstoasc", "-f", "mp4", str(out_path)]
    return cmd
