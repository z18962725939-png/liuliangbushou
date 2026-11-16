# -*- coding: utf-8 -*-
"""回归：匿名故障摘要 + 统一运行体检（P0，2026-11-15）。

这扇门禁守的是**隐私红线**，不只守功能。一旦有人把机器码、视频链接、
客户名、本地路径、Token 塞进自动上报，或者把开关默认值改成 True，
发版就会在这里被拦下。

分三层：
1) 静态：字段白名单、配置默认值、spec hiddenimports、源码无写死服务器地址；
2) AST：所有 record_event 调用点只准传固定枚举，不准塞自由文本；
3) 运行时：脱敏、默认零联网、体检结论不含身份信息、落盘与轮转、
   启用后能发送且清理待发队列。

用法：PYTHONPATH=. python tools/_validate_diagnostics.py
"""
import ast
import json
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui_qt import diagnostics as diag                      # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {extra}" if not ok else ""))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


print("=" * 64)
print("匿名故障摘要 / 统一体检：隐私红线 + 功能回归")
print("=" * 64)

# ---------------------------------------------------------------- 1) 静态
print("\n[1] 静态：白名单 / 默认值 / 打包登记")

src = read("gui_qt/diagnostics.py")
base = read("gui_qt/pages/_base.py")
spec = read("justoneapi_collector.spec")
settings = read("gui_qt/pages/settings_page.py")
logexp = read("gui_qt/widgets/log_export.py")
apppy = read("gui_qt/app.py")

check("EVENT_FIELDS 是 9 个固定字段",
      len(diag.EVENT_FIELDS) == 9 and isinstance(diag.EVENT_FIELDS, tuple))
check("字段名里没有任何敏感词",
      not any(w in f for f in diag.EVENT_FIELDS
              for w in diag.FORBIDDEN_FIELD_WORDS),
      "白名单被放宽了")
check("FORBIDDEN_FIELD_WORDS 覆盖机器码/客户名/Token/链接/路径",
      all(w in diag.FORBIDDEN_FIELD_WORDS
          for w in ("machine", "customer", "token", "url", "path")))

# AST 精确取默认值，不靠字符串蒙混
defaults = {}
try:
    tree = ast.parse(base)
    for node in ast.walk(tree):
        # ★ 这里是 AnnAssign（_SETTINGS_DEFAULTS: dict = {...}），
        #   只找 ast.Assign 会静默拿到空字典，三条隐私断言形同虚设。
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for t in targets:
                if (isinstance(t, ast.Name) and t.id == "_SETTINGS_DEFAULTS"
                        and isinstance(node.value, ast.Dict)):
                    defaults = {k.value: v.value for k, v in zip(
                        node.value.keys, node.value.values)
                        if isinstance(k, ast.Constant)
                        and isinstance(v, ast.Constant)}
except Exception:                                            # noqa: BLE001
    pass
check("diag_report_enabled 默认 False（★ 隐私红线）",
      defaults.get("diag_report_enabled") is False,
      f"实际={defaults.get('diag_report_enabled')!r}")
check("diag_endpoint 默认空串（客户端不写死服务器）",
      defaults.get("diag_endpoint") == "",
      f"实际={defaults.get('diag_endpoint')!r}")
check("diag_anon_id 默认空串",
      defaults.get("diag_anon_id") == "")

check("spec hiddenimports 登记 gui_qt.diagnostics",
      '"gui_qt.diagnostics"' in spec)

# 源码里不许出现任何「带域名的 http(s) 地址」
url_literals = re.findall(r"https?://[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", src)
check("模块里没有写死的服务器域名", not url_literals,
      f"发现 {url_literals[:2]}")

check("设置页有「匿名故障摘要」独立档与默认关闭说明",
      "匿名故障摘要（默认关闭）" in settings
      and "不记录视频链接、账号、文案" in settings)
check("设置页有上报通道状态标签（不暴露地址）",
      "_refresh_diag_channel" in settings
      and "上报通道：未配置" in settings)
check("设置页三个出口按钮都在",
      all(s in settings for s in ("复制运行体检结论", "完整体检（实测网络）",
                                  "查看本地记录")))
check("完整体检走 Worker（真探测不卡 UI）",
      "probe_network=True" in settings and "Worker(run)" in settings)
check("设置页新按钮用 connect_guarded（禁裸 connect）",
      "connect_guarded(btn.clicked" in settings)

check("失败弹窗自动附带体检结论",
      "quick_health_block(ctx)" in logexp)
check("失败弹窗记匿名摘要",
      'diagnostics.record_event(ctx, "failure"' in logexp)
check("日志打包失败记匿名摘要",
      '"log_export_failed"' in logexp)
check("启动崩溃记匿名摘要",
      '"startup_crash"' in apppy)

# ---------------------------------------------------------------- 2) AST
print("\n[2] AST：record_event 调用点只准传固定枚举")

_ALLOWED_KW = {"step", "code", "egress", "result", "out_dir"}
bad_calls = []
call_count = 0
for rel in ("gui_qt/app.py", "gui_qt/pages/settings_page.py",
            "gui_qt/widgets/log_export.py"):
    t = ast.parse(read(rel))
    for node in ast.walk(t):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "record_event"):
            continue
        call_count += 1
        if len(node.args) > 2:
            bad_calls.append(f"{rel}: 位置参数 {len(node.args)} 个（最多 2）")
        for kw in node.keywords:
            if kw.arg not in _ALLOWED_KW:
                bad_calls.append(f"{rel}: 非法关键字 {kw.arg}")
            if isinstance(kw.value, ast.Call):
                bad_calls.append(f"{rel}: {kw.arg} 传了函数调用（疑似自由文本）")
check(f"共 {call_count} 处埋点，全部只传固定枚举",
      call_count >= 4 and not bad_calls, "; ".join(bad_calls[:4]))

# ---------------------------------------------------------------- 3) 运行时
print("\n[3] 运行时：脱敏 / 零联网 / 体检 / 落盘 / 发送")

check("scrub 清除链接",
      "https://" not in diag.scrub("失败 https://v.douyin.com/abc123 打不开"))
check("scrub 清除 Windows 本地路径",
      "Users" not in diag.scrub(r"C:\Users\Zhang\Desktop\a.mp4"))
check("scrub 清除长十六进制（机器码 / 凭据）",
      "A1B2C3D4E5F60718" not in diag.scrub("mid=A1B2C3D4E5F60718"))
check("scrub 清除邮箱与手机号",
      "@" not in diag.scrub("a.b@example.com 13812345678"))
check("scrub 截断到 120 字",
      len(diag.scrub("字" * 500)) == 120)

ev = diag.build_event("failure", step="live", code="E-502",
                      egress="direct", result="fail")
check("build_event 只产出白名单键",
      set(ev) == set(diag.EVENT_FIELDS), f"实际={sorted(ev)}")
check("build_event 的 anon_id 不含机器信息",
      ev["anon_id"] in ("", "unknown") or re.fullmatch(r"[0-9a-f]{16}", ev["anon_id"]) is not None)

check("默认（无 ctx）上报关闭", diag.report_enabled(None) is False)
check("默认 endpoint 为空", diag.endpoint(None) == "")


class FakeCtx:
    def __init__(self, **kw):
        self.settings = {"diag_report_enabled": False,
                         "diag_endpoint": "", "diag_anon_id": ""}
        self.settings.update(kw)

    def get_setting(self, k, d=None):
        return self.settings.get(k, d)

    def set_setting(self, k, v):
        self.settings[k] = v

    def save_settings(self):
        return True


check("开关开但没配地址 → 仍然关闭",
      diag.report_enabled(FakeCtx(diag_report_enabled=True)) is False)
check("拒绝明文 http 外部地址",
      diag.endpoint(FakeCtx(diag_endpoint="http://example.com/collect")) == "")
check("接受 https 地址",
      diag.endpoint(FakeCtx(diag_endpoint="https://example.com/collect"))
      == "https://example.com/collect")
check("接受本机回环（本地自测）",
      diag.endpoint(FakeCtx(diag_endpoint="http://127.0.0.1:8765/ingest"))
      == "http://127.0.0.1:8765/ingest")

# ★ 最关键一条：关闭时连 transport 都不能被调用
def _boom(*_a, **_kw):
    raise AssertionError("上报关闭时绝对不该发起网络请求")


with tempfile.TemporaryDirectory() as td:
    out = Path(td)
    n, state = diag.flush(None, out_dir=out, transport=_boom)
    check("未启用时 flush 零网络请求", n == 0 and state == "disabled",
          f"实际=({n}, {state})")
    n, state = diag.flush(FakeCtx(diag_report_enabled=True),
                          out_dir=out, transport=_boom)
    check("只开开关、没配地址时仍零网络请求",
          n == 0 and state == "disabled", f"实际=({n}, {state})")

    # 落盘 + 脱敏
    diag.record_event(FakeCtx(), "failure",
                      step="解析 https://v.douyin.com/x 失败",
                      code="E-502", out_dir=out)
    p = out / "events.jsonl"
    check("事件落到 events.jsonl", p.exists())
    body = p.read_text(encoding="utf-8") if p.exists() else ""
    check("落盘内容同样是脱敏后的", "douyin.com" not in body and "https://" not in body)
    check("落盘只有白名单键",
          all(set(json.loads(ln)) == set(diag.EVENT_FIELDS)
              for ln in body.splitlines() if ln.strip()))
    check("关闭时没有待发队列", not (out / "pending.jsonl").exists())

    # 轮转
    p.write_text("x" * (diag._MAX_FILE_BYTES + 10), encoding="utf-8")
    diag.record_event(FakeCtx(), "failure", step="rotate", out_dir=out)
    check("超过 256KB 自动轮转出 .1", (out / "events.jsonl.1").exists())

# 体检结论不含任何真实身份值
machine = ""
try:
    from justoneapi_collector import licensing
    machine = str(licensing.machine_id() or "")
except Exception:                                            # noqa: BLE001
    pass
host = ""
try:
    host = socket.gethostname()
except Exception:                                            # noqa: BLE001
    pass
report = diag.health_report(FakeCtx())
leaks = [v for v in (machine, host, os.environ.get("APPDATA", ""))
         if v and len(v) > 6 and v in report]
check("体检结论不含机器码 / 计算机名 / 本地路径", not leaks,
      f"泄露 {leaks[:2]}")
check("体检结论含版本、授权、出口、ffmpeg 四块",
      all(s in report for s in ("软件版本", "授权状态", "出口模式", "ffmpeg")))
check("体检结论明示可安全复制", "可安全复制发送" in report)
check("quick_health_block 非空且带分隔线",
      diag.quick_health_block(FakeCtx()).startswith("\n\n"))

# 启用后能发送、成功后清队列
class FakeResp:
    def read(self, _n):
        return b"{}"

    def close(self):
        pass


sent = []


def _ok(req, _timeout):
    sent.append(json.loads(req.data.decode("utf-8")))
    return FakeResp()


with tempfile.TemporaryDirectory() as td:
    out = Path(td)
    # ★ 门禁绝不许真联网：record_event 会触发后台 flush，这里临时把它换掉，
    #   只让下面显式注入 _ok 的同步 flush 跑。否则测试会真往外部域名发包。
    _orig_async = diag.flush_async
    diag.flush_async = lambda *a, **k: None
    ctx = FakeCtx(diag_report_enabled=True,
                  diag_endpoint="https://example.com/collect")
    for i in range(3):
        diag.record_event(ctx, "failure", step=f"s{i}", out_dir=out)
    # 上面每写一条都会触发后台 flush，这里显式同步再跑一次保证确定性
    n, state = diag.flush(ctx, out_dir=out, transport=_ok)
    check("启用后成功发送一批", n > 0 and state == "ok" and bool(sent),
          f"实际=({n}, {state})")
    check("发送的每批都只有白名单字段",
          all(set(e) == set(diag.EVENT_FIELDS)
              for batch in sent for e in batch["events"]))
    check("发送成功后清空待发队列", not (out / "pending.jsonl").exists())
    diag.flush_async = _orig_async

print("\n" + "=" * 64)
print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
print("全部通过")
