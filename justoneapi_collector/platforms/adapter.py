"""
多平台适配层 —— 把抖音 / 快手 / 小红书的差异屏蔽掉，
对上层暴露同一套「作品 / 作者 / 评论」模型和同一组方法。

设计原则（重要，改动前请先读）：
1. **不赌字段名**。各家返回结构差异极大，且会随平台改版变化，所以解析
   统一走「按 id 键递归扫描 + 多候选路径取值」，任何一个字段取不到都
   退化为默认值，而不是抛异常。
2. **参数按平台配置**。驼峰 / 下划线混用是本项目踩过的坑（抖音要 secUid
   而不是 sec_uid），所以参数名集中在 PARAM 表里，不散落在业务代码里。
3. 上层（选题挖掘、复盘监控、周报）只依赖本模块，不再直接调平台接口。
4. **在这里统一转存历史快照**。所有取到的作品 / 账号在返回前都会往本机
   历史库留一份（见 history_store.py），因此以后新增的取数方法也自动覆盖。
   代价为零：写库失败一律静默，不改变返回值，调用方无感。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# ------------------------------------------------------- 历史快照（惰性挂载）

_HIST = None
_HIST_LOADED = False


def _history():
    """惰性取得历史库模块。

    为什么惰性：本模块在程序各处都会被 import（含无 GUI 的命令行/测试），
    顶层直接 import 会在不需要落库的场景也建立数据库连接。
    取不到（sqlite 缺失 / 打包异常）就返回 None，之后所有写入自动跳过。
    """
    global _HIST, _HIST_LOADED
    if not _HIST_LOADED:
        _HIST_LOADED = True
        try:
            from .. import history_store
            _HIST = history_store
        except Exception:                           # noqa: BLE001
            _HIST = None
    return _HIST

# ---------------------------------------------------------------- 平台标识

PLATFORM_DISPLAY = {
    "douyin-tiktok-china": "抖音",
    "kuaishou": "快手",
    "xiaohongshu-rednote": "小红书",
}

DISPLAY_TO_PLATFORM = {v: k for k, v in PLATFORM_DISPLAY.items()}

SUPPORTED_PLATFORMS = list(PLATFORM_DISPLAY.keys())


def normalize_platform(name: str) -> str:
    """把「抖音 / douyin / douyin-tiktok-china」等写法统一成 slug。"""
    n = (name or "").strip()
    if n in PLATFORM_DISPLAY:
        return n
    if n in DISPLAY_TO_PLATFORM:
        return DISPLAY_TO_PLATFORM[n]
    low = n.lower()
    for slug, disp in PLATFORM_DISPLAY.items():
        if low in (slug, disp, slug.split("-")[0], low):
            if low == slug or low == disp or low == slug.split("-")[0]:
                return slug
    alias = {
        "dy": "douyin-tiktok-china", "douyin": "douyin-tiktok-china",
        "ks": "kuaishou", "kuaishou": "kuaishou",
        "xhs": "xiaohongshu-rednote", "rednote": "xiaohongshu-rednote",
        "xiaohongshu": "xiaohongshu-rednote", "red": "xiaohongshu-rednote",
    }
    return alias.get(low, n)


# ---------------------------------------------------------------- 数据模型

@dataclass
class AuthorInfo:
    platform: str = ""
    author_id: str = ""
    nickname: str = ""
    fans: int = 0
    total_digg: int = 0
    video_count: int = 0
    avatar: str = ""
    signature: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def fans_text(self) -> str:
        return _compact_num(self.fans)


@dataclass
class PostItem:
    platform: str = ""
    post_id: str = ""
    title: str = ""
    author_id: str = ""
    author_name: str = ""
    author_fans: int = 0
    digg: int = 0
    comment: int = 0
    collect: int = 0
    share: int = 0
    play: int = 0
    duration: int = 0          # 秒
    publish_ts: int = 0
    cover: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def publish_text(self) -> str:
        if not self.publish_ts:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.publish_ts))

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return ""
        m, s = divmod(int(self.duration), 60)
        return f"{m}:{s:02d}" if m else f"0:{s:02d}"

    @property
    def interact(self) -> int:
        """总互动量（点赞+评论+收藏+分享）。"""
        return self.digg + self.comment + self.collect + self.share

    @property
    def is_video(self) -> bool:
        return self.duration > 0 or bool(
            _deep_get(self.raw, "video", "images") is None
        )

    def as_row(self) -> list:
        return [
            PLATFORM_DISPLAY.get(self.platform, self.platform),
            self.title[:60],
            self.author_name,
            self.author_fans,
            self.digg,
            self.comment,
            self.collect,
            self.share,
            self.publish_text,
            self.duration_text,
            self.url,
        ]


@dataclass
class CommentItem:
    platform: str = ""
    comment_id: str = ""
    post_id: str = ""
    user_name: str = ""
    user_id: str = ""
    text: str = ""
    digg: int = 0
    reply_count: int = 0
    publish_ts: int = 0
    raw: dict = field(default_factory=dict)


POST_COLUMNS = ["平台", "标题", "作者", "作者粉丝", "点赞", "评论", "收藏", "分享",
                "发布时间", "时长", "链接"]
COMMENT_COLUMNS = ["平台", "用户", "评论内容", "点赞", "回复数", "时间"]


# ---------------------------------------------------------------- 取值工具

def _compact_num(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(int(n))


def _to_int(v) -> int:
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([万亿wW])?$", s)
    if not m:
        try:
            return int(float(s))
        except ValueError:
            return 0
    base = float(m.group(1))
    unit = m.group(2)
    if unit in ("万", "w", "W"):
        base *= 10_000
    elif unit == "亿":
        base *= 100_000_000
    return int(base)


def _deep_get(obj: Any, *paths: str) -> Any:
    """按多条候选路径取值（支持 "a.b.c"，数字段按列表下标处理），
    返回第一个非空结果。"""
    for p in paths:
        cur = obj
        ok = True
        for part in p.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, (list, tuple)) and part.lstrip("-").isdigit():
                idx = int(part)
                if -len(cur) <= idx < len(cur):
                    cur = cur[idx]
                else:
                    ok = False
                    break
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


# ------------------------------------------------- 响应扫描（结构无关解析）

_POST_ID_KEYS = ("aweme_id", "awemeId", "note_id", "noteId", "video_id", "videoId",
                 "photo_id", "photoId", "id", "noteId")
_POST_BODY_KEYS = ("desc", "title", "note_card", "statistics", "interact_info",
                   "video", "image_list", "images", "caption", "content",
                   "share_info", "text")


def scan_post_dicts(payload: Any, max_depth: int = 9) -> list[dict]:
    """递归扫描响应，收集"看起来像作品对象"的 dict。

    不依赖任何固定的外层结构（aweme_list / business_data / notes …），
    平台改版或字段改名时仍能工作。
    """
    found: list[dict] = []
    seen: set[int] = set()

    def walk(node, depth):
        if depth > max_depth or node is None:
            return
        if isinstance(node, dict):
            has_id = any(k in node for k in _POST_ID_KEYS)
            has_body = any(k in node for k in _POST_BODY_KEYS)
            if has_id and has_body and id(node) not in seen:
                seen.add(id(node))
                found.append(node)
                return          # 命中就不再往里钻，避免嵌套重复
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(payload, 0)
    return found


_COMMENT_ID_KEYS = ("cid", "comment_id", "commentId", "id")
_COMMENT_BODY_KEYS = ("text", "content", "comment_text", "commentText", "desc")


def scan_comment_dicts(payload: Any, max_depth: int = 9) -> list[dict]:
    found: list[dict] = []
    seen: set[int] = set()

    def walk(node, depth):
        if depth > max_depth or node is None:
            return
        if isinstance(node, dict):
            has_id = any(k in node for k in _COMMENT_ID_KEYS)
            has_body = any(k in node for k in _COMMENT_BODY_KEYS)
            if has_id and has_body and id(node) not in seen:
                seen.add(id(node))
                found.append(node)
                return
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(payload, 0)
    return found


# ---------------------------------------------------------------- 字段映射

def parse_post(platform: str, d: dict) -> PostItem:
    """把一个原始作品 dict 解析成统一的 PostItem。"""
    # 小红书的内容常包在 note_card 里，先摊平一次
    flat = dict(d)
    card = _deep_get(d, "note_card", "noteCard")
    if isinstance(card, dict):
        for k, v in card.items():
            flat.setdefault(k, v)

    post_id = str(_deep_get(flat, "aweme_id", "awemeId", "note_id", "noteId",
                            "video_id", "videoId", "photo_id", "photoId", "id") or "")
    # title 优先：小红书标题在 note_card.title，抖音没有 title 会自然回落到 desc，
    # 快手回落到 caption。取错顺序会让小红书把正文当标题。
    title = str(_deep_get(flat, "title", "display_title", "desc", "caption",
                          "content", "text") or "")
    title = title.replace("\n", " ").strip()

    # 作者 ID：抖音在 author.sec_uid，快手是**扁平**的 user_id，
    # 小红书在 user.userid。三家位置完全不同，只能靠候选路径覆盖。
    author_id = str(_deep_get(flat, "author.sec_uid", "author.secUid", "author.uid",
                              "author.id", "author.user_id", "user.sec_uid",
                              "user.id", "user.user_id", "user.userid", "user.red_id",
                              "user_info.sec_uid", "user_info.uid", "user_info.user_id",
                              "user_info.userid",
                              "author_info.user_id", "author_info.id",
                              "user_id", "userId", "userid") or "")
    author_name = str(_deep_get(flat, "author.nickname", "author.name", "author.nickName",
                                "user.nickname", "user.user_name", "user.name",
                                "user_info.nickname", "author_info.nickname",
                                "user_name", "userName", "nickname") or "")
    author_fans = _to_int(_deep_get(
        flat,
        "author.follower_count", "author.followerCount", "author.fans", "author.fans_count",
        "user.follower_count", "user.followerCount", "user.fans", "user.fans_count",
        "user_info.follower_count", "user_info.fans", "user_info.fans_count",
        "author_info.follower_count", "author_info.fans",
        "fans_count", "fans", "follower_count", "followerCount",
    ))

    stats = _deep_get(flat, "statistics", "stats", "interact_info", "interactInfo") or {}
    if not isinstance(stats, dict):
        stats = {}

    def stat(*keys):
        return _to_int(_deep_get(flat, *keys) or _deep_get(stats, *keys))

    digg = stat("digg_count", "diggCount", "like_count", "likeCount", "likes",
                "liked_count", "likedCount", "digg")
    comment = stat("comment_count", "commentCount", "comments", "comment")
    collect = stat("collect_count", "collectCount", "collects", "favorite_count",
                   "favoriteCount", "collected_count")
    share = stat("share_count", "shareCount", "shares", "shared_count")
    play = stat("play_count", "playCount", "plays", "view_count", "viewCount",
                "read_count", "readCount")

    duration_ms = _deep_get(flat, "video.duration", "duration", "video_duration",
                            "video.playDuration")
    duration = 0
    if duration_ms:
        v = _to_int(duration_ms)
        # 抖音是毫秒，小红书/快手多为秒
        duration = v // 1000 if v > 3600 else v

    publish_ts = _to_int(_deep_get(flat, "create_time", "createTime", "publish_time",
                                   "publishTime", "time", "date", "timestamp"))
    if publish_ts and publish_ts > 10_000_000_000:   # 毫秒
        publish_ts //= 1000

    cover = str(_deep_get(flat, "video.cover.url_list.0", "video.origin_cover.url_list.0",
                          "cover.url_list.0", "cover", "image_list.0.url",
                          "images.0.url", "note_card.cover.url",
                          "note_card.image_list.0.url") or "")
    if isinstance(cover, list):
        cover = cover[0] if cover else ""

    url = str(_deep_get(flat, "share_url", "shareUrl", "url", "share_info.share_url",
                        "share_info.shareUrl") or "")
    if not url and post_id:
        url = build_post_url(platform, post_id)

    return PostItem(platform=platform, post_id=post_id, title=title,
                    author_id=author_id, author_name=author_name,
                    author_fans=author_fans, digg=digg, comment=comment,
                    collect=collect, share=share, play=play, duration=duration,
                    publish_ts=publish_ts, cover=str(cover), url=url, raw=d)


def parse_comment(platform: str, d: dict, post_id: str = "") -> CommentItem:
    cid = str(_deep_get(d, "cid", "comment_id", "commentId", "id") or "")
    text = str(_deep_get(d, "text", "content", "comment_text", "commentText",
                         "desc") or "").replace("\n", " ").strip()
    user_name = str(_deep_get(d, "user.nickname", "user_info.nickname", "nickname",
                              "user.name", "author.nickname") or "")
    user_id = str(_deep_get(d, "user.sec_uid", "user.uid", "user.id", "user_id",
                            "user_info.user_id") or "")
    digg = _to_int(_deep_get(d, "digg_count", "diggCount", "like_count",
                             "likeCount", "likes", "digg"))
    reply_count = _to_int(_deep_get(d, "reply_comment_total", "reply_count",
                                    "replyCount", "sub_comment_count", "replies"))
    ts = _to_int(_deep_get(d, "create_time", "createTime", "time", "publish_time"))
    if ts and ts > 10_000_000_000:
        ts //= 1000
    return CommentItem(platform=platform, comment_id=cid, post_id=post_id,
                       user_name=user_name, user_id=user_id, text=text,
                       digg=digg, reply_count=reply_count, publish_ts=ts, raw=d)


def build_post_url(platform: str, post_id: str) -> str:
    if platform == "douyin-tiktok-china":
        return f"https://www.douyin.com/video/{post_id}"
    if platform == "kuaishou":
        return f"https://www.kuaishou.com/short-video/{post_id}"
    if platform == "xiaohongshu-rednote":
        return f"https://www.xiaohongshu.com/explore/{post_id}"
    return ""


# ---------------------------------------------------------------- 参数配置

class ParamProfile:
    """一个平台的接口与参数命名配置。"""

    def __init__(self, platform: str, search: str, search_kw: str, search_page: str,
                 search_sort: str | None, user_posts: str, user_posts_id: str,
                 user_posts_cursor: str, profile: str, profile_id: str,
                 comments: str, comments_id: str, comments_cursor: str,
                 detail: str, detail_id: str, hot: str | None,
                 user_search: str | None, extra_search: dict | None = None,
                 count_key: str = "count", search_page_mode: str = "page",
                 search_publish: str | None = None):
        self.platform = platform
        self.search = search
        self.search_kw = search_kw
        self.search_page = search_page
        # "page"   = 页码，第 N 页就传 N（快手 / 小红书）
        # "offset" = 条目偏移，第 N 页要传 (N-1)*每页条数（抖音的 offset）
        # 这一条踩过坑：抖音拿页码当 offset 传，第 2 页只往后挪一条，
        # 翻 5 页等于白花 5 倍额度却几乎拿不到新作品。
        self.search_page_mode = search_page_mode
        self.search_sort = search_sort
        self.user_posts = user_posts
        self.user_posts_id = user_posts_id
        self.user_posts_cursor = user_posts_cursor
        self.profile = profile
        self.profile_id = profile_id
        self.comments = comments
        self.comments_id = comments_id
        self.comments_cursor = comments_cursor
        self.detail = detail
        self.detail_id = detail_id
        self.hot = hot
        self.user_search = user_search
        self.extra_search = extra_search or {}
        self.count_key = count_key
        # 搜索接口的"发布时间范围"参数名（抖音 video-search-v4 为 publish_time：
        # 0 不限 / 1 一天 / 7 一周 / 180 半年）。其它平台接口文档未开放此参数，
        # 留 None 即不传，避免拿抖音的参数名去砸别人的接口导致 400。
        self.search_publish = search_publish


# 说明：以下参数名来自接口文档与本项目已验证的抖音调用（抖音确认用驼峰）。
# 若平台调整，改这张表即可，业务代码无需改动。
PROFILES: dict[str, ParamProfile] = {
    "douyin-tiktok-china": ParamProfile(
        platform="douyin-tiktok-china",
        search="video-search-v4", search_kw="keyword", search_page="offset",
        search_sort="sort_type", search_page_mode="offset",
        user_posts="user-published-videos-v3", user_posts_id="secUid",
        user_posts_cursor="maxCursor",
        profile="user-profile-v3", profile_id="secUid",
        comments="video-comments-v1", comments_id="awemeId", comments_cursor="cursor",
        # 实测（2026-10-06）：同一平台的评论接口要 awemeId，详情接口却要 videoId，
        # 两个接口参数名并不统一。不要"顺手统一"，会直接 400。
        detail="video-details-v2", detail_id="videoId",
        hot="hot-search-v1", user_search="user-search-v2",
        count_key="count", search_publish="publish_time",
    ),
    "kuaishou": ParamProfile(
        platform="kuaishou",
        search="video-search-v2", search_kw="keyword", search_page="page",
        search_sort=None,
        user_posts="user-published-videos-v2", user_posts_id="userId",
        user_posts_cursor="pcursor",
        profile="user-profile-v1", profile_id="userId",
        # 实测：快手评论与详情都要 videoId，不是 photoId
        comments="video-comments-v1", comments_id="videoId", comments_cursor="cursor",
        detail="video-details-v2", detail_id="videoId",
        hot=None, user_search="user-search-v2",
        count_key="count",
    ),
    "xiaohongshu-rednote": ParamProfile(
        platform="xiaohongshu-rednote",
        search="note-search-v2", search_kw="keyword", search_page="page",
        search_sort="sort",
        user_posts="user-published-notes-v4", user_posts_id="userId",
        user_posts_cursor="cursor",
        profile="user-profile-v3", profile_id="userId",
        comments="note-comments-v2", comments_id="noteId", comments_cursor="cursor",
        detail="note-details-v1", detail_id="noteId",
        hot="hot-search-v1", user_search="user-search-v2",
        count_key="count",
    ),
}


# ---------------------------------------------------------------- 参数名自校准
#
# 本项目已经两次踩到"同一平台不同接口参数名不统一"的坑（抖音详情要 videoId
# 而评论要 awemeId；快手两个都要 videoId 而不是 photoId）。与其每次改版都发
# 新版，不如让程序自己听服务器的话：接口报 "param [xxx] can not be empty"
# 时，说明我们传的键名不对 —— 直接改用服务器要的那个键名重试，并记住它。

_ID_PARAM_RE = re.compile(r"param\s*\[(\w+)\]\s*can not be empty", re.I)

# {endpoint: 正确参数名}
_PARAM_OVERRIDES: dict[str, str] = {}
_OVERRIDES_LOADED = False


def _override_file():
    try:
        import os
        from pathlib import Path
        base = os.getenv("APPDATA") or str(Path.home())
        d = Path(base) / "JustOneAPICollector"
        d.mkdir(parents=True, exist_ok=True)
        return d / "param_overrides.json"
    except Exception:       # noqa: BLE001
        return None


def load_param_overrides() -> dict[str, str]:
    """读取上次自校准的结果（跨会话生效）。"""
    global _OVERRIDES_LOADED
    if _OVERRIDES_LOADED:
        return _PARAM_OVERRIDES
    _OVERRIDES_LOADED = True
    f = _override_file()
    if f and f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _PARAM_OVERRIDES.update({str(k): str(v) for k, v in d.items()})
        except Exception:   # noqa: BLE001
            pass
    return _PARAM_OVERRIDES


def save_param_overrides():
    f = _override_file()
    if not f:
        return
    try:
        f.write_text(json.dumps(_PARAM_OVERRIDES, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except Exception:       # noqa: BLE001
        pass


# ---------------------------------------------------------------- 适配器

class PlatformAdapter:
    """对单个平台提供统一方法。所有返回都做了降级处理，绝不抛异常给上层。"""

    def __init__(self, client, platform: str):
        self.client = client
        self.platform = normalize_platform(platform)
        self.profile = PROFILES.get(self.platform)
        self.last_error = ""
        self.param_notes: list[str] = []

    @property
    def display(self) -> str:
        return PLATFORM_DISPLAY.get(self.platform, self.platform)

    def supported(self, op: str) -> bool:
        if not self.profile:
            return False
        return bool(getattr(self.profile, op, None))

    # -- 历史快照 --------------------------------------------------
    # 两个 _snap* 是全公司唯一的挂载点：**在这儿挂一次，所有页面都能沉淀**。
    # 任何异常都必须吞掉 —— 落库只是顺手记一笔，绝不能反之把采集搞挂。

    def _snap(self, posts, source: str) -> None:
        """把一批作品写入本机历史库（每天一个数据点，同日覆盖）。

        **这里是全局唯一的挂载点**：在这儿挂一次，选题挖掘 / 关键词抓取 /
        竞品体检 / 批量导入等所有页面都能自动沉淀数据，不必逐个改 UI。
        """
        hs = _history()
        if hs is None or not posts:
            return
        try:
            hs.snap_posts(posts, source=f"{self.display}/{source}")
        except Exception:                           # noqa: BLE001
            pass

    def _snap_author(self, info, source: str) -> None:
        """把一个账号写入本机历史库。"""
        hs = _history()
        if hs is None or info is None:
            return
        try:
            hs.snap_authors([info], source=f"{self.display}/{source}")
        except Exception:                           # noqa: BLE001
            pass

    # -- 底层调用 --------------------------------------------------

    def _call(self, endpoint: str, params: dict, id_value: str = ""):
        """调用接口。id_value 传入本次的关键 ID，用于失败时自动校准参数名。"""
        load_param_overrides()
        want = _PARAM_OVERRIDES.get(endpoint)
        if want and id_value:
            params = self._rekey(params, id_value, want)

        resp = self._request(endpoint, params)
        ok, code, msg = self._unpack(resp)
        if ok:
            self.last_error = ""
            return getattr(resp, "data", None)

        # 参数名不对 → 按服务器要求改名重试一次，并记住
        if id_value and msg:
            m = _ID_PARAM_RE.search(str(msg))
            if m:
                need = m.group(1)
                if params.get(need) != id_value:
                    retry = self._rekey(params, id_value, need)
                    r2 = self._request(endpoint, retry)
                    ok2, code2, msg2 = self._unpack(r2)
                    if ok2:
                        _PARAM_OVERRIDES[endpoint] = need
                        save_param_overrides()
                        self.param_notes.append(
                            f"{self.display}/{endpoint}: 参数 {need} 由自校准确定")
                        self.last_error = ""
                        return getattr(r2, "data", None)
                    code, msg = code2, msg2

        self.last_error = f"{self.display}/{endpoint} 失败 [{code}] {msg}"
        return None

    def _request(self, endpoint: str, params: dict):
        try:
            return self.client.request(self.platform, endpoint, params)
        except Exception as e:      # noqa: BLE001
            self.last_error = f"{self.display}/{endpoint} 请求异常: {e}"
            return None

    @staticmethod
    def _unpack(resp) -> tuple[bool, object, str]:
        if resp is None:
            return False, "?", "无响应"
        ok = bool(getattr(resp, "success", False))
        return ok, getattr(resp, "code", "?"), getattr(resp, "message", "") or ""

    @staticmethod
    def _rekey(params: dict, id_value: str, want: str) -> dict:
        """把承载 id_value 的那个键改名成服务器要的 want。"""
        out = {k: v for k, v in params.items() if v != id_value}
        out[want] = id_value
        self._snap(out, "账号搜索")
        return out

    def _as_list(self, data) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 有些接口把列表放在 data.list / data.items / data.data
            for key in ("list", "items", "data", "notes", "videos", "aweme_list"):
                v = data.get(key)
                if isinstance(v, list):
                    return v
            return [data]
        return []

    # -- 业务方法 --------------------------------------------------

    def search_posts(self, keyword: str, page=1, count=20, sort=None,
                     publish_time=None) -> list[PostItem]:
        """按关键词搜索作品。

        sort / publish_time 只有在平台支持时才传（见 PROFILES 的
        search_sort / search_publish），不支持的会静默忽略——
        宁可少一个筛选维度，也不能拿错参数名去砸接口导致整次失败。
        """
        p = self.profile
        if not p or not p.search:
            self.last_error = f"{self.display} 暂不支持作品搜索"
            return []
        page_no = max(1, int(page or 1))
        page_val = (page_no - 1) * count if p.search_page_mode == "offset" else page_no
        params = {p.search_kw: keyword, p.count_key: count, p.search_page: page_val}
        if sort is not None and p.search_sort:
            params[p.search_sort] = sort
        if publish_time is not None and p.search_publish:
            params[p.search_publish] = publish_time
        params.update(p.extra_search)
        data = self._call(p.search, params)
        dicts = scan_post_dicts(data) if data is not None else []
        items = [parse_post(self.platform, d) for d in dicts]
        # 抖音搜索会混入非视频卡片（type != 1 的关联词），过滤掉空 id
        out = [it for it in items if it.post_id][:count]
        self._snap(out, "作品搜索")
        return out

    def user_posts(self, author_id: str, cursor="", count=20) -> tuple[list[PostItem], str, bool]:
        """返回 (作品列表, 下一页游标, 是否还有更多)。"""
        p = self.profile
        if not p:
            return [], "", False
        params = {p.user_posts_id: author_id, p.count_key: count}
        if cursor:
            params[p.user_posts_cursor] = cursor
        data = self._call(p.user_posts, params, id_value=author_id)
        if data is None:
            return [], "", False
        dicts = scan_post_dicts(data)
        items = [parse_post(self.platform, d) for d in dicts if parse_post(self.platform, d).post_id]
        nxt = ""
        more = False
        if isinstance(data, dict):
            nxt = str(data.get("max_cursor") or data.get("nextCursor")
                      or data.get("pcursor") or data.get("cursor")
                      or data.get("next_cursor") or data.get("lastCursor") or "")
            more = bool(data.get("has_more") or data.get("hasMore"))
            if not more and nxt and nxt not in ("no_more", "0"):
                more = True
        self._snap(items, "账号作品")
        return items, nxt, more

    def user_profile(self, author_id: str) -> AuthorInfo | None:
        p = self.profile
        if not p:
            return None
        data = self._call(p.profile, {p.profile_id: author_id}, id_value=author_id)
        if data is None:
            return None
        info = self._profile_from(data, author_id)
        self._snap_author(info, "账号主页")
        return info

    def _profile_from(self, data, author_id: str) -> AuthorInfo | None:
        """把原始资料响应解析成 AuthorInfo（单独抽出，便于离线测试）。"""
        if not isinstance(data, dict):
            return None
        # 抖音/小红书把资料放在 user / data 下，快手放在 userProfile / result 下
        node = _deep_get(data, "user", "userProfile", "user_profile", "user_info",
                         "author_info", "profile", "data", "result") or data
        if not isinstance(node, dict):
            return None
        return AuthorInfo(
            platform=self.platform,
            author_id=str(_deep_get(node, "sec_uid", "secUid", "uid", "id", "userId",
                                    "user_id", "userid") or author_id),
            nickname=str(_deep_get(node, "nickname", "nickName", "userName", "user_name",
                                   "name", "remark_name") or ""),
            fans=_to_int(_deep_get(node, "follower_count", "followerCount", "fans",
                                   "fans_count", "fansCount", "fan", "fansNum")),
            total_digg=_to_int(_deep_get(node, "total_favorited", "totalFavorited",
                                         "total_digg", "liked_count", "likeCount")),
            video_count=_to_int(_deep_get(node, "aweme_count", "awemeCount", "video_count",
                                          "note_count", "works_count", "photo_count",
                                          "photoCount")),
            avatar=str(_deep_get(node, "avatar_thumb.url_list.0", "avatar", "avatar_url",
                                 "avatar_larger.url_list.0", "headurl", "headUrl",
                                 "images") or ""),
            signature=str(_deep_get(node, "signature", "desc", "bio", "user_text") or ""),
            raw=data,
        )

    def post_comments(self, post_id: str, cursor="", count=20,
                      sort_type: int | None = None) -> tuple[list[CommentItem], str, bool]:
        p = self.profile
        if not p:
            return [], "", False
        params = {p.comments_id: post_id, p.count_key: count}
        if cursor:
            params[p.comments_cursor] = cursor
        if sort_type is not None:
            params["sortType"] = sort_type
        data = self._call(p.comments, params, id_value=post_id)
        if data is None:
            return [], "", False
        dicts = scan_comment_dicts(data)
        items = [parse_comment(self.platform, d, post_id) for d in dicts]
        items = [c for c in items if c.text]
        nxt = ""
        more = False
        if isinstance(data, dict):
            nxt = str(data.get("cursor") or data.get("nextCursor")
                      or data.get("pcursor") or data.get("max_cursor")
                      or data.get("next_cursor") or "")
            more = bool(data.get("has_more") or data.get("hasMore"))
        return items, nxt, more

    def post_detail(self, post_id: str) -> PostItem | None:
        p = self.profile
        if not p:
            return None
        data = self._call(p.detail, {p.detail_id: post_id}, id_value=post_id)
        if data is None:
            return None
        dicts = scan_post_dicts(data)
        if not dicts:
            return None
        item = parse_post(self.platform, dicts[0])
        self._snap([item], "作品详情")
        return item

    def hot_search(self, **kw) -> list[str]:
        p = self.profile
        if not p or not p.hot:
            return []
        data = self._call(p.hot, kw)
        if data is None:
            return []
        out: list[str] = []
        node = data
        if isinstance(data, dict):
            node = _deep_get(data, "data", "list", "items", "word_list") or data
        if isinstance(node, list):
            for it in node:
                if isinstance(it, dict):
                    w = _deep_get(it, "word", "title", "name", "hot_word", "keyword")
                    if w:
                        out.append(str(w))
                elif isinstance(it, str):
                    out.append(it)
        return out

    def user_search(self, keyword: str, page=1) -> list[AuthorInfo]:
        p = self.profile
        if not p or not p.user_search:
            return []
        data = self._call(p.user_search, {"keyword": keyword, "page": page})
        if data is None:
            return []
        out: list[AuthorInfo] = []
        # 抖音的博主搜索结果藏在 raw_data 字符串里，特殊处理
        for d in self._flatten_user_candidates(data):
            out.append(AuthorInfo(
                platform=self.platform,
                author_id=str(_deep_get(d, "sec_uid", "secUid", "uid", "id", "userId",
                                        "user_id") or ""),
                nickname=str(_deep_get(d, "nickname", "nickName", "name") or ""),
                fans=_to_int(_deep_get(d, "follower_count", "followerCount", "fans")),
                raw=d,
            ))
        out = [a for a in out if a.author_id]
        hs = _history()
        if hs is not None and out:
            try:
                hs.snap_authors(out, source=f"{self.display}/账号搜索")
            except Exception:                       # noqa: BLE001
                pass
        return out

    @staticmethod
    def _flatten_user_candidates(data) -> list[dict]:
        """抖音的 user-search 结果里 user_info 常以 JSON 字符串嵌在 raw_data 中。"""
        out: list[dict] = []

        def walk(node, depth=0):
            if depth > 8 or node is None:
                return
            if isinstance(node, dict):
                if any(k in node for k in ("sec_uid", "secUid", "uid", "userId", "user_id")) \
                        and any(k in node for k in ("nickname", "nickName", "name")):
                    out.append(node)
                    return
                raw = node.get("raw_data") or node.get("rawData")
                if isinstance(raw, str) and raw.strip().startswith("{"):
                    try:
                        walk(json.loads(raw), depth + 1)
                    except ValueError:
                        pass
                for v in node.values():
                    walk(v, depth + 1)
            elif isinstance(node, list):
                for v in node:
                    walk(v, depth + 1)

        walk(data)
        return out


def get_adapter(client, platform: str) -> PlatformAdapter:
    return PlatformAdapter(client, platform)


# ---------------------------------------------------------------- 字段校准

def probe_platform(client, platform: str, probe_post_id: str = "",
                   log: Callable[[str], None] = print) -> dict:
    """用真实接口跑一遍关键请求，确认参数名与字段解析是否对得上。

    覆盖 5 类接口：作品搜索、作品详情、评论拉取、作者资料、博主作品。
    返回 {操作: {ok, error, sample}}。
    """
    ad = get_adapter(client, platform)
    report: dict[str, dict] = {}

    def record(op, ok, sample=""):
        report[op] = {"ok": bool(ok), "error": ad.last_error, "sample": sample[:400]}
        tail = ""
        if not ok:
            tail = f" —— {ad.last_error or '无数据返回'}"
        log(f"{'[OK]  ' if ok else '[失败]'} {op}{tail}")

    posts = ad.search_posts("测试", count=3)
    record("作品搜索", bool(posts),
           json.dumps([p.as_row()[:6] for p in posts], ensure_ascii=False))
    if not posts:
        log("       ↑ 搜索无结果，后续检查跳过")
        return report

    pid = probe_post_id or posts[0].post_id
    log(f"       样本作品 ID={pid}  作者={posts[0].author_name or '?'}"
        f"  作者ID={posts[0].author_id or '(空)'}")

    detail = ad.post_detail(pid)
    record("作品详情", detail is not None,
           json.dumps(detail.as_row()[:6], ensure_ascii=False) if detail else "")

    cmts, _, _ = ad.post_comments(pid, count=3)
    record("评论拉取", bool(cmts),
           json.dumps([c.text[:40] for c in cmts], ensure_ascii=False))

    author_id = posts[0].author_id or (detail.author_id if detail else "")
    if author_id:
        prof = ad.user_profile(author_id)
        ok_p = prof is not None
        record("作者资料", ok_p,
               f"{prof.nickname} 粉丝{prof.fans}" if prof else "")
        vids, _, _ = ad.user_posts(author_id, count=5)
        record("博主作品", bool(vids),
               f"共 {len(vids)} 条，首条：{vids[0].title[:30]}" if vids else "")
    else:
        line = "作者资料"
        report[line] = {"ok": False, "error": "搜索结果的作者ID为空，无法继续",
                        "sample": ""}
        log(f"[失败] {line} —— 搜索结果的作者ID为空（解析层没取到）")

    for note in ad.param_notes:
        log(f"[校准] {note}")

    return report
