# -*- coding: utf-8 -*-
"""复刻 capture_aweme_detail 的开头几步，把每次读到的 location.href 打出来。

目的：它凭一句 `home_re.match(href)` 就判「被重定向到首页」，批量导入
每一条都走这个判断。现在全批都报重定向，得看清 href 到底经历了哪些值。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

BASE = Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "browser_profile"
VID = "7698127444486328433"
URL = f"https://www.douyin.com/video/{VID}"


def load_cookie() -> str:
    cfg = json.loads((Path.home() / "AppData" / "Roaming" / "TrafficCatcher"
                      / "config.json").read_text(encoding="utf-8"))
    return str(cfg.get("douyin_cookie") or "")


def trace(caption: str, warm: bool) -> None:
    print(f"\n===== {caption} =====", flush=True)
    prof = BASE / "_resolve"
    c = DouyinCDPCrawler(prof, headless=True, timeout=25.0)
    ok, _ = c.start()
    print("启动：", ok, flush=True)
    try:
        n = c.add_cookies(load_cookie())
        print(f"注入 Cookie 条数：{n}", flush=True)
        if warm:
            c.open_url("https://www.douyin.com/")
            time.sleep(1.5)
            print("预热完成，当前 href =", c.evaluate("location.href"), flush=True)

        # —— 完全照抄 capture_aweme_detail 开头 ——
        try:
            c._page.call("Network.enable", {"maxResourceBufferSize": 50_000_000,
                                            "maxPostDataSize": 1_000_000}, timeout=10)
        except Exception:                       # noqa: BLE001
            pass
        c._page.call("Page.navigate", {"url": URL}, timeout=15)
        for i in range(16):
            time.sleep(0.5)
            href = str(c.evaluate("location.href") or "")
            title = str(c.evaluate("document.title") or "")
            cookie_len = str(c.evaluate("document.cookie.length||0") or "")
            print(f"  {i*0.5+0.5:4.1f}s href={href[:52]:<52} title={title[:18]:<18} cookie长度={cookie_len}",
                  flush=True)
    finally:
        c.stop()


if __name__ == "__main__":
    trace("A 不做预热", warm=False)
    trace("B 做预热", warm=True)
