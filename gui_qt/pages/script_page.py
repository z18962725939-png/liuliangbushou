"""文案仿写页：拆开爆款看结构，产出能照着开拍的分镜稿。

干什么：
    把一条跑通的对标视频文案丢进来，程序先拆出它的钩子 / 转折 / 促单与语速，
    再按你选的体裁骨架（口播 / 剧情 / Vlog / 种草带货 / 测评开箱 / 教程干货 /
    情感共鸣 / 访谈对话）重新写一版，最后过一遍广告法违禁词，给出分镜表、
    标题备选、话题标签和质检评分。

怎么用：
    1. 粘贴对标视频链接（本地语音转写成口播稿，自动算出真实语速与前 3 秒钩子）
       —— 或者直接在「对标原文」粘文案，两者任选
    2. 选体裁、时长，填产品与卖点（语气 / 创意度 / 版本数在「高级选项」里，都有默认值）
    3. 选引擎并填接口 Key（与 GEO 页共用，存本机）
    4. 点「开始仿写」→ 看评分挑版本 → 导出 Excel / Markdown

三条口径（界面上也写了，别删）：
    · 仿的是结构与节奏，不是措辞 —— 输出是全新表达，不是原句改写
    · 生成失败就是没生成出来（限流 / Key / 网络），绝不能当成「这个选题不行」
    · 合规只能拦词库里的词，发布前仍需人工过一遍（尤其医疗、金融、食品类目）
"""
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)
from gui_qt.widgets.progress import FlowProgressBar

from ._base import (CollapseBox, StatusLine, Worker, append_log, connection_diagnosis,
                    form_row, guide_banner, log_panel, page_title, vertical_card)

from justoneapi_collector import licensing
from justoneapi_collector import script_rewriter as sr
from justoneapi_collector import video_script_extract as vse
from justoneapi_collector.transcribe import (MODELS, bootstrap_engine,
                                             engine_problem, install_model,
                                             model_ready, status_text)

# ★ 2026-10-31 重建补回：下面这些 import 在源码反向重建时丢失，
#   页面一打开就 NameError（按 pyflakes 的 undefined name 定位）。
from gui_qt.widgets.kit import DisclosurePanel, RulerSlider, Segmented
from ._base import require_feature

RESULT_COLUMNS = ["版本", "体裁", "评分", "字数", "预估时长", "标题", "钩子",
                  "违规词", "状态"]

# 提取结果表：一眼能看出「这条值不值得仿」
EXTRACT_COLUMNS = ["作者", "平台", "时长", "字数", "实测口播", "语速", "来源", "状态"]

# 每种体裁在界面上的一句话说明（下拉框里显示，帮客户选对分类）
_GENRE_HINT = {
    "talk": "一个人讲观点 / 知识 / 行业内幕",
    "story": "情景短剧，靠冲突与反转推进",
    "vlog": "日常 / 探店 / 旅行，靠画面代入",
    "sell": "卖货口播，痛点开场、演示促单",
    "review": "测评开箱，结论前置讲可信度",
    "tutorial": "步骤教学，结果前置好收藏",
    "emotion": "情绪共鸣，金句 + 小故事",
    "interview": "一问一答，靠尖锐提问出增量",
}


def _split_lines(text: str) -> list[str]:
    out: list[str] = []
    for s in re.split(r"[\r\n,，;；]+", str(text or "")):
        s = s.strip()
        if s and s not in out:
            out.append(s)
    return out


class ScriptPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._stop = False
        self._result = None
        self._items = []          # list[vse.ExtractedCopy] 提取结果
        self._extractor = None
        self._src_segments = []   # 当前原文的时间轴片段（粘贴文本时为空）
        self._build_ui()
        self._refresh_gate()
        self._refresh_genre_hint()
        self._refresh_estimate()
        self._refresh_asr_status()
        # 开箱即用：内置 CPU 转写引擎需要先释放到本机，首次进页时静默做掉
        QTimer.singleShot(0, lambda: Worker(lambda w: bootstrap_engine("cpu")).start())

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("短视频文案仿写"))
        root.addWidget(guide_banner("文案仿写", self))

        self.gate_hint = QLabel("")
        self.gate_hint.setWordWrap(True)
        self.gate_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        root.addWidget(self.gate_hint)

        # ★ 2026-10-27 改版：原来六张卡从上往下叠到 2900+ px —— 客户要滚三屏
        #   才能从「粘原文」走到「点开始」，而中间那屏全是"以后都不会再看一眼"
        #   的接口 Key 和日志。仿写本来就是三步的事，按步骤切成三档，一次只
        #   显示一段，每档都能一屏装下。
        #   （Segmented + setVisible 切换，与视频下载页同一套写法）
        self.step_seg = Segmented(
            [("① 找素材", "input"), ("② 调参数并生成", "make"),
             ("③ 看结果", "result")], "light", self)
        self.step_seg.changed.connect(self._sync_step)
        seg_row = QHBoxLayout()
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.addWidget(self.step_seg)
        seg_row.addStretch()
        root.addLayout(seg_row)

        self._view_input = QWidget(self)
        self._view_make = QWidget(self)
        self._view_result = QWidget(self)
        self._lay_input = QVBoxLayout(self._view_input)
        self._lay_make = QVBoxLayout(self._view_make)
        self._lay_result = QVBoxLayout(self._view_result)
        for lay in (self._lay_input, self._lay_make, self._lay_result):
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
        self._view_make.setVisible(False)
        self._view_result.setVisible(False)
        root.addWidget(self._view_input)
        root.addWidget(self._view_make)
        root.addWidget(self._view_result)

        self._build_extract_card(self._lay_input)

        # ---------------- 对标原文 ----------------
        c1, l1 = vertical_card("对标原文（只学结构，不许抄句）", self)
        self.source = QPlainTextEdit()
        self.source.setFixedHeight(140)
        self.source.setTabChangesFocus(True)
        self.source.setPlaceholderText(
            "把跑通的对标视频文案粘进来（口播稿 / 转写稿 / 字幕都行）。\n"
            "没有原文也能写：留空就按体裁骨架直接原创。")
        self.source.textChanged.connect(self._on_source_edited)
        l1.addWidget(self.source)

        r1 = QHBoxLayout()
        r1.setSpacing(10)
        self.import_txt_btn = QPushButton("从 txt / md 导入")
        self.import_txt_btn.setToolTip("读取本地文本文件（转写稿可直接用）")
        self.import_txt_btn.clicked.connect(self._import_text)
        r1.addWidget(self.import_txt_btn)
        self.import_xlsx_btn = QPushButton("从 Excel 导入")
        self.import_xlsx_btn.setToolTip("优先读「文案 / 标题 / 内容」列，没有就读第一列")
        self.import_xlsx_btn.clicked.connect(self._import_excel)
        r1.addWidget(self.import_xlsx_btn)
        self.clear_src_btn = QPushButton("清空原文")
        self.clear_src_btn.clicked.connect(lambda: self.source.setPlainText(""))
        r1.addWidget(self.clear_src_btn)
        r1.addStretch()
        l1.addLayout(r1)

        self.src_info = QLabel("")
        self.src_info.setWordWrap(True)
        self.src_info.setStyleSheet("color:#6B7280;font-size:12px;")
        l1.addWidget(self.src_info)
        self._lay_input.addWidget(c1)

        # ---------------- 仿写设置 ----------------
        c2, l2 = vertical_card("仿写设置", self)
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        self.genre_box = QComboBox()
        for key in sr.GENRE_ORDER:
            g = sr.GENRES[key]
            self.genre_box.addItem(f"{g.label}　·　{_GENRE_HINT.get(key, '')}", key)
        self.genre_box.setFixedWidth(300)
        self.genre_box.currentIndexChanged.connect(self._refresh_genre_hint)
        r2.addLayout(form_row("体裁", self.genre_box))
        r2.addStretch()
        l2.addLayout(r2)

        self.genre_hint = QLabel("")
        self.genre_hint.setWordWrap(True)
        self.genre_hint.setStyleSheet(
            "color:#374151;font-size:12px;background:#F9FAFB;padding:8px;border-radius:6px;")
        l2.addWidget(self.genre_hint)

        # 主区只留真正影响结果的四项：体裁 / 时长 / 产品 / 卖点。
        # 语气、创意度、版本数、人群这些都"能调但不必调"，收进高级选项里，
        # 免得新客户以为每个框都得填才敢点开始。
        #
        # ★ 2026-10-17 三次改版：时长从下拉框换成**刻度尺**。
        #   原因很实在：15/30/60/90 四个档位里最常用的 30~45 秒区间，
        #   下拉框根本给不出来（只有 30 和 60 两个点）。刻度尺把 15~90 秒
        #   每 5 秒一格全摊开，拖动时右下角的字数预估实时跟着变 ——
        #   客户是在"调一个连续的量"，不是在"从四个选项里挑一个"。
        dur_head = QHBoxLayout()
        dur_head.setContentsMargins(0, 0, 0, 0)
        dur_head.setSpacing(8)
        dlab = QLabel("目标时长")
        dlab.setMinimumWidth(96)
        dlab.setStyleSheet("color:#374151;")
        dur_head.addWidget(dlab)
        self.dur_tip = QLabel("左右拖动刻度尺，松手自动吸附到 5 秒")
        self.dur_tip.setStyleSheet("color:#9CA3AF;font-size:11px;")
        dur_head.addWidget(self.dur_tip)
        dur_head.addStretch()
        # 拖动时实时跟着变的那个数：客户拖的是"秒"，真正关心的是"要念多少字"。
        # 把它放在尺子同一行右侧，视线不用来回跳（参考照片编辑里亮度数值跟手）。
        self.dur_live = QLabel("")
        # ★ 颜色交给 QSS（#LiveChip），不能在这里写死：#F2F3F5 是浅色主题的
        #   槽色，深色下会变成一块白底 + 近白字的"白块"（实拍踩过）。
        self.dur_live.setObjectName("LiveChip")
        dur_head.addWidget(self.dur_live)
        l2.addLayout(dur_head)

        self.dur_ruler = RulerSlider(15, 90, 5, "light", self, unit="秒")
        self.dur_ruler.set_value(30)
        self.dur_ruler.valueChanged.connect(lambda _v: self._refresh_estimate())
        l2.addWidget(self.dur_ruler)

        self.product = QLineEdit()
        self.product.setPlaceholderText("例如：某某按摩棒 / XX 工厂（会写进提示词）")
        l2.addLayout(form_row("产品 / 品牌", self.product))

        r2d = QHBoxLayout()
        r2d.setSpacing(10)
        plab = QLabel("卖点")
        plab.setMinimumWidth(96)
        plab.setStyleSheet("color:#374151;")
        r2d.addWidget(plab, 0, Qt.AlignmentFlag.AlignTop)
        self.points = QPlainTextEdit()
        self.points.setFixedHeight(58)
        self.points.setTabChangesFocus(True)
        self.points.setPlaceholderText("一行一个，必须讲到的卖点。例：三档力度 / 静音 45 分贝 / Type-C 充电")
        r2d.addWidget(self.points, 1)
        l2.addLayout(r2d)

        # ---------------- 高级选项（默认收起） ----------------
        self.adv = CollapseBox("高级选项：语气 / 创意度 / 版本数 / 人群 / 必含词（都有默认值，不调也能写）")
        r2c = QHBoxLayout()
        r2c.setSpacing(10)
        self.tone_box = QComboBox()
        for k, v in sr.TONES.items():
            self.tone_box.addItem(v.split("：")[0], k)
        self.tone_box.setFixedWidth(140)
        r2c.addLayout(form_row("语气", self.tone_box))
        self.temp_box = QComboBox()
        self.temp_box.addItem("稳（保守，适合品牌号）", "steady")
        self.temp_box.addItem("活（推荐，有变化）", "lively")
        self.temp_box.addItem("野（发散，挑着用）", "wild")
        self.temp_box.setCurrentIndex(1)
        self.temp_box.setFixedWidth(200)
        r2c.addLayout(form_row("创意度", self.temp_box))
        self.ver_box = QComboBox()
        self.ver_box.addItems(["1 版（最快）", "2 版", "3 版（推荐，好挑）"])
        self.ver_box.setCurrentIndex(2)
        self.ver_box.setFixedWidth(160)
        self.ver_box.currentIndexChanged.connect(self._refresh_estimate)
        r2c.addLayout(form_row("版本数", self.ver_box))
        r2c.addStretch()
        self.adv.add(r2c)

        self.audience = QLineEdit()
        self.audience.setPlaceholderText("例如：久坐上班族、产后妈妈、小店老板")
        self.adv.add(form_row("目标人群", self.audience))
        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText("文案里必须出现的词，逗号分隔（可留空）")
        self.adv.add(form_row("必含词", self.keywords))
        self.forbidden = QLineEdit()
        self.forbidden.setPlaceholderText("你自己行业要避开的词，逗号分隔（广告法常用词已内置拦截）")
        self.adv.add(form_row("额外禁用词", self.forbidden))
        l2.addWidget(self.adv)
        self._lay_make.addWidget(c2)

        # ---------------- 引擎与 Key ----------------
        # ★ 2026-10-27：接口 Key 这节默认收起。它是全页唯一会让新客户迟迟
        #   不敢点「开始」的东西（"我是不是还得去注册个 API"），而实际上
        #   配一次能用很久 —— 折叠起来既省高度，也让流程看着是一条直线。
        self.key_panel = DisclosurePanel("AI 引擎与接口 Key",
                                         meta="存在本机，不外传",
                                         mode="light", parent=self)
        key_body = QWidget(self.key_panel)
        l3 = QVBoxLayout(key_body)
        l3.setContentsMargins(0, 0, 0, 0)
        l3.setSpacing(10)
        self.key_panel.add_widget(key_body)

        r3 = QHBoxLayout()
        r3.setSpacing(10)
        self.engine_box = QComboBox()
        for key, spec in sr.gm.ENGINE_SPECS.items():
            if spec.kind != "api":
                continue
            self.engine_box.addItem(spec.label, key)
        self.engine_box.setFixedWidth(180)
        self.engine_box.currentIndexChanged.connect(self._refresh_key_edit)
        r3.addLayout(form_row("引擎", self.engine_box))
        r3.addStretch()
        l3.addLayout(r3)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("在对应平台注册即得，费用从你的 Key 里扣，本软件不加价")
        l3.addLayout(form_row("接口 Key", self.key_edit))
        self._refresh_key_edit()

        r3b = QHBoxLayout()
        self.save_key_btn = QPushButton("保存 Key")
        self.save_key_btn.clicked.connect(self._save_key)
        r3b.addWidget(self.save_key_btn)
        self.key_hint = QLabel("Key 与 GEO 页共用一份，存在本机配置里")
        self.key_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        r3b.addWidget(self.key_hint)
        r3b.addStretch()
        l3.addLayout(r3b)
        self._lay_make.addWidget(self.key_panel)

        # ---------------- 运行 ----------------
        c4, l4 = vertical_card("生成", self)
        r4 = QHBoxLayout()
        self.run_btn = QPushButton("开始仿写")
        self.run_btn.setObjectName("Primary")
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self._run)
        r4.addWidget(self.run_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_run)
        r4.addWidget(self.stop_btn)
        self.export_btn = QPushButton("导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_excel)
        r4.addWidget(self.export_btn)
        self.md_btn = QPushButton("导出 Markdown")
        self.md_btn.setEnabled(False)
        self.md_btn.clicked.connect(self._export_md)
        r4.addWidget(self.md_btn)
        self.hist_btn = QPushButton("历史仿写")
        self.hist_btn.clicked.connect(self._show_history)
        r4.addWidget(self.hist_btn)
        r4.addStretch()
        l4.addLayout(r4)

        self.progress = FlowProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        l4.addWidget(self.progress)
        self.status = StatusLine("")
        self.status.mark("idle")
        self.status.setWordWrap(True)
        l4.addWidget(self.status)
        self.estimate = QLabel("")
        self.estimate.setStyleSheet("color:#6B7280;font-size:12px;")
        self.estimate.setWordWrap(True)
        l4.addWidget(self.estimate)
        self._lay_make.addWidget(c4)

        # ---------------- 结果 ----------------
        c5, l5 = vertical_card("结果与质检", self)
        self.summary = QLabel("还没有生成过文案")
        self.summary.setStyleSheet("color:#374151;font-size:13px;font-weight:600;")
        self.summary.setWordWrap(True)
        l5.addWidget(self.summary)

        self.table = QTableWidget(0, len(RESULT_COLUMNS))
        self.table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(180)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._show_selected)
        l5.addWidget(self.table)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(280)
        self.detail.setPlaceholderText("点上面任意一行，这里显示该版的完整分镜稿")
        l5.addWidget(self.detail)

        r5 = QHBoxLayout()
        self.copy_btn = QPushButton("复制口播全文")
        self.copy_btn.setToolTip("只复制要念的话，不含时间轴与画面说明")
        self.copy_btn.clicked.connect(self._copy_spoken)
        r5.addWidget(self.copy_btn)
        self.copy_all_btn = QPushButton("复制完整分镜稿")
        self.copy_all_btn.clicked.connect(self._copy_detail)
        r5.addWidget(self.copy_all_btn)
        r5.addStretch()
        l5.addLayout(r5)
        self._lay_result.addWidget(c5)

        # 日志收起来：它是"出错时才看"的东西，原来常年占着 200px。
        # ★ 保持在最后 —— 追加日志时控件隐藏与否都不会报错，
        #   完全不需要 append_log 那边配合。
        self.log = log_panel()
        self.log_panel_box = DisclosurePanel("运行日志", meta="出错时才需要看",
                                             mode="light", parent=self)
        self.log_panel_box.add_widget(self.log)
        self._lay_make.addWidget(self.log_panel_box)

        # 每档末尾垫弹簧：单档比窗口矮时，多余高度会钻进卡片内部（QLabel 的
        # 垂直策略是 Preferred，会被拉长），看上去像一块莫名其妙的空白。
        for _lay in (self._lay_input, self._lay_make, self._lay_result):
            _lay.addStretch(1)

    # ------------------------------------------------- ① 链接提取卡片
    def _build_extract_card(self, root):
        """「链接 → 口播稿」这一段的界面。

        本地转写，视频不出本机 —— 对标视频常常是同行的爆款，传到第三方 ASR
        平台等于替对手做备份。
        """
        c0, l0 = vertical_card("① 从视频链接提取文案（本地语音转写，视频不出本机）", self)
        tip = QLabel("粘贴作品链接，程序解析后把声音转成文字，再交给下面拆结构。"
                     "第一次用需要先下载一个转写模型（约 140 MB）。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6B7280;font-size:12px;")
        l0.addWidget(tip)

        self.link_edit = QPlainTextEdit()
        self.link_edit.setFixedHeight(66)
        self.link_edit.setTabChangesFocus(True)
        self.link_edit.setPlaceholderText(
            "粘贴抖音 / 小红书 / 快手 / 视频号 作品链接，一行一个；"
            "TikTok 请在浏览器打开视频后复制地址栏的完整链接；"
            "也支持整段分享文案和 19 位作品 ID。")
        l0.addWidget(self.link_edit)

        r0 = QHBoxLayout()
        r0.setSpacing(10)
        self.extract_btn = QPushButton("提取文案")
        self.extract_btn.setObjectName("Primary")
        self.extract_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.extract_btn.clicked.connect(self._extract_start)
        r0.addWidget(self.extract_btn)
        self.extract_stop_btn = QPushButton("停止")
        self.extract_stop_btn.setEnabled(False)
        self.extract_stop_btn.clicked.connect(self._extract_stop)
        r0.addWidget(self.extract_stop_btn)
        self.load_btn = QPushButton("载入选中到原文")
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self._load_selected)
        r0.addWidget(self.load_btn)
        self.clear_link_btn = QPushButton("清空")
        self.clear_link_btn.clicked.connect(self._clear_links)
        r0.addWidget(self.clear_link_btn)
        r0.addStretch()
        l0.addLayout(r0)

        r0b = QHBoxLayout()
        r0b.setSpacing(10)
        self.model_box = QComboBox()
        for key, info in MODELS.items():
            label_ready = "（已下载）" if model_ready(key) else f"（需下载 {info['size_mb']} MB）"
            self.model_box.addItem(f"{key}　·　{info['desc']} {label_ready}", key)
        self.model_box.setFixedWidth(340)
        # 默认 base：tiny 中文准确率不够用来仿写，small 起步又要下 465 MB，
        # 141 MB 的 base 是"装得下就用"的平衡点，也能后续再换。
        saved_model = str(self.ctx.get_setting("script_asr_model") or "").strip() or "base"
        saved_dev = str(self.ctx.get_setting("script_asr_device") or "").strip() or "cpu"
        idx = self.model_box.findData(saved_model)
        if idx >= 0:
            self.model_box.setCurrentIndex(idx)
        self.model_box.currentIndexChanged.connect(self._save_asr_pref)
        r0b.addLayout(form_row("转写模型", self.model_box))
        self.install_btn = QPushButton("安装模型")
        self.install_btn.clicked.connect(self._install_model)
        r0b.addWidget(self.install_btn)

        saved_dev = str(self.ctx.get_setting("script_asr_device") or "").strip() or "cpu"
        self.device_box = QComboBox()
        # ★ 选项文案要短：下拉的 minimumSizeHint 按**最长那条 item** 算，
        #   setFixedWidth 挡不住（brand 自检 F 段就是量这个）。原来写
        #   「显卡 CUDA（快 10 倍，需 N 卡）」光这一项就 252px，把整张卡顶出
        #   横向滚动条 —— 「快 10 倍」挪到 tooltip 里，信息一点没少。
        self.device_box.addItem("CPU（都能跑）", "cpu")
        self.device_box.addItem("显卡 CUDA（需 N 卡）", "cuda")
        self.device_box.setToolTip(
            "显卡转写大约快 10 倍；没有 N 卡就选 CPU，一样能跑完，只是慢一些。")
        didx = self.device_box.findData(saved_dev)
        if didx >= 0:
            self.device_box.setCurrentIndex(didx)
        self.device_box.currentIndexChanged.connect(self._save_asr_pref)
        r0b.addLayout(form_row("设备", self.device_box))
        r0b.addStretch()
        l0.addLayout(r0b)

        r0c = QHBoxLayout()
        r0c.setSpacing(10)
        self.ffmpeg_label = QLabel("ffmpeg：检测中")
        self.ffmpeg_label.setStyleSheet("color:#6B7280;font-size:12px;")
        r0c.addWidget(self.ffmpeg_label)
        b_ff = QPushButton("指定 ffmpeg...")
        b_ff.clicked.connect(self._choose_ffmpeg)
        r0c.addWidget(b_ff)
        self.keep_video_cb = QCheckBox("转写后保留视频文件")
        self.keep_video_cb.setChecked(False)
        self.keep_video_cb.setToolTip("默认转完就删，省硬盘；勾上会留在程序数据目录里")
        r0c.addWidget(self.keep_video_cb)
        self.auto_fit_cb = QCheckBox("载入时自动套用体裁建议与相近时长")
        self.auto_fit_cb.setChecked(True)
        r0c.addWidget(self.auto_fit_cb)
        r0c.addStretch()
        l0.addLayout(r0c)

        self.extract_progress = FlowProgressBar()
        self.extract_progress.setRange(0, 100)
        self.extract_progress.setValue(0)
        self.extract_progress.setTextVisible(True)
        l0.addWidget(self.extract_progress)
        self.asr_status = QLabel("")
        self.asr_status.setWordWrap(True)
        self.asr_status.setStyleSheet("color:#6B7280;font-size:12px;")
        l0.addWidget(self.asr_status)

        self.extract_table = QTableWidget(0, len(EXTRACT_COLUMNS))
        self.extract_table.setHorizontalHeaderLabels(EXTRACT_COLUMNS)
        self.extract_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.extract_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.extract_table.verticalHeader().setVisible(False)
        self.extract_table.setMinimumHeight(110)
        hh0 = self.extract_table.horizontalHeader()
        hh0.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh0.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.extract_table.itemSelectionChanged.connect(
            lambda: self.load_btn.setEnabled(self.extract_table.currentRow() >= 0))
        l0.addWidget(self.extract_table)
        root.addWidget(c0)

    # ------------------------------------------------------------ 跨页入口
    def accept_links(self, links, auto_start: bool = True) -> int:
        """接收别的页面送来的作品链接（选题挖掘 / 关键词抓取的「写同款文案」）。

        把「复制 → 切页 → 粘贴 → 点提取」四步压成一步。填完链接可选直接开跑，
        这样从结果表点一下，人就到了正在提取的文案仿写页。
        """
        got = [str(x).strip() for x in (links or []) if str(x).strip()]
        if not got:
            return 0
        # 别的页面送链接过来时，客户当前可能在「③ 结果」档 —— 不切回去的话
        # 他会看到"什么都没发生"。分档之后这一步必须有。
        self.step_seg.set_value("input")
        self.link_edit.setPlainText("\n".join(got))
        if auto_start:
            self._extract_start()
        return len(got)

    # ------------------------------------------------------------ 分档
    def _sync_step(self, key: str):
        self._view_input.setVisible(key == "input")
        self._view_make.setVisible(key == "make")
        self._view_result.setVisible(key == "result")

    def apply_theme(self, mode: str):
        """主窗口切主题时广播到这里（用了 kit 自绘控件就必须实现）。"""
        self.step_seg.apply_theme(mode)
        self.key_panel.apply_theme(mode)
        self.log_panel_box.apply_theme(mode)

    # ------------------------------------------------------------ 小工具
    def _refresh_gate(self):
        """功能门禁提示：授权里不含「文案仿写」就明确告知，不静默失败。

        ★ v1.21.0：按钮不再禁用。禁用 = 点了没反应，用户只会以为软件坏了；
          保持可点，由 _extract_start() / _run() 弹统一的「付费须知」。
        """
        ok, why = licensing.check("script")
        if ok:
            self.gate_hint.setText("")
            return
        self.gate_hint.setStyleSheet("color:#B45309;font-size:12px;font-weight:600;")
        self.gate_hint.setText(f"⚠ {why}")

    def _genre_key(self) -> str:
        return self.genre_box.currentData() or "talk"

    def _refresh_genre_hint(self):
        """切换体裁时展示它的结构骨架 —— 让客户知道「这一类该怎么写」。"""
        g = sr.GENRES.get(self._genre_key(), sr.GENRES["talk"])
        secs = self.dur_ruler.value() or 30
        lines = [f"{g.desc}　·　语速约 {g.wps} 字/秒 → {secs} 秒 ≈ {int(secs * g.wps)} 字", ""]
        for i, (name, duty, ratio) in enumerate(g.structure, 1):
            lines.append(f"{i}. {name}（约 {max(1, round(secs * ratio))} 秒）：{duty}")
        lines.append("")
        lines.append("常用钩子：" + " / ".join(g.hooks[:3]))
        self.genre_hint.setText("\n".join(lines))
        self._refresh_estimate()

    def _on_source_edited(self):
        """手改了原文，时间轴就对不上了，扔掉（程序写入时信号被屏蔽，不受影响）。"""
        self._src_segments = []
        self._refresh_source_info()

    def _refresh_source_info(self):
        text = self.source.toPlainText().strip()
        if not text:
            self.src_info.setText("未填对标原文：将按体裁骨架直接原创")
            return
        g = sr.GENRES.get(self._genre_key(), sr.GENRES["talk"])
        a = sr.analyze_source(text, g.wps, self._src_segments)
        self.src_info.setText("原文拆解：" + a.summary())

    def _refresh_key_edit(self):
        key = self.engine_box.currentData() or "deepseek"
        saved = str((self.ctx.get_setting("geo_api_keys", {}) or {}).get(key, "") or "")
        self.key_edit.setText(saved)
        spec = sr.gm.ENGINE_SPECS.get(key)
        if spec is not None:
            self.key_edit.setPlaceholderText(f"{spec.label} 的接口 Key（{spec.note}）")

    def _save_key(self):
        keys = dict(self.ctx.get_setting("geo_api_keys", {}) or {})
        keys[self.engine_box.currentData() or "deepseek"] = self.key_edit.text().strip()
        self.ctx.settings["geo_api_keys"] = keys
        if self.ctx.save_settings():
            self.key_hint.setText("Key 已保存到本机")
            self.key_hint.setStyleSheet("color:#2E7D32;font-size:12px;")
        else:
            self.key_hint.setText("保存失败（配置写入被拒绝）")
            self.key_hint.setStyleSheet("color:#DC2626;font-size:12px;")

    def _api_keys(self) -> dict[str, str]:
        keys = dict(self.ctx.get_setting("geo_api_keys", {}) or {})
        v = self.key_edit.text().strip()
        if v:
            keys[self.engine_box.currentData() or "deepseek"] = v
        return keys

    def _refresh_estimate(self):
        spec = self._spec()
        est = sr.estimate_cost(spec)
        wps = spec.genre_spec.wps
        try:
            self.dur_live.setText(f"≈ {spec.target_chars} 字")
        except Exception:                                    # noqa: BLE001
            pass
        self.estimate.setText(
            f"本次生成 {est['calls']} 版，每版口播约 {spec.target_chars} 字"
            f"（{spec.seconds} 秒 × {wps} 字/秒），预计 {est['seconds']:.0f} 秒左右。"
            "费用从你填的 Key 里扣，本软件不加价；失败的版会单独标出，不会拿模板文字充数。")

    def _spec(self) -> sr.RewriteSpec:
        return sr.RewriteSpec(
            genre=self._genre_key(),
            seconds=int(self.dur_ruler.value() or 30),
            tone=self.tone_box.currentData() or "pro",
            audience=self.audience.text().strip(),
            product=self.product.text().strip(),
            points=_split_lines(self.points.toPlainText()),
            keywords=_split_lines(self.keywords.text()),
            forbidden_extra=_split_lines(self.forbidden.text()),
            versions=[1, 2, 3][max(0, min(self.ver_box.currentIndex(), 2))],
            engine=self.engine_box.currentData() or "deepseek",
            temperature=self.temp_box.currentData() or "lively",
        )

    # ---------------------------------------------------- ① 链接提取
    def _ffmpeg_setting(self) -> str:
        return str(self.ctx.get_setting("ffmpeg_path") or "").strip()

    def _refresh_asr_status(self):
        """把「现在能不能转」摆在明面上 —— 让客户在开跑之前就知道缺什么。"""
        ff = vse.find_ffmpeg(self._ffmpeg_setting())
        # ★ 只显示文件名，完整路径放悬浮提示。
        #   原写法直接嵌完整路径（C:\Users\…\ffmpeg.exe 有 60~80 字符），
        #   QLabel 的 sizeHint 会把这一行撑到比窗口还宽，整页出现横向滚动条、
        #   右侧控件被切掉 —— 2026-10-17 视觉改版截图时才发现。
        #   客户只需要知道"找没找到"，不需要逐字读路径。
        if ff:
            import os as _os
            name = _os.path.basename(str(ff)) or "已找到"
            self.ffmpeg_label.setText(f"ffmpeg：{name}")
            self.ffmpeg_label.setToolTip(str(ff))
            self.ffmpeg_label.setStyleSheet("color:#2E7D32;font-size:12px;")
        else:
            self.ffmpeg_label.setText("ffmpeg：未找到（点右边指定）")
            self.ffmpeg_label.setToolTip("")
            self.ffmpeg_label.setStyleSheet("color:#C62828;font-size:12px;")
        device = self.device_box.currentData() or "cpu"
        model = self.model_box.currentData() or "small"
        text = status_text(device, model)
        problem = engine_problem(device)
        if problem:
            if device == "cuda" and not engine_problem("cpu"):
                # 显卡引擎坏了不该吓人：CPU 能跑就先跑，把话说清楚
                text += ("\n⚠ 显卡转写引擎缺运行库，现在开跑会自动改用 CPU"
                         "（结果完全一样，只是慢一些）。想用显卡请到「音视频转文本」"
                         "页点「检测显卡并一键安装」重装一次（约 439 MB）。")
            else:
                text += "\n⚠ " + problem.replace("\n", " ")
            self.asr_status.setStyleSheet("color:#B45309;font-size:12px;")
        else:
            self.asr_status.setStyleSheet("color:#6B7280;font-size:12px;")
        self.asr_status.setText(text)

    def _save_asr_pref(self):
        """记住上次选的模型和设备，别让客户每次进来都重选一遍。"""
        self.ctx.settings["script_asr_model"] = self.model_box.currentData() or "base"
        self.ctx.settings["script_asr_device"] = self.device_box.currentData() or "cpu"
        self.ctx.save_settings()
        self._refresh_asr_status()

    def _choose_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg 可执行文件（抽音轨要用）", "",
            "ffmpeg (ffmpeg.exe);;所有文件 (*.*)")
        if not path:
            return
        self.ctx.settings["ffmpeg_path"] = path
        self.ctx.save_settings()
        if self.ctx.downloader is not None:
            try:
                self.ctx.downloader.ffmpeg_path = path
            except Exception:                                   # noqa: BLE001
                pass
        self._refresh_asr_status()

    def _install_model(self):
        key = self.model_box.currentData() or "small"
        if model_ready(key):
            QMessageBox.information(self, "已经就绪",
                                    f"模型 {key} 已经在本机了，不用再下载。")
            return
        self.install_btn.setEnabled(False)

        def task(worker):
            return install_model(key, progress=lambda p, m: worker.emit_progress(p, m),
                                 log=lambda m: worker.emit_log(m))

        w = Worker(task)
        w.progress.connect(lambda p, m: (self.extract_progress.setValue(int(p or 0)),
                                         self.asr_status.setText(m)))
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(lambda r: self._model_installed(bool(r[0]), key, str(r[1])))
        w.error.connect(lambda e: (append_log(self.log, f"模型安装失败：{e}"),
                                   self._model_installed(False, key, str(e))))
        w.start()

    def _model_installed(self, ok: bool, key: str, msg: str):
        self.install_btn.setEnabled(True)
        self._refresh_asr_status()
        # 下拉框里写死了"已下载"标记，装完要重建一遍，否则客户以为还没装
        cur = self.model_box.currentData()
        for i in range(self.model_box.count()):
            k = self.model_box.itemData(i)
            info = MODELS.get(k, {})
            label_ready = "（已下载）" if model_ready(k) else f"（需下载 {info.get('size_mb','?')} MB）"
            self.model_box.setItemText(i, f"{k}　·　{info.get('desc','')} {label_ready}")
        idx = self.model_box.findData(cur)
        if idx >= 0:
            self.model_box.setCurrentIndex(idx)
        if ok:
            self.asr_status.setText(f"模型 {key} 已就绪，可以开始提取了")
        else:
            self.asr_status.setStyleSheet("color:#DC2626;font-size:12px;")
            self.asr_status.setText(f"模型 {key} 下载失败：{msg}")

    def _expr_opt(self) -> vse.ExtractOptions:
        return vse.ExtractOptions(
            model=self.model_box.currentData() or "small",
            device=self.device_box.currentData() or "cpu",
            lang="zh",
            keep_video=self.keep_video_cb.isChecked(),
            ffmpeg=self._ffmpeg_setting() or None,
        )

    def _extract_start(self):
        # ★ 付费闸门（v1.21.0）：提取口播稿 = 下载 + 本地转写 + 后续仿写，
        #   整条都是「文案仿写」付费功能。放在最前面，别等下载完才拦。
        if not require_feature(self, "script", "提取原文口播稿"):
            return
        links = vse.split_links(self.link_edit.toPlainText())
        if not links:
            QMessageBox.information(self, "没有链接",
                                    "先把作品链接粘进去（一行一个），或整段分享文案也行。")
            return
        if len(links) > 30:
            QMessageBox.warning(
                self, "一次别太多",
                f"一次贴了 {len(links)} 条链接。每条都要下载加本地转写，"
                "建议一次不超过 30 条，多了分批来（已经提取过的是走缓存，不重复转写）。")
            return

        dm = self.ctx.downloader or self.ctx.ensure_downloader()
        if dm is None:
            title, detail = connection_diagnosis()
            QMessageBox.warning(self, title, detail)
            return

        ex = vse.ScriptExtractor(dm, self._expr_opt())
        ok, why = ex.preflight()
        if not ok:
            QMessageBox.warning(self, "还不能开始转写", why)
            return
        # preflight 可能因为显卡引擎缺运行库把 device 降级成 cpu。
        # 界面要跟着变，否则下拉框还写着"显卡"，用户会以为还是显卡在跑。
        # blockSignals 是必须的：否则会触发 _save_asr_pref 把降级写进偏好，
        # 等引擎修好了也还是默认 CPU。
        if ex.opt.device != (self.device_box.currentData() or "cpu"):
            idx = self.device_box.findData(ex.opt.device)
            if idx >= 0:
                self.device_box.blockSignals(True)
                self.device_box.setCurrentIndex(idx)
                self.device_box.blockSignals(False)
            append_log(self.log, "显卡引擎不可用，本次改用 CPU 转写")
            self._refresh_asr_status()

        self._extractor = ex
        self._items = []
        self.extract_table.setRowCount(0)
        self.extract_btn.setEnabled(False)
        self.extract_stop_btn.setEnabled(True)
        self.load_btn.setEnabled(False)
        self.extract_progress.setValue(2)
        append_log(self.log, f"[提取] 开始：{len(links)} 条链接")

        def task(worker):
            return ex.extract_many(
                links,
                progress=lambda p, m: worker.emit_progress(p, m),
                log=lambda m: worker.emit_log(m))

        w = Worker(task)
        w.progress.connect(lambda p, m: (self.extract_progress.setValue(
            max(0, min(100, int(p or 0)))), self.asr_status.setText(m or "")))
        w.log.connect(lambda m: append_log(self.log, m))
        w.done.connect(self._on_extract_done)
        w.error.connect(lambda e: (
            append_log(self.log, f"提取出错：{e}"),
            self.asr_status.setText(f"提取出错：{e}")))
        w.finished.connect(self._extract_finished)
        w.start()

    def _extract_stop(self):
        self.extract_stop_btn.setEnabled(False)
        self.asr_status.setText("正在停止…（正在跑的转写会被收掉，已提取的前几条仍然保留）")
        try:
            if self._extractor is not None:
                self._extractor.cancel()
        except Exception:                                       # noqa: BLE001
            pass

    def _extract_finished(self):
        self.extract_btn.setEnabled(True)
        self.extract_stop_btn.setEnabled(False)
        self._refresh_gate()

    def _on_extract_done(self, items):
        self._items = list(items or [])
        self.extract_table.setRowCount(len(self._items))
        okn = 0
        for i, it in enumerate(self._items):
            if it.ok:
                okn += 1
            row = [
                it.author or "—",
                it.platform_label or it.platform or "—",
                f"{it.video_seconds:.0f}s" if it.video_seconds else "—",
                str(it.chars or "—"),
                f"{it.speech_seconds:.0f}s" if it.speech_seconds else "—",
                f"{it.real_wps:.1f}" if it.real_wps else "—",
                it.source_label,
                "成功" if it.ok else (it.msg or "失败"),
            ]
            for c, val in enumerate(row):
                cell = QTableWidgetItem(str(val))
                if not it.ok and c == 7:
                    cell.setForeground(QBrush(QColor("#DC2626")))
                elif it.source == "desc" and c == 6:
                    cell.setForeground(QBrush(QColor("#B45309")))
                self.extract_table.setItem(i, c, cell)

        self.extract_progress.setValue(100)
        fails = [f"{it.author or it.url[:24]}：{it.msg}" for it in self._items if not it.ok]
        if okn == 0:
            why = fails[0] if fails else "一条都没提取出来"
            self.asr_status.setStyleSheet("color:#DC2626;font-size:12px;")
            self.asr_status.setText(f"全部失败：{why}")
            return

        self.asr_status.setStyleSheet("color:#2E7D32;font-size:12px;font-weight:600;")
        self.asr_status.setText(
            f"提取完成：成功 {okn}/{len(self._items)} 条。"
            + ("失败原因见表格「状态」列。" if fails else "点表格一行 → 「载入选中到原文」。"))
        # 只提了一条就直接替他载入，少点一下鼠标
        if okn == 1:
            first = next(i for i, it in enumerate(self._items) if it.ok)
            self.extract_table.selectRow(first)
            self._load_selected()
        else:
            self.load_btn.setEnabled(True)

    def _load_selected(self):
        row = self.extract_table.currentRow()
        if not (0 <= row < len(self._items)):
            return
        item = self._items[row]
        if not item.ok or not item.text.strip():
            QMessageBox.warning(self, "这条没能提取出文案",
                                item.msg or "未知原因（见表格状态列）")
            return

        if self.auto_fit_cb.isChecked():
            g = sr.guess_genre(item.text, item.desc)
            idx = self.genre_box.findData(g)
            if idx >= 0:
                self.genre_box.setCurrentIndex(idx)
            want = item.speech_seconds or item.video_seconds
            if want:
                # 刻度尺能吃连续值：直接把实测到的时长吸附进去（内部裁到 15~90
                # 并对齐 5 秒），比以前"在 15/30/60/90 里挑最近的一个"准得多
                self.dur_ruler.set_value(int(round(want)), animate=True)

        self._set_source(item.text, item.segments)
        self.step_seg.set_value("make")
        # 载入完成就顺手把流程推到下一步：要调的参数都在「② 调参数并生成」档，
        # 让客户自己去找第二步，等于把"四步压成一步"的努力又吐回去一半。
        if item.source == "desc":
            self.status.mark("warn")
            self.status.setText(
                "注意：这条只拿到了作者写的标题，没转写到音轨 —— "
                "结构参考价值有限，建议换一条有人说话的视频再提取一次。")
        else:
            self.status.mark("idle")
            self.status.setText(
                f"已载入 {item.author or '这条'} 的口播稿：{item.chars} 字"
                + (f" / 实测 {item.speech_seconds:.0f} 秒" if item.speech_seconds else ""))

    def _clear_links(self):
        self.link_edit.setPlainText("")
        self.extract_table.setRowCount(0)
        self._items = []
        self.load_btn.setEnabled(False)
        self.extract_progress.setValue(0)
        self.asr_status.setText("")

    def _set_source(self, text: str, segments=None):
        """写进原文框；blockSignals 是为了别触发「用户手动编辑」那条清理逻辑。"""
        self._src_segments = list(segments or [])
        self.source.blockSignals(True)
        self.source.setPlainText(text or "")
        self.source.blockSignals(False)
        self._refresh_source_info()

    # ------------------------------------------------------------ 导入
    def _import_text(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入原文", "", "文本文件 (*.txt *.md *.srt)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        # srt 字幕里的时间轴与序号对仿写没有意义，直接剥掉
        if path.lower().endswith(".srt"):
            lines = []
            for line in text.splitlines():
                s = line.strip()
                if not s or s.isdigit() or "-->" in s:
                    continue
                lines.append(s)
            text = "\n".join(lines)
        self.source.setPlainText(text.strip())
        self.status.setText(f"已导入：{path}")

    def _import_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入原文", "", "表格文件 (*.xlsx *.xls *.csv)")
        if not path:
            return
        try:
            rows = self._read_text_column(path)
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, "导入失败", f"读不了这个文件：{e}")
            return
        if not rows:
            QMessageBox.information(self, "没有读到内容", "没在表里找到可用的文案列。")
            return
        self.source.setPlainText("\n".join(rows))
        self.status.setText(f"已导入 {len(rows)} 条文案")

    @staticmethod
    def _read_text_column(path: str) -> list[str]:
        """优先读「文案 / 标题 / 内容 / 正文」列，没有就读第一列。"""
        wanted = ("文案", "标题", "内容", "正文", "口播", "字幕", "描述")
        p = str(path).lower()
        if p.endswith(".csv"):
            import csv
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                return [r[0].strip() for r in csv.reader(f) if r and r[0].strip()]
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        head = [(str(c or "")).strip() for c in rows[0]]
        idx = next((i for i, h in enumerate(head) if any(w in h for w in wanted)), None)
        out: list[str] = []
        for row in (rows[1:] if idx is not None else rows):
            if idx is None or idx >= len(row):
                cell = row[0] if row else None
            else:
                cell = row[idx]
            if cell:
                s = str(cell).strip()
                if s and s.lower() != "none":
                    out.append(s)
        return out

    # ------------------------------------------------------------ 运行
    def _run(self):
        # ★ 付费闸门（v1.21.0）：仿写要调大模型接口，每次都有实际成本
        if not require_feature(self, "script", "文案仿写"):
            return
        spec = self._spec()
        source = self.source.toPlainText().strip()
        engine_key = spec.engine
        if not (self._api_keys().get(engine_key) or "").strip():
            QMessageBox.warning(
                self, "缺少接口 Key",
                f"还没填 {sr.gm.ENGINE_SPECS[engine_key].label} 的接口 Key。\n\n"
                "在上面「接口 Key」里填写并点「保存 Key」后再生成，"
                "否则一个字也出不来。")
            return

        self._stop = False
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.export_btn.setEnabled(False)
        self.md_btn.setEnabled(False)
        self.progress.setValue(3)
        self.status.mark("idle")
        self.status.setText("仿写中…")
        append_log(self.log, f"[仿写] 开始：{spec.genre_spec.label} / "
                             f"{spec.seconds}秒 / {spec.versions} 版")

        keys = self._api_keys()
        models = dict(self.ctx.get_setting("geo_models", {}) or {})

        def task(worker):
            def on_prog(done, total, text):
                pct = (done / total * 100.0) if total else 0.0
                worker.emit_progress(pct, text)

            return sr.rewrite(source=source, spec=spec, api_keys=keys, models=models,
                              progress=on_prog, should_stop=lambda: self._stop,
                              segments=self._src_segments)

        w = Worker(task)
        w.progress.connect(self._on_progress)
        w.log.connect(lambda m: append_log(self.log, m) if m else None)
        w.done.connect(self._on_done)
        w.error.connect(lambda e: self.status.setText(f"仿写出错：{e}"))
        w.finished.connect(self._on_finished)
        w.start()

    def _on_progress(self, pct, msg):
        self.progress.setValue(int(pct))
        if msg:
            self.status.setText(msg)

    def _stop_run(self):
        self._stop = True
        self.stop_btn.setEnabled(False)
        self.status.setText("正在停止…（已发出的请求会跑完）")

    def _on_finished(self):
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setValue(100)
        self._refresh_gate()

    def _on_done(self, result):
        self._result = result
        # ★ 分档后必须自己把客户送到结果档：原来结果就在按钮下面，生成完
        #   抬头就能看见；现在结果在另一档，不切过去等于"点了什么都没发生"。
        #   失败也切 —— 失败原因写在结果表格上方，总得让他看见。
        self.step_seg.set_value("result")
        rows = result.items
        self.table.setRowCount(len(rows))
        for i, d in enumerate(rows):
            r = d.to_row()
            for c, col in enumerate(RESULT_COLUMNS):
                cell = QTableWidgetItem(str(r.get(col, "")))
                if not d.ok:
                    cell.setForeground(QBrush(QColor("#DC2626")))
                elif d.violations and col in ("违规词",):
                    cell.setForeground(QBrush(QColor("#B45309")))
                self.table.setItem(i, c, cell)

        ok = result.ok_count
        self.export_btn.setEnabled(ok > 0)
        self.md_btn.setEnabled(ok > 0)
        if ok == 0:
            self.summary.setStyleSheet("color:#DC2626;font-size:13px;font-weight:600;")
            err = (result.errors[0] if result.errors else "未知原因")
            self.summary.setText(f"一版都没生成出来：{err}")
            self.status.setText("生成失败：" + err)
            self.detail.setPlainText(
                "排查顺序：1) Key 是否有效、有没有余额；2) 网络 / 代理能不能连到接口；"
                "3) 是否触发限流（换引擎或减少版本数再试）。\n\n"
                "注意：生成失败只说明这次没拿到结果，不代表这个选题不行 —— "
                "排除故障后重跑即可。")
            return

        best = result.best()
        self.summary.setStyleSheet("color:#2E7D32;font-size:13px;font-weight:600;")
        warn = f"　⚠ {result.failed_count} 版失败" if result.failed_count else ""
        self.summary.setText(
            f"已生成 {ok} 版，最高分 {best.score} 分（第 {best.index} 版，"
            f"{best.chars} 字 / 约 {best.est_seconds:.0f} 秒）{warn}")
        self.status.mark("ok")
        self.status.setText("仿写完成：点表格任意一行看全文，红色为失败、橙色为命中违禁词")
        if rows:
            self.table.selectRow(0)
        try:
            sr.save_history(result)
        except Exception as e:                                   # noqa: BLE001
            append_log(self.log, f"[仿写] 历史保存失败：{e}")

    # ------------------------------------------------------------ 展示
    def _selected_draft(self):
        if self._result is None:
            return None
        row = self.table.currentRow()
        if 0 <= row < len(self._result.items):
            return self._result.items[row]
        return None

    def _show_selected(self):
        d = self._selected_draft()
        if d is None:
            return
        if d.error:
            self.detail.setPlainText(f"第 {d.index} 版生成失败：{d.error}\n\n"
                                     "（这版没有内容，不是「生成了但不好」，请排除故障后重跑）")
            return
        self.detail.setPlainText(self._render(d))

    def _render(self, d) -> str:
        spec = self._result.spec if self._result else sr.RewriteSpec()
        parts = [f"【第 {d.index} 版】{d.genre_label}　{d.seconds} 秒　"
                 f"质检 {d.score} 分　{d.chars} 字（约 {d.est_seconds:.0f} 秒）", ""]
        if d.title_options:
            parts.append("标题备选：" + " | ".join(d.title_options))
            parts.append("")
        if d.hook:
            parts.append(f"钩子（前 3 秒）：{d.hook}")
            parts.append("")
        parts.append("分镜：")
        for s in d.shots:
            tl = s.get("时间轴") or "-"
            pic = s.get("画面") or ""
            spk = s.get("口播") or ""
            parts.append(f"  {tl}　画面：{pic}　口播：{spk}")
        parts.append("")
        if d.cta:
            parts.append(f"结尾引导：{d.cta}")
        if d.hashtags:
            parts.append("话题：" + " ".join(("#" + h.lstrip("#"))
                                            for h in d.hashtags))
        parts.append("")
        if d.violations:
            parts.append("【合规提醒｜发布前必须改】")
            for v in d.violations:
                parts.append(f"  · 「{v['词']}」属{v['类别']}，建议改为：{v['建议']}")
        else:
            parts.append("【合规】未命中内置违禁词（医疗 / 金融 / 食品类目请再人工核一遍）")
        parts.append("")
        parts.append("【质检明细】")
        parts += [f"  · {t}" for t in d.score_detail]
        return "\n".join(parts)

    def _copy_spoken(self):
        d = self._selected_draft()
        if d is None or not d.ok:
            self.status.setText("先选一个生成成功的版本")
            return
        QApplication.clipboard().setText(d.spoken)
        self.status.setText("已复制口播全文（可直接粘去提词器）")

    def _copy_detail(self):
        text = self.detail.toPlainText().strip()
        if not text:
            self.status.setText("还没有可复制的内容")
            return
        QApplication.clipboard().setText(text)
        self.status.setText("已复制完整分镜稿")

    # ------------------------------------------------------------ 导出 / 历史
    def _export_excel(self):
        if self._result is None or self._result.ok_count == 0:
            self.status.setText("还没有可导出的结果")
            return
        safe = re.sub(r'[\\/:*?"<>|]+', "_",
                      self._result.spec.product or "文案").strip() or "文案"
        name = f"短视频文案仿写_{safe}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出文案", name, "Excel (*.xlsx)")
        if not path:
            return
        try:
            sr.export_excel(self._result, path)
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.status.mark("ok")
        self.status.setText(f"已导出：{path}")
        try:
            from justoneapi_collector.support import open_path
            open_path(path)
        except Exception:                                        # noqa: BLE001
            pass

    def _export_md(self):
        if self._result is None or self._result.ok_count == 0:
            self.status.setText("还没有可导出的结果")
            return
        safe = re.sub(r'[\\/:*?"<>|]+', "_",
                      self._result.spec.product or "文案").strip() or "文案"
        name = f"短视频文案仿写_{safe}.md"
        path, _ = QFileDialog.getSaveFileName(self, "导出 Markdown", name, "Markdown (*.md)")
        if not path:
            return
        try:
            sr.export_markdown(self._result, path)
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))
            return
        self.status.mark("ok")
        self.status.setText(f"已导出：{path}")

    def _show_history(self):
        items = sr.load_history(limit=20)
        if not items:
            QMessageBox.information(self, "历史仿写",
                                    "还没有历史记录。生成成功后会自动存一份在本机，"
                                    "换电脑不跟着走。")
            return
        lines = []
        for it in items:
            sp = it.get("spec") or {}
            scores = [f"{d.get('score', 0)}分" for d in it.get("items", [])
                      if not d.get("error")]
            lines.append(f"{it.get('started_at', '')}　{sp.get('product') or '未命名'}　"
                         f"{sp.get('genre_label', '')}　{sp.get('seconds', '')}秒　"
                         + ("、".join(scores) or "全部失败"))
        QMessageBox.information(self, "历史仿写（最近 %d 次）" % len(items), "\n".join(lines))
