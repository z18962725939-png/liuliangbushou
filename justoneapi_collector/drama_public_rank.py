# -*- coding: utf-8 -*-
"""红果官网公开热榜（免凭证降级数据源，2026-11-14）。

★ 用途边界（与 drama_parse_page.py 的合规护栏一致，不许放宽）
  只取官网首页 SSR 内联 JSON 里的「热播」**元数据**：
  剧名 / 名次 / 总集数 / 封面 / 分类标签。
  **不解析播放直链、不碰 CENC 解密、不登录、不翻页、不批量下载。**
  用途仅限「找选题 / 预览」参考；要拿集数与素材，必须由客户自备
  授权凭证走 drama_api 的完整链路，并在下载前确认内容授权。

为什么可以不用凭证：
  hongguoduanju.com 首页对匿名访客直接返回 _ROUTER_DATA，
  loaderData.page.homeSections 里有 4 个热播分区（all/human/comic/ai），
  每个 video_list 9 条，字段含 series_id / rank / series_title /
  series_cover / episode_cnt / category_list。一次 GET、无签名、无 Cookie。

为什么不抓带哈希的 CSS 卡片：
  类名带构建哈希（如 pc-scatter-card-title-t3QwNu），改版即失效；
  内联 JSON 是数据层，字段语义稳定得多。注意 JSON 后面还跟着一段 JS，
  所以必须用 JSONDecoder().raw_decode() 只取第一个值，整段 json.loads 会报
  "Extra data"。

★ 降级不是主链路：客户端配了 XiaoYingAPI 凭证时仍优先走 drama_api.rank()，
  只有没凭证时才用这里，让「看热榜」不至于变成一句无解的报错。
"""

from __future__ import annotations

import json
from typing import Any

HOME_URL = "https://hongguoduanju.com/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MARK = "_ROUTER_DATA = "
MAX_ITEMS = 24
DEFAULT_TIMEOUT = 12.0

# 失败时统一提醒还有另一条路，别让降级源变成新的死胡同
_TAIL = "；也可点「设置凭证」使用完整热榜"


def extract_router_data(html_text: str) -> dict[str, Any]:
    """从首页 HTML 里抠出内联 _ROUTER_DATA 的第一个 JSON 值。"""
    i = html_text.find(_MARK)
    if i < 0:
        raise ValueError("未找到榜单数据块")
    raw = html_text[i + len(_MARK):]
    end = raw.find("</script>")
    if end > 0:
        raw = raw[:end]
    obj, _ = json.JSONDecoder().raw_decode(raw.strip())
    if not isinstance(obj, dict):
        raise ValueError("榜单数据块结构异常")
    return obj


def _norm_item(raw: dict[str, Any], tab_name: str) -> dict[str, Any]:
    """摊平成 drama_api.normalize_series() 同构字段，字段缺失一律留空。"""
    rank = raw.get("rank")
    rank_txt = f"热榜第 {rank} 名" if isinstance(rank, int) and rank > 0 else ""
    cats = [
        str(c.get("name") or "").strip()
        for c in (raw.get("category_list") or [])
        if isinstance(c, dict)
    ]
    cats = [c for c in cats if c]
    cnt = raw.get("episode_cnt")
    if isinstance(cnt, int) and cnt > 0:
        cats.append(f"全{cnt}集")
    return {
        "series_id": str(raw.get("series_id") or "").strip(),
        "name": str(raw.get("series_title") or "").strip(),
        "cover": str(raw.get("series_cover") or "").strip(),
        "url": "",  # ★ 公开源不产生任何播放 / 外链入口
        "tags": cats,
        "intro": f"{tab_name} · {rank_txt}" if rank_txt else tab_name,
        "heat": rank_txt,
        "score": "",
        "favorite": "",
        "like": "",
    }


def parse_sections(router: dict[str, Any], limit: int = MAX_ITEMS) -> list[dict[str, Any]]:
    """把 4 个热播分区摊平并按 series_id 去重（all 分区优先，保留原榜序）。"""
    limit = max(1, min(int(limit), MAX_ITEMS))
    page = (router.get("loaderData") or {}).get("page") or {}
    sections = page.get("homeSections") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sec in sections if isinstance(sections, list) else []:
        if not isinstance(sec, dict):
            continue
        tab = str(sec.get("tab_name") or "热播").strip()
        videos = sec.get("video_list") or []
        for raw in videos if isinstance(videos, list) else []:
            if not isinstance(raw, dict):
                continue
            item = _norm_item(raw, tab)
            sid = item["series_id"]
            if not sid or not item["name"] or sid in seen:
                continue
            seen.add(sid)
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def _download(timeout: float) -> str:
    import httpx  # 函数体内 import：不用此功能的会话不付这个代价

    with httpx.Client(
        timeout=httpx.Timeout(timeout, connect=8.0),
        follow_redirects=True,
        trust_env=False,  # 与 media_parser / drama_api 一致，走系统代理反而常超时
    ) as c:
        r = c.get(
            HOME_URL,
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        r.raise_for_status()
        return r.text


def fetch_rank(limit: int = MAX_ITEMS, timeout: float = DEFAULT_TIMEOUT,
               html_text: str | None = None) -> tuple[list[dict[str, Any]], str]:
    """返回 (归一化剧集列表, 人话错误)。绝不抛异常。

    html_text 仅供离线测试 / 门禁注入，生产留空走真实网络。
    """
    try:
        html = html_text if html_text is not None else _download(timeout)
        items = parse_sections(extract_router_data(html), limit)
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        if "Timeout" in cls or "Connect" in cls or "Network" in cls:
            return [], "连不上红果官网（网络超时），换条网络或稍后再试" + _TAIL
        if "HTTPStatus" in cls:
            return [], "红果官网暂时不可用，稍后再试" + _TAIL
        return [], "官网榜单结构已调整，免凭证热榜暂不可用" + _TAIL
    if not items:
        return [], "公开热榜暂时没有返回数据，稍后再试" + _TAIL
    return items, ""
