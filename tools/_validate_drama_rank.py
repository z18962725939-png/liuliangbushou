# -*- coding: utf-8 -*-
"""门禁：短剧解析页「看热榜 / 搜索 / 选剧 / 下载」不得永远停在「正在…」。

背景（2026-11-14 客户报障）：
  原代码 4 处 `connect_guarded(..., on_error=self._status.set)`。
  StatusLine 没有 `set` 方法 ⇒ 属性访问在**参数求值**时抛 AttributeError，
  发生在 w.start() 之前 ⇒ Worker 从未启动、done/error 永远不来；
  按钮又是裸 `.clicked.connect`，打包后槽异常没有控制台 ⇒ 客户只看到
  状态栏停在「正在拉取热榜…」。整页四个 Worker 全废。

本门禁 = 静态断言 + 离屏 Qt 真点按钮（三条返回路径都跑），
并验证凭证保存不破坏 config.json 其它键。
"""
from __future__ import annotations

import os
import sys
import ast
import json
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

PASS = 0
FAIL = 0


def chk(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[OK] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f" :: {detail}" if detail else ""))


DP_PATH = ROOT / "gui_qt" / "pages" / "drama_parse_page.py"
API_PATH = ROOT / "justoneapi_collector" / "drama_api.py"
BASE_PATH = ROOT / "gui_qt" / "pages" / "_base.py"
DP = DP_PATH.read_text(encoding="utf-8")
API = API_PATH.read_text(encoding="utf-8")
BASE = BASE_PATH.read_text(encoding="utf-8")

def _bad_on_error(src: str) -> list[str]:
    """AST 精确找出 on_error=self._status.set 这类无效回调（注释/文档不算）。"""
    found: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"语法错误: {exc}"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "on_error":
                continue
            v = kw.value
            if (isinstance(v, ast.Attribute) and v.attr == "set"
                    and isinstance(v.value, ast.Attribute)
                    and v.value.attr in ("_status", "status")):
                found.append(f"line {v.lineno}")
    return found


print("\n[1] 静态：卡死根因不得回归")
chk("StatusLine 确实没有 set 方法（这就是当年崩掉的原因）",
    "def set(" not in BASE)
_bad_dp = _bad_on_error(DP)
chk("本页无无效的 on_error=self._status.set", not _bad_dp, str(_bad_dp))
_bad_all = []
for _p in (ROOT / "gui_qt" / "pages").glob("*.py"):
    _bad_all += [f"{_p.name}:{x}" for x in
                 _bad_on_error(_p.read_text(encoding="utf-8", errors="ignore"))]
chk("全库 gui_qt/pages 无同类无效回调", not _bad_all, str(_bad_all[:5]))
chk("无裸 .clicked.connect（槽抛异常会静默卡死）",
    ".clicked.connect(" not in DP)
chk("全部 Worker 统一走 _wire_worker（含新增的公开热榜 Worker）",
    DP.count("self._wire_worker(") == 5,
    f"实际 {DP.count('self._wire_worker(')} 处")
chk("_wire_worker 的 done 与 error 都 guarded 且带 on_error",
    DP.count("connect_guarded(w.done") == 1 and
    DP.count("connect_guarded(w.error") == 1 and
    "on_error=lambda m: self._say(m, \"error\")" in DP)
_seg_search = DP.split("def _do_search", 1)[1].split("def _run_search", 1)[0]
_seg_rank = DP.split("def _do_rank", 1)[1].split("def _run_rank", 1)[0]
chk("搜索仍前置凭证检查（不发注定失败的签名请求）",
    DP.count("self._ensure_credentials()") == 1 and
    "_ensure_credentials()" in _seg_search)
chk("热榜无凭证时改走免凭证公开源，有凭证时仍走 drama_api.rank",
    "drama_api.credentials_ready()" in _seg_rank and
    "Worker(self._run_public_rank)" in _seg_rank and
    "Worker(self._run_rank)" in _seg_rank and
    "_ensure_credentials()" not in _seg_rank)
chk("凭证设置弹窗存在且 secret 用密码回显",
    "def _edit_credentials" in DP and "QLineEdit.EchoMode.Password" in DP)
chk("凭证标签会在构造时刷新（一进页就能看见状态）",
    "self._refresh_cred()" in DP and "def _refresh_cred" in DP)

print("\n[2] 静态：drama_api 凭证闭环")
for fn in ("configure", "credentials_ready", "current_credentials",
           "credential_source", "save_credentials"):
    chk(f"drama_api.{fn} 存在", f"def {fn}(" in API)
chk("configure 支持清空凭证（不再是只补非空）",
    "PROJECT_ID = (project_id or \"\").strip()" in API)
chk("错误文案不再把客户指向不存在的设置页入口",
    "在设置页填写" not in API and "点「设置凭证」" in API)
chk("save_credentials 只改 xiaoying_api 一个键",
    "data[\"xiaoying_api\"]" in API and "ensure_ascii=False" in API)
chk("显式 configure 后不再被 config 回补（清空凭证真的能清掉）",
    "_CONFIG_OVERRIDE" in API and
    "if _CONFIG_OVERRIDE or (PROJECT_ID and PROJECT_SECRET):" in API)

print("\n[3] 离屏 Qt：真点「看热榜」按钮")
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from PySide6.QtCore import QTimer                        # noqa: E402
from gui_qt.pages.drama_parse_page import DramaParsePage  # noqa: E402
from justoneapi_collector import drama_api               # noqa: E402
from justoneapi_collector import drama_public_rank       # noqa: E402


class _Ctx:
    demo = True


app = QApplication.instance() or QApplication([])
page = DramaParsePage(_Ctx())
btn = next((b for b in page.findChildren(QPushButton) if b.text() == "看热榜"), None)
chk("找到「看热榜」按钮", btn is not None)


def pump(ms: int = 900) -> None:
    """跑一小段事件循环，让跨线程 done 信号有机会回到主线程。"""
    QTimer.singleShot(ms, app.quit)
    app.exec()


# --- A：未配置凭证 → 自动降级到免凭证公开榜；不发签名请求、不卡「正在拉取」
_pub_fake = [
    {"series_id": "p1", "name": "公开剧甲", "heat": "热榜第 1 名", "tags": ["玄幻", "全30集"]},
    {"series_id": "p2", "name": "公开剧乙", "heat": "热榜第 2 名", "tags": ["都市", "全60集"]},
]
with mock.patch.object(drama_api, "credentials_ready", return_value=False), \
     mock.patch.object(drama_api, "rank", side_effect=AssertionError("不该发签名请求")) as m_rank, \
     mock.patch.object(drama_public_rank, "fetch_rank",
                       return_value=(_pub_fake, "")) as m_pub:
    btn.click()
    pump(900)
    chk("[A] 未配置凭证：状态栏不再停在「正在拉取…」",
        "正在拉取" not in page._status.text(), repr(page._status.text()))
    chk("[A] 未配置凭证：没有发出 drama_api.rank 签名请求", m_rank.call_count == 0)
    chk("[A] 未配置凭证：改走免凭证公开榜", m_pub.call_count == 1)
    chk("[A] 未配置凭证：列表渲染出 2 条公开结果",
        page._list.count() == 2, f"实际 {page._list.count()} 条")
    chk("[A] 未配置凭证：状态栏标明来源并给出可操作提示",
        "公开热榜" in page._status.text() and "凭证" in page._status.text(),
        repr(page._status.text()))

# --- B：有凭证 + 正常返回 → 列表出数据
fake = {"results": [{"series_id": "s1", "name": "测试短剧", "score": "9.2", "heat": "12万"}]}
with mock.patch.object(drama_api, "credentials_ready", return_value=True), \
     mock.patch.object(drama_api, "rank", return_value=(fake, "")):
    btn.click()
    pump(900)
    chk("[B] 正常返回：状态栏不再是「正在拉取热榜…」",
        "正在拉取热榜" not in page._status.text(), repr(page._status.text()))
    chk("[B] 正常返回：列表渲染出 1 条",
        page._list.count() == 1, f"实际 {page._list.count()} 条")
    chk("[B] 正常返回：状态栏给出结果数",
        "找到 1 部" in page._status.text(), repr(page._status.text()))

# --- C：服务端回错误 → 错误必须显示在状态栏，而不是永远转圈
with mock.patch.object(drama_api, "credentials_ready", return_value=True), \
     mock.patch.object(drama_api, "rank", return_value=(None, "短剧解析服务正在限流 —— 等一两分钟再试")):
    btn.click()
    pump(900)
    chk("[C] 服务报错：状态栏显示具体原因",
        "限流" in page._status.text(), repr(page._status.text()))
    chk("[C] 服务报错：没有卡在「正在拉取热榜…」",
        "正在拉取热榜" not in page._status.text())

# --- D：Worker 内部抛异常 → error 信号也要把原因写回状态栏
with mock.patch.object(drama_api, "credentials_ready", return_value=True), \
     mock.patch.object(drama_api, "rank", side_effect=RuntimeError("模拟线程内异常")):
    btn.click()
    pump(900)
    chk("[D] Worker 抛异常：状态栏写明失败原因",
        "模拟线程内异常" in page._status.text(), repr(page._status.text()))

# --- 弹窗：真点「设置凭证」按钮，用 patch 掉 exec 避免模态阻塞；
#     若 QDialogButtonBox 枚举 / EchoMode 写错，guarded 会把异常写进状态栏
from PySide6.QtWidgets import QDialog  # noqa: E402
with mock.patch.object(QDialog, "exec",
                       return_value=int(QDialog.DialogCode.Rejected)):
    cred_btn = next((b for b in page.findChildren(QPushButton)
                     if b.text() == "设置凭证"), None)
    chk("[弹窗] 找到「设置凭证」按钮", cred_btn is not None)
    cred_btn.click()
    pump(200)
    chk("[弹窗] 凭证对话框构造无异常（状态栏没有 guarded 报错）",
        "出错" not in page._status.text(), repr(page._status.text()))
    chk("[弹窗] 取消后不会误报「已保存」",
        "已保存" not in page._status.text(), repr(page._status.text()))

print("\n[4] 凭证落盘：不破坏 config.json 其它键")
from justoneapi_collector import branding  # noqa: E402

_orig_pid, _orig_sec = drama_api.PROJECT_ID, drama_api.PROJECT_SECRET
with tempfile.TemporaryDirectory() as td:
    cfg = Path(td) / "config.json"
    cfg.write_text(json.dumps({"token": "keep-me", "transcribe_model": "small"},
                              ensure_ascii=False), encoding="utf-8")
    with mock.patch.object(branding, "app_data_dir", return_value=Path(td)):
        ok, msg = drama_api.save_credentials("pid-123", "sec-456")
        chk("[E] 保存成功", ok, msg)
        saved = json.loads(cfg.read_text(encoding="utf-8"))
        chk("[E] 其它键原样保留",
            saved.get("token") == "keep-me" and saved.get("transcribe_model") == "small")
        chk("[E] xiaoying_api 段写入正确",
            saved.get("xiaoying_api") == {"project_id": "pid-123", "secret": "sec-456"})
        chk("[E] credentials_ready 立刻为真", drama_api.credentials_ready())
        drama_api.configure("", "")
        chk("[E] 清空凭证后不会被 config 回补（可撤销）",
            not drama_api.credentials_ready())
        chk("[E] 清空后凭证来源显示为空", drama_api.credential_source() == "")
drama_api.configure(_orig_pid, _orig_sec)
drama_api._CONFIG_OVERRIDE = False   # 还原模块状态，别影响同进程后续用例

print(f"\n==== 短剧解析热榜门禁：{PASS}/{PASS + FAIL} ====")
sys.exit(1 if FAIL else 0)
