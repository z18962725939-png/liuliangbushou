# -*- coding: utf-8 -*-
"""回归：TikTok 直播监控正式接入主程序（2026-11-15）。

客户反馈「TikTok 直播监控以及录制不了」，根因是整条直播链路只做抖音，
TikTok 链接被原样丢给抖音 webcast 解析器。本门禁钉死接入后的硬判据：

  1. 纯解析：handle 归一化 / WAF 判定 / roomId 多路解析 / webcast 三态 /
     流地址只收 http(s) / ffmpeg 代理参数。
  2. 主程序路由：TikTok 链接不进抖音解析器、platform 落盘可恢复、
     check_once 与 resolve_stream_url 分派、状态未知保留上次、
     下播清空流地址、开录前必须重取（旧地址会 404）。
  3. 录制：TikTok 命令带 -http_proxy + Referer + UA、子进程注入代理 env；
     抖音命令一个字节都不变。
  4. 打包/界面/口径：spec 登记、代理可配置且立即生效、指南写明条件。
  5. 边界：适配层不调 JustOneAPI、不进付费闸门、不写死 7897 为唯一出口。

全程离线：不联网、不启浏览器、不写真配置。
用法：python tools/_validate_tiktok_live.py
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector import tiktok_live                      # noqa: E402
from justoneapi_collector.live_monitor import LiveMonitor        # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PASS, FAIL = [], []
TK_URL = ("https://www.tiktok.com/@dannkansai/live?_r=1&"
          "enter_from_merge=pc_share&sender_device=pc")
ROOM_ID = "7708051583588141845"

ORIG_LOAD = LiveMonitor._load_state
ORIG_SAVE = LiveMonitor._save_state
# 自检铁律：绝不写本机真实配置
LiveMonitor._save_state = lambda self: True
LiveMonitor._load_state = lambda self: 0


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {extra}" if not ok else ""))


def _live_payload(status=2, with_stream=True):
    su = ({"hls_pull_url": "https://pull-hls.tiktokcdn.com/a.m3u8?expire=1795881685&sign=x",
           "flv_pull_url": {"FULL_HD1": "https://pull-flv.tiktokcdn.com/b.flv"},
           "rtmp_pull_url": "rtmp://pull.tiktokcdn.com/live"}
          if with_stream else {})
    return {"status_code": 0, "data": {
        "status": status, "title": "absen malam", "user_count": 42,
        "owner": {"display_id": "dannkansai"}, "stream_url": su}}


def _sigi_html(room_id=ROOM_ID):
    sigi = {"LiveRoom": {"liveRoomUserInfo": {"user": {
        "uniqueId": "dannkansai", "nickname": "Vibe", "roomId": room_id}}}}
    return ("<html><head><title>Vibe 正在直播 - TikTok 直播</title></head>"
            '<script id="SIGI_STATE">' + json.dumps(sigi) + '</script>'
            + "<div>" + "x" * 9000 + "</div></html>")


# ---------------------------------------------------------------- 1 纯解析
def test_core():
    print("\n[1] 适配层纯解析")
    check("直播间链接归一化",
          tiktok_live.normalize_handle(TK_URL) == "dannkansai",
          tiktok_live.normalize_handle(TK_URL))
    check("@主播号归一化", tiktok_live.normalize_handle("@dannkansai") == "dannkansai")
    check("live_url 归一化（无跟踪参数）",
          tiktok_live.live_url("dannkansai") == "https://www.tiktok.com/@dannkansai/live")
    check("TikTok 链接识别", tiktok_live.is_tiktok_input(TK_URL))
    check("@ 开头识别", tiktok_live.is_tiktok_input("@dannkansai"))
    check("抖音链接不误判", not tiktok_live.is_tiktok_input("https://live.douyin.com/123"))
    check("★ 裸 handle 不误判（保护抖音 web_rid）",
          not tiktok_live.is_tiktok_input("ofdwex9"))
    check("WAF 挑战页判定", tiktok_live.is_waf_page("<html>Please wait...</html>"))
    check("正常大页不误判", not tiktok_live.is_waf_page(_sigi_html()))

    info = tiktok_live.extract_page_info(_sigi_html())
    check("★ roomId 主路径 = SIGI_STATE.LiveRoom.liveRoomUserInfo.user",
          info.room_id == ROOM_ID, info.room_id)
    check("解析来源可追溯", "SIGI_STATE" in info.source)
    check("顺带取到 uniqueId", info.unique_id == "dannkansai")

    uni = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {
        "roomId": "1234567890123", "uniqueId": "abc", "nickname": "A"}}}}}
    html2 = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">'
             + json.dumps(uni) + ";var tail=1;</script>")
    check("UNIVERSAL 备用路径", tiktok_live.extract_page_info(html2).room_id == "1234567890123")
    check("raw_decode 容忍尾部 JS", "UNIVERSAL" in tiktok_live.extract_page_info(html2).source)
    check("空页不抛异常", not tiktok_live.extract_page_info("").ok)

    snap = tiktok_live.parse_room(_live_payload())
    check("status=2 判在播", snap.is_live and not snap.is_offline)
    check("HLS 优先于 FLV", snap.stream_url.endswith("a.m3u8?expire=1795881685&sign=x"))
    check("只收 http(s)，排除 rtmp",
          bool(snap.stream_urls) and all(u.startswith("http") for u in snap.stream_urls))
    check("owner 解析", snap.owner == "dannkansai")
    check("expire 解析", snap.expires_at > 0)
    check("status=4 判下播", tiktok_live.parse_room(_live_payload(4, False)).is_offline)
    off_with_url = tiktok_live.parse_room(_live_payload(4, True))
    check("★ 下播即使接口还塞了地址也不返回流（避免拉到 404 死链）",
          off_with_url.is_offline and off_with_url.stream_url == ""
          and not off_with_url.stream_urls)
    bad = tiktok_live.parse_room({"status_code": 10011})
    check("10011 是失败不是下播", bool(bad.error) and not bad.is_offline)
    check("非 JSON 不抛", bool(tiktok_live.parse_room("not json").error))

    args = tiktok_live.ffmpeg_input_args("http://127.0.0.1:7897")
    check("ffmpeg 带 -http_proxy", "-http_proxy" in args)
    check("headers 含 Referer", any("Referer" in str(a) for a in args))
    check("headers 含浏览器 UA", any("User-Agent" in str(a) for a in args))
    env = tiktok_live.proxy_env("http://127.0.0.1:7897")
    check("子进程代理环境变量四件套",
          len(env) == 4 and all(v == "http://127.0.0.1:7897" for v in env.values()))
    check("无代理时 env 为空", tiktok_live.proxy_env("") == {})


# ---------------------------------------------------------------- 2 路由
def test_routing():
    print("\n[2] 主程序平台路由（打桩，不联网）")
    dm = LiveMonitor(object(), save_dir=Path(tempfile.mkdtemp(prefix="tk_gate_")),
                     tiktok_proxy="http://127.0.0.1:7897")

    def _no_net(*_a, **_k):
        raise AssertionError("添加阶段绝不该联网解析")

    dm._resolver.resolve_auto = _no_net

    ok, nick, uid = dm.add_target_by_link(TK_URL, note="海外-对标")
    check("TikTok 链接零网络添加成功", ok is True, str(nick))
    check("sec_uid = tiktok:<handle>", uid == "tiktok:dannkansai", uid)
    st = dm._targets.get(uid)
    check("platform = tiktok", st is not None and st.platform == "tiktok")
    check("live_url 归一化", st.live_url == "https://www.tiktok.com/@dannkansai/live")
    check("备注保留", st.note == "海外-对标")
    check("room_id 初始为空（由状态检查回填）", st.room_id == "")

    ok2, _nick2, uid2 = dm.add_target_by_link("https://live.douyin.com/123456789")
    check("★ 抖音链路不受影响", ok2 and uid2 == "web_rid:123456789", uid2)
    check("抖音 platform 默认 douyin", dm._targets[uid2].platform == "douyin")

    called = []
    dm._check_tiktok = lambda u, s: (called.append(u), s)[1]
    dm.check_once(uid)
    check("check_once 分派到 _check_tiktok", called == [uid])
    check("check_once 没碰抖音解析器", uid2 not in called)

    # -- 状态检查三态（room_id 已有 ⇒ 不会启浏览器）
    dm._check_tiktok = LiveMonitor._check_tiktok.__get__(dm)
    st.room_id = ROOM_ID
    original = tiktok_live.fetch_room
    try:
        tiktok_live.fetch_room = lambda *_a, **_k: tiktok_live.parse_room(_live_payload())
        r = dm._check_tiktok(uid, st)
        check("在播 → is_live / live_status", r.is_live and r.live_status == 1)
        check("在播 → 缓存流地址", "tiktokcdn" in (r.stream_url or ""))
        check("在播 → 无错误", r.stream_error == "")

        tiktok_live.fetch_room = lambda *_a, **_k: tiktok_live.parse_room(_live_payload(4, False))
        r = dm._check_tiktok(uid, st)
        check("下播 → 状态归零", not r.is_live and r.live_status == 0)
        check("★ 下播清空旧流地址（旧地址会 404）", r.stream_url == "")

        st.is_live = True
        tiktok_live.fetch_room = lambda *_a, **_k: tiktok_live.RoomSnapshot(error="网络抖动")
        r = dm._check_tiktok(uid, st)
        check("★ 异常保留上次状态，不冒充下播", r.is_live is True)
        check("状态检查失败前缀（界面显示状态未知）",
              r.stream_error.startswith("状态检查失败"))

        invalid = tiktok_live.RoomSnapshot(error="接口返回 status_code=10011",
                                           status_code=10011)
        tiktok_live.fetch_room = lambda *_a, **_k: invalid
        dm._check_tiktok(uid, st)
        check("10011 清空 room_id 以便下轮重取", st.room_id == "")

        st.room_id = ROOM_ID
        tiktok_live.fetch_room = lambda *_a, **_k: tiktok_live.parse_room(_live_payload())
        # -- 开录前必须重取，即使 st.stream_url 还留着旧值
        st.stream_url = "https://stale.tiktokcdn.com/404.m3u8"
        url = dm.resolve_stream_url(uid)
        check("★ resolve_stream_url 走 TikTok 重取", "tiktokcdn" in (url or ""))
        check("★ 重取覆盖旧流地址", url != "https://stale.tiktokcdn.com/404.m3u8")

        captured = {}
        dm.start_recording = lambda u, n, sec_uid=None: (captured.update(url=u), (True, "ok"))[1]
        dm._active_procs = {}
        dm.recording_started_at = lambda _u: 123.0
        dm.auto_record(uid)
        check("★ auto_record 对 TikTok 一律重取",
              captured.get("url") == url, str(captured.get("url")))

        # 下播瞬间：接口仍塞回带签名的死链，resolve 必须拒绝并给可行动文案
        tiktok_live.fetch_room = lambda *_a, **_k: tiktok_live.parse_room(
            _live_payload(4, True))
        st.is_live = False
        st.stream_url = ""
        r = dm.resolve_stream_url(uid)
        check("★ 下播时 resolve 返回 None（不拿 404 死链去开录）", r is None)
        check("下播文案明确「刚下播」", "刚下播" in st.stream_error,
              st.stream_error)
    finally:
        tiktok_live.fetch_room = original

    # -- 录制命令（打桩 _spawn，不起真 ffmpeg）
    # 上一段为了验证 auto_record 替换过实例方法，这里必须还原成真实实现
    dm.start_recording = LiveMonitor.start_recording.__get__(dm)
    dm._ffmpeg = "ffmpeg.exe"          # 假路径即可：_spawn 已打桩，不会真拉起进程
    captured = {}

    def fake_spawn(name, duration, sec_uid, startup_check, build_cmd,
                   env_extra=None, startup_wait=None):
        captured["cmd"] = build_cmd(Path("x.mp4.part"))
        captured["env"] = env_extra
        captured["wait"] = startup_wait
        return True, "ok"

    dm._spawn = fake_spawn
    dm.start_recording("https://pull-hls.tiktokcdn.com/a.m3u8", "tk",
                       sec_uid=uid, startup_check=False)
    cmd = captured["cmd"]
    check("TikTok 命令带 -http_proxy", "-http_proxy" in cmd)
    check("TikTok 命令带 -headers", "-headers" in cmd)
    check("TikTok 命令保留 -f mp4", "-f" in cmd and "mp4" in cmd)
    check("TikTok 命令保留 -c copy", "-c" in cmd and "copy" in cmd)
    check("TikTok 子进程注入代理 env",
          bool(captured["env"]) and len(captured["env"]) == 4)
    check("★ TikTok 启动检查窗口放宽到 15s", captured["wait"] == 15.0,
          str(captured["wait"]))

    dm.start_recording("https://pull-hls.tiktokcdn.com/a.m3u8", "manual",
                       sec_uid="", startup_check=False)
    check("★ 手动粘贴 TikTok 流地址也带代理", "-http_proxy" in captured["cmd"])

    dm.start_recording("https://pull.douyincdn.com/a.m3u8", "dy",
                       sec_uid=uid2, startup_check=False)
    check("★ 抖音命令不含 -http_proxy（链路不混）", "-http_proxy" not in captured["cmd"])
    check("★ 抖音命令不含 -headers", "-headers" not in captured["cmd"])
    check("★ 抖音子进程 env 为 None", captured["env"] is None)
    check("★ 抖音启动窗口保持默认 6s", captured["wait"] is None,
          str(captured["wait"]))

    # -- 代理可配置、立即生效
    check("set_tiktok_proxy 生效",
          dm.set_tiktok_proxy("http://127.0.0.1:9999") == "http://127.0.0.1:9999")
    check("重建浏览器会话（不会沿用旧出口）", dm._tiktok_browser is None)


# ---------------------------------------------------------------- 3 持久化
def test_persistence():
    print("\n[3] 平台字段持久化（重启不丢 / 兼容旧名单）")
    with tempfile.TemporaryDirectory(prefix="tk_state_") as d:
        p = Path(d) / "live_targets.json"
        p.write_text(json.dumps({"saved": 1, "targets": [
            {"sec_uid": "tiktok:dannkansai", "nickname": "@dannkansai",
             "input_type": "链接", "live_url": "https://www.tiktok.com/@dannkansai/live",
             "room_id": ROOM_ID, "note": "海外"},
            {"sec_uid": "web_rid:123456789", "nickname": "抖音号",
             "input_type": "链接", "live_url": "https://live.douyin.com/123456789"},
        ]}, ensure_ascii=False), encoding="utf-8")
        orig_path = LiveMonitor._state_path
        try:
            LiveMonitor._state_path = staticmethod(lambda: p)
            obj = object.__new__(LiveMonitor)
            obj._targets = {}
            n = ORIG_LOAD(obj)
            check("恢复 2 条目标", n == 2, str(n))
            check("TikTok 目标按伪 ID 反推 platform",
                  obj._targets["tiktok:dannkansai"].platform == "tiktok")
            check("旧版抖音名单默认 douyin",
                  obj._targets["web_rid:123456789"].platform == "douyin")
            check("room_id 一并恢复",
                  obj._targets["tiktok:dannkansai"].room_id == ROOM_ID)
            ORIG_SAVE(obj)
            written = json.loads(p.read_text(encoding="utf-8"))
            platforms = {t["sec_uid"]: t.get("platform") for t in written["targets"]}
            check("保存时写入 platform 字段",
                  platforms.get("tiktok:dannkansai") == "tiktok", str(platforms))
        finally:
            LiveMonitor._state_path = orig_path


# ---------------------------------------------------------------- 4 源码判据
def test_source():
    print("\n[4] 打包 / 界面 / 口径 / 边界")
    read = lambda rel: (ROOT / rel).read_text(encoding="utf-8", errors="ignore")  # noqa: E731
    lm = read("justoneapi_collector/live_monitor.py")
    tk = read("justoneapi_collector/tiktok_live.py")
    spec = read("justoneapi_collector.spec")
    base = read("gui_qt/pages/_base.py")
    page = read("gui_qt/pages/live_page.py")
    guide = read("gui_qt/widgets/guide.py")

    check('LiveStatus 有 platform 字段', 'platform: str = "douyin"' in lm)
    check("_load_state 兼容旧名单", '"tiktok" if sec_uid.startswith("tiktok:")' in lm)
    check("_save_state 写 platform", '"platform": t.platform' in lm)
    check("check_once 分派 TikTok", 'sec_uid.startswith("tiktok:")' in lm)
    check("resolve_stream_url 分派", 'if st.platform == "tiktok"' in lm)
    check("ffmpeg 注入代理参数", "ffmpeg_input_args(proxy)" in lm)
    check("_spawn 支持 env_extra", "env_extra: dict | None = None" in lm)
    check("_spawn 支持 startup_wait", "startup_wait: float | None = None" in lm)
    check("Popen 传 env", "env=popen_env" in lm)
    check("TikTok 单独启动窗口 15s", "TIKTOK_STARTUP_WAIT = 15.0" in lm)
    check("抖音默认窗口 6s 不变", "STARTUP_CHECK_WAIT = 6.0" in lm)
    check("开录前重取（不复用缓存）", "st.platform == \"tiktok\"" in lm)
    check("状态未知保留上次", "保留上次 is_live" in lm)

    check("spec 登记 tiktok_live", '"justoneapi_collector.tiktok_live"' in spec)
    check("ensure_monitor 传代理", "tiktok_proxy=tk_proxy" in base)
    check("直播页有代理输入框", "tiktok_proxy_edit" in page)
    check("代理按钮走 connect_guarded",
          "connect_guarded(self.detect_proxy_btn.clicked" in page
          and "connect_guarded(self.save_proxy_btn.clicked" in page)
    check("保存代理立即生效", "dm.set_tiktok_proxy(val)" in page)
    check("TikTok 输入走链接路径", "tiktok_live.is_tiktok_input(text)" in page)
    check("TikTok 首次检查有耗时提示", "首次需过浏览器校验" in page)
    check("指南写明 TikTok 代理", "TikTok 代理" in guide)
    check("指南写明不消耗采集额度", "不消耗采集额度" in guide)
    check("指南提示区域录制兜底", "区域录制" in guide)

    import ast                                                   # noqa: PLC0415
    consts = [n.value for n in ast.walk(ast.parse(tk))
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    check("★ 适配层不调 JustOneAPI", "client.request" not in tk and "JustOneAPICollector" not in tk)
    check("★ 不进付费闸门", "require_feature" not in tk)
    lm_consts = [n.value for n in ast.walk(ast.parse(lm))
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    check("★ 引擎层不写死 7897 为唯一出口",
          not any("127.0.0.1:7897" in c for c in lm_consts))
    detect_fn = tk.split("def detect_proxy(", 1)[-1].split("\ndef ", 1)[0]
    egress_fn = tk.split("def detect_egress(", 1)[-1].split("\ndef ", 1)[0]
    cand_fn = tk.split("def proxy_candidates()", 1)[-1].split("\ndef ", 1)[0]
    quick_fn = tk.split("def quick_proxy()", 1)[-1].split("\ndef ", 1)[0]
    fetch_fn = tk.split("def fetch_room(", 1)[-1].split("\ndef ", 1)[0]
    check("★ 端口探测优先于环境变量（客户机装的多是 Clash）",
          cand_fn.index("_COMMON_PROXY_PORTS") < cand_fn.index("env_proxy"))
    # ── 「状态未知」整片出现（客户 11-15 反馈）的防回归 ──
    #   真因：探测只验端口 listening，不验能否出海；且没读 Windows 系统代理，
    #   选中一条死代理后 webcast 请求 502，界面就整片「状态未知」。
    check("★ 读 Windows 系统代理（Clash 的「系统代理」写这里）",
          "Internet Settings" in tk and "ProxyEnable" in tk
          and "ProxyServer" in tk)
    check("★ 探测必须真连通验证，不能只看端口开着",
          "def proxy_works" in tk and "proxy_works(p" in egress_fn)
    check("★ proxy_works 不走环境变量（避免被 IDE 代理带偏）",
          "trust_env=False" in tk.split("def proxy_works(", 1)[-1]
          .split("\ndef ", 1)[0])
    check("★ 快取候选不读环境变量（IDE 注入的临时端口会误导客户）",
          "env_proxy(" not in quick_fn)
    check("★ 状态接口多出口回退：给定代理 → 系统代理 → 直连",
          "system_proxy()" in fetch_fn and 'tries.append("")' in fetch_fn)
    check("★ 记录实际走通的出口（ffmpeg 不读系统代理）",
          "out.via = p or" in tk and "def _tiktok_egress" in lm
          and "self._tiktok_egress() if is_tiktok" in lm)
    check("★ 失败文案给可操作指引（端口 7897 / 自动探测）",
          "127.0.0.1:7897" in tk and "自动探测" in tk
          and "def _outage_hint" in tk)
    check("★ 界面探测走验证版（三态：代理 / 直连 / 全不通）",
          "detect_egress()" in page and 'mode == "direct"' in page)
    _m_ports = re.search(r"_COMMON_PROXY_PORTS\s*=\s*\(([^)]*)\)", tk)
    _ports = ([int(x) for x in re.findall(r"\d+", _m_ports.group(1))]
              if _m_ports else [])
    check("★ 10808（Clash mixed 端口）在清单里且排在 10809 前",
          10808 in _ports and 10809 in _ports
          and _ports.index(10808) < _ports.index(10809))
    check("★ 直连也当候选验证（客户开 TUN / 全局时直连就通）",
          "def direct_works" in tk and 'return "", "direct"' in tk
          and "direct_works(" in egress_fn)
    check("★ detect_proxy 不再自己扫端口，统一走 detect_egress",
          "detect_egress()" in detect_fn)
    check("★ 界面区分「可直连（TUN）」与「全不通」",
          "TUN" in page and "代理留空即可" in page)
    check("★ 自动模式下空代理会重扫（客户先开软件后开代理）",
          "_tiktok_proxy_auto" in lm and "_tiktok_rescan_at" in lm)
    check("★ 状态未知带悬浮原因", 'tip=(getattr(s, "stream_error"' in page)
    check("★ 禁止虚拟时间参数（会跑废 WAF 挑战）",
          not any(c.startswith("--virtual-time-budget") for c in consts))
    check("webcast 路径带尾斜杠", tk.count('webcast/room/info/"') >= 1)
    check("CDP 真等真实时间", "time.sleep(self.wait)" in tk)
    check("CDP 会话可重建兜底", "self.close()" in tk)

    # ── 官网口径：TikTok 直播免费、不进付费闸门 ──
    #   客户端支持了但官网还写「只支持抖音」，客户照样不敢用；尤其旧版
    #   FAQ 把「查直播状态」笼统算成付费次数，会让人以为 TikTok 要扣额度。
    site = read("tools/build_site.py")
    sub = read("tools/build_subpages.py")
    seo = read("tools/site_seo.py")
    check("★ 官网首页明确写 TikTok 直播免费不消耗额度",
          "TikTok 直播能录吗" in site and "不消耗接口额度" in site
          and "永久免费、不限次数" in site)
    check("★ 官网不再把「查直播状态」笼统计入付费次数",
          "查直播状态各算一次" not in site)
    check("★ 首页免费版对照表含 TikTok 与代理说明",
          "抖音 / TikTok 直播录制" in site and "TikTok 需本机代理可用" in site)
    check("★ 直播栏目子页有 TikTok 专节（链接 / 代理 / 三态 / 重取）",
          "TikTok 直播间也能一起盯" in sub
          and all(k in sub for k in ("TikTok 代理", "状态未知", "重新取一次")))
    check("★ 免费版落地页写清 TikTok 直播不花钱",
          "抖音 / TikTok 直播间自动录制" in sub and "不消耗接口额度" in sub)
    check("★ SEO 描述同步 TikTok（标题保留已收录搜索词）", "TikTok 直播" in seo)


def test_egress_fallback():
    """运行时验证出口回退链 —— 源码字符串匹配抓不到「逻辑跑错」。

    场景（对应 2026-11-15 客户反馈「能取到直播间但状态未知」）：
      给定代理端口开着却连不出去 → 必须自动退到 Windows 系统代理。
    """
    from justoneapi_collector import tiktok_live as tkm       # noqa: PLC0415

    real_sys, real_client = tkm.system_proxy, tkm.httpx.Client
    calls: list[str] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"status_code": 0,
                    "data": {"status": 2, "title": "t", "stream_url": {}}}

    class _Client:
        def __init__(self, **kw):
            self.proxy = kw.get("proxy", "")
            calls.append(self.proxy)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):       # noqa: ARG002
            if self.proxy == "http://127.0.0.1:1":
                raise RuntimeError("502 Bad Gateway")
            return _Resp()

    tkm.system_proxy = lambda: "http://127.0.0.1:9"
    tkm.httpx.Client = _Client
    try:
        snap = tkm.fetch_room("7708051583588141845", "http://127.0.0.1:1")
        calls_a = list(calls)
        calls.clear()

        # 第二个场景：全都不通 → 错误必须是人话（含端口示例与「自动探测」）
        tkm.system_proxy = lambda: ""
        tkm.httpx.Client = type("C", (_Client,), {
            "get": lambda self, url, headers=None: (_ for _ in ()).throw(
                RuntimeError("ConnectTimeout"))})
        bad = tkm.fetch_room("7708051583588141845", "")
    finally:
        tkm.system_proxy, tkm.httpx.Client = real_sys, real_client

    check("★ 运行时：给定代理不通会自动退到系统代理",
          snap.via == "http://127.0.0.1:9")
    check("★ 先试给定代理，再试系统代理（顺序不能反）",
          calls_a[:2] == ["http://127.0.0.1:1", "http://127.0.0.1:9"])
    check("★ 运行时：全出口失败时给可操作文案，不是异常堆栈",
          bad.status == tkm.STATUS_UNKNOWN and "自动探测" in bad.error
          and "127.0.0.1:7897" in bad.error)

    # ── 场景三：TUN / 全局模式（客户 11-15 截图：Tun 绿色 + mixed:10808）──
    #   代理候选全不通，但进程直连能出网 ⇒ 必须判 direct，界面才好提示「留空」
    real_cands, real_pw, real_dw = (tkm.proxy_candidates, tkm.proxy_works,
                                    tkm.direct_works)
    tkm.proxy_candidates = lambda: ["http://127.0.0.1:1"]
    tkm.proxy_works = lambda p, timeout=4.0: False      # noqa: ARG005
    tkm.direct_works = lambda timeout=5.0: True         # noqa: ARG005
    try:
        p3, m3 = tkm.detect_egress()
        p4, m4 = tkm.detect_egress()                     # 幂等，别只对一次
    finally:
        (tkm.proxy_candidates, tkm.proxy_works,
         tkm.direct_works) = real_cands, real_pw, real_dw
    check("★ 运行时：代理全不通但直连通 → 判为 direct（TUN 可留空）",
          p3 == "" and m3 == "direct" and p4 == "" and m4 == "direct")


def main():
    print("=" * 62)
    print("TikTok 直播监控接入 · 离线回归门禁")
    print("=" * 62)
    test_core()
    test_routing()
    test_persistence()
    test_source()
    test_egress_fallback()
    total = len(PASS) + len(FAIL)
    print(f"\n结果：{len(PASS)}/{total} 通过")
    if FAIL:
        for name in FAIL:
            print("   失败：", name)
        return 1
    print("全部通过（未联网 / 未启浏览器 / 未写真实配置 / 未发版）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
