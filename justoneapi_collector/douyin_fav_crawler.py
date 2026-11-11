"""抖音【喜欢 / 收藏】列表自研爬虫。

背景
----
JustOneAPI 官方仅暴露 作品列表 / 视频详情 / 用户详情 / 搜索 等接口，
**没有**「喜欢列表」「收藏列表」接口（已在 justoneapi_collector/platforms/
api_paths_generated.py 与官网双重核实）。而用户明确希望「喜欢和收藏的列表
一键下载」，因此本模块自研爬虫，直接调用抖音内部 Web API。

实现要点
--------
1. 直接调用抖音内部 Web 接口：
     - 喜欢（点赞）列表：https://www.douyin.com/aweme/v1/web/user/favourite/
     - 收藏列表：      https://www.douyin.com/aweme/v1/web/user/collect/
2. 签名：复用本工程已验证的**纯 Python a_bogus 签名**
   （见 douyin_live_stream._ab_sign，源自开源 DouyinLiveRecorder，无需任何
   外部 JS 引擎 / Node.js，打包零额外依赖）。
3. 翻页：用 max_cursor 顺序翻页，has_more 控制是否继续。
4. 反爬对抗：
     - 必须带**登录态 Cookie**（扫码登录抖音后写入 config.json 的 douyin_cookie），
       否则接口返回 2151/2152/2153（未登录 / Cookie 失效）。
     - 风控时接口不返 JSON 而返 HTML 验证页，本模块专门识别并给出可读错误。
     - 失败时明确区分「网络错误 / 风控 / 未登录 / 空列表」，方便用户排查。

可见性说明
----------
抖音的「喜欢」「收藏」大多仅对**本人**或**公开账号**可见，所以本功能主要面向
「登录账号本人的喜欢/收藏」下载；粘贴他人主页链接时，仅其公开可见的喜欢列表可拉取。
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import httpx

# 复用工程内已验证的纯 Python a_bogus 签名 + 默认 UA（与直播解析同源，零额外依赖）
from .douyin_live_stream import _ab_sign, _DEFAULT_UA


_FAVOURITE_API = "https://www.douyin.com/aweme/v1/web/user/favourite/"
_COLLECT_API = "https://www.douyin.com/aweme/v1/web/user/collect/"
_SELF_PROFILE_API = "https://www.douyin.com/aweme/v1/web/user/profile/self/"

# 通用 Web 接口参数（与 douyin_live_stream 的 web enter 对齐，已被 a_bogus 验证可用）
_COMMON_PARAMS: dict[str, str] = {
    "aid": "6383",
    "app_name": "douyin_web",
    "device_platform": "web",
    "language": "zh-CN",
    "browser_language": "zh-CN",
    "browser_platform": "Win32",
    "browser_name": "Chrome",
    "browser_version": "116.0.0.0",
    "cookie_enabled": "yes",
    "count": "20",
}

# 抖音常见「未登录 / Cookie 失效」状态码
_LOGIN_REQUIRED_CODES = {2151, 2152, 2153}


def _status_code(data: Any) -> int:
    """安全读取 status_code。

    注意：抖音成功时返回 status_code=0，而 0 是假值，
    若写成 ``data.get("status_code", -1) or -1`` 会把「成功」误判成失败，
    故此处必须显式判 None 而不能依赖真值判断。
    """
    raw = data.get("status_code", None)
    if raw is None:
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


class FavCrawlError(Exception):
    """爬虫层明确异常：缺登录态 / 风控拦截 / 接口报错 / 空列表等。"""


class DouyinFavCrawler:
    """直连抖音内部 Web API 拉取喜欢 / 收藏列表，自带 a_bogus 签名。"""

    def __init__(self, cookie: str = "", timeout: float = 20.0):
        self.cookie = (cookie or "").strip()
        self.timeout = timeout
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": _DEFAULT_UA},
        )

    # ----------------------------------------------------------- 工具方法
    @staticmethod
    def extract_sec_uid(text: str) -> str | None:
        """从文本 / 链接中提取 sec_uid（形如 MS4wLjAB...）。"""
        if not text:
            return None
        m = re.search(r"MS4wLj[A-Za-z0-9_\-]+", text)
        return m.group(0) if m else None

    @staticmethod
    def _extract_ms_token(cookie: str) -> str:
        """从 Cookie 中提取 msToken（接口常需要，缺失则留空靠 a_bogus 兜底）。"""
        m = re.search(r"msToken=([^;,\s]+)", cookie or "")
        return m.group(1) if m else ""

    def set_cookie(self, cookie: str) -> None:
        self.cookie = (cookie or "").strip()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ----------------------------------------- 登录账号本人资料（含 sec_uid）
    def fetch_own_profile(self) -> dict | None:
        """调 /aweme/v1/web/user/profile/self/ 取登录账号本人资料（含 sec_uid）。

        用于「拉取我自己的喜欢 / 收藏」：无需粘贴链接即可定位到本人。
        失败返回 None（例如未登录 / 风控 / 接口调整）。
        """
        params = dict(_COMMON_PARAMS)
        params["msToken"] = self._extract_ms_token(self.cookie)
        query = urllib.parse.urlencode(params)
        a_bogus = _ab_sign(query, _DEFAULT_UA)
        url = f"{_SELF_PROFILE_API}?{query}&a_bogus={a_bogus}"
        headers = {
            "User-Agent": _DEFAULT_UA,
            "Referer": "https://www.douyin.com/",
            "Cookie": self.cookie or "",
        }
        try:
            resp = self._client.get(url, headers=headers)
        except httpx.RequestError as e:
            raise FavCrawlError(f"网络请求失败：{e}")
        if "json" not in (resp.headers.get("content-type", "") or "") \
                and "<html" in resp.text[:256].lower():
            raise FavCrawlError(
                "抖音风控拦截：接口返回了 HTML 验证页（请确认已扫码登录且 Cookie 有效）"
            )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            raise FavCrawlError("响应解析失败（非 JSON）")
        status = _status_code(data)
        if status != 0:
            if status in _LOGIN_REQUIRED_CODES:
                raise FavCrawlError(
                    "未登录或 Cookie 已失效（status_code=%s）：请到「设置」页重新扫码登录抖音" % status
                )
            raise FavCrawlError(f"接口返回错误：status_code={status}")
        return data.get("user") or data.get("data") or {}

    # ----------------------------------------------------- 核心：拉取单页
    def _fetch_page(
        self, api: str, sec_uid: str, max_cursor: int, count: int,
    ) -> tuple[list[dict], int, bool, str]:
        """拉取喜欢 / 收藏的单页。

        返回 (aweme_list, next_max_cursor, has_more, error_msg)。
        error_msg 非空表示失败。
        """
        params = dict(_COMMON_PARAMS)
        params["sec_user_id"] = sec_uid
        params["max_cursor"] = str(max_cursor)
        params["count"] = str(count)
        params["msToken"] = self._extract_ms_token(self.cookie)
        query = urllib.parse.urlencode(params)
        a_bogus = _ab_sign(query, _DEFAULT_UA)
        url = f"{api}?{query}&a_bogus={a_bogus}"
        headers = {
            "User-Agent": _DEFAULT_UA,
            "Referer": "https://www.douyin.com/",
            "Cookie": self.cookie or "",
        }
        try:
            resp = self._client.get(url, headers=headers)
        except httpx.RequestError as e:
            return [], max_cursor, False, f"网络请求失败：{e}"

        # 风控：返回 HTML 验证页而非 JSON
        if "json" not in (resp.headers.get("content-type", "") or "") \
                and "<html" in resp.text[:256].lower():
            return [], max_cursor, False, (
                "抖音风控拦截：接口返回了 HTML 验证页（请确认已扫码登录、Cookie 有效，"
                "且未被限流；若刚登录请稍后再试）"
            )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return [], max_cursor, False, "响应解析失败（非 JSON，可能被风控）"

        status = _status_code(data)
        if status != 0:
            if status in _LOGIN_REQUIRED_CODES:
                return [], max_cursor, False, (
                    "未登录或 Cookie 已失效（status_code=%s）：请到「设置」页重新扫码登录抖音" % status
                )
            if status == 2142:
                return [], max_cursor, False, (
                    "该账号的喜欢 / 收藏列表不可见（status_code=2142）："
                    "抖音仅允许查看本人或公开账号的列表"
                )
            return [], max_cursor, False, f"接口返回错误：status_code={status}"

        # 兼容两种返回结构
        awemes = data.get("aweme_list") or (data.get("data") or {}).get("aweme_list") or []
        if not isinstance(awemes, list):
            awemes = []
        try:
            next_cursor = int(data.get("max_cursor", max_cursor) or max_cursor)
        except (TypeError, ValueError):
            next_cursor = max_cursor
        has_more = int(data.get("has_more", 0) or 0) == 1
        return awemes, next_cursor, has_more, ""

    def fetch_favourite(
        self, sec_uid: str, max_cursor: int = 0, count: int = 20,
    ) -> tuple[list[dict], int, bool, str]:
        """拉取单页「喜欢」列表。"""
        return self._fetch_page(_FAVOURITE_API, sec_uid, max_cursor, count)

    def fetch_collect(
        self, sec_uid: str, max_cursor: int = 0, count: int = 20,
    ) -> tuple[list[dict], int, bool, str]:
        """拉取单页「收藏」列表。"""
        return self._fetch_page(_COLLECT_API, sec_uid, max_cursor, count)
