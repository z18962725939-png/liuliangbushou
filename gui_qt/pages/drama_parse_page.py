# -*- coding: utf-8 -*-
"""短剧解析页（合规护栏版，2026-11-02；2026-11-14 修复卡死 + 补凭证入口）。

★ 用途边界（与 drama_api.py 一致，不许改）：
  仅用于「客户自有 / 已授权」短剧的二创素材获取，或「找选题 / 预览」参考。
  不提供默认全量批量抓取；下载前必须一次性弹窗确认授权。

为什么单独成页、不并进「短剧成片」：
  短剧成片是二创剪辑引擎（本地组件 + 转写）；本页是「内容获取」侧入口，
  二者上下游但不耦合。客户要先在这里把授权的短剧拿到本地，再进成片页二创。

★ 2026-11-14 客户报障「点看热榜，一直显示正在拉取热榜」：
  真因＝4 处 `on_error=self._status.set`。StatusLine 没有 `set` 方法，
  这个属性访问在 connect_guarded 的**参数求值阶段**就抛 AttributeError，
  发生在 w.start() 之前 ⇒ Worker 从未启动、done/error 永远不来；
  而按钮是裸 `.clicked.connect`，槽异常在打包成 exe 后没有控制台，
  客户只看到状态栏停在上一句「正在拉取热榜…」。整页四个 Worker 全废。
  同时该页从上线起就没有凭证入口（错误文案却写「在设置页填写」），
  drama_api.configure() 全库无调用方 ⇒ 即使回调修好也只会显示「未配置凭证」。
  本轮一并补齐：unified guarded 回调 + 凭证设置弹窗 + 未配置时的前置引导。

★ 2026-11-14 22:20 增补「免凭证公开热榜」降级：
  调查 PhoenixReadApi 后确认现状同源（小影 API 的 drama/hongguo 就是红果），
  缺的只是客户自备凭证。为让没凭证的客户也能「看热榜」找选题，新增
  drama_public_rank：只取红果官网首页 SSR 的公开元数据（剧名/名次/集数/
  分类/封面），不解析播放直链、不碰 CENC 解密、不登录、不翻页。
  链路：有凭证 → drama_api.rank()（完整热榜 + 集数 + 下载）；
        无凭证 → drama_public_rank.fetch_rank()（仅选题参考）。
  ★ 公开源条目一律不许拿去调 detail / 下载：series_id 用途不同，
    且会重演「点了没反应」。_public_mode 下前置拦截并给出可操作提示。

★ 2026-11-28 新增「SocialDataX 红果专用通道」（socialdatax_client.py）：
  用户点名要短剧走 SocialDataX。三条链路严格隔离、用户自己切换：
    ① SocialDataX（推荐）：HTTP POST + 用户自己的 sd Key，按积分计费；
    ② XiaoYingAPI（原有）：project id / secret，保留不删；
    ③ 免凭证公开榜：仅选题参考，不能下载。
  okdata 官方 383 个接口无短剧能力，绝不拿 okdata Key 冒充短剧通道。
  ★ 软件不收款、不内置支付：积分不足只弹官网充值链接，由用户自行完成。
  ★ 用户限制必须上屏（自有/已授权、积分消耗、链接时效、无周榜），
    见 socialdatax_client.LIMIT_HINT / RECHARGE_HINT。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QFileDialog,
    QDialog, QFormLayout, QDialogButtonBox, QStackedWidget,
)

from ._base import StatusLine, Worker, connect_guarded
from gui_qt.widgets.progress import FlowProgressBar
from justoneapi_collector import drama_api, drama_public_rank, socialdatax_client

_COMPLIANCE_HINT = (
    "本功能仅用于「自有 / 已授权」短剧的二创素材获取，或找选题 / 预览参考。"
    "批量搬运未授权内容属侵权，本工具不提供该能力。"
)

_SD_CRED_HINT = socialdatax_client.CRED_HINT + "\n\n" + socialdatax_client.LIMIT_HINT

_CRED_HINT = (
    "备用通道：XiaoYingAPI 项目凭证，与软件授权码不是同一套。"
    "请在项目控制台创建项目，把 project id / secret 填在下面；"
    "凭证只保存在本机 config.json，不会上传。"
    "推荐优先使用 SocialDataX 通道；本通道仅在你已有旧凭证时保留。"
)

# 通道标识：严格三选一，绝不混用 Key
_CH_SD = "socialdatax"
_CH_XY = "xiaoying"
_CH_PUBLIC = "public"


class DramaParsePage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._series: list[dict] = []     # 当前搜索 / 热榜结果（归一化）
        self._eps: list[dict] = []        # 当前选中剧集的集列表（归一化）
        self._authorized = False          # 会话内授权确认标记
        self._public_mode = False         # 当前列表是否来自免凭证公开榜（仅选题参考）
        self._channel = _CH_PUBLIC         # 当前生效通道：socialdatax / xiaoying / public
        self._sd_page_token = ""          # SocialDataX 搜索分页令牌（同条件原样回传）
        self._save_dir = self._default_save_dir()
        self._build()
        self.apply_theme("light")

    # ------------------------------------------------------------------
    #  统一 guarded 接线（★ 禁止裸 .clicked.connect / 禁止 self._status.set）
    # ------------------------------------------------------------------
    def _wire_btn(self, btn, slot, label: str) -> None:
        """按钮：clicked 会带 checked 布尔，用 *_ 吃掉再调无参槽。"""
        connect_guarded(btn.clicked, lambda *_: slot(),
                        on_error=lambda m: self._say(m, "error"), label=label)

    def _wire_worker(self, w, done_slot, err_prefix: str, label: str) -> None:
        """Worker 的 done / error 一律 guarded；出错必须写回状态栏，不许静默。"""
        connect_guarded(w.done, done_slot,
                        on_error=lambda m: self._say(m, "error"), label=label)
        connect_guarded(w.error, lambda m: self._say(f"{err_prefix}：{m}", "error"),
                        on_error=lambda m: self._say(m, "error"), label=label + "异常")

    def _wire_signal(self, signal, slot, label: str) -> None:
        """带参信号（如 itemClicked）—— 原样转发参数。"""
        connect_guarded(signal, slot,
                        on_error=lambda m: self._say(m, "error"), label=label)

    # ------------------------------------------------------------------
    #  布局
    # ------------------------------------------------------------------
    @staticmethod
    def _empty_label(text: str) -> QLabel:
        """列表空态：居中 QLabel，复用现有组件，不新增 QSS 选择器。"""
        lab = QLabel(text)
        lab.setObjectName("ListEmptyState")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setWordWrap(True)
        return lab

    def _sync_empty_states(self) -> None:
        """按列表实际条数切换空态/列表，绝不伪造数据。"""
        self._list_stack.setCurrentWidget(
            self._list if self._list.count() else self._list_empty)
        self._eps_stack.setCurrentWidget(
            self._eps_list if self._eps_list.count() else self._eps_empty)

    def _default_save_dir(self) -> str:
        try:
            from justoneapi_collector import branding
            d = branding.app_data_dir() / "短剧解析"
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
        except Exception:  # noqa: BLE001
            return str(Path.home() / "Downloads")

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("短剧解析")
        title.setObjectName("H1")
        root.addWidget(title)

        self._hint = QLabel(_COMPLIANCE_HINT)
        self._hint.setWordWrap(True)
        self._hint.setObjectName("ComplianceHint")
        root.addWidget(self._hint)

        # 通道状态行：一进页就知道走哪条链路、要不要先配置
        cred_row = QHBoxLayout()
        self._cred_label = QLabel("")
        self._cred_label.setWordWrap(True)
        self._cred_label.setObjectName("Caption")
        cred_row.addWidget(self._cred_label, 5)
        self._sd_cred_btn = QPushButton("设置通道")
        self._wire_btn(self._sd_cred_btn, self._edit_sd_credentials, "设置 SocialDataX 通道")
        cred_row.addWidget(self._sd_cred_btn, 1)
        self._balance_btn = QPushButton("查询余额")
        self._wire_btn(self._balance_btn, self._query_balance, "查询积分余额")
        cred_row.addWidget(self._balance_btn, 1)
        self._recharge_btn = QPushButton("充值说明")
        self._wire_btn(self._recharge_btn, self._show_recharge, "充值说明")
        cred_row.addWidget(self._recharge_btn, 1)
        self._xy_cred_btn = QPushButton("备用凭证")
        self._wire_btn(self._xy_cred_btn, self._edit_credentials, "设置 XiaoYingAPI 凭证")
        cred_row.addWidget(self._xy_cred_btn, 1)
        root.addLayout(cred_row)
        self._refresh_cred()

        # 搜索行
        row = QHBoxLayout()
        self._q = QLineEdit()
        self._q.setPlaceholderText("输入剧名关键词，如「逆袭」「甜宠」")
        connect_guarded(self._q.returnPressed, lambda *_: self._do_search(),
                        on_error=lambda m: self._say(m, "error"), label="回车搜索")
        row.addWidget(self._q, 4)
        b_search = QPushButton("搜索")
        self._wire_btn(b_search, self._do_search, "搜索短剧")
        row.addWidget(b_search, 1)
        b_hot = QPushButton("看热榜")
        b_hot.setToolTip(
            "SocialDataX 通道：红果综合搜索（可查看集数并下载，按积分计费）。\n"
            "XiaoYingAPI 通道：拉取完整热榜，可查看集数并下载。\n"
            "都没有：改用红果官网公开热榜，免凭证看选题，"
            "只提供剧名 / 名次 / 集数 / 分类等元数据，不能下载。"
        )
        self._wire_btn(b_hot, self._do_rank, "看热榜")
        row.addWidget(b_hot, 1)
        root.addLayout(row)

        # 结果列表（空态与列表同位置切换，避免两块纯白大框让人无从下手）
        self._list = QListWidget()
        self._wire_signal(self._list.itemClicked, self._on_series_clicked, "选剧")
        self._list_empty = self._empty_label(
            "还没有短剧结果\n输入剧名关键词，点「搜索」或「看热榜」开始")
        self._list_stack = QStackedWidget()
        self._list_stack.addWidget(self._list_empty)
        self._list_stack.addWidget(self._list)
        root.addWidget(self._list_stack, 3)

        # 选集区
        self._detail_label = QLabel("选择上方一部剧，可查看集数并下载")
        self._detail_label.setWordWrap(True)
        root.addWidget(self._detail_label)
        self._eps_list = QListWidget()
        self._wire_signal(self._eps_list.itemClicked, self._on_ep_clicked, "选集")
        self._eps_empty = self._empty_label(
            "还没有可下载的集数\n选择上方一部剧，可查看集数并下载")
        self._eps_stack = QStackedWidget()
        self._eps_stack.addWidget(self._eps_empty)
        self._eps_stack.addWidget(self._eps_list)
        root.addWidget(self._eps_stack, 2)

        # 下载控制
        dl = QHBoxLayout()
        self._dl_btn = QPushButton("下载选中集")
        self._wire_btn(self._dl_btn, self._on_dl_clicked, "下载选中集")
        dl.addWidget(self._dl_btn, 3)
        self._dir_btn = QPushButton("保存目录")
        self._wire_btn(self._dir_btn, self._choose_dir, "保存目录")
        dl.addWidget(self._dir_btn, 1)
        root.addLayout(dl)
        self._dir_label = QLabel(f"保存到：{self._save_dir}")
        self._dir_label.setWordWrap(True)
        root.addWidget(self._dir_label)

        self._bar = FlowProgressBar()
        root.addWidget(self._bar)

        self._status = StatusLine()
        root.addWidget(self._status)
        self._say("输入剧名或点「看热榜」开始")
        self._sync_empty_states()

    def apply_theme(self, mode: str) -> None:
        """切换主题时刷新合规横幅与列表空态（主动跟随 mode，避免写死色发暗）。"""
        if mode == "dark":
            self._hint.setStyleSheet(
                "background:#1A212B;border:1px solid #2E3A47;border-radius:10px;"
                "padding:10px 12px;color:#FBBF24;font-size:12px;"
            )
            empty_style = (
                "background:#1A212B;border:1px solid #2E3A47;border-radius:10px;"
                "padding:16px;color:#98A1AF;font-size:13px;"
            )
        else:
            self._hint.setStyleSheet(
                "background:#FEF3C7;border:1px solid #FDE68A;border-radius:10px;"
                "padding:10px 12px;color:#92400E;font-size:12px;"
            )
            empty_style = (
                "background:#F5F6F8;border:1px solid #D8DBE0;border-radius:10px;"
                "padding:16px;color:#6B7280;font-size:13px;"
            )
        self._list_empty.setStyleSheet(empty_style)
        self._eps_empty.setStyleSheet(empty_style)

    def _say(self, msg: str, level: str = "idle") -> None:
        """状态行：setText + mark 语义档（StatusLine 接口）。"""
        self._status.setText(msg)
        self._status.mark(level)

    # ------------------------------------------------------------------
    #  凭证配置（独立账号体系，只存本机）
    # ------------------------------------------------------------------
    def _active_channel(self) -> str:
        """通道优先级：SocialDataX → XiaoYingAPI → 免凭证公开榜。"""
        if socialdatax_client.credentials_ready():
            return _CH_SD
        if drama_api.credentials_ready():
            return _CH_XY
        return _CH_PUBLIC

    def _refresh_cred(self) -> None:
        self._channel = self._active_channel()
        if self._channel == _CH_SD:
            src = socialdatax_client.credential_source()
            self._cred_label.setText(
                f"当前通道：SocialDataX 红果专用（Key 来源：{src}）— "
                "搜索 / 集数 / 下载按积分计费；点「查询余额」看剩余积分"
            )
        elif self._channel == _CH_XY:
            src = drama_api.credential_source()
            self._cred_label.setText(
                f"当前通道：XiaoYingAPI 备用（凭证来源：{src}）— "
                "需要 SocialDataX 请点「设置通道」"
            )
        else:
            self._cred_label.setText(
                "当前通道：免凭证公开榜（仅选题参考，不能下载）— "
                "点「设置通道」填 SocialDataX Key 即可搜索 / 下载"
            )

    # ------------------------------------------------------------------
    #  SocialDataX 专用通道（★ 软件不收款，只给官网链接）
    # ------------------------------------------------------------------
    def _edit_sd_credentials(self) -> bool:
        """SocialDataX Key 设置弹窗；Key 只存本机，绝不回显。"""
        key = socialdatax_client.current_credentials()
        dlg = QDialog(self)
        dlg.setWindowTitle("SocialDataX 红果通道")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)
        tip = QLabel(_SD_CRED_HINT)
        tip.setWordWrap(True)
        tip.setObjectName("Caption")
        lay.addWidget(tip)
        form = QFormLayout()
        e_key = QLineEdit(key)
        e_key.setEchoMode(QLineEdit.EchoMode.Password)
        e_key.setPlaceholderText("在 socialdatax.com 申请的 API Key")
        form.addRow("API Key", e_key)
        lay.addLayout(form)
        link = QLabel(
            f"申请 Key：{socialdatax_client.KEY_URL}\n"
            f"充值页面：{socialdatax_client.RECHARGE_URL}"
        )
        link.setWordWrap(True)
        link.setObjectName("Caption")
        lay.addWidget(link)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        ok, msg = socialdatax_client.save_credentials(e_key.text())
        self._refresh_cred()
        self._say(msg, "ok" if ok else "error")
        return ok

    def _query_balance(self):
        """只读查询积分余额，不扣费。"""
        if not socialdatax_client.credentials_ready():
            self._say("请先点「设置通道」填 SocialDataX Key，再查询余额", "warn")
            return
        self._say("正在查询 SocialDataX 积分余额…")
        w = Worker(self._run_balance)
        self._wire_worker(w, self._on_balance_done, "查询余额失败", "积分余额")
        w.start()

    def _run_balance(self, worker):
        return socialdatax_client.get_balance()

    def _on_balance_done(self, payload):
        bal, err = payload
        if err:
            self._say(err, "error")
            return
        self._say(f"SocialDataX 积分余额：{bal}", "ok")

    def _show_recharge(self):
        """只弹说明与官网链接；不内置支付、不收集支付信息。"""
        QMessageBox.information(
            self, "SocialDataX 充值说明", socialdatax_client.RECHARGE_HINT,
            QMessageBox.StandardButton.Ok,
        )

    def _edit_credentials(self) -> bool:
        """凭证设置弹窗。返回是否成功保存（取消 / 失败均为 False）。"""
        pid, sec = drama_api.current_credentials()
        dlg = QDialog(self)
        dlg.setWindowTitle("短剧解析凭证")
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        tip = QLabel(_CRED_HINT)
        tip.setWordWrap(True)
        tip.setObjectName("Caption")
        lay.addWidget(tip)
        form = QFormLayout()
        e_id = QLineEdit(pid)
        e_id.setPlaceholderText("项目控制台里的 project id / app_id")
        e_sec = QLineEdit(sec)
        e_sec.setEchoMode(QLineEdit.EchoMode.Password)
        e_sec.setPlaceholderText("项目控制台里的 secret / appsecret")
        form.addRow("project id", e_id)
        form.addRow("secret", e_sec)
        lay.addLayout(form)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        ok, msg = drama_api.save_credentials(e_id.text(), e_sec.text())
        self._refresh_cred()
        self._say(msg, "ok" if ok else "error")
        return ok

    def _ensure_credentials(self) -> bool:
        """搜索前置：SocialDataX / XiaoYingAPI 任一可用即放行。

        ★ 热榜不走这里：两个都没有时改走免凭证公开榜（仅选题参考）。
        """
        if socialdatax_client.credentials_ready() or drama_api.credentials_ready():
            return True
        self._say("尚未配置短剧通道 —— 请在弹窗里填 SocialDataX API Key", "warn")
        if not self._edit_sd_credentials() and "保存失败" not in self._status.text():
            self._say("已取消：未配置通道时无法搜索 / 下载", "warn")
        return socialdatax_client.credentials_ready() or drama_api.credentials_ready()

    # ------------------------------------------------------------------
    #  搜索 / 热榜
    # ------------------------------------------------------------------
    def _do_search(self):
        kw = self._q.text().strip()
        if not kw:
            self._say("请先输入剧名关键词")
            return
        if not self._ensure_credentials():
            return
        self._channel = self._active_channel()
        self._say(f"正在搜索「{kw}」…")
        w = Worker(self._run_search, kw)
        self._wire_worker(w, self._on_search_done, "搜索失败", "搜索结果")
        w.start()

    def _run_search(self, worker, kw: str):
        """按当前通道分流；SocialDataX 搜索分页令牌原样回传。"""
        if self._channel == _CH_SD:
            data, err = socialdatax_client.search(kw, page_token=self._sd_page_token)
            return ("sd-search", data, err)
        data, err = drama_api.search(kw)
        return ("search", data, err)

    def _do_rank(self):
        """SocialDataX → XiaoYingAPI → 免凭证公开榜，逐级降级。"""
        self._channel = self._active_channel()
        if self._channel == _CH_SD:
            self._sd_page_token = ""
            self._say("正在红果综合搜索（SocialDataX 通道，按积分计费）…")
            w = Worker(self._run_search, "热门")
            self._wire_worker(w, self._on_search_done, "搜索失败", "热榜结果")
            w.start()
            return
        if self._channel == _CH_XY:
            self._say("正在拉取热榜…")
            w = Worker(self._run_rank)
            self._wire_worker(w, self._on_search_done, "拉取失败", "热榜结果")
            w.start()
            return
        self._say("正在拉取公开热榜（免凭证，仅供选题参考）…")
        w = Worker(self._run_public_rank)
        self._wire_worker(w, self._on_search_done, "公开热榜拉取失败", "公开热榜")
        w.start()

    def _run_rank(self, worker):
        data, err = drama_api.rank()
        return ("rank", data, err)

    def _run_public_rank(self, worker):
        items, err = drama_public_rank.fetch_rank()
        return ("public-rank", {"results": items}, err)

    def _on_search_done(self, payload):
        kind, data, err = payload
        if err:
            self._say(err, "error")
            return
        results = []
        sd_note = ""
        if kind == "sd-search" and isinstance(data, dict):
            results = [r for r in (data.get("items") or [])
                       if str(r.get("type") or "") == "series"]
            self._sd_page_token = str(data.get("next_page_token") or "")
            # ★ 积分消耗必须留到最终状态栏，不能在下面被「找到 N 部」覆盖掉
            sd_note = str(data.get("_note") or "")
        elif kind in ("search", "rank", "public-rank") and isinstance(data, dict):
            results = data.get("results") or []
        if not results:
            self._say(f"没有找到相关短剧（{sd_note}）" if sd_note
                      else "没有找到相关短剧")
            self._list.clear()
            self._public_mode = False
            self._sync_empty_states()
            return
        self._public_mode = (kind == "public-rank")
        if kind == "sd-search":
            self._series = [socialdatax_client.normalize_series(r) for r in results]
        else:
            self._series = [drama_api.normalize_series(r) for r in results]
        self._list.clear()
        # 换一批结果就清空集数与选中态，避免上一部剧残留导致误下载
        self._eps = []
        self._eps_list.clear()
        if self._public_mode:
            self._detail_label.setText(
                "公开热榜只提供选题 / 预览参考的元数据 —— "
                "配置凭证后再点「看热榜」，才能查看集数与下载"
            )
        else:
            self._detail_label.setText("选择上方一部剧，可查看集数并下载")
        for s in self._series:
            if self._public_mode:
                tags = " / ".join(s["tags"][:3]) or "公开榜单"
                label = f"{s['name']}  ｜ {s['heat'] or '—'}  ｜ {tags}"
            else:
                label = f"{s['name']}  ｜ {s['score'] or '—'}分  ｜ {s['heat'] or '—'}热度"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, s["series_id"])
            self._list.addItem(item)
        if self._public_mode:
            self._say(
                f"公开热榜 {len(self._series)} 部（免凭证，仅供选题参考；"
                "看集数与下载请先点「设置凭证」再点「看热榜」）", "ok")
        else:
            msg = f"找到 {len(self._series)} 部短剧，点击查看集数"
            self._say(f"{msg}（{sd_note}）" if sd_note else msg, "ok")
        self._sync_empty_states()

    # ------------------------------------------------------------------
    #  选剧 → 加载集列表
    # ------------------------------------------------------------------
    def _on_series_clicked(self, item: QListWidgetItem):
        # ★ 公开榜的 series_id 不是小影 API 的取集凭证，拿去调 detail 必然失败。
        #   点了必须当场说清，绝不许退化成「点了没反应」。
        if self._public_mode:
            self._say(
                "公开热榜仅提供选题参考的元数据；要看集数或下载，"
                "请先点「设置凭证」，再点「看热榜」拉取完整热榜", "warn")
            return
        sid = item.data(Qt.UserRole)
        if not sid:
            return
        self._say("正在加载剧集列表…")
        w = Worker(self._run_detail, sid)
        self._wire_worker(w, self._on_detail_done, "加载失败", "剧集详情")
        w.start()

    def _run_detail(self, worker, sid: str):
        if self._channel == _CH_SD:
            data, err = socialdatax_client.series_detail(series_id=sid)
            if err:
                return (None, err)
            return (socialdatax_client.normalize_detail(data or {}), "")
        data, err = drama_api.detail(sid)
        return (data, err)

    def _on_detail_done(self, payload):
        data, err = payload
        if err:
            self._say(err, "error")
            return
        if not isinstance(data, dict):
            self._say("剧集详情返回异常")
            return
        name = data.get("name") or ""
        episodes = data.get("episodes") or []
        self._detail_label.setText(
            f"《{name}》共 {len(episodes)} 集 —— 点选一集后下载（仅限自有/已授权内容）"
        )
        if self._channel == _CH_SD:
            self._eps = episodes
        else:
            self._eps = [drama_api.normalize_episode(e) for e in episodes]
        self._eps_list.clear()
        for e in self._eps:
            quality = e.get("quality") or ""
            source = e.get("source") or ""
            vid = e.get("video_id") or ""
            label = f"第 {e.get('ep', 0)} 集  ｜ {quality or '—'}p  ｜ {source or '—'}"
            if vid:
                label += f"  ｜ {vid}"
            self._eps_list.addItem(label)
            self._eps_list.item(self._eps_list.count() - 1).setData(
                Qt.UserRole, e.get("ep", 0))
        self._say(f"《{name}》加载完成，共 {len(self._eps)} 集", "ok")
        self._sync_empty_states()

    def _on_ep_clicked(self, item: QListWidgetItem):
        ep = item.data(Qt.UserRole)
        self._say(f"已选中第 {ep} 集，点「下载选中集」保存到本地")

    # ------------------------------------------------------------------
    #  下载（合规护栏：首次弹授权确认）
    # ------------------------------------------------------------------
    def _on_dl_clicked(self):
        item = self._eps_list.currentItem()
        if item is None:
            self._say("请先在上方选一集")
            return
        ep = item.data(Qt.UserRole)
        if not self._authorized:
            ok = QMessageBox.question(
                self, "确认内容授权",
                "你确认要下载的短剧内容已获得授权，或属于你/客户自有素材吗？\n\n"
                "本功能仅用于二创素材获取与选题参考，不支持批量搬运未授权内容。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ok != QMessageBox.StandardButton.Yes:
                self._say("已取消下载（未确认授权）")
                return
            self._authorized = True

        # 拿这一集的播放地址
        sel = next((e for e in self._eps if e["ep"] == ep), None)
        if sel is None:
            self._say("请先从上方选一集")
            return
        if self._channel == _CH_SD:
            vid = str(sel.get("video_id") or "").strip()
            if not vid:
                self._say("该集缺少单集 ID，无法换取下载地址")
                return
        elif not sel.get("url"):
            self._say("该集没有可用的播放地址")
            return
        safe_name = (self._detail_label.text().split("》")[0].lstrip("《")
                     if "》" in self._detail_label.text() else "短剧")
        dest = Path(self._save_dir) / f"{safe_name}_第{ep}集.mp4"
        self._say(f"正在下载第 {ep} 集…")
        self._bar.setValue(5)
        w = Worker(self._run_download, ep, str(dest))
        self._wire_worker(w, self._on_dl_done, "下载失败", "下载结果")
        w.start()

    def _run_download(self, worker, ep: int, dest: str):
        sel = next((e for e in self._eps if e["ep"] == ep), None)
        if sel is None:
            return False, "选中集已失效，请重新点选"
        if self._channel == _CH_SD:
            # ★ 链接有时效：必须下载前即时换取，不能缓存旧地址
            data, err = socialdatax_client.video_download_links(
                video_id=str(sel.get("video_id") or ""))
            if err:
                return False, err
            stream = socialdatax_client.pick_stream((data or {}).get("streams") or [])
            if not stream:
                return False, "该集没有返回可用清晰度"
            ok, msg = socialdatax_client.download_episode(
                stream, dest, authorized=True)
            note = (data or {}).get("_note") or ""
            return ok, f"{msg}（{note}）" if note else (ok, msg)
        ok, msg = drama_api.download_episode(str(sel.get("url") or ""), dest,
                                             authorized=True)
        return (ok, msg)

    def _on_dl_done(self, payload):
        ok, msg = payload
        if ok:
            self._bar.setValue(100)
            self._say(f"下载完成：{msg}", "ok")
        else:
            self._bar.setValue(0)
            self._say(f"下载失败：{msg}", "error")

    def _choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self._save_dir)
        if d:
            self._save_dir = d
            self._dir_label.setText(f"保存到：{self._save_dir}")
