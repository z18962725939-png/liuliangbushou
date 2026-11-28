#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""流量捕手官网：单页营销站（产品矩阵 / 核心能力 / 功能详解 / 价格 / 下载 / FAQ）。

设计基调（参考 Kimi 官网）：
    纯白底、大留白、黑白灰为主 + 柔和渐变卡片、点阵装饰、黑色胶囊按钮。
    产品矩阵用「已上线 + 规划中」表达路线图，方便后续陆续点亮新功能。

为什么单独一个脚本：
    `build_download_page.py` 产出的是"工具型下载页"（只有版本、下载按钮、价格），
    官网需要的是完整的信息架构（卖点 / 详解 / 截图 / 常见问题），版式完全不同，
    混在一个模板里会把两边都写乱。

产物与流程兼容：
    输出仍写到 `installer_output/standalone/index.html`，因此
    `publish_download_page.py`（上传 COS）与 WorkBuddy 部署流程**都不用改**。

★ 以后新增功能怎么改（重要）：
    产品矩阵的数据在 `tools/products.json`（首次运行会自动生成）。
    新功能上线时，把对应条目的 "status" 从 "soon" 改成 "live"、更新 tagline/desc/points 即可，
    脚本不用动。已上线产品会获得实心高亮样式与"立即下载"入口。

用法：
    python tools/build_site.py              # 生成 + 上传截图 + 上传页面到 COS
    python tools/build_site.py --local      # 只在本地生成预览（图片走本地副本，不上传）
    python tools/build_site.py --no-upload  # 生成 + 上传截图，但不上传 HTML
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
# 更新日志生成器与本脚本同在 tools/，直接 import 即可（官网与安装包共用一套渲染）
sys.path.insert(0, str(Path(__file__).resolve().parent))
INSTALLER_OUT = ROOT / "installer_output"
STANDALONE = INSTALLER_OUT / "standalone"
SHOTS_DIR = ROOT / "官网素材" / "ui"
CONFIG_FILE = ROOT / "tools" / "cos_config.json"
PAY_CONFIG_FILE = ROOT / "tools" / "payment_config.json"
PRODUCTS_FILE = ROOT / "tools" / "products.json"

# 正式对外地址（与 build_download_page.py 的 PAGE_URL 保持一致）
PAGE_URL = "https://liuliangbushou-1258850027.cos-website.ap-guangzhou.myqcloud.com/"
WECHAT_ID = "zsx807108536"
BRAND = "流量捕手"

# ★ 作者个人微信「加好友」二维码（对外联系用）。
#   与 tools/pay_qr/wechat.png 的「收款码」是两码事，严格区分、绝不互相替换：
#     · 收款码 → 客户购买授权时付款，替换掉就收不到钱；
#     · 个人码 → 咨询 / 售后 / 加好友，扫了是加人不是付款。
#   两者在联系弹窗里分成上下两个带标题的区块，避免客户扫错。
PERSONAL_QR_SRC = ROOT / "docs" / "contact" / "wechat-personal-qr.jpg"
PERSONAL_QR_ASSET = "assets/wechat-personal-qr.jpg"

# 官网用到的界面截图：(源文件名去扩展名, COS 上传名)
SITE_SHOTS = [
    ("视频下载", "shot-video-download.png"),
    ("直播监控", "shot-live-monitor.png"),
    ("音视频转文本", "shot-transcribe.png"),
    ("数据采集", "shot-collect.png"),
]
SHOT_WIDTH = 1400          # 界面截图展示宽度约 700px，2x 清晰度足够
SHOT_CACHE = "public, max-age=604800"

# ── 浏览器插件（2026-10-24 上新）────────────────────────────────────
# 动图由 D:\workbuddy\2026-10-03-23-06-16\生成插件演示动图.py 产出后拷到这里。
# GIF 不做成 1400px 宽的"高清图"，是因为动图体积会翻好几倍 —— 720 宽在
# 官网卡片里已经够看，334 KB 也不会把首页拖慢。
EXT_GIF_SRC = SHOTS_DIR / "plugin-demo.gif"
EXT_GIF_KEY = "plugin-demo.gif"
# Edge 商店详情页。后台（微软合作伙伴中心）2026-10-24 17:18 状态为 Live。
# ★★ ID 是从后台「扩展标识 → CRX ID」**逐字符抄下来的**：
#        opjdobghhbhphnpeaeipljkhhigjlfga
#    之前写的是 opjdosghbhphnpeaepjkhhjgffga —— 那是照缩略图**看错的**，
#    白测了半小时。以后改这个常量只认后台复制的值，**绝不照图手打**。
#    （Microsoft Store ID = 0RDCKDB5QFML，商店内部接口才用，别拿它拼链接。）
#
# ★★★ 判定商店页面是否可访问的唯一可靠方法（2026-10-24 实测得出）：
#    微软商店是 SPA，**不管 ID 存不存在，HTTP 都返回 200**，拿 curl 判状态码
#    会得到完全相反的结论。必须渲染后看 **title**：
#        真页面 = "<扩展名> - Microsoft Edge Addons"    （带 " - "）
#        404 页 = "Microsoft Edge 加载项" / "Microsoft Edge Add-ons"（通用）
#    别去 body 里找"获取/Get" —— 英文页按钮是 Get、导航里也带 Edge，都会误判。
#    工具：tools/_probe_edge_detail.py（自带对照组，成对跑）。
#
# ★ 当前状态（2026-10-24 17:52 实测，别再重复踩）：
#   后台 Live，作者自己的 Edge 能打开详情页（有「获取」按钮、版本 1.0.1）；
#   但同一台机器上：curl 打 getproductdetailsbycrxid 连续 12 次全 404，
#   全新无头 profile 渲染 title 也是 404 通用标题，而对照组扩展在同环境正常
#   ⇒ 不是 URL 写法（带不带 slug 都试了）、不是 headless 检测、不是 CDN 抖动，
#     是微软侧的公开可见性还在铺量（作者浏览器有登录态/缓存，所以先看到）。
#   ⇒ 主按钮继续用扩展包（100% 能装），商店只当备用小字入口；
#     等「无痕窗口」也能打开，再把主按钮换成 EXT_STORE_URL。
#   复核脚本：tools/_probe_edge_slug.py（A 纯 ID / B 带 slug / D 对照组）。
EXT_STORE_URL = "https://microsoftedge.microsoft.com/addons/detail/opjdobghhbhphnpeaeipljkhhigjlfga"
# 审核期的真实可用入口：tools/publish_ext_zip.py 打包上传的免审核安装包。
# 解压 + 开发者模式加载，功能与商店版完全一致，当天就能用。
EXT_ZIP_URL = ("https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com"
               "/trafficcatcher/ext/TrafficCatcher_Edge_Ext_v1.0.1.zip")

# ─────────────────────────────────────────────────────────────────────
#  产品矩阵（★ 后续新增功能改 tools/products.json，而不是这里）
#  status: live = 已上线（卡片高亮 + 立即下载）；soon = 规划中（虚线 + 预约）
# ─────────────────────────────────────────────────────────────────────

PRODUCTS_DEFAULT = [
    {
        "key": "catcher", "name": "流量捕手", "status": "live", "badge": "已上线",
        "glyph": "◎", "tint": "indigo",
        "tagline": "抖音 / TikTok 素材下载与直播录制，一个人顶一个剪辑组",
        "desc": "把链接变成素材：作品无水印下载、抖音 / TikTok 直播间自动录制、"
                "主页作品批量入库、本地转写文案，全部在你自己的电脑上完成。",
        "points": ["作品 / 图集无水印原画质下载", "抖音 / TikTok 直播间粘贴链接，在播自动开录",
                   "主页 / 喜欢 / 收藏列表批量采集导出"],
    },
    {
        "key": "geo", "name": "GEO 优化", "status": "soon", "badge": "规划中",
        "glyph": "◈", "tint": "blue",
        "tagline": "让 AI 搜索结果里，先出现你的品牌",
        "desc": "面向生成式搜索的内容优化：诊断品牌在 AI 回答中的出现情况，"
                "定位缺失的引用来源，给出可执行的优化清单。",
        "points": ["品牌 AI 可见度诊断", "回答来源与引用追踪", "内容优化建议清单"],
    },
    {
        "key": "copywriting", "name": "短视频文案仿写", "status": "soon", "badge": "规划中",
        "glyph": "✎", "tint": "violet",
        "tagline": "拆开爆款看结构，产出你自己的口播稿",
        "desc": "丢一条对标爆款进去，自动拆结构、提钩子、换表达，"
                "直接产出可以照着开拍的文案，而不是给一堆形容词。",
        "points": ["爆款结构逐段拆解", "锁定钩子与节奏仿写", "批量产出待拍稿"],
    },
    {
        "key": "aiclip", "name": "AI 精简视频直出", "status": "soon", "badge": "规划中",
        "glyph": "▶", "tint": "amber",
        "tagline": "长片自动掐点成片，剪完直接发",
        "desc": "自动找出高光片段、剔除废话与停顿、补字幕与转场，"
                "把一场长直播或长视频压成可以直接发布的短视频。",
        "points": ["高光片段自动识别", "废话停顿自动剔除", "字幕转场自动配齐"],
    },
    {
        "key": "traffic", "name": "多平台评论区截流", "status": "soon", "badge": "规划中",
        "glyph": "◍", "tint": "green",
        "tagline": "在精准的评论区里，稳稳拿到属于你的流量",
        "desc": "多平台评论区监控与关键词命中，第一时间发现高意向话题与账号，"
                "把公域里飘着的流量接回你的私域。",
        "points": ["多平台评论实时监控", "高意向关键词命中提醒", "线索自动归档跟进"],
    },
]

PRICE_PLANS = [
    {
        "key": "trial10", "name": "体验版", "tag": "首购专享",
        "price": "19.9", "unit": "38 天", "quota": "10",
        "desc": "31 天 + 赠送 7 天，先试再说",
        # ★ 首条别再写「每天 N 次采集额度」—— 那行已经由卡片上的 .plan-q 高亮显示，
        #   两边重复一次，客户读到的是"这话怎么说了两遍"。
        "feats": ["全部功能可用", "一码一机，换机免费重发", "到期回到免费版，不锁死"],
        "hot": True,
    },
    {
        "key": "std30", "name": "标准版", "tag": "续费用",
        "price": "29.9", "unit": "月", "quota": "30",
        "desc": "到期续费价，数据不丢",
        "feats": ["全部功能可用", "优先答疑", "到期回到免费版，不锁死"],
        "hot": False,
    },
    {
        "key": "pro100", "name": "专业版", "tag": "工作室",
        "price": "59.9", "unit": "月", "quota": "100",
        "desc": "重度用户 / 矩阵号团队",
        "feats": ["全部功能可用", "优先答疑", "一对一远程协助"],
        "hot": False,
    },
]

FEATURES = [
    ("⬇", "视频下载", "抖音 / 小红书 / 快手 / 视频号链接粘贴即解析，原画质无水印，直接进本地素材库。不调你的采集接口，不花钱。"),
    ("◉", "直播监控录制", "粘贴抖音 / TikTok 直播间链接添加监控，正在直播就自动开录，下播自动停。"
                        "两条链路都不消耗接口额度。"),
    ("≡", "公众号运营", "搜竞对公众号、拉历史文章，阅读点赞与评论一起看，正文存成笔记。"),
    ("⧉", "小红书笔记", "按关键词或博主批量查笔记，全文正文、赞藏评与评论表一次拿到。"),
    ("✎", "音视频转文字", "本地转写不联网，转出的文案直接当选题库和切片脚本。"),
    ("▤", "数据导出", "一键导出 Excel / CSV，直接进表格做分析、写报告。"),
    ("◎", "竞品体检", "对标账号数据一键体检，粉丝 / 互动 / 更新频率差距一目了然。"),
    ("◇", "选题挖掘", "基于真实爆款数据找方向，不靠感觉拍脑袋。"),
    ("▣", "短剧成片", "导入一部剧的素材文件夹，自动分集扫描、语音转写、场景识别，批量产出竖屏成片。"
              "剪辑引擎按需下载（约 275MB），不用不占磁盘。"),
]

STATS = [
    ("3", "个平台内容，一站采集"),
    ("9", "项核心能力，开箱可用"),
    ("24h", "直播监控可以挂着跑"),
    ("5", "个产品持续演进中"),
]

FAQ = [
    ("支持哪些系统？", "Windows 10 / Windows 11（64 位）。Mac 版暂未开放。"),
    ("电脑配置有要求吗？",
     "普通办公本即可，占用很低。音视频转文字如果量大，有独立显卡会快很多。"),
    ("怎么激活？",
     "打开软件 → 左侧「设置」→「授权与激活」→ 把机器码发给作者，收到授权码后粘贴进去点激活。"),
    ("一个授权码能用几台电脑？",
     "一码一机。换电脑把新机器码发给作者，免费重发一张，不重复收费。"),
    ("数据会上传到你们服务器吗？",
     "不会。采集与转写都在你本机完成，软件只在你主动点击时调用接口取数据。"),
    ("有免费版吗？",
     "有，而且不是限时试用。装完就是免费版：<b>抖音 / 小红书 / 快手 / 视频号的视频下载</b>"
     "和<b>抖音 / TikTok 直播录制</b>永久可用，无需激活码、不限制时间、<b>也不限次数</b>。"
     "这几项走的是不花钱的通道，所以没必要拿它收费。"),
    ("免费版和付费版差在哪？",
     "免费版含视频下载与直播录制（抖音 / TikTok 都支持）；数据采集、评论截流、文案仿写、GEO 这些需要调用付费接口，"
     "放在付费版里。视频下载这一项两个版本走同一条通道，成功率没有差别。"),
    ("TikTok 直播能录吗？要另外付费吗？",
     "能录，而且和抖音直播一样<b>永久免费、不限次数、不消耗接口额度</b>。"
     "在「直播监控」里粘贴 TikTok 直播间链接，或直接填 <b>@主播号</b> 就行。"
     "TikTok 直播走本机直连，需要你电脑上的代理能正常访问海外；软件会自动探测常用代理端口，"
     "也能在页面里手动填写并保存。首次添加要过一次浏览器校验（约 10~20 秒），"
     "之后按轮询间隔自动监控、开播自动开录、下播自动停。"),
    ("有免费试用吗？",
     "有。首购体验版 19.9 元得 38 天（31 天 + 赠送 7 天），全部功能可用。"),
    ("产品矩阵里那些「规划中」的什么时候能用？",
     "按开发进度陆续放出，重点功能会先在老客户群里试。想优先用上哪个，加微信告诉我，"
     "需求集中的会往前排。"),
    ("采集额度怎么算？",
     "按「每天调用接口的次数」计：抓列表、查抖音直播间状态这些调用付费接口的动作各算一次，"
     "次日 0 点重置。<b>视频下载、TikTok 直播监控与录制都不算次数</b> "
     "—— 它们不走付费接口，接口欠费也不影响使用。"),
]

# ─────────────────────────────────────────────────────────────────────
#  页面模板
# ─────────────────────────────────────────────────────────────────────

PAGE_TPL = Template(r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
$HEAD
</head>
<body>

<header class="nav" id="nav">
  <div class="nav-in">
    <a class="logo" href="#top"><i>捕</i>$BRAND</a>
    <nav class="nav-links">
      <a href="free.html">免费版</a>
      <a href="#try">功能演示</a>
      <a href="douyin-xiazai.html">视频下载</a>
      <a href="zhibo-luzhi.html">直播录制</a>
      <a href="shuju-caiji.html">数据采集</a>
      <a href="wenan-fangxie.html">文案仿写</a>
      <a href="price.html">价格</a>
    </nav>
    <div class="nav-right">
      <a class="quiet" href="faq.html">常见问题</a>
      <a class="btn btn-key" href="$FREE_DL_URL">下载免费版</a>
      <button class="nav-burger" id="burger" onclick="toggleMenu()" aria-label="打开菜单">
        <i></i><i></i><i></i>
      </button>
    </div>
  </div>
</header>

<nav class="mnav" id="mnav">
  <a href="free.html">免费版（无需激活码）</a>
  <a href="#try" onclick="closeMenu()">功能演示</a>
  <a href="douyin-xiazai.html">抖音视频批量下载</a>
  <a href="zhibo-luzhi.html">抖音 / TikTok 直播录制</a>
  <a href="shuju-caiji.html">数据采集与选题挖掘</a>
  <a href="pinglun-jieliu.html">评论区截流</a>
  <a href="wenan-fangxie.html">短视频文案仿写</a>
  <a href="geo.html">GEO 优化</a>
  <a href="price.html">价格</a>
  <a href="changelog.html">更新日志</a>
  <a href="faq.html">常见问题</a>
  <a href="#download" onclick="closeMenu()">下载 Windows 版</a>
</nav>

<a id="top"></a>

<!-- 滚动刻度导轨：纯装饰、对读屏隐藏。原生滚动条由 site_motion.js
     在确认脚本跑起来之后（html.has-rail）才隐藏 —— 脚本没跑就得留着。 -->
<div class="rail" id="rail" aria-hidden="true">
  <span class="rail-ticks"></span>
  <span class="rail-mag"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>
  <span class="rail-thumb" id="railThumb"><i></i></span>
  <span class="rail-label" id="railLabel"><b></b><span></span></span>
</div>

<!-- Hero -->
<section class="hero">
  <div class="wrap hero-in">
    <span class="pill"><span class="dot"></span>v$VERSION 已发布 · $PILL_NOTE</span>
    <h1>流量捕手<br>抖音 / TikTok 视频下载与直播录制，<em>永久免费</em></h1>
    <p class="sub">粘贴链接就能拿素材，挂着就能录直播，主页作品一键批量入库 ——
      全程在你自己的电脑上完成，数据不出本机。</p>
    <div class="cta">
      <a class="btn btn-dark btn-lg" href="$DL_URL">下载 Windows 版</a>
      <a class="btn btn-ghost btn-lg" href="#products">看看能做什么</a>
    </div>
    <p class="tip">Windows 10 / 11 · 安装包 $SIZE_MB · 首购 ¥19.9 得 38 天</p>
    <div class="hero-shot">
      <div class="stage"><img src="$IMG_VIDEO" alt="$BRAND 主界面"></div>
    </div>
  </div>
</section>

<!-- 数据条 -->
<section class="stats">
  <div class="wrap">
    <div class="stats-in rv">
      $STAT_ITEMS
    </div>
  </div>
</section>

<!-- 免费版：首屏说完「免费」就立刻兑现，别让人往下滑才找到答案 -->
<section id="free">
  <div class="wrap">
    <div class="sec-head rv">
      <span class="kick">免费版</span>
      <h2>两件事永久免费，装完就能用</h2>
      <p>不用激活码、不看天数，试用期结束或者授权到期之后也不会锁死。
        要花钱的是另外那些需要调用接口的活 —— 用不上就不必买。</p>
    </div>
    <div class="vs rv">
      <div class="vs-row hd"><div class="c1">功能</div><div class="c2">免费版</div><div class="c3">付费版</div></div>
      <div class="vs-row">
        <div class="c1">抖音视频下载</div>
        <div class="c2">支持，无水印原画质，不限次数</div>
        <div class="c3">支持，额度按套餐给（每天 10~100 次）</div>
      </div>
      <div class="vs-row">
        <div class="c1">抖音 / TikTok 直播录制</div>
        <div class="c2">支持，在播自动开录；TikTok 需本机代理可用，不消耗额度</div>
        <div class="c3">支持</div>
      </div>
      <div class="vs-row">
        <div class="c1">小红书、快手、视频号下载</div>
        <div class="c2">支持（服务端解析，不用登录，同样不限次数）</div>
        <div class="c3">支持</div>
      </div>
      <div class="vs-row">
        <div class="c1">要不要激活码</div>
        <div class="c2">不需要，装完直接用</div>
        <div class="c3">填一次授权码即可</div>
      </div>
      <div class="vs-row">
        <div class="c1">到期之后</div>
        <div class="c2">下载和录制继续可用，不锁死</div>
        <div class="c3">续费后全部功能恢复</div>
      </div>
      <div class="vs-row">
        <div class="c1">数据采集、选题挖掘</div>
        <div class="c2">不含</div>
        <div class="c3">含，按接口调用扣额度</div>
      </div>
      <div class="vs-row">
        <div class="c1">评论截流、文案仿写</div>
        <div class="c2">不含</div>
        <div class="c3">含</div>
      </div>
    </div>
    <p class="roadmap-note rv">更完整的说明写在<a href="free.html">免费版栏目页</a>，
      也可以直接下载 —— 装完就是免费版。</p>
  </div>
</section>

$EXT_SECTION

<!-- ★ 演示舱 —— 整站唯一能上手点的地方。
     结果行全是静态 HTML：爬虫、审核机器人、关了 JS 的浏览器都看得见，
     脚本只负责把它们逐行「演」出来，不负责生成内容。 -->
<section id="try">
  <div class="wrap">
    <div class="sec-head rv">
      <span class="kick">先试为快</span>
      <h2>不用看介绍，先看它干活</h2>
      <p>下面这块是真的能点的。选一个任务、点一下运行，右边就是它跑完的结果 ——
        不注册、不登录、不发任何请求，纯前端演示。</p>
    </div>

    <div class="deck rv" id="deck">
      <div class="deck-tabs" role="tablist" aria-label="功能演示">
        <button class="deck-tab" type="button" role="tab" data-tab="dl" aria-selected="true"><i>01</i>批量下载视频</button>
        <button class="deck-tab" type="button" role="tab" data-tab="live" aria-selected="false"><i>02</i>直播录制</button>
        <button class="deck-tab" type="button" role="tab" data-tab="collect" aria-selected="false"><i>03</i>数据采集<i>付费</i></button>
        <button class="deck-tab" type="button" role="tab" data-tab="script" aria-selected="false"><i>04</i>文案仿写<i>付费</i></button>
      </div>

      <div class="deck-body">
        <div class="deck-bar">
          <span class="tl"></span><span class="tl"></span><span class="tl"></span>
          <span class="tt">流量捕手 v$VERSION</span>
          <span class="tstat deck-stat">就绪</span>
        </div>

        <div class="deck-stage">
          <!-- 左：任务参数 -->
          <div class="deck-left">
            <div class="deck-swap">

              <div class="deck-pane" data-pane="dl">
                <label class="deck-label" for="in-dl">粘贴视频链接（一条一行，可批量）</label>
                <textarea class="deck-input" id="in-dl" rows="3" readonly>https://v.douyin.com/iRbXkLqP/</textarea>
                <div class="deck-opts">
                  <button class="deck-opt on" type="button">去水印</button>
                  <button class="deck-opt" type="button">最高画质</button>
                  <button class="deck-opt" type="button">用标题命名</button>
                </div>
                <button class="btn btn-key deck-run" type="button" data-run="▶ 开始下载">▶ 开始下载</button>
                <p class="deck-hint">免费功能 · 不限次数 · 装完就能用，不需要激活码</p>
              </div>

              <div class="deck-pane" data-pane="live" hidden>
                <label class="deck-label" for="in-live">直播间链接 / 主播主页</label>
                <input class="deck-input" id="in-live" value="https://live.douyin.com/8412*******" readonly>
                <div class="deck-opts">
                  <button class="deck-opt on" type="button">开播自动录</button>
                  <button class="deck-opt" type="button">每 30 分钟分段</button>
                  <button class="deck-opt" type="button">掉线重连</button>
                </div>
                <button class="btn btn-key deck-run" type="button" data-run="▶ 接上去">▶ 接上去</button>
                <p class="deck-hint">免费功能 · 次数不限 · 挂着就行，不用盯屏幕</p>
              </div>

              <div class="deck-pane" data-pane="collect" hidden>
                <label class="deck-label" for="in-collect">关键词 / 博主主页</label>
                <input class="deck-input" id="in-collect" value="筋膜枪" readonly>
                <div class="deck-opts">
                  <button class="deck-opt on" type="button">近 7 天</button>
                  <button class="deck-opt" type="button">按点赞排序</button>
                  <button class="deck-opt" type="button">只要视频</button>
                </div>
                <button class="btn btn-key deck-run" type="button" data-run="▶ 开始抓取">▶ 开始抓取</button>
                <p class="deck-hint">付费功能 · 按扣次计费 · 走接口，所以要激活码</p>
              </div>

              <div class="deck-pane" data-pane="script" hidden>
                <label class="deck-label" for="in-script">对标视频链接</label>
                <input class="deck-input" id="in-script" value="https://v.douyin.com/kQ2mAta/" readonly>
                <div class="deck-opts">
                  <button class="deck-opt on" type="button">本地转写</button>
                  <button class="deck-opt" type="button">拆解结构</button>
                  <button class="deck-opt" type="button">生成 3 版</button>
                </div>
                <button class="btn btn-key deck-run" type="button" data-run="▶ 开始仿写">▶ 开始仿写</button>
                <p class="deck-hint">付费功能 · 音频在你自己电脑上转，视频不外传</p>
              </div>

            </div>
          </div>

          <!-- 右：结果 -->
          <div class="deck-right">
            <div class="deck-rows">

              <div class="deck-out" data-out="dl">
                <div class="deck-row"><span>01</span><b>2026 新款筋膜枪实测</b><em>1080P · 12.4 MB</em></div>
                <div class="deck-row"><span>02</span><b>工厂车间实地记录</b><em>1080P · 38.1 MB</em></div>
                <div class="deck-row"><span>03</span><b>肩颈放松跟练版</b><em>4K · 66.7 MB</em></div>
                <p class="deck-note">三条排着队下，不用守着。文件名自动取视频标题，
                  原画质、无水印，存到你指定的文件夹。</p>
              </div>

              <div class="deck-out" data-out="live" hidden>
                <div class="deck-row"><span>01</span><b>检测到已在播 · 原画质</b><em>已录 00:42:18</em></div>
                <div class="deck-row"><span>02</span><b>分段文件 #2 已落盘</b><em>00:30:00</em></div>
                <div class="deck-row"><span>03</span><b>网络抖动 · 已自动重连</b><em>未丢帧</em></div>
                <p class="deck-note">设好就挂着，主播开播自动接上，中途掉线自己重连。
                  录完直接是 mp4，拖进剪辑软件不用转码。</p>
              </div>

              <div class="deck-out" data-out="collect" hidden>
                <div class="deck-row"><span>01</span><b>「筋膜枪」相关作品</b><em>214 条</em></div>
                <div class="deck-row"><span>02</span><b>3 个博主主页批量入库</b><em>去重后 187 条</em></div>
                <div class="deck-row"><span>03</span><b>数据进入本地历史库</b><em>可导 CSV</em></div>
                <p class="deck-note">抓回来就是能筛能排的表格，点赞、评论、发布时间都在，
                  一键导出进 Excel 继续做分析。</p>
              </div>

              <div class="deck-out" data-out="script" hidden>
                <div class="deck-row"><span>01</span><b>语音转文字 · 本地 whisper</b><em>04:12</em></div>
                <div class="deck-row"><span>02</span><b>结构拆解完成</b><em>钩子 + 3 段论点 + 引导</em></div>
                <div class="deck-row"><span>03</span><b>同结构文案已生成</b><em>468 字 · 3 版</em></div>
                <p class="deck-note">仿的是节奏和结构，不是照抄句子。音频在本机转写，
                  视频解析完就删，不会传到任何地方。</p>
              </div>

            </div>

            <div class="deck-meter">
              <div class="mb"><div class="mf"></div></div>
              <div class="mt"><span>任务进度</span><span>等待开始</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- 核心能力 -->
<section id="features">
  <div class="wrap">
    <div class="sec-head center rv">
      <span class="kick">核心能力</span>
      <h2>一条流水线，从找料到出稿</h2>
      <p>不是十个互不相干的小工具，是从「发现选题 → 拿到素材 → 转成文案 → 导出分析」
        的一条完整链路。</p>
    </div>
    <div class="fgrid">
      $FEATURE_CARDS
    </div>
  </div>
</section>

<!-- 产品矩阵（路线图）：讲未来，所以排在已经能用的功能后面 -->
<section class="products" id="products">
  <div class="wrap">
    <div class="sec-head center rv">
      <span class="kick">产品矩阵</span>
      <h2>不只是一个下载器</h2>
      <p>$BRAND 是今天的主力产品，后面还有几个正在路上。
        它们共用同一套本地数据与授权，装上就是一套完整的内容工作台。</p>
    </div>
    <div class="pgrid">
      $PRODUCT_CARDS
    </div>
    <p class="roadmap-note rv">产品路线图随开发进度更新，以实际发布为准 ·
      想优先用上哪个，加微信 $WECHAT_ID 告诉我</p>
  </div>
</section>

<!-- 功能详解 -->
<section class="details" id="how">
  <div class="wrap">
    <div class="sec-head rv">
      <span class="kick">功能详解</span>
      <h2>看它怎么把活干掉</h2>
    </div>
    $DETAIL_BLOCKS
  </div>
</section>

<!-- 价格 -->
<section id="pricing">
  <div class="wrap">
    <div class="sec-head center rv">
      <span class="kick">价格</span>
      <h2>一杯咖啡钱，先试一个月</h2>
      <p>$PROMO</p>
    </div>
    <div class="pgrid3">
      $PLAN_CARDS
    </div>
  </div>
</section>

<!-- 下载 -->
<section id="download">
  <div class="wrap">
    <div class="dl-card rv">
      <div class="dl-in">
        <div>
          <h2>下载 $BRAND</h2>
          <p class="lead">当前版本 <b>v$VERSION</b>，更新于 $PUBLISHED，安装包 $SIZE_MB。
            支持 Windows 10 / 11（64 位）。</p>
          <div class="meta">
            <div>SHA256：<code>$SHA256</code></div>
          </div>
          $NOTES_BLOCK
          <div class="wx-note">售后与授权：<a href="javascript:void(0)" onclick="openPay()">扫码加作者微信</a>，或搜索 <b>$WECHAT_ID</b></div>
        </div>
        <div class="dl-side">
          <a class="dl-btn" href="$DL_URL">⬇ 下载 Windows 版</a>
          <div class="dl-hint">在微信里打开时，点右上角「···」<br>选择「在浏览器打开」再下载</div>
          <div class="dl-hint">一码一机 · 换机免费重发 · 包教包会</div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- FAQ：用原生 <details>，不依赖 JS —— 关了脚本也能展开，爬虫也读得到。
     ★ 注意样式要和结构对上：模板给 <details class="qa">，CSS 就必须写
       .qa summary / .qa-a，不能再用「按钮 + JS 折叠层」那套（对不上就是
       页面上出现浏览器默认的 ▶ 三角、没有内边距）。见 _validate_theme.py。 -->
<section id="faq" style="padding-top:0">
  <div class="wrap">
    <div class="faq-in">
      <div class="faq-head rv">
        <span class="kick">常见问题</span>
        <h2>你可能想问</h2>
        <p>没答上的，<a href="javascript:void(0)" onclick="openPay()">扫码加作者个人微信</a>问。</p>
      </div>
      <div class="rv">
        $FAQ_ITEMS
      </div>
    </div>
  </div>
</section>

<!-- 更新日志：内容与安装包里那份同源，改 changelog.json 即可，页面不用动 -->
$CHANGELOG_SECTION

<footer>
  <div class="wrap">
    <div class="f-in">
      <div class="f-brand">
        <a class="logo" href="#top"><i>捕</i>$BRAND</a>
        <p>抖音 / TikTok / 小红书 / 快手 / 视频号内容采集、视频下载、直播录制与本地转写工具。
          数据在你自己的电脑上处理，不上传。</p>
      </div>
      <div class="f-col">
        <h5>产品</h5>
        <a href="#products">$BRAND</a>
        <a href="geo.html">GEO 优化</a>
        <a href="wenan-fangxie.html">文案仿写</a>
        <a href="price.html">价格与套餐</a>
      </div>
      <div class="f-col">
        <h5>全部栏目</h5>
        <a href="douyin-xiazai.html">抖音视频下载</a>
        <a href="zhibo-luzhi.html">抖音 / TikTok 直播录制</a>
        <a href="shuju-caiji.html">数据采集与选题</a>
        <a href="pinglun-jieliu.html">评论区截流</a>
        <a href="wenan-fangxie.html">文案仿写</a>
        <a href="geo.html">GEO 优化</a>
        <a href="price.html">价格与套餐</a>
        <a href="changelog.html">更新日志</a>
        <a href="faq.html">常见问题</a>
        <a href="free.html">免费版</a>
      </div>
      <div class="f-col">
        <h5>支持</h5>
        <a href="#download">下载安装</a>
        <a href="#faq">常见问题</a>
        <a href="#changelog">更新日志</a>
        <a href="#pricing">价格与授权</a>
        <a href="javascript:void(0)" onclick="openPay()">联系作者</a>
      </div>
      <div class="f-col">
        <h5>联系</h5>
        <a href="javascript:void(0)" onclick="openPay()">扫码加作者微信</a>
        <span>微信号：$WECHAT_ID</span>
        <span>版本：v$VERSION</span>
        <span>系统：Windows 10 / 11</span>
      </div>
    </div>
    <div class="f-bot">
      <span>$COPYRIGHT</span>
      <span>工具仅用于合法的内容研究与素材整理，请遵守平台规则</span>
    </div>
  </div>
</footer>

<!-- 联系 / 付款弹窗 -->
<!-- ★ 两个二维码严格分区：上面「收款码」是付款专用（客户付钱靠它），
     下面「个人微信二维码」只用于加好友咨询 / 售后。绝不互相替换，
     否则客户扫了好友码付不了款。 -->
<div class="modal" id="payModal">
  <div class="modal-card">
    <h3>联系作者</h3>
    <p class="m-sub">购买授权、试用咨询、产品建议都走这里</p>
    <div class="qr-label">① 购买授权 · 付款码</div>
    $QR_IMG_BLOCK
    <p class="m-note">$PAY_NOTE</p>
    <div class="qr-label qr-label-2">② 咨询 / 售后 · 加作者个人微信</div>
    $PERSONAL_QR_IMG
    <div class="wx">微信号：<b>$WECHAT_ID</b>（二维码失效时搜索添加）</div>
    <p class="m-note">上面是付款码、下面是加好友码，别扫错；加好友不收费。</p>
    <button class="btn btn-ghost" onclick="closePay()">关闭</button>
  </div>
</div>

<script>
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

function openPay(){
  document.getElementById('payModal').classList.add('on');
  document.body.style.overflow='hidden';
}
function closePay(){
  document.getElementById('payModal').classList.remove('on');
  document.body.style.overflow='';
}
function toggleMenu(){
  document.getElementById('mnav').classList.toggle('on');
  document.getElementById('burger').classList.toggle('on');
}
function closeMenu(){
  document.getElementById('mnav').classList.remove('on');
  document.getElementById('burger').classList.remove('on');
}
window.addEventListener('resize',function(){
  if(window.innerWidth>720) closeMenu();
});
document.addEventListener('keydown',function(e){ if(e.key==='Escape') closePay(); });
document.getElementById('payModal').addEventListener('click',function(e){
  if(e.target===this) closePay();
});

/* 缓存自愈：浏览器/微信若拿到的是旧版页面，自动带时间戳重载到最新版。
   依赖发版时同步生成的同目录 version.json（Cache-Control: no-store）。
   取不到或跨域失败时静默跳过，绝不影响页面本身。 */
(function(){
  try{
    var pageVer="$VERSION";
    fetch("version.json",{cache:"no-store"}).then(function(r){
      return r.ok?r.json():null;
    }).then(function(d){
      if(!d||!d.version) return;
      var latest=String(d.version);
      if(latest===pageVer) return;                       /* 已是最新 */
      var u=new URL(location.href);
      if(u.searchParams.get("_ver")===latest) return;    /* 已为这个版本重载过，防死循环 */
      u.searchParams.set("_r",String(Date.now()));
      u.searchParams.set("_ver",latest);
      location.replace(u.toString());
    }).catch(function(){});
  }catch(e){}
})();
</script>

<!-- 互动层：必须放在最后 —— 它要拿到上面所有 DOM。
     由 tools/site_seo.py 内联，详见那里的说明。 -->
__MOTION__
</body>
</html>
""")

DETAIL_TPL = Template(r"""
      <div class="dl $FLIP rv">
        <div class="txt">
          <div class="kick">$KICK</div>
          <h3>$TITLE</h3>
          <p>$DESC</p>
          <ul>$POINTS</ul>
        </div>
        <div class="pic">
          <div class="stage $TINT"><img src="$IMG" alt="$TITLE"></div>
        </div>
      </div>
""")


# ─────────────────────────────────────────────────────────────────────
#  构建辅助
# ─────────────────────────────────────────────────────────────────────

def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MB" if size else "—"


def read_pay_config() -> dict:
    if PAY_CONFIG_FILE.is_file():
        try:
            return json.loads(PAY_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def load_products() -> list[dict]:
    """产品矩阵数据：优先读 tools/products.json（方便以后新增功能不用改代码）。

    文件不存在时会用内置默认数据写出一个模板，之后直接改 JSON 即可。
    """
    if PRODUCTS_FILE.is_file():
        try:
            data = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                live = sum(1 for p in data if p.get("status") == "live")
                print(f"   已读取 {PRODUCTS_FILE.name}：{len(data)} 个产品（{live} 个已上线）")
                return data
            print("   [警告] products.json 不是非空数组，改用内置数据")
        except Exception as e:  # noqa: BLE001
            print(f"   [警告] products.json 解析失败，改用内置数据：{e}")
    else:
        PRODUCTS_FILE.write_text(
            json.dumps(PRODUCTS_DEFAULT, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   已写出产品矩阵模板 → tools/{PRODUCTS_FILE.name}")
    return PRODUCTS_DEFAULT


def shrink_shot(src: Path, dst: Path, width: int = SHOT_WIDTH) -> bool:
    """把界面截图缩到展示宽度（2x 清晰度），控制体积。"""
    try:
        from PIL import Image
    except ImportError:
        shutil.copyfile(src, dst)
        return True
    try:
        im = Image.open(src).convert("RGB")
    except Exception as e:  # noqa: BLE001
        print(f"   [跳过] 读不了 {src.name}: {e}")
        return False
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(dst, "PNG", optimize=True)
    print(f"   {src.name} → {dst.name}  {im.width}x{im.height}  {dst.stat().st_size/1024:.0f} KB")
    return True


def placeholder_shot(name: str) -> str:
    """界面截图丢失时的内联 SVG 兜底图。

    2026-11-10 客户反馈「官网配图全丢」：官网素材/ui/ 整个目录不在了，
    而老逻辑遇到缺图只是 continue，模板变量被替换成空字符串，
    产物里就是一排 <img src=""> —— 浏览器会把当前页面当图片重新请求，
    显示破图。宁可给一张写着页面名的示意图，也绝不允许吐空 src。

    用 data URI 而不是外部文件：多一个文件就多一条可能漏传的发布链路，
    SVG 只有几 KB，内联最稳。★ 里面不能出现 $，PAGE_TPL 是 string.Template。
    """
    import html as _html
    label = _html.escape(name or "软件界面")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="880" '
        'viewBox="0 0 1400 880">'
        '<rect width="1400" height="880" rx="18" fill="#0B0E14"/>'
        '<rect x="1" y="1" width="1398" height="878" rx="18" fill="none" '
        'stroke="#242A35" stroke-width="2"/>'
        '<path d="M0 64 H1400" stroke="#242A35" stroke-width="2"/>'
        '<circle cx="34" cy="32" r="7" fill="#3A4150"/>'
        '<circle cx="60" cy="32" r="7" fill="#3A4150"/>'
        '<circle cx="86" cy="32" r="7" fill="#3A4150"/>'
        '<text x="120" y="40" fill="#8A93A6" font-size="20" '
        'font-family="sans-serif">流量捕手</text>'
        '<rect x="24" y="96" width="220" height="760" rx="12" fill="#111722" '
        'stroke="#242A35"/>'
    )
    for i in range(7):
        y = 128 + i * 52
        color = "#C8F751" if i == 0 else "#232B39"
        svg += (f'<rect x="48" y="{y}" width="172" height="30" rx="7" '
                f'fill="{color}" opacity="{0.85 if i == 0 else 1}"/>')
    svg += (
        '<rect x="276" y="96" width="1100" height="150" rx="12" fill="#111722" '
        'stroke="#242A35"/>'
        '<rect x="276" y="274" width="530" height="270" rx="12" fill="#111722" '
        'stroke="#242A35"/>'
        '<rect x="836" y="274" width="540" height="270" rx="12" fill="#111722" '
        'stroke="#242A35"/>'
        '<rect x="276" y="572" width="1100" height="284" rx="12" fill="none" '
        'stroke="#C8F751" stroke-width="2" stroke-dasharray="10 8" opacity="0.7"/>'
        f'<text x="700" y="692" fill="#C8F751" font-size="42" text-anchor="middle" '
        f'font-family="sans-serif">{label}</text>'
        '<text x="700" y="742" fill="#8A93A6" font-size="24" text-anchor="middle" '
        'font-family="sans-serif">界面示意图 · 截图素材待补齐</text>'
        '</svg>'
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return "data:image/svg+xml;base64," + b64


def upload_shot(src: Path, client, bucket: str, prefix: str, base: str, key: str,
                ctype: str = "image/png") -> str:
    client.put_object_from_local_file(
        Bucket=bucket, LocalFilePath=str(src), Key=key,
        ContentType=ctype, Metadata={"Cache-Control": SHOT_CACHE},
    )
    print(f"   [OK] 上传 {key}")
    return f"{base}/{key}"


def build_stat_items() -> str:
    return "\n        ".join(
        f'<div class="stat"><b>{v}</b><span>{k}</span></div>' for v, k in STATS
    )


# 「按平台」那张表的数据。三种平台共用同一组列，靠 grid 保证列对齐 ——
# 之前是每块各排各的，列宽随内容浮动，读起来根本不像一张表。
PLATFORM_ROWS: list[dict] = [
    dict(
        name="抖音", glyph="◎", tint="t-indigo",
        tags=["视频下载", "直播录制", "主页采集"],
        pain=["看到好视频想存下来，要么录屏、要么找去水印网站，画质被压一遍",
              "要盯同行的直播，人得守着，录屏时电脑还不能干别的",
              "想看某个博主的全部作品，只能一屏屏翻、一条条记"],
        tool=["粘贴作品链接直接解析下载，原画质无水印，图集也一起拿",
              "把直播间加进监控列表，正在播就自动开录，录完存本地",
              "主页链接一次拉取几百条作品，标题、点赞、评论导出成表"],
        effect=("存素材、盯直播、看主页，从几小时压到几分钟",
                "剩下的时间用在选题和剪辑上"),
    ),
    dict(
        name="小红书", glyph="◈", tint="t-violet",
        tags=["笔记数据采集", "评论意向挖掘"],
        pain=["想统计某个话题下的笔记数据，只能一屏屏手动抄",
              "评论里的真实需求散在各条笔记下面，看不过来",
              "想知道同行最近在发什么，只能靠自己刷"],
        tool=["按关键词批量采集笔记，赞藏评、发布时间一次导出",
              "按意向短语扫评论，把真有需求的挑出来归档",
              "话题下的一次抓完，导出即表格"],
        effect=("做竞品调研不再靠手动抄", "导出即表格"),
    ),
    dict(
        name="快手", glyph="◍", tint="t-green",
        tags=["作品数据采集", "主页批量导出"],
        pain=["想看某个博主的作品数据，只能一条条记",
              "多个平台来回切，数据散在好几个表格里"],
        tool=["按关键词或主页批量采集，作品数据一次导出",
              "抖音、快手的结果字段统一，进同一张表"],
        effect=("两个平台一套字段、一张表", "不用装好几个工具来回倒"),
    ),
]


def build_platform_table() -> str:
    """「按平台」对照表。

    输出是一整个 grid 容器的直接子元素（表头用 display:contents 摊平），
    所以三行的列一定对齐 —— 这是这张表唯一的设计意图。
    """
    out = ['<div class="pl rv">',
           '      <div class="pl-hd"><div>平台</div>'
           '<div>手工做，卡在哪</div><div>流量捕手怎么做</div></div>']
    for p in PLATFORM_ROWS:
        tags = "".join(f"<li>{t}</li>" for t in p["tags"])
        pain = "".join(f"<li>{x}</li>" for x in p["pain"])
        tool = "".join(f"<li>{x}</li>" for x in p["tool"])
        main, tail = p["effect"]
        out.append(f"""      <div class="pl-plat">
        <b><span class="pl-ic {p['tint']}">{p['glyph']}</span>{p['name']}</b>
        <ul>{tags}</ul>
      </div>
      <div class="pl-cell manual">
        <h4>手工做，卡在哪</h4>
        <ul>{pain}</ul>
      </div>
      <div class="pl-cell tool">
        <h4>流量捕手怎么做</h4>
        <ul>{tool}</ul>
      </div>
      <div class="pl-eff">
        <span>实际效果</span>
        <b><em>{main}</em> —— {tail}</b>
      </div>""")
    out.append("    </div>")
    return "\n    ".join(out)


def build_product_cards(products: list[dict]) -> str:
    out = []
    for p in products:
        live = str(p.get("status", "")).lower() == "live"
        cls = "pcard live" if live else "pcard soon"
        badge_cls = "pbadge live" if live else "pbadge"
        pts = "".join(f"<li>{x}</li>" for x in p.get("points", []))
        if live:
            cta = '<a class="pcta" href="#download">立即下载 →</a>'
        else:
            cta = '<span class="pcta soon" onclick="openPay()">想优先用上？加微信留言 →</span>'
        out.append(f"""<article class="{cls} rv">
          <div class="phead">
            <span class="pglyph t-{p.get('tint', 'indigo')}">{p.get('glyph', '◆')}</span>
            <span class="{badge_cls}">{p.get('badge', '')}</span>
          </div>
          <h3>{p.get('name', '')}</h3>
          <p class="ptag">{p.get('tagline', '')}</p>
          <p class="pdesc">{p.get('desc', '')}</p>
          <ul class="ppts">{pts}</ul>
          {cta}
        </article>""")
    return "\n        ".join(out)


def build_feature_cards() -> str:
    out = []
    for icon, title, desc in FEATURES:
        out.append(
            f'<div class="feat rv"><div class="ic">{icon}</div>'
            f"<b>{title}</b><p>{desc}</p></div>"
        )
    return "\n        ".join(out)


def build_ext_section(gif_url: str) -> str:
    """浏览器插件区块（2026-10-24 上新）。

    ★ 位置在「免费版」之后：客户端要装软件，插件只要装个扩展，是更轻的一步。
      这类访客最容易转化 —— 先让他用上，再谈深度功能。
    ★ 动图缺失时整块不出，而不是留一个裂图。首页不能因为少个素材就挂个破图。
    """
    if not gif_url:
        return ""
    return f"""
<!-- ★ 浏览器插件（2026-10-24 上新）-->
<section id="extension" data-rail="浏览器插件">
  <div class="wrap">
    <div class="sec-head rv">
      <span class="kick">上新<i class="newtag">NEW</i></span>
      <h2>不想装软件？装个扩展就行</h2>
      <p>Edge 扩展。装上之后打开抖音，视频页右下角就多一个「下载无水印」，点一下存原片。
        博主主页还能整号批量 —— 往下滚一滚，一次把这个人所有作品存下来。
        不注册、不登录、不要钱。</p>
    </div>
    <div class="extgrid">
      <figure class="extdemo rv">
        <img src="{gif_url}" width="720" height="560" loading="lazy" decoding="async"
             alt="浏览器插件操作演示：在抖音视频页右下角点一下「下载无水印」，按钮变成已保存">
        <figcaption>视频页右下角点一下，存下来的就是无水印原片</figcaption>
      </figure>
      <div class="extside rv">
        <ul class="extpts">
          <li><b>只申请一个下载权限。</b>没有「读取浏览记录」这一项，
            你还在看哪些网页，它根本读不到。</li>
          <li><b>整号批量。</b>博主主页右下角的按钮会变成「下载该作者 N 条」，
            点一下按顺序全存，视频和图集都支持。</li>
          <li><b>不调付费接口。</b>地址直接从抖音页面自己的数据里取，
            不存在「接口一欠费、全体用户都用不了」。</li>
        </ul>
        <a class="btn btn-key extbtn" href="{EXT_ZIP_URL}">下载扩展包 →</a>
        <p class="extnote">解压后：Edge 地址栏输入 <b>edge://extensions</b>
          → 打开左下角「开发人员模式」→ 点「加载解压缩的扩展」，
          选解压出来的那个文件夹。三步装完，以后不用重装。
          Chrome 把第一步换成 <b>chrome://extensions</b> 即可。</p>
        <p class="extnote">Edge 商店版已经上架，
          <a href="{EXT_STORE_URL}" target="_blank" rel="noopener">商店页在这里</a>。
          新上架的扩展，微软那边要铺一段时间才能让所有地区都打开 ——
          点进去是空白页，就直接用上面的扩展包，功能完全一样。</p>
      </div>
    </div>
  </div>
</section>
"""


def build_plan_cards() -> str:
    out = []
    for p in PRICE_PLANS:
        hot = " hot" if p["hot"] else ""
        tagcls = "tag" + ("" if p["hot"] else " g")
        feats = "".join(f"<li>{f}</li>" for f in p["feats"])
        out.append(f"""<div class="plan{hot} rv">
          <span class="{tagcls}">{p['tag']}</span>
          <h3>{p['name']}</h3>
          <div class="psub">{p['desc']}</div>
          <div class="amt"><b>¥{p['price']}</b><span>/ {p['unit']}</span></div>
          <div class="plan-q">每天 {p['quota']} 次采集额度</div>
          <ul>{feats}</ul>
          <button class="btn {'btn-dark' if p['hot'] else 'btn-ghost'}" onclick="openPay()">立即购买</button>
        </div>""")
    return "\n        ".join(out)


def build_detail_blocks(imgs: dict[str, str]) -> str:
    blocks = [
        dict(
            kick="视频下载", title="粘贴链接，原画质直接落盘", flip="", tint="",
            img=imgs.get("视频下载", ""),
            desc="抖音、小红书、快手、微信视频号都能下。粘贴作品链接或分享口令，"
                 "点一下解析，"
                 "无水印原片直接下载到本地，还能自动归档进项目素材库。"
                 "链接是交给服务端去打开解析的 —— 不用登录，也不扣你的接口额度。",
            points=["抖音 / 小红书 / 快手 / 视频号作品链接都能解析", "不用扫码登录，服务端解析不挑本机环境",
                    "支持 Excel 与 TXT 清单批量导入，几百条一次解析完"],
        ),
        dict(
            kick="直播监控", title="粘贴直播间链接，开播自动录", flip="flip", tint="tint-blue",
            img=imgs.get("直播监控", ""),
            desc="把抖音或 TikTok 直播间链接粘进去点「添加监控」，软件会立刻查一次 —— "
                 "正在直播就自动开始录制，不用守着开播时间。多主播可以同时监控，轮询间隔自己调，"
                 "两条链路都不消耗接口额度。",
            points=["添加即检查，在播自动开录", "支持抖音 / TikTok 与多主播并行监控",
                    "直播结束自动停止，录像按主播分目录"],
        ),
        dict(
            kick="批量采集", title="一个主页，一次全拉完", flip="", tint="tint-violet",
            img=imgs.get("数据采集", ""),
            desc="对标账号的主页作品、喜欢列表、收藏列表，一次性批量抓取入库，"
                 "标题、标签、互动数据一起拿到，直接导出表格做分析。",
            points=["主页 / 喜欢 / 收藏三处通吃", "标题标签互动数一并落表",
                    "一键导出 Excel / CSV"],
        ),
        dict(
            kick="本地转写", title="转成文字，就是你的选题库", flip="flip", tint="tint-amber",
            img=imgs.get("音视频转文本", ""),
            desc="视频音频在你自己电脑上转成文字，不联网、不上传。"
                 "对标号的爆款口播转出来，切片脚本、选题方向直接有了。",
            points=["本地转写，隐私数据不出本机", "支持视频 / 音频批量转",
                    "输出纯文本，直接进笔记或表格"],
        ),
    ]
    out = []
    for b in blocks:
        pts = "".join(f"<li>{p}</li>" for p in b["points"])
        out.append(DETAIL_TPL.substitute(
            FLIP=b["flip"], KICK=b["kick"], TITLE=b["title"], TINT=b["tint"],
            DESC=b["desc"], POINTS=pts, IMG=b["img"],
        ))
    return "".join(out)


def build_faq() -> str:
    """FAQ 条目：原生 <details>。

    结构必须和 site_theme.css 里 .qa summary / .qa-a / .arw 对上 ——
    2026-10-17 之前这里输出的是 <details>，而 CSS 写的是另一套按钮式
    折叠（.q / .aw），两边对不上，页面上就是浏览器默认三角 + 答案没样式。
    """
    return "\n        ".join(
        f'<details class="qa"><summary>{q}'
        f'<span class="arw" aria-hidden="true"></span></summary>'
        f'<div class="qa-a">{a}</div></details>'
        for q, a in FAQ
    )


def build_changelog_block(releases: list[dict], version: str) -> str:
    """官网「更新日志」栏目。

    和安装包里那份 更新日志.html 用的是同一个渲染函数，两边长得一模一样 ——
    客户在官网看到的、装完在自己电脑里翻到的，不会是两套说法。
    首页只直接铺最近 6 条，更早的收进折叠：二十几个版本全摊开会长得没边。
    """
    if not releases:
        return ""
    try:
        import build_changelog as _bclog
        body = _bclog.render_entries_html(releases, newest=version, fold_after=3)
    except Exception:                                   # noqa: BLE001
        return ""
    return (
        '<section id="changelog">\n'
        '  <div class="wrap">\n'
        '    <div class="sec-head rv">\n'
        '      <span class="kick">更新日志</span>\n'
        '      <h2>每个版本改了什么</h2>\n'
        f'      <p>共 {len(releases)} 个版本 · 最新版 v{version}。'
        '装好的软件里也有同样一份，安装目录下的「更新日志」随时能翻。</p>\n'
        '    </div>\n'
        f'    <div class="rv">{body}</div>\n'
        '  </div>\n'
        '</section>'
    )


def build_notes(notes: str, version: str) -> str:
    if not notes.strip():
        return ""
    items = [ln.strip() for ln in notes.splitlines() if ln.strip()]
    if len(items) <= 1:
        items = [notes.strip()]
    lis = "".join(f"<li>{it}</li>" for it in items)
    return (f'<div class="notes"><h4>v{version} 更新内容</h4>'
            f"<ol>{lis}</ol></div>")


def build_changelog_block(releases: list[dict], version: str) -> str:
    """官网「更新日志」栏目。

    和安装包里那份 更新日志.html 用的是同一个渲染函数，两边长得一模一样 ——
    客户在官网看到的、装完在自己电脑里翻到的，不会是两份说法。
    首页只直接铺最近 6 条，更早的收进折叠：二十几个版本全摊开会长得没边。
    """
    if not releases:
        return ""
    try:
        import build_changelog as _bclog
        body = _bclog.render_entries_html(releases, newest=version, fold_after=6)
    except Exception:                                   # noqa: BLE001
        return ""
    return (
        '<section id="changelog">\n'
        '  <div class="wrap">\n'
        '    <div class="sec-head rv">\n'
        '      <span class="kick">更新日志</span>\n'
        '      <h2>每个版本改了什么</h2>\n'
        f'      <p>共 {len(releases)} 个版本 · 最新版 v{version}。'
        '装好的软件里也有同样一份，安装目录下的「更新日志」随时能翻。</p>\n'
        '    </div>\n'
        f'    <div class="rv">{body}</div>\n'
        '  </div>\n'
        '</section>'
    )


def build_html(*, version: str, published: str, size: int, url: str, sha256: str,
               notes: str, qr_img: str, pay: dict, imgs: dict[str, str],
               products: list[dict], changelog: list[dict] | None = None,
               free_version: str = "", free_size: int = 0,
               free_url: str = "", ext_gif: str = "") -> str:
    # ★ 素材再缺也不能吐空 src（2026-11-10 官网配图全丢的真因）。
    #   入口处统一补齐，下面 hero / 四个详情块 / 所有 imgs.get 都拿到有效值。
    imgs = {n: (imgs.get(n) or placeholder_shot(n)) for n, _ in SITE_SHOTS}
    promo = "首购 19.9 元即得 38 天（31 天 + 赠送 7 天），到期后续费 29.9 元/月，数据不丢。"
    pay_note = pay.get("pay_note") or "长按二维码 → 识别图中二维码 → 付款"
    qr_block = f'<div class="qr">{qr_img}</div>' if qr_img else ""
    # ★ 个人微信「加好友」码走相对路径引用，不内嵌 base64：
    #   首页已有 <400KB 硬门禁，99KB 图片内嵌会直接撑爆。
    #   构建时由 copy_personal_qr() 复制进 standalone/assets/。
    personal_qr_img = (
        f'<img class="personal-qr" src="{PERSONAL_QR_ASSET}" '
        'alt="作者个人微信二维码，长按识别添加好友" loading="lazy">'
    )
    pill_note = "新增「添加即录制」" if version.startswith("1.2") else "持续更新中"
    try:
        import build_changelog as _bclog
        # CLOG_CSS 是按 f-string 写的（{{ }} 是转义），这里直接插进 HTML
        # 不会经过 f-string，必须先还原成单花括号，否则 CSS 整段失效。
        clog_css = _bclog.CLOG_CSS.replace('{{', '{').replace('}}', '}')
    except Exception:                                   # noqa: BLE001
        clog_css = ""
    # 样式：全站共用 site_theme.css，更新日志那部分由 changelog 模块追加
    import site_seo as _seo
    css = _seo.load_css(clog_css)
    meta = _seo.page_by_file(_seo.INDEX_FILE)
    head = _seo.build_head(
        title=meta["title"],
        desc=meta["desc"],
        page_file=_seo.INDEX_FILE,
        json_ld=[
            _seo.ld_software(version=version or "?", size_mb=_mb(size)),
            _seo.ld_website(),
        ],
        css=css,
    )
    # ★ 先在 Template 里把所有 $变量 替换掉，最后再塞互动层。
    #   反过来会被 JS 里的 $('#id') 整出 ValueError —— 那个 `$` 不是占位符。
    return _seo.inject_motion(PAGE_TPL.substitute(
        HEAD=head,
        # 更新日志那块自带样式，由 build_changelog 提供；取不到就留空（页面照常能出）。
        CLOG_CSS=clog_css,
        CHANGELOG_SECTION=build_changelog_block(changelog or [], version or "?"),
        EXT_SECTION=build_ext_section(ext_gif),
        BRAND=BRAND,
        VERSION=version or "?",
        PILL_NOTE=pill_note,
        PUBLISHED=published or "",
        SIZE_MB=_mb(size),
        DL_URL=url,
        # 免费版是独立安装包（tools/release_free.py 产出）。没有免费版信息时
        # 退回完整版链接与版本号 —— 页面照常能出，不会因为缺文件就 KeyError。
        FREE_VER=free_version or (version or "?"),
        FREE_SIZE_MB=_mb(free_size) if free_size else _mb(size),
        FREE_DL_URL=free_url or url,
        SHA256=sha256 or "—",
        STAT_ITEMS=build_stat_items(),
        PRODUCT_CARDS=build_product_cards(products),
        PLATFORM_TABLE=build_platform_table(),
        FEATURE_CARDS=build_feature_cards(),
        DETAIL_BLOCKS=build_detail_blocks(imgs),
        PLAN_CARDS=build_plan_cards(),
        FAQ_ITEMS=build_faq(),
        NOTES_BLOCK=build_notes(notes, version),
        QR_IMG_BLOCK=qr_block,
        PERSONAL_QR_IMG=personal_qr_img,
        PAY_NOTE=pay_note,
        WECHAT_ID=WECHAT_ID,
        PROMO=promo,
        COPYRIGHT=f"© 2026 {BRAND}工作室",
        IMG_VIDEO=imgs.get("视频下载", ""),
    ))



# ─────────────────────────────────────────────────────────────────────
#  主流程
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="生成流量捕手官网并上传 COS")
    ap.add_argument("--local", action="store_true", help="只本地生成（图片用本地副本），不上传")
    ap.add_argument("--no-upload", action="store_true", help="生成 + 上传图片，但不上传 HTML")
    args = ap.parse_args()

    vj = INSTALLER_OUT / "version.json"
    if not vj.is_file():
        print(f"[错误] 找不到 {vj}")
        return 1
    data = json.loads(vj.read_text(encoding="utf-8"))
    url = (data.get("win") or {}).get("url", "")
    if not url:
        print("[错误] version.json 里没有下载地址")
        return 1

    # 更新日志：每次生成页面都自己同步一次，这样不管发版流程里谁先跑谁后跑，
    # 栏目永远有内容（踩过"页面比数据先生成，栏目整块空白"的坑）。
    changelog: list[dict] = []
    try:
        import build_changelog as _bclog
        changelog, _info = _bclog.build()
        print(f"更新日志：{len(changelog)} 个版本，最新 v{_info.get('latest', '?')}")
    except Exception as exc:                            # noqa: BLE001
        print(f"[警告] 更新日志生成失败：{exc}（官网栏目会空着）")

    STANDALONE.mkdir(parents=True, exist_ok=True)
    pay = read_pay_config()

    print("=== 0) 产品矩阵数据 ===")
    products = load_products()

    print("=== 1) 处理界面截图 ===")
    local_imgs: dict[str, str] = {}
    prep: list[tuple[str, Path, str]] = []      # (名称, 待上传临时文件, COS 文件名)

    for name, cos_key in SITE_SHOTS:
        src = SHOTS_DIR / f"{name}.png"
        if not src.is_file():
            # 原来是 continue —— local_imgs 里没这个键，页面就吐 <img src="">。
            # 现在给内联示意图，并在 build_html 入口再兜一层。
            print(f"   [缺失] {src} → 用内联示意图兜底（不会再出现破图）")
            local_imgs[name] = placeholder_shot(name)
            continue
        if args.local:
            dst = STANDALONE / cos_key
            shrink_shot(src, dst)
            local_imgs[name] = cos_key
        else:
            tmp = INSTALLER_OUT / f".tmp-{cos_key}"
            shrink_shot(src, tmp)
            prep.append((name, tmp, cos_key))

    client = None
    base = ""
    bucket = region = cfg_prefix = ""
    if not args.local:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        client = CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                       SecretKey=cfg["secret_key"], Scheme="https"))
        cfg_prefix = str(cfg.get("prefix") or "trafficcatcher").strip("/")
        base = (cfg.get("cdn_domain") or
                f"https://{cfg['bucket']}.cos.{cfg['region']}.myqcloud.com").rstrip("/")
        bucket, region = cfg["bucket"], cfg["region"]
        print("   上传到 COS：")
        for name, tmp, cos_key in prep:
            key = f"{cfg_prefix}/site/{cos_key}"
            local_imgs[name] = upload_shot(tmp, client, bucket, cfg_prefix, base, key)
            tmp.unlink(missing_ok=True)

    print("=== 1.5) 插件演示动图 ===")
    ext_gif = ""
    if not EXT_GIF_SRC.is_file():
        print(f"   [提示] 没找到 {EXT_GIF_SRC.name}，插件区块只出文字、不出动图")
    elif args.local:
        shutil.copy2(EXT_GIF_SRC, STANDALONE / EXT_GIF_KEY)
        ext_gif = EXT_GIF_KEY
        print(f"   [OK] 复制 {EXT_GIF_KEY}（本地模式）")
    else:
        ext_gif = upload_shot(
            EXT_GIF_SRC, client, bucket, cfg_prefix, base,
            f"{cfg_prefix}/site/{EXT_GIF_KEY}", ctype="image/gif",
        )

    print("=== 1.8) 背景音乐歌单 ===")
    # 音乐是外链静态文件（不内联，免得把首页撑到 400KB 门禁之外）。
    # 歌单来自 官网素材/bgm/playlist.json；site_seo.inject_bgm() 会把顺序
    # 与中文歌名内联进每一页，运行时不再请求 json。三首都漏传时播放器
    # 还有纯 Web Audio 合成曲兜底 —— 任何部署渠道都不会点了没声音。
    bgm_dir = ROOT / "官网素材" / "bgm"
    assets = STANDALONE / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    mf = bgm_dir / "playlist.json"
    if mf.is_file():
        shutil.copy2(mf, assets / "playlist.json")
        print("   [OK] playlist.json → standalone/assets/（线上核对用）")
    else:
        print("   [提示] 没有 playlist.json，播放器改用内置合成曲")
    copied = 0
    for src in sorted(bgm_dir.glob("bgm-*.mp3")):
        shutil.copy2(src, assets / src.name)
        copied += 1
        print(f"   [OK] {src.name}（{src.stat().st_size / 1024 / 1024:.2f} MB）"
              "→ standalone/assets/")
    if not copied:
        print("   [提示] 歌单里没有 mp3，播放器改用内置合成曲（无需外部文件）")

    # ★ 1.9) 作者个人微信「加好友」二维码 → standalone/assets/。
    #   与下面第 2 步的「收款码」是两张不同的图、两种用途，绝不互相覆盖。
    print("=== 1.9) 作者个人微信二维码 ===")
    if PERSONAL_QR_SRC.is_file():
        shutil.copy2(PERSONAL_QR_SRC, STANDALONE / PERSONAL_QR_ASSET)
        print(f"   [OK] {PERSONAL_QR_SRC.name} → standalone/{PERSONAL_QR_ASSET}")
    else:
        print(f"   [警告] 缺 {PERSONAL_QR_SRC}，联系弹窗里的加好友码会显示不出来")

    print("=== 2) 收款码 ===")
    qr_img = ""
    qr_local = pay.get("wechat_qr") or ""
    qp = (ROOT / qr_local) if qr_local else None
    if qp and qp.is_file():
        b64 = base64.b64encode(qp.read_bytes()).decode()
        ext = qp.suffix.lstrip(".").lower()
        ext = "jpeg" if ext in ("jpg", "jpeg") else ext
        qr_img = f'<img src="data:image/{ext};base64,{b64}" alt="微信收款码">'
        print(f"   已内嵌收款码（{qp.stat().st_size/1024:.0f} KB）")
    else:
        print("   [警告] 没配收款码，购买按钮点了会是空弹窗")

    # 免费版产物信息（可选）：由 tools/release_free.py 生成。没有就退回完整版，
    # 首页下载区不会因为这个文件缺失而空着或报错。
    free_ver, free_size, free_url = "", 0, ""
    vj_free = INSTALLER_OUT / "version_free.json"
    if vj_free.is_file():
        try:
            _fv = json.loads(vj_free.read_text(encoding="utf-8"))
            _fw = _fv.get("win") or {}
            free_ver = str(_fv.get("version") or "")
            free_size = int(_fw.get("size") or 0)
            free_url = str(_fw.get("url") or "")
        except Exception as exc:                        # noqa: BLE001
            print(f"   [警告] version_free.json 读取失败：{exc}")
    if not free_url:
        print("   [提示] 还没有免费版安装包（version_free.json），"
              "首页免费版按钮暂指向完整版")

    print("=== 3) 生成页面 ===")
    html = build_html(
        version=data.get("version", "?"),
        published=data.get("published", ""),
        size=int((data.get("win") or {}).get("size", 0)),
        url=url,
        sha256=(data.get("win") or {}).get("sha256", ""),
        notes=data.get("notes", ""),
        qr_img=qr_img,
        pay=pay,
        imgs=local_imgs,
        products=products,
        changelog=changelog,
        free_version=free_ver,
        free_size=free_size,
        free_url=free_url,
        ext_gif=ext_gif,
    )
    out = STANDALONE / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"   已生成 {out}（{len(html)/1024:.0f} KB）")

    # 页面「缓存自愈」用的比对文件：必须和页面同目录，这样才能同源相对路径取到
    # （跨域 fetch 会被浏览器拦；同源则两边托管都可用）。
    vjson_text = vj.read_text(encoding="utf-8")
    (STANDALONE / "version.json").write_text(vjson_text, encoding="utf-8")
    print(f"   已生成 {STANDALONE / 'version.json'}")

    # 强制登录的「云端保险丝」：客户端每次启动会拉一次这个文件。
    # require_login 改成 false 就能全量关掉强制登录，不用发版 —— 万一登录
    # 链路在某个环境大面积失败，这是唯一的远程急救手段。
    # 已存在则保留原值（作者可能刚手动关掉，不能被脚本覆盖回 true）。
    gate = STANDALONE / "gate.json"
    if not gate.exists():
        gate.write_text(json.dumps({"require_login": True}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(f"   已生成 {gate}（默认 require_login=true）")

    if args.local:
        print("\n[本地模式] 预览：直接双击上面的 index.html 即可")
        return 0

    if args.no_upload:
        print("\n[--no-upload] 已跳过 HTML 上传")
        return 0

    print("=== 4) 上传 HTML → COS ===")
    # ★ 必须传到「桶根 index.html」：COS 静态网站的索引文档就是它，
    #   传到 trafficcatcher/index.html 前端访问不到（2026-10-10 踩过，
    #   页面看起来"没更新"，实际是新旧两份文件并存）。
    client.put_object_from_local_file(
        Bucket=bucket, LocalFilePath=str(out), Key="index.html",
        ContentType="text/html; charset=utf-8",
        Metadata={"Cache-Control": "no-store, max-age=0"},
    )
    # 桶根再放一份 version.json：官网首页的相对路径 fetch 才能命中（同源）。
    # 必须 no-store，否则"缓存自愈"自己也会被缓存住，等于失效。
    client.put_object_from_local_file(
        Bucket=bucket, LocalFilePath=str(STANDALONE / "version.json"),
        Key="version.json", ContentType="application/json; charset=utf-8",
        Metadata={"Cache-Control": "no-store, max-age=0"},
    )
    print("   [OK] 已上传 → version.json（页面缓存自愈用）")
    # 云端保险丝 gate.json（强制登录开关），同样放桶根、同样 no-store
    gate = STANDALONE / "gate.json"
    if gate.exists():
        client.put_object_from_local_file(
            Bucket=bucket, LocalFilePath=str(gate),
            Key="gate.json", ContentType="application/json; charset=utf-8",
            Metadata={"Cache-Control": "no-store, max-age=0"},
        )
        print("   [OK] 已上传 → gate.json（强制登录云端开关）")
    site = f"https://{bucket}.cos-website.{region}.myqcloud.com/"
    print("   [OK] 已上传 → index.html")
    print(f"\n官网地址（微信里可直接打开）：\n   {site}")
    print("\n⚠️  记得再发布一次 WorkBuddy 旧域名（installer_output/standalone），"
          "否则旧链接还是上一版。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
