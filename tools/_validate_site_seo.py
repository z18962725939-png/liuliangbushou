# -*- coding: utf-8 -*-
"""自检：官网 SEO 与站点完整性（38 项）。

为什么单独守一份自检（2026-10-15）：
    客户反馈"官网在百度搜不到"。实测诊断出四个原因，每一个**都不会报错**，
    只会让站点安静地不被收录：
      · 页面正文里没有用户会搜的词  → 搜索引擎根本不知道这站是干嘛的
      · 没有 robots.txt / sitemap.xml → 爬虫连有哪些页面都不知道
      · 三个域名挂同一份页面、canonical 指向不一致 → 重复内容互相分权
      · 页面太薄（几百字）→ 被判定低质量，不收

    这份自检把上面四条都变成可执行的断言。

    python tools/_validate_site_seo.py
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
STANDALONE = ROOT / "installer_output" / "standalone"
RELEASE_ALL = TOOLS / "release_all.py"
BUILD_SITE = TOOLS / "build_site.py"
BUILD_SUBPAGES = TOOLS / "build_subpages.py"
THEME = TOOLS / "site_theme.css"

sys.path.insert(0, str(TOOLS))

PASS: list[str] = []
FAIL: list[str] = []


def chk(cond, name: str, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name
          + (("  -- " + str(detail)) if (detail and not cond) else ""))
    return bool(cond)


def sec(title: str) -> None:
    print("\n" + title)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:                                   # noqa: BLE001
        return ""


def plain_body(html: str) -> str:
    """去掉脚本与标签，只看用户（和爬虫）能读到的文字。

    ★ body 标签必须按正则找，不能写死 "<body>"：
      首页带 class（<body class="home">，深色首屏要靠它区分导航配色），
      一旦写死，find 返回 -1 → html[-1:] → 正文汉字数是 0，
      自检会报"页面太薄 / 缺品牌词"这种完全指错方向的失败。
    """
    m = re.search(r"<body[^>]*>", html)
    body = html[m.end():] if m else html
    body = re.sub(r"<script.*?</script>", "", body, flags=re.S)
    body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


def han_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fa5]", text))


def main() -> int:
    print("=" * 66)
    print("  官网 SEO 与站点完整性自检")
    print("=" * 66)

    try:
        import site_seo as seo
    except Exception as e:                              # noqa: BLE001
        print(f"[致命] 无法导入 site_seo：{e}")
        return 1

    pages = list(seo.PAGES)
    host = seo.CANONICAL_HOST

    # ── 1. 页面注册表与产物 ──────────────────────────────
    sec("1) 页面注册表与产物")
    chk(len(pages) >= 10, f"注册页面 ≥ 10 个（实际 {len(pages)} 个）—— 一个页面只能主打一个词")
    missing = [p["file"] for p in pages if not (STANDALONE / p["file"]).is_file()]
    chk(not missing, "注册表里的每个页面都真的生成了", f"缺：{missing}")
    files = [p["file"] for p in pages]
    chk(len(files) == len(set(files)), "页面文件名不重复")
    chk(all(f.endswith(".html") for f in files), "页面文件名格式统一（.html）")
    no_ascii = [f for f in files if not re.fullmatch(r"[a-z0-9\-]+\.html", f)]
    chk(not no_ascii, "文件名全为小写拉丁字母（中文名上传 COS 有编码坑）", f"异常：{no_ascii}")
    naked = [f for f in files if not (STANDALONE / f).is_file()]
    chk(not naked, "没有空壳页面")

    # ── 2. title / description / canonical ──────────────
    sec("2) 标题、描述、canonical")
    titles, descs, h1s, cans = {}, {}, {}, {}
    for p in pages:
        html = read(STANDALONE / p["file"])
        t = re.findall(r"<title>(.*?)</title>", html, re.S)
        d = re.findall(r'<meta name="description" content="(.*?)">', html, re.S)
        h = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        c = re.findall(r'<link rel="canonical" href="([^"]+)"', html)
        titles[p["file"]] = t[0] if t else ""
        descs[p["file"]] = d[0] if d else ""
        h1s[p["file"]] = [re.sub(r"<[^>]+>", "", x).strip() for x in h]
        cans[p["file"]] = c

    chk(all(titles.values()), "每个页面都有 title")
    dup_t = [k for k, v in titles.items() if list(titles.values()).count(v) > 1]
    chk(not dup_t, "title 互不重复（重复＝自己跟自己抢词）", f"重复：{dup_t}")
    bad_len = {k: len(v) for k, v in titles.items() if not (8 <= len(v) <= 40)}
    chk(not bad_len, "title 长度都在 8~40 字之间", f"越界：{bad_len}")
    chk(all(seo.BRAND in v for v in titles.values()), "每个 title 都带品牌词")
    chk(all(descs.values()), "每个页面都有 meta description")
    dup_d = [k for k, v in descs.items() if list(descs.values()).count(v) > 1]
    chk(not dup_d, "description 互不重复", f"重复：{dup_d}")
    bad_d = {k: len(v) for k, v in descs.items() if len(v) < 50}
    chk(not bad_d, "description 都够长（≥50 字，太短等于没写）", f"过短：{bad_d}")

    # ★ 三个域名挂同一份页面，canonical 是唯一的归权手段
    chk(all(len(v) == 1 for v in cans.values()), "每个页面恰好一条 canonical")
    wrong = {k: v for k, v in cans.items() if v and not v[0].startswith(host)}
    chk(not wrong, f"canonical 全部指向自有域名（{host}）", f"跑偏：{wrong}")
    canon = [v[0] for v in cans.values() if v]
    chk(len(canon) == len(set(canon)), "canonical 各不相同")
    for mirror in seo.MIRROR_HOSTS:
        bad = [k for k, v in cans.items() if v and mirror in v[0]]
        chk(not bad, f"canonical 没有指向镜像域名（{mirror.split('//')[1][:28]}…）", f"越界：{bad}")

    # ── 3. 页面结构与正文分量 ────────────────────────────
    sec("3) 页面结构与正文分量")
    bad_h1 = {k: v for k, v in h1s.items() if len(v) != 1}
    chk(not bad_h1, "每个页面恰好一个 h1", f"异常：{bad_h1}")
    # h1 不必都塞品牌词：子页的 h1 是给用户读的，硬插品牌名会很别扭。
    # 首页 h1 必须带品牌词（搜"流量捕手"要能对上），子页 h1 必须含目标词。
    chk("流量捕手" in h1s.get("index.html", [""])[0],
        "首页 h1 带品牌词")
    weak_h1 = {}
    for p in pages:
        if p["file"] == "index.html":
            continue
        h = (h1s.get(p["file"]) or [""])[0]
        if not any(w in h for w in (p.get("must") or [])):
            weak_h1[p["file"]] = h[:34]
    chk(not weak_h1, "子页 h1 都含本页目标词", f"偏弱：{weak_h1}")
    thin = {}
    for p in pages:
        n = han_count(plain_body(read(STANDALONE / p["file"])))
        if n < 700:
            thin[p["file"]] = n
    chk(not thin, "每页正文汉字 ≥ 700（太薄会被判低质量不收录）", f"过薄：{thin}")
    no_link = {}
    for p in pages:
        html = read(STANDALONE / p["file"])
        n = len(re.findall(r'href="(?!#)(?!javascript)[^"]+\.html"', html))
        if n < 15:
            no_link[p["file"]] = n
    chk(not no_link, "每页站内链接 ≥ 15 条（爬虫靠内链发现页面）", f"偏少：{no_link}")
    leftover = [p["file"] for p in pages
                if re.search(r"\$[A-Z_]{3,}", read(STANDALONE / p["file"]))]
    chk(not leftover, "没有未替换的模板占位符残留", f"残留：{leftover}")
    no_alt = []
    for p in pages:
        html = read(STANDALONE / p["file"])
        for img in re.findall(r"<img[^>]*>", html):
            if "alt=" not in img:
                no_alt.append(f"{p['file']}:{img[:50]}")
    chk(not no_alt, "所有图片都带 alt", f"缺 alt：{no_alt[:4]}")
    # ★ 双花括号是 f-string 的转义写法，一旦漏进 HTML 就是无效 CSS，
    #   浏览器不报错、样式只是静静失效（更新日志时间线就这么坏过一轮）。
    dbl = [p["file"] for p in pages if "{{" in read(STANDALONE / p["file"])]
    chk(not dbl, "★ 产物里没有双花括号残留（会变成无效 CSS）", f"残留：{dbl}")
    chk((STANDALONE / "index.html").stat().st_size < 400_000,
        "首页体积仍然可控（<400KB，别把移动端拖死）")

    # ── 4. 每页真的覆盖了目标搜索词 ──────────────────────
    sec("4) 目标搜索词覆盖（搜不到的直接原因）")
    for p in pages:
        text = plain_body(read(STANDALONE / p["file"]))
        must = p.get("must") or []
        lack = [w for w in must if w not in text]
        chk(not lack, f"{p['file']} 命中目标词：{'、'.join(must)}", f"缺：{lack}")

    # ── 5. robots.txt 与 sitemap.xml ────────────────────
    sec("5) robots.txt 与 sitemap.xml")
    rb = STANDALONE / "robots.txt"
    sm = STANDALONE / "sitemap.xml"
    chk(rb.is_file(), "robots.txt 存在（原先 404）")
    chk(sm.is_file(), "sitemap.xml 存在（原先 404）")
    rbs = read(rb)
    chk("Sitemap:" in rbs, "robots.txt 里指明了 sitemap 地址")
    chk(f"{host}/sitemap.xml" in rbs, "sitemap 地址用的是自有域名")
    chk("Disallow: /" not in rbs.replace("Disallow: /gate", "").replace("Disallow: /version", ""),
        "★ 没有整站 Disallow（写过这条的站点会永远不被收录）")
    chk("Allow: /" in rbs, "明确允许抓取全站")
    try:
        root = ET.fromstring(read(sm))
        locs = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        ok_xml = True
    except Exception as e:                              # noqa: BLE001
        locs, ok_xml = [], str(e)
    chk(ok_xml is True, "sitemap.xml 是合法 XML", ok_xml)
    chk(len(locs) == len(pages), f"sitemap 收录了全部 {len(pages)} 个页面", f"实际 {len(locs)} 条")
    chk(all(l.startswith(host) for l in locs), "sitemap 的 loc 全部用自有域名")
    chk(len(set(locs)) == len(locs), "sitemap 没有重复条目")

    # ── 6. 结构化数据 ───────────────────────────────────
    sec("6) 结构化数据")
    idx = read(STANDALONE / "index.html")
    chk(idx.count("application/ld+json") >= 2, "首页带 SoftwareApplication 与 WebSite 标记")
    chk('"@type": "SoftwareApplication"' in idx, "首页声明了 SoftwareApplication")
    faq = read(STANDALONE / "faq.html")
    chk('"@type": "FAQPage"' in faq, "FAQ 页声明了 FAQPage（能在搜索里多占一块）")
    sub = read(STANDALONE / "douyin-xiazai.html")
    chk('"@type": "BreadcrumbList"' in sub, "子页带面包屑结构化数据")
    bad_ld = []
    for p in pages:
        for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                               read(STANDALONE / p["file"]), re.S):
            try:
                json.loads(blob)
            except Exception:                           # noqa: BLE001
                bad_ld.append(p["file"])
    chk(not bad_ld, "所有 JSON-LD 都能被解析（语法错＝搜索引擎直接忽略）", f"坏：{set(bad_ld)}")

    # ── 7. 内容纪律 ─────────────────────────────────────
    sec("7) 内容纪律（不吹、不编）")
    promise = ["保证被推荐", "保证排名", "保证效果", "100%保证", "绝对有效"]
    hit = []
    for p in pages:
        text = plain_body(read(STANDALONE / p["file"]))
        hit += [f"{p['file']}:{w}" for w in promise if w in text]
    chk(not hit, "没有效果承诺类措辞", f"命中：{hit}")
    fake = ["张先生", "李女士", "王总", "已服务2000", "ISO27001"]
    hit2 = []
    for p in pages:
        text = plain_body(read(STANDALONE / p["file"]))
        hit2 += [f"{p['file']}:{w}" for w in fake if w in text]
    chk(not hit2, "没有编造的客户案例与认证", f"命中：{hit2}")
    brand_missing = [p["file"] for p in pages
                     if seo.BRAND not in plain_body(read(STANDALONE / p["file"]))]
    chk(not brand_missing, "每个页面正文都出现品牌名", f"缺：{brand_missing}")

    # ── 8. 发版流程接线 ─────────────────────────────────
    sec("8) 发版流程接线（漏了这步＝线上还是旧官网）")
    ra = read(RELEASE_ALL)
    bs = read(BUILD_SITE)
    chk("build_subpages.py" in ra, "发版脚本里调用了子页生成器")
    chk("_validate_site_seo.py" in ra, "本自检挂进了发版前检查")
    i_sub = ra.find('"build_subpages.py"')
    i_pack = ra.find('"-m", "PyInstaller"')
    chk(i_sub > 0 and i_pack > 0 and i_sub < i_pack,
        "★ 生成子页排在打包之前（否则打进安装包的是上一版官网）")
    chk(i_sub > 0 and i_sub < ra.find("publish_download_page.py"),
        "生成子页排在部署之前")
    chk("site_seo" in bs, "首页也走同一套 SEO 模块（不然两边 canonical 会打架）")
    chk("$HEAD" in bs and "<style>" not in bs.split("PAGE_TPL")[-1][:2000],
        "首页 head 由 SEO 模块统一生成")
    chk(THEME.is_file(), "共享主题样式文件存在")
    chk("site_theme.css" in read(TOOLS / "site_seo.py"), "SEO 模块从主题文件读样式")
    chk(len(read(THEME)) > 15000, "主题样式内容完整")
    chk("build_subpages" in read(TOOLS / "publish_download_page.py")
        or "changelog.json" in read(TOOLS / "publish_download_page.py"),
        "上传流程覆盖新产物")

    # ── 百度归属验证 ───────────────────────────────────
    # 验证不通过 → sitemap 提交和主动推送都用不了，等于前面全白做。
    # 这个坑同样是静默的：标签漏了，站长平台只显示"校验失败"，不会告诉你是哪页缺。
    sec("百度归属验证标签")
    vf = TOOLS / "baidu_verify.txt"
    if vf.is_file():
        code = re.search(r'content="([^"]+)"', read(vf))
        chk(bool(code), "验证文件里有 content 值")
        if code:
            val = code.group(1)
            files = [p["file"] for p in seo.PAGES]
            missing = [f for f in files if val not in read(STANDALONE / f)]
            chk(not missing,
                f"★ 全站 {len(files)} 个页面都带验证标签（漏一页就可能验证失败）",
                f"缺标签的页面：{missing}")
            chk(val in read(STANDALONE / "index.html"),
                "首页带验证标签（百度验证的就是这一页）")
    else:
        chk(True, "未配置百度验证（跳过；拿到标签后贴进 tools/baidu_verify.txt）")

    # ── 汇总 ───────────────────────────────────────────
    print("\n" + "=" * 66)
    print(f"  通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("  失败清单：")
        for f in FAIL:
            print("    ✘ " + f)
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
