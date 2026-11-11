"""
竞品账号体检模块（抖音）。

输入抖音主页分享链接 / 主页 URL / 直接 sec_uid，通过 Just One API 的抖音接口
（share-link-resolution / user-profile / user-published-videos）抓取账号基础
指标与作品表现，输出"干净"的竞品体检表（固定 10 列），可直接导出 Excel。

对应设计稿：解决 B2（账号数据被平铺成 100+ 列、运营无法阅读）的问题，
把运营真正关心的指标收敛成固定列。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PLATFORM = "douyin-tiktok-china"

# 竞品体检固定列：(字段键, 中文表头)。顺序即 Excel 列顺序。
COMPETITOR_COLUMNS = [
    ("nickname", "账号昵称"),
    ("sec_uid", "sec_uid"),
    ("follower_count", "粉丝数"),
    ("aweme_count", "作品数"),
    ("total_favorited", "获赞总数"),
    ("avg_interact", "平均互动(近N条)"),
    ("engage_rate", "赞粉比(互动率%)"),
    ("recent30_posts", "近30天更新数"),
    ("top5", "爆款Top5(标题|赞)"),
    ("updated_at", "体检时间"),
]


# ---- 字段容错提取 ----------------------------------------------------------

def _pick(data: Any, *keys: str, default: Any = None) -> Any:
    """在嵌套 dict 中按候选键顺序取第一个存在且非空的，支持 'a.b' 点路径。"""
    if not isinstance(data, dict):
        return default
    for key in keys:
        if "." in key:
            cur = data
            ok = True
            for part in key.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    ok = False
                    break
            if ok:
                return cur
        else:
            if key in data and data[key] not in (None, ""):
                return data[key]
    return default


def _to_int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, str):
        v = re.sub(r"[^\d]", "", v)
        return int(v) if v else 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ---- sec_uid 解析 ----------------------------------------------------------

_SHORT_LINK_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)

_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_SEC_UID_RE = re.compile(r"MS4wLj[A-Za-z0-9_\-]+")


def _first_url(text: str) -> str:
    """从整段分享文案中提取第一个 URL；没有则返回原文本身。"""
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;:!?）】》，。；：！？\"'')") if m else (text or "").strip()


def _resolve_short_link_local(url: str) -> str:
    """本地跟随抖音短链重定向，返回最终 URL（不消耗 API 额度）。

    抖音短链 v.douyin.com/xxxx 只有跟随重定向后才知道是视频还是博主主页。
    失败（网络不通 / 链接失效）返回空串。
    """
    u = url or ""
    if not u.startswith("http"):
        return ""
    try:
        import httpx
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=10.0),
            max_redirects=8,
            headers={"User-Agent": _SHORT_LINK_UA, "Referer": "https://www.douyin.com/"},
        ) as c:
            resp = c.get(u)
            final = str(resp.url or "")
            return final if final and final != u else ""
    except Exception:  # noqa: BLE001
        return ""


def _extract_sec_uid_from_url(url: str) -> str:
    """从（可能是重定向后的）URL 中提取 sec_uid。"""
    u = url or ""
    m = re.search(r"[?&]sec_uid=(MS4wLj[A-Za-z0-9_\-]+)", u)
    if m:
        return m.group(1)
    m = re.search(r"(?:share/user|share/profile|user|profile)/(MS4wLj[A-Za-z0-9_\-]+)", u)
    if m:
        return m.group(1)
    m = _SEC_UID_RE.search(u)
    return m.group(0) if m else ""


def _resolve_sec_uid(target: str, client) -> tuple[str | None, str]:
    """把用户输入（分享短链 / 主页URL / sec_uid / 整段分享文案）解析为 sec_uid。

    解析顺序（本地优先，省额度且不依赖 Token）：
      1. 输入本身即 sec_uid / 含 sec_uid=
      2. 从文本中提取 URL → 本地跟随重定向 → 提取 sec_uid
      3. 调 share-link-resolution-v1 兜底（消耗额度，Token 失效时会失败）

    返回 (sec_uid, 说明文字)。
    """
    t = (target or "").strip()
    if not t:
        return None, "输入为空"

    # 1) 直接就是 sec_uid（抖音 sec_uid 固定以 MS4w 开头）
    if t.startswith("sec_uid="):
        return t[len("sec_uid="):].strip(), "直接解析"
    if t.startswith("MS4w") or t.startswith("ms4w"):
        return t, "按sec_uid处理"
    if "sec_uid=" in t:
        m = re.search(r"sec_uid=([^&\s]+)", t)
        if m:
            return m.group(1), "从URL提取"
    # 不含斜杠且足够长，兜底当作 sec_uid（纯 sec_uid 无空格无链接特征）
    if "/" not in t and " " not in t and len(t) >= 20:
        return t, "按sec_uid处理"

    # 2) 从整段分享文案中提取 URL，本地解析（不消耗 API 额度）
    url = _first_url(t)
    local_final = ""
    if url.startswith("http"):
        # 2.1 字面就能看出是视频链接 → 直接给明确指引，免去网络请求
        if re.search(r"(?:share/video|/video/|/note/|modal_id=|aweme_id=)", url):
            return None, "这是【视频】链接，请用「视频下载」功能；竞品体检需要博主主页链接或 sec_uid"

        local_final = _resolve_short_link_local(url)
        candidate = local_final or url
        su = _extract_sec_uid_from_url(candidate)
        if su:
            return su, "本地短链解析" if local_final else "从URL提取"
        # 打开成功但拿到的是视频页 → 明确提示用户
        if local_final and re.search(r"(?:share/video|/video/|modal_id=)", local_final):
            return None, "这是【视频】链接，请用「视频下载」功能；竞品体检需要博主主页链接或 sec_uid"

    # 3) API 兜底：share-link-resolution-v1
    try:
        resp = client.request(PLATFORM, "share-link-resolution-v1", {"shareUrl": url})
    except Exception as e:  # noqa: BLE001
        return None, f"链接解析请求失败: {e}"
    if not resp.success:
        if getattr(client, "token_invalid", False):
            return None, f"API Token 无效或未激活（{resp.message}），请到「设置」页更新 Token"
        return None, f"链接解析失败: [{resp.code}] {resp.message}"
    data = resp.data or {}
    redir = _pick(data, "url", "redirect_url", "share_url", "link", "target_url", default="")
    su = _extract_sec_uid_from_url(str(redir))
    if su:
        return su, "短链解析"
    su = _pick(data, "sec_uid", "secUid", "sec_uid_str")
    if su:
        return str(su), "短链解析"
    return None, "短链解析未返回 sec_uid，请直接粘贴博主主页链接或 sec_uid"


# ---- 数据解析 --------------------------------------------------------------

def _parse_profile(profile_data: Any) -> dict:
    """从 user-profile-v3 返回里提取账号基础指标（兼容多层包裹）。"""
    d = profile_data or {}
    if not isinstance(d, dict):
        d = {}
    user = _pick(d, "user", "data.user", "author", "data", default=d)
    if not isinstance(user, dict):
        user = d
    return {
        "nickname": _pick(user, "nickname", "name", "unique_id", default=""),
        "follower_count": _to_int(_pick(user, "follower_count", "mplatform_followers_count",
                                        "fan_count", "followers", "fans")),
        "aweme_count": _to_int(_pick(user, "aweme_count", "video_count", "item_count")),
        "total_favorited": _to_int(_pick(user, "total_favorited", "liked_count",
                                        "favorited_count", "favorited")),
    }


def _parse_videos(video_data: Any) -> list:
    """从 user-published-videos 返回里提取作品列表。"""
    d = video_data or {}
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for key in ("aweme_list", "list", "items", "data", "awemes", "video_list", "awemeList"):
            if key in d and isinstance(d[key], list):
                return d[key]
    return []


def _video_metrics(v: dict) -> tuple[int, int, int, int]:
    """返回 (赞, 评论, 转发, 发布时间)。"""
    digg = _to_int(_pick(v, "statistics.digg_count", "aweme_stats.digg_count", "digg_count"))
    comment = _to_int(_pick(v, "statistics.comment_count", "aweme_stats.comment_count", "comment_count"))
    share = _to_int(_pick(v, "statistics.share_count", "aweme_stats.share_count", "share_count"))
    ct = _to_int(_pick(v, "create_time", "createTime", "publish_time"))
    return digg, comment, share, ct


# ---- 单账号体检 ------------------------------------------------------------

def check_one_account(client, target: str, max_pages: int = 5) -> dict:
    """体检单个抖音账号，返回一行固定列字典。"""
    row = {k: "" for k, _ in COMPETITOR_COLUMNS}
    row["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    sec_uid, note = _resolve_sec_uid(target, client)
    if not sec_uid:
        row["nickname"] = f"解析失败:{note}"
        return row

    # 账号基础信息（参数名须为驼峰 secUid，与已验证的视频下载模块保持一致）
    try:
        presp = client.request(PLATFORM, "user-profile-v3", {"secUid": sec_uid})
    except Exception as e:  # noqa: BLE001
        row["nickname"] = f"资料请求失败:{e}"
        return row
    if not presp.success:
        if getattr(client, "token_invalid", False):
            row["nickname"] = "解析失败:API Token 无效/未激活，请到「设置」页更新 Token"
        else:
            row["nickname"] = f"资料失败:[{presp.code}]{presp.message}"
        return row

    prof = _parse_profile(presp.data)
    row["nickname"] = prof["nickname"] or sec_uid
    row["sec_uid"] = sec_uid
    row["follower_count"] = prof["follower_count"]
    row["aweme_count"] = prof["aweme_count"]
    row["total_favorited"] = prof["total_favorited"]

    # 作品列表（带翻页，尽力覆盖近30天）
    videos: list = []
    max_cursor = 0
    pages = 0
    while pages < max_pages:
        params = {"secUid": sec_uid, "count": 20}
        if max_cursor:
            params["maxCursor"] = max_cursor
        try:
            vresp = client.request(PLATFORM, "user-published-videos-v1", params)
        except Exception:  # noqa: BLE001
            break
        if not vresp.success:
            break
        batch = _parse_videos(vresp.data)
        if not batch:
            break
        videos.extend(batch)
        pages += 1
        nxt = _to_int(_pick(vresp.data, "max_cursor", "maxCursor", "cursor"))
        has_more = _pick(vresp.data, "has_more", "hasMore", default=False)
        if not has_more or not nxt or nxt == max_cursor:
            break
        max_cursor = nxt

    # 计算作品指标
    interactions: list = []
    recent30 = 0
    now = time.time()
    for v in videos:
        if not isinstance(v, dict):
            continue
        digg, comment, share, ct = _video_metrics(v)
        interactions.append(digg + comment + share)
        if ct and (now - ct) <= 30 * 86400:
            recent30 += 1

    avg = round(sum(interactions) / len(interactions)) if interactions else 0
    row["avg_interact"] = avg
    fans = prof["follower_count"] or 1
    row["engage_rate"] = round(avg / fans * 100, 2) if fans else 0
    row["recent30_posts"] = recent30

    # 爆款 Top5
    ranked = sorted(
        [
            (_pick(v, "desc", "title", "caption", default="无标题"),
             _to_int(_pick(v, "statistics.digg_count", "aweme_stats.digg_count", "digg_count")))
            for v in videos if isinstance(v, dict)
        ],
        key=lambda x: x[1], reverse=True,
    )[:5]
    row["top5"] = "\n".join(f"{str(t)[:20]} | {d}赞" for t, d in ranked) if ranked else "无"
    # 顺手沉淀一份到本机历史库：同一个竞品往往会被反复体检，
    # 有了历史才能回答「他这个月到底在涨还是在掉」。失败一律静默。
    _snapshot_account(prof, videos, sec_uid)
    return row


def _snapshot_account(prof: dict, videos: list, sec_uid: str) -> None:
    """把账号与其作品写入本机历史库（每天一个数据点，同日覆盖）。

    本模块不走 platforms/adapter（它自己有一套针对抖音体检的取数字段），
    所以这里单独补一次挂载，否则竞品体检的数据永远不会进历史库。
    """
    try:
        from . import history_store as hs
    except Exception:                               # noqa: BLE001
        return
    try:
        if not hs or not hs.enabled():
            return
        fans = prof.get("follower_count", 0) or 0
        name = prof.get("nickname", "") or ""
        hs.snap_authors([{
            "platform": PLATFORM, "author_id": sec_uid,
            "nickname": name, "fans": fans,
        }], source="竞品体检")

        items: list[dict] = []
        for v in videos:
            if not isinstance(v, dict):
                continue
            pid = _pick(v, "aweme_id", "awemeId", "video_id", "id", default="")
            if not pid:
                continue
            digg, comment, share, ct = _video_metrics(v)
            items.append({
                "platform": PLATFORM, "post_id": str(pid),
                "title": _pick(v, "desc", "title", "caption", default=""),
                "author_id": sec_uid, "author_name": name, "author_fans": fans,
                "digg": digg, "comment": comment, "share": share,
                "play": _to_int(_pick(v, "statistics.play_count", "play_count",
                                      "statistics_play_count")),
                "publish_ts": ct,
            })
        if items:
            hs.snap_posts(items, source="竞品体检")
    except Exception:                               # noqa: BLE001
        pass


# ---- 批量体检 --------------------------------------------------------------

def check_accounts(client, targets: list[str], max_pages: int = 5) -> tuple[list[dict], list[str]]:
    """批量体检，返回 (结果行列表, 错误说明列表)。"""
    rows: list[dict] = []
    errors: list[str] = []
    for t in targets:
        try:
            row = check_one_account(client, t, max_pages=max_pages)
        except Exception as e:  # noqa: BLE001
            row = {k: "" for k, _ in COMPETITOR_COLUMNS}
            row["nickname"] = f"异常:{e}"
            row["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            errors.append(f"{t[:30]} -> {e}")
        rows.append(row)
    return rows, errors


# ---- 干净 Excel 导出 -------------------------------------------------------

def export_competitor_excel(rows: list[dict], filepath: str | Path) -> Path:
    """把体检结果导出为固定列 Excel（不平铺），Top5 单元格自动换行。"""
    try:
        import openpyxl
        from openpyxl.styles import Alignment
        from openpyxl.utils import get_column_letter
    except ImportError as e:  # noqa: BLE001
        raise ImportError("需要 openpyxl 才能导出 Excel：pip install openpyxl") from e

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "竞品体检"

    keys = [k for k, _ in COMPETITOR_COLUMNS]
    headers = [label for _, label in COMPETITOR_COLUMNS]
    ws.append(headers)

    for r in rows:
        ws.append([r.get(k, "") for k in keys])

    widths = {
        "账号昵称": 22, "sec_uid": 30, "粉丝数": 12, "作品数": 10,
        "获赞总数": 14, "平均互动(近N条)": 16, "赞粉比(互动率%)": 16,
        "近30天更新数": 14, "爆款Top5(标题|赞)": 42, "体检时间": 18,
    }
    for i, (_, label) in enumerate(COMPETITOR_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(label, 14)

    top5_idx = keys.index("top5") + 1
    for cell in ws[get_column_letter(top5_idx)]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(path)
    return path
