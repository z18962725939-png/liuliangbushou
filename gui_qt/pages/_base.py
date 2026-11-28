"""PySide6 页面共享基础：复用组件、后台任务线程、应用上下文。

- card / section_header / form_row / log_panel 等 UI 小部件
- Worker：把耗时操作丢到子线程，避免界面卡死，并回传进度/日志/结果
- AppContext：持有 client / settings / monitor / transcriber / downloader 等共享资源；
  资源缺失时自动降级为「演示模式」，保证界面在无真实环境时也能构建、不崩溃。
"""
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 页面引导条工厂：各页面统一从本模块取用（实际实现在 gui_qt.widgets.guide）
from gui_qt.widgets.guide import guide_banner  # noqa: E402,F401


# --------------------------------------------------------------------------
# 平台名：内部键 → 客户看得懂的中文
# --------------------------------------------------------------------------
# ★ 界面里不该出现 douyin / xiaohongshu 这种内部标识。
#   改版前有 4 处（关键词抓取/选题挖掘/评论区截流的下拉、复盘监控的表格）
#   直接把键当文案显示，客户看到的是英文单词挂在「平台」旁边。
#
#   真名不另立新表：短视频类的短键去 DOWNLOAD_PLATFORMS 取 label，
#   历史数据里的 slug（douyin-tiktok-china）去 adapter.PLATFORM_DISPLAY 取。
#   下面这张只在整个包都 import 失败时兜底。
_PLATFORM_FALLBACK = {
    "douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手",
    "tiktok": "TikTok", "bilibili": "B站", "weibo": "微博",
    "weixin_channels": "视频号", "weixin_mp": "公众号",
}


def platform_label(key: str) -> str:
    """把平台内部键（douyin / douyin-tiktok-china …）换成中文名。

    认不出来就原样返回 —— 宁可显示一个陌生英文串，也不要把值吞成空。
    """
    k = str(key or "").strip()
    if not k:
        return ""
    try:
        from justoneapi_collector.video_downloader import DOWNLOAD_PLATFORMS
        label = str((DOWNLOAD_PLATFORMS.get(k) or {}).get("label") or "").strip()
        if label:
            return label
    except Exception:                                      # noqa: BLE001
        pass
    # ★ 新增的解析平台（视频号 / 公众号 / B站…）在 JustOneAPI 上没有采集接口，
    #   所以它们既不在 DOWNLOAD_PLATFORMS 也不在 PLATFORM_DISPLAY 里。
    #   中文名的**单一数据源**在 media_parser.PLATFORM_LABELS —— 不在这边另立
    #   一张表，否则两处会慢慢写歪，界面上就会出现两种叫法。
    try:
        from justoneapi_collector.media_parser import PLATFORM_LABELS
        label = str(PLATFORM_LABELS.get(k) or "").strip()
        if label:
            return label
    except Exception:                                      # noqa: BLE001
        pass
    try:
        from justoneapi_collector.platforms.adapter import PLATFORM_DISPLAY
        label = str(PLATFORM_DISPLAY.get(k) or "").strip()
        if label:
            return label
    except Exception:                                      # noqa: BLE001
        pass
    return _PLATFORM_FALLBACK.get(k, k)


def platform_choices() -> list[tuple[str, str]]:
    """下拉要用的 [(中文名, 内部键), ...]，顺序固定。"""
    return [(platform_label(k), k) for k in ("douyin", "xiaohongshu", "kuaishou")]


def fill_platform_box(box, width: int = 120) -> None:
    """给一个空 QComboBox 填上平台选项：显示中文、真值挂在 itemData 上。

    取真值时一律用 `box.currentData() or box.currentText()`，
    这样即便将来有人手工 addItem 忘了带 data 也不会拿到中文名当接口参数。
    """
    for label, key in platform_choices():
        box.addItem(label, key)
    box.setFixedWidth(width)


# --------------------------------------------------------------------------
# 单行状态行：正常时一行灰字，出问题时自动升格成一条提示条
# --------------------------------------------------------------------------
# 每档：文字色 / 底色 / 边框（None 表示透明底、无边框、无内边距）
_STATUS_STYLE = {
    "light": {
        "idle":  ("#5B6472", None, None),
        "ok":    ("#0F9960", None, None),
        "warn":  ("#78350F", "#FFFBEB", "#FDE68A"),
        "error": ("#7F1D1D", "#FEF2F2", "#FECACA"),
    },
    "dark": {
        "idle":  ("#9AA4B2", None, None),
        "ok":    ("#34D399", None, None),
        "warn":  ("#CBB47E", "#2A2110", "#4A3A12"),
        "error": ("#DBA5A5", "#2A1618", "#4A2226"),
    },
}


class StatusLine(QLabel):
    """页面底部的单行状态。

    ★ 为什么不用普通 QLabel（2026-10-17 视觉改版）
      全软件 14 个页面、41 处 `setStyleSheet("color:#DC2626;font-size:12px")`：
      改版前所有异常都只是一行更红的 12px 文字，没有容器、没有留白。
      客户扫过去只看到"红字"，分不清这是系统消息还是正文的一部分 ——
      和那行裸贴的「⚠ 授权码里缺少采集凭证」是同一个问题。

      现在统一四档语义：
        idle   一行灰字，透明底 —— 正常情况下完全不占视觉
        ok     一行绿字 —— "做完了"
        warn   琥珀浅底 + 细边 + 内边距 —— "要注意"
        error  红色浅底 + 细边 + 内边距 —— "失败了"

      关键取舍：**只有 warn / error 才升格成"条"**。正常态保持一行字，
      否则每条状态都变成色块，整页全是框，反而更乱。

    用法（页面里只需把 QLabel 换成它，并把内联改样式换成 mark）：
        self.status = StatusLine("就绪", self)
        self.status.setText("挖掘完成：12 条")
        self.status.mark("ok")
    """

    def __init__(self, text: str = "", parent=None, mode: str | None = None):
        # 参数顺序与 QLabel(text, parent) 对齐 —— 页面里原本的 QLabel(...) 调用
        # 可以直接换成 StatusLine(...) 而不必改参数位置。
        super().__init__(text, parent)
        if mode is None:
            # 构造时没指定就跟当前主题，避免深色主题下页面里冒出一条白底状态
            try:
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                mode = (app.property("theme_mode") or "light") if app else "light"
            except Exception:                                # noqa: BLE001
                mode = "light"
        self._mode = mode
        self._level = "idle"
        self.setWordWrap(True)
        self._restyle()

    def apply_theme(self, mode: str) -> None:
        self._mode = mode
        self._restyle()

    def mark(self, level: str) -> None:
        """设置语义档位并刷新样式。level 取 idle / ok / warn / error。"""
        new = level if level in _STATUS_STYLE["light"] else "idle"
        # 从"没事"升格成"有警告"时淡入一次：这条是突然出现的系统消息，
        # 120ms 的浮现能让眼睛捕捉到"刚才多了一行东西"，而不是凭空多一块色。
        rising = new in ("warn", "error") and self._level not in ("warn", "error")
        self._level = new
        self._restyle()
        if rising and self.isVisible():
            from gui_qt.widgets.motion import fade_in
            fade_in(self, duration=120)

    def _restyle(self) -> None:
        table = _STATUS_STYLE["dark" if self._mode == "dark" else "light"]
        color, bg, line = table.get(self._level, table["idle"])
        parts = [f"color:{color};", "font-size:12px;"]
        if self._level in ("warn", "error"):
            parts.append("font-weight:600;")
            parts.append(f"background:{bg};")
            parts.append(f"border:1px solid {line};")
            parts.append("border-radius:8px;")
            parts.append("padding:7px 11px;")
        else:
            # 用 background:transparent 显式盖掉 QSS 的通配底色（见 light.qss 顶部说明）
            parts.append("background:transparent;")
        self.setStyleSheet("".join(parts))



# --------------------------------------------------------------------------
# 「未连接采集服务」的真实原因诊断
# --------------------------------------------------------------------------

def connection_diagnosis() -> tuple[str, str]:
    """返回 (标题, 详细说明)，说清**到底为什么连不上**，而不是笼统让人去激活。

    ★ 这个函数是踩坑补的（2026-10-12 客户投诉）：
    客户明明已经激活了授权码，弹窗却写着「请到设置粘贴授权码并点立即激活」。
    他反复激活都没用，因为**真正的原因是授权码里没带采集凭证**（发码时
    「接口 Token」字段留空 —— 批量出码那条路径更是永远留空）。
    把「没激活」和「激活了但码里没凭证」混成一句话，客户就彻底无路可走。

    所以这里按状态分开说，并且一律带上机器码与联系方式 —— 客户要解决这类
    问题只能找服务方换码，必须让他一步就能把该给的信息给全。
    """
    try:
        from justoneapi_collector import branding, licensing
    except Exception:                                        # noqa: BLE001
        branding = licensing = None                          # type: ignore[assignment]

    contact = str(getattr(branding, "APP_CONTACT", "") or "") if branding else ""
    st: dict = {}
    try:
        st = licensing.status() if licensing else {}         # type: ignore[union-attr]
    except Exception:                                        # noqa: BLE001
        st = {}

    mid = str(st.get("machine_id") or "") or "（设置页可复制）"
    tail = ""
    if contact:
        tail += f"\n联系方式：{contact}"
    tail += f"\n本机机器码：{mid}"

    # ★ 2026-11-23：默认 okdata 通道下，采集凭证是用户在「设置 → 数据服务」
    #   自己填的 okd_ Key，授权码里的 token 只服务原 JustOneAPI 通道。
    #   这时再让人「找服务方重发带接口凭证的授权码」是彻底误导——
    #   客户的授权码没问题，缺的是自己那把 okdata Key。
    try:
        _cfg = AppContext._load_settings()
        _provider = str(_cfg.get("api_provider", "okdata") or "okdata").strip().lower()
        _has_okd = bool(str(_cfg.get("okdata_key") or "").strip())
    except Exception:                                        # noqa: BLE001
        _provider, _has_okd = "justoneapi", False
    if _provider != "justoneapi":
        if _has_okd:
            return (
                "okdata 接口连接失败",
                "你已经在「设置 → 数据服务」填了 okdata Key，但采集接口仍连不上。\n"
                "请确认 Key 有效、余额充足、网络能访问 okdata.online；"
                "必要时重新复制 Key 再保存一次。\n\n"
                "视频下载、直播录制和免费解析不受影响。\n"
                + tail)
        return (
            "请先填写 okdata Key",
            "当前数据通道是 okdata（默认），采集功能需要你自己的接口 Key，"
            "和授权码是两回事 —— 你的授权码本身没有问题。\n\n"
            "① 打开 okdata.online/zh/console，邮箱注册拿到 okd_ 开头的 Key；\n"
            "② 回到软件「设置 → 数据服务」，粘进「okdata Key」并保存；\n"
            "③ 再回来操作即可。\n\n"
            "视频下载、直播录制、视频号/抖音免费解析都不需要这个 Key。\n"
            + tail)

    if st.get("licensed"):
        return (
            "授权码里缺少采集凭证",
            "你的授权码【已经激活成功】了，但它里面没有包含采集服务的接口凭证，"
            "所以仍然连不上。\n\n"
            "问题不在你，也不用重复激活 —— 只能由服务方重新签发一张"
            "「带接口凭证」的授权码来换掉这张。\n"
            "请把下面的机器码发给服务方，并说明「授权码里没有采集凭证」。"
            + tail)
    if st.get("state") == "trial":
        return (
            "试用期不含采集服务",
            "当前处于试用期。试用可以浏览界面和各功能，但采集服务需要在激活"
            "授权码之后才会接入。\n\n"
            "需要授权码的话，把下面的机器码发给服务方即可。"
            + tail)
    if st.get("state") == "free":
        # ★ 免费版（v1.14.0）：试用期结束后不再锁死，但采集类功能仍需激活。
        #   这里必须分开说 —— 把「免费版不含采集」和「你没激活」混成一句，
        #   客户会以为软件坏了（2026-10-12 那次投诉就是这么来的）。
        return (
            "免费版不含采集服务",
            "当前是免费版：「视频下载」和「直播录制」可以一直用，不收费。\n"
            "但评论截流、文案仿写这类需要调用接口的功能，"
            "要激活授权码之后才会接入。\n\n"
            "需要授权码的话，把下面的机器码发给服务方即可。"
            + tail)
    if st.get("state") == "expired":
        return (
            "授权已过期",
            f"{st.get('label') or '授权已到期'}。\n\n续费后重新激活即可继续使用；"
            "在此之前「视频下载」和「直播录制」仍可免费使用。"
            + tail)
    return (
        "尚未激活授权码",
        "当前还没有激活授权码，采集服务未接入。\n\n"
        "请到「设置 → 授权与激活」粘贴服务方发给你的授权码，点「立即激活」，"
        "成功后再回来操作。" + tail)


# --------------------------------------------------------------------------
# 占位采集客户端
# --------------------------------------------------------------------------
class NullClient:
    """免费档用的占位客户端：发不出真请求，但**不会 AttributeError**。

    为什么需要它（v1.14.2）：免费档没有 Token，`ctx.client` 是 None，于是
    下载器和直播监控都构造不出来 —— 而「抖音下载」与「直播录制」这两条
    零成本链路**本来就不需要用 client**（一个是 httpx 抓页面，一个是 webcast
    接口）。结果就是免费档被自己的门禁挡死，客户点什么都提示"请填 API Token"。

    给它一个永远失败的 request()：
      - 零成本链路照常跑（它们不打这里）；
      - 万一走到付费路径，拿到的是 success=False，而不是异常。
    """

    token_invalid = False

    def request(self, *args, **kwargs):
        try:
            from justoneapi_collector.models import ApiResponse
        except Exception:  # noqa: BLE001
            return None
        return ApiResponse(success=False, code=-1,
                           message="未接入采集服务（免费档只走零成本通道）",
                           data=None, raw_json={})


def _feature_ok(name: str, default: bool = True) -> bool:
    """某个功能位是否可用；读不到授权态按 default（不拦）。"""
    try:
        from justoneapi_collector import licensing
        ok, _why = licensing.check(name)
        return bool(ok)
    except Exception:  # noqa: BLE001
        return default


# --------------------------------------------------------------------------
# 应用上下文：跨页面共享业务资源（缺失时降级为演示模式）
# --------------------------------------------------------------------------
# 配置默认值（与旧版 gui.py 的 defaults 对齐；仅保留新版实际用到的键）
_SETTINGS_DEFAULTS: dict = {
    "token": "",
    # ★ 2026-11-23：付费数据通道默认 okdata（按次、价格公开、支持支付宝）。
    # 视频直链仍走 video.zacao.top 免费解析，直播录制仍是本地 ffmpeg，互不影响。
    # 原 JustOneAPI 通道保留，设置页可随时切回。
    "api_provider": "okdata",
    "okdata_key": "",
    "okdata_base_url": "https://api.okdata.online",
    "base_url": "https://api.justoneapi.com",
    "timeout": 120,
    "max_retries": 3,
    "max_concurrency": 5,
    "cache_enabled": True,
    # ★ 2026-11-28 本机 media-parser 解析引擎（可选、免费、不花采集额度）。
    #   默认关，配置后优先解析、失败自动回退既有 zacao / CDP 链路。
    "local_parser_enabled": False,
    "local_parser_base_url": "http://127.0.0.1:8051",
    "local_parser_api_key": "",       # 仅自建 SaaS 模式才需要；默认免鉴权
    "local_parser_dir": "",           # 服务目录，空则用 exe 同级的 media-parser
    # 强制登录开关：开启后未登录抖音不允许进入软件（见 gui_qt/login_gate.py）
    "require_login": True,
    "douyin_cookie": "",
    "dl_save_dir": "",
    "ffmpeg_path": "",
    "lm_save_dir": "",
    "history_enabled": True,      # 抓取时是否把结果记到本机历史库（v1.12.0）
    # ★ 匿名故障摘要（2026-11-15 P0）：三项默认必须保持「关 / 空 / 空」。
    #   客户端不写死任何服务器地址；没配 endpoint 就绝不联网。
    #   `gui_qt/diagnostics.py` 的门禁会断言这三项默认值，不许改。
    "diag_report_enabled": False,
    "diag_endpoint": "",
    "diag_anon_id": "",           # 随机匿名 ID，与机器无关（首次记录时生成）
}


class AppContext:
    """跨页面共享的资源容器。

    真实应用启动时用真实 client / monitor / transcriber / downloader 填充；
    无环境（无头烟囱测试）时保持 None，各页面自动切到演示数据。
    """

    def __init__(self):
        self.client = None          # justoneapi_collector.client.JustOneAPICollector
        self.settings: dict = self._load_settings()
        self.daemon = None          # 直播监控 LiveMonitor
        self.transcriber = None     # transcribe.Transcriber
        self.downloader = None      # video_downloader.VideoDownloader
        self.project_manager = None
        self.app_data_dir = self._resolve_app_data_dir()   # %APPDATA%/TrafficCatcher
        # 历史库开关：写在设置里，启动时同步给业务模块。
        # 关掉后不再写新快照，但已有记录照样能查（只影响写入）。
        try:
            from justoneapi_collector import history_store
            history_store.set_enabled(bool(self.settings.get("history_enabled", True)))
        except Exception:                                    # noqa: BLE001
            pass

    @staticmethod
    def _resolve_app_data_dir() -> Path:
        """程序数据目录（登录 profile、ffmpeg、配置都在这里）。"""
        try:
            from justoneapi_collector import branding
            return Path(branding.app_data_dir())
        except Exception:  # noqa: BLE001
            return (Path(os.environ.get("APPDATA", str(Path.home())))
                    / "TrafficCatcher")

    # ------------------------------------------------------------ 配置读写
    @staticmethod
    def settings_path() -> Path:
        """本机配置文件路径（%APPDATA%\\TrafficCatcher\\config.json）。"""
        try:
            from justoneapi_collector import branding
            return branding.app_data_dir() / "config.json"
        except Exception:  # noqa: BLE001
            return (Path(os.environ.get("APPDATA", str(Path.home())))
                    / "TrafficCatcher" / "config.json")

    @classmethod
    def _load_settings(cls) -> dict:
        """从 config.json 读取配置并与默认值合并（老用户缺键也能拿到默认值）。

        读取失败一律回退默认值，绝不让配置问题拖垮启动。
        """
        merged = dict(_SETTINGS_DEFAULTS)
        try:
            p = cls.settings_path()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    merged.update({k: v for k, v in data.items() if v is not None})
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 读取配置失败，使用默认值：{exc}")
        return merged

    def save_settings(self) -> bool:
        """把当前 settings 落盘到 config.json。失败返回 False（不抛异常）。"""
        try:
            p = self.settings_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.settings, indent=2, ensure_ascii=False),
                         encoding="utf-8")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 保存配置失败：{exc}")
            return False

    @property
    def demo(self) -> bool:
        """是否为演示模式（没有真实 client）。"""
        return self.client is None

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    # ------------------------------------------------------------ 业务资源
    def connect_real_client(self) -> bool:
        """构造真实采集 client：按设置选择 okdata（默认）或原 JustOneAPI。

        两家都没有凭证 / 构造失败都安全回退演示模式（client=None），返回 False。
        视频直链解析不经过这里，始终走 video.zacao.top 免费通道。
        供启动时 build_context 与设置页保存/激活后即时调用。
        """
        def _int(key, dv):
            try:
                return int(self.settings.get(key, dv) or dv)
            except (TypeError, ValueError):
                return dv

        provider = str(self.settings.get("api_provider", "okdata") or "okdata").strip().lower()

        # ---------------------------------------------------- okdata（默认）
        if provider != "justoneapi":
            try:
                from justoneapi_collector.okdata_client import OkdataClient
            except Exception as exc:  # noqa: BLE001
                print(f"[流量捕手] 未能加载 okdata 通道，维持演示模式：{exc}")
                return False
            key = str(self.settings.get("okdata_key") or "").strip()
            if not key:
                # 老用户若把 okdata Key 放进授权码里（okd_ 开头），照样认。
                try:
                    from justoneapi_collector.licensing import embedded_token
                    embedded = (embedded_token() or "").strip()
                    if embedded.startswith("okd_"):
                        key = embedded
                except Exception:                        # noqa: BLE001
                    pass
            if not key:
                self.client = None
                return False
            try:
                self.client = OkdataClient(
                    key=key,
                    base_url=str(self.settings.get("okdata_base_url")
                                 or "https://api.okdata.online"),
                    timeout=_int("timeout", 30),
                    max_retries=_int("max_retries", 1),
                )
                # ★ 不套 JustOneAPI 授权码额度闸门：那是按 JustOneAPI 次数发的，
                #   okdata 有自己的 Key 预算上限和逐笔账单，套上去会误拦。
                print("[流量捕手] 已接入 okdata 采集通道")
                return True
            except Exception as exc:  # noqa: BLE001
                print(f"[流量捕手] okdata 通道构造失败，回退演示模式：{exc}")
                self.client = None
                return False

        # ---------------------------------------------------- JustOneAPI（原通道）
        try:
            from justoneapi_collector.client import JustOneAPICollector
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 未能加载业务模块，维持演示模式：{exc}")
            return False

        # 1) 授权码内嵌 Token（激活用户，最高优先级）
        token = ""
        try:
            from justoneapi_collector.licensing import embedded_token
            token = (embedded_token() or "").strip()
        except Exception:  # noqa: BLE001
            token = ""
        # 2) 设置页里填写的 API Token（旧版的使用方式，务必保留）
        if not token:
            token = str(self.settings.get("token") or "").strip()
        if not token:
            self.client = None
            return False

        try:
            self.client = JustOneAPICollector(
                token=token,
                base_url=self.settings.get("base_url", "https://api.justoneapi.com"),
                timeout=_int("timeout", 120),
                max_retries=_int("max_retries", 3),
                max_concurrency=_int("max_concurrency", 5),
                cache_enabled=bool(self.settings.get("cache_enabled", True)),
            )
            # 套上每日调用配额闸门：额度写在本机授权码里，超限直接拦下不发请求。
            # 只统计 JustOneAPI 调用，抖音直连的功能（喜欢/收藏抓取、下载）不受影响。
            try:
                from justoneapi_collector import quota
                quota.install(self.client)
            except Exception as exc:              # noqa: BLE001
                print(f"[流量捕手] 配额闸门未启用（不影响使用）：{exc}")
            print("[流量捕手] 已接入真实采集服务")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 真实 client 构造失败，回退演示模式：{exc}")
            self.client = None
            return False

    def ensure_downloader(self):
        """按需构造/刷新 VideoDownloader。

        返回 VideoDownloader 或 None。会注入设置里保存的各平台登录 Cookie，
        并采用设置里的保存目录（缺省则用下载器自带默认）。

        ★ v1.14.2：没有真实 client（免费档 / 没 Token 的试用机）时，用
          NullClient 也能构造 —— 抖音免费通道靠的是 httpx 抓页面 + 登录
          Cookie，压根不需要接口。**顺手把 Cookie 注入也带上了**，这比
          页面临时 `VideoDownloader(_MockClient())` 的裸构造命中率高得多。
          其余付费功能仍由各自的门禁拦住，不会被免费档白用。
        """
        client = self.client
        if client is None:
            if not _feature_ok("download"):
                return None
            client = NullClient()
        try:
            from justoneapi_collector.video_downloader import VideoDownloader
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 未能加载下载模块：{exc}")
            return None
        save_dir = str(self.settings.get("dl_save_dir") or "").strip() or None
        cookie = str(self.settings.get("douyin_cookie") or "").strip()
        ffmpeg_path = str(self.settings.get("ffmpeg_path") or "").strip() or None
        try:
            if self.downloader is not None:
                try:
                    self.downloader.close()
                except Exception:  # noqa: BLE001
                    pass
            self.downloader = VideoDownloader(
                client, save_dir=save_dir, cookie=cookie, ffmpeg_path=ffmpeg_path,
                browser_profile_dir=str(self.app_data_dir / "browser_profile"),
            )
            # 其它平台（小红书 / 快手 / TikTok）已保存的 Cookie 一并注入
            for plat in ("xiaohongshu", "kuaishou", "tiktok"):
                ck = str(self.settings.get(f"{plat}_cookie") or "").strip()
                if ck:
                    try:
                        self.downloader.set_cookie_for(plat, ck)
                    except Exception:  # noqa: BLE001
                        pass
            return self.downloader
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 构造下载器失败：{exc}")
            self.downloader = None
            return None

    def ensure_monitor(self):
        """按需构造/刷新直播监控 LiveMonitor。

        返回 LiveMonitor 或 None。会注入设置里保存的 ffmpeg 路径、抖音登录
        Cookie 与直播录制保存目录（缺省则用 LiveMonitor 自带默认目录）。
        直播监控页依赖此对象，缺失时整页会落到演示分支、录制无法使用。

        ★ v1.14.2：没有真实 client 时用 NullClient 构造。直播录制的主力链路是
          抖音 webcast 接口（本地 a_bogus 签名，零成本），只有"按 sec_uid 查
          开播状态"才走付费接口 —— 用占位 client 时那条路返回失败，但
          「粘贴直播间链接监控」这条路完全可用，免费档才不至于整页锁死。
        """
        client = self.client
        if client is None:
            if not _feature_ok("live"):
                return None
            client = NullClient()
        try:
            from justoneapi_collector.live_monitor import LiveMonitor
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 未能加载直播监控模块：{exc}")
            return None
        if self.daemon is not None:
            return self.daemon
        try:
            from pathlib import Path as _P
            save_dir = str(self.settings.get("lm_save_dir") or "").strip() or None
            ffmpeg_path = str(self.settings.get("ffmpeg_path") or "").strip() or None
            cookie = str(self.settings.get("douyin_cookie") or "").strip()
            # 未配置时传 None ⇒ LiveMonitor 自动探测客户机代理端口；
            # 用户显式存空串则代表「明确不用代理」，不再自动覆盖。
            tk_proxy = self.settings.get("tiktok_live_proxy")
            self.daemon = LiveMonitor(
                client,
                save_dir=_P(save_dir) if save_dir else None,
                auto_record=bool(self.settings.get("lm_auto_record", True)),
                ffmpeg_path=ffmpeg_path,
                douyin_cookie=cookie,
                tiktok_proxy=tk_proxy,
            )
            print("[流量捕手] 直播监控引擎已就绪")
            return self.daemon
        except Exception as exc:  # noqa: BLE001
            print(f"[流量捕手] 构造直播监控失败：{exc}")
            self.daemon = None
            return None


# --------------------------------------------------------------------------
# 跨页联动：把结果带去下一个环节
# --------------------------------------------------------------------------

def send_to_script(ctx, links, parent=None) -> tuple[bool, str]:
    """把作品链接送到「文案仿写」页并跳过去。

    ★ 为什么要这条链路（2026-10-14 体验重构）：
    以前挖到一条好爆款，要自己复制链接 → 左侧点「文案仿写」→ 粘进链接框 →
    点「提取文案」四步，中间还容易丢。真实用法本来就是连着的：
    「找对标 → 扒文案 → 仿写」，软件应该自己把这三步接起来。

    links 为空或目标页不可用时返回 (False, 原因)，调用方据此提示用户。
    """
    links = [str(x).strip() for x in (links or []) if str(x).strip()]
    if not links:
        return False, "没有拿到作品链接"
    win = getattr(ctx, "main_window", None)
    if win is None:
        return False, "主窗口未就绪"
    target = win.page("文案仿写") if hasattr(win, "page") else None
    if target is None or not hasattr(target, "accept_links"):
        return False, "文案仿写页未就绪"
    try:
        n = target.accept_links(links)
    except Exception as exc:                                 # noqa: BLE001
        return False, f"填入链接失败：{exc}"
    if not n:
        return False, "链接没能填进文案仿写页"
    win.switch_to("文案仿写")
    return True, f"已带 {n} 条链接到「文案仿写」，正在提取口播稿"


def cell_num(text) -> float:
    """把表格里的数字文本转回数值：'1.2万' → 12000，'1,234' → 1234。

    结果表里为了好读会把大数写成「1.2万」，统计概览要算平均值就得还原回去。
    认不出来的返回 0，不抛异常——统计数字算错不影响主流程。
    """
    s = str(text or "").strip().replace(",", "")
    if not s:
        return 0.0
    mul = 1.0
    for mark, m in (("亿", 1e8), ("万", 1e4), ("w", 1e4), ("W", 1e4), ("千", 1e3), ("k", 1e3)):
        if s.endswith(mark):
            s = s[: -len(mark)]
            mul = m
            break
    try:
        return float(s) * mul
    except ValueError:
        return 0.0


def selected_row_links(table, link_col: int = -1, limit: int = 5) -> list[str]:
    """取结果表里选中行（没选中就取前 N 行）的链接列。

    没选中时取前几行而不是报错：用户往往是刚跑完就直接点「写同款文案」，
    这时表格根本还没点过，硬性要求选一行很反直觉。
    """
    if table is None or table.rowCount() == 0:
        return []
    col = link_col if link_col >= 0 else table.columnCount() - 1
    rows = []
    cur = table.currentRow()
    if cur >= 0:
        rows = [cur]
    else:
        rows = list(range(min(limit, table.rowCount())))
    out = []
    for r in rows:
        it = table.item(r, col)
        if it is not None and it.text().strip():
            out.append(it.text().strip())
    return out


# --------------------------------------------------------------------------
# 后台任务线程
# --------------------------------------------------------------------------
# 运行中的 Worker 强引用集合 —— 这是一条**生死攸关**的保护。
#
# QThread 对象若没有 Python 侧的强引用，会在线程仍在运行时被垃圾回收销毁，
# Qt 随即触发 fatal 错误 "QThread: Destroyed while thread is still running"
# 并直接 abort（进程退出码 0xC0000409），表现为：打包后的 exe 启动几秒内
# 无声崩溃，且没有任何 Python 异常、没有 traceback —— 极难排查。
#
# 像 `Worker(fn).start()` 这种不留引用的写法必踩此坑；即使是局部变量
# `w = Worker(fn); w.start()`，一旦所在函数返回也可能被回收。
# 因此在这里统一托管：start() 时登记，finished 时释放。所有调用点无需
# 各自保存引用，也不会再被 GC 提前销毁。
_LIVE_WORKERS = set()


class Worker(QThread):
    """通用后台任务。

    用法::
        w = Worker(self._do_work, arg1, kw=value)
        w.progress.connect(self._on_progress)
        w.log.connect(self._on_log)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.start()

    fn 的签名为 fn(worker, *args, **kwargs)，通过 worker.emit_progress / worker.emit_log
    在任务内部上报进度与日志。

    线程存活期间的强引用由本类自动托管（见 _LIVE_WORKERS），调用方不必自行保存。
    """

    progress = Signal(float, str)   # (百分比0-100, 文案)
    log = Signal(str)
    done = Signal(object)
    error = Signal(str)
    # ★ 逐条完成事件（2026-11-19）：批量任务里每完成一个条目就发一次，
    #   让界面能在任务**进行中**累加计数，而不是等整批 done 才一次性跳数。
    #   payload 由各任务自己定（下载页用 (video, ok)），基类不解释内容。
    #   加信号是纯增量，其他页面不 connect 就完全不受影响。
    item_done = Signal(object)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._registered = False

    def start(self, *args, **kwargs):
        """启动线程，并登记强引用防止运行期被 GC 销毁。"""
        if not self._registered:
            self._registered = True
            _LIVE_WORKERS.add(self)
            self.finished.connect(self._release)
        super().start(*args, **kwargs)

    def _release(self):
        """线程结束后释放强引用，允许正常回收。"""
        _LIVE_WORKERS.discard(self)
        self._registered = False

    def emit_progress(self, pct: float, msg: str = ""):
        self.progress.emit(float(pct), msg)

    def emit_log(self, msg: str):
        self.log.emit(str(msg))

    def emit_item(self, payload):
        """上报一条「逐条完成」事件；界面可据此实时累加进度计数。"""
        self.item_done.emit(payload)

    def run(self):
        try:
            result = self._fn(self, *self._args, **self._kwargs)
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(f"{type(exc).__name__}: {exc}")


def connect_guarded(signal, slot, *, on_error=None, label="界面更新"):
    """把槽接到信号上，并兜住槽**内部**抛出的异常。

    为什么需要：PySide6 里槽函数抛异常只会把 traceback 打到 stderr。
    打包成 exe 后没有控制台，于是现象是「按钮恢复了、列表不动、状态栏
    永远停在『正在解析，请稍候…』」—— 客户只看得到卡住，看不到原因。
    2026-10-18 客户现场正是如此：`_fmt_duration` 里 `f"{m:02D}"` 抛
    ValueError，点在「解析成功」的回调上，看起来像一直没解析完。

    on_error(msg)：把一句话原因交给调用方（一般是写状态栏）。
    """
    def _wrapped(*args):
        try:
            return slot(*args)
        except Exception as exc:                       # noqa: BLE001
            msg = f"{label}出错：{type(exc).__name__}: {exc}"
            try:
                from justoneapi_collector.logger import get_logger
                get_logger("ui").exception(msg)
            except Exception:                          # noqa: BLE001
                pass
            import traceback
            traceback.print_exc()
            if on_error is not None:
                try:
                    on_error(msg)
                except Exception:                      # noqa: BLE001
                    pass
    signal.connect(_wrapped)
    return _wrapped


# --------------------------------------------------------------------------
# 复用 UI 组件
# --------------------------------------------------------------------------
def card(parent=None) -> QFrame:
    """圆角白卡片（浅色主题），用作内容分区容器。"""
    c = QFrame(parent)
    c.setObjectName("Card")
    return c


def vertical_card(title: str, parent=None) -> tuple[QFrame, QVBoxLayout]:
    """带标题的竖向卡片，返回 (frame, layout)。"""
    c = card(parent)
    layout = QVBoxLayout(c)
    # ★ 2026-10-27：卡片圆角 16 → 18（更圆润），上下内边距跟着抬到 18 让文字
    #   离弧线远一点。**左右留在 16 不动** —— 实测每加 1px，宽表格（9 列那几
    #   张）就会挤出一根横向滚动条（brand 自检拦的就是这个），而客户最常用的
    #   那几张表本来就贴着可用宽度的边。左右一动就是全局回归，不值。

    if title:
        t = QLabel(title, c)
        # ★ 走 QSS 语义类，不内联写字号：内联样式优先级高于 QSS，一旦写死
        #   color:#111827，切到深色主题就是"深底深字"直接看不见（这是真踩过的坑）。
        t.setObjectName("H2")
        layout.addWidget(t)
    return c, layout


def section_header(text: str, parent=None) -> QLabel:
    """页面内小节标题。"""
    t = QLabel(text, parent)
    t.setObjectName("H2")
    return t


def page_title(text: str, parent=None) -> QLabel:
    """页面大标题。"""
    t = QLabel(text, parent)
    t.setObjectName("H1")
    return t


def log_panel(parent=None) -> QPlainTextEdit:
    """只读运行日志面板（等宽字体，自动滚动）。"""
    te = QPlainTextEdit(parent)
    te.setReadOnly(True)
    te.setObjectName("LogPanel")
    return te


def append_log(te, text: str):
    """向日志面板追加一行并滚动到底。"""
    if te is None:
        return
    te.appendPlainText(str(text))
    te.verticalScrollBar().setValue(te.verticalScrollBar().maximum())


def row_widget(*widgets, spacing: int = 8, parent=None) -> QHBoxLayout:
    """快速拼一个横向布局行。"""
    h = QHBoxLayout()
    h.setSpacing(spacing)
    for w in widgets:
        if w is None:
            h.addStretch()
            continue
        if isinstance(w, str):
            h.addWidget(QLabel(w))
        else:
            h.addWidget(w)
    return h


def form_row(label: str, field, parent=None) -> QHBoxLayout:
    """表单行：左标签 + 右控件（或右布局，自动识别）。"""
    h = QHBoxLayout()
    h.setSpacing(10)
    lab = QLabel(label)
    lab.setMinimumWidth(96)
    lab.setStyleSheet("color:#374151;")
    h.addWidget(lab)
    if isinstance(field, QLayout):
        h.addLayout(field, 1)
    else:
        h.addWidget(field, 1)
    return h


class CollapseBox(QWidget):
    """带标题的可折叠区块。

    ★ 为什么需要它（2026-10-14 体验重构）：
    文案仿写页堆了 23 个输入控件，一屏放不下，新客户看到密密麻麻的
    表单会以为"每个都得填才能用"，于是卡在第一步。真实情况是有 4 个必填
    （体裁 / 时长 / 产品 / 卖点），其余都是可调可不调的。
    把不那么常用的收进折叠区，主界面只留"必填 + 主按钮"，心理负担小很多。
    """

    def __init__(self, title: str, collapsed: bool = True, parent=None):
        super().__init__(parent)
        self._collapsed = bool(collapsed)
        self._title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.btn = QPushButton()
        self.btn.setObjectName("CollapseBtn")
        self.btn.setCheckable(True)
        self.btn.setChecked(not collapsed)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        connect_guarded(self.btn.clicked, lambda *_: self._toggle(), on_error=lambda msg: None, label="toggle")  # 折叠组件无状态栏，异常已由守卫写日志
        lay.addWidget(self.btn)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(10, 8, 10, 8)
        self.body_lay.setSpacing(10)
        lay.addWidget(self.body)
        self._apply()

    def add(self, item):
        """往折叠区里加控件或布局。"""
        if isinstance(item, QLayout):
            self.body_lay.addLayout(item)
        else:
            self.body_lay.addWidget(item)
        return item

    def add_layout(self, layout):
        return self.add(layout)

    def _toggle(self):
        self._collapsed = not self._collapsed
        self.btn.setChecked(not self._collapsed)
        self._apply()

    def _apply(self):
        self.body.setVisible(not self._collapsed)
        try:
            from gui_qt.icons import chevron_icon
            self.btn.setIcon(chevron_icon("#6B7280", 14, up=not self._collapsed))
        except Exception:                                    # noqa: BLE001
            pass
        mark = "▾" if not self._collapsed else "▸"
        self.btn.setText(f"  {mark}　{self._title}")
        self.btn.setStyleSheet(
            "text-align:left;padding:6px 8px;color:#6B7280;font-size:12px;")


# --------------------------------------------------------------------------
# 把控件滚进视口
# --------------------------------------------------------------------------
def ensure_visible(widget) -> None:
    """把控件滚动到可视区域内（页面的祖先里有 QScrollArea 时）。

    ★ 为什么需要：页面都装在 QScrollArea 里，列表一长（批量导入几十条、
      采集结果几百行），进度条就被表格顶到屏幕下面。客户点了「下载选中」，
      只看到按钮变灰，看不到进度 —— 2026-10-17 反馈「进度条不明显」，
      有一半是这个原因。任务一开始就把它滚进视口，比什么都直接。
    """
    try:
        from PySide6.QtWidgets import QScrollArea
        p = widget.parent()
        while p is not None and not isinstance(p, QScrollArea):
            p = p.parent()
        if p is not None:
            p.ensureWidgetVisible(widget, 0, 120)
    except Exception:  # noqa: BLE001
        pass          # 滚不动也不该拦住任务


# --------------------------------------------------------------------------
# 破坏性动作（清空列表等）：统一按钮样式 + 二次确认
# --------------------------------------------------------------------------
def danger_button(text: str, parent=None) -> QPushButton:
    """破坏性动作按钮（清空列表 / 清空结果）：白底红字红边，hover 加深。

    与主操作（Primary 实心靛蓝）形成明确层级差，避免误点；
    左侧配垃圾桶图标，强化「清空」语义。
    """
    b = QPushButton(text, parent)
    b.setObjectName("Danger")
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    try:
        from gui_qt.icons import clear_icon
        # 红色图标在浅/深色主题都清晰，无需随主题刷新
        b.setIcon(clear_icon("#DC2626", 15))
    except Exception:  # noqa: BLE001
        pass
    return b


def confirm_destructive(parent, title: str, text: str,
                        ok_text: str = "确认清空", detail: str = "") -> bool:
    """破坏性动作的二次确认；默认按钮锁定为「取消」，防止手快误删。

    返回 True 表示用户确认执行。
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is ok


# --------------------------------------------------------------------------
# 付费功能闸门（v1.21.0）：免费档点任何付费功能，统一弹「付费须知」
# --------------------------------------------------------------------------
# ★ 为什么要做这一层（2026-10-20 产品定位调整）
#   产品思路改成「免费功能引流」：视频下载 + 直播录制两项零成本、不限量，
#   拿来当入口；其余功能每条都要调采集接口、有实际成本，收费。
#
#   但改之前各页的门禁是五花八门的：
#     · 5 个页有弹窗，文案却各不相同（「功能未开通」「这一项不在免费版里」…）
#     · 8 个付费页**完全没有门禁**（数据采集/批量采集/选题挖掘/竞品体检/
#       复盘监控/报告/音视频转文本 + 评论区/GEO/关键词/仿写的老弹窗文案不统一）
#       —— 免费用户点进去照样跑，看到的是一堆失败和空表格，第一反应是
#       "这软件坏了"，而不是"这项要钱"。引流入口反而变成差评来源。
#       （数据趋势页是例外：只读本机账本、零接口成本，且到期客户要能看
#         自己的历史数据，故意不设闸门，理由写在 trend_page.py 顶部注释里。）
#
#   现在统一成两个函数，页面里一行调用：
#       if not require_feature(self, "collect", "执行采集"):
#           return
#   不可用就弹付费须知并返回 False，调用方直接 return。
#
#   ⚠️ 只拦「点了要干活」的主动作，不要在切页时弹 —— 那是骚扰。
#      免费档能正常用的只有视频下载与直播录制，这两条路一根手指都不许碰。
#   ⚠️ 老代码用「禁用按钮」当门禁（comment/geo 两页这么干过）。禁用 = 点了
#      没反应，用户只会以为软件坏了。一律改成按钮可点 + 弹须知。

# 免费档永久可用、且不限次数的两项（与 licensing.FREE_FEATURES 一致，
# 这里单独列一份是为了写文案；判据仍以 licensing 为准）。
_FREE_FEATURE_BLURB = (
    "抖音视频下载：原画质无水印，可批量，不限次数\n"
    "直播录制：开播自动录，不用人守着，不限次数"
)

# 付费档位（与官网 PRICE_PLANS、licensing.PLANS 一致；改价要三处同步）
_PAID_PLANS = (
    ("体验版", "19.9", "38 天", "10"),
    ("标准版", "29.9", "月", "30"),
    ("专业版", "59.9", "月", "100"),
)


def _goto_activation(parent) -> None:
    """跳转到「设置 → 授权与激活」。拿不到主窗口就安静失败（弹窗照常关）。"""
    try:
        ctx = getattr(parent, "ctx", None)
        win = getattr(ctx, "main_window", None)
        if win is not None and hasattr(win, "switch_to"):
            win.switch_to("设置")
            page = win.page("设置") if hasattr(win, "page") else None
            # 直接滚到授权区：客户粘码时不用自己在一长串设置里找
            if page is not None and hasattr(page, "focus_activation"):
                page.focus_activation()
    except Exception:                                          # noqa: BLE001
        pass


def paid_notice(parent, feature: str = "", what: str = "",
                extra: str = "") -> None:
    """付费须知弹窗：说清「这项要钱、免费能给什么、多少钱、怎么买」。

    feature 传功能位（collect/keyword/…）只为文案更准；what 是用户刚点的动作
    （如「执行采集」），会出现在第一行，让人知道弹窗是被自己哪一下触发的。
    extra 给独立模块补一句特殊说明（如 GEO 是单独卖的，不随采集套餐送）。
    """
    from justoneapi_collector import licensing, branding

    name = licensing.FEATURES.get(feature, "") or (what or "这项功能")
    action = f"「{what}」" if what else f"「{name}」"

    lines = [
        f"{action}是付费功能，当前是免费版，还不能用。",
        "",
        "免费版可以一直用的是这两项（不需要激活码、不限制次数）：",
        _FREE_FEATURE_BLURB,
        "",
        "其他功能每跑一次都要调用采集接口，每条都有实际成本，所以放在付费版里。",
        "",
        "付费版按「每天可以调用接口的次数」分三档：",
    ]
    for plan, price, unit, quota in _PAID_PLANS:
        lines.append(f"· {plan}：{price} 元 / {unit}，每天 {quota} 次")
    if extra:
        lines += ["", extra]
    lines += [
        "",
        "到期不会锁死软件，自动回到免费版，下载和录制照样能用。",
    ]
    text = "\n".join(lines)

    # 详情区放机器码和联系方式 —— 客户截图发过来时我们不用再问一遍
    detail = f"本机机器码：{licensing.machine_id()}"
    if branding.APP_CONTACT:
        detail += f"\n购买 / 续费：{branding.APP_CONTACT}"
    if branding.APP_HOMEPAGE:
        detail += f"\n官网：{branding.APP_HOMEPAGE}"

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("需要激活授权码")
    box.setText(text)
    box.setDetailedText(detail)
    go = box.addButton("去激活授权码", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("知道了", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(go)
    box.exec()
    if box.clickedButton() is go:
        _goto_activation(parent)


def require_feature(parent, feature: str, what: str = "",
                    extra: str = "") -> bool:
    """付费闸门。可用返回 True；不可用弹「付费须知」并返回 False。

    页面里的标准用法（放在主动作的最前面）：
        def _execute(self):
            if not require_feature(self, "collect", "执行采集"):
                return
            ...
    """
    try:
        from justoneapi_collector import licensing
        ok, _why = licensing.check(feature)
    except Exception:                                          # noqa: BLE001
        # 读不到授权态时不拦：拦错了比漏拦更糟（客户明明买了却被挡住）
        return True
    if ok:
        return True
    paid_notice(parent, feature, what, extra)
    return False
