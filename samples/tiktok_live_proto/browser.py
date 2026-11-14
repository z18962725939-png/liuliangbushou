"""用真实 Chromium 浏览器过 TikTok 的 JS 挑战，拿直播间完整 HTML。

实测结论（2026-11-14）：
  * curl / Chrome 指纹模拟库 / 加随机 msToken Cookie / --dump-dom
    一律只拿到 706~1462B 的「Please wait…」WAF 挑战页，或 HTTP 403。
  * 唯一可行的是真浏览器会话：导航后**真实等待 8 秒**让挑战脚本跑完，
    Cookie 里出现 _waftokenid 后页面才变成 300KB+ 的正常直播页。
  * 绝对不能加 --virtual-time-budget：虚拟时间会让挑战脚本跑废。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from .config import ProtoConfig
from .core import is_waf_page, live_url

try:
    from justoneapi_collector.browser_login import CDPSession, find_browser, free_port
except Exception as _exc:                                # noqa: BLE001
    CDPSession = None                                   # type: ignore[assignment]
    find_browser = None                                 # type: ignore[assignment]
    free_port = None                                    # type: ignore[assignment]
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None


class BrowserError(RuntimeError):
    """浏览器会话或 WAF 挑战失败。"""


class TikTokBrowser:
    """一个可复用的无头浏览器会话；轮询期间保持不关，避免反复过 WAF。"""

    def __init__(self, cfg: ProtoConfig):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.session = None
        self.port = 0
        self.profile_dir = ""
        self.last_reason = ""

    # -- 生命周期 -----------------------------------------------------
    def start(self) -> None:
        if self.proc:
            return
        if _IMPORT_ERROR is not None:
            raise BrowserError(f"无法复用主工程 CDP 通道：{_IMPORT_ERROR}")
        found = find_browser()
        if not found:
            raise BrowserError("本机没找到 Edge / Chrome，无法过 TikTok 的浏览器校验")
        exe = found[0]
        self.port = free_port()
        self.profile_dir = str(Path(tempfile.gettempdir()) /
                               f"tc_tiktok_proto_{self.port}")
        args = [
            exe, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-sync",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--ignore-certificate-errors",
            "--disable-blink-features=AutomationControlled",
        ]
        if self.cfg.proxy:
            args.append(f"--proxy-server={self.cfg.proxy}")
        # 注意：这里刻意不加 --virtual-time-budget
        args.append("about:blank")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=creationflags)
        s = CDPSession(self.port)
        if not s.wait_ready(25):
            self.close()
            raise BrowserError("浏览器调试端口没起来")
        try:
            s.connect()
        except Exception as exc:                        # noqa: BLE001
            self.close()
            raise BrowserError(f"CDP 连接失败：{exc}") from exc
        self.session = s

    def close(self) -> None:
        try:
            if self.session:
                self.session.close()
        except Exception:                               # noqa: BLE001
            pass
        self.session = None
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:                           # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:                       # noqa: BLE001
                    pass
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_args):
        self.close()

    # -- 取页 ---------------------------------------------------------
    def _navigate(self, url: str) -> None:
        """在启动时的 about:blank 这唯一 tab 上导航。

        刻意不先 createTarget：Edge 首次启动的引导页会抢占「第一个 page
        target」，导致后续读回来的是引导页而不是直播页。实测直接在
        about:blank 上改 location.href 最稳。
        """
        try:
            self.session.eval_js(f"location.href={url!r}", timeout=6)
        except Exception:                               # noqa: BLE001
            try:
                self.session.open_url(url)
            except Exception:                           # noqa: BLE001
                pass

    def _read_html(self) -> str:
        expr = ("JSON.stringify({h:document.documentElement.outerHTML,"
                "t:document.title,c:document.cookie})")
        raw = self.session.eval_js(expr, timeout=30)
        if not raw:
            return ""
        import json                                     # noqa: PLC0415
        try:
            return str((json.loads(raw) or {}).get("h") or "")
        except (ValueError, TypeError):
            return ""

    def fetch_live_page(self, handle: str) -> str:
        """返回过完 WAF 挑战的完整页面 HTML；失败抛 BrowserError。

        实测补充：同一个 CDP 会话跨页导航后，偶尔会读回 0 字节（tab 被
        Edge 引导页/新 target 抢占）。因此一轮等待全失败时，先关掉浏览器
        重建一个干净的 about:blank 单 tab 会话，再试一轮。
        """
        url = live_url(handle)
        if not url:
            raise BrowserError("handle 格式不对，认不出 TikTok 主播号")
        last_size = 0
        for attempt in range(2):
            self.start()
            self._navigate(url)
            for _round in range(self.cfg.waf_rounds):
                time.sleep(self.cfg.waf_wait)           # 必须是真实时间
                html = self._read_html()
                last_size = len(html)
                if html and not is_waf_page(html):
                    return html
            if attempt == 0:
                self.close()                            # 重建会话再试一轮
        if not self.cfg.proxy:
            self.last_reason = (
                f"只拿到 {last_size} 字节的校验页；本机没有配置海外出口，"
                "TikTok 页面在国内直连会被重置，请先在设置里填代理"
            )
        else:
            self.last_reason = (
                f"等了 {self.cfg.waf_rounds} 轮仍是 {last_size} 字节的校验页；"
                "可能是代理 IP 被风控，或该直播间需要登录/年龄验证"
            )
        raise BrowserError(self.last_reason)

    def cookies_header(self) -> str:
        """可选的 Cookie 头（webcast 接口实测匿名可用，这里只作兼容备用）。"""
        if not self.session:
            return ""
        try:
            cookies = self.session.get_all_cookies() or []
        except Exception:                               # noqa: BLE001
            return ""
        parts = []
        for c in cookies:
            name, value = c.get("name"), c.get("value")
            domain = str(c.get("domain") or "")
            if name and value and ("tiktok" in domain or not domain):
                parts.append(f"{name}={value}")
        return "; ".join(parts)
