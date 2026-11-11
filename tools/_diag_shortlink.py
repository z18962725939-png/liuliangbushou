# -*- coding: utf-8 -*-
"""短链重定向链路对比：无 cookie / 带登录 cookie / 换 UA，逐步打印 302 与 body 特征。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

CFG = Path(r"C:\Users\Administrator\AppData\Roaming\TrafficCatcher\config.json")
cfg = json.loads(CFG.read_text(encoding="utf-8"))
COOKIE = (cfg.get("douyin_cookie") or "").strip()

UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LINK = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/iFIK4dwHNpo/"
print(f"测试链接: {LINK}")
print(f"配置 cookie 长度: {len(COOKIE)}")


def trace(tag: str, cookie: str, ua: str):
    print("\n" + "=" * 68)
    print(f"【{tag}】")
    headers = {"User-Agent": ua, "Referer": "https://www.douyin.com/",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    if cookie:
        headers["Cookie"] = cookie
    url = LINK
    try:
        with httpx.Client(follow_redirects=False, timeout=15) as cx:
            for hop in range(6):
                r = cx.get(url, headers=headers)
                loc = r.headers.get("location", "")
                print(f"  hop{hop}: {r.status_code}  <- {url}")
                if loc:
                    print(f"          Location → {loc}")
                if r.status_code not in (301, 302, 303, 307, 308):
                    body = r.text
                    print(f"          body {len(body)} 字节")
                    # 关键：页面里有没有我们想要的跳转线索
                    pats = {
                        "video/ID": r"douyin\.com/video/(\d{15,25})",
                        "RENDER_DATA": r"RENDER_DATA",
                        "_ROUTER_DATA": r"_ROUTER_DATA",
                        "location.replace": r"location\.replace\(",
                        "window.location.href": r"window\.location\.href",
                        "meta refresh": r"http-equiv=[\"']?refresh",
                        "aweme_id": r"aweme_id[\"']?\s*[:=]\s*[\"']?(\d{15,25})",
                    }
                    for name, pat in pats.items():
                        m = re.search(pat, body, re.IGNORECASE)
                        flag = "✅" if m else "—"
                        extra = ""
                        if m and name in ("video/ID", "aweme_id"):
                            extra = " " + (m.group(1) if m.groups() else "")
                        print(f"          {flag} {name}{extra}")
                    if "RENDER_DATA" in body or "_ROUTER_DATA" in body:
                        idx = body.find("RENDER_DATA")
                        if idx < 0:
                            idx = body.find("_ROUTER_DATA")
                        print("          片段:", body[max(0, idx - 60):idx + 200].replace("\n", " ")[:260])
                    break
                if not loc:
                    print("          无 Location，停止")
                    break
                url = loc if loc.startswith("http") else httpx.URL(url).join(loc)
    except Exception as e:  # noqa: BLE001
        print("  异常:", type(e).__name__, e)


trace("无 cookie + 移动端 UA", "", UA_MOBILE)
trace("带登录 cookie + 移动端 UA", COOKIE, UA_MOBILE)
trace("带登录 cookie + 桌面 UA", COOKIE, UA_DESKTOP)
