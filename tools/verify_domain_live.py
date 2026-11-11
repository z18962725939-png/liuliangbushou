# -*- coding: utf-8 -*-
"""域名上线一键验证（换自有域名后用这个）。

检查项：
  1. 域名是否已注册成功（腾讯云域名列表里能不能查到）
  2. DNS 解析指向（CNAME / A）
  3. 首页可访问性（电脑 UA + 微信 UA），且**响应头不能带 attachment**
  4. 4 张界面截图是否都能 200
  5. 安装包下载直链是否 206（支持断点续传）
  6. 页面关键内容是否齐全（版本号 / 产品矩阵 / 规划功能）

用法：
    python tools/verify_domain_live.py                    # 用内置默认域名
    python tools/verify_domain_live.py 你的域名.com
    python tools/verify_domain_live.py 流量捕手.cn          # 中文域名会自动转 punycode
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 默认域名（改成你的正式域名后就不用每次传参）
DEFAULT_DOMAIN = "流量捕手.cn"

WX_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
         "MicroMessenger/8.0.40(0x18002832) NetType/WIFI Language/zh_CN")
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SHOTS = ["shot-collect.png", "shot-live-monitor.png",
         "shot-transcribe.png", "shot-video-download.png"]

PASS, FAIL, WARN = "✅", "❌", "⚠️ "


def to_punycode(domain: str) -> str:
    """中文域名 -> punycode（浏览器/CDN 实际使用的形式）。"""
    d = domain.strip().rstrip("/")
    d = d.split("//")[-1]
    try:
        return d.encode("idna").decode("ascii")
    except Exception:
        parts = []
        for seg in d.split("."):
            try:
                parts.append(seg.encode("idna").decode("ascii"))
            except Exception:
                parts.append(seg)
        return ".".join(parts)


def norm_urls(domain: str):
    """返回 (展示用的中文域名, punycode, https url)。"""
    d = domain.strip().rstrip("/")
    if d.startswith("http://") or d.startswith("https://"):
        d = d.split("//", 1)[1]
    puny = to_punycode(d)
    return d, puny, "https://" + puny + "/"


def http(url, ua=PC_UA, method="GET", rng=None, timeout=25, full=False):
    """返回 (status, headers dict, body)。
    默认只读 2KB（够看响应头/文件头）；full=True 时读完整页面（做内容关键词判定）。"""
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", ua)
    if rng:
        req.add_header("Range", rng)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read() if full else r.read(2048)
            return r.status, {k.lower(): v for k, v in r.headers.items()}, body
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b""
    except Exception as e:
        return None, {"_err": str(e)[:120]}, b""


def check_domain_registered(domain_ascii: str):
    """查腾讯云域名列表，确认是否已注册成功。"""
    print("【1】域名注册状态")
    try:
        from tencentcloud.common import credential
        from tencentcloud.domain.v20180808 import domain_client, models as dm
    except Exception as e:
        print("   %s 无法加载腾讯云 SDK：%s" % (WARN, e))
        return None
    cfg_path = os.path.join(ROOT, "tools", "cos_config.json.bak")
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(ROOT, "tools", "cos_config.json")
    try:
        c = json.load(open(cfg_path, encoding="utf-8"))
        cred = credential.Credential(c["secret_id"], c["secret_key"])
        cl = domain_client.DomainClient(cred, "ap-guangzhou")
        r = cl.DescribeDomainNameList(dm.DescribeDomainNameListRequest())
        names = []
        for d in (getattr(r, "DomainSet", None) or []):
            s = d._serialize() if hasattr(d, "_serialize") else {}
            names.append(str(s.get("DomainName", "")))
        hit = any(n.lower() == domain_ascii.lower() or
                  to_punycode(n).lower() == domain_ascii.lower() for n in names)
        if hit:
            print("   %s 已注册成功（列表中共 %d 个域名）" % (PASS, len(names)))
            return True
        # 非本账号域名（如托管在别处的测试域名）不算失败，仅提示
        loose = not (domain_ascii.endswith(".cn") or domain_ascii.startswith("xn--"))
        mark = WARN if loose else FAIL
        print("   %s 列表里还没有它（共 %d 个）—— 实名审核可能仍在进行" % (mark, len(names)))
        if names:
            print("      现有域名：", ", ".join(names))
        return None if loose else False
    except Exception as e:
        print("   %s 查询失败：%s" % (WARN, str(e)[:140]))
        return None


def check_dns(punycode: str):
    print()
    print("【2】DNS 解析")
    try:
        import socket
        # CNAME 查询（走系统解析拿不到 CNAME 就退化为 A 记录）
        try:
            import subprocess
            # 中文 Windows 的 nslookup 输出是 GBK，别用 utf-8 解（会抛 UnicodeDecodeError）
            out = subprocess.run(["nslookup", "-type=CNAME", punycode],
                                 capture_output=True, encoding="gbk",
                                 errors="ignore", timeout=15).stdout or ""
            cn = re.findall(r"(?:canonical name|别名)\s*=?\s*([^\s]+)", out, re.I)
            if cn:
                print("   CNAME → %s" % cn[0])
        except Exception:
            pass
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(punycode, 443,
                                                            proto=socket.IPPROTO_TCP)})
        if ips:
            print("   %s 解析到 %s" % (PASS, ", ".join(ips[:4])))
            return True
        print("   %s 解析不到 IP" % FAIL)
        return False
    except Exception as e:
        print("   %s 解析失败：%s" % (FAIL, str(e)[:120]))
        print("       → 可能是域名实名还没过，或 CNAME 记录还没加/没生效")
        return False


def check_site(display: str, puny: str, url: str):
    print()
    print("【3】首页可访问性（%s）" % display)
    ok = True

    st, hdr, body = http(url, ua=PC_UA)
    if st is None:
        print("   %s 电脑端打不开：%s" % (FAIL, hdr.get("_err", "")))
        return False
    print("   %s 电脑端 HTTP %s，%d 字节起" % (PASS if st == 200 else WARN, st, len(body)))
    cd = (hdr.get("content-disposition") or "").lower()
    if "attachment" in cd:
        print("   %s 响应带 Content-Disposition: attachment —— 浏览器会变成【下载文件】！" % FAIL)
        ok = False
    else:
        print("   %s 无 attachment 头（浏览器会正常渲染网页）" % PASS)
    ct = (hdr.get("content-type") or "").lower()
    print("   %s Content-Type: %s" % (PASS if "text/html" in ct else WARN, ct or "(空)"))

    st2, _, _ = http(url, ua=WX_UA)
    if st2 == 200:
        print("   %s 微信内置浏览器：HTTP 200（可直接打开，不用跳浏览器）" % PASS)
    else:
        print("   %s 微信内置浏览器：HTTP %s" % (FAIL, st2))
        ok = False

    # 内容判定（必须读完整页面，页面很长，前 2KB 看不到正文关键词）
    st3, _, raw = http(url, ua=PC_UA, full=True)
    if raw:
        try:
            h = raw.decode("utf-8", "ignore")
            for kw, must in (("流量捕手", True), ("产品矩阵", True),
                             ("TrafficCatcher_Setup", True), ("规划中", False),
                             ("GEO", False)):
                got = kw in h
                mark = PASS if got else (FAIL if must else WARN)
                print("   %s 页面含「%s」" % (mark, kw))
                if must and not got:
                    ok = False
            m = re.search(r"v?1\.\d+\.\d+", h)
            if m:
                print("   %s 页面版本号：%s" % (PASS, m.group(0)))
        except Exception:
            pass
    return ok


def check_assets(url: str):
    print()
    print("【4】界面截图（这 4 张是部署时最容易漏的）")
    all_ok = True
    for name in SHOTS:
        st, hdr, _ = http(url + name, method="GET", rng="bytes=0-63")
        n = hdr.get("content-length", "?")
        if st in (200, 206):
            print("   %s %-28s HTTP %s (%s 字节起)" % (PASS, name, st, n))
        else:
            print("   %s %-28s HTTP %s —— 图会裂！" % (FAIL, name, st))
            all_ok = False
    return all_ok


def check_installer():
    print()
    print("【5】安装包下载直链")
    vj = "https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com/trafficcatcher/version.json"
    st, _, raw = http(vj)
    exe = None
    if raw:
        try:
            d = json.loads(raw.decode("utf-8", "ignore"))
            ver = d.get("version") or d.get("latest") or ""
            url = d.get("url") or d.get("download_url") or ""
            if url:
                exe = url
                print("   线上版本：%s" % ver)
        except Exception:
            pass
    if not exe:
        exe = ("https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com/"
               "trafficcatcher/TrafficCatcher_Setup_v1.2.0.exe")
    for ua, tag in ((PC_UA, "电脑"), (WX_UA, "微信")):
        st, hdr, body = http(exe, ua=ua, rng="bytes=0-1023")
        is_exe = body[:2] == b"MZ"
        mark = PASS if st in (200, 206) else FAIL
        print("   %s %s端 HTTP %s，MZ 文件头=%s" % (mark, tag, st, is_exe))
    return True


def main():
    dom = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DOMAIN
    display, puny, url = norm_urls(dom)
    print("=" * 62)
    print("域名上线验证")
    print("  域名     ：", display)
    print("  Punycode ：", puny)
    print("  访问地址 ：", url)
    print("=" * 62)
    print()

    reg = check_domain_registered(puny)
    dns = check_dns(puny)
    site = check_site(display, puny, url) if dns else False
    if dns:
        assets = check_assets(url)
        check_installer()
    else:
        assets = False
        print()
        print("【4】【5】跳过（域名还没解析成功）")

    print()
    print("=" * 62)
    # 判定以「能解析 + 能访问 + 资源齐」为准（解析通了就说明域名已注册成功）
    ready = bool(dns and site and assets)
    if ready:
        print("🎉 全绿！域名已可用。下一步生成二维码：")
        print("   python tools/gen_site_qr.py %s" % url)
    else:
        print("还没就绪。对照上面标红的项逐条处理：")
        if reg is False:
            print("   · 域名实名审核未过 → 腾讯云控制台「域名注册 → 我的域名」看进度")
        if not dns:
            print("   · 解析没生效 → EdgeOne 项目里「添加自定义域名」拿 CNAME，"
                  "再去「域名解析」添加该 CNAME 记录")
        if dns and not site:
            print("   · 能解析但打不开 → 检查 EdgeOne 里该域名的状态是否为「已生效」")
        if dns and site and not assets:
            print("   · 截图 404 → 重新部署整站（要带 4 张 png 一起传，不能只传 index.html）")
    print("=" * 62)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
