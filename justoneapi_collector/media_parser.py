# -*- coding: utf-8 -*-
"""媒体解析服务客户端（video.zacao.top）—— **只负责"链接 → 无水印直链"这一件事**。

═══════════════════════════════════════════════════════════════════════════
为什么要有第二个解析服务（2026-10-25，v1.24.6）
═══════════════════════════════════════════════════════════════════════════
客户投诉原话：「很多很多都不能用，只有 15 个？……一直跟着售后处理也解决不了」。
现场是批量导入 182 条链接，只成功 14 条。

根因是**本机去抖音抓页面**这条老路先天不稳，三个变量一个都控制不了：

  ① 抖音把 SSR 数据从页面里拿掉了（`_ROUTER_DATA` 只剩 `video_layout: null`），
     只能起无头浏览器跑一遍 JS 再截它自己发的详情响应；
  ② 浏览器没有登录态时，作品页会被 302 到 `/jingxuan` 精选首页，一条都读不到；
  ③ 同一个 IP 短时间高频抓作品页会上风控 —— 批量导入几百条几乎必然撞上。

v1.24.5 已经把这三处都补过了（浏览器渲染 / Cookie 注入 / 熔断改冷却），
单条耗时从 55 秒降到 4~13 秒，但**失败率仍然取决于客户那台机器那一刻的状态**。

新解析服务把这个活搬到**服务端**去做：我们只 POST 一条链接过去，对方返回
无水印直链。本机 IP 干不干净、有没有扫码登录、有没有装浏览器、抖音今天又
改没改版 —— 全都无关。这条路一开，批量解析的成功率就跟客户端环境脱钩了。

═══════════════════════════════════════════════════════════════════════════
★ 边界铁律（用户明确指定，不许改）
═══════════════════════════════════════════════════════════════════════════
**只有"视频 / 图文作品的解析下载"走这个服务。**
其余功能（关键词搜索、博主列表、评论采集、详情数据采集、文案提取…）
一律照旧走 JustOneAPI —— 那是另一套账号体系、另一个 key、按次计费，
两个 key 不能混用，混用会让客户看到"欠费"这种跟他无关的报错。

═══════════════════════════════════════════════════════════════════════════
★ Key 的性质
═══════════════════════════════════════════════════════════════════════════
这个 key 是**作者自己的**，成本由作者承担，不从客户身上扣。因此：
  · 不接「付费闸门」（require_feature）——它是"软件自带的能力"，不是功能位；
  · 免费版和完整版**都走同一条路**（两个版本的解析链路必须一致，见 v1.24.0 家规）；
  · 界面上**不许出现"额度 / 次数 / 欠费"**这类字眼跟这条链路挂钩。
Key 泄露可在 https://video.zacao.top/account 点「更换」作废旧串。
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

# Base URL 由 https://video.zacao.top/docs 给出（当前部署地址）
BASE_URL = "https://video.zacao.top"

# ★ 作者自己的解析服务 Key（mp_ 前缀）。
#   允许被环境变量 TRAFFIC_MEDIA_KEY 覆盖 —— 哪天要换新串，
#   不必改代码重新打包，设置一次环境变量就能顶上（换串应急用）。
DEFAULT_API_KEY = os.getenv(
    "TRAFFIC_MEDIA_KEY",
    "mp_gSUiBNZBV7TRhcXRWaPmPk3U0wbuEVny",
).strip()

# 解析超时：服务端要去对方平台抓一次页面，偶尔会比较慢。
# ★ 但也不能太久：批量导入 200 条时，每条多等 10 秒就是半小时。
#   30 秒足够服务端完成一次抓取（实测 1~4 秒），超时就认输走兜底。
DEFAULT_TIMEOUT = 30.0

# ----------------------------------------------------------------------
#  平台名 ↔ 内部键
# ----------------------------------------------------------------------
# 解析服务 `data.platform` 返回的是**中文名**（"抖音" / "小红书" / "快手"）。
# 内部键要尽量复用软件已有的四个（douyin / xiaohongshu / kuaishou / tiktok），
# 因为 cookies 匹配、下载 Referer、落库都按这个键走；其余平台用可读的拉丁键，
# 中文名统一登记在 PLATFORM_LABELS 里 —— 界面显示从这里取，
# 绝不能把英文内部键直接摆到客户面前（老规矩：界面不许出现内部键）。
PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "tiktok": "TikTok",
    "bilibili": "B站",
    "weibo": "微博",
    "ixigua": "西瓜视频",
    "toutiao": "今日头条",
    "zhihu": "知乎",
    "weixin_channels": "视频号",
    "weixin_mp": "公众号",
    "baidu": "百度",
    "dewu": "得物",
    "tencent_news": "腾讯新闻",
    "doubao": "豆包",
    "dola": "Dola",
    "jimeng": "即梦",
    "weishi": "微视",
    "pear_video": "梨视频",
}

# 解析服务可能给出的其它写法 → 内部键（含小写、别名、繁简差异）
_NAME_ALIASES: dict[str, str] = {
    "抖音": "douyin", "douyin": "douyin",
    "小红书": "xiaohongshu", "rednote": "xiaohongshu", "xiaohongshu": "xiaohongshu",
    "快手": "kuaishou", "kuaishou": "kuaishou",
    "tiktok": "tiktok",
    "b站": "bilibili", "哔哩哔哩": "bilibili", "bilibili": "bilibili",
    "微博": "weibo", "weibo": "weibo",
    "西瓜视频": "ixigua", "ixigua": "ixigua",
    "今日头条": "toutiao", "toutiao": "toutiao",
    "知乎": "zhihu", "zhihu": "zhihu",
    "视频号": "weixin_channels", "微信视频号": "weixin_channels",
    "公众号": "weixin_mp", "微信公众平台": "weixin_mp",
    "百度": "baidu", "得物": "dewu",
    "腾讯新闻": "tencent_news", "豆包": "doubao",
    "dola": "dola", "即梦": "jimeng", "微视": "weishi",
    "梨视频": "pear_video",
}

# ----------------------------------------------------------------------
#  错误码 → 客户能照着做的一句话
# ----------------------------------------------------------------------
# 出处：https://video.zacao.top/docs 「错误码」小节。
# 每条必须是"下一步该干嘛"，不能停在"服务器返回 403"这种复述上。
# ★ 400/403/500/502 都会附带服务端 message，最后统一括在原话后面。
_ERR_TEXT: dict[int, str] = {
    400: "这条链接不是解析服务能认的格式（换成抖音/小红书里复制的完整分享链接再试）",
    401: "解析通道缺少授权（软件内置的解析通道失效了，请联系作者刷新）",
    403: "解析通道无效或已被停用（请联系作者刷新），也可能是这条内容对你不可见",
    404: "这条内容可能已被作者删除或设为私密",
    429: "解析服务正在限流 —— 等一两分钟再跑剩下这些",
    500: "解析服务端抓取失败（多数是对方平台的临时风控），隔几分钟再试一次",
    502: "解析服务端没能打开这条链接（多数是对方平台的临时风控），隔几分钟再试一次",
}

# 从一段文本里抠出第一条 http(s) 链接 —— 客户粘贴的常常是整段分享文案，
# 前后还带着 emoji 和"复制此条消息…"之类的引导语，必须抠出来再发。
_URL_RE = re.compile(r"https?://[^\s\"'<>，。、；：！？（）【】《》]+")


def label_for(platform_key: str) -> str:
    """内部键 → 中文名。认不出来给键本身（总比空着强）。"""
    k = str(platform_key or "").strip()
    if not k:
        return ""
    return PLATFORM_LABELS.get(k, k)


def normalize_platform(name: str) -> str:
    """解析服务返回的 platform 字符串 → 内部键。认不出来返回空串。"""
    n = str(name or "").strip()
    if not n:
        return ""
    if n in _NAME_ALIASES:
        return _NAME_ALIASES[n]
    low = n.lower()
    if low in _NAME_ALIASES:
        return _NAME_ALIASES[low]
    # 退一步：含关键子串也算（"抖音短视频" / "xiaohongshu explore" 这类）
    for frag, key in (("抖音", "douyin"), ("小红书", "xiaohongshu"),
                      ("快手", "kuaishou"), ("tiktok", "tiktok")):
        if frag in low:
            return key
    return ""


def extract_first_url(text: str) -> str:
    """从分享文案里抠出第一条链接；没有则原样返回（让服务端去判）。"""
    s = str(text or "").strip()
    m = _URL_RE.search(s)
    return m.group(0).rstrip(".,;:，。；：、）)】》") if m else s


# ----------------------------------------------------------------------
#  TikTok 分享短链 → 完整作品页（尽力而为，2026-11-14）
# ----------------------------------------------------------------------
# ★ 客户反馈「tk 无法提取」查实：服务端解析对
#   https://www.tiktok.com/@作者/video/数字ID 这种完整链接实测可用
#   （能拿到标题 / 封面 / 无水印直链），但 App「分享 → 复制链接」出来的
#   vm.tiktok.com / vt.tiktok.com / www.tiktok.com/t/ 短链一律回「链接不可访问」。
#   客户本机在国内直连 TikTok 会被重置，所以这里只做一次**不跟内容**的 302
#   探测：有代理或海外网络的客户能当场展开，没有的 5 秒内放弃、原样交服务端。
#   ★ 只跟短链这一跳，Location 指向长链就停 —— 绝不去 GET 长链页面。
_TIKTOK_SHORT_HOSTS = ("vm.tiktok.com", "vt.tiktok.com", "t.tiktok.com")
_TIKTOK_SHORT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)


def is_tiktok_shortlink(url: str) -> bool:
    u = str(url or "").lower()
    if any(h in u for h in _TIKTOK_SHORT_HOSTS):
        return True
    return "tiktok.com" in u and "/t/" in u


def expand_tiktok_shortlink(url: str, timeout: float = 5.0) -> str:
    """短链 → 完整作品页 URL。展开不了原样返回，绝不抛异常、不拖慢主流程。"""
    if not is_tiktok_shortlink(url):
        return url
    try:
        import httpx  # 函数体内 import：非 TikTok 链接不该付这个代价
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=3.0),
            follow_redirects=False,
            headers={"User-Agent": _TIKTOK_SHORT_UA, "Accept": "*/*"},
            trust_env=True,       # 有代理的客户靠它展开；没代理直连被重置即放弃
        ) as c:
            r = c.get(url)
            loc = str(r.headers.get("location") or "").strip()
        if loc.startswith("//"):
            loc = "https:" + loc
        low = loc.lower()
        if loc.startswith("http") and "tiktok.com" in low and "/video/" in low:
            return loc.split("?")[0].rstrip("/")
    except Exception:            # noqa: BLE001
        pass                     # 连不上 / 被重置 / 超时 —— 一律当没展开
    return url


# ----------------------------------------------------------------------
#  结果结构
# ----------------------------------------------------------------------
# parse() 返回 (dict | None, reason)。dict 的键刻意做成纯数据 ——
# 转成 VideoInfo 由 video_downloader 负责，本模块不 import 那边
# （VideoInfo 定义在 video_downloader 里，反向 import 会成环）。
#  dict 键：
#    platform      内部键（douyin / xiaohongshu / …）
#    platform_name 解析服务给的中文平台名
#    video_id      作品 ID（平台侧）
#    title / author / cover_url
#    video_url     无水印直链（单视频作品）
#    audio_url     音频直链（有则给）
#    images        [str, …]  图集
#    videos        [(url, cover), …] 一帖多视频时的各个视频
def parse(text: str, api_key: str = "", timeout: float = DEFAULT_TIMEOUT
          ) -> tuple[dict[str, Any] | None, str]:
    """解析一条分享链接 / 分享文案 → 归一化 dict。

    失败返回 (None, 人话原因)。**不抛异常** —— 调用方是批量循环，
    一条抛出去会连累整批（v1.22.0 的教训：free Edition 里 client 是占位
    对象，炸出「API异常(AttributeError)」这种没人看得懂的话）。
    """
    payload_text = extract_first_url(text)
    if not payload_text:
        return None, "没有可解析的内容（请粘贴链接或包含链接的分享文案）"
    # ★ TikTok 分享短链先尽力展开成完整作品页（能展开就直接救回这一条，
    #   展开不了原样交给服务端，由调用方给可操作的换链提示）。
    payload_text = expand_tiktok_shortlink(payload_text)

    key = (api_key or DEFAULT_API_KEY).strip()
    if not key:
        return None, "未配置解析 Key（请联系作者）"

    import httpx  # 函数体内 import：不用下载功能的会话不该付这个代价

    body: dict[str, Any] | None = None
    # ★ 429（限流）单独给一次重试：批量导入几百条时，服务端对"每分钟多少次"
    #   有软限制，撞上就整条失败太亏 —— 而 429 意味着这次**没被处理**，
    #   重试不会重复扣费。
    # ★ 2026-11-14 客户「tk 无法提取」实测：服务端抓 TikTok 是**间歇性**的，
    #   同一条完整链接连打 3 次里 2 次 200 成功、1 次 502「链接不可访问」。
    #   客户试一次撞上 502 就以为软件不支持。只对 TikTok + 500/502 重发一次，
    #   1.5 秒间隔；其它平台的失败多半是链接真失效，重试无益还拖慢批量。
    # ★ 2026-11-24：网络超时/瞬时连接失败同样给**恰好 1 次**短间隔（1 秒）重试，
    #   与 429/502 共用下面同一个 3 次总上限。初衷仍是"批量不能慢一倍"：
    #   只有第一次抖动才重发，第二次还失败就立刻认输，绝不放开成无限重试。
    _is_tk = "tiktok.com" in payload_text.lower()
    _retried_5xx = False
    _retried_net = False
    for attempt in (1, 2, 3):
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout, connect=10.0),
                follow_redirects=True,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json",
                         "X-API-Key": key},
                trust_env=False,          # ★ 服务端在境外，走系统代理反而常超时
            ) as c:
                r = c.post(f"{BASE_URL}/api/parse", json={"text": payload_text})
                # ★ 204 是服务端明确的"没有内容"、body 是空的 —— 这是独立的
                #   语义，不能混进"非 JSON 响应"里让排查没方向。
                #   （下载侧 CDN 的 204 空文件走 video_downloader 的 st_size
                #     判据，与本处解析响应无关，别改成同一套处理。）
                if getattr(r, "status_code", None) == 204:
                    return None, "解析服务返回空结果(204)，内容可能不可用或服务端异常"
                try:
                    body = r.json()
                except Exception:                               # noqa: BLE001
                    return None, f"解析服务返回了非 JSON 响应（HTTP {r.status_code}）"
        except Exception as e:                                  # noqa: BLE001
            cls = type(e).__name__
            # 超时/网络类：客户做不了任何事，但要让他知道是网络而不是链接坏了
            if "Timeout" in cls or "Connect" in cls or "Network" in cls:
                # 偶发抖动只救一次；救不回来立刻认输，批量不会整体翻倍。
                if not _retried_net:
                    _retried_net = True
                    time.sleep(1.0)
                    continue
                return None, "连不上解析服务（网络超时）—— 换条网络或稍后再试这一条"
            return None, f"解析服务请求失败（{cls}）—— 稍后再试这一条"

        try:
            _code = int((body or {}).get("code"))
        except Exception:                                       # noqa: BLE001
            _code = -1
        if _code == 429 and attempt == 1:
            time.sleep(2.5)
            continue
        if _is_tk and _code in (500, 502) and not _retried_5xx:
            _retried_5xx = True
            time.sleep(1.5)
            continue
        break

    if not isinstance(body, dict):
        return None, "解析服务返回了无法识别的响应"

    try:
        code_i = int(body.get("code"))
    except Exception:                                           # noqa: BLE001
        code_i = -1
    message = str(body.get("message") or "").strip()
    succ = bool(body.get("succ"))
    data = body.get("data") if isinstance(body.get("data"), dict) else None

    out = _normalize_data(data, payload_text) if data else None
    if not succ or out is None:
        # 到这里都是失败：按具体错误码给话，并把服务端原话括在后面 ——
        # 「多步链路必须带原因」是本项目老规矩，只说「解析失败」等于没说。
        why = _ERR_TEXT.get(code_i) or message or "解析服务没能给出结果"
        if message and message not in why:
            why = f"{why}（{message}）"
        return None, why

    return out, ""


def _normalize_data(data: dict, fallback_url: str) -> dict[str, Any] | None:
    """把服务端 data 摊平成上面那张 dict。拿不到任何可下载地址时返回 None。"""
    raw_name = str(data.get("platform") or "").strip()
    platform = normalize_platform(raw_name)

    def _s(v) -> str:
        return str(v or "").strip()

    video_url = _s(data.get("video_url") or data.get("source_video_url"))
    images: list[str] = []
    for item in (data.get("image_list") or []):
        if isinstance(item, str):
            u = _s(item)
            if u:
                images.append(u)
        elif isinstance(item, dict):
            # 动态图（live photo）会带 live_photo_url，那是视频不是图 —— 别收进来
            for k in ("url", "image_url", "img_url", "origin_url"):
                u = _s(item.get(k))
                if u:
                    images.append(u)
                    break
            else:
                ul = item.get("url_list") or []
                if isinstance(ul, list) and ul:
                    u = _s(ul[0])
                    if u:
                        images.append(u)

    videos: list[tuple[str, str]] = []
    for item in (data.get("video_list") or []):
        if isinstance(item, str):
            u = _s(item)
            if u:
                videos.append((u, ""))
        elif isinstance(item, dict):
            u = _s(item.get("video_url") or item.get("source_video_url"))
            if u:
                videos.append((u, _s(item.get("cover_url"))))

    if not video_url and videos:
        video_url = videos[0][0]
    if not video_url and not images:
        return None

    author_raw = data.get("author")
    author = ""
    if isinstance(author_raw, dict):
        author = _s(author_raw.get("nickname") or author_raw.get("name")
                    or author_raw.get("author_name"))
    elif isinstance(author_raw, str):
        author = _s(author_raw)

    return {
        "platform": platform or (platform_from_url(fallback_url) or "douyin"),
        "platform_name": raw_name or label_for(platform),
        "video_id": _s(data.get("video_id") or data.get("aweme_id") or data.get("work_id")),
        "title": _s(data.get("title") or data.get("desc")),
        "author": author,
        "cover_url": _s(data.get("cover_url")),
        "audio_url": _s(data.get("audio_url")),
        "video_url": video_url,
        "images": images,
        "videos": videos,
    }


def platform_from_url(url: str) -> str:
    """按链接域名猜平台（解析服务没给 platform 时的兜底）。"""
    u = (url or "").lower()
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    if "kuaishou.com" in u or "kuaishou.cn" in u or "chenzhongtech" in u:
        return "kuaishou"
    if "tiktok.com" in u:
        return "tiktok"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if "weibo.com" in u or "weibo.cn" in u:
        return "weibo"
    if "douyin.com" in u or "iesdouyin.com" in u:
        return "douyin"
    # ★ 微信两个东西共用 weixin.qq.com 这个域名，得靠路径分：
    #   /sph/  → 视频号短链（https://weixin.qq.com/sph/AM94sIUqvX）
    #   /s/    → 公众号文章（https://mp.weixin.qq.com/s/xxxx）
    # 判错会把视频号作品当公众号，下载时按错平台给头。
    if "weixin.qq.com" in u:
        return "weixin_channels" if "/sph/" in u else "weixin_mp"
    return ""


def health(api_key: str = "", timeout: float = 10.0) -> tuple[bool, str]:
    """探活。返回 (是否可用, 说明)。用于自检脚本与客户报障时的一键诊断。"""
    key = (api_key or DEFAULT_API_KEY).strip()
    import httpx
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0),
                          trust_env=False) as c:
            r = c.get(f"{BASE_URL}/api/health",
                      headers={"X-API-Key": key} if key else None)
        ok = r.status_code == 200
        return ok, f"HTTP {r.status_code}"
    except Exception as e:                                      # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _self_check() -> int:
    """命令行自检：python -m justoneapi_collector.media_parser"""
    t0 = time.time()
    ok, note = health()
    print(f"[health] ok={ok} {note}")
    if not ok:
        return 1
    # 一条真实存在的抖音作品（video-search-v4 抓取后筛出来的有效链接）
    for probe in (
        "https://www.douyin.com/video/7644834211292280293",
    ):
        t1 = time.time()
        data, reason = parse(probe)
        cost = time.time() - t1
        if data is None:
            print(f"[parse] 失败（{cost:.1f}s）：{reason}")
            return 1
        print(f"[parse] 成功（{cost:.1f}s）")
        print(f"  平台：{data['platform_name']}（{data['platform']}）")
        print(f"  作者：{data['author']}　ID：{data['video_id']}")
        print(f"  标题：{data['title'][:40]}")
        print(f"  视频：{data['video_url'][:80]}…")
        print(f"  图片：{len(data['images'])}　多视频：{len(data['videos'])}")
    print(f"总耗时 {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_check())
