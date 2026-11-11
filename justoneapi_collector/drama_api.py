# -*- coding: utf-8 -*-
"""短剧解析客户端（XiaoYingAPI · drama 服务）。

═══════════════════════════════════════════════════════════════
★ 用途边界（合规护栏，2026-11-02 立，不许改）
═══════════════════════════════════════════════════════════════
本模块**只用于**：
  ① 客户**自有 / 已授权**短剧的二创素材获取；
  ② 「找选题 / 看竞品打法」的预览参考（少量、非系统性下载）。
**绝不**用于批量搬运未授权短剧 —— 该 API 第 4 集起会在服务端解密
DRM 加密流再中转，绕过平台版权保护，批量扒未授权内容即构成侵权。

因此本模块刻意**不提供"默认全量批量抓取"**能力：下载必须逐集、
且 UI 层在第一次下载前必须弹「确认内容已获授权」的一次性确认。

═══════════════════════════════════════════════════════════════
★ 与 media_parser 的关系（独立、不混用）
═══════════════════════════════════════════════════════════════
media_parser 走的是 video.zacao.top（作者自己的「链接→无水印直链」服务，
X-API-Key 鉴权）。本模块走的是 XiaoYingAPI（project signature 鉴权）。
**两套账号体系、两个 key，绝不能混用**（老规矩：混用会让客户看到
跟他无关的报错）。凭证走独立环境变量 / config.json 的 xiaoying_api 段，
不碰 TRAFFIC_MEDIA_KEY。
"""

from __future__ import annotations

import os
import re
import time
import hmac
import hashlib
from typing import Any
from pathlib import Path

# ----------------------------------------------------------------------
#  配置：独立凭证，env 优先，其次 config.json 的 xiaoying_api 段
# ----------------------------------------------------------------------
BASE_URL = "https://www.xiaoyingapi.com"
# 当前只接红果；其它渠道（如后续接入的源站）按同结构加 CHANNEL 即可。
CHANNEL = "hongguo"

PROJECT_ID = os.getenv("TRAFFIC_XIAOYING_PROJECT", "").strip()
PROJECT_SECRET = os.getenv("TRAFFIC_XIAOYING_SECRET", "").strip()

DEFAULT_TIMEOUT = 20.0

# 剧集列表/搜索的字段归一化（源站返回的常见中文键 → 内部键）
_NAME_ALIASES: dict[str, str] = {
    "逆袭": "逆袭", "甜宠": "甜宠",
}  # 占位：tags 直接保留源站字符串，不做强映射


def configure(project_id: str = "", secret: str = "") -> None:
    """由启动逻辑 / 设置页注入凭证（优先于模块级默认值）。"""
    global PROJECT_ID, PROJECT_SECRET
    if project_id:
        PROJECT_ID = project_id.strip()
    if secret:
        PROJECT_SECRET = secret.strip()


def _load_from_config() -> None:
    """若 env 没给，则从 config.json 的 xiaoying_api 段读取（只补空的）。"""
    global PROJECT_ID, PROJECT_SECRET
    if PROJECT_ID and PROJECT_SECRET:
        return
    try:
        from justoneapi_collector import branding
        cfg = branding.app_data_dir() / "config.json"
        if cfg.exists():
            import json
            data = json.loads(cfg.read_text(encoding="utf-8"))
            sec = data.get("xiaoying_api") or {}
            if not PROJECT_ID:
                PROJECT_ID = str(sec.get("project_id", "")).strip()
            if not PROJECT_SECRET:
                PROJECT_SECRET = str(sec.get("secret", "")).strip()
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------------
#  签名（project signature）—— 2026-11-02 联调确认的真实算法
# ----------------------------------------------------------------------
# 官方文档（/docs/drama/ 与 /docs/user_center/ 的 FAQ）：
#   "需签名的端点按 app_id + timestamp + nonce + sign（HMAC-SHA256，密钥为 APPSECRET）调用"
# 实测确认：
#   ★ 算法 = HMAC-SHA256，密钥 = APPSECRET；
#   ★ 待签名消息 = 除 sign 外【所有】参数（app_id / timestamp / nonce + 业务参数）
#     按 key 升序拼成 "k=v&k=v..." 的字符串；
#   ★ 这四个鉴权参数（app_id / timestamp / nonce / sign）全部放在【查询串】里，
#     不是请求头（请求头传会被服务器判「签名参数缺失」）。
def _sign(params: dict[str, Any], secret: str) -> str:
    items = sorted(
        (k, v) for k, v in params.items()
        if k != "sign" and v is not None and v != ""
    )
    msg = "&".join(f"{k}={v}" for k, v in items)
    return hmac.new(
        secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _nonce() -> str:
    return str(int(time.time() * 1000))


# ----------------------------------------------------------------------
#  请求封装
# ----------------------------------------------------------------------
def _request(path: str, params: dict[str, Any] | None = None,
             timeout: float = DEFAULT_TIMEOUT) -> tuple[dict | None, str]:
    """统一 GET 请求 + 签名 + 包络解析。失败返回 (None, 人话原因)。不抛异常。"""
    _load_from_config()
    if not PROJECT_ID or not PROJECT_SECRET:
        return None, "未配置短剧解析凭证（在项目控制台拿到 project id / secret 后，设置环境变量 TRAFFIC_XIAOYING_PROJECT / TRAFFIC_XIAOYING_SECRET，或在设置页填写）"

    params = dict(params or {})
    # 鉴权四参数走【查询串】（非请求头）：app_id / timestamp / nonce / sign
    params["app_id"] = PROJECT_ID
    params["timestamp"] = str(int(time.time()))
    params["nonce"] = _nonce()
    params["sign"] = _sign(params, PROJECT_SECRET)

    import httpx  # 函数体内 import：不用此功能的会话不付这个代价（与 media_parser 一致）

    url = f"{BASE_URL}/api/dramas/{CHANNEL}/{path}"
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            trust_env=False,  # 服务端在公网，走系统代理反而常超时
        ) as c:
            r = c.get(url, params=params)
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            return None, f"短剧服务返回了非 JSON 响应（HTTP {r.status_code}）"
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        if "Timeout" in cls or "Connect" in cls or "Network" in cls:
            return None, "连不上短剧解析服务（网络超时）—— 换条网络或稍后再试"
        return None, f"短剧解析请求失败（{cls}）—— 稍后再试"

    try:
        code_i = int((body or {}).get("code"))
    except Exception:  # noqa: BLE001
        code_i = -1
    msg = str((body or {}).get("msg") or "").strip()
    if code_i != 10000:
        why = _ERR_TEXT.get(code_i) or msg or "短剧解析服务未返回结果"
        if msg and msg not in why:
            why = f"{why}（{msg}）"
        return None, why
    return body.get("data"), ""


_ERR_TEXT: dict[int, str] = {
    10001: "短剧解析服务：签名校验失败（凭证可能有误，请在设置页重新填写）",
    10002: "短剧解析服务：项目未启用或已停用（请在项目控制台检查状态）",
    400: "请求参数有误（可能是集数超出范围）",
    429: "短剧解析服务正在限流 —— 等一两分钟再试",
    500: "短剧解析服务端异常 —— 稍后再试",
}


# ----------------------------------------------------------------------
#  业务接口（均返回归一化 dict 或 list；失败返回 (None, 原因)）
# ----------------------------------------------------------------------
def rank(drama_type: str = "hot-drama", page: int = 1) -> tuple[dict | None, str]:
    """热榜。drama_type: hot-drama / live-action / ai-drama / comic-drama。"""
    return _request("rank", {"type": drama_type, "page": page})


def categories() -> tuple[dict | None, str]:
    """两级分类树。"""
    return _request("categories")


def list_by_category(category: str = "", page: int = 1) -> tuple[dict | None, str]:
    """按分类拉剧集列表。category 留空则取默认。"""
    params: dict[str, Any] = {"page": page}
    if category:
        params["category"] = category
    return _request("list", params)


def search(keyword: str, page: int = 1) -> tuple[dict | None, str]:
    """按剧名关键词搜索。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return None, "请输入剧名关键词"
    return _request("search", {"keyword": keyword, "page": page})


def detail(series_id: str, ep: int | None = None) -> tuple[dict | None, str]:
    """剧集详情。
    ep 为 None → 返回整部剧信息（含剧集列表 episodes）；
    ep 为具体集数 → 返回该集播放信息（playable / url / source / codec …）。
    """
    series_id = (series_id or "").strip()
    if not series_id:
        return None, "缺少 series_id"
    params: dict[str, Any] = {"series_id": series_id}
    if ep is not None:
        params["ep"] = ep
    return _request("detail", params)


def play_url(series_id: str, ep: int, codec: str = "h264",
             mode: str = "pipe") -> tuple[dict | None, str]:
    """拿某一集的播放地址。
    codec: hevc（原画不重编码）/ h264（兜底重编码）；
    mode:  pipe（边解密边发，不可拖动）/ file（先生成整集再出流，可拖动）。
    返回 detail 的播放信息（url 为限时直链 / 中转流）。
    """
    return detail(series_id, ep)


# ----------------------------------------------------------------------
#  归一化辅助（让 UI 拿到的字段稳定）
# ----------------------------------------------------------------------
def normalize_series(item: dict) -> dict[str, Any]:
    """把列表/搜索里的一条剧集摊平成稳定字段。"""
    return {
        "series_id": str(item.get("series_id") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "cover": str(item.get("cover") or "").strip(),
        "url": str(item.get("url") or "").strip(),
        "tags": list(item.get("tags") or []),
        "intro": str(item.get("intro") or "").strip(),
        "heat": str(item.get("heat") or "").strip(),
        "score": str(item.get("score") or "").strip(),
        "favorite": str(item.get("favorite") or "").strip(),
        "like": str(item.get("like") or "").strip(),
    }


def normalize_episode(item: dict) -> dict[str, Any]:
    """单集播放信息归一化。"""
    return {
        "ep": int(item.get("ep") or 0),
        "playable": bool(item.get("playable")),
        "url": str(item.get("url") or "").strip(),
        "source": str(item.get("source") or "").strip(),  # origin / stream
        "codec": str(item.get("codec") or "").strip(),
        "quality": int(item.get("quality") or 0),
        "duration": int(item.get("duration") or 0),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
        "poster": str(item.get("poster") or "").strip(),
        "ready": bool(item.get("ready")) if item.get("source") == "stream" else True,
        "stream_mode": str(item.get("stream_mode") or "").strip(),
    }


# ----------------------------------------------------------------------
#  下载（限时直链即时下；中转流支持 Range 断点）
# ----------------------------------------------------------------------
def download_episode(url: str, dest: str | Path, authorized: bool = False,
                    timeout: float = 120.0) -> tuple[bool, str]:
    """下载一集到本地。

    ★ 合规护栏：authorized 必须由 UI 层在「确认内容已获授权」弹窗后传 True。
      本函数不校验授权内容本身，只机械执行下载；授权确认的责任在调用方。
    限时直链必须拿到就下；中转流（source=stream）支持 Range 断点续传。
    """
    if not authorized:
        return False, "未确认授权：只允许下载自有 / 已授权的短剧"
    url = (url or "").strip()
    if not url:
        return False, "没有可下载的播放地址"
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return True, "已存在，跳过"

    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    start = tmp.stat().st_size if tmp.exists() else 0
    headers = {}
    if start > 0:
        headers["Range"] = f"bytes={start}-"
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0),
                          follow_redirects=True, trust_env=False) as c:
            with c.stream("GET", url, headers=headers) as resp:
                if resp.status_code not in (200, 206):
                    return False, f"下载失败（HTTP {resp.status_code}）"
                mode = "ab" if start > 0 else "wb"
                with open(tmp, mode) as f:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
        tmp.replace(dest)
        return True, str(dest)
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        return False, f"下载中断（{cls}）—— 可重试续传"
