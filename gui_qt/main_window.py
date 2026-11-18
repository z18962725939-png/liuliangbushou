"""流量捕手主窗口：左侧分组导航 + 右侧堆叠内容区（替代原 Tkinter 顶部 10 tab）。"""
import traceback

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QScrollArea,
                               QStackedWidget, QVBoxLayout, QWidget)

from justoneapi_collector import branding
from gui_qt.guide_dialog import open_guide
from gui_qt.icons import (group_caret_icon, help_icon, logo_icon, nav_icon,
                          theme_toggle_icon)
from gui_qt.pages._base import AppContext
from gui_qt.pages.batch_page import BatchPage
from gui_qt.pages.collect_page import CollectPage
from gui_qt.pages.comment_page import CommentPage
from gui_qt.pages.competitor_page import CompetitorPage
from gui_qt.pages.drama_page import DramaPage
from gui_qt.pages.drama_parse_page import DramaParsePage
from gui_qt.pages.geo_page import GeoPage
from gui_qt.pages.home_page import HomePage
from gui_qt.pages.keyword_page import KeywordPage
from gui_qt.pages.live_page import LivePage
from gui_qt.pages.report_page import ReportPage
from gui_qt.pages.script_page import ScriptPage
from gui_qt.pages.settings_page import SettingsPage
from gui_qt.pages.topic_page import TopicPage
from gui_qt.pages.transcribe_page import TranscribePage
from gui_qt.pages.trend_page import TrendPage
from gui_qt.pages.video_download_page import VideoDownloadPage
from gui_qt.subwindow import SubWindow
from gui_qt.pages.watch_page import WatchPage
from gui_qt.theme import accent_color, nav_active_icon_color, nav_text_color
from gui_qt.widgets.command_palette import install_shortcut
from gui_qt.widgets.empty import attach_empty_hint, refresh_empty_hints
from gui_qt.widgets.guide import GuideBanner
from gui_qt.widgets.motion import fade_in, stagger

# ★ 导航顺序 = 使用频率（2026-10-14 体验重构）
# 改版前 14 个功能平铺，且默认停在「数据采集」——那是给开发者调接口用的页面
# （选分类 → 选平台 → 选接口 → 手填参数），客户打开第一眼看到一堆看不懂的
# 下拉框，第一印象就是"难"。现在：工作台打头，天天用的排前面，低频的收进
# 「更多工具」；功能一个没删，老客户照样找得到。
NAV_GROUPS = [
    ("开始", ["工作台"]),
    # 短剧成片排在「视频下载」之后：下载拿素材 → 剪辑出成片，是一条上下游链路
    ("常用", ["选题挖掘", "关键词抓取", "文案仿写", "视频下载", "短剧解析", "短剧成片"]),
    ("分析", ["竞品体检", "评论区截流", "复盘监控", "数据趋势"]),
    ("更多工具", ["数据采集", "批量采集", "直播监控", "GEO 监测", "报告",
                  "音视频转文本"]),
    ("设置", ["设置"]),
]

# ★ 免费版导航（2026-10-23）——只有三个入口，一个都不许加回来。
#   客户要的很明确：「只有视频解析和直播录制的功能工具」。所以：
#     · 「视频下载」放第一组并默认打开：这是客户装它的唯一理由；
#     · 「直播监控」单独一组：第二个真功能；
#     · 「设置」必须留：保存目录、抖音扫码登录、代理都在这里。没有它，
#       客户解析失败时无处可去（而失败的第一大原因就是没登录）。
#   不加「工作台」：那页的核心是授权 / 额度 / 登录三张卡，免费版没有
#   授权和额度，摆上去只剩两张看不懂的空卡。
#   也不加任何「更多工具」：付费页在免费版里点开只会弹「需要激活」，
#   那正是客户这次投诉的观感——一排点不动的按钮。
FREE_NAV_GROUPS = [
    ("下载", ["视频下载"]),
    ("工具", ["直播监控"]),
    ("设置", ["设置"]),
]

if branding.APP_IS_FREE:
    NAV_GROUPS = FREE_NAV_GROUPS

# ★ 侧栏分组折叠（2026-10-17 UI 诊断 P2）
# 改版前 16 个导航项全部平铺，每项视觉重量一样，找功能只能逐字读。
# 现在：天天用的常驻，低频的收进分组并默认折叠 —— 侧栏从「一张长清单」
# 变成「一张有重点的清单」。
# 两条硬约束：
#   1. **一个功能都不删**（项目既定契约）：折叠只是收起，不是隐藏；
#   2. **折叠必须配 Ctrl+K**：否则客户会认为功能被砍了。这两个是打包交付的，
#      不许只做一半。
# 「开始」和「设置」不参与折叠：前者只有一个入口，后者是客户遇到问题时
# 唯一会去找的落点，藏起来纯属给自己找麻烦。
_NAV_PINNED = ("开始", "设置")
# 默认收起的分组
_NAV_DEFAULT_COLLAPSED = ("分析", "更多工具")
_NAV_STATE_KEY = "nav_collapsed"

_PAGE_CLASS = {
    "工作台": HomePage,
    "数据采集": CollectPage,
    "关键词抓取": KeywordPage,
    "批量采集": BatchPage,
    "视频下载": VideoDownloadPage,
    "直播监控": LivePage,
    "选题挖掘": TopicPage,
    "竞品体检": CompetitorPage,
    "评论区截流": CommentPage,
    "GEO 监测": GeoPage,
    "复盘监控": WatchPage,
    "数据趋势": TrendPage,
    "报告": ReportPage,
    "文案仿写": ScriptPage,
    "短剧成片": DramaPage,
    "短剧解析": DramaParsePage,
    "音视频转文本": TranscribePage,
    "设置": SettingsPage,
}


# ----------------------------------------------------------------------
#  单页故障降级（2026-10-19）
#
#  ★ 为什么加这一段（客户现场报障）
#    一位授权已过期的客户一打开软件就是「启动失败」红框：
#        StatCard.set_value() takes from 2 to 4 positional arguments
#        but 5 were given
#    根因是旧版首页在「试用期 / 已过期」两个分支里给指标卡多传了一个参数，
#    而首页的 refresh() 在主窗口构造过程中就被调用 —— 一个页面的 TypeError
#    把**整个窗口**带走了，客户连设置页都进不去，只能重装。
#    该调用本身在新版已改成三参（v1.19.2 起），但「一个页面出错 = 全软件不可用」
#    这个结构性问题必须一并堵死：以后任何页面再抛异常，最多是那一页打不开，
#    其余功能照常可用，同时 traceback 落进日志，客户把日志发来即可定位。
# ----------------------------------------------------------------------

def _page_fault_log() -> str:
    """返回可读的日志路径（用于在占位页上告诉客户去哪找日志）。"""
    try:
        from justoneapi_collector.logger import log_file_path
        return str(log_file_path())
    except Exception:                                           # noqa: BLE001
        return ""


def _log_page_fault(name: str, where: str, exc: BaseException) -> None:
    """把页面故障写进运行日志（打包后没有控制台，日志是唯一线索）。"""
    try:
        from justoneapi_collector.logger import get_logger
        get_logger().error(
            "页面「%s」%s失败，已降级为占位页：%r\n%s",
            name, where, exc, traceback.format_exc())
    except Exception:                                           # noqa: BLE001
        pass


def _fault_page(name: str, where: str) -> QWidget:
    """构造失败的页面的替身：说明发生了什么、日志在哪，不阻断其它功能。"""
    p = QFrame()
    p.setObjectName("Panel")
    p.setMinimumSize(520, 320)
    lay = QVBoxLayout(p)
    lay.setContentsMargins(28, 28, 28, 28)
    lay.setSpacing(10)
    lay.addStretch(1)

    title = QLabel(f"「{name}」这一页暂时打不开")
    title.setObjectName("H2")
    lay.addWidget(title)

    body = QLabel(
        "软件的其它功能都可以正常使用。这一页在打开时出了点问题，"
        "我们已经把它隔开了，不会影响你继续干活。\n\n"
        "如果想让它恢复，把下面的日志文件发给服务方即可定位：")
    body.setObjectName("Body")
    body.setWordWrap(True)
    lay.addWidget(body)

    path = _page_fault_log()
    log_lab = QLabel(path or "（日志路径不可用）")
    log_lab.setObjectName("Meta")
    log_lab.setWordWrap(True)
    log_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(log_lab)

    hint = QLabel(f"故障位置：{where}")
    hint.setObjectName("Meta")
    lay.addWidget(hint)

    lay.addStretch(1)
    return p


def _scroll_wrap(widget: QWidget) -> QScrollArea:
    """把页面包进滚动容器：内容高于窗口时出现滚动条，而不是被压缩到重叠。

    ★ 为什么必须这么做（2026-10-11 客户反馈修复）：
      设置页内容实测高 1701px、音视频转文本 1267px、选题挖掘 1088px，
      而窗口默认只有 760px（笔记本 768 屏可用约 690px）。
      没有滚动区时，页面的 QVBoxLayout 会把各卡片强行压到 minimumSizeHint，
      卡片内部控件（标签、按钮、输入框）于是互相重叠 —— 客户截图里的
      「扫码登录按钮被压扁」「授权区文字糊成一团」就是这个原因。
      套上 QScrollArea 后，页面按自身理想高度布局，超出部分改为滚动。
    """
    sa = QScrollArea()
    sa.setObjectName("PageScroll")
    sa.setWidgetResizable(True)                 # 页面宽度跟随视口，随窗口缩放
    sa.setFrameShape(QFrame.Shape.NoFrame)      # 去掉默认边框，保持无边框观感
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    sa.setWidget(widget)
    return sa


def _shrink_long_combos(page: QWidget) -> None:
    """压扁「选项文字特别长」的下拉，避免它把整页撑出横向滚动条。

    ★ 为什么要这一步（2026-10-17 视觉改版时发现）
      文案仿写页的转写模型下拉，item 是「large-v3-turbo　·　接近 large 的
      准确率，速度很快」这种长描述，QComboBox 默认按最长 item 算 sizeHint
      ⇒ 单个下拉的 sizeHint 有 759px。**即使它自己 setFixedWidth(340)**，
      QComboBox 的 minimumSizeHint 仍然按内容算，页面的 minimumSizeHint
      于是被顶到 1053px（视口只有 950px）—— 整页出现横向滚动条，
      右侧的「语速」「来源」列被切掉。

      修法有两步，缺一不可：
        1. sizeAdjustPolicy 改成按「若干字符宽」估算 —— 这会把 sizeHint()
           降下来（606 → 170），但 **minimumSizeHint() 仍然按最长 item 算**
           （Qt 的这个行为与直觉相反，实测如此）；
        2. 水平 sizePolicy 设成 Ignored —— 布局计算最小宽度时不再采信控件
           自己报的 minimumSizeHint，页面 therefore 不会被单个下拉顶宽。
           Ignored 只在"控件已有固定/最大宽度"时安全，所以顺手给没设上限的
           补一个 360px 上限，避免它转而把整行拉满。
      只处理 minimumSizeHint > 380px 的，正常宽度的下拉一概不动。
    """
    from PySide6.QtWidgets import QComboBox, QSizePolicy

    for cb in page.findChildren(QComboBox):
        if cb.minimumSizeHint().width() <= 380:
            continue
        try:
            cb.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            if cb.minimumContentsLength() < 10:
                cb.setMinimumContentsLength(10)
            if cb.maximumWidth() > 800:          # 没设过上限 → 补一个
                cb.setMaximumWidth(360)
            if cb.minimumWidth() < 160:
                cb.setMinimumWidth(160)
            cb.setSizePolicy(QSizePolicy.Policy.Ignored,
                             cb.sizePolicy().verticalPolicy())
            cb.updateGeometry()
        except Exception:                                    # noqa: BLE001
            pass


# 表头名 → 建议列宽（px）。只按「这一列里会放什么」来估，不看具体页面。
# 没列到的表头保持 Qt 默认的 100px，不会被这次调整波及。
_TABLE_COL_WIDTHS = {
    # —— 二三个字的短字段：基本是枚举/计数，不需要 100px ——
    "选择": 52, "平台": 62, "接口": 64, "版本": 56, "体裁": 72, "评分": 56,
    "时长": 62, "字数": 62, "语速": 62, "来源": 62, "状态": 64, "情感": 56,
    "点赞": 62, "评论": 62, "收藏": 62, "分享": 62, "播放": 62,
    "钩子": 120, "违规词": 82, "爆粉比": 72,
    "粉丝数": 76, "作者粉丝": 84, "作品数": 74, "获赞总数": 86, "回复数": 68,
    "发布时间": 94, "评论时间": 94, "最近记录": 94, "观察天数": 82,
    "意图分类": 84, "意向强度": 82, "实测口播": 84, "预估时长": 84,
    "关键词": 92, "抖音号": 100, "用户主页": 96, "视频链接": 96, "来源视频": 110,
    "命中意图词": 110, "用户昵称": 96, "用户": 96, "作者": 96,
    "直播状态": 84, "房间ID": 96, "录制状态": 84, "录制时长": 84,
    # ★ v1.26.2 直播监控备注列：写给赛道/用途（「美妆-护肤」这种），
    #   给 140px 才不至于把「母婴-待对标」截成「母婴-待…」
    "备注": 140,
    "提问词": 120, "引擎": 66, "采样次数": 82, "提及次数": 82, "提及率": 68,
    "首次位次": 78, "竞品提及": 78, "引用信源": 120,
    "账号昵称": 110, "sec_uid": 110, "平均互动(近N条)": 116,
    "赞粉比(互动率%)": 116, "近30天更新数": 108, "体检时间": 92,
    "时长 / 大小": 96, "博主昵称": 104,
    # v1.28.9 直播录制历史表：大小/时间两列走短字段宽度，文件名列走 Stretch
    "大小": 76, "录制时间": 148,
    # v1.28.3 转录页状态表格：首列徽标最长是「源文件已更新」，64px 会截字
    "转录状态": 108, "大小": 84, "所在目录": 140, "转录稿": 96,
}

# 装长文本的列：不给固定宽，让它吃掉剩下的横向空间。
# 按优先级排列 —— 一张表里能拉伸的通常不止一列，取最靠前的那一个。
# ★ 不要把枚举列（状态/平台/时长）留给 setStretchLastSection：
#   否则「正常」两个字会被撑进一块 350px 的空白里，比滚动条更难看。
_TABLE_STRETCH = ("文件名", "标题", "评论内容", "消息", "参数(JSON)", "爆款Top5(标题|赞)",
                  "账号昵称", "博主昵称", "用户昵称", "作者", "关键词",
                  "来源视频", "违规词", "引用信源", "触发原因")


def _tune_tables(page: QWidget) -> None:
    """按表头名给各列定宽，别让「一堆 100px」把表格顶出横向滚动条。

    ★ 为什么要这一步（2026-10-17 视觉改版时发现）
      Qt 的 QTableWidget 每一列默认 100px。9 列就 900px，超过内容区视口
      （默认窗口下约 866px）—— 于是 10 个列以上的表格（选题挖掘、关键词抓取、
      竞品体检、评论区截流、数据趋势）在**默认窗口大小**下底部就挂着一根
      横向滚动条，右侧最后一列被切一半。客户第一眼看到的就是「没做完」。

      改法：短字段（平台/时长/点赞…）收到 52~96px，长文本列设 Stretch 吃掉
      余量。★ 收窄前先跟「表头文字本身的宽度」取大值 —— 否则表头会被省略成
      「粉丝…」，那比滚动条更难看。

      仍然装不下的（13 列的表格本来就放不进 866px）允许它滚，
      那是数据密度问题，不是排版问题。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QHeaderView, QTableView

    for table in page.findChildren(QTableView):
        hdr = table.horizontalHeader()
        count = hdr.count()
        if count <= 0:
            continue
        try:
            hdr.setMinimumSectionSize(48)
            # 表头左对齐，跟单元格文字对齐成一条线。
            # ★ 这条只能走 API：QSS 里给 QHeaderView::section 写 text-align 是无效的
            #   （不会报错，看着像改好了，实际一点没变）。
            hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft |
                                    Qt.AlignmentFlag.AlignVCenter)
            fm = QFontMetrics(hdr.font())
            names = []
            for i in range(count):
                item = table.horizontalHeaderItem(i)
                names.append((item.text() if item is not None else "").strip())

            # 按 _TABLE_STRETCH 的优先级挑一列来拉伸（越靠前越适合装长文本）
            stretch_idx = -1
            for want in _TABLE_STRETCH:
                if want in names:
                    stretch_idx = names.index(want)
                    break

            for i in range(count):
                name = names[i]
                if i == stretch_idx:
                    hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
                    continue
                if name in _TABLE_COL_WIDTHS:
                    # 表头文字本身要放得下（padding 左右各 10px，再留一点余量）
                    need = fm.horizontalAdvance(name) + 28
                    hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                    table.setColumnWidth(i, max(need, _TABLE_COL_WIDTHS[name]))

            # 没有可拉伸的列时让最后一列吃掉余量，右侧不留一条空白带
            hdr.setStretchLastSection(stretch_idx < 0)
        except Exception:                                    # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# ★ 空态文案中心（2026-10-17 UI 诊断 P1）
#
# 表格空着的时候是一片纯白：客户既不知道这格是干什么的，也不知道下一步点哪。
# 每张表配「一句话说明 + 一个能点得动的出口」，空态才算完成。
#
# 结构：页面名 -> [ (首列表头名, 标题, 正文, 按钮文案, 回调) ]
#   · 首列表头名用来在多表页面里认出具体是哪张表（比 findChildren 的序号稳）；
#     只有一张表的页面用 None。
#   · 回调是页面的方法名（字符串），取不到就只显示文案不给按钮 —— 宁可少一个
#     按钮，也不要挂一个点下去报错的按钮。
#   · 回调也支持 "goto:页面名"，用于「数据的来源在别的页面」这种跨页引导。
_EMPTY_HINTS = {
    # ★ v1.28.3：转录页原来用 QListWidget（自己画空态），换成状态表格后
    #   也要登记空态引导，否则全站「每张表都有空态」的规矩破一个口子。
    #   页面里另有 file_empty 提示，表格空时整体 setVisible(False)，
    #   两层不会同时露出来说两遍。
    "音视频转文本": [
        ("转录状态", "还没有待转写的音视频",
         "点「添加本地目录」可一次导入整个文件夹。加进来后，这里会直接标出每个文件是已转录还是待转录。",
         "添加本地目录", "_add_folder"),
    ],
    "选题挖掘": [
        ("爆粉比", "还没有挖到选题",
         "在上面填一个你所在行业的关键词，找出粉丝不多但数据很好的视频 —— "
         "这类最值得学结构。", "开始挖掘", "_mine"),
        ("平台", "还没有挖到评论",
         "粘贴一条对标作品的链接，从用户的提问和吐槽里找选题 —— "
         "评论区里的原话，往往是最真实的需求。", "挖掘评论", "_mine_comments"),
    ],
    "关键词抓取": [
        (None, "还没有抓到视频",
         "在上面填一个或多个关键词（一行一个），点「开始抓取」，"
         "把这个词下数据最好的视频尽量抓全。", "开始抓取", "_run"),
    ],
    "文案仿写": [
        ("作者", "还没有提取到文案",
         "粘贴对标视频的链接，软件会下载视频并在本机把声音转成文字，"
         "连前 3 秒的钩子在第几秒都算出来 —— 视频不出本机。",
         "提取文案", "_extract_start"),
        ("版本", "还没有仿写结果",
         "载入一篇对标原文（不填就按体裁骨架直接原创），选好体裁和目标时长，"
         "点「开始仿写」出能拍的分镜稿。", "开始仿写", "_run"),
    ],
    "视频下载": [
        # ★ 2026-10-26 / 2026-11-01 再改：这里原来固定挂一颗「解析」指向 _resolve，
        #   但页面有「单条粘贴 / 批量导入 / 博主主页列表」三条进链接的路，切到批量
        #   或主页场景时那颗按钮仍然去调单条解析 —— 点了会解析一个空输入框，
        #   看着像坏了。所以按钮改成 _empty_action：只把光标送进链接输入框，
        #   **不替用户选哪条路**（空态可以没有按钮，但不能有一颗"点了没反应"的按钮）。
        # ★ 文案必须手动断行、且每行 ≤16 字，这是量出来的：
        #   EmptyHint 正文是「居中 + wordWrap + 限宽 460」的 QLabel，但实际
        #   可用宽度只有 220px 左右（居中布局 + 表格内边距吃掉），Qt 又按 460
        #   去算 sizeHint 高度 —— 结论是**它只会显示 2 行**，第 3 行直接裁掉。
        #   所以这里写成两行短句，每行都不触发折行，跟 sizeHint 对齐。
        #   （第一次写成 23 字/行，截图上就少了半句，看着像文字被吞了。）
        (None, "待下载列表是空的",
         "三条路任选一种加入链接。\n"
         "识别后勾选，再点「下载选中」。",
         "去粘贴链接", "_empty_action"),
    ],
    "竞品体检": [
        (None, "还没有体检结果",
         "在上方一行一个粘贴竞品的主页链接或 sec_uid，点「开始体检」，"
         "一次把多个账号的粉丝、作品、获赞横向对比出来。",
         "开始体检", "_run"),
    ],
    "评论区截流": [
        (None, "还没有截流结果",
         "粘贴对标视频的链接，点「开始截流」，把评论区里想买、问价、求链接的人"
         "挖出来，导出给运营去跟进。", "开始截流", "_run"),
    ],
    "复盘监控": [
        (None, "还没有监控的账号",
         "粘贴对标账号的主页链接或 sec_uid，点「加入监控」；以后每次回到这一页"
         "刷新一次快照，慢慢就能看出他在涨还是在掉。", "加入监控", "_add"),
    ],
    "数据趋势": [
        (None, "还没有积累到趋势数据",
         "这一页不用手动存：在「选题挖掘」「关键词抓取」「竞品体检」里抓到的结果"
         "都会自动留一份。先从那几页跑一次，攒够两天数据就能画出曲线。",
         "去抓一批数据", "goto:选题挖掘"),
    ],
    "数据采集": [
        (None, "还没有采集结果",
         "按「分类 → 平台 → 接口」依次选好，填完参数点「执行采集」，"
         "接口返回的数据会直接进这张表。", "执行采集", "_execute"),
    ],
    "批量采集": [
        # ★ 2026-10-26：动作分成三档之后「新建任务」收在「手动添加」档里，
        #   文案必须点明去哪一档找 —— 否则客户在「文件导入」档看着"点新建任务"
        #   却找不到那颗按钮。空态出口本身仍直连 _new_task，不受档位影响。
        #   两行、每行 ≤16 字：正文实际只有 2 行可见（同视频下载页量出来的结论）。
        (None, "任务队列是空的",
         "在「手动添加」档新建一条，\n或先点「生成示例任务」看个样例。",
         "新建任务", "_new_task"),
    ],
    "直播监控": [
        # ★ 精确匹配历史表的首列「文件名」：不登记的话它会回退到下面那条
        #   None 引导，空历史表上浮出「还没有监控目标 / 添加监控」，文不对题。
        ("文件名", "还没有录制的回放",
         "录完的直播会自动列在这里，双击就能用电脑默认播放器打开。"
         "也可以点右边按钮先看看文件都存在哪。", "打开保存目录", "_open_save_dir"),
        (None, "还没有监控目标",
         "粘贴抖音直播间链接，或先搜索博主再选中；添加后软件会立刻查一次"
         "是不是正在直播，在播就直接开始录。", "添加监控", "_add"),
    ],
    "GEO 监测": [
        (None, "还没有监测结果",
         "填品牌名（别名要补全，漏了会明明被提到却统计成没提到）、提问词和引擎 "
         "Key，点「开始体检」，算出品牌在 AI 回答里被提到多少。",
         "开始体检", "_run"),
    ],
    # 短剧成片有三张表，各自空的时候要说不同的话：
    # 剧集为空 → 教他从文件夹起手；集为空 → 让他重新扫；成片为空 → 教他导出。
    # 三张表的按钮直接指到页面自己的方法上，所以付费门禁一样会走到。
    "短剧成片": [
        ("剧名", "还没有剧集项目",
         "点「新增剧集」，选中放着整部剧的那个文件夹，软件自己把每一集认出来。",
         "新增剧集", "_new_project"),
        ("#", "这一部剧还没扫出集",
         "先在上面选中一部剧；扫得不理想就点「重新扫描」再认一次。",
         "重新扫描", "_scan"),
        ("成片", "还没有导出成片",
         "选中一批集，点「生成成片并导出」，成片会直接落到输出目录里。",
         "生成成片", "_export"),
    ],
}


def _attach_empty_hints(page: QWidget, page_name: str,
                        goto=None) -> int:
    """给页面里的表格挂空态引导。返回实际挂载数量。

    ★ 为什么集中在这里，而不是改 14 个页面文件
      同样的四行代码复制 14 遍 = 14 处漂移；文案一旦和按钮对不上（比如按钮改名、
      流程改了），没有一个地方能一次性核出来。集中成一张表之后，自检只要跑一遍
      就能验证「每张表的回调是不是真的存在」。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTableView

    rules = _EMPTY_HINTS.get(page_name)
    if not rules:
        return 0
    tables = list(page.findChildren(QTableView))
    mounted = 0
    for table in tables:
        model = table.model()
        first = ""
        if model is not None and model.columnCount() > 0:
            try:
                first = str(model.headerData(0, Qt.Orientation.Horizontal) or "")
            except Exception:                                # noqa: BLE001
                first = ""
        rule = next((r for r in rules if r[0] == first), None) or \
            next((r for r in rules if r[0] is None), None)
        if rule is None:
            continue
        _first, title, body, btn_text, cb = rule
        if callable(cb):
            handler = cb                                     # 跨页跳转移交给主窗口
        elif isinstance(cb, str) and cb.startswith("goto:"):
            target = cb.split(":", 1)[1].strip()
            handler = (lambda t=target: goto(t)) if goto else None
        else:
            handler = getattr(page, str(cb), None)
        hint = attach_empty_hint(table, title, body, btn_text, handler,
                                 icon_name=page_name)
        if hint is not None:
            mounted += 1
    return mounted


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext | None = None):
        super().__init__()
        self.ctx = ctx or AppContext()
        # 让各页面能回调主窗口做跳转（工作台卡片、结果表「写同款文案」都靠它）
        self.ctx.main_window = self
        self.setWindowTitle(branding.APP_DISPLAY_NAME)
        # ★ 窗口尺寸边界（2026-11-18 客户反馈「右侧内容过多会拉长整个窗口」）：
        #   页面全都套了 QScrollArea（见 _scroll_wrap），内容再长也不会撑大窗口；
        #   真正缺的是窗口本身的上下限 —— 之前只有 app.py 里一句 resize，
        #   最大化/拖边缘就能把它拉到铺满整屏，长页面摊开后视觉上像「被内容撑大」。
        #   锁一个舒适区间：默认见 app.py 的 resize(1180,760)，这里只管边界。
        #   · 下限保证控件不被压到重叠（实测最小可读宽约 1080）；
        #   · 上限 1520×940 —— 大屏仍能放大看表格，但不会一根拉满 4K。
        #   注意用 setMinimum/MaximumSize 而非 setFixedSize：后者连最大化都禁掉，
        #   对需要看宽表的客户反而是倒退。
        self.setMinimumSize(1080, 680)
        self.setMaximumSize(1520, 940)
        # ★ 窗口自己接管初始焦点（2026-10-18）：TAB 顺序的第一站如果不是窗口，
        #   Qt 会把焦点交给第一个可聚焦子控件 —— 也就是侧栏第一项，于是客户
        #   一打开软件就看见一个焦点环，误以为"这项被选中了"。窗口拿住焦点，
        #   启动时整屏干净；按下 Tab 才依次进入控件（键盘可达性不受影响）。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._focus_given = False
        self._theme_mode = "light"
        self._nav_buttons = []
        self._pages = {}        # 页面名 -> 真实页面对象（业务用）
        self._scrolls = {}      # 页面名 -> 承载该页面的滚动容器（放进 stack）
        self._stack = QStackedWidget()
        self._theme_btn = None
        self._guide_btn = None
        self._login_bar = None      # 顶部「未登录」提示条（跳过登录后才显示）
        self._group_heads = {}      # 分组名 -> 可点击的分组标题按钮
        self._group_bodies = {}     # 分组名 -> 该组导航按钮列表（折叠时一起隐藏）
        # ★ 自己记的展开状态：不能读 head.isChecked()（详见 _is_expanded 注释）。
        #   2026-10-31 重建补回 —— 这一行丢失后，一点一级菜单就 AttributeError。
        self._expanded = {}         # 分组名 -> 当前是否展开
        # 点一级菜单时回到「这一组上次看的那一页」，而不是组内第一页
        # （2026-10-31 重建补回：这一行丢失后点一级菜单直接 AttributeError）
        self._last_in_group = {}    # 分组名 -> 组内上次停留的页面名
        # ★ 分组名 -> 组内页面列表（2026-10-31 重建补回：这一行丢失后，
        #   _on_root_clicked 读 self._group_items 直接 AttributeError，
        #   表现为「点一级菜单（开始/常用/分析/更多工具/设置）完全没反应」）
        self._group_items = {}
        self._group_of = {}         # 页面名 -> 所属分组（Ctrl+K 跳过去要展开用）
        self._cmd_palette = None
        # 独立窗口：页面名 -> SubWindow。同一页只弹一个，重复点「独立窗口」
        # 把已开的那个 raise 出来，避免把 widget 借给两个父控件而崩掉。
        self._popouts = {}
        self._build_ui()
        self._refresh_nav_icons()

    def _build_login_banner(self) -> QFrame:
        """顶部「未登录」常驻提示条（跳过强制登录后才出现）。

        未登录时抓取与解析都得走付费接口，额度消耗明显更快 —— 这条提示让
        客户随时能补登，而不是一路默默烧额度。
        """
        bar = QFrame()
        bar.setObjectName("LoginWarnBar")
        # 品牌柠檬浅底：顶部常驻条属于"轻量提醒"，用和引导条同一套语言，
        # 不再用外来的暖黄（#FEF3C7）—— 那是改版前唯一的黄，看着像另一套系统。
        bar.setStyleSheet("background:#F2FCD9;border-bottom:1px solid #DCF3A0;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(10)
        lab = QLabel("未登录抖音：作品抓取与视频解析会走付费接口，额度消耗更快")
        lab.setStyleSheet("color:#0B0E14;font-size:12px;")
        h.addWidget(lab)
        h.addStretch()
        btn = QPushButton("立即登录")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "color:#0B0E14;font-size:12px;font-weight:600;"
            "background:transparent;border:none;text-decoration:underline;")
        btn.clicked.connect(self._on_banner_login)
        h.addWidget(btn)
        bar.setVisible(False)
        return bar

    def show_login_warning(self, show: bool = True):
        """显示 / 隐藏顶部未登录提示条。"""
        if self._login_bar is not None:
            self._login_bar.setVisible(bool(show))

    def _on_banner_login(self):
        """提示条上的「立即登录」：当场拉起登录门，成功后自动隐藏。"""
        try:
            from gui_qt.login_gate import ensure_login, is_logged_in
            ensure_login(self, self.ctx, window=self, force=True)
            self.show_login_warning(not is_logged_in(self.ctx.settings))
        except Exception:                     # noqa: BLE001
            pass

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)
        self._login_bar = self._build_login_banner()
        outer.addWidget(self._login_bar)
        h = QHBoxLayout()
        h.setSpacing(0)
        h.setContentsMargins(0, 0, 0, 0)

        nav = QFrame()
        nav.setObjectName("NavBar")
        nav.setFixedWidth(220)
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(12, 16, 12, 16)
        nv.setSpacing(4)

        logo_row = QHBoxLayout()
        self._logo_icon = QLabel()
        self._logo_icon.setPixmap(
            logo_icon(accent_color(self._theme_mode)).pixmap(QSize(24, 24)))
        logo_row.addWidget(self._logo_icon)
        self._logo_label = QLabel(branding.APP_DISPLAY_NAME)
        # 22px：品牌名是整屏唯一该用「展示级」字号的文字，改版前和正文同级
        self._logo_label.setStyleSheet(
            f"font-size:22px;font-weight:700;"
            f"color:{accent_color(self._theme_mode)};padding:2px 8px 14px;"
            f"letter-spacing:1px;")
        logo_row.addWidget(self._logo_label)
        logo_row.addStretch()
        nv.addLayout(logo_row)

        first_name = None
        saved = self.ctx.settings.get(_NAV_STATE_KEY) or {}
        for group, items in NAV_GROUPS:
            collapsible = group not in _NAV_PINNED
            # ★ body 必须是 QFrame（2026-10-26）：纯 QWidget 不吃 QSS 的
            #   border-left，层级导轨那根竖线会整根不画 —— QWidget 没有
            #   paintEvent 去描边框，这是 Qt 的老规矩，写了不报错也不生效。
            body = QFrame()
            body.setObjectName("NavGroupBody")
            body.setFrameShape(QFrame.Shape.NoFrame)
            bv = QVBoxLayout(body)
            # 二级比一级右缩 14px：层级是"退"出来的，不是靠加粗描出来的
            bv.setContentsMargins(14, 2, 0, 6)
            bv.setSpacing(4)
            self._group_bodies[group] = []

            gt = QPushButton(group)
            # 分组标题用 11px：它是"分隔标签"，不该跟导航项抢视觉重量。
            # ★ 2026-10-17 二次改版去掉「常用（4）」这种括号计数：
            #   括号计数是开发者工具的语感（VS Code 的数量徽标），
            #   苹果 / Linear 的侧栏分组只有名字。客户也不关心"这组有几个"，
            #   他关心的是"我要的那个在哪"，而这件事由 Ctrl+K 负责。
            # ★ 一级菜单（2026-10-26 二级化改版）
            #   改版前它是「一行小灰字 + 箭头」，看着像分隔符，客户不会去点；
            #   17 个功能仍然几乎同时在视野里 —— 这才是最挤的地方。
            #   现在它是真正的一级菜单：更大、更重、可点，点一下展开这一组并
            #   直接落到组内上次用的功能；二级（具体功能）缩进在它下面。
            #   同一时刻只展开一组（手风琴），侧栏行数从「最多 22 行」压到 10 行内。
            gt.setObjectName("NavRoot")
            # ★ 分组头不参与 Tab（2026-10-18）：它是"收/展"开关，不是功能入口，
            #   键盘用户走 Ctrl+K 或 Tab 到导航项就够了。否则按 Tab 第一下就落在
            #   「开始」上，一整条 2px 墨色焦点环横贯侧栏，看着像整组被选中。
            gt.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            gt.setCursor(Qt.CursorShape.PointingHandCursor)
            gt.setIconSize(QSize(12, 12))
            gt.setText(group)
            self._group_heads[group] = gt
            nv.addWidget(gt)

            if collapsible:
                collapsed = bool(saved.get(group, group in _NAV_DEFAULT_COLLAPSED))
                gt.setCheckable(True)
                gt.setChecked(not collapsed)
                # ★ 只接 clicked，不接 toggled（2026-10-26）
                #   toggled 会在 _set_group_collapsed 内部 blockSignals 之外
                #   的 setChecked 上连带触发，两个信号都接会互相把状态翻回去，
                #   表现为「点了一下没反应 / 要点两下」。
                gt.clicked.connect(
                    lambda _ck=False, g=group: self._on_root_clicked(g))
                gt.setToolTip(f"展开「{group}」（再点一下收起）")
            else:
                collapsed = False
                gt.clicked.connect(
                    lambda _ck=False, g=group: self._on_root_clicked(g))
                gt.setToolTip(f"打开「{group}」")
            nv.addWidget(body)

            for name in items:
                btn = QPushButton(name)
                btn.setObjectName("NavItem")
                btn.setCheckable(True)
                btn.setIconSize(QSize(18, 18))
                btn.clicked.connect(lambda _checked=False, n=name: self._switch(n))
                bv.addWidget(btn)
                self._nav_buttons.append((name, btn))
                self._group_bodies[group].append(btn)
                # ★ 单个页面构造失败绝不能拖垮整个窗口（2026-10-19 客户报障）：
                #   以前这里是裸调用，某个页面抛一次异常，客户看到的就是
                #   「启动失败」红框，连设置页都进不去。现在退化成占位页，
                #   其余功能照常可用；traceback 同时写进日志。
                try:
                    page = _PAGE_CLASS[name](self.ctx)
                except Exception as exc:                        # noqa: BLE001
                    _log_page_fault(name, "打开", exc)
                    page = _fault_page(name, "打开页面")
                # 后处理同样可能抛异常（它们要在页面里翻控件），
                # 失败了顶多是这一页少了某项润色，不能让窗口起不来。
                try:
                    # 长选项下拉会顶宽整页（详见 _shrink_long_combos 注释）
                    _shrink_long_combos(page)
                    # 「一堆 100px」的表格会挂横向滚动条（详见 _tune_tables 注释）
                    _tune_tables(page)
                    # 空表格给「下一步点哪」的引导（详见 _EMPTY_HINTS 注释）
                    _attach_empty_hints(page, name, goto=self._switch)
                except Exception as exc:                        # noqa: BLE001
                    _log_page_fault(name, "界面润色", exc)
                self._pages[name] = page
                # 页面统一包进滚动容器：长页面（设置/转文本/选题挖掘…）
                # 内容高于窗口时滚动查看，不再被压缩到控件重叠。
                scroll = _scroll_wrap(page)
                self._scrolls[name] = scroll
                self._stack.addWidget(scroll)
                if first_name is None:
                    first_name = name

            self._apply_group_collapsed(group, collapsed)
            self._group_items[group] = list(items)
            self._group_of = getattr(self, "_group_of", {})
            for name in items:
                self._group_of[name] = group
                self._last_in_group.setdefault(group, name)

        # 手风琴收敛：最多只让一组摊开。
        # 老客户上次可能同时展开了两三组，一进软件侧栏还是一长串 —— 那这次
        # 改版对他就没有意义。所以启动时按导航顺序保留**第一组**展开，其余
        # 收起（persist=False：这是本次会话的临时整理，不覆盖他存过的状态）。
        self._collapse_others(None)
        nv.addStretch()
        self._theme_btn = QPushButton("切换深色 / 浅色")
        self._theme_btn.setObjectName("NavItem")
        self._theme_btn.setIconSize(QSize(18, 18))
        self._theme_btn.clicked.connect(self._toggle_theme)
        nv.addWidget(self._theme_btn)

        # 「使用教程」入口：打开快速上手对话框，与左侧页面导航平级
        self._guide_btn = QPushButton("使用教程")
        self._guide_btn.setObjectName("NavItem")
        self._guide_btn.setIconSize(QSize(18, 18))
        self._guide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._guide_btn.setToolTip("查看各功能的使用教程与操作步骤")
        self._guide_btn.clicked.connect(lambda: open_guide(self, None))
        nv.addWidget(self._guide_btn)

        # ★ 独立窗口（2026-11-18）：把当前页弹出去单独看，主窗口继续干活。
        #   页面内容多时不必主窗口拉到铺满屏 —— 弹出来自成一个可缩放窗口，
        #   关闭自动归位。全站通用，任何页面都能用。
        self._popout_btn = QPushButton("独立窗口打开本页")
        self._popout_btn.setObjectName("NavItem")
        self._popout_btn.setIconSize(QSize(18, 18))
        self._popout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._popout_btn.setToolTip(
            "把当前这一页弹到独立窗口，方便单独查看；\n"
            "关闭窗口后页面自动回到主界面")
        self._popout_btn.clicked.connect(lambda *_: self.popout_current())
        nv.addWidget(self._popout_btn)

        # ★ 一键导出日志（2026-11-15）：常驻侧栏底部，任何页面都能点。
        #   背景：出口原来只在「设置 → 系统档」里，客户遇到问题时（如
        #   TikTok 录制失败）根本找不到 —— 拿不到日志只能来回猜。
        self._log_export_btn = QPushButton("导出日志")
        self._log_export_btn.setObjectName("NavItem")
        self._log_export_btn.setIconSize(QSize(18, 18))
        self._log_export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._log_export_btn.setToolTip(
            "把最近的运行日志和环境信息打包成压缩包放到桌面，\n"
            "发给作者即可精准定位问题（遇到任何报错都建议先点这里）")
        self._log_export_btn.clicked.connect(
            lambda *_: self._export_logs())
        nv.addWidget(self._log_export_btn)

        # 版本号：大公司软件的侧栏底部都有这一行（Slack / Linear / Raycast）。
        # 它的作用是「客户报问题时能直接说出来」，顺带让界面看起来是正式版。
        try:
            from justoneapi_collector.branding import APP_VERSION
        except Exception:                                    # noqa: BLE001
            APP_VERSION = ""
        if APP_VERSION:
            ver = QLabel(f"v{APP_VERSION}")
            ver.setObjectName("Meta")
            ver.setStyleSheet("padding:10px 12px 2px;background:transparent;")
            ver.setToolTip("当前版本 · 反馈问题时请附上这个版本号")
            self._ver_label = ver
            nv.addWidget(ver)

        h.addWidget(nav)
        h.addWidget(self._stack, 1)
        outer.addLayout(h, 1)
        # Ctrl+K 功能搜索：折叠分组之后，这是「按名字直达」的唯一出口
        install_shortcut(self, self._open_palette)
        if first_name:
            self._switch(first_name)

    def switch_to(self, name: str):
        """切到指定页面（对外：工作台卡片与跨页跳转用）。页面不存在时忽略。"""
        self._switch(name)

    # ------------------------------------------------------------ 一键导出日志
    def _export_logs(self):
        """侧栏「导出日志」：打包诊断报告 + 最近日志到桌面（Worker 后台跑）。"""
        from gui_qt.widgets.log_export import export_logs
        export_logs(self, self.ctx, kind="日志",
                    success_note="作者微信见「设置 → 系统档 → 反馈与日志」。")

    def page(self, name: str):
        """取页面实例（跨页传参用，例如把爆款链接送到文案仿写页）。"""
        return self._pages.get(name)

    def showEvent(self, e):                                   # noqa: N802
        """首次显示时把焦点收回到窗口本身（见 __init__ 里的说明）。"""
        super().showEvent(e)
        if not self._focus_given:
            self._focus_given = True
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        # ★ 系统标题栏跟随应用主题（2026-11-17）：必须在 show 之后，
        #   此时 winId() 才落地。首次显示同步一次，避免深色界面顶白条。
        from gui_qt.titlebar import apply_native_titlebar
        apply_native_titlebar(self, self._theme_mode)

    # ------------------------------------------------------------ 侧栏分组折叠
    def _refresh_group_icon(self, group: str):
        """刷新分组标题上的展开/收起箭头。

        ★ 为什么不用 QSS 的 ::indicator
          QPushButton 不支持 indicator 子控件（那是 QCheckBox / QTreeView 的），
          写了不报错但也不生效 —— 于是标题看着就是一行纯文字，没人知道能点。
          所以箭头用 group_caret_icon 画上去，顺带吃到当前主题色。
        """
        head = self._group_heads.get(group)
        if head is None or group in _NAV_PINNED:
            return
        # 收起 → 朝右 ▸（"内容在里面，还没摊开"）；展开 → 朝下 ▾。
        # 这是 Finder / 文件树的通行写法。原写法收起用 ^，实拍时三个分组标题
        # 全挂着 ^ 却没有内容，读起来像「已经展开」—— 反而更像坏了。
        collapsed = not head.isChecked()
        color = nav_text_color(self._theme_mode)
        head.setIcon(group_caret_icon(color, 12, collapsed))

    def _apply_group_collapsed(self, group: str, collapsed: bool):
        """实际显隐一组里的导航项，并记下展开状态（见 _is_expanded 注释）。"""
        self._expanded[group] = not collapsed
        for btn in self._group_bodies.get(group, []):
            btn.setVisible(not collapsed)
        self._refresh_group_icon(group)

    def _set_group_collapsed(self, group: str, collapsed: bool,
                             persist: bool = True):
        """收起 / 展开一组，可按需把状态写回配置。

        ★ 为什么要持久化
          折叠是客户自己整理出来的侧栏。每次启动都重置回默认，等于每天都把他
          摆好的东西推乱一次 —— 那还不如不做。

        ★ 为什么 persist 要能关（2026-10-17 踩过）
          程序跳转（Ctrl+K、工作台卡片、跨页「写同款文案」）也会走到这里。
          如果这些也写盘，客户访问过一次某组，这组就**再也收不回去了** ——
          用几天侧栏又变回全展开，折叠等于白做。
          所以：只有客户亲手点分组标题才 persist=True。
        """
        head = self._group_heads.get(group)
        if head is not None:
            # 用 blockSignals 截断「setChecked → toggled → 再调回来」的死循环
            head.blockSignals(True)
            head.setChecked(not collapsed)
            head.blockSignals(False)
        self._apply_group_collapsed(group, collapsed)
        if not persist:
            return
        try:
            saved = dict(self.ctx.settings.get(_NAV_STATE_KEY) or {})
            saved[group] = bool(collapsed)
            self.ctx.settings[_NAV_STATE_KEY] = saved
            save = getattr(self.ctx, "save_settings", None)
            if callable(save):
                save()
        except Exception:                                    # noqa: BLE001
            pass

    # ------------------------------------------------------- 一级菜单（手风琴）
    def _collapsible_groups(self) -> list:
        """可折叠的一级菜单名（「开始」「设置」不参与 —— 见 _NAV_PINNED 注释）。"""
        return [g for g, _items in NAV_GROUPS if g not in _NAV_PINNED]

    def _is_expanded(self, group: str) -> bool:
        """这一组当前是不是摊开的。

        ★ 必须读自己记的状态，不能读 head.isChecked()（2026-10-26 实测踩坑）
          QPushButton 是 checkable 时，click() **先翻转 checked、再发 clicked**。
          在 clicked 槽里问 isChecked()，拿到的永远是"翻完之后"的值 —— 于是
          「点一下展开」被自己判成「已经展开 → 收起」，表现就是**点一级菜单
          完全没反应**（第一次实测就是这么翻车的）。状态自己记一份，最稳。
        """
        if group in self._expanded:
            return bool(self._expanded[group])
        head = self._group_heads.get(group)
        return bool(head is not None and head.isChecked())

    def _collapse_others(self, keep: str | None):
        """手风琴：收起除 keep 外的所有一级组。

        ★ 为什么一定要 pass `keep=None` 的用法
          启动时调用一次，把「上次同时摊开的三组」压成一组 —— 否则这次改版
          对老客户等于没做（他一打开还是一长串）。此时不写盘：这是本次会话的
          临时整理，不能覆盖他亲手存下的侧栏状态。
        """
        if keep is None:
            keep = next((g for g in self._collapsible_groups()
                         if self._is_expanded(g)), None)
        for g in self._collapsible_groups():
            if g != keep and self._is_expanded(g):
                self._set_group_collapsed(g, True, persist=False)

    def _on_root_clicked(self, group: str):
        """一级菜单被点：展开这一组并落到组内上次的功能；已展开则收起。

        ★ 为什么要「落到上次的功能」
          二级菜单的价值是"点大类 → 看到这一类"，但如果每次都弹回组内第一项，
          客户从一个功能跳去同组另一个功能时会被拽回原点，等于多了半步操作。
        """
        items = self._group_items.get(group) or []
        if not items:
            return
        target = self._last_in_group.get(group) or items[0]
        if group in _NAV_PINNED:
            # 开始 / 设置只有一项：一级就是功能本身，点一下直接进
            self._switch(target)
            return
        if self._is_expanded(group):
            self._set_group_collapsed(group, True, persist=True)
            return
        self._collapse_others(group)
        self._set_group_collapsed(group, False, persist=True)
        self._switch(target)

    def _ensure_group_expanded(self, name: str):
        """跳到某个页面时，先把它所在的组展开。

        ★ 不这么做就会出现最难受的一种 bug：从 Ctrl+K 跳到一个折叠组里的页面，
          内容区已经换了，但侧栏里看不到这个页面被选中 —— 客户会以为点错了。
        """
        group = self._group_of.get(name)
        if not group or group in _NAV_PINNED:
            return
        hidden = not any(b.isVisible()
                         for b in self._group_bodies.get(group, []))
        # 手风琴：不管是不是已经摊开，都先把它人压平，保证侧栏只有一组是开的
        self._collapse_others(group)
        if hidden:
            # persist=False：只是「让客户看见他跳到了哪」，不代表他改了侧栏布局
            self._set_group_collapsed(group, False, persist=False)

    def _open_palette(self):
        """唤起 Ctrl+K 功能搜索。"""
        from PySide6.QtWidgets import QApplication

        from gui_qt.widgets.command_palette import CommandPalette, build_commands

        def pick(text: str):
            if text == "使用教程":
                open_guide(self, None)
                return
            if text == "切换深色 / 浅色":
                self._toggle_theme()
                return
            self._switch(text)

        app = QApplication.instance()
        mode = (app.property("theme_mode") or "light") if app else "light"
        palette = CommandPalette(self, build_commands(NAV_GROUPS), pick)
        self._cmd_palette = palette
        palette.exec_at(self)

    def _mark_active_root(self, group: str | None):
        """把当前所在的一级菜单标成「你在这里」。

        ★ 为什么不用 checked 而是动态属性
          一级按钮的 checked 已经被「这一组是否展开」占用（=_is_expanded）。
          两套语义挤在一个布尔量上，客户只要展开某组就会被误判成"选中了它"。
          Qt 的属性选择器是干净的第二个通道：setProperty("current") 配 QSS 的
          [current="true"]，跟展开状态互不打搅。
        """
        for g, head in self._group_heads.items():
            same = (g == group)
            if bool(head.property("current")) == same:
                continue
            head.setProperty("current", same)
            # ★ 改了属性必须 polish：QSS 只在样式重算时才读属性，不刷新就等于
            #   "点了没反应"（与 Primary 按钮改名那处完全同一个坑）。
            st = head.style()
            st.unpolish(head)
            st.polish(head)
            head.update()

    # ------------------------------------------------------------ 独立窗口
    def popout_current(self):
        """把当前页弹到独立窗口；关闭自动归还主界面。

        ★ 为什么这样做（2026-11-18 客户「内容多拉长窗口」）：
          页面本身在 QScrollArea 里不会被内容撑大，但客户想要「把这一页
          抽出来单独盯、主窗口继续干别的」。做法是**借**出页面 widget 给
          独立窗口（同一时刻一个 QWidget 只能有一个父控件），关闭时再还回
          原滚动容器 —— 页面对象、Worker、状态全部保留，不重建。
        """
        cur = self._stack.currentWidget()
        if cur is None:
            return
        # 反查当前页名（stack 里放的是 QScrollArea，名字映射在 _scrolls）
        name = None
        for n, scroll in self._scrolls.items():
            if scroll is cur:
                name = n
                break
        if not name:
            return
        # 已弹出 → 激活已有窗口，不重复借出
        existing = self._popouts.get(name)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        page_widget = cur.widget()
        if page_widget is None:
            return

        scroll_ref = cur  # 关闭时还回这里

        def _on_closed(win, name=name):
            w = win.take_content()
            if w is not None:
                scroll_ref.setWidget(w)
                w.setGraphicsEffect(None)
                w.setVisible(True)
            self._popouts.pop(name, None)

        try:
            sub = SubWindow(f"{name} · 独立窗口", parent=self,
                            on_closed=_on_closed)
            cur.takeWidget()                 # 从滚动容器摘下（不销毁）
            sub.set_content(page_widget)
            self._popouts[name] = sub
            sub.show()
            self.statusBar().showMessage(
                f"已把「{name}」弹到独立窗口，关闭后自动回到主界面", 4000) \
                if hasattr(self, "statusBar") else None
        except Exception as exc:             # noqa: BLE001
            # 弹出失败必须把 widget 还回去，否则主界面这块永久空白
            try:
                cur.setWidget(page_widget)
            except Exception:                # noqa: BLE001
                pass
            import traceback
            traceback.print_exc()

    def _switch(self, name):
        # 若目标页此刻正被借到独立窗口里，先关闭它把 widget 还回来，
        # 否则主界面这一格会是空白（widget 同时只能有一个父控件）。
        popped = self._popouts.get(name)
        if popped is not None:
            try:
                popped.close()
            except Exception:                                # noqa: BLE001
                pass
        self._ensure_group_expanded(name)
        group = self._group_of.get(name)
        if group:
            self._last_in_group[group] = name
        self._mark_active_root(group)
        for n, btn in self._nav_buttons:
            btn.setChecked(n == name)
        self._refresh_nav_icons()
        page = self._pages.get(name)
        if name in self._scrolls:
            scroll = self._scrolls[name]
            # 防御：若上一轮 fade_in 动画异常未结束，可能把页面留在透明态
            # （看起来像"切了但空白"，用户会误判为跳转失效）。切页前强制清掉
            # 残留的透明度效果并复位可见性，保证目标页一定显示出来。
            try:
                w = scroll.widget()
                if w is not None:
                    w.setGraphicsEffect(None)
                    w.setVisible(True)
                scroll.setVisible(True)
            except Exception:  # noqa: BLE001
                pass
            self._stack.setCurrentWidget(scroll)
            # 切页淡入：硬切会让"点了导航"和"内容换了"之间没有因果感。
            # 只淡化内容页，不碰 QScrollArea 本身（滚动条跟着变透明会闪）。
            # 入场：有编排清单的页面（目前是工作台）按阅读顺序依次浮现，
            # 其它页面退回整页淡入。依据见 home_page.entrance_order() 的注释。
            order = None
            if page is not None and hasattr(page, "entrance_order"):
                try:
                    order = page.entrance_order()
                except Exception:                            # noqa: BLE001
                    order = None
            if order:
                stagger(order)
            else:
                fade_in(scroll.widget() or scroll)
            # 字距 / 等宽数字是逐控件的字体属性，新页面第一次显示时要补一遍
            # （否则只有切过一次主题的页面才有"紧排"，看起来两套风格）
            try:
                from gui_qt.theme import apply_tracking
                apply_tracking(scroll.widget(), self._theme_mode)
            except Exception:                                # noqa: BLE001
                pass
        # 离开工作台再回来，额度/授权可能已经变过，顺手刷新一次
        if page is not None and hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception as exc:                            # noqa: BLE001
                # ★ 不能只是 pass：静默吞掉意味着客户看到的是"数字不动了"，
                #   而我们这边什么线索都没有。写进日志，客户把日志发过来
                #   就能直接看到是哪一页、哪一行、什么异常。
                _log_page_fault(name, "刷新数据", exc)

    def _refresh_nav_icons(self):
        """按当前主题色与选中态刷新所有导航图标 + 主题切换按钮图标。

        选中项背景是实心靛蓝（见 QSS 的 NavItem:checked），图标必须用反白色，
        否则靛蓝底 + 靛蓝图标 = 依然看不清（这正是用户反馈的「高亮没显示」）。
        """
        normal = nav_text_color(self._theme_mode)
        accent = accent_color(self._theme_mode)
        active_icon = nav_active_icon_color(self._theme_mode)
        for n, btn in self._nav_buttons:
            active = btn.isChecked()
            btn.setIcon(nav_icon(n, active_icon if active else normal))
        if self._theme_btn is not None:
            # 当前浅色 → 显示月亮（去深色）；当前深色 → 显示太阳
            self._theme_btn.setIcon(theme_toggle_icon(self._theme_mode == "light", accent))
        # 品牌名与 logo 也跟主题色走（浅色墨 / 深色柠檬）——
        # 改版前这里写死 #6366F1，切主题后紫标孤零零留在左上一整个版本。
        if getattr(self, "_logo_label", None) is not None:
            self._logo_label.setStyleSheet(
                f"font-size:22px;font-weight:700;color:{accent};"
                f"padding:2px 8px 14px;letter-spacing:1px;")
        if getattr(self, "_logo_icon", None) is not None:
            self._logo_icon.setPixmap(logo_icon(accent).pixmap(QSize(24, 24)))
        if self._guide_btn is not None:
            # 问号圈图标：与导航文字同色，保持低调
            self._guide_btn.setIcon(help_icon(normal))
        # 「导出日志」：与教程按钮同色，主题切换后必须跟着重画（否则深色下
        # 会留一枚浅色描边图标，和旁边两个不一样）
        if getattr(self, "_log_export_btn", None) is not None:
            try:
                from gui_qt.icons import export_icon
                self._log_export_btn.setIcon(export_icon(normal))
            except Exception:                                # noqa: BLE001
                pass
        # 分组的展开/收起箭头也是画上去的，同样漏了就会留在旧主题色
        for _g in self._group_heads:
            self._refresh_group_icon(_g)

    def _toggle_theme(self):
        self._theme_mode = "dark" if self._theme_mode == "light" else "light"
        # 系统标题栏跟着变（QSS 管不到它，必须单独调 DWM）。
        from gui_qt.titlebar import apply_native_titlebar
        apply_native_titlebar(self, self._theme_mode)
        app = QApplication.instance()
        if app is not None:
            from gui_qt.theme import apply_theme, restyle_tree
            apply_theme(app, self._theme_mode)
            # 页面里 130 多处内联色值是浅色时写死的（内联优先级高于 QSS），
            # 不置换的话深色下标题会变成黑底黑字。先粗粒度置换，
            # 后面 _refresh_guide_banners 里各页面的 apply_theme 会用 palette 精修。
            restyle_tree(self, self._theme_mode)
        self._refresh_nav_icons()
        self._refresh_guide_banners()
        # ★ 字距 / 等宽数字必须最后补：QSS 每次重套都会把字体重置回默认字距，
        #   它又没有 letter-spacing 属性，只能在样式全部落定之后走 QFont。
        from gui_qt.theme import apply_tracking
        apply_tracking(self, self._theme_mode)

    def _refresh_guide_banners(self):
        """主题切换后刷新所有页面里「按主题自绘/自设样式」的组件。

        别看这个名字只提了引导条 —— 它是目前唯一一处"遍历所有页面 + 页面内
        组件"的钩子，所以凡是**自己持有内联样式、不靠 QSS 伪状态**的组件都要
        挂进来。目前三处：
          · GuideBanner：图标色
          · StatusLine：状态行的四档语义色（idle/ok/warn/error）
          · EmptyHint：空态引导层的图标色
        颜色写死在内联样式里的组件，切主题时不会自动变 —— 漏一个就是深色主题
        下某处还是浅色底。
        """
        from gui_qt.pages._base import StatusLine

        for page in self._pages.values():
            for banner in page.findChildren(GuideBanner):
                banner.apply_theme(self._theme_mode)
            for line in page.findChildren(StatusLine):
                line.apply_theme(self._theme_mode)
            refresh_empty_hints(page, self._theme_mode)
            # ★ 自绘交互组件（刻度尺 / 展开面板 / 节点轴 / 标签 / 数据板）同样
            #   不读 QSS，谁都不会自己变。在这里统一扫一遍，省得以后每加一个
            #   页面又忘了转发 —— 漏掉的表现是"深色下突然冒出一块浅色"。
            from gui_qt.widgets import kit
            for cls in (kit.RulerSlider, kit.DisclosurePanel, kit.NodeTimeline,
                        kit.MetricBoard, kit.ChoiceFlow, kit.StepDots,
                        kit.ChoiceChips, kit.Segmented, kit.IconToggle):
                for w in page.findChildren(cls):
                    try:
                        w.apply_theme(self._theme_mode)
                    except Exception:                        # noqa: BLE001
                        pass
            # 工作台的自绘组件（指标卡 / 卡片 / 额度条）也要跟着换色
            apply = getattr(page, "apply_theme", None)
            if callable(apply):
                try:
                    apply(self._theme_mode)
                except Exception:                            # noqa: BLE001
                    pass
