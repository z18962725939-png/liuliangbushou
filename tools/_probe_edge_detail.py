# -*- coding: utf-8 -*-
"""打开 Edge 商店详情页，确认页面真实内容（是否 404 / 是否是我们的扩展）。

临时排查脚本，不参与打包。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

# 第 1 个是对照组（商店里确定存在的第三方扩展），第 2 个是我们自己的。
# 必须成对测：微软商店是 SPA，404 页面也返回 HTTP 200，
# 只 curl 判状态码会得出完全错误的结论（2026-10-24 踩过）。
OURS = "opjdobghhbhphnpeaeipljkhhigjlfga"
# 同一个 ID 跑不同地区参数：后台显示语言是「英语（美国）」，
# 若提交时"市场"只勾了美国，中国区就会拿到 404 页。
# ★★ 唯一可靠的判据是 title，不要去 body 里找关键词：
#    真页面 title = "<扩展名> - Microsoft Edge Addons"（含 " - "）；
#    404 页只有通用标题 "Microsoft Edge 加载项" / "Microsoft Edge Add-ons"。
#    body 里找"获取/Get"不可靠 —— 英文页按钮是 Get、导航里也带 Edge，都会误判。
CASES = [
    ("对照-抖珍藏  zh-CN", "kaepaapgaokpobjhamjjekinijbcoihe", "?hl=zh-CN"),
    ("对照-抖珍藏  en-US", "kaepaapgaokpobjhamjjekinijbcoihe", "?hl=en-US&gl=US"),
    ("我们的       zh-CN", OURS, "?hl=zh-CN&gl=CN"),
    ("我们的       en-US", OURS, "?hl=en-US&gl=US"),
]

JS = r"""
(function () {
  var t = document.title || '';
  var b = (document.body && document.body.innerText ? document.body.innerText : '')
            .replace(/\s+/g, ' ').trim();
  var links = [];
  document.querySelectorAll('a[href]').forEach(function (a) {
    var h = a.getAttribute('href') || '';
    if (/addons\/detail|edgeaddons|crx/i.test(h)) links.push(h);
  });
  return JSON.stringify({ title: t, body: b.slice(0, 900), links: links.slice(0, 6) });
})()
"""


def main() -> int:
    prof = Path(r"C:\Users\Administrator\AppData\Local\Temp\_ext_probe_profile")
    c = DouyinCDPCrawler(prof, headless=True, timeout=30.0)
    ok, msg = c.start()
    print("start:", ok, msg)
    if not ok:
        return 1
    try:
        for label, ext_id, qs in CASES:
            url = f"https://microsoftedge.microsoft.com/addons/detail/{ext_id}{qs}"
            print(f"\n########## {label}  {ext_id} ##########")
            c.navigate(url)
            last = ""
            for i in range(8):
                time.sleep(2.0)
                raw = c.evaluate(JS, timeout=15)
                if not raw:
                    continue
                d = json.loads(raw)
                body = d.get("body", "")
                last = body
                title = d.get("title", "") or ""
                # 真页面特征：title 里带 " - "（扩展名在前，站点名在后）
                if " - " in title and "edge addon" in title.lower():
                    print(f"  >>> 真页面 ✓  title={title!r}")
                    print("  body :", body[:200])
                    break
                # 落空：通用标题 = 商店 404 页
                if body and len(body) > 150:
                    print(f"  >>> 404 页（通用标题）title={title!r}")
                    print("  含404文案:", "找不到你请求的页面" in body
                          or "can't find the page" in body.lower())
                    break
            else:
                print("  未判定，body 前 160 字：", last[:160])
        return 0
    finally:
        c.stop()


if __name__ == "__main__":
    raise SystemExit(main())
