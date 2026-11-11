# -*- coding: utf-8 -*-
"""GEO：AI 可见度监测（品牌在 AI 回答里的提及 / 引用 / 竞品对比）。

═══════════════════════════════════════════════════════════════════════════
这个模块在测什么
═══════════════════════════════════════════════════════════════════════════
把一批「客户真实会问 AI 的问题」丢给若干 AI 引擎，把回答抓回来，统计：

  · 提及率      —— 品牌在多少比例的回答里出现了
  · 首次位次    —— 第一次被提到时位于回答的什么位置（越靠前越值钱）
  · 引用信源    —— AI 参考了哪些站点（知乎？小红书？百家号？）
  · 竞品共现    —— 同一问题下竞品被提到的比例，谁压着谁
  · 情感倾向    —— 提到时是推荐还是吐槽

═══════════════════════════════════════════════════════════════════════════
必须写进报告的三个口径（别让客户误读，否则后期必扯皮）
═══════════════════════════════════════════════════════════════════════════
  1. **AI 回答有随机性**：同一个问题不同会话答案不同。所以本模块按「采样
     次数」多次提问取比例，绝不拿单次结果下结论（samples 默认 3）。
  2. **普通模型接口不带联网搜索**：走官方 API 时，模型回答的是训练数据里
     的认知，跟用户在 App 里开「联网搜索」看到的答案可能不同。报告里必须
     写明这一条，否则客户拿 App 一比对就说你数据不准。
  3. **见效以月为单位**：模型收录新内容需要时间，报告只承诺「可见度变化
     趋势」，不承诺排名、更不承诺「保证被推荐」。

═══════════════════════════════════════════════════════════════════════════
为什么引擎分两类
═══════════════════════════════════════════════════════════════════════════
  · api —— 官方接口（DeepSeek / Kimi），稳定、便宜、可并发，首选
  · cdp —— 浏览器自动化（豆包 / 元宝无公开接口），能拿到网页版真实的
            联网回答与引用卡片，但依赖登录态、网页一改就要维护
本模块把两类抽象成同一个 ask()，引擎表 ENGINE_SPECS 里加一行即可扩展。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

# ----------------------------------------------------------------------
#  引擎表
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class EngineSpec:
    """一个 AI 引擎的接入参数。"""
    key: str                 # 内部标识
    label: str               # 界面显示名
    kind: str                # "api" | "cdp"
    base_url: str = ""       # api 型：OpenAI 兼容根地址（含 /v1）
    model: str = ""          # api 型：模型名
    note: str = ""           # 给用户的说明（怎么拿 key 等）
    temperature: float = 0.6  # api 型：采样温度（各家限制不同，见下）
    concurrency: int = 4     # 该引擎最多同时打几个请求（受各家速率限制约束）


# ⚠️⚠️ 模型名与 temperature 是这套功能最容易失效的地方，改之前先看这里 ⚠️⚠️
# 2026-10-12 用真实 Key 实测（tools/_probe_geo_models.py 可复跑）：
#
#   DeepSeek  /models 返回：deepseek-flash、deepseek-v4-pro
#     · deepseek-chat 不在列表里但**实测可用**（1.6~2.0s，最快），是长期兼容别名
#     · temperature 0.6 / 1.0 都接受
#   Kimi      /models 返回：kimi-k2.6、kimi-k2.7-code
#     · kimi-k3 根本不存在（曾据过时资料误填，会导致 100% 调用失败）
#     · kimi-k2.7-code 是代码模型且被限流，不适合做问答监测
#     · **只接受 temperature=1.0**，传 0.6 直接 400：
#       invalid temperature: only 1 is allowed for this model
#     · 账号有组织级限流，并发猛了会 429
#
# 光靠写死默认值扛不住下一次改名，所以代码里还留了两道保险：
#   1) 界面「模型名 / 温度」输入框可临时覆盖，不用等发版
#   2) ask_engine() 遇到 temperature 不合法会自动降级重试，遇到 429 会退避重试
ENGINE_SPECS: dict[str, EngineSpec] = {
    "deepseek": EngineSpec(
        key="deepseek", label="DeepSeek", kind="api",
        base_url="https://api.deepseek.com/v1", model="deepseek-chat",
        note="platform.deepseek.com 注册即得，成本最低、响应最快，建议优先用",
        temperature=0.6,
    ),
    "kimi": EngineSpec(
        key="kimi", label="Kimi", kind="api",
        base_url="https://api.moonshot.cn/v1", model="kimi-k2.6",
        note="platform.moonshot.cn 注册即得；现行主力 kimi-k2.6，仅支持温度 1.0；"
             "免费额度限流很紧，建议充值或降低采样次数",
        temperature=1.0,
        # 实测（2026-10-12）：免费额度有组织级速率限制，并发打过去会成片 429。
        # 串行 + 退避重试仍可能失败，但失败会被明确统计成「失败」而不是「未提及」。
        concurrency=1,
    ),
    "doubao": EngineSpec(
        key="doubao", label="豆包", kind="cdp",
        note="暂无公开接口，走浏览器自动化；需先在本机登录豆包网页版",
    ),
}

# 让 AI 稳定作答的 system 提示：不联网、只说知道的、要点名具体品牌
_SYSTEM_PROMPT = (
    "你是资深行业顾问。用户会问你一个选型/推荐类问题。请直接给出具体品牌或产品名单，"
    "不要只给抽象方法论。只依据你已有的知识回答，不知道就说不知道，不要编造。"
    "回答控制在 400 字以内，用中文。"
)

_URL_RE = re.compile(r"https?://[^\s，。；）)\"'）】]+", re.IGNORECASE)

# 域名 -> 平台中文名（AI 引用来源里最常见的那几家）
_DOMAIN_PLATFORM = (
    ("zhihu.com", "知乎"), ("xiaohongshu.com", "小红书"), ("xhslink.com", "小红书"),
    ("baijiahao.baidu.com", "百家号"), ("baidu.com", "百度"), ("sohu.com", "搜狐"),
    ("163.com", "网易"), ("sina.com.cn", "新浪"), ("toutiao.com", "今日头条"),
    ("mp.weixin.qq.com", "微信公众号"), ("weixin.qq.com", "微信"),
    ("bilibili.com", "B站"), ("douyin.com", "抖音"), ("qq.com", "腾讯网"),
    ("csdn.net", "CSDN"), ("jianshu.com", "简书"), ("36kr.com", "36氪"),
    ("ithome.com", "IT之家"), ("tieba.baidu.com", "百度贴吧"),
    ("zhidao.baidu.com", "百度知道"), ("jiemian.com", "界面新闻"),
    ("people.com.cn", "人民网"), ("xinhuanet.com", "新华网"), ("gov.cn", "政府网站"),
    ("wikipedia.org", "维基百科"), ("zh.wikipedia.org", "维基百科"),
)

_POSITIVE_WORDS = ("推荐", "靠谱", "领先", "优质", "不错", "专业", "值得", "口碑好",
                   "性价比高", "首选", "老牌", "知名", "放心", "好评")
_NEGATIVE_WORDS = ("不推荐", "不建议", "差评", "投诉", "虚假", "智商税", "踩坑",
                   "避雷", "质量差", "翻车", "不靠谱", "贵得离谱")


# ----------------------------------------------------------------------
#  数据结构
# ----------------------------------------------------------------------

@dataclass
class AskResult:
    """一次提问的原始结果。"""
    engine: str = ""
    query: str = ""
    ok: bool = False
    text: str = ""
    sources: list[str] = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0


@dataclass
class QueryReport:
    """一个问题在一个引擎上的汇总（多次采样后）。"""
    query: str
    engine: str
    samples: int = 0                       # 计划采样次数
    valid: int = 0                         # 成功拿到回答的次数
    mentions: int = 0                      # 提到品牌的次数
    first_pos: float = -1.0                # 首次提及位置的平均值（0~1，越小越靠前）
    competitors: dict[str, int] = field(default_factory=dict)   # 竞品 -> 被提到次数
    sources: list[str] = field(default_factory=list)            # 引用到的域名
    sentiment: str = "中性"                 # 正面 / 中性 / 负面
    answer_sample: str = ""                # 一条样本文本（给客户看证据）
    error: str = ""

    @property
    def mention_rate(self) -> float:
        """提及率 = 提到次数 ÷ 有效回答数。"""
        return (self.mentions / self.valid) if self.valid else 0.0

    @property
    def failed(self) -> bool:
        """一次有效回答都没拿到（限流 / Key 错 / 网络问题）。

        这跟「问到了但没提到品牌」是两回事，绝对不能混为一谈：
        混了的话，接口挂掉会显示成「AI 不知道这个品牌」，客户据此
        以为自己不行而加大投入 —— 报告就成了误导。
        """
        return self.valid == 0

    def to_row(self) -> dict:
        if self.failed:
            return {
                "提问词": self.query,
                "引擎": ENGINE_SPECS.get(self.engine, EngineSpec(self.engine, self.engine, "")).label,
                "状态": "失败",
                "采样次数": f"0/{self.samples}",
                "提及次数": "-",
                "提及率": "-",
                "首次位次": "-",
                "竞品提及": "-",
                "情感": "-",
                "引用信源": "-",
                "回答样本": "【本次提问失败，不能当作“未提及”】" + (self.error or "未知原因"),
            }
        return {
            "提问词": self.query,
            "引擎": ENGINE_SPECS.get(self.engine, EngineSpec(self.engine, self.engine, "")).label,
            "状态": "成功",
            "采样次数": f"{self.valid}/{self.samples}",
            "提及次数": self.mentions,
            "提及率": f"{self.mention_rate * 100:.0f}%",
            "首次位次": f"{self.first_pos * 100:.0f}%" if self.first_pos >= 0 else "-",
            "竞品提及": "、".join(f"{k}×{v}" for k, v in self.competitors.items() if v) or "-",
            "情感": self.sentiment,
            "引用信源": "、".join(self.sources[:5]) or "-",
            "回答样本": self.answer_sample[:200],
        }


@dataclass
class ScanReport:
    """一轮体检的完整结果。"""
    brand: str = ""
    aliases: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    samples: int = 1
    started_at: str = ""
    items: list[QueryReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ---------------- 汇总指标 ----------------
    @property
    def total_answers(self) -> int:
        return sum(i.valid for i in self.items)

    @property
    def total_mentions(self) -> int:
        return sum(i.mentions for i in self.items)

    @property
    def mention_rate(self) -> float:
        """整体提及率。"""
        return (self.total_mentions / self.total_answers) if self.total_answers else 0.0

    @property
    def failed_items(self) -> list[QueryReport]:
        """一次有效回答都没拿到的条目（限流 / Key 错 / 网络问题）。"""
        return [i for i in self.items if i.failed]

    @property
    def failed_count(self) -> int:
        return len(self.failed_items)

    def by_engine(self) -> dict[str, float]:
        """各引擎的提及率。"""
        out: dict[str, list[int]] = {}
        for i in self.items:
            a, b = out.setdefault(i.engine, [0, 0])
            out[i.engine] = [a + i.mentions, b + i.valid]
        return {k: (v[0] / v[1] if v[1] else 0.0) for k, v in out.items()}

    def source_ranking(self) -> list[tuple[str, int]]:
        """引用信源排名：[(平台/域名, 次数)]，从高到低。"""
        counter: dict[str, int] = {}
        for i in self.items:
            for s in i.sources:
                counter[s] = counter.get(s, 0) + 1
        return sorted(counter.items(), key=lambda kv: kv[1], reverse=True)

    def competitor_ranking(self) -> list[tuple[str, int, float]]:
        """竞品对比：[(竞品, 被提次数, 提及率)]。"""
        out = []
        for c in self.competitors:
            hits = sum(i.competitors.get(c, 0) for i in self.items)
            total = self.total_answers
            out.append((c, hits, (hits / total) if total else 0.0))
        return sorted(out, key=lambda t: t[1], reverse=True)

    def suggestions(self) -> list[str]:
        """可执行优化清单（规则化生成，不经过 AI，保证可复现）。"""
        tips: list[str] = []
        rate = self.mention_rate
        if self.total_answers == 0:
            return ["本次没有拿到任何有效回答，先检查接口 Key 与网络。",
                    "若全部是「调用太频繁被限流」，说明该 Key 是免费额度，"
                    "速率限制很紧 —— 充值或换用其它引擎后再跑。"]

        # 失败数必须排在前面：拿不全的数据去指导下决策，比不做诊断更糟
        if self.failed_count:
            tips.append(
                f"【⚠ 数据不全】有 {self.failed_count} 个提问没拿到回答（多为限流 / Key 问题），"
                f"本次结论只基于成功的 {self.total_answers} 个回答。"
                "失败的提问**不能算作「AI 没提到你」**，请排除故障后重跑再对比，"
                "否则提及率会被人为拉低。")

        if rate == 0:
            tips.append(
                f"【最紧急】{len(self.queries)} 个提问词里 AI 一次都没提到「{self.brand}」，"
                "说明模型知识里几乎没有你的品牌信息。优先补「能被 AI 采信的公开内容」，"
                "而不是继续发广告贴。")
        elif rate < 0.2:
            tips.append(
                f"【薄弱】整体提及率仅 {rate * 100:.0f}%，AI 对你的认知很浅。"
                "把资源集中投在下面排名最高的 2~3 个平台上，比广撒网有效。")
        elif rate < 0.5:
            tips.append(
                f"【有基础】提及率 {rate * 100:.0f}%，已有存在感。下一步是把它推到"
                "「被优先推荐」：补充对比类、选型类内容（AI 最爱引用这类）。")
        else:
            tips.append(
                f"【健康】提及率 {rate * 100:.0f}%。重点转为防守：每月复测，"
                "盯住竞品反超和负面表述。")

        top = self.source_ranking()[:3]
        if top:
            names = "、".join(f"{n}（{c} 次）" for n, c in top)
            tips.append(f"【补内容】AI 现在最常引用这些地方：{names}。"
                        "去这几个平台做问答型 / 测评型内容，最容易被进一步采信。")
        else:
            tips.append("【补内容】本轮没抓到引用来源，说明回答里没给链接。"
                        "可以换用带联网搜索的通道复核一次。")

        mine = rate
        for name, _hits, cr in self.competitor_ranking()[:3]:
            if cr > mine + 0.05:
                tips.append(f"【竞品压制】「{name}」提及率 {cr * 100:.0f}%，"
                            f"高于你的 {mine * 100:.0f}%。去搜它被引用的内容长什么样，"
                            "照着结构补一篇你自己的。")

        if any(i.sentiment == "负面" for i in self.items):
            tips.append("【舆情预警】有回答在负面语境里提到你，先把这些原文找出来核对，"
                        "确认是不是错误信息；确认后优先发正面权威内容覆盖。")

        tips.append("【节奏】AI 收录内容以月为单位，建议每月固定一天复测，"
                    "只比趋势，不比单次数字。")
        return tips

    def to_dict(self) -> dict:
        return {
            "brand": self.brand, "aliases": self.aliases,
            "competitors": self.competitors, "engines": self.engines,
            "queries": self.queries, "samples": self.samples,
            "started_at": self.started_at,
            "mention_rate": round(self.mention_rate, 4),
            "total_answers": self.total_answers,
            "total_mentions": self.total_mentions,
            "failed_count": self.failed_count,
            "by_engine": {k: round(v, 4) for k, v in self.by_engine().items()},
            "sources": self.source_ranking(),
            "competitor_rank": [(n, h, round(r, 4)) for n, h, r in self.competitor_ranking()],
            "suggestions": self.suggestions(),
            "items": [
                {"query": i.query, "engine": i.engine, "valid": i.valid,
                 "mentions": i.mentions, "rate": round(i.mention_rate, 4),
                 "first_pos": round(i.first_pos, 4), "competitors": i.competitors,
                 "sources": i.sources, "sentiment": i.sentiment}
                for i in self.items
            ],
            "errors": self.errors,
        }


# ----------------------------------------------------------------------
#  文本处理
# ----------------------------------------------------------------------

def brand_variants(brand: str, aliases: list[str] | None = None) -> list[str]:
    """品牌名 -> 待匹配的全部写法。

    为什么需要：AI 可能写「斯堪尼亚」也可能写「Scania」，客户填的品牌名往往
    只是其中一种。漏了变体 = 明明被提到了却统计成没提到，报告直接失真。
    """
    out: list[str] = []
    for raw in [brand] + list(aliases or []):
        s = (raw or "").strip()
        if not s:
            continue
        cand = {s, s.lower(), s.replace(" ", ""), s.replace("　", "")}
        # 去掉「有限公司 / 股份有限公司」等后缀，只留字号
        core = re.sub(r"(有限|股份|责任|公司|集团|科技|实业).*$", "", s).strip()
        if len(core) >= 2:
            cand.add(core)
        for c in cand:
            if c and c not in out:
                out.append(c)
    return out


# 平台中文名（长名优先匹配，避免「百度」先命中而漏掉「百度贴吧」）
_PLATFORM_NAMES = tuple(sorted({cn for _d, cn in _DOMAIN_PLATFORM}, key=len, reverse=True))


def extract_sources(text: str) -> list[str]:
    """从回答里抽引用来源，转成「平台名 / 域名」，去重保序。

    两条路子都走，因为只认 URL 会几乎一无所获：
      1) 回答里带的链接 → 取域名 → 映射平台中文名
      2) 回答里直接提到的平台名（「知乎上讨论很多」这种）
    注意：官方接口通道不带联网搜索，模型**不会**给引用链接，所以主要靠第 2 条。
    因此本表只作「内容投放方向参考」，不能当成严格的引用溯源证据。
    """
    out: list[str] = []
    body = text or ""
    for url in _URL_RE.findall(body):
        url = url.rstrip("。，、；)）】")
        host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower()
        if not host or "." not in host:
            continue
        name = next((cn for d, cn in _DOMAIN_PLATFORM if host.endswith(d)), host)
        if name not in out:
            out.append(name)
    for name in _PLATFORM_NAMES:
        if name in body and name not in out:
            out.append(name)
    return out


def detect_sentiment(text: str, variants: list[str]) -> str:
    """判断品牌被提到时的情感倾向：看提及位置附近有没有正/负面词。"""
    low = (text or "").lower()
    window = ""
    for v in variants:
        pos = low.find(v.lower())
        if pos >= 0:
            window += text[max(0, pos - 40): pos + 60]
    if not window:
        return "中性"
    pos = sum(window.count(w) for w in _POSITIVE_WORDS)
    neg = sum(window.count(w) for w in _NEGATIVE_WORDS)
    if neg > pos:
        return "负面"
    if pos > neg:
        return "正面"
    return "中性"


def first_position(text: str, variants: list[str]) -> float:
    """品牌第一次出现的位置，归一化到 0~1（越小越靠前）。没出现返回 -1。"""
    low = (text or "").lower()
    total = max(len(low), 1)
    best = -1
    for v in variants:
        p = low.find(v.lower())
        if p >= 0 and (best < 0 or p < best):
            best = p
    return (best / total) if best >= 0 else -1.0


# ----------------------------------------------------------------------
#  提问：API / CDP
# ----------------------------------------------------------------------

def _post_json(url: str, body: dict, api_key: str, timeout: float = 60.0,
               retries: int = 3) -> tuple[bool, str]:
    """OpenAI 兼容的 /chat/completions 调用。失败返回 (False, 原因)。

    retries —— 对「限流(429) / 服务端错误(5xx) / 网络抖动」自动退避重试。
    为什么必须有：Kimi 免费额度有**组织级速率限制**，2026-10-12 实测即使
    完全串行提问也有 2/5 被 429。不重试就会静默丢掉一批回答，客户看到的
    提及率因此偏低却不知道为什么 —— 那报告就是错的。

    退避取 3s / 6s / 12s：实测 1.5s / 3s 太短，Kimi 的限流窗口根本没过去。
    宁可慢一点拿全数据，也不要快一点拿份假报告。
    """
    ctx = ssl.create_default_context()
    last = ""
    for attempt in range(max(0, retries) + 1):
        if attempt:                                        # 退避：3s、6s、12s
            time.sleep(min(3 * (2 ** (attempt - 1)), 15))
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            })
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return True, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:                              # noqa: BLE001
                detail = ""
            if e.code == 401:
                return False, "接口 Key 无效或已失效（401）"
            if e.code == 402:
                return False, "账户余额不足，请充值（402）"
            if e.code == 429:
                last = "调用太频繁被限流（429），已自动重试仍未成功"
                continue                                   # 限流：值得重试
            if 500 <= e.code < 600:
                last = f"服务端错误 {e.code}，已自动重试仍未成功"
                continue                                   # 服务端抖动：值得重试
            return False, f"接口返回 {e.code}：{detail or e.reason}"
        except urllib.error.URLError as e:
            last = f"连不上接口：{e.reason}（检查网络 / 代理 / Key）"
            continue                                       # 网络抖动：值得重试
        except Exception as e:                             # noqa: BLE001
            last = f"请求异常：{e}"
            continue
    return False, last or "请求失败"


def chat_completion(spec: EngineSpec, system_prompt: str, user_prompt: str,
                    api_keys: dict[str, str] | None = None,
                    models: dict[str, str] | None = None,
                    temperature: float | None = None,
                    timeout: float = 120.0,
                    retries: int = 2) -> tuple[bool, str, str]:
    """通用「系统提示 + 用户提示」调用。返回 (是否成功, 回答或失败原因, 备注)。

    为什么单独抽一个：GEO 是「一问一答」，文案仿写是「长提示 + 长输出」，
    两者只是 system/user 内容不同。若各写一份请求代码，限流退避、温度降级
    这些踩过坑的逻辑就会在两处漂移 —— 一处修了另一处没修，故障必然复发。

    温度降级：Kimi 只允许 temperature=1.0，传别的值直接 400。遇到这类报错
    自动降级重试（1.0 → 完全不带该参数），而不是整轮失败。
    """
    if spec.kind != "api":
        return False, (f"{spec.label} 走浏览器自动化通道，当前版本尚未接通"), ""
    key = (api_keys or {}).get(spec.key, "").strip()
    if not key:
        return False, f"未填写 {spec.label} 的接口 Key", ""
    model = ((models or {}).get(spec.key) or "").strip() or spec.model
    url = spec.base_url.rstrip("/") + "/chat/completions"
    note = ""

    def build(t):
        b = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        if t is not None:
            b["temperature"] = t
        return b

    t = temperature if temperature is not None else spec.temperature
    ok, text = _post_json(url, build(t), key, timeout=timeout, retries=retries)
    if not ok and "temperature" in (text or "").lower():
        for fallback in (1.0, None):
            if t == fallback:
                continue
            ok2, text2 = _post_json(url, build(fallback), key, timeout=timeout, retries=0)
            if ok2:
                ok, text = True, text2
                note = f"该模型不接受温度 {t}，已自动改用 {fallback}"
                break
    if not ok:
        return False, text, note
    try:
        data = json.loads(text)
        content = (((data.get("choices") or [{}])[0].get("message") or {})
                   .get("content") or "").strip()
    except Exception as e:                                 # noqa: BLE001
        return False, f"返回内容解析失败：{e}", note
    if not content:
        return False, "接口返回了空回答", note
    return True, content, note


def ask_engine(spec: EngineSpec, query: str, api_keys: dict[str, str] | None = None,
               timeout: float = 60.0,
               models: dict[str, str] | None = None,
               temps: dict[str, str] | None = None) -> AskResult:
    """向一个引擎提一个问题。失败不抛异常，错误写进 result.error。

    models  {引擎key: 模型名}   —— 临时覆盖引擎表里的默认模型
    temps   {引擎key: 温度}     —— 临时覆盖默认采样温度

    为什么要留这两个口子：各家模型名下线/改名非常快（Kimi 的 kimi-latest、
    moonshot-v1 全系都已下线，2026-10-12 实测在售的只有 kimi-k2.6），
    写死必然过期。界面里改一下就能继续用，不用等发版。

    temperature 兼容重试：Kimi 只允许 temperature=1.0，传别的值直接 400。
    遇到这类报错会自动降级重试（1.0 → 不带该参数），而不是整轮失败。
    """
    res = AskResult(engine=spec.key, query=query)
    t0 = time.time()
    try:
        if spec.kind == "api":
            key = (api_keys or {}).get(spec.key, "").strip()
            if not key:
                res.error = f"未填写 {spec.label} 的接口 Key"
                return res
            raw_temp = ((temps or {}).get(spec.key) or "").strip()
            try:
                temp = float(raw_temp) if raw_temp else spec.temperature
            except ValueError:
                temp = spec.temperature
            ok, text, _note = chat_completion(
                spec, _SYSTEM_PROMPT, query, api_keys=api_keys, models=models,
                temperature=temp, timeout=timeout)
            if not ok:
                res.error = text
                return res
            res.text = text
            res.ok = True
        elif spec.kind == "cdp":
            res.error = (f"{spec.label} 走浏览器自动化通道，当前版本尚未接通。"
                         "请先用 DeepSeek / Kimi 跑，豆包通道后续版本开放。")
            return res
        else:
            res.error = f"未知引擎类型：{spec.kind}"
            return res
    except Exception as e:                                 # noqa: BLE001
        res.error = f"提问异常：{e}"
        return res
    finally:
        res.elapsed = time.time() - t0
        if res.ok:
            res.sources = extract_sources(res.text)
    return res


# ----------------------------------------------------------------------
#  一轮体检
# ----------------------------------------------------------------------

def scan(brand: str, queries: list[str], engines: list[str],
         aliases: list[str] | None = None,
         competitors: list[str] | None = None,
         samples: int = 3, api_keys: dict[str, str] | None = None,
         models: dict[str, str] | None = None,
         temps: dict[str, str] | None = None,
         workers: int = 4, timeout: float = 60.0,
         progress: Callable[[int, int, str], None] | None = None,
         should_stop: Callable[[], bool] | None = None) -> ScanReport:
    """跑一轮完整的可见度体检。

    brand        品牌名（必填）
    queries      提问词列表（20~50 个为宜）
    engines      引擎 key 列表（见 ENGINE_SPECS）
    aliases      品牌别名（全称 / 简称 / 英文名 / 域名）
    competitors  竞品名列表
    samples      每个「问题 × 引擎」问几次（AI 有随机性，默认 3）
    api_keys     {引擎key: 接口Key}
    models       {引擎key: 模型名}，留空则用引擎表里的默认模型
    temps        {引擎key: 温度}，留空则用引擎表里的默认温度（Kimi 只能用 1.0）
    progress     (已完成, 总数, 当前描述) 回调
    should_stop  返回 True 则中止（界面「停止」按钮）
    """
    report = ScanReport(
        brand=brand, aliases=list(aliases or []), competitors=list(competitors or []),
        engines=list(engines), queries=list(queries), samples=max(1, int(samples)),
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    variants = brand_variants(brand, aliases)
    comp_variants = {c: brand_variants(c) for c in (competitors or [])}

    tasks = []
    for q in queries:
        for e in engines:
            spec = ENGINE_SPECS.get(e)
            if spec is None:
                report.errors.append(f"未知引擎：{e}")
                continue
            for _ in range(report.samples):
                tasks.append((spec, q))

    total = len(tasks)
    done = 0
    bucket: dict[tuple[str, str], list[AskResult]] = {}

    def say(text: str):
        if progress:
            try:
                progress(done, total, text)
            except Exception:                              # noqa: BLE001
                pass

    say("准备开始…")
    # 按引擎分组并发：限流紧的引擎（Kimi 免费额度）自己排队，
    # 不会占着线程池槽位把跑得快的引擎（DeepSeek）一起拖慢。
    groups: dict[str, list[tuple[EngineSpec, str]]] = {}
    for spec, q in tasks:
        groups.setdefault(spec.key, []).append((spec, q))

    stopped = False
    for ekey, items in groups.items():
        if stopped:
            break
        spec0 = items[0][0]
        n = max(1, min(spec0.concurrency, workers, 8))
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = {pool.submit(ask_engine, sp, q, api_keys, timeout, models, temps): (sp, q)
                       for sp, q in items}
            for fut in as_completed(futures):
                if should_stop and should_stop():
                    for f in futures:
                        f.cancel()
                    report.errors.append("已手动停止，本次为不完整结果")
                    stopped = True
                    break
                spec, q = futures[fut]
                try:
                    r = fut.result()
                except Exception as e:                     # noqa: BLE001
                    r = AskResult(engine=spec.key, query=q, error=f"任务异常：{e}")
                bucket.setdefault((q, spec.key), []).append(r)
                done += 1
                say(f"{spec.label}：{q[:24]}{'…' if len(q) > 24 else ''}")

    # 汇总每个「问题 × 引擎」
    for (q, ekey), results in bucket.items():
        item = QueryReport(query=q, engine=ekey, samples=len(results))
        positions: list[float] = []
        for r in results:
            if not r.ok:
                if r.error and r.error not in item.error:
                    item.error = (item.error + "；" if item.error else "") + r.error
                continue
            item.valid += 1
            pos = first_position(r.text, variants)
            if pos >= 0:
                item.mentions += 1
                positions.append(pos)
                if not item.answer_sample:
                    item.answer_sample = r.text
            for s in r.sources:
                if s not in item.sources:
                    item.sources.append(s)
            for cname, cvs in comp_variants.items():
                if first_position(r.text, cvs) >= 0:
                    item.competitors[cname] = item.competitors.get(cname, 0) + 1
            sent = detect_sentiment(r.text, variants)
            if sent == "负面":
                item.sentiment = "负面"
            elif sent == "正面" and item.sentiment != "负面":
                item.sentiment = "正面"
        item.first_pos = (sum(positions) / len(positions)) if positions else -1.0
        report.items.append(item)
        # 失败的提问单独记账，绝不混进「未提及」里（见 QueryReport.failed 的说明）
        if item.failed:
            reason = item.error or "未知原因"
            msg = f"{ekey}「{q}」提问失败：{reason}"
            if msg not in report.errors:
                report.errors.append(msg)

    report.items.sort(key=lambda i: (i.engine, -i.mention_rate))
    return report


# ----------------------------------------------------------------------
#  快照（趋势）与导出
# ----------------------------------------------------------------------

def snapshot_dir() -> Path:
    """历史快照目录：%APPDATA%/TrafficCatcher/geo_snapshots/"""
    try:
        from . import branding
        base = branding.app_data_dir()
    except Exception:                                      # noqa: BLE001
        base = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "TrafficCatcher"
    d = base / "geo_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_snapshot(report: ScanReport) -> Path:
    """把一轮结果存成快照（文件名带时间戳，供趋势图读取）。"""
    d = snapshot_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|]', "_", (report.brand or "brand"))[:20]
    path = d / f"{stamp}_{safe}.json"
    # 同一秒内连跑两次（或同一品牌复测）会撞名覆盖掉上一次，趋势图就少一个点。
    # 撞名时自动加序号，历史只增不减。
    i = 2
    while path.exists():
        path = d / f"{stamp}_{safe}_{i}.json"
        i += 1
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_snapshots(limit: int = 30) -> list[dict]:
    """读取历史快照（最新的在后），用于画趋势。"""
    out: list[dict] = []
    try:
        files = sorted(snapshot_dir().glob("*.json"),
                       key=lambda p: p.name)[-max(1, limit):]
    except OSError:
        return out
    for p in files:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def export_report_excel(report: ScanReport, path: str | Path) -> Path:
    """导出体检报告（xlsx）：概览 / 明细 / 信源 / 建议 四张表。"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill
    except ImportError as e:                               # pragma: no cover
        raise ImportError("导出 Excel 需要 openpyxl：pip install openpyxl") from e

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="374151")

    def write_sheet(ws, headers: list[str], rows: list[list]):
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = head_font
            cell.fill = head_fill
        for r in rows:
            ws.append(r)
        for col in ws.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max(width + 4, 10), 60)

    # 1) 概览
    ws = wb.active
    ws.title = "概览"
    engines_txt = "、".join(ENGINE_SPECS.get(e, EngineSpec(e, e, "")).label
                           for e in report.engines)
    overview = [
        ["品牌", report.brand],
        ["品牌别名", "、".join(report.aliases) or "-"],
        ["竞品", "、".join(report.competitors) or "-"],
        ["引擎", engines_txt],
        ["提问词数", len(report.queries)],
        ["每词采样次数", report.samples],
        ["有效回答数", report.total_answers],
        ["失败提问数", report.failed_count],
        ["整体提及率", f"{report.mention_rate * 100:.1f}%"],
        ["体检时间", report.started_at],
        ["", ""],
        ["口径说明", "「失败提问」= 没拿到回答（限流/Key/网络），不等于 AI 没提到你，"
                    "两者绝不能混算；失败多时请排除故障后重跑"],
        ["口径说明", "AI 回答有随机性，本表按多次采样统计比例，不代表单次结果"],
        ["口径说明", "接口通道不带联网搜索，反映的是模型既有认知中的品牌信息"],
        ["口径说明", "AI 收录内容以月为单位，请按月复测看趋势，不承诺排名"],
        ["", ""],
        ["各引擎提及率", ""],
    ]
    for k, v in report.by_engine().items():
        overview.append([f"  {ENGINE_SPECS.get(k, EngineSpec(k, k, '')).label}",
                         f"{v * 100:.1f}%"])
    overview.append(["", ""])
    overview.append(["竞品对比", ""])
    for name, hits, rate in report.competitor_ranking():
        overview.append([f"  {name}", f"被提 {hits} 次（{rate * 100:.1f}%）"])
    write_sheet(ws, ["项目", "内容"], overview)

    # 2) 明细
    ws2 = wb.create_sheet("明细")
    keys = ["提问词", "引擎", "状态", "采样次数", "提及次数", "提及率", "首次位次",
            "竞品提及", "情感", "引用信源", "回答样本"]
    rows = [[i.to_row().get(k, "") for k in keys] for i in report.items]
    write_sheet(ws2, keys, rows)

    # 3) 信源
    ws3 = wb.create_sheet("引用信源")
    write_sheet(ws3, ["信源 / 平台", "被引用次数"],
                [[n, c] for n, c in report.source_ranking()] or [["（本轮未抓到引用链接）", 0]])

    # 4) 建议
    ws4 = wb.create_sheet("优化建议")
    write_sheet(ws4, ["序号", "建议"],
                [[i + 1, t] for i, t in enumerate(report.suggestions())])

    if report.errors:
        ws5 = wb.create_sheet("异常")
        write_sheet(ws5, ["异常"], [[e] for e in report.errors[:200]])

    wb.save(path)
    return path
