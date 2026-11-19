# -*- coding: utf-8 -*-
"""启动性能 / 启动画面门禁（2026-11-19）。

★ 为什么要有这个脚本
  客户反馈「每次打开软件都会有一闪一闪的窗口等待」。真机抓窗口事件定位到：
    · 构造 LiveMonitor 时 tiktok_live.quick_proxy() 逐个 connect_ex 14 个端口、
      每个超时 0.25s = 没开代理时白等 3.5s（UI 线程）；
    · require_login_enabled() 一启动被调两次，每次同步发 3s 超时的 HTTP；
    · 主窗口 show() 前先闪一个 Qt 默认尺寸的 136x57 小窗；
    · drama_page 有 17 处 StatusLine.set()（无此方法）在启动刷新时抛异常。
  这些都是「能编译、能冒烟、门禁全绿」也漏掉的启动体验问题，必须钉死：

    ① 构造期不扫端口（quick_proxy 支持 ports=False 且 LiveMonitor 用它）；
    ② 云端开关一次启动只拉一次（remote_override 结果缓存）；
    ③ 启动画面在重活之前 show、主窗口 show 之后 finish；
    ④ StatusLine 无 .set 误用（drama_page 全部 setText）。
  纯源码 AST + 轻量行为断言，不联网、不起 GUI。

跑法：C:/Python310/python.exe tools/_validate_startup.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PASSED = 0
_FAILED = 0


def chk(name: str, ok: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if ok:
        _PASSED += 1
        print(f"  [OK ] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def funcs_of(src: str) -> dict:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def calls_in(node: ast.AST) -> list[str]:
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.append(f.id)
            elif isinstance(f, ast.Attribute):
                out.append(f.attr)
    return out


print("\n== 1. 端口探测延迟（quick_proxy ports 参数）==")
tl_src = read("justoneapi_collector/tiktok_live.py")
tl_funcs = funcs_of(tl_src)
qp = tl_funcs.get("quick_proxy")
chk("tiktok_live.py 可解析且含 quick_proxy", qp is not None)
if qp is not None:
    args = [a.arg for a in qp.args.args]
    chk("quick_proxy 支持 ports 开关", "ports" in args, str(args))
    qp_src = ast.get_source_segment(tl_src, qp) or ""
    chk("ports=False 时跳过端口循环", "if not ports:" in qp_src)
    chk("端口循环仍在（默认行为不变）", "_COMMON_PROXY_PORTS" in qp_src)

lm_src = read("justoneapi_collector/live_monitor.py")
lm_funcs = funcs_of(lm_src)
init = lm_funcs.get("__init__")
chk("live_monitor.py 可解析且含 __init__", init is not None)
if init is not None:
    init_src = ast.get_source_segment(lm_src, init) or ""
    chk("构造期用 quick_proxy(ports=False)", "ports=False" in init_src)
    # 只数真正的 Call 节点， ignores 注释里的示例写法
    qp_calls = [n for n in ast.walk(init)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "quick_proxy"]
    no_arg = [n for n in qp_calls if not n.args and not n.keywords]
    chk("构造期无无参 quick_proxy() 调用", not no_arg, f"发现 {len(no_arg)} 处")
    with_ports_false = [n for n in qp_calls
                        if any(k.arg == "ports" and isinstance(k.value, ast.Constant)
                               and k.value.value is False for k in n.keywords)]
    chk("有 ports=False 的真实调用", bool(with_ports_false))
    chk("后台补扫用线程", "Thread" in init_src and "daemon=True" in init_src)

print("\n== 2. 云端开关一次启动只拉一次（缓存）==")
lg_src = read("gui_qt/login_gate.py")
lg_funcs = funcs_of(lg_src)
ro = lg_funcs.get("remote_override")
chk("login_gate.py 可解析且含 remote_override", ro is not None)
if ro is not None:
    ro_src = ast.get_source_segment(lg_src, ro) or ""
    chk("有进程级缓存 _REMOTE_CACHE", "_REMOTE_CACHE" in ro_src)
    chk("命中缓存直接返回（不再发请求）", "return _REMOTE_CACHE" in ro_src)
    chk("支持 force 绕过缓存（设置页手动重判）",
        any(a.arg == "force" for a in ro.args.args))
chk("模块级缓存哨兵存在", "_REMOTE_CACHE" in lg_src and "..." in lg_src)

print("\n== 3. 启动画面覆盖等待 ==")
app_src = read("gui_qt/app.py")
app_funcs = funcs_of(app_src)
chk("app.py 定义了 _make_splash", "_make_splash" in app_funcs)
run = app_funcs.get("run")
if run is not None:
    run_src = ast.get_source_segment(app_src, run) or ""
    c = calls_in(run)
    chk("run() 调用 _make_splash", "_make_splash" in c)
    chk("run() 调用 splash.show", "show" in c)
    chk("run() 调用 splash.finish", "finish" in c)
    # 顺序：_make_splash 必须在 build_context 之前
    i_splash = run_src.find("_make_splash()")
    i_build = run_src.find("build_context()")
    chk("启动画面在 build_context 之前显示",
        i_splash >= 0 and i_build >= 0 and i_splash < i_build,
        f"splash@{i_splash} build@{i_build}")
    i_finish = run_src.find("splash.finish")
    i_wshow = run_src.find("window.show()")
    chk("启动画面在主窗口 show 之后收起",
        i_finish >= 0 and i_wshow >= 0 and i_wshow < i_finish,
        f"wshow@{i_wshow} finish@{i_finish}")
    chk("异常兜底里也隐藏 splash", "splash.hide()" in run_src)
# splash 纯代码绘制，不引外部图、不新增模块（打包零改动）
mk = app_funcs.get("_make_splash")
if mk is not None:
    mk_src = ast.get_source_segment(app_src, mk) or ""
    chk("splash 用 QPainter 自绘", "QPainter" in mk_src)
    chk("splash 不读外部图片（无 QPixmap 路径加载）",
        ".load(" not in mk_src and "QImage" not in mk_src)

print("\n== 4. StatusLine 无 .set 误用 ==")
drama_src = read("gui_qt/pages/drama_page.py")
import re
bad = re.findall(r"self\.status\.set\(", drama_src)
chk("drama_page 无 self.status.set(（应为 setText）", not bad, f"残留 {len(bad)} 处")
chk("drama_page 用 setText", "self.status.setText(" in drama_src)
# 全工程页面扫一遍同类误用（StatusLine 对象没有 set 方法）
import glob
all_bad = []
for p in glob.glob(os.path.join(ROOT, "gui_qt", "pages", "*.py")):
    s = open(p, encoding="utf-8").read()
    for m in re.finditer(r"(?:self\.)?(?:status|_status)\.set\(", s):
        all_bad.append(os.path.basename(p))
chk("全工程页面无 status.set( 误用", not all_bad, str(set(all_bad)))

print("\n" + "=" * 62)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print(f"【失败 {_FAILED} 项】启动体验退化（空框闪/久等/status 异常），先修好再发版。")
    sys.exit(1)
print("启动性能与启动画面门禁全绿")
sys.exit(0)
