"""启动「流量捕手工作台原型」真实桌面窗口。

- 界面：系统 WebView2（pywebview），不额外打包 Chromium
- 数据：js_api=Bridge，Python 业务侧只读真实数据 + 实时推进度
- 网络：不起本地服务、不占端口、不联网、不调用付费接口
- 用法：
    python 启动原型.py              打开窗口（可交互）
    python 启动原型.py --selftest   自动验证桥接三条链路后关窗，写 _bridge_result.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

HTML_FILE = HERE / "流量捕手_工作台原型.html"
CSS_FILE = HERE / "tokens.css"
RESULT_FILE = HERE / "_bridge_result.json"
LINK_TAG = '<link rel="stylesheet" href="tokens.css">'

import webview  # noqa: E402
from bridge import Bridge  # noqa: E402


def render_html() -> str:
    """pywebview 用 html= 直接喂内容（不起 HTTP 服务）。

    原型页默认已由 build_tokens.py 同步成自包含样式；若仍是外链版本，
    这里兜底把 tokens.css 内联进去，两种形态都能跑。
    """
    html = HTML_FILE.read_text(encoding="utf-8")
    if LINK_TAG in html:
        css = CSS_FILE.read_text(encoding="utf-8")
        return html.replace(LINK_TAG, "<style>\n" + css + "\n</style>")
    return html


def wait_dom(window, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if window.evaluate_js("document.readyState") == "complete":
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def read_js(window, script: str):
    try:
        return window.evaluate_js(script)
    except Exception:
        return None


def self_test(window, bridge: Bridge) -> dict:
    """自动验证：DOM 就绪 → JS 调 Python → Python 推 JS → 状态落回 DOM。"""
    result = {
        "mode": "pywebview + WebView2 真实窗口",
        "dom_ready": wait_dom(window),
        "js_to_python": {},
        "python_to_js": {},
        "checks": [],
    }

    # 1) JS → Python：页面在 pywebviewready 里调用 api.get_snapshot()
    snapshot = None
    deadline = time.time() + 12
    while time.time() < deadline:
        snapshot = read_js(window, "window.__apiSnapshot || null")
        if snapshot:
            break
        time.sleep(0.25)
    local = bridge.get_snapshot()
    result["js_to_python"] = {
        "js_received": bool(snapshot),
        "version": (snapshot or {}).get("version"),
        "qt_pages": (snapshot or {}).get("qt_pages"),
        "site_pages": (snapshot or {}).get("site_pages"),
        "brand_dark": (snapshot or {}).get("brand_dark"),
        "python_version": local["version"],
    }
    result["checks"].append(("JS 拿到 Python 真实数据", bool(snapshot) and
                             (snapshot or {}).get("version") == local["version"]))
    result["checks"].append(("版本号来自 branding.py", local["version"] == "1.28.2"))

    # 2) Python → JS：启动演示任务，进度由 Python 线程 evaluate_js 推送
    bridge.start_demo_task("桥接自检")
    time.sleep(0.9)
    width = read_js(window, "document.getElementById('live-bar').style.width")
    log_text = read_js(window, "document.getElementById('bridge-log').textContent") or ""
    state = read_js(window, "document.getElementById('live-state').textContent") or ""
    result["python_to_js"] = {
        "progress_width": width,
        "log_has_running": "进度" in log_text or "运行中" in log_text,
        "state": state,
    }
    result["checks"].append(("Python 进度推到网页 DOM", bool(width) and "%" in str(width)))
    result["checks"].append(("网页日志记录推送事件", "进度" in log_text or "运行中" in log_text))

    # 3) 任务终态
    deadline = time.time() + 8
    final_state = ""
    while time.time() < deadline:
        final_state = read_js(window, "document.getElementById('live-state').textContent") or ""
        if "已完成" in final_state:
            break
        time.sleep(0.3)
    result["python_to_js"]["final_state"] = final_state
    result["checks"].append(("任务终态回传网页", "已完成" in final_state))

    # 4) 令牌同源：网页 --brand 与 Python tokens.color 一致
    web_brand = read_js(window, "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()")
    result["token_source"] = {"web": web_brand, "python": local["brand_light"]}
    result["checks"].append(("网页与 Python 令牌同源",
                             str(web_brand).lower() == local["brand_light"].lower()))

    result["ok"] = all(passed for _, passed in result["checks"])
    return result


def main() -> int:
    selftest = "--selftest" in sys.argv
    bridge = Bridge()
    window = webview.create_window(
        "流量捕手 · 工作台原型（TDesign 令牌 + WebView2）",
        html=render_html(),
        js_api=bridge,
        width=1360,
        height=900,
        min_size=(980, 680),
        background_color="#F3F4F6",
    )
    bridge.bind(window)

    def on_start(win, api):
        if not selftest:
            return
        try:
            result = self_test(win, api)
        except Exception as exc:  # 自检脚本本身出错也要留下证据
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SELFTEST " + ("OK" if result.get("ok") else "FAIL"))
        print(json.dumps(result, ensure_ascii=False)[:600])
        try:
            win.destroy()
        except Exception:
            pass

    webview.start(on_start, args=(window, bridge), gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
