"""强制登录门离线校验（不联网、不弹真实窗口）。

覆盖：Cookie 键名规则 / 登录态判定 / 云端保险丝优先级 / 保存 Cookie /
      登录门对话框行为 / ensure_login 各分支放行与拦截。

用法：
    QT_QPA_PLATFORM=offscreen python tools/_validate_login_gate.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication, QDialog     # noqa: E402

import gui_qt.login_gate as lg                          # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------- 假上下文
class FakeDownloader:
    def __init__(self):
        self.cookies = {}

    def set_cookie_for(self, platform, header):
        self.cookies[platform] = header


class FakeCtx:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else {}
        self.saved = 0
        self.downloader = None
        self.daemon = None
        self._dl_factory = FakeDownloader

    def save_settings(self):
        self.saved += 1
        return True

    def ensure_downloader(self):
        if self.downloader is None:
            self.downloader = self._dl_factory()
        return self.downloader


class FakeSettingsPage:
    def __init__(self):
        self.restored = 0

    def _restore_saved_logins(self):
        self.restored += 1


class FakeWindow:
    def __init__(self, page):
        self._pages = {"设置": page}


print("=== A. Cookie 键名与登录态判定 ===")
check("抖音保留历史键名 douyin_cookie", lg.cookie_key("douyin") == "douyin_cookie",
      lg.cookie_key("douyin"))
check("其它平台用 <平台>_cookie", lg.cookie_key("kuaishou") == "kuaishou_cookie",
      lg.cookie_key("kuaishou"))
check("空 Cookie 视为未登录", lg.is_logged_in({}) is False)
check("只有普通 Cookie（ttwid）不算登录",
      lg.is_logged_in({"douyin_cookie": "ttwid=abc; msToken=xyz"}) is False)
check("含 sessionid 视为已登录",
      lg.is_logged_in({"douyin_cookie": "ttwid=abc; sessionid=8f2c; uid_tt=1"}) is True)
check("含 sid_tt 也算已登录",
      lg.is_logged_in({"douyin_cookie": "sid_tt=xxx"}) is True)
check("空白 Cookie 不算登录", lg.is_logged_in({"douyin_cookie": "   "}) is False)

print("\n=== B. 云端保险丝 remote_override ===")


class FakeResp:
    def __init__(self, code, text):
        self.status_code = code
        self.text = text


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    def __enter__(self):
        if self._exc:
            raise self._exc
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return self._resp


def fake_httpx(resp=None, exc=None):
    class M:
        Client = staticmethod(lambda **kw: FakeClient(resp, exc))
    return M


_real_httpx = None
lg_httpx_backup = sys.modules.get("httpx")
# ★ 2026-11-19：remote_override 结果按进程缓存（一次启动只发一次请求），
#   每个场景用 force=True 显式重拉，测的是「各响应如何解读」；
#   缓存命中本身另设一项断言（见末尾），避免测试与新语义冲突。
sys.modules["httpx"] = fake_httpx(FakeResp(200, '{"require_login": false}'))
check("云端返回 false → 读到 False", lg.remote_override(force=True) is False)
sys.modules["httpx"] = fake_httpx(FakeResp(200, '{"require_login": true}'))
check("云端返回 true → 读到 True", lg.remote_override(force=True) is True)
sys.modules["httpx"] = fake_httpx(FakeResp(500, "boom"))
check("云端非 200 → None（放行给本地）", lg.remote_override(force=True) is None)
sys.modules["httpx"] = fake_httpx(FakeResp(200, "not json"))
check("云端返回垃圾 → None", lg.remote_override(force=True) is None)
sys.modules["httpx"] = fake_httpx(exc=RuntimeError("no network"))
check("云端请求异常 → None（断网绝不拦人）", lg.remote_override(force=True) is None)
# 缓存：同一进程内第二次调用不发请求（换一个会抛错的 mock，命中缓存则不碰它）
_cached = lg.remote_override(force=True)
sys.modules["httpx"] = fake_httpx(exc=RuntimeError("第二次不该发请求"))
check("二次调用命中缓存（不重复发请求）", lg.remote_override() == _cached)
if lg_httpx_backup is not None:
    sys.modules["httpx"] = lg_httpx_backup
else:
    del sys.modules["httpx"]

print("\n=== C. 开关优先级（云端 > 本地 > 默认开） ===")
ctx = FakeCtx({"require_login": True})
lg.remote_override = lambda: None
check("本地开 + 云端无 → 要求登录", lg.require_login_enabled(ctx) is True)
ctx.settings["require_login"] = False
check("本地关 + 云端无 → 不要求", lg.require_login_enabled(ctx) is False)
lg.remote_override = lambda: False
check("本地开 + 云端关 → 云端说了算（关）", lg.require_login_enabled(ctx) is False)
ctx.settings["require_login"] = False
lg.remote_override = lambda: True
check("本地关 + 云端开 → 云端说了算（开）", lg.require_login_enabled(ctx) is True)
lg.remote_override = lambda: None
check("设置里没这个键 → 默认开", lg.require_login_enabled(FakeCtx({})) is True)

print("\n=== D. 保存 Cookie 并注入下载器 ===")
ctx2 = FakeCtx({})
lg._save_cookie(ctx2, "douyin", "sessionid=abc; sid_tt=1")
check("Cookie 写入设置", ctx2.settings.get("douyin_cookie") == "sessionid=abc; sid_tt=1",
      ctx2.settings.get("douyin_cookie"))
check("写入登录时间戳", bool(ctx2.settings.get("douyin_cookie_ts")))
check("已落盘", ctx2.saved >= 1, f"saved={ctx2.saved}")
check("已注入下载器", (ctx2.downloader.cookies if ctx2.downloader else {}).get("douyin")
      == "sessionid=abc; sid_tt=1")
check("保存后 is_logged_in 为真", lg.is_logged_in(ctx2.settings) is True)

print("\n=== E. 登录门对话框 ===")
ctx3 = FakeCtx({})
dlg = lg.LoginGateDialog(ctx3)
# 注意：offscreen 下对话框未 show，子控件 isVisible() 恒为 False，
# 所以这里统一用 isHidden()（显式隐藏标志）判断，避免假阴性。
check("逃生按钮默认隐藏", dlg.skip_btn.isHidden() is True)
dlg._on_done(False, "浏览器启动失败")
check("失败 1 次仍不显示逃生按钮", dlg.skip_btn.isHidden() is True)
dlg._on_done(False, "登录超时")
check("连续失败 2 次后显示逃生按钮", dlg.skip_btn.isHidden() is False)
dlg._on_done(True, "")
check("登录成功但无 Cookie → 不认成功", dlg.logged_in is False)
check("登录成功但无 Cookie → 不写设置", ctx3.settings.get("douyin_cookie") is None)

ctx4 = FakeCtx({})
dlg2 = lg.LoginGateDialog(ctx4)
accepted = {"v": False}
dlg2.accept = lambda: accepted.__setitem__("v", True)
dlg2._on_done(True, "sessionid=zzz; sid_tt=9")
check("登录成功 → 标记已登录", dlg2.logged_in is True)
check("登录成功 → 关闭对话框", accepted["v"] is True)
check("登录成功 → Cookie 落盘", ctx4.settings.get("douyin_cookie") == "sessionid=zzz; sid_tt=9")

ctx5 = FakeCtx({})
dlg3 = lg.LoginGateDialog(ctx5)
accepted3 = {"v": False}
dlg3.accept = lambda: accepted3.__setitem__("v", True)
dlg3._skip()
check("点「暂不登录」→ 标记 skipped", dlg3.skipped is True)
check("点「暂不登录」→ 放行关闭", accepted3["v"] is True)
check("点「暂不登录」→ 不写 Cookie", ctx5.settings.get("douyin_cookie") is None)

print("\n=== F. ensure_login 分支 ===")
lg.remote_override = lambda: None

c_ok = FakeCtx({"require_login": True, "douyin_cookie": "sessionid=1"})
calls = {"n": 0}
lg.LoginGateDialog = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), None)[1]
check("已登录 → 放行且不弹窗",
      lg.ensure_login(None, c_ok) is True and calls["n"] == 0, f"calls={calls['n']}")

c_off = FakeCtx({"require_login": False})
check("开关关闭 → 放行且不弹窗",
      lg.ensure_login(None, c_off) is True and calls["n"] == 0)


class DlgOk:
    def __init__(self, ctx, parent=None, **kw):
        self.logged_in = True
        self.skipped = False

    def exec(self):
        return QDialog.DialogCode.Accepted


class DlgSkip:
    def __init__(self, ctx, parent=None, **kw):
        self.logged_in = False
        self.skipped = True

    def exec(self):
        return QDialog.DialogCode.Accepted


class DlgQuit:
    def __init__(self, ctx, parent=None, **kw):
        self.logged_in = False
        self.skipped = False

    def exec(self):
        return QDialog.DialogCode.Rejected


class DlgBoom:
    def __init__(self, *a, **k):
        raise RuntimeError("对话框炸了")


page = FakeSettingsPage()
win = FakeWindow(page)

c_new = FakeCtx({"require_login": True})
lg.LoginGateDialog = DlgOk
check("未登录 + 登录成功 → 放行", lg.ensure_login(None, c_new, window=win) is True)
check("登录成功后刷新设置页显示", page.restored == 1, f"restored={page.restored}")

lg.LoginGateDialog = DlgSkip
check("未登录 + 跳过 → 放行", lg.ensure_login(None, FakeCtx({"require_login": True})) is True)

lg.LoginGateDialog = DlgQuit
check("未登录 + 点退出 → 不放行（终止启动）",
      lg.ensure_login(None, FakeCtx({"require_login": True})) is False)

lg.LoginGateDialog = DlgBoom
check("对话框构造异常 → 放行（绝不拦人）",
      lg.ensure_login(None, FakeCtx({"require_login": True})) is True)


class BoomCtx:
    """settings 访问就抛异常：任何环节出错都必须放行。"""
    @property
    def settings(self):
        raise RuntimeError("配置坏了")


check("判定阶段异常 → 放行", lg.ensure_login(None, BoomCtx()) is True)

print("\n=== G. 强制模式 force=True ===")
c_force = FakeCtx({"require_login": True, "douyin_cookie": "sessionid=1"})
lg.LoginGateDialog = DlgOk
check("force=True 时即便已登录也弹门（退出登录场景）",
      lg.ensure_login(None, c_force, force=True) is True)

print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项\n{'=' * 46}")
sys.exit(1 if FAIL else 0)
