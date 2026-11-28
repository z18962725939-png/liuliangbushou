"""设置页（PySide6）。

包含：API 配置、抖音/小红书/快手/TikTok 扫码登录、授权与激活、软件更新、反馈与日志。
业务逻辑复用 justoneapi_collector 的 licensing / updater / support / browser_login 模块。
"""
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QTextEdit, QVBoxLayout,
                               QWidget)

from ._base import (StatusLine, Worker, append_log, card, connection_diagnosis,
                    connect_guarded, form_row, guide_banner, log_panel,
                    page_title, row_widget, vertical_card)

import justoneapi_collector.branding as branding
import justoneapi_collector.licensing as licensing
import justoneapi_collector.updater as updater
import justoneapi_collector.support as support
import justoneapi_collector.browser_login as bl

# 扫码登录线程与 Cookie 键名规则已抽到 gui_qt/login_gate.py —— 启动时的
# 「强制登录门」与设置页共用同一套逻辑，避免两处实现漂移（2026-10-11）。
from gui_qt.login_gate import BrowserLoginWorker as _LoginWorker  # noqa: E402
from gui_qt.login_gate import cookie_key                          # noqa: E402
from gui_qt.widgets.kit import ChoiceFlow, Segmented                   # noqa: E402
# P0 匿名故障摘要：diagnostics 是纯逻辑模块（不依赖 Qt），LogDialog 是自绘
# 中文弹窗（双主题已处理）。两者都放顶部 import，PyInstaller 静态可见。
from gui_qt import diagnostics                                         # noqa: E402
from gui_qt.widgets.log_export import LogDialog                        # noqa: E402


class SettingsPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._login_threads = {}
        self._app_data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "流量捕手"
        self._build_ui()
        self._refresh_license()
        # 启动时把上次已保存的登录态回显出来（旧版只在登录成功那一刻显示，
        # 重启后就变回「未登录」，客户以为登录白做了）
        self._restore_saved_logins()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(14)
        root.addWidget(page_title("设置"))
        root.addWidget(guide_banner("设置", self))

        # ★ 2026-10-27 改版：六张卡一次全铺开 = 2200+ px，客户要改一个"保存
        #   目录"得先滚过偏好问卷、接口参数、扫码登录、机器码、反馈表单。
        #   设置是"一次只为一件事而来"的地方，按组切开（苹果系统设置就是这套
        #   二级结构），选谁显示谁。
        #   ★ 免费版的档位另给一套：它没有"接口与性能 / 授权"这两回事，
        #     摆出来只会让客户以为"得先配点什么才能用"（这是真投诉）。
        self._group_opts = (
            [("保存位置", "save"), ("平台登录", "login"),
             ("关于这个免费版", "about"), ("更新与反馈", "system")]
            if branding.APP_IS_FREE else
            [("内容偏好", "pref"), ("平台登录", "login"),
             ("授权与激活", "license"), ("接口与性能", "api"),
             ("更新与反馈", "system")])
        self.group_seg = Segmented(self._group_opts, "light", self)
        self.group_seg.changed.connect(self._sync_group)
        grow = QHBoxLayout()
        grow.setContentsMargins(0, 0, 0, 0)
        grow.addWidget(self.group_seg)
        grow.addStretch()
        root.addLayout(grow)

        # ★ 容器一律全建、跟版本无关：免费版的档位里没有「内容偏好 / 接口」，
        #   但卡片照样要塞进某个容器。按版本裁 traced 的话一走岔就是
        #   KeyError('pref') —— 而 Qt 槽里的异常打包后是静默的，客户看到的
        #   只是"设置页是空的"。多建两个空容器的成本可以忽略。
        self._views, self._lays = {}, {}
        for key in ("pref", "save", "login", "license", "api", "about",
                    "system"):
            w = QWidget(self)
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(14)
            w.setVisible(False)          # 由 _sync_group 打开第一档
            self._views[key] = w
            self._lays[key] = lay
            root.addWidget(w)
        self._sync_group(self._group_opts[0][1])

        # ---------- 内容偏好（分步选择） ----------
        # ★ 2026-10-17 三次改版新增
        #   客户给的第三个参考是「兴趣引导页：点标签切换选中态，选够才亮下一步」。
        #   放在设置页而不是首次启动弹窗：它是**可以改的偏好**，不是一次性问卷。
        #   两个作用：① 让「内容偏好」这件事有个像样的入口（原来散落在各处）；
        #   ② 记下来之后工作台和选题页能按它推荐，不用每次从 8 个体裁里挑。
        self.pref_card, self._pref_l = vertical_card("内容偏好", self)
        self._pref_host = QWidget(self.pref_card)
        self._pref_v = QVBoxLayout(self._pref_host)
        self._pref_v.setContentsMargins(0, 0, 0, 0)
        self._pref_v.setSpacing(0)
        # ★ 2026-11-23：必须把 host 挂进卡片布局。只设 parent 不进 layout
        #   的控件是"游离子控件"，Qt 会把它挤成 0 尺寸 —— 客户看到的就是
        #   「内容偏好」整页空白，只有左上角一丝残影（真实投诉截图）。
        self._pref_l.addWidget(self._pref_host)
        self._lays["pref"].addWidget(self.pref_card)
        self._render_pref()
        # ★ 免费版（2026-10-23）：「内容偏好」是给工作台/选题页做推荐用的，
        #   免费版这两个页面都没有。留着就是一张"选满 3 个才能下一步"的死卡
        #   —— 客户会以为不选完就用不了。直接隐藏（控件照建，refresh 里还在引用）。
        if branding.APP_IS_FREE:
            self.pref_card.setVisible(False)

        # ---------- 接口与性能 ----------
        # 说明：接口凭证（Token）已加密内置在授权码里，客户无需也无法自己填写，
        # 所以这里不再提供 Token 输入框 —— 激活授权码即可联网使用。
        api, al = vertical_card("接口与性能", self)
        s = self.ctx.settings
        # ★ 2026-11-23：付费数据通道默认为 okdata；JustOneAPI 作为可切回的
        #   原通道保留。视频直链解析始终走 video.zacao.top，与这里无关。
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("okdata（按次计费，推荐）", "okdata")
        self.provider_combo.addItem("JustOneAPI（原通道）", "justoneapi")
        current_provider = str(s.get("api_provider", "okdata") or "okdata")
        idx = self.provider_combo.findData(current_provider)
        self.provider_combo.setCurrentIndex(max(0, idx))
        self.okdata_key_edit = QLineEdit(str(s.get("okdata_key") or ""))
        self.okdata_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.okdata_key_edit.setPlaceholderText("okd_ 开头，在 okdata 控制台获取")
        self.base_url_edit = QLineEdit(s.get("base_url", "https://api.justoneapi.com"))
        self.timeout_edit = QLineEdit(str(s.get("timeout", 120)))
        self.retries_edit = QLineEdit(str(s.get("max_retries", 3)))
        self.concurrency_edit = QLineEdit(str(s.get("max_concurrency", 5)))
        self.cache_chk = QCheckBox("启用缓存（仅 JustOneAPI 通道；okdata 自带逐笔账单与服务端缓存）")
        self.cache_chk.setChecked(bool(s.get("cache_enabled", True)))

        al.addLayout(form_row("数据服务", self.provider_combo))
        al.addLayout(form_row("okdata Key", self.okdata_key_edit))
        al.addLayout(form_row("API 地址", self.base_url_edit))
        al.addLayout(form_row("超时(秒)", self.timeout_edit))
        al.addLayout(form_row("最大重试", self.retries_edit))
        al.addLayout(form_row("最大并发", self.concurrency_edit))
        al.addWidget(self.cache_chk)
        save_btn = QPushButton("保存设置"); save_btn.setObjectName("Primary")
        connect_guarded(save_btn.clicked, lambda *_: self._save(), on_error=lambda msg: self.fb_status.setText(msg), label="save")
        h = QHBoxLayout(); h.setSpacing(8); h.addWidget(save_btn); h.addStretch()
        al.addLayout(h)
        info = QLabel(
            "okdata：到 okdata.online/zh/console 用邮箱注册（新账号送 $0.10 试用），\n"
            "把 okd_ 开头的 Key 填到上面即可按次计费；余额不过期、无月费。\n"
            "视频直链解析始终走免费的 zacao 通道，抖音开播监控走本机免费浏览器通道，\n"
            "这两项不花 okdata 的钱。API 地址 / 重试 / 并发仅原通道使用。\n"
            "切回 JustOneAPI 时，凭证仍随授权码下发，无需自己填写。\n"
            f"当前版本：v{branding.APP_VERSION}")
        info.setStyleSheet("color:#9CA3AF;font-size:12px;")
        al.addWidget(info)
        self._lays["api"].addWidget(api)
        # ★ 免费版（2026-10-23）：「接口与性能」里的 API 地址 / 超时 / 重试 /
        #   并发全是给付费接口用的，免费版永远不走接口 —— 摆着只会让客户以为
        #   "要先配好接口才能用"。整块隐藏，换成真正会踩坑的那一件事：保存目录。
        if branding.APP_IS_FREE:
            api.setVisible(False)
            self._lays["save"].addWidget(self._build_free_save_card())

        # ---------- 平台登录 ----------
        login, ll = vertical_card("平台扫码登录（解析/下载成功率更高）", self)
        self._login_status = {}
        self._login_btns = {}
        self._logout_btns = {}
        for plat in ("douyin", "xiaohongshu", "kuaishou", "tiktok"):
            cfg = bl.LOGIN_PLATFORMS.get(plat, {})
            row = QHBoxLayout()
            label = QLabel(cfg.get("label", plat))
            label.setFixedWidth(64)
            row.addWidget(label)
            btn = QPushButton("扫码登录")
            # ★ 2026-10-17：与「视频下载」页统一成中性胶囊 #PlatformChip。
            #   原来这里给每个平台套一套品牌色内联样式（抖音粉 / 小红书红 / 快手橙），
            #   内联写法在深色主题下盖不掉白底，而且四行彩色描边把设置页弄得像
            #   一张贴满便利贴的表单。平台靠名字就能认出来，颜色留给品牌自己用。
            btn.setObjectName("PlatformChip")
            btn.setFixedWidth(96)
            disabled = cfg.get("domestic_blocked", False)
            if disabled:
                btn.setEnabled(False)
            else:
                connect_guarded(btn.clicked, lambda *_: self._start_login(p), on_error=lambda msg: self.fb_status.setText(msg), label="start login")
            row.addWidget(btn)
            self._login_btns[plat] = btn
            st = QLabel("未登录" if not disabled else "国内禁用")
            st.setObjectName("Meta")
            self._login_status[plat] = st
            row.addWidget(st)
            out = QPushButton("退出登录")
            out.setFixedWidth(76)
            out.setObjectName("PlatformChip")
            out.setCursor(Qt.CursorShape.PointingHandCursor)
            out.setVisible(False)          # 仅在已登录时出现
            connect_guarded(out.clicked, lambda *_: self._logout(p), on_error=lambda msg: self.fb_status.setText(msg), label="logout")
            self._logout_btns[plat] = out
            row.addWidget(out)
            row.addStretch()
            ll.addLayout(row)
        # 强制登录开关（2026-10-11 新增）：开启后每次启动都会先要求扫码登录
        # 抖音，未登录不能进入软件。登录态能省下大量接口额度，故默认开启；
        # 客户环境实在登不上时可自行关闭，作者也能云端一键关闭（见 login_gate）。
        self.require_login_chk = QCheckBox("要求先登录抖音再使用（未登录时启动会先弹登录窗口）")
        self.require_login_chk.setChecked(bool(s.get("require_login", True)))
        self.require_login_chk.toggled.connect(self._save_require_login)
        ll.addWidget(self.require_login_chk)

        tip = QLabel("登录成功后会自动保存，下次打开软件仍然是「已登录」，无需重复扫码。")
        tip.setStyleSheet("color:#9CA3AF;font-size:12px;")
        ll.addWidget(tip)
        self._lays["login"].addWidget(login)

        # ---------- 授权与激活 ----------
        # ★ 免费版这张卡改成「关于这个免费版」：它不需要也没有激活码，
        #   留着「授权与激活」四个字，客户第一反应就是"还要买？"(2026-10-23)
        lic, lic_l = vertical_card(
            "关于这个免费版" if branding.APP_IS_FREE else "授权与激活", self)

        # 免费版：直接说清这个版本能干嘛、不能干嘛，别让他猜
        if branding.APP_IS_FREE:
            lic_intro = QLabel(
                "这个免费版只做两件事：<b>抖音视频下载</b> 和 <b>直播录制</b>。<br>"
                "不限次数、不消耗任何接口额度，接口欠费也不影响使用。<br>"
                "需要公众号运营、小红书笔记、热榜、关键词、文案仿写、GEO 监测这些功能，<br>"
                "请下载完整版（同一台电脑可以两个都装，互不覆盖）。")
            lic_intro.setWordWrap(True)
            lic_intro.setTextFormat(Qt.TextFormat.RichText)
        else:
            # 注意措辞：采集服务是随授权码下发的，试用期内【连不上采集服务】。
            # 以前这里写「N 天全功能试用」，客户试用时发现什么都采不了，
            # 会觉得被骗 —— 文案必须和实际能力一致（2026-10-12 修正）。
            lic_intro = QLabel(
                f"{branding.APP_NAME} 提供 {licensing.TRIAL_DAYS} 天试用：可浏览全部界面与功能，"
                "采集服务需激活授权码后接入。")
            lic_intro.setStyleSheet("color:#374151;")
        lic_l.addWidget(lic_intro)
        self.lic_status = QLabel("正在读取授权状态…")
        self.lic_status.setStyleSheet("color:#1565c0;font-weight:600;")
        lic_l.addWidget(self.lic_status)

        # 采集服务接入状态 —— 客户最常卡在这一步，直接在设置页给出结论和原因，
        # 不让他点遍所有页面才发现「激活了但用不了」（2026-10-12 客户投诉补的）
        self.svc_status = QLabel("")
        self.svc_status.setWordWrap(True)
        lic_l.addWidget(self.svc_status)

        # 今日调用额度（只统计 JustOneAPI 调用；抖音直连的抓取/下载不占额度）
        self.quota_label = QLabel("")
        self.quota_label.setStyleSheet("color:#374151;font-size:12px;")
        lic_l.addWidget(self.quota_label)

        # 机器码 + 激活码这一整块装进一个 QWidget，免费版整体隐藏。
        # 早期是逐个控件 setVisible(False)，漏一个就露出「激活码：」几个孤字，
        # 客户以为"要激活才能用"（2026-10-23 投诉原话）。
        lic_dev = QWidget()
        dev_l = QVBoxLayout(lic_dev)
        dev_l.setContentsMargins(0, 0, 0, 0)
        dev_l.setSpacing(8)

        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("本机机器码："))
        self.machine_label = QLabel(licensing.machine_id())
        self.machine_label.setStyleSheet("font-family:Consolas;font-weight:700;color:#c62828;")
        mrow.addWidget(self.machine_label)
        copy_btn = QPushButton("复制机器码"); connect_guarded(copy_btn.clicked, lambda *_: self._copy_machine(), on_error=lambda msg: self.fb_status.setText(msg), label="copy machine")
        mrow.addWidget(copy_btn)
        mrow.addStretch()
        dev_l.addLayout(mrow)

        dev_l.addWidget(QLabel("激活码（粘贴后点「立即激活」）："))
        self.lic_input = QTextEdit(); self.lic_input.setMaximumHeight(60)
        dev_l.addWidget(self.lic_input)
        brow = QHBoxLayout()
        act = QPushButton("✔ 立即激活"); act.setObjectName("Primary")
        connect_guarded(act.clicked, lambda *_: self._activate(), on_error=lambda msg: self.fb_status.setText(msg), label="activate")
        imp = QPushButton("📂 从 .lic 导入"); connect_guarded(imp.clicked, lambda *_: self._import_lic(), on_error=lambda msg: self.fb_status.setText(msg), label="import lic")
        ref = QPushButton("刷新状态"); connect_guarded(ref.clicked, lambda *_: self._refresh_license(), on_error=lambda msg: self.fb_status.setText(msg), label="refresh license")
        clr = QPushButton("清除激活"); connect_guarded(clr.clicked, lambda *_: self._clear_lic(), on_error=lambda msg: self.fb_status.setText(msg), label="clear lic")
        brow.addWidget(act); brow.addWidget(imp); brow.addWidget(ref); brow.addWidget(clr)
        brow.addStretch()
        dev_l.addLayout(brow)
        lic_l.addWidget(lic_dev)
        self._lic_dev_box = lic_dev
        if branding.APP_IS_FREE:
            lic_dev.setVisible(False)

        self.lic_hint = QLabel("")
        self.lic_hint.setStyleSheet("color:#6B7280;font-size:12px;")
        self.lic_hint.setWordWrap(True)
        lic_l.addWidget(self.lic_hint)
        # ★ 2026-11-15 修既有 bug：分档改造时这里漏改成 self._lays，
        #   授权卡被直接挂到 root —— 结果「授权与激活」档永远是空的，
        #   而其它每一档底部都多出一张授权卡 + 一大块空白。归位到自己的档。
        self._lays["license"].addWidget(lic)

        # ---------- 软件更新 ----------
        up, up_l = vertical_card("软件更新", self)
        up_l.addWidget(QLabel(
            f"当前版本：v{branding.APP_VERSION}（作者发布新版本后，软件启动会自动检查并弹窗提示，"
            "点「立即更新」即自动下载安装）"))
        urow = QHBoxLayout()
        urow.addWidget(QLabel("更新地址："))
        self.update_url = QLineEdit(self.ctx.get_setting("update_feed_url", branding.UPDATE_FEED_URL or ""))
        urow.addWidget(self.update_url, 1)
        save_url = QPushButton("保存地址"); connect_guarded(save_url.clicked, lambda *_: self._save_update_url(), on_error=lambda msg: self.fb_status.setText(msg), label="save update url")
        urow.addWidget(save_url)
        up_l.addLayout(urow)
        self.update_auto = QCheckBox("启动时自动检查更新")
        self.update_auto.setChecked(bool(self.ctx.get_setting("update_auto_check", True)))
        self.update_auto.toggled.connect(lambda _v: self._save_update_cfg())
        up_l.addWidget(self.update_auto)
        brow2 = QHBoxLayout()
        chk = QPushButton("🔍 立即检查更新"); connect_guarded(chk.clicked, lambda *_: self._check_update(), on_error=lambda msg: self.fb_status.setText(msg), label="check update")
        reset_btn = QPushButton("重置更新提示"); connect_guarded(reset_btn.clicked, lambda *_: self._reset_update_prompt(), on_error=lambda msg: self.fb_status.setText(msg), label="reset update prompt")
        open_dir = QPushButton("打开下载目录"); connect_guarded(open_dir.clicked, lambda *_: updater.open_containing_folder(updater.update_cache_dir()), on_error=lambda msg: self.fb_status.setText(msg), label="open containing folder")
        brow2.addWidget(chk); brow2.addWidget(reset_btn); brow2.addWidget(open_dir)
        brow2.addStretch()
        up_l.addLayout(brow2)
        self.update_status = StatusLine("")
        up_l.addWidget(self.update_status)
        self._lays["system"].addWidget(up)

        # ---------- 反馈与日志 ----------
        fb, fb_l = vertical_card("反馈与日志（遇到问题一键发给作者）", self)
        fb_l.addWidget(QLabel(
            "点「提交反馈」会把【描述 + 最近运行日志 + 环境信息】打成压缩包放到桌面，通过微信发给作者即可。"))
        frow = QHBoxLayout()
        frow.addWidget(QLabel("反馈类型："))
        self.fb_kind = QComboBox()
        self.fb_kind.addItems(["Bug / 报错", "功能建议", "使用问题", "其他"])
        frow.addWidget(self.fb_kind)
        self.fb_logs = QCheckBox("附带最近日志（建议勾选）"); self.fb_logs.setChecked(True)
        frow.addWidget(self.fb_logs); frow.addStretch()
        fb_l.addLayout(frow)
        fb_l.addWidget(QLabel("问题 / 建议描述："))
        self.fb_text = QPlainTextEdit(); self.fb_text.setMaximumHeight(80)
        fb_l.addWidget(self.fb_text)
        brow3 = QHBoxLayout()
        submit = QPushButton("📮 提交反馈"); submit.setObjectName("Primary")
        connect_guarded(submit.clicked, lambda *_: self._submit_feedback(), on_error=lambda msg: self.fb_status.setText(msg), label="submit feedback")
        send_logs = QPushButton("📋 一键发日志"); connect_guarded(send_logs.clicked, lambda *_: self._send_logs(), on_error=lambda msg: self.fb_status.setText(msg), label="send logs")
        open_logs = QPushButton("📂 打开日志目录"); connect_guarded(open_logs.clicked, lambda *_: self._open_logs(), on_error=lambda msg: self.fb_status.setText(msg), label="open logs")
        brow3.addWidget(submit); brow3.addWidget(send_logs); brow3.addWidget(open_logs)
        brow3.addStretch()
        fb_l.addLayout(brow3)
        contact = branding.APP_CONTACT or "（见授权与激活里的联系方式）"
        self.fb_status = StatusLine(f"作者联系方式：{contact}")
        fb_l.addWidget(self.fb_status)
        self._lays["system"].addWidget(fb)

        # ---------- 匿名故障摘要（P0，2026-11-15） ----------
        # 独立一档，不塞进「反馈与日志」：这是隐私开关，客户必须一眼看到
        # 「发了什么、没发什么」，不能藏在折叠说明里。默认关闭；没配通道时
        # 界面明说「不会联网发送任何内容」。
        dg, dg_l = vertical_card("匿名故障摘要（默认关闭）", self)
        dg_l.addWidget(QLabel(
            "开启后只记录：软件版本、故障类别、失败步骤、错误码、出口模式、结果。"
            "不记录视频链接、账号、文案、机器码、授权码、客户名或本地路径。"))
        self.diag_enable = QCheckBox("允许匿名上报故障摘要")
        self.diag_enable.setChecked(
            bool(self.ctx.get_setting("diag_report_enabled", False)))
        connect_guarded(self.diag_enable.toggled,
                        lambda *_: self._save_diag_cfg(),
                        on_error=lambda m: self.fb_status.setText(m),
                        label="保存故障摘要开关")
        dg_l.addWidget(self.diag_enable)
        self.diag_channel = QLabel("")
        self.diag_channel.setWordWrap(True)
        dg_l.addWidget(self.diag_channel)
        self._refresh_diag_channel()
        drow = QHBoxLayout()
        btn_copy = QPushButton("复制运行体检结论")
        btn_full = QPushButton("完整体检（实测网络）")
        btn_open = QPushButton("查看本地记录")
        for btn, slot, label in (
                (btn_copy, lambda *_: self._copy_health(), "复制体检结论"),
                (btn_full, lambda *_: self._full_health(), "完整体检"),
                (btn_open, lambda *_: self._open_diag_records(), "查看本地记录")):
            connect_guarded(btn.clicked, slot,
                            on_error=lambda m: self.fb_status.setText(m),
                            label=label)
            drow.addWidget(btn)
        drow.addStretch()
        dg_l.addLayout(drow)
        self._lays["system"].addWidget(dg)

        # ---------- 本机解析引擎（可选、免费；完整版 / 免费版都可见） ----------
        st = self.ctx.settings
        lp_card, lp_l = vertical_card("本机解析引擎（免费、可选）", self)
        lp_info = QLabel(
            "点「一键部署」即可：软件自带运行环境，不用装 Python、不用联网、不花采集额度；"
            "解析优先走本机，没部署或失败时自动退回原有通道。\n"
            "仅处理自有或已获授权内容；小红书等平台触发风控时需先配置对应 Cookie。"
        )
        lp_info.setWordWrap(True)
        lp_info.setObjectName("Caption")
        lp_l.addWidget(lp_info)
        self.local_enable_chk = QCheckBox("启用本机解析引擎（默认关闭）")
        self.local_enable_chk.setChecked(bool(st.get("local_parser_enabled", False)))
        lp_l.addWidget(self.local_enable_chk)
        self.local_url_edit = QLineEdit(
            str(st.get("local_parser_base_url") or "http://127.0.0.1:8051"))
        self.local_dir_edit = QLineEdit(str(st.get("local_parser_dir") or ""))
        self.local_dir_edit.setPlaceholderText("留空 = 软件同级的 media-parser 目录")
        lp_l.addLayout(form_row("服务地址", self.local_url_edit))
        lp_l.addLayout(form_row("服务目录", self.local_dir_edit))
        self.local_key_edit = QLineEdit(str(st.get("local_parser_api_key") or ""))
        self.local_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_key_edit.setPlaceholderText("仅自建 SaaS 模式才需要；本机微服务模式免填")
        lp_l.addLayout(form_row("服务 Key（可选）", self.local_key_edit))
        self.local_status = QLabel("")
        self.local_status.setWordWrap(True)
        self.local_status.setObjectName("Caption")
        lp_l.addWidget(self.local_status)
        lrow = QHBoxLayout()
        for text, slot, label in (
                ("检查服务", lambda *_: self._local_check(), "检查本机解析服务"),
                ("一键部署", lambda *_: self._local_deploy(), "部署本机解析服务"),
                ("启动服务", lambda *_: self._local_start(), "启动本机解析服务"),
                ("停止服务", lambda *_: self._local_stop(), "停止本机解析服务")):
            btn = QPushButton(text)
            connect_guarded(btn.clicked, slot,
                            on_error=lambda m: self.local_status.setText(m),
                            label=label)
            lrow.addWidget(btn)
        lrow.addStretch()
        lp_l.addLayout(lrow)
        self._lays["system"].addWidget(lp_card)

        # ★ 2026-10-27：每档末尾必须垫一根弹簧。分档后单档内容常常比窗口矮，
        #   QVBoxLayout 会把多出来的高度分给「可以被拉长」的那一项 —— 实测
        #   「授权与激活」档里，机器码和激活码输入框之间被塞进一大块空白
        #   （QLabel 的垂直策略是 Preferred，会被撑高）。弹簧把余量收到最底部。
        for _lay in self._lays.values():
            _lay.addStretch(1)

    # ----------------------------------------------------------- 分组切换
    def _sync_group(self, key: str):
        for k, w in self._views.items():
            w.setVisible(k == key)

    def apply_theme(self, mode: str):
        """主窗口切主题时广播到这里（用了 kit 自绘控件就必须实现）。"""
        self.group_seg.apply_theme(mode)

    # ----------------------------------------------------------- 内容偏好
    def _render_pref(self):
        """按是否已保存偏好，渲染「分步选择」或「一行摘要」。

        ★ 已保存时不再显示问卷：设置页是拿来改东西的，一进来就摆一整套
          问卷会让人以为没保存成功。摘要 + 一个「重选」足够。
        """
        while self._pref_v.count():
            item = self._pref_v.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        tags = list(self.ctx.settings.get("content_tags") or [])
        level = str(self.ctx.settings.get("content_level") or "")
        mode = "light"
        try:
            from PySide6.QtWidgets import QApplication
            mode = QApplication.instance().property("theme_mode") or "light"
        except Exception:                                    # noqa: BLE001
            pass

        if tags and level:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            txt = QLabel(f"你在做 {' · '.join(tags)}，每天投入 {level}。"
                         "工作台和选题页会按这个偏好给建议。")
            txt.setWordWrap(True)
            txt.setStyleSheet("color:#6B7280;font-size:12px;")
            row.addWidget(txt, 1)
            again = QPushButton("重选")
            again.setObjectName("EmptyAction")
            again.setCursor(Qt.CursorShape.PointingHandCursor)
            again.setFixedHeight(28)
            connect_guarded(again.clicked, lambda *_: self._reset_pref(), on_error=lambda msg: self.fb_status.setText(msg), label="reset pref")
            row.addWidget(again)
            self._pref_v.addLayout(row)
            return

        flow = ChoiceFlow(
            "你主要做什么内容？",
            ["口播", "剧情", "Vlog", "种草带货", "测评开箱",
             "教程干货", "情感共鸣", "访谈对话"],
            "每天能投入多少时间？",
            ["30 分钟", "1 小时", "2 小时以上"],
            need=3, mode=mode,
            step1_hint="选 3 个最常做的体裁（选满 3 个才能继续）。"
                       "选得越准，工作台和选题页给的建议越贴你的活儿。",
            step2_hint="选一个你能长期坚持的时长 —— 宁可选少一点，也别选一个"
                       "撑不过一周的量。",
        )
        flow.finished.connect(self._save_pref)
        self._pref_v.addWidget(flow)

    def _save_pref(self, data: dict):
        s = self.ctx.settings
        s["content_tags"] = list(data.get("tags") or [])
        s["content_level"] = str(data.get("level") or "")
        try:
            self.ctx.save_settings()
        except Exception:                                    # noqa: BLE001
            pass
        self._render_pref()

    def _reset_pref(self):
        s = self.ctx.settings
        s["content_tags"] = []
        s["content_level"] = ""
        try:
            self.ctx.save_settings()
        except Exception:                                    # noqa: BLE001
            pass
        self._render_pref()

    # ----------------------------------------------------- 下载与保存（免费版）
    def _build_free_save_card(self):
        """免费版的「下载与保存」卡片（2026-10-23）。

        为什么免费版要有这一块：客户 2026-10-23 的截图是
        `D:\\流量捕手\\下载` 报「写入失败：没有写入权限(代码 13)」。
        软件本身会自动改存到文档目录（这条逻辑是对的，实测 15 条全下来了），
        但客户看到「已自动改存」四个字是懵的 —— 我的文件到底在哪？怎么改回去？
        所以把这个检查搬到设置页：当场告诉他这个目录能不能写、不能写该换哪。
        """
        from justoneapi_collector.video_downloader import fallback_save_dirs

        card_w, lay = vertical_card("下载与保存", self)
        tip = QLabel("下载的视频和直播录像都存在这个目录里。"
                     "换盘、换文件夹都在这里改。")
        tip.setStyleSheet("color:#374151;")
        lay.addWidget(tip)

        row = QHBoxLayout()
        cur = str(self.ctx.settings.get("dl_save_dir") or "")
        self.free_dir_edit = QLineEdit(cur)
        self.free_dir_edit.setPlaceholderText(
            f"留空就用默认目录：{fallback_save_dirs()[0]}")
        row.addWidget(self.free_dir_edit, 1)
        pick = QPushButton("浏览...")
        connect_guarded(pick.clicked, lambda *_: self._pick_free_dir(), on_error=lambda msg: self.fb_status.setText(msg), label="pick free dir")
        row.addWidget(pick)
        lay.addLayout(row)

        self.free_dir_status = QLabel("")
        self.free_dir_status.setWordWrap(True)
        lay.addWidget(self.free_dir_status)

        save = QPushButton("保存并检查这个目录")
        save.setObjectName("Primary")
        connect_guarded(save.clicked, lambda *_: self._save_free_dir(), on_error=lambda msg: self.fb_status.setText(msg), label="save free dir")
        lay.addWidget(save)
        return card_w

    def _pick_free_dir(self):
        from PySide6.QtWidgets import QFileDialog
        start = self.free_dir_edit.text().strip()
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", start)
        if d:
            self.free_dir_edit.setText(d)
            self._save_free_dir()

    def _save_free_dir(self):
        """保存目录 + **当场检查能不能写**（不做这个检查等于把坑留给客户）。"""
        from justoneapi_collector.video_downloader import (
            fallback_save_dirs, writable_problem)

        raw = self.free_dir_edit.text().strip()
        target = Path(raw) if raw else fallback_save_dirs()[0]

        # 客户手打一个不存在的路径很常见，先建出来再判断可写性
        if not target.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.free_dir_status.setStyleSheet(
                    "color:#c62828;font-size:12px;font-weight:600;")
                self.free_dir_status.setText(
                    f"✘ 这个目录建不出来：{exc}\n换一个位置试试，比如直接点「浏览...」"
                    f"选到「文档」下面。")
                return

        problem = writable_problem(target)
        if problem:
            alt = next((c for c in fallback_save_dirs()
                        if not writable_problem(c)), None)
            self.free_dir_status.setStyleSheet(
                "color:#c62828;font-size:12px;font-weight:600;")
            if alt is not None:
                self.free_dir_status.setText(
                    f"✘ 这个目录写不进去（{problem}）。\n"
                    f"下载时会自动改存到 {alt}，但建议你现在就换过去 —— "
                    f"点上面的「浏览...」选这个位置即可。")
            else:
                self.free_dir_status.setText(
                    f"✘ 这个目录写不进去（{problem}），备用目录也不可写。"
                    f"请换一个盘试试。")
            return

        self.ctx.settings["dl_save_dir"] = str(target)
        try:
            self.ctx.save_settings()
        except Exception:                              # noqa: BLE001
            pass
        # 下载器已经在跑的话，立刻让它用新目录（不用重启软件）
        dl = getattr(self.ctx, "downloader", None)
        if dl is not None:
            try:
                dl.save_dir = target
                dl.last_save_dir_used = target
            except Exception:                          # noqa: BLE001
                pass
        self.free_dir_status.setStyleSheet(
            "color:#2e7d32;font-size:12px;font-weight:600;")
        self.free_dir_status.setText(f"✔ 已保存。这个目录能正常写入：{target}")

    # ----------------------------------------------------------- 接口与性能
    def _save(self):
        s = self.ctx.settings
        # 注意：这里【刻意不碰】s["token"]。
        # 接口凭证由授权码内嵌提供，界面已不再让客户填写；
        # 但本机配置文件里若已存有 Token（作者自用 / 历史用户），必须原样保留，
        # 否则一保存就把联网能力弄丢了。
        s["api_provider"] = self.provider_combo.currentData() or "okdata"
        s["okdata_key"] = self.okdata_key_edit.text().strip()
        s["base_url"] = self.base_url_edit.text().strip()
        try:
            s["timeout"] = int(self.timeout_edit.text() or 120)
            s["max_retries"] = int(self.retries_edit.text() or 3)
            s["max_concurrency"] = int(self.concurrency_edit.text() or 5)
        except ValueError:
            pass
        s["cache_enabled"] = self.cache_chk.isChecked()
        # 本机解析引擎：和采集通道完全隔离，只写自己的四个键
        s["local_parser_enabled"] = self.local_enable_chk.isChecked()
        s["local_parser_base_url"] = self.local_url_edit.text().strip() or "http://127.0.0.1:8051"
        s["local_parser_api_key"] = self.local_key_edit.text().strip()
        s["local_parser_dir"] = self.local_dir_edit.text().strip()

        # 落盘到本机 config.json —— 否则重启后 Token 丢失（旧版行为，必须保留）
        self.ctx.save_settings()

        # 用最新 Token 重建客户端与下载器：填完保存即可直接解析 / 下载
        if self.ctx.connect_real_client():
            try:
                self.ctx.client.config.timeout = s["timeout"]
                self.ctx.client.config.max_retries = s["max_retries"]
                self.ctx.client.config.max_concurrency = s["max_concurrency"]
                self.ctx.client.config.cache_enabled = s["cache_enabled"]
            except Exception:  # noqa: BLE001
                pass
            self.ctx.ensure_downloader()
            self.fb_status.setText("设置已保存，已接入采集服务（可直接解析 / 下载）")
        elif str(s.get("api_provider")) == "justoneapi":
            self.fb_status.setText(
                "设置已保存；当前未激活授权码，处于演示模式。"
                "请在下方「授权与激活」粘贴授权码后点「立即激活」")
        else:
            self.fb_status.setText(
                "设置已保存；还没填 okdata Key，付费采集暂不可用。"
                "请到 okdata.online 注册后把 okd_ 开头的 Key 填到上面。"
                "（视频解析与下载仍可正常使用）")

    # ----------------------------------------------------------- 本机解析引擎
    def _local_check(self):
        self._local_run("check")

    def _local_deploy(self):
        self._local_run("deploy")

    def _local_start(self):
        self._local_run("start")

    def _local_stop(self):
        self._local_run("stop")

    def _local_run(self, action: str):
        """部署 / 探活 / 启停都丢 Worker：pip 与端口等待都会卡住主线程。"""
        from justoneapi_collector import local_media_parser as lmp

        url = self.local_url_edit.text().strip() or lmp.DEFAULT_BASE_URL
        directory = self.local_dir_edit.text().strip()
        self._local_action = action
        self.local_status.setText("正在处理，请稍候…")

        def run(worker):
            if action == "check":
                return lmp.health(url)
            if action == "start":
                if directory:
                    lmp.save_config(service_dir=directory)
                return lmp.start_service(url, directory)
            if action == "stop":
                return lmp.stop_service()
            # deploy：进度实时上屏，失败给人话，不弹英文 traceback
            def progress(pct, msg):
                worker.emit_progress(float(pct), str(msg))
            ok, msg = lmp.ensure_deployed(directory, progress=progress)
            if ok:
                lmp.save_config(service_dir=str(lmp.service_dir() if not directory else directory))
            return ok, msg

        w = Worker(run)
        connect_guarded(w.progress, self._on_local_progress,
                        on_error=lambda m: None, label="部署进度")
        connect_guarded(w.done, self._on_local_done,
                        on_error=lambda m: self.local_status.setText(m),
                        label="本机解析引擎")
        connect_guarded(w.error, lambda m: self.local_status.setText(f"失败：{m}"),
                        on_error=lambda m: None, label="本机解析引擎失败")
        w.start()

    def _on_local_progress(self, pct, msg):
        self.local_status.setText(f"{int(float(pct))}%  {msg}")

    def _on_local_done(self, result):
        ok, msg = result if isinstance(result, tuple) else (bool(result), str(result))
        action = getattr(self, "_local_action", "")
        if action == "check":
            self.local_status.setText(("服务正常：" if ok else "未就绪：") + msg)
            return
        self.local_status.setText(("完成：" if ok else "失败：") + msg)
        if action == "deploy" and ok:
            try:
                from justoneapi_collector import local_media_parser as lmp
                path = self.local_dir_edit.text().strip() or str(lmp.service_dir())
                self.local_dir_edit.setText(path)
                self.local_status.setText("部署完成，点「启动服务」后勾上「启用本机解析引擎」即可")
            except Exception:                               # noqa: BLE001
                pass

    # ----------------------------------------------------------- 平台登录
    def _start_login(self, platform):
        if platform in self._login_threads and self._login_threads[platform].isRunning():
            return
        thread = _LoginWorker(platform, self._app_data_dir)
        thread.status.connect(lambda m, p=platform: self._login_status[p].setText(m))
        thread.done.connect(lambda ok, header, p=platform: self._on_login_done(p, ok, header))
        thread.start()
        self._login_threads[platform] = thread
        self._login_status[platform].setText("等待扫码…（已打开浏览器，扫码后本行会自动变成「已登录」）")

    def _on_login_done(self, platform, ok, header):
        if ok and header:
            self._save_cookie(platform, header)
            self._inject_cookie(platform, header)
            self._mark_logged_in(platform, "")
        elif ok:
            # 状态是 logged_in 但没读到 Cookie：不能假装成功，否则用户以为登录了
            self._login_status[platform].setText("登录成功但未读到 Cookie，请重新扫码")
        else:
            self._login_status[platform].setText(f"未登录（{header}）")

    def _mark_logged_in(self, platform, ts=""):
        label = f"已登录（{ts}）" if ts else "已登录"
        self._login_status[platform].setText(label)
        self._login_status[platform].setStyleSheet("color:#2E7D32;font-size:12px;font-weight:600;")
        self._logout_btns[platform].setVisible(True)
        btn = self._login_btns.get(platform)
        if btn is not None and btn.isEnabled():
            btn.setText("重新登录")

    def _save_cookie(self, platform, header):
        """登录成功后把 Cookie 落盘到 config.json，下次启动自动恢复。"""
        self.ctx.settings[cookie_key(platform)] = header
        self.ctx.settings[f"{platform}_cookie_ts"] = time.strftime("%m-%d %H:%M")
        self.ctx.save_settings()

    def _inject_cookie(self, platform, header):
        """把 Cookie 注入下载器 / 直播监控（缺失时才试着补建下载器）。"""
        if self.ctx.downloader is None:
            try:
                self.ctx.ensure_downloader()
            except Exception:  # noqa: BLE001
                pass
        if self.ctx.downloader is not None:
            try:
                self.ctx.downloader.set_cookie_for(platform, header)
            except Exception:  # noqa: BLE001
                pass
        if platform == "douyin" and self.ctx.daemon is not None:
            try:
                self.ctx.daemon.set_douyin_cookie(header)
            except Exception:  # noqa: BLE001
                pass

    def _restore_saved_logins(self):
        """启动时把上次保存的登录态回显并注入（旧版重启就变回「未登录」）。"""
        for plat in self._login_status:
            ck = str(self.ctx.settings.get(cookie_key(plat)) or "").strip()
            if not ck:
                continue
            ts = str(self.ctx.settings.get(f"{plat}_cookie_ts") or "").strip()
            self._mark_logged_in(plat, ts)
            self._inject_cookie(plat, ck)

    def _save_require_login(self, _checked=False):
        """「要求先登录抖音再使用」开关：立即落盘，下次启动生效。"""
        self.ctx.settings["require_login"] = bool(self.require_login_chk.isChecked())
        self.ctx.save_settings()

    def _recheck_login_gate(self):
        """退出抖音登录后：若开着强制登录，立刻重新要求扫码。"""
        try:
            from gui_qt.login_gate import ensure_login, is_logged_in, require_login_enabled
            if not require_login_enabled(self.ctx) or is_logged_in(self.ctx.settings):
                return
            ensure_login(self, self.ctx, force=True)
            self._restore_saved_logins()
        except Exception:                     # noqa: BLE001
            pass

    def _logout(self, platform):
        self.ctx.settings.pop(cookie_key(platform), None)
        self.ctx.settings.pop(f"{platform}_cookie_ts", None)
        self.ctx.save_settings()
        self._login_status[platform].setText("未登录")
        self._login_status[platform].setStyleSheet("color:#6B7280;font-size:12px;")
        self._logout_btns[platform].setVisible(False)
        # 下载器里也清掉，避免继续带着旧 Cookie 发请求
        if self.ctx.downloader is not None:
            try:
                self.ctx.downloader.set_cookie_for(platform, "")
            except Exception:  # noqa: BLE001
                pass
        # 开着强制登录时，退出抖音登录就等于丢掉免费通道 —— 立刻再要一次登录
        if platform == "douyin":
            QTimer.singleShot(300, self._recheck_login_gate)

    # ----------------------------------------------------------- 授权
    def focus_activation(self):
        """从「付费须知」弹窗跳过来时：滚到授权区并把光标放进输入框。

        客户点「去激活授权码」时心里已经决定要买了，这时候还要他在一长串
        设置里自己找「授权与激活」在哪，是把到口的转化白白漏掉。
        页面被 main_window 套在 QScrollArea 里，所以顺着父链找滚动容器。
        """
        try:
            node = self.lic_status
            while node is not None and not isinstance(node, QScrollArea):
                node = node.parent()
            if node is not None:
                node.ensureWidgetVisible(self.lic_status)
        except Exception:                                      # noqa: BLE001
            pass
        try:
            self.lic_input.setFocus()
        except Exception:                                      # noqa: BLE001
            pass

    def _refresh_license(self):
        # ★ 免费版（2026-10-23）：别显示「授权状态：未激活」—— 客户看到
        #   「未激活」会以为自己缺了什么、或者软件没装好。这里直接说清能力。
        #   也不调 _refresh_service_status()：免费版本来就不连采集服务，
        #   那一行会显示红字「✘ 采集服务未连接」，等于自己给自己报故障。
        if branding.APP_IS_FREE:
            self.lic_status.setStyleSheet("color:#1565c0;font-weight:600;")
            self.lic_status.setText("免费版 · 永久可用，下载与录制不限次数")
            hint = ("想要公众号运营、小红书笔记、热榜、关键词、文案仿写、GEO 监测？"
                    "下载完整版即可，两个版本可以同时安装、互不覆盖。")
            if branding.APP_CONTACT:
                hint += f"\n联系方式：{branding.APP_CONTACT}"
            self.lic_hint.setText(hint)
            self.svc_status.setText("")
            self._refresh_quota()
            return
        st = licensing.current()
        state = st.get("state")
        # free 用蓝色而不是红色：免费版是一个正常可用的档位，不是「出错」。
        # 标红会让人以为软件坏了，进而直接卸载 —— 那正好和引流目的相反。
        color = {"licensed": "#2e7d32", "trial": "#ef6c00",
                 "free": "#1565c0"}.get(state, "#c62828")
        self.lic_status.setStyleSheet(f"color:{color};font-weight:600;")
        self.lic_status.setText(f"授权状态：{st.get('label') or '未激活'}")
        if state != "licensed":
            self.lic_hint.setText(licensing.purchase_hint())
        else:
            self.lic_hint.setText("")
        self._refresh_service_status()
        self._refresh_quota()

    def _refresh_service_status(self):
        """在设置页直接给出「采集服务到底通没通、为什么不通」的结论。"""
        # ★ 免费版不含采集功能，这一行必须留空 —— 否则它会红字报
        #   「✘ 采集服务未连接」，客户拿去找人修一个根本不存在的问题。
        if branding.APP_IS_FREE:
            self.svc_status.setText("")
            return
        if self.ctx.client is not None:
            self.svc_status.setStyleSheet("color:#2e7d32;font-size:12px;font-weight:600;")
            self.svc_status.setText("✔ 采集服务已连接，可以正常抓取数据。")
            return
        title, detail = connection_diagnosis()
        self.svc_status.setStyleSheet("color:#c62828;font-size:12px;font-weight:600;")
        # 弹窗里的长说明在页面上压缩成一句话，细节留给弹窗和联系方式
        first = detail.split("\n\n")[0].replace("\n", " ")
        self.svc_status.setText(f"✘ 采集服务未连接 —— {title}：{first}")

    def _refresh_quota(self):
        """渲染「今日调用额度」一行。"""
        # ★ 免费版（2026-10-23）：免费版永远不碰接口，额度这回事不存在。
        #   放在最前面判：客户就算把完整版的授权码粘进来也不显示数字，
        #   否则客户会问「我明明没买，怎么显示 30 次」。
        if branding.APP_IS_FREE:
            self.quota_label.setStyleSheet("color:#1565c0;font-size:12px;")
            self.quota_label.setText(
                "今日额度：免费版下载与录制不限量（不消耗接口额度）")
            return
        try:
            from justoneapi_collector import quota
            q = quota.summary()
        except Exception:                                # noqa: BLE001
            self.quota_label.setText("")
            return
        plan = (licensing.current().get("plan") or "").strip()
        if licensing.current().get("state") == "free":
            # 免费版的下载/录制走的是零成本链路，压根不经过额度统计。
            # 这里要是照常显示「今日额度 5/5」，用户会以为下载只能用 5 次。
            # ★ v1.20.0：免费档的下载/录制已改为不限量，文案跟着改。
            self.quota_label.setStyleSheet("color:#1565c0;font-size:12px;")
            self.quota_label.setText(
                "今日额度：免费版下载与录制不限量（采集类功能需激活后才有额度）")
            return
        if q["unlimited"]:
            txt = f"今日额度：不限量（套餐：{plan or '不限'}），已用 {q['used']} 次"
            color = "#2e7d32"
        elif q["limit"] <= 0:
            txt = "今日额度：不可用（未激活或授权已过期）"
            color = "#c62828"
        else:
            left = q["remaining"]
            txt = (f"今日额度：{left}/{q['limit']} 次"
                   f"（套餐：{plan or '标准'}，已用 {q['used']} 次，明天 0 点重置）")
            color = "#2e7d32" if left > 0 else "#c62828"
        self.quota_label.setStyleSheet(f"color:{color};font-size:12px;")
        self.quota_label.setText(txt)

    def _copy_machine(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(licensing.machine_id())

    def _activate(self):
        code = self.lic_input.toPlainText().strip()
        if not code:
            self.lic_hint.setText("请先粘贴授权码")
            return
        ok, msg = licensing.activate(code)
        if ok:
            self.lic_input.clear()
            # 授权码若内嵌 Token，立即接入真实采集服务（无需重启）
            self.ctx.connect_real_client()
            self._refresh_license()
        else:
            self.lic_hint.setText(f"激活失败：{msg}\n机器码：{licensing.machine_id()}\n{licensing.purchase_hint()}")

    def _import_lic(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择授权文件", "", "授权文件 (*.lic)")
        if not path:
            return
        code = licensing.read_license_file(Path(path))
        if not code:
            self.lic_hint.setText("该文件里没有找到授权码")
            return
        self.lic_input.setPlainText(code)
        self._activate()

    def _clear_lic(self):
        licensing.deactivate()
        # 回到演示模式（真实采集服务断开）
        self.ctx.client = None
        self._refresh_license()
        self.lic_hint.setText("已清除本机激活")

    # ----------------------------------------------------------- 更新
    def _save_update_cfg(self):
        """把更新地址与「启动时自动检查」落盘（旧版只在点保存地址时存）。"""
        self.ctx.settings["update_feed_url"] = self.update_url.text().strip()
        self.ctx.settings["update_auto_check"] = self.update_auto.isChecked()
        self.ctx.save_settings()

    def _save_update_url(self):
        self._save_update_cfg()
        self.update_status.setText("更新配置已保存")
        self.update_status.mark("ok")

    def _reset_update_prompt(self):
        """清除「跳过此版本」记录 —— 之前跳过过新版本的用户点这里可恢复提示。"""
        updater.clear_skipped(self.ctx.settings)
        self.ctx.save_settings()
        self.update_status.setText("已重置：下次检查会重新提示新版本")
        self.update_status.mark("ok")

    def _check_update(self):
        # 无论是否激活都允许检查更新：授权过期/未激活的用户同样需要修 bug
        self._save_update_cfg()
        self.update_status.setText("正在检查更新，请稍候…")
        self.update_status.mark("idle")
        try:
            from gui_qt.update_flow import maybe_check_update
        except Exception as e:  # noqa: BLE001
            self.update_status.setText(f"更新模块加载失败：{e}")
            self.update_status.mark("error")
            return
        maybe_check_update(self, self.ctx, manual=True,
                           on_message=lambda t: self.update_status.setText(t))

    # ----------------------------------------------------------- 反馈
    def _submit_feedback(self):
        note = self.fb_text.toPlainText().strip()
        if not note:
            self.fb_status.setText("建议至少写一句问题描述，作者定位会快很多")
            self.fb_status.mark("warn")
            return
        self._pack_feedback(self.fb_kind.currentText(), note,
                           self.fb_logs.isChecked())

    def _send_logs(self):
        self._pack_feedback("日志", "（用户直接发送了日志，未填写描述）", True)

    def _pack_feedback(self, kind, note, with_logs):
        try:
            # ★ make_support_bundle 的参数名是 include_logs（2026-11-15 修复）：
            #   之前这里传 with_logs=，直接 TypeError 被下面 except 吞成
            #   「打包失败：…」—— 「一键发日志」按钮其实一直是坏的。
            path = support.make_support_bundle(
                kind=kind, note=note, include_logs=with_logs,
                current_token=str(self.ctx.settings.get("token") or ""))
            support.open_in_explorer(path)
            self.fb_status.setText(f"已打包到桌面：{path.name}，请通过微信发给作者")
            self.fb_status.mark("ok")
        except Exception as e:  # noqa: BLE001
            self.fb_status.setText(f"打包失败：{e}")
            self.fb_status.mark("error")

    def _open_logs(self):
        try:
            support.open_in_explorer(support._iter_log_files()[0] if support._iter_log_files() else Path("."))
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------- 匿名故障摘要
    def _save_diag_cfg(self, _checked=False):
        """开关落盘。★ 关掉后本地仍记脱敏摘要，但永远不会外发。"""
        on = bool(self.diag_enable.isChecked())
        self.ctx.settings["diag_report_enabled"] = on
        self.ctx.save_settings()
        self._refresh_diag_channel()
        diagnostics.record_event(self.ctx, "diag_toggle",
                                 result="on" if on else "off")
        tail = ("；上报通道已配置" if diagnostics.endpoint(self.ctx)
                else "；通道未配置，不会联网发送")
        self.fb_status.setText(("已开启" if on else "已关闭")
                               + "匿名故障摘要" + tail)
        self.fb_status.mark("ok")

    def _refresh_diag_channel(self):
        """只显示状态，不把服务器地址暴露给客户（也避免误填）。"""
        ep = diagnostics.endpoint(self.ctx)
        on = bool(self.diag_enable.isChecked())
        if not ep:
            txt = "上报通道：未配置 · 只在本机记录，软件不会联网发送任何内容。"
        elif on:
            txt = "上报通道：已配置且已开启 · 仅发送上方列出的字段。"
        else:
            txt = "上报通道：已配置但未开启 · 只在本机记录。"
        self.diag_channel.setText(txt)

    def _copy_health(self):
        """快速体检：不联网、毫秒级，结论可直接复制发作者。"""
        self._show_health_text(diagnostics.health_report(self.ctx), False)

    def _full_health(self):
        """完整体检：真发请求探测出口，必须丢 Worker，否则界面会僵住数秒。"""
        self.fb_status.setText("正在实测网络出口（最多 5 秒）…")
        self.fb_status.mark("idle")

        def run(worker):
            return diagnostics.health_report(self.ctx, probe_network=True)

        w = Worker(run)
        self._diag_worker = w          # 保活：QThread 被 GC 会直接崩进程
        connect_guarded(w.done, self._on_health_done,
                        on_error=lambda m: self._set_health_error(m),
                        label="完整体检结果")
        connect_guarded(w.error, self._set_health_error,
                        on_error=lambda m: self.fb_status.setText(m),
                        label="完整体检失败")
        w.start()

    def _on_health_done(self, text):
        self._show_health_text(str(text or ""), True)

    def _set_health_error(self, msg):
        self.fb_status.setText(f"完整体检失败：{msg}")
        self.fb_status.mark("error")

    def _show_health_text(self, text: str, probed: bool):
        try:
            QApplication.clipboard().setText(text)
        except Exception:                                    # noqa: BLE001
            pass
        try:
            LogDialog(
                self, title="运行体检完成",
                summary="体检结论已复制到剪贴板。",
                body="点「显示详细信息」查看全文；这份结论不含任何身份信息，"
                     "可直接粘贴发给作者。",
                detail=text,
                note=("已实测网络出口" if probed
                      else "未实测网络（点「完整体检」可补测）"),
                show_export=False, tone="success",
            ).show_nonmodal()
        except Exception:                                    # noqa: BLE001
            pass
        self.fb_status.setText("体检结论已复制，可直接粘贴发给作者")
        self.fb_status.mark("ok")

    def _open_diag_records(self):
        d = diagnostics.events_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        support.open_in_explorer(d)
