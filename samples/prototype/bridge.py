"""网页 ↔ Python 桥接层（pywebview 的 js_api）。

这一层就是建议里「业务内核」的边界：网页只负责画，真实数据、任务状态、
设计令牌全部由 Python 给。本原型只做只读真实数据 + 演示任务进度推送，
不联网、不调用任何付费接口、不启动采集或下载。
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tokens  # noqa: E402  （由 design_tokens.json 生成，与网页同源）


def _app_version() -> str:
    source = ROOT / "justoneapi_collector" / "branding.py"
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "未读到"
    matched = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)', text)
    return matched.group(1) if matched else "未读到"


class Bridge:
    """暴露给 JS 的方法都会自动挂到 window.pywebview.api 上。"""

    def __init__(self) -> None:
        # ★ 必须以下划线开头：pywebview 会递归扫描 js_api 的公开属性来生成
        #   JS 代理；若把 Window 存成公开属性，它会一路扫到 WebView2 的 COM
        #   对象，在非 UI 线程爆出 "CoreWebView2 can only be accessed from
        #   the UI thread" 一大串无害但吓人的异常日志。
        self._window = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def bind(self, window) -> None:
        self._window = window

    # ── JS → Python：网页主动取真实数据 ──────────────────────────
    def get_snapshot(self) -> dict:
        return {
            "version": _app_version(),
            "qt_pages": len(list((ROOT / "gui_qt" / "pages").glob("*.py"))),
            "site_pages": len(list((ROOT / "installer_output" / "standalone").glob("*.html"))),
            "brand_light": tokens.color("--brand", "light"),
            "brand_dark": tokens.color("--brand", "dark"),
            "token_version": tokens.TOKENS["version"],
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": "只读真实数据：branding.py / gui_qt/pages / 官网产物 / design_tokens.json",
        }

    def get_tasks(self) -> list:
        return [
            {"name": "抖音 · 冬季护肤赛道 50 条视频", "percent": 68, "state": "下载中"},
            {"name": "文案仿写 · 口播稿 12 篇", "percent": 75, "state": "处理中"},
            {"name": "短剧成片 · 第 3 集合成", "percent": 31, "state": "渲染中"},
            {"name": "直播间录制 · 晚间场", "percent": 0, "state": "排队中"},
        ]

    def get_tokens(self, theme: str = "light") -> dict:
        return dict(tokens.TOKENS.get(theme, tokens.TOKENS["light"]))

    # ── Python → JS：任务进度实时推到网页 ─────────────────────────
    def start_demo_task(self, name: str = "演示任务") -> dict:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "已有任务在跑"}
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(name,), daemon=True)
        self._thread.start()
        return {"started": True, "name": name}

    def stop_demo_task(self) -> dict:
        self._stop.set()
        return {"stopped": True}

    def _push(self, script: str) -> None:
        if self._window is None:
            return
        try:
            self._window.evaluate_js(script)
        except Exception:
            pass  # 窗口已关闭时静默跳过

    def _run(self, name: str) -> None:
        for percent in range(0, 101, 5):
            if self._stop.is_set():
                payload = {"name": name, "percent": percent, "state": "已停止"}
                self._push("window.onTaskDone && window.onTaskDone(%s)" % json.dumps(payload, ensure_ascii=False))
                return
            payload = {"name": name, "percent": percent, "state": "已完成" if percent == 100 else "运行中"}
            self._push("window.onProgress && window.onProgress(%s)" % json.dumps(payload, ensure_ascii=False))
            time.sleep(0.12)
        self._push("window.onTaskDone && window.onTaskDone(%s)" % json.dumps(
            {"name": name, "percent": 100, "state": "已完成"}, ensure_ascii=False))
