"""启动「流量捕手」WebView2 桌面窗口原型。

- 界面：系统 WebView2（pywebview），不额外打包 Chromium
- 数据：js_api=Bridge，Python 业务侧只读真实数据 + 实时推进度
- 网络：不起本地服务、不占端口、不联网、不调用付费接口
- 用法：
    python 启动原型.py                                 工作台窗口（可交互）
    python 启动原型.py --page download                视频下载窗口（可交互）
    python 启动原型.py --page transcribe              转录任务可视化窗口（可交互）
    python 启动原型.py --selftest                     工作台桥接自检后关窗
    python 启动原型.py --page download --selftest     下载页全链路自检后关窗
    python 启动原型.py --page transcribe --selftest  转录页全链路自检后关窗

自检结果分别写入 _bridge_result.json / _bridge_result_download.json /
_bridge_result_transcribe.json。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

CSS_FILE = HERE / "tokens.css"
LINK_TAG = '<link rel="stylesheet" href="tokens.css">'
PAGES = {
    "workbench": {
        "html": HERE / "流量捕手_工作台原型.html",
        "result": HERE / "_bridge_result.json",
        "title": "流量捕手 · 工作台原型（TDesign 令牌 + WebView2）",
    },
    "download": {
        "html": HERE / "流量捕手_视频下载原型.html",
        "result": HERE / "_bridge_result_download.json",
        "title": "流量捕手 · 视频下载原型（真实契约 + WebView2 桥接）",
    },
    "transcribe": {
        "html": HERE / "流量捕手_转录任务可视化原型.html",
        "result": HERE / "_bridge_result_transcribe.json",
        "title": "流量捕手 · 转录任务可视化原型（真实状态 + WebView2 桥接）",
    },
}

import webview  # noqa: E402
from bridge import Bridge  # noqa: E402


def render_html(page: dict) -> str:
    """pywebview 用 html= 直接喂内容（不起 HTTP 服务）。

    原型页默认已由 build_tokens.py 同步成自包含样式；若仍是外链版本，
    这里兜底把 tokens.css 内联进去，两种形态都能跑。
    """
    html = page["html"].read_text(encoding="utf-8")
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


def base_result(mode: str, window, bridge: Bridge) -> dict:
    return {
        "mode": mode,
        "dom_ready": wait_dom(window),
        "js_to_python": {},
        "python_to_js": {},
        "checks": [],
    }


def self_test_workbench(window, bridge: Bridge) -> dict:
    """工作台：DOM 就绪 → JS 调 Python → Python 推 JS → 状态落回 DOM。"""
    result = base_result("pywebview + WebView2 真实窗口 · 工作台", window, bridge)
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

    bridge.start_demo_task("桥接自检")
    time.sleep(0.9)
    width = read_js(window, "document.getElementById('live-bar').style.width")
    log_text = read_js(window, "document.getElementById('bridge-log').textContent") or ""
    result["python_to_js"] = {
        "progress_width": width,
        "log_has_running": "进度" in log_text or "运行中" in log_text,
    }
    result["checks"].append(("Python 进度推到网页 DOM", bool(width) and "%" in str(width)))
    result["checks"].append(("网页日志记录推送事件", "进度" in log_text or "运行中" in log_text))

    deadline = time.time() + 8
    final_state = ""
    while time.time() < deadline:
        final_state = read_js(window, "document.getElementById('live-state').textContent") or ""
        if "已完成" in final_state:
            break
        time.sleep(0.3)
    result["python_to_js"]["final_state"] = final_state
    result["checks"].append(("任务终态回传网页", "已完成" in final_state))

    web_brand = read_js(window, "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()")
    result["token_source"] = {"web": web_brand, "python": local["brand_light"]}
    result["checks"].append(("网页与 Python 令牌同源",
                             str(web_brand).lower() == local["brand_light"].lower()))
    result["ok"] = all(passed for _, passed in result["checks"])
    return result


def self_test_download(window, bridge: Bridge) -> dict:
    """下载页：契约/输出目录/表格 → 进度回调 → 状态与统计 → 令牌同源。"""
    result = base_result("pywebview + WebView2 真实窗口 · 视频下载", window, bridge)
    deadline = time.time() + 12
    while time.time() < deadline:
        if read_js(window, "!!(window.__apiRows && window.__apiOutput && window.__contractFromApi)"):
            break
        time.sleep(0.25)

    # ① JS → Python：真实数据契约（从 Qt 源码抽取，不是手写的）
    web_contract = read_js(window, "window.__apiContract || null")
    py_contract = bridge.get_download_contract()
    result["js_to_python"] = {
        "from_api": read_js(window, "!!window.__contractFromApi"),
        "columns": (web_contract or {}).get("columns"),
        "platforms": [p["label"] for p in (web_contract or {}).get("platforms", [])],
        "formula": ((web_contract or {}).get("progress") or {}).get("formula"),
    }
    result["checks"].append((
        "网页拿到 Python 的真实数据契约",
        bool(web_contract) and (web_contract or {}).get("columns") == py_contract["columns"]
        and bool(read_js(window, "!!window.__contractFromApi")),
    ))
    result["checks"].append((
        "进度口径来自 Qt 源码",
        ((web_contract or {}).get("progress") or {}).get("formula") == py_contract["progress"]["formula"],
    ))

    # ② JS → Python：本机真实输出目录（只读）
    web_out = read_js(window, "window.__apiOutput || null")
    py_out = bridge.get_output_stats()
    result["js_to_python"]["output_dir"] = (web_out or {}).get("dir")
    result["js_to_python"]["output_files"] = (web_out or {}).get("files")
    result["checks"].append((
        "网页读到本机 config.json 的下载目录",
        bool(web_out) and (web_out or {}).get("dir") == py_out["dir"] and bool(py_out["dir"]),
    ))

    # ③ 表格数据：行数与 Python 一致，且界面只出现中文平台名
    rows = read_js(window, "window.__apiRows || []") or []
    dom_rows = read_js(window, "document.querySelectorAll('#tbody tr').length") or 0
    body_text = read_js(window, "document.getElementById('tbody').textContent") or ""
    result["js_to_python"]["rows"] = len(rows)
    result["checks"].append(("表格行数与 Python 返回一致", len(rows) == dom_rows == 5))
    result["checks"].append((
        "界面显示平台中文名、不暴露英文内部键",
        "视频号" in body_text and "weixin_channels" not in body_text,
    ))

    # ④ Python → JS：点网页按钮启动批次，Python 线程按真实口径推进度
    read_js(window, "document.getElementById('runBtn').click()")
    mid = None
    deadline = time.time() + 8
    while time.time() < deadline:
        mid = read_js(window, "window.__lastProgress || null")
        if mid and (mid.get("percent") or 0) > 0:
            break
        time.sleep(0.2)
    width = read_js(window, "document.getElementById('live-bar').style.width")
    log_text = ""
    deadline = time.time() + 4
    while time.time() < deadline:
        log_text = read_js(window, "document.getElementById('bridge-log').textContent") or ""
        if "进度" in log_text:
            break
        time.sleep(0.2)
    result["python_to_js"] = {
        "mid_percent": (mid or {}).get("percent"),
        "progress_width": width,
        "log_has_progress": "进度" in log_text,
    }
    result["checks"].append(("Python 进度推到网页 DOM", bool(width) and "%" in str(width)))
    result["checks"].append(("网页日志记录进度回调", "进度" in log_text))
    result["checks"].append((
        "进度按真实口径推送（含已完成条数 / 字节数）",
        bool(mid) and "total" in mid and "d_sum" in mid and "rows" in mid,
    ))

    # ⑤ 终态：失败条、统计卡、完成文案全部落回 DOM
    done = None
    deadline = time.time() + 15
    while time.time() < deadline:
        done = read_js(window, "window.__batchDone || null")
        if done:
            break
        time.sleep(0.25)
    m1 = read_js(window, "document.getElementById('m1').textContent") or ""
    m2 = read_js(window, "document.getElementById('m2').textContent") or ""
    final_msg = read_js(window, "document.getElementById('live-msg').textContent") or ""
    fail_badges = read_js(window, "document.querySelectorAll('.state-fail').length") or 0
    result["python_to_js"].update({
        "final_percent": (done or {}).get("percent"),
        "done": (done or {}).get("done"),
        "failed": (done or {}).get("failed"),
        "success_card": m1.strip(), "fail_card": m2.strip(),
        "final_message": final_msg, "fail_badges": fail_badges,
    })
    result["checks"].append(("批次终态 100% 回传网页", bool(done) and (done or {}).get("percent") == 100.0))
    result["checks"].append(("统计卡「已下载」随进度更新", "4" in m1))
    result["checks"].append(("失败态徽标与统计卡同步", "1" in m2 and fail_badges == 1))
    result["checks"].append(("完成文案用源码原话", "下载完成：成功 4 条，失败 1 条" in final_msg))

    # ⑥ 令牌同源
    local = bridge.get_snapshot()
    web_brand = read_js(window, "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()")
    result["token_source"] = {"web": web_brand, "python": local["brand_light"]}
    result["checks"].append(("网页与 Python 令牌同源",
                             str(web_brand).lower() == local["brand_light"].lower()))

    # ⑦ 产物级静态断言：防止页面被改回手写数据
    html = PAGES["download"]["html"].read_text(encoding="utf-8")
    result["static"] = {
        "inline_contract": '<script type="application/json" id="inline-contract">' in html,
        "tokens_block": '<style id="tokens">' in html,
        "no_external_css": LINK_TAG not in html,
        "hooks": all(h in html for h in ("onBatchStart", "onDownloadProgress", "onBatchDone")),
        "columns_from_source": all(c in html for c in py_contract["columns"]),
    }
    result["checks"].append(("页面自包含且数据来自唯一真源", all(result["static"].values())))

    # ⑧ 新页面也吃同一份令牌：点换肤后主色应变为深色品牌色
    dark_brand = read_js(
        window,
        "document.getElementById('themeBtn').click();"
        "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()",
    )
    py_dark_brand = local["brand_dark"]
    result["token_source"]["web_dark"] = dark_brand
    result["token_source"]["python_dark"] = py_dark_brand
    result["checks"].append((
        "下载页换肤同样来自令牌真源",
        str(dark_brand).lower() == py_dark_brand.lower(),
    ))

    result["ok"] = all(passed for _, passed in result["checks"])
    return result


def self_test_transcribe(window, bridge: Bridge) -> dict:
    """转录页：状态契约 → 真实目录扫描 → 四态徽标 → 进度推送 → 令牌同源。"""
    result = base_result("pywebview + WebView2 真实窗口 · 转录任务可视化", window, bridge)
    deadline = time.time() + 20
    while time.time() < deadline:
        if read_js(window, "!!(window.__apiContract && window.__overviewFromApi)"):
            break
        time.sleep(0.25)

    # ① 状态契约与输出命名规则必须来自 transcribe.py 源码
    web_c = read_js(window, "window.__apiContract || null")
    py_c = bridge.get_transcribe_contract()
    result["js_to_python"] = {
        "from_api": read_js(window, "!!window.__contractFromApi"),
        "columns": (web_c or {}).get("columns"),
        "states": [s["label"] for s in (web_c or {}).get("states", [])],
        "extensions": len((web_c or {}).get("extensions", [])),
    }
    result["checks"].append((
        "网页拿到 Python 的状态契约",
        bool(web_c) and (web_c or {}).get("columns") == py_c["columns"]
        and bool(read_js(window, "!!window.__contractFromApi")),
    ))
    code_text = read_js(window, "document.getElementById('code-rules').textContent") or ""
    result["checks"].append((
        "输出命名规则逐行来自 transcribe.py",
        all(line in code_text for line in py_c["output_code"]),
    ))

    # ② 本机真实目录扫描（只读）
    web_o = read_js(window, "window.__apiOverview || null")
    py_o = bridge.get_transcribe_overview()
    result["js_to_python"]["root"] = (web_o or {}).get("root")
    result["js_to_python"]["total"] = (web_o or {}).get("total")
    result["checks"].append((
        "网页读到本机真实目录与文件总数",
        bool(web_o) and (web_o or {}).get("root") == py_o["root"]
        and (web_o or {}).get("total", 0) > 0,
    ))
    m1 = read_js(window, "document.getElementById('m1').textContent") or ""
    m3 = read_js(window, "document.getElementById('m3').textContent") or ""
    result["checks"].append((
        "统计卡数字与真实扫描一致",
        str(py_o["total"]) in m1 and str(py_o["stats"]["pending"]) in m3,
    ))
    dom_rows = read_js(window, "document.querySelectorAll('#tbody tr').length") or 0
    result["checks"].append((
        "文件表格行数与 Python 返回一致",
        dom_rows == len((web_o or {}).get("rows", [])),
    ))

    # ③ 显卡实时卡片（nvidia-smi 只读）
    gpu_title = read_js(window, "document.getElementById('gpu-title').textContent") or ""
    gpu_util = read_js(window, "document.getElementById('gpu-util').textContent") or ""
    result["js_to_python"]["gpu"] = gpu_title
    result["checks"].append((
        "显卡卡片读到 nvidia-smi 真实读数",
        "—" not in gpu_title and "—" not in gpu_util and "%" in gpu_util,
    ))

    # ④ 切到演示状态：四种状态徽标都要出现，且必须显式标注「演示」
    read_js(window, "document.getElementById('modeBtn').click();true")
    deadline = time.time() + 12
    while time.time() < deadline:
        if read_js(window, "!!(window.__apiOverview && window.__apiOverview.demo)"):
            break
        time.sleep(0.25)
    badges = {
        key: read_js(window, f"document.querySelectorAll('.state-{key}').length") or 0
        for key in ("done", "pending", "stale", "conflict", "running")
    }
    demo_label = read_js(window, "document.getElementById('h2').textContent") or ""
    result["python_to_js"] = {"demo_badges": badges}
    result["checks"].append((
        "演示状态覆盖 5 类徽标",
        all(v >= 1 for v in badges.values()),
    ))
    result["checks"].append((
        "演示数据显式标注、不冒充真实文件",
        "演示" in demo_label,
    ))

    # ⑤ Python → JS：阶段 / 进度 / 终态
    read_js(window, "document.getElementById('runDemo').click();true")
    mid = None
    deadline = time.time() + 10
    while time.time() < deadline:
        mid = read_js(window, "window.__tcProgress || null")
        if mid and (mid.get("percent") or 0) > 0:
            break
        time.sleep(0.2)
    width = read_js(window, "document.getElementById('live-bar').style.width") or ""
    log_text = read_js(window, "document.getElementById('bridge-log').textContent") or ""
    result["python_to_js"].update({
        "mid_percent": (mid or {}).get("percent"),
        "phase": (mid or {}).get("phase"),
        "progress_width": width,
        "log_has_progress": "进度回调" in log_text,
    })
    result["checks"].append((
        "Python 进度与阶段推到网页 DOM",
        bool(width) and "%" in width and bool((mid or {}).get("phase")),
    ))
    result["checks"].append(("网页日志记录进度回调", "进度回调" in log_text))
    done = None
    deadline = time.time() + 12
    while time.time() < deadline:
        done = read_js(window, "window.__tcDone || null")
        if done:
            break
        time.sleep(0.25)
    result["python_to_js"]["final"] = done
    result["checks"].append((
        "任务终态 100% 回传网页",
        bool(done) and done.get("percent") == 100 and done.get("phase") == "已完成",
    ))

    # ⑥ 输出目录重新判定：结果里的 out_dir 必须等于用户填的值
    out_value = py_o["root"].replace("\\", "/")
    read_js(window,
            "document.getElementById('outDir').value=" + json.dumps(out_value) + ";"
            "document.getElementById('rescan').click();true")
    deadline = time.time() + 10
    new_out = ""
    while time.time() < deadline:
        new_out = read_js(window, "(window.__apiOverview||{}).out_dir || ''") or ""
        if new_out and new_out.replace("\\", "/") == out_value:
            break
        time.sleep(0.25)
    result["checks"].append((
        "按输出目录重新判定生效",
        new_out.replace("\\", "/") == out_value,
    ))

    # ⑦ 令牌同源 + 换肤
    local = bridge.get_snapshot()
    web_brand = read_js(
        window,
        "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()")
    dark_brand = read_js(
        window,
        "document.getElementById('themeBtn').click();"
        "getComputedStyle(document.documentElement).getPropertyValue('--brand').trim()")
    result["token_source"] = {
        "web": web_brand, "python": local["brand_light"],
        "web_dark": dark_brand, "python_dark": local["brand_dark"],
    }
    result["checks"].append((
        "新页与 Python 令牌同源、换肤生效",
        str(web_brand).lower() == local["brand_light"].lower()
        and str(dark_brand).lower() == local["brand_dark"].lower(),
    ))

    # ⑧ 产物级静态断言
    html = PAGES["transcribe"]["html"].read_text(encoding="utf-8")
    result["static"] = {
        "inline_contract": 'id="inline-transcribe-contract">' in html,
        "inline_overview": 'id="inline-transcribe-overview">' in html,
        "tokens_block": '<style id="tokens">' in html,
        "no_external_css": LINK_TAG not in html,
        "hooks": all(h in html for h in ("onTranscribeProgress", "onTranscribeDone",
                                         "get_transcribe_overview")),
        "state_badges": all(f".state-{k}" in html for k in
                            ("done", "pending", "stale", "conflict", "running")),
    }
    result["checks"].append(("页面自包含且状态来自唯一真源", all(result["static"].values())))
    result["ok"] = all(passed for _, passed in result["checks"])
    return result


def main() -> int:
    selftest = "--selftest" in sys.argv
    tail = sys.argv[sys.argv.index("--page") + 1:] if "--page" in sys.argv else []
    page_key = "workbench"
    if any("download" in a or "下载" in a for a in tail):
        page_key = "download"
    elif any("transcribe" in a or "转录" in a for a in tail):
        page_key = "transcribe"
    page = PAGES[page_key]
    bridge = Bridge()
    window = webview.create_window(
        page["title"],
        html=render_html(page),
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
            fn = {
                "download": self_test_download,
                "transcribe": self_test_transcribe,
            }.get(page_key, self_test_workbench)
            result = fn(win, api)
        except Exception as exc:  # 自检脚本本身出错也要留下证据
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result["page"] = page_key
        page["result"].write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SELFTEST " + ("OK" if result.get("ok") else "FAIL"))
        print(json.dumps(result, ensure_ascii=False)[:900])
        try:
            win.destroy()
        except Exception:
            pass

    webview.start(on_start, args=(window, bridge), gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
