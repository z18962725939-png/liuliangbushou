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

# 第三方桩 / 旧数据没有 web_rid 字段时，仍可回落 room_id（不比旧版差）
dm._resolver.resolve_auto = lambda u: SimpleNamespace(room_id="1122334455",
                                                       anchor_name="")
ok5, _, uid5 = dm.add_target_by_link("https://v.douyin.com/NoWebRid/")
check("无 web_rid 字段时兼容回落 room_id", ok5 and uid5 == "web_rid:1122334455", uid5)

# 识别不出来的输入：给可行动原因，不抛异常
dm._resolver.resolve_auto = lambda u: SimpleNamespace(room_id="", anchor_name="")
ok4, why4, _ = dm.add_target_by_link("随便一句话")
check("无效输入有明确原因", not ok4 and "web_rid" in why4, why4)

print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  ✗", f)
    sys.exit(1)
print("ALL_LIVE_ADD_FAST_CHECKS_PASS")
