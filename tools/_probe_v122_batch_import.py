# -*- coding: utf-8 -*-
"""端到端验证 v1.22.0：付费档批量导入 + 接口欠费（601）时的完整表现。"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import licensing, quota                      # noqa: E402
from justoneapi_collector.models import ApiResponse                    # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


# —— 模拟付费客户：标准版 30 次/天，今日还没用 ——
licensing.status = lambda *a, **k: {                                   # type: ignore
    "state": "active", "label": "标准版", "licensed": True, "days_left": 30,
    "customer": "测试客户", "expires_at": 0, "machine_id": "TEST-MACHINE",
    "features": ["collect", "batch", "download", "live", "comment", "geo",
                 "keyword", "script", "topic", "competitor", "watch",
                 "report", "transcribe", "analyze"],
    "embedded_token": "", "tamper": 0, "trial_start": 0.0, "message": "",
    "daily_quota": 30, "plan": "标准版",
}
licensing.is_free_tier = lambda: False                                 # type: ignore
licensing.daily_quota = lambda: 30                                     # type: ignore

# 用量状态重定向到临时目录，绝不碰本机真实 usage.json
import tempfile                                                        # noqa: E402
import pathlib                                                         # noqa: E402
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="e2e_gate_"))
quota._state_paths = lambda: [_TMP / "usage.json"]                     # type: ignore


class _NoBalanceClient:
    """永远返回 601 欠费的假客户端（复现 2026-10-22 客户现场）。"""

    def __init__(self):
        self.calls = 0

    def request(self, platform, endpoint, params=None):
        self.calls += 1
        return ApiResponse(success=False, code=601, message="INSUFFICIENT BALANCE",
                           data=None, raw_json={"code": 601,
                                                "message": "INSUFFICIENT BALANCE"})


from PySide6.QtWidgets import QApplication, QMessageBox                # noqa: E402
from PySide6.QtCore import QTimer                                      # noqa: E402

app = QApplication.instance() or QApplication([])

from gui_qt.pages.video_download_page import VideoDownloadPage         # noqa: E402

# 拦截弹窗，记录文本（QMessageBox.information/warning(parent, title, text)）
_dialogs = []


def _fake_box(kind):
    def _f(*a, **k):
        title = a[1] if len(a) > 1 else ""
        text = a[2] if len(a) > 2 else ""
        _dialogs.append(f"{title}\n{text}")
        return 0
    return _f


QMessageBox.information = staticmethod(_fake_box("info"))              # type: ignore
QMessageBox.warning = staticmethod(_fake_box("warn"))                  # type: ignore
QMessageBox.question = staticmethod(_fake_box("question"))             # type: ignore

# —— 造页面：ctx 带一个「免费通道必失败」的下载器 ——
dl = VideoDownloader(_NoBalanceClient(), save_dir=str(_TMP))
dl._free_html_resolve = lambda url, desktop=False: (                   # type: ignore
    None, "7695449147242974527", "页面未解析出视频数据")
dl.resolve_short_link = lambda url: "https://www.douyin.com/video/7695449147242974527"


class _Ctx:
    demo = False
    client = dl.client
    project_manager = None
    main_window = None
    downloader = dl
    settings = {"token": "x", "dl_save_dir": str(_TMP)}

    def ensure_downloader(self):
        return dl

    def get_setting(self, k, d=None):
        return self.settings.get(k, d)


page = VideoDownloadPage(_Ctx())
check("页面构造成功（付费档）", page is not None)
check("免费档判断为 False（走付费分支）", page._free_mode() is False)

before = quota.today_count()
page._start_batch_resolve([
    "https://v.douyin.com/aaaa0001/",
    "https://v.douyin.com/bbbb0002/",
    "https://v.douyin.com/cccc0003/",
])

_worker = {"ref": None}


def _poll():
    w = getattr(page, "_import_worker", None)
    _worker["ref"] = w
    if w is not None and w.isRunning():
        QTimer.singleShot(300, _poll)
        return
    app.quit()


QTimer.singleShot(300, _poll)
app.exec()

w = _worker["ref"]
status = page.status.text() if hasattr(page.status, "text") else ""
print("\n状态栏：", status)
print("弹窗：", _dialogs[0][:500] if _dialogs else "（无）")

check("★ 预检闸门没有拦住批量导入（v1.22.0 核心）", w is not None)
check("接口被调用了（说明真的走到了兜底）", dl.client.calls >= 1,
      f"calls={dl.client.calls}")
check("失败原因说人话：提到「余额不足」",
      any("余额不足" in d for d in _dialogs))
check("失败原因说人话：提到「联系作者」",
      any("联系作者" in d for d in _dialogs))
check("弹窗引导去平台登录", any("平台登录" in d for d in _dialogs))
check("事后算账：状态栏有接口消耗/剩余", "接口兜底消耗" in status, status)
check("本机额度计数没被假 601 污染（失败不计费）",
      quota.today_count() == before, f"{before} -> {quota.today_count()}")

print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  ✗", f)
    raise SystemExit(1)
print("E2E_V122_OK")
