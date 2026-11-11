# -*- coding: utf-8 -*-
"""发版门禁：抖音博主主页批量下载 + 工作台深色主题一致性。

两条客户反馈（2026-11-10），共同点是**编译完全不报错、只是功能/观感静默坏掉**：

1. 「无法直接提取抖音博主主页实现批量下载」
   - `_douyin_resolve_blogger_impl` 源码重建时被截断 50 行，函数永远返回 None；
   - v.douyin.com 主页短链对无登录态的 HTTP 客户端统一 302 回抖音首页，
     纯 HTTP 拿不到 sec_uid；带登录态的浏览器才能跳到 /user/MS4w…；
   - 界面「拉取列表」在解析失败时直接 return，勾了浏览器会话抓取也用不上。
2. 「切深色主题，工作台右侧颜色不一致」
   - `HomePage.apply_theme` 读 `_PANEL_STATUS`，构造时存的却是 `_panel_status`，
     GlowPanel 从不刷主题 ⇒ 深色下一块白底 + 浅色字。

不联网、不写真配置、不起真浏览器；CDP / API 客户端全部替换成假对象。
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

_PASSED = 0
_FAILED = 0
SEC = "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp"


def chk(name: str, cond: bool, why: str = ""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print(f"OK   {name}")
    else:
        _FAILED += 1
        print(f"FAIL {name}" + (f"  — {why}" if why else ""))


def src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────── ① 深色主题
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from gui_qt.theme import apply_theme  # noqa: E402
from gui_qt.widgets.stat import GlowPanel  # noqa: E402
apply_theme(app, "light")

from gui_qt.pages._base import AppContext  # noqa: E402
from gui_qt.pages.home_page import HomePage  # noqa: E402
from gui_qt.main_window import MainWindow  # noqa: E402


def _lum(c) -> float:
    return (c.red() + c.green() + c.blue()) / 3.0


# 组件自身：切主题后画出来必须明显变暗（不是只改了个 mode 变量）
gp = GlowPanel("light")
gp.resize(320, 140)
light_l = _lum(gp.grab().toImage().pixelColor(160, 70))
gp.apply_theme("dark")
dark_l = _lum(gp.grab().toImage().pixelColor(160, 70))
chk("GlowPanel 深色渲染确实变暗", dark_l < light_l - 60,
    f"浅 {light_l:.0f} / 深 {dark_l:.0f}")

# 完整链路：必须走主窗口的 _toggle_theme()（项目铁律），而不是页面自己调
win = MainWindow(AppContext())
win.show()
app.processEvents()
home = win.findChild(HomePage)
chk("能找到工作台页面", home is not None)
if home is not None:
    chk("状态面板属性名与构造处一致",
        getattr(home, "_panel_status", None) is not None)
    win._toggle_theme()
    app.processEvents()
    chk("切深色后页面 mode 已更新", home._mode == "dark", str(home._mode))
    chk("★ 切深色后 GlowPanel 真的被刷到 dark",
        home._panel_status._mode == "dark",
        str(getattr(home._panel_status, "_mode", "缺失")))
    # 再切回浅色，确认双向都能刷（不是只在第一次生效）
    win._toggle_theme()
    app.processEvents()
    chk("切回浅色后 GlowPanel 也跟着回 light",
        home._panel_status._mode == "light",
        str(getattr(home._panel_status, "_mode", "缺失")))
win.hide()

# 只禁止「代码里真的去读」错名字；注释里解释这个坑是允许的
chk("源码里不再读拼错的全大写状态面板属性",
    'getattr(self, "_PANEL_STATUS"' not in src("gui_qt/pages/home_page.py"))

# ─────────────────────────────────────────────── ② 博主解析函数完整性
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402


class DummyClient:
    calls = 0

    def request(self, *a, **k):
        DummyClient.calls += 1
        raise RuntimeError("门禁不允许调接口")


impl_src = inspect.getsource(VideoDownloader._douyin_resolve_blogger_impl)
chk("★ 解析函数不再被截断（有 return BloggerInfo）",
    "return BloggerInfo" in impl_src)
chk("解析函数保留了资料接口兜底", "user-profile-v3" in impl_src)
chk("解析函数把资料接口异常也兜住了", "except Exception" in impl_src)

d = VideoDownloader(DummyClient(), save_dir=None)

long_url = f"https://www.douyin.com/user/{SEC}"
info = d.resolve_blogger_link(long_url)
chk("完整主页长链能解出 BloggerInfo",
    info is not None and info.sec_uid == SEC,
    str(info.sec_uid if info else None))

info = d.resolve_blogger_link(f"看看TA的作品 https://v.douyin.com/x/?sec_uid={SEC}")
chk("分享文本里的 ?sec_uid= 也能解出",
    info is not None and info.sec_uid == SEC,
    str(info.sec_uid if info else None))

info = d.resolve_blogger_link("https://www.douyin.com/video/7696734238402222720")
chk("单条视频链接不会被误判成博主主页", info is None, str(info))

DummyClient.calls = 0
sid, why = d.resolve_sec_uid("https://v.douyin.com/i5U4fVPdxJBw/",
                             allow_network=False)
chk("浏览器模式下不做同步联网解析", DummyClient.calls == 0,
    f"调了 {DummyClient.calls} 次接口")
chk("短链放行给浏览器而不是直接失败", sid is None and "浏览器" in why, why)

sid2, _ = d.resolve_sec_uid(long_url, allow_network=False)
chk("浏览器模式下长链仍走零耗时正则", sid2 == SEC, str(sid2))

# Step 2.5：短链本地跟 302 后能认出主页（用假跳转，不联网）
with patch.object(d, "resolve_short_link",
                  return_value=f"https://www.douyin.com/user/{SEC}?from=share"):
    info = d._douyin_resolve_blogger_impl("https://v.douyin.com/abc/")
chk("短链跟 302 落到 /user/MS4w… 时能解出", info is not None
    and info.sec_uid == SEC, str(info.sec_uid if info else None))

# ────────────────────────────────────────── ③ 短链交给带登录态的浏览器
import justoneapi_collector.douyin_cdp_crawler as cdp  # noqa: E402
from justoneapi_collector.douyin_cdp_crawler import DouyinCDPCrawler  # noqa: E402

chk("CDP 有 resolve_profile_link", hasattr(DouyinCDPCrawler, "resolve_profile_link"))

crawler = DouyinCDPCrawler.__new__(DouyinCDPCrawler)
crawler._page = object()
crawler.navigate = lambda url: True
crawler.evaluate = lambda expr, timeout=10: (
    "https://www.douyin.com/user/MS4wLjXYZ?from=share")
with patch.object(cdp.time, "sleep", lambda _s: None):
    got, err = crawler.resolve_profile_link("https://v.douyin.com/abc/")
chk("★ 浏览器打开短链后能从落地地址解出 sec_uid",
    got == "MS4wLjXYZ" and not err, f"got={got!r} err={err!r}")

with patch.object(cdp.time, "sleep", lambda _s: None):
    crawler.evaluate = lambda expr, timeout=10: "https://www.douyin.com/"
    got, err = crawler.resolve_profile_link("https://v.douyin.com/abc/")
chk("短链跳回首页时给人话而不是「无法识别」",
    not got and "跳回" in err and "首页" in err and "MS4w" in err, err)

# list_user_list：浏览器模式下先用 profile_url 解，再拿 sec_uid 抓列表
calls = []


class FakeCrawler:
    def __init__(self, *a, **k):
        pass

    def start(self):
        return True, ""

    def resolve_profile_link(self, url):
        calls.append(("resolve", url))
        return "MS4wLjXYZ", ""

    def get_own_sec_uid(self):
        calls.append(("own", ""))
        return "", "不该走到本人兜底"

    def capture_list(self, kind, sec_uid, **kwargs):
        calls.append(("capture", sec_uid))
        return [], ""

    def stop(self):
        pass


with patch.object(cdp, "DouyinCDPCrawler", FakeCrawler):
    d.list_user_list("post", "", use_browser=True,
                     profile_url="https://v.douyin.com/abc/")
chk("★ 列表抓取会先用浏览器解开短链", ("resolve", "https://v.douyin.com/abc/") in calls,
    str(calls))
chk("★ 解开后的 sec_uid 确实传给了列表抓取", ("capture", "MS4wLjXYZ") in calls,
    str(calls))

# 界面后台任务：sec_uid 为空但带 profile_url 时不能提前 return
from gui_qt.pages.video_download_page import _fetch_list_task  # noqa: E402

seen = {}


class FakeDL:
    def list_user_list(self, *a, **k):
        seen.update(k)
        seen["args"] = a
        return [], ""


class FakeWorker:
    def emit_progress(self, *a, **k):
        pass


_fetch_list_task(FakeWorker(), FakeDL(), "post", "", False, True,
                 "https://v.douyin.com/abc/")
chk("界面后台任务把 profile_url 传下去",
    seen.get("profile_url") == "https://v.douyin.com/abc/", str(seen))
chk("界面后台任务允许 sec_uid 为空",
    seen.get("args", (None,))[1] == "", str(seen.get("args")))

ui_src = src("gui_qt/pages/video_download_page.py")
chk("界面浏览器模式跳过同步联网解析", "allow_network=False" in ui_src)
chk("界面解析失败时转交原始链接", "profile_url = url" in ui_src)
chk("界面仍保留非浏览器模式的明确报错", "无法识别博主" in ui_src)

# ─────────────────────────── ④ 真按钮点击：短链不再被挡在「拉取列表」门外
from unittest.mock import Mock  # noqa: E402
import gui_qt.pages.video_download_page as vdp  # noqa: E402

SHORT = "https://v.douyin.com/i5U4fVPdxJBw/"
seen_calls = []
created = []


class UIFakeDL:
    def resolve_sec_uid(self, url, allow_network=True):
        seen_calls.append((url, allow_network))
        return None, "本地无法识别"

    def list_user_list(self, *a, **k):
        return [], ""


class UIFakeWorker:
    """只记录参数，绝不起真线程 / 真浏览器。"""

    def __init__(self, fn, *a, **k):
        self.args = a
        for sig in ("progress", "log", "done", "error", "finished"):
            setattr(self, sig, Mock())
        created.append(self)

    def start(self):
        pass

    def isRunning(self):
        return False


ui_page = vdp.VideoDownloadPage(AppContext())
ui_page._ensure_real_downloader = lambda: True
ui_page.downloader = UIFakeDL()
idx = ui_page.blogger_platform_combo.findData("douyin")
if idx >= 0:
    ui_page.blogger_platform_combo.setCurrentIndex(idx)
ui_page.use_browser_chk.setChecked(True)
ui_page.list_url_edit.setText(SHORT)
ui_page._list_worker = None

with patch.object(vdp, "Worker", UIFakeWorker):
    ui_page.fetch_list_btn.click()
    app.processEvents()

chk("★ 真点「拉取列表」不再报「无法识别博主」",
    "无法识别博主" not in ui_page.status.text(), ui_page.status.text())
chk("★ 真点后提示用浏览器识别主页链接",
    "浏览器打开主页链接" in ui_page.status.text(), ui_page.status.text())
chk("浏览器模式确实走零联网解析",
    bool(seen_calls) and seen_calls[-1][1] is False, str(seen_calls))
chk("★ 原始短链确实传进后台任务",
    bool(created) and created[-1].args[-1] == SHORT,
    str(created[-1].args if created else None))

# 反例：没勾浏览器时仍要给能操作的报错，不能悄悄什么都不做
created.clear()
ui_page.use_browser_chk.setChecked(False)
ui_page._list_worker = None
ui_page.fetch_list_btn.setEnabled(True)
with patch.object(vdp, "Worker", UIFakeWorker):
    ui_page.fetch_list_btn.click()
chk("未勾浏览器时给出可操作报错",
    "无法识别博主" in ui_page.status.text()
    and "浏览器会话抓取" in ui_page.status.text(),
    ui_page.status.text())
ui_page.hide()

print("=" * 66)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print(f"【失败 {_FAILED} 项】主页批量下载 / 深色主题一致性退化，先修好再发版。")
    sys.exit(1)
print("主页批量下载 + 工作台深色主题门禁全绿")
sys.exit(0)
