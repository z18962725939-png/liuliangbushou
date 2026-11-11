#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""官网 SEO 共享模块：页面注册表 / head 生成 / 面包屑 / robots / sitemap / 结构化数据。

为什么单独抽出来：
    首页由 `build_site.py` 生成，栏目子页由 `build_subpages.py` 生成。
    两边如果各自写一套 title / canonical / JSON-LD，很快就会漂移 ——
    搜索引擎最忌讳同一站点里 canonical 指向不一致、结构化数据格式五花八门。
    所以要有一个"唯一说法"的模块，两边都调它。

★ 为什么官网在百度搜不到（2026-10-15 实测诊断，结论写在这里以免下次重犯）：
    1) 站点只有 index.html 一个 URL，正文里几乎没有用户会搜的词
       （原 H1 是"把抖音里最费时间的事交给一个软件"，没有品牌词也没有关键词）。
    2) 没有 robots.txt 和 sitemap.xml（实测两个域名都返回 404），
       爬虫没有入口清单，全靠自己撞。
    3) 同一份页面挂在三个域名上（自有域名 / COS / WorkBuddy 旧域名），
       属重复内容，百度只会挑一个收录、权重还分散 —— 必须用 canonical 指定正主。
    4) 域名注册时间短，本身就在百度的观察期，更依赖主动提交。

    因此本模块做的四件事：多页面覆盖不同搜索词 / 补 robots+sitemap /
    全站 canonical 指向自有域名 / 全站带结构化数据。

★ 改页面清单时只动 PAGES，别在生成脚本里硬编码文件名。
"""
from __future__ import annotations

import json
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
STANDALONE = ROOT / "installer_output" / "standalone"
THEME_CSS = TOOLS / "site_theme.css"
MOTION_JS = TOOLS / "site_motion.js"

BRAND = "流量捕手"

# ★ 权威域名：全站 canonical / sitemap 都指向这里。
# 自有中文域名（流量捕手.cn），对外的正主。COS 与旧域名是镜像，靠 canonical 归权。
CANONICAL_HOST = "https://xn--kpuzw91vr71b.cn"

# 站点地图里声明的其他镜像（只用于人工核对，不进 sitemap）
MIRROR_HOSTS = [
    "https://liuliangbushou-1258850027.cos-website.ap-guangzhou.myqcloud.com",
    "https://trafficcatcher-download.app.workbuddy.host",
]

# ─────────────────────────────────────────────────────────────────────
#  页面注册表：新增栏目只改这里（★ 唯一清单）
#  file   —— 输出文件名（用拼音，中文文件名上传 COS 有编码坑）
#  title  —— 60 字符内，必须同时含品牌词与主关键词
#  desc   —— 80~120 字，写给搜索结果摘要看的
#  h1     —— 页面主标题，同样要含关键词
# ─────────────────────────────────────────────────────────────────────
PAGES: list[dict] = [
    {
        "file": "index.html",
        "must": ["流量捕手", "抖音", "下载"],
        "nav": "首页",
        "title": "流量捕手 - 抖音视频批量下载与数据采集软件",
        "desc": "流量捕手是一款装在 Windows 上的抖音内容工作台：视频无水印批量下载、直播间自动录制、"
                "博主主页作品批量采集、本地转写文案，数据全程在自己电脑上处理。",
        "h1": "流量捕手：抖音视频批量下载、直播录制与数据采集，一个软件做完",
    },
    {
        "file": "free.html",
        # ★ 这一页是引流入口，主打「免费」相关搜索词。
        #   must 里的词必须真的出现在正文里，_validate_site_seo.py 会逐页核对。
        "must": ["免费", "视频下载", "直播录制", "无需激活"],
        "nav": "免费版",
        "title": "抖音视频下载与直播录制免费软件 - 无需激活码 | 流量捕手",
        "desc": "流量捕手免费版：抖音视频无水印下载、直播间自动录制，两项功能永久免费，"
                "无需激活码、不限使用时长和次数，装完就能用。采集、截流、仿写等按需付费解锁。",
        "h1": "抖音视频下载与直播录制，免费版永久可用",
    },
    {
        "file": "douyin-xiazai.html",
        "must": ["抖音视频", "批量下载", "无水印", "原画质"],
        "nav": "视频下载",
        "title": "抖音视频批量下载工具 - 无水印原画质保存 | 流量捕手",
        "desc": "粘贴抖音链接即可解析下载，支持无水印原画质、图集与主页作品批量下载，"
                "不用一条条点、不用开代理。Windows 桌面软件，批量入库到本地素材库。",
        "h1": "抖音视频批量下载：粘贴链接就拿素材，不用一条条点",
    },
    {
        "file": "zhibo-luzhi.html",
        "must": ["直播录制", "开播", "自动"],
        "nav": "直播录制",
        "title": "抖音直播录制软件 - 在播自动开录，不用守着 | 流量捕手",
        "desc": "把直播间链接加进监控列表，软件定时查一次，正在播就自动开始录制，"
                "录完直接存成本地视频文件。适合长期盯对标直播间、留存直播素材。",
        "h1": "抖音直播录制：加一次链接，之后开播就自动录",
    },
    {
        "file": "shuju-caiji.html",
        "must": ["数据采集", "选题", "导出"],
        "nav": "数据采集",
        "title": "抖音小红书数据采集工具 - 对标账号与选题挖掘 | 流量捕手",
        "desc": "按关键词、博主主页、喜欢收藏列表批量采集作品数据，标题、点赞、评论、"
                "发布时间一次导出；低粉高赞选题挖掘，快速找到值得抄结构的爆款。",
        "h1": "抖音小红书数据采集：主页作品批量入库，选题一屏看完",
    },
    {
        "file": "pinglun-jieliu.html",
        "must": ["评论", "意向", "短语"],
        "nav": "评论截流",
        "title": "评论区截流工具 - 按意向词挖高意向评论 | 流量捕手",
        "desc": "按「怎么买、多少钱、求链接」这类完整意向短语扫描评论区，"
                "把真正有购买意向的评论挑出来归档跟进，避免泛词误伤。",
        "h1": "评论区截流：用完整意向短语，把真想买的人挑出来",
    },
    {
        "file": "wenan-fangxie.html",
        "must": ["文案", "仿写", "结构", "体裁"],
        "nav": "文案仿写",
        "title": "短视频文案仿写 - 拆解爆款结构生成口播稿 | 流量捕手",
        "desc": "丢一条对标视频链接进去，自动转写、拆结构、提钩子、按体裁节奏仿写，"
                "直接产出可以照着开拍的口播稿与分镜表，仿结构不仿措辞。",
        "h1": "短视频文案仿写：拆开爆款看结构，产出你自己的口播稿",
    },
    {
        "file": "geo.html",
        "must": ["GEO", "AI", "提及"],
        "nav": "GEO 优化",
        "title": "GEO 优化 - AI 搜索品牌可见度诊断 | 流量捕手",
        "desc": "用一批真实提问词去问主流 AI，检测你的品牌在回答里有没有被提到、"
                "被引用了哪些来源，给出可执行的优化清单。请求失败会单独标出，不计入提及率。",
        "h1": "GEO 优化：看看 AI 被问到时，会不会提到你",
    },
    {
        "file": "price.html",
        "must": ["19.9", "授权", "额度", "一码一机"],
        "nav": "价格",
        "title": "流量捕手价格与授权 - 19.9 元起，一码一机",
        "desc": "体验版 19.9 元 38 天每天 10 次，标准版 29.9 元/月每天 30 次，"
                "专业版 59.9 元/月每天 100 次。全部功能可用，一码一机，换机免费重发。",
        "h1": "价格与授权：先花一杯咖啡钱试一个月",
    },
    {
        "file": "changelog.html",
        "must": ["更新日志", "版本"],
        "nav": "更新日志",
        "title": "流量捕手更新日志 - 每个版本改了什么",
        "desc": "流量捕手全部版本的更新记录，从最初版本到现在，"
                "每个版本新增了什么功能、修了什么问题，按时间倒序完整列出。",
        "h1": "更新日志：每个版本改了什么",
    },
    {
        "file": "faq.html",
        "must": ["授权", "额度", "机器码"],
        "nav": "常见问题",
        "title": "流量捕手常见问题 - 下载、安装、授权、额度一次说清",
        "desc": "流量捕手怎么下载安装、授权码怎么用、额度怎么算、换电脑怎么办、"
                "支持哪些平台，常见问题在这里一次说清。答不上的直接加微信问。",
        "h1": "常见问题：下载、安装、授权、额度一次说清",
    },
    {
        # ★ 这一页不为 SEO，是为了上架：Edge / Chrome 商店都要填一个
        #   「可访问的隐私策略网址」。挂在自有域名下，别用三方文档链接 ——
        #   商店审核会看这个域名跟开发者是不是对得上。
        "file": "privacy.html",
        "must": ["隐私", "权限", "上传"],
        "nav": "隐私策略",
        "title": "隐私策略 - 流量捕手与浏览器插件怎么处理你的数据",
        "desc": "流量捕手的数据处理说明：采集到的数据只存在你自己电脑上，不上传服务器；"
                "浏览器插件只申请下载权限，不读取浏览记录。这里逐条说清我们碰了什么、没碰什么。",
        "h1": "隐私策略：数据留在你自己的电脑上",
    },
]

INDEX_FILE = "index.html"


def load_css(extra: str = "") -> str:
    """读取共享主题样式。首页与所有子页共用，改一处全站生效。"""
    css = THEME_CSS.read_text(encoding="utf-8").strip()
    if extra:
        # ★ CLOG_CSS 是按 f-string 写的（{{ }} 只是转义），这里直接拼进 HTML
        #   不会再过 f-string —— 不还原就会把 {{margin-top:4px}} 原样吐出去，
        #   整段 CSS 失效（2026-11-01 全站 12 页都中招，自检才抓出来）。
        css += "\n" + extra.strip().replace("{{", "{").replace("}}", "}")
    return css


# ─────────────────────────────────────────────────────────────────────
#  全站背景音乐播放器（2026-11-10）
#  ─────────────────────────────────────────────────────────────────
#  设计取舍，改之前先读完：
#  1. 进页面就自动起播（客户 2026-11-10 明确要求）。浏览器对「无手势
#     自动出声」有限制，所以四级兜底：直接 play → 静音 play 成功后恢复
#     音量 → 微信 JSBridge 的 getNetworkType 回调免手势起播 → 仍被拦就
#     等首次轻点/触摸/滑动立刻补播，并在手机端浮出「轻点页面开启音乐」。
#     右下角按钮保留为手动暂停 / 恢复，手机端和网页端同一套。
#  2. 三首歌走外链 assets/bgm-1..3.mp3（preload="none"），不内联 base64 ——
#     首页有 <400KB 的硬门禁，内联音乐会把移动端拖死。同一时刻 audio
#     元素只挂当前一首的 src，一次只下载一首（约 3MB）。
#  3. 官网有三个发布渠道（自有域名 / COS 镜像 / 旧域名应用），历史上
#     漏传静态文件不是一次两次。某首 404 会先跳下一首，三首全废才落
#     纯 Web Audio 合成曲兜底：点按钮依然有音乐，页面自包含。
#  4. 歌单顺序与中文歌名在构建期从 playlist.json 内联进页面，运行时
#     不再请求 json；最后一首播完回到第一首＝整单循环。翻页时把曲目
#     序号与播放秒数写进 localStorage，新页面接着播，不会从头再来。
#  5. 全部用 id，不写 class —— tools/_validate_theme.py 会钉死
#     「HTML 里每个 class 都必须在 site_theme.css 里有定义」。
#  6. 不用 $ 符号，免得和 PAGE_TPL 的 string.Template 撞车。
# ─────────────────────────────────────────────────────────────────
BGM_BLOCK = r"""
<style>
#tcbgm{position:fixed;right:22px;bottom:22px;z-index:9990;display:flex;align-items:center;gap:10px;pointer-events:none}
#tcbgm-btn{pointer-events:auto;width:48px;height:48px;border-radius:50%;cursor:pointer;display:grid;place-items:center;
  border:1px solid #3A4150;background:#111722;color:#C8F751;
  box-shadow:0 8px 28px rgba(0,0,0,.45);transition:transform .25s ease,border-color .25s ease,background .25s ease}
#tcbgm-btn:hover{transform:translateY(-2px);border-color:#C8F751}
#tcbgm-btn:focus-visible{outline:2px solid #C8F751;outline-offset:3px}
#tcbgm-btn[data-on="1"]{border-color:#C8F751;background:#1B2510;
  box-shadow:0 0 0 4px rgba(200,247,81,.12),0 8px 28px rgba(0,0,0,.45)}
#tcbgm-btn[data-on="1"] svg{animation:tcbgmPulse 2.6s ease-in-out infinite}
#tcbgm-btn svg{width:22px;height:22px;fill:currentColor;display:block}
#tcbgm-tip{pointer-events:none;max-width:0;overflow:hidden;white-space:nowrap;opacity:0;
  padding:7px 0;border-radius:8px;background:#111722;border:1px solid #3A4150;
  color:#C8D0DD;font-size:12px;letter-spacing:.04em;transition:max-width .3s ease,opacity .3s ease,padding .3s ease}
#tcbgm:hover #tcbgm-tip,#tcbgm:focus-within #tcbgm-tip{max-width:240px;opacity:1;padding:7px 12px}
#tcbgm-next{pointer-events:auto;width:34px;height:34px;border-radius:50%;cursor:pointer;display:grid;place-items:center;
  border:1px solid #3A4150;background:#111722;color:#C8D0DD;
  box-shadow:0 6px 20px rgba(0,0,0,.35);transition:transform .25s ease,border-color .25s ease,color .25s ease}
#tcbgm-next:hover{transform:translateY(-2px);border-color:#C8F751;color:#C8F751}
#tcbgm-next:focus-visible{outline:2px solid #C8F751;outline-offset:3px}
#tcbgm-next svg{width:16px;height:16px;fill:currentColor;display:block}
@keyframes tcbgmPulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.62;transform:scale(.88)}}
@media (max-width:720px){#tcbgm{right:14px;bottom:14px}#tcbgm-btn{width:44px;height:44px}
  #tcbgm-next{width:32px;height:32px}#tcbgm-tip{display:none}}
@media (prefers-reduced-motion:reduce){#tcbgm-btn,#tcbgm-next,#tcbgm-tip,#tcbgm-btn svg{animation:none!important;transition:none!important}}
</style>
<div id="tcbgm">
  <span id="tcbgm-tip">背景音乐 · 3 首循环</span>
  <button id="tcbgm-next" type="button" aria-label="下一首" title="下一首">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 5v14l9-7-9-7zm10 0v14h2V5h-2z"/></svg>
  </button>
  <button id="tcbgm-btn" type="button" data-on="0" aria-pressed="false"
          aria-label="播放背景音乐" title="播放 / 暂停背景音乐">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6zm-2 16a2 2 0 1 1 0-4 2 2 0 0 1 0 4z"/></svg>
  </button>
</div>
<audio id="tcbgm-audio" preload="none" playsinline webkit-playsinline></audio>
<script>
(function(){
  "use strict";
  var BTN=document.getElementById("tcbgm-btn");
  var NXT=document.getElementById("tcbgm-next");
  var TIP=document.getElementById("tcbgm-tip");
  var AUD=document.getElementById("tcbgm-audio");
  if(!BTN||!AUD){return;}
  // 歌单由构建期从 官网素材/bgm/playlist.json 内联进来：三个部署渠道
  // 即使漏传 playlist.json 也照样能播，页面不依赖运行时再发一次请求。
  var TRACKS=__BGM_TRACKS__;
  var KEY="tcbgm_on",KIDX="tcbgm_idx",KPOS="tcbgm_pos";
  var on=false,idx=0,seekTo=0,failRun=0,pending=false,autoPending=false,lastSave=0;
  var ctx=null,master=null,timer=null,step=0,nextAt=0,synthOn=false;
  // 进页面就自动起播：被浏览器拦时先静音重试，再不行等第一次轻点补播。
  var retryArmed=false,mutedTried=false,userStop=false,dropRetry=null,bridgeTried=false;
  var CHORDS=[[50,53,57,60,64],[46,50,53,57],[41,45,48,52],[48,52,55,62]];
  var BASS=[38,34,29,36];

  function count(){return TRACKS.length;}
  function label(){
    if(!count()){return "背景音乐";}
    var t=TRACKS[idx]||TRACKS[0];
    return t.title+"  "+(idx+1)+"/"+count();
  }
  function tip(s){if(TIP){TIP.textContent=s;}}
  // 手机端 CSS 把 #tcbgm-tip 整块隐藏了；自动播放真被拦住时，用行内样式
  // 把「轻点页面开启音乐」露出来，否则手机用户根本不知道该点哪里。
  function hint(s){
    if(!TIP){return;}
    TIP.textContent=s;
    try{
      TIP.style.display="block";
      TIP.style.maxWidth="240px";
      TIP.style.opacity="1";
      TIP.style.padding="7px 12px";
    }catch(e){}
  }
  function clearHint(){
    if(!TIP){return;}
    try{
      TIP.style.display="";TIP.style.maxWidth="";
      TIP.style.opacity="";TIP.style.padding="";
    }catch(e){}
  }
  function paint(v){
    on=v;
    BTN.setAttribute("data-on",v?"1":"0");
    BTN.setAttribute("aria-pressed",v?"true":"false");
    BTN.setAttribute("aria-label",v?"暂停背景音乐":"播放背景音乐");
    BTN.setAttribute("title",v?("暂停 · "+label()):("播放 · "+label()));
    tip(v?("正在播放 · "+label()):("背景音乐 · "+(count()||1)+" 首循环 · 点击播放"));
  }
  function remember(v){try{localStorage.setItem(KEY,v?"1":"0");}catch(e){}}
  function savedOn(){try{return localStorage.getItem(KEY)==="1";}catch(e){return false;}}
  function num(k){
    try{var n=parseFloat(localStorage.getItem(k));return (isFinite(n)&&n>=0)?n:0;}
    catch(e){return 0;}
  }
  function saveIdx(){try{localStorage.setItem(KIDX,String(idx));}catch(e){}}
  function savePos(){
    if(!count()){return;}
    try{
      localStorage.setItem(KIDX,String(idx));
      localStorage.setItem(KPOS,String(AUD.currentTime||0));
    }catch(e){}
  }

  function mtof(m){return 440*Math.pow(2,(m-69)/12);}
  function note(t,dur,midi,amp,type){
    try{
      var o=ctx.createOscillator(),g=ctx.createGain();
      o.type=type;o.frequency.value=mtof(midi);
      g.gain.setValueAtTime(0.0001,t);
      g.gain.exponentialRampToValueAtTime(amp,t+0.02);
      g.gain.exponentialRampToValueAtTime(0.0001,t+dur);
      o.connect(g);g.connect(master);o.start(t);o.stop(t+dur+0.03);
    }catch(e){}
  }
  function schedule(){
    if(!ctx||!synthOn){return;}
    var horizon=ctx.currentTime+1.2;
    while(nextAt<horizon){
      var bar=Math.floor(step/8)%4,k=step%8,chord=CHORDS[bar];
      note(nextAt,0.55,chord[k%chord.length]+((k===3||k===7)?12:0),0.075,"triangle");
      if(k===0||k===4){
        note(nextAt,1.2,BASS[bar],0.095,"sine");
        note(nextAt,2.5,chord[0],0.030,"sine");
      }
      step=(step+1)%32;
      nextAt+=0.375;
    }
  }
  function startSynth(){
    if(synthOn){return true;}
    try{
      var AC=window.AudioContext||window.webkitAudioContext;
      if(!AC){return false;}
      if(!ctx){
        ctx=new AC();master=ctx.createGain();master.gain.value=0.5;
        master.connect(ctx.destination);
      }
      if(ctx.state==="suspended"){ctx.resume();}
      step=0;nextAt=ctx.currentTime+0.06;
      synthOn=true;schedule();
      timer=setInterval(schedule,350);
      return true;
    }catch(e){return false;}
  }
  function stopSynth(){
    synthOn=false;
    if(timer){clearInterval(timer);timer=null;}
    if(ctx){try{ctx.suspend();}catch(e){}}
  }
  function synthFlow(){
    if(startSynth()){paint(true);remember(true);armRetry();}
    else{paint(false);tip("当前浏览器不支持背景音乐");}
  }
  function stopAll(){
    userStop=true;                     // 手动暂停后，本页不再抢播
    if(dropRetry){var d=dropRetry;dropRetry=null;d();}
    try{AUD.pause();}catch(e){}
    stopSynth();paint(false);remember(false);
  }
  // 只把「当前这一首」挂到 audio 元素上。preload="none" + 单 src 意味着
  // 客户点一次最多下载一首（约 3MB），三首不会一起偷跑手机流量。
  function setSource(){
    if(!count()){return false;}
    var want=TRACKS[idx].file;
    if((AUD.getAttribute("src")||"").indexOf(want)<0){AUD.setAttribute("src",want);}
    AUD.volume=0.42;
    return true;
  }
  function playOK(){
    pending=false;mutedTried=false;retryArmed=false;
    if(dropRetry){var d=dropRetry;dropRetry=null;d();}
    try{AUD.muted=false;}catch(e){}
    clearHint();paint(true);remember(true);
  }
  function playCurrent(pos,auto){
    if(!count()){if(!auto){synthFlow();}return;}
    pending=true;autoPending=!!auto;
    seekTo=pos>0?pos:0;
    if(!setSource()){pending=false;return;}
    saveIdx();
    var p;
    try{p=AUD.play();}catch(e){onFail({name:"NotAllowedError"});return;}
    if(p&&p.then){
      p.then(playOK).catch(function(err){onFail(err);});
    }else{playOK();}
  }
  // 某一首 404 / 解码失败时先试着跳下一首；绕一圈全废才落内置合成曲。
  // error 事件和 play() 的 promise 可能都来，靠 pending 去重。
  function onFail(err){
    if(!pending){return;}
    pending=false;
    if(err&&err.name==="NotAllowedError"){
      // 浏览器拦了「无手势自动播放」：先静音起播再把音量还回来，
      // 还不行就等第一次轻点 / 微信 JSBridge 就绪自动补播。
      if(!mutedTried&&!userStop){mutedTried=true;tryMuted();return;}
      armRetry();wxKick();paint(false);hint("轻点页面开启音乐 · "+label());return;
    }
    if(autoPending){armRetry();paint(false);return;}
    failRun++;
    if(count()>1&&failRun<count()){
      idx=(idx+1)%count();saveIdx();playCurrent(0,false);return;
    }
    synthFlow();
  }
  // 桌面浏览器几乎都放行「静音自动播放」：无声起播成功后再恢复音量。
  function tryMuted(){
    if(userStop){return;}
    pending=true;autoPending=false;
    try{AUD.muted=true;}catch(e){}
    var p;
    try{p=AUD.play();}catch(e){onFail({name:"NotAllowedError"});return;}
    if(p&&p.then){
      p.then(function(){
        pending=false;
        try{AUD.muted=false;AUD.volume=0.42;}catch(e){}
        if(AUD.paused){armRetry();paint(false);}
        else{playOK();}
      }).catch(function(){
        try{AUD.muted=false;}catch(e){}
        onFail({name:"NotAllowedError"});
      });
    }else{
      try{AUD.muted=false;}catch(e){}
      if(AUD.paused){armRetry();paint(false);}else{playOK();}
    }
  }
  // 微信内置浏览器的经典免手势起播：JSBridge 的 getNetworkType 回调被
  // 微信当作可信上下文，在回调里 play() 常常能直接出声（iOS 微信尤其关键）。
  function wxKick(){
    if(bridgeTried||on||userStop||pending){return;}
    bridgeTried=true;
    try{
      var B=window.WeixinJSBridge;
      if(!B||!B.invoke){bridgeTried=false;return;}
      B.invoke("getNetworkType",{},function(){
        if(on||userStop||synthOn||pending){return;}
        if(dropRetry){var d=dropRetry;dropRetry=null;d();}
        retryArmed=false;mutedTried=false;
        playCurrent(num(KPOS),false);
      });
    }catch(e){bridgeTried=false;}
  }
  // 自动播放被拦时，任何一次轻点 / 触摸 / 滑动都立刻把音乐补上。
  function armRetry(){
    if(retryArmed||userStop){return;}
    retryArmed=true;
    var events=["pointerdown","touchstart","click","keydown","wheel","scroll"];
    function drop(){
      events.forEach(function(t){
        try{document.removeEventListener(t,kick,true);}catch(e){}
      });
    }
    function kick(){
      unlock();
      if(on||userStop||synthOn){retryArmed=false;dropRetry=null;drop();return;}
      retryArmed=false;dropRetry=null;drop();
      mutedTried=false;
      playCurrent(num(KPOS),false);
    }
    dropRetry=drop;
    events.forEach(function(t){
      try{document.addEventListener(t,kick,{capture:true,passive:true});}
      catch(e){try{document.addEventListener(t,kick,true);}catch(e2){}}
    });
  }
  // ★ 页面一加载就尝试出声，不等用户点播放按钮。
  function boot(){
    idx=count()?Math.min(Math.floor(num(KIDX)),count()-1):0;
    if(count()){playCurrent(num(KPOS),false);}
    else{synthFlow();}
  }
  function nextTrack(auto){
    if(!count()){synthFlow();return;}
    idx=(idx+1)%count();          // ★ 最后一首播完回到第 1 首＝整单循环
    saveIdx();failRun=0;
    playCurrent(0,auto);
  }
  AUD.addEventListener("ended",function(){
    if(on&&count()){nextTrack(true);}
  },false);
  AUD.addEventListener("error",function(){onFail(null);},false);
  AUD.addEventListener("loadedmetadata",function(){
    if(seekTo>0){
      try{if(!isFinite(AUD.duration)||seekTo<AUD.duration-1){AUD.currentTime=seekTo;}}catch(e){}
      seekTo=0;
    }
  },false);
  AUD.addEventListener("timeupdate",function(){
    var now=Date.now();
    if(on&&now-lastSave>3000){lastSave=now;savePos();}
  },false);
  BTN.addEventListener("click",function(){
    if(on){savePos();stopAll();return;}
    userStop=false;mutedTried=false;failRun=0;
    idx=count()?Math.min(Math.floor(num(KIDX)),count()-1):0;
    playCurrent(num(KPOS),false);
  },false);
  if(NXT){
    NXT.addEventListener("click",function(){
      userStop=false;mutedTried=false;
      if(!count()){synthFlow();return;}
      idx=(idx+1)%count();saveIdx();failRun=0;
      playCurrent(0,false);      // 点「下一首」本身就是用户手势，可以直接出声
    },false);
  }
  // iOS / 微信：JSBridge 就绪时争取免手势起播；首次触摸恢复被挂起的音频上下文
  function unlock(){if(ctx&&ctx.state==="suspended"){try{ctx.resume();}catch(e){}}}
  document.addEventListener("WeixinJSBridgeReady",wxKick,false);
  document.addEventListener("WeixinJSBridgeReady",unlock,false);
  document.addEventListener("touchstart",unlock,{once:true,passive:true});
  document.addEventListener("visibilitychange",function(){
    if(document.hidden){
      if(on){savePos();}
      try{AUD.pause();}catch(e){}
      if(synthOn&&ctx){try{ctx.suspend();}catch(e){}}
    }else{
      if(userStop){return;}
      unlock();
      if(on&&count()){var p=AUD.play();if(p&&p.catch){p.catch(function(){});}}
      else if(!synthOn){
        if(dropRetry){var d=dropRetry;dropRetry=null;d();}
        retryArmed=false;mutedTried=false;
        if(count()){playCurrent(num(KPOS),false);}
      }
    }
  },false);
  window.addEventListener("pagehide",function(){if(on){savePos();}},false);
  // ★ 绝不在这里清掉 localStorage，否则用户翻两页音乐就"忘"了。
  // 现在不看「以前有没有点过播放」：每一页载入都直接起播，被浏览器自动播放
  // 策略拦住就走静音重试 + 首次轻点补播，微信里等 JSBridge 就绪自己补。
  boot();
  // 微信里 JSBridge 可能比脚本先就绪、事件已经过去了：主动再探一次。
  try{if(window.WeixinJSBridge){wxKick();}}catch(e){}
})();
</script>
"""


def bgm_tracks() -> list[dict]:
    """读歌单，只返回文件确实存在的曲目。

    歌单唯一编辑入口是 `官网素材/bgm/playlist.json`（由 import_site_bgm.py
    生成）。构建期把顺序与中文歌名内联进每一页，运行时不再请求 json ——
    三个部署渠道少一个请求就少一个「漏传文件就哑掉」的坑。
    找不到歌单时回落到旧单曲 bgm.mp3，再找不到才让播放器跑合成曲。
    """
    manifest_names = (
        ROOT / "官网素材" / "bgm" / "playlist.json",
        STANDALONE / "assets" / "playlist.json",
    )
    for mf in manifest_names:
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            items = data.get("tracks", []) if isinstance(data, dict) else data
        except Exception:                                     # noqa: BLE001
            continue
        out: list[dict] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("file") or "").strip()
            if not name.lower().endswith(".mp3"):
                continue
            rel = name if name.startswith("assets/") else "assets/" + name
            if not ((STANDALONE / rel).is_file() or
                    (ROOT / "官网素材" / "bgm" / Path(name).name).is_file()):
                continue
            out.append({"file": rel, "title": str(it.get("title") or "背景音乐")})
        if out:
            return out
    if (STANDALONE / "assets" / "bgm.mp3").is_file():
        return [{"file": "assets/bgm.mp3", "title": "背景音乐"}]
    return []


def inject_bgm(html: str, audio_src: str | None = None,
               tracks: list[dict] | None = None) -> str:
    """给页面塞三曲循环播放器。首页与全部子页共用，一次改动全站生效。

    tracks 传 None 时自动探测 playlist.json；显式传 [] 或 audio_src=""
    表示无外部音频，播放器只跑内置合成曲 —— 任何渠道漏传也不会哑掉。
    """
    if 'id="tcbgm"' in html:                 # 防重复注入
        return html
    if tracks is None:
        if audio_src == "":
            tracks = []
        elif audio_src:
            tracks = [{"file": audio_src, "title": "背景音乐"}]
        else:
            tracks = bgm_tracks()
    payload = json.dumps(tracks, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")   # 歌名里带 </script> 也不能提前闭合
    block = BGM_BLOCK.replace("__BGM_TRACKS__", payload)
    if "</body>" in html:
        return html.replace("</body>", block + "\n</body>", 1)
    return html + block


def inject_motion(html: str) -> str:
    """把互动层内联进页面，并顺带注入全站背景音乐播放器。

    ★ 为什么用占位符替换而不是模板变量：
      页面模板是 string.Template，它会把 `$xxx` 当占位符，
      而 JS 里到处是 `$('#id')` 这种写法 —— 交给 substitute 会直接
      ValueError。所以在 substitute 跑完之后，再用 __MOTION__ 这个
      不带 `$` 的标记做一次纯替换，两边互不干扰。

    ★ 为什么内联而不是外链一个 motion.js：
      官网要在微信内置浏览器、百度蜘蛛、各种国产壳里都能跑。
      多一个请求就多一个失败点（子页上传漏文件的老坑就是这么来的），
      内联之后页面自包含，缺什么都不怕。代价是每页多十几 KB，值。

    ★ 首页和全部子页都经过这里，所以音乐播放器挂在这一层最省事，
      不需要去改 12 个模板。没有 __MOTION__ 的页面也照样有音乐。
    """
    if "__MOTION__" in html:
        if MOTION_JS.is_file():
            js = MOTION_JS.read_text(encoding="utf-8").strip()
            html = html.replace("__MOTION__", "<script>\n" + js + "\n</script>")
        else:
            html = html.replace("__MOTION__", "")
    return inject_bgm(html)


def page_by_file(name: str) -> dict:
    for p in PAGES:
        if p["file"] == name:
            return p
    raise KeyError(f"页面注册表里没有 {name}，请先加进 site_seo.PAGES")


def abs_url(path_file: str) -> str:
    """本站绝对路径。首页就是根，子页是 /文件名。"""
    if path_file == INDEX_FILE:
        return CANONICAL_HOST + "/"
    return f"{CANONICAL_HOST}/{path_file}"


# ─────────────────────────────────────────────────────────────────────
#  head
# ─────────────────────────────────────────────────────────────────────

_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
    "%3Crect width='64' height='64' rx='14' fill='%230B0B0F'/%3E"
    "%3Ccircle cx='32' cy='32' r='17' fill='none' stroke='%234F46E5' stroke-width='4'/%3E"
    "%3Ccircle cx='32' cy='32' r='6' fill='%234F46E5'/%3E%3C/svg%3E"
)


def _esc(s: str) -> str:
    """属性值转义。手写实体替换会把自己替换自己，用标准库最稳。"""
    from html import escape
    return escape(str(s), quote=True)


def _verify_meta() -> str:
    """百度站长平台的归属验证标签。

    站长平台给的那行 meta 由人工贴进 tools/baidu_verify.txt 即可，
    全站自动带上 —— 验证不通过的话，后面 sitemap 提交和主动推送都用不了。
    只给 content 值也认（有些入口只给一串码）。
    """
    f = TOOLS / "baidu_verify.txt"
    if not f.is_file():
        return ""
    try:
        line = (f.read_text(encoding="utf-8", errors="replace").strip()
                .splitlines() or [""])[0].strip()
    except Exception:                                    # noqa: BLE001
        return ""
    if not line:
        return ""
    if line.startswith("<meta"):
        return "\n" + line
    return f'\n<meta name="baidu-site-verification" content="{_esc(line)}">'


def build_head(*, title: str, desc: str, page_file: str,
               json_ld: list[str] | None = None, css: str = "") -> str:
    """生成完整 <head>。canonical 一律指向自有域名，避免三个镜像互相分权。"""
    url = abs_url(page_file)
    ld = ""
    if json_ld:
        ld = "\n" + "\n".join(
            '<script type="application/ld+json">' + b + "</script>" for b in json_ld
        )
    return (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        f'<meta name="description" content="{_esc(desc)}">\n'
        f'<link rel="canonical" href="{_esc(url)}">\n'
        '<meta name="theme-color" content="#0B0B0F">\n'
        '<meta name="format-detection" content="telephone=no">'
        + _verify_meta() + "\n"
        '<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="{_esc(BRAND)}">\n'
        f'<meta property="og:title" content="{_esc(title)}">\n'
        f'<meta property="og:description" content="{_esc(desc)}">\n'
        f'<meta property="og:url" content="{_esc(url)}">\n'
        '<meta name="twitter:card" content="summary">\n'
        f'<link rel="icon" href="{_FAVICON}">\n'
        "<!-- 防缓存：微信 X5 / 国产浏览器常忽略 HTTP 头，靠这几条兜底 -->\n"
        '<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\n'
        '<meta http-equiv="Pragma" content="no-cache">\n'
        '<meta http-equiv="Expires" content="0">\n'
        f"<style>\n{css}\n</style>"
        + ld
    )


# ─────────────────────────────────────────────────────────────────────
#  结构化数据
# ─────────────────────────────────────────────────────────────────────

def ld_software(*, version: str, size_mb: str) -> str:
    """首页用：告诉搜索引擎这是一个什么软件（影响搜索结果的富摘要）。"""
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": BRAND,
        "applicationCategory": "BusinessApplication",
        "applicationSubCategory": "抖音内容采集与素材管理工具",
        "operatingSystem": "Windows 10 / 11",
        "softwareVersion": version,
        "fileSize": size_mb,
        "inLanguage": "zh-CN",
        "url": abs_url(INDEX_FILE),
        "description": page_by_file(INDEX_FILE)["desc"],
        "offers": {
            "@type": "Offer",
            "price": "19.9",
            "priceCurrency": "CNY",
            "description": "体验版 19.9 元 / 38 天，每天 10 次采集额度",
        },
    }
    return json.dumps(data, ensure_ascii=False)


def ld_website() -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": BRAND,
        "url": CANONICAL_HOST + "/",
        "inLanguage": "zh-CN",
        "description": page_by_file(INDEX_FILE)["desc"],
    }
    return json.dumps(data, ensure_ascii=False)


def ld_breadcrumb(items: list[tuple[str, str]]) -> str:
    """items: [(名称, 页面文件名 or '' 表示当前页)]"""
    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [],
    }
    for i, (name, f) in enumerate(items, start=1):
        node = {"@type": "ListItem", "position": i, "name": name}
        if f:
            node["item"] = abs_url(f)
        data["itemListElement"].append(node)
    return json.dumps(data, ensure_ascii=False)


def ld_faq(qa: list[tuple[str, str]]) -> str:
    """FAQ 页用。百度对 FAQPage 有独立展现，能多占一块搜索结果。"""
    data = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in qa
        ],
    }
    return json.dumps(data, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────
#  站点地图与 robots
# ─────────────────────────────────────────────────────────────────────

def render_sitemap(lastmod: str) -> str:
    """sitemap.xml。优先级：首页最高，主功能页次之。"""
    prio = {
        "index.html": "1.0",
        "douyin-xiazai.html": "0.9",
        "zhibo-luzhi.html": "0.8",
        "shuju-caiji.html": "0.8",
        "price.html": "0.8",
        "pinglun-jieliu.html": "0.7",
        "wenan-fangxie.html": "0.7",
        "faq.html": "0.6",
        "geo.html": "0.6",
        "changelog.html": "0.5",
    }
    rows = []
    for p in PAGES:
        rows.append(
            "  <url>\n"
            f"    <loc>{abs_url(p['file'])}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>{prio.get(p['file'], '0.5')}</priority>\n"
            "  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )


def render_robots() -> str:
    """robots.txt：放行全部，并把 sitemap 地址交给爬虫。

    ★ 注意：这里不能写 Disallow: /，曾经有站点因为默认模板带这条整站不被收录。
    """
    lines = [
        "# 流量捕手官网 robots.txt",
        "# 全站开放抓取；镜像域名请用 canonical 归权，不在此声明。",
        "User-agent: *",
        "Allow: /",
        "",
        "# 不需要被抓取的：版本接口与门禁配置（不是页面内容）",
        "Disallow: /gate.json",
        "Disallow: /version.json",
        "",
        f"Sitemap: {CANONICAL_HOST}/sitemap.xml",
    ]
    return "\n".join(lines) + "\n"


def render_baidu_verify_hint() -> str:
    """给人工看的提交清单（不写进页面，只在脚本输出里提示）。"""
    return (
        "百度收录提交（需手工做一次，约 3 分钟）：\n"
        "  1. 打开 https://ziyuan.baidu.com/ 用百度账号登录\n"
        "  2. 添加网站：{host}\n"
        "  3. 验证站点（推荐 HTML 标签验证，把给的 meta 标签发我，我加进全站 head）\n"
        "  4. 左栏「资源提交 → 普通收录 → 手动提交」把 sitemap.xml 地址粘进去：\n"
        "     {host}/sitemap.xml\n"
        "  5. 同页面「API 提交」拿到 token 给我，我写进 tools/seo_push.py 做发版自动推送\n"
    ).format(host=CANONICAL_HOST)
