# -*- coding: utf-8 -*-
"""用**有头** Edge + 全新 profile 打开商店页 —— 等价于一个陌生访客。

为什么要这一步（2026-10-24）：
    无头模式对新上架扩展一律 404（老扩展正常），怀疑商店对 headless 有反爬；
    但结论关系到官网主按钮要不要指向商店，必须用"真浏览器 + 干净状态"验一次。
    会短暂弹出一个 Edge 窗口，跑完自己关掉。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

OURS = "https://microsoftedge.microsoft.com/addons/detail/opjdobghhbhphnpeaeipljkhhigjlfga"
CTRL = "https://microsoftedge.microsoft.com/addons/detail/kaepaapgaokpobjhamjjekinijbcoihe"

JS = r"""
(function () {
  var t = document.title || '';
  var b = (document.body && document.body.innerText ? document.body.innerText : '')
            .replace(/\s+/g, ' ').trim();
  return JSON.stringify({ title: t, body: b.slice(0, 300) });
})()
"""


def look(c, url, label):
    print(f"\n########## {label}\n          {url}", flush=True)
    c.navigate(url)
    for _ in range(10):
        time.sleep(2.0)
        raw = c.evaluate(JS, timeout=15)
        if not raw:
            continue
        d = json.loads(raw)
        title = (d.get("title") or "").strip()
        body = d.get("body") or ""
        if " - " in title and "edge addon" in title.lower():
            print(f"   真页面 ✓  title={title!r}", flush=True)
            # 顺带抓「获取」按钮是否存在（英文页是 Get）
            hit = c.evaluate(
                r"""(function(){var b=(document.body.innerText||'');
                    return JSON.stringify({get:/\b(获取|Get|安裝|安装)\b/.test(b),
                                           name:/流量捕手/.test(b)});})()""",
                timeout=10,
            )
            print("   页面特征:", hit, flush=True)
            return True
        if body and len(body) > 150:
            print(f"   404 页  title={title!r}", flush=True)
            return False
    print("   未判定", flush=True)
    return None


def main() -> int:
    prof = Path(r"C:\Users\Administrator\AppData\Local\Temp\_ext_real_profile")
    c = DouyinCDPCrawler(prof, headless=False, timeout=30.0)
    ok, msg = c.start()
    print("start:", ok, msg, flush=True)
    if not ok:
        return 1
    try:
        look(c, OURS, "我们的扩展（有头 + 干净 profile）")
        look(c, CTRL, "对照组")
    finally:
        c.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
