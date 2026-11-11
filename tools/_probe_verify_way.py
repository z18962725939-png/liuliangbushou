# -*- coding: utf-8 -*-
"""无头 vs 有头打开同一条作品页对照。

无头那条卡在「验证码中间页」25 秒不动。这份脚本回答：是不是 headless 的
自动化指纹被抖音认出来了？如果有头能正常出作品，说明只是指纹问题，
就有得修；如果两种都是验证码，那就是这个 IP / 账号被风控了，软件改什么都不好使。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

VID = "7698127444486328433"
URL = f"https://www.douyin.com/video/{VID}"


def load_cookie() -> str:
    cfg = json.loads((Path.home() / "AppData" / "Roaming" / "TrafficCatcher"
                      / "config.json").read_text(encoding="utf-8"))
    return str(cfg.get("douyin_cookie") or "")


def probe(headless: bool) -> None:
    tag = "无头" if headless else "有头（会弹窗）"
    prof = (Path.home() / "AppData" / "Roaming" / "TrafficCatcher"
            / "browser_profile" / ("_probe_hless" if headless else "_probe_headful"))
    c = DouyinCDPCrawler(prof, headless=headless, timeout=25.0)
    ok, msg = c.start()
    print(f"\n===== {tag} =====\n启动：{ok} {msg}", flush=True)
    if not ok:
        return
    try:
        c.add_cookies(load_cookie())
        c.navigate(URL)
        for i in range(10):
            time.sleep(2.0)
            title = str(c.evaluate("document.title") or "")
            href = str(c.evaluate("location.href") or "")
            has_v = str(c.evaluate(
                "(document.querySelectorAll('video').length||0)+''") or "0")
            print(f"  {(i+1)*2:3.0f}s title={title[:26]:<26} video={has_v} href={href[:46]}",
                  flush=True)
            if has_v.isdigit() and int(has_v) > 0:
                print("  >>> 作品页渲染出来了 ✓", flush=True)
                break
    finally:
        c.stop()


if __name__ == "__main__":
    probe(headless=True)
    probe(headless=False)
