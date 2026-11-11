# -*- coding: utf-8 -*-
"""商店详情页：带 slug 的 URL 和纯 ID 的 URL 是不是都能打开？

起因（2026-10-24 17:52）：
    客户后台截图里地址栏是
        /addons/detail/流量捕手-抖音无水印下载/opjdobghhbhphnpeaeipljkhhigjlfga
    带 slug，页面正常、有「获取」按钮；
    而 _probe_edge_detail.py 只用纯 ID，渲染出来是 404 页。
    ⇒ 怀疑新上架扩展只有带 slug 的完整路径被路由到，纯 ID 还没建索引。
      这个脚本就是把三种写法摆在一起对拍。

★ 判据仍然是 title（商店是 SPA，HTTP 一律 200）：
      真页面 = "<扩展名> - Microsoft Edge Addons"
      404 页 = "Microsoft Edge 加载项" / "Microsoft Edge Add-ons"
临时排查脚本，不参与打包。
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

EXT_ID = "opjdobghhbhphnpeaeipljkhhigjlfga"
SLUG = "流量捕手-抖音无水印下载"
BASE = "https://microsoftedge.microsoft.com/addons/detail"

CASES = [
    ("A 纯 ID（旧写法）", f"{BASE}/{EXT_ID}"),
    ("B 带 slug（后台给的）", f"{BASE}/{quote(SLUG)}/{EXT_ID}"),
    ("C 带 slug + 无 gl", f"{BASE}/{quote(SLUG)}/{EXT_ID}?hl=zh-CN"),
    ("D 对照 纯 ID", f"{BASE}/kaepaapgaokpobjhamjjekinijbcoihe"),
]

JS = r"""
(function () {
  var t = document.title || '';
  var b = (document.body && document.body.innerText ? document.body.innerText : '')
            .replace(/\s+/g, ' ').trim();
  return JSON.stringify({ title: t, body: b.slice(0, 400) });
})()
"""


def main() -> int:
    prof = Path(r"C:\Users\Administrator\AppData\Local\Temp\_ext_slug_profile")
    c = DouyinCDPCrawler(prof, headless=True, timeout=30.0)
    ok, msg = c.start()
    print("start:", ok, msg, flush=True)
    if not ok:
        return 1
    try:
        for label, url in CASES:
            print(f"\n########## {label}\n          {url}", flush=True)
            c.navigate(url)
            verdict = "未判定"
            for _ in range(9):
                time.sleep(2.0)
                raw = c.evaluate(JS, timeout=15)
                if not raw:
                    continue
                d = json.loads(raw)
                title = (d.get("title") or "").strip()
                body = d.get("body") or ""
                if " - " in title and "edge addon" in title.lower():
                    verdict = f"真页面 ✓  title={title!r}"
                    extra = body[:160]
                    break
                if body and len(body) > 150:
                    verdict = f"404 页  title={title!r}"
                    extra = body[:160]
                    break
            else:
                extra = ""
            print("   ", verdict, flush=True)
            if extra:
                print("    body:", extra, flush=True)
        return 0
    finally:
        c.stop()


if __name__ == "__main__":
    raise SystemExit(main())
