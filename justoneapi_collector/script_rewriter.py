# -*- coding: utf-8 -*-
"""短视频文案仿写：拆开爆款看结构，产出能照着开拍的分镜稿。

═══════════════════════════════════════════════════════════════════════════
这个模块到底在做什么（先想清楚，不然做出来的只是「同义词替换」）
═══════════════════════════════════════════════════════════════════════════
一条短视频能跑，靠的不是某句漂亮话，而是**结构与节奏**：前 3 秒能不能留人、
痛点扎得准不准、转折在第几秒、促单落在什么情绪上。所以仿写的单位是
「结构 + 节奏 + 钩子类型」，不是「措辞」。

流程：
    对标原文 → 结构拆解（钩子/痛点/转折/促单在哪、语速多少）
             → 按体裁骨架（口播 / 剧情 / Vlog / …）重建
             → 套客户的卖点、人群、语气、时长
             → 过合规拦截（广告法极限词 / 功效承诺 / 引流词）
             → 产出分镜表（时间轴｜画面｜口播）+ 标题备选 + 话题标签 + 质检评分

═══════════════════════════════════════════════════════════════════════════
三条不能破的规矩（踩了就是给客户埋雷）
═══════════════════════════════════════════════════════════════════════════
  1. **只仿结构，不许抄句。** 提示词里写明红线，输出必须是全新表达。
     客户拿去照搬原句，轻则搬运限流，重则侵权纠纷。
  2. **合规必须硬拦，不能只提示。** 极限词、功效承诺、站外引流词是代运营
     挨罚的重灾区；生成完扫一遍，命中就标红并给替换建议。
  3. **失败不能伪装成成功。** 接口挂了 / Key 没填 / 被限流，就是「没生成」，
     绝不能塞一段模板文字充数（参照 GEO 那次「失败被算成未提及」的教训）。

═══════════════════════════════════════════════════════════════════════════
关于引擎
═══════════════════════════════════════════════════════════════════════════
复用 geo_monitor 的引擎表与 chat_completion()：Key 存在本机
（设置项 geo_api_keys，与 GEO 页共用），本软件不加价、不经手。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import geo_monitor as gm

# ======================================================================
#  一、体裁体系：分类决定写法
# ======================================================================

@dataclass(frozen=True)
class GenreSpec:
    """一种短视频体裁的写作骨架。

    wps         语速（字/秒）。中文口播实测：带货快节奏约 5.2，叙述类约 4.2。
                用它把「目标秒数」换算成「目标字数」——不换算的话，
                模型写出来的稿子往往长一倍，客户照着拍要砍一半，等于白干。
    structure   (段落名, 这一段要干什么, 时长占比)
    hooks       这类体裁常用的钩子套路（给模型当范式，不是让它照抄）
    tips        写作要点（会进提示词）
    """
    key: str
    label: str
    desc: str
    wps: float
    structure: tuple[tuple[str, str, float], ...]
    hooks: tuple[str, ...]
    tips: tuple[str, ...]


GENRES: dict[str, GenreSpec] = {
    "talk": GenreSpec(
        key="talk", label="口播", wps=4.6,
        desc="一个人对着镜头讲观点 / 知识 / 行业内幕，靠信息差留人",
        structure=(
            ("钩子", "前 3 秒抛反常识结论或犀利提问，必须让人想知道为什么", 0.12),
            ("观点", "把核心结论一句话说死，不含糊", 0.16),
            ("论证", "2~3 个论据，每个都要有具体场景或数字，不要空讲道理", 0.44),
            ("补充", "补一个「例外 / 但是」，显得客观，顺手堵住杠精", 0.16),
            ("收尾", "一句总结 + 引导评论或关注", 0.12),
        ),
        hooks=(
            "90% 的人都做错了：…",
            "别再…了，我踩过这个坑",
            "为什么…？答案跟你想的相反",
            "做了 3 年…，今天说点得罪人的",
            "如果你也…，这条一定要看完",
        ),
        tips=("一段只讲一个点，贪多必糊",
              "论据要有具体数字 / 场景，抽象道理没人听",
              "全片不出现「大家好」「今天跟大家分享」这类废话开场"),
    ),
    "story": GenreSpec(
        key="story", label="剧情", wps=4.2,
        desc="情景短剧，靠冲突与反转推进，结尾落一个情绪或观点",
        structure=(
            ("开场", "直接进入冲突中的场景，第一句就是台词，不铺垫", 0.14),
            ("冲突", "把矛盾摆上台面：谁跟谁、争什么", 0.24),
            ("升级", "加码，让冲突更难受（误解 / 加价 / 第三者视角）", 0.24),
            ("反转", "一句话或一个动作把局面翻过来，这是全片最高点", 0.24),
            ("落点", "反转后的余味：一句台词或一个镜头收住", 0.14),
        ),
        hooks=("（开场即台词）「你再说一遍？」",
               "「这钱我不出了」",
               "老板把方案摔在桌上的那一刻，我知道完了",
               "她走了三年，第一次回来是找我借钱",
               "「你凭什么替我做决定」"),
        tips=("开场第一句必须是台词或动作，不要旁白介绍背景",
              "人物不超过 3 个，场景不超过 2 个（拍摄成本）",
              "反转要在最后 20% 处，太早了后面没看头"),
    ),
    "vlog": GenreSpec(
        key="vlog", label="Vlog", wps=4.0,
        desc="日常 / 探店 / 旅行记录，靠真实感与画面节奏带人代入",
        structure=(
            ("代入", "一句话交代「我在哪、要干嘛」，用现场声或动作开场", 0.12),
            ("过程", "按时间或动线推进，挑 3~4 个节点，不要流水账", 0.40),
            ("特写", "1~2 个高光画面细写（食物 / 风景 / 细节），给停留理由", 0.24),
            ("感受", "说真话：好在哪、坑在哪，不要全是夸", 0.14),
            ("收尾", "一句轻量的互动邀请", 0.10),
        ),
        hooks=("早上 6 点出门，就是为了这一口",
               "这家店排队 2 小时，值不值我替你试了",
               "第一次来…，跟着我走不踩坑",
               "花 300 块在…能玩成什么样",
               "本地人才知道的…"),
        tips=("画面优先：口播只补画面说不清的信息",
              "一定要有 1 个真实的槽点，纯夸会像广告",
              "节奏靠剪辑点，不靠说话快"),
    ),
    "sell": GenreSpec(
        key="sell", label="种草带货", wps=5.2,
        desc="卖货型口播，痛点开场，演示卖点，价格与稀缺收口",
        structure=(
            ("痛点", "前 3 秒直接戳一个具体的使用场景痛点", 0.14),
            ("出场", "产品亮相 + 一句话说清它解决什么", 0.14),
            ("演示", "2~3 个卖点，每个都要「看得见的演示」而不是形容词", 0.38),
            ("价格", "报价格锚点 / 规格 / 赠品，给下单理由", 0.18),
            ("促单", "稀缺或时限 + 明确的下单动作", 0.16),
        ),
        hooks=("每次…都烦到想扔，直到我换了它",
               "用了三个月，说点实话",
               "别买贵了，这个价位段我只推这一个",
               "…的人一定要看到最后",
               "我妈用了都说好的…"),
        tips=("卖点必须是能拍出来的动作，别写「品质优良」这种词",
              "价格段位要具体（几十 / 一两百），别说「超便宜」",
              "促单只给一个动作，别又让关注又让加微又让下单"),
    ),
    "review": GenreSpec(
        key="review", label="测评开箱", wps=4.6,
        desc="结论前置型的横向 / 单品测评，靠可信度转化",
        structure=(
            ("结论", "先把结论抛出来（值不值 / 选哪个），再讲过程", 0.12),
            ("外观", "开箱第一印象，说材质与做工细节", 0.16),
            ("实测", "2~3 项真实测试，带过程与结果", 0.40),
            ("优劣", "明确说缺点，缺点说得越真，优点越可信", 0.18),
            ("人群", "说清适合谁、不适合谁，顺带引导", 0.14),
        ),
        hooks=("买了 5 个…，只有 2 个能留",
               "这个价位，它是不是智商税？测完再说",
               "花 2000 块测出来的一句话结论",
               "别急着下单，先看完这 3 个坑",
               "同价位里它到底排第几"),
        tips=("缺点不能省 —— 全是优点的测评没人信",
              "测试要有对照与标准，别只说「感觉不错」",
              "结论可以主观，过程必须客观"),
    ),
    "tutorial": GenreSpec(
        key="tutorial", label="教程干货", wps=4.4,
        desc="步骤型教学，结果前置，让人觉得「照做就有效」",
        structure=(
            ("结果", "先把成品 / 效果亮出来，给一个留下的理由", 0.14),
            ("准备", "需要什么，一句话带过，不要清单念半天", 0.12),
            ("步骤", "3~4 步，每步一句动作指令，动词开头", 0.46),
            ("避坑", "一个最常见的错误做法 + 后果", 0.16),
            ("收尾", "引导收藏 / 关注（这类内容收藏价值最高）", 0.12),
        ),
        hooks=("3 步搞定…，不用求人",
               "学会这个，一年省下…",
               "别再手动…了，30 秒教会你",
               "90% 的人第一步就错了",
               "收藏这一条，用得上的时候不用到处找"),
        tips=("步骤动词开头（点开/拖到/改成），别用名词堆砌",
              "步数控制在 4 步以内，步骤一多完播率就崩",
              "避坑环节是这类内容最被收藏的部分"),
    ),
    "emotion": GenreSpec(
        key="emotion", label="情感共鸣", wps=3.8,
        desc="情绪型内容，金句 + 小故事 + 升华，靠共鸣转发",
        structure=(
            ("金句", "开头一句能当文案用的话，直击处境", 0.14),
            ("场景", "一个具体到画面感的生活片段", 0.26),
            ("转折", "从「我以为」到「后来才懂」", 0.24),
            ("升华", "把个人体验讲成一句普遍的道理", 0.22),
            ("提问", "一句开放式提问，把评论区打开", 0.14),
        ),
        hooks=("成年人的崩溃，都是从一句「没事」开始的",
               "有些话，只有到了某个年纪才懂",
               "我妈说了一句话，我记了十年",
               "你有没有过那种，明明很累却不敢停的时候",
               "后来我才明白，…"),
        tips=("金句要能单独截图，是这类内容的传播单元",
              "故事必须具体到时间地点动作，抽象抒情没人共鸣",
              "结尾提问要真的想听回答，不要「你觉得呢」敷衍"),
    ),
    "interview": GenreSpec(
        key="interview", label="访谈对话", wps=4.4,
        desc="一问一答型，靠嘉宾身份或尖锐提问制造信息增量",
        structure=(
            ("提问", "开场直接抛一个尖锐或好奇的问题", 0.12),
            ("身份", "一句话交代对方是谁、凭什么说这个话", 0.12),
            ("问答", "3 组问答，每组问题递进，答案要有信息量", 0.52),
            ("金句", "挑一句最狠的回答单独强调", 0.14),
            ("收尾", "一个开放式的追问或引导", 0.10),
        ),
        hooks=("「你这个行业，还能干几年？」",
               "我问了他一个得罪人的问题",
               "做了 10 年…，他劝年轻人别入行",
               "「你后悔吗？」他想了很久",
               "年薪 50 万的人，为什么辞职去…"),
        tips=("问题要递进：是什么 → 为什么 → 怎么办",
              "答案必须是观点或经历，不能是百科式解释",
              "保留一句口语化的狠话，比润色过的漂亮话有力"),
    ),
}

GENRE_ORDER = ("talk", "story", "vlog", "sell", "review",
               "tutorial", "emotion", "interview")

# 时长档位（秒）。字数由「时长 × 语速」算出，不写死。
DURATION_CHOICES: tuple[int, ...] = (15, 30, 60, 90)

# 语气档位
TONES: dict[str, str] = {
    "pro": "专业干练：用词准、句子短、少感叹号，像行内人在交底",
    "warm": "亲和聊天：像跟朋友说话，允许口语词和轻微啰嗦",
    "sharp": "犀利吐槽：敢下判断，敢说「别买」，有态度",
    "fun": "搞笑玩梗：夸张对比、自嘲，节奏快",
    "touch": "温情走心：慢一点，句子长一点，留白",
    "push": "促销催单：紧迫感强，动词密集，句句指向下单",
}

# 创意档位（温度）。Kimi 只接受 1.0，geo_monitor 里会自动降级重试。
TEMPERATURES: dict[str, float] = {"steady": 0.7, "lively": 1.0, "wild": 1.3}


# ======================================================================
#  二、合规：广告法与平台红线
# ======================================================================

# (词, 类别, 建议替换)。命中即标红 —— 这是代运营最容易挨罚的地方，
# 只提示不拦截等于没做：客户复制走就发出去了。
FORBIDDEN_WORDS: tuple[tuple[str, str, str], ...] = (
    ("最好", "极限词", "试试 / 很不错"),
    ("最佳", "极限词", "比较合适 / 我更推荐"),
    ("最强", "极限词", "表现不错"),
    ("最便宜", "极限词", "性价比高 / 价格友好"),
    ("最低价", "极限词", "这个价位段里算低的"),
    ("全网最低", "极限词", "我比过几家里算低的"),
    ("第一", "极限词", "靠前的 / 我常用"),
    ("排名第一", "极限词", "排在前面"),
    ("唯一", "极限词", "少见的 / 我目前只遇到这一个"),
    ("顶级", "极限词", "用料的 / 做得比较讲究"),
    ("极致", "极限词", "很到位"),
    ("绝对", "极限词", "基本 / 通常"),
    ("百分百", "极限词", "大多数情况下"),
    ("100%", "极限词", "大多数情况下"),
    ("完美", "极限词", "没什么明显短板"),
    ("无敌", "极限词", "很难找到对手"),
    ("国家级", "极限词", "（删除，或改用具体认证名）"),
    ("世界级", "极限词", "（删除）"),
    ("史上", "极限词", "近期 / 我见过的"),
    ("彻底", "虚假承诺", "明显改善 / 基本解决"),
    ("永久", "虚假承诺", "很长一段时间 / 长期"),
    ("根治", "医疗功效", "缓解 / 改善"),
    ("痊愈", "医疗功效", "好转"),
    ("特效", "医疗功效", "效果明显"),
    ("药到病除", "医疗功效", "（删除）"),
    ("包治", "医疗功效", "（删除）"),
    ("消炎", "医疗功效", "舒缓"),
    ("杀菌", "医疗功效", "抑菌（需有检测依据）"),
    ("治疗", "医疗功效", "护理 / 舒缓"),
    ("疗效", "医疗功效", "使用感受"),
    ("美白", "需资质", "提亮（若无特证，别提美白）"),
    ("祛斑", "需资质", "淡化（若无特证）"),
    ("丰胸", "需资质", "（删除）"),
    ("减肥", "需资质", "塑形 / 体态管理"),
    ("瘦身", "需资质", "塑形"),
    ("保证", "虚假承诺", "大概率 / 我这边用下来"),
    ("承诺", "虚假承诺", "（删除）"),
    ("无效退款", "虚假承诺", "（改为具体售后政策）"),
    ("稳赚", "虚假承诺", "（删除）"),
    ("躺赚", "虚假承诺", "（删除）"),
    ("暴富", "虚假承诺", "（删除）"),
    ("月入过万", "虚假承诺", "（删除，涉收益承诺）"),
    ("包过", "虚假承诺", "（删除）"),
    ("包瘦", "虚假承诺", "（删除）"),
    ("必瘦", "虚假承诺", "（删除）"),
    ("加微信", "站外引流", "（改为平台内动作，如「评论区扣 1」）"),
    ("加微", "站外引流", "（改为平台内动作）"),
    ("薇信", "站外引流", "（删除）"),
    ("微信号", "站外引流", "（删除）"),
    ("私信我", "站外引流", "评论区留言"),
    ("扫码", "站外引流", "（删除）"),
)


def check_compliance(text: str, extra: list[str] | None = None) -> list[dict]:
    """扫描一段文案里的违规词，返回 [{词, 类别, 建议}]。

    命中长度短的词（如「第一」）会误伤「第一次」，所以做了简单的上下文豁免：
    后面紧跟「次/步/口/天/个」等量词时不算。误报比漏报更惹人烦 —— 客户
    看到满屏标红会直接关掉这个功能。
    """
    body = text or ""
    hits: list[dict] = []
    seen: set[str] = set()

    def scan(word: str, category: str, advice: str):
        if word in seen:
            return
        for m in re.finditer(re.escape(word), body):
            after = body[m.end(): m.end() + 1]
            if after in ("次", "步", "口", "天", "个", "口儿", "年", "版"):
                continue
            seen.add(word)
            hits.append({"词": word, "类别": category, "建议": advice})
            return

    for w, c, a in FORBIDDEN_WORDS:
        scan(w, c, a)
    for w in (extra or []):
        w = (w or "").strip()
        if w:
            scan(w, "自定义禁用词", "（按你的要求避开）")
    return hits


# ======================================================================
#  三、原文拆解
# ======================================================================

_SENT_SPLIT = re.compile(r"(?<=[。！？!?…；;])\s*|\n+")
_STOPWORDS = set("的了是我你他她它我们你们他们这那有个在什么就都也很还要会去到和跟对把被吧呢啊哦呀哈")

# 悬念 / 反常识 / 提问类
_HOOK_MARKS = ("为什么", "凭什么", "别再", "千万别", "居然", "竟然", "其实",
               "你以为", "真相", "后悔", "踩坑", "错了", "秘密",
               "告诉你", "注意", "千万不要", "第一", "90%", "99%")
# 痛点开场同样能留人（真机验证里模型最爱用这类开头），单列出来便于调整
_HOOK_PAIN_MARKS = ("累", "疼", "痛", "酸", "僵硬", "硬得", "受不了", "废了",
                    "白花", "浪费", "亏", "吵", "忍了很久", "烦")
_CTA_MARKS = ("关注", "收藏", "点赞", "评论", "留言", "私信", "下单", "链接",
              "购物车", "橱窗", "扣1", "扣 1", "主页", "试试", "买")
_TURN_MARKS = ("但是", "可是", "不过", "然而", "结果", "没想到", "后来",
               "直到", "反而", "殊不知")


@dataclass
class SourceAnalysis:
    """对标原文的拆解结果：先看清它为什么能跑，再谈仿。"""
    chars: int = 0
    sentences: int = 0
    est_seconds: float = 0.0
    first_sentence: str = ""
    last_sentence: str = ""
    hook_hits: list[str] = field(default_factory=list)
    turn_hits: list[str] = field(default_factory=list)
    cta_hits: list[str] = field(default_factory=list)
    top_words: list[str] = field(default_factory=list)

    # ↓↓↓ 下面这几项只有「带着时间轴的转写稿」才算得出来 ——
    # 也就是从视频链接直接提取出来的那种（见 video_script_extract）。
    # 纯文本粘贴进来时全为空，不能瞎填。
    speech_seconds: float = 0.0          # 实测：真正在说话的秒数（末句落点）
    real_wps: float = 0.0                # 实测语速（字/秒）
    hook_window: str = ""                # 前 3 秒念了什么 —— 留人全看这几秒
    pace: list[tuple[str, int]] = field(default_factory=list)   # 每 5 秒说多少字

    def summary(self) -> str:
        parts = []
        if self.speech_seconds > 0:
            head = f"{self.chars} 字 / 实测口播 {self.speech_seconds:.0f} 秒"
            if self.real_wps > 0:
                head += f"（{self.real_wps:.1f} 字/秒）"
            parts.append(head + f" / {self.sentences} 句")
        else:
            parts.append(f"{self.chars} 字 / 约 {self.est_seconds:.0f} 秒 / {self.sentences} 句")
        if self.hook_window:
            parts.append("前 3 秒：「" + self.hook_window[:40] + "」")
        if self.hook_hits:
            parts.append("开场钩子：" + "、".join(self.hook_hits[:3]))
        else:
            parts.append("开场没有明显钩子（原文可能靠画面留人）")
        if self.turn_hits:
            parts.append("转折：" + "、".join(self.turn_hits[:3]))
        if self.cta_hits:
            parts.append("促单/引导：" + "、".join(self.cta_hits[:3]))
        if self.pace:
            parts.append("节奏：" + " ".join(f"{seg}{n}字" for seg, n in self.pace[:6]))
        if self.top_words:
            parts.append("高频词：" + "、".join(self.top_words[:6]))
        return "　·　".join(parts)


def _seg_tuple(seg) -> tuple[float, float, str] | None:
    """兼容 dict / dataclass 两种时间轴片段写法，统一成 (起秒, 止秒, 文本)。"""
    if seg is None:
        return None
    if isinstance(seg, dict):
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or start)
        text = str(seg.get("text") or "")
    else:
        start = float(getattr(seg, "start", 0.0) or 0.0)
        end = float(getattr(seg, "end", start) or start)
        text = str(getattr(seg, "text", "") or "")
    if end < start:
        end = start
    text = text.strip()
    return (start, end, text) if text else None


def analyze_source(text: str, wps: float = 4.5,
                   segments: list | None = None) -> SourceAnalysis:
    """把原文拆成可复用的结构信息。

    纯规则实现（不调 AI）：拆解这一步必须可复现、零成本、秒出结果。
    让 AI 去拆，等于多花一次调用换一个没法解释的结果。

    segments 是带时间轴的片段（从视频语音转写来的才有），传进来就能多算出
    实测口播时长 / 真实语速 / 前 3 秒钩子 / 每 5 秒节奏。这几项是纯文本
    永远猜不出来的，也是客户拿到手最有用的部分：他照着的不是文案，
    是「几句搓在第几秒」的那个节奏。
    """
    body = (text or "").strip()
    a = SourceAnalysis(chars=len(re.sub(r"\s+", "", body)))
    sents = [s.strip() for s in _SENT_SPLIT.split(body) if s and s.strip()]
    a.sentences = len(sents)
    a.est_seconds = (a.chars / wps) if wps > 0 else 0.0
    a.first_sentence = sents[0] if sents else ""
    a.last_sentence = sents[-1] if sents else ""

    head = body[:60]
    a.hook_hits = [w for w in _HOOK_MARKS if w in head]
    if not a.hook_hits and sents and re.search(
            r"吗|呢[？?]|[？?]|多少|是不是|有没有|凭啥|为什么", sents[0]):
        a.hook_hits = ["提问开场"]      # 真机稿里最常见的钩子，未必带套话关键词
    a.turn_hits = [w for w in _TURN_MARKS if w in body]
    tail = body[-80:]
    a.cta_hits = [w for w in _CTA_MARKS if w in tail] or \
                 [w for w in _CTA_MARKS if w in body][:3]

    counter: dict[str, int] = {}
    for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", body):
        if w in _STOPWORDS:
            continue
        counter[w] = counter.get(w, 0) + 1
    a.top_words = [w for w, _c in sorted(counter.items(),
                                         key=lambda kv: kv[1], reverse=True)[:10]]

    # ---- 时间轴部分：有才算，没有就保持空 ----
    segs = [s for s in (_seg_tuple(x) for x in (segments or [])) if s]
    if segs:
        total = max(e for _s, e, _t in segs)
        a.speech_seconds = round(total, 1)
        if total > 1 and a.chars:
            a.real_wps = round(a.chars / total, 2)
            a.est_seconds = a.speech_seconds          # 实测值优先于估算值

        hook_parts = [t for s, _e, t in segs if s < 3.0] or [segs[0][2]]
        a.hook_window = re.sub(r"\s+", "", "".join(hook_parts))

        buckets: dict[int, int] = {}
        for s, e, t in segs:
            mid = (s + e) / 2.0
            k = int(mid // 5)
            buckets[k] = buckets.get(k, 0) + len(re.sub(r"\s+", "", t))
        if buckets:
            top = max(buckets)
            a.pace = [(f"{i*5}-{i*5+5}s", buckets.get(i, 0))
                      for i in range(top + 1) if buckets.get(i, 0)]
    return a


# ----------------------------------------------------------------------
#  从文案反推体裁：让客户不用猜该选哪一类
# ----------------------------------------------------------------------

# (体裁, 命中词) —— 命中即加分；多类都命中时取总分最高的。
# 顺序即优先级：带货 / 测评这类"围绕商品组织内容"的先判，口播兜底
# （它本来就什么都像，放在最后当默认值最合适）。
_GENRE_MARKS: list[tuple[str, tuple[str, ...]]] = [
    ("sell", ("上链接", "购物车", "橱窗", "优惠券", "限时", "原价", "秒杀",
              "下单", "买它", "拍下", "福利", "库存不多", "评论区扣")),
    ("review", ("测评", "实测", "开箱", "对比", "翻车", "回购", "平替",
                "性价比", "智商税", "功课")),
    ("tutorial", ("第一步", "第二步", "第三步", "教程", "技巧", "方法",
                  "步骤", "记得收藏", "照着做", "一分钟学会")),
    ("interview", ("你觉得", "请问", "采访", "怎么看", "如果你是", "一句话送给")),
    ("emotion", ("其实我们", "小时候", "妈妈", "爸爸", "后来我才", "心疼",
                 "孤独", "夜里", "再见", "撑不住", "你知道吗")),
    # 「故事」只留真正叙事向的词。曾经把「老板 / 同事」也算进去，结果职场干货类
    # （"汇报工作说结果、请示工作说方案"这种）全被判成剧情 —— 那其实是标准口播。
    ("story", ("那天", "后来我就", "然后她就", "第二天一早", "说着说着",
               "老板突然", "同事一把", "我当场", "画面一转", "结果我妈")),
    ("vlog", ("今天带你们", "出发", "探店", "打卡", "早上好", "来到",
              "我的日常", "一天")),
]


def guess_genre(text: str, desc: str = "") -> str:
    """按文案里的行话猜体裁，返回 GENRES 的 key。认不出来就退回 talk（口播）。

    意图：客户粘进来一条别人的爆款，不该让他自己判断这是什么类型。
    猜错了不致命 —— 界面会把建议值选上，他下拉改一下就行。
    """
    body = f"{text or ''}\n{desc or ''}"
    head = (text or "")[:120]
    best_key, best_score = "talk", 0
    for key, marks in _GENRE_MARKS:
        score = sum(1 for m in marks if m in body)
        # 出现在开头的话分量加倍（体裁往往在第一句就亮出来了）
        score += sum(1 for m in marks if m in head)
        if score > best_score:
            best_key, best_score = key, score
    return best_key if best_score >= 2 else "talk"


# ======================================================================
#  四、请求参数与结果
# ======================================================================

@dataclass
class RewriteSpec:
    """一次仿写的全部参数。"""
    genre: str = "talk"
    seconds: int = 30
    tone: str = "pro"
    audience: str = ""
    product: str = ""
    points: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    forbidden_extra: list[str] = field(default_factory=list)
    versions: int = 3
    engine: str = "deepseek"
    temperature: str = "lively"

    @property
    def genre_spec(self) -> GenreSpec:
        return GENRES.get(self.genre, GENRES["talk"])

    @property
    def target_chars(self) -> int:
        return int(self.seconds * self.genre_spec.wps)


@dataclass
class ScriptDraft:
    """一版仿写稿。"""
    index: int = 1
    genre: str = "talk"
    genre_label: str = "口播"
    seconds: int = 30
    tone: str = "pro"
    title_options: list[str] = field(default_factory=list)
    hook: str = ""
    shots: list[dict] = field(default_factory=list)   # {时间轴, 画面, 口播}
    cta: str = ""
    hashtags: list[str] = field(default_factory=list)
    raw: str = ""
    error: str = ""
    chars: int = 0
    est_seconds: float = 0.0
    score: int = 0
    score_detail: list[str] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    source_ref: str = ""
    created_at: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.shots or self.hook) and not self.error

    @property
    def spoken(self) -> str:
        """纯口播全文（去掉时间轴与画面说明，可直接复制去念）。"""
        lines = []
        if self.hook:
            lines.append(self.hook)
        for s in self.shots:
            t = (s.get("口播") or "").strip()
            if t:
                lines.append(t)
        if self.cta and self.cta not in lines:
            lines.append(self.cta)
        return "\n".join(lines).strip()

    def to_row(self) -> dict:
        return {
            "版本": f"第 {self.index} 版",
            "体裁": self.genre_label,
            "评分": self.score if self.ok else "-",
            "字数": self.chars if self.ok else "-",
            "预估时长": f"{self.est_seconds:.0f} 秒" if self.ok else "-",
            "标题": (self.title_options[0] if self.title_options else "-"),
            "钩子": self.hook or "-",
            "违规词": "、".join(h["词"] for h in self.violations) or "无",
            "状态": "失败：" + (self.error or "未知原因") if self.error else "成功",
        }


@dataclass
class RewriteResult:
    """一轮仿写的完整结果。"""
    spec: RewriteSpec = field(default_factory=RewriteSpec)
    engine: str = "deepseek"
    source: str = ""
    analysis: SourceAnalysis | None = None
    items: list[ScriptDraft] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = ""

    @property
    def ok_count(self) -> int:
        return sum(1 for d in self.items if d.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for d in self.items if not d.ok)

    def best(self) -> ScriptDraft | None:
        ok = [d for d in self.items if d.ok]
        if not ok:
            return None
        return max(ok, key=lambda d: d.score)


# ======================================================================
#  五、提示词
# ======================================================================

def build_prompt(spec: RewriteSpec, source: str,
                 analysis: SourceAnalysis | None = None) -> tuple[str, str]:
    """构造 (system, user) 提示词。返回两个，便于引擎侧分开传。

    为什么把「体裁骨架」写得这么死：
    不给骨架时模型最爱写「开头引入 + 中间展开 + 结尾呼吁」这种万能八股，
    看着通顺但拍出来没人看。给出段落名、每段要干什么、占比多少，
    输出才落到具体的节奏上。
    """
    g = spec.genre_spec
    struct_lines = []
    for i, (name, duty, ratio) in enumerate(g.structure, 1):
        sec = max(1, round(spec.seconds * ratio))
        struct_lines.append(f"  {i}. {name}（约 {sec} 秒 / {ratio * 100:.0f}%）：{duty}")

    forbid_preview = "、".join(w for w, _c, _a in FORBIDDEN_WORDS[:16])
    extra_forbid = _split_lines("\n".join(spec.forbidden_extra))

    # 原片真实节奏（只有语音转写出来的稿子才有）。
    # 明说「不是让你照抄长度」：不说的话模型会照着 3 分钟的原片写一大篇，
    # 客户要的是 30 秒版本 —— 提示词里这种边界必须写死。
    real = ""
    if analysis is not None and analysis.speech_seconds > 1:
        real = (
            "\n【原片真实节奏（供对齐，不是要你照抄长度）】\n"
            f"- 原片实际口播 {analysis.speech_seconds:.0f} 秒，"
            f"真实语速 {analysis.real_wps:.1f} 字/秒\n"
            f"- 它的前 3 秒念的是：「{analysis.hook_window[:40]}」"
            "（学它为什么能留人，不要照搬措辞）\n"
            f"- 你要写的仍是 {spec.seconds} 秒 / 约 {spec.target_chars} 字，以这个为准\n")

    system = (
        "你是资深短视频编剧，写过大量跑量的口播稿与短剧脚本。"
        "你写的稿子要能直接交给摄影开拍：每句话都能念出来，每个画面都能拍出来。"
        "你只说中文，不说客套话，不做免责声明。"
    )

    user = f"""【任务】
参照下面「对标原文」的**结构与节奏**，为客户的选题写一版全新的短视频脚本。

【红线（违反即作废）】
1. 只仿结构、节奏、钩子类型，**禁止照搬原句**，输出必须是全新表达。
2. 严禁出现广告法极限词与功效承诺：{forbid_preview}{'、' + '、'.join(extra_forbid) if extra_forbid else ''}
3. 不要写「大家好」「今天给大家分享」「废话不多说」这类无效开场。
4. 不要输出任何解释、前言、后记，只要正文脚本。

【视频体裁】{g.label}（{g.desc}）
【结构骨架】按下面的段落与节奏写，段落之间要自然衔接：
{chr(10).join(struct_lines)}
【常用钩子套路（学它的逻辑，不要照抄句子）】
  {chr(10).join('  · ' + h for h in g.hooks)}
【体裁要点】
  {chr(10).join('  · ' + t for t in g.tips)}

【硬指标】
- 目标时长：{spec.seconds} 秒（语速约 {g.wps} 字/秒 → 全片口播控制在 {spec.target_chars} 字左右，
  严格上限 {int(spec.target_chars * 1.15)} 字，超出就是不合格；宁短勿长）
- 语气：{TONES.get(spec.tone, TONES['pro'])}
- 目标人群：{spec.audience or '（未指定，按产品常见人群写）'}
- 产品 / 品牌：{spec.product or '（未指定）'}
- 必须讲到的卖点：{'、'.join(spec.points) if spec.points else '（未指定，从原文推断）'}
- 必须出现的词：{'、'.join(spec.keywords) if spec.keywords else '（无）'}
- 额外禁用词：{'、'.join(extra_forbid) if extra_forbid else '（无）'}

【对标原文（只学结构与节奏，不许抄句）】
{source[:4000] if source.strip() else '（未提供对标原文，请完全按上面的体裁骨架与硬指标原创）'}

【原文拆解结果（供参考）】
{analysis.summary() if analysis else '（未提供原文，未拆解）'}
{real}
【输出格式】严格按下面的分节标记输出，一行一个分镜，不要增删分节：
【标题】3 个备选标题，用 | 分隔，每个 ≤ 20 字
【钩子】开头 3 秒要念的那句话（≤ 25 字，要有悬念 / 冲突 / 反常识）
【分镜】每行一条，格式严格为：时间轴｜画面：……｜口播：……
       时间轴从 0 秒开始首尾相接，共 4~8 条；画面写清镜头与动作，口播写清要念的原话
【结尾】一句行动号召（按体裁选：引导评论 / 收藏 / 下单，只给一个动作）
【话题】5 个话题标签，空格分隔，形如 #xxx #xxx
"""
    return system, user


def _split_lines(text: str) -> list[str]:
    """一行一个，兼容中英文逗号 / 分号，去重保序。"""
    out: list[str] = []
    for s in re.split(r"[\r\n,，;；]+", str(text or "")):
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out


# ======================================================================
#  六、解析模型输出
# ======================================================================

# 分节标记：允许 【】/ ** / 冒号 等常见变体
_SEC_RE = re.compile(
    r"^\s*(?:[#*\-]*\s*)【?\s*(标题|钩子|开头|分镜|脚本|正文|结尾|行动号召|话题|标签)"
    r"\s*】?\s*[:：]?\s*(.*)$")


def parse_script(text: str, spec: RewriteSpec) -> dict:
    """把模型输出切成结构化字段。

    为什么不要求 JSON：长文本 + 中文标点时模型很容易吐出坏 JSON（缺引号、
    多逗号），一坏就得整版重跑。分节标记容错空间大得多，缺哪一节就补哪一节，
    不至于整版作废。
    """
    out: dict = {"标题": [], "钩子": "", "分镜": [], "结尾": "", "话题": []}
    cur = ""
    buf: list[str] = []

    def flush():
        if not cur:
            return
        body = "\n".join(buf).strip()
        if cur == "标题":
            out["标题"] = [t.strip(" |·•") for t in re.split(r"[|｜/／]", body)
                           if t.strip(" |·•")]
        elif cur == "分镜":
            out["分镜"] = _parse_shots(body)
        elif cur == "话题":
            out["话题"] = re.findall(r"#?[\u4e00-\u9fa5A-Za-z0-9_]{2,12}", body)
        else:
            out[cur] = body.split("\n")[0].strip() or body

    for line in (text or "").splitlines():
        m = _SEC_RE.match(line)
        if m:
            flush()
            key = m.group(1)
            cur = {"开头": "钩子", "脚本": "分镜", "正文": "分镜",
                   "行动号召": "结尾", "标签": "话题"}.get(key, key)
            buf = [m.group(2)] if m.group(2).strip() else []
        elif cur:
            buf.append(line)
    flush()

    # 兜底：模型没按格式来，就把它整段当口播，按句切分镜。
    # 这时必须在界面上说明「格式未识别」，不能假装解析成功。
    if not out["分镜"] and (text or "").strip():
        sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
        out["分镜"] = [{"时间轴": "-", "画面": "", "口播": s} for s in sents[:8]]
        if not out["钩子"] and sents:
            out["钩子"] = sents[0][:40]
        out["_fallback"] = True
    return out


def _parse_shots(body: str) -> list[dict]:
    """解析分镜区：每行「时间轴｜画面：…｜口播：…」。容错到只剩口播也能用。"""
    shots: list[dict] = []
    for line in body.splitlines():
        line = line.strip(" -•·|｜")
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"[|｜]", line) if p.strip()]
        timeline, picture, spoken = "", "", ""
        if len(parts) >= 2:
            if not any(parts[0].startswith(k) for k in ("时间", "画面", "口播")):
                timeline = parts[0]
                parts = parts[1:]
            for p in parts:
                if p.startswith("画面"):
                    picture = p.split("：", 1)[-1].split(":", 1)[-1].strip()
                elif p.startswith("口播") or p.startswith("台词"):
                    spoken = p.split("：", 1)[-1].split(":", 1)[-1].strip()
                elif not spoken:
                    spoken = p
        else:
            spoken = parts[0] if parts else ""
            spoken = re.sub(r"^(口播|台词)\s*[:：]\s*", "", spoken)
        if not (spoken or picture):
            continue
        shots.append({"时间轴": timeline or "-", "画面": picture, "口播": spoken})
    return shots


# ======================================================================
#  七、质检评分
# ======================================================================

def claimed_seconds(shots: list[dict]) -> float:
    """从分镜时间轴里读出模型自己声称的总时长（秒）。

    为什么要读它：模型经常把时间轴写得刚好等于目标时长，可正文却写了 1.4 倍的
    字数 —— 时间轴是「它以为的节奏」，字数才是「真正要念多久」。两个对不上时
    必须提醒，否则客户按时间轴备画面，拍出来发现口播念不完。
    """
    best = 0.0
    for s in shots or []:
        tl = str(s.get("时间轴") or "").replace("秒", "").replace("s", "")
        nums = re.findall(r"\d+(?:\.\d+)?", tl)
        for n in nums:
            try:
                best = max(best, float(n))
            except ValueError:
                continue
    return best


def score_draft(draft: ScriptDraft, spec: RewriteSpec) -> tuple[int, list[str]]:
    """给一版稿子打分（满分 100），并给出可读的扣分/加分理由。

    评分是给客户挑版本用的，不是给模型评优：
    「结构 / 钩子 / 时长 / 合规 / 卖点」五项都能解释得清，客户才知道该改哪里。
    """
    detail: list[str] = []
    score = 0

    # 1) 结构完整度（30）
    have_shots = len(draft.shots) >= 3
    need = max(3, min(len(spec.genre_spec.structure), 5))
    if have_shots and draft.hook and draft.cta:
        score += 30
        detail.append(f"结构完整（{len(draft.shots)} 个分镜 + 钩子 + 结尾）　30/30")
    else:
        miss = []
        if not have_shots:
            miss.append("分镜不足 3 条")
        if not draft.hook:
            miss.append("缺钩子")
        if not draft.cta:
            miss.append("缺结尾引导")
        score += 10
        detail.append("结构缺失：" + "、".join(miss) + "　10/30　建议重跑或手工补")
    if len(draft.shots) >= need:
        detail.append(f"分镜数达到该体裁要求（≥{need} 条）")

    # 2) 钩子强度（25）
    hook = draft.hook or ""
    if hook:
        strong = (len(hook) <= 30
                  and (any(k in hook for k in _HOOK_MARKS)
                       or any(k in hook for k in _HOOK_PAIN_MARKS)))
        if strong:
            score += 25
            detail.append("钩子短且带悬念/冲突　25/25")
        elif len(hook) <= 30:
            score += 15
            detail.append("钩子长度合格但冲击力一般（建议加数字、反常识或提问）　15/25")
        else:
            score += 8
            detail.append(f"钩子偏长（{len(hook)} 字），前 3 秒念不完　8/25")
    else:
        detail.append("没有钩子　0/25")

    # 3) 时长匹配（20）
    target = max(1, spec.seconds)
    if draft.est_seconds > 0:
        diff = abs(draft.est_seconds - target) / target
        if diff <= 0.25:
            score += 20
            detail.append(f"时长贴合目标（{draft.est_seconds:.0f} 秒 / 目标 {target} 秒）　20/20")
        elif diff <= 0.5:
            score += 12
            detail.append(f"时长偏差 {diff * 100:.0f}%（{draft.est_seconds:.0f} 秒），"
                          f"拍的时候要删减或拖节奏　12/20")
        else:
            score += 5
            detail.append(f"时长偏差过大（{draft.est_seconds:.0f} 秒 / 目标 {target} 秒）　5/20")
    else:
        detail.append("无口播正文，无法估时长　0/20")

    # 4) 合规（15）
    if not draft.violations:
        score += 15
        detail.append("合规通过：无极限词 / 功效承诺 / 站外引流　15/15")
    else:
        words = "、".join(h["词"] for h in draft.violations[:6])
        detail.append(f"命中 {len(draft.violations)} 个违禁词（{words}）　0/15　"
                      "发布前必须改掉，广告法最高可罚 20 万")

    # 5) 卖点 / 关键词覆盖（10）
    keys = [k for k in (spec.keywords or []) if k.strip()]
    pts = [p for p in (spec.points or []) if p.strip()]
    full = draft.spoken
    if keys or pts:
        hit_k = sum(1 for k in keys if k in full)
        hit_p = sum(1 for p in pts if _point_hit(p, full))
        total = len(keys) + len(pts)
        hit = hit_k + hit_p
        got = int(round(10 * hit / total)) if total else 10
        score += got
        detail.append(f"卖点 / 必含词覆盖 {hit}/{total}　{got}/10")
    else:
        score += 10
        detail.append("未指定卖点与必含词，本项不扣分　10/10")

    return max(0, min(100, score)), detail


def _point_hit(point: str, spoken: str) -> bool:
    """这条卖点算不算讲到？

    以前只做「整段命中」，结果客户写「汇报工作说结果」这种短语时，模型明明
    讲到了（"汇报只说结果"）也被判 0 分，白扣 10 分还让人以为没写到位点。
    改成两级判定：
      1. 核心词整段出现（老逻辑，最准）
      2. 核心词的相邻二字组合命中两个以上 —— 说明确实围绕这个词在讲，
         而不是碰巧撞上一个字。只中一个字不算，避免"三档"这种泛词刷分。
    """
    chunks = re.findall(r"[\u4e00-\u9fa5]{2,}", point or "")
    if not chunks:
        return bool((point or "").strip()) and (point or "").strip() in spoken
    core = max(chunks, key=len)
    if core in spoken:
        return True
    grams = [core[i:i + 2] for i in range(len(core) - 1)]
    return sum(1 for g in grams if g in spoken) >= 2


# ======================================================================
#  八、执行仿写
# ======================================================================

def rewrite(source: str, spec: RewriteSpec, api_keys: dict[str, str] | None = None,
            models: dict[str, str] | None = None,
            timeout: float = 150.0,
            progress: Callable[[int, int, str], None] | None = None,
            should_stop: Callable[[], bool] | None = None,
            segments: list | None = None) -> RewriteResult:
    """按 spec 生成若干版仿写稿。

    串行生成：一次仿写通常只要 1~3 版，并发省下的时间有限，
    却会显著抬高 Kimi 免费额度的 429 概率（GEO 那边的实测教训）。

    segments：原文的时间轴片段（从视频语音转写来的才有）。传进来之后
    「实测口播时长 / 真实语速 / 节奏」会一起进提示词，模型才知道该写到多长、
    钩子该塞在第几秒 —— 这几个数是纯文本永远猜不出来的。
    """
    result = RewriteResult(spec=spec, engine=spec.engine, source=source or "",
                           started_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    g = spec.genre_spec
    result.analysis = analyze_source(source, g.wps, segments) if source else None

    eng = gm.ENGINE_SPECS.get(spec.engine)
    if eng is None:
        result.errors.append(f"未知引擎：{spec.engine}")
        return result
    if eng.kind != "api":
        result.errors.append(f"{eng.label} 通道当前版本未开放，请先用 DeepSeek / Kimi")
        return result
    if not (api_keys or {}).get(spec.engine, "").strip():
        result.errors.append(f"未填写 {eng.label} 的接口 Key，一个字也生成不出来")
        return result

    versions = max(1, min(int(spec.versions or 1), 5))
    base_temp = TEMPERATURES.get(spec.temperature, 1.0)

    def say(done, text):
        if progress:
            try:
                progress(done, versions, text)
            except Exception:                              # noqa: BLE001
                pass

    for i in range(1, versions + 1):
        if should_stop and should_stop():
            result.errors.append("已手动停止，本次为不完整结果")
            break
        say(i - 1, f"正在生成第 {i} 版…")
        # 每版温度小幅抖动：同一温度连出三版，后两版往往是第一版的复述
        temp = round(min(1.5, max(0.3, base_temp + 0.1 * (i - 1))), 2)
        system, user = build_prompt(spec, source, result.analysis)
        ok, text, err = gm.chat_completion(
            eng, system, user, api_keys=api_keys, models=models,
            temperature=temp, timeout=timeout)

        draft = ScriptDraft(index=i, genre=spec.genre, genre_label=g.label,
                            seconds=spec.seconds, tone=spec.tone,
                            created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                            source_ref=(source or "")[:40])
        if not ok:
            draft.error = err or "生成失败"
            result.items.append(draft)
            result.errors.append(f"第 {i} 版生成失败：{draft.error}")
            continue

        parsed = parse_script(text, spec)
        draft.raw = text
        draft.title_options = parsed.get("标题") or []
        draft.hook = (parsed.get("钩子") or "").strip()
        draft.shots = parsed.get("分镜") or []
        draft.cta = (parsed.get("结尾") or "").strip()
        draft.hashtags = parsed.get("话题") or []
        if parsed.get("_fallback"):
            draft.score_detail.append(
                "模型未按分镜格式输出，已按句子兜底切分 —— 拍摄前请人工核对画面栏")
        spoken = draft.spoken
        draft.chars = len(re.sub(r"\s+", "", spoken))
        draft.est_seconds = (draft.chars / g.wps) if g.wps > 0 else 0.0
        draft.violations = check_compliance(spoken, spec.forbidden_extra)
        draft.score, detail = score_draft(draft, spec)
        draft.score_detail = (draft.score_detail or []) + detail
        # 模型标的时间轴 vs 按字数估的真实时长：对不上要说，不然客户按时间轴备画面会翻车
        claimed = claimed_seconds(draft.shots)
        if claimed > 0 and abs(claimed - draft.est_seconds) / max(claimed, 1) > 0.3:
            draft.score_detail.append(
                f"分镜时间轴写到 {claimed:.0f} 秒，但按字数 ÷ 语速要念 "
                f"{draft.est_seconds:.0f} 秒 —— 以试录为准，偏长就删一个卖点，"
                "别靠念快点硬塞")
        result.items.append(draft)
        say(i, f"第 {i} 版完成（{draft.score} 分）")

    return result


# ======================================================================
#  九、导出与历史
# ======================================================================

def history_dir() -> Path:
    """仿写历史目录：%APPDATA%/TrafficCatcher/script_history/"""
    try:
        from . import branding
        base = branding.app_data_dir()
    except Exception:                                      # noqa: BLE001
        import os
        base = Path(os.environ.get("APPDATA") or str(Path.home())) / "TrafficCatcher"
    d = base / "script_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_history(result: RewriteResult) -> Path:
    """存一份仿写记录（给客户回头找稿子用，只存本机）。"""
    d = history_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|]', "_", (result.spec.product or "稿"))[:16]
    path = d / f"{stamp}_{safe}.json"
    i = 2
    while path.exists():
        path = d / f"{stamp}_{safe}_{i}.json"
        i += 1
    payload = {
        "started_at": result.started_at,
        "engine": result.engine,
        "spec": {
            "genre": result.spec.genre, "genre_label": result.spec.genre_spec.label,
            "seconds": result.spec.seconds, "tone": result.spec.tone,
            "audience": result.spec.audience, "product": result.spec.product,
            "points": result.spec.points, "keywords": result.spec.keywords,
            "versions": result.spec.versions,
        },
        "source": result.source[:500],
        "items": [
            {"index": d.index, "score": d.score, "chars": d.chars,
             "est_seconds": round(d.est_seconds, 1),
             "titles": d.title_options, "hook": d.hook, "shots": d.shots,
             "cta": d.cta, "hashtags": d.hashtags,
             "violations": d.violations, "error": d.error}
            for d in result.items
        ],
        "errors": result.errors,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_history(limit: int = 30) -> list[dict]:
    """读取历史仿写记录（最新的在后）。"""
    out: list[dict] = []
    try:
        files = sorted(history_dir().glob("*.json"), key=lambda p: p.name)[-max(1, limit):]
    except OSError:
        return out
    for p in files:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def to_markdown(draft: ScriptDraft, spec: RewriteSpec) -> str:
    """一版稿子的 Markdown 全文（可直接贴给剪辑或客户）。"""
    lines = [f"# {draft.title_options[0] if draft.title_options else f'第 {draft.index} 版'}",
             "",
             f"- 体裁：{draft.genre_label}　- 目标时长：{spec.seconds} 秒　"
             f"- 语气：{TONES.get(spec.tone, '')[:4]}　- 质检：{draft.score} 分",
             f"- 口播字数：{draft.chars} 字（约 {draft.est_seconds:.0f} 秒）",
             ""]
    if draft.title_options:
        lines.append("## 标题备选")
        lines += [f"{i}. {t}" for i, t in enumerate(draft.title_options, 1)]
        lines.append("")
    lines.append("## 分镜脚本")
    lines.append("| 时间轴 | 画面 | 口播 |")
    lines.append("| --- | --- | --- |")
    for s in draft.shots:
        lines.append(f"| {s.get('时间轴', '-')} | {s.get('画面', '')} | {s.get('口播', '')} |")
    lines.append("")
    if draft.cta:
        lines += ["## 结尾引导", draft.cta, ""]
    if draft.hashtags:
        lines += ["## 话题标签",
                  " ".join(("#" + h.lstrip("#")) if not h.startswith("#") else h
                           for h in draft.hashtags), ""]
    if draft.violations:
        lines.append("## 合规提醒（发布前必须改）")
        for v in draft.violations:
            lines.append(f"- 「{v['词']}」属{v['类别']}，建议改为：{v['建议']}")
        lines.append("")
    if draft.score_detail:
        lines += ["## 质检明细"] + [f"- {t}" for t in draft.score_detail]
    return "\n".join(lines)


def export_excel(result: RewriteResult, path: str | Path) -> Path:
    """导出 Excel：概览 / 文案总览 / 分镜脚本 / 质检明细 四张表。"""
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

    spec = result.spec
    g = spec.genre_spec
    # 1) 概览
    ws = wb.active
    ws.title = "概览"
    eng = gm.ENGINE_SPECS.get(result.engine)
    overview = [
        ["产品 / 品牌", spec.product or "-"],
        ["体裁", f"{g.label}（{g.desc}）"],
        ["目标时长", f"{spec.seconds} 秒（语速 {g.wps} 字/秒 → 约 {spec.target_chars} 字）"],
        ["语气", TONES.get(spec.tone, "-")],
        ["目标人群", spec.audience or "-"],
        ["必须讲到的卖点", "、".join(spec.points) or "-"],
        ["必须出现的词", "、".join(spec.keywords) or "-"],
        ["生成引擎", eng.label if eng else result.engine],
        ["生成时间", result.started_at],
        ["成功 / 失败", f"{result.ok_count} 版成功 / {result.failed_count} 版失败"],
        ["", ""],
        ["口径说明", "仿的是结构与节奏，不是措辞；输出为全新表达，发布前请人工过一遍"],
        ["口径说明", "「失败」= 没生成出来（限流 / Key / 网络），不等于「没有好文案」，"
                    "请勿据此判断选题不行"],
        ["口径说明", "预估时长按字数 ÷ 语速估算，实际以试录为准"],
    ]
    if result.analysis:
        overview += [["", ""], ["原文拆解", result.analysis.summary()]]
    write_sheet(ws, ["项目", "内容"], overview)

    # 2) 文案总览
    ws2 = wb.create_sheet("文案总览")
    keys = ["版本", "体裁", "评分", "字数", "预估时长", "标题", "钩子", "违规词", "状态"]
    write_sheet(ws2, keys, [[d.to_row().get(k, "") for k in keys] for d in result.items])

    # 3) 分镜脚本
    ws3 = wb.create_sheet("分镜脚本")
    rows = []
    for d in result.items:
        if not d.ok:
            continue
        for s in d.shots:
            rows.append([f"第 {d.index} 版", s.get("时间轴", "-"),
                         s.get("画面", ""), s.get("口播", "")])
    write_sheet(ws3, ["版本", "时间轴", "画面", "口播"],
                rows or [["（没有可导出的分镜）", "", "", ""]])

    # 4) 质检明细
    ws4 = wb.create_sheet("质检明细")
    qc = []
    for d in result.items:
        if d.error:
            qc.append([f"第 {d.index} 版", "失败", d.error])
            continue
        for t in d.score_detail:
            qc.append([f"第 {d.index} 版", f"{d.score} 分", t])
        for v in d.violations:
            qc.append([f"第 {d.index} 版", "违规词",
                       f"「{v['词']}」属{v['类别']}，建议改为：{v['建议']}"])
    write_sheet(ws4, ["版本", "项目", "说明"], qc or [["-", "-", "（无）"]])

    if result.errors:
        ws5 = wb.create_sheet("异常")
        write_sheet(ws5, ["异常"], [[e] for e in result.errors[:200]])

    wb.save(path)
    return path


def export_markdown(result: RewriteResult, path: str | Path) -> Path:
    """把所有版本写成一个 Markdown 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [f"# 短视频文案仿写 · {result.spec.product or '未命名'}",
             f"\n生成时间：{result.started_at}　体裁：{result.spec.genre_spec.label}"
             f"　目标时长：{result.spec.seconds} 秒\n"]
    for d in result.items:
        if d.error:
            parts.append(f"\n## 第 {d.index} 版\n\n生成失败：{d.error}\n")
            continue
        parts.append("\n---\n\n" + to_markdown(d, result.spec))
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def estimate_cost(spec: RewriteSpec) -> dict:
    """这次仿写要花多少次调用 / 大概多久（只给用户估时间，不是计费依据）。"""
    n = max(1, min(int(spec.versions or 1), 5))
    per = {"deepseek": 18.0, "kimi": 30.0}.get(spec.engine, 20.0)
    return {"calls": n, "seconds": n * per}
