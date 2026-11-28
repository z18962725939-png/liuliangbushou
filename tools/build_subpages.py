#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""官网栏目子页生成：一个功能一个页面，各自覆盖一组搜索词。

为什么要有子页（2026-10-15）：
    之前官网只有 index.html 一个 URL。一个页面只能主打一个词，
    结果「抖音视频批量下载」「抖音直播录制」「小红书数据采集」这些真实搜索词
    一个都覆盖不到 —— 页面里根本没出现这些说法，百度自然搜不到。
    拆成多个栏目页之后，每个页面有自己的 title / H1 / 正文，
    彼此用导航和页脚互相链接，爬虫顺着内链就能把全部页面抓走。

★ 内容纪律（别写砸了）：
    - 只写软件真的能做到的事。没做的写「规划中」，不吹。
    - 不承诺任何「保证效果」「保证被推荐」之类的话。
    - 不编造客户数据、不编造案例公司名。
    - 页面之间不要复制粘贴同一段话，搜索引擎会判低质量。

用法：
    python tools/build_subpages.py            # 生成全部子页 + robots.txt + sitemap.xml
    python tools/build_subpages.py --version 1.13.0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
INSTALLER_OUT = ROOT / "installer_output"
STANDALONE = INSTALLER_OUT / "standalone"

sys.path.insert(0, str(TOOLS))
import site_seo as seo                                   # noqa: E402
# 商店直链只在 build_site.py 里定义一份（防止两边写歪）。
# 它模块级只有常量与函数定义，没有副作用，import 安全。
import build_site as _site_build                         # noqa: E402

EXT_STORE_URL = _site_build.EXT_STORE_URL
EXT_ZIP_URL = _site_build.EXT_ZIP_URL

BRAND = seo.BRAND
WECHAT_ID = "zsx807108536"
# 与 build_site.py 共用同一份作者个人微信加好友码（相对路径，不内联）。
PERSONAL_QR_ASSET = "assets/wechat-personal-qr.jpg"

# ─────────────────────────────────────────────────────────────────────
#  共用骨架：导航 / 页脚 / 内链
# ─────────────────────────────────────────────────────────────────────

NAV_LINKS = [
    # 免费版放最前：这是引流入口，新访客第一眼就该看到「有不要钱的版本」
    ("free.html", "免费版"),
    # 插件是 2026-10-24 上新的，指向首页锚点（子页没有自己的插件页）。
    # 排在免费版后面第二位：装个扩展比装软件更轻，先把这类访客接住。
    ("index.html#extension", "浏览器插件"),
    ("douyin-xiazai.html", "视频下载"),
    ("zhibo-luzhi.html", "直播录制"),
    ("shuju-caiji.html", "数据采集"),
    ("pinglun-jieliu.html", "评论截流"),
    ("wenan-fangxie.html", "文案仿写"),
    ("price.html", "价格"),
]


def _nav(cur_file: str) -> str:
    """顶部导航。当前页高亮（加个 aria-current，搜索引擎也看得懂）。"""
    items = []
    for f, name in NAV_LINKS:
        cur = ' aria-current="page"' if f == cur_file else ""
        # 「上新」标记只给插件这一项挂，别写成通用开关 ——
        # 全站到处 NEW 就等于没有 NEW。
        tag = '<i class="newtag">NEW</i>' if "extension" in f else ""
        items.append(f'      <a href="{f}"{cur}>{name}{tag}</a>')
    return (
        '<header class="nav" id="nav">\n'
        '  <div class="nav-in">\n'
        f'    <a class="logo" href="index.html"><i>捕</i>{BRAND}</a>\n'
        '    <nav class="nav-links">\n' + "\n".join(items) + "\n    </nav>\n"
        '    <div class="nav-right">\n'
        '      <a class="quiet" href="changelog.html">更新日志</a>\n'
        # 导航上的主按钮跟首页保持一致：直接用「下载免费版」，
        # 而不是含糊的「免费试用」（客户点进去期待的是下载，不是弹二维码）
        '      <a class="btn btn-key" href="index.html#download">下载免费版</a>\n'
        '      <button class="nav-burger" id="burger" onclick="toggleMenu()" aria-label="打开菜单">'
        '<i></i><i></i><i></i></button>\n'
        '    </div>\n'
        '  </div>\n'
        '</header>\n'
        '<nav class="mnav" id="mnav">\n'
        '  <a href="index.html">首页</a>\n'
        + "".join(
            f'  <a href="{f}">{n}'
            + ('<i class="newtag">NEW</i>' if "extension" in f else "")
            + "</a>\n"
            for f, n in NAV_LINKS
        )
        + '  <a href="free.html">免费版（无需激活码）</a>\n'
        '  <a href="geo.html">GEO 优化</a>\n'
        '  <a href="changelog.html">更新日志</a>\n'
        '  <a href="faq.html">常见问题</a>\n'
        '  <a href="index.html#download">下载 Windows 版</a>\n'
        '</nav>'
    )


def _footer(version: str) -> str:
    return (
        "<footer>\n"
        '  <div class="wrap">\n'
        '    <div class="f-in">\n'
        '      <div class="f-brand">\n'
        f'        <a class="logo" href="index.html"><i>捕</i>{BRAND}</a>\n'
        f'        <p>抖音 / 小红书 / 快手 / 视频号内容采集、直播录制与本地转写工具。'
        '数据在你自己的电脑上处理，不上传。</p>\n'
        "      </div>\n"
        '      <div class="f-col">\n'
        "        <h5>功能</h5>\n"
        '        <a href="free.html">免费版（无需激活码）</a>\n'
        '        <a href="douyin-xiazai.html">抖音视频批量下载</a>\n'
        '        <a href="zhibo-luzhi.html">抖音 / TikTok 直播录制</a>\n'
        '        <a href="shuju-caiji.html">数据采集与选题</a>\n'
        '        <a href="pinglun-jieliu.html">评论区截流</a>\n'
        "      </div>\n"
        '      <div class="f-col">\n'
        "        <h5>创作</h5>\n"
        '        <a href="wenan-fangxie.html">短视频文案仿写</a>\n'
        '        <a href="geo.html">GEO 优化</a>\n'
        '        <a href="price.html">价格与授权</a>\n'
        '        <a href="index.html#products">产品路线图</a>\n'
        "      </div>\n"
        '      <div class="f-col">\n'
        "        <h5>支持</h5>\n"
        '        <a href="index.html#download">下载安装</a>\n'
        '        <a href="faq.html">常见问题</a>\n'
        '        <a href="changelog.html">更新日志</a>\n'
        '        <span>扫码加作者个人微信 ↓</span>\n'
        f'        <img class="f-qr" src="{PERSONAL_QR_ASSET}" '
        'alt="作者个人微信二维码" loading="lazy">\n'
        f"        <span>微信号：{WECHAT_ID}（二维码失效时搜索）</span>\n"
        "      </div>\n"
        "    </div>\n"
        '    <div class="f-bot">\n'
        f"      <span>© 2026 {BRAND}工作室 · v{version}</span>\n"
        "      <span>工具仅用于合法的内容研究与素材整理，请遵守平台规则</span>\n"
        "    </div>\n"
        "  </div>\n"
        "</footer>"
    )


def _siblings(cur_file: str) -> str:
    """底部内链卡片：让爬虫和用户都能顺手走到相邻栏目。"""
    out = []
    for p in seo.PAGES:
        if p["file"] == cur_file or p["file"] == seo.INDEX_FILE:
            continue
        out.append(f'    <a href="{p["file"]}">{p["nav"]}<em>{p["desc"][:38]}…</em></a>')
    # 首页永远放第一个，保证有回路
    idx = seo.page_by_file(seo.INDEX_FILE)
    out.insert(0, f'    <a href="index.html">回首页<em>{idx["desc"][:38]}…</em></a>')
    return (
        '  <div class="wrap">\n'
        '    <div class="sib">\n' + "\n".join(out) + "\n    </div>\n  </div>"
    )


def _cta_download(version: str, size_mb: str, free_url: str = "",
                  free_ver: str = "", free_size: str = "") -> str:
    """免费版页的下载卡（2026-10-23）。

    以前这里只有一个「去下载页面」按钮，落地页的访客要多跳一次、还要在
    完整版的下载区里自己判断该下哪个。既然免费版已经独立打包，直接给直链。
    free_url 为空（还没打过免费版包）时退回原来的引导按钮。
    """
    if free_url:
        size_txt = f"，安装包 {free_size}" if free_size and free_size != "—" else ""
        return (
            '<div class="pcard2">\n'
            f"  <b>下载 流量捕手免费版 v{free_ver or version}</b>"
            f"（Windows 10 / 11{size_txt}）\n"
            "  <p>这个安装包只有两个功能：抖音视频下载、直播录制。"
            "装完打开就是能用的状态 —— 不用激活码、不用注册账号、没有次数限制。"
            "采集、仿写、截流这类要调接口的功能不在里面，"
            "想要那些请下完整版（两个版本可以同时装，互不覆盖）。</p>\n"
            '  <p style="margin-top:16px">'
            f'<a class="btn btn-key" href="{free_url}">下载免费版</a>'
            '<a class="btn" style="margin-left:8px" href="index.html#download">'
            "下载完整版</a></p>\n"
            "</div>"
        )
    return (
        '<div class="pcard2">\n'
        f"  <b>下载 {BRAND} v{version}</b>（Windows 10 / 11，安装包 {size_mb}）\n"
        "  <p>装上就能用：视频下载与直播录制永久免费、不限次数，不需要激活码。"
        "采集、仿写、截流这类要调接口的功能，用授权码解锁。"
        "下载与激活有疑问，扫码加作者个人微信，一步步带你走完"
        f"（微信号 {WECHAT_ID}）。</p>\n"
        '  <p style="margin-top:16px">'
        '<a class="btn btn-key" href="index.html#download">去下载页面</a></p>\n'
        "</div>"
    )


def render_page(*, page_file: str, body: str, version: str,
                json_ld: list[str] | None = None, css: str = "") -> str:
    """子页通用骨架：面包屑 + 内容 + 内链 + 页脚。"""
    meta = seo.page_by_file(page_file)
    crumbs = [("首页", seo.INDEX_FILE), (meta["nav"], "")]
    ld = list(json_ld or [])
    ld.append(seo.ld_breadcrumb(crumbs))
    head = seo.build_head(
        title=meta["title"], desc=meta["desc"], page_file=page_file,
        json_ld=ld, css=css,
    )
    crumb_html = (
        '  <div class="wrap crumb">'
        '<a href="index.html">首页</a><span>/</span>' + meta["nav"] + "</div>"
    )
    # 互动层放最后：它要拿到上面所有 DOM 才能给元素挂动画
    return seo.inject_motion(
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n" + head + "\n</head>\n"
        "<body>\n"
        + _nav(page_file) + "\n"
        + crumb_html + "\n"
        + body + "\n"
        + _siblings(page_file) + "\n"
        + _footer(version) + "\n"
        + _SCRIPT + "\n"
        "__MOTION__\n"
        "</body>\n</html>\n"
    )


_SCRIPT = """<script>
(function(){
  var nav=document.getElementById('nav');
  function onScroll(){ nav.classList.toggle('scrolled', window.scrollY>6); }
  window.addEventListener('scroll', onScroll, {passive:true});
  onScroll();
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){ if(e.isIntersecting){ e.target.classList.add('in'); io.unobserve(e.target); } });
  },{threshold:.12,rootMargin:'0px 0px -40px 0px'});
  document.querySelectorAll('.rv').forEach(function(el,i){
    el.style.transitionDelay=(Math.min(i,6)*40)+'ms';
    io.observe(el);
  });
})();
function toggleMenu(){
  document.getElementById('mnav').classList.toggle('on');
  document.getElementById('burger').classList.toggle('on');
}
window.addEventListener('resize',function(){ if(window.innerWidth>720){
  document.getElementById('mnav').classList.remove('on');
  document.getElementById('burger').classList.remove('on');
}});
</script>"""


# ─────────────────────────────────────────────────────────────────────
#  小组件
# ─────────────────────────────────────────────────────────────────────

def steps(items: list[tuple[str, str]]) -> str:
    """三步流程条。"""
    li = "".join(
        f'      <div class="feat"><div class="ic">{i}</div><b>{t}</b><p>{d}</p></div>'
        for i, (t, d) in enumerate(items, start=1)
    )
    return f'    <div class="fgrid rv">\n{li}\n    </div>'


def cmp_table(rows: list[tuple[str, str, str]]) -> str:
    out = [
        '    <div class="vs rv">',
        '      <div class="vs-row hd"><div class="c1">场景</div>'
        '<div class="c2">通常怎么做</div><div class="c3">用流量捕手</div></div>',
    ]
    for a, b, c in rows:
        out.append(
            f'      <div class="vs-row"><div class="c1">{a}</div>'
            f'<div class="c2">{b}</div><div class="c3">{c}</div></div>'
        )
    out.append("    </div>")
    return "\n".join(out)


def list_block(title: str, items: list[str]) -> str:
    li = "".join(f"<li>{x}</li>" for x in items)
    return f"    <h3>{title}</h3>\n    <ul>{li}</ul>"


def para(text: str) -> str:
    return f"    <p>{text}</p>"


def section(title: str, inner: str, kick: str = "") -> str:
    k = f'      <span class="kick">{kick}</span>\n' if kick else ""
    return (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="sec-head rv">{k}      <h2>{title}</h2>\n    </div>\n'
        f"{inner}\n  </div>\n</section>"
    )


# ─────────────────────────────────────────────────────────────────────
#  各栏目正文
# ─────────────────────────────────────────────────────────────────────

def body_free(version: str, size_mb: str, free_url: str = "",
              free_ver: str = "", free_size: str = "") -> str:
    """免费版落地页：引流入口。

    写法上刻意先讲「包含什么」，再讲「不包含什么」，最后解释为什么敢免费。
    把边界摊开说，比藏着掖着更能换来信任 —— 用户最怕的是装完发现被骗。

    ★ 2026-10-23：免费版从「同一个安装包 + 免费档」变成**独立发行版**
      （界面上只有视频下载、直播监控、设置三页）。所以两个细节跟着改：
      ① 下载按钮给免费版直链（不再让人跳到完整版下载区自己挑）；
      ② 「不包含什么」那段不能再说「点进去会提示」—— 免费版里根本没有那些
         页面，说「点进去」等于描述一个不存在的界面。
    """
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("free.html")["h1"]}</h1>\n'
        '      <p class="lead">流量捕手有两件事可以一直免费用：抖音视频下载、'
        '抖音 / TikTok 直播间自动录制。<b>无需激活码</b>，不限制用多久，也不限次数，装完就能开始。'
        '原因也很直白 —— 这两条链路走的都是不花钱的通道，没必要拿它收费。</p>\n'
        "    </div>\n"
        + steps([
            ("下载安装", "装完打开就是能用的状态，不用找激活码、不用注册账号。"),
            ("登录一次抖音", "扫码登录后免费通道的命中率明显更高；未登录时抖音经常不给页面数据。"),
            ("直接用", "粘贴链接就下载视频；或把直播间加进列表，开播自动录。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("免费版包含这两项，永久可用", "\n".join([
        list_block("", [
            "<b>抖音视频下载</b>：粘贴链接拿原画质无水印视频，图集也能一次打包，"
            "可以多条链接批量下。",
            "<b>直播录制</b>：把抖音或 TikTok 直播间链接加进监控列表（TikTok 也可以直接填 @主播号），"
            "在播就自动开录、下播自动停，录完存成本地文件，不用人守着。",
            "<b>无需激活码</b>：不用申请授权、不用绑定机器码，换电脑重装也一样能用。",
            "<b>不限制使用时长</b>：不是试用，到期不会锁死，这两项一直都在。",
            "<b>不限制次数</b>：一天下 3 条还是 300 条都行，没有每日上限。",
            "<b>不消耗接口额度</b>：完全不走付费接口，接口那边欠费、余额不足都不影响你。",
            "<b>数据在本机</b>：解析和录制都在你自己电脑上完成，下载的文件不上传。",
        ]),
        _cta_download(version, size_mb, free_url, free_ver, free_size),
    ]), kick="包含")
    # ★ 2026-10-24：插件上新，在免费版落地页插一块交叉引流。
    #   放在「包含什么」之后、「边界」之前 —— 这一页的访客诉求就是「别花钱、
    #   别麻烦」，插件正好是更省事的那条路，插在讲边界之前转化最顺。
    #   链接给扩展包直链（访客在这页就想马上装，别再让他跳一圈），
    #   次链接回首页看动图。商店页先不放主位：2026-10-24 实测陌生访客
    #   打开还是 404（详见 build_site.py 里 EXT_STORE_URL 上方的说明）。
    s1b = section("连软件都不想装？浏览器里装个扩展", "\n".join([
        para("如果你只是偶尔存几条视频，有个更轻的做法：装一个 Edge 扩展。"
             "打开抖音视频页，右下角就多一个「下载无水印」，点一下存原片；"
             "博主主页那个按钮会变成「下载该作者 N 条」，整号批量也能一次存完。"),
        list_block("", [
            "只申请一个下载权限，不读浏览记录，也不上传任何东西。",
            "同样免费，不用注册、不用登录我们的账号。",
            "地址直接从抖音页面自己的数据里取，不调付费接口。",
        ]),
        para(f'<a href="{EXT_ZIP_URL}">下载扩展包 →</a>'
             '　　<a href="index.html#extension">先看它怎么用（带操作动图）</a>'),
    ]), kick="更轻的选择")
    s2 = section("这几项不在免费版里", "\n".join([
        para("下面这些功能要调用付费接口，有实实在在的成本，所以放在完整版里。"
             "免费版的界面上<b>根本不会出现这些入口</b> —— 不会给你一排点不动的按钮："),
        list_block("", [
            "数据采集与关键词抓取（博主主页作品批量入库、低粉高赞选题挖掘）",
            "评论区截流（按意向短语扫评论、挑高意向用户）",
            "短视频文案仿写（拆解爆款结构、生成口播稿）",
            "GEO 优化（AI 搜索可见度监测）",
        ]),
    ]), kick="边界")
    s3 = section("一个要提前知道的区别", "\n".join([
        para("视频下载有两条路：一条是软件直接读抖音页面自己解析（不花钱），"
             "一条是查付费接口（每条都有成本）。<b>两个版本都只走第一条</b>"
             "—— 这是 2026-10-23 之后定死的：说好下载不要钱，那就一次接口"
             "都不调，接口欠费、额度用完都不影响下载。"),
        # ★ 2026-10-25（v1.24.7）：上一版这里还写着「多半是没登录，去扫码登录」，
        #   那是服务端解析之前的旧结论。解析搬到服务端之后跟本机登录态无关，
        #   官网继续挂着这句话，等于把访客往一条走不通的路上推 —— 客户已经被
        #   这条提示坑过一次（软件里的同一句在 v1.24.6 就删了，这边漏改了）。
        para("偶尔会遇到「这条链接没能解析出来」—— 最常见的原因是链接本身已经失效"
             "（分享链接有有效期），到 App 里重新「分享 → 复制链接」再导一次；"
             "下载提示「链接已过期」时软件会自动重新解析一次。"),
        para("还有一个差别要说清：小红书、快手、TikTok 这三个平台以前只有付费解析接口"
             "这一条路，所以最早那几版下不了。2026-10-25 之后改成服务端解析，"
             "<b>抖音、小红书、快手、微信视频号都能下，而且都不用登录</b> "
             "—— 打开链接的是服务端，不看你本机有没有登录态。"
             "TikTok 也支持，把浏览器地址栏里的完整作品链接复制过来即可"
             "（形如 tiktok.com/@作者/video/数字ID）；App 分享出来的短链要先跳转一次，"
             "建议换成完整链接更稳。"),
    ]), kick="说明")
    s4 = section("为什么这两项敢永久免费", cmp_table([
        ("视频下载", "付费版会查详情接口，每条都有接口成本",
         "免费版走页面解析，取到的播放地址与接口同源，一样无水印，零成本"),
        ("直播录制", "本身就不经过付费接口",
         "抖音走自有直播流接口；TikTok 走本机直连 + 本地 ffmpeg 录制，两者全程都不消耗接口额度"),
        ("结论", "收费项都要付接口钱", "这两项不付，所以免费给你用"),
    ]), kick="成本")
    s5 = section("什么时候值得升级到付费版", "\n".join([
        list_block("", [
            "你要看的不只是「存视频」，而是<b>批量看一个博主的全部作品数据</b>、"
            "按关键词铺量采集 —— 那是数据采集。",
            "你想<b>从评论区里挖客户</b>，把「怎么买」「多少钱」这类评论挑出来跟进。",
            "你要<b>拆爆款文案的结构</b>，批量出口播稿。",
            "免费通道偶尔读不到时，你希望软件自动换接口兜底，不想手动重试。",
        ]),
        para('付费版 19.9 元起，一码一机。<a href="price.html">看价格与授权 →</a>'),
    ]), kick="升级")
    return hero + "\n" + s1 + "\n" + s1b + "\n" + s2 + "\n" + s3 + "\n" + s4 + "\n" + s5


def body_download(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("douyin-xiazai.html")["h1"]}</h1>\n'
        '      <p class="lead">看到一条值得存下来的抖音视频，最麻烦的不是找，是「怎么存」。'
        "录屏画质糊一层，在线去水印网站今天能用明天就挂，一张张点更是点到手酸。"
        "流量捕手把这件事变成：复制链接，粘贴，下载。</p>\n"
        "    </div>\n"
        + steps([
            ("复制链接", "在抖音点分享 → 复制链接，一次可以复制好几条，不用一条条来。"),
            ("粘贴解析", "回软件粘贴进去，点解析，软件自己取原画质地址，不用你判断。"),
            ("批量下载", "确认列表后一键下载，视频、图集各自归档，直接进本地素材库。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("能拿到什么", "\n".join([
        para("下面是这条链路里实际能拿到的东西，没有夸大，也没有隐藏条件："),
        list_block("", [
            "<b>无水印原画质</b>：拿到的是平台原始画质，不是录屏二次压缩的版本，拖进剪辑软件直接能用。",
            "<b>图集也能拿</b>：抖音的图文作品一组图一次打包下载，不用右键一张张存。",
            "<b>批量入库</b>：链接可以一次粘贴多条，下载结果按你设的目录归档，不用自己重命名。",
            "<b>不上传、不云端</b>：解析在你本机完成，下载下来的文件也只在你自己的硬盘上。",
            "<b>抖音 / 小红书 / 快手 / 视频号都能下</b>：解析由服务端完成，不用扫码登录，"
            "也不扣你的接口额度；接口欠费、额度用完都不影响下载。",
            "<b>几百条也不怕</b>：链接清单可以直接存成 .txt（一行一条），"
            "点「导入 TXT 链接清单」一次跑完，失败的会逐条列出原因。",
        ]),
        _cta_download(version, size_mb),
    ]), kick="能力")
    s2 = section("和常见的做法比一比", cmp_table([
        ("画质", "录屏会压一遍，声音还可能录进环境音", "取原画质地址，就是平台上那条视频本身"),
        ("批量", "一条条点，十几条就烦了", "多条链接一起粘，一次下载完"),
        ("稳定性", "在线去水印网站随时可能失效或加限制", "桌面软件在你本机，不依赖某个网站活着"),
        ("图集", "右键一张张存，长图还容易存不全", "一组图打包下载"),
        ("隐私", "上传到别人的服务器处理", "全程在本机，文件不上网"),
    ]), kick="对比")
    s3 = section("这些情况要提前知道", "\n".join([
        list_block("", [
            "只能下载公开可见的内容。别人设了权限、或者已经删除的作品，解析不到。",
            "平台偶尔会调整接口，遇到解析失败先看是不是有新版（更新日志里会写）。",
            "下载的内容版权仍归原作者，请用于合法的内容研究、素材整理与学习，不要直接搬运发布。",
            "Windows 10 / 11（64 位）可用，Mac 版暂未开放。",
        ]),
    ]), kick="说明")
    return hero + "\n" + s1 + "\n" + s2 + "\n" + s3


def body_live(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("zhibo-luzhi.html")["h1"]}</h1>\n'
        '      <p class="lead">盯同行直播最耗人的不是看，是「守」。开播时间不固定，'
        "人得一直在电脑前等着，录屏的时候电脑还占着干不了别的。"
        "流量捕手的处理方式是：你把链接给它，它替你等。抖音和 TikTok 直播间都支持。</p>\n"
        "    </div>\n"
        + steps([
            ("粘贴直播间链接", "把抖音或 TikTok 的主播直播间链接加进监控列表，可以一次加好几个；"
                              "TikTok 也能直接填 @主播号。"),
            ("软件替你轮询", "定时查一次这个直播间在不在播，轮询间隔自己调。"),
            ("在播自动开录", "查到正在直播就自动开始录制，结束自动停，录像按主播分目录存好。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("为什么不用录屏", cmp_table([
        ("要不要人守", "要，开播时间不定就得一直盯着", "不用，加一次链接就行"),
        ("电脑能不能干别的", "录屏占着，基本干不了别的", "后台录制，该干嘛干嘛"),
        ("多个主播", "一台机器同时录几路很吃力", "监控列表里可以加多个直播间"),
        ("文件整理", "录完自己重命名、自己归档", "按主播分目录，文件名带时间"),
    ]), kick="对比")
    s2 = section("适合拿它干什么", "\n".join([
        list_block("", [
            "<b>长期盯对标直播间</b>：把同行都加进列表，他们什么时候播、播了多久、讲了什么，都有留存。",
            "<b>攒直播素材</b>：一场直播里的讲解、演示、促单话术，录下来慢慢拆。",
            "<b>复盘自己的直播</b>：自己直播间也加进去，录完回看找问题。",
            "<b>转写成稿</b>：录完的文件可以直接本地转写成文字，做成话术库。",
        ]),
        _cta_download(version, size_mb),
    ]), kick="场景")
    # ★ 标题里不要出现「也」：官网字体是按**旧标题字符集**打的子集，
    #   而 tools/fonts_src 的源 woff2 已缺失、无法重建 —— 新字会掉回雅黑，
    #   同一个标题出现两种字体（_validate_theme.py 会直接报错）。
    s2b = section("TikTok 直播间一起盯", "\n".join([
        para("直播监控同时支持抖音和 TikTok。TikTok 那边不采集数据、不调用付费接口，"
             "走的是本机直连 + 本地 ffmpeg 录制，<b>和抖音直播一样永久免费、"
             "不限次数、不消耗接口额度</b>。"),
        list_block("", [
            "在「直播监控」里粘贴 TikTok 直播间链接，或直接填主播号（形如 @主播名）。",
            "TikTok 需要你电脑上的代理能正常访问海外。软件会自动探测本机常用代理端口，"
            "页面上也有「TikTok 代理」一栏可以手动填写并保存。",
            "首次添加要过一次浏览器校验（约 10~20 秒），之后按轮询间隔自动查状态。",
            "状态分「正在直播 / 已下播 / 状态未知」三种；接口临时失败会保留上次结果，"
            "不会把「没查到」当成「没开播」。下播后旧流地址会立刻失效，软件每次开录前都会重新取一次。",
        ]),
    ]), kick="TikTok")
    s3 = section("提前说明", "\n".join([
        list_block("", [
            "录制的是公开直播画面，和下载视频一样，请用于合法的内容研究与复盘。",
            "长时间录制占用的磁盘空间不小，硬盘留够余量；录制时尽量别让电脑休眠。",
            "录制期间需要保持软件在后台运行。",
        ]),
    ]), kick="说明")
    return hero + "\n" + s1 + "\n" + s2 + "\n" + s2b + "\n" + s3


def body_collect(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("shuju-caiji.html")["h1"]}</h1>\n'
        '      <p class="lead">想知道一个账号最近什么内容跑得好，最笨的办法是翻他的主页、'
        "一条条记数据。流量捕手把这件事做成一次拉取：给一个主页链接，"
        "几百条作品的标题、点赞、评论、发布时间一次导出成表。</p>\n"
        "    </div>\n"
        + steps([
            ("选入口", "可以按关键词抓，也可以直接给博主主页、喜欢列表、收藏列表。"),
            ("批量采集", "点一次就开始拉，作品标题、标签、互动数据一起入库。"),
            ("导出分析", "导出成表格，按点赞、按时间排序，哪条跑得好一目了然。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("三种抓法，解决三个不同的问题", "\n".join([
        list_block("<b>按关键词抓</b> —— 看一个话题整体的情况", [
            "输入一个词，把这个词下的作品抓回来，看这个话题里大家都在怎么拍。",
            "这一路不做粉丝过滤，抓到什么就是什么，适合看大盘。",
        ]),
        list_block("<b>按主页抓</b> —— 盯一个具体的号", [
            "给一个博主主页链接，把他的作品批量拉下来，看他最近在发什么、什么在跑。",
            "喜欢列表、收藏列表也能抓，能看出他在参考谁。",
        ]),
        list_block("<b>低粉高赞筛</b> —— 找值得抄结构的选题", [
            "专门筛「粉丝不多但点赞很高」的作品：这类内容说明是结构赢了，不是账号底子赢了。",
            "找选题比凭感觉刷快得多，抄结构也比抄内容稳。",
        ]),
    ]), kick="用法")
    s2 = section("采集结果里有什么", "\n".join([
        list_block("", [
            "作品标题、发布时间、点赞 / 评论 / 收藏 / 分享等互动数据",
            "作品链接与作者信息，方便回跳去看原片",
            "支持导出表格，直接做排序、筛选、透视",
            "历史留档：抓过的数据会留在本机，隔一段时间再看能对比变化",
        ]),
        _cta_download(version, size_mb),
    ]), kick="字段")
    s3 = section("提前说明", "\n".join([
        list_block("", [
            "关键词抓抖音单次大约返回 11 条，所以实际是靠多套排序 × 多个时间桶叠加去重来扩大覆盖面的。",
            "排序方式、发布时间筛选目前主要支持抖音；小红书与快手以实际支持为准。",
            "采集的是平台公开展示的数据，不含任何非公开信息。",
            "数据全部存在你本机，不上传。",
        ]),
    ]), kick="说明")
    return hero + "\n" + s1 + "\n" + s2 + "\n" + s3


def body_comment(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("pinglun-jieliu.html")["h1"]}</h1>\n'
        '      <p class="lead">评论区里确实有人问「这个怎么买」，但同样有几百条「哈哈哈哈」。'
        "如果只按「买」这个字去扫，扫出来大半是没用的。"
        "流量捕手用的是完整意向短语，不是单个词。</p>\n"
        "    </div>\n"
        + steps([
            ("填意向短语", "把「怎么买」「多少钱」「求链接」「加个微」这类完整说法填进去。"),
            ("扫评论区", "对指定作品或关键词下的评论做匹配，命中的挑出来。"),
            ("归档跟进", "命中的评论导出成表，谁、在哪条下面、说了什么，一清二楚。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("为什么必须用完整短语", cmp_table([
        ("用「买」", "扫出「我买过」「买不起」「哈哈哈买」，噪声大", "不推荐"),
        ("用「怎么买」", "大多是真的在问购买方式", "推荐"),
        ("用「多少钱」", "基本都是询价意向", "推荐"),
        ("用「求链接」", "要链接的人意图非常明确", "推荐"),
        ("用「加个微」", "主动求联系方式", "推荐"),
    ]), kick="关键")
    s2 = section("导出之后怎么用", "\n".join([
        list_block("", [
            "导出的表格里带着评论内容、评论者、所在作品，可以直接分给客服跟进。",
            "同一个人的历史评论能看出意向强弱，优先跟那些反复问的。",
            "数据存本机，换人交接直接拷文件夹。",
        ]),
        _cta_download(version, size_mb),
    ]), kick="落地")
    s3 = section("说明", "\n".join([
        list_block("", [
            "这一路抓的是公开评论，不含任何私密信息。",
            "抖音链路已经实测跑通；小红书、快手目前正在逐个验证中，以实际支持为准。",
            "联系他人时请遵守平台规则，不要群发骚扰。",
        ]),
    ]), kick="说明")
    return hero + "\n" + s1 + "\n" + s2 + "\n" + s3


def body_script(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("wenan-fangxie.html")["h1"]}</h1>\n'
        '      <p class="lead">仿写不是换个词重说一遍——那叫洗稿，平台也不认。'
        "真正有用的是拆结构：这条视频前 3 秒用什么钩子、中间几段各占多少时间、"
        "结尾怎么收。结构是可以学的，措辞必须是你自己的。</p>\n"
        "    </div>\n"
        + steps([
            ("丢一条链接进去", "把对标视频的链接粘贴进去，软件先下载再本地转写成文字稿。"),
            ("拆结构", "自动分析字数、句数、时长、钩子、转折、促单位置，还有真实语速。"),
            ("按体裁产出", "选体裁、给目标时长，产出分镜表：时间轴｜画面｜口播，照着拍就行。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("它到底仿什么", "\n".join([
        list_block("<b>仿结构，不仿措辞</b>", [
            "段落骨架（每段讲什么、占多少时间）、钩子套路、节奏与语速，这些是可以学的。",
            "原句不照搬，提示词里明确禁止复制原句。",
            "内置 8 类体裁：口播、剧情、Vlog、种草带货、测评开箱、教程干货、情感共鸣、访谈对话。",
            "每类体裁有各自的语速参考（口播偏快、情感偏慢），目标字数 = 秒数 × 语速，写完不会突然长一倍。",
        ]),
        list_block("<b>时间轴带来的信息，纯文本算不出来</b>", [
            "实测口播秒数、真实语速、前 3 秒钩子、每 5 秒的节奏分布。",
            "没有时间轴时这些字段就是 0，不编数字。",
        ]),
        list_block("<b>写完会自己检查</b>", [
            "合规词检查：极限词、医疗功效、虚假承诺、站外引流，命中会给替换建议。",
            "量词豁免——「第一次」不算「第一」，不会误伤正常表达。",
            "评分 100 分：结构 / 钩子 / 时长 / 合规 / 卖点覆盖，每项给可读理由。",
        ]),
        _cta_download(version, size_mb),
    ]), kick="原理")
    s2 = section("转写为什么放在本地做", "\n".join([
        para("对标视频往往就是同行的爆款。把这种内容传到第三方语音识别服务，"
             "等于替对手把家底存了一份在别人服务器上。所以转写这一步是本地完成的，"
             "同一条视频只转一次，转完原片默认删除。"),
        list_block("本地转写会碰到的三种情况，软件都做了处理", [
            "模型收尾时可能自己编一句「请点赞订阅」之类的话 —— 有黑名单直接剔除。",
            "偶尔会复读同一句十几遍 —— 同句最多保留两次。",
            "纯音乐、没有人声的视频 —— 会明确提示「没听出人话」，这是结果不是故障。",
        ]),
    ]), kick="说明")
    return hero + "\n" + s1 + "\n" + s2


def body_geo(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("geo.html")["h1"]}</h1>\n'
        '      <p class="lead">现在越来越多人不搜了，直接问 AI。'
        "那就有个新问题：用户问到你的品类时，AI 的回答里提不提你？"
        "GEO 这一路就是把这个「看不见」的东西测出来。</p>\n"
        "    </div>\n"
        + steps([
            ("准备提问词", "把用户真会问的那些问题列出来，比如「XX 怎么选」「XX 哪家靠谱」。"),
            ("去问 AI", "拿这批问题去问主流 AI 引擎，把回答原文采回来。"),
            ("检测与报告", "看你的品牌有没有被提到、被引用的是哪些来源，出优化清单。"),
        ]) + "\n  </div>\n</section>"
    )
    s1 = section("报告里有什么", "\n".join([
        list_block("", [
            "<b>提及率</b>：多少个问题里提到了你的品牌，哪些问题提了、哪些没提。",
            "<b>引用来源</b>：AI 回答里引用了哪些页面，你的内容在不在里面。",
            "<b>优化清单</b>：针对没被提到的问题，给出可执行的下一步。",
            "<b>失败单独标出</b>：某个引擎这次请求失败，会标红单列，<b>不计入提及率</b>。",
        ]),
        para("最后这条是硬规矩：请求失败不等于「没提到你」。如果把失败当成未提及算进比例，"
             "整个报告就废了。"),
        _cta_download(version, size_mb),
    ]), kick="产出")
    s2 = section("说明与边界", "\n".join([
        list_block("", [
            "目前接的是 DeepSeek 与 Kimi 的官方接口，需要你填自己的 Key；豆包走浏览器通道，暂未开放。",
            "引擎的可用模型会变，界面里可以自己改模型名，不用等软件更新。",
            "<b>不做任何效果承诺</b>：这一路是「测量」和「给建议」，不保证被 AI 推荐。",
            "引用信源目前主要靠公开页面，不是联网检索通道，报告里能看到的来源有限，"
            "这点如实写在这里，不夸大。",
        ]),
    ]), kick="说明")
    return hero + "\n" + s1 + "\n" + s2


def body_price(version: str, size_mb: str) -> str:
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("price.html")["h1"]}</h1>\n'
        '      <p class="lead">先说清楚：视频下载和直播录制<b>永久免费</b>，不用付钱。'
        '下面这三档是给需要「采集、截流、仿写」的人准备的，按每天的使用次数分 ——'
        "你是偶尔用一次，还是一天到晚挂着。</p>\n"
        "    </div>\n  </div>\n</section>"
    )
    plans = [
        # 免费版放第一个：让人先看到「有不要钱的」，降低试用门槛
        ("免费版", "0", "永久", "无需激活码，装完就能用",
         ["抖音视频下载：原画质无水印、可批量、不限次数",
          "直播录制：抖音 / TikTok 都支持，开播自动录，不用守着",
          "不绑机器、不限制使用时间和次数"]),
        ("体验版", "19.9", "38 天", "31 天 + 赠送 7 天",
         ["每天 10 次采集额度", "全部功能可用", "一码一机，换机免费重发"]),
        ("标准版", "29.9", "月", "到期续费价，数据不丢",
         ["每天 30 次采集额度", "全部功能可用", "优先答疑"]),
        ("专业版", "59.9", "月", "重度用户 / 矩阵号团队",
         ["每天 100 次采集额度", "全部功能可用", "一对一远程协助"]),
    ]
    cards = []
    for name, price, unit, desc, feats in plans:
        li = "".join(f"<li>{x}</li>" for x in feats)
        hot = " hot" if name == "体验版" else ""
        tag = {"免费版": "永久免费", "体验版": "首购专享"}.get(name, "续费用")
        # ¥0 显示成「免费」更直观，也避免让人以为是限时的 0 元活动
        amt = ('<div class="amt"><b>免费</b><span>/ 永久</span></div>'
               if name == "免费版"
               else f'<div class="amt"><b>¥{price}</b><span>/ {unit}</span></div>')
        btn = "直接下载" if name == "免费版" else "选这个"
        cards.append(
            f'      <div class="plan{hot}">\n'
            f'        <span class="tag">{tag}</span>\n'
            f'        <h3>{name}</h3>\n'
            f'        <div class="psub">{desc}</div>\n'
            f'        {amt}\n'
            f"        <ul>{li}</ul>\n"
            f'        <a class="btn btn-dark" href="index.html#download">{btn}</a>\n'
            "      </div>"
        )
    s1 = (
        "<section>\n  <div class=\"wrap\">\n"
        '    <div class="pgrid3 rv">\n' + "\n".join(cards) + "\n    </div>\n  </div>\n</section>"
    )
    s2 = section("关于额度，说清楚一点", "\n".join([
        list_block("额度怎么算", [
            "按「调用接口的次数」计：下载一个视频算一次，抓一次列表算一次，查一次直播状态也算一次。",
            "当天没用完不累积，过了一天重新开始计。",
            "对着同一个视频反复下载同样算次数，所以批量下载前先确认列表更省。",
        ]),
        list_block("关于授权", [
            "一码一机：一个授权码绑定一台电脑。",
            "换电脑把新机器码发给作者，免费重发一张，不重复收费。",
            "购买与售后扫码加作者个人微信，作者本人对接，不是机器人客服。",
        ]),
        _cta_download(version, size_mb),
    ]), kick="细则")
    return hero + "\n" + s1 + "\n" + s2


def body_changelog(version: str, releases: list[dict]) -> str:
    try:
        import build_changelog as _bclog
        entries = _bclog.render_entries_html(releases, newest=version, fold_after=0)
    except Exception:                                    # noqa: BLE001
        entries = ""
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("changelog.html")["h1"]}</h1>\n'
        f'      <p class="lead">共 {len(releases)} 个版本，最新版 v{version}，按时间倒序。'
        "装好的软件里也自带同样一份：安装目录下的「更新日志」，随时能翻，不用联网。</p>\n"
        "    </div>\n"
        f'    <div class="rv" style="margin-top:34px">{entries}</div>\n'
        "  </div>\n</section>"
    )
    return hero


# 问答按主题归组：既方便人找，也避免整页只有一个「常见问题」标题显得单薄
FAQ_GROUPS = [
    ("装之前：系统与配置", ("系统", "配置", "电脑", "Windows", "Mac"),
     "软件是 Windows 桌面程序，普通办公本就能跑，不需要额外装运行环境。"),
    ("装之后：授权与激活", ("激活", "授权", "机器码", "几台"),
     "授权码跟机器码绑定，一台电脑一个码。换电脑不用重新买，把新机器码发过来重发一张。"),
    ("用起来：额度、隐私与售后", ("额度", "数据", "上传", "试用", "规划", "免费"),
     "额度按调用接口的次数算，数据在您本机处理。这一块是最容易被问的，下面一次说清。"),
]


def body_faq(version: str, size_mb: str) -> tuple[str, list[tuple[str, str]]]:
    """返回 (html, 问答对)。问答对同时喂给 FAQPage 结构化数据。"""
    try:
        qa = list(_load_faq_from_site())
    except Exception:                                    # noqa: BLE001
        qa = []

    used: set[str] = set()
    groups_html = []

    def _items(picked: list[tuple[str, str]]) -> str:
        """FAQ 条目：结构必须和 site_theme.css 的 .qa summary / .qa-a / .arw 对上。"""
        return "".join(
            f'      <details class="qa"><summary>{q}'
            f'<span class="arw" aria-hidden="true"></span></summary>'
            f'<div class="qa-a">{a}</div></details>'
            for q, a in picked
        )

    for title, keys, lead in FAQ_GROUPS:
        picked = [(q, a) for q, a in qa
                  if q not in used and any(k in q or k in a for k in keys)]
        if not picked:
            continue
        used.update(q for q, _ in picked)
        groups_html.append(
            "    <h3>" + title + "</h3>\n"
            f'    <p style="margin-top:10px;color:var(--ink3);font-size:14.5px">{lead}</p>\n'
            f'    <div class="faq-in" style="margin-top:16px">{_items(picked)}</div>'
        )
    # 没被任何组收下的，统一放进最后一块，保证一条都不丢
    rest = [(q, a) for q, a in qa if q not in used]
    if rest:
        groups_html.append("    <h3>其他</h3>\n"
                           f'    <div class="faq-in" style="margin-top:16px">{_items(rest)}</div>')

    items = "\n".join(groups_html)
    hero = (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("faq.html")["h1"]}</h1>\n'
        '      <p class="lead">问题分成三类：装之前、装之后、用起来。'
        "对着找比较快。没答上的直接加微信问，作者本人回，不是机器人。</p>\n"
        "    </div>\n"
        f'    <div class="prose rv" style="margin-top:26px">{items}</div>\n'
        f'    <div class="prose">{_cta_download(version, size_mb)}</div>\n'
        "  </div>\n</section>"
    )
    return hero, qa


def body_privacy(ver: str) -> str:
    """隐私策略。

    ★ 这一页是给应用商店审核看的，不是营销页。所以原则只有一条：
      **逐条如实**。写了却做不到，被抽查到就是下架；反过来，凡是我们
      真的没碰的东西（浏览记录、身份信息），一定要明确写出来 ——
      竞品在商店里写「处理个人身份信息」，我们连 tab.url 都不读，
      这个差别就是用户选我们不选它的理由，得让它摆在明面上。
    """
    blocks = [
        ("先说结论", [
            "我们不采集你的身份信息，不读取你的浏览记录，不要求注册账号，"
            "不把你的数据发送到任何服务器。",
            "采集到的作品数据（标题、点赞、评论）只存在你自己电脑上的本地数据库里，"
            "卸载软件时一并删除。",
        ]),
        ("浏览器插件碰了什么", [
            "插件的内容脚本只在 <code>douyin.com</code>、<code>iesdouyin.com</code> 这两个域名下运行。",
            "它<b>没有</b>申请「读取您的浏览记录」权限，技术上也不知道你还在浏览哪些其他网页。",
            "它申请的权限是：<b>下载文件</b>，以及访问抖音的视频与图片服务器"
            "（<code>douyinvod.com</code>、<code>douyinpic.com</code>、<code>zjcdn.com</code> 等）。"
            "后者是必需的：那些服务器会校验请求来源，不带来源头就返回错误页，文件根本下不下来。",
            "下载时，插件会把<b>你点选的那一个文件</b>从抖音服务器取回浏览器本地，"
            "随即交给浏览器的下载功能存成文件。取回的内容不经过我们的服务器，"
            "也不在插件里留副本，存完即弃。",
            "插件从抖音页面已加载的数据里读取作品的标题、作者和播放地址，仅用于生成下载任务。",
            "这些内容不会上传，只在你自己的浏览器内存里存在，用完即弃。",
        ]),
        ("电脑软件碰了什么", [
            "视频下载、直播录制产生的文件，全部保存在你自己指定的目录，默认在「文档 / 流量捕手下载」。",
            "数据采集（关键词、博主主页、评论区）的结果存在本地 SQLite 数据库，"
            "路径在 <code>%APPDATA%\\TrafficCatcher\\history.db</code>。",
            "登录态 Cookie 保存在同一目录下，用于让抖音页面返回完整数据。它不会离开这台电脑。",
            "授权校验只需要机器码，不绑定手机号、邮箱或任何身份信息。",
        ]),
        ("我们明确不做的事", [
            "不向第三方出售或共享任何数据。",
            "不上传你的采集结果、文件列表或登录态。",
            "不在软件里植入统计 SDK、不做行为埋点。",
            "不读取除上述目录之外的任何文件。",
        ]),
        ("什么时候会联网", [
            "<b>视频下载与页面解析</b>：直接请求抖音，不经过我们的服务器。",
            "<b>授权激活</b>：向授权服务提交机器码与授权码，只返回可用状态。",
            "<b>版本更新</b>：从对象存储拉取更新清单与安装包。",
            "<b>部分采集功能</b>：少数平台数据需要经由第三方数据接口，"
            "此时提交的是作品链接或关键词，不含你的个人信息。这部分会在界面上标注。",
        ]),
    ]

    parts = []
    for h, items in blocks:
        li = "\n".join(f"          <li>{x}</li>" for x in items)
        parts.append(f"      <h2>{h}</h2>\n      <ul>\n{li}\n      </ul>")

    body = "\n".join(parts)
    return (
        "<section>\n  <div class=\"wrap\">\n"
        f'    <div class="prose rv">\n      <h1>{seo.page_by_file("privacy.html")["h1"]}</h1>\n'
        '      <p class="lead">最后更新：2026-10-24。这一页写给想搞清楚「我装的东西到底碰了什么」的人，'
        "逐条列明，不含糊。</p>\n"
        f"      {body}\n"
        '      <p style="margin-top:26px;color:var(--ink3);font-size:14px">'
        "有疑问直接加微信问，作者本人回。条款改了会更新这一页，并同步到应用商店。</p>\n"
        "    </div>\n"
        "  </div>\n</section>"
    )


def _load_faq_from_site() -> list[tuple[str, str]]:
    """问答只维护一份：复用官网首页那份，避免两处说法不一致。"""
    import importlib
    mod = importlib.import_module("build_site")
    return list(getattr(mod, "FAQ", []))


# ─────────────────────────────────────────────────────────────────────
#  主流程
# ─────────────────────────────────────────────────────────────────────

def read_json(name: str) -> dict:
    f = INSTALLER_OUT / name
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:                                # noqa: BLE001
            return {}
    return {}


def read_version_json() -> dict:
    return read_json("version.json")


def read_free_json() -> dict:
    """免费版产物信息（tools/release_free.py 生成）。

    没有这个文件（还没打过免费版包）时返回空字典，free.html 会退回
    「去下载页面」按钮 —— 页面照常能上，不会因为这个文件缺失就报错。
    """
    return read_json("version_free.json")


def build(version: str = "", notes: str = "") -> list[Path]:
    """生成全部子页 + robots.txt + sitemap.xml。返回产出文件列表。"""
    import build_changelog as _bclog

    vj = read_version_json()
    ver = version or vj.get("version") or "?"
    size_mb = f"{int(vj.get('size', 0)) / 1024 / 1024:.1f} MB" if vj.get("size") else "—"

    # 免费版安装包信息（独立产物，见 tools/release_free.py）
    fj = read_free_json()
    _fw = fj.get("win") or {}
    free_ver = str(fj.get("version") or "")
    free_url = str(_fw.get("url") or "")
    free_size = (f"{int(_fw.get('size', 0)) / 1024 / 1024:.1f} MB"
                 if _fw.get("size") else "")

    releases = _bclog.load_changelog()
    if notes and ver and ver != "?":
        # 有更新说明就先并进更新日志，子页里的版本列表才是完整的
        _bclog.build(version=ver, notes=notes)
        releases = _bclog.load_changelog()

    css = seo.load_css(_bclog.CLOG_CSS)
    STANDALONE.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    bodies = {
        "douyin-xiazai.html": (body_download(ver, size_mb), None),
        "free.html": (body_free(ver, size_mb, free_url, free_ver, free_size), None),
        "zhibo-luzhi.html": (body_live(ver, size_mb), None),
        "shuju-caiji.html": (body_collect(ver, size_mb), None),
        "pinglun-jieliu.html": (body_comment(ver, size_mb), None),
        "wenan-fangxie.html": (body_script(ver, size_mb), None),
        "geo.html": (body_geo(ver, size_mb), None),
        "price.html": (body_price(ver, size_mb), None),
        "changelog.html": (body_changelog(ver, releases), None),
        # 隐私策略：不为引流，是应用商店上架的必备材料（要填可访问的网址）
        "privacy.html": (body_privacy(ver), None),
    }
    for f, (body, _) in bodies.items():
        html = render_page(page_file=f, body=body, version=ver, css=css)
        out = STANDALONE / f
        out.write_text(html, encoding="utf-8")
        written.append(out)

    faq_html, qa = body_faq(ver, size_mb)
    out = STANDALONE / "faq.html"
    out.write_text(
        render_page(page_file="faq.html", body=faq_html, version=ver,
                    json_ld=[seo.ld_faq(qa)], css=css),
        encoding="utf-8",
    )
    written.append(out)

    # robots / sitemap：百度能不能顺着清单抓全站，就靠这两个文件
    lastmod = (vj.get("published") or "")[:10] or _today()
    (STANDALONE / "robots.txt").write_text(seo.render_robots(), encoding="utf-8")
    written.append(STANDALONE / "robots.txt")
    (STANDALONE / "sitemap.xml").write_text(
        seo.render_sitemap(lastmod), encoding="utf-8")
    written.append(STANDALONE / "sitemap.xml")

    return written


def _today() -> str:
    import time
    return time.strftime("%Y-%m-%d", time.localtime())


def main() -> int:
    ap = argparse.ArgumentParser(description="生成官网栏目子页与站点地图")
    ap.add_argument("--version", default="", help="版本号，默认读 version.json")
    ap.add_argument("--notes", default="", help="本次更新说明（补进更新日志）")
    args = ap.parse_args()

    files = build(args.version, args.notes)
    print(f"已生成 {len(files)} 个文件：")
    for f in files:
        print(f"   {f.name}  ({f.stat().st_size:,} 字节)")
    print()
    print(seo.render_baidu_verify_hint())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
