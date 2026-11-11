# -*- coding: utf-8 -*-
"""对照：长期使用的 _resolve 目录 vs 全新目录，谁会被抖音判「验证码中间页」。

推断来源（2026-10-25）：
    同一个作品、同一份 Cookie，用老的 `_resolve` 目录访问会卡在
    「验证码中间页」25 秒不动；换一个全新目录（`_probe_hless`）却
    4 秒就渲染出 <video>。怀疑是长期不变的 profile 被逐步标记了。

如果这条成立，修法就是：批量任务开始时换一个**新目录**，别让指纹累积。
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


def run(tag: str, prof: Path) -> str:
    c = DouyinCDPCrawler(prof, headless=True, timeout=25.0)
    ok, msg = c.start()
    if not ok:
        print(f"{tag}: 启动失败 {msg}", flush=True)
        return "启动失败"
    verdict = "没渲染出来"
    try:
        c.add_cookies(load_cookie())
        c.navigate(URL)
        for i in range(9):
            time.sleep(2.0)
            title = str(c.evaluate("document.title") or "")
            nv = str(c.evaluate("(document.querySelectorAll('video').length||0)+''") or "0")
            if nv.isdigit() and int(nv) > 0:
                verdict = f"渲染成功 ✓（{int((i+1)*2)} 秒，video={nv}）"
                break
            if "验证码" in title:
                verdict = f"验证码中间页 ✗（卡 {int((i+1)*2)} 秒）"
                break
    finally:
        c.stop()
    print(f"{tag}: {verdict}", flush=True)
    return verdict


if __name__ == "__main__":
    print("作品：", URL, flush=True)
    run("A 老目录 _resolve        ", BASE / "_resolve")
    run("B 全新目录  _rot_test1   ", BASE / "_rot_test1")
    run("C 再换全新  _rot_test2   ", BASE / "_rot_test2")
    run("D 回到老目录 _resolve    ", BASE / "_resolve")
