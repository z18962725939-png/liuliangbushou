# -*- coding: utf-8 -*-
"""见舟深度内嵌门禁（2026-11-20）。

钉六件事：
  ① vendor 源码完整（后端 45 模块 + 免构建前端），且能被发版 Python 3.10 解析；
  ② 宿主：同进程启动、数据目录隔离、端口避让、只绑回环、可幂等停止；
  ③ 前端静态资源真的能被 HTTP 取到（不只是 200，是 HTML + JS 内容）；
  ④ 页面：QWebEngineView 内嵌、主题跟随、主动作付费闸门、无裸 connect；
  ⑤ 数据链路：历史库 → 见舟线索 → push 到内容雷达；
  ⑥ 打包配置：datas / hiddenimports / spec 登记齐全。

跑法：C:/Python310/python.exe tools/_validate_jianzhou_embed.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import jianzhou_host as hostmod  # noqa: E402
from justoneapi_collector import jianzhou_bridge as jz  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def read(rel: str) -> str:
    return (Path(ROOT) / rel).read_text(encoding="utf-8")


print("=" * 62)
print("见舟深度内嵌门禁")
print("=" * 62)

print("\n1) vendor 源码完整 + 3.10 语法兼容")
v = hostmod.vendor_dir()
check("vendor 目录存在", v.is_dir(), str(v))
check("见舟主模块在", (v / "app" / "main.py").is_file())
check("免构建前端在", (v / "frontend" / "index.html").is_file())
check("前端 JS 在", (v / "frontend" / "assets" / "js" / "app.js").is_file())
py_files = list((v / "app").rglob("*.py"))
check("后端 py 模块数量 ≥ 40", len(py_files) >= 40, str(len(py_files)))
bad = []
for p in py_files:
    try:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except SyntaxError as exc:
        bad.append(f"{p.name}: {exc}")
check("全部能被发版 Python 3.10 解析", not bad, "; ".join(bad[:3]))
check("未误带上游 data/ 与 .git", not (v / "data").exists() and not (v / ".git").exists())

print("\n2) 宿主生命周期（同进程 / 回环 / 隔离 / 幂等）")
check("availability 判定为真", hostmod.available())
check("只绑 127.0.0.1", "host=\"127.0.0.1\"" in read("justoneapi_collector/jianzhou_host.py"))
check("数据目录在流量捕手 AppData 下", "TrafficCatcher" in str(hostmod.data_dir())
      and "见舟工作台数据" in str(hostmod.data_dir()))
check("启动前覆盖前端目录", "FRONTEND_DIR = _VENDOR" in read("justoneapi_collector/jianzhou_host.py"))
check("禁系统代理读健康", "ProxyHandler({})" in read("justoneapi_collector/jianzhou_host.py"))
check("软件退出能停服务", "def shutdown()" in read("justoneapi_collector/jianzhou_host.py"))
# ★ 2026-11-20 发版实测补的：shutdown() 定义了还不够，必须真的接到退出时机。
#   漏接时 daemon 线程 + QWebEngineView 在解释器收尾互抢 → 0xC0000409 崩溃。
_app_src = read("gui_qt/app.py")
check("主程序退出接线 aboutToQuit", "aboutToQuit.connect" in _app_src
      and "jianzhou_host.shutdown()" in _app_src,
      "真实软件关窗时必须停内嵌服务，否则线程悬空/端口占用/退出崩溃")
_smoke_src = read("gui_qt/_smoke.py")
check("冒烟跳过见舟 boot（防假失败）",
      "JianzhouPage._boot = lambda self: None" in _smoke_src,
      "无头冒烟只验界面构建，起服务会随机 0xC0000409 误拦发版")
# ★ 治本：所有无头门禁（不止 _smoke）都会构造主窗口，必须在 _boot 内
#   短路 QT_QPA_PLATFORM=offscreen，否则 daemon 线程 + WebEngine 退出互抢崩溃。
_page_src = read("gui_qt/pages/jianzhou_page.py")
check("_boot 内无头短路（全体门禁免疫）",
      'QT_QPA_PLATFORM' in _page_src and 'offscreen' in _page_src,
      "见舟页在无头环境不得真起服务，否则构造主窗口的门禁随机崩溃误拦发版")

h = hostmod.host()
url = h.start()
check("服务启动返回 URL", url.startswith("http://127.0.0.1:"), url)
port = h.port or 0
check("端口落在 8741 起避让区间", 8741 <= port < 8741 + hostmod.PORT_SCAN_LIMIT, str(port))
check("status 报 running", h.status().get("state") == "running", str(h.status()))
url2 = h.start()
check("重复 start 幂等（同一 URL）", url2 == url, f"{url2} vs {url}")
h.stop()
check("stop 后不再 running", not h.is_running())
check("stop 后 URL 清空", h.url == "")

print("\n3) 前端静态资源真的可取（重启一次）")
url3 = h.start()
check("停止后可再次启动", bool(url3))
if url3:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url3.rstrip("/") + "/healthz", timeout=5) as r:
            health = json.loads(r.read().decode("utf-8"))
        check("healthz 报 ok", health.get("ok") is True)
        check("能力清单含 channel_operations",
              bool((health.get("capabilities") or {}).get("channel_operations")))
        with opener.open(url3.rstrip("/") + "/", timeout=5) as r:
            html = r.read().decode("utf-8", errors="replace")
        check("首页是真 HTML", html.lstrip().startswith("<!DOCTYPE html>"), html[:40])
        check("首页标题是见舟", "见舟" in html)
        with opener.open(url3.rstrip("/") + "/assets/js/app.js", timeout=5) as r:
            js = r.read().decode("utf-8", errors="replace")
        check("app.js 非空且像代码", len(js) > 1000 and "function" in js, str(len(js)))
    except Exception as exc:                                 # noqa: BLE001
        check("HTTP 冒烟无异常", False, str(exc))
    h.stop()

print("\n4) 内嵌页面（QWebEngine / 主题 / 接线）")
src = read("gui_qt/pages/jianzhou_page.py")
check("用 QWebEngineView 内嵌", "QWebEngineView" in src)
check("明确不是外部浏览器跳转主体", "QDesktopServices.openUrl" in src
      and "self.view.setUrl" in src)
check("主题跟随（apply_theme 约定名）", "def apply_theme(self, mode" in src)
check("JS 切 data-theme", "data-theme" in src)
check("主动作有付费闸门", src.count("require_feature(self, \"jianzhou_bridge\"") >= 3)
check("status 用 setText 而非 set", "self.status.setText(" in src and ".status.set(" not in src)
check("没有致命 on_error=self._status.set", "on_error=self._status.set" not in src)
check("Worker 全走 connect_guarded", src.count("connect_guarded(") >= 3)
check("不用 setStretchLastSection", "setStretchLastSection" not in src)
main = read("gui_qt/main_window.py")
check("导航登记", '"见舟工作台"' in main)
check("_PAGE_CLASS 登记", '"见舟工作台": JianzhouPage' in main)
check("教程登记为已内嵌", "整套装进本软件" in read("gui_qt/widgets/guide.py"))

print("\n5) 数据链路（历史库 → 见舟线索 → 推送）")
items = jz.from_history(limit=5)
check("历史库为空时不伪造数据（返回 list）", isinstance(items, list), str(type(items)))
rows, errs = jz.parse_rows(jz.to_rows_text([
    {"platform": "抖音", "title": "车间实拍", "url": "https://x.test/1", "note": "点赞 12"},
]))
check("to_rows_text → parse_rows 往返无损", len(rows) == 1 and rows[0]["title"] == "车间实拍"
      and rows[0]["note"] == "点赞 12", f"{rows} / {errs}")
check("导入按钮已接", "_import_history" in src and "from_history" in src)

print("\n6) 打包配置")
spec = read("justoneapi_collector.spec")
check("datas 收 app 后端", "vendor/jianzhou/app" in spec)
check("datas 收前端", "vendor/jianzhou/frontend" in spec)
check("hiddenimports 有宿主", "justoneapi_collector.jianzhou_host" in spec)
check("hiddenimports 有 QtWebEngine", "PySide6.QtWebEngineWidgets" in spec)
check("hiddenimports 有 fastapi", '"fastapi"' in spec)
check("hiddenimports 有 uvicorn", '"uvicorn"' in spec)
check("hiddenimports 有 pydantic", '"pydantic"' in spec)
check("release_all 挂了内嵌门禁", "_validate_jianzhou_embed.py" in read("tools/release_all.py"))

print("\n" + "=" * 62)
print(f"通过 {PASS} 项，失败 {FAIL} 项")
if FAIL:
    print("见舟深度内嵌退化：先修好再发版。")
    raise SystemExit(1)
print("全部通过：见舟可随包内嵌交付。")
