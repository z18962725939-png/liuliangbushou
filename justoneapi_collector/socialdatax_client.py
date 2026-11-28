# -*- coding: utf-8 -*-
"""SocialDataX 红果短剧专用通道（HTTP 直连）。

═══════════════════════════════════════════════════════════════
★ 用途边界（合规护栏，不许改）
═══════════════════════════════════════════════════════════════
本模块只用于：
  ① 客户自有 / 已授权短剧的二创素材获取；
  ② 找选题 / 看竞品打法的预览参考（少量、非系统性下载）。
绝不用于批量搬运未授权短剧。函数 ``download_episode`` 必须由 UI 在
「确认内容已获授权」弹窗之后才传 authorized=True。

★ 这是什么通道（与既有链路严格隔离）
═══════════════════════════════════════════════════════════════
- 本模块 = SocialDataX「社媒数据助手」的红果 HTTP API（POST + Bearer Key），
  端点 https://mcp.socialdatax.com/socialdatax/api/v1/hongguo/*
- 与 okdata（okd_ Key，无短剧接口）、XiaoYingAPI（project/secret）、
  media_parser（video.zacao.top 直链）是**四套独立账号与凭证**，严禁混用。
- 本模块**不内置支付、不收款**：积分不足时只给官网充值链接，由用户
  自己在浏览器完成；凭证只存本机 config.json，不外发、不写日志。

★ 计费与限制（来自官方 OpenAPI，2026-11-28 实读）
═══════════════════════════════════════════════════════════════
- 7 个业务接口每次成功调用返回 ``points.cost`` / ``points.balance``。
- HTTP 200 也可能带 ``{code, message}`` 业务错误 —— 必须先查 code。
- 下载地址有时效，必须拿到就下；解密密钥按清晰度一一配对。
- 官方不提供周榜 / 历史榜 / 排名趋势；「看热榜」只能走免凭证公开榜
  （drama_public_rank）或本模块的综合搜索，不能伪造榜单。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 常量

BASE_URL = "https://mcp.socialdatax.com"
API_PREFIX = "/socialdatax/api/v1"

# 七个红果能力（仅这 7 条，别的一律不许拼）
ENDPOINTS: dict[str, str] = {
    "search": f"{API_PREFIX}/hongguo/search/general",
    "series_detail": f"{API_PREFIX}/hongguo/series/detail",
    "video_download_links": f"{API_PREFIX}/hongguo/video/download-links",
    "series_comments": f"{API_PREFIX}/hongguo/series/comment/list",
    "series_comment_replies": f"{API_PREFIX}/hongguo/series/comment/reply/list",
    "video_comments": f"{API_PREFIX}/hongguo/video/comment/list",
    "video_comment_replies": f"{API_PREFIX}/hongguo/video/comment/reply/list",
}
BALANCE_PATH = f"{API_PREFIX}/points/balance"

# 官网 / 充值入口：只给链接，软件不收款、不代付、不收集支付信息
WEBSITE_URL = "https://socialdatax.com/ai"
KEY_URL = "https://socialdatax.com/ai"
RECHARGE_URL = "https://socialdatax.com/dashboard/billing"

CRED_HINT = (
    "短剧解析走 SocialDataX 红果专用通道，需要你自己的 API Key（与软件授权码、"
    "okdata、小影 API 都不是同一套）。Key 只保存在本机 config.json，不上传。"
    "积分不足时软件只会给出官网充值链接，由你自己在浏览器完成，软件不收款。"
)

LIMIT_HINT = (
    "使用限制：① 仅限自有 / 已授权短剧的二创素材获取与选题参考，"
    "批量搬运未授权内容属侵权；② 每次调用按官方返回扣除积分，余额为 0 时接口会拒绝；"
    "③ 下载地址有时效，拿到后请尽快下载；④ 官方不提供周榜 / 历史榜 / 排名趋势，"
    "'看热榜'显示的是红果官网公开榜（仅选题参考，不能下载）。"
)

# 充值提示：不含任何支付表单、二维码、代付入口
RECHARGE_HINT = (
    f"当前积分不足。请打开官网充值页面：{RECHARGE_URL}\n"
    "充值完成后回到本页点「查询余额」即可继续；软件不会代收任何款项。"
)

DEFAULT_TIMEOUT = 30.0
_MAX_PAGE_TOKEN = 524288
_ID_RE = re.compile(r"^[1-9][0-9]{0,18}$")

_API_KEY = os.getenv("SOCIALDATAX_API_KEY", "").strip()
_CONFIG_OVERRIDE = False


# ---------------------------------------------------------------- 凭证

def configure(api_key: str = "") -> None:
    """显式注入 Key（弹窗保存时调用）。无条件覆盖，允许清空。"""
    global _API_KEY, _CONFIG_OVERRIDE
    _CONFIG_OVERRIDE = True
    _API_KEY = (api_key or "").strip()


def credentials_ready() -> bool:
    _load_from_config()
    return bool(_API_KEY)


def current_credentials() -> str:
    _load_from_config()
    return _API_KEY


def credential_source() -> str:
    """凭证来源：只显示来源名，绝不回显 Key。"""
    _load_from_config()
    if not _API_KEY:
        return ""
    if os.getenv("SOCIALDATAX_API_KEY", "").strip():
        return "环境变量"
    return "本机配置"


def save_credentials(api_key: str) -> tuple[bool, str]:
    """把 Key 写进 config.json 的 socialdatax 段，只改这一个键。"""
    key = (api_key or "").strip()
    try:
        from justoneapi_collector import branding
        cfg = branding.app_data_dir() / "config.json"
        data: dict = {}
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001  配置损坏不连累其它键
                data = {}
        data["socialdatax"] = {"api_key": key}
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return False, f"凭证保存失败（{type(e).__name__}）：{e}"
    configure(key)
    return True, "SocialDataX Key 已保存到本机并立即生效"


def _load_from_config() -> None:
    """env 优先；显式 configure 之后不再回补旧值（否则清空清不掉）。"""
    global _API_KEY
    if _CONFIG_OVERRIDE or _API_KEY:
        return
    try:
        from justoneapi_collector import branding
        cfg = branding.app_data_dir() / "config.json"
        if cfg.exists():
            data = json.loads(cfg.read_text(encoding="utf-8"))
            sec = data.get("socialdatax") or {}
            _API_KEY = str(sec.get("api_key", "")).strip()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 请求

def _post(path: str, payload: dict[str, Any],
          timeout: float = DEFAULT_TIMEOUT) -> tuple[dict | None, str]:
    """POST JSON + Bearer。业务错误不抛异常，返回 (None, 人话原因)。"""
    _load_from_config()
    if not _API_KEY:
        return None, (
            "尚未配置 SocialDataX Key —— 请在「短剧解析」页点「设置通道」，"
            f"填你在 {KEY_URL} 申请的 API Key（与软件授权码不是同一套）"
        )
    try:
        import httpx
    except Exception as e:  # noqa: BLE001
        return None, f"缺少网络组件（{type(e).__name__}），请用完整安装包"

    url = BASE_URL + path
    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0),
                          follow_redirects=True, trust_env=False) as client:
            resp = client.post(url, headers=headers, json=payload)
    except Exception as e:  # noqa: BLE001
        return None, f"网络请求失败（{type(e).__name__}），请检查网络后重试"

    if resp.status_code == 401:
        return None, "SocialDataX Key 无效或已失效，请到官网重新获取后更新"
    if resp.status_code == 403:
        return None, "服务端拒绝该 Key（权限不足），请检查账号状态"
    if resp.status_code == 429:
        return None, "请求过于频繁，请稍等几秒再试"
    if resp.status_code >= 500:
        return None, f"服务端暂时不可用（HTTP {resp.status_code}），请稍后重试"
    if resp.status_code != 200:
        return None, f"请求失败（HTTP {resp.status_code}）"

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None, "返回内容不是合法 JSON，请稍后重试"

    if not isinstance(body, dict):
        return None, "返回结构异常"
    # ★ HTTP 200 也可能带业务错误码，必须先查
    if "code" in body and "message" in body:
        msg = str(body.get("message") or "未知错误")
        if int(body.get("code") or 0) in (1400, 1401):
            return None, f"接口拒绝：{msg}"
        return None, msg
    return body, ""


def _points_note(body: dict) -> str:
    """把本次积分消耗拼成可上屏的短句；不回显 Key。"""
    pts = body.get("points")
    if not isinstance(pts, dict):
        return ""
    cost = pts.get("cost")
    bal = pts.get("balance")
    if cost is None or bal is None:
        return ""
    return f"本次消耗 {cost} 积分，剩余 {bal} 积分"


def get_balance() -> tuple[int | None, str]:
    """只读查询积分余额，不扣费。"""
    _load_from_config()
    if not _API_KEY:
        return None, "尚未配置 SocialDataX Key，无法查询余额"
    try:
        import httpx
        with httpx.Client(timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=15.0),
                          follow_redirects=True, trust_env=False) as client:
            resp = client.get(BASE_URL + BALANCE_PATH,
                              headers={"Authorization": f"Bearer {_API_KEY}",
                                       "Accept": "application/json"})
    except Exception as e:  # noqa: BLE001
        return None, f"查询余额失败（{type(e).__name__}）"
    if resp.status_code == 401:
        return None, "Key 无效或已失效"
    if resp.status_code != 200:
        return None, f"查询余额失败（HTTP {resp.status_code}）"
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return None, "余额返回不是合法 JSON"
    if "code" in body:
        return None, str(body.get("message") or "查询余额失败")
    bal = body.get("balance")
    return (int(bal) if bal is not None else None), ""


# ---------------------------------------------------------------- 业务

def search(keyword: str, content_types: list[str] | None = None,
           page_token: str = "") -> tuple[dict | None, str]:
    """综合搜索：返回 {items, next_page_token, points}，另附 _note。"""
    kw = (keyword or "").strip()
    if not kw:
        return None, "请先输入剧名关键词"
    if len(kw) > 200:
        return None, "关键词最多 200 个字符"
    types = list(content_types or [])
    allowed = {"live_action", "comic", "ai_drama", "film_tv", "short_video", "image_text"}
    if any(t not in allowed for t in types):
        return None, "内容类型不合法"
    if len(page_token) > _MAX_PAGE_TOKEN:
        return None, "分页令牌过长"
    payload: dict[str, Any] = {"keyword": kw}
    if types:
        payload["content_types"] = types
    if page_token:
        payload["page_token"] = page_token
    body, err = _post(ENDPOINTS["search"], payload)
    if err:
        return None, err
    assert body is not None
    body["_note"] = _points_note(body)
    return body, ""


def series_detail(series_id: str = "", url: str = "") -> tuple[dict | None, str]:
    """剧集详情 + 完整分集列表（不分页、不含播放地址）。"""
    sid, link = (series_id or "").strip(), (url or "").strip()
    if bool(sid) == bool(link):
        return None, "series_id 与 url 必须二选一"
    if sid and not _ID_RE.match(sid):
        return None, "series_id 必须是 1~19 位数字"
    payload = {"series_id": sid} if sid else {"url": link}
    body, err = _post(ENDPOINTS["series_detail"], payload)
    if err:
        return None, err
    assert body is not None
    body["_note"] = _points_note(body)
    return body, ""


def video_download_links(video_id: str = "", video_url: str = "") -> tuple[dict | None, str]:
    """单集下载地址 + CENC 解密密钥。密钥与清晰度一一配对。"""
    vid, link = (video_id or "").strip(), (video_url or "").strip()
    if bool(vid) == bool(link):
        return None, "video_id 与 video_url 必须二选一"
    if vid and not _ID_RE.match(vid):
        return None, "video_id 必须是 1~19 位数字"
    payload = {"video_id": vid} if vid else {"video_url": link}
    body, err = _post(ENDPOINTS["video_download_links"], payload)
    if err:
        return None, err
    assert body is not None
    streams = body.get("streams") or []
    if not isinstance(streams, list) or not streams:
        return None, "该集没有返回可用清晰度"
    body["_note"] = _points_note(body)
    return body, ""


def series_comments(series_id: str = "", url: str = "",
                    page_token: str = "", max_items: int = 20) -> tuple[dict | None, str]:
    """整剧剧评（与单集评论是两个独立入口，不许混用 ID）。"""
    sid, link = (series_id or "").strip(), (url or "").strip()
    if not sid and not link:
        return None, "请提供 series_id 或剧集链接"
    payload: dict[str, Any] = {"series_id": sid} if sid else {"url": link}
    if page_token:
        payload["page_token"] = page_token
    if max_items:
        payload["max_items"] = int(max_items)
    body, err = _post(ENDPOINTS["series_comments"], payload)
    if err:
        return None, err
    assert body is not None
    body["_note"] = _points_note(body)
    return body, ""


def video_comments(video_id: str = "", video_url: str = "",
                   page_token: str = "", max_items: int = 20) -> tuple[dict | None, str]:
    """单集 / 独立视频评论。"""
    vid, link = (video_id or "").strip(), (video_url or "").strip()
    if not vid and not link:
        return None, "请提供 video_id 或单集链接"
    payload: dict[str, Any] = {"video_id": vid} if vid else {"video_url": link}
    if page_token:
        payload["page_token"] = page_token
    if max_items:
        payload["max_items"] = int(max_items)
    body, err = _post(ENDPOINTS["video_comments"], payload)
    if err:
        return None, err
    assert body is not None
    body["_note"] = _points_note(body)
    return body, ""


# ---------------------------------------------------------------- 归一化

def normalize_series(item: dict) -> dict[str, Any]:
    """把 SocialDataX 搜索结果摊平成页面稳定字段。"""
    it = item or {}
    return {
        "series_id": str(it.get("series_id") or "").strip(),
        "name": str(it.get("title") or "").strip(),
        "cover": str(it.get("cover_image_url") or it.get("cover") or "").strip(),
        "url": str(it.get("url") or "").strip(),
        "tags": [str(t) for t in (it.get("tags") or [])],
        "intro": str(it.get("description") or "").strip(),
        "heat": str(it.get("hot_score") or "").strip(),
        "score": str(it.get("score") or "").strip(),
        "favorite": str(it.get("collect_count") or "").strip(),
        "like": str(it.get("like_count") or "").strip(),
        "type": str(it.get("type") or "").strip(),
    }


def normalize_detail(data: dict) -> dict[str, Any]:
    """剧集详情 → 页面期望的 {name, episodes:[{ep,url,...}]}。

    ★ episodes 只有 video_id / 集号，没有播放地址；播放地址必须点击下载时
      再按单集调 video_download_links 取（链接有时效，提前取会过期）。
    """
    d = data or {}
    eps = []
    for e in (d.get("episodes") or []):
        eps.append({
            "ep": int(e.get("episode_number") or 0),
            "video_id": str(e.get("video_id") or "").strip(),
            "url": "",            # 刻意留空：必须下载前即时换取
            "source": "socialdatax",
            "quality": 0,
            "title": str(e.get("title") or "").strip(),
        })
    return {
        "name": str(d.get("title") or "").strip(),
        "series_id": str(d.get("series_id") or "").strip(),
        "episodes": eps,
        "total_episode_count": int(d.get("total_episode_count") or len(eps)),
    }


def pick_stream(streams: list[dict], prefer: str = "1080p") -> dict:
    """按清晰度标签挑一条：优先 prefer，其次按常见档位降序。"""
    if not streams:
        return {}
    for s in streams:
        if str(s.get("definition") or "").lower() == prefer.lower():
            return s
    order = ["1080p", "720p", "540p", "480p", "360p"]
    for label in order:
        for s in streams:
            if str(s.get("definition") or "").lower() == label:
                return s
    return streams[0]


def ffmpeg_decrypt_command(stream: dict, encrypted_path: str | Path,
                           dest: str | Path) -> list[str]:
    """CENC 解密命令：-decryption_key 必须在 -i 之前，与同一档 URL 配对。"""
    key = str((stream or {}).get("decryption_key") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("解密密钥不是 32 位小写十六进制，拒绝拼接命令")
    return [
        "ffmpeg", "-y", "-decryption_key", key,
        "-i", str(encrypted_path), "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy", "-movflags", "+faststart", str(dest),
    ]


def download_episode(stream: dict, dest: str | Path, authorized: bool = False,
                     timeout: float = 120.0) -> tuple[bool, str]:
    """下载单集：先取加密 MP4，再用同档密钥解密。

    ★ authorized 必须由 UI 在授权确认弹窗之后传 True。
    ★ 必须先调 video_download_links 即时换 URL（有时效），不能缓存旧地址。
    """
    if not authorized:
        return False, "未确认授权：只允许下载自有 / 已授权的短剧"
    url = str((stream or {}).get("download_url") or "").strip()
    if not url:
        return False, "该集没有可用的下载地址"
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return True, "已存在，跳过"
    dest.parent.mkdir(parents=True, exist_ok=True)
    enc = dest.with_name(dest.stem + ".encrypted.mp4")
    try:
        import httpx
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0),
                          follow_redirects=True, trust_env=False) as c:
            with c.stream("GET", url) as r:
                if r.status_code not in (200, 206):
                    return False, f"下载失败（HTTP {r.status_code}），链接可能已过期"
                with open(enc, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=1024 * 256):
                        if chunk:
                            f.write(chunk)
    except Exception as e:  # noqa: BLE001
        return False, f"下载中断（{type(e).__name__}）"
    try:
        cmd = ffmpeg_decrypt_command(stream, enc, dest)
    except ValueError as e:
        return False, str(e)
    try:
        import subprocess
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            return False, f"解密失败（ffmpeg 退出码 {proc.returncode}）"
    except FileNotFoundError:
        return False, "未找到 ffmpeg：请先安装 ffmpeg 并加入系统 PATH"
    except Exception as e:  # noqa: BLE001
        return False, f"解密失败（{type(e).__name__}）"
    try:
        enc.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return True, str(dest)
