# -*- coding: utf-8 -*-
"""见舟工作台桥接门禁（2026-11-20）。

钉三件事：
  ① 只连本机、禁系统代理、失败不抛到界面（安全边界）；
  ② 批量解析 / 健康检查 / 内容雷达推送对真实 HTTP 结构正确；
  ③ 新增页接入清单齐全（漏一项打包后就静默失效）。

跑法：C:/Python310/python.exe tools/_validate_jianzhou_bridge.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import jianzhou_bridge as jz  # noqa: E402

PASS = 0
FAIL = 0
RECEIVED: list[dict] = []


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


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                                 # noqa: N802
        if self.path == "/healthz":
            self._send({"success": True, "data": {
                "ok": True, "demo_mode": True, "schema_version": 10,
                "build_id": "gate-test",
                "capabilities": {"visual_factory": True, "channel_operations": True,
                                 "advisor": False},
            }})
        else:
            self._send({"success": False, "error": "not found"}, 404)

    def do_POST(self) -> None:                                # noqa: N802
        if self.path == "/radar/signals/manual":
            size = int(self.headers.get("Content-Length") or 0)
            RECEIVED.append(json.loads(self.rfile.read(size).decode("utf-8")))
            self._send({"success": True, "data": {
                "id": "SG-MAN-GATE", "existing": False, "note": "已保存到雷达并永久沉淀。",
            }})
        else:
            self._send({"success": False, "error": "not found"}, 404)

    def log_message(self, *_args) -> None:
        pass


print("=" * 62)
print("见舟工作台桥接门禁")
print("=" * 62)

print("\n1) 安全边界（只连本机 / 禁代理 / 失败关闭）")
for bad in ("https://example.com", "http://192.168.1.8:8741", "http://user:pass@127.0.0.1:8741"):
    try:
        jz.normalize_base(bad)
        check(f"拒绝非本机地址 {bad}", False)
    except ValueError:
        check(f"拒绝非本机地址 {bad}", True)
for bad2 in ("http://127.0.0.1:8741/radar", "http://127.0.0.1:8741/?x=1", "http://127.0.0.1:99999"):
    try:
        jz.normalize_base(bad2)
        check(f"拒绝带路径/参数/坏端口 {bad2}", False)
    except ValueError:
        check(f"拒绝带路径/参数/坏端口 {bad2}", True)
check("默认地址是本机 8741", jz.normalize_base("") == "http://127.0.0.1:8741")
check("允许 localhost:8742 并补齐协议", jz.normalize_base("localhost:8742") == "http://localhost:8742")
check("源码显式禁用环境代理", "ProxyHandler({})" in read("justoneapi_collector/jianzhou_bridge.py"))
check("源码不硬编码公网采集端点", "justoneapi" not in read("justoneapi_collector/jianzhou_bridge.py").lower())
unreachable = jz.probe("http://127.0.0.1:1", timeout=0.5)
check("连不上时返回结构化失败而非抛异常", unreachable.get("ok") is False and bool(unreachable.get("error")))
check("连不上的提示是可操作的中文", "见舟" in str(unreachable.get("error") or ""))

print("\n2) 批量解析（平台|标题|链接|备注）")
rows, errs = jz.parse_rows(
    "# 这是注释\n"
    "抖音|工厂车间实拍|https://www.douyin.com/video/x|客户问交期\n"
    "小红书|竞品封面拆解\n"
    "https://www.xiaohongshu.com/explore/y\n"
    "抖音||\n"
)
check("解析出 3 条有效线索", len(rows) == 3, f"{len(rows)} / {errs}")
check("四段格式字段正确", rows[0]["platform"] == "抖音" and rows[0]["url"].endswith("/x")
      and rows[0]["note"] == "客户问交期")
check("三段格式链接可空", rows[1]["platform"] == "小红书" and rows[1]["url"] == "")
check("纯网址自动当标题", rows[2]["title"].startswith("https://"))
check("缺标题整行报错不静默丢弃", any("缺少标题" in e for e in errs), str(errs))
big, big_err = jz.parse_rows("\n".join(f"抖音|标题{i}" for i in range(jz.MAX_ROWS + 5)))
check(f"每次最多 {jz.MAX_ROWS} 条", len(big) == jz.MAX_ROWS and bool(big_err))
check("空输入不给假数据", jz.parse_rows("")[0] == [])

print("\n3) 真实 HTTP 对接（本地假见舟服务）")
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
try:
    st = jz.probe(base, timeout=2.0)
    check("healthz 探测成功", st.get("ok") is True)
    check("演示模式解析正确", st.get("demo_mode") is True)
    check("数据库版本解析正确", st.get("schema_version") == 10)
    check("能力清单只列已开启项", st.get("capabilities") == ["visual_factory", "channel_operations"])
    check("状态摘要可直接展示", "已连接" in str(st.get("summary") or ""))

    one = jz.push_signal(base, {"platform": "抖音", "title": "车间实拍",
                                "url": "https://www.douyin.com/video/x", "note": "客户要交期"})
    check("推送成功", one.get("ok") is True, str(one))
    check("返回见舟信号 ID", one.get("id") == "SG-MAN-GATE")
    sent = RECEIVED[-1] if RECEIVED else {}
    check("写入的是 manual 类信号", sent.get("category") == "manual", str(sent))
    check("标题 / 平台 / 链接 / 备注都带到", sent.get("title") == "车间实拍"
          and sent.get("platform") == "抖音" and sent.get("url").endswith("/x")
          and sent.get("note") == "客户要交期")
    check("空标题本地就拦下", jz.push_signal(base, {"title": "   "}).get("ok") is False)
    check("超长标题被截断而非撑爆接口", len(jz.push_signal(
        base, {"title": "字" * 900}).get("title") or "") <= jz.MAX_TITLE)
finally:
    server.shutdown()
    server.server_close()

print("\n4) 新增页接入清单（漏一项打包后静默失效）")
main = read("gui_qt/main_window.py")
check("导航里有「见舟工作台」", '"见舟工作台"' in main)
check("_PAGE_CLASS 已登记", '"见舟工作台": JianzhouPage' in main)
check("空态引导已登记", '"见舟工作台": [' in main)
check("页面 import 已接", "from gui_qt.pages.jianzhou_page import JianzhouPage" in main)
guide = read("gui_qt/widgets/guide.py")
check("教程顺序已登记", '"见舟工作台"' in guide)
check("教程内容已登记", '"见舟工作台": {' in guide)
palette = read("gui_qt/widgets/command_palette.py")
check("命令面板别名 ≥3 个", all(x in palette for x in ("见舟", "jianzhou", "内容雷达")))
lic = read("justoneapi_collector/licensing.py")
check("功能位已注册", '"jianzhou_bridge": "见舟工作台桥接"' in lic)
check("老码兼容策略是 auto", '"jianzhou_bridge": "auto"' in lic)
spec = read("justoneapi_collector.spec")
check("打包登记页面 + 核心模块", "gui_qt.pages.jianzhou_page" in spec
      and "justoneapi_collector.jianzhou_bridge" in spec)
check("UX 门禁 EXPECTED 已登记", '"见舟工作台"' in read("tools/_validate_ux.py"))
check("功能位同步门禁已登记", '"见舟工作台": ("jianzhou_bridge", "jianzhou_page")'
      in read("tools/_validate_feature_sync.py"))

src = read("gui_qt/pages/jianzhou_page.py")
tree = ast.parse(src)
first_guarded: list[str] = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("_boot", "_probe", "_push"):
        body = node.body
        if body and isinstance(body[0], ast.If):
            call = body[0].test
            if isinstance(call, ast.UnaryOp) and isinstance(call.op, ast.Not) and isinstance(call.operand, ast.Call):
                fn = call.operand.func
                if ((isinstance(fn, ast.Attribute) and fn.attr == "require_feature")
                        or (isinstance(fn, ast.Name) and fn.id == "require_feature")):
                    first_guarded.append(node.name)
check("主动作第一行都是付费闸门", {"_boot", "_push"}.issubset(set(first_guarded)), str(first_guarded))
check("status 用 setText（不是 set）", "self.status.setText(" in src and ".status.set(" not in src)
check("按钮/Worker 均走 connect_guarded", src.count("connect_guarded(") >= 3)
check("没有致命的 on_error=self._status.set", "on_error=self._status.set" not in src)
check("不用 setStretchLastSection", "setStretchLastSection" not in src)
check("页面未直连公网", "http://" not in src.replace("http://127.0.0.1", "") or "PROJECT_URL" in src)

print("\n" + "=" * 62)
print(f"通过 {PASS} 项，失败 {FAIL} 项")
if FAIL:
    print("见舟桥接退化：先修好再发版。")
    raise SystemExit(1)
print("全部通过：见舟桥接可随包交付。")
