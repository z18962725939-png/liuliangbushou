# -*- coding: utf-8 -*-
"""实测探针：真实链接解析时，「免费页面解析」通道的命中率与省钱效果。

会联网，只花「关键词搜索」的钱（快速档约 0.2 元）；解析阶段若命中免费
通道则 0 元，回落接口才 0.1 元/条。用途：
  · 验证「免费优先」改动在真实抖音页面上是否真的省钱
  · 观察未登录 / 已登录 Cookie 两种情况下的命中率差异

用法：
    python tools/_probe_free_resolve.py 牙刷 5
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import keyword_search as ks                 # noqa: E402
from justoneapi_collector.client import JustOneAPICollector           # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader     # noqa: E402

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "牙刷"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "kPa7Fxdepu2GAuxU")


class _CountingClient(JustOneAPICollector):
    """给真实客户端套一层计数，看解析阶段到底打了几次接口。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.api_calls = 0
        self.api_endpoints = []

    def request(self, platform, endpoint, params=None):
        self.api_calls += 1
        self.api_endpoints.append(endpoint)
        return super().request(platform, endpoint, params)


client = _CountingClient(token=TOKEN, base_url="https://api.justoneapi.com",
                         cache_enabled=False)

print(f"真实搜索「{KEYWORD}」…（这一步会花约 0.2 元）")
results = ks.search_keywords(client, "douyin", [KEYWORD],
                             depth_label="快速（2~3 次请求）")
rows = ks.rows_from_results(results)
links = [r[-1] for r in rows if r and str(r[-1]).startswith("http")][:N]
print(f"搜到 {len(rows)} 条，取前 {len(links)} 条链接做解析测试\n")

search_calls = client.api_calls
client.api_calls = 0               # 只统计解析阶段

save_dir = Path(tempfile.mkdtemp(prefix="probe_dl_"))
dl = VideoDownloader(client, save_dir=save_dir)

# ---- 先看免费通道为什么没命中（诊断用）----
if links:
    probe_url = links[0]
    _v, _vid, note = dl._free_html_resolve(probe_url)
    try:
        resp = dl._redirect_client.get(probe_url, headers={
            "User-Agent": dl._SHORT_LINK_HEADERS["User-Agent"],
            "Referer": "https://www.douyin.com/"})
        print(f"诊断 · 页面状态：HTTP {resp.status_code}，落地 {str(resp.url)[:60]}")
        print(f"诊断 · 页面长度：{len(resp.text)} 字符")
        print(f"诊断 · 含 play_addr：{'play_addr' in resp.text}"
              f"｜含 aweme_id：{'aweme_id' in resp.text}"
              f"｜含 RENDER_DATA：{'RENDER_DATA' in resp.text}")
        print(f"诊断 · Cookie：{'有' if dl.cookie else '无（未登录，抖音大概率不给数据）'}")
    except Exception as e:                                    # noqa: BLE001
        print(f"诊断 · 页面请求异常：{type(e).__name__}: {e}")
    print(f"诊断 · 免费通道返回：{note or '成功'}\n")

ok = 0
for i, u in enumerate(links, 1):
    before = client.api_calls
    info, reason = dl._douyin_resolve_impl(u)
    used = client.api_calls - before
    flag = "免费" if used == 0 else f"花了 {used} 次接口"
    if info:
        ok += 1
        print(f"  [{i}] {flag}  ✓ {str(info.desc)[:28]}  无水印={'有' if info.no_watermark_url else '无'}")
    else:
        print(f"  [{i}] {flag}  ✗ {reason[:60]}")

print(f"\n解析成功 {ok}/{len(links)} 条")
print(f"解析阶段接口调用：{client.api_calls} 次  → 约 {client.api_calls * 0.1:.1f} 元")
print(f"（改动前：每条都要查详情接口，{len(links)} 条 ≈ {len(links) * 0.1:.1f} 元）")
if client.api_endpoints:
    from collections import Counter
    print("接口明细：", dict(Counter(client.api_endpoints)))
print(f"搜索阶段接口调用：{search_calls} 次")
