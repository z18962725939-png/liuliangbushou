"""
Video downloader module for self-media platforms.

Supports no-watermark video download from Douyin (抖音) and provides
three workflows:
  1. Blogger homepage batch download  — search blogger → list videos → batch download
  2. Single video download            — paste share link / video ID → resolve → download
  3. Keyword search download          — search by keyword → select → download

Key field facts (verified by live API calls):
  - aweme_list[].video.play_addr.url_list[0]      => no-watermark mp4 URL
  - aweme_list[].video.download_addr.url_list[0]  => watermarked URL (avoid)
  - aweme_list[].aweme_id, desc, create_time, statistics.digg_count, author.nickname
  - Pagination: maxCursor (use returned max_cursor), has_more == 1 means more pages
  - Blogger search: business_data[].data.raw_data is a JSON string; user_info.sec_uid inside
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import httpx

from . import branding
from .client import JustOneAPICollector

try:                                             # 本机配额闸门的业务码（-9001）
    from .quota import QUOTA_EXCEEDED_CODE
except Exception:                                # noqa: BLE001
    QUOTA_EXCEEDED_CODE = -9001


# ----------------------------------------------------------------------
#  多平台下载配置（抖音 / 小红书 / 快手 / TikTok）
# ----------------------------------------------------------------------

# 各平台对应的 justoneapi 平台 slug 与接口名（与 platforms/registry.py 对齐）。
# 下载模块据此把同一套"搜博主 / 列作品 / 取详情 / 搜关键词"流程路由到正确接口。
DOWNLOAD_PLATFORMS: dict[str, dict] = {
    "douyin": {
        "slug": "douyin-tiktok-china", "label": "抖音", "home": "douyin.com",
        "video_search": "video-search-v4", "user_search": "user-search-v2",
        "user_videos": "user-published-videos-v1", "video_detail": "video-details-v2",
        "user_profile": "user-profile-v3", "share_resolve": "share-link-resolution-v1",
        "domestic_blocked": False,
    },
    "xiaohongshu": {
        "slug": "xiaohongshu-rednote", "label": "小红书", "home": "xiaohongshu.com",
        "video_search": "note-search-v2", "user_search": "user-search-v2",
        "user_videos": "user-published-notes-v4", "video_detail": "note-details-v6",
        "user_profile": "user-profile-v3", "share_resolve": "share-link-resolution-v1",
        "domestic_blocked": False,
    },
    "kuaishou": {
        "slug": "kuaishou", "label": "快手", "home": "kuaishou.com",
        "video_search": "video-search-v2", "user_search": "user-search-v2",
        "user_videos": "user-published-videos-v2", "video_detail": "video-details-v2",
        "user_profile": "user-profile-v1", "share_resolve": "share-link-resolution-v1",
        "domestic_blocked": False,
    },
    "tiktok": {
        "slug": "tiktok", "label": "TikTok", "home": "tiktok.com",
        "video_search": "post-search-v1", "user_search": "user-search-v2",
        "user_videos": "user-published-posts-v1", "video_detail": "post-details-v1",
        "user_profile": "user-profile-v1", "share_resolve": "share-link-resolution-v1",
        "domestic_blocked": True,
    },
}


# 免费通道被自适应停用时的原因标记（主流程据此不把它当"解析失败"展示给用户）
_FREE_DISABLED_NOTE = "免费通道本轮已停用"

# ★ 下载 CDN 的 Referer 必须按平台发（v1.24.6）：
#   小红书 CDN 见到 `Referer: douyin.com` 直接 403；反过来抖音见小红书的也拒。
#   认不出的平台干脆不发这个头（视频号四种组合实测全 200，故意不登记）。
_PLATFORM_REFERER = {
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/",
    "kuaishou": "https://www.kuaishou.com/",
    "tiktok": "https://www.tiktok.com/",
    "bilibili": "https://www.bilibili.com/",
    "weibo": "https://weibo.com/",
}

# Windows 写盘 errno → 人话（客户看不懂 errno 5 是什么）
_WINERR_TEXT = {
    2: "路径不存在",
    3: "路径不存在",
    5: "没有写入权限",
    19: "磁盘处于写保护",
    21: "设备未就绪（U 盘/移动硬盘拔了？）",
    32: "文件正被其它程序占用",
    33: "文件正被其它程序占用",
    39: "磁盘空间不足",
    112: "磁盘空间不足",
    123: "文件名不合法",
    206: "路径过长（超过 Windows 260 字符上限）",
    267: "保存目录无效",
}

# 这几个 WinError 属于网络问题（不是写盘问题），别误导客户去查磁盘
_NET_WINERR = {10048, 10049, 10060, 10061, 10064, 10065, 11001, 10013}

# 标题里的话题标签：#ai #ui设计 这种，对「认出这个文件」毫无帮助
_HASHTAG_RE = re.compile(r"#[^\s#]+")


def _deep_get(obj: Any, *paths: str) -> Any:
    """按多条候选路径取值（支持 "a.b.c"，数字段按列表下标处理），返回第一个非空。"""
    for p in paths:
        cur = obj
        ok = True
        for part in p.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return None


def platform_from_url(url: str) -> str | None:
    """从链接域名识别所属平台；无法识别返回 None。"""
    u = (url or "").lower()
    if "xiaohongshu.com" in u or "xhslink.com" in u:
        return "xiaohongshu"
    if "kuaishou.com" in u or "kuaishou.cn" in u:
        return "kuaishou"
    if "tiktok.com" in u:
        return "tiktok"
    if any(h in u for h in ("douyin.com", "iesdouyin.com", "v.douyin.com")):
        return "douyin"
    # ★ 微信两形态必须一起认（v1.24.6）：/sph/ 是视频号，其余是公众号文章。
    #   只认一半的后果是下载器按「未知平台」发默认 Referer，视频号 CDN 直接 403。
    if "weixin.qq.com" in u:
        return "weixin_channels" if "/sph/" in u else "weixin_mp"
    return None






def _to_int_safe(v) -> int:
    """把各种形态的计数/时长转成 int（与 platforms/adapter._to_int 行为一致）。

    与 adapter._to_int 的唯一区别：任何无法解析的脏值都返回 0，不抛异常，
    以便 best-effort 的多平台字段解析在脏数据下也能稳健产出 VideoInfo/BloggerInfo。
    """
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
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "万":
        num *= 10_000
    elif unit == "亿":
        num *= 100_000_000
    elif unit in ("w", "W"):
        num *= 10_000
    return int(num)


@dataclass
class BloggerInfo:
    """A blogger search result."""
    sec_uid: str = ""
    nickname: str = ""
    uid: str = ""
    follower_count: int = 0
    avatar_url: str = ""
    signature: str = ""
    platform: str = "douyin"



@dataclass
class VideoInfo:
    """A downloadable video's metadata.

    ⚠️ 所有字段都有默认值 —— 这是刻意的。
    这个 dataclass 有 4 条构造路径（API 详情 / HTML 快速提取 / HTML 完整解析 /
    多平台通用解析），任何一条多传或少传一个字段都会当场抛 TypeError。
    曾经 HTML 提取路径传了 comment_count 而此处没有该字段，导致「带登录 Cookie
    时解析任何作品链接都失败」的致命 bug（异常还被 catch 成了不起眼的提示）。
    全部给默认值后，将来再加字段也不会把整条链路炸掉。
    新字段一律追加在末尾，保证位置参数调用的兼容性。
    """
    video_id: str = ""
    desc: str = ""
    author: str = ""
    sec_uid: str = ""
    create_time: int = 0
    duration: int = 0          # milliseconds
    digg_count: int = 0
    cover_url: str = ""
    no_watermark_url: str = ""
    share_url: str = ""
    image_list: list[str] = field(default_factory=list)
    platform: str = "douyin"
    comment_count: int = 0
    share_count: int = 0



def _safe_filename(text: str, max_len: int = 50) -> str:
    """Strip characters that are illegal in Windows filenames."""
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", text).strip()
    return cleaned[:max_len] if len(cleaned) > max_len else cleaned or "untitled"

































def _oserror_reason(exc: BaseException, path: "Path | str" = "") -> str:
    """把 OSError 翻成能照着做的中文，带错误码与实际路径。

    只给"保存目录不可写"等于没说 —— 客户既不知道该改哪个目录，
    也不知道是权限、磁盘满还是被杀软锁住了。
    """
    win = getattr(exc, "winerror", None)
    err = getattr(exc, "errno", None)
    code = win if win is not None else err
    if win in _NET_WINERR:
        return (f"网络被系统拦下(代码 {win})：防火墙/杀毒软件的联网拦截，"
                "或代理端口不可用 —— 放行本程序或关掉代理后重试")
    text = _WINERR_TEXT.get(win) or _WINERR_TEXT.get(err)
    if text is None:
        text = {13: "没有写入权限", 28: "磁盘空间不足", 36: "路径过长",
                2: "路径不存在", 22: "文件名不合法"}.get(err, "写入失败")
    tail = f"｜路径 {path}" if path else ""
    return f"写入失败：{text}(代码 {code}){tail}"


def writable_problem(target: "Path | str") -> str:
    """真写一个临时文件确认目录可写。返回 "" 表示可写，否则返回原因。

    为什么要真写：目录存在、属性正常、`os.access(W_OK)` 全通过，
    但杀软/同步盘/受控文件夹访问照样能把 CreateFile 拦下来 ——
    只有真建一个文件才靠得住。
    """
    d = Path(target)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _oserror_reason(e, d)
    probe = d / f".trafficcatcher_write_{os.getpid()}_{threading.get_ident()}.tmp"
    try:
        with open(probe, "wb") as f:
            f.write(b"ok")
        return ""
    except OSError as e:
        return _oserror_reason(e, d)
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def fallback_save_dirs() -> list[Path]:
    """备用保存目录：文档 → 下载 → 用户主目录 → LOCALAPPDATA。

    优先"客户找得到"的位置，D 盘/移动硬盘/按项目配置的目录出问题时兜底。
    """
    home = Path.home()
    name = f"{branding.APP_NAME}下载"
    out: list[Path] = []
    for base in (home / "Documents", home / "Downloads", home,
                 Path(os.environ.get("LOCALAPPDATA") or home)):
        cand = base / name
        if all(cand != x for x in out):
            out.append(cand)
    return out






def _clean_desc_for_name(desc: str, max_len: int = 40) -> str:
    """作品标题 → 文件名主干：砍掉 #话题标签，只留第一行。

    ★ 不清洗的下场（2026-10-25 微信视频号实测）：
        AI做的UI总有股AI味？这5个Skill直接救命_#ai #ai工具 #ui设计 #UI设计师 #_AM94sIUqvX.mp4
      96 个字符，一屏看不完，Windows 路径 260 限制下放深一点还会写不进去。
      话题标签对"认出这个文件"毫无帮助 —— 客户找的是内容，不是标签。
    """
    s = str(desc or "").replace("\r", "\n")
    first = s.split("\n", 1)[0]
    # 先剥 #标签再折叠空白 —— 顺序反了会留下一串孤立空格
    cleaned = re.sub(r"\s+", " ", _HASHTAG_RE.sub("", first)).strip(" -_")
    if cleaned:
        return cleaned[:max_len]
    # 整条都是话题标签（少数）：退一步保留原文，总比叫 untitled 强
    return re.sub(r"\s+", " ", s).strip()[:max_len]


def _video_stem(video, max_author: int = 30) -> str:
    """"作者_标题_作品ID" 的文件名主干。

    ★ v1.24.6：作者拿不到时**把这一段整个去掉**，而不是补一个 "unknown"。
      小红书 / 快手的解析结果里作者常常是空的，以前会变成
      `untitled_麻辣酱蟹_ID.mp4` —— 批量下 200 条时整屏都是 untitled，
      既看不出是谁的作品，也没法按作者排序。
    """
    author = _safe_filename(video.author, max_author) if (video.author or "").strip() else ""
    # ★ 先过 _clean_desc_for_name 砍掉 #话题标签再取文件名用字，
    #   否则标题里一串 #ai #ai工具 会把文件名撑到一屏看不完（微信视频号实测 96 字符）。
    desc = _safe_filename(_clean_desc_for_name(video.desc), 50) or "untitled"
    vid = str(video.video_id or "").strip()
    return "_".join(p for p in (author, desc, vid) if p)


def _bad_windows_name(name: str) -> str:
    """文件名本身是否会让 open() 直接失败。返回 "" 表示没问题。"""
    if not name or name in (".", ".."):
        return "为空"
    stem = name.split(".")[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"COM\d|LPT\d", stem):
        return "是 Windows 保留名"
    if name[-1] in " .":
        return "以空格或点结尾"
    if re.search(r'[\\/:*?"<>|\x00-\x1f]', name):
        return "含非法字符"
    return ""


def video_file_path(video, save_dir: Path) -> tuple[Path, str]:
    """算出一个视频「将会落到」的路径 —— 下载行为的唯一命名口径。

    ★ 2026-11-19：下载页要在列表里显示「是否已下载」，判断必须与
      `_download_video_file_once` 真正跳过下载时用的文件名**完全同源**。
      以前这段命名规则只写在下载函数里，界面若照着字面另抄一遍，
      一旦命名规则改动（如 v1.24.6 的 _video_stem 就与这里不同），
      就会出现「列表说没下、点下载却跳过」的错位。所以抽成单一函数：
      下载器自己调它，界面也调它，从此不可能漂移。

    返回 (最终路径, 改名说明)。改名说明为 "" 表示文件名原样可用。
    """
    # getattr 兜底：界面状态刷新拿到的可能是外部传入的轻量对象（如门禁用
    # SimpleNamespace），缺字段时按空串处理，绝不让「刷新状态」把整页带崩。
    author = _safe_filename(getattr(video, "author", "") or "", 30) or "unknown"
    desc = _safe_filename(getattr(video, "desc", "") or "", 50)
    filename = f"{author}_{desc}_{getattr(video, 'video_id', '')}.mp4"
    save_path = save_dir / filename
    # 文件名本身会让 open() 失败的（超长/保留名/结尾点号），换短名
    return VideoDownloader._prepare_path(save_dir, save_path, video.video_id)


def video_is_downloaded(video, save_dir) -> bool:
    """这个视频在给定目录里是否已有完整文件（下载器跳过的同一判据）。"""
    if video is None or not save_dir:
        return False
    path, _ = video_file_path(video, Path(save_dir))
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


class VideoDownloader:
    """Download no-watermark videos from Douyin."""

    SAVE_SUBDIR = "JustOneAPIVideos"

    # 批量下载默认并发数（download_videos 未显式传 max_workers 时用）。
    DEFAULT_DOWNLOAD_WORKERS = 3

    # 免费解析连续「没拿到地址」多少次才熔断。
    # ★ 6 不是随手写的：3 会把整批打死（182 条里前 3 条坏链 → 后面 179 条
    #   全部秒败，客户看到的是「作品已删除」），6 才能扛住一小段坏链。
    FREE_MISS_LIMIT = 6

    # 解析不再回退到 JustOneAPI 付费接口（作者定死）：解析走服务端媒体解析。
    PAID_FALLBACK_ENABLED = False

    # 桌面端 UA：抖音对移动端 UA 常常只给混淆页，桌面 UA 命中率更高。
    _DESKTOP_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # Douyin CDN requires a realistic UA + referer, otherwise returns 403.
    _DL_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    }

    # Headers for short-link resolution — mimic a mobile browser visiting Douyin
    _SHORT_LINK_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }




































    def __init__(self, client: JustOneAPICollector, save_dir: Path | None = None,
                 cookie: str = "", ffmpeg_path: str | None = None,
                 browser_profile_dir: str = ""):
        self.client = client
        # ffmpeg 路径：用于「仅音频」从视频抽取 mp3。优先用调用方传入，
        # 否则按 PATH 与程序私有目录自动查找（含 %APPDATA%/TrafficCatcher/ffmpeg）。
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()
        # 浏览器会话抓取用的登录 profile 目录（设置页扫码登录时使用的那个）。
        # 喜欢 / 收藏列表必须走真实浏览器会话才能过抖音 Argus 风控，
        # 为空时会自动回退到 %APPDATA%/TrafficCatcher/browser_profile。
        self.browser_profile_dir = (browser_profile_dir or "").strip()
        if not self.browser_profile_dir:
            try:
                from . import branding
                self.browser_profile_dir = str(branding.app_data_dir() / "browser_profile")
            except Exception:  # noqa: BLE001
                self.browser_profile_dir = ""
        if save_dir is None:
            desktop = Path.home() / "Desktop"
            if not desktop.exists():
                desktop = Path.home()
            save_dir = desktop / self.SAVE_SUBDIR
        self.save_dir = Path(save_dir)
        # 目录建不起来（盘符不存在/无权限）不许把整个下载器拖死：
        # 记一句人话给界面，后面真正落盘时再报给用户。
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            self.last_dir_note = ""
        except OSError as exc:  # noqa: BLE001
            self.last_dir_note = f"保存目录不可用：{exc}"
        # 上一次真正落盘用的目录（换目录后界面要提示实际存到哪了）
        self.last_save_dir_used = self.save_dir
        # 目录可写性探测结果缓存，避免同一个目录反复 stat/写测试
        self._probe_cache = {}

        # 免费解析连续失败的计数与熔断开关。★ 这两个必须在 __init__ 里先建好：
        # close()/_note_free_miss() 会无条件读它们，缺了就是 AttributeError
        # （v1.26.2 发版自检时才暴露出来 —— 重建源码时漏了这一批属性）。
        self._free_miss = 0
        self._free_disabled = False
        self._last_free_block = ""
        # 浏览器渲染解析器，用到才建（启动浏览器很贵）
        self._page_resolver = None
        self._last_blogger_sec_uid = ""
        self._last_list_note = ""

        # 各平台登录 Cookie（来自内置浏览器扫码登录）。
        # 抖音 Cookie 带上后解析分享链接/取视频页数据的成功率明显更高；
        # 其它平台（小红书/快手/TikTok）的 Cookie 同样在下载时按平台匹配使用。
        # 为空时行为与以前完全一致，不影响老用户。
        self.cookies = {"douyin": (cookie or "").strip()}
        self.cookie = self.cookies["douyin"]  # 向后兼容别名（抖音）

        dl_headers = dict(self._DL_HEADERS)
        resolve_headers = dict(self._SHORT_LINK_HEADERS)
        if self.cookie:
            dl_headers["Cookie"] = self.cookie
            resolve_headers["Cookie"] = self.cookie

        # Separate httpx client for file downloads — longer timeout, CDN headers.
        self._dl_client = httpx.Client(
            timeout=httpx.Timeout(300.0, connect=30.0),
            follow_redirects=True,
            headers=dl_headers,
        )

        # Separate httpx client for short-link resolution — browser-like headers
        # follow_redirects=False: used when we need to capture intermediate redirects
        self._resolver_client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=False,
            headers=resolve_headers,
        )

        # Lightweight client that follows redirects automatically — much faster for
        # the common case where we just want the final page content
        self._redirect_client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            headers=resolve_headers,
            max_redirects=8,
        )


































    def _dl_headers_for(self, platform: str) -> dict:
        """按平台组装下载请求头：Referer 用**该平台自己的**，Cookie 用**该平台自己的**。

        ★ 以前这里直接 `dict(self._DL_HEADERS)` —— Referer 恒为抖音首页。
          小红书 CDN 会因为这条 Referer 直接回 403，见上面 _PLATFORM_REFERER 注释。
          平台不在表里时把 Referer 删掉（不是留空串）：有些 CDN 见到空的
          Referer 头照样拒绝，干脆不发。
        """
        plat = (platform or "douyin").strip() or "douyin"
        h = dict(self._DL_HEADERS)
        ref = _PLATFORM_REFERER.get(plat)
        if ref:
            h["Referer"] = ref
        else:
            h.pop("Referer", None)
        ck = (self.cookies.get(plat) or "").strip()
        if ck:
            h["Cookie"] = ck
        else:
            h.pop("Cookie", None)
        return h

    @staticmethod
    def _find_ffmpeg() -> str | None:
        """在 PATH 与程序私有目录里查找 ffmpeg 可执行文件。"""
        from justoneapi_collector._ffmpeg import find_ffmpeg
        return find_ffmpeg()


    def set_cookie(self, cookie: str):
        """运行时更新抖音 Cookie（登录后无需重启程序）。"""
        self.cookies["douyin"] = (cookie or "").strip()
        self.cookie = self.cookies["douyin"]  # 向后兼容别名
        for c in (self._dl_client, self._resolver_client, self._redirect_client):
            try:
                if self.cookie:
                    c.headers["Cookie"] = self.cookie
                elif "Cookie" in c.headers:
                    del c.headers["Cookie"]
            except Exception:               # noqa: BLE001
                pass

    def set_cookie_for(self, platform: str, cookie: str):
        """运行时更新某平台的登录 Cookie（小红书 / 快手 / TikTok 等）。

        platform 缺省按抖音处理；抖音会同步更新共享客户端（向后兼容）。
        """
        platform = (platform or "douyin").strip().lower() or "douyin"
        self.cookies[platform] = (cookie or "").strip()
        if platform == "douyin":
            self.cookie = self.cookies["douyin"]
            self.set_cookie(self.cookie)

    def close(self):
        self._dl_client.close()
        try:
            self._resolver_client.close()
        except Exception:
            pass
        try:
            self._redirect_client.close()
        except Exception:
            pass
        # 浏览器渲染器：只断开会话，不强杀进程 —— 客户可能还开着那个窗口
        # 手动过一次人机验证，杀了下次又得重来。
        if self._page_resolver is not None:
            try:
                self._page_resolver.close()
            except Exception:  # noqa: BLE001
                pass
            self._page_resolver = None

    def __enter__(self) -> "VideoDownloader":
        return self









    def __exit__(self, *exc):
        self.close()
    def search_blogger(self, keyword: str) -> list[BloggerInfo]:
        """Search Douyin bloggers by keyword. Returns up to ~20 candidates."""
        resp = self.client.request(
            "douyin-tiktok-china", "user-search-v2",
            {"keyword": keyword, "page": 1},
        )
        if not resp.success or not isinstance(resp.data, dict):
            return []

        bd = resp.data.get("business_data", [])
        results: list[BloggerInfo] = []
        for item in bd[:20]:
            if not isinstance(item, dict):
                continue
            d = item.get("data", {}) or {}
            raw = d.get("raw_data")
            if not raw:
                continue
            try:
                raw_obj = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            user = raw_obj.get("user_info", {}) or {}
            sec_uid = user.get("sec_uid")
            if not sec_uid:
                continue
            avatar_obj = user.get("avatar_medium") or user.get("avatar_larger") or {}
            avatar_urls = avatar_obj.get("url_list") or []
            results.append(BloggerInfo(
                sec_uid=sec_uid,
                nickname=user.get("nickname", ""),
                uid=str(user.get("uid", "")),
                follower_count=user.get("follower_count", 0),
                avatar_url=avatar_urls[0] if avatar_urls else "",
                signature=user.get("signature", ""),
                platform="douyin",
            ))
        return results

    def list_blogger_videos(
        self,
        sec_uid: str,
        max_cursor: int = 0,
        count: int = 20,
        sort_type: int = 1,   # 1 = latest, 2 = most popular
    ) -> tuple[list[VideoInfo], int, bool]:
        """
        List one page of a blogger's videos.

        Returns (videos, next_max_cursor, has_more).
        """
        resp = self.client.request(
            "douyin-tiktok-china", "user-published-videos-v1",
            {"secUid": sec_uid, "maxCursor": max_cursor, "count": count, "sortType": sort_type},
        )
        if not resp.success or not isinstance(resp.data, dict):
            return [], max_cursor, False

        awemes = resp.data.get("aweme_list", []) or []
        videos = [self._aweme_to_video_info(a) for a in awemes if isinstance(a, dict)]
        next_cursor = resp.data.get("max_cursor", max_cursor)
        has_more = resp.data.get("has_more", 0) == 1
        return videos, next_cursor, has_more

    def list_all_blogger_videos(
        self,
        sec_uid: str,
        max_pages: int = 50,
        sort_type: int = 1,
        on_page: Callable[[int, int, list[VideoInfo]], None] | None = None,
    ) -> list[VideoInfo]:
        """
        Iterate through all pages of a blogger's videos.

        Stops when has_more == 0, max_pages reached, or empty page returned.
        Calls on_page(page_idx, total_so_far, videos) after each page for progress.
        """
        all_videos: list[VideoInfo] = []
        cursor = 0
        for page in range(max_pages):
            videos, cursor, has_more = self.list_blogger_videos(
                sec_uid, max_cursor=cursor, count=20, sort_type=sort_type
            )
            if not videos:
                break
            all_videos.extend(videos)
            if on_page:
                on_page(page + 1, len(all_videos), videos)
            if not has_more:
                break
        return all_videos




    def resolve_sec_uid(self, text: str,
                        allow_network: bool = True) -> tuple[str | None, str]:
        """
        从博主主页链接 / 短链 / 文本中解析出 sec_uid。

        返回 (sec_uid, reason)：sec_uid 为空时 reason 说明失败原因。
        优先正则直提；失败再走博主链接解析（本地跟 302 + JustOneAPI 兜底）。

        ★ allow_network=False（界面「浏览器会话抓取」用，2026-11-10）：
          这个方法是**主线程同步调用**的，而 v.douyin.com 短链在无登录态下
          会 302 回抖音首页，本地跟随最多白等两个 UA × 15 秒、最后还是拿不到
          sec_uid，纯属把界面卡死。浏览器模式直接把原始链接交给带登录态的
          CDP 会话去跳，所以这里只做零耗时的正则判断。
        """
        if not text or not text.strip():
            return None, "输入为空"
        sec = VideoDownloader._extract_sec_uid(text)
        if sec:
            return sec, ""
        if not allow_network:
            return None, (
                "本地无法识别该主页链接，已转交浏览器会话抓取；"
                "若仍失败，请复制浏览器地址栏里的完整主页链接（含 MS4w…）")
        # 兜底：走博主链接解析（需要真实 client）
        try:
            info = self.resolve_blogger_link(text)
        except Exception as e:  # noqa: BLE001
            info = None
            reason = f"博主解析异常：{e}"
        else:
            reason = ""
        if info is not None:
            sid = getattr(info, "sec_uid", "") or ""
            if sid:
                return sid, ""
            reason = "博主解析成功但未取到 sec_uid"
        if not reason:
            reason = "未能从输入中识别博主主页 / sec_uid"
        return None, reason



























    def list_user_favorites(
        self, sec_uid: str, max_cursor: int = 0, count: int = 20,
    ) -> tuple[list[VideoInfo], int, bool, str]:
        """
        拉取单页「喜欢」列表。返回 (videos, next_max_cursor, has_more, error_msg)。
        error_msg 非空表示失败（含缺登录态 / 风控等可读原因）。
        """
        from .douyin_fav_crawler import DouyinFavCrawler
        crawler = DouyinFavCrawler(cookie=self.cookies.get("douyin", ""), timeout=20.0)
        try:
            awemes, nxt, has_more, err = crawler.fetch_favourite(sec_uid, max_cursor, count)
        finally:
            crawler.close()
        if err:
            return [], max_cursor, False, err
        videos = [self._aweme_to_video_info(a) for a in awemes if isinstance(a, dict)]
        return videos, nxt, has_more, ""

    def list_user_collects(
        self, sec_uid: str, max_cursor: int = 0, count: int = 20,
    ) -> tuple[list[VideoInfo], int, bool, str]:
        """
        拉取单页「收藏」列表。返回 (videos, next_max_cursor, has_more, error_msg)。
        """
        from .douyin_fav_crawler import DouyinFavCrawler
        crawler = DouyinFavCrawler(cookie=self.cookies.get("douyin", ""), timeout=20.0)
        try:
            awemes, nxt, has_more, err = crawler.fetch_collect(sec_uid, max_cursor, count)
        finally:
            crawler.close()
        if err:
            return [], max_cursor, False, err
        videos = [self._aweme_to_video_info(a) for a in awemes if isinstance(a, dict)]
        return videos, nxt, has_more, ""

    def _list_user_all(
        self, sec_uid: str, fetcher, max_pages: int = 20,
        on_page: Callable[[int, int, list[VideoInfo]], None] | None = None,
    ) -> tuple[list[VideoInfo], str]:
        """
        分页拉取喜欢 / 收藏全量。fetcher 为 list_user_favorites / list_user_collects。
        返回 (videos, error_msg)，error_msg 非空表示中途失败（已尽量返回已拉到的部分）。
        """
        all_videos: list[VideoInfo] = []
        cursor = 0
        last_err = ""
        for page in range(max_pages):
            videos, cursor, has_more, err = fetcher(sec_uid, max_cursor=cursor, count=20)
            if err:
                last_err = err
                break
            if not videos:
                break
            all_videos.extend(videos)
            if on_page:
                on_page(page + 1, len(all_videos), videos)
            if not has_more:
                break
        return all_videos, last_err

    def list_user_all_favorites(
        self, sec_uid: str, max_pages: int = 20,
        on_page: Callable[[int, int, list[VideoInfo]], None] | None = None,
    ) -> tuple[list[VideoInfo], str]:
        """分页拉取「喜欢」全量列表。返回 (videos, error_msg)。"""
        return self._list_user_all(
            sec_uid, self.list_user_favorites, max_pages=max_pages, on_page=on_page
        )

    def list_user_all_collects(
        self, sec_uid: str, max_pages: int = 20,
        on_page: Callable[[int, int, list[VideoInfo]], None] | None = None,
    ) -> tuple[list[VideoInfo], str]:
        """分页拉取「收藏」全量列表。返回 (videos, error_msg)。"""
        return self._list_user_all(
            sec_uid, self.list_user_collects, max_pages=max_pages, on_page=on_page
        )

    def get_own_sec_uid(self) -> tuple[str | None, str]:
        """
        获取登录账号本人的 sec_uid（用于「拉取我自己的喜欢 / 收藏」）。
        返回 (sec_uid, error_msg)。
        """
        from .douyin_fav_crawler import DouyinFavCrawler, FavCrawlError
        crawler = DouyinFavCrawler(cookie=self.cookies.get("douyin", ""), timeout=20.0)
        try:
            profile = crawler.fetch_own_profile()
        except FavCrawlError as e:
            return None, str(e)
        finally:
            crawler.close()
        if not profile:
            return None, "未能获取本人资料（请确认已扫码登录抖音）"
        sid = profile.get("sec_uid") or ""
        if not sid:
            return None, "本人资料中未包含 sec_uid"
        return sid, ""

    def list_user_list(
        self,
        kind: str,
        sec_uid: str,
        use_browser: bool = True,
        max_pages: int = 20,
        on_progress: Callable[[int], None] | None = None,
        profile_url: str = "",
    ) -> tuple[list[VideoInfo], str]:
        """
        统一入口：拉取某账号的 作品 / 喜欢 / 收藏 列表。

        kind: post（作品）/ favorite（喜欢）/ collect（收藏）
        use_browser: True 时用**真实浏览器会话**抓取（默认，能过抖音 Argus 风控）；
                     False 时退化为纯 HTTP 直连（通常会被风控拦截，仅作兜底）。
        profile_url: 原始博主主页链接 / 分享短链。sec_uid 为空且浏览器模式时，
                     由带登录态的浏览器打开它、从落地地址里解出 sec_uid。
        返回 (videos, error_msg)：error_msg 非空表示失败，videos 可能含部分结果。
        """
        if use_browser:
            try:
                from .douyin_cdp_crawler import DouyinCDPCrawler
            except Exception as e:              # noqa: BLE001
                return [], f"浏览器抓取模块加载失败：{e}"
            crawler = DouyinCDPCrawler(self.browser_profile_dir or ".", headless=False)
            ok, msg = crawler.start()
            if not ok:
                return [], msg
            try:
                # ★ 2026-11-10 客户「无法直接提取抖音博主主页批量下载」：
                #   直接把 v.douyin.com 主页短链贴进「拉取列表」时，无登录态的
                #   HTTP 客户端只会 302 回抖音首页，永远拿不到 sec_uid；但带登录态
                #   的浏览器能跳到 /user/MS4w...，所以把原始链接交给浏览器解开。
                if not sec_uid and profile_url:
                    sec_uid, resolve_err = crawler.resolve_profile_link(profile_url)
                    if resolve_err:
                        return [], resolve_err
                # 未指定博主时，直接从浏览器登录态取本人 sec_uid
                # （比读 config.json 里可能过期的 Cookie 稳）
                if not sec_uid:
                    sec_uid, own_err = crawler.get_own_sec_uid()
                    if own_err:
                        return [], own_err
                awemes, err = crawler.capture_list(
                    kind, sec_uid, max_pages=max_pages,
                    on_page=(lambda total, _all: on_progress(total)) if on_progress else None,
                )
            finally:
                crawler.stop()
            videos = [self._aweme_to_video_info(a) for a in awemes if isinstance(a, dict)]
            return videos, err

        # ---- 纯 HTTP 兜底（多半会被风控，保留以便无浏览器环境排查）
        if not sec_uid:
            return [], "缺少博主 sec_uid（纯 HTTP 模式请粘贴博主主页链接）"
        if kind == "favorite":
            videos, err = self.list_user_all_favorites(sec_uid, max_pages=max_pages)
            return videos, err
        if kind == "collect":
            videos, err = self.list_user_all_collects(sec_uid, max_pages=max_pages)
            return videos, err
        try:
            videos = self.list_all_blogger_videos(sec_uid, max_pages=max_pages)
        except Exception as e:                  # noqa: BLE001
            return [], f"作品列表拉取失败：{e}"
        return videos, ""










    def get_video_detail(self, video_id: str) -> VideoInfo | None:
        """Get a single video's detail (incl. no-watermark URL) by video ID.

        Tries multiple parameter names since different API versions use different names.

        ★ 免费版短路（v1.14.0）：这条走的是付费详情接口（video-details-v2）。
          免费通道没命中时，付费用户靠它兜底是应该的；但免费用户如果也兜底，
          等于我们替每一个白嫖的用户付接口钱 —— 点几十次就是几十次调用。
          所以免费档在这里直接返回 None，把原因交给上层展示。

        ★ v1.22.0 失败原因透明化：以前这里只返回 None，上层只能拼出
          「详情接口无结果」—— 客户分不清是「今日额度用完」「接口欠费」
          还是「作品真没了」，对着这句话什么都做不了。现在把接口的真实
          业务码翻成能行动的原因写进 self._last_detail_reason，由调用方
          拼进失败列表。2026-10-22 客户现场的真因就是接口欠费
          （code 601 INSUFFICIENT BALANCE）被显示成了「无结果」。
        """
        # ★ 2026-10-23：解析不花接口钱（见 PAID_FALLBACK_ENABLED 的说明）。
        #   这里短路之后，无论免费版还是完整版，解析都只走零成本的页面通道。
        if not self._paid_resolve_ok():
            self._last_free_block = (
                "这条链接没能从抖音页面里读到播放地址（多半是未登录、链接失效，"
                "或撞上抖音风控页）。到「设置 → 平台登录」扫码登录后再试一次，"
                "命中率会明显提高。")
            return None

        # Try different parameter names
        param_variants = [
            {"videoId": video_id},
            {"aweme_id": video_id},
            {"item_id": video_id},
            {"id": video_id},
            {"awemeId": video_id},
        ]

        self._last_detail_reason = ""
        saw_empty_success = False
        for params in param_variants:
            resp = self.client.request(
                "douyin-tiktok-china", "video-details-v2", params
            )
            if not resp.success or not isinstance(resp.data, dict):
                self._last_detail_reason = self._explain_api_failure(resp)
                # 欠费 / 额度用完 / Token 失效 / 限流 / 5xx：换参数名也救不回
                # 来，别把剩下 4 个变体全跑一遍（每次都是一次真实往返，纯浪费）
                if self._is_final_api_failure(resp):
                    break
                continue

            # Try various response structures
            data = resp.data

            # Structure 1: aweme_detail directly
            aweme = data.get("aweme_detail", {}) or {}
            if aweme and isinstance(aweme, dict) and aweme.get("aweme_id"):
                return self._aweme_to_video_info(aweme)

            # Structure 2: data.aweme_detail
            inner_data = data.get("data", {}) or {}
            if isinstance(inner_data, dict):
                aweme2 = inner_data.get("aweme_detail", {}) or {}
                if aweme2 and isinstance(aweme2, dict) and aweme2.get("aweme_id"):
                    return self._aweme_to_video_info(aweme2)

            # Structure 3: the whole response is the aweme object
            if data.get("aweme_id") and data.get("video"):
                return self._aweme_to_video_info(data)

            # Structure 4: search inside for any aweme-like object
            found = self._find_aweme_in_dict(data)
            if found:
                return self._aweme_to_video_info(found)

            # 这个参数名通了但没数据：记一笔，继续试下一个名字
            saw_empty_success = True

        if not self._last_detail_reason and saw_empty_success:
            self._last_detail_reason = (
                "接口未返回这条作品的数据（可能已删除、仅粉丝可见，或平台已下架）")
        return None







    @staticmethod
    def _is_final_api_failure(resp) -> bool:
        """这次失败，换参数名也救不回来吗（v1.22.0）。

        只有「参数不被接受」（400）值得换下一个参数名再试；欠费、额度用完、
        Token 失效、限流、服务端错误都是全局状态，对同一个接口重试没有意义，
        把 5 个参数变体全跑一遍只是白白多出 4 次往返。
        """
        try:
            code = int(getattr(resp, "code", 0) or 0)
        except (TypeError, ValueError):
            return False
        return (code in (401, 403, 429, 601, QUOTA_EXCEEDED_CODE)
                or code >= 500)


    @staticmethod
    def _explain_api_failure(resp) -> str:
        """把接口失败翻成客户能行动的原因（v1.22.0）。

        以前统一显示「详情接口无结果」—— 客户看到这句什么都不能做。
        现在按业务码分开说：
          · 601 INSUFFICIENT BALANCE → 接口余额不足。这是**我们**的问题，
            必须说清：藏着只会让客户以为软件坏了，也不会有人来提醒我们充值；
          · -9001（本机额度闸门）    → 今日额度已用完，0 点重置；
          · 401/403                  → Token 无效，联系作者；
          · 429 / 5xx                → 稍后再试；
          · 其他                     → 带上接口原话，不许吞。
        """
        try:
            code = int(getattr(resp, "code", 0) or 0)
        except (TypeError, ValueError):
            code = 0
        msg = str(getattr(resp, "message", "") or "").strip()
        upper = msg.upper()
        if code == QUOTA_EXCEEDED_CODE:
            return ("今日接口额度已用完（每天 0 点自动重置；"
                    "已解析出的视频不受影响，可以先下载）")
        if code == 601 or "BALANCE" in upper or "INSUFFICIENT" in upper:
            return "采集接口余额不足，请联系作者处理（与你的账号和授权码无关）"
        if code in (401, 403):
            return "接口 Token 无效或已过期，请联系作者处理"
        if code == 429:
            return "接口请求太频繁，请稍后再试"
        if code >= 500:
            return "接口服务暂时不可用，请稍后再试"
        if code == 400:
            return f"接口拒绝了请求（{msg[:40]}）" if msg else "接口拒绝了请求"
        return f"接口返回：{msg[:50]}" if msg else "接口没有响应"


    @staticmethod
    def extract_urls(text: str) -> list[str]:
        """
        Extract all URLs from arbitrary text (e.g. Douyin share text with emoji/captions).
        Returns a list of URL strings found.
        """
        if not text:
            return []
        # Match http/https URLs — stop at whitespace, CJK characters, or
        # obvious sentence-ending punctuation that is unlikely to be inside a URL.
        # URL-safe chars: letters, digits, and : / . ? # @ ! $ & ' ( ) * + , ; = - _ . ~ %
        pattern = re.compile(
            r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
        )
        urls = pattern.findall(text)
        # Strip trailing punctuation that commonly follows URLs in share text
        cleaned = []
        for u in urls:
            u = u.rstrip(".,;:!?\uff09\u3011\u300b\uff0c\u3002\uff1b\uff1a\uff01\uff1f\"''\uff09")
            if u:
                cleaned.append(u)
        return cleaned

    @staticmethod
    def extract_video_id(text: str) -> str | None:
        """
        Try to extract a Douyin video ID (19-digit number) from text/URL.
        Returns the video_id string or None.
        """
        if not text:
            return None
        # Pattern 1: /video/7294795678901234567 (most common)
        m = re.search(r"video/(\d{15,25})", text)
        if m:
            return m.group(1)
        # Pattern 2: aweme_id=7294795678901234567
        m = re.search(r"aweme[_-]?id[=:]?(\d{15,25})", text, re.IGNORECASE)
        if m:
            return m.group(1)
        # Pattern 3: item_ids=7294795678901234567
        m = re.search(r"item[_-]?ids?[=:]?(\d{15,25})", text, re.IGNORECASE)
        if m:
            return m.group(1)
        # Pattern 4: standalone 19-digit number in URL params
        m = re.search(r"[?&](?:id|itemId|videoId)=(\d{15,25})", text, re.IGNORECASE)
        if m:
            return m.group(1)
        return None


    def _follow_short_link(self, url: str) -> tuple[str | None, str]:
        """
        Follow a Douyin short link (v.douyin.com/xxx) locally via HTTP redirects.

        Returns (final_url_or_None, error_message).
        final_url will contain the full Douyin URL with video_id or sec_uid.
        """
        current_url = url
        visited = set()
        max_hops = 8

        for hop in range(max_hops):
            if current_url in visited:
                return None, f"重定向循环 (已访问 {len(visited)} 个URL)"
            visited.add(current_url)

            try:
                r = self._resolver_client.get(
                    current_url,
                    headers={
                        "User-Agent": self._SHORT_LINK_HEADERS["User-Agent"],
                        "Referer": "https://www.douyin.com/",
                    },
                )
            except httpx.RequestError as e:
                return None, f"网络请求失败: {type(e).__name__}"

            # If we got a redirect, follow it
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("Location") or r.headers.get("location")
                if location:
                    if location.startswith("http"):
                        current_url = location
                    else:
                        from urllib.parse import urljoin
                        current_url = urljoin(current_url, location)
                    continue
                return None, f"重定向状态码 {r.status_code} 但无 Location 头"

            # If we got 200 OK, the final URL might be in the response body
            if r.status_code == 200:
                html = r.text

                # Try to extract video URL from JS redirect
                m = re.search(r'window\.location\.href\s*=\s*["\']([^"\']+)["\']', html)
                if m:
                    redirect_url = m.group(1)
                    if redirect_url.startswith("http"):
                        current_url = redirect_url
                        continue

                # If we have a final URL from request history that contains video/,
                # we have reached the video page
                final_url_str = str(r.url)
                if "/video/" in final_url_str:
                    return final_url_str, ""

                # Try to extract aweme_id from HTML
                vid = self._extract_video_id(html)
                if vid:
                    return f"https://www.douyin.com/video/{vid}", ""

                # Try to extract sec_uid (blogger profile)
                sec_uid = self._extract_sec_uid(html)
                if sec_uid:
                    return f"https://www.douyin.com/user/{sec_uid}", ""

                # Final fallback: if URL contains douyin.com and is not the homepage
                if "douyin.com" in final_url_str and final_url_str != "https://www.douyin.com/":
                    return final_url_str, ""

                return None, f"页面200但未找到视频ID，可能是隐私视频或已删除"

            return None, f"HTTP {r.status_code}"

        return None, f"重定向次数过多 (>{max_hops}次)"
























    def _extract_video_from_html(self, html: str) -> VideoInfo | None:
        """
        Try to extract full video info from a Douyin video page HTML.
        Two-phase approach:
          Phase 1 (fast): regex extraction of key fields without full JSON parsing.
          Phase 2 (full): parse __INITIAL_STATE__ / _ROUTER_DATA / RENDER_DATA JSON.
        """
        if not html:
            return None

        # ===== Phase 1: Fast regex extraction (no JSON parsing) =====
        fast = self._fast_extract_from_html(html)
        if fast and fast.no_watermark_url:
            return fast

        # ===== Phase 2: Full JSON parsing (slower but more complete) =====
        aweme_data = None

















































    def _fast_extract_from_html(self, html: str) -> VideoInfo | None:
        """
        Fast regex extraction of video info from HTML — no JSON parsing.
        Extracts aweme_id, play URL, author, desc directly using regex.
        Much faster than full JSON parse but may miss some fields.
        """
        # Extract aweme_id
        vid_match = re.search(r'"aweme_id"\s*:\s*"(\d{15,25})"', html)
        if not vid_match:
            vid_match = re.search(r"/video/(\d{15,25})", html)
        if not vid_match:
            return None

        video_id = vid_match.group(1)
























































    def _find_aweme_in_dict(self, data: Any, depth: int = 0) -> dict | None:
        """Recursively search a nested dict for an aweme detail object."""
        if depth > 10:
            return None
        if isinstance(data, dict):
            # Check if this dict looks like an aweme object
            if "aweme_id" in data and "video" in data and isinstance(data.get("video"), dict):
                return data
            if "aweme_detail" in data and isinstance(data.get("aweme_detail"), dict):
                return data["aweme_detail"]
            # Search common containers
            for key in ("aweme", "itemInfo", "item_data", "videoData", "detail"):
                v = data.get(key)
                if isinstance(v, dict):
                    result = self._find_aweme_in_dict(v, depth + 1)
                    if result:
                        return result
            # Search all values
            for v in data.values():
                if isinstance(v, (dict, list)):
                    result = self._find_aweme_in_dict(v, depth + 1)
                    if result:
                        return result
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    result = self._find_aweme_in_dict(item, depth + 1)
                    if result:
                        return result
        return None

    def _free_html_resolve(self, page_url: str,
                           desktop: bool = False) -> tuple[VideoInfo | None, str, str]:
        """[零成本通道] 直接抓页面 HTML 自己解析出作品信息。

        desktop: 目标是不是桌面版视频页（douyin.com/video/…）。桌面页的 SSR
                 数据通常比移动版分享页全，用桌面 UA 命中率更高。

        这是**不花一分钱**的解析路径：带登录 Cookie 请求抖音页面，从
        __INITIAL_STATE__ / _ROUTER_DATA / RENDER_DATA 里抠出 aweme 对象，
        取到的播放地址与详情接口同源（都是 video.play_addr），一样是无水印的，
        质量没有差别。

        ★ 2026-10-11 之前它排在 get-video-detail（0.1 元/条）**后面**，
        等于放着免费的路不走先花钱。批量导入几十条链接时，这一处顺序就是
        几块钱的差别。现在提到最前，详情接口只在它失败时才兜底。

        Returns: (video, page_vid, reason)
                 video    —— 解析成功且带播放地址时为 VideoInfo，否则 None
                 page_vid —— 从页面里抠到的作品 ID（供后续 API 兜底复用）
                 reason   —— 失败原因（成功时为空串）

        ★ 自适应短路：抖音给不给页面数据是有波动的（未登录 / 撞上风控页时
        常常只丢回一串混淆 JS）。连续 FREE_MISS_LIMIT 次没命中，就本次会话
        停用这条通道，直接走接口 —— 省下的钱不能拿批量导入的速度去换。
        一旦又命中了，立刻恢复。
        """
        if getattr(self, "_free_disabled", False):
            return None, "", _FREE_DISABLED_NOTE

        ua = self._DESKTOP_UA if desktop else self._SHORT_LINK_HEADERS["User-Agent"]
        try:
            page_resp = self._redirect_client.get(
                page_url,
                headers={
                    "User-Agent": ua,
                    "Referer": "https://www.douyin.com/",
                },
            )
        except Exception as e:                          # noqa: BLE001
            self._note_free_miss()
            return None, "", f"页面请求异常({type(e).__name__}: {e})"

        if page_resp.status_code != 200:
            self._note_free_miss()
            return None, "", f"页面HTTP {page_resp.status_code}"

        landing = str(page_resp.url or "")
        # ★ 跳回首页 = 链接失效/被风控。首页 HTML 里塞满推荐流的 aweme_id，
        #   照抓就会张冠李戴，所以直接判定失败（调用方会据此跳过这条）。
        if VideoDownloader.is_douyin_home(landing):
            self._note_free_miss()
            return None, "", ("链接已失效或被抖音拦截（打开后跳回抖音首页）——"
                              "请在抖音里重新复制一次分享链接")

        page_vid = self.extract_video_id(page_resp.text)
        video = self._extract_video_from_html(page_resp.text)
        if video and video.no_watermark_url:
            self._free_miss = 0
            self._free_disabled = False
            return video, page_vid or "", ""
        self._note_free_miss()
        if video:
            return None, page_vid or "", "页面提取到视频但无播放地址"
        return None, page_vid or "", "页面未解析出视频数据"

    def _browser_render_resolve(self, page_url: str) -> tuple[VideoInfo | None, str]:
        """[零成本通道 · 2026-10-24 起的主解析] 让真浏览器渲染作品页，把数据读回来。

        ★ 为什么非加这条路不可（客户报「两个版本都用不了」的根因）：
          2026-10-23~24 之间抖音做了两件事 ——
            ① 页面 SSR 里不再下发作品数据（`_ROUTER_DATA` 的 loaderData 只剩
               `video_layout: null` 和请求上下文，`aweme_id` 在整页出现 0 次）；
            ② 接口全面上 Argus 签名（detail 接口 403 `Signature Not Found`，
               老的 iteminfo 接口 11110 `encrypt_data_miss`）。
          于是上面 `_free_html_resolve()`（抠 SSR）必然全失败，
          `get_video_detail()` 也拿不到 —— 两条路一起死。
          唯一还活着的是**浏览器自己跑一遍**：它的 JS 带着合规签名去请求，
          我们用 CDP 把响应截下来（见 douyin_page_resolver.py）。

        ★ 这一路不吃接口额度、也不需要客户扫码登录，对免费版和完整版一视同仁。
        """
        if getattr(self, "_browser_disabled", False):
            return None, getattr(self, "_browser_note", "") or "浏览器渲染通道已停用"
        try:
            from .douyin_page_resolver import DouyinPageResolver, pick_play_url
        except Exception as exc:                    # noqa: BLE001
            self._browser_disabled = True
            self._browser_note = f"加载浏览器渲染组件失败：{exc}"
            return None, self._browser_note
        try:
            if self._page_resolver is None:
                self._page_resolver = DouyinPageResolver(self.browser_profile_dir)
            data, why = self._page_resolver.resolve(page_url)
        except Exception as exc:                    # noqa: BLE001
            return None, f"浏览器渲染异常（{type(exc).__name__}: {exc}）"

        if not data:
            # 通道自己判定"连续失败，先停"时，把它的原因记下来，后续不再逐条重试
            if getattr(self._page_resolver, "disabled", False):
                self._browser_disabled = True
                self._browser_note = (getattr(self._page_resolver, "start_error", "")
                                      or why or "浏览器渲染通道已停用")
            return None, why or "浏览器渲染没取到数据"

        srcs = data.get("srcs") or []
        play = pick_play_url(srcs)
        images = data.get("image_list") or []
        if not play and not images:
            return None, "页面数据里没有播放地址（作品可能已删除或仅粉丝可见）"
        return VideoInfo(
            video_id=str(data.get("video_id") or ""),
            desc=str(data.get("desc") or ""),
            author=str(data.get("author") or ""),
            sec_uid=str(data.get("sec_uid") or ""),
            create_time=int(data.get("create_time") or 0),
            duration=int(data.get("duration") or 0),
            digg_count=int(data.get("digg_count") or 0),
            cover_url=str(data.get("cover_url") or ""),
            no_watermark_url=play,
            share_url=str(data.get("share_url") or ""),
            image_list=images,
            platform="douyin",
            comment_count=int(data.get("comment_count") or 0),
            share_count=int(data.get("share_count") or 0),
        ), ""







    def _free_tier_blocked(self) -> bool:
        """免费档是否禁止回退到付费接口。

        ★ 异常一律返回 False（不拦）：宁可偶尔放过一次调用，也绝不因为
          读不到授权状态而误伤付费用户 —— 老客户的权益不能收。
        """
        try:
            from .licensing import is_free_tier
            return bool(is_free_tier())
        except Exception:                           # noqa: BLE001
            return False

    def _paid_resolve_ok(self) -> bool:
        """解析时**允不允许**花接口钱兜底（2026-10-23 起默认不允许）。

        和 `_free_tier_blocked()` 的区别：那个只管"免费档别去烧接口"，
        这个管的是"整条解析链路要不要用接口"—— 现在答案是不要，
        所以它对免费版和完整版一视同仁。

        ★ 异常时返回 True（放行）而不是 False：宁可偶尔多花一次接口钱，
          也绝不因为读不到开关就让整条解析链路静默失效 —— 那样客户只会
          看到一堆"解析不出来"，根本查不出为什么。
        """
        try:
            if not bool(self.PAID_FALLBACK_ENABLED):
                return False
        except Exception:                           # noqa: BLE001
            return True
        return not self._free_tier_blocked()

    def _note_free_miss(self) -> None:
        """记一次免费通道未命中；连续到上限就暂时停用这条通道。"""
        self._free_miss = int(getattr(self, "_free_miss", 0)) + 1
        if self._free_miss >= self.FREE_MISS_LIMIT:
            self._free_disabled = True





    def _media_service_resolve(self, target: str) -> tuple[VideoInfo | None, str]:
        """把一条链接交给**服务端解析**通道，拿不到就返回 None + 原因。

        为什么把它排在本机浏览器之前，见 `media_parser.py` 顶部注释：
        本机去抖音抓页面那条路的三个变量（SSR 数据被拿掉 / 未登录被踢回首页 /
        同 IP 高频触发风控）客户一个都控制不了，v1.24.5 补完之后的失败率依旧
        取决于客户那台机器。服务端解析跟本地环境完全无关。

        ★ 异常一律吞掉并返回原因：调用方是批量循环，一条抛异常会让整批中止
          （v1.22.0 的老教训 —— 免费档的占位 client 炸出「API异常(TypeError)」
          这种客户看不懂的话）。
        """
        try:
            from .media_parser import parse as _svc_parse
        except Exception:                                   # noqa: BLE001
            return None, ""
        try:
            data, reason = _svc_parse(target)
        except Exception as e:                              # noqa: BLE001
            return None, f"解析服务异常({type(e).__name__})"
        if data is None:
            return None, reason or ""
        video = self._media_to_video_info(data, target)
        if video is None:
            return None, "解析服务返回了结果，但里面没有可用的下载地址"
        return video, ""

    def _media_to_video_info(self, data: dict, target: str) -> VideoInfo | None:
        """服务端解析结果 → VideoInfo（best-effort，字段拿不到就留默认）。

        ★ 平台以服务说的为准，不认链接域名：同一条 xhslink 短链落到什么平台，
          只有真正打开它的人才知道。
        """
        if not isinstance(data, dict):
            return None
        plat = str(data.get("platform") or "").strip() or "douyin"
        video_url = str(data.get("video_url") or "").strip()
        images = [str(u) for u in (data.get("images") or []) if u]
        if not video_url and not images:
            return None
        # 多视频（一帖多视频）时把其余片段按顺序挂在后面备查 —— 主地址仍是第一条
        extra = [(u, c) for u, c in (data.get("videos") or []) if u]
        # 作者拿不到就留空：不编造。文件名会退化为 unknown（_download_video_file_once）
        return VideoInfo(
            video_id=str(data.get("video_id") or "").strip(),
            desc=str(data.get("title") or "").strip(),
            author=str(data.get("author") or "").strip(),
            cover_url=str(data.get("cover_url") or "").strip(),
            no_watermark_url=video_url,
            share_url=str(target or "").strip(),
            image_list=images,
            platform=plat,
            **({"duration": 0} if False else {}),          # 占位：保持字段顺序可读
        ) if not extra else VideoInfo(
            video_id=str(data.get("video_id") or "").strip(),
            desc=str(data.get("title") or "").strip(),
            author=str(data.get("author") or "").strip(),
            cover_url=str(data.get("cover_url") or extra[0][1]).strip(),
            no_watermark_url=video_url or extra[0][0],
            share_url=str(target or "").strip(),
            image_list=images,
            platform=plat,
        )
    def resolve_share_link(self, share_text: str) -> VideoInfo | None:
        """
        Resolve a Douyin share link / short link / share text to a VideoInfo.

        Accepts:
          - Pure video ID (19-digit number)
          - Full video URL (douyin.com/video/xxx, iesdouyin.com/share/video/xxx)
          - Short link (v.douyin.com/xxxxx)
          - Full share text with emoji/hashtags + embedded link

        Resolution strategy (in order):
          1. Extract URLs from the text
          2. Extract video_id directly from URL
          3. Follow short link redirects locally (httpx) to get real URL
          4. Call share-link-resolution-v1 API as backup
          5. Try video-details-v2 with extracted ID
        """
        result, _reason = self.resolve_share_link_with_reason(share_text)
        return result

    def _douyin_resolve_impl(self, share_text: str) -> tuple[VideoInfo | None, str]:
        """
        [抖音专属实现] Resolve a Douyin share link / short link / share text to a VideoInfo.

        原抖音解析逻辑整体搬至此私有方法；公开入口 resolve_share_link_with_reason
        (带 platform 参数，默认 auto) 在自动分流判定为非抖音链接时会调用本方法兜底抖音。
        解析顺序（由快到慢）：
         0. 纯视频 ID → 拼成视频页抓 HTML（免费）
         1. 短链 → 本地跟随重定向（免费）判定是视频还是博主主页
         2. 视频页 / 落地页 → 抓 HTML 自己解析（免费）★ 主通道
         3. 上面都没拿到 → 用作品 ID 查详情接口（0.1 元）
         4. 还没有 ID → share-link-resolution + video-details（0.1~0.2 元）兜底

        ★ 2026-10-11 把「免费」的两步提到了花钱的两步之前。以前只要 URL 里
        含作品 ID 就直接查接口，一条 0.1 元；现在先试 0 成本的页面解析，
        批量导入几十条链接时能省下整批的钱（速度还更快）。
        """
        if not share_text or not share_text.strip():
            return None, "输入为空"

        text = share_text.strip()
        reasons = []
        # ★ 必须每次重置：界面是拿这个字段判断"要不要转成拉列表"的，
        #   残留上一次的值会把一条正常作品链接也误转走。
        self._last_blogger_sec_uid = ""

        # Step 0: pure video ID — ★ 先拼成视频页走免费 HTML，失败才查 API
        if text.isdigit() and len(text) >= 15:
            page = f"https://www.douyin.com/video/{text}"
            free_video, _pv, _note = self._free_html_resolve(page, desktop=True)
            if free_video:
                return free_video, ""
            # ★ 2026-10-24：SSR 里已经没有数据了，必须让浏览器跑一遍
            browser_video, _bn = self._browser_render_resolve(page)
            if browser_video:
                return browser_video, ""
            video = self.get_video_detail(text)
            if video:
                return video, ""
            why = getattr(self, "_last_detail_reason", "") or "接口查询无结果"
            reasons.append(f"视频ID {text[:10]}… {why}")

        # Step 1: extract URLs from text
        urls = self.extract_urls(text)
        if not urls:
            vid = self.extract_video_id(text)
            if vid:
                video = self.get_video_detail(vid)
                if video:
                    return video, ""
                why = getattr(self, "_last_detail_reason", "") or "接口查询无结果"
                reasons.append(f"提取到视频ID {vid[:10]}… 但{why}")
            else:
                reasons.append("未从文本中识别出链接或视频ID")
            return None, "；".join(reasons)

        for i, url in enumerate(urls):
            url_label = f"链接{i+1}" if len(urls) > 1 else "链接"
            short_link_local_failed = False
            free_video_id = ""     # 免费通道（短链跟随 / 页面 HTML）拿到的作品 ID
            page_url = url         # 真正去抓页面的地址（短链会被换成落地页）
            final = ""             # 短链跟随后的落地页（长链时保持空）

            is_short_link = VideoDownloader.is_short_link(url)
            # 视频页 + 图文笔记页都算"可以直接抓页面解析"
            is_video_page = any(k in url for k in ("douyin.com/video/",
                                                   "douyin.com/note/",
                                                   "douyin.com/slides/"))

            # ---- Step 1（免费）：短链本地跟随重定向，判定真实类型 ----
            # 短链本身不含任何类型信息，可能是「视频」也可能是「博主主页」。
            if is_short_link:
                final = self.resolve_short_link(url)
                info = VideoDownloader.parse_share_url(final)
                if info["kind"] == "video":
                    # ★ 只记下 ID，不再立刻花钱查详情 —— 先让 Step 2 免费试一次
                    free_video_id = info["aweme_id"]
                    if final:
                        page_url = final
                if not final:
                    # 本地跟随失败（多数是短链失效或被风控跳回首页）。
                    # 【不要】就此放弃：服务端的 share-link-resolution 接口往往还能救回来，
                    # 所以这里只标记"跳过重复的本地抓取"，继续往下走 Step 4。
                    reasons.append(
                        f"{url_label}本地跟随失败（短链可能已失效或被抖音拦截）"
                    )
                    short_link_local_failed = True

            # ---- Step 1.5（免费）：博主主页链接 —— 不是失败，是入口走错了 ----
            # ★ 2026-10-24 客户现场：把主页链接贴进「视频 / 博主链接」点「解析」，
            #   软件报「作品被删除 / 仅粉丝可见」，客户以为链接坏了、以为没登录，
            #   反复重试反复扫码，永远不可能成功 —— 主页里本来就没有"某一条作品"。
            #   正确动作是拉这个账号的作品列表。这里把 sec_uid 记到实例上，
            #   由界面接手自动转「拉取列表」（浏览器会话抓取，不花接口钱）。
            #   ★ 长链也必须在这里拦住：否则它会掉进 Step 2.5，
            #     白起一次浏览器、白等 20 秒，最后报一句"没读到播放地址"。
            probe = final or url
            if probe and not free_video_id:
                pinfo = VideoDownloader.parse_share_url(probe)
                if pinfo["kind"] == "blogger":
                    self._last_blogger_sec_uid = pinfo.get("sec_uid") or ""
                    reasons.append(
                        f"{url_label}这是博主主页链接（不是单条作品），"
                        "已自动改用「拉取列表」获取该账号的作品"
                    )
                    continue

            # ---- Step 2（免费，★ 2026-10-11 提到 API 之前）----
            # 抓页面 HTML 自己解析，零成本。以前这段排在 get-video-detail
            # 后面，等于放着免费的路不走先花钱；批量导入几十条链接时，
            # 这一处顺序就是几块钱的差别。拿不到再往下花钱，兜底逻辑不变。
            if (is_short_link and not short_link_local_failed) or is_video_page:
                video_from_html, page_vid, note = self._free_html_resolve(
                    page_url, desktop=is_video_page)
                if video_from_html:
                    return video_from_html, ""
                # 通道被自适应停用时不算"解析失败"，免得给用户报一堆莫名的原因
                if note and _FREE_DISABLED_NOTE not in note:
                    reasons.append(f"{url_label}{note}")
                if page_vid:
                    free_video_id = free_video_id or page_vid
                # 链接已经失效（跳回首页），后面花 API 也是白花，直接看下一条
                if "跳回抖音首页" in note:
                    continue

            # ---- Step 2.5（免费，★ 2026-10-24 新增）：浏览器渲染 ----
            # SSR 解析在上面已经必然失败（抖音把数据从页面里拿掉了），
            # 这里让真浏览器跑一遍、截它自己发的详情接口。零成本、不吃额度。
            # ★ 2026-10-25：短链展开后拿到的常常是**老分享页**
            #   `iesdouyin.com/share/video/<id>`，那个页面根本渲染不出播放器。
            #   只要已经从链接里拿到作品 ID，就换成标准作品页地址再渲染 ——
            #   同一个作品，换一个 URL 就是成功与失败的区别。
            render_url = page_url
            if free_video_id:
                render_url = f"https://www.douyin.com/video/{free_video_id}"
            browser_video, b_note = self._browser_render_resolve(render_url)
            if browser_video:
                return browser_video, ""
            # ★ 2026-10-25：被抖音踢回首页/精选页常常是**瞬时风控**。
            #   真机时序：0~5 秒页面还在作品页（作品标题已经出来了），
            #   5.5 秒才被重定向到 /jingxuan —— 同一条链接过几秒再试往往就成了。
            #   以前一次失败就判死，把这类可救的链接全浪费了。
            if b_note and ("重定向" in b_note or "首页" in b_note):
                time.sleep(2.5)
                browser_video, b_note2 = self._browser_render_resolve(render_url)
                if browser_video:
                    return browser_video, ""
                b_note = b_note2 or b_note
            if b_note and "已停用" not in b_note:
                reasons.append(f"{url_label}{b_note}")

            # ---- Step 3（0.1 元）：用作品 ID 查详情接口 ----
            # ★ 2026-10-24：`_paid_resolve_ok()` 为假时**整段跳过**。
            #   以前这里没判断，短路返回 None 后仍然拼一句「详情接口无结果」，
            #   客户看到就以为是欠费/额度问题 —— 其实一次接口都没调。
            vid = free_video_id or self.extract_video_id(url)
            if vid and self._paid_resolve_ok():
                result = self.get_video_detail(vid)
                if result:
                    return result, ""
                # ★ v1.22.0：把接口的真实原因带出去（欠费/额度用完/无数据），
                #   别再显示一句「详情接口无结果」让客户干瞪眼
                why = (getattr(self, "_last_detail_reason", "")
                       or "详情接口无结果")
                reasons.append(f"{url_label}视频ID {vid[:10]}… {why}")

            # ---- Step 4（0.1 元兜底）：服务端解析分享链接 ----
            # 已经拿到作品 ID 就没必要再让服务端解析一遍链接，省一次调用。
            # ★ 免费档直接跳过：免费版的 client 是占位对象（没有 request），
            #   而且这一步本身就是花钱的，两条理由都指向"不许走"。
            if (not is_video_page and not vid
                    and self._paid_resolve_ok()
                    and not self._free_tier_blocked()):
                # Only try API if we haven't already extracted a video ID from the URL
                try:
                    resp = self.client.request(
                        "douyin-tiktok-china", "share-link-resolution-v1", {"shareUrl": url}
                    )
                    if resp.success:
                        resolved = resp.data
                        resolved_url = ""
                        resolved_id = ""

                        if isinstance(resolved, dict):
                            for key in ("url", "resolved_url", "location", "target_url",
                                         "redirect_url", "share_url", "long_url"):
                                v = resolved.get(key)
                                if isinstance(v, str) and v:
                                    resolved_url = v
                                    break
                            for key in ("aweme_id", "item_id", "video_id", "itemId", "awemeId"):
                                v = resolved.get(key)
                                if v and str(v).isdigit() and len(str(v)) >= 15:
                                    resolved_id = str(v)
                                    break
                        elif isinstance(resolved, str):
                            resolved_url = resolved

                        if resolved_id:
                            result = self.get_video_detail(resolved_id)
                            if result:
                                return result, ""
                            why = (getattr(self, "_last_detail_reason", "")
                                   or "接口查询无结果")
                            reasons.append(f"服务端解析出ID后{why}")

                        if resolved_url:
                            if VideoDownloader.is_douyin_home(resolved_url):
                                # 服务端也解不出来 → 基本可以确定链接本身失效了
                                reasons.append(
                                    "链接已失效（接口解析同样指向抖音首页）——"
                                    "请在抖音里重新复制一次分享链接"
                                )
                            else:
                                vid2 = self.extract_video_id(resolved_url)
                                if vid2:
                                    result = self.get_video_detail(vid2)
                                    if result:
                                        return result, ""
                                    why = (getattr(self, "_last_detail_reason", "")
                                           or "接口查询无结果")
                                    reasons.append(f"服务端解出URL取到ID后{why}")
                                else:
                                    reasons.append(f"API返回URL但无视频ID")
                    else:
                        if getattr(self.client, "token_invalid", False):
                            reasons.append(
                                f"API Token 无效/未激活（{getattr(resp, 'message', '') or 'code=' + str(getattr(resp, 'code', '?'))}），"
                                "请到「设置」页更新 Token"
                            )
                        else:
                            reasons.append(f"API解析失败(code={getattr(resp, 'code', '?')})")
                except Exception as e:
                    reasons.append(f"API异常({type(e).__name__})")

        # Step 5: final fallback — 全文里再抠一次作品 ID（只在允许调接口时）
        vid = self.extract_video_id(text)
        if vid and self._paid_resolve_ok() and not any(vid[:10] in r for r in reasons):
            result = self.get_video_detail(vid)
            if result:
                return result, ""
            reasons.append(f"全文提取ID查询无结果({vid[:10]}...)")

        # ★ 2026-10-24：主页链接不是"失败"，必须在这里就把话交回界面。
        #   放在所有兜底文案之前，否则会被下面那句「作品被删除、仅粉丝可见」
        #   整个盖掉 —— 客户看到的就永远是一句与真实原因无关的话
        #   （本次投诉的原话就是从这里来的）。
        if self._last_blogger_sec_uid:
            return None, ("这是博主主页链接（不是单条作品），"
                          "已自动改用「拉取列表」获取该账号的作品…")

        # ★ 解析只走零成本通道（v1.24.0）："查询无结果""接口异常"这类话
        #   现在谁都不该再看到（接口根本没被调用），换成能照着做的一句。
        if not self._paid_resolve_ok():
            # ★ 2026-10-24：以前这里不看原因、直接把四句话糊上去，客户收到的
            #   是「本地跟随失败；链接已失效…请重新复制；这次没读到播放地址…
            #   请重新复制；还不行就重新扫码」—— 同一句"重新复制"出现两遍，
            #   读起来像软件坏了。现在按**最具体的那条**给一句话。
            joined = "；".join(reasons)
            # ★ 2026-10-25：**先判有没有登录态**，再谈其他。
            #   顺序错了会骗人：没有 Cookie 的机器同样会表现为"被踢回首页"，
            #   若排在后面就永远轮不到它，客户收到的会是"登录态已经失效"——
            #   而人家压根没登录过，"失效"和"没有"是两件不同的事，
            #   对应的动作也不一样（一个是重新扫码，一个是先去扫码）。
            if not (getattr(self, "cookie", "") or "").strip():
                tail = ("当前【没有登录态】。抖音现在对未登录的浏览器会把作品页直接"
                        "重定向到首页，读不到任何作品数据 —— 到「设置 → 平台登录」"
                        "扫码登录，再重新导入这批链接。")
            elif "重定向" in joined:
                tail = ("抖音把浏览器踢回了首页 —— 软件里的登录态已经失效。"
                        "到「设置 → 平台登录」重新扫码登录，再点一次解析。")
            elif "已失效" in joined or "跳回" in joined or "跳转" in joined:
                tail = ("这条链接打不开了 —— 抖音的分享链接有效期很短，"
                        "请在抖音里重新复制一次再导入（旧链接放几天就会失效）。")
            elif getattr(self._page_resolver, "disabled", False):
                # 通道被限流停掉了 —— 别说成"作品被删除"，那会把客户引到错误方向
                tail = ("抖音这边像是在限流，浏览器解析已暂停 —— "
                        "过 10 分钟再跑剩下的这些，或到「设置 → 平台登录」换一份登录态。")
            else:
                tail = ("页面里没读到作品数据。登录态是有的，通常是这条作品被删除、"
                        "仅粉丝可见，或者抖音临时风控 —— 隔几分钟再试一次；"
                        "连续多条都这样，就到「设置 → 平台登录」换一份新登录态。")
            return None, tail

        return None, "；".join(reasons) if reasons else "未知原因"






































    @staticmethod
    def _extract_sec_uid(text: str) -> str | None:
        """Extract sec_uid (MS4wLj...) from text/URL."""
        if not text:
            return None
        m = re.search(r"MS4wLj[A-Za-z0-9_\-]+", text)
        return m.group(0) if m else None


    def _douyin_resolve_blogger_impl(self, profile_text: str) -> BloggerInfo | None:
        """
        [抖音专属实现] Resolve a Douyin blogger profile link / short link / share text to a BloggerInfo.

        原抖音作者解析逻辑整体搬至此私有方法；公开入口 resolve_blogger_link
        (带 platform 参数，默认 auto) 在自动分流判定为非抖音链接时调用本方法兜底抖音。

        Accepts:
          - Full profile URL (douyin.com/user/MS4wLjAB...)
          - Short link
          - Share text with embedded link

        Tries:
          1. Extract URLs from the text
          2. Extract sec_uid directly from the URL
          3. Call share-link-resolution-v1 to follow the redirect, then extract
          4. Call user-profile-v3 with the extracted sec_uid to get full info
        """
        if not profile_text or not profile_text.strip():
            return None

        text = profile_text.strip()
        sec_uid = ""

        # Step 1: extract URLs from the text
        urls = self.extract_urls(text)
        candidates = urls if urls else [text]

        for url in candidates:
            # Step 2: direct sec_uid extraction
            m = re.search(r"(?:user|profile)/([A-Za-z0-9_\-]+)", url)
            if m and m.group(1).startswith("MS4wLj"):
                sec_uid = m.group(1)
                break

            # Step 2.5: 短链本地跟随重定向（不消耗 API 额度，且 Token 失效时依然可用）
            if not sec_uid and VideoDownloader.is_short_link(url):
                final = self.resolve_short_link(url)
                info = VideoDownloader.parse_share_url(final)
                if info["kind"] == "blogger" and info["sec_uid"]:
                    sec_uid = info["sec_uid"]
                    break

            # Step 3: 云端 share-link-resolution-v1 兜底（数据采集类，JustOneAPI）
            #   ★ 2026-11-10：源码重建时这一段被截断，函数从这里开始少了 50 行，
            #     整个 _douyin_resolve_blogger_impl 永远隐式返回 None —— 客户粘
            #     任何不含 MS4w... 的主页链接都报「无法识别博主」。按备份补齐。
            #   免费版 / 欠费 / 无 client 时 request 会失败，静默跳过即可：
            #   浏览器会话抓取链路不依赖这一步，绝不能让它变成一句「欠费」。
            if not sec_uid:
                try:
                    resp = self.client.request(
                        "douyin-tiktok-china", "share-link-resolution-v1", {"shareUrl": url}
                    )
                except Exception:                          # noqa: BLE001
                    resp = None
                if resp is not None and getattr(resp, "success", False):
                    resolved = resp.data
                    resolved_url = ""
                    if isinstance(resolved, dict):
                        for key in ("url", "resolved_url", "location",
                                    "target_url", "redirect_url"):
                            v = resolved.get(key)
                            if isinstance(v, str) and v:
                                resolved_url = v
                                break
                    elif isinstance(resolved, str):
                        resolved_url = resolved
                    if resolved_url:
                        m2 = re.search(r"MS4wLj[A-Za-z0-9_\-]+", resolved_url)
                        if m2:
                            sec_uid = m2.group(0)
                            break

        # 分享口令里 sec_uid 有时不在链接里（"…?sec_uid=MS4w…" 或纯文本）
        if not sec_uid:
            m = re.search(r"MS4wLj[A-Za-z0-9_\-]+", text)
            if m:
                sec_uid = m.group(0)

        if not sec_uid:
            return None

        # Step 4: 资料接口只用来补昵称/头像；列表抓取只认 sec_uid，
        #   所以哪怕这里失败也必须返回 BloggerInfo，不能把整条链路判死。
        try:
            resp = self.client.request(
                "douyin-tiktok-china", "user-profile-v3", {"secUid": sec_uid}
            )
            user = {}
            if getattr(resp, "success", False) and isinstance(resp.data, dict):
                candidate = resp.data.get("user") or resp.data.get("data") or {}
                if isinstance(candidate, dict):
                    user = candidate
            avatar_obj = user.get("avatar_medium") or user.get("avatar_larger") or {}
            urls = (avatar_obj.get("url_list") or []) if isinstance(avatar_obj, dict) else []
            return BloggerInfo(
                sec_uid=sec_uid,
                nickname=user.get("nickname") or sec_uid[:16],
                uid=str(user.get("uid") or ""),
                follower_count=user.get("follower_count") or 0,
                avatar_url=urls[0] if urls else "",
                signature=user.get("signature") or "",
                platform="douyin",
            )
        except Exception:                              # noqa: BLE001
            return BloggerInfo(
                sec_uid=sec_uid, nickname=sec_uid[:16], uid="",
                follower_count=0, avatar_url="", signature="", platform="douyin",
            )

















































    @staticmethod
    def is_short_link(url: str) -> bool:
        """是否为需要跟随重定向才能判定类型的抖音短链。"""
        u = (url or "").lower()
        return any(h in u for h in (
            "v.douyin.com", "v.iesdouyin.com", "www.iesdouyin.com/share/",
        ))


    @staticmethod
    def is_douyin_home(url: str) -> bool:
        """是不是"跳回了抖音首页"。

        短链失效 / 被风控 / 无登录态时，抖音会统一 302 到 https://www.douyin.com。
        这种情况下页面 HTML 里塞满了推荐流的 aweme_id —— 如果继续往下解析，
        会抓出一个跟用户想看的完全不相干的视频（比直接报错更坑人）。
        所以凡是指向抖音首页根路径的落点，一律判定为"解析失败"。
        """
        u = (url or "").strip()
        if not u:
            return False
        try:
            import urllib.parse as _up
            p = _up.urlsplit(u)
        except Exception:                                  # noqa: BLE001
            return False
        host = (p.netloc or "").lower()
        if "douyin.com" not in host:
            return False
        return (p.path or "").strip("/") == ""


    def resolve_short_link(self, url: str) -> str:
        """本地跟随短链重定向，返回最终 URL（不消耗 API 额度）。

        失败返回空串。这是判定"视频链接 vs 博主主页链接"的唯一可靠方式，
        因为 v.douyin.com/xxxx 短链本身不含任何类型信息。
        """
        if not url:
            return ""
        # 已经是长链，无需解析
        if not self.is_short_link(url):
            return url
        # 移动端与桌面端 UA 在抖音短链上的落点并不一致
        # （移动端常见 iesdouyin 分享页，桌面端常见 douyin.com/video/…），
        # 两个 UA 各试一次，命中率明显更高。
        for ua in (self._SHORT_LINK_HEADERS["User-Agent"], self._DESKTOP_UA):
            try:
                resp = self._redirect_client.get(
                    url,
                    headers={"Referer": "https://www.douyin.com/", "User-Agent": ua},
                )
                final = str(resp.url or "")
                if not final or final == url:
                    continue
                if self.is_douyin_home(final):
                    # 短链没跳到作品页，而是掉进首页 → 视为失败，别让上层误判
                    continue
                return final
            except Exception:                              # noqa: BLE001
                continue
        return ""

    @staticmethod
    def parse_share_url(url: str) -> dict:
        """从（已跟随重定向的）URL 中识别类型并提取 ID。

        返回 {"kind": "video"|"blogger"|None, "sec_uid": str, "aweme_id": str}
        """
        u = url or ""
        out = {"kind": None, "sec_uid": "", "aweme_id": ""}

        # 博主主页：/share/user/<sec_uid>、/user/<sec_uid>、?sec_uid=xxx
        m = re.search(r"(?:share/user|share/profile|user|profile)/(MS4wLj[A-Za-z0-9_\-]+)", u)
        if m:
            return {"kind": "blogger", "sec_uid": m.group(1), "aweme_id": ""}
        m = re.search(r"[?&]sec_uid=(MS4wLj[A-Za-z0-9_\-]+)", u)
        if m:
            return {"kind": "blogger", "sec_uid": m.group(1), "aweme_id": ""}

        # 视频：/share/video/<id>、/video/<id>、?modal_id=<id>
        m = re.search(r"(?:share/video|video|note)/(\d{15,})", u)
        if m:
            return {"kind": "video", "sec_uid": "", "aweme_id": m.group(1)}
        m = re.search(r"[?&](?:modal_id|aweme_id|item_id|vid)=(\d{15,})", u)
        if m:
            return {"kind": "video", "sec_uid": "", "aweme_id": m.group(1)}

        return out


    def classify_url_kind(self, url: str) -> str:
        """classify_url 的便捷封装，直接返回 'video'|'blogger'|'unknown'。

        供 classify_input(resolver=...) 使用。
        """
        return self.classify_url(url)[0]

    def classify_url(self, url: str) -> tuple[str, str]:
        """把单个 URL 判定为 ('video'|'blogger'|'unknown', 原始url)。

        短链会先本地跟随重定向再判定；网络失败时按字面特征兜底。
        """
        kind = VideoDownloader.detect_input_type(url)
        if kind == "blogger_link":
            return "blogger", url
        if kind == "video_link" and not VideoDownloader.is_short_link(url):
            return "video", url
        # 短链：必须解析重定向才能知道是主页还是视频
        if VideoDownloader.is_short_link(url):
            final = self.resolve_short_link(url)
            info = VideoDownloader.parse_share_url(final)
            if info["kind"] == "blogger":
                return "blogger", url
            if info["kind"] == "video":
                return "video", url
            # 解析不出 → 按视频处理（保持旧行为），但上层会给出明确失败原因
            return "video", url
        return ("video", url) if kind == "video_link" else ("unknown", url)

    @staticmethod
    def detect_input_type(text: str) -> str:
        """
        Detect what kind of input the user provided.
        Accepts raw share text, pure URLs, nicknames, video IDs.

        Returns: 'video_link', 'blogger_link', 'nickname'
        """
        t = text.strip()
        if not t:
            return "nickname"

        # Extract URLs from text (handles share-text pastes)
        urls = VideoDownloader.extract_urls(t)
        check_text = urls[0] if urls else t

        # Video link patterns
        if any(p in check_text for p in ("douyin.com/video/", "iesdouyin.com/share/video",
                                          "v.douyin.com", "douyin.com/root/share/")):
            return "video_link"
        if re.search(r"video/\d{15,}", check_text):
            return "video_link"
        # Blogger profile link patterns
        if any(p in check_text for p in ("douyin.com/user/", "douyin.com/profile/",
                                          "www.douyin.com/user/")):
            return "blogger_link"
        if re.search(r"(?:user|profile)/MS4wLj", check_text):
            return "blogger_link"
        # Short link (v.douyin.com/xxxxx) - most are video links, but some are blogger
        # Default to video_link; resolve_share_link and resolve_blogger_link both try
        # to extract what they can from short links.
        if "v.douyin.com" in check_text:
            return "video_link"
        # Pure digits could be a video ID
        if check_text.isdigit() and len(check_text) >= 15:
            return "video_link"
        # Contains sec_uid pattern → blogger
        if re.search(r"MS4wLj[A-Za-z0-9_\-]+", t):
            return "blogger_link"
        # Otherwise treat as nickname
        return "nickname"


    @staticmethod
    def classify_input(text: str, resolver=None) -> dict:
        """
        四合一智能识别：把任意输入拆分为 {videos, bloggers, texts} 三类。

        - videos:   视频分享链接 / 纯数字视频ID（逐条解析为单个视频）
        - bloggers: 博主主页链接 / 含 sec_uid 的文本（解析该博主并列出其视频）
        - texts:    不含链接的纯文本（博主昵称 或 搜索关键词，交由上层二次判定）

        支持多行混合输入（每行一个），也支持整段复制粘贴的分享文案。
        返回的 videos / bloggers 已按出现顺序去重。

        resolver: 可选回调 (url) -> 'video'|'blogger'|'unknown'。
                  传入后，v.douyin.com 这类"无类型信息的短链"会先跟随重定向再判定，
                  从而支持粘贴【博主主页短链】也能自动识别为博主。
        """
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        videos: list[str] = []
        bloggers: list[str] = []
        texts: list[str] = []

        def _kind(u: str) -> str:
            if resolver is not None:
                try:
                    k = resolver(u)
                    if k in ("video", "blogger", "unknown"):
                        return k
                except Exception:
                    pass
            k = VideoDownloader.detect_input_type(u)
            if k == "blogger_link":
                return "blogger"
            if k == "video_link":
                return "video"
            return "unknown"

        for line in lines:
            urls = VideoDownloader.extract_urls(line)
            if urls:
                for u in urls:
                    kind = _kind(u)
                    if kind == "blogger":
                        bloggers.append(u)
                    else:
                        videos.append(u)
                continue
            kind = VideoDownloader.detect_input_type(line)
            if kind == "video_link":
                videos.append(line)
            elif kind == "blogger_link":
                bloggers.append(line)
            else:
                texts.append(line)

        def _dedup(seq: list[str]) -> list[str]:
            seen = set()
            out = []
            for x in seq:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out

        return {
            "videos": _dedup(videos),
            "bloggers": _dedup(bloggers),
            "texts": _dedup(texts),
        }
    def search_videos(self, keyword: str, count: int = 20) -> list[VideoInfo]:
        """Search Douyin videos by keyword. Returns up to `count` results."""
        resp = self.client.request(
            "douyin-tiktok-china", "video-search-v4",
            {"keyword": keyword, "count": count},
        )
        if not resp.success or not isinstance(resp.data, dict):
            return []
        awemes: list[dict] = []

        # 抖音搜索接口的实际返回结构：business_data[].data.aweme_info
        # 注意 business_data 中混有 related_word_list（关联词卡片，type!=1）等非视频项，需按 aweme_info 过滤。
        bd = resp.data.get("business_data")
        if isinstance(bd, list):
            for item in bd:
                if not isinstance(item, dict):
                    continue
                d = item.get("data")
                if not isinstance(d, dict):
                    continue
                aw = d.get("aweme_info") or d.get("aweme")
                if isinstance(aw, dict) and aw.get("aweme_id"):
                    awemes.append(aw)

        # 兼容其它返回容器（若接口版本变化）
        if not awemes:
            for key in ("data", "aweme_list", "list", "items", "video_list"):
                v = resp.data.get(key)
                if isinstance(v, list):
                    awemes = v
                    break
                if isinstance(v, dict):
                    for sub_key in ("aweme_list", "list", "items", "video_list"):
                        sv = v.get(sub_key)
                        if isinstance(sv, list):
                            awemes = sv
                            break
                    if awemes:
                        break

        videos = [self._aweme_to_video_info(a) for a in awemes if isinstance(a, dict)]
        # 服务端不一定遵守 count，这里客户端再截断一次
        return videos[:count] if count and count > 0 else videos





    def download_video(
        self,
        video: VideoInfo,
        subdir: str = "",
        on_chunk: Callable[[int, int], None] | None = None,
        media_type: str = "video",
    ) -> tuple[Path | None, str]:
        """下载单个作品。media_type ∈ {video, audio, image, text}。

        - video: 无水印视频 mp4（默认）
        - audio: 先下视频再用 ffmpeg 抽 mp3
        - image: 图集图片（抖音图文笔记）
        - text:  仅保存文案(desc) 为 .md
        """
        media_type = (media_type or "video").lower()
        if media_type == "text":
            return self._save_caption(video, subdir)
        if media_type == "image":
            return self._download_images(video, subdir)
        # video / audio 都需先拿到视频文件
        path, err = self._download_video_file(video, subdir, on_chunk)
        if err or not path:
            return path, err
        if media_type == "audio":
            return self._extract_audio(path, video, subdir)
        return path, ""


    def _probe(self, d: Path) -> str:
        """目录可写性探测（带缓存，同一目录一批只探一次）。"""
        key = str(d).lower()
        if key not in self._probe_cache:
            self._probe_cache[key] = writable_problem(d)
        return self._probe_cache[key]

    def _relocate_save_dir(self, base: Path, problem: str,
                           subdir: str = "") -> tuple[Path | None, str]:
        """原目录写不进去时，找一个真能写的备用目录。返回 (目录 或 None, 说明)。"""
        for cand in fallback_save_dirs():
            target = cand / subdir if subdir else cand
            if not self._probe(target):
                note = (f"原保存目录 {base} 写不进去（{problem}），"
                        f"已自动改存到 {target}（可在设置里改回）")
                self.last_dir_note = note
                return target, note
        note = f"保存目录 {base} 写不进去（{problem}），备用目录也都不可写"
        self.last_dir_note = note
        return None, note

    def _resolve_save_dir(self, subdir: str = "") -> tuple[Path, str]:
        """返回本次真正可用的保存目录；原目录不可写则自动换掉。

        ★ 2026-11-21：兜底把字符串目录转成 Path。调用方（视频号解析页）
          曾直接把 QLineEdit 的文本塞进来，导致 `str / str` TypeError。
          页面侧已修，这里再兜一层，防止其它页面/历史配置重现同类事故。

        为什么要在每次下载前探：客户的 D 盘可能是移动硬盘（拔了就没了）、
        目录可能被杀软/同步盘临时锁住、也可能是上个版本以管理员身份建出来的
        只读目录。这些都不该让「下载 0 条」变成一堵墙。
        """
        if not isinstance(self.save_dir, Path):
            self.save_dir = Path(self.save_dir)
        base = self.save_dir / subdir if subdir else self.save_dir
        problem = self._probe(base)
        if not problem:
            self.last_save_dir_used = base
            return base, ""
        alt, note = self._relocate_save_dir(base, problem, subdir)
        self.last_save_dir_used = alt or base
        return (alt or base), note

    @staticmethod
    def _prepare_path(save_dir: Path, path: Path, video_id: str) -> tuple[Path, str]:
        """文件名会直接让 open() 失败的，换成 `<video_id>.<后缀>`。"""
        bad = _bad_windows_name(path.name)
        if not bad and len(str(path)) <= 250:
            return path, ""
        alt_name = f"{_safe_filename(video_id, 40) or 'video'}{path.suffix}"
        alt = save_dir / alt_name
        return alt, f"文件名{bad or '过长'}，已改存为 {alt_name}"


    @staticmethod
    def _is_write_error(err: str) -> bool:
        """这条失败原因是不是"写不进磁盘"（区别于链接失效/网络问题）。"""
        return err.startswith("写入失败")


    def _download_video_file(
        self,
        video: VideoInfo,
        subdir: str = "",
        on_chunk: Callable[[int, int], None] | None = None,
        _reparse_tried: bool = False,
    ) -> tuple[Path | None, str]:
        """下载无水印视频 —— 播放地址失效时**自动重解析一次**（v1.23.0）。

        客户现场（2026-10-23 截图）：20 条链接里有 5 条，四条候选地址
        （无水印/原地址 × 直连/代理）全部回 HTTP 404，界面上只剩一屏
        「地址不存在(404，链接可能已过期)」，客户的结论是"软件坏了"。

        404 的真相：抖音播放地址里带 expire 时间戳，有效期只有几小时到
        一两天。只要**地址是上一次解析留下来的**（批量导入的列表、客户
        午饭前解析完下午才点下载、软件里存了历史记录），CDN 上那个对象
        就已经没了 —— 换协议、换代理、换 UA 全都没用，唯一的解法是
        **拿这条作品的链接重新解析一遍，换一条新地址**。

        以前这一步丢给客户做（提示"重新解析一次再下"），但客户不会去
        一条条点重新解析，他只会觉得这软件不好用。现在软件自己重试：
        失败原因属于"链接失效"这一类时，重新解析一次拿新地址再下一次，
        仍然失败才把两次的原因一起报出来。

        成本：重解析优先走免费页面通道，不烧接口额度；真走了接口也只
        多花一次，比让客户反复手动重试划算。
        """
        path, err = self._download_video_file_once(video, subdir, on_chunk)
        if path or not err:
            return path, err
        if _reparse_tried or not self._looks_like_dead_url(err):
            return path, err
        fresh = self._reparse_for_fresh_url(video)
        if fresh is None:
            return path, err
        path2, err2 = self._download_video_file_once(
            fresh, subdir, on_chunk, )
        if path2:
            return path2, ""
        # 两次都失败：把两轮原因都留着，客户/服务方才能判断是不是真没了
        if err2 and err2 not in err:
            return None, f"{err2}（已自动重新解析一次，新地址同样失败）"
        return None, err

    @staticmethod
    def _looks_like_dead_url(err: str) -> bool:
        """失败原因是不是「播放地址本身失效」（值得重新解析）。

        必须和「写不进磁盘」「断网」「代理挂了」分开：后三种重解析一遍
        拿到的还是同一类结果，纯属白跑一次请求，还可能招来风控。
        """
        if not err:
            return False
        # 400 也收进来：视频号 finder.video.qq.com 的 token 过期后实测回 400，
        # 换一条新直链就能救回（2026-11-21 客户反馈）。
        for key in ("400", "404", "403", "410", "空内容", "不是视频", "链接可能已过期"):
            if key in err:
                return True
        return False


    def _reparse_for_fresh_url(self, video: VideoInfo) -> VideoInfo | None:
        """重新解析这条作品，拿到一条**新的**播放地址（拿不到则 None）。

        优先用原始分享链接；没有就按 video_id 拼一个作品页地址。两种都
        走解析链路的第 2 步（免费页面解析），免费档/免费版也能用。
        ★ 2026-11-05：快手列表直链同样会过期 —— 重解析走 zacao.top
          （视频采集端口），别拿快手的 video_id 去拼抖音地址。
        """
        plat = (getattr(video, "platform", "") or "douyin").lower()
        url = (getattr(video, "share_url", "") or "").strip()
        vid = (getattr(video, "video_id", "") or "").strip()
        if plat == "kuaishou":
            if not url:
                if not vid:
                    return None
                url = f"https://www.kuaishou.com/short-video/{vid}"
            try:
                from .media_parser import parse as _svc_parse
                data, _r = _svc_parse(url)
            except Exception:                           # noqa: BLE001
                return None
            new_url = str((data or {}).get("video_url") or "")
            if not new_url or new_url == (video.no_watermark_url or ""):
                return None
            # 只换地址、保留列表里带回来的标题/作者等元数据（文件名靠它们）
            return replace(video, no_watermark_url=new_url)
        # ★ 2026-11-21：视频号直链同样会过期（finder.video.qq.com 的 token
        #   有时效）。原来会掉进下面的抖音重解析分支，拿 /sph/ 链接当抖音
        #   作品页处理，注定失败 —— 免费解析通道本来就能处理它，直接复用。
        if plat in ("weixin_channels", "weixin_mp"):
            if not url:
                return None
            try:
                from .media_parser import parse as _svc_parse
                data, _r = _svc_parse(url)
            except Exception:                           # noqa: BLE001
                return None
            new_url = str((data or {}).get("video_url") or "")
            if not new_url or new_url == (video.no_watermark_url or ""):
                return None
            return replace(video, no_watermark_url=new_url)
        if not url:
            if not vid:
                return None
            url = f"https://www.douyin.com/video/{vid}"
        try:
            fresh, _reason = self._douyin_resolve_impl(url)
        except Exception:                           # noqa: BLE001
            return None
        if not fresh or not fresh.no_watermark_url:
            return None
        # 解析回来的还是同一条死链 —— 再下一次也没意义（省一次网络请求）
        if fresh.no_watermark_url == video.no_watermark_url:
            return None
        return fresh

    def _download_video_file_once(
        self,
        video: VideoInfo,
        subdir: str = "",
        on_chunk: Callable[[int, int], None] | None = None,
    ) -> tuple[Path | None, str]:
        """Download a single no-watermark video to disk. (原 download_video 主体)"""
        if not video.no_watermark_url:
            return None, "无可用下载链接（API未返回播放地址）"

        # ★ v1.19.1：先预检目录真能写。不能写就自动换到「文档/下载/主目录」
        #   下的备用目录，并把换目录的原因记在 last_dir_note 里给界面显示。
        #   以前没有这一步，客户只能看到 4 条「保存目录不可写」然后自己找设置。
        save_dir, dir_note = self._resolve_save_dir(subdir)

        # ★ 2026-11-19：命名口径抽成模块级 video_file_path，下载页的
        #   「是否已下载」状态列调的是同一个函数，杜绝两套规则漂移。
        save_path, name_note = video_file_path(video, save_dir)

        # Skip if already downloaded
        if save_path.exists() and save_path.stat().st_size > 0:
            return save_path, ""

        # 按视频所属平台匹配对应登录 Cookie，避免把抖音 Cookie 错用到其它平台。
        # 每个视频单独建客户端，规避多线程并发改同一共享客户端 Cookie 头的竞态。
        plat = getattr(video, "platform", "douyin") or "douyin"
        # ★ 2026-11-21：以前这里硬编码抖音 Referer，视频号/小红书等平台全靠
        #   _dl_headers_for() 才生效；统一走它，按平台发 Referer/Cookie。
        dl_headers = self._dl_headers_for(plat)

        # ★ v1.14.3：以前只有一条地址、一次尝试，失败了界面上只剩"失败 1 条"，
        #   客户和服务方都不知道为什么（2026-10-17 客户截图正是这样）。
        #   现在：① 优先用无水印地址（playwm → play）② 直连失败再走系统代理
        #   ③ 每条路都记下原因，全部失败时把原因带回去显示。
        errors: list[str] = []
        tried_relocate = False
        tried_short = False
        for url in self._download_candidates(video.no_watermark_url):
            kind = "无水印" if "/play/" in url else "原地址"
            for trust_env in (False, True):
                ok, err = self._download_once(url, dl_headers, save_path, on_chunk,
                                              trust_env=trust_env)
                if ok:
                    # 只在有需要告知的信息时才覆盖，别把"已自动改存"的提示清掉
                    if dir_note or name_note:
                        self.last_dir_note = dir_note or name_note
                    return save_path, ""
                errors.append(f"{kind}{'经代理' if trust_env else '直连'}：{err}")
                if not self._is_write_error(err):
                    continue        # 链接/网络问题，接着试下一条地址

                # 到底是"这个文件写不进去"还是"整个目录写不进去"？
                # 别猜错误码，直接探目录 —— 目录可写而文件写不进去，
                # 那就是文件被占用/只读/名字非法，换个短名就能过。
                dir_ok = self._probe(save_dir) == ""

                # ① 文件级：换短名重试一次
                short = save_dir / (f"{_safe_filename(video.video_id, 40) or 'video'}"
                                    f"{save_path.suffix}")
                if dir_ok and not tried_short and short != save_path:
                    tried_short = True
                    save_path = short
                    name_note = f"原文件写不进去，已改存为 {short.name}"
                    continue

                # ② 目录级：换到备用目录（原目录被杀软锁住、同步盘占用、盘符掉线）
                if not dir_ok and not tried_relocate:
                    tried_relocate = True
                    alt_dir, alt_note = self._relocate_save_dir(save_dir, err)
                    if alt_dir is not None:
                        save_dir = alt_dir
                        save_path, name_note = self._prepare_path(
                            save_dir, save_dir / save_path.name, video.video_id)
                        dir_note = alt_note
                        continue

                uniq = []
                for e in errors:
                    if e not in uniq:
                        uniq.append(e)
                return None, "；".join(uniq) + (f"（{dir_note}）" if dir_note else "")
        # 全部失败：返回去重后的原因，让界面能说人话。
        # ★ 2026-11-21：这里原先只写了 `uniq: list[str] = []` 就结束，函数
        #   隐式返回 None → 上层 `path, err = ...` 直接 TypeError，客户只看到
        #   「下载异常」而不是「404 / 网络错误」的真实原因。
        uniq: list[str] = []
        for e in errors:
            if e not in uniq:
                uniq.append(e)
        return None, "；".join(uniq) + (f"（{dir_note}）" if dir_note else "")

    @staticmethod
    def _download_candidates(url: str) -> list[str]:
        """同一个作品的可尝试播放地址，**无水印优先**。

        playwm = play with watermark，play = 无水印。页面解析出来的地址
        常常是 playwm —— 换成 play 既兑现"无水印"的承诺，也顺便多一条腿：
        CDN 偶尔只对其中一个路径的请求挑剔。
        """
        if not url:
            return []
        out: list[str] = []
        if "/playwm/" in url:
            out.append(url.replace("/playwm/", "/play/"))
        out.append(url)
        seen: set[str] = set()
        # 保序去重：两条腿偶尔会算成同一个地址，别白下载两遍
        return [x for x in out if not (x in seen or seen.add(x))]

    def _download_once(self, url: str, headers: dict, save_path: Path,
                       on_chunk=None, trust_env: bool = False) -> tuple[bool, str]:
        """下载一次（一条地址 + 一种网络路径）。成功返回 (True, "")。

        trust_env=False 表示忽略系统代理直连：抖音 CDN 在国内，走代理反而
        常被拦或超时；但有些客户机器只有代理能出网，所以两种都试一遍。
        """
        try:
            with httpx.Client(
                timeout=httpx.Timeout(300.0, connect=20.0),
                follow_redirects=True,
                headers=headers,
                trust_env=trust_env,
            ) as client:
                with client.stream("GET", url) as r:
                    r.raise_for_status()
                    ctype = (r.headers.get("content-type") or "").lower()
                    if ctype and not any(k in ctype for k in
                                         ("video", "octet-stream", "mp4", "binary")):
                        self._cleanup(save_path)
                        return False, (f"返回的不是视频({ctype.split(';')[0]}，"
                                       "多半是链接失效或被风控拦了，重新解析一次再下)")
                    total = int(r.headers.get("content-length", 0))
                    downloaded = 0
                    with open(save_path, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if on_chunk:
                                on_chunk(downloaded, total)
            if save_path.stat().st_size == 0:
                save_path.unlink(missing_ok=True)
                return False, "地址返回空内容（多为链接已过期，重新解析一次再下）"
            return True, ""
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            hint = {403: "被拒绝(403，多半是链接失效或需要登录)",
                    404: "地址不存在(404，链接可能已过期)",
                    410: "地址已失效(410)"}.get(code, "")
            self._cleanup(save_path)
            return False, f"HTTP {code} {hint}".strip()
        except httpx.RequestError as e:
            self._cleanup(save_path)
            return False, f"网络错误({type(e).__name__})"
        except OSError as e:
            # ★ 这里以前一律报「保存目录不可写」，但 OSError 覆盖了
            #   套接字被拦(WinError 10013)、磁盘满(112)、路径过长(206)……
            #   客户拿着「不可写」去改目录，改了也没用。现在按错误码说实话。
            self._cleanup(save_path)
            return False, _oserror_reason(e, save_path)
        except Exception as e:                              # noqa: BLE001
            self._cleanup(save_path)
            return False, f"下载异常({type(e).__name__})"

    @staticmethod
    def _cleanup(save_path: Path) -> None:
        try:
            if save_path.exists() and save_path.stat().st_size == 0:
                save_path.unlink()
        except OSError:
            pass



    def _save_caption(self, video: VideoInfo, subdir: str = "") -> tuple[Path | None, str]:
        """仅保存文案(desc) 为 Markdown 文件。"""
        save_dir, _note = self._resolve_save_dir(subdir)
        author = _safe_filename(video.author, 30) or "unknown"
        desc = _safe_filename(video.desc, 50)
        filename = f"{author}_{desc}_{video.video_id}.md"
        save_path, _ = self._prepare_path(save_dir, save_dir / filename, video.video_id)
        if save_path.exists() and save_path.stat().st_size > 0:
            return save_path, ""
        try:
            import datetime as _dt
            ctime = video.create_time
            ctime_str = _dt.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S") if ctime else "未知"
            content = (
                f"# {video.desc or '(无标题)'}\n\n"
                f"- 作者：{video.author}\n"
                f"- 平台：{video.platform}\n"
                f"- 发布时间：{ctime_str}\n"
                f"- 链接：{video.share_url}\n\n"
                f"## 文案\n\n{video.desc}\n"
            )
            save_path.write_text(content, encoding="utf-8")
            return save_path, ""
        except OSError as e:
            return None, _oserror_reason(e, save_path)
        except Exception as e:
            return None, f"保存文案失败: {type(e).__name__}"
    def _download_images(self, video: VideoInfo, subdir: str = "") -> tuple[Path | None, str]:
        """下载图集（图文笔记）的每张图片到独立子目录。"""
        if not video.image_list:
            return None, "该作品不是图集/无图片列表"
        save_dir, _note = self._resolve_save_dir(subdir)
        author = _safe_filename(video.author, 30) or "unknown"
        desc = _safe_filename(video.desc, 50)
        album_dir = save_dir / f"{author}_{desc}_{video.video_id}"
        try:
            album_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return None, _oserror_reason(e, album_dir)
        plat = getattr(video, "platform", "douyin") or "douyin"
        ck = (self.cookies.get(plat) or "").strip()
        dl_headers = dict(self._DL_HEADERS)
        if ck:
            dl_headers["Cookie"] = ck
        saved = 0
        last = None
        for idx, url in enumerate(video.image_list, 1):
            fpath = album_dir / f"{idx:02d}.jpg"
            if fpath.exists() and fpath.stat().st_size > 0:
                saved += 1
                last = fpath
                continue
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(120.0, connect=30.0),
                    follow_redirects=True, headers=dl_headers,
                ) as c:
                    with c.stream("GET", url) as r:
                        r.raise_for_status()
                        with open(fpath, "wb") as f:
                            for chunk in r.iter_bytes(chunk_size=65536):
                                f.write(chunk)
                if fpath.stat().st_size == 0:
                    fpath.unlink(missing_ok=True)
                    return None, f"第{idx}张图片下载为空"
                saved += 1
                last = fpath
            except OSError as e:
                return None, f"第{idx}张图片{_oserror_reason(e, fpath)}"
            except Exception as e:
                return None, f"第{idx}张图片下载失败: {type(e).__name__}"
    def _extract_audio(self, video_path: Path, video: VideoInfo, subdir: str = "") -> tuple[Path | None, str]:
        """用 ffmpeg 从已下载的视频中抽取 mp3。"""
        if not self.ffmpeg_path:
            return None, "未找到 ffmpeg，无法抽取音频（请在设置中指定 ffmpeg 路径）"
        mp3 = video_path.with_suffix(".mp3")
        if mp3.exists() and mp3.stat().st_size > 0:
            return mp3, ""
        try:
            proc = subprocess.run(
                [self.ffmpeg_path, "-y", "-i", str(video_path),
                 "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(mp3)],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0 or not mp3.exists() or mp3.stat().st_size == 0:
                err = proc.stderr[-300:] if proc.stderr else "未知错误"
                return None, f"音频抽取失败: {err}"
            return mp3, ""
        except Exception as e:
            return None, f"音频抽取异常: {type(e).__name__}: {e}"

    def download_videos(
        self,
        videos: list[VideoInfo],
        subdir: str = "",
        on_progress: Callable[[int, int, VideoInfo, Path | None, str], None] | None = None,
        on_chunk: Callable[[VideoInfo, int, int], None] | None = None,
        max_workers: int | None = None,
        media_type: str = "",
    ) -> list[tuple[VideoInfo, Path | None, str]]:
        """Download multiple videos concurrently.

        Args:
            videos: list of VideoInfo to download.
            subdir: optional subfolder under save_dir.
            on_progress: called after each video completes: (done_count, total, video, path, err).
            on_chunk: called during download of each video: (video, downloaded_bytes, total_bytes).
            max_workers: max parallel downloads. None → DEFAULT_DOWNLOAD_WORKERS.

        Returns [(video, path_or_None, error_msg), ...] in the same order as *videos*.
        error_msg is empty string on success.
        """
        total = len(videos)
        if total == 0:
            return []
        workers = max_workers or self.DEFAULT_DOWNLOAD_WORKERS
        workers = max(1, min(int(workers), total))

        self.last_dir_note = ""

        slots: list[tuple[VideoInfo, Path | None, str] | None] = [None] * total
        done = [0]
        lock = threading.Lock()

        # 同一个作品ID 只让一个线程下：并发重复下载同一条会互相覆盖写文件
        id_locks: dict[str, threading.Lock] = {}
        id_locks_guard = threading.Lock()

        def _lock_for(v: VideoInfo) -> threading.Lock:
            key = str(getattr(v, "video_id", "") or id(v))
            with id_locks_guard:
                return id_locks.setdefault(key, threading.Lock())

        def worker(idx: int, v: VideoInfo):
            chunk_cb = None
            if on_chunk:
                def chunk_cb(d, t, _v=v):
                    on_chunk(_v, d, t)
            try:
                with _lock_for(v):
                    path, err = self.download_video(
                        v, subdir=subdir, on_chunk=chunk_cb, media_type=media_type,
                    )
            except Exception as e:                      # noqa: BLE001
                path, err = None, f"下载异常: {type(e).__name__}: {e}"
            slots[idx] = (v, path, err)
            with lock:
                done[0] += 1
                finished = done[0]
            if on_progress:
                on_progress(finished, total, v, path, err)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dl") as ex:
            for idx, v in enumerate(videos):
                ex.submit(worker, idx, v)
        return [s for s in slots if s is not None]
    def _to_video_info_p(self, platform: str, raw: dict) -> VideoInfo | None:
        """把某平台的原始作品对象转成统一的 VideoInfo（best-effort）。"""
        if not isinstance(raw, dict):
            return None
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        if platform == "douyin":
            return self._aweme_to_video_info(raw)

        if platform == "xiaohongshu":
            vid = str(_deep_get(raw, "note_id", "id") or "")
            desc = str(_deep_get(raw, "title", "desc", "display_title") or "")
            author = str(_deep_get(raw, "user.nickname", "author.name", "nickname") or "")
            author_id = str(_deep_get(raw, "user.userid", "user.uid", "user.id", "userid") or "")
            cover = str(_deep_get(raw, "cover.url_list.0", "cover.url", "image.url") or "")
            play = str(_deep_get(
                raw, "video.media.stream.h264.0.master_url", "video.url",
                "video_info.url", "video.consumerUrl", "video.urlDefault") or "")
            dur = _deep_get(raw, "video.time", "time", "duration") or 0
            digg = _to_int_safe(_deep_get(raw, "interact_info.liked_count", "liked_count", "like_count"))
            ctime = _deep_get(raw, "time", "create_time", "createTime") or 0

        elif platform == "kuaishou":
            # ★ 2026-11-05 字段按真实返回校准（user-published-videos-v2 的 feeds，
            #   实测样本 10/10 命中）：photo_id / caption / user_name / user_id /
            #   like_count / timestamp 全在顶层；duration 与 timestamp 是毫秒；
            #   main_mv_urls 是播放直链（无水印，Range 206 可直下、不强制
            #   Referer），http 的升级成 https 再用。
            vid = str(_deep_get(raw, "photo_id", "photoId", "id") or "")
            desc = str(_deep_get(raw, "caption", "description", "desc") or "")
            author = str(_deep_get(raw, "user_name", "user.user_name", "author_name",
                                   "author.name", "nickname") or "")
            author_id = str(_deep_get(raw, "user_id", "user.user_id", "userId") or "")
            cover = str(_deep_get(raw, "coverUrl", "cover_urls.0.url",
                                  "cover_thumbnail_urls.0.url", "poster") or "")
            play = str(_deep_get(raw, "main_mv_urls.0.url", "mainMvUrls.0.url",
                                 "playUrl", "urlInfo.url") or "")
            if play.startswith("http://"):
                play = "https://" + play[len("http://"):]
            dur = _deep_get(raw, "duration") or 0
            digg = _to_int_safe(_deep_get(raw, "like_count", "likeCount", "count.likeCount"))
            ctime = _deep_get(raw, "timestamp", "createTime", "create_time") or 0

        elif platform == "tiktok":
            vid = str(_deep_get(raw, "id", "aweme_id", "awemeId", "video_id") or "")
            desc = str(_deep_get(raw, "desc", "caption.text", "caption") or "")
            author = str(_deep_get(raw, "author.nickname", "author.uniqueId", "author.name") or "")
            author_id = str(_deep_get(raw, "author.id", "author.uniqueId", "author.uid") or "")
            cover = str(_deep_get(raw, "video.cover", "video.coverUrl", "cover") or "")
            play = str(_deep_get(raw, "video.playAddr", "video.downloadAddr", "video.play_addr.url_list.0") or "")
            dur = _deep_get(raw, "video.duration", "duration") or 0
            digg = _to_int_safe(_deep_get(raw, "stats.diggCount", "likeCount", "like_count"))
            ctime = _deep_get(raw, "createTime", "create_time") or 0

        else:
            return None

        # 时长：抖音/小红书多为毫秒，快手/ TikTok 多为毫秒或秒，统一尽量转毫秒
        try:
            dur = int(dur) if dur else 0
        except (TypeError, ValueError):
            dur = 0
        try:
            ctime = int(ctime) if ctime else 0
        except (TypeError, ValueError):
            ctime = 0

        return VideoInfo(
            video_id=vid, desc=desc, author=author, sec_uid=author_id,
            create_time=ctime, duration=dur, digg_count=digg,
            cover_url=cover, no_watermark_url=play, share_url="",
            platform=platform,
        )

    def _to_blogger_p(self, platform: str, raw: dict) -> BloggerInfo | None:
        """把某平台的原始作者对象转成统一的 BloggerInfo（best-effort）。"""
        if not isinstance(raw, dict):
            return None
        if platform == "douyin":
            return None  # 抖音走原 search_blogger 逻辑
        if platform == "xiaohongshu":
            uid = str(_deep_get(raw, "user.userid", "user.uid", "userid", "id") or "")
            nick = str(_deep_get(raw, "user.nickname", "nickname") or "")
            fans = _to_int_safe(_deep_get(raw, "user.fans", "user.follower_count", "fans"))
            avatar = str(_deep_get(raw, "user.images.0", "user.avatar", "avatar") or "")
            sig = str(_deep_get(raw, "user.desc", "description", "signature") or "")
        elif platform == "kuaishou":
            uid = str(_deep_get(raw, "user_id", "userId", "id", "author.id") or "")
            nick = str(_deep_get(raw, "user_name", "userName", "nickname", "author.name") or "")
            fans = _to_int_safe(_deep_get(raw, "fan", "follower_count", "userProfile.fan"))
            avatar = str(_deep_get(raw, "headurl", "avatar", "user_profile.headurl") or "")
            sig = str(_deep_get(raw, "userProfile.signature", "signature") or "")
        elif platform == "tiktok":
            uid = str(_deep_get(raw, "author.id", "author.uniqueId", "id") or "")
            nick = str(_deep_get(raw, "author.nickname", "author.uniqueId", "nickname") or "")
            fans = _to_int_safe(_deep_get(raw, "author.fans", "author.follower_count", "authorStats.followerCount"))
            avatar = str(_deep_get(raw, "author.avatar", "avatar") or "")
            sig = str(_deep_get(raw, "author.signature", "signature") or "")
        else:
            return None
        return BloggerInfo(
            sec_uid=uid, nickname=nick, uid=uid, follower_count=fans,
            avatar_url=avatar, signature=sig, platform=platform,
        )

    def get_video_detail_p(self, platform: str, video_id: str) -> VideoInfo | None:
        """按平台取单条作品详情（含可下载播放地址）。"""
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        if platform == "douyin":
            return self.get_video_detail(video_id)
        if cfg.get("domestic_blocked"):
            return None
        id_param = {
            "xiaohongshu": "noteId", "kuaishou": "videoId", "tiktok": "videoId",
        }.get(platform, "videoId")
        resp = self.client.request(cfg["slug"], cfg["video_detail"], {id_param: video_id})
        if not resp.success or not isinstance(resp.data, dict):
            return None
        # 详情接口常把对象包在 data / note / video 等容器里，递归找作品对象
        obj = resp.data
        found = _deep_get(obj, "note", "video", "aweme", "data.note", "data.video")
        if isinstance(found, dict) and found:
            obj = found
        return self._to_video_info_p(platform, obj)






    def list_blogger_videos_p(self, platform: str, uid: str, max_cursor: int = 0,
                              count: int = 20, sort_type: int = 1):
        """按平台列出某博主/作者的作品页。返回 (videos, next_cursor, has_more)。"""
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        if platform == "douyin":
            return self.list_blogger_videos(uid, max_cursor=max_cursor, count=count, sort_type=sort_type)
        if cfg.get("domestic_blocked"):
            return [], max_cursor, False
        id_param = {"xiaohongshu": "userId", "kuaishou": "userId",
                    "tiktok": "secUid"}.get(platform, "userId")
        if platform == "kuaishou":
            # ★ 2026-11-05：page 参数实测**不翻页**（page=2/3 返回与 page=1
            #   完全相同的 10 条）。真正的游标是返回里的 pcursor —— 首页传
            #   空串，之后原样回传，直到它变成 "no_more"。
            params = {id_param: uid, "pcursor": str(max_cursor or ""), "count": count}
        else:
            params = {id_param: uid, "cursor": max_cursor, "count": count, "pageSize": count}
        resp = self.client.request(cfg["slug"], cfg["user_videos"], params)
        if not resp.success and platform == "kuaishou":
            # 301 = 上游瞬时采集失败（实测偶发），隔一拍重发一次
            time.sleep(1.5)
            resp = self.client.request(cfg["slug"], cfg["user_videos"], params)
        if not resp.success or not isinstance(resp.data, dict):
            return [], max_cursor, False
        raw_list = _deep_get(resp.data, "feeds", "notes", "items", "videoList",
                             "list", "data.items", "aweme_list")
        if not isinstance(raw_list, list):
            raw_list = []
        videos = [v for v in (self._to_video_info_p(platform, r) for r in raw_list) if v]
        if platform == "kuaishou":
            # pcursor 是 "1.792661175799E12" 这种科学计数字符串，不是 int ——
            # 单独走，别落进下面的 int() 转换
            pc = str(_deep_get(resp.data, "pcursor") or "no_more")
            return videos, pc, pc not in ("no_more", "", "None")
        next_cursor = _deep_get(resp.data, "cursor", "nextCursor", "page")
        if next_cursor is None:
            next_cursor = max_cursor
        try:
            next_cursor = int(next_cursor)
        except (TypeError, ValueError):
            next_cursor = max_cursor
        has_more = _deep_get(resp.data, "hasMore", "has_more") in (True, 1, "1", "true")
        return videos, next_cursor, bool(has_more)

    def search_blogger_p(self, platform: str, keyword: str) -> list[BloggerInfo]:
        """按平台搜作者。返回最多约 20 个候选。"""
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        if platform == "douyin":
            return self.search_blogger(keyword)
        if cfg.get("domestic_blocked"):
            return []
        resp = self.client.request(cfg["slug"], cfg["user_search"], {"keyword": keyword, "page": 1})
        if not resp.success or not isinstance(resp.data, dict):
            return []
        # 候选常包在 business_data / data.items / users 等容器
        raw_list = _deep_get(resp.data, "business_data", "data.items", "items", "users", "data.users")
        if not isinstance(raw_list, list):
            raw_list = []
        results = []
        for r in raw_list[:20]:
            if isinstance(r, dict):
                user = r.get("user", r.get("data", r))
                if isinstance(user, dict):
                    bi = self._to_blogger_p(platform, user)
                    if bi and bi.sec_uid:
                        results.append(bi)
        return results

    def search_videos_p(self, platform: str, keyword: str, count: int = 20) -> list[VideoInfo]:
        """按平台搜作品（关键词）。"""
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        if platform == "douyin":
            return self.search_videos(keyword, count=count)
        if cfg.get("domestic_blocked"):
            return []
        resp = self.client.request(cfg["slug"], cfg["video_search"], {"keyword": keyword, "count": count})
        if not resp.success or not isinstance(resp.data, dict):
            return []
        raw_list = _deep_get(resp.data, "business_data", "data.items", "items", "list", "videos")
        if not isinstance(raw_list, list):
            raw_list = []
        videos = []
        for r in raw_list:
            if not isinstance(r, dict):
                continue
            d = r.get("data", r)
            v = self._to_video_info_p(platform, d if isinstance(d, dict) else r)
            if v:
                videos.append(v)
        return videos[:count] if count and count > 0 else videos

    def resolve_blogger_link_p(self, platform: str, profile_text: str) -> BloggerInfo | None:
        """按平台解析作者主页链接 → BloggerInfo。"""
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        if platform == "douyin":
            return self.resolve_blogger_link(profile_text)
        if cfg.get("domestic_blocked"):
            return None
        text = (profile_text or "").strip()
        if not text:
            return None
        urls = self.extract_urls(text)
        candidates = urls if urls else [text]
        uid = ""
        for url in candidates:
            # 优先从 URL 直接提取作者 ID
            m = re.search(r"(?:user|profile|u|share/user|share/profile)/([A-Za-z0-9_\-]+)", url)
            if m and not m.group(1).isdigit():
                uid = m.group(1)
                break
            # 再试 share-link-resolution 接口
            try:
                resp = self.client.request(cfg["slug"], cfg["share_resolve"], {"shareUrl": url})
                if resp.success and isinstance(resp.data, dict):
                    for key in ("url", "resolved_url", "location", "target_url"):
                        v = resp.data.get(key)
                        if isinstance(v, str) and v:
                            mm = re.search(r"(?:user|profile|u)/([A-Za-z0-9_\-]+)", v)
                            if mm and not mm.group(1).isdigit():
                                uid = mm.group(1)
                                break
            except Exception:  # noqa: BLE001
                pass
            if uid:
                break
        if not uid:
            # 退化为整段文本里找非纯数字 ID
            m = re.search(r"(?:user|profile|u)/([A-Za-z0-9_\-]{6,})", text)
            if m and not m.group(1).isdigit():
                uid = m.group(1)
        if not uid:
            return None
        resp = self.client.request(cfg["slug"], cfg["user_profile"], {"userId" if platform != "tiktok" else "secUid": uid})
        if resp.success and isinstance(resp.data, dict):
            user = _deep_get(resp.data, "user", "userProfile", "data.user", "data")
            if isinstance(user, dict):
                bi = self._to_blogger_p(platform, user)
                if bi:
                    return bi
        return BloggerInfo(sec_uid=uid, nickname=uid[:16], uid=uid, platform=platform)

    # --------------------------------------------- 快手：账号作品批量采集（两段式）
    #
    # ★ 双端口路由铁律（用户 2026-11-05 确立，永远分清楚）：
    #   ① 作品**列表** = 数据采集类，走 JustOneAPI（user-published-videos-v2，
    #      按次计费，数字 userId + pcursor 翻页）；
    #   ② 每条的**下载地址** = 视频采集类，走 video.zacao.top（作者自己的
    #      服务，免费、不进付费闸门）。
    #   两个 key 绝不混用。实测 feeds 里自带 main_mv_urls 播放直链（无水印、
    #   Range 206 可直下、不强制 Referer），它随列表一起回来、零额外请求 ——
    #   作为首选地址；zacao.top 只对「列表没带地址」的条目兜底。这样没有
    #   为下载地址多调任何一个 JustOneAPI 接口，不违背分工铁律。

    _KS_PROFILE_RE = re.compile(r"kuaishou\.c[no]m?/profile/([A-Za-z0-9_\-]{3,})", re.I)
    _KS_SHORT_RE = re.compile(r"v\.kuaishou\.com/([A-Za-z0-9]{5,})", re.I)

    def _resolve_kuaishou_uid(self, profile_text: str) -> tuple[str, str]:
        """快手主页链接 / 分享短链 → (数字 userId, 失败原因)。

        链路（每一步都实测过）：
          · www.kuaishou.com/profile/<kwaiId> → 直接取 kwaiId；
          · v.kuaishou.com/<短码> → 本机免费跟一次 302 重定向，Location 里
            同时有 user/<kwaiId> 与 shareObjectId=<数字 userId>；
          · user-profile-v1 吃 kwaiId（参数名叫 userId，字符串也认），返回里
            shareInfo.webShareVerifyData.objectId 是权威数字 userId。
        ★ 注意 objectId 与 shareUserId 的区别（两个账号交叉验证过）：
          objectId = 博主本人；shareUserId = 生成这条分享链接的人 ——
          拿后者去拉列表会拉出别人的（或空的）作品列表。
        """
        text = (profile_text or "").strip()
        if not text:
            return "", "请粘贴快手博主主页链接"
        kwai_id, num_hint = "", ""
        m = self._KS_PROFILE_RE.search(text)
        if m:
            kwai_id = m.group(1)
        if not kwai_id:
            m = self._KS_SHORT_RE.search(text)
            if m:
                # 短链：本机跟一次 302（免费、不花接口钱），从 Location 拿两个 ID
                try:
                    with httpx.Client(timeout=12, follow_redirects=False,
                                      trust_env=False,
                                      headers={"User-Agent": "Mozilla/5.0"}) as cli:
                        rr = cli.get(f"https://v.kuaishou.com/{m.group(1)}")
                    loc = rr.headers.get("location") or ""
                    mm = re.search(r"user/([A-Za-z0-9_\-]{3,})", loc)
                    if mm:
                        kwai_id = mm.group(1)
                    mm = re.search(r"shareObjectId=(\d{6,})", loc)
                    if mm:
                        num_hint = mm.group(1)
                except Exception:                       # noqa: BLE001
                    pass
        if not kwai_id and not num_hint:
            # 纯 kwaiId（字母开头）或纯数字 userId 直接粘进来的
            if text.isdigit():
                num_hint = text
            else:
                m = re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{4,}", text)
                if m:
                    kwai_id = m.group(0)
        if not kwai_id and not num_hint:
            return "", "没认出快手主页链接 —— 请复制博主主页链接或 App 里的分享链接再粘"
        # user-profile-v1 拿权威数字 userId（objectId）
        resp = self.client.request("kuaishou", "user-profile-v1",
                                   {"userId": kwai_id or num_hint})
        if resp.success and isinstance(resp.data, dict):
            oid = str(_deep_get(resp.data, "shareInfo.webShareVerifyData.objectId") or "")
            if oid.isdigit():
                return oid, ""
        if num_hint.isdigit():
            return num_hint, ""
        return "", "快手账号资料没查到 —— 换一条主页链接再试（或稍后再试）"

    def list_kuaishou_blogger_videos(self, profile_text: str, max_pages: int = 30,
                                     on_progress: Callable[[int], None] | None = None
                                     ) -> tuple[list[VideoInfo], str]:
        """快手账号作品批量采集（两段式）。返回 (videos, error_msg)。

        第 1 段（JustOneAPI，数据采集端口）：解析主页 → user-published-videos-v2
        按 pcursor 翻页拉全作品列表（每页 10 条，page 参数不翻页，别用）。
        第 2 段（video.zacao.top，视频采集端口）：列表没带 main_mv_urls 的条目，
        拼成 https://www.kuaishou.com/short-video/<photo_id> 逐条解析补直链。

        ★ 实测已知坑：
          · 301 COLLECT FAILED 是上游瞬时失败，重发一次；**账号一条公开作品
            都没有时上游会一直 301**（拿忠岩主页实测 4 连 301），此时报
            "可能没有公开作品"而不是"链接坏了"；
          · feed_id 字段（"id"）在 feeds 里是 None，f 短码在 share_info 里 ——
            都不用管，下载地址走 photo_id。
        """
        uid, reason = self._resolve_kuaishou_uid(profile_text)
        if not uid:
            return [], reason
        self._last_list_note = ""     # 别把上一轮（抖音/上次快手）的收尾说明串过来
        videos: list[VideoInfo] = []
        pcursor = ""
        partial_err = ""
        for _page in range(max(1, max_pages)):
            params = {"userId": int(uid), "pcursor": pcursor, "count": 20}
            resp = self.client.request("kuaishou", "user-published-videos-v2", params)
            if not resp.success:
                time.sleep(1.5)
                resp = self.client.request("kuaishou", "user-published-videos-v2", params)
            if not resp.success or not isinstance(resp.data, dict):
                if not videos:
                    return [], ("快手作品列表拉取失败 —— 该账号可能没有公开作品，"
                                "或上游采集暂时拥堵，过几分钟再试一次")
                partial_err = "列表没拉全（上游采集中途失败），稍后可以再拉一次补齐"
                break
            data = resp.data
            feeds = data.get("feeds") or []
            if not isinstance(feeds, list) or not feeds:
                break
            for raw in feeds:
                if not isinstance(raw, dict):
                    continue
                v = self._to_video_info_p("kuaishou", raw)
                if v and v.video_id:
                    v.share_url = f"https://www.kuaishou.com/short-video/{v.video_id}"
                    videos.append(v)
            if on_progress:
                try:
                    on_progress(len(videos))
                except Exception:                       # noqa: BLE001
                    pass
            pcursor = str(data.get("pcursor") or "no_more")
            if pcursor in ("no_more", "", "None"):
                break
            time.sleep(0.4)     # 轻微节流，别把上游打热
        if not videos:
            return [], partial_err or "该账号的作品列表是空的（可能没有公开作品）"

        # ---- 第 2 段：缺直链的条目走 zacao.top 补（免费端口，不花接口钱）
        missing = [v for v in videos if not v.no_watermark_url]
        if missing:
            try:
                from .media_parser import parse as _svc_parse
            except Exception:                           # noqa: BLE001
                _svc_parse = None
            for i, v in enumerate(missing):
                if _svc_parse is not None:
                    try:
                        data, _r = _svc_parse(v.share_url)
                    except Exception:                   # noqa: BLE001
                        data = None
                    if data and data.get("video_url"):
                        v.no_watermark_url = str(data["video_url"])
                if i < len(missing) - 1:
                    time.sleep(0.6)     # 对齐批量导入节流节奏，别触发服务端 429
        ok = sum(1 for v in videos if v.no_watermark_url)
        note = f"快手作品 {len(videos)} 条" + (
            f"，{len(videos) - ok} 条暂缺下载地址（可重新解析）" if ok < len(videos) else "")
        self._last_list_note = note
        return videos, partial_err

    def _resolve_share_link_with_reason_p(self, platform: str, share_text: str
                                          ) -> tuple[VideoInfo | None, str]:
        """按平台解析作品分享链接/ID → VideoInfo（best-effort）。"""
        cfg = DOWNLOAD_PLATFORMS.get(platform, DOWNLOAD_PLATFORMS["douyin"])
        # ★ 服务端解析必须排**第一**（v1.24.6）：一次付费接口都不查，小红书 /
        #   快手 / 视频号全靠它。排到 _paid_resolve_ok 那个分支之后就永远走不到
        #   这儿 —— 免费档客户会被一句「只有付费接口」直接挡回，而实际上服务端
        #   解析根本不花钱（2026-11-01 重建源码时顺序被写反，自检才抓出来）。
        svc_video, svc_reason = self._media_service_resolve((share_text or "").strip())
        if svc_video:
            return svc_video, ""
        label = cfg.get("label") or platform
        if cfg.get("domestic_blocked"):
            # ★ 2026-11-14：旧版那句「国内被屏蔽、本机无法解析」是服务端解析上线前的
            #   旧结论。实测服务端能打开 TikTok 完整作品页并给出无水印直链，
            #   真正打不开的只有 App「分享 → 复制链接」出来的短链。旧文案把客户
            #   推去「这平台不支持」，而换一条链接就能用 —— 客户因此报「tk 无法提取」。
            #   domestic_blocked 只保留作「不走付费接口」的闸门（详情/搜索等处），
            #   解析失败这里改给可操作的换链指引，不甩锅给网络环境。
            why = f"（{svc_reason}）" if svc_reason else ""
            return None, (
                f"这条{label}链接没能解析出来{why}。"
                "请换成完整作品链接：在浏览器里打开视频，复制地址栏网址"
                "（形如 tiktok.com/@作者/video/数字ID）。"
                "App 分享出来的短链要先跳一次转，网络不通时解析服务打不开；"
                "抖音 / 小红书 / 快手 / 视频号 的分享链接可以直接粘贴。")
        # 免费档解析不出来时，把服务端给的原因原样说清楚 —— 不甩锅给「没登录」
        # （下载走 CDN 直链，跟本机登录态无关），也不提「付费接口」这种客户听不懂的话。
        if not self._paid_resolve_ok():
            why = f"{svc_reason}。" if svc_reason else ""
            return None, (
                f"这条{label}链接没能解析出来：{why}"
                "换个完整的分享链接重复制一次再试（在 App 里点「分享 → 复制链接」，"
                "别用浏览器地址栏里的网址）；抖音链接直接粘贴就能下。")
        text = (share_text or "").strip()
        if not text:
            return None, "输入为空"
        urls = self.extract_urls(text)
        reasons = []
        # 1) 纯 ID 直查
        vid = self.extract_video_id(text)
        if vid:
            v = self.get_video_detail_p(platform, vid)
            if v:
                return v, ""
            reasons.append(f"ID {vid[:10]}… 查询无结果")
        # 2) 从 URL 提取 ID 直查
        for url in urls:
            v = platform == "douyin" and None
            # 不同平台 ID 在 URL 里的形态不同：小红书 noteId、快手 photoId、TikTok id
            m = re.search(r"(?:video|note|v|share/video|share/note)/([A-Za-z0-9_\-]+)", url)
            if m and not m.group(1).isdigit():
                v = self.get_video_detail_p(platform, m.group(1))
                if v:
                    return v, ""
                reasons.append(f"链接ID查询无结果")
        # 3) 退化为 share-link-resolution 接口
        for url in urls:
            try:
                resp = self.client.request(cfg["slug"], cfg["share_resolve"], {"shareUrl": url})
                if resp.success:
                    resolved = resp.data
                    rurl = resolved if isinstance(resolved, str) else (resolved or {}).get("url") or (resolved or {}).get("resolved_url") or ""
                    rid = ""
                    if isinstance(resolved, dict):
                        for k in ("noteId", "photoId", "videoId", "id", "aweme_id"):
                            val = resolved.get(k)
                            if val and not str(val).isdigit() or (str(val).isdigit() and len(str(val)) >= 10):
                                rid = str(val)
                                break
                    if not rid and rurl:
                        mm = re.search(r"(?:video|note|v)/([A-Za-z0-9_\-]+)", rurl)
                        if mm:
                            rid = mm.group(1)
                    if rid:
                        v = self.get_video_detail_p(platform, rid)
                        if v:
                            return v, ""
                        reasons.append("解析ID查询无结果")
            except Exception as e:  # noqa: BLE001
                reasons.append(f"接口异常({type(e).__name__})")
        return None, "；".join(reasons) if reasons else "未识别到有效链接"





















    def resolve_share_link_with_reason(self, share_text: str,
                                      platform: str = "auto") -> tuple[VideoInfo | None, str]:
        """解析作品分享链接/ID → VideoInfo。platform='auto' 时按域名自动分流。"""
        # ★ 必须在分流之前清空：非抖音平台走的是另一条实现（不会进
        #   _douyin_resolve_impl），残留上一次的主页 sec_uid 会让界面
        #   把一条小红书链接误当成抖音博主主页去拉列表。
        self._last_blogger_sec_uid = ""
        if platform == "auto":
            urls = self.extract_urls(share_text)
            if urls:
                p = platform_from_url(urls[0])
                if p and p != "douyin":
                    return self._resolve_share_link_with_reason_p(p, share_text)
        elif platform != "douyin":
            return self._resolve_share_link_with_reason_p(platform, share_text)
        # —— 以下为抖音原逻辑（保持不动）——
        return self._resolve_douyin_share_link_with_reason(share_text)






    def resolve_blogger_link(self, profile_text: str,
                             platform: str = "auto") -> BloggerInfo | None:
        """解析作者主页链接 → BloggerInfo。platform='auto' 时按域名自动分流。"""
        if platform == "auto":
            urls = self.extract_urls(profile_text)
            if urls:
                p = platform_from_url(urls[0])
                if p and p != "douyin":
                    return self.resolve_blogger_link_p(p, profile_text)
        elif platform != "douyin":
            return self.resolve_blogger_link_p(platform, profile_text)
        return self._douyin_resolve_blogger_impl(profile_text)





    def _aweme_to_video_info(self, aweme: dict) -> VideoInfo:
        """Build a VideoInfo from a raw aweme object."""
        video = aweme.get("video", {}) or {}

        # Try multiple sources for the no-watermark play URL
        no_wm_url = ""
        # Priority 1: play_addr.url_list (standard no-watermark CDN URL)
        play_addr = video.get("play_addr", {}) or {}
        urls = play_addr.get("url_list") or []
        if urls:
            no_wm_url = urls[0]

        # Priority 2: bit_rate[].play_addr.url_list (higher quality variants)
        if not no_wm_url:
            bit_rates = video.get("bit_rate") or []
            for br in bit_rates:
                if isinstance(br, dict):
                    bp = br.get("play_addr", {}) or {}
                    bp_urls = bp.get("url_list") or []
                    if bp_urls:
                        no_wm_url = bp_urls[0]
                        break

        # Priority 3: play_addr_h264 / play_addr_265 etc.
        if not no_wm_url:
            for key in ("play_addr_h264", "play_addr_265", "play_addr_hdr", "download_addr"):
                addr = video.get(key) or {}
                if isinstance(addr, dict):
                    u = addr.get("url_list") or []
                    if u:
                        no_wm_url = u[0]
                        break

        cover = video.get("cover", {}) or {}
        cover_urls = cover.get("url_list") or []
        cover_url = cover_urls[0] if cover_urls else ""

        # 图集（图文笔记）：aweme.images[] 每项含 url_list；单视频作品无此字段
        image_list: list[str] = []
        for img in (aweme.get("images") or []):
            if isinstance(img, dict):
                ul = img.get("url_list") or []
                if ul:
                    image_list.append(ul[0])

        author = aweme.get("author", {}) or {}
        stats = aweme.get("statistics", {}) or {}

        return VideoInfo(
            video_id=str(aweme.get("aweme_id", "")),
            desc=aweme.get("desc", "") or "",
            author=author.get("nickname", "") or "",
            sec_uid=author.get("sec_uid", "") or "",
            create_time=int(aweme.get("create_time", 0) or 0),
            duration=int(video.get("duration", 0) or 0),
            digg_count=int(stats.get("digg_count", 0) or 0),
            cover_url=cover_url,
            no_watermark_url=no_wm_url,
            share_url=aweme.get("share_url", "") or "",
            image_list=image_list,
            platform="douyin",
        )


    def _resolve_douyin_share_link_with_reason(
        self, share_text: str
    ):
        return self._douyin_resolve_impl(share_text)
