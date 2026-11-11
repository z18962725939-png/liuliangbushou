# -*- coding: utf-8 -*-
"""验证：修好滚动后，「拉取列表」能不能拉满（客户现场：官网 68 条，软件 53 条）。"""
from __future__ import annotations

import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

SEC = "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp"


class _Null:
    calls = 0

    def request(self, *a, **k):
        _Null.calls += 1
        raise RuntimeError("不允许调接口")


d = VideoDownloader(_Null())
print("profile:", d.browser_profile_dir)

last = [0]


def on_progress(total):
    if total - last[0] >= 10 or total < last[0]:
        print(f"  ...已拉取 {total} 条  ({time.time()-t0:.1f}s)")
        last[0] = total


t0 = time.time()
videos, err = d.list_user_list("post", SEC, use_browser=True, max_pages=30,
                               on_progress=on_progress)
print("-" * 60)
print(f"结果：{len(videos)} 条 / 耗时 {time.time()-t0:.1f}s / 接口调用 {_Null.calls} 次")
print("err =", repr(err))
print("note =", repr(getattr(d, "_last_list_note", "")))
ids = [getattr(v, "video_id", "") for v in videos]
print("唯一 id 数:", len(set(ids)))
