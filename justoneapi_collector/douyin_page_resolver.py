#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音作品页「浏览器渲染」解析器 —— 2026-10-24 起的主解析通道。

★ 为什么必须要有它（这是整条下载功能的生死线，别删）
──────────────────────────────────────────────────────────────
2026-10-24 客户报「两个版本都用不了」，实测定因：

  1. **页面 SSR 里没有作品数据了**。抓 `https://www.douyin.com/video/<id>`
     返回的 HTML 里 `aweme_id` 出现 0 次、`play_addr` 0 次；
     `_ROUTER_DATA.loaderData` 只剩 `video_layout: null` 和一堆请求上下文
     （ua / query / abParams），`videoInfoRes` / `item_list` 整个没了。
     以前 `_extract_video_from_html()` 抠的就是这些键，所以它必然全失败。
  2. **接口全面上 Argus 签名**。不带签名直接打：
       - `www.douyin.com/aweme/v1/web/aweme/detail/` → 403
         `Blocked by ArgusSecurityPlugin Signature Not Found`
       - `www.iesdouyin.com/web/api/v2/aweme/iteminfo/` → 11110 `encrypt_data_miss`
     Cookie 是完整的（sessionid / sid_tt / ttwid / odin_tt 48 个字段都在），
     跟"没登录""Cookie 过期"无关 —— 别再去让客户扫码了，没用。
  3. **但作品本身没删**：真实浏览器打开页面，`<title>` 是正确的作品标题，
     而且渲染出来的 `<source src="https://v26-web.douyinvod.com/.../video/tos/...">`
     **带着页面自己算好的播放地址**，实测下载得到 22 MB 的 `video/mp4` 原片，
     `ftyp` 校验通过，**连 Cookie 都不需要**。

结论：纯 HTTP 抓页面的路已经死了；**让真浏览器把页面渲染出来、再把
`<video>` 里的地址读出来**是唯一还活着的免费通道。浏览器自己的 JS 带着
合规的签名去请求，我们只在旁边读结果，不碰签名算法。

★ 为什么用独立 profile + headless
  不复用「设置 → 抖音登录」那个 profile：同一个 user-data-dir 只能被一个
  浏览器进程占用，复用会把客户正开着的标签页导航走（客户会以为软件在乱跳）。
  代价是没有登录态，但公开作品不需要登录 —— 实测全新 profile 就能拿到原片。
  真要下私密/粉丝可见的作品，那是账号权限问题，这里也解决不了。

★ 为什么要记得"连续失败就停"
  批量导入动辄上百条，每条都要起页面等渲染（约 3~8 秒）。一旦抖音开始
  拦无头浏览器（比如返回验证码页），不停下来就是几百次 × 20 秒的死等，
  客户只会看到进度条卡住。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

# 页面里取数据的脚本。
# ★ 只认 douyinvod.com / /video/tos/ 上的地址：那是作品原片（tos 原始文件）。
#   抖音页面里还有别的 <video>，比如客户端引导素材
#   `lf-douyin-pc-web.douyinstatic.com/.../uuu_265.mp4` —— 实测第一版就抓到了它，
#   结果 id 对了、地址却是推广片。用域名白名单过滤掉，别指望"面积最大的那个"
#   （引导层的 video 常常是全屏，面积比作品还大）。
_JS_READ = r"""
(() => {
  const isWork = (u) => /douyinvod\.com|\/video\/tos\//i.test(u || '');
  const srcs = [];
  const push = (u) => { if (u && /^https?:/i.test(u) && isWork(u) && srcs.indexOf(u) < 0) srcs.push(u); };
  for (const v of document.querySelectorAll('video')) {
    for (const s of v.querySelectorAll('source')) push(s.src || s.getAttribute('src'));
    push(v.currentSrc); push(v.src);
  }
  const og = (p) => { const m = document.querySelector('meta[property="' + p + '"]'); return m ? m.content : ''; };
  return JSON.stringify({
    srcs: srcs,
    title: document.title || '',
    href: location.href || '',
    cover: og('og:image'),
    hasVerify: !!document.querySelector('#captcha_container, .captcha_verify_container, [class*="captcha"]')
  });
})()
"""

# 打开页面之前先注入的"去自动化痕迹"脚本。
# 带 --remote-debugging-port 启动时，浏览器会把 navigator.webdriver 置为 true，
# 抖音据此认定是机器人，作品播放器不初始化（<video> 永远不出 source，
# 只会渲染客户端引导素材）。启动参数里的 --disable-blink-features 是第一道，
# 这里再盖一遍，双保险 —— 少一道就整条通道静默失效，很难查。
_JS_STEALTH = r"""
Object.defineProperty(navigator, 'webdriver', { get: () => false });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
try {
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
} catch (e) {}
"""

# 作品页 URL 里的 ID（视频 / 图文 / 图集）
_ID_RE = re.compile(r"/(?:video|note|slides)/(\d{15,25})")


class DouyinPageResolver:
    """渲染抖音作品页并读出播放地址。一个实例可反复用，用完 close()。"""

    # ★ 2026-10-25 客户现场（导入 182 条、失败 168 条）改了三个数：
    #   MAX_FAILS 3 → 6：一整批共用一个实例，3 条坏链就把整批废掉，代价太大。
    #   到上限不再是"永久停用"，而是**冷却一段时间后自动恢复**（见 _note_fail）。
    #   WAIT_SECONDS 20 → 14：链接打不开时能更早认出来，批量不再一条干等 20 秒。
    MAX_FAILS = 6
    COOLDOWN_SECONDS = 45.0    # 每次冷却停这么久（风控时硬冲只会更糟）
    MAX_COOLDOWNS = 3          # 冷却 3 轮还救不回来，才真正停用
    WAIT_SECONDS = 14.0        # 单条视频页最多等多久把 <source> 等出来

    def __init__(self, profile_dir: str | Path = "", headless: bool = True,
                 cookie: str = ""):
        base = Path(profile_dir) if profile_dir else (
            Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "browser_profile")
        # 独立子目录：不跟客户的登录 profile 抢（抢了会把人家正在看的页面导航走）
        self.profile_dir = base / "_resolve"
        self.headless = headless
        self._crawler: Any = None
        self._start_error = ""
        self._fails = 0
        self._cooldowns = 0
        self._disabled = False
        self.last_reason = ""
        # ★ 登录态 Cookie：独立 profile 本来是干净的，不注入的话抖音会把
        #   作品页重定向到 /jingxuan，整条渲染通道白跑（详见 add_cookies）。
        self.cookie = (cookie or "").strip()
        self._cookie_injected = False

    # ------------------------------------------------------------ 生命周期
    def _ensure(self) -> bool:
        """懒启动浏览器。启动失败只记原因，不抛异常 —— 调用方还要继续报别的错。"""
        if self._crawler is not None:
            return True
        if self._disabled:
            return False
        try:
            from .douyin_cdp_crawler import DouyinCDPCrawler
        except Exception as exc:                    # noqa: BLE001
            self._start_error = f"加载浏览器组件失败：{exc}"
            self._disabled = True
            return False
        try:
            crawler = DouyinCDPCrawler(self.profile_dir, headless=self.headless,
                                       timeout=25.0)
            ok, msg = crawler.start()
        except Exception as exc:                    # noqa: BLE001
            self._start_error = f"启动浏览器失败：{exc}"
            self._disabled = True
            return False
        if not ok:
            # 没装 Edge/Chrome 是环境问题，重试也没用，直接停用并记住原因
            self._start_error = msg or "浏览器启动失败"
            self._disabled = True
            return False
        self._crawler = crawler
        # 必须在任何导航之前注册，否则第一个页面还是带着 webdriver 标记加载的
        crawler.add_init_script(_JS_STEALTH)
        # ★ 2026-10-25：登录态也要在第一次导航之前注进去。
        #   没有它，抖音会把 /video/<id> 重定向到 /jingxuan（精选首页），
        #   页面照常加载、但永远读不到播放地址 —— 客户那批 168 条失败就是这个。
        #   注入失败不阻断（页面可能本来就公开），但把原因记下来，别静默。
        if self.cookie and not self._cookie_injected:
            try:
                n = crawler.add_cookies(self.cookie)
            except Exception:                   # noqa: BLE001
                n = 0
            self._cookie_injected = n > 0
            if not self._cookie_injected:
                self.last_reason = "登录态注入失败（Cookie 格式异常），本次只能靠公开页面"
            else:
                # ★★ CDP 的 setCookies 是**异步**的：命令返回时 Cookie 往往
                #    还没落进会话存储。紧接着就去翻作品页，请求发出那一刻
                #    浏览器还是"未登录"，抖音照旧把它重定向到 /jingxuan。
                #    实测：注入后立刻解析 → 全批失败；先打开一次站点根、
                #    给 Cookie 落地的机会 → 同一批立刻正常。
                #    这里用 open_url（只发 Page.navigate，不等首屏加载完），
                #    不能用 navigate —— 那个要等 readyState 到 complete，
                #    抖音首页的图片推荐流会让人白等 8~10 秒。
                try:
                    crawler.open_url("https://www.douyin.com/")
                    time.sleep(1.5)
                except Exception:               # noqa: BLE001
                    pass
        return True

    def close(self) -> None:
        if self._crawler is not None:
            try:
                self._crawler.stop(kill_browser=False)
            except Exception:                       # noqa: BLE001
                pass
            self._crawler = None

    @property
    def disabled(self) -> bool:
        return self._disabled

    @property
    def start_error(self) -> str:
        return self._start_error

    # ------------------------------------------------------------ 解析
    def resolve(self, page_url: str) -> tuple[dict | None, str]:
        """渲染一个作品页，返回 (数据字典, 失败原因)。

        数据字典的键与 VideoInfo 同名字段 + `srcs`（全部候选播放地址），
        由调用方自己组装 VideoInfo —— 这里不 import VideoInfo，
        免得和 video_downloader 形成循环依赖（也让字段增删互不牵连）。

        两条腿，先接口后 DOM：
          ① `capture_aweme_detail()` —— 截页面自己发的详情接口响应。数据最全
             （描述、作者、点赞、封面、图集一次到位），是主通道。
          ② 读 `<video>` 里的 `<source>` —— 万一某个版本页面不发 detail 请求，
             至少还能拿到播放地址（标题靠 document.title）。
        """
        if self._disabled:
            return None, self._start_error or "浏览器渲染通道已停用"
        if not self._ensure():
            return None, self._start_error or "浏览器渲染通道不可用"

        aweme, why = self._crawler.capture_aweme_detail(page_url)
        if aweme:
            self._fails = 0
            return self._aweme_to_dict(aweme, page_url), ""

        # ★★★ 2026-10-25「抢窗口」：capture 那一路没截到详情响应时，
        #   页面往往**还活着**。真机时序（实测三次都一样）：
        #       0~5.0 秒  href 停在 /video/<id>，标题是真实作品名
        #       5.5 秒    被重定向到 /jingxuan（精选首页）
        #   也就是说数据窗口只有 5 秒。旧代码在这里调 `_resolve_from_dom()`，
        #   而它是先 `navigate()` 重新加载页面 —— 等于把这个窗口亲手关掉，
        #   重新加载后早已被踢走，于是每条都"没读到播放地址"。
        #   现在改成：**先不导航**，就地读一次 DOM，读到就走。
        snap = self._crawler.evaluate(_JS_READ, timeout=6)
        if snap:
            try:
                early = json.loads(snap)
            except Exception:                   # noqa: BLE001
                early = {}
            if early.get("srcs"):
                self._fails = 0
                return self._dom_to_dict(early, page_url), ""

        # 已经明确"打不开"的，别再花十几秒去读 DOM —— 结果不会变，
        # 只是让批量导入多等一轮。
        # ★ soft=True：这是**链接或登录态**的问题，不是通道故障，不计入熔断。
        #   否则一批旧链接就能把好链接一起拖死（2026-10-25 客户现场）。
        if ("失效" in (why or "") or "首页" in (why or "")
                or "重定向" in (why or "")):
            self._note_fail(soft=True)
            return None, why

        # 接口没截到，退到读 DOM（它内部会做失败计数，这里不再重复记）
        data, why_dom = self._resolve_from_dom(page_url)
        if data:
            return data, ""
        return None, why_dom or why or "没能读到作品数据"

    @staticmethod
    def _aweme_to_dict(aweme: dict, page_url: str) -> dict:
        """把接口里的 aweme 摊平成解析器统一的字典。"""
        video = aweme.get("video") or {}
        author = aweme.get("author") or {}
        stats = aweme.get("statistics") or {}

        def _first_url(node: Any) -> str:
            if isinstance(node, dict):
                urls = node.get("url_list") or []
                if isinstance(urls, list) and urls:
                    return str(urls[0])
            return ""

        # 播放地址候选：play_addr 优先，再按码率从高到低补齐。
        # play_addr 是无水印的那份；download_addr 带水印，绝不能用。
        srcs: list[str] = []
        for u in (video.get("play_addr") or {}).get("url_list") or []:
            if isinstance(u, str) and u and u not in srcs:
                srcs.append(u)
        rates = video.get("bit_rate") or []
        if isinstance(rates, list):
            for br in rates:
                if isinstance(br, dict):
                    u = _first_url(br.get("play_addr"))
                    if u and u not in srcs:
                        srcs.append(u)

        cover = _first_url(video.get("origin_cover")) or _first_url(video.get("cover"))

        # 图集作品没有 video.play_addr，图在 images 里
        images: list[str] = []
        for img in aweme.get("images") or []:
            if isinstance(img, dict):
                u = _first_url(img)
                if u:
                    images.append(u)

        return {
            "video_id": str(aweme.get("aweme_id") or ""),
            "desc": str(aweme.get("desc") or ""),
            "author": str(author.get("nickname") or ""),
            "sec_uid": str(author.get("sec_uid") or ""),
            "create_time": int(aweme.get("create_time") or 0),
            "duration": int(video.get("duration") or 0),
            "digg_count": int(stats.get("digg_count") or 0),
            "comment_count": int(stats.get("comment_count") or 0),
            "share_count": int(stats.get("share_count") or 0),
            "cover_url": cover,
            "share_url": page_url,
            "image_list": images,
            "srcs": srcs,
        }

    @staticmethod
    def _dom_to_dict(data: dict, page_url: str) -> dict:
        """把 DOM 快照（title / href / cover / srcs）摊平成解析器统一字典。

        和 `_aweme_to_dict` 的区别：那份数据来自详情接口，字段全；
        这里只有页面 DOM 能给的东西 —— 标题靠 document.title，
        播放地址靠 `<video><source>`，够下载用了。
        """
        href = str(data.get("href") or "")
        vid = ""
        m = _ID_RE.search(href or page_url or "")
        if m:
            vid = m.group(1)
        title = str(data.get("title") or "")
        # 标题尾巴统一是「 - 抖音」，去掉；顺手把空格规整一下
        title = re.sub(r"\s*-\s*抖音\s*$", "", title).strip()
        return {
            "video_id": vid,
            "desc": title,
            "cover_url": str(data.get("cover") or ""),
            "share_url": page_url,
            "srcs": [str(u) for u in (data.get("srcs") or [])],
            "href": href,
        }

    def _resolve_from_dom(self, page_url: str) -> tuple[dict | None, str]:
        """兜底通道：读页面 `<video>` 里的 `<source>`。

        只在详情接口没截到时才用。失败计数在本方法内部做，调用方别重复记。
        """
        if not self._crawler.navigate(page_url):
            self._note_fail()
            return None, "浏览器打开页面超时"

        href, data = "", {}
        started = time.time()
        deadline = started + self.WAIT_SECONDS
        # ★ 2026-10-25：`/jingxuan`（精选首页）也算"被踢回首页"。抖音对未登录
        #   的浏览器就是把作品页重定向到这里，页面标题是「抖音精选电脑版」。
        #   旧正则只匹配 `douyin.com/` 和 `douyin.com/?x`，匹配不到它。
        home_re = re.compile(r"^https?://(www\.)?douyin\.com/(jingxuan)?/?(\?.*)?$")
        while time.time() < deadline:
            raw = self._crawler.evaluate(_JS_READ, timeout=12.0)
            if raw:
                try:
                    data = json.loads(raw)
                except Exception:                   # noqa: BLE001
                    data = {}
                href = str(data.get("href") or "")
                if data.get("srcs"):
                    break
                # ★ 落地到首页 = 被抖音踢走了。但不能一读到首页就判死：短链重定向
                #   的头几百毫秒 location 还是 douyin.com/，过早判定会把好链接
                #   也报成"已失效"。给它 6 秒，重定向早该完成了。
                if home_re.match(href) and time.time() - started > 6.0:
                    self._note_fail(soft=True)
                    return None, ("页面被抖音重定向到首页/精选页 —— "
                                  "浏览器没有有效登录态，请在「设置 → 平台登录」扫码后再试")
            time.sleep(0.7)
        else:
            self._note_fail()
            if data.get("hasVerify"):
                return None, ("浏览器里被抖音要求人机验证 —— 请打开软件弹出的浏览器窗口"
                              "过一次验证后重试")
            if home_re.match(href):
                self._note_fail(soft=True)
                return None, ("页面被抖音重定向到首页/精选页 —— "
                              "浏览器没有有效登录态，请在「设置 → 平台登录」扫码后再试")
            return None, "页面渲染完成但没读到播放地址（作品可能已删除或仅粉丝可见）"

        self._fails = 0
        # 组装成统一字典（video_id / 标题 / 封面 / 播放地址）交给上层
        return self._dom_to_dict(data, page_url), ""

    def _note_fail(self, soft: bool = False) -> None:
        """记一次失败；到上限先**冷却**，冷却完自动恢复，而不是永久停用。

        ★ 改这个的现场依据（2026-10-25，客户导入 182 条、失败 168 条）：
          旧版 MAX_FAILS=3 且整批共用一个实例 ⇒ 前 3 条坏链之后，剩下 160 多条
          好链接**根本没被尝试**（实测第 4 条 0.8 秒、第 5 条 0.3 秒就返回失败），
          而界面把它们统一报成「链接已失效，请重新复制」。
          软件在撒谎 —— 比"失败"本身更糟：客户会把好链接一起删掉重导。

        Args:
            soft: True = 这条是**链接自身**的问题（被重定向到首页、短链失效），
                  通道本身没毛病，不该计入熔断。只有通道真的读不到数据才算。
        """
        if soft:
            return
        self._fails += 1
        if self._fails < self.MAX_FAILS:
            return
        # —— 到上限：先冷却，再给一次机会 ——
        self._cooldowns += 1
        if self._cooldowns > self.MAX_COOLDOWNS:
            self._disabled = True
            self._start_error = (
                "浏览器渲染连续多轮都读不到数据，抖音这边像是在限流。"
                "已暂停本次批量 —— 过 10 分钟再跑剩下的，或到「设置 → 平台登录」换一份登录态")
            return
        wait = self.COOLDOWN_SECONDS * self._cooldowns
        self.last_reason = (f"连续 {self.MAX_FAILS} 条没读到数据，"
                            f"疑似被限流，已暂停 {int(wait)} 秒后自动继续")
        # 这里是后台解析线程，阻塞式等待是可以接受的：风控期间硬冲只会换来
        # 更多失败，而且会把账号标记得更重。
        time.sleep(wait)
        self._fails = 0


def pick_play_url(srcs: list[str]) -> str:
    """从候选地址里挑一个最像"原片直链"的。

    优先级：
      1. douyinvod.com 上的 CDN 直链 —— tos 原始文件，无水印、无转码损失
      2. `aweme/v1/play/?...` —— 会 302 到 CDN，也能用，只是多一跳
      3. `aweme/v1/playwm/` —— **带水印**（wm = watermark）。
         一个都不能优先用，客户拿到片子看到角标会直接来问。
         实在只有它时，把路径里的 `/playwm/` 换成 `/play/` 通常就是无水印版，
         这是抖音接口多年不变的老规律。
    """
    clean, wm = "", ""
    for u in srcs:
        low = u.lower()
        if "playwm" in low:
            wm = wm or u
            continue
        if "douyinvod.com" in low or "/video/tos/" in low:
            return u
        if "/aweme/v1/play" in low:
            clean = clean or u
        elif "douyin.com" not in low and not clean:
            clean = u
    if clean:
        return clean
    if wm:
        return wm.replace("/playwm/", "/play/").replace("playwm?", "play?")
    return srcs[0] if srcs else ""
