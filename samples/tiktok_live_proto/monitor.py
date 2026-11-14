"""轮询状态机：下播 → 开播自动录 → 下播自动停。

设计原则（沿用主工程直播监控的教训）：
  * 状态与流地址解耦：接口失败时**保留上一次状态**，不因一次网络抖动就停录。
  * 状态只有三态：在播(2) / 已下播(4) / 状态未知(0)。
  * 每段录制独立文件；流地址到期或 ffmpeg 提前退出时自动重取、重开一段。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .browser import BrowserError, TikTokBrowser
from .client import WebcastClient
from .config import ProtoConfig
from .core import PageInfo, RoomSnapshot, extract_page_info, normalize_handle
from .recorder import Recorder, RecorderError

Emit = Callable[[str, str, dict], None]


def _duration_label(info: dict) -> str:
    """给日志用的时长文案：媒体真实时长优先，墙钟则明确标注。"""
    sec = info.get("seconds", 0) or 0
    suffix = "" if info.get("duration_source") == "media" else "（墙钟）"
    return f"{sec:g} 秒{suffix}"


@dataclass
class MonitorState:
    """界面直接读的快照，避免在线程里直接碰控件。"""

    phase: str = "未开始"          # 未开始/取房间号/等待开播/录制中/状态未知/出错
    handle: str = ""
    room_id: str = ""
    nickname: str = ""
    title: str = ""
    last_status: str = "状态未知"
    last_check: str = ""
    recording_file: str = ""
    segments: int = 0
    errors: int = 0
    proxy: str = ""
    extra: dict = field(default_factory=dict)


class ProtoMonitor:
    """单主播监控器；一个实例只盯一个 @handle。"""

    def __init__(self, cfg: ProtoConfig, emit: Emit | None = None,
                 browser: TikTokBrowser | None = None,
                 client: WebcastClient | None = None,
                 recorder: Recorder | None = None,
                 page_provider: Callable[[str], str] | None = None,
                 room_id_override: str = "", html_file: str | Path = "",
                 max_segments: int = 0):
        self.cfg = cfg
        self._emit_fn = emit or (lambda *_args: None)
        self.browser = browser
        self.client = client or WebcastClient(cfg)
        self.recorder = recorder or Recorder(cfg)
        self.page_provider = page_provider
        self.room_id_override = str(room_id_override or "").strip()
        self.html_file = str(html_file or "")
        self.max_segments = max(0, int(max_segments))   # 0 = 不限段数

        self.state = MonitorState(handle=normalize_handle(cfg.handle), proxy=cfg.proxy)
        self.page_info: PageInfo | None = None
        self.snapshot: RoomSnapshot | None = None
        self.room_id = ""
        self.seq = 0
        self.consecutive_errors = 0
        self.start_failures = 0
        self.abnormal_exits = 0
        self._last_stream_url = ""
        self._record_started_ts = 0.0
        self._stop_event = threading.Event()

    # -- 事件 ---------------------------------------------------------
    def _emit(self, kind: str, text: str, data: dict | None = None) -> None:
        try:
            self._emit_fn(kind, text, data or {})
        except Exception:                               # noqa: BLE001
            pass                                        # 回调异常绝不影响状态机

    def request_stop(self) -> None:
        self._stop_event.set()

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    # -- 房间号 -------------------------------------------------------
    def _html_for(self, handle: str) -> str:
        if self.html_file:
            p = Path(self.html_file)
            if not p.is_file():
                raise BrowserError(f"指定的本地页面不存在：{p}")
            return p.read_text(encoding="utf-8", errors="ignore")
        if self.page_provider:
            return self.page_provider(handle)
        if self.browser is None:
            self.browser = TikTokBrowser(self.cfg)
        return self.browser.fetch_live_page(handle)

    def resolve_room(self) -> bool:
        """取 roomId。返回 False 时保留旧房间号，下轮重试。"""
        self.state.phase = "取房间号"
        if self.room_id_override:
            self.room_id = self.room_id_override
            self._emit("info", f"使用指定的 room_id：{self.room_id}", {})
            return True
        try:
            html = self._html_for(self.state.handle)
        except BrowserError as exc:
            self.state.phase = "出错"
            self.state.errors += 1
            self._emit("error", f"取直播间页失败：{exc}", {})
            return False
        info = extract_page_info(html)
        self.page_info = info
        if not info.ok:
            self.state.phase = "出错"
            self.state.errors += 1
            self._emit("error", "页面里没找到 roomId（可能未开播、需登录或页面结构改版）", {})
            return False
        self.room_id = info.room_id
        self.state.room_id = info.room_id
        self.state.nickname = info.nickname
        if info.unique_id:
            self.state.handle = info.unique_id
        self._emit("info",
                   f"拿到房间号 {info.room_id}（{info.source}，页面 {info.html_size} 字节）",
                   {})
        return True

    # -- 单次检查 -----------------------------------------------------
    def check_once(self) -> RoomSnapshot:
        snap = self.client.fetch(self.room_id)
        self.snapshot = snap
        self.state.last_status = snap.status_text if not snap.error else "状态未知"
        self.state.last_check = time.strftime("%H:%M:%S")
        if snap.error:
            self.consecutive_errors += 1
            self.state.errors = self.consecutive_errors
            self._emit("warn", f"状态检查失败：{snap.error}（保留上次状态，不停录）", {})
        else:
            self.consecutive_errors = 0
            if snap.title:
                self.state.title = snap.title
            self._emit("info",
                       f"状态：{snap.status_text}｜{snap.title or '无标题'}"
                       f"｜流地址 {len(snap.stream_urls)} 路", {})
            # 10011 = 房间无效，通常是换了一场直播，需要重新解析 roomId
            if snap.status_code == 10011:
                self._emit("warn", "房间已失效，下一轮重新解析房间号", {})
                self.room_id = ""
        return snap

    # -- 录制 ---------------------------------------------------------
    def _start_recording(self, snap: RoomSnapshot) -> None:
        if not snap.stream_url:
            self._emit("error", "接口没给流地址，无法开录（可能需要登录或该直播间受限）", {})
            return
        self.seq += 1
        try:
            out = self.recorder.start(snap.stream_url, self.state.handle, self.seq)
        except RecorderError as exc:
            self.start_failures += 1
            self._emit("error", f"开录失败（第 {self.start_failures} 次）：{exc}", {})
            return
        self.start_failures = 0
        self._last_stream_url = snap.stream_url
        self._record_started_ts = time.time()
        self.state.phase = "录制中"
        self.state.recording_file = str(out)
        self.state.segments = self.seq
        self._emit("record", f"开始录制：{out.name}", {"file": str(out)})

    def _stop_recording(self, reason: str) -> None:
        if not self.recorder.proc:
            return
        rc = self.recorder.stop()  # 内部会把 proc 置 None，避免重复报成片
        info = self.recorder.result()
        size_mb = round(info["size"] / 1024 / 1024, 2)
        self.state.phase = "等待开播"
        self.state.recording_file = ""
        self._emit("done",
                   f"已停止（{reason}）：{Path(info['file']).name} "
                   f"{size_mb} MB / {_duration_label(info)} / ffmpeg 退出码 {rc}",
                   {"file": info["file"], "size": info["size"]})

    def _handle_live(self, snap: RoomSnapshot) -> None:
        if not self.recorder.is_running:
            if self.recorder.poll() is not None and self.recorder.out_path:
                # 上一段自己结束了（时长到 / 流断），日志里说清楚再开新段
                self._emit("warn", "上一段录制已结束，准备重取流地址开新段", {})
                self.recorder.reset()
            self._start_recording(snap)
            return
        # 录制中：流地址定时续期。变了就收尾当前文件、用新地址开一段
        elapsed_min = (time.time() - self._record_started_ts) / 60.0
        if elapsed_min >= max(1.0, self.cfg.refresh_minutes):
            self._record_started_ts = time.time()
            if snap.stream_url and snap.stream_url != self._last_stream_url:
                self._emit("info", "流地址已更新，分段续录", {})
                self._stop_recording("流地址更新")
                self._start_recording(snap)
            else:
                self._emit("info", "流地址复查：未变化，继续录制", {})

    def _watch_recorder(self) -> None:
        """录制进程提前退出时清句柄。

        实测教训：主播下播后旧流地址 404（sign/expire 还没到点也没用），
        ffmpeg 会立刻退出、成片 0 字节。若不加判断就无脑重连，会每轮
        刷一次失败。这里连续 3 次异常就停止自动重连并说清原因。
        """
        if not (self.recorder.proc and self.recorder.poll() is not None):
            return
        info = self.recorder.result()
        size = info["size"]
        if size < 10 * 1024:
            self.abnormal_exits += 1
            self._emit("error",
                       f"录制异常结束（成片 {size} 字节，多为流地址失效 / 代理不通）"
                       f"｜第 {self.abnormal_exits} 次", {})
            if self.abnormal_exits >= 3:
                self._emit("error", "连续三次录制失败，停止自动重连；"
                           "请确认主播确在直播、代理可用，或稍后重新开始", {})
                self.request_stop()
        else:
            self.abnormal_exits = 0
            self._emit("done",
                       f"一段录制完成：{Path(info['file']).name} "
                       f"{round(size / 1024 / 1024, 2)} MB / {_duration_label(info)}",
                       {"file": info["file"], "size": size})
        reached_limit = bool(self.max_segments and self.seq >= self.max_segments)
        if not self.stopped and not reached_limit:
            self._emit("warn", "若仍在播将自动重取流地址续录", {})
        self.recorder.reset()
        self.state.recording_file = ""

    # -- 主循环 -------------------------------------------------------
    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        ev = stop_event or self._stop_event
        self._emit("info",
                   f"开始监控 @{self.state.handle or '未填'}｜代理："
                   f"{self.cfg.proxy or '未配置（国内直连大概率不通）'}"
                   f"｜轮询 {self.cfg.poll_interval:g}s｜保存目录 {self.cfg.save_dir}", {})
        try:
            while not ev.is_set():
                self._watch_recorder()
                if self.max_segments and self.seq >= self.max_segments:
                    self._emit("info", f"已完成 {self.seq} 段录制，达到设定的段数上限，停止监控", {})
                    break
                if not self.room_id and not self.resolve_room():
                    if self._wait(ev, self.cfg.poll_interval):
                        break
                    continue

                snap = self.check_once()
                if snap.error:
                    if self.consecutive_errors == 3 and not self.cfg.proxy:
                        self._emit("error", "连续三次请求失败：请确认代理可用（设置里可改）", {})
                    # 保留上次状态，录制中绝不因抖动停录
                elif snap.is_live:
                    self._handle_live(snap)
                elif snap.is_offline:
                    if self.recorder.is_running:
                        self._stop_recording("主播下播")
                    elif self.state.phase != "等待开播":
                        self.state.phase = "等待开播"
                        self._emit("info", "当前未开播，继续轮询等待开播", {})
                else:
                    if self.recorder.is_running:
                        self._emit("warn", "状态未知，保留当前录制", {})
                    else:
                        self.state.phase = "状态未知"

                interval = (self.cfg.live_poll_interval if self.recorder.is_running
                            else self.cfg.poll_interval)
                if self._wait(ev, interval):
                    break
        finally:
            if self.recorder.is_running or self.recorder.proc:
                self._stop_recording("监控停止")
            if self.browser:
                self.browser.close()
            self.state.phase = "已停止"
            self._emit("info", "监控已停止", {})

    def _wait(self, ev: threading.Event, seconds: float) -> bool:
        """可被停止事件打断的等待；返回 True 表示应当退出循环。"""
        return ev.wait(max(1.0, seconds))

    # -- 一次性诊断（CLI --once 用） ----------------------------------
    def diagnose(self) -> dict:
        """只做一次：取页面 → 查状态，不录制、不启轮询。"""
        if not self.room_id:
            if not self.resolve_room():
                return {"ok": False, "reason": "取房间号失败"}
        snap = self.check_once()
        return {
            "ok": not bool(snap.error),
            "room_id": self.room_id,
            "page": self.page_info.__dict__ if self.page_info else {},
            "status": snap.status_text,
            "raw_status": snap.raw_status,
            "status_code": snap.status_code,
            "title": snap.title,
            "owner": snap.owner,
            "streams": len(snap.stream_urls),
            "expires_at": snap.expires_at,
            "error": snap.error,
        }
