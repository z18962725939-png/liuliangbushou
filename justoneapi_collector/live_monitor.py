"""
Live stream monitor & recorder for Douyin + TikTok.

★ TikTok 走独立适配层 tiktok_live.py：CDP 真浏览器过 WAF 取 roomId →
  webcast 查状态/流地址 → 带代理的 ffmpeg 录制。它不调 JustOneAPI、
  不进付费闸门，免费版同样可用；客户机需自备可用海外出口（代理可配置）。

Capabilities:
  - Monitor:  Poll user-profile-v3 for live_status == 1 (live now).
              When a blogger goes live, fire a callback so the GUI can notify
              the user and auto-start recording.
  - Resolve:  Automatically obtain the no-watermark stream URL (m3u8/flv) via
              Douyin's internal webcast API (with a_bogus signature).
              When auto-resolution fails (signature expired, etc.), the failure
              reason is surfaced and the user can fall back to the manual
              "手动录制" box (paste a stream URL) — see B1.
              NOTE: Just One API does NOT provide a Douyin live-stream-URL
              endpoint (only xingtu live-statistics), so no official fallback
              for the stream URL itself is possible.
  - Record:   Record a live stream to mp4 via ffmpeg. Fully automated:
              detect live → resolve stream URL → start ffmpeg → stop on offline.

Field facts (verified by live API call):
  - user-profile-v3 returns user.live_status (0=offline, 1=live, 2=playback)
  - user.room_id / user.room_id_str are non-zero when live
  - Stream URLs obtained via internal webcast API (douyin_live_stream.py)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import tiktok_live
from .client import JustOneAPICollector
from .douyin_live_stream import DouyinLiveStreamInfo, DouyinLiveStreamResolver
from .douyin_live_status_cdp import DouyinHomeLiveCDP


@dataclass
class LiveStatus:
    """Snapshot of one blogger's live status at a moment in time."""
    sec_uid: str
    nickname: str
    is_live: bool            # True if live_status == 1
    live_status: int        # raw value: 0/1/2
    room_id: str
    avatar_url: str
    follower_count: int
    last_checked: float = 0.0
    # Auto-resolved stream info (filled when is_live == True)
    stream_url: str = ""           # best available m3u8/flv URL
    hls_urls: dict = field(default_factory=dict)
    flv_urls: dict = field(default_factory=dict)
    is_recording: bool = False     # True when ffmpeg is actively recording
    record_start_time: float = 0.0  # unix timestamp when recording started
    input_type: str = "昵称"        # "昵称" or "链接" — how the user added this target
    stream_error: str = ""          # 最近一次流地址解析失败原因（自动解析失败时回填，供 UI 引导手动）
    live_url: str = ""              # 原始直播间链接 / web_rid（链接方式添加时保存，用于 HTML 兜底解析）
    # ★ v1.26.2：用户自己的备注（记赛道/用途，比如「美妆-护肤」「母婴-待对标」）。
    #   监控目标一多，光看昵称根本想不起来这个号是干嘛的 —— 备注随目标一起
    #   落盘（live_targets.json），重启不丢。新字段一律追加在末尾。
    note: str = ""
    # ★ v1.28.5：直播间平台。"douyin"（默认，兼容旧名单）/ "tiktok"。
    #   TikTok 不走 user-profile-v3，也不走抖音 webcast/a_bogus 签名。
    platform: str = "douyin"
    # ★ v1.28.14：在播主页上的直播入口 ID。19 位 room_id_str 是内部房间号，
    #   拿去 webcast 解析流地址会返回「no room data」；自动录制要用 web_rid。
    web_rid: str = ""


def _safe_filename(text: str, max_len: int = 50) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", text).strip()
    return cleaned[:max_len] if len(cleaned) > max_len else cleaned or "untitled"


def _candidate_ffmpeg_paths() -> list[Path]:
    """Build a list of common ffmpeg locations (Windows-first, safe elsewhere).

    Covers: program-bundled dir, PATH-adjacent installs, WinGet/Scoop shims,
    and the common "unzipped a build onto the Desktop" case so users who
    already downloaded ffmpeg don't have to touch their PATH.
    """
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    out: list[Path] = []

    # 1) 程序自带目录（PyInstaller 解包目录 / exe 同级 / _internal）
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        bases.append(Path(meipass))
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    for b in bases:
        out += [b / exe, b / "ffmpeg" / exe, b / "bin" / exe,
                b / "_internal" / exe, b / "_internal" / "ffmpeg" / exe]

    # 1.5) 程序数据目录内的私有 ffmpeg（安装包 / 首次运行引导会下到这里）
    #      放在 %APPDATA%\<AppDataDir>\ffmpeg 下，重装软件也不会丢。
    try:
        ad = branding.app_data_dir()
        out += [ad / "ffmpeg" / exe, ad / "ffmpeg" / "bin" / exe]
    except Exception:  # noqa: BLE001
        pass

    # 2) 常见安装位置
    home = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    prog = os.environ.get("ProgramFiles", "C:/Program Files")
    prog86 = os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
    out += [
        Path("C:/ffmpeg/bin") / exe,
        Path("C:/ffmpeg") / exe,
        Path(prog) / "ffmpeg/bin" / exe,
        Path(prog) / "ffmpeg" / exe,
        Path(prog86) / "ffmpeg/bin" / exe,
        home / "scoop/shims" / exe,
    ]
    if local:
        out += [
            Path(local) / "Microsoft/WinGet/Links" / exe,
            Path(local) / "ffmpeg/bin" / exe,
        ]
    if os.name != "nt":
        out += [Path("/usr/bin") / exe, Path("/usr/local/bin") / exe, Path("/opt/homebrew/bin") / exe]

    # 3) 桌面一级子目录（用户常见做法：解压到桌面某文件夹）
    desktop = home / "Desktop"
    if desktop.is_dir():
        try:
            for sub in desktop.iterdir():
                if sub.is_dir():
                    out += [sub / exe, sub / "ffmpeg" / exe, sub / "ffmpeg/bin" / exe,
                            sub / "bin" / exe]
        except OSError:
            pass
    return out


def find_ffmpeg(saved_path: str | None = None) -> str | None:
    """Locate the ffmpeg executable.

    搜索顺序（先精确、后宽泛）：
      1. 用户显式指定：saved_path 参数 → 环境变量 JUSTONEAPI_FFMPEG
      2. 系统 PATH（shutil.which）
      3. 常见位置：程序自带目录、C:\\ffmpeg、WinGet/Scoop shims、桌面解压目录

    Returns the resolved path string, or None when nothing usable is found.
    """
    # 1) 显式指定
    for explicit in (saved_path, os.getenv("JUSTONEAPI_FFMPEG")):
        if not explicit:
            continue
        p = Path(str(explicit).strip().strip('"').strip("'"))
        try:
            if p.is_file():
                return str(p)
        except OSError:
            pass

    # 2) PATH
    found = shutil.which("ffmpeg")
    if found:
        return found

    # 3) 常见位置
    for cand in _candidate_ffmpeg_paths():
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return None


class LiveMonitor:
    """
    Monitor Douyin bloggers for live status changes and record streams.

    Fully automated flow:
      1. Poll user-profile-v3 every N seconds
      2. When live_status flips to 1 (live), resolve the stream URL via
         Douyin's internal webcast API (a_bogus signature)
      3. Auto-start ffmpeg recording with the resolved URL
      4. When live_status flips back to 0 (offline), stop recording

    Usage:
        mon = LiveMonitor(client, auto_record=True)
        mon.add_target(sec_uid, nickname)
        mon.start_polling(interval=60, on_status_change=callback)
    """

    SAVE_SUBDIR = "JustOneAPILiveRecordings"

    def __init__(self, client: JustOneAPICollector, save_dir: Path | None = None,
                 auto_record: bool = True, ffmpeg_path: str | None = None,
                 douyin_cookie: str = "", tiktok_proxy: str | None = None,
                 cdp_fallback: bool = True):
        self.client = client
        if save_dir is None:
            desktop = Path.home() / "Desktop"
            if not desktop.exists():
                desktop = Path.home()
            save_dir = desktop / self.SAVE_SUBDIR
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        # 注意：属性名必须是 auto_record_enabled，不能叫 auto_record ——
        # 否则会遮蔽同名方法 auto_record(sec_uid)，使其调用时抛
        # TypeError: 'bool' object is not callable（历史 bug，已修）。
        self.auto_record_enabled = auto_record
        # 抖音登录 Cookie（内置浏览器扫码登录得到）：带上它解析直播流更稳
        self.douyin_cookie = (douyin_cookie or "").strip()
        self._resolver = DouyinLiveStreamResolver(cookie=self.douyin_cookie)
        # ★ 2026-11-23：付费接口失败时的免费兜底（浏览器读主页 profile 请求）。
        #   懒加载：只有真的查状态失败才起浏览器，正常启动零开销。
        self.cdp_fallback_enabled = bool(cdp_fallback)
        self._home_cdp: DouyinHomeLiveCDP | None = None
        # ★ TikTok 代理：None = 自动探测（显式配置 → Windows 系统代理 →
        #   本机常见端口）；空串 = 用户明确不用代理。这里用 quick_proxy()
        #   毫秒级取候选、**不联网验证**，否则构造会在 UI 线程卡好几秒；
        #   真正的可用性由 fetch_room 的「给定代理 → 系统代理 → 直连」兜底。
        # ★ 2026-11-19 启动性能：构造期绝不扫端口。quick_proxy() 逐个
        #   connect_ex 14 个常见端口、每个超时 0.25s = 客户没开代理时白等 3.5s。
        #   这里只同步取「显式配置 → Windows 系统代理」（读注册表，毫秒级），
        #   端口扫描交给下面那个后台线程补扫回填 —— 客户最常见的 Clash
        #   「系统代理」场景零延迟命中，纯端口代理场景后台几百毫秒内自动补上，
        #   界面不再等待。fetch_room 本身还有「给定→系统→直连」兜底，不会误判。
        self.tiktok_proxy = (tiktok_live.quick_proxy(ports=False) if tiktok_proxy is None
                             else str(tiktok_proxy or "").strip())
        # ★ 是否为「自动探测」状态：客户常常先开软件、后开代理。自动模式下
        #   代理为空不能一次定死，失败时每 2 分钟重扫一次端口（毫秒级、
        #   不联网），否则得让客户重启软件才认。用户手动保存过就不再干预。
        self._tiktok_proxy_auto = tiktok_proxy is None
        self._tiktok_rescan_at = 0.0
        # 实际走通的出口：配置为空但靠「系统代理」连通时记在这里。
        # ★ ffmpeg 不读 Windows 系统代理，录制必须显式拿到这个值，
        #   否则状态查得到、流却拉不下来。
        #   ★ 必须在下面的后台补扫线程之前初始化，避免竞态。
        self._tiktok_via = ""
        if self._tiktok_proxy_auto and not self.tiktok_proxy:
            # 后台补扫端口：不阻塞构造，扫到且仍处自动模式/当前为空才回填。
            def _probe_ports():
                try:
                    found = tiktok_live.quick_proxy(ports=True)
                except Exception:                # noqa: BLE001
                    return
                if (found and self._tiktok_proxy_auto
                        and not self.tiktok_proxy):
                    self.tiktok_proxy = found
                    self._tiktok_via = ""

            import threading as _th
            _th.Thread(target=_probe_ports, name="tiktok-proxy-probe",
                       daemon=True).start()
        # 浏览器会话懒加载且复用，避免每个轮询周期都重过一次 WAF 挑战
        self._tiktok_browser: tiktok_live.TikTokLiveBrowser | None = None

        self._targets: dict[str, LiveStatus] = {}   # sec_uid -> LiveStatus
        self._polling = False
        self._poll_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

        # Active recordings: sec_uid -> (key, subprocess.Popen, part_path)
        # 注意 part_path 是录制中的临时文件（*.mp4.part），
        # 停止并校验通过后才会改名为正式的 *.mp4。
        self._active_procs: dict[str, tuple[str, subprocess.Popen, Path]] = {}
        # key -> ffmpeg 日志文件句柄
        self._proc_logs: dict[str, object] = {}
        # 最近一次 stop_recording / reap_finished 的明细报告（供 UI 展示）
        self.last_stopped: list[dict] = []

        # ★ 权威录制登记表（本次修复的核心）
        # 历史上 record_start_time / is_recording 只在「自动录制」这一条路径里
        # 赋值，手动粘贴流地址录制、以及通过链接添加的目标都是空的 —— 于是
        # 界面出现「明明在录、录制时长却永远是 00:00」和「录完了还显示录制中」。
        # 现在统一在这里登记：只要 start_recording 成功，就往 _rec_start 写时间戳，
        # UI 一律以它为准（reconcile_targets / recording_elapsed），
        # 不再依赖某一个调用点是否记得给 LiveStatus 赋值。
        self._rec_start: dict[str, float] = {}      # key -> 开始录制的 unix 时间戳
        self._rec_meta: dict[str, dict] = {}        # key -> {sec_uid, name, part}
        self._rec_lock = threading.Lock()

        self._ffmpeg = find_ffmpeg(ffmpeg_path)
        # 屏幕录制用的编码器缓存（首次探测后填；优先 h264_nvenc 硬编）
        self._encoders: set | None = None

        # ★ v1.26.2：从本机 live_targets.json 恢复上次监控的目标（含备注）。
        #   历史行为是纯内存 —— 关掉软件再打开，加了十几个对标号全没了，
        #   备注更是无从谈起。这里只恢复「名单」，开播状态靠后续轮询刷新。
        self._load_state()

    # ------------------------------------------------------------------
    #  目标名单持久化（%APPDATA%/TrafficCatcher/live_targets.json）
    # ------------------------------------------------------------------
    @staticmethod
    def _state_path() -> Path:
        """监控名单落盘路径。用品牌数据目录，跟 config.json / 授权同一处。"""
        try:
            from . import branding
            return branding.app_data_dir() / "live_targets.json"
        except Exception:                   # noqa: BLE001
            base = os.environ.get("APPDATA") or str(Path.home())
            return Path(base) / "TrafficCatcher" / "live_targets.json"

    def _load_state(self) -> int:
        """恢复上次保存的监控目标。返回恢复条数；失败一律返回 0（不抛异常）。

        只恢复「能重建名单」的字段：sec_uid / nickname / input_type / live_url /
        room_id / note。开播状态、录制状态是运行时数据，恢复成初始值。
        """
        path = self._state_path()
        try:
            if not path.exists():
                return 0
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = raw.get("targets") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                return 0
        except Exception:                   # noqa: BLE001
            return 0
        n = 0
        for it in items:
            try:
                if not isinstance(it, dict):
                    continue
                sec_uid = str(it.get("sec_uid") or "").strip()
                if not sec_uid:
                    continue
                self._targets[sec_uid] = LiveStatus(
                    sec_uid=sec_uid,
                    nickname=str(it.get("nickname") or ""),
                    is_live=False, live_status=0,
                    room_id=str(it.get("room_id") or "0"),
                    avatar_url="", follower_count=0,
                    input_type=str(it.get("input_type") or "昵称"),
                    live_url=str(it.get("live_url") or ""),
                    note=str(it.get("note") or ""),
                    web_rid=str(it.get("web_rid") or ""),
                    # 旧版名单没有 platform 字段：从伪 ID 反推，绝不丢目标
                    platform=str(it.get("platform") or
                                 ("tiktok" if sec_uid.startswith("tiktok:")
                                  else "douyin")),
                )
                n += 1
            except Exception:               # noqa: BLE001
                continue
        return n

    def _save_state(self) -> bool:
        """把当前监控名单（含备注）落盘。失败返回 False，绝不抛异常。"""
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"saved": int(time.time()), "targets": [
                {"sec_uid": t.sec_uid, "nickname": t.nickname,
                 "input_type": t.input_type, "room_id": t.room_id,
                 "live_url": t.live_url, "note": t.note,
                 "platform": t.platform,
                 "web_rid": getattr(t, "web_rid", "")}
                for t in self._targets.values()
            ]}
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(str(tmp), str(path))     # 原子替换，写一半断电也不毁文件
            return True
        except Exception:                   # noqa: BLE001
            return False

    def set_note(self, sec_uid: str, note: str) -> bool:
        """给监控目标写备注（记赛道/用途）并立即落盘。目标不存在返回 False。"""
        st = self._targets.get(str(sec_uid or ""))
        if st is None:
            return False
        st.note = str(note or "")
        self._save_state()
        return True

    def get_note(self, sec_uid: str) -> str:
        """读取某个目标的备注；没有目标或没填过都返回空串。"""
        st = self._targets.get(str(sec_uid or ""))
        return (st.note if st is not None else "") or ""

    def set_ffmpeg_path(self, path: str | None) -> str | None:
        """Re-detect ffmpeg with an optional explicit path. Returns the resolved path or None.

        Lets the GUI apply a user-chosen ffmpeg.exe immediately, without
        recreating the whole monitor (which would drop monitoring targets).
        """
        self._ffmpeg = find_ffmpeg(path)
        return self._ffmpeg

    def set_douyin_cookie(self, cookie: str):
        """运行时把扫码登录拿到的 Cookie 应用到直播流解析器（不必重建 monitor）。"""
        self.douyin_cookie = (cookie or "").strip()
        try:
            self._resolver.set_cookie(self.douyin_cookie)
        except Exception:                   # noqa: BLE001
            pass
        if self._home_cdp is not None:
            try:
                self._home_cdp.set_cookie(self.douyin_cookie)
            except Exception:               # noqa: BLE001
                pass

    def home_cdp(self) -> DouyinHomeLiveCDP:
        """免费兜底查询器（单例）。门禁/测试可直接替换 self._home_cdp。"""
        if self._home_cdp is None:
            self._home_cdp = DouyinHomeLiveCDP(cookie=self.douyin_cookie)
        return self._home_cdp

    def ffmpeg_path(self) -> str:
        """Currently resolved ffmpeg path ('' when not found)."""
        return self._ffmpeg or ""

    # ==================================================================
    #  Monitoring
    # ==================================================================

    def set_tiktok_proxy(self, proxy: str | None) -> str:
        """界面改完代理立即生效；重建浏览器会话，避免旧会话还走老出口。"""
        self.tiktok_proxy = (tiktok_live.quick_proxy() if proxy is None
                             else str(proxy or "").strip())
        self._tiktok_proxy_auto = proxy is None   # 手动保存后不再自动改
        self._tiktok_via = ""               # 换了出口，旧记录作废
        try:
            if self._tiktok_browser:
                self._tiktok_browser.close()
        except Exception:                   # noqa: BLE001
            pass
        self._tiktok_browser = None
        return self.tiktok_proxy

    def _tiktok_egress(self) -> str:
        """录制该用的出口：优先用户配置，其次上次真走通的那个。

        ★ 客户没填代理、但系统代理能出海时，`tiktok_proxy` 是空的；
          ffmpeg 又不读 Windows 系统代理，不把这个补上就会「状态查得到、
          录制拉不到流」。
        """
        return (self.tiktok_proxy or self._tiktok_via or "").strip()

    def _remember_egress(self, snap) -> None:
        """记住本次真走通的出口；「直连」表示不需要代理（TUN / 全局模式）。"""
        via = str(getattr(snap, "via", "") or "")
        self._tiktok_via = "" if via in ("", "直连") else via

    def add_target(self, sec_uid: str, nickname: str = "", input_type: str = "昵称",
                   note: str = ""):
        """Add a blogger to monitor by sec_uid.

        note：可选的备注（记赛道/用途），跟目标一起落盘。
        """
        self._targets[sec_uid] = LiveStatus(
            sec_uid=sec_uid, nickname=nickname, is_live=False,
            live_status=0, room_id="0", avatar_url="", follower_count=0,
            input_type=input_type, note=str(note or ""),
        )
        self._save_state()

    def _add_tiktok_target(self, raw: str, note: str = "") -> tuple[bool, str, str]:
        """TikTok 目标：零网络快路径，只登记 handle + 归一化链接。

        真实昵称 / roomId / 开播状态由 _check_tiktok 异步回填（首次会过一次
        浏览器 WAF 挑战，约 10~20 秒），绝不在这里阻塞「添加」这个动作。
        """
        handle = tiktok_live.normalize_handle(raw)
        if not handle:
            return False, "TikTok 链接里没认出主播号，请贴完整直播间链接或 @主播号", ""
        sec_uid = f"tiktok:{handle}"
        url = tiktok_live.live_url(handle)
        if sec_uid in self._targets:
            st = self._targets[sec_uid]
            st.platform = "tiktok"
            st.live_url = url
            if note:                       # 重复添加只补备注，不覆盖空备注
                st.note = str(note)
            self._save_state()
            return True, f"@{handle}", sec_uid
        self.add_target(sec_uid, f"@{handle}", input_type="链接", note=note)
        st = self._targets[sec_uid]
        st.platform = "tiktok"
        st.live_url = url
        st.room_id = ""                   # 由 _check_tiktok 首次回填
        self._save_state()
        return True, f"@{handle}", sec_uid

    def add_target_by_link(self, live_url: str, note: str = "") -> tuple[bool, str, str]:
        """
        通过抖音直播间链接 / web_rid 添加监控目标。

        ★ 2026-11-05：直播间 URL 与纯 web_rid 走**纯正则快路径**（零网络、
        点击即出结果）；只有短链 / 主页 URL 需要跟随重定向时才调
        DouyinLiveStreamResolver.resolve_auto（web enter 优先 + HTML 兜底）。
        真实昵称与开播状态不在这里取，由后续的 check_once 异步回填。

        Returns (success, nickname_or_error, sec_uid)。
        """
        import re

        s = (live_url or "").strip()
        # ★ 2026-11-23：客户常把抖音分享文案整段粘进来（例：
        #   「搜索，查看TA的更多作品。 https://v.douyin.com/xxx/」）。
        #   解析器只认纯 URL，整段文字会被当 URL 请求而失败，最后报
        #   「无法从链接/ID 提取 web_rid」。先把文案里的 http(s) 链接抠出来。
        m_url = re.search(r"https?://[^\s\u4e00-\u9fff]+", s)
        if m_url:
            s = m_url.group(0).rstrip(".,;，。；")
        # ★ TikTok 直播间链接 / @主播号走独立适配层，绝不丢给抖音解析器
        #   （旧行为会把链接原样交给抖音 webcast，客户只看到「无法提取 web_rid」）。
        if tiktok_live.is_tiktok_input(s):
            return self._add_tiktok_target(s, note)

        # ★ 2026-11-23：抖音「账号主页」监控修复。
        # 主页长链 / 分享文本 / 纯 sec_uid 里带 MS4wLj...，过去会被下面的
        # `[A-Za-z0-9_-]{6,}` 正则吞成 web_rid:xxx，后续按直播间查，永远
        # 查不到状态（客户表现为「主页监控加不上/加上了也不动」）。
        # 这类输入必须登记成**真实账号目标**，走 user-profile-v3 查状态。
        m_sec = re.search(r"MS4wLj[A-Za-z0-9_\-]+", s)
        if m_sec and "live.douyin.com/" not in s:
            return self._add_sec_uid_target(m_sec.group(0), note)

        web_rid = ""
        nickname = ""
        # ★ 2026-11-05 客户现场「点添加监控无反应」另一半根因：添加这个动作
        #   本身只需要 web_rid，一个正则就能拿到；以前却先调 resolve_auto
        #   联网解析（web enter + HTML 兜底，最长 30 秒），期间界面毫无反馈。
        #   直播间 URL / 纯 web_rid 走**零网络快路径**，秒加；真实昵称与
        #   开播状态由 LivePage._probe_after_add 的后台检查回填（链路已有）。
        m = re.search(r"live\.douyin\.com/([^?/\s#]+)", s)
        if m:
            web_rid = m.group(1)
        elif re.fullmatch(r"[A-Za-z0-9_-]{6,}", s):
            web_rid = s
        if not web_rid:
            # 只有短链 / 主页 URL 才需要跟随重定向（联网，各段自带超时）
            info = self._resolver.resolve_auto(s)
            # ★ 2026-11-23：短链若重定向到的是**账号主页**（不是直播间），
            # 解析器会把 sec_uid 放在 info.sec_uid 里，这里按账号登记。
            # 注意 getattr：旧桩/第三方 resolver 可能没有这个字段。
            home_uid = getattr(info, "sec_uid", "") or ""
            if home_uid.startswith("MS4wLj"):
                return self._add_sec_uid_target(home_uid, note)
            # ★ 2026-11-06：必须用重定向得到的真 web_rid。info.room_id 是
            #   webcast 返回的真实房间号，拿它拼 live.douyin.com/<room_id>
            #   打不开，后续 check_once 会永远查不到状态（表现为「未开播」）。
            web_rid = getattr(info, "web_rid", "") or info.room_id or ""
            nickname = info.anchor_name or ""
            if not web_rid:
                m2 = re.search(r"live\.douyin\.com/([^?/\s#]+)", s)
                if m2:
                    web_rid = m2.group(1)
                elif s.isdigit():
                    web_rid = s
        if not web_rid:
            return False, "无法从链接/ID 提取 web_rid，请检查格式", ""

        # 链接类主播用 web_rid 作伪 sec_uid（后续 resolve_stream_url 走 resolve_auto）
        sec_uid = f"web_rid:{web_rid}"
        nickname = nickname or f"直播间{web_rid}"
        if sec_uid in self._targets:
            self._targets[sec_uid].live_url = live_url
            if note:                       # 重复添加时只补备注，不覆盖空备注
                self._targets[sec_uid].note = str(note)
            self._save_state()
            return True, nickname, sec_uid
        self.add_target(sec_uid, nickname, input_type="链接", note=note)
        self._targets[sec_uid].room_id = web_rid
        self._targets[sec_uid].live_url = live_url
        self._save_state()
        return True, nickname, sec_uid

    def _add_sec_uid_target(self, sec_uid: str, note: str = "") -> tuple[bool, str, str]:
        """登记真实抖音账号（sec_uid）。

        ★ 绝不给这类目标写 live_url：check_once 一看到 live_url 就会走
          `_check_by_link` 按直播间 ID 解析，账号目标会被误判成「未开播」。
        """
        sec_uid = (sec_uid or "").strip()
        if not sec_uid:
            return False, "没识别到博主 sec_uid，请粘贴完整主页链接", ""
        if sec_uid in self._targets:
            if note:
                self._targets[sec_uid].note = str(note)
        else:
            self.add_target(sec_uid, f"博主{sec_uid[:12]}…",
                            input_type="主页", note=note)
        self._save_state()
        return True, self._targets[sec_uid].nickname, sec_uid

    def remove_target(self, sec_uid: str):
        self._targets.pop(sec_uid, None)
        self._save_state()                  # 移除也要落盘，否则重启后目标复活

    def get_targets(self) -> list[LiveStatus]:
        return list(self._targets.values())

    def check_once(self, sec_uid: str) -> LiveStatus | None:
        """Single-shot check of one blogger's live status."""
        st = self._targets.get(sec_uid)

        # ★ TikTok 目标走独立链路：不调 JustOneAPI、不进付费闸门
        if sec_uid.startswith("tiktok:") or (st is not None and st.platform == "tiktok"):
            return self._check_tiktok(sec_uid, st)

        # 链接方式添加的目标：sec_uid 是伪 ID（web_rid:xxx），user-profile-v3
        # 查不到它。改走直播间解析路径——既能判断开播，又能顺带拿到真实
        # 昵称并缓存流地址（供开播自动录制直接使用）。
        if sec_uid.startswith("web_rid:") or (st is not None and getattr(st, "live_url", "")):
            return self._check_by_link(sec_uid, st)

        # ★ 2026-11-23：okdata 的账号资料接口不返回开播字段，拿它查直播会
        #   把在播误判成「未开播」，还要花 $0.002。本机免费 CDP 已实测
        #   4/4 准确且能回填 web_rid 取流，所以在 okdata 通道下直接走免费。
        if (getattr(self.client, "provider", "") == "okdata"
                and sec_uid.startswith("MS4wLj") and self.cdp_fallback_enabled):
            return self._check_by_cdp(sec_uid, st)

        resp = self.client.request("douyin-tiktok-china", "user-profile-v3",
                                   {"secUid": sec_uid})
        if not resp.success or not isinstance(resp.data, dict):
            # ★ 2026-11-23：过去直接 return None，界面只剩「接口未返回状态」，
            #   客户根本不知道是**账户余额不足**（实测 code=601
            #   INSUFFICIENT BALANCE）。这里把真实原因写进 stream_error，
            #   界面会显示「状态未知」并把原因放进悬浮提示，绝不冒充没开播。
            msg = str(getattr(resp, "message", "") or "").strip()
            code = getattr(resp, "code", None)
            if st is None:
                st = LiveStatus(
                    sec_uid=sec_uid, nickname="", is_live=False,
                    live_status=0, room_id="0", avatar_url="",
                    follower_count=0,
                )
                self._targets[sec_uid] = st
            st.last_checked = time.time()
            if "INSUFFICIENT BALANCE" in msg.upper() or code == 601:
                hint = str(getattr(self.client, "recharge_hint", "")
                           or "api.justoneapi.com")
                base_error = (f"状态检查失败：采集账户余额不足，"
                              f"请到 {hint} 充值后重试")
            elif getattr(self.client, "token_invalid", False):
                base_error = "状态检查失败：API Token 无效或未激活，请到设置页更新"
            else:
                base_error = (
                    f"状态检查失败：接口未返回数据（{msg or 'code=' + str(code)}）")

            # ★ 2026-11-23：付费查不到时走**免费浏览器通道**兜底。
            # 主页 profile 请求里有同名字段（live_status/room_id_str），
            # 客户没充值也能知道有没有开播、开播了还能自动录制。
            # 只有真实账号目标（MS4wLj...）能走这条；web_rid/TikTok 目标
            # 原本就不走付费接口，不会到这里。
            snap = None
            if self.cdp_fallback_enabled and sec_uid.startswith("MS4wLj"):
                try:
                    snap = self.home_cdp().fetch(sec_uid)
                except Exception as exc:                # noqa: BLE001
                    snap = None
                    base_error += f"（免费通道异常：{exc}）"
            if snap is not None and self._apply_cdp_snapshot(st, snap):
                return st
            # 兜底也失败：保留「状态未知」语义，把两条原因都说清楚
            if snap is not None and getattr(snap, "error", ""):
                base_error += f"；免费通道：{snap.error}"
            st.stream_error = base_error[:400]
            return st
        user = resp.data.get("user", {}) or {}
        if st is None:
            st = LiveStatus(
                sec_uid=sec_uid, nickname=user.get("nickname", ""),
                is_live=False, live_status=0, room_id="0",
                avatar_url="", follower_count=0,
            )
            self._targets[sec_uid] = st
        st.nickname = user.get("nickname", st.nickname)
        st.live_status = int(user.get("live_status", 0) or 0)
        st.is_live = (st.live_status == 1)
        st.room_id = str(user.get("room_id_str") or user.get("room_id") or "0")
        av = user.get("avatar_medium") or user.get("avatar_larger") or {}
        urls = av.get("url_list") or []
        st.avatar_url = urls[0] if urls else st.avatar_url
        st.follower_count = int(user.get("follower_count", 0) or 0)
        st.last_checked = time.time()
        return st

    def _apply_cdp_snapshot(self, st: LiveStatus, snap) -> bool:
        """把免费浏览器查到的主页状态写进 LiveStatus；成功返回 True。"""
        if snap is None or not getattr(snap, "ok", False):
            return False
        st.nickname = getattr(snap, "nickname", "") or st.nickname
        st.live_status = int(getattr(snap, "live_status", 0) or 0)
        st.is_live = (st.live_status == 1)
        room = str(getattr(snap, "room_id", "") or "")
        if room and room != "0":
            st.room_id = room
        web_rid = str(getattr(snap, "web_rid", "") or "")
        if web_rid:
            st.web_rid = web_rid
        st.stream_error = ""
        if not st.is_live:
            st.stream_url = ""
            st.hls_urls = {}
            st.flv_urls = {}
        st.last_checked = time.time()
        return True

    def _check_by_cdp(self, sec_uid: str, st: LiveStatus | None) -> LiveStatus | None:
        """okdata 通道下的抖音开播检查：直接走免费主页 CDP，零付费调用。"""
        if st is None:
            st = LiveStatus(
                sec_uid=sec_uid, nickname="", is_live=False,
                live_status=0, room_id="0", avatar_url="", follower_count=0,
            )
            self._targets[sec_uid] = st
        st.last_checked = time.time()
        snap = None
        try:
            snap = self.home_cdp().fetch(sec_uid)
        except Exception as exc:                          # noqa: BLE001
            st.stream_error = f"状态检查失败：免费通道异常：{exc}"[:400]
            return st
        if self._apply_cdp_snapshot(st, snap):
            return st
        detail = str(getattr(snap, "error", "") or "免费通道未返回状态")
        st.stream_error = f"状态检查失败：{detail}"[:400]
        return st

    def _check_tiktok(self, sec_uid: str, st: LiveStatus | None) -> LiveStatus | None:
        """TikTok 状态检查：必要时先过 WAF 取 roomId，再查 webcast。

        三态约定沿用抖音链路：接口失败 / 状态未知一律**保留上次 is_live**，
        写「状态检查失败：…」让界面显示「状态未知」，绝不冒充「未开播」。
        下播后 /live 页仍给 roomId 且标题仍写「正在直播」，故状态只认接口。
        """
        if st is None:
            return None
        handle = sec_uid.split(":", 1)[-1]
        # ★ 自动模式 + 上次失败 + 当前没代理：客户可能刚把代理软件打开。
        #   每 2 分钟重扫一次端口（只是 connect_ex，不联网），扫到就换出口。
        if (self._tiktok_proxy_auto and not self.tiktok_proxy
                and st.stream_error
                and time.time() - self._tiktok_rescan_at > 120):
            self._tiktok_rescan_at = time.time()
            found = tiktok_live.quick_proxy()
            if found:
                self.tiktok_proxy = found
                self._tiktok_via = ""
                st.stream_error = ""        # 换了出口，重新给一次机会
        if not st.room_id or st.room_id == "0":
            try:
                if self._tiktok_browser is None:
                    self._tiktok_browser = tiktok_live.TikTokLiveBrowser(
                        proxy=self.tiktok_proxy)
                html = self._tiktok_browser.fetch_live_page(handle)
                info = tiktok_live.extract_page_info(html)
                if not info.ok:
                    # 页面拿到了却没有直播数据结构：多半是根本没在播
                    # （TikTok 下播后 /live 会落到主播主页）。有据可依才改状态。
                    if tiktok_live.looks_offline_page(html):
                        st.is_live = False
                        st.live_status = 0
                        st.room_id = "0"
                        st.stream_error = "未开播（该主播当前没有直播）"
                    else:
                        st.stream_error = ("状态检查失败：直播间页里没找到 roomId"
                                           "（可能需要登录或页面结构改版）")
                    st.last_checked = time.time()
                    return st
                st.room_id = info.room_id
                if info.nickname:
                    st.nickname = info.nickname
            except Exception as exc:                        # noqa: BLE001
                st.stream_error = f"状态检查失败：{exc}"
                st.last_checked = time.time()
                return st                                  # 保留上次 is_live

        snap = tiktok_live.fetch_room(st.room_id, self.tiktok_proxy)
        st.last_checked = time.time()
        if snap.error or snap.status == tiktok_live.STATUS_UNKNOWN:
            st.stream_error = (f"状态检查失败：{snap.error or '接口没给出明确的在播/下播状态'}")
            if snap.status_code == tiktok_live.ROOM_INVALID:
                st.room_id = ""                            # 换了一场直播，下轮重取
            return st                                      # 保留上次 is_live

        live = snap.is_live
        st.is_live = live
        st.live_status = 1 if live else 0
        st.stream_error = ""
        self._remember_egress(snap)                        # 录制侧要复用同一出口
        if live:
            st.stream_url = snap.stream_url
            st.hls_urls = {f"hls_{i}": u for i, u in enumerate(snap.stream_urls)}
            st.flv_urls = {}
            if not st.stream_url:
                st.stream_error = "正在直播，但接口没给可用的流地址；可点「录制选中主播」重试"
        else:
            # ★ 实测 11-14：下播后旧流地址立刻 404（sign/expire 没过期也没用）
            st.stream_url = ""
            st.hls_urls = {}
            st.flv_urls = {}
        if snap.owner:
            st.nickname = f"@{snap.owner}"
        return st

    def _check_by_link(self, sec_uid: str, st: LiveStatus | None) -> LiveStatus | None:
        """链接方式添加的目标：经直播间解析判断开播状态。

        这类目标的 sec_uid 是伪 ID（web_rid:xxx），user-profile-v3 无法查询，
        故改用 webcast 接口解析。顺便完成三件事：
          1. 判断是否开播（is_live / live_status）
          2. 回填真实昵称（解决「直播间<数字>」这种难看兜底名）
          3. 缓存流地址（开播自动录制时可直接复用，不必二次解析）

        ★ 2026-11-06 客户「账号正在直播、工具却显示未开播」修复：
          ① 旧判定是 `is_live AND 有流地址` —— 只要流地址没取到（风控、
             付费连麦、签名失效）就把正在直播判成未开播。开播状态只看
             webcast 的 raw_status / is_live，流地址是**另一件事**。
          ② 解析抛异常、或只拿到「未找到 roomStore」这类错误信息时，
             过去把 is_live 留成 False ⇒ 客户看到「未开播」。现在改为
             **保留上一次状态** + 写入「状态检查失败：…」，由界面显示
             「状态未知」并提示重试，绝不把「没查到」冒充「确认没播」。
          ★ 约定：stream_error 以「状态检查失败：」开头 == 状态未知。
        """
        if st is None:
            return None
        target_url = getattr(st, "live_url", "") or (
            f"https://live.douyin.com/{sec_uid.split(':', 1)[-1]}"
        )
        try:
            info = self._resolver.resolve_auto(target_url)
        except Exception as e:                          # noqa: BLE001
            st.stream_error = f"状态检查失败：解析异常 {e}"
            st.last_checked = time.time()
            return st                                  # 保留上次 is_live

        st.last_checked = time.time()
        # webcast 明确给出状态码（2=在播 / 4=已结束）才算「查到了」；
        # raw_status=0 + error 只说明这次没拿到数据，不是没开播。
        status_known = (getattr(info, "raw_status", 0) != 0) or bool(info.is_live)
        if not status_known:
            st.stream_error = f"状态检查失败：{info.error or '未取到直播间状态'}"
            return st                                  # 保留上次 is_live

        live = bool(info.is_live) or info.raw_status == 2
        st.is_live = live
        st.live_status = 1 if live else 0
        st.stream_error = ""
        if live:
            st.hls_urls = info.hls_pull_urls or {}
            st.flv_urls = info.flv_pull_urls or {}
            if st.flv_urls or st.hls_urls:
                try:
                    st.stream_url = DouyinLiveStreamResolver.pick_best_url(info) or ""
                except Exception:                       # noqa: BLE001
                    st.stream_url = ""
            else:
                st.stream_url = ""
                st.stream_error = ("正在直播，但未取到流地址（直播间类型或风控限制）；"
                                   "可点「录制选中主播」重试")
        else:
            st.stream_url = ""
            st.hls_urls = {}
            st.flv_urls = {}
        if info.room_id:
            st.room_id = str(info.room_id)
        if info.anchor_name:
            st.nickname = info.anchor_name
        return st

    def _tiktok_stream_url(self, sec_uid: str, st: LiveStatus) -> str | None:
        """开录前现取流地址：缓存地址可能已因下播/换场失效（实测 404）。"""
        if not st.room_id or st.room_id == "0":
            self.check_once(sec_uid)                       # 先过 WAF 补 room_id
        if not st.room_id or st.room_id == "0":
            st.stream_error = (st.stream_error or
                               "还没取到 TikTok 房间号，请先点「立即检查一次」")
            return None
        snap = tiktok_live.fetch_room(st.room_id, self.tiktok_proxy)
        if snap.error:
            st.stream_error = f"TikTok 流地址获取失败：{snap.error}"
            return None
        if not snap.is_live or not snap.stream_url:
            # 下播瞬间 webcast 还会给一个已撤销的死链，明确说清原因，
            # 别让客户以为是软件坏了（实测 11-15）
            st.stream_error = ("主播刚下播，直播流已撤销，等下次开播会自动重试"
                               if snap.is_offline else
                               "主播当前未开播（或接口没给流地址），开播后会自动重试")
            return None
        st.stream_url = snap.stream_url
        st.hls_urls = {f"hls_{i}": u for i, u in enumerate(snap.stream_urls)}
        st.flv_urls = {}
        st.stream_error = ""
        self._remember_egress(snap)
        return st.stream_url

    def resolve_stream_url(self, sec_uid: str) -> str | None:
        """
        Auto-resolve the no-watermark stream URL for a live blogger.

        优先用抖音内部 webcast 接口（本地 a_bogus 签名）自动解析；解析
        失败时把原因回填到 st.stream_error，供 auto_record / GUI 给出清晰
        提示并引导用户改用「手动录制」框粘贴流地址（B1）。

        注意：Just One API 不提供抖音直播流地址解析接口（仅有星图直播
        统计类接口），故无法用官方接口兜底流地址；自动解析依赖本地逆向，
        存在被抖音更新签名算法致使失效的风险。
        """
        st = self._targets.get(sec_uid)
        if not st:
            return None

        # ★ TikTok 开录前一律重取流地址，绝不复用检查时缓存的旧地址
        if st.platform == "tiktok":
            return self._tiktok_stream_url(sec_uid, st)

        # 链接方式添加的主播：直接走 resolve_auto（web enter 优先 + HTML 兜底），最稳
        if getattr(st, "live_url", ""):
            try:
                info = self._resolver.resolve_auto(st.live_url)
                errors = []
                if info.error:
                    errors.append(info.error)
                if info.is_live and (info.flv_pull_urls or info.hls_pull_urls):
                    st.hls_urls = info.hls_pull_urls
                    st.flv_urls = info.flv_pull_urls
                    st.stream_url = DouyinLiveStreamResolver.pick_best_url(info)
                    st.stream_error = ""
                    return st.stream_url
            except Exception as e:
                errors = [f"解析异常: {e}"]
            st.stream_error = (
                "；".join(errors)
                if errors
                else "自动解析未返回流地址（博主未开播或直播间类型不支持网页录制）"
            )
            return None

        # 昵称/搜索添加的主播（只有 sec_uid + room_id）：走 app reflow + web enter 兜底
        if not st.room_id or st.room_id == "0":
            st.stream_error = (
                "尚未获取到房间号。请先点「立即检查一次」确认主播直播状态，"
                "或改用直播间链接方式添加监控（链接方式解析更稳定）"
            )
            return None

        errors: list[str] = []
        # ★ 2026-11-23：CDP 免费兜底从在播主页拿到 web_rid 时优先用它。
        # 19 位 room_id_str 是内部房间号，app/web 两条路都返回「no room data」
        # （实测），只有短数字 web_rid 能拿到真实流地址。
        web_rid = str(getattr(st, "web_rid", "") or "")
        if web_rid:
            try:
                wi = self._resolver.resolve_by_web_rid(web_rid)
                if wi.error:
                    errors.append(wi.error)
                if wi.is_live and (wi.flv_pull_urls or wi.hls_pull_urls):
                    st.hls_urls = wi.hls_pull_urls
                    st.flv_urls = wi.flv_pull_urls
                    st.stream_url = DouyinLiveStreamResolver.pick_best_url(wi) or ""
                    st.stream_error = ""
                    return st.stream_url
            except Exception as e:                  # noqa: BLE001
                errors.append(f"web_rid 解析异常: {e}")

        try:
            # 优先 app 接口（已有 room_id + sec_uid）
            info = self._resolver.resolve_by_app(st.room_id, sec_uid)
            if info.error:
                errors.append(info.error)
            if info.is_live and (info.flv_pull_urls or info.hls_pull_urls):
                st.hls_urls = info.hls_pull_urls
                st.flv_urls = info.flv_pull_urls
                st.stream_url = DouyinLiveStreamResolver.pick_best_url(info)
                st.stream_error = ""
                return st.stream_url

            # 兜底：web 接口（以 room_id 当作 web_rid 尝试）
            info2 = self._resolver.resolve_by_web_rid(st.room_id)
            if info2.error:
                errors.append(info2.error)
            if info2.is_live and (info2.flv_pull_urls or info2.hls_pull_urls):
                st.hls_urls = info2.hls_pull_urls
                st.flv_urls = info2.flv_pull_urls
                st.stream_url = DouyinLiveStreamResolver.pick_best_url(info2)
                st.stream_error = ""
                return st.stream_url
        except Exception as e:
            # 解析过程中的任何异常都不应让监控线程崩溃，记录后降级
            errors.append(f"解析异常: {e}")

        # 记录失败原因，供 auto_record / GUI 给出清晰提示
        st.stream_error = (
            "；".join(errors)
            if errors
            else "自动解析未返回流地址（博主未开播或直播间类型不支持网页录制）"
        )
        return None

    def auto_record(self, sec_uid: str) -> tuple[bool, str]:
        """
        Auto-resolve stream URL and start recording for a live blogger.

        Returns (success, message).
        """
        st = self._targets.get(sec_uid)
        if not st:
            return False, "target not found"

        # Already recording this blogger?
        if sec_uid in self._active_procs:
            return True, "already recording"

        # TikTok 一律重取（旧地址下播即 404）；抖音可复用刚检查时缓存的地址
        url = (self.resolve_stream_url(sec_uid)
               if st.platform == "tiktok"
               else (st.stream_url or self.resolve_stream_url(sec_uid)))
        if not url:
            err = getattr(st, "stream_error", "") or "未返回可用的直播流地址"
            # 「尚未获取到房间号」本身就是可操作的引导语，原样返回
            if "尚未获取到房间号" in err:
                return False, err
            # 未开播 / 直播间无 roomStore → 本质都是"当前没在播"，别丢技术细节给用户
            if any(k in err for k in ("未开播", "no room", "not live", "roomStore")):
                return False, "博主当前未开播（或直播已结束），暂无法录制；开播后再点「● 录制选中主播」即可。"
            return False, (
                f"自动解析直播流失败：{err}。"
                f"可在本页「高级选项」点「从选中主播解析流地址」重试。"
            )

        name = f"直播_{st.nickname}_{int(time.time())}"
        ok, msg = self.start_recording(url, name, sec_uid=sec_uid)
        # ★ TikTok 一次给出多个档位（HLS 各清晰度 / FLV）。单个档位被 CDN 拒
        #   或拉不到数据很常见，换个地址往往就通了 —— 客户自建节点尤其明显。
        if not ok and st.platform == "tiktok":
            tried = {url}
            for alt in list(getattr(st, "hls_urls", {}).values()):
                if not alt or alt in tried:
                    continue
                tried.add(alt)
                ok, msg = self.start_recording(alt, name, sec_uid=sec_uid)
                if ok:
                    msg = "（主地址拉不到数据，已自动换备用地址）" + msg
                    break
        if ok:
            # 开始时间以登记表为准（ffmpeg 真实被拉起的时刻）。
            # 这里绝不能再用 time.time()：start_recording 内部的健康检查会阻塞
            # 数秒，用它会让界面上的录制时长凭空少掉几秒。
            st.is_recording = True
            st.record_start_time = self.recording_started_at(sec_uid) or time.time()
            return True, msg
        return False, msg

    def check_all(self) -> list[LiveStatus]:
        """Check all targets once. Returns the updated statuses."""
        results = []
        for sec_uid in list(self._targets.keys()):
            st = self.check_once(sec_uid)
            if st:
                results.append(st)
        return results

    def start_polling(
        self,
        interval: int = 60,
        on_status_change: Callable[[LiveStatus], None] | None = None,
        on_tick: Callable[[int, int], None] | None = None,
        on_auto_record: Callable[[str, str, str], None] | None = None,
    ):
        """
        Start background polling every `interval` seconds.

        Callbacks:
          on_status_change(live_status) — fires when is_live flips.
          on_tick(checked, total)      — fires after each full sweep.
          on_auto_record(sec_uid, status, msg) — fires when auto-record
              starts/stops. status is "start" or "stop" or "fail".
        """
        if self._polling:
            return
        self._polling = True
        self._stop_flag.clear()

        def loop():
            while not self._stop_flag.is_set():
                total = len(self._targets)
                checked = 0
                for sec_uid in list(self._targets.keys()):
                    if self._stop_flag.is_set():
                        break
                    prev = self._targets.get(sec_uid)
                    prev_live = prev.is_live if prev else False
                    st = self.check_once(sec_uid)
                    if st and st.is_live != prev_live:
                        if st.is_live and self.auto_record_enabled and self._ffmpeg:
                            # Just went live → auto-resolve + record
                            def do_record(su=sec_uid):
                                ok, msg = self.auto_record(su)
                                if on_auto_record:
                                    try:
                                        status = "start" if ok else "fail"
                                        on_auto_record(su, status, msg)
                                    except Exception:
                                        pass
                            threading.Thread(target=do_record, daemon=True).start()
                        elif not st.is_live and sec_uid in self._active_procs:
                            # Just went offline → stop recording
                            self.stop_recording(sec_uid)
                            st.is_recording = False
                            st.record_start_time = 0.0
                            if on_auto_record:
                                try:
                                    on_auto_record(sec_uid, "stop", "博主已下播，录制已停止")
                                except Exception:
                                    pass
                        if on_status_change:
                            try:
                                on_status_change(st)
                            except Exception:
                                pass
                    if st:
                        checked += 1
                if on_tick:
                    try:
                        on_tick(checked, total)
                    except Exception:
                        pass
                for _ in range(interval):
                    if self._stop_flag.is_set():
                        break
                    time.sleep(1)
            self._polling = False

        self._poll_thread = threading.Thread(target=loop, daemon=True)
        self._poll_thread.start()

    def stop_polling(self):
        self._stop_flag.set()
        self._polling = False

    @property
    def is_polling(self) -> bool:
        return self._polling

    # ==================================================================
    #  Recording (via ffmpeg)
    # ==================================================================

    # 启动健康检查最长等待秒数（确认 ffmpeg 真在写数据，而不是启动即失败）
    STARTUP_CHECK_WAIT = 6.0
    # TikTok 经代理拉海外 HLS，首字节明显慢于抖音本地链路；实测 6 秒会把
    # 正常起播误判成「流地址无效」。客户自建节点绕路更远，15 秒仍会误杀
    # （2026-11-15 客户反馈），所以放宽到 30 秒；同时 _verify_started 会在
    # 日志出现 403/404/超时这类明确错误时立刻失败，不必等满窗口。
    # 抖音仍用 6 秒，不受影响。
    TIKTOK_STARTUP_WAIT = 30.0
    # 小于该体积视为"没录到东西"：mp4 空壳只有 ftyp 头，约 48 字节
    MIN_VALID_BYTES = 20 * 1024
    # 写 q 之后等待 ffmpeg 优雅退出的上限秒数
    GRACE_TIMEOUT = 12

    def _ffmpeg_log_path(self, save_path: Path) -> Path:
        return save_path.with_name(save_path.name + ".log")

    @staticmethod
    def _tail_log(log_path: Path, limit: int = 1500) -> str:
        """读取 ffmpeg 日志尾部，把真实错误回报给用户（而不是干巴巴一句"失败"）。"""
        try:
            if not log_path.exists():
                return ""
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            return ""
        lines = text.splitlines()
        keep = [ln for ln in lines[-40:]
                if re.search(r"error|fail|invalid|denied|refused|timed? ?out|not found|unable|"
                             r"错误|失败|无法|超时", ln, re.I)]
        return "\n".join(keep[-8:]) or text[-400:]

    def _close_proc_streams(self, key: str, proc) -> None:
        fh = self._proc_logs.pop(key, None)
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass

    def start_recording(
        self,
        stream_url: str,
        save_name: str,
        duration: int = 0,   # seconds; 0 = until stopped
        sec_uid: str = "",
        startup_check: bool = True,
    ) -> tuple[bool, str]:
        """
        Start ffmpeg recording of a stream URL to mp4.

        ⚠️ 以下三条都是实测踩坑后的硬性约束，改动前务必先读懂：

        1. stderr 必须重定向到【文件】，绝不能用 PIPE。ffmpeg 会持续往 stderr
           打印进度与告警，管道缓冲（约 64KB）写满而我们又不读取时，ffmpeg
           会【永久阻塞】——实测输出文件停在 48 字节（只有 mp4 的 ftyp 头）
           再也不增长，界面却照样显示"录制中"，用户点停止后拿不到任何成片。
        2. stdin 必须是 PIPE。停止时写入 b"q" 让 ffmpeg 优雅退出，它才会把
           mp4 的 moov atom（索引）写进文件；用 terminate() 硬杀（Windows 上
           等价于 TerminateProcess）只会得到打不开的废文件。
        3. 录制期间写 *.mp4.part，停止并校验通过后才改名 *.mp4，
           保证目录里出现的 mp4 一定是完整可播放的。

        Args:
            stream_url:  m3u8 or flv pull URL.
            save_name:   base filename (without extension).
            duration:    max seconds to record (0 = until stop_recording).
            sec_uid:     if provided, used as key so auto-stop can find it.
            startup_check: 启动后是否做健康检查（会阻塞数秒，需在后台线程调用）。

        Returns (success, message_or_final_path).
        """
        if not self._ffmpeg:
            return False, "未找到 ffmpeg，请安装 ffmpeg 并加入 PATH，或在设置中指定路径。"

        # ★ TikTok 拉流比抖音多三样：-http_proxy、Referer、浏览器 UA
        #   （实测 11-14：直连 CDN 被重置，裸请求被拒）。手动粘贴 TikTok
        #   流地址录制时按 CDN 域名识别，不要求目标一定在监控表里。
        target = self._targets.get(sec_uid)
        is_tiktok = ((target is not None and target.platform == "tiktok")
                     or "tiktokcdn" in (stream_url or "")
                     or "tiktok.com" in (stream_url or ""))
        proxy = self._tiktok_egress() if is_tiktok else ""

        def _build(part_path: Path) -> list:
            cmd = [self._ffmpeg, "-y"]
            if is_tiktok:
                cmd += ["-loglevel", "warning"]
                cmd += tiktok_live.ffmpeg_input_args(proxy)
            cmd += [
                "-i", stream_url,
                "-c", "copy",       # no re-encode (fast)
                "-bsf:a", "aac_adtstoasc",
            ]
            if duration > 0:
                cmd += ["-t", str(duration)]
            # -f mp4 必须显式指定：输出文件是 .mp4.part，ffmpeg 认不出这个后缀，
            # 不指定就会报 "Unable to choose an output format" 直接退出。
            cmd += ["-f", "mp4", str(part_path)]
            return cmd

        return self._spawn(save_name, duration, sec_uid, startup_check, _build,
                           env_extra=tiktok_live.proxy_env(proxy) if is_tiktok else None,
                           startup_wait=(self.TIKTOK_STARTUP_WAIT
                                         if is_tiktok else None),
                           probe_url=stream_url if is_tiktok else "")

    # ------------------------------------------------------------------
    #  Screen region recording (屏幕区域录制)
    # ------------------------------------------------------------------
    def _probe_encoders(self) -> set:
        """一次性探测 ffmpeg 支持的视频编码器集合并缓存（避免每次录制都 fork）。"""
        if self._encoders is not None:
            return self._encoders
        enc: set = set()
        try:
            r = subprocess.run(
                [self._ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            for line in (r.stdout or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("V") and parts[1].islower():
                    enc.add(parts[1])
        except Exception:                       # noqa: BLE001
            enc = set()
        self._encoders = enc
        return enc

    def _pick_screen_encoder(self) -> tuple[str, list]:
        """为屏幕录制挑选编码器：优先 NVIDIA 硬编（RTX 3060 零 CPU 负担），
        没有则退回 libx264 软编。返回 (编码器名, 额外参数列表)。"""
        encs = self._probe_encoders()
        if "h264_nvenc" in encs:
            return "h264_nvenc", ["-preset", "p1", "-cq", "23", "-pix_fmt", "yuv420p"]
        return "libx264", ["-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]

    def _build_region_cmd(self, x: int, y: int, w: int, h: int,
                          part_path: Path) -> list:
        """构造 gdigrab 区域录屏命令（Windows GDI 抓屏 + 区域偏移/尺寸）。"""
        enc, opts = self._pick_screen_encoder()
        return [
            self._ffmpeg, "-y",
            "-f", "gdigrab", "-framerate", "30",
            "-offset_x", str(int(x)), "-offset_y", str(int(y)),
            "-video_size", f"{int(w)}x{int(h)}",
            "-i", "desktop",
            "-c:v", enc, *opts,
            "-f", "mp4", str(part_path),
        ]

    def start_region_recording(self, x: int, y: int, w: int, h: int,
                              save_name: str, duration: int = 0,
                              startup_check: bool = True) -> tuple[bool, str, str]:
        """屏幕区域录制：抓取屏幕上 (x, y, w, h) 矩形区域到 mp4。

        与直播拉流录制共用同一套 ffmpeg 生命周期（启动健康检查 / .part→.mp4 /
        写 q 优雅停止），因此具备同等的健壮性。返回的第三项是本次录制的 key，
        调用方用它调 stop_recording 停止。

        Args:
            x, y, w, h: 屏幕区域（设备像素，对应 gdigrab 的 offset / video_size）。
            save_name:  基础文件名（不含扩展名）。
            duration:   最长录制秒数（0 = 手动停止）。
            startup_check: 是否在后台线程做启动健康检查。

        Returns: (success, message, key)
        """
        if not self._ffmpeg:
            return False, "未找到 ffmpeg，请安装 ffmpeg 并加入 PATH，或在设置中指定路径。", ""
        if int(w) <= 0 or int(h) <= 0:
            return False, "录制区域的宽高必须大于 0", ""
        key = f"region_{int(time.time() * 1000)}"

        def _build(part_path: Path) -> list:
            return self._build_region_cmd(x, y, w, h, part_path)

        ok, _msg = self._spawn(save_name, duration, key, startup_check, _build)
        if ok:
            return True, "已开始录制", key
        return False, _msg, ""

    # ------------------------------------------------------------------
    #  Generic ffmpeg spawn（拉流录制与屏幕区域录制共用）
    # ------------------------------------------------------------------
    def _spawn(self, save_name: str, duration: int, sec_uid: str,
               startup_check: bool, build_cmd,
               env_extra: dict | None = None,
               startup_wait: float | None = None,
               probe_url: str = "") -> tuple[bool, str]:
        """启动一个 ffmpeg 录制任务并登记，复用统一的健壮逻辑。

        Args:
            save_name: 基础文件名（不含扩展名）。
            duration:  最长录制秒数（0 = 直到被停止）。
            sec_uid:   登记用的 key（主播录制传 sec_uid；区域录制传唯一 key）。
            startup_check: 是否做启动健康检查。
            build_cmd: 接收 part_path，返回完整 ffmpeg 命令列表的 callable。
            env_extra: 额外环境变量（TikTok 录制注入代理；None = 继承父进程）。
            startup_wait: 启动健康检查窗口；TikTok 经代理拉海外 HLS 比抖音慢，
                需要更久才写出第一个字节，None = 用类常量（抖音 6 秒）。

        Returns: (success, message)
        """
        if not self._ffmpeg:
            return False, "未找到 ffmpeg，请安装 ffmpeg 并加入 PATH，或在设置中指定路径。"

        safe_name = _safe_filename(save_name, 60)
        final_path = self.save_dir / f"{safe_name}.mp4"
        counter = 1
        while final_path.exists() or final_path.with_name(final_path.name + ".part").exists():
            final_path = self.save_dir / f"{safe_name}_{counter}.mp4"
            counter += 1
        # 录制中的临时文件；正式文件在停止并校验后才出现
        part_path = final_path.with_name(final_path.name + ".part")
        log_path = self._ffmpeg_log_path(final_path)

        cmd = build_cmd(part_path)

        log_fh = None
        try:
            log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
        except Exception:
            log_fh = None
        popen_env = None
        if env_extra:
            popen_env = os.environ.copy()
            popen_env.update(env_extra)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,                  # 必须：优雅停止靠它
                stdout=subprocess.DEVNULL,
                stderr=log_fh if log_fh else subprocess.DEVNULL,   # 绝不能是 PIPE
                env=popen_env,                          # None = 继承（抖音链路不变）
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except Exception as e:
            if log_fh:
                try:
                    log_fh.close()
                except Exception:
                    pass
            return False, f"启动 ffmpeg 失败: {e}"

        key = sec_uid if sec_uid else f"{safe_name}_{proc.pid}"
        self._proc_logs[key] = log_fh

        # ★ 从「ffmpeg 真正被拉起」的那一刻开始计时。
        # 不能等 start_recording 返回后再记 —— 健康检查会阻塞几秒，
        # 那段被录进去的时间就会从时长里凭空消失（旧实现的另一个小偏差）。
        started_at = time.time()
        with self._rec_lock:
            self._rec_start[key] = started_at
            self._rec_meta[key] = {"sec_uid": sec_uid, "name": safe_name,
                                   "part": str(part_path)}

        if startup_check:
            ok, reason = self._verify_started(
                proc, part_path, log_path,
                wait=startup_wait or self.STARTUP_CHECK_WAIT,
                tiktok=bool(startup_wait), probe_url=probe_url)
            if not ok:
                self._cleanup_failed(proc, key, part_path)
                return False, reason
            # 定时录制时长很短（短于健康检查窗口）时，ffmpeg 可能已经正常收工，
            # 直接在这里收尾改名，避免留下无人认领的 .part。
            if proc.poll() is not None:
                self._active_procs[key] = (key, proc, part_path)
                self.last_stopped = [
                    self._finish_one(key, note="录制时长已到，ffmpeg 正常结束")
                ]
                return True, str(final_path)

        self._active_procs[key] = (key, proc, part_path)
        return True, str(final_path)

    @staticmethod
    def _explain_ffmpeg_failure(detail: str, tiktok: bool = False) -> str:
        """把 ffmpeg 的报错翻成客户能照着做的一句话。"""
        d = detail or ""
        low = d.lower()
        if "403" in d or "401" in d or "forbidden" in low or "unauthorized" in low:
            return ("直播 CDN 拒绝了请求（403/401）：多半是当前代理出口被风控，"
                    "或流地址已失效。换个节点后点「立即检查一次」再录。")
        if "404" in d or "not found" in low:
            return ("流地址已失效（404）：主播可能刚下播或换了场次。"
                    "点「立即检查一次」刷新状态后再录。")
        if ("timed out" in low or "timeout" in low
                or "connection reset" in low or "broken pipe" in low):
            return ("连不上直播 CDN（超时 / 连接被重置）：当前出口到 TikTok 不通。"
                    "换个节点，或到「TikTok 代理」重新探测后保存。")
        if "refused" in low:
            return "代理端口拒绝连接：代理软件可能没开，或端口填错了。"
        if tiktok:
            return ("TikTok 拉流失败：常见原因是代理出口不稳、节点被风控，"
                    "或主播刚下播。换节点后点「立即检查一次」重试。")
        return ""

    # 日志里出现这些就说明已经确定失败，没必要把 30 秒窗口等满
    _FATAL_LOG_RE = re.compile(
        r"HTTP error (4\d\d|5\d\d)|Server returned (4\d\d|5\d\d)"
        r"|403 Forbidden|404 Not Found|Connection refused"
        r"|Connection timed out|Name or service not known"
        r"|Invalid data found|Immediate exit requested", re.I)

    def _verify_started(self, proc, part_path: Path, log_path: Path,
                        wait: float, tiktok: bool = False,
                        probe_url: str = "") -> tuple[bool, str]:
        """确认 ffmpeg 真的在录，而不是"启动即失败"或"卡住不写数据"。

        ★ 不傻等：日志里一旦出现 403/404/超时这类确定失败，立刻返回并给出
          可操作提示。客户自建节点慢的时候窗口要够长，但明确失败不该让客户
          盯着进度条等 30 秒。
        """
        deadline = time.time() + wait
        tick = 0
        diag_cache: list[str] = []

        def _diag() -> str:
            """失败时用同一出口探一次流地址，区分是节点问题还是参数问题。

            ★ 客户（自建节点）只说「15 秒没数据」，光看 ffmpeg 日志没法定性。
              这一步在客户机上实测，结论直接写进提示，客户照着改就行。
            """
            if not (tiktok and probe_url):
                return ""
            if not diag_cache:
                try:
                    diag_cache.append(tiktok_live.stream_diagnosis(
                        probe_url, self._tiktok_egress()))
                except Exception:                   # noqa: BLE001
                    diag_cache.append("")
            return diag_cache[0]

        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None:
                # ffmpeg 已经退出。若它已经写出数据，可能是"定时录制时长太短，
                # 在健康检查窗口内就正常录完了"（返回码 0）—— 那是成功不是失败。
                try:
                    if part_path.exists() and part_path.stat().st_size > 0:
                        return True, ""
                except OSError:
                    pass
                detail = self._tail_log(log_path)
                nice = self._explain_ffmpeg_failure(detail, tiktok)
                d = _diag()
                return False, (
                    f"ffmpeg 启动后立即退出，未能录制（返回码 {rc}）。"
                    + (f"\n{nice}" if nice else "")
                    + (f"\n{d}" if d else "")
                    + (f"\n\nffmpeg 报错：\n{detail}\n" if detail else "\n")
                    + ("" if (nice or d) else
                       "\n常见原因：流地址无效或已过期、网络不通、该直播间不允许网页录制。")
                )
            try:
                if part_path.exists() and part_path.stat().st_size > 0:
                    return True, ""
            except OSError:
                pass
            tick += 1
            if tick % 2 == 0:                       # 每 0.6 秒看一次日志
                early = self._tail_log(log_path, limit=800)
                if early and self._FATAL_LOG_RE.search(early):
                    try:
                        grew = (part_path.exists()
                                and part_path.stat().st_size > 0)
                    except OSError:
                        grew = False
                    if not grew:
                        nice = self._explain_ffmpeg_failure(early, tiktok)
                        try:
                            proc.terminate()
                        except Exception:           # noqa: BLE001
                            pass
                        d = _diag()
                        return False, (
                            (nice or "ffmpeg 拉流失败。")
                            + (f"\n{d}" if d else "")
                            + f"\n\nffmpeg 输出：\n{early}"
                        )
            time.sleep(0.3)
        detail = self._tail_log(log_path)
        nice = self._explain_ffmpeg_failure(detail, tiktok)
        d = _diag()
        return False, (
            f"ffmpeg 已启动，但 {wait:.0f} 秒内没有写出任何数据"
            + ("（TikTok 经代理拉流本来就慢，若频繁出现请换个节点）。"
               if tiktok else "。")
            + (f"\n{nice}" if nice else ("" if d else "流地址可能无效或网络不通。"))
            + (f"\n{d}" if d else "")
            + (f"\n\nffmpeg 输出：\n{detail}" if detail else "")
        )

    def _cleanup_failed(self, proc, key: str, part_path: Path) -> None:
        """启动失败或健康检查未通过时的收尾：停进程、关句柄、删空壳文件。"""
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._close_proc_streams(key, proc)
        self._active_procs.pop(key, None)
        with self._rec_lock:
            self._rec_start.pop(key, None)
            self._rec_meta.pop(key, None)
        # 健康检查没通过，但 LiveStatus 可能已被标记为「录制中」，一并纠正
        st = self._targets.get(key)
        if st is not None:
            st.is_recording = False
            st.record_start_time = 0.0
        try:
            if part_path.exists() and part_path.stat().st_size < self.MIN_VALID_BYTES:
                part_path.unlink()
        except OSError:
            pass

    def _verify_mp4(self, path: Path) -> tuple[bool, str]:
        """能用 ffprobe 就用它确认索引完整；没有 ffprobe 就退化为体积判定。"""
        probe = None
        try:
            cand = Path(self._ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if cand.is_file():
                probe = str(cand)
        except Exception:
            probe = None
        if not probe:
            return True, ""
        try:
            r = subprocess.run(
                [probe, "-v", "error", "-f", "mp4", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            dur = (r.stdout or "").strip()
            if r.returncode == 0 and dur and dur.upper() != "N/A":
                try:
                    if float(dur) > 0:
                        return True, ""
                except ValueError:
                    pass
            return False, "文件已生成但缺少索引（moov atom），可能无法正常播放"
        except Exception:
            # 检查本身出问题不应该否定录制结果
            return True, ""

    def _finish_one(self, key: str, note: str = "") -> dict:
        """
        结束一个录制：优雅退出 → 关闭句柄 → 校验文件 → .part 改名 .mp4。

        返回本次结束的明细 dict（同时写入 self.last_stopped）。
        """
        entry = self._active_procs.pop(key, None)
        if not entry:
            return {"key": key, "nickname": "", "path": "", "size": -1,
                    "ok": False, "note": "未找到该主播的录制进程", "exited_early": True}
        _, proc, part_path = entry
        final_path = part_path.with_suffix("")   # xxx.mp4.part -> xxx.mp4

        was_alive = proc.poll() is None
        if was_alive:
            # 1) 优雅停止：写 q，ffmpeg 才会写 moov、文件才打得开
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=self.GRACE_TIMEOUT)
            except Exception:
                # 2) 退化：CTRL_BREAK → 最后才硬杀
                try:
                    proc.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._close_proc_streams(key, proc)

        # 校验产物
        ok = False
        size = -1
        reason = ""
        if part_path.exists():
            size = part_path.stat().st_size
            if size >= self.MIN_VALID_BYTES:
                ok, reason = self._verify_mp4(part_path)
            else:
                reason = f"文件只有 {size} 字节，基本等于没录到内容"
        else:
            reason = "未生成录制文件"

        if ok:
            try:
                if final_path.exists():
                    final_path.unlink()
                part_path.replace(final_path)
                out_path = final_path
            except OSError as e:
                out_path = part_path
                ok = False
                reason = f"文件已录好但改名失败（{e}），仍留在：{part_path.name}"
        else:
            out_path = part_path
            if not reason:
                reason = note or "录制内容不完整"

        # ★ 先把登记表清干净，再按 meta 里记录的真实 sec_uid 复位 LiveStatus。
        # 旧实现只查 self._targets.get(key)：手动粘贴流地址录制时 key 不是
        # sec_uid，于是「录制中」这个状态永远没人复位，界面就一直挂着。
        with self._rec_lock:
            started_at = self._rec_start.pop(key, 0.0)
            meta = self._rec_meta.pop(key, {}) or {}
        target_uid = meta.get("sec_uid") or key
        st = self._targets.get(target_uid)
        nickname = st.nickname if st else ""
        if st:
            st.is_recording = False
            st.record_start_time = 0.0
        elapsed = int(time.time() - started_at) if started_at else 0
        if not was_alive and ok:
            reason = ""      # ffmpeg 自行正常结束（如设定时长到点），不算异常
        return {"key": key, "nickname": nickname, "path": str(out_path),
                "size": size, "ok": ok, "note": reason, "exited_early": not was_alive,
                "elapsed": elapsed}

    def stop_recording(self, sec_uid: str | None = None) -> int:
        """
        Stop one recording (by sec_uid) or all recordings.
        Returns number of recordings processed.

        ⚠️ 本方法会等待 ffmpeg 优雅退出（最长 GRACE_TIMEOUT 秒），
        因此【不要在 GUI 主线程直接调用】，否则界面会卡住。
        GUI 已改为后台线程调用；本次停止的明细见 self.last_stopped。
        """
        self.last_stopped = []
        keys = [sec_uid] if sec_uid else list(self._active_procs.keys())
        for k in keys:
            if k not in self._active_procs:
                continue
            self.last_stopped.append(self._finish_one(k))
        return len(self.last_stopped)

    def stop_all_recordings(self) -> int:
        return self.stop_recording(None)

    def reap_finished(self) -> list[dict]:
        """回收已自行退出的 ffmpeg（定时录制到点 / 直播中断 / ffmpeg 出错）。

        GUI 每秒调用一次，保证 .part 及时改名为 .mp4，
        也让"录制中"状态能及时纠正。
        """
        done: list[dict] = []
        for key in list(self._active_procs.keys()):
            entry = self._active_procs.get(key)
            if not entry or entry[1].poll() is None:
                continue
            done.append(self._finish_one(key, note="ffmpeg 已自行结束"))
        self.last_stopped = done
        return done

    def get_active_recordings(self) -> dict[str, tuple[str, subprocess.Popen, Path]]:
        return dict(self._active_procs)

    def is_recording(self) -> bool:
        return any(entry[1].poll() is None for entry in self._active_procs.values())

    # ------------------------------------------------------------------
    #  录制事实查询（UI 一律走这里，不要自己算时间）
    # ------------------------------------------------------------------

    def _resolve_key(self, key: str) -> str:
        """把 sec_uid 或内部 key 统一解析成登记表里的 key。

        手动粘贴流地址录制时，登记表里的 key 是「文件名_pid」，而 LiveStatus
        上挂着的是 sec_uid。查询接口允许传任意一种，免得调用方自己要记住。
        """
        if key in self._rec_start:
            return key
        with self._rec_lock:
            for k, meta in self._rec_meta.items():
                if (meta or {}).get("sec_uid") == key:
                    return k
        return key

    def _proc_alive(self, key: str) -> bool:
        entry = self._active_procs.get(self._resolve_key(key))
        if not entry:
            return False
        try:
            return entry[1].poll() is None
        except Exception:                      # noqa: BLE001
            return False

    def recording_started_at(self, key: str) -> float:
        """该录制任务的开始时间戳；0 表示当前没在录。"""
        if not self._proc_alive(key):
            return 0.0
        with self._rec_lock:
            return float(self._rec_start.get(self._resolve_key(key)) or 0.0)

    def recording_elapsed(self, key: str) -> int:
        """已录制秒数；不在录返回 0。"""
        t0 = self.recording_started_at(key)
        return int(time.time() - t0) if t0 > 0 else 0

    def live_recordings(self) -> list[dict]:
        """当前真正在录的任务清单（按开始时间排序），供界面直接渲染。

        每项：{key, sec_uid, name, path, started_at, elapsed}
        """
        out: list[dict] = []
        now = time.time()
        for key in list(self._active_procs.keys()):
            if not self._proc_alive(key):
                continue
            with self._rec_lock:
                t0 = float(self._rec_start.get(key) or 0.0)
                meta = dict(self._rec_meta.get(key) or {})
            out.append({
                "key": key,
                "sec_uid": meta.get("sec_uid") or "",
                "name": meta.get("name") or "",
                "path": meta.get("part") or "",
                "started_at": t0,
                "elapsed": int(now - t0) if t0 > 0 else 0,
            })
        out.sort(key=lambda d: d["started_at"])
        return out

    def reconcile_targets(self) -> int:
        """把「真正在录」这一事实回写进各目标的 LiveStatus，返回活跃录制数。

        ★ 这是修复「录制时长永远是 00:00」「录完了还显示录制中」的关键：
        以 _active_procs + _rec_start 作为唯一事实来源，而不是相信某个
        调用点有没有记得给 LiveStatus 赋值。UI 每次刷新前调一次即可。
        """
        live: dict[str, float] = {}
        for item in self.live_recordings():
            uid = item.get("sec_uid") or ""
            if uid:
                live[uid] = item["started_at"]
        for sec_uid, st in self._targets.items():
            if sec_uid in live:
                st.is_recording = True
                st.record_start_time = live[sec_uid]
            elif st.is_recording:
                # 登记表里已经没有它 → 录制确实结束，及时熄灯
                st.is_recording = False
                st.record_start_time = 0.0
        return len(live)

    # ==================================================================
    #  Stream URL helpers
    # ==================================================================

    def try_build_stream_url(self, room_id: str) -> str | None:
        """
        Attempt to construct a stream URL from a room_id.

        NOTE: Douyin's CDN URL format changes frequently and this may not
        work. This is a best-effort fallback; users should paste the
        actual stream URL when known.
        """
        if not room_id or room_id == "0":
            return None
        # Common fallback patterns — may not work for all rooms
        return f"https://pull-flv-f77.douyincdn.com/thirdparty/stream-{room_id}.flv"

    def close(self):
        self.stop_polling()
        self.stop_all_recordings()
        # 兜底：任何遗漏的进程也要优雅收尾，避免留下孤儿 ffmpeg 一直占着文件
        for key in list(self._active_procs.keys()):
            self._finish_one(key)
        if self._resolver:
            self._resolver.close()
