# -*- coding: utf-8 -*-
"""
每日调用配额：按自然日统计 JustOneAPI 调用次数，超额即拦下。

═══════════════════════════════════════════════════════════════════════════
为什么要这个模块
═══════════════════════════════════════════════════════════════════════════
软件按「包 API」卖（客户不用自己申请 Token，Token 加密嵌在授权码里），
所以**每一次接口调用都是作者真金白银的成本**。19.9 元/月如果不限额，
一个批量采集用户一天就能把整月成本打穿。

限额写在授权码里（licensing 的 q 字段，受 Ed25519 签名保护），客户改不了；
本模块只负责「数数 + 拦人」。

═══════════════════════════════════════════════════════════════════════════
设计要点
═══════════════════════════════════════════════════════════════════════════
1) 只统计 JustOneAPI 调用。
   抖音直连的功能（喜欢/收藏列表抓取、视频文件下载、分享链接解析）不花钱，
   不占用额度，可以放心给客户敞开用。

2) 只统计【成功】的调用（code=0），与 JustOneAPI 的计费规则一致；
   缓存命中（raw_json._cached）也不计数 —— 那次本来就没发请求。

2.5) ★ 按【成本】扣，不是按【次数】扣（2026-10-11 修正）。
   JustOneAPI 各接口单价并不统一：抖音绝大部分是 0.1 元/请求，但
   hot-search-v1（热榜）、search-image-v1（图文搜索）是 0.2 元/请求。
   以前不管哪个接口一律 +1 次，等于把 0.2 元的接口按半价卖 —— 客户只要
   猛刷热榜，成本就翻倍。现在引入「分」：1 分 = 0.1 元，贵接口一次扣 2 分。
   授权码里的 q 字段单位仍是「分」，界面沿用「次额度」的说法不变。

3) 日界按 UTC+8（与 JustOneAPI 的配额日一致），**0 点**自动重置。
   ★ v1.20.1：曾经因为 `localtime(t+8h)` 在中国机器上等于 UTC+16，
     日界跑到下午 16:00，客户每天平白丢掉 16:00–24:00 这段额度。
     现在用 `gmtime(t+8h)`，且不随机器时区漂移。详见 `_today()` 的注释。

4) 计数写两处（%APPDATA% 与 %LOCALAPPDATA%），读取时取【较大】那份，
   删掉一个文件不会让额度回血；日期取【较新】那份，防止靠改系统时间刷额度。

安全边界（说清楚，不吹牛）：这是本地计数，防的是「随手删配置/改时间」级别的
白嫖，防不了专门逆向的人。真遇到重度滥用，靠 JustOneAPI 后台的用量监控发现，
再按机器码停他的码即可。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import branding
from .licensing import DEFAULT_DAILY_QUOTA, UNLIMITED
from .models import ApiResponse

STATE_FILENAME = "usage.json"

# 超限返回的业务码（自定义，避免与 JustOneAPI 官方 code 冲突）
QUOTA_EXCEEDED_CODE = -9001

# ----------------------------------------------------------------------
#  按天历史（2026-10-27，为首页的迷你趋势线而加）
# ----------------------------------------------------------------------
# 以前 usage.json 只有 {"date": 今天, "count": N}，每天覆盖一次 ——
# 想画「最近 7 天用了多少」这条线，连数据都没有。
#
# ★ 三条设计约束（别改成别的形状）：
#   1. **它是附加字段**，不参与任何计费判断。读不到就等于没有历史，
#      界面自动不画线（不会报错、不会显示 0 骗人）。
#   2. **只保留 14 天**：文件要小而稳，客户不可能看三个月前的用量。
#   3. **值只增不减**：同一天多次调用取最大值，避免两个落点（APPDATA /
#      LOCALAPPDATA）互相覆盖时把数字改小 —— 那是"白送额度"的同款风险。
HISTORY_KEY = "history"
HISTORY_KEEP = 14

# ======================================================================
#  接口成本表（1 分 = 0.1 元）
# ======================================================================
# 来源：JustOneAPI 后台「定价」页实测（抖音类目）。未登记的接口按 1 分计。
# 以后官方调价 / 新增高价接口，改这张表即可，闸门逻辑不用动。
ENDPOINT_COST: dict[str, int] = {
    "hot-search-v1": 2,          # 抖音 / 小红书 热榜：0.2 元
    "search-image-v1": 2,        # 抖音 图文搜索：0.2 元
}

# 未登记接口的默认成本（0.1 元）
DEFAULT_ENDPOINT_COST = 1


def endpoint_cost(endpoint: str) -> int:
    """该接口一次成功调用扣几分（1 分 = 0.1 元）。未知接口按 1 分计。

    拿不准成本时宁可少扣也别乱加 —— 扣多了会冤枉客户，但别把已知的高价
    接口漏掉，那是真金白银的亏。
    """
    try:
        n = int(ENDPOINT_COST.get(str(endpoint or "").strip().lower(),
                                 DEFAULT_ENDPOINT_COST))
    except (TypeError, ValueError):
        n = DEFAULT_ENDPOINT_COST
    return max(1, n)

_UTC8_OFFSET = 8 * 3600

# ★ 免费版次数住在额度状态文件的这个键里（与付费接口的 count 分开记）。
#   分开的理由：免费版走页面解析、不经过 client，两者混在一个计数器里，
#   会出现"免费用户把付费额度吃掉"或"付费客户额度被少算"两种事故。
FREE_STATE_KEY = "free_count"


# ======================================================================
#  日期与计数
# ======================================================================

def _today() -> str:
    """当前自然日（UTC+8），格式 YYYY-MM-DD。

    ★ v1.20.1：以前写成 `localtime(time.time() + 8h)`，在中国机器上等于
      **UTC+16** —— 日界落在下午 16:00 而不是 0 点。后果不只是文案难看：
      客户 16:00 之后的调用全被记到"明天"，等于每天白丢 8 小时额度，
      而且和 JustOneAPI 真实的计费日（UTC+8 零点）对不上，本地计数会
      偏离真实成本。首页因此一直写着「每天 16:00 重置」，而弹窗写的
      是 0 点，两边互相矛盾（客户 2026-10-20 报障的正是这个）。

      改成 `gmtime(t + 8h)`：把平移后的时刻当 UTC 解析，才是真正的
      UTC+8 墙上时钟，且**不随机器时区漂移**（客户把电脑改成美国时间
      也不会影响额度日界）。
    """
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + _UTC8_OFFSET))


def _shift(day: str, n: int) -> str:
    """day 加减 n 天（YYYY-MM-DD）。n 为负数表示往前推。"""
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _read_count(d: dict, day: str, key: str = "count") -> int:
    """把一个状态文件里的计数折算到「今天」，读不出按 0。

    ★ v1.20.1 兼容旧版日界 bug：老代码在 16:00–24:00 之间会把调用写成
      **明天**的日期（见 _today 的说明）。修正后如果直接忽略这些文件，
      客户会白拿一整天额度 —— 而那些接口调用是真花了钱的。所以「日期
      正好是明天」的文件按今天算。

      比明天还新的仍然不算：那是系统时间被往回拨过的痕迹（_bump 里
      有同款保护），给它计数就等于奖励改时钟。
    """
    d_day = str(d.get("date") or "")
    if d_day == day or d_day == _shift(day, 1):
        try:
            return max(0, int(d.get(key) or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def _state_paths() -> list[Path]:
    """两个落点：删掉一个，另一个还在。"""
    paths = [branding.app_data_dir() / STATE_FILENAME]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.append(Path(local) / branding.DATA_DIR_NAME / STATE_FILENAME)
    return paths


def _read(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                                  # noqa: BLE001
        return {}


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass


def today_count() -> int:
    """今日已用次数（取两个落点里较大的那份，防删文件回血）。"""
    day = _today()
    best = 0
    for p in _state_paths():
        best = max(best, _read_count(_read(p), day))
    return best


def _clean_history(raw) -> dict:
    """把文件里的 history 洗成 {YYYY-MM-DD: int}，脏数据一律丢掉。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            day = str(k)
            date.fromisoformat(day)          # 只认真正的日期串
            out[day] = max(0, int(v))
        except Exception:                    # noqa: BLE001
            continue
    return out


def _trim_history(hist: dict) -> dict:
    """只留最近 HISTORY_KEEP 天（按日期排序后切尾）。"""
    if len(hist) <= HISTORY_KEEP:
        return dict(hist)
    keep = sorted(hist.keys())[-HISTORY_KEEP:]
    return {k: hist[k] for k in keep}


def history(days: int = 7) -> list[int]:
    """最近 N 天的每日用量，**按时间正序**，最后一项是今天。

    ★ 返回长度恒等于 days：中间没用的日子补 0。
      补 0 是有意的 —— 折线要能看出"那天断掉了"，跳过空缺会让折线
      撒谎（把 0 的两天画成连续上涨）。
    ★ 两个落点合并时逐日取最大值（同 _bump 的说明，不许把数字改小）。
    """
    n = max(1, int(days))
    merged: dict[str, int] = {}
    for p in _state_paths():
        for day, cnt in _clean_history(_read(p).get(HISTORY_KEY)).items():
            merged[day] = max(merged.get(day, 0), cnt)
    # 今天这一格用 today_count() 兜底：老版本写的历史里今天可能是旧的
    today = _today()
    merged[today] = max(merged.get(today, 0), today_count())
    out = []
    for i in range(n - 1, -1, -1):
        out.append(int(merged.get(_shift(today, -i), 0)))
    return out


def _bump(n: int = 1) -> int:
    """今日计数 +n，返回增加后的总数。"""
    day = _today()
    total = today_count() + max(1, int(n))
    # 明天及以后才算「时钟被往回拨」（见 _read_count 的说明）
    future = _shift(day, 1)
    for p in _state_paths():
        d = _read(p)
        # 日期比"明天"还新 → 系统时间被往回拨过，保留那个更新的日期
        if d.get("date") and str(d.get("date")) > future:
            _write(p, dict(d))
            continue
        # ★ 免费版计数住在同一个文件的 free_count 键里，改写 count 时不能把它冲掉
        keep_free = _read_count(d, day, FREE_STATE_KEY)
        payload = {"date": day, "count": total, "updated": int(time.time())}
        if keep_free:
            payload[FREE_STATE_KEY] = keep_free
        # ★ 2026-10-27：顺手记一条按天的历史，给首页的迷你趋势线用。
        #   以前只有「今天」这一格，每天覆盖 —— 想画条 7 天的线都没数据。
        #   它是**纯附加字段**：老版本读到多余键会原样写回，不会出错；
        #   新版本读不到这个键就当没有历史（返回空列表），界面自动不画线。
        hist = _clean_history(d.get(HISTORY_KEY))
        hist[day] = total
        payload[HISTORY_KEY] = _trim_history(hist)
        _write(p, payload)
    return total


def _reset_if_needed() -> None:
    """跨天时把两份文件都重置为今天（正常路径下 today_count 已自动不读旧值）。

    ★ 2026-10-27：这里原来整份重写 {"date","count","updated"}，
      等于**跨天就把 history 清空** —— 首页的趋势线永远只有今天一个点，
      而且不会报错，看在眼里就是"线怎么画不出来"。
      现在把历史与其他附加键原样带过去，只把 count 归零。
    """
    day = _today()
    for p in _state_paths():
        d = _read(p)
        if d.get("date") != day and not (d.get("date") and str(d["date"]) > day):
            payload = dict(d)                    # 保住 history / free_count 等附加键
            payload.update({"date": day, "count": 0,
                            "updated": int(time.time())})
            _write(p, payload)


# ======================================================================
#  免费版次数（v1.14.1 起计数，v1.20.0 起不再限额）
# ======================================================================
# 为什么单独记：免费版下载走「页面解析」通道，压根不经过 client，所以
# install() 打的那个闸拦不到它。早期官网写明「每天 5 次」，所以必须记。
#
# ★ v1.20.0：免费档的下载 / 直播录制改为【不限量】。
#   这条路一分钱接口费都不花（页面解析 + 抖音自有 webcast 接口），
#   卡次数只会把愿意留下来的人赶去用别的工具，没有任何收益。
#   计数本身保留下来当诊断用：状态栏会显示「今日已用 N 次」，
#   也能让我们看清免费档到底被用得多狠。
#   注意这与 licensing.FREE_DAILY_QUOTA 是两件事 —— 那个管的是
#   【接口调用】的兜底上限，防止某条链路意外落到付费接口上被无限调用。

def free_limit() -> int:
    """免费档每日上限。-1 = 不限量（v1.20.0 起）。

    想改回限额就改这里（例如 `return 5`），界面上的提示会自己跟着变，
    不用去挨个改文案。
    """
    return UNLIMITED


def free_used_today() -> int:
    """免费版今日已用次数（同样取两个落点里较大的那份）。"""
    day = _today()
    best = 0
    for p in _state_paths():
        best = max(best, _read_count(_read(p), day, FREE_STATE_KEY))
    return best


def free_remaining() -> int:
    """免费档今日剩余次数。不限量时返回 -1（UNLIMITED），不会出现负数。"""
    lim = free_limit()
    if lim == UNLIMITED:
        return UNLIMITED
    if lim <= 0:
        return 0
    return max(0, lim - free_used_today())


def consume_free(n: int = 1) -> int:
    """免费版计数 +n，返回今日已用。

    ★ 计数失败绝不能拦住用户：写盘异常一律吞掉，只在内存里返回估算值。
      宁可少记一次，也不能让"免费版能用"这件事因为一个写权限问题失效。
    """
    try:
        used = free_used_today() + max(0, int(n))
        if used <= 0:
            return free_used_today()
        day = _today()
        future = _shift(day, 1)
        for p in _state_paths():
            d = _read(p)
            if d.get("date") != day:
                # 比"明天"还新 = 时钟被往回拨过，不动它（防刷额度）
                if d.get("date") and str(d["date"]) > future:
                    continue
                # 旧版日界 bug 可能写下"明天"的日期，这里顺手改回今天
                d = {"date": day, "count": _read_count(d, day)}
            d[FREE_STATE_KEY] = used
            d["updated"] = int(time.time())
            _write(p, d)
        return used
    except Exception:                                  # noqa: BLE001
        return 0


# ======================================================================
#  限额查询
# ======================================================================

def limit() -> int:
    """当前生效的每日上限（-1 不限，0 不可用）。来自授权码。"""
    try:
        from .licensing import daily_quota
        return int(daily_quota())
    except Exception:                                  # noqa: BLE001
        return DEFAULT_DAILY_QUOTA


def remaining() -> int:
    """今日剩余额度（不限时返回 -1；不可用返回 0）。"""
    lim = limit()
    if lim == UNLIMITED:
        return UNLIMITED
    if lim <= 0:
        return 0
    return max(0, lim - today_count())


def check(n: int = 1) -> tuple[bool, str]:
    """是否还能调用 n 次。返回 (ok, reason)。"""
    lim = limit()
    if lim == UNLIMITED:
        return True, ""
    if lim <= 0:
        return False, (
            "当前没有可用的调用额度（软件未激活或授权已过期）。"
            "请到「设置 → 授权与激活」粘贴授权码后使用。"
        )
    used = today_count()
    if used + n > lim:
        return False, (
            f"今日调用额度已用完（{lim} 次/天，已用 {used} 次），"
            "将于明天 0 点自动重置。需要更多额度请联系作者升级套餐。"
        )
    return True, ""


def consume(n: int = 1) -> int:
    """直接计 n 次（用于不走 client 的计费点）。返回今日累计。"""
    return _bump(n)


def summary() -> dict:
    """给 UI 用的一览：{limit, used, remaining, unlimited, date, history}。

    history 是最近 7 天的用量（正序，末项是今天），首页的迷你趋势线用；
    老版本没有这个键时返回空列表，界面自己决定不画线。
    """
    lim = limit()
    used = today_count()
    try:
        hist = history(7)
    except Exception:                                    # noqa: BLE001
        hist = []
    return {
        "limit": lim,
        "used": used,
        "remaining": UNLIMITED if lim == UNLIMITED else max(0, lim - used),
        "unlimited": lim == UNLIMITED,
        "date": _today(),
        "history": hist,
    }


# ======================================================================
#  给采集客户端打闸
# ======================================================================

def _denied(reason: str) -> ApiResponse:
    """构造一个「额度已用完」的失败响应，保证调用方不崩。"""
    return ApiResponse(
        success=False, code=QUOTA_EXCEEDED_CODE, message=reason,
        data=None, raw_json={"code": QUOTA_EXCEEDED_CODE, "message": reason,
                             "data": None, "_quota_exceeded": True},
    )


def install(client: Any) -> Any:
    """给 JustOneAPICollector 套一层配额闸门（幂等）。

    · 调用【前】检查额度，超限直接返回失败响应，不发请求（省钱）；
    · 调用【成功】后才计数，与 JustOneAPI「成功才计费」的规则一致；
    · 缓存命中不计数（那次根本没发请求）。

    注意：只拦 client.request / request_async，
    抖音直连的功能（喜欢/收藏抓取、下载、解析分享链接）不受影响。
    """
    if client is None or getattr(client, "_quota_installed", False):
        return client

    original_request = getattr(client, "request", None)
    original_async = getattr(client, "request_async", None)

    if callable(original_request):
        def _wrapped(platform: str, endpoint: str, params: dict | None = None):
            # ★ 按接口实际单价扣（热榜/图文搜索 0.2 元 → 扣 2 分），不再一律 +1
            cost = endpoint_cost(endpoint)
            ok, reason = check(cost)
            if not ok:
                return _denied(reason)
            resp = original_request(platform, endpoint, params)
            try:
                raw = getattr(resp, "raw_json", None) or {}
                cached = bool(raw.get("_cached"))
                if getattr(resp, "success", False) and not cached:
                    _bump(cost)
            except Exception:                          # noqa: BLE001
                pass
            return resp

        client.request = _wrapped

    if callable(original_async):
        async def _wrapped_async(platform: str, endpoint: str,
                                 params: dict | None = None):
            cost = endpoint_cost(endpoint)
            ok, reason = check(cost)
            if not ok:
                return _denied(reason)
            resp = await original_async(platform, endpoint, params)
            try:
                raw = getattr(resp, "raw_json", None) or {}
                cached = bool(raw.get("_cached"))
                if getattr(resp, "success", False) and not cached:
                    _bump(cost)
            except Exception:                          # noqa: BLE001
                pass
            return resp

        client.request_async = _wrapped_async

    client._quota_installed = True
    return client


def reset_today() -> None:
    """清零今日计数（仅作者调试用；界面上不暴露）。"""
    day = _today()
    for p in _state_paths():
        _write(p, {"date": day, "count": 0, "updated": int(time.time())})
