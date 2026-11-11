# -*- coding: utf-8 -*-
"""
把官网（含下载与购买）部署到腾讯云 COS 静态网站，并用「微信 UA」实测能否打开。

页面由 `tools/build_site.py` 生成（单页官网：产品介绍 / 功能详解 / 价格 / 下载 / FAQ）。
历史上这里调的是 `build_download_page.py`（纯下载页），2026-10-10 起改为官网。

为什么要放 COS：
    原来的 WorkBuddy 托管域名对微信 User-Agent 返回 403 +「请在浏览器上打开」，
    客户在微信里点链接直接被拒。实测 COS（myqcloud.com）不拒绝微信 UA。

★ 上传目标必须是「桶根 index.html」——COS 静态网站的索引文档就是它，
  传成 trafficcatcher/index.html 前端访问不到（看起来像"没更新"）。

用法：
    python tools/publish_download_page.py            # 生成最新页 → 上传 → 自检
    python tools/publish_download_page.py --no-build # 跳过生成，只上传已有页面
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG_FILE = ROOT / "tools" / "cos_config.json"
STANDALONE = ROOT / "installer_output" / "standalone" / "index.html"
# 页面「缓存自愈」用的版本比对文件。必须和 index.html 一起更新：
# 只传页面不传它 → 页面版本跑到前面 → 自愈脚本判定"不是最新"→ 反复重载（死循环）。
VERSION_JSON = ROOT / "installer_output" / "standalone" / "version.json"
BUILD_SCRIPT = ROOT / "tools" / "build_site.py"

WX_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.40(0x18002832) NetType/WIFI")

# 品牌入口（自有域名，走 EdgeOne，本脚本不负责上传，但必须体检）：
# 它一度是最差的一个 —— 节点在境外，跨境传输常卡死，百度也因此抓不到。
BRAND_SITE = "https://xn--kpuzw91vr71b.cn/"


def build_page() -> None:
    print("[1/4] 重新生成官网（版本号从 version.json 读）…")
    # --no-upload：页面由本脚本统一上传（build_site 仍会同步界面截图到 COS）
    r = subprocess.run([sys.executable, str(BUILD_SCRIPT), "--no-upload"],
                       cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(r.stdout or "", r.stderr or "")
        raise SystemExit("生成官网失败")
    print("      完成")


def make_client(cfg: dict):
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore
    return CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                 SecretKey=cfg["secret_key"], Scheme="https"))


def ensure_website(client, bucket: str) -> None:
    """开启静态网站托管。失败不阻断——页面直链仍可能可用，后面会实测。"""
    print("[2/4] 开启 COS 静态网站托管…")
    try:
        client.put_bucket_website(
            Bucket=bucket,
            WebsiteConfiguration={
                "IndexDocument": {"Suffix": "index.html"},
                "ErrorDocument": {"Key": "index.html"},
            },
        )
        print("      已开启（索引文档 index.html）")
    except Exception as e:  # noqa: BLE001
        print(f"      [警告] 开启失败：{e}")
        print("      继续上传，稍后用直链实测。")


def upload(client, bucket: str) -> None:
    print("[3/4] 上传页面 → index.html")
    if not STANDALONE.exists():
        raise SystemExit(f"找不到 {STANDALONE}，先跑一次 build_download_page.py --standalone")
    html = STANDALONE.read_text(encoding="utf-8")
    client.put_object(
        Bucket=bucket,
        Body=html.encode("utf-8"),
        Key="index.html",
        ContentType="text/html; charset=utf-8",
        # 关键：绝不能带 Content-Disposition: attachment，否则浏览器会下载而不是打开
        Metadata={"Cache-Control": "no-store, max-age=0"},
    )
    print(f"      已上传 {len(html):,} 字节")

    # ★ 必须一起传 version.json（桶根）：官网首页用相对路径 fetch 它做版本比对，
    #   取不到浏览器缓存里的旧页面就无法自愈；传晚了则会把最新页面判成旧版。
    if not VERSION_JSON.exists():
        raise SystemExit(f"找不到 {VERSION_JSON}，先跑一次 tools/build_site.py 生成")
    vj = VERSION_JSON.read_text(encoding="utf-8")
    client.put_object(
        Bucket=bucket,
        Body=vj.encode("utf-8"),
        Key="version.json",
        ContentType="application/json; charset=utf-8",
        Metadata={"Cache-Control": "no-store, max-age=0"},
    )
    print(f"      已上传 version.json（{len(vj):,} 字节，页面缓存自愈用）")

    # 更新日志数据：页面已经把内容烘进 HTML，桶里再放一份是为了
    # 日后能直接核对「线上这一版到底写了什么」，也方便别处引用。
    clog = STANDALONE / "changelog.json"
    try:
        if not clog.exists():
            subprocess.run([sys.executable, str(ROOT / "tools" / "build_changelog.py")],
                           cwd=str(ROOT), capture_output=True, text=True)
        if clog.exists():
            cj = clog.read_text(encoding="utf-8")
            client.put_object(
                Bucket=bucket,
                Body=cj.encode("utf-8"),
                Key="changelog.json",
                ContentType="application/json; charset=utf-8",
                Metadata={"Cache-Control": "no-store, max-age=0"},
            )
            print(f"      已上传 changelog.json（{len(cj):,} 字节，更新日志数据）")
    except Exception as e:                          # noqa: BLE001
        print(f"      [警告] changelog.json 上传失败（不阻断发版）：{e}")

    # 云端保险丝 gate.json：强制登录的总开关。客户端每次启动拉一次，
    # 作者把它改成 {"require_login": false} 就能全量关掉强制登录，无需发版。
    # 本地没有就按默认（开启）生成一份，绝不能因为缺文件中断发版。
    gate = STANDALONE.parent / "gate.json"
    try:
        if not gate.exists():
            gate.write_text('{\n  "require_login": true\n}\n', encoding="utf-8")
        gt = gate.read_text(encoding="utf-8")
        client.put_object(
            Bucket=bucket,
            Body=gt.encode("utf-8"),
            Key="gate.json",
            ContentType="application/json; charset=utf-8",
            Metadata={"Cache-Control": "no-store, max-age=0"},
        )
        print(f"      已上传 gate.json（强制登录云端开关）")
    except Exception as e:                       # noqa: BLE001
        print(f"      [警告] gate.json 上传失败（不阻断发版）：{e}")

    # ★ 2026-10-15 补：栏目子页 / robots.txt / sitemap.xml / 界面截图。
    #   原先这里只硬编码上传 index.html 一个页面，子页只在本地产出，
    #   线上永远是单页 —— 百度自然搜不到「抖音视频批量下载」这类栏目词。
    #   改成把 standalone 目录整体同步，新增页面不用再来改这段。
    _CTYPE = {
        ".html": "text/html; charset=utf-8",
        ".xml": "application/xml; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        # ★ 自带字体（见 tools/build_fonts.py）。漏了这一行，字体会被下面的
        #   `ext not in _CTYPE: continue` 静默跳过 —— 线上页面照样打得开，
        #   只是整站退回系统黑体，而且不会有任何报错。
        ".woff2": "font/woff2",
        ".woff": "font/woff",
    }
    _SKIP = {"changelog.json", "gate.json"}       # version.json 上面已传
    _NO_STORE = {".html", ".xml", ".txt", ".json"}
    extra = 0
    # 注意：本脚本里的 STANDALONE 指的是 index.html 这个「文件」，
    # 站点目录要用它的 parent（别写成 STANDALONE.iterdir()，会报目录名无效）。
    # ★ 必须递归：字体在 fonts/ 子目录里，只扫一层会把它们整批漏掉。
    site_dir = STANDALONE.parent
    for f in sorted(site_dir.rglob("*")):
        if not f.is_file() or f.name in _SKIP:
            continue
        rel = f.relative_to(site_dir)
        if any(part.startswith("_") for part in rel.parts):
            continue
        ext = f.suffix.lower()
        if ext not in _CTYPE:
            continue
        key = rel.as_posix()
        if key == "index.html":                   # 首页上面已经传过
            continue
        try:
            data = f.read_bytes()
            meta = ({"Cache-Control": "no-store, max-age=0"} if ext in _NO_STORE
                    else {"Cache-Control": "public, max-age=604800"})
            client.put_object(Bucket=bucket, Body=data, Key=key,
                              ContentType=_CTYPE[ext], Metadata=meta)
            extra += 1
        except Exception as e:                    # noqa: BLE001
            print(f"      [警告] {key} 上传失败（不阻断发版）：{e}")
    print(f"      已同步 {extra} 个站点文件（栏目子页 / robots.txt / sitemap.xml / 截图 / 字体）")


def probe(url: str, ua: str, label: str, timeout: int = 25) -> bool:
    """真实探测：**必须完整下载完**才算通过。

    ★★ 为什么不能只看状态码（2026-10-15 的血教训）：
        排查「官网百度搜不到」时发现，自有域名（EdgeOne 海外节点）在
        跨境链路上有约一半请求传到 40~60KB 就卡死，但**状态码一律 200**。
        旧版这里只 read(400) 就判「网页正常」，于是「客户在微信里点官网
        打不开、221KB 要 46 秒」这个真问题被瞒了整整一年。
        现在改成：读到底 + 校验 HTML 收尾 + 统计实际耗时。

    ★ 必须绕开本机代理：
        开发机常挂着代理（环境变量 http_proxy 指向 127.0.0.1:xxxx），
        走代理测出来的速度和成功率**不代表客户的真实体验**——实测两者
        结论能完全相反。用空 ProxyHandler 强制直连。
    """
    import gzip
    import time
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept-Encoding": "gzip",       # 客户浏览器就是这么请求的
    })
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as r:
            status = r.status
            ct = r.headers.get("Content-Type", "")
            enc = (r.headers.get("Content-Encoding") or "").lower()
            raw = r.read()               # ★ 读到底，绝不截断
            if enc == "gzip":
                raw = gzip.decompress(raw)
    except Exception as e:               # noqa: BLE001
        cost = time.time() - t0
        print(f"      [{label}] ✘ 传输中断（{cost:.1f}s 后放弃）：{type(e).__name__}")
        print(f"        服务器已回 200，但响应体没传完 —— 客户看到的是白屏")
        return False

    cost = time.time() - t0
    body = raw.decode("utf-8", "ignore")
    ok = ("text/html" in ct) and body.rstrip().lower().endswith("</html>") and len(raw) > 10240
    print(f"      [{label}] HTTP {status} | 完整 {len(raw):,} 字节 | 耗时 {cost:.2f}s"
          f" | {'网页正常' if ok else '★ 内容不完整'}")
    if not ok:
        print(f"        HTML 没有正常收尾，尾部内容：{body[-160:]!r}")
    elif cost > 8:
        print(f"        ★ 太慢了：{cost:.1f}s，大陆访客多半等不到页面就关掉了")
    return ok


def probe_asset(url: str, label: str, min_bytes: int = 20000,
                timeout: int = 25) -> bool:
    """探测二进制资源（自带字体）。

    ★ 为什么不复用 probe()：那个函数会校验响应体以 </html> 收尾，字体是二进制，
      用它必然判失败。但「读到底 + 看真实字节数」这条不能省 —— 弱网下服务器
      先回 200、传到一半断掉，页面照样能打开，只是字体悄悄退回系统黑体。
    """
    import time
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as r:
            status = r.status
            ct = r.headers.get("Content-Type", "")
            raw = r.read()
    except Exception as e:                       # noqa: BLE001
        print(f"      [{label}] ✘ 取不到（{time.time()-t0:.1f}s）：{type(e).__name__}")
        return False
    cost = time.time() - t0
    ok = status == 200 and len(raw) >= min_bytes
    print(f"      [{label}] HTTP {status} | {len(raw):,} 字节 | {cost:.2f}s"
          f" | {ct} | {'正常' if ok else '★ 不完整'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true", help="跳过生成，只上传已有页面")
    args = ap.parse_args()

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    bucket, region = cfg["bucket"], cfg["region"]

    if not args.no_build:
        build_page()

    client = make_client(cfg)
    ensure_website(client, bucket)
    upload(client, bucket)

    site = f"https://{bucket}.cos-website.{region}.myqcloud.com/"
    direct = f"https://{bucket}.cos.{region}.myqcloud.com/index.html"

    print("[4/4] 实测（重点看微信 UA 是否被拒）")
    ok_site = probe(site, WX_UA, "静态网站·微信UA")
    ok_site_web = probe(site, "Mozilla/5.0", "静态网站·普通UA")
    ok_direct = probe(direct, WX_UA, "对象直链·微信UA")

    # ── 自带字体体检 ──
    #   字体会被 upload() 的扩展名白名单静默跳过（.woff2 不在表里，
    #   或者文件在 fonts/ 子目录而循环只扫了一层）。线上表现是「页面正常，
    #   但整站变成系统黑体」——不报错、不白屏，只有人眼看设计稿才发现。
    print()
    print("[4/4a] 自带字体是否真的传上去了")
    ok_font = all(
        probe_asset(f"https://{bucket}.cos.{region}.myqcloud.com/fonts/{n}",
                    f"COS·{n}")
        for n in ("serif-sc-700.woff2", "sans-sc-400.woff2")
    )
    if not ok_font:
        print("      ⚠️  字体没同步好 —— 线上会退回系统黑体（页面本身不报错）。")
        print("         查 publish_download_page.py 的 _CTYPE 白名单与 rglob 遍历。")

    # ── 自有域名单独体检 ──
    # 它不在本脚本的上传范围内（走 EdgeOne），但客户名片的地址栏写着它，
    # 百度要收录的也是它 —— 必须每次发版都看一眼，别等客户反馈。
    print()
    print("[4/4b] 品牌入口体检（自有域名 · EdgeOne 节点）")
    ok_brand = probe(BRAND_SITE, WX_UA, "自有域名·微信UA")
    if not ok_brand:
        print("      ⚠️  自有域名现在打不开或极慢 —— 先对外用下面的 COS 链接，")
        print("        并把加速区域切到国内（需先完成 ICP 备案，见 MEMORY.md）。")

    print()
    # 两种 COS 形态在微信里都打不开时，必须中断发版 —— 宁可不发，别让客户点空。
    if not (ok_site or ok_direct):
        print("❌ 两种方式在微信 UA 下都打不开，需要换托管（见 README 说明）")
        return 1
    # ★ COS 域名【不能当官网入口】：响应头恒带 Content-Disposition: attachment
    #   （桶级 x-cos-force-download，给对象改 inline 也压不住），浏览器打开是下载
    #   index.html 而不是渲染网页（2026-10-16 实测）。它只适合做安装包直链。
    #   官网入口用自有域名（微信 UA 实测 200），尽管境外节点偶尔抽风。
    print("✅ 官网入口（能正常渲染，微信里也能打开）：")
    print(f"   {BRAND_SITE}")
    print("   ↑ 这是唯一能当网页发的地址；COS 域名只能用来贴安装包直链。")
    if ok_site:
        print(f"   COS 已同步正常（{site}）—— 只做下载源，别拿它当官网链接发")
    if not ok_site_web:
        print("   [注意] 普通浏览器访问静态网站域名异常，请检查桶权限是否为公有读")

    # ── 关键提醒：旧域名是另一套托管，本脚本管不到它 ──
    # 2026-10-10 踩坑：发版只同步了 COS，WorkBuddy 旧域名仍挂着旧页，
    # 客户点旧链接看到的是上一版（v1.1.0），误以为"更新没生效"。
    print()
    print("⚠️  旧域名不会跟着自动更新（老客户手里大多是它）：")
    print("    https://trafficcatcher-download.app.workbuddy.host/")
    print("    发版后必须再用「发布为应用」重新部署一次 installer_output/standalone，")
    print("    否则点旧链接看到的仍是上一版。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
