"""okdata 付费数据通道（与 JustOneAPICollector 同签名的轻量适配器）。

设计边界（改动前务必读）：
1. 本模块只替换**付费采集**通道。视频直链解析仍走 video.zacao.top
   （见 media_parser.py），直播录制仍走本地 ffmpeg，三条链路不许混。
2. 对外只暴露 request(platform, endpoint, params) / request_async /
   close / config / token_invalid，保持既有页面、下载器、监控器零改动。
3. 没映射的接口**明确报错、不发请求、不扣费**，绝不假装成功或返回空数据。
4. okdata 的查询一律发往 https://api.okdata.online，Key 只放 Authorization
   头；任何错误文案都不把 Key 内容写进去。

okdata 协议（2026-11-23 核对官方文档）：
    GET /v1/<接口 id 的点换成斜杠>
    Authorization: Bearer okd_...
    200 -> {"code":"ok","data":{"item":{...}}|{"items":[...]},"page":{...},"meta":{...}}
    4xx/5xx -> {"code":"insufficient_balance", "charged":false, ...}
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import httpx

from .models import ApiResponse

DEFAULT_OKDATA_BASE_URL = "https://api.okdata.online"

#: 老工程里的平台 slug -> okdata 平台名
PLATFORM_ALIAS: dict[str, str] = {
    "douyin-tiktok-china": "douyin",
    "douyin": "douyin",
    "xiaohongshu-rednote": "xiaohongshu",
    "xiaohongshu": "xiaohongshu",
    "kuaishou": "kuaishou",
    "tiktok": "tiktok",
    "wechat-channels": "wechat_channels",
    "wechat_channels": "wechat_channels",
    "wechat-official-accounts": "wechat_mp",
    "weibo": "weibo",
    "bilibili": "bilibili",
    "zhihu": "zhihu",
    "toutiao": "toutiao",
    "youtube": "youtube",
    "twitter": "x",
    "x": "x",
    "instagram": "instagram",
    "reddit": "reddit",
    "1688": "alibaba_1688",
    "taobao-and-tmall": "taobao",
    "jdcom": "jdcom",
    "amazon": "amazon",
    "aliexpress": "aliexpress",
    "xianyu-goofish": "xianyu",
}

#: (okdata 平台名, 老工程的 endpoint) -> okdata 接口 id
#: 只登记软件真实会调用的接口；没登记的就明确失败，不做猜测式转发。
ENDPOINT_MAP: dict[tuple[str, str], str] = {
    # 抖音：账号 / 作品 / 视频 / 评论 / 热榜
    ("douyin", "user-profile-v3"): "douyin.users.get",
    ("douyin", "user-profile-v1"): "douyin.users.get",
    ("douyin", "user-search-v1"): "douyin.users.search",
    ("douyin", "user-search-v2"): "douyin.users.search",
    ("douyin", "user-published-videos-v1"): "douyin.users.videos.list",
    # ★ 视频详情（video-details-* / note-details-* / post-details-*）刻意不映射：
    #   下载器调它是为了拿播放直链，而 okdata 的 Post 对象不含播放地址——
    #   映射过去只会白花钱还下不了。直链一律走免费的 video.zacao.top。
    ("douyin", "video-search-v4"): "douyin.videos.search",
    ("douyin", "video-search-v3"): "douyin.videos.search",
    ("douyin", "video-comments-v1"): "douyin.videos.comments.list",
    ("douyin", "comment-replies-v1"): "douyin.comments.replies.list",
    ("douyin", "hot-search-v1"): "douyin.trends.list",
    ("douyin", "hot-search-v2"): "douyin.trends.list",
    ("douyin", "live-user-status-v1"): "douyin.live.users.get",
    ("douyin", "live-room-info-v1"): "douyin.live.rooms.get",
    # 小红书
    ("xiaohongshu", "user-profile-v3"): "xiaohongshu.users.get",
    ("xiaohongshu", "user-search-v2"): "xiaohongshu.users.search",
    ("xiaohongshu", "user-published-notes-v4"): "xiaohongshu.users.notes.list",
    ("xiaohongshu", "note-search-v2"): "xiaohongshu.notes.search",
    ("xiaohongshu", "note-comments-v2"): "xiaohongshu.notes.comments.list",
    ("xiaohongshu", "comment-replies-v2"): "xiaohongshu.comments.replies.list",
    # 小红书笔记内容提取（2026-11-23，独立 endpoint）：
    #   note-content-v1 → notes.get 拿**正文文本**；刻意不复用下载器的
    #   note-details-v6（那个是拿播放直链，okdata 不含播放地址，映射=白花钱）。
    ("xiaohongshu", "note-content-v1"): "xiaohongshu.notes.get",
    ("xiaohongshu", "topic-notes-v1"): "xiaohongshu.topics.notes.list",
    ("xiaohongshu", "keyword-related-v1"): "xiaohongshu.keywords.related",
    # 快手
    ("kuaishou", "user-profile-v1"): "kuaishou.users.get",
    ("kuaishou", "user-search-v2"): "kuaishou.users.search",
    ("kuaishou", "user-published-videos-v2"): "kuaishou.users.videos.list",
    ("kuaishou", "video-search-v2"): "kuaishou.videos.search",
    ("kuaishou", "video-comments-v1"): "kuaishou.videos.comments.list",
    ("kuaishou", "comment-replies-v1"): "kuaishou.comments.replies.list",
    # TikTok（软件里直播不走付费，这里只覆盖点播采集）
    ("tiktok", "user-profile-v1"): "tiktok.users.get",
    ("tiktok", "user-search-v2"): "tiktok.users.search",
    ("tiktok", "user-published-posts-v1"): "tiktok.users.videos.list",
    ("tiktok", "post-search-v1"): "tiktok.videos.search",
    ("tiktok", "post-comments-v1"): "tiktok.videos.comments.list",
    # 视频号：分享链接解析仍走 zacao，这里只覆盖付费数据接口
    ("wechat_channels", "video-comments-v1"): "wechat_channels.videos.comments.list",
    ("wechat_channels", "video-sub-comments-v1"): "wechat_channels.comments.replies.list",
    ("wechat_channels", "user-profile-v1"): "wechat_channels.users.get",
    ("wechat_channels", "user-published-videos-v1"): "wechat_channels.users.videos.list",
    ("wechat_channels", "live-user-status-v1"): "wechat_channels.live.users.get",
    # 微信公众号（2026-11-23 按 okdata 官方技术文档逐条核对）
    # 账号搜索按返回条数计费（$0.058/条），其余按次 $0.02，页面上有明确提示。
    # article-search-v1 在老工程里是「公众号文章搜索」，对应 okdata 的微信搜一搜
    # 综合搜索（文章/视频混排，可用 type=article 只要文章）。
    ("wechat_mp", "account-search-v1"): "wechat_mp.accounts.search",
    ("wechat_mp", "account-historical-articles-v1"): "wechat_mp.accounts.articles.list",
    ("wechat_mp", "account-historical-articles-v2"): "wechat_mp.accounts.articles.list",
    ("wechat_mp", "article-details-v1"): "wechat_mp.articles.get",
    ("wechat_mp", "article-metrics-v1"): "wechat_mp.articles.metrics",
    ("wechat_mp", "article-comments-v1"): "wechat_mp.articles.comments.list",
    ("wechat_mp", "article-search-v1"): "wechat.posts.search",
}

#: 老参数名 -> okdata 参数名。只保留这些白名单字段，避免把无意义参数发给
#: 计费接口（okdata 参数错误不扣费，但无谓的请求仍要省掉）。
PARAM_ALIAS: dict[str, str] = {
    "secUid": "id", "sec_uid": "id", "userId": "id", "user_id": "id",
    "authorId": "id", "author_id": "id", "uid": "id", "finderUserName": "id",
    "awemeId": "id", "noteId": "id", "videoId": "id", "itemId": "id",
    "postId": "id", "objectId": "id", "id": "id", "url": "url", "shareUrl": "url",
    "keyword": "query", "query": "query", "q": "query", "keywordStr": "query",
    "maxCursor": "cursor", "max_cursor": "cursor", "cursor": "cursor",
    "nextCursor": "cursor", "pageToken": "cursor",
    # 公众号：主页栏目 / 搜一搜时间窗与内容类型
    "ghid": "id", "gh_id": "id", "biz": "id",
    "publishedWithin": "published_within", "timeRange": "published_within",
}
_PASSTHROUGH = {"id", "url", "query", "cursor", "view", "fields", "sort",
                "lang", "tab", "type", "published_within"}

_ERROR_TEXT: dict[str, str] = {
    "invalid_params": "okdata 参数错误（不扣费），请检查关键词或账号 ID",
    "unauthorized": "okdata Key 无效或未激活，请到设置页填写 okd_ 开头的 Key",
    "insufficient_balance": "okdata 余额不足，请到 okdata 控制台充值后重试",
    "not_found": "okdata 查无此项（这次已按规则计费）",
    "rate_limited": "okdata 请求过于频繁，稍后重试",
    "capability_paused": "okdata 接口暂停（不扣费），稍后重试",
    "settlement_unknown": "okdata 扣费状态未知，请用相同请求重试，不会重复扣费",
    "upstream_failed": "okdata 数据源暂时失败，稍后重试",
    "timeout": "okdata 请求超时，稍后重试",
}

#: 列表类接口把 okdata 的 items 同时挂到老代码常用的几个键上，
#: 这样 _deep_get(resp.data, "feeds"/"notes"/"aweme_list"...) 无需改动。
_LIST_KEYS = ("feeds", "notes", "videoList", "aweme_list", "list",
              "comments", "replies")


def _set(d: dict, key: str, value: Any) -> None:
    """只在有值时写字段，避免把 None 伪装成有效数据。"""
    if value not in (None, "", [], {}):
        d[key] = value


def _decorate(capability: str, obj: Any) -> dict:
    """把 okdata 的统一对象补上老工程读取的别名字段。"""
    if not isinstance(obj, dict):
        return {}
    d = dict(obj)

    # ---- Account（账号信息 / 作品作者）
    name = obj.get("name") or obj.get("username") or ""
    followers = obj.get("followers")
    avatar = obj.get("avatar_url") or obj.get("avatar") or ""
    bio = obj.get("bio") or obj.get("signature") or ""
    _set(d, "nickname", name)
    _set(d, "nick_name", name)
    _set(d, "user_name", name)
    _set(d, "follower_count", followers)
    _set(d, "fans", followers)
    _set(d, "aweme_count", obj.get("posts"))
    _set(d, "total_favorited", obj.get("likes"))
    _set(d, "avatar", avatar)
    _set(d, "signature", bio)
    _set(d, "desc", bio)
    if obj.get("id"):
        for k in ("sec_uid", "uid", "user_id", "author_id"):
            _set(d, k, obj["id"])
        d["user"] = {
            "userid": obj["id"], "id": obj["id"], "nickname": name,
            "nick_name": name, "fans": followers,
            "follower_count": followers, "avatar": avatar,
            "images": [avatar] if avatar else [], "desc": bio,
        }

    # ---- Post（视频 / 笔记 / 帖子）
    author = obj.get("author") if isinstance(obj.get("author"), dict) else {}
    metrics = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else {}
    images = obj.get("images") if isinstance(obj.get("images"), list) else []
    text = obj.get("text") or obj.get("title") or ""
    if obj.get("id"):
        for k in ("aweme_id", "note_id", "video_id", "item_id", "post_id"):
            _set(d, k, obj["id"])
    _set(d, "desc", text)
    _set(d, "title", obj.get("title") or text[:80])
    _set(d, "nickname", author.get("name") or author.get("username") or "")
    if author:
        d["author"] = _decorate("account", author)
        for k in ("sec_uid", "user_id"):
            _set(d, k, author.get("id"))
    _set(d, "digg_count", metrics.get("likes"))
    _set(d, "comment_count", metrics.get("comments"))
    _set(d, "share_count", metrics.get("shares"))
    _set(d, "play_count", metrics.get("views"))
    _set(d, "create_time", obj.get("published_at"))
    _set(d, "cover", images[0] if images else obj.get("cover_url"))
    _set(d, "cover_url", obj.get("cover_url") or (images[0] if images else ""))

    # ---- Comment
    if "comments" in capability or "replies" in capability:
        _set(d, "text", obj.get("text"))
        _set(d, "content", obj.get("text"))
        _set(d, "digg_count", metrics.get("likes"))
        _set(d, "create_time", obj.get("created_at"))

    # ---- LiveRoom：okdata 用字符串 live/offline，老代码认 1/0
    if "live" in capability:
        status = str(obj.get("status") or "").lower()
        if status in ("live", "offline"):
            d["live_status"] = 1 if status == "live" else 0
            d["is_live"] = status == "live"
        _set(d, "room_id", obj.get("id"))
        host = obj.get("host") if isinstance(obj.get("host"), dict) else {}
        _set(d, "nickname", host.get("name") or host.get("username") or "")
        _set(d, "anchor_name", host.get("name") or "")
        _set(d, "started_at", obj.get("started_at"))
    return d


def _translate(capability: str, raw: dict) -> dict:
    """okdata 响应 -> 老工程期望的 data 结构（含别名与分页游标）。"""
    data = raw.get("data")
    if not isinstance(data, dict):
        return {}
    out: dict[str, Any] = {}

    item = data.get("item")
    if isinstance(item, dict):
        decorated = _decorate(capability, item)
        out["item"] = decorated
        out.update({k: v for k, v in decorated.items() if k not in out})

    items = data.get("items")
    if isinstance(items, list):
        decorated_list = [_decorate(capability, x) for x in items if isinstance(x, dict)]
        out["items"] = decorated_list
        for key in _LIST_KEYS:
            out[key] = decorated_list
        out["data"] = {"items": decorated_list}

    page = raw.get("page") if isinstance(raw.get("page"), dict) else {}
    cursor = page.get("next_cursor") or ""
    if page or cursor:
        out["page"] = dict(page)
        _set(out, "next_cursor", cursor)
        _set(out, "max_cursor", cursor)
        _set(out, "cursor", cursor)
        _set(out, "nextCursor", cursor)
        out["has_more"] = bool(page.get("has_more", False))
        out["hasMore"] = out["has_more"]

    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    billing = meta.get("billing") if isinstance(meta.get("billing"), dict) else {}
    if billing:
        out["_billing"] = dict(billing)
    out["_okdata_raw"] = raw
    return out


class OkdataClient:
    """付费数据客户端：对外兼容 JustOneAPICollector 的最小调用面。"""

    provider = "okdata"
    recharge_hint = "okdata 控制台（okdata.online/zh/console/billing/）"

    def __init__(self, key: str, base_url: str = DEFAULT_OKDATA_BASE_URL,
                 timeout: float = 30.0, max_retries: int = 1):
        self.key = (key or "").strip()
        if not self.key:
            raise ValueError("okdata Key 不能为空（格式：okd_...）")
        self.base_url = (base_url or DEFAULT_OKDATA_BASE_URL).rstrip("/")
        self.timeout = float(timeout or 30)
        self.max_retries = max(0, int(max_retries or 1))
        # 设置页保存后会直接改 client.config.*，这里保留同名对象即可。
        self.config = SimpleNamespace(
            timeout=self.timeout, max_retries=self.max_retries,
            max_concurrency=5, cache_enabled=False, base_url=self.base_url,
            token=self.key,
        )
        self.token_invalid = False
        self.token_error = ""
        self._client = httpx.Client(
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.key}",
                     "Accept": "application/json"},
        )

    # ------------------------------------------------------------ 映射
    @staticmethod
    def capability_for(platform: str, endpoint: str) -> str | None:
        slug = PLATFORM_ALIAS.get(str(platform or "").strip())
        if not slug:
            return None
        return ENDPOINT_MAP.get((slug, str(endpoint or "").strip()))

    @staticmethod
    def _query(params: dict[str, Any] | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for src, value in (params or {}).items():
            src = str(src)
            name = PARAM_ALIAS.get(src, src if src in _PASSTHROUGH else "")
            if name not in _PASSTHROUGH or value in (None, "", 0, [], {}):
                continue  # cursor=0 / 空串表示首页，okdata 首页不传 cursor
            if isinstance(value, (list, tuple)):
                value = ",".join(str(x) for x in value)
            out[name] = str(value)
        return out

    # ------------------------------------------------------------ 请求
    def request(self, platform: str, endpoint: str,
                params: dict[str, Any] | None = None) -> ApiResponse:
        capability = self.capability_for(platform, endpoint)
        if not capability:
            # 明确失败：不发请求、不扣费、不冒充空数据。
            msg = (f"okdata 暂不支持该接口（{platform}/{endpoint}），"
                   "可在设置页切回 JustOneAPI 或使用免费通道")
            return ApiResponse(False, "UNSUPPORTED", msg, None,
                               {"code": "UNSUPPORTED", "message": msg, "charged": False})

        url = self.base_url + "/v1/" + capability.replace(".", "/")
        query = self._query(params)
        raw: dict[str, Any] = {}
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.get(url, params=query)
                try:
                    raw = resp.json()
                except Exception:                       # noqa: BLE001
                    raw = {}
                if not isinstance(raw, dict):
                    raw = {}
            except Exception as exc:                    # noqa: BLE001
                if attempt >= self.max_retries:
                    return ApiResponse(False, "TRANSPORT_ERROR",
                                       f"okdata 网络异常：{exc}", None,
                                       {"code": "TRANSPORT_ERROR", "message": str(exc)})
                time.sleep(0.6)
                continue

            code = str(raw.get("code") or "")
            if resp.status_code == 200 and code == "ok":
                self.token_invalid = False
                self.token_error = ""
                return ApiResponse(
                    True, 0, "", _translate(capability, raw), raw,
                )

            # 401/402/400 不重试；429/5xx 才可能重发。
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if code == "unauthorized":
                self.token_invalid = True
                self.token_error = code
            if retryable and attempt < self.max_retries:
                time.sleep(0.8)
                continue

            message = (_ERROR_TEXT.get(code) or
                       f"okdata 调用失败（HTTP {resp.status_code}，{code or '未知错误'}）")
            detail = raw.get("message") or raw.get("reason") or ""
            if detail and detail not in message:
                message += f"：{str(detail)[:120]}"
            return ApiResponse(False, resp.status_code or code, message, None, raw)
        return ApiResponse(False, "UNKNOWN", "okdata 调用失败", None, raw)

    async def request_async(self, platform: str, endpoint: str,
                            params: dict[str, Any] | None = None) -> ApiResponse:
        # 现有调用方都在 Worker 线程里用同步接口；异步保留签名即可。
        return await asyncio.to_thread(self.request, platform, endpoint, params)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:                                # noqa: BLE001
            pass

    def __repr__(self) -> str:
        return f"<OkdataClient base_url={self.base_url!r} key={'okd_***' if self.key else '(空)'}>"
