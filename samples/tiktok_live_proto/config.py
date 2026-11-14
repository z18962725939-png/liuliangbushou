"""TikTok 直播监控原型 · 配置层。

独立原型，不接主程序、不接 release_all 发版链。
所有「环境相关」的东西都做成可配置项，绝不写死：
代理地址、ffmpeg 路径、保存目录、轮询间隔、WAF 等待时长。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field, asdict
from pathlib import Path

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 常见本地代理端口；只用于「自动探测后填进输入框」，不作为硬编码出口
_COMMON_PROXY_PORTS = (7897, 7890, 7891, 10809, 10808, 1080, 2080, 20171)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.25) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def detect_proxy() -> str:
    """探测一个可用的 HTTP 代理，返回形如 http://127.0.0.1:7897。

    顺序：显式环境变量 TIKTOK_LIVE_PROXY → 系统 HTTPS_PROXY/HTTP_PROXY
    → 本机常见代理端口。都没有则返回空串（表示直连，由用户自行判断）。
    """
    explicit = (os.environ.get("TIKTOK_LIVE_PROXY") or "").strip()
    if explicit:
        return explicit if "://" in explicit else f"http://{explicit}"
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val if "://" in val else f"http://{val}"
    for port in _COMMON_PROXY_PORTS:
        if _port_open(port):
            return f"http://127.0.0.1:{port}"
    return ""


def default_ffmpeg() -> str:
    """优先用工程自带 ffmpeg；找不到再退回 PATH。"""
    root = Path(__file__).resolve().parents[2]
    for cand in (root / "assets" / "ffmpeg" / "ffmpeg.exe",
                 root / "assets" / "ffmpeg" / "ffmpeg"):
        if cand.is_file():
            return str(cand)
    import shutil                                        # noqa: PLC0415
    return shutil.which("ffmpeg") or ""


@dataclass
class ProtoConfig:
    """一份可直接序列化到 JSON 的原型配置。"""

    handle: str = ""
    proxy: str = field(default_factory=detect_proxy)
    save_dir: str = ""
    ffmpeg: str = field(default_factory=default_ffmpeg)
    poll_interval: float = 20.0        # 秒，下播时的轮询间隔
    live_poll_interval: float = 15.0   # 秒，录制中的状态复查间隔
    waf_wait: float = 8.0              # 秒，真实浏览器等 JS 挑战的固定真实时间
    waf_rounds: int = 3                # 最多等几轮（每轮 waf_wait 秒）
    headless: bool = True
    record_duration: float = 0.0       # >0 时每段只录这么多秒（便于验证）；0=录到下播
    refresh_minutes: float = 30.0      # 流地址定时重取间隔
    max_start_retry: int = 2           # 开录失败后最多再试几次
    user_agent: str = CHROME_UA

    def __post_init__(self):
        # 用户可能直接粘完整直播间链接；统一存成裸 handle，避免
        # 界面/文件名里出现 @https://… 这种难看又容易出错的值。
        from .core import normalize_handle              # noqa: PLC0415 延迟导入防循环
        self.handle = normalize_handle(self.handle)
        if not self.save_dir:
            self.save_dir = str(Path(__file__).resolve().parent / "recordings")
        self.poll_interval = max(5.0, float(self.poll_interval))
        self.live_poll_interval = max(5.0, float(self.live_poll_interval))
        self.waf_wait = max(3.0, float(self.waf_wait))
        self.waf_rounds = max(1, int(self.waf_rounds))

    # -- 持久化 -------------------------------------------------------
    @property
    def config_path(self) -> Path:
        return Path(__file__).resolve().parent / "config.json"

    @classmethod
    def load(cls, **overrides) -> "ProtoConfig":
        """读取同目录 config.json（只覆盖已知字段），再用命令行参数覆盖。"""
        data = {}
        try:
            import json                                  # noqa: PLC0415
            path = Path(__file__).resolve().parent / "config.json"
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        except Exception:                                # noqa: BLE001
            data = {}
        data.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**data)

    def save(self) -> None:
        """只写用户可改的几项，不把临时运行态落盘。"""
        import json                                      # noqa: PLC0415
        keep = ("handle", "proxy", "save_dir", "ffmpeg", "poll_interval",
                "headless", "record_duration", "refresh_minutes")
        d = {k: v for k, v in asdict(self).items() if k in keep}
        self.config_path.write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                    encoding="utf-8")

    def save_path(self) -> Path:
        p = Path(self.save_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p
