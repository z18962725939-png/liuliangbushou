# -*- coding: utf-8 -*-
"""回归：直播监控「添加监控」必须是零网络快路径（2026-11-05 客户现场）。

客户报「点添加监控没反应」。一半根因是 add_target_by_link 为了拿昵称先联网
解析（web enter + HTML 兜底，最长 30 秒），期间界面没有任何反馈。
本脚本把 resolve_auto 换成「一调用就炸」的桩：
  · 直播间 URL / 纯 web_rid 必须秒回、绝不联网；
  · 短链 / 主页 URL 才允许联网解析；
  · 识别不出来的输入给可行动的原因。
用法：PYTHONPATH=. python tools/_validate_live_add_fast.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json  # noqa: E402

from justoneapi_collector.douyin_live_status_cdp import (  # noqa: E402
    DouyinHomeLiveCDP, HomeLiveSnapshot,
)
from justoneapi_collector.live_monitor import LiveMonitor  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {extra}" if not ok else ""))


# 绝不写本机真实配置（自检铁律）
LiveMonitor._save_state = lambda self: True
LiveMonitor._load_state = lambda self: 0

dm = LiveMonitor(object(), save_dir=Path(tempfile.mkdtemp(prefix="lm_fast_")))
net_calls = []


def _no_net(url):
    net_calls.append(url)
    raise AssertionError("标准直播间链接不该走联网解析：" + url)


dm._resolver.resolve_auto = _no_net

print("=" * 60)
print("直播监控添加：零网络快路径回归")
print("=" * 60)

t0 = time.time()
ok, nick, uid = dm.add_target_by_link("https://live.douyin.com/123456789?from=share",
                                      note="美妆-护肤")
elapsed = time.time() - t0
check("直播间 URL 添加成功", ok is True, str(nick))
check("sec_uid 形如 web_rid:<id>", uid == "web_rid:123456789", uid)
check("昵称先用占位（后续异步回填真实昵称）", "123456789" in nick, nick)
check("标准链接零网络调用", not net_calls, str(net_calls))
check("添加耗时 < 0.3 秒", elapsed < 0.3, f"{elapsed:.3f}s")
check("备注已落到目标", dm.get_targets()[0].note == "美妆-护肤")

t1 = time.time()
ok2, nick2, uid2 = dm.add_target_by_link("745964462470")
check("纯 web_rid 添加成功", ok2 and uid2 == "web_rid:745964462470", uid2)
check("纯 web_rid 同样零网络", not net_calls, str(net_calls))
check("纯 web_rid 耗时 < 0.3 秒", time.time() - t1 < 0.3)

# 短链才允许联网：这里换成返回假数据的桩
calls2 = []


def _fake_resolve(url):
    calls2.append(url)
    # web_rid = 重定向后 URL 里的真 ID；room_id = webcast 的真实房间号，
    # 两者不是一回事，拿 room_id 拼直播间链接必然打不开。
    return SimpleNamespace(room_id="9988776655", anchor_name="短链主播",
                           web_rid="5566778899")


dm._resolver.resolve_auto = _fake_resolve
ok3, nick3, uid3 = dm.add_target_by_link("https://v.douyin.com/AbCdEf/")
check("短链走联网解析", ok3 and calls2 and uid3 == "web_rid:5566778899", uid3)
check("短链采用真 web_rid，不拿 room_id 冒充",
      uid3 != "web_rid:9988776655", uid3)
check("短链解析出的真实昵称被采用", nick3 == "短链主播", nick3)

# ★ 2026-11-23：客户把抖音分享文案整段粘进来（带中文前缀），
# 必须先抠出链接再解析，不能把整段文字当 URL 发请求。
calls2.clear()
ok6, _, uid6 = dm.add_target_by_link(
    "搜索，查看TA的更多作品。 https://v.douyin.com/ShareText/ ，复制此链接打开抖音")
check("分享文案整段能提取链接", ok6 and calls2 == ["https://v.douyin.com/ShareText/"],
      str(calls2))
check("分享文案提取后目标正确", uid6 == "web_rid:5566778899", uid6)

# 第三方桩 / 旧数据没有 web_rid 字段时，仍可回落 room_id（不比旧版差）
dm._resolver.resolve_auto = lambda u: SimpleNamespace(room_id="1122334455",
                                                       anchor_name="")
ok5, _, uid5 = dm.add_target_by_link("https://v.douyin.com/NoWebRid/")
check("无 web_rid 字段时兼容回落 room_id", ok5 and uid5 == "web_rid:1122334455", uid5)

# 识别不出来的输入：给可行动原因，不抛异常
dm._resolver.resolve_auto = lambda u: SimpleNamespace(room_id="", anchor_name="")
ok4, why4, _ = dm.add_target_by_link("随便一句话")
check("无效输入有明确原因", not ok4 and "web_rid" in why4, why4)

# ★ 2026-11-23 回归：抖音账号主页监控
# 过去主页链接会被拒、纯 sec_uid 会被正则吞成 web_rid:xxx，
# 加上后 check_once 又按直播间查，状态永远是「未开播」。
SECU = ("MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp")
net_before = len(net_calls)
okh, nickh, uidh = dm.add_target_by_link(
    "https://www.douyin.com/user/" + SECU, note="主页-测试")
check("账号主页链接添加成功", okh and uidh == SECU, uidh)
check("主页目标不是 web_rid 伪 ID", not uidh.startswith("web_rid:"), uidh)
st_h = dm._targets.get(uidh)
check("主页目标不写 live_url（否则状态检查走错分支）",
      st_h is not None and not getattr(st_h, "live_url", ""),
      getattr(st_h, "live_url", ""))
check("主页链接零网络", len(net_calls) == net_before, str(net_calls))
check("主页目标备注已落盘", st_h is not None and st_h.note == "主页-测试",
      getattr(st_h, "note", ""))
okp, _, uidp = dm.add_target_by_link(SECU)
check("纯 sec_uid 不再被误吞成 web_rid", okp and uidp == SECU, uidp)
dm._resolver.resolve_auto = lambda u: SimpleNamespace(
    sec_uid=SECU, room_id="", anchor_name="", web_rid="")
okq, _, uidq = dm.add_target_by_link("https://v.douyin.com/HomeShort/")
check("短链重定向到主页时按账号登记", okq and uidq == SECU, uidq)

# 付费接口失败必须说人话：实测余额不足曾只显示「接口未返回状态」
class _BadResp:
    success = False
    code = 601
    message = "INSUFFICIENT BALANCE"
    data = None


class _BadClient:
    def request(self, *a, **k):
        return _BadResp()


dm2 = LiveMonitor(_BadClient(), save_dir=Path(tempfile.mkdtemp(prefix="lm_bal_")),
                  cdp_fallback=False)   # 这段只验余额文案，别真起浏览器
dm2._save_state = lambda: True
_, _, uid_bad = dm2.add_target_by_link(SECU)
st_bad = dm2.check_once(uid_bad)
check("余额不足原因上屏", st_bad is not None and "余额不足" in st_bad.stream_error,
      getattr(st_bad, "stream_error", ""))
check("检查失败不冒充未开播",
      st_bad is not None and st_bad.stream_error.startswith("状态检查失败"),
      getattr(st_bad, "stream_error", ""))

# ★ 2026-11-23 回归：付费查不到状态时走 CDP 免费兜底（不冒充未开播）
class _FakeHomeCDP:
    def __init__(self, snap=None):
        self.snap = snap
        self.calls: list[str] = []
        self.cookies: list[str] = []

    def fetch(self, uid):
        self.calls.append(uid)
        if isinstance(self.snap, Exception):
            raise self.snap
        return self.snap

    def set_cookie(self, cookie):
        self.cookies.append(cookie)


dm3 = LiveMonitor(_BadClient(), save_dir=Path(tempfile.mkdtemp(prefix="lm_cdp_")))
_, _, uid_cdp = dm3.add_target_by_link("https://www.douyin.com/user/" + SECU)
st_cdp = dm3._targets[uid_cdp]
st_cdp.stream_url = "http://old-stream"
st_cdp.hls_urls = {"hls_0": "http://old"}
fake = _FakeHomeCDP(HomeLiveSnapshot(
    True, live_status=1, room_id="1234567890123456789",
    nickname="免费通道主播", sec_uid=SECU, web_rid="870887192950"))
dm3._home_cdp = fake
out = dm3.check_once(uid_cdp)
check("付费失败时确实调用了免费兜底", fake.calls == [SECU], str(fake.calls))
check("免费兜底回填开播状态", bool(out and out.is_live and out.live_status == 1))
check("免费兜底回填房间号/昵称",
      bool(out and out.room_id == "1234567890123456789"
           and out.nickname == "免费通道主播"),
      f"{getattr(out, 'room_id', '')}/{getattr(out, 'nickname', '')}")
check("免费兜底成功后清空「余额不足」提示",
      bool(out and not out.stream_error), getattr(out, "stream_error", ""))
check("在播主页的 web_rid 被回填（供自动录制取流）",
      getattr(out, "web_rid", "") == "870887192950",
      getattr(out, "web_rid", ""))

# 19 位 room_id_str 解析不出流，必须优先用 web_rid
seen_web: list[str] = []
seen_app: list[tuple] = []


def _fake_web_rid(rid):
    seen_web.append(rid)
    return SimpleNamespace(is_live=True, error="", room_id="1",
                           hls_pull_urls={},
                           flv_pull_urls={"ORIGIN": "http://flv/test"})


def _fake_app(*a):
    seen_app.append(a)
    return SimpleNamespace(is_live=False, error="no room data",
                           room_id="1", hls_pull_urls={}, flv_pull_urls={})


dm3._resolver.resolve_by_web_rid = _fake_web_rid
dm3._resolver.resolve_by_app = _fake_app
st_web = dm3._targets[uid_cdp]
st_web.room_id = "1234567890123456789"
stream = dm3.resolve_stream_url(uid_cdp)
check("自动录制优先用 web_rid 拿流地址",
      stream == "http://flv/test" and seen_web == ["870887192950"]
      and not seen_app, f"{stream} / {seen_web} / {seen_app}")

fake.snap = HomeLiveSnapshot(True, live_status=0, room_id="0", sec_uid=SECU)
out2 = dm3.check_once(uid_cdp)
check("免费兜底判未开播也清空旧流地址",
      bool(out2 and not out2.is_live and not out2.stream_url and not out2.hls_urls))
check("免费兜底判未开播不算状态未知",
      bool(out2 and not out2.stream_error.startswith("状态检查失败")),
      getattr(out2, "stream_error", ""))

fake.snap = HomeLiveSnapshot(False, error="主页未发出资料请求")
out3 = dm3.check_once(uid_cdp)
check("免费兜底失败仍报状态未知",
      bool(out3 and out3.stream_error.startswith("状态检查失败")),
      getattr(out3, "stream_error", ""))
check("免费兜底失败原因一并上屏",
      bool(out3 and "余额不足" in out3.stream_error
           and "免费通道" in out3.stream_error),
      getattr(out3, "stream_error", ""))

dm3.set_douyin_cookie("sessionid=test")
check("重新扫码后 Cookie 同步给免费通道", fake.cookies == ["sessionid=test"],
      str(fake.cookies))
check("免费通道查询器复用单例", dm3.home_cdp() is fake)

fake2 = _FakeHomeCDP(HomeLiveSnapshot(True, live_status=1, sec_uid=SECU))
dm2._home_cdp = fake2
dm2.check_once(uid_bad)
check("关闭兜底后绝不启动浏览器", not fake2.calls, str(fake2.calls))

cdp = DouyinHomeLiveCDP(cookie="")
bad = cdp.fetch("随便一句话")
check("非法 sec_uid 直接拒绝且不启动浏览器",
      (not bad.ok and "sec_uid" in bad.error and cdp._crawler is None),
      bad.error)

# 签名模板只换 sec_user_id，其余签名参数原样保留
cdp2 = DouyinHomeLiveCDP(cookie="")
cdp2._api_url = ("https://www.douyin.com/aweme/v1/web/user/profile/other/"
                 "?a=1&sec_user_id=OLD&b=2")
cdp2._api_at = time.time()
seen: dict[str, str] = {}


class _FakePage:
    def call(self, method, params=None, timeout=None):
        seen["method"] = method
        seen["expr"] = (params or {}).get("expression", "")
        return {"result": {"value": json.dumps({
            "status": "ok",
            "user": {"live_status": 1, "room_id": "0",
                     "room_id_str": "777", "nickname": "N"},
        })}}


class _FakeCrawler:
    _page = _FakePage()


cdp2._crawler = _FakeCrawler()
got = cdp2.fetch(SECU)
expr = seen.get("expr", "")
check("签名模板只替换 sec_user_id",
      ("sec_user_id=" + SECU) in expr and "a=1&" in expr and "&b=2" in expr,
      expr[:220])
check("页面内 fetch 解析出开播字段",
      got.ok and got.live_status == 1 and got.room_id == "777"
      and got.nickname == "N", str(got))
check("连续失败会进入冷却", (lambda c: (
    [c._note_fail("x") for _ in range(3)], c._disabled_until > time.time())[1])(
    DouyinHomeLiveCDP(cookie="")))

print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  ✗", f)
    sys.exit(1)
print("ALL_LIVE_ADD_FAST_CHECKS_PASS")
