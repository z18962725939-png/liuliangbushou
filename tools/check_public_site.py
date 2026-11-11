# -*- coding: utf-8 -*-
"""公网官网一键体检。

验证「任何人、任何网络、包括微信内置浏览器」能否完整打开官网并下载安装包。

检查项：
  0. 官网首页（cos-website 静态网站域名）—— 电脑 UA / 微信 UA / Content-Type
  1. 页面内引用的所有外链资源（界面截图等）
  2. 更新源 version.json（客户端「检查更新」读的就是它）
  3. 安装包直链（HTTP Range 下载 + MZ 头 + 体积与 version.json 一致）
  4. 页面版本号与 version.json 是否一致（防止「发了新版但线上还是旧的」）

用法：
    python tools/check_public_site.py                 # 按线上 version.json 自动体检
    python tools/check_public_site.py --expect 1.3.0  # 指定必须是某个版本（发版后校验用）
退出码 0 = 全绿，1 = 有红灯。
"""
import argparse
import json
import re
import sys
import urllib.request
import urllib.error

SITE = "https://liuliangbushou-1258850027.cos-website.ap-guangzhou.myqcloud.com/"
ORIGIN = "https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com"
PREFIX = "trafficcatcher"
FEED = f"{ORIGIN}/{PREFIX}/version.json"

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UA_WX = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40(0x18002832)")

_n_ok = 0
_n_bad = 0


def _urlopen(url, ua=UA_PC, timeout=25, range_bytes=None):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", ua)
    if range_bytes:
        req.add_header("Range", range_bytes)
    return urllib.request.urlopen(req, timeout=timeout)


def head(url, ua=UA_PC, timeout=25, range_bytes=None):
    """返回 (status, body, headers)。网络异常时 status=0。"""
    try:
        with _urlopen(url, ua, timeout, range_bytes) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, b"", dict(e.headers or {})
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode(), {}


def check(name, ok, detail=""):
    global _n_ok, _n_bad
    if ok:
        _n_ok += 1
    else:
        _n_bad += 1
    print(f"  {'[OK]' if ok else '[!!]'} {name}" + (f"  {detail}" if detail else ""))


def human(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n:,} 字节"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", help="要求线上必须是该版本，否则判失败（发版后校验用）")
    args = ap.parse_args()

    print("=" * 68)
    print(" 公网官网体检")
    print("=" * 68)
    print(f" 首页地址 {SITE}\n")

    # ── 0) 首页 ──
    print("[0] 官网首页")
    code_pc, html, h_pc = head(SITE, UA_PC)
    code_wx, _, _ = head(SITE, UA_WX)
    check("首页可打开（电脑 UA）", code_pc == 200, f"HTTP {code_pc}，{human(len(html))}")
    check("首页可打开（微信 UA）", code_wx == 200, f"HTTP {code_wx}")
    ct = h_pc.get("Content-Type", "")
    check("返回 text/html（不是被强制下载）", "text/html" in ct, ct or "无响应头")
    check("未带 attachment 响应头", "attachment" not in str(h_pc.get("Content-Disposition", "")),
          h_pc.get("Content-Disposition") or "无")
    if not html:
        print("\n首页取不到内容，后续检查跳过。")
        return 1
    text = html.decode("utf-8", "ignore")

    # ── 1) 页面资源 ──
    print("\n[1] 页面引用的外部资源")
    urls = sorted({u for u in re.findall(r'https?://[^"\'()\s]+?\.(?:png|jpg|jpeg|webp)', text)})
    if not urls:
        check("页面无外链资源（已全部内联）", True)
    for u in urls:
        c, body, _ = head(u)
        check(f"资源 {u.rsplit('/', 1)[-1][:38]}", c == 200 and len(body) > 0,
              f"HTTP {c}，{human(len(body))}")

    # ── 2) 更新源 ──
    print("\n[2] 更新源 version.json（客户端「检查更新」读取）")
    c, body, _ = head(FEED)
    check("可匿名读取", c == 200, f"HTTP {c}")
    ver = None
    if c == 200:
        try:
            ver = json.loads(body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            check("JSON 可解析", False, str(e))
    if ver:
        v = str(ver.get("version", ""))
        win = ver.get("win") or {}
        check("含 version 字段", bool(v), v)
        check("含安装包 url", bool(win.get("url")))
        check("含 size / sha256", bool(win.get("size")) and bool(win.get("sha256")),
              f"{human(win.get('size') or 0)} / {(win.get('sha256') or '')[:12]}…")
        if args.expect:
            check(f"线上版本 == 期望 {args.expect}", v == args.expect, f"实际 {v}")

    # ── 3) 安装包直链 ──
    print("\n[3] 安装包直链")
    win = (ver or {}).get("win") or {}
    exe = win.get("url") or ""
    if not exe:
        check("取到安装包地址", False, "version.json 里没有 win.url")
    else:
        print(f"      {exe}")
        c_pc, chunk, h = head(exe, UA_PC, range_bytes="bytes=0-1023")
        c_wx, _, _ = head(exe, UA_WX, range_bytes="bytes=0-1023")
        check("可下载（电脑 UA）", c_pc in (200, 206), f"HTTP {c_pc}")
        check("可下载（微信 UA）", c_wx in (200, 206), f"HTTP {c_wx}")
        check("内容正确（MZ 文件头）", chunk[:2] == b"MZ", repr(chunk[:2]))
        # 注意：Range 请求拿到的 Content-Length 只是分片长度，
        # 要测真实体积必须单独发一次不带 Range 的请求读 Content-Range 的总长。
        _, _, h_full = head(exe, UA_PC, range_bytes="bytes=0-0")
        rng = h_full.get("Content-Range", "")
        remote_size = int(rng.rsplit("/", 1)[-1]) if "/" in rng else 0
        want = int(win.get("size") or 0)
        if want:
            check("包体积与更新源一致", remote_size == want,
                  f"线上 {human(remote_size)} / 声明 {human(want)}")

    # ── 4) 版本一致性 ──
    print("\n[4] 页面版本与更新源是否一致")
    page = {v.lstrip("vV") for v in re.findall(r"[vV]?\d+\.\d+\.\d+", text) if "/" not in v}
    cur = str(ver.get("version", "")) if ver else ""
    if cur:
        check("官网写的是最新版本", cur in page,
              f"线上 {cur} / 页面出现 " + ", ".join(sorted(page)))
    else:
        check("拿到线上版本号", False)

    print("\n" + "=" * 68)
    if _n_bad:
        print(f" 失败 {_n_bad} 项 / 通过 {_n_ok} 项 —— 上面带 [!!] 的要处理")
        print("=" * 68)
        return 1
    print(f" 全部 {_n_ok} 项通过 —— 官网对公网完好，可直接发给客户")
    print("=" * 68)
    print("\n公网地址（发给客户就用这个）：")
    print("  " + SITE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
