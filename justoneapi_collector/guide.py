# -*- coding: utf-8 -*-
"""各功能板块的「使用指导区」。

为什么单独成一个模块：指导文案会随功能迭代频繁改动，
集中放在这里，改文案不用到 2400 多行的 gui.py 里翻找。

设计要点：
  - 每个板块一份 GUIDES 文案（steps 为 1234 步骤，tips 为注意事项）
  - GuidePanel 是可折叠面板：默认展开（用户要的就是"打开就知道怎么用"），
    点标题收起，折叠状态写入设置持久化
  - 正文用只读 Text 渲染，支持简单的标记：[标题] / 数字步骤 / 注意
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ----------------------------------------------------------------------
#  文案
# ----------------------------------------------------------------------

GUIDES: dict[str, dict] = {
    "collect": {
        "title": "这个页面是干什么的：按接口取数（一次调用计一次费）",
        "steps": [
            "先确保「设置」页的 API Token 已填好，并点过「测试连接」看到 ✓（Token 失效会让所有功能一起失败）。",
            "在左侧从上往下选：平台分类 → 平台 → 接口。右侧灰字会说明这个接口能取到什么数据。",
            "在「参数」区填该接口需要的参数。常见参数：keyword(关键词)、videoId(视频ID)、secUid(博主ID)、offset/page(翻页)。",
            "点「执行采集」，结果显示在右侧表格；点「导出 Excel / CSV / JSON」存到本地。",
        ],
        "tips": [
            "不确定参数名怎么写？鼠标停在下拉框上先看灰字说明；实在不确定就用「选题挖掘」页的「接口自检」让程序自己去校准参数名。",
            "同一个接口 + 同样的参数会被缓存，不会重复扣费（可在设置里关闭缓存）。",
            "要一次跑很多接口，请用「批量采集」页，不要把这条流程重复点一百遍。",
        ],
    },
    "batch": {
        "title": "这个页面是干什么的：一次跑几十上百个采集任务",
        "steps": [
            "点「新建任务」（或「从数据采集页取当前接口」），把要采集的接口和参数一条条加进任务列表。",
            "也可以点「生成示例任务」看格式，再点「保存任务文件」导出 JSON，用记事本批量改好参数后「加载任务文件」导入。",
            "确认「设置」页的「最大并发」合理（数据接口建议 3–8，太高容易触发风控和超配额）。",
            "点「执行批量采集」，进度条和每行状态会实时刷新；跑完点「导出全部结果」。",
        ],
        "tips": [
            "任务文件是一个 JSON 数组，每项形如 {\"platform\":\"douyin-tiktok-china\",\"endpoint\":\"hot-search-v1\",\"params\":{}}。",
            "并发是让多个任务同时跑，不是让一个任务跑得更快；单个接口永远是串行翻页。",
            "大批量跑之前，先拿 2–3 条任务试一次，确认参数正确再全量跑，避免白花额度。",
        ],
    },
    "video": {
        "title": "这个页面是干什么的：抖音视频无水印下载（四合一自动识别）",
        "steps": [
            "强烈建议先去「设置」页用内置浏览器扫码登录抖音，拿到 Cookie 后再回来（能明显提高解析成功率）。",
            "在上方输入框粘贴任意内容：视频分享链接 / 博主主页链接 / 博主昵称 / 关键词 / 纯数字视频ID。支持多行混合粘贴。",
            "点「🚀 自动解析」，程序自动识别每一行的类型：主页→列出该博主作品；关键词→搜索视频；视频链接→定位到该视频。",
            "在下方表格点选要下载的视频（点「全选 / 反选」批量选），确认「保存目录」，点「下载选中视频」。",
            "要留档/发客户：点「📊 导出 Excel」，把视频列表（含点赞、时长、发布时间、下载状态、本地文件名、作品链接）导成一份表格；有勾选时它会问你是导勾选的还是全部。",
        ],
        "tips": [
            "下载默认 3 个并发；下载完成后会自动归档到项目素材库（按 平台/博主/月份 分类，同一作品只存一份）。",
            "导出的 Excel 里「作品链接」「无水印地址」是可点的超链接，客户或同事点开就能核对；点赞/时长是真数字，可以直接排序筛选。",
            "竞品主页链接如果解析失败，改用「竞品体检」页或「选题挖掘」页，那两处对博主链接的处理更宽松。",
            "视频链接请用视频下载页；博主主页链接请用竞品体检页——两者搞混会提示解析失败。",
        ],
    },
    "live": {
        "title": "这个页面是干什么的：盯直播 + 自动录制回放",
        "steps": [
            "添加监控对象：上方输入博主昵称或直播间链接 → 点「添加监控」（界面上方也支持「搜索博主 → 选中并添加」）。",
            "设置「轮询间隔」（建议 30–60 秒），勾上「开播自动录制」可无人值守，然后点「开始监控」。",
            "想手动录：在下方表格选中主播（可按住 Ctrl 多选）→ 点「● 录制选中主播」，程序会自动解析直播流地址，你不用知道流地址。",
            "停止：选中该行 → 点「■ 停止选中录制」，然后【等约 10 秒】——程序要让 ffmpeg 写完索引文件，完成后会弹窗列出保存路径和文件大小。",
            "看进度：录制中的那一行，「录制时长」会每秒跳动、左边还有一个闪烁的 ● 指示灯；页面左下角也会实时显示「● 正在录制 · 已录 00:12:34」。",
        ],
        "tips": [
            "「录制时长」从 ffmpeg 真正开始拉流那一刻起算（不是从你点按钮开始算），所以它和你录出来的文件时长是对得上的。",
            "如果时长显示 00:00 长时间不动，先看左下角指示灯是否在闪：在闪说明录制正常，只是这一行没绑定到主播；手动粘贴流地址录制时，先选中表格里对应主播再点录制，这一行就会亮起来。",
            "录制中的文件是 .mp4.part，只有停止并校验通过才会变成 .mp4；所以「录到一半的 mp4」不会出现在目录里，这是有意设计。",
            "点停止后状态栏会短暂显示「正在停止…」，属正常；若超过 1 分钟仍在停，程序会提示，可再点一次或关掉程序（关窗时会自动收尾，不会留孤儿进程）。",
            "ffmpeg 未就绪时点「指定 ffmpeg...」选择本机 ffmpeg.exe（只需设置一次，之后自动记住）。",
            "「高级选项：手动粘贴直播流 URL 录制」是给已知 m3u8/flv 地址的高级用户用的，普通用户不用管。",
        ],
    },
    "competitor": {
        "title": "这个页面是干什么的：一次看清竞品账号的家底",
        "steps": [
            "准备竞品清单：抖音主页分享短链 / 主页链接 / sec_uid，每行一个（一次建议不超过 20 个）。",
            "粘贴到左侧输入框，点「开始体检」，程序会逐个取粉丝、作品数、获赞、近期更新频率。",
            "等表格出数据后，重点看：平均互动、赞粉比（互动率）、近 30 天更新数、爆款 Top5。",
            "点「导出 Excel」得到固定 10 列的干净表格，可直接贴进客户方案或汇报里。",
        ],
        "tips": [
            "这里用【主页链接】；如果你粘的是某个视频的链接，程序会提示你改用「视频下载」页，因为视频链接里没有博主信息。",
            "分享出来的整段文案（含「长按复制此条消息…」）也可以直接粘，程序会自动把链接挑出来。",
            "某个账号体检失败时，先点表格对应行看失败原因；如果全部失败，先回「设置」页点「测试连接」。",
        ],
    },
    "topic": {
        "title": "这个页面是干什么的：找到「跟我同量级却爆了」的选题",
        "steps": [
            "第一次用先点「接口自检」，让程序实测三个平台的返回结构是否对得上（对齐过一次就行）。",
            "在关键词框填行业词，例如：按摩棒、美容仪、筋膜枪（多个词用逗号隔开），选平台（抖音/快手/小红书）。",
            "点「挖掘低粉爆款」，程序按【爆粉比 = 点赞 ÷ 粉丝】排序 —— 这个指标比绝对点赞数有用得多，能找出小号大爆款。",
            "选中感兴趣的选题 → 点「加入对标监控」（后续在「复盘监控」页持续跟踪），或点「复制链接」去原视频拆解。",
        ],
        "tips": [
            "评论区挖掘是免费选题库：用户在评论里问什么、骂什么、想要什么，直接就是下一条内容的方向。",
            "翻页是自动的（抖音按条目偏移，其它平台按页码），不用自己算 offset。",
            "小红书标题取的是笔记标题，抖音没有标题会回落到文案首句，比较时注意口径差异。",
        ],
    },
    "watch": {
        "title": "这个页面是干什么的：盯自己的号有没有被限流，盯竞品什么时候更新",
        "steps": [
            "先「添加监控账号」：把要跟的账号 sec_uid / 主页链接加进来（自营号看异动，竞品号看更新）。",
            "点「抓取快照」，程序记录每个账号当前的作品数据，并与上一次快照对比。",
            "看下方两个列表：「数据异动预警」会标出疑似限流（增速断崖）、数据停滞、未进推荐池；「竞品新作品」发现即提醒。",
            "每天固定时间抓一次（例如每晚 21:00），连续抓 3 天以上，判断才稳。",
        ],
        "tips": [
            "第一次抓快照只建立基线，不会报「新作品」，这是避免误报的设计。",
            "爆款/限流的判定看的是【增速变化】而不是绝对值，所以至少要有两次快照相隔数小时以上。",
            "「加入对标监控」也会出现在这里 —— 选题挖掘页看到值得跟的号，一键就能加进来。",
        ],
    },
    "report": {
        "title": "这个页面是干什么的：把监控数据变成能直接发给客户的周报",
        "steps": [
            "先在「复盘监控」页把要写进报告的账号加进监控，并至少抓过一次快照。",
            "选好项目和时间范围（本周 / 本月）。",
            "点「生成周报」，程序汇总账号数据、算出最佳发布时段，并给出人话结论。",
            "点「导出 Excel」（多页签，适合自己复盘）或「导出 HTML」（版面好看，可直接发客户）。",
        ],
        "tips": [
            "报告里的结论是按数据自动生成的，交付前花 2 分钟改成你自己的口吻，客户观感会好很多。",
            "数据不足时会明确提示「样本不足」，不要拿这种报告去见客户。",
            "报告默认存到当前项目目录下，方便按客户归档。",
        ],
    },
    "settings": {
        "title": "这个页面是干什么的：Token、抖音登录、ffmpeg、授权激活、软件更新，都在这里配",
        "steps": [
            "在 dashboard.justoneapi.com 拿到 API Token，粘到「API Token」里，点「测试连接」看到 ✓ 才算配好。（如果买的是带接口的授权码，激活后这一栏会自动填好，不用管）",
            "抖音登录：点「打开内置浏览器登录抖音」→ 在弹出的窗口里用手机扫码 → 程序自动检测登录态并把 Cookie 存下来。",
            "ffmpeg（直播录制必需）：点「指定 ffmpeg...」选择本机 ffmpeg.exe，选一次就永久记住。",
            "按需调整「超时 / 最大重试 / 最大并发」，点「保存设置」。设置存在本机，换电脑要重配。",
            "授权与激活：这里能看到本机机器码和试用剩余天数。买到授权码后，粘贴进来点「立即激活」即可；也可以直接导入 .lic 文件。",
            "软件更新：填好更新地址后，程序会在启动时自动检查新版本，有更新点一下就自动装好，不用重新下载安装包。",
        ],
        "tips": [
            "Token 是计费的，且会过期；任何「全部解析失败」的报障，第一步都是回这里点「测试连接」。",
            "抖音登录状态会过期（通常数周），视频解析或直播录制开始频繁失败时回来重新扫码即可。",
            "不要把 Token 截图或发给别人；它等同于你的钱包。",
            "机器码是本机唯一标识（换电脑/重装系统会变），买授权时把这串码发给作者；一码一机。",
            "试用期内所有功能都能用满，到期后会提示激活 —— 试用状态不会影响你已有的 Token、Cookie 和已下载的转写引擎。",
        ],
    },
    "transcribe": {
        "title": "这个页面是干什么的：把采集来的视频 / 直播录像，在本地转成文字（音频不外传）",
        "steps": [
            "选设备：CPU 转录的引擎已随程序自带，开箱即用；想快 10 倍以上且有 N 卡，就选「显卡(CUDA)」并点「🔍 检测显卡并一键安装」——程序会自动识别显卡、匹配并装好对应的 CUDA 运行环境（只装一次）。",
            "点「下载模型」把选中的模型拉到本机（只下一次，之后一直可用）：显存 8G 以上选 large-v3-turbo、4–8G 选 medium、2–4G 选 small；纯 CPU 建议 base。",
            "加文件：点「添加文件」挑本地视频，或点「扫描录制/下载目录」把直播录制 / 视频下载目录里的视频一次性拉进列表。",
            "点「开始转写」等进度跑完，txt 会生成在输出目录（默认与源视频同目录），点「打开输出目录」就能拿到文案和直播话术去分析。",
        ],
        "tips": [
            "全程在本机算，音频不会上传到任何服务器；没显卡也能用，选 CPU 即可，只是慢 10 倍左右。",
            "引擎和模型存在 AppData 目录里，换 exe 不用重装；模型 tiny/base 几分钟就能下完，medium/large 体积大要等更久。",
            "识别中文请把「语言」保持 zh；纯英文素材再改成 auto 或 en。勾「同时生成 srt」可得到带时间轴的字幕。",
            "自动下载太慢时（GitHub 在国内时快时慢），可以用迅雷 / Motrix 先把包下好，再点「导入本地引擎包」或「导入本地模型」。",
            "显卡模式第一次运行要初始化 CUDA，会多等十几秒；如果报显存不足，换小一档模型重试。",
        ],
    },
}

# 标题栏上显示的短标签（表格化的 tab 名 → 引导标题前缀）
_TAB_LABEL = {
    "collect": "数据采集",
    "batch": "批量采集",
    "video": "视频下载",
    "live": "直播监控",
    "competitor": "竞品体检",
    "topic": "选题挖掘",
    "watch": "复盘监控",
    "report": "周报",
    "settings": "设置",
    "transcribe": "音视频转文本",
}


# ----------------------------------------------------------------------
#  组件
# ----------------------------------------------------------------------


class GuidePanel(ttk.Frame):
    """可折叠的使用指导面板。

    Args:
        parent:  父容器
        key:     GUIDES 里的键
        app:     主应用（用于持久化折叠状态，可传 None）
    """

    def __init__(self, parent, key: str, app=None):
        super().__init__(parent)
        self.key = key
        self.app = app
        g = GUIDES.get(key)
        if not g:
            # 没有文案就不占空间，避免留一个空框
            return

        collapsed = False
        if app is not None:
            try:
                collapsed = key in (app.settings.get("guide_collapsed") or [])
            except Exception:            # noqa: BLE001
                collapsed = False

        label = _TAB_LABEL.get(key, "")
        head_text = f"📖  {label}·使用说明（点这里展开 / 收起）"
        self.head_btn = ttk.Button(
            self, text=head_text, command=self.toggle, takefocus=False
        )
        self.head_btn.pack(fill="x", padx=6, pady=(6, 0))

        self.body = ttk.Frame(self)
        self.text = tk.Text(
            self.body, wrap="word", height=self._height(g),
            font=("Microsoft YaHei UI", 9), relief="solid", borderwidth=1,
            background="#fbfbfb", foreground="#222222",
            padx=10, pady=6, cursor="arrow",
        )
        self.text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(self.body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        self._fill(g)
        self.text.configure(state="disabled")   # 只读，但可滚动、可复制

        if not collapsed:
            self.body.pack(fill="x", padx=6, pady=(0, 2))
        self._sync_head()

    # -- 内部 ---------------------------------------------------------

    @staticmethod
    def _height(g: dict) -> int:
        """指导区展开后的可视行数。

        刻意压到 6 行以内（超出部分用面板内部的滚动条查看）：
        「选题挖掘 / 复盘监控 / 直播监控」这几个页面本身内容就高，
        指导区一旦占掉 200 多像素，底部的数据表格就被压得看不见了。
        宁可真需要时滚动看一眼，也不要挤坏主界面。
        """
        n = len(g.get("steps") or []) + len(g.get("tips") or []) + 1
        return max(4, min(n, 6))

    def _fill(self, g: dict):
        t = self.text
        t.tag_configure("title", font=("Microsoft YaHei UI", 9, "bold"),
                        foreground="#1565c0", spacing3=4)
        t.tag_configure("step", font=("Microsoft YaHei UI", 9), lmargin1=4,
                        lmargin2=18, spacing1=2)
        t.tag_configure("warn", font=("Microsoft YaHei UI", 9), foreground="#b26a00",
                        lmargin1=4, lmargin2=18, spacing1=2)
        t.tag_configure("gap", font=("Microsoft YaHei UI", 3))

        t.insert("end", g.get("title", "") + "\n", "title")
        for i, s in enumerate(g.get("steps") or [], 1):
            t.insert("end", f"{i}. {s}\n", "step")
        tips = g.get("tips") or []
        if tips:
            t.insert("end", "\n", "gap")
            for tip in tips:
                t.insert("end", f"⚠ {tip}\n", "warn")

    def _sync_head(self):
        label = _TAB_LABEL.get(self.key, "")
        shown = bool(self.body.winfo_manager())
        arrow = "▾" if shown else "▸"
        self.head_btn.config(text=f"{arrow}  📖  {label}·使用说明（点这里展开 / 收起）")

    # -- 对外 ---------------------------------------------------------

    def toggle(self):
        if self.body.winfo_manager():
            self.body.pack_forget()
            collapsed = True
        else:
            self.body.pack(fill="x", padx=6, pady=(0, 2))
            collapsed = False
        self._sync_head()
        self._persist(collapsed)

    def _persist(self, collapsed: bool):
        app = self.app
        if app is None:
            return
        try:
            cur = set(app.settings.get("guide_collapsed") or [])
            if collapsed:
                cur.add(self.key)
            else:
                cur.discard(self.key)
            app.settings["guide_collapsed"] = sorted(cur)
            persist = getattr(app, "_persist_settings_quiet", None)
            if callable(persist):
                persist()
        except Exception:                # noqa: BLE001
            pass


def attach_guide(parent, key: str, app=None) -> GuidePanel | None:
    """给一个标签页顶部挂上使用指导面板。没有对应文案时返回 None。"""
    if key not in GUIDES:
        return None
    panel = GuidePanel(parent, key, app)
    panel.pack(fill="x")
    return panel
