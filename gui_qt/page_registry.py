"""页面注册表：软件里所有页面的唯一真相源。

★ 新增/下线页面只改这个文件，然后跑一条命令同步所有清单：

    C:/Python310/python.exe tools/gen_pages.py

  生成器会把下面这些数据渲染到各自文件的「标记块」里：
    · gui_qt/main_window.py  → 页面类 import / NAV_GROUPS / FREE_NAV_GROUPS / _PAGE_CLASS
    · gui_qt/icons.py        → _NAV_INNER
    · gui_qt/widgets/guide.py→ GUIDE_ORDER
    · gui_qt/widgets/command_palette.py → _ALIASES
    · gui_qt/pages/home_page.py → QUICK_ACTIONS / MORE_TOOLS / MAIN_FLOW
    · justoneapi_collector.spec → hiddenimports 的页面区块
    · tools/_validate_feature_sync.py → PAGE_FEATURE / NAV_NO_FEATURE
    · tools/_validate_ux.py  → EXPECTED

  随后跑 `python tools/_validate_page_registry.py` —— 它会核对所有清单与注册表
  一致；漏同步、手改生成块、别名不足 3 个、功能位没登记，全部当场报错。
  发版链 release_all.py 已挂上这道门禁，漂移的清单无法上线。

为什么不直接从注册表 import、让清单运行时派生？
  因为 6 个老门禁是用 AST / 字符串切源码字面量来读 NAV_GROUPS、_PAGE_CLASS
  这些清单的（改一行会让它们全部失效）。保留字面量、由生成器维护，是唯一
  既能「一处定义」又不破坏现有门禁的做法。

字段说明见 PageSpec。此模块是纯数据、零 Qt 依赖，门禁可直接 import。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    """一页的全部注册属性。新增页面时照着相邻条目填一行即可。"""

    name: str
    """页面中文名，同时是导航/教程/别名/空态清单的键，全局唯一。"""

    cls: str
    """页面类名，如 XhsNotesPage；必须能从 module 里 import。"""

    module: str
    """页面模块文件名（不含 gui_qt.pages. 前缀），如 xhs_notes_page。"""

    group: str
    """完整版导航分组：开始 / 常用 / 分析 / 更多工具 / 设置。"""

    feature: str | None
    """功能位 key（对应 licensing.FEATURES）；None = 不计费入口页。"""

    aliases: tuple[str, ...]
    """Ctrl+K 搜索别名，至少 3 个，第一个通常是拼音首字母。"""

    free_group: str | None = None
    """免费版导航分组；None = 免费版不出现该页。"""

    icon: str | None = None
    """导航图标的 SVG inner 路径；None = 不登记，回退到设置图标。"""

    icon_note: str = ""
    """图标语义注释（渲染到 _NAV_INNER 对应条目的上方）。"""

    quick: tuple[str, str] | None = None
    """首页「常用功能」卡片：(一句话说明, 角标)。None = 不上首页。"""

    guide_no: int = 0
    """使用教程顺序号；0 = 不进教程。有条目请给正整数。"""

    quick_no: int = 0
    """首页「常用功能」卡片顺序号；0 = 不上首页（与 quick 文案配套）。"""

    more_no: int = 0
    """首页「其它工具」顺序号；0 = 不进。"""

    ux_no: int = 0
    """UX 门禁 EXPECTED 顺序号；0 = 不进（工作台/短剧解析历史上不进）。"""

    note: str = ""
    """渲染到 NAV_GROUPS 该行上方的注释（解释排序/上下游关系）。"""

    spec_extra: tuple[str, ...] = ()
    """该页附带的、函数体内 import 需防裁的模块（如 drama_api）。"""


PAGES: tuple[PageSpec, ...] = (
    PageSpec(
        name="工作台", cls="HomePage", module="home_page",
        group="开始", feature=None,
        aliases=("gzt", "首页", "home", "总览", "额度", "授权", "概览", "看板"),
        icon='<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/>'
             '<rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
             '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>'
             '<rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
        icon_note="四格仪表盘（总览 / 首页）",
        guide_no=1, quick_no=0, more_no=0, ux_no=0,
    ),
    PageSpec(
        name="选题挖掘", cls="TopicPage", module="topic_page",
        group="常用", feature="analyze",
        aliases=("xtwj", "选题", "挖掘", "爆款", "低粉高赞", "痛点", "爆粉比",
                 "找选题", "抄结构"),
        icon='<path d="M9 18h6"/><path d="M10 21h4"/>'
             '<path d="M12 3a6 6 0 014 10.5c-.8.7-1.2 1.4-1.2 2.5H9.2'
             'c0-1.1-.4-1.8-1.2-2.5A6 6 0 0112 3z"/>',
        quick=("找低粉高赞的爆款，今天发什么看它", ""),
        icon_note="灯泡",
        guide_no=2, quick_no=1, more_no=0, ux_no=4,
    ),
    PageSpec(
        name="关键词抓取", cls="KeywordPage", module="keyword_page",
        group="常用", feature="keyword",
        aliases=("gjcq", "关键词", "抓取", "搜索", "搜词", "找视频", "竞品词",
                 "长尾词"),
        icon='<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>'
             '<path d="M7.5 10.5h6"/>',
        quick=("一个词，把这个赛道数据最好的视频抓全", ""),
        icon_note="放大镜 + 关键词",
        guide_no=3, quick_no=2, more_no=0, ux_no=1,
    ),
    PageSpec(
        name="文案仿写", cls="ScriptPage", module="script_page",
        group="常用", feature="script",
        aliases=("wafx", "文案", "仿写", "改写", "脚本", "分镜", "提词",
                 "ai写作", "伪原创", "口播稿", "标题"),
        icon='<path d="M14 3l6 6-9.5 9.5H5v-5.5z"/><path d="M13 4l6 6"/>'
             '<path d="M5 20h14"/>',
        quick=("粘链接就能扒出文案，按体裁仿写成能拍的分镜", "最常用"),
        icon_note="纸笔（拆结构后重写一版）",
        guide_no=4, quick_no=3, more_no=0, ux_no=10,
    ),
    PageSpec(
        name="视频下载", cls="VideoDownloadPage", module="video_download_page",
        group="素材与短剧", feature="download",
        aliases=("spxz", "下载", "视频", "去水印", "保存", "素材", "搬运"),
        free_group="下载",
        icon='<rect x="3" y="5" width="13" height="14" rx="2"/>'
             '<path d="M16 9l5-3v12l-5-3z"/>',
        quick=("批量下载无水印视频，留着做素材", ""),
        icon_note="显示器 + 播放三角",
        guide_no=5, quick_no=4, more_no=0, ux_no=2,
    ),
    PageSpec(
        name="视频号解析", cls="WechatChannelsPage", module="wechat_channels_page",
        group="素材与短剧", feature=None,
        aliases=("sphjx", "视频号", "微信视频号", "weixin", "channels",
                 "sph", "视频号链接", "解析视频号"),
        icon='<path d="M21 11.5a8 8 0 01-8 8 8.7 8.7 0 01-3-.6L4 21l1.3-4.2A8 8 0 1121 11.5z"/>'
             '<path d="M10 8.5l4 3-4 3z"/>',
        quick=("粘视频号链接，一键出作者和直链", ""),
        icon_note="对话气泡 + 播放三角（微信生态里的短视频）",
        guide_no=6, quick_no=5, more_no=0, ux_no=16,
    ),
    PageSpec(
        name="短剧解析", cls="DramaParsePage", module="drama_parse_page",
        group="素材与短剧", feature="drama_parse",
        aliases=("djjx", "拆解", "分镜解析", "镜头解析", "剧本拆解",
                 "分析短剧", "拉片", "剧本题"),
        note="短剧解析 → 短剧成片是一对上下游入口",
        spec_extra=("justoneapi_collector.drama_api",
                    "justoneapi_collector.drama_public_rank"),
        guide_no=0, quick_no=0, more_no=0, ux_no=0,
    ),
    PageSpec(
        name="短剧成片", cls="DramaPage", module="drama_page",
        group="素材与短剧", feature="drama",
        aliases=("djcp", "短剧", "成片", "剪辑", "切片", "混剪", "竖屏",
                 "自动剪辑", "剧集", "解说", "卡点"),
        note="排在「视频下载」之后：下载拿素材 → 剪辑出成片",
        spec_extra=("justoneapi_collector.drama_bridge",
                    "justoneapi_collector.drama_bundle"),
        guide_no=9, quick_no=0, more_no=0, ux_no=14,
    ),
    PageSpec(
        name="公众号运营", cls="WechatMpPage", module="wechat_mp_page",
        group="平台内容", feature="wechat_mp",
        aliases=("gzhyy", "公众号", "微信公众", "wechat mp", "mp.weixin",
                 "原创文章", "历史文章", "阅读数", "搜一搜", "gh_"),
        icon='<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/>'
             '<path d="M9 12h7"/><path d="M9 16h5"/><path d="M3 17l3-3 2 2 4-5"/>',
        quick=("搜公众号、拉历史文章，查阅读点赞和选题", "新"),
        icon_note="文档 + 阅读数折线（文章与数据一体）",
        guide_no=7, quick_no=8, more_no=0, ux_no=17,
    ),
    PageSpec(
        name="小红书笔记", cls="XhsNotesPage", module="xhs_notes_page",
        group="平台内容", feature="xhs_notes",
        aliases=("xhsbj", "小红书", "rednote", "xiaohongshu", "xhs",
                 "笔记提取", "笔记正文", "种草笔记", "全文", "联想词"),
        icon='<rect x="4" y="3" width="16" height="18" rx="2"/>'
             '<path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/>',
        quick=("搜笔记、读全文正文，导出 Markdown 做选题", "新"),
        icon_note="便签 + 文字行（内容提取）",
        guide_no=8, quick_no=9, more_no=0, ux_no=18,
    ),
    PageSpec(
        name="竞品体检", cls="CompetitorPage", module="competitor_page",
        group="分析", feature="analyze",
        aliases=("jptj", "竞品", "体检", "对标", "同行", "对比", "看对手"),
        icon='<path d="M4 20V11"/><path d="M10 20V5"/><path d="M16 20v-7"/>'
             '<path d="M3 20h18"/>',
        quick=("看同行最近在发什么、哪条跑起来了", ""),
        icon_note="柱状图",
        guide_no=10, quick_no=6, more_no=0, ux_no=5,
    ),
    PageSpec(
        name="评论区截流", cls="CommentPage", module="comment_page",
        group="分析", feature="comment",
        aliases=("plqjl", "评论", "截流", "挖客户", "意向", "问价", "私信",
                 "求链接", "评论区", "留资"),
        icon='<path d="M21 12a8 8 0 01-8 8H8l-5 3 1.2-4.2A8 8 0 1121 12z"/>'
             '<circle cx="10" cy="11" r="1.6"/><path d="M7.2 15.4a3.4 3.4 0 005.6 0"/>',
        quick=("挖同行评论区里正在问怎么买的人", ""),
        spec_extra=("justoneapi_collector.comment_mining",),
        icon_note="对话气泡 + 里面的小人（挖意向客户）",
        guide_no=11, quick_no=7, more_no=0, ux_no=6,
    ),
    PageSpec(
        name="复盘监控", cls="WatchPage", module="watch_page",
        group="分析", feature="analyze",
        aliases=("fpjk", "复盘", "监控", "涨粉", "掉粉", "粉丝曲线", "跟踪账号"),
        icon='<path d="M3 16l5-6 4 3 5-8 4 5"/><path d="M3 20h18"/>',
        icon_note="上升折线",
        guide_no=12, quick_no=0, more_no=3, ux_no=8,
    ),
    PageSpec(
        name="数据趋势", cls="TrendPage", module="trend_page",
        group="分析", feature=None,
        aliases=("sjqs", "趋势", "曲线", "历史", "涨跌", "看过的数据", "记录"),
        icon='<path d="M4 19V5"/><path d="M4 19h16"/>'
             '<path d="M7 15.5l3.5-4 3 2.5L20 7"/>'
             '<circle cx="7" cy="15.5" r="1.4"/><circle cx="20" cy="7" r="1.4"/>',
        note="数据趋势不进使用教程（本地历史查看页，无独立操作）",
        icon_note="折线向上 + 数据点（看涨跌）",
        guide_no=0, quick_no=0, more_no=4, ux_no=12,
    ),
    PageSpec(
        name="直播监控", cls="LivePage", module="live_page",
        group="更多工具", feature="live",
        aliases=("zbjk", "直播", "录屏", "录制", "直播间", "挂机", "录直播"),
        free_group="工具",
        icon='<circle cx="12" cy="12" r="2.5"/><path d="M7 7a7 7 0 000 10"/>'
             '<path d="M17 7a7 7 0 010 10"/><path d="M4 4a11 11 0 000 16"/>'
             '<path d="M20 4a11 11 0 010 16"/>',
        icon_note="电台波纹",
        guide_no=13, quick_no=0, more_no=1, ux_no=3,
    ),
    PageSpec(
        name="GEO 监测", cls="GeoPage", module="geo_page",
        group="更多工具", feature="geo",
        aliases=("geo", "geo监测", "ai搜索", "deepseek", "豆包", "kimi",
                 "ai提及", "可见度", "ai推荐", "品牌监测"),
        icon='<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>'
             '<circle cx="12" cy="12" r="1.4"/><path d="M12 12l6-4"/>',
        icon_note="雷达屏幕 + 中心信号点（品牌在 AI 里的可见度）",
        guide_no=14, quick_no=0, more_no=2, ux_no=7,
    ),
    PageSpec(
        name="报告", cls="ReportPage", module="report_page",
        group="更多工具", feature="report",
        aliases=("bg", "报告", "周报", "月报", "汇报", "总结", "excel报告"),
        icon='<path d="M7 3h8l4 4v14H7z"/><path d="M15 3v4h4"/>'
             '<path d="M10 12h6"/><path d="M10 16h6"/>',
        icon_note="文档",
        guide_no=15, quick_no=0, more_no=5, ux_no=9,
    ),
    PageSpec(
        name="音视频转文本", cls="TranscribePage", module="transcribe_page",
        group="更多工具", feature="transcribe",
        aliases=("ypzzwb", "转写", "转录", "字幕", "语音转文字", "whisper",
                 "识别", "模型", "显卡"),
        icon='<rect x="3" y="6" width="18" height="11" rx="2"/>'
             '<path d="M7 10h3"/><path d="M14 10h3"/><path d="M7 13.5h10"/>',
        icon_note="字幕框",
        guide_no=16, quick_no=0, more_no=6, ux_no=11,
    ),
    PageSpec(
        name="微信聊天整理", cls="WechatChatPage", module="wechat_chat_page",
        group="更多工具", feature="wechat_chat",
        aliases=("wxlc", "微信", "聊天记录", "合并转发", "聊天归档",
                 "微信流", "wechat", "obsidian", "转笔记", "聊天导出"),
        spec_extra=("justoneapi_collector.wechat_archive",),
        guide_no=17, quick_no=0, more_no=0, ux_no=15,
    ),
    PageSpec(
        name="设置", cls="SettingsPage", module="settings_page",
        group="设置", feature=None,
        aliases=("sz", "设置", "授权码", "激活", "token", "密钥", "更新", "版本",
                 "登录", "反馈", "日志"),
        free_group="设置",
        icon='<circle cx="12" cy="12" r="3"/><path d="M12 2v3"/><path d="M12 19v3"/>'
             '<path d="M2 12h3"/><path d="M19 12h3"/><path d="M4.5 4.5l2 2"/>'
             '<path d="M17.5 17.5l2 2"/><path d="M4.5 19.5l2-2"/>'
             '<path d="M17.5 4.5l2 2"/>',
        icon_note="齿轮（中心圆 + 辐条）",
        guide_no=18, quick_no=0, more_no=7, ux_no=13,
    ),
)

# ★ 已下线但功能位仍在已签发授权码里的历史页 —— 不给页面、不给入口，
#   只保留功能位兼容。对应 licensing.FEATURES 的 collect / batch。
HISTORICAL_FEATURES: tuple[str, ...] = ("collect", "batch")

# 不占功能位的入口页（与 feature_sync 的 NAV_NO_FEATURE 一致）。
# ★ 「设置」虽然也无功能位，但 feature_sync 本来就用 p != "设置" 单独排除，
#   这里刻意不列它 —— 保持与旧清单逐项一致，避免门禁语义漂移。
NAV_NO_FEATURE: tuple[str, ...] = tuple(
    p.name for p in PAGES
    if p.feature is None and p.group and p.name != "设置"
)

# ★ 展示顺序是历史形成的，与导航顺序不同 —— 顺序号存在 PageSpec 里（guide_no /
#   more_no / ux_no），这里只负责排序派生。新增页面填了号就自动进对应清单，
#   不再需要「记得去另一张表补一行」：
#   · 教程顺序里没有「短剧解析」（它靠教程链接跳转，不单列，guide_no=0）；
#   · 首页「其它工具」按运营动线排，含「数据趋势」（教程不列它）；
#   · UX 门禁 EXPECTED 历史上不含「工作台」「短剧解析」（ux_no=0）。
def _ordered(attr: str) -> tuple[str, ...]:
    items = [(getattr(p, attr), i, p.name) for i, p in enumerate(PAGES)
             if getattr(p, attr) > 0]
    items.sort()
    return tuple(n for _rank, _i, n in items)


GUIDE_ORDER: tuple[str, ...] = _ordered("guide_no")
HOME_MORE_ORDER: tuple[str, ...] = _ordered("more_no")
HOME_QUICK_ORDER: tuple[str, ...] = _ordered("quick_no")
UX_EXPECTED: tuple[str, ...] = _ordered("ux_no")

# 首页五步运营任务流：(序号, 动作名, 说明, 跳转目标页)。
# 这是跨页编排（同一个「文案仿写」页出现两步），不属于任何单页属性，故单独放。
FLOW_STEPS: tuple[tuple[str, str, str, str], ...] = (
    ("01", "找选题", "低粉高赞爆款", "选题挖掘"),
    ("02", "扒文案", "粘链接出口播稿", "文案仿写"),
    ("03", "写同款", "选体裁出分镜", "文案仿写"),
    ("04", "存素材", "原画质进本地", "视频下载"),
    ("05", "剪成片", "自动分集出片", "短剧成片"),
)


def by_name(name: str) -> PageSpec:
    for p in PAGES:
        if p.name == name:
            return p
    raise KeyError(f"页面注册表里没有「{name}」")


def real_pages() -> tuple[PageSpec, ...]:
    """只返回真正占导航位置的页（排除为流程复用的虚拟条目）。"""
    return tuple(p for p in PAGES if p.group)
