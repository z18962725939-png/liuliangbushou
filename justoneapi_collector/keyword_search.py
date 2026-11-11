# -*- coding: utf-8 -*-
"""关键词视频抓取：搜一个词，把这个词下数据最好的相关视频尽量抓全。

═══════════════════════════════════════════════════════════════════════════
为什么单独做这个，而不是继续用「选题挖掘」
═══════════════════════════════════════════════════════════════════════════
客户反馈：搜一个关键词只能用「选题挖掘」，但**搜出来的视频太少了**。
原因有三层，都实测过（2026-10-11，关键词「减肥」）：

1. 接口单次返回就少：抖音 video-search-v4 传 count=20 也只回 **11 条**
   有效视频（其余是关联词卡片等非视频内容）。
2. 选题挖掘还会二次过滤：它只留「点赞 ≥ 1万 且 粉丝 ≤ 上限」的以小博大作品，
   绝大多数结果在这一步就被丢掉了 —— 那是它的定位，不是 bug。
3. 想要"数据最好的"就得换排序：实测 sort_type=1（最多点赞）+ publish_time=180
   能翻出 474 万赞那种头部爆款，而默认综合排序里根本没有。

所以本模块的策略是**多角度组合抓取 + 全局去重**：

   排序（综合 / 最多点赞 / 最新发布） × 时间范围（不限 / 一周 / 半年） × 翻页
   → 发 N 次请求 → 按作品 ID 去重 → 按点赞降序 → 交回界面

实测「减肥」：单页 11 条 → 三排序×三时间桶共 9 次请求可到 60~90 条去重结果。

额度提示：每发一次请求算一次额度（成功才计费）。深度模式约 12~18 次，
标准版客户（30 次/天）一天只能跑一两次深度，界面上会把预计消耗写在按钮旁。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .platforms.adapter import PLATFORM_DISPLAY, PlatformAdapter, PostItem

# ---------------------------------------------------------------- 选项

# 排序：抖音 video-search-v4 的 sort_type（0 综合 / 1 最多点赞 / 2 最新发布）
SORT_OPTIONS: dict[str, int | None] = {
    "数据最好（点赞最多）": 1,
    "综合排序": 0,
    "最新发布": 2,
}

# 时间范围：抖音 publish_time（0 不限 / 7 一周 / 180 半年）
TIME_OPTIONS: dict[str, int | None] = {
    "不限时间": 0,
    "近 7 天": 7,
    "近半年": 180,
}

# 抓取深度 → (每个维度翻几页, 是否交叉组合排序×时间)
# 标签写成区间：抖音走「排序×时间」组合（9/18 次），小红书/快手只能纯翻页
# （6/10 次），同一个档位在不同平台请求数不同。界面上的「预计消耗」由
# build_plan 实时算出来，不靠标签，所以标签只需诚实给个区间。
DEPTH_PLANS: dict[str, tuple[int, bool]] = {
    "快速（2~3 次请求）": (2, False),
    "标准（6~9 次请求）": (1, True),
    "深度（10~18 次请求）": (2, True),
}

# 非抖音平台（不支持排序/时间参数）每档翻几页
_PAGES_NO_FILTER = {"快速（2~3 次请求）": 2, "标准（6~9 次请求）": 6,
                    "深度（10~18 次请求）": 10}

# 导出表头（首列是关键词，支持一次跑多个词）
KEYWORD_COLUMNS = ["关键词", "平台", "标题", "作者", "粉丝数", "点赞", "评论",
                   "收藏", "分享", "播放", "发布时间", "时长", "链接"]


@dataclass
class SearchResult:
    """一次关键词抓取的结果。"""
    keyword: str = ""
    posts: list[PostItem] = field(default_factory=list)
    requests_used: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.posts)

    def row(self, post: PostItem) -> list:
        """把一条作品拼成表格行（与 KEYWORD_COLUMNS 一一对应）。

        这里刻意不用 PostItem.as_row()：那个方法不带「播放量」，而"数据最好的
        视频"这个需求里播放数是核心指标之一，必须单独列出来。
        """
        return [
            self.keyword,
            PLATFORM_DISPLAY.get(post.platform, post.platform),
            post.title[:60],
            post.author_name,
            post.author_fans,
            post.digg,
            post.comment,
            post.collect,
            post.share,
            post.play,
            post.publish_text,
            post.duration_text,
            post.url,
        ]


def build_plan(sort_label: str, time_label: str, depth_label: str,
               platform: str = "douyin") -> list[tuple[int | None, int | None, int]]:
    """生成请求计划：[(sort_type, publish_time, page), …]。

    只有抖音同时支持排序与时间范围（其它平台接口未开放），所以非抖音平台
    退化成"纯翻页"，避免传了不支持的参数导致整批失败。
    """
    pages, cross = DEPTH_PLANS.get(depth_label, DEPTH_PLANS["标准（6~9 次请求）"])
    douyin = str(platform).startswith("douyin") or str(platform) == "douyin-tiktok-china"
    if not douyin:
        n = _PAGES_NO_FILTER.get(depth_label, 2)
        return [(None, None, p) for p in range(1, max(1, n) + 1)]

    if cross:
        sorts = [SORT_OPTIONS[sort_label]] + [v for k, v in SORT_OPTIONS.items()
                                              if k != sort_label]
        times = [TIME_OPTIONS[time_label]] + [v for k, v in TIME_OPTIONS.items()
                                              if k != time_label]
        plan: list[tuple[int | None, int | None, int]] = []
        for s in sorts[:3]:
            for t in times[:3]:
                for pg in range(1, pages + 1):
                    plan.append((s, t, pg))
        return plan

    # 快速模式：用用户选的排序，只翻 pages 页
    s = SORT_OPTIONS.get(sort_label, 1)
    t = TIME_OPTIONS.get(time_label, 0)
    return [(s, t, p) for p in range(1, pages + 1)]


def search_keyword(
    client,
    platform: str,
    keyword: str,
    sort_label: str = "数据最好（点赞最多）",
    time_label: str = "不限时间",
    depth_label: str = "标准（约 6 次请求）",
    min_digg: int = 0,
    max_fans: int = 0,
    count: int = 20,
    on_progress: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SearchResult:
    """抓一个关键词下的相关视频，按点赞降序返回。

    Args:
        min_digg: 点赞下限（0 = 不过滤）
        max_fans: 作者粉丝上限（0 = 不限）。只有接口真的返回了粉丝数才参与
                  过滤 —— 小红书/快手搜索结果里粉丝数是 0（接口不返回），
                  若照搬选题挖掘的"fans<=0 就丢弃"会把全部结果误杀。
    """
    ad = PlatformAdapter(client, platform)
    result = SearchResult(keyword=keyword)
    seen: dict[str, PostItem] = {}
    plan = build_plan(sort_label, time_label, depth_label, platform)

    def say(text: str):
        if on_progress:
            try:
                on_progress(text)
            except Exception:                              # noqa: BLE001
                pass

    say(f"「{keyword}」共 {len(plan)} 次请求，开始抓取…")
    for idx, (s, t, page) in enumerate(plan, 1):
        if should_stop and should_stop():
            say("已中止")
            break
        label = f"{_sort_name(s)}/{_time_name(t)}/第{page}页"
        say(f"[{idx}/{len(plan)}] {label} …")
        try:
            posts = ad.search_posts(keyword, page=page, count=count,
                                    sort=s, publish_time=t)
        except Exception as e:                             # noqa: BLE001
            result.errors.append(f"{label} 异常：{e}")
            continue
        result.requests_used += 1
        if not posts:
            if ad.last_error:
                result.errors.append(f"{label}：{ad.last_error}")
            continue
        new_cnt = 0
        for p in posts:
            if not p.post_id or p.post_id in seen:
                continue
            if min_digg and p.digg < min_digg:
                continue
            if max_fans and p.author_fans > max_fans:      # 仅在有粉丝数据时过滤
                continue
            seen[p.post_id] = p
            new_cnt += 1
        say(f"　{label} 新增 {new_cnt} 条，累计 {len(seen)} 条")

    result.posts = sorted(seen.values(), key=lambda p: p.digg, reverse=True)
    say(f"「{keyword}」完成：{len(result.posts)} 条（去重后），共请求 {result.requests_used} 次")
    if not result.posts and result.errors:
        say(f"　未取到数据，首次错误：{result.errors[0]}")
    return result


def search_keywords(
    client,
    platform: str,
    keywords: list[str],
    sort_label: str = "数据最好（点赞最多）",
    time_label: str = "不限时间",
    depth_label: str = "标准（约 6 次请求）",
    min_digg: int = 0,
    max_fans: int = 0,
    count: int = 20,
    on_progress: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_keyword: Callable[[int, int, str], None] | None = None,
) -> list[SearchResult]:
    """多个关键词依次抓取（串行：省额度，也便于中途停止）。

    Args:
        on_keyword: 每开始一个关键词时回调 (第几个从1开始, 共几个, 关键词)，
                    界面用它显示「正在抓第 2/3 个关键词」并推进进度条。
    """
    out: list[SearchResult] = []
    total = len(keywords)
    for idx, kw in enumerate(keywords, 1):
        if should_stop and should_stop():
            break
        if on_keyword:
            try:
                on_keyword(idx, total, kw)
            except Exception:                              # noqa: BLE001
                pass
        out.append(search_keyword(client, platform, kw, sort_label=sort_label,
                                  time_label=time_label, depth_label=depth_label,
                                  min_digg=min_digg, max_fans=max_fans, count=count,
                                  on_progress=on_progress, should_stop=should_stop))
        if on_progress:
            on_progress("")
    return out


def rows_from_results(results: list[SearchResult]) -> list[list]:
    """把多个关键词的结果拼成表格行（与 KEYWORD_COLUMNS 对齐）。"""
    rows: list[list] = []
    for r in results:
        for p in r.posts:
            rows.append(r.row(p))
    return rows


def _sort_name(v: int | None) -> str:
    for k, val in SORT_OPTIONS.items():
        if val == v:
            return k
    return "默认排序"


def _time_name(v: int | None) -> str:
    for k, val in TIME_OPTIONS.items():
        if val == v:
            return k
    return "不限时间"
