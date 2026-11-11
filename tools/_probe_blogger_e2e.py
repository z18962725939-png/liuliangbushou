# -*- coding: utf-8 -*-
"""端到端：客户粘主页分享口令 → 认出博主 → 拉作品列表（真浏览器）。"""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac100ef13e78468d54cea96\justoneapi-collector")

from justoneapi_collector.video_downloader import VideoDownloader

TEXT = "长按复制此条消息，打开抖音搜索，查看TA的更多作品。 https://v.douyin.com/GfePy6r3aYY/"


class DummyClient:
    calls = 0
    def request(self, *a, **k):
        DummyClient.calls += 1
        raise RuntimeError("不允许调接口")


d = VideoDownloader(DummyClient(), save_dir=None)

t0 = time.time()
info, reason = d.resolve_share_link_with_reason(TEXT)
sec = getattr(d, "_last_blogger_sec_uid", "")
print(f"[1] 解析判定：info={info} 识别为博主主页={bool(sec)} {time.time()-t0:.1f}s", flush=True)

t1 = time.time()
sid, why = d.resolve_sec_uid(TEXT)
print(f"[2] 取 sec_uid：{bool(sid)} why={why!r} {time.time()-t1:.1f}s", flush=True)

t2 = time.time()
videos, err = d.list_user_list("post", sid, use_browser=True, max_pages=1)
print(f"[3] 拉列表：{len(videos)} 条 err={err!r} {time.time()-t2:.1f}s", flush=True)
for v in videos[:4]:
    print("    -", getattr(v, "video_id", ""), (getattr(v, "desc", "") or "")[:34], flush=True)

print(f"\n总耗时 {time.time()-t0:.1f}s | 接口调用 {DummyClient.calls} 次（必须 0）")
print("结论：", "✅ 通过" if videos and DummyClient.calls == 0 else "❌ 有问题")
