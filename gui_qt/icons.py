"""流量捕手 PySide6 图标系统：内嵌 SVG，按主题色渲染为 QIcon。

- 所有图标为单色线性风格，使用 currentColor 占位，运行时替换为主题色；
- 图标内嵌在代码里，不依赖任何外部图片文件，打包后也不会丢资源；
- 渲染按 2x 像素生成，配合 setIconSize 在 HiDPI 屏保持清晰。
"""
from PySide6.QtCore import QByteArray, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import Qt


def _svg(inner: str) -> str:
    """包裹成完整 SVG 文档。线框图标默认 fill=none stroke=currentColor；
    若 inner 内某元素显式写了 fill=\"currentColor\" 则按填充渲染。"""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        + inner + "</svg>"
    )


# 10 个导航项 + 主题切换 + 品牌 logo 的 SVG 内部路径
_NAV_INNER = {
    # 工作台：四格仪表盘（总览 / 首页）
    "工作台": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/>'
              '<rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/>'
              '<rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/>'
              '<rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/>',
    # 关键词抓取：放大镜 + 关键词
    "关键词抓取": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>'
                 '<path d="M7.5 10.5h6"/>',
    # 视频下载：显示器 + 播放三角
    "视频下载": '<rect x="3" y="5" width="13" height="14" rx="2"/><path d="M16 9l5-3v12l-5-3z"/>',
    # 视频号解析：对话气泡 + 播放三角（微信生态里的短视频）
    "视频号解析": '<path d="M21 11.5a8 8 0 01-8 8 8.7 8.7 0 01-3-.6L4 21l1.3-4.2A8 8 0 1121 11.5z"/>'
                 '<path d="M10 8.5l4 3-4 3z"/>',
    # 公众号运营：文档 + 阅读数折线（文章与数据一体）
    "公众号运营": '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/>'
                 '<path d="M9 12h7"/><path d="M9 16h5"/><path d="M3 17l3-3 2 2 4-5"/>',
    # 小红书笔记：便签 + 文字行（内容提取）
    "小红书笔记": '<rect x="4" y="3" width="16" height="18" rx="2"/>'
                '<path d="M8 8h8"/><path d="M8 12h8"/><path d="M8 16h5"/>',
    # 直播监控：电台波纹
    "直播监控": '<circle cx="12" cy="12" r="2.5"/><path d="M7 7a7 7 0 000 10"/>'
               '<path d="M17 7a7 7 0 010 10"/><path d="M4 4a11 11 0 000 16"/>'
               '<path d="M20 4a11 11 0 010 16"/>',
    # 选题挖掘：灯泡
    "选题挖掘": '<path d="M9 18h6"/><path d="M10 21h4"/>'
               '<path d="M12 3a6 6 0 014 10.5c-.8.7-1.2 1.4-1.2 2.5H9.2c0-1.1-.4-1.8-1.2-2.5A6 6 0 0112 3z"/>',
    # 竞品体检：柱状图
    "竞品体检": '<path d="M4 20V11"/><path d="M10 20V5"/><path d="M16 20v-7"/><path d="M3 20h18"/>',
    # 评论区截流：对话气泡 + 里面的小人（挖意向客户）
    "评论区截流": '<path d="M21 12a8 8 0 01-8 8H8l-5 3 1.2-4.2A8 8 0 1121 12z"/>'
                  '<circle cx="10" cy="11" r="1.6"/><path d="M7.2 15.4a3.4 3.4 0 005.6 0"/>',
    # 数据趋势：折线向上 + 数据点（看涨跌）
    "数据趋势": '<path d="M4 19V5"/><path d="M4 19h16"/>'
                '<path d="M7 15.5l3.5-4 3 2.5L20 7"/>'
                '<circle cx="7" cy="15.5" r="1.4"/><circle cx="20" cy="7" r="1.4"/>',
    # GEO 监测：雷达屏幕 + 中心信号点（品牌在 AI 里的可见度）
    "GEO 监测": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>'
               '<circle cx="12" cy="12" r="1.4"/>'
               '<path d="M12 12l6-4"/>',
    # 复盘监控：上升折线
    "复盘监控": '<path d="M3 16l5-6 4 3 5-8 4 5"/><path d="M3 20h18"/>',
    # 报告：文档
    "报告": '<path d="M7 3h8l4 4v14H7z"/><path d="M15 3v4h4"/>'
           '<path d="M10 12h6"/><path d="M10 16h6"/>',
    # 文案仿写：纸笔（拆结构后重写一版）
    "文案仿写": '<path d="M14 3l6 6-9.5 9.5H5v-5.5z"/><path d="M13 4l6 6"/>'
               '<path d="M5 20h14"/>',
    # 音视频转文本：字幕框
    "音视频转文本": '<rect x="3" y="6" width="18" height="11" rx="2"/>'
                   '<path d="M7 10h3"/><path d="M14 10h3"/><path d="M7 13.5h10"/>',
    # 设置：齿轮（中心圆 + 辐条）
    "设置": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3"/><path d="M12 19v3"/>'
           '<path d="M2 12h3"/><path d="M19 12h3"/><path d="M4.5 4.5l2 2"/>'
           '<path d="M17.5 17.5l2 2"/><path d="M4.5 19.5l2-2"/><path d="M17.5 4.5l2 2"/>',
}

_LOGO_INNER = (
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>'
    '<path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/><path d="M18 12h3"/>'
)

_SUN_INNER = (
    '<circle cx="12" cy="12" r="4"/>'
    '<path d="M12 2v2"/><path d="M12 20v2"/><path d="M2 12h2"/><path d="M20 12h2"/>'
    '<path d="M4.9 4.9l1.4 1.4"/><path d="M17.7 17.7l1.4 1.4"/>'
    '<path d="M4.9 19.1l1.4-1.4"/><path d="M17.7 6.3l1.4-1.4"/>'
)

_MOON_INNER = '<path d="M21 12.8A8 8 0 1111.2 3 6.3 6.3 0 0021 12.8z" fill="currentColor" stroke="none"/>'

# 问号圆圈（「使用教程」导航项 / 引导入口）
_HELP_INNER = (
    '<circle cx="12" cy="12" r="9"/>'
    '<path d="M9.6 9.4a2.6 2.6 0 115.1.7c0 1.7-2.6 2.1-2.6 3.7"/>'
    '<path d="M12 17.3v.1"/>'
)

# 信息圆圈（页面引导条前缀）
_INFO_INNER = (
    '<circle cx="12" cy="12" r="9"/>'
    '<path d="M12 11.2v5.3"/>'
    '<path d="M12 7.6v.1"/>'
)

# 垃圾桶（清空列表：明确的破坏性语义，避免与「移除选中」混淆）
_TRASH_INNER = (
    '<path d="M4 7h16"/>'
    '<path d="M9.5 7V5.6A1.6 1.6 0 0111.1 4h1.8a1.6 1.6 0 011.6 1.6V7"/>'
    '<path d="M6.6 7l1 12.6A1.5 1.5 0 009.1 21h5.8a1.5 1.5 0 001.5-1.4L17.4 7"/>'
    '<path d="M10.2 11v6"/>'
    '<path d="M13.8 11v6"/>'
)

# 导出（「导出日志」导航项）：向下的箭头落进托盘 —— 与「下载」同语义，
# 客户一眼就知道点下去会得到一个文件。
_EXPORT_INNER = (
    '<path d="M12 3.5v10.2"/>'
    '<path d="M8.2 10.2L12 14l3.8-3.8"/>'
    '<path d="M4.5 16.4v2.1A1.5 1.5 0 006 20h12a1.5 1.5 0 001.5-1.5v-2.1"/>'
)

# 展开箭头（引导条「详细步骤」）
_CHEVRON_DOWN_INNER = '<path d="M6 9.5l6 6 6-6"/>'
_CHEVRON_UP_INNER = '<path d="M6 14.5l6-6 6 6"/>'
# 侧栏分组用：收起时朝右（Finder / 文件树的通行写法）。
# ★ 为什么不用「收起朝上」（2026-10-17 实拍发现）
#   原写法收起时画 ^，可 ^ 在界面语汇里读作「已经展开、点我收起」——
#   三个分组标题全挂着 ^ 却没内容，客户会以为侧栏坏了。
_CHEVRON_RIGHT_INNER = '<path d="M9.5 6l6 6-6 6"/>'

# 书本（「使用教程」对话框标题）
_BOOK_INNER = (
    '<path d="M4 5.5A1.5 1.5 0 015.5 4H10a2 2 0 012 2v13a1.6 1.6 0 00-1.6-1.6H4z"/>'
    '<path d="M20 5.5A1.5 1.5 0 0018.5 4H14a2 2 0 00-2 2v13a1.6 1.6 0 011.6-1.6H20z"/>'
)


def make_icon(svg: str, color: str, size: int = 18) -> QIcon:
    """把 SVG 字符串按指定颜色渲染成 QIcon（2x 像素，HiDPI 清晰）。"""
    svg = svg.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    px = size * 2
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return QIcon(pm)


def nav_icon(name: str, color: str, size: int = 18) -> QIcon:
    """取导航项图标。name 需与 NAV_GROUPS 中的页面名一致。"""
    inner = _NAV_INNER.get(name, _NAV_INNER["设置"])
    return make_icon(_svg(inner), color, size)


def logo_icon(color: str, size: int = 22) -> QIcon:
    """品牌靶心图标。"""
    return make_icon(_svg(_LOGO_INNER), color, size)


def theme_toggle_icon(to_dark: bool, color: str, size: int = 18) -> QIcon:
    """主题切换按钮图标：当前浅色(to_dark=True)→显示月亮（去深色）；
    当前深色(to_dark=False)→显示太阳。"""
    if to_dark:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
               'viewBox="0 0 24 24" fill="none" stroke="currentColor" '
               'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
               + _MOON_INNER + "</svg>")
    else:
        svg = _svg(_SUN_INNER)
    return make_icon(svg, color, size)


def help_icon(color: str, size: int = 18) -> QIcon:
    """问号圆圈：教程 / 帮助入口。"""
    return make_icon(_svg(_HELP_INNER), color, size)


def info_icon(color: str, size: int = 16) -> QIcon:
    """信息圆圈：页面引导条前缀图标。"""
    return make_icon(_svg(_INFO_INNER), color, size)


def clear_icon(color: str, size: int = 16) -> QIcon:
    """垃圾桶：清空列表（破坏性动作）。"""
    return make_icon(_svg(_TRASH_INNER), color, size)


def chevron_icon(color: str, size: int = 14, up: bool = False) -> QIcon:
    """展开/收起箭头。up=True 时朝上。"""
    inner = _CHEVRON_UP_INNER if up else _CHEVRON_DOWN_INNER
    return make_icon(_svg(inner), color, size)


def group_caret_icon(color: str, size: int = 12, collapsed: bool = False) -> QIcon:
    """侧栏分组的展开状态指示：收起朝右，展开朝下。

    单独开一个函数而不是给 chevron_icon 加参数，是因为「朝上」在引导条里
    有正当用途（点了收起详情），而分组标题只可能是「右 / 下」两态 ——
    混用一个函数迟早写反，10-17 已经写反过一次。
    """
    inner = _CHEVRON_RIGHT_INNER if collapsed else _CHEVRON_DOWN_INNER
    return make_icon(_svg(inner), color, size)


def book_icon(color: str, size: int = 20) -> QIcon:
    """书本：使用教程对话框。"""
    return make_icon(_svg(_BOOK_INNER), color, size)


def export_icon(color: str, size: int = 18) -> QIcon:
    """下载箭头入托盘：侧栏「导出日志」入口（2026-11-15）。"""
    return make_icon(_svg(_EXPORT_INNER), color, size)
