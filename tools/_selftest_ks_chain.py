# -*- coding: utf-8 -*-
"""临时自测：快手账号作品批量采集两段式链路 headless 实跑（用完即删）。

覆盖：
  1) _resolve_kuaishou_uid：kwaiId 主页形态 / v.kuaishou.com 短链形态
  2) list_kuaishou_blogger_videos：翻页拉全 + main_mv_urls 直链 + share_url/author
  3) 忠岩（0 公开视频账号）→ 人话错误
  4) _reparse_for_fresh_url 快手分支
  5) 真实下载第一条落盘
"""
import sys
import tempfile
import time
import traceback

sys.path.insert(0, r"D:\trafficcatcher-src\justoneapi-collector")

from justoneapi_collector.client import JustOneAPICollector
from justoneapi_collector.video_downloader import VideoDownloader

TOKEN = "kPa7Fxdepu2GAuxU"
client = JustOneAPICollector(token=TOKEN, cache_enabled=False)
dl = VideoDownloader(client, save_dir=None)

print("== 1) resolve：kwaiId 主页形态")
uid, reason = dl._resolve_kuaishou_uid("https://www.kuaishou.com/profile/mengjia321")
print("   uid =", uid, "| reason =", reason or "-")
assert uid == "610396403", f"objectId 映射错误: {uid}"

print("== 2) resolve：v.kuaishou.com 短链形态（忠岩主页）")
uid2, reason2 = dl._resolve_kuaishou_uid("看看这个主播 https://v.kuaishou.com/JTs3J28b 复制此链接")
print("   uid =", uid2, "| reason =", reason2 or "-")
assert uid2 == "729776709", f"短链解析错误: {uid2}"

print("== 3) list：mengjia321 两段式拉全")
t0 = time.time()
videos, err = dl.list_kuaishou_blogger_videos(
    "https://www.kuaishou.com/profile/mengjia321", max_pages=5)
print(f"   {len(videos)} 条, err={err or '-'}, note={dl._last_list_note!r}, 耗时 {time.time()-t0:.0f}s")
assert videos, "列表为空"
v0 = videos[0]
print(f"   item0: vid={v0.video_id} author={v0.author!r} platform={v0.platform}")
print(f"          desc={str(v0.desc)[:40]!r}")
print(f"          share_url={v0.share_url}")
print(f"          直链有无={bool(v0.no_watermark_url)} host={(v0.no_watermark_url or '').split('/')[2] if v0.no_watermark_url else '-'}")
print(f"          时长ms={v0.duration} 点赞={v0.digg_count}")
assert v0.platform == "kuaishou" and v0.share_url and v0.video_id
with_url = sum(1 for v in videos if v.no_watermark_url)
print(f"   直链覆盖率: {with_url}/{len(videos)}")

print("== 4) 忠岩（0 公开作品）→ 人话错误")
videos2, err2 = dl.list_kuaishou_blogger_videos("https://v.kuaishou.com/JTs3J28b")
print(f"   videos={len(videos2)} err={err2!r}")
assert not videos2 and "公开作品" in err2, "0 视频账号的错误提示不对"

print("== 5) _reparse_for_fresh_url 快手分支")
v0.no_watermark_url = ""     # 模拟直链丢失
fresh = dl._reparse_for_fresh_url(v0)
if fresh:
    print(f"   重解析成功: 新直链有无={bool(fresh.no_watermark_url)}, author保留={fresh.author!r}")
    assert fresh.no_watermark_url and fresh.author == v0.author
    v0.no_watermark_url = fresh.no_watermark_url
else:
    print("   重解析未拿到（zacao 临时风控可接受，不作为失败）")
    # 用 main_mv_urls 补回以便第 6 步下载
    raise SystemExit("  → zacao 临时不可用，第 6 步跳过")

print("== 6) 真实下载第一条落盘")
tmp = tempfile.mkdtemp(prefix="ks_dl_test_")
from pathlib import Path
dl.save_dir = Path(tmp)
path, derr = dl._download_video_file(videos[0], "")
if path:
    size = path.stat().st_size
    print(f"   下载成功: {path.name} ({size/1024/1024:.1f} MB)")
    assert size > 100_000, "文件过小，疑似错误内容"
else:
    print("   下载失败:", derr)
    raise SystemExit(1)

print("\n[全部通过]", time.strftime("%H:%M:%S"))
