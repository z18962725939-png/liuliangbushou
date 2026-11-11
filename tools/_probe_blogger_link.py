# -*- coding: utf-8 -*-
"""验证：主页链接识别链路（不调接口），以及正常作品链接不被误判。"""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\Administrator\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6ac100ef13e78468d54cea96\justoneapi-collector")

from justoneapi_collector.video_downloader import VideoDownloader

SHORT_BLOG = "https://v.douyin.com/GfePy6r3aYY/"
LONG_BLOG = ("https://www.douyin.com/user/"
             "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp")
SHARE_TEXT = ("长按复制此条消息，打开抖音搜索，查看TA的更多作品。 "
              "https://v.douyin.com/GfePy6r3aYY/")
VIDEO = "https://www.douyin.com/video/7696734238402222720"


class DummyClient:
    calls = 0
    def request(self, *a, **k):
        DummyClient.calls += 1
        raise RuntimeError("测试不允许调接口")


d = VideoDownloader(DummyClient(), save_dir=None)

print("=" * 60)
for label, txt in (("主页短链", SHORT_BLOG), ("主页长链", LONG_BLOG),
                   ("分享口令文本", SHARE_TEXT)):
    t = time.time()
    sec, why = d.resolve_sec_uid(txt)
    ok = "✅" if sec == "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp" else "❌"
    print(f"{ok} resolve_sec_uid({label}) -> {sec!r} why={why!r} {time.time()-t:.1f}s")

print("-" * 60)
for label, txt in (("主页短链", SHORT_BLOG), ("主页长链", LONG_BLOG)):
    t = time.time()
    info, reason = d.resolve_share_link_with_reason(txt)
    flag = getattr(d, "_last_blogger_sec_uid", "")
    ok = "✅" if (info is None and flag) else "❌"
    print(f"{ok} 解析({label}) info={info} blogger={flag[:16]}… {time.time()-t:.1f}s")
    print(f"    文案：{reason[:80]}")

print("-" * 60)
# 正常作品链接：绝不能落到主页分支
info, reason = d.resolve_share_link_with_reason(VIDEO)
flag = getattr(d, "_last_blogger_sec_uid", "")
print(f"{'❌' if flag else '✅'} 作品链接 blogger 标志={flag!r}（必须为空）")
print(f"    info={'有' if info else '无'}  reason={reason[:90]}")
if info:
    print(f"    video_id={getattr(info,'video_id','')} desc={(getattr(info,'desc','') or '')[:30]}")
    print(f"    play_url={(getattr(info,'play_url','') or '')[:80]}")

print("-" * 60)
print(f"接口调用次数 = {DummyClient.calls}（必须为 0）")
