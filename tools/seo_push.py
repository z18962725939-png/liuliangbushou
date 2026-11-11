#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把官网全部页面主动推送给百度，加快收录。

为什么需要它：
    新域名在百度眼里是"陌生人"。不主动报，它就只靠自己爬 ——
    站点地图挂在 robots.txt 里也可能十天半月没动静。
    主动推送是百度官方给的快速通道（普通收录 API），
    每次发版推一次，新页面基本当天就能进索引。

准备（只需做一次）：
    1. 打开 https://ziyuan.baidu.com/ 用百度账号登录
    2. 添加站点 https://xn--kpuzw91vr71b.cn
    3. 验证归属（推荐「HTML 标签验证」，把给的那行 meta 整行贴进
       tools/baidu_verify.txt，本脚本会自动加进全站 head）
    4. 左栏「资源提交 → 普通收录 → API 提交」，把那串 token 贴进
       tools/baidu_token.txt
    5. 跑一次：python tools/seo_push.py

之后每次发版跑一次即可。配额按站点算，用超了接口会明确返回剩余额度。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
STANDALONE = ROOT / "installer_output" / "standalone"
TOKEN_FILE = TOOLS / "baidu_token.txt"
VERIFY_FILE = TOOLS / "baidu_verify.txt"

SITE = "https://xn--kpuzw91vr71b.cn"
API = "http://data.zz.baidu.com/urls"

sys.path.insert(0, str(TOOLS))


def read_secret(path: Path) -> str:
    if not path.is_file():
        return ""
    txt = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    return lines[0] if lines else ""


def collect_urls() -> list[str]:
    """站点地图里的全部页面 —— 和推送给百度的是同一份清单。"""
    try:
        import site_seo as seo
        return [seo.abs_url(p["file"]) for p in seo.PAGES]
    except Exception:                                    # noqa: BLE001
        # 兜底：直接啃 sitemap.xml
        import re
        xml = (STANDALONE / "sitemap.xml").read_text(encoding="utf-8")
        return re.findall(r"<loc>(.*?)</loc>", xml)


def push(urls: list[str], token: str) -> tuple[bool, str, dict]:
    q = urllib.parse.urlencode({"site": SITE, "token": token})
    body = "\n".join(urls).encode("utf-8")
    req = urllib.request.Request(
        f"{API}?{q}", data=body,
        headers={"Content-Type": "text/plain", "User-Agent": "curl/8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return False, raw, json.loads(raw)
        except Exception:                                # noqa: BLE001
            return False, raw, {}
    except Exception as e:                               # noqa: BLE001
        return False, str(e), {}
    try:
        return True, raw, json.loads(raw)
    except Exception:                                    # noqa: BLE001
        return True, raw, {}


def main() -> int:
    token = read_secret(TOKEN_FILE)
    verify = read_secret(VERIFY_FILE)
    urls = collect_urls()

    print(f"站点：{SITE}")
    print(f"待推送页面：{len(urls)} 个")
    for u in urls:
        print(f"   {u}")
    print()

    if verify:
        print("已配置归属验证标签（会由 site_seo 加进全站 head）")
    else:
        print(f"[提示] 还没有归属验证标签。百度站长平台会给一行 meta，")
        print(f"       整行贴进 {VERIFY_FILE} 后重跑本脚本即可自动加到全站。")

    if not token:
        print()
        print(f"[未配置] 找不到 {TOKEN_FILE}")
        print("         把百度站长平台「普通收录 → API 提交」里的 token 贴进去，")
        print("         再跑一次 python tools/seo_push.py 就能推送。")
        print()
        print("         没有 token 也可以先手工提交：")
        print("         资源提交 → 普通收录 → 手动提交 → 填 sitemap 地址")
        print(f"         {SITE}/sitemap.xml")
        return 0

    ok, raw, data = push(urls, token)
    if data:
        if data.get("success") is not None:
            print(f"✔ 推送成功 {data.get('success')} 条"
                  f"，今日剩余配额 {data.get('remain')}")
        if data.get("not_same_site"):
            print(f"✘ 不是同一站点（域名要跟站长平台里填的完全一致）："
                  f"{data['not_same_site']}")
        if data.get("not_valid"):
            print(f"✘ 无效链接：{data['not_valid']}")
        if data.get("error"):
            print(f"✘ 百度返回错误：{data['error']}（token 或站点填错了）")
    else:
        print(("✔ " if ok else "✘ ") + raw[:400])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
