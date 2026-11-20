# -*- coding: utf-8 -*-
"""见舟工作台宿主（2026-11-20）。

把见舟（FastAPI + Vue3 本地工作台）**整套内嵌进流量捕手**：

  * 同进程启动见舟的 ASGI 服务（uvicorn.Server 跑在守护线程）；
  * 数据目录隔离到流量捕手自己的 AppData 下，不碰用户单独装的见舟；
  * 端口自动避让（8741 被占用时顺延），只绑 127.0.0.1；
  * 主题跟随：给见舟页面注入 CSS 变量，深色/浅色跟软件一致；
  * 软件退出时自动停止服务，不留后台进程。

上游代码放在 ``vendor/jianzhou/``，未修改业务逻辑，只做了一处
Python 3.10 语法兼容改写（见 vendor/jianzhou/app/api/channels.py）。

⚠ 授权提示：上游仓库未附 LICENSE，随商业软件分发前应联系作者确认。
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
#  路径：打包后 vendor 在 _MEIPASS 下，源码运行时在工程根下
# ---------------------------------------------------------------------------
_THIS = Path(__file__).resolve().parent
_ROOT = _THIS.parent
_VENDOR = Path(getattr(sys, "_MEIPASS", _ROOT)) / "vendor" / "jianzhou"

BASE_PORT = 8741
PORT_SCAN_LIMIT = 20
START_TIMEOUT = 25.0
HEALTH_PATH = "/healthz"


def vendor_dir() -> Path:
    return _VENDOR


def available() -> bool:
    """上游源码是否随包可用。"""
    return (_VENDOR / "app" / "main.py").is_file() and (_VENDOR / "frontend" / "index.html").is_file()


def data_dir() -> Path:
    """见舟嵌入实例的独立数据目录（与用户自己装的见舟隔离）。"""
    from . import branding
    d = branding.app_data_dir() / "见舟工作台数据"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def choose_port(preferred: int = BASE_PORT) -> int:
    for port in range(preferred, preferred + PORT_SCAN_LIMIT):
        if _port_free(port):
            return port
    raise RuntimeError(f"{preferred} 起连续 {PORT_SCAN_LIMIT} 个端口都被占用")


def _probe(port: int, timeout: float = 1.0) -> dict | None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{HEALTH_PATH}",
                                 headers={"Accept": "application/json"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            import json
            return json.loads(resp.read(64 * 1024).decode("utf-8", errors="replace"))
    except Exception:                                        # noqa: BLE001
        return None


class JianzhouHost:
    """内嵌见舟服务的生命周期管理器（单例由 module-level 变量持有）。"""

    def __init__(self):
        self._server = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        self._lock = threading.RLock()
        self._error: str = ""

    @property
    def port(self) -> int | None:
        return self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/" if self._port else ""

    @property
    def error(self) -> str:
        return self._error

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._port
                    and _probe(self._port) is not None)

    def start(self) -> str:
        """幂等启动，返回见舟首页 URL。失败返回空串并设置 self.error。"""
        with self._lock:
            if self.is_running():
                return self.url
            self._error = ""
            if not available():
                self._error = "内嵌模块未随包安装，请检查安装包完整性"
                return ""
            try:
                port = choose_port()
                d = data_dir()
                # ★ 必须在 import 见舟之前设环境变量：它的 config.py 在
                #   模块加载时就把 DATA_DIR / DEMO_MODE 固化了。
                os.environ["JIANZHOU_DATA_DIR"] = str(d)
                os.environ["JIANZHOU_NO_BROWSER"] = "1"
                os.environ["JIANZHOU_PORT"] = str(port)
                # 演示模式只决定新目录初始状态；用户可在见舟设置页切真实。
                os.environ.setdefault("JIANZHOU_DEMO", "1")

                sys.path.insert(0, str(_VENDOR))
                try:
                    # ★ 先加载 config 再覆盖前端目录，最后才 import app.main：
                    #   app.main 在模块级就 create_app() 并 mount 静态资源，
                    #   晚一步就会挂到不存在的 _MEIPASS/frontend 上（打包后 404）。
                    import app.config as jz_config  # type: ignore
                    jz_config.FRONTEND_DIR = _VENDOR / "frontend"
                    from app.main import app as asgi_app  # type: ignore
                    import uvicorn
                except Exception as exc:                     # noqa: BLE001
                    self._error = f"见舟依赖缺失：{type(exc).__name__}: {exc}"
                    return ""

                cfg = uvicorn.Config(asgi_app, host="127.0.0.1", port=port,
                                     log_level="error", access_log=False)
                server = uvicorn.Server(cfg)
                thread = threading.Thread(target=self._serve, args=(server,),
                                          name="jianzhou-server", daemon=True)
                thread.start()

                deadline = time.monotonic() + START_TIMEOUT
                while time.monotonic() < deadline:
                    if not thread.is_alive():
                        break
                    if _probe(port) is not None:
                        self._server, self._thread, self._port = server, thread, port
                        return self.url
                    time.sleep(0.15)
                self._error = "见舟服务启动超时（数据库迁移可能较慢，请重试）"
                server.should_exit = True
                return ""
            except Exception as exc:                         # noqa: BLE001
                self._error = f"见舟启动失败：{type(exc).__name__}: {exc}"
                return ""

    @staticmethod
    def _serve(server) -> None:
        try:
            server.run()
        except Exception:                                    # noqa: BLE001
            pass

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                try:
                    self._server.should_exit = True
                except Exception:                            # noqa: BLE001
                    pass
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=4)
            self._server, self._thread, self._port = None, None, None

    def status(self) -> dict:
        if not available():
            return {"ok": False, "state": "missing", "error": "内嵌模块未随包安装"}
        if self.is_running():
            return {"ok": True, "state": "running", "url": self.url, "port": self._port,
                    "data_dir": str(data_dir())}
        return {"ok": False, "state": "stopped", "error": self._error or "服务未启动"}


_HOST: JianzhouHost | None = None


def host() -> JianzhouHost:
    global _HOST
    if _HOST is None:
        _HOST = JianzhouHost()
    return _HOST


def shutdown() -> None:
    """软件退出时调用：停掉内嵌服务。"""
    global _HOST
    if _HOST is not None:
        _HOST.stop()
        _HOST = None
