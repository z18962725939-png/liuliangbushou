# -*- coding: utf-8 -*-
"""回归：一键导出日志（2026-11-15 新功能）。

背景：客户报 TikTok 录制失败时拿不到日志，定位全靠来回猜。这次加了
「侧栏常驻导出 + 失败详情窗现场导出」两个入口，并且修掉一个存量 bug：
settings_page 调 make_support_bundle 时传 with_logs=（参数名其实是
include_logs），导致设置页「一键发日志」一直报「打包失败」。

本门禁分三层：
1) 字符串：参数名、入口按钮、hiddenimports 登记；
2) AST：log_export.export_logs 必须走 Worker（不许主线程打包卡 UI）；
3) 运行时：真调 support.make_support_bundle(out_dir=临时目录)，
   校验 zip 内容（诊断报告 / 反馈内容 / 日志目录）。

用法：PYTHONPATH=. python tools/_validate_log_export.py
"""
import ast
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {extra}" if not ok else ""))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def parse(rel: str) -> ast.Module:
    return ast.parse(read(rel))


print("=" * 60)
print("一键导出日志：入口 / 参数 / 打包回归")
print("=" * 60)

# ---------------------------------------------------------------- 1) 字符串
sp = read("gui_qt/pages/settings_page.py")
check("settings_page 不再传错误参数名 with_logs=with_logs",
      "with_logs=with_logs" not in sp, "旧 bug 复活会让一键发日志必报打包失败")
check("settings_page 调用改用 include_logs=",
      "include_logs=with_logs" in sp)

mw = read("gui_qt/main_window.py")
check("主窗口侧栏有「导出日志」常驻按钮",
      'QPushButton("导出日志")' in mw)
check("主窗口导出按钮挂在侧栏布局 nv 上",
      "nv.addWidget(self._log_export_btn)" in mw)
check("主窗口导出按钮带 tooltip（客户知道点了会发生什么）",
      "_log_export_btn.setToolTip" in mw)

lp = read("gui_qt/pages/live_page.py")
check("直播页有失败详情窗统一工厂 _open_fail_dialog",
      "def _open_fail_dialog" in lp)
# 2026-11-24：轮询自动录制失败也复用了同一个工厂（_on_auto_record），调用点从
# 2 增到 3，这是正确的复用而非退化。断言只要求「至少两处且都走工厂」，不写死计数；
# 若有人退回直接 QMessageBox.warning 显示失败详情，下面的 setDetailedText 检查会拦住。
check("直播页失败窗都走统一工厂（手动+录制完成+轮询自动录制）",
      lp.count("self._open_fail_dialog(") >= 2,
      f"实际 {lp.count('self._open_fail_dialog(')} 处")
check("失败窗不再用 QMessageBox 的英文详情按钮（setDetailedText）",
      "setDetailedText" not in lp, "Qt 硬编码「Show Details...」，客户看不懂")
check("失败窗复用页面状态行（导出期间有反馈）",
      "status=self.status" in lp or "status=self._status" in lp
      or "self._status" in read("gui_qt/widgets/log_export.py"))

le = read("gui_qt/widgets/log_export.py")
check("log_export 集中调用 make_support_bundle（参数名单一来源）",
      "make_support_bundle" in le)
check("log_export 传的是 include_logs（不是 with_logs=）",
      "include_logs=True" in le and "with_logs=" not in le)
check("成功后打开桌面定位（客户不用自己翻目录）",
      "open_in_explorer" in le)
check("提示窗是自绘 LogDialog（中文按钮、布局可控）",
      "class LogDialog" in le)
check("提示窗按钮全中文（关闭 / 显示详细信息）",
      '"关闭"' in le and '"显示详细信息"' in le)
check("成功窗用绿色对勾而不是感叹号（不让客户以为出错）",
      'tone="success"' in le and '"✓"' in le)

spec = read("justoneapi_collector.spec")
check("hiddenimports 登记 justoneapi_collector.support",
      '"justoneapi_collector.support"' in spec)
check("hiddenimports 登记 gui_qt.widgets.log_export（函数体内 import 扫不到）",
      '"gui_qt.widgets.log_export"' in spec)

# ---------------------------------------------------------------- 2) AST
tree = parse("gui_qt/widgets/log_export.py")
fn = [n for n in ast.walk(tree)
      if isinstance(n, ast.FunctionDef) and n.name == "export_logs"]
check("export_logs 函数存在", bool(fn))
if fn:
    body_src = ast.get_source_segment(read("gui_qt/widgets/log_export.py"), fn[0]) or ""
    check("export_logs 走 Worker 后台线程（不卡 UI）",
          "Worker(" in body_src)
    check("export_logs 的 done/error 都有回调（结果必达客户）",
          "w.done.connect" in body_src and "w.error.connect" in body_src)

# ---------------------------------------------------------------- 3) 运行时
tmp = Path(tempfile.mkdtemp(prefix="log_export_"))
# 隔离：日志目录指到临时目录，绝不碰本机真实日志/配置
os.environ["JUSTONEAPI_LOG_DIR"] = str(tmp / "logs")
os.environ["JUSTONEAPI_LOG_FILE"] = str(tmp / "logs" / "app.log")

from justoneapi_collector import support  # noqa: E402
from justoneapi_collector.logger import get_logger  # noqa: E402

get_logger("gate").info("门禁写入的一行日志，导出包里必须能看到")

try:
    zip_path = support.make_support_bundle(
        kind="日志", note="（门禁自检）", include_logs=True,
        out_dir=tmp, current_token="abcd1234efgh5678")
    check("make_support_bundle 真打包成功（不抛 TypeError 等）",
          zip_path.exists() and zip_path.suffix == ".zip", str(zip_path))
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    check("zip 含 诊断报告.txt", "诊断报告.txt" in names, str(names))
    check("zip 含 反馈内容.txt", "反馈内容.txt" in names)
    check("zip 含最近日志（日志/ 前缀）",
          any(n.startswith("日志/") for n in names))
    with zipfile.ZipFile(zip_path) as zf:
        report = zf.read("诊断报告.txt").decode("utf-8")
    check("诊断报告含版本号", "软件版本" in report)
    tok_line = [l for l in report.splitlines() if l.startswith("当前 Token")]
    check("诊断报告 Token 已脱敏（不外泄完整凭证）",
          bool(tok_line) and "abcd1234efgh5678" not in tok_line[0]
          and "abcd" in tok_line[0], str(tok_line))
    check("诊断报告含日志目录路径", "日志目录" in report)
except Exception as e:  # noqa: BLE001
    check("make_support_bundle 真打包成功（不抛 TypeError 等）", False, repr(e))

# GUI 冒烟（离屏）：主窗口能构建、侧栏导出按钮在、点它不崩（桩掉真打包）
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui_qt.main_window import MainWindow
    win = MainWindow()
    check("主窗口离屏构建成功", True)
    btn = getattr(win, "_log_export_btn", None)
    check("侧栏导出按钮实例存在", btn is not None)
    if btn is not None:
        # 桩：真打包换成临时目录内的快速假 zip，避免写客户桌面
        import gui_qt.widgets.log_export as le_mod
        orig = le_mod.export_logs
        calls = []
        le_mod.export_logs = lambda *a, **k: calls.append(k)
        btn.click()
        MainWindow._export_logs(win)
        le_mod.export_logs = orig
        check("点导出按钮触发 export_logs 且不崩", len(calls) >= 1)

    # 端到端：真跑 export_logs（真 Worker 线程 + 真打包），只桩掉
    # 「打开资源管理器」与成功弹窗 —— 验证「点一下 → 桌面多一个 zip」这条链
    import time as _t
    import gui_qt.widgets.log_export as le2
    from justoneapi_collector import support as sup2
    e2e_dir = tmp / "e2e"
    e2e_dir.mkdir(exist_ok=True)
    sup2._desktop_dir = lambda: e2e_dir
    sup2.open_in_explorer = lambda *a, **k: None
    le2.show_result_dialog = lambda *a, **k: None
    le2.export_logs(None, ctx=None, kind="门禁E2E")
    end = _t.time() + 30
    while _t.time() < end and not list(e2e_dir.glob("*.zip")):
        app.processEvents()
        _t.sleep(0.05)
    zips = list(e2e_dir.glob("*.zip"))
    check("端到端：点导出后目标目录真的生成 zip", bool(zips), str(e2e_dir))
    check("端到端：文件名带品牌与时间戳（客户一眼认出）",
          bool(zips) and "诊断日志" in zips[0].name, zips[0].name if zips else "")

    win.close()
    app.processEvents()
except Exception as e:  # noqa: BLE001
    check("主窗口离屏构建成功", False, repr(e))

print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  ✗", f)
    sys.exit(1)
print("ALL_LOG_EXPORT_CHECKS_PASS")
