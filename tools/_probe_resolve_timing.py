# -*- coding: utf-8 -*-
"""解析耗时探针：逐阶段计时，找"一直解析中"卡在哪一步。

用法：python tools/_probe_resolve_timing.py
不消耗 API 额度（把 client 换成永远失败的桩，只走免费通道）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402


class UpsideDownClient:
    """永远失败的桩：任何接口调用立刻返回失败，不联网、不花钱。"""

    class _R:
        success = False
        data = None
        message = "probe: 接口未接入"

    def request(self, *a, **kw):
        return self._R()

    def __getattr__(self, name):
        def _fail(*a, **kw):
            return None
        return _fail


def timed(label, fn, *a, **kw):
    t0 = time.perf_counter()
    try:
        out = fn(*a, **kw)
    except Exception as e:                                   # noqa: BLE001
        out = f"EXC {type(e).__name__}: {e}"
    dt = time.perf_counter() - t0
    brief = repr(out)
    if len(brief) > 110:
        brief = brief[:110] + "…"
    print(f"  [{dt:6.2f}s] {label} -> {brief}")
    return dt, out


def main():
    dl = VideoDownloader(UpsideDownClient(), save_dir=Path.cwd() / "_tmp_script_out" / "probe_dl")
    dl._free_disabled = False

    cases = [
        ("纯数字ID(19位，页面不一定存在)",
         "7345678901234567890"),
        ("不存在的短链", "https://v.douyin.com/AAAAAAAAAAA/"),
        ("普通视频页URL", "https://www.douyin.com/video/7345678901234567890"),
        ("博主主页URL", "https://www.douyin.com/user/MS4wLjABAAAA_test_probe"),
    ]
    total = 0.0
    for name, text in cases:
        print(f"\n=== {name} ===")
        dt, out = timed("resolve_share_link_with_reason", dl.resolve_share_link_with_reason, text)
        total += dt
    print(f"\n合计 {total:.1f}s")


if __name__ == "__main__":
    main()
