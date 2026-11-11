# -*- coding: utf-8 -*-
"""查 Edge 商店里「流量捕手」的上架状态与正确详情页地址。

临时排查脚本，不参与打包。
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

QUERIES = [
    "流量捕手·抖音无水印下载",
    "抖音无水印下载",
    "流量捕手",
]
SEARCH = ("https://microsoftedge.microsoft.com/addons/search/"
          "{q}?hl=zh-CN")

JS = r"""
(function () {
  var out = [];
  document.querySelectorAll('a[href*="/addons/detail/"]').forEach(function (a) {
    var href = a.getAttribute('href') || '';
    var card = a.closest('[class*="card"]') || a.parentElement;
    var txt = (card && card.innerText ? card.innerText : a.innerText || '').slice(0, 200);
    out.push({ href: href, text: txt.replace(/\s+/g, ' ').trim() });
  });
  return JSON.stringify({
    count: out.length,
    items: out.slice(0, 12),
    heading: (document.querySelector('#list-heading') || {}).innerText || ''
  });
})()
"""


def main() -> int:
    prof = Path(r"C:\Users\Administrator\AppData\Local\Temp\_ext_probe_profile")
    c = DouyinCDPCrawler(prof, headless=True, timeout=30.0)
    ok, msg = c.start()
    print("start:", ok, msg)
    if not ok:
        return 1
    import json
    import urllib.parse
    hit = False
    try:
        for q in QUERIES:
            url = SEARCH.format(q=urllib.parse.quote(q))
            print(f"\n########## 搜索：{q} ##########")
            c.navigate(url)
            got = False
            for i in range(8):
                time.sleep(1.5)
                raw = c.evaluate(JS, timeout=15)
                if not raw:
                    continue
                data = json.loads(raw)
                if data.get("count"):
                    print(f"heading: {data.get('heading', '')}")
                    seen = set()
                    for it in data["items"]:
                        if it["href"] in seen:
                            continue
                        seen.add(it["href"])
                        print("  href:", it["href"])
                        print("  text:", it["text"][:130])
                    got = True
                    if "流量捕手" in " ".join(x["text"] for x in data["items"]):
                        print("  ★★★ 命中：结果里出现「流量捕手」")
                        hit = True
                    break
            if not got:
                print("  轮询 8 次无结果卡片")
        return 0 if hit else 2
    finally:
        c.stop()


if __name__ == "__main__":
    raise SystemExit(main())
