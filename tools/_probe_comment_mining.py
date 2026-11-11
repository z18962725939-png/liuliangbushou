# -*- coding: utf-8 -*-
"""实测探针：真实跑一遍「评论区截流」，看真接口到底能挖出多少意向用户。

会联网，成本约 0.3~0.6 元（搜索 2 次 + 每视频 1 页评论）。
用途：验证离线自测之外的真实链路 —— 参数名、分页、字段提取、意图命中率。

用法：
    python tools/_probe_comment_mining.py 按摩棒 2
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import comment_mining as cm                  # noqa: E402
from justoneapi_collector import keyword_search as ks                  # noqa: E402
from justoneapi_collector.client import JustOneAPICollector            # noqa: E402

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "按摩棒"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 2
TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "kPa7Fxdepu2GAuxU")


class _CountingClient(JustOneAPICollector):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.api_calls = 0

    def request(self, platform, endpoint, params=None):
        self.api_calls += 1
        return super().request(platform, endpoint, params)


client = _CountingClient(token=TOKEN, base_url="https://api.justoneapi.com",
                         cache_enabled=False)

print(f"【1】搜索「{KEYWORD}」拿真实视频…")
rows = ks.rows_from_results(ks.search_keywords(
    client, "douyin", [KEYWORD], depth_label="快速（2~3 次请求）"))
import re                                                              # noqa: E402
ids: list[str] = []
for r in rows:
    for cell in r:
        m = re.search(r"/video/(\d{15,})", str(cell))
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
ids = ids[:N]
print(f"    取到 {len(ids)} 个视频：{ids}")
if not ids:
    sys.exit("没拿到视频 ID，退出")

before = client.api_calls
print(f"\n【2】对每个视频抓 1 页评论并过滤意向词（预计 {cm.estimate_requests(len(ids), 1)} 次请求）")


def on_post(idx, total, pid):
    print(f"    · 第 {idx}/{total} 个：{pid}")


res = cm.mine_comments(client, "douyin", ids, pages=1,
                       include_replies=False, exclude_author=False,
                       on_log=lambda m: print("      ", m), on_post=on_post)

print(f"\n【3】结果")
print(f"    扫描评论     ：{res.scanned} 条")
print(f"    命中意向     ：{len(res.leads)} 条")
print(f"    实际请求     ：{client.api_calls - before} 次"
      f"（≈ {(client.api_calls - before) * 0.1:.1f} 元）")
if res.errors:
    print(f"    错误         ：{res.errors[:3]}")

print("\n【4】命中样例（最多 8 条）")
for x in res.leads[:8]:
    print(f"    · [{x.digg}赞] {x.user_name}（{x.user_unique_id}）"
          f"：{x.text[:40]}")
    print(f"        命中={x.hits} 主页={x.user_home[:60]}")

print("\n【5】导出行校验")
for row in cm.rows_from_leads(res.leads[:2]):
    print("   ", [str(v)[:26] for v in row])
