# -*- coding: utf-8 -*-
"""探测：抖音主页「作品」列表滚动到底为什么停在上限（53 条 vs 官网 68 条）。

要回答三件事：
  1. 页面的滚动容器到底是 window 还是某个内部 div？
  2. 现在的 window.scrollTo 到底有没有触发新一页请求？
  3. 换成「滚动容器 scrollTop」或「真实滚轮事件」后，能不能继续加载？
"""
from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, ".")

from justoneapi_collector.douyin_cdp_crawler import (  # noqa: E402
    DouyinCDPCrawler, _LIST_PATTERNS,
)
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402

SEC = "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp"
URL = f"https://www.douyin.com/user/{SEC}"

JS_PROBE = r"""
(() => {
  const out = [];
  const nodes = document.querySelectorAll('div,main,section');
  for (const el of nodes) {
    if (el.clientHeight < 240) continue;
    if (el.scrollHeight <= el.clientHeight + 150) continue;
    const cs = getComputedStyle(el);
    if (!/(auto|scroll)/.test(cs.overflowY)) continue;
    out.push({
      tag: el.tagName,
      id: el.id || '',
      cls: String(el.className || '').slice(0, 70),
      sh: el.scrollHeight,
      ch: el.clientHeight,
      st: Math.round(el.scrollTop),
      e2e: el.getAttribute('data-e2e') || '',
    });
    if (out.length >= 10) break;
  }
  return JSON.stringify({
    docSh: document.documentElement.scrollHeight,
    docCh: document.documentElement.clientHeight,
    bodySh: document.body ? document.body.scrollHeight : -1,
    win: window.innerHeight,
    scrollY: Math.round(window.scrollY),
    containers: out,
  });
})()
"""


def evaluate(c, expr):
    res = c._page.call("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True}, timeout=10)
    return (res.get("result") or {}).get("value")


def count_now(c, patterns, collected, seen):
    c._collect_events(patterns, collected, seen)
    return len(collected)


def main():
    # 与生产同一条路径：app_data_dir()/browser_profile（用假 client 只为拿路径）
    class _Null:
        def request(self, *a, **k):
            raise RuntimeError("探测脚本不调接口")

    from justoneapi_collector.video_downloader import VideoDownloader as _VD
    prof_dir = _VD(_Null()).browser_profile_dir or \
        r"C:\Users\Administrator\AppData\Roaming\TrafficCatcher\browser_profile"
    print("profile:", prof_dir)

    c = DouyinCDPCrawler(prof_dir, headless=False)
    ok, msg = c.start()
    print("start:", ok, msg)
    if not ok:
        return

    try:
        patterns = _LIST_PATTERNS["post"]
        collected: list[dict] = []
        seen: set[str] = set()

        c._pending.clear()
        c._done.clear()
        c._page.drain_events()

        t0 = time.time()
        c._page.call("Page.navigate", {"url": URL}, timeout=20)
        # 等首屏接口 + 预取
        for _ in range(12):
            time.sleep(1.0)
            count_now(c, patterns, collected, seen)
        print(f"[首屏 {time.time()-t0:.1f}s] 抓到 {len(collected)} 条")

        info = json.loads(evaluate(c, JS_PROBE))
        print("=== 页面滚动结构 ===")
        print(f"  文档 scrollHeight={info['docSh']} clientHeight={info['docCh']}"
              f" body={info['bodySh']} window={info['win']} scrollY={info['scrollY']}")
        for el in info["containers"]:
            print(f"  容器 <{el['tag']} id={el['id']!r} cls={el['cls']!r}"
                  f" e2e={el['e2e']!r}> sh={el['sh']} ch={el['ch']} st={el['st']}")

        # ---- 方式 A：现在的做法（window.scrollTo） -------------------------
        print("\n=== A. window.scrollTo(0, document.body.scrollHeight) ===")
        for i in range(4):
            before = len(collected)
            evaluate(c, "window.scrollTo(0, document.body.scrollHeight); 1")
            time.sleep(2.5)
            now = count_now(c, patterns, collected, seen)
            print(f"  第{i+1}次: {before} -> {now}  scrollY={evaluate(c,'Math.round(window.scrollY)')}")

        # ---- 方式 B：滚动容器 scrollTop -----------------------------------
        print("\n=== B. 滚动容器 scrollTop = scrollHeight ===")
        js_scroll = r"""
        (() => {
          let n = 0;
          const nodes = document.querySelectorAll('div,main,section');
          for (const el of nodes) {
            if (el.clientHeight < 240) continue;
            if (el.scrollHeight <= el.clientHeight + 150) continue;
            const cs = getComputedStyle(el);
            if (!/(auto|scroll)/.test(cs.overflowY)) continue;
            el.scrollTop = el.scrollHeight;
            n++;
            if (n >= 3) break;
          }
          return n;
        })()
        """
        for i in range(4):
            before = len(collected)
            hit = evaluate(c, js_scroll)
            time.sleep(2.5)
            now = count_now(c, patterns, collected, seen)
            print(f"  第{i+1}次: {before} -> {now}（滚动容器 {hit} 个）")

        # ---- 方式 C：真实滚轮事件（Input.dispatchMouseEvent） -------------
        print("\n=== C. Input.dispatchMouseEvent 滚轮 ===")
        for i in range(4):
            before = len(collected)
            try:
                c._page.call("Input.dispatchMouseEvent", {
                    "type": "mouseWheel", "x": 700, "y": 600,
                    "deltaX": 0, "deltaY": 3000, "pointerType": "mouse",
                }, timeout=8)
            except Exception as e:                      # noqa: BLE001
                print("   滚轮失败:", e)
            time.sleep(2.5)
            now = count_now(c, patterns, collected, seen)
            print(f"  第{i+1}次: {before} -> {now}")

        print(f"\n最终 {len(collected)} 条（官网显示 68）")
        print("前 3 条 id:", [str(a.get('aweme_id')) for a in collected[:3]])
    finally:
        c.stop()


if __name__ == "__main__":
    main()
