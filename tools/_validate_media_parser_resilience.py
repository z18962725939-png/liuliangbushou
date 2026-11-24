# -*- coding: utf-8 -*-
"""media_parser 韧性门禁：失败说人话、单点故障不拖垮批量。

全部离线执行：mock httpx.Client，不发一个真实请求、不花一分钱、不碰 Key。
覆盖 204 / 超时 / 429 耗尽 / 非 JSON / Key 缺失 / 成功 六种响应，
另加超时耗尽、health 探活、批量入口短路的断言。

跑法：C:/Python310/python.exe tools/_validate_media_parser_resilience.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# patch.object(mp, "DEFAULT_API_KEY", "") 只在进程内临时顶掉默认值，
# 源码字面量一个字都不改（硬编码 mp_ Key 是红线）。
from justoneapi_collector import media_parser as mp  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if not ok else ""))


# ------------------------------------------------------------------ mock 基建
class Response:
    def __init__(self, status_code, body=None, raise_json=False):
        self.status_code = status_code
        self._body = body
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._body


class FakeClient:
    """按脚本序列返回响应 / 抛异常，并统计真正的请求次数。"""
    script = []
    calls = 0

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, _url, json=None):
        i = FakeClient.calls
        FakeClient.calls += 1
        item = FakeClient.script[min(i, len(FakeClient.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, _url, headers=None):
        return self.post(_url)


def run(script):
    """用给定响应序列跑一次 parse()，返回 (data, reason, calls)。"""
    FakeClient.script = script
    FakeClient.calls = 0
    # 和 _validate_tiktok_extract.py 同一套 patch 风格：sleep 直接吞掉，
    # 门禁不真的等 1/2.5 秒。
    with patch("httpx.Client", FakeClient), \
            patch.object(mp.time, "sleep", lambda *_: None):
        data, reason = mp.parse("https://v.douyin.com/Fake/")
    return data, reason, FakeClient.calls


OK_BODY = {"code": 200, "succ": True, "message": "", "data": {
    "platform": "抖音", "title": "demo",
    "video_url": "https://cdn.invalid/v.mp4"}}

print("=" * 62)
print("media_parser 韧性门禁（不联网 / 不耗额度 / 不改 Key）")
print("=" * 62)

# 1) 204 空结果 —— 独立人话，不再混进"非 JSON"
data, reason, calls = run([Response(204)])
check("204 不抛异常、说清是空结果",
      data is None and "204" in reason and "空结果" in reason, f"{reason} calls={calls}")
check("204 不重试（只请求一次）", calls == 1, f"calls={calls}")

# 2) 偶发超时 —— 重发一次救回
data, reason, calls = run([TimeoutError("模拟网络超时"), Response(200, OK_BODY)])
check("超时重发一次后救回",
      data is not None and (data.get("video_url") or "").startswith("https://cdn.invalid/"),
      f"{reason} calls={calls}")
check("超时恰好重发一次", calls == 2, f"calls={calls}")

# 2b) 一直超时 —— 最多两次，给可操作的人话
data, reason, calls = run([TimeoutError("模拟网络超时")])
check("超时耗尽给人话原因",
      data is None and "连不上解析服务" in reason and "网络超时" in reason, reason)
check("超时耗尽也只请求两次（没放开成无限）", calls == 2, f"calls={calls}")

# 3) 429 耗尽 —— 原样保留 1 次重试
data, reason, calls = run([Response(200, {"code": 429, "succ": False, "message": ""})])
check("429 耗尽不抛异常、说限流", data is None and "限流" in reason, reason)
check("429 恰好重发一次", calls == 2, f"calls={calls}")

# 4) 非 JSON 响应
data, reason, calls = run([Response(200, raise_json=True)])
check("非 JSON 不抛异常、说人话",
      data is None and "非 JSON" in reason, reason)
check("非 JSON 不重试", calls == 1, f"calls={calls}")

# 5) Key 缺失 —— patch 模块默认值，绝不改源码字面量
with patch.object(mp, "DEFAULT_API_KEY", ""):
    FakeClient.script, FakeClient.calls = [Response(200, OK_BODY)], 0
    with patch("httpx.Client", FakeClient):
        data, reason = mp.parse("https://v.douyin.com/Fake/", api_key="")
check("Key 缺失不抛异常、说联系作者",
      data is None and "未配置解析 Key" in reason, reason)
check("Key 缺失一个请求都不发", FakeClient.calls == 0, f"calls={FakeClient.calls}")

# 6) 成功路径不受影响
data, reason, calls = run([Response(200, OK_BODY)])
check("成功路径照常返回 dict 且 reason 为空",
      data is not None and data.get("platform") == "douyin"
      and data.get("platform_name") == "抖音" and reason == "",
      f"{reason} {data}")
check("成功只请求一次", calls == 1, f"calls={calls}")

# 7) health 探活：GET 500 → 明确不可用
class DownClient(FakeClient):
    def get(self, _url, headers=None):
        return Response(500)

with patch("httpx.Client", DownClient):
    ok, note = mp.health()
check("health 探活失败返回 (False, 说明)", ok is False and "HTTP 500" in note, note)

# 8) 源码断言：多套门禁钉死的字面量只加不删
MP = (ROOT / "justoneapi_collector" / "media_parser.py").read_text(encoding="utf-8")
VD = (ROOT / "justoneapi_collector" / "video_downloader.py").read_text(encoding="utf-8")
check("204 独立判空仍在",
      'if getattr(r, "status_code", None) == 204:' in MP)
check("超时单次重试标记仍在", "_retried_net" in MP and "time.sleep(1.0)" in MP)
check("原有 429 / 502 重试字面量没被改坏",
      "_code == 429 and attempt == 1" in MP and "for attempt in (1, 2, 3):" in MP)
check("批量补全入口先探活",
      "from .media_parser import health as _svc_health" in VD)
check("探活失败给统一提示", "解析服务当前不可用" in VD)
check("BASE_URL 字面量未被改（okdata 门禁依赖）",
      'BASE_URL = "https://video.zacao.top"' in MP)

print("=" * 62)
print(f"韧性门禁：{len(PASS)} 项通过，{len(FAIL)} 项失败")
if FAIL:
    for f in FAIL:
        print("  失败：", f)
    sys.exit(1)
