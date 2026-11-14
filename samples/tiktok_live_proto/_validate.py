"""离线自测：不联网、不启浏览器、不碰主程序。

覆盖：handle 归一化 / WAF 判定 / roomId 多路解析 / webcast 状态与流地址 /
ffmpeg 命令构造 / 录制进程生命周期 / 监控状态机（下播→开播→下播）/
不接发版链。运行：python samples/tiktok_live_proto/_validate.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # samples/

from tiktok_live_proto import core                                   # noqa: E402
from tiktok_live_proto.config import ProtoConfig, default_ffmpeg     # noqa: E402
from tiktok_live_proto.monitor import ProtoMonitor                   # noqa: E402
from tiktok_live_proto.recorder import Recorder                      # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PASSED = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED.append(f"{name} {detail}")
        print(f"  ✗ {name} {detail}")


# ---------------------------------------------------------------- 合成样本
def make_sigi_html(room_id: str, handle: str = "ofdwex9") -> str:
    sigi = {"LiveRoom": {"liveRoomUserInfo": {"user": {
        "uniqueId": handle, "nickname": "测试主播", "roomId": room_id}}},
        "CurrentRoom": {"roomId": ""}}
    body = json.dumps(sigi, ensure_ascii=False)
    return ("<html><head><title>测试主播 (@ofdwex9) 正在直播 - TikTok 直播</title>"
            f'</head><body>{"x" * 9000}'
            f'<script id="SIGI_STATE" type="application/json">{body}</script>'
            "</body></html>")


def make_universal_html(room_id: str) -> str:
    uni = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {
        "user": {"uniqueId": "someone", "nickname": "主页型", "roomId": room_id}}}}}
    body = json.dumps(uni, ensure_ascii=False)
    return ("<html><head><title>someone</title></head><body>" + "x" * 9000 +
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
            + body + "</script></body></html>")


def make_room_payload(status: int, hls: str = "", sdk_hls: str = "",
                      status_code: int = 0) -> dict:
    stream = {}
    if hls:
        stream["hls_pull_url"] = hls
    if sdk_hls:
        inner = json.dumps({"data": {"HD": {"main": {"hls": sdk_hls, "flv": ""}}}})
        stream["live_core_sdk_data"] = {"pull_data": {"stream_data": inner}}
    stream["flv_pull_url"] = {"SD1": "https://pull-flv.example.com/sd.flv"}
    stream["rtmp_pull_url"] = "rtmp://push.example.com/live"
    return {"status_code": status_code, "data": {
        "status": status, "title": "测试直播标题", "user_count": 123,
        "owner": {"display_id": "ofdwex9"}, "stream_url": stream}}


# ---------------------------------------------------------------- 各项检查
def test_handle() -> None:
    print("\n[1] handle 归一化")
    cases = {
        "https://www.tiktok.com/@ofdwex9/live?_r=1&enter_from_merge=pc_share": "ofdwex9",
        "https://www.tiktok.com/@ofdwex9/live": "ofdwex9",
        "https://www.tiktok.com/@ofdwex9": "ofdwex9",
        "@ofdwex9": "ofdwex9",
        "ofdwex9": "ofdwex9",
        "  @user.name_12  ": "user.name_12",
        "": "", "https://www.douyin.com/user/123": "",
        "https://www.tiktok.com/video/123456": "",
    }
    for raw, want in cases.items():
        check(f"{raw[:44] or '(空)'} → {want or '空'}",
              core.normalize_handle(raw) == want,
              f"实际={core.normalize_handle(raw)!r}")
    check("live_url 拼装", core.live_url("@ofdwex9") ==
          "https://www.tiktok.com/@ofdwex9/live")
    check("配置自动归一化完整链接",
          ProtoConfig(handle="https://www.tiktok.com/@ofdwex9/live?_r=1").handle
          == "ofdwex9")
    check("配置轮询间隔下限", ProtoConfig(poll_interval=1).poll_interval >= 5)


def test_page() -> None:
    print("\n[2] WAF 判定与 roomId 多路解析")
    check("706B 挑战页判为 WAF", core.is_waf_page("<html>Please wait…</html>"))
    check("SlardarWAF 标记判为 WAF", core.is_waf_page("SlardarWAF" + "x" * 2000))
    check("正常大页不判 WAF", not core.is_waf_page(make_sigi_html("7708019193639521076")))

    rid = "7708019193639521076"
    a = core.extract_page_info(make_sigi_html(rid))
    check("SIGI_STATE 主路径", a.room_id == rid, f"实际={a.room_id}")
    check("SIGI_STATE 附带主播信息", a.unique_id == "ofdwex9" and a.nickname == "测试主播")
    check("source 标注正确", "SIGI_STATE" in a.source, a.source)

    b = core.extract_page_info(make_universal_html(rid))
    check("UNIVERSAL 备用路径", b.room_id == rid and "UNIVERSAL" in b.source,
          f"实际={b.room_id}/{b.source}")

    c = core.extract_page_info("<html>" + "x" * 9000 +
                               f'<script>window.x={{"roomId":"{rid}"}}</script></html>')
    check("全页正则兜底", c.room_id == rid, f"实际={c.room_id}")
    d = core.extract_page_info("<html>" + "x" * 9000 + "</html>")
    check("没有 roomId 时 ok=False", not d.ok)

    # 真实缓存页（存在才验，不进版本库）
    for cand in (Path(tempfile.gettempdir()) / "tk_cdp2_61985_page.html",):
        if cand.is_file():
            real = core.extract_page_info(cand.read_text(encoding="utf-8", errors="ignore"))
            check("真实在播页面解析", real.room_id == rid, f"实际={real.room_id}")
            check("真实页面主播号", real.unique_id == "ofdwex9", real.unique_id)
            break


def test_room() -> None:
    print("\n[3] webcast 状态与流地址")
    hls = "https://pull-hls.example.com/live.m3u8?expire=1794841200&sign=abc"
    live = core.parse_room(make_room_payload(2, hls=hls))
    check("status=2 → 在播", live.is_live and live.status_text == "直播中")
    check("标题/主播/人数", live.title == "测试直播标题" and live.owner == "ofdwex9"
          and live.user_count == 123)
    check("HLS 优先", live.stream_url == hls, live.stream_url)
    check("expire 提取", live.expires_at == 1794841200.0, str(live.expires_at))
    # rtmp:// 不走 -http_proxy，原型刻意不收；只保留 http(s) 的 HLS/FLV
    check("多路径收集（只收 http(s)）", len(live.stream_urls) == 2
          and all(u.startswith("http") for u in live.stream_urls),
          str(live.stream_urls))

    off = core.parse_room(make_room_payload(4, hls=hls))
    check("status=4 → 已下播", off.is_offline and not off.is_live)
    unk = core.parse_room(make_room_payload(3, hls=hls))
    check("未知状态码 → 状态未知", unk.status == core.STATUS_UNKNOWN)
    bad = core.parse_room(make_room_payload(2, status_code=10011))
    check("10011 带出错误信息", "10011" in bad.error and bad.status_text == "状态未知")
    check("非 JSON 不抛异常", core.parse_room("not json").status == core.STATUS_UNKNOWN)
    sdk = core.parse_room(make_room_payload(2, sdk_hls="https://sdk.example.com/a.m3u8"))
    check("SDK 嵌套流地址回退", sdk.stream_url == "https://sdk.example.com/a.m3u8",
          sdk.stream_url)
    check("空 stream_url 不崩", not core.parse_room(
        {"status_code": 0, "data": {"status": 2}}).stream_url)


def test_cmd() -> None:
    print("\n[4] ffmpeg 命令与文件名")
    cmd = core.build_ffmpeg_cmd("ffmpeg.exe", "https://x.example.com/a.m3u8",
                                "D:/out/a.mp4", proxy="http://127.0.0.1:7897",
                                duration=10)
    check("带 -http_proxy", "-http_proxy" in cmd and "http://127.0.0.1:7897" in cmd)
    check("带 -headers", "-headers" in cmd and "Referer: https://www.tiktok.com/" in cmd[cmd.index("-headers") + 1])
    check("带浏览器 UA", "Chrome/131" in cmd[cmd.index("-headers") + 1])
    check("不加 -nostdin（要能发 q）", "-nostdin" not in cmd)
    check("选项都在 -i 之前", cmd.index("-http_proxy") < cmd.index("-i")
          and cmd.index("-headers") < cmd.index("-i"))
    check("-t 时长", "-t" in cmd and "10" in cmd)
    check("输出 mp4", "-f" in cmd and cmd[-1].endswith("a.mp4"))
    no_proxy = core.build_ffmpeg_cmd("ffmpeg.exe", "https://x/a.m3u8", "o.mp4")
    check("无代理时不写 -http_proxy", "-http_proxy" not in no_proxy)
    check("文件名清洗", core.safe_filename('a/b:c*d?"e') == "a_b_c_d__e",
          core.safe_filename('a/b:c*d?"e'))
    with tempfile.TemporaryDirectory() as d:
        p1 = core.new_record_path("@ofdwex9", d, 1)
        p2 = core.new_record_path("@ofdwex9", d, 2)
        check("分段文件名不重名", p1 != p2 and p1.suffix == ".mp4")


def test_recorder_lifecycle() -> None:
    print("\n[5] 录制进程生命周期（本地 lavfi 假流，不联网）")
    ffmpeg = default_ffmpeg()
    if not ffmpeg or not Path(ffmpeg).is_file():
        check("跳过：本机没找到 ffmpeg", True)
        return

    def fake_builder(ffmpeg, stream_url, out_path, proxy, user_agent, duration):
        return [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc=size=160x90:rate=10", "-t", "60",
                "-c:v", "mpeg4", "-q:v", "6", "-f", "mp4", str(out_path)]

    with tempfile.TemporaryDirectory() as d:
        cfg = ProtoConfig(handle="ofdwex9", save_dir=d, ffmpeg=ffmpeg, proxy="")
        rec = Recorder(cfg, command_builder=fake_builder)
        out = rec.start("https://fake/live.m3u8", "ofdwex9", 1)
        check("start 后进程在跑", rec.is_running)
        threading.Event().wait(1.2)
        rc = rec.stop(timeout=3)
        check("stop 后进程不在", not rec.is_running)
        check("优雅停止有退出码", rc is not None, str(rc))
        check("成片已落盘且非空", out.exists() and out.stat().st_size > 0,
              f"size={out.stat().st_size if out.exists() else 0}")
        check("stop 后 proc 被清空", rec.proc is None)
        info = rec.result()
        check("结果摘要带文件与大小", info["file"] == str(out) and info["size"] > 0)
        out2 = rec.start("https://fake/live2.m3u8", "ofdwex9", 2)
        check("停止后可再开一段", rec.is_running and out2 != out)
        rec.stop(timeout=3)


# ---------------------------------------------------------------- 状态机
class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def fetch(self, _room_id):
        self.calls += 1
        return self.script.pop(0) if self.script else core.parse_room(
            make_room_payload(4))


class FakeRecorder:
    def __init__(self):
        self.proc = None
        self.out_path = None
        self.starts = 0
        self.stops = 0
        self._running = False

    @property
    def is_running(self):
        return self._running

    def start(self, url, handle, seq=1):
        self.starts += 1
        self._running = True
        self.proc = object()
        self.out_path = Path(tempfile.gettempdir()) / f"fake_{seq}.mp4"
        return self.out_path

    def stop(self, timeout=8):
        self.stops += 1
        self._running = False
        self.proc = None
        return 0

    def poll(self):
        return None if self._running else 0

    def reset(self):
        self.proc = None
        self._running = False

    def result(self):
        return {"file": str(self.out_path or ""), "size": 2048,
                "started_at": "", "seconds": 10.0}


class ScriptedWait:
    """让主循环跑固定轮数后自动停，避免真的 sleep。"""

    def __init__(self, rounds: int):
        self.rounds = rounds
        self.n = 0

    def __call__(self, ev: threading.Event, _seconds: float) -> bool:
        self.n += 1
        if self.n >= self.rounds:
            ev.set()
        return ev.is_set()


def test_monitor() -> None:
    print("\n[6] 监控状态机（模拟 下播→开播→下播）")
    hls = "https://x.example.com/a.m3u8?expire=1794841200"
    snapshots = [core.parse_room(make_room_payload(4)),
                 core.parse_room(make_room_payload(2, hls=hls)),
                 core.parse_room(make_room_payload(2, hls=hls)),
                 core.parse_room(make_room_payload(4))]
    events: list[tuple[str, str]] = []
    cfg = ProtoConfig(handle="ofdwex9", proxy="http://127.0.0.1:7897",
                      save_dir=tempfile.gettempdir())
    client, recorder = FakeClient(snapshots), FakeRecorder()
    mon = ProtoMonitor(cfg, emit=lambda k, t, d: events.append((k, t)),
                       client=client, recorder=recorder,
                       page_provider=lambda _h: make_sigi_html("7708019193639521076"))
    mon._wait = ScriptedWait(4)
    mon.run_forever()
    texts = "\n".join(t for _k, t in events)
    check("拿到房间号", "7708019193639521076" in texts)
    check("开播自动开录", any(k == "record" for k, _t in events))
    check("只开录一次", recorder.starts == 1, f"starts={recorder.starts}")
    check("下播自动停录", any(k == "done" and "主播下播" in t for k, t in events))
    check("只停止一次", recorder.stops == 1, f"stops={recorder.stops}")
    check("最终状态已停止", mon.state.phase == "已停止", mon.state.phase)

    print("\n[7] 接口抖动时保留录制")
    err = core.RoomSnapshot()
    err.error = "模拟网络抖动"
    snapshots2 = [core.parse_room(make_room_payload(2, hls=hls)),
                  err,
                  core.parse_room(make_room_payload(4))]
    events2: list[tuple[str, str]] = []
    client2, recorder2 = FakeClient(snapshots2), FakeRecorder()
    mon2 = ProtoMonitor(cfg, emit=lambda k, t, d: events2.append((k, t)),
                        client=client2, recorder=recorder2,
                        page_provider=lambda _h: make_sigi_html("7708019193639521076"))
    mon2._wait = ScriptedWait(3)
    mon2.run_forever()
    check("抖动时没有停录", recorder2.stops == 1 and all(
        "网络抖动" not in t or "不停录" in t for _k, t in events2),
        f"stops={recorder2.stops}")
    check("恢复后正常收尾", any(k == "done" for k, _t in events2))

    print("\n[7b] 流地址连续失效时不会无脑重连")
    class FailingRecorder(FakeRecorder):
        def poll(self):
            return 0                         # 一启动就退，成片 0 字节

        def result(self):
            return {"file": str(Path(tempfile.gettempdir()) / "empty.mp4"),
                    "size": 0, "started_at": "", "seconds": 0.0}

    events3: list[tuple[str, str]] = []
    mon3 = ProtoMonitor(cfg, emit=lambda k, t, d: events3.append((k, t)),
                        client=FakeClient([]), recorder=FailingRecorder(),
                        page_provider=lambda _h: make_sigi_html("7708019193639521076"))
    for _ in range(3):
        mon3.recorder.proc = object()
        mon3._watch_recorder()
    check("连续三次异常后请求停止", mon3.stopped)
    check("给出明确原因", any("停止自动重连" in t for _k, t in events3))
    check("没有误报成片", not any(k == "done" for k, _t in events3))


def test_isolation() -> None:
    print("\n[8] 独立原型边界")
    release = ROOT / "tools" / "release_all.py"
    text = release.read_text(encoding="utf-8", errors="ignore") if release.is_file() else ""
    check("未挂进 release_all 发版链", "tiktok_live_proto" not in text)
    spec = ROOT / "justoneapi_collector.spec"
    spec_text = spec.read_text(encoding="utf-8", errors="ignore") if spec.is_file() else ""
    check("未写进打包 spec", "tiktok_live_proto" not in spec_text)
    check("原型目录在 samples/ 下", "samples" in str(Path(__file__).resolve()))


def main() -> int:
    print("TikTok 直播监控原型 · 离线自测")
    test_handle()
    test_page()
    test_room()
    test_cmd()
    test_recorder_lifecycle()
    test_monitor()
    test_isolation()
    total = PASSED + len(FAILED)
    print(f"\n结果：{PASSED}/{total} 通过")
    if FAILED:
        for item in FAILED:
            print("  失败：", item)
        return 1
    print("全部通过（全程未联网、未改主程序、未发版）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
