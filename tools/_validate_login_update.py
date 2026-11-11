# -*- coding: utf-8 -*-
"""自测：扫码登录状态回显 + 自动更新流程（无头运行，不出窗口）。

覆盖三件事：
  1. 证明旧写法 `ok, header = session.poll()` 必然抛 ValueError（客户反馈的根因）
  2. 新的 _LoginWorker 在 logged_in / waiting / dead 三种状态下行为正确
  3. 更新流程：清单解析、弹窗构造、启动检查的开关与跳过逻辑
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication          # noqa: E402

app = QApplication.instance() or QApplication([])

ok_all = True


def check(name: str, cond: bool, extra: str = ""):
    global ok_all
    if cond:
        print(f"  ✔ {name} {extra}")
    else:
        ok_all = False
        print(f"  ✘ {name} {extra}")


print("=== 1. 旧写法的根因复现 ===")
try:
    _ok, _header = ("waiting", "", {})     # poll() 真实返回的三元组
    check("旧写法会抛错", False, "（居然没抛，与预期不符）")
except ValueError as e:
    check("旧写法确会抛 ValueError", True, f"→ {e}")


print("\n=== 2. _LoginWorker 三态行为 ===")
import justoneapi_collector.browser_login as bl            # noqa: E402
from gui_qt.pages.settings_page import _LoginWorker, cookie_key  # noqa: E402


class FakeSession:
    """按脚本依次返回指定状态，模拟浏览器登录过程。"""

    def __init__(self, states):
        self.states = list(states)
        self.start_ret = (True, "ok")
        self.stopped = False

    def start(self):
        return self.start_ret

    def poll(self):
        if self.states:
            return self.states.pop(0)
        return ("waiting", "", {})

    def stop(self):
        self.stopped = True


def run_worker(states, start_ret=(True, "ok")):
    fake = FakeSession(states)

    def _factory(platform, app_data_dir, port=None, open_url=None):
        fake.start_ret = start_ret
        return fake

    bl.create_login_session = _factory
    got = {}
    w = _LoginWorker("douyin", Path(os.environ.get("TEMP", ".")))
    w.done.connect(lambda ok, header: got.update(ok=ok, header=header))
    # 直接同步执行 run()：无头环境里没有真正的事件循环，跨线程信号是
    # 队列连接、不会被派发（真实 app 里有 app.exec()，不存在此问题）。
    # 这里要验证的是业务逻辑（三元组解包 / 状态判定），故同步调用即可。
    w.run()
    return got, fake


got, _ = run_worker([("logged_in", "sessionid=abc; ttwid=xyz", {"count": 2})])
check("logged_in → 成功拿到 Cookie", got.get("ok") is True and "sessionid=abc" in (got.get("header") or ""),
      f"→ {got}")

got, _ = run_worker([("waiting", "", {}), ("logged_in", "sessionid=ok", {})])
check("先 waiting 再 logged_in → 仍然成功", got.get("ok") is True, f"→ {got}")

got, _ = run_worker([("dead", "", {"error": "closed"})] * 6)
check("连续 dead → 判定失败而非误判成功", got.get("ok") is False, f"→ {got}")

got, _ = run_worker([], start_ret=(False, "未找到浏览器"))
check("浏览器启动失败 → 立刻返回原因", got.get("ok") is False and "未找到浏览器" in (got.get("header") or ""),
      f"→ {got}")

check("cookie 键名（抖音沿用旧键）", cookie_key("douyin") == "douyin_cookie"
      and cookie_key("xiaohongshu") == "xiaohongshu_cookie")


print("\n=== 3. 自动更新流程 ===")
import justoneapi_collector.updater as updater             # noqa: E402
import justoneapi_collector.branding as branding           # noqa: E402
from gui_qt.update_flow import UpdateDialog, maybe_check_update  # noqa: E402
from gui_qt.pages._base import AppContext                  # noqa: E402

info = updater.parse_manifest({
    "version": "9.9.9",
    "published": "2026-10-11",
    "notes": "测试更新说明",
    "win": {"url": "https://example.com/流量捕手_Setup_v9.9.9.exe", "size": 1024, "sha256": ""},
})
check("清单解析出新版", info.available is True and info.version == "9.9.9", f"→ {info.summary()}")
check("中文文件名直链被 percent-encode", "%E6%B5%81" in updater._encode_url(info.url),
      f"→ {updater._encode_url(info.url)}")

dlg = UpdateDialog(info, None, None)
check("更新弹窗可构造", dlg.windowTitle().endswith("v9.9.9"), f"→ {dlg.windowTitle()}")
check("按钮齐全", dlg._btn_update is not None and dlg._btn_later is not None and dlg._btn_skip is not None)

ctx = AppContext()
ctx.settings["update_auto_check"] = False
check("关闭自动检查 → 启动不联网", maybe_check_update(None, ctx, manual=False) is None)
ctx.settings["update_auto_check"] = True
ctx.settings["update_skip_version"] = "9.9.9"
check("已跳过的版本不再提示", updater.should_prompt(ctx.settings, "9.9.9") is False)
updater.clear_skipped(ctx.settings)
check("重置后恢复提示", updater.should_prompt(ctx.settings, "9.9.9") is True)


print("\n=== 4. 设置页登录态回显 ===")
from gui_qt.pages.settings_page import SettingsPage        # noqa: E402

ctx2 = AppContext()
ctx2.settings["douyin_cookie"] = "sessionid=saved"
ctx2.settings["douyin_cookie_ts"] = "10-11 20:00"
page = SettingsPage(ctx2)
check("启动时回显已登录", "已登录" in page._login_status["douyin"].text(),
      f"→ {page._login_status['douyin'].text()}")
check("退出登录按钮出现", page._logout_btns["douyin"].isVisibleTo(page) or page._logout_btns["douyin"].isVisible())
page._logout("douyin")
check("退出登录后清空", "未登录" in page._login_status["douyin"].text()
      and not ctx2.settings.get("douyin_cookie"))

print("\n结论：", "全部通过 ✔" if ok_all else "存在失败 ✘")
sys.exit(0 if ok_all else 1)
