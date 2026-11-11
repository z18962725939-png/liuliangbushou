"""
选题侧：低粉爆款挖掘 + 评论区痛点挖掘。

为什么是这两个：
- 操盘手找选题最快的路径不是看头部大号，而是找「跟我同量级却爆了」的号，
  那才是可复制的。所以用「点赞 / 粉丝数」的爆粉比排序，而不是看绝对点赞。
- 评论区是免费的用户调研：用户在问什么、骂什么、想要什么，直接就是选题和脚本。

本模块零第三方依赖（不引 jieba），中文关键词用 n-gram + 停用词自己算，
打包体积小、也不会因为缺依赖在用户机器上崩。
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .platforms.adapter import (PLATFORM_DISPLAY, CommentItem, PlatformAdapter,
                                PostItem, normalize_platform)

# ---------------------------------------------------------------- 停用词

_STOPWORDS = set("""
的 了 是 我 你 他 她 它 们 这 那 有 在 和 就 都 而 及 与 也 很 太 更 最 不 没 无
要 会 能 对 把 被 让 给 从 到 于 之 以 所 其 吧 呢 啊 呀 哦 嗯 哈 么 一个 可以
因为 所以 但是 如果 这个 那个 什么 为什么 还是 就是 不是 真的 感觉 觉得 已经
还有 没有 自己 我们 你们 他们 一下 一直 一样 这些 那些 知道 看到 现在 时候
可以 需要 应该 不能 可能 好像 然后 最后 开始 之后 之前 里面 外面 这样 那样
谢谢 感谢 支持 关注 点赞 收藏 分享 主播 视频 博主 作者 楼 主 一层 二楼
""".split())

_PUNCT = re.compile(r"[^\u4e00-\u9fa5a-zA-Z0-9]+")
_URL = re.compile(r"(https?://\S+|www\.\S+)")
_AT = re.compile(r"@\S+")

# 评论意图关键词
_QUESTION_MARKS = ("?", "？", "吗", "呢", "怎么", "如何", "多少", "哪里", "哪个",
                   "求", "请问", "能不能", "可以吗", "是不是", "有没有", "为啥")
_COMPLAINT_MARKS = ("差", "烂", "坑", "退", "垃圾", "不值", "骗", "坏", "没用",
                    "失望", "后悔", "避雷", "别买", "踩雷", "投诉", "假货",
                    "智商税", "难用", "翻车", "劝退", "虚假", "割韭菜")
_BUY_MARKS = ("多少钱", "哪里买", "求链接", "想要", "求推荐", "买哪个", "下单",
              "入手", "链接", "求购", "怎么买", "价格", "哪里可以买", "有链接吗",
              "求个", "团购", "优惠")


@dataclass
class RankedPost:
    """一条被判定为「以小博大」的作品。"""
    post: PostItem
    ratio: float = 0.0        # 点赞 / 粉丝数，越大越说明内容力强

    @property
    def ratio_text(self) -> str:
        if self.ratio >= 10:
            return f"{self.ratio:.0f}倍"
        return f"{self.ratio:.1f}倍"

    def row(self) -> list:
        r = self.post.as_row()
        return [self.ratio_text] + r


@dataclass
class CommentInsight:
    total: int = 0
    keywords: list[tuple[str, int]] = field(default_factory=list)
    questions: list[CommentItem] = field(default_factory=list)
    complaints: list[CommentItem] = field(default_factory=list)
    buyers: list[CommentItem] = field(default_factory=list)
    top_liked: list[CommentItem] = field(default_factory=list)

    def summary(self) -> str:
        return (f"共 {self.total} 条评论　"
                f"疑问 {len(self.questions)}　抱怨 {len(self.complaints)}　"
                f"购买意向 {len(self.buyers)}")


# ---------------------------------------------------------------- 爆款挖掘

def mine_low_fan_viral(
    client,
    platform: str,
    keyword: str,
    max_fans: int = 100_000,
    min_digg: int = 10_000,
    pages: int = 2,
    count: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> list[RankedPost]:
    """
    按关键词搜索作品，挑出「作者粉丝不多但点赞很高」的以小博大爆款。

    Args:
        max_fans: 作者粉丝上限（超过的算大号，参考价值低）
        min_digg: 点赞下限（太低不算爆）
        pages:   翻几页（页数越多越费额度）

    Returns 按爆粉比（点赞/粉丝）降序排列的结果。
    """
    ad = PlatformAdapter(client, platform)
    out: list[RankedPost] = []
    seen: set[str] = set()

    for page in range(1, max(1, pages) + 1):
        if on_progress:
            on_progress(f"{ad.display} 第 {page}/{pages} 页搜索「{keyword}」…")
        posts = ad.search_posts(keyword, page=page, count=count)
        if not posts:
            if ad.last_error and on_progress:
                on_progress(f"　{ad.last_error}")
            break
        for p in posts:
            if p.post_id in seen:
                continue
            seen.add(p.post_id)
            if p.digg < min_digg:
                continue
            # 粉丝数未知时无法判断量级，跳过而不是误判为大号
            if p.author_fans <= 0 or p.author_fans > max_fans:
                continue
            ratio = p.digg / max(p.author_fans, 1)
            out.append(RankedPost(post=p, ratio=ratio))

    out.sort(key=lambda r: r.ratio, reverse=True)
    if on_progress:
        on_progress(f"「{keyword}」筛出 {len(out)} 条以小博大作品")
    return out


def mine_keywords_batch(
    client,
    platform: str,
    keywords: list[str],
    max_fans: int = 100_000,
    min_digg: int = 10_000,
    pages: int = 2,
    workers: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> list[RankedPost]:
    """多个关键词并发挖掘，结果汇总去重后按爆粉比排序。"""
    results: list[RankedPost] = []
    seen: set[str] = set()

    def one(kw: str) -> list[RankedPost]:
        return mine_low_fan_viral(client, platform, kw, max_fans=max_fans,
                                  min_digg=min_digg, pages=pages,
                                  on_progress=on_progress)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for lst in ex.map(one, keywords):
            for r in lst:
                if r.post.post_id in seen:
                    continue
                seen.add(r.post.post_id)
                results.append(r)

    results.sort(key=lambda r: r.ratio, reverse=True)
    return results


# ---------------------------------------------------------------- 评论挖掘

def fetch_comments(
    client,
    platform: str,
    post_ids: list[str],
    max_each: int = 50,
    workers: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> list[CommentItem]:
    """批量拉取多个作品的评论（含翻页）。"""
    ad = PlatformAdapter(client, platform)
    out: list[CommentItem] = []

    def one(pid: str) -> list[CommentItem]:
        got: list[CommentItem] = []
        cursor = ""
        for _ in range(max(1, max_each // 20 + 1)):
            comments, nxt, more = ad.post_comments(pid, cursor=cursor, count=20)
            if not comments:
                break
            got.extend(comments)
            if not more or not nxt or len(got) >= max_each:
                break
            cursor = nxt
        return got[:max_each]

    def task(pid):
        res = one(pid)
        if on_progress:
            on_progress(f"已拉取作品 {pid} 的评论 {len(res)} 条")
        return res

    seen: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for lst in ex.map(task, post_ids):
            for c in lst:
                # 翻页时接口偶尔会重复吐同一条，按 (作品, 评论) 去重
                key = (c.post_id, c.comment_id or c.text[:40])
                if key in seen:
                    continue
                seen.add(key)
                out.append(c)

    out.sort(key=lambda c: c.digg, reverse=True)
    return out


# ---------------------------------------------------------------- 评论分析

def _clean_text(text: str) -> str:
    t = _URL.sub(" ", text or "")
    t = _AT.sub(" ", t)
    return t.strip()


def extract_keywords(texts: list[str], top_n: int = 30,
                     min_len: int = 2, max_len: int = 4) -> list[tuple[str, int]]:
    """轻量中文关键词提取：n-gram + 停用词过滤 + 子串合并，不依赖分词库。"""
    counter: Counter = Counter()
    for raw in texts:
        t = _clean_text(raw)
        for seg in _PUNCT.split(t):
            seg = seg.strip()
            if len(seg) < min_len:
                continue
            for n in range(min_len, max_len + 1):
                for i in range(len(seg) - n + 1):
                    gram = seg[i:i + n]
                    if not gram or gram in _STOPWORDS:
                        continue
                    # 含停用单字的片段基本无意义
                    if any(ch in _STOPWORDS for ch in gram):
                        continue
                    if gram.isdigit():
                        continue
                    counter[gram] += 1

    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    picked: list[tuple[str, int]] = []
    for word, freq in ranked:
        if freq < 2:
            continue
        # 已被更长的高频词包含，就跳过（"护肤" 被 "护肤品" 吃掉）
        if any(word in w and abs(f - freq) <= max(1, freq // 2)
               for w, f in picked):
            continue
        picked.append((word, freq))
        if len(picked) >= top_n:
            break
    return picked


def analyze_comments(comments: list[CommentItem],
                     top_n: int = 30) -> CommentInsight:
    """把一堆评论提炼成可执行的选题素材。"""
    texts = [c.text for c in comments if c.text]
    insight = CommentInsight(total=len(comments))
    insight.keywords = extract_keywords(texts, top_n=top_n)

    for c in comments:
        t = c.text or ""
        if any(k in t for k in _QUESTION_MARKS):
            insight.questions.append(c)
        if any(k in t for k in _COMPLAINT_MARKS):
            insight.complaints.append(c)
        if any(k in t for k in _BUY_MARKS):
            insight.buyers.append(c)

    insight.questions.sort(key=lambda c: c.digg, reverse=True)
    insight.complaints.sort(key=lambda c: c.digg, reverse=True)
    insight.buyers.sort(key=lambda c: c.digg, reverse=True)
    insight.top_liked = sorted(comments, key=lambda c: c.digg, reverse=True)[:20]
    return insight


def insight_to_rows(insight: CommentInsight) -> list[list]:
    """把分析结果摊平成可导出的表格行。"""
    rows: list[list] = []
    for c in insight.top_liked:
        kind = []
        if c in insight.questions:
            kind.append("疑问")
        if c in insight.complaints:
            kind.append("抱怨")
        if c in insight.buyers:
            kind.append("购买意向")
        rows.append([
            PLATFORM_DISPLAY.get(c.platform, c.platform),
            c.user_name,
            c.text[:120],
            c.digg,
            c.reply_count,
            "/".join(kind) or "其他",
        ])
    return rows


COMMENT_EXPORT_COLUMNS = ["平台", "用户", "评论内容", "点赞", "回复数", "意图分类"]
RANKED_POST_COLUMNS = ["爆粉比"] + [
    "平台", "标题", "作者", "作者粉丝", "点赞", "评论", "收藏", "分享",
    "发布时间", "时长", "链接",
]
