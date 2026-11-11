# -*- coding: utf-8 -*-
"""评论区截流：把对标视频评论区里「有购买/咨询意向」的用户挖出来。

典型用法（代运营场景）：
    1. 客户给出 3~10 个对标爆款视频链接
    2. 本模块把这些视频的评论按页抓下来（抖音实测每页 20 条，cursor 翻页）
    3. 用「意图词库」过滤出想买、想问价、想私信的人
    4. 导出 Excel：昵称 / 抖音号 / 主页链接 / 评论内容 / 点赞 / 时间
       → 直接交给运营去私信、评论回复、加微

★ 成本意识（2026-10-12）：评论接口 0.1 元/次，一个视频抓 3 页就是 0.3 元。
  体验版客户一天只有 10 次额度，所以：
    · 默认只抓 2 页（40 条），由用户自己往上加
    · 「抓子评论」「排除视频作者」都要额外请求，默认关闭，且界面会算给客户看
    · 接口没数据（has_more=0）立即停止，不把额度浪费在空页上

★ 只走平台适配器（platforms.adapter），参数名（awemeId / cursor / count）由
  PROFILES 统一维护，不要在本文件里硬编码 —— 快手、小红书的参数名不一样。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .platforms.adapter import PlatformAdapter

# ---------------------------------------------------------------- 意图词库

#: 默认意图词：命中任意一条即视为「有意向」。
#:
#: ★ 2026-10-12 实测踩坑：一开始用了「购买」「联系」「求」「链接」这类泛词，
#:   结果真实评论区里全是误伤 ——
#:     · "网上购买的产品内容不该写在外包装上" → 命中"购买"（在聊快递隐私）
#:     · "中通丢了包裹，联系态度也很差"     → 命中"联系"（在骂快递）
#:   导出来全是无关人，运营根本没法用。所以这里**一律用完整短语**，
#:   宁可少抓几个，也不要把沙子倒进客户的表里。想放宽的客户可以自己在界面加词。
DEFAULT_INTENT_WORDS: list[str] = [
    # 想买 / 问价
    "怎么买", "哪里买", "哪买", "哪儿买", "哪里可以买", "求购", "想买", "我也想买",
    "怎么购买", "在哪买", "多少钱", "多少米", "什么价", "价格多少", "报价",
    "贵吗", "贵不贵", "下单", "怎么下单", "求链接", "给个链接", "发个链接",
    "链接给", "想入手", "值得买", "买过",
    # 想要 / 求资源
    "我想要", "我也想要", "我也想", "求推荐", "求分享", "求带", "求教程",
    "有吗", "哪里有", "怎么弄", "怎么做", "求资料", "发份资料",
    # 联系 / 加私
    "怎么联系", "联系一下", "联系你", "私信", "私聊", "私我", "加我",
    "加个微", "加微", "微信", "vx", "V我", "威信", "薇信", "绿泡泡",
    # B 端
    "批发", "代理", "加盟", "拿货", "厂家", "供货", "想合作", "怎么合作",
    "一件代发",
    # 兴趣确认（弱信号）
    "有兴趣", "感兴趣", "想了解", "了解一下", "有用吗", "好用吗",
    "效果怎么样", "有效果吗",
]

#: 强信号词：命中这些基本就是真意向客户，导出时排在前面。
#: 弱信号（"有用吗""想了解"）只说明有兴趣，排后面。
STRONG_INTENT_WORDS: set[str] = {
    "怎么买", "哪里买", "哪买", "哪儿买", "哪里可以买", "求购", "想买", "我也想买",
    "怎么购买", "在哪买", "多少钱", "多少米", "什么价", "价格多少", "报价",
    "下单", "怎么下单", "求链接", "给个链接", "发个链接", "链接给", "想入手",
    "我想要", "我也想要", "怎么联系", "联系一下", "联系你", "私信", "私聊",
    "私我", "加我", "加个微", "加微", "微信", "vx", "V我", "威信", "薇信",
    "绿泡泡", "批发", "代理", "加盟", "拿货", "厂家", "供货", "想合作",
    "怎么合作", "一件代发",
}

#: 明确无意向、纯灌水的评论（命中则丢弃，优先级低于意图词：先判垃圾再判意图）
DEFAULT_SPAM_WORDS: list[str] = [
    "沙发", "板凳", "前排", "占楼", "路过", "打卡", "第一", "来了", "顶",
]

COMMENT_COLUMNS: list[str] = [
    "来源视频", "用户昵称", "抖音号", "评论内容", "点赞", "回复数",
    "评论时间", "意向强度", "命中意图词", "粉丝数", "用户主页", "视频链接",
]

#: 抓取页数档位：标签 -> 页数（每页 20 条）
PAGE_PLANS: dict[str, int] = {
    "快速（每视频 1 页，约 20 条）": 1,
    "标准（每视频 3 页，约 60 条）": 3,
    "深度（每视频 5 页，约 100 条）": 5,
}

COMMENTS_PER_PAGE = 20

_ID_RE = re.compile(r"(?:video/|aweme[_-]?id[=:]?|item[_-]?ids?[=:]?|"
                    r"note/|photo/)(\d{12,25})")


# ---------------------------------------------------------------- 数据结构

@dataclass
class CommentLead:
    """一条「有意向的评论」，导出的最小单位。"""

    platform: str = ""
    post_id: str = ""
    post_url: str = ""
    comment_id: str = ""
    user_name: str = ""
    user_unique_id: str = ""      # 抖音号（可直接搜索到的那个）
    user_uid: str = ""            # 平台内部 uid
    user_home: str = ""           # 主页链接（可点进去私信）
    text: str = ""
    digg: int = 0
    reply_count: int = 0
    published: str = ""
    followers: int = 0
    hits: list[str] = field(default_factory=list)
    is_author: bool = False
    strong: bool = False         # 命中了强信号词（基本就是真意向客户）

    @property
    def strength(self) -> str:
        return "强" if self.strong else "弱"

    def as_row(self) -> list:
        return [
            self.post_id,
            self.user_name,
            self.user_unique_id,
            self.text,
            self.digg,
            self.reply_count,
            self.published,
            self.strength,
            "、".join(self.hits),
            # 评论接口不返回粉丝数（实测全是 0），显示 0 会让客户以为这人没粉丝，
            # 拿不到就留空
            self.followers or "",
            self.user_home,
            self.post_url,
        ]


@dataclass
class MineResult:
    leads: list[CommentLead] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requests: int = 0             # 实际发出的接口请求数（收费次数）
    scanned: int = 0              # 扫描到的评论总数（过滤前）
    posts_done: int = 0


# ---------------------------------------------------------------- 工具

def extract_post_id(text: str) -> str | None:
    """从链接/分享文本/纯数字里抠出作品 ID（本地正则，零成本）。"""
    if not text:
        return None
    s = str(text).strip()
    m = _ID_RE.search(s)
    if m:
        return m.group(1)
    # 纯数字 ID（抖音 19 位）
    if s.isdigit() and len(s) >= 12:
        return s
    return None


def split_post_inputs(raw: str) -> list[str]:
    """把输入框内容拆成作品 ID 列表：一行一个，也兼容逗号/空格。去重保序。"""
    out: list[str] = []
    for chunk in re.split(r"[,，\s]+", str(raw or "")):
        chunk = chunk.strip()
        if not chunk:
            continue
        pid = extract_post_id(chunk)
        if pid and pid not in out:
            out.append(pid)
    return out


def match_intent(text: str, keywords: list[str] | None = None) -> list[str]:
    """返回命中的意图词列表（空列表＝没命中）。keywords 为空时用默认词库。"""
    words = keywords if keywords else DEFAULT_INTENT_WORDS
    body = str(text or "")
    low = body.lower()
    return [w for w in words if w and (w in body or w.lower() in low)]


def is_spam(text: str) -> bool:
    body = str(text or "").strip()
    # 太短的纯表情/语气词基本没有截流价值（且意图词命中时另行判断）
    return any(w in body for w in DEFAULT_SPAM_WORDS) and len(body) <= 8


_EMOJI_TAG_RE = re.compile(r"\[[^\[\]\s]{1,8}\]")


def clean_text(text: str) -> str:
    """去掉抖音的表情占位（[泣不成声][流泪]）与换行，让导出的表好看。

    只影响展示：意图词匹配仍用原文（表情里不含意图词，清理与否都不影响）。
    """
    s = _EMOJI_TAG_RE.sub("", str(text or ""))
    s = re.sub(r"[\r\n\t]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _fmt_ts(ts) -> str:
    try:
        n = int(ts)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if n > 10_000_000_000:        # 毫秒
        n //= 1000
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(n))
    except (ValueError, OSError, OverflowError):
        return ""


def _user_of(raw: dict) -> dict:
    u = raw.get("user") if isinstance(raw, dict) else None
    return u if isinstance(u, dict) else {}


def _home_url(platform: str, sec_uid: str, uid: str) -> str:
    sec = str(sec_uid or "").strip()
    if not sec:
        return ""
    if "douyin" in platform:
        return f"https://www.douyin.com/user/{sec}"
    if "xiaohongshu" in platform:
        return f"https://www.xiaohongshu.com/user/profile/{sec}"
    if "kuaishou" in platform:
        return f"https://www.kuaishou.com/profile/{sec}"
    return ""


def _post_url(platform: str, post_id: str) -> str:
    if not post_id:
        return ""
    if "douyin" in platform:
        return f"https://www.douyin.com/video/{post_id}"
    if "xiaohongshu" in platform:
        return f"https://www.xiaohongshu.com/explore/{post_id}"
    if "kuaishou" in platform:
        return f"https://www.kuaishou.com/short-video/{post_id}"
    return ""


def build_lead(platform: str, item, keywords: list[str] | None = None) -> CommentLead:
    """把适配器给的 CommentItem 转成导出用的一行。"""
    raw = getattr(item, "raw", None) or {}
    u = _user_of(raw)
    sec_uid = str(u.get("sec_uid") or getattr(item, "user_id", "") or "")
    uid = str(u.get("uid") or "")
    unique_id = str(u.get("unique_id") or u.get("short_id") or "")
    text = str(getattr(item, "text", "") or "")
    hits = match_intent(text, keywords)

    try:
        fans = int(u.get("follower_count") or 0)
    except (TypeError, ValueError):
        fans = 0

    return CommentLead(
        platform=platform,
        post_id=str(getattr(item, "post_id", "") or ""),
        post_url=_post_url(platform, str(getattr(item, "post_id", "") or "")),
        comment_id=str(getattr(item, "comment_id", "") or ""),
        user_name=str(getattr(item, "user_name", "") or u.get("nickname") or ""),
        user_unique_id=unique_id,
        user_uid=uid,
        user_home=_home_url(platform, sec_uid, uid),
        text=clean_text(text),
        digg=int(getattr(item, "digg", 0) or 0),
        reply_count=int(getattr(item, "reply_count", 0) or 0),
        published=_fmt_ts(getattr(item, "publish_ts", 0)),
        followers=fans,
        hits=hits,
        strong=bool(set(hits) & STRONG_INTENT_WORDS),
    )


# ---------------------------------------------------------------- 成本预估

def estimate_requests(n_posts: int, pages: int, *, exclude_author: bool = False,
                      include_replies: bool = False) -> int:
    """预计消耗的接口次数（1 次 ≈ 0.1 元）。

    子评论只对「已命中意图词」的评论抓，命中数事先不可知，这里按
    每页 20 条里约 20% 命中粗估 —— 界面要标注是粗估，别让客户以为精确。
    """
    n = max(0, int(n_posts))
    p = max(1, int(pages))
    base = n * p
    if exclude_author:            # 每条视频多调一次详情拿作者 ID
        base += n
    if include_replies:
        base += int(n * p * COMMENTS_PER_PAGE * 0.2)
    return base


# ---------------------------------------------------------------- 抓取

#: 子评论（回复）端点名 —— 各平台版本后缀不一样，别写死 v1
REPLY_ENDPOINT = {
    "douyin-tiktok-china": "comment-replies-v1",
    "xiaohongshu-rednote": "comment-replies-v2",
}


def _fetch_replies(client, platform: str, post_id: str, comment_id: str,
                   count: int = 20) -> list[dict]:
    """抓某条评论的回复（子评论）。

    抖音接口 `comment-replies-v1`，参数 awemeId + commentId（2026-10-12 实测）。
    快手未开放子评论接口 → 直接返回空（不报错、不浪费请求）。
    失败一律返回空列表，绝不抛给上层 —— 回复抓不到不影响主流程。
    """
    endpoint = REPLY_ENDPOINT.get(str(platform))
    if not endpoint:
        return []
    try:
        resp = client.request(platform, endpoint,
                              {"awemeId": post_id, "commentId": comment_id,
                               "cursor": 0, "count": count})
    except Exception:                                        # noqa: BLE001
        return []
    if not getattr(resp, "success", False):
        return []
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        lst = data.get("comments") or []
        return [c for c in lst if isinstance(c, dict)]
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    return []


def fetch_post_comments(client, platform: str, post_id: str, pages: int = 3,
                        *, include_replies: bool = False,
                        keywords: list[str] | None = None,
                        should_stop=None, on_log=None,
                        state: MineResult | None = None) -> tuple[list[CommentLead], int]:
    """抓一个视频的评论并过滤成「意向线索」。

    Returns: (leads, requests_used)
    """
    ad = PlatformAdapter(client, platform)
    res = state if state is not None else MineResult()
    leads: list[CommentLead] = []
    used = 0

    def say(msg: str):
        if on_log:
            try:
                on_log(msg)
            except Exception:                                # noqa: BLE001
                pass

    cursor = ""
    scanned = 0
    for page in range(1, max(1, pages) + 1):
        if should_stop and should_stop():
            break
        try:
            items, nxt, more = ad.post_comments(post_id, cursor=cursor,
                                               count=COMMENTS_PER_PAGE)
        except Exception as e:                               # noqa: BLE001
            res.errors.append(f"{post_id} 第{page}页异常：{type(e).__name__}: {e}")
            break
        used += 1
        if not items:
            # ★ 别静默：适配器会把请求异常吞掉（_request 里 except 后返回 None），
            #   表现为 items 为空但没有任何报错 —— 界面只显示"没命中意图词"，
            #   客户会以为是自己的问题。这里把适配层记的 last_error 翻出来。
            if page == 1:
                err = str(getattr(ad, "last_error", "") or "").strip()
                if err:
                    res.errors.append(f"{post_id}：{err}")
            # 空页立即停：评论少时后面几页必然还是空，别把额度浪费在空页上
            break
        scanned += len(items)
        for it in items:
            lead = build_lead(platform, it, keywords)
            if lead.hits and not is_spam(lead.text):
                leads.append(lead)
                # 子评论：只对已命中的评论抓（回复里常有"已私你""加v"）
                if include_replies and lead.comment_id:
                    if should_stop and should_stop():
                        break
                    for rd in _fetch_replies(client, ad.platform, post_id,
                                             lead.comment_id):
                        used += 1
                        sub = _reply_to_lead(platform, rd, post_id, keywords)
                        if sub and sub.hits:
                            leads.append(sub)
        if not more or not nxt or str(nxt) == str(cursor):
            break
        cursor = str(nxt)

    res.scanned += scanned
    res.requests += used
    return leads, used


def _reply_to_lead(platform: str, d: dict, post_id: str,
                   keywords: list[str] | None = None) -> CommentLead | None:
    """把子评论的原始 dict 转成 CommentLead（与顶级评论同结构，便于统一导出）。"""
    if not isinstance(d, dict):
        return None
    text = str(d.get("text") or d.get("content") or "")
    if not text:
        return None
    u = _user_of(d)
    sec_uid = str(u.get("sec_uid") or "")
    uid = str(u.get("uid") or "")
    try:
        fans = int(u.get("follower_count") or 0)
    except (TypeError, ValueError):
        fans = 0
    try:
        digg = int(d.get("digg_count") or d.get("digg") or 0)
    except (TypeError, ValueError):
        digg = 0
    sub_hits = match_intent(text, keywords)
    return CommentLead(
        platform=platform,
        post_id=post_id,
        post_url=_post_url(platform, post_id),
        comment_id=str(d.get("cid") or d.get("comment_id") or ""),
        user_name=str(u.get("nickname") or ""),
        user_unique_id=str(u.get("unique_id") or u.get("short_id") or ""),
        user_uid=uid,
        user_home=_home_url(platform, sec_uid, uid),
        text=clean_text(text),
        digg=digg,
        reply_count=0,
        published=_fmt_ts(d.get("create_time")),
        followers=fans,
        hits=sub_hits,
        strong=bool(set(sub_hits) & STRONG_INTENT_WORDS),
    )


def _author_uids(client, platform: str, post_id: str) -> set[str]:
    """拿视频作者的标识集合（uid / sec_uid / 昵称），用于排除作者本人。

    多花 1 次请求（video-details-v2，0.1 元）。拿不到就返回空集（不过滤）。
    """
    ad = PlatformAdapter(client, platform)
    try:
        post = ad.post_detail(post_id)
    except Exception:                                        # noqa: BLE001
        return set()
    if post is None:
        return set()
    raw = getattr(post, "raw", None) or {}
    au = raw.get("author") if isinstance(raw, dict) else None
    au = au if isinstance(au, dict) else {}
    out = set()
    for v in (getattr(post, "author_id", ""), getattr(post, "author_name", ""),
              au.get("sec_uid"), au.get("uid"), au.get("unique_id"),
              au.get("short_id")):
        v = str(v or "").strip()
        if v:
            out.add(v)
    return out


def mine_comments(client, platform: str, post_ids: list[str], *,
                  pages: int = 3,
                  keywords: list[str] | None = None,
                  include_replies: bool = False,
                  exclude_author: bool = False,
                  dedupe: bool = True,
                  should_stop=None,
                  on_log=None,
                  on_post=None) -> MineResult:
    """批量挖评论意向客户。

    Args:
        pages: 每个视频最多抓几页（每页 20 条）
        keywords: 自定义意图词；None = 用默认词库
        include_replies: 是否抓子评论（会显著增加请求数）
        exclude_author: 是否排除视频作者本人（每条视频 +1 次请求）
        dedupe: 同一用户只保留点赞最高的 1 条
        should_stop / on_log / on_post: 进度回调（on_post(idx, total, pid)）
    """
    res = MineResult()
    ad_platform = PlatformAdapter(client, platform).platform   # 归一化后的平台键

    total = len(post_ids or [])
    for idx, pid in enumerate(post_ids or [], 1):
        if should_stop and should_stop():
            break
        if on_post:
            try:
                on_post(idx, total, pid)
            except Exception:                                 # noqa: BLE001
                pass
        author_ids: set[str] = set()
        if exclude_author:
            author_ids = _author_uids(client, ad_platform, pid)
            res.requests += 1
        leads, _used = fetch_post_comments(
            client, platform, pid, pages=pages,
            include_replies=include_replies, keywords=keywords,
            should_stop=should_stop, on_log=on_log, state=res)
        if author_ids:
            leads = [x for x in leads
                     if not ({x.user_uid, x.user_name, x.user_unique_id} & author_ids)]
        res.leads.extend(leads)
        res.posts_done += 1

    if dedupe:
        res.leads = dedupe_leads(res.leads)
    # 强意向优先，其次点赞高的 —— 运营从上往下跟进，前几行就是最该联系的人
    res.leads.sort(key=lambda x: (0 if x.strong else 1, -x.digg))
    return res


def dedupe_leads(leads: list[CommentLead]) -> list[CommentLead]:
    """同一用户（抖音号 / uid / 昵称）只保留点赞最高的那一条。"""
    best: dict[str, CommentLead] = {}
    order: list[str] = []
    for x in leads:
        key = (x.user_unique_id or x.user_uid or x.user_name or x.comment_id).strip()
        if not key:
            key = x.comment_id or f"_anon_{len(best)}"
        prev = best.get(key)
        if prev is None:
            best[key] = x
            order.append(key)
        elif x.digg > prev.digg:
            best[key] = x
    return [best[k] for k in order]


def rows_from_leads(leads: list[CommentLead]) -> list[list]:
    return [x.as_row() for x in leads]
