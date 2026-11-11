# -*- coding: utf-8 -*-
"""回归：账号正在直播，工具不许显示「未开播」（2026-11-06 客户反馈）。

事故复盘（两层根因叠加，都不报错、只是安静地判错）：
  1. 2026-10-31 源码重建把整套 a_bogus 签名函数丢了，只剩调用它们的
     _ab_sign 空壳 ⇒ resolve_by_web_rid 必抛 NameError，链接方式添加的
     目标状态检查全废。
  2. 旧判定是 `info.is_live AND 有流地址`，且 resolve_auto 在 webcast 没吐
     流地址时无条件用 HTML 结果覆盖。抖音页面改版后 HTML 常找不到
     roomStore ⇒ 把 status=2（在播）冲成 is_live=False。
  3. 解析异常时把 is_live 留成 False ⇒ 「没查到」被冒充成「确认没播」。

本脚本钉死：签名模块可用 / 状态与流地址解耦 / 未知与未开播分离 /
前端显示「状态未知」/ 短链采用真 web_rid。
用法：PYTHONPATH=. python tools/_validate_live_status.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.douyin_live_stream import (          # noqa: E402
    DouyinLiveStreamInfo, DouyinLiveStreamResolver, _ab_sign)
from justoneapi_collector.live_monitor import LiveMonitor, LiveStatus   # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {extra}" if not ok else ""))


def info(status=2, live=True, flv=None, err="", room="7376083140344859455",
         anchor="主播A", web_rid="745964462470"):
    return DouyinLiveStreamInfo(live, anchor, room, "MS4wLjABAAAA", {},
                                flv or {}, status, err, web_rid=web_rid)


print("=" * 66)
print("A. 静态：签名模块与前端「状态未知」态")
check("a_bogus 签名可生成（不再 NameError）",
      isinstance(_ab_sign("aid=6383", "Mozilla/5.0"), str) and
      len(_ab_sign("aid=6383", "Mozilla/5.0")) > 20)
spec_src = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
check("spec hiddenimports 含 ab_sign", "justoneapi_collector.ab_sign" in spec_src)
page_src = (ROOT / "gui_qt/pages/live_page.py").read_text(encoding="utf-8")
check("前端有「状态未知」显示", '"状态未知"' in page_src and "状态检查失败" in page_src)
mon_src = (ROOT / "justoneapi_collector/live_monitor.py").read_text(encoding="utf-8")
# 只看状态检查函数体：resolve_stream_url 里用「有流地址」判能否录制是合理的
check_body = mon_src.split("def _check_by_link", 1)[1].split("\n    def ", 1)[0]
check("_check_by_link 不再用「有流地址」当开播前提",
      "info.is_live and (info.flv_pull_urls or info.hls_pull_urls)" not in check_body
      and "status_known" in check_body)

print("\nB. 解析层：HTML 兜底不许覆盖 webcast 的开播状态")
r = DouyinLiveStreamResolver()


def _html_fail(web_rid):
    return info(status=0, live=False, err="HTML 中未找到 roomStore", room=web_rid,
                anchor="")


r.resolve_by_html = _html_fail
merged = r._with_html_fallback("745964462470", info(status=2, live=True))
check("在播但无流地址 + HTML 失败 ⇒ 仍判在播", merged.is_live and merged.raw_status == 2,
      f"is_live={merged.is_live} status={merged.raw_status}")
check("webcast 的真实昵称不被 HTML 空值冲掉", merged.anchor_name == "主播A")
merged2 = r._with_html_fallback("745964462470", info(status=4, live=False))
check("已结束(status=4)不会被 HTML 改成在播", not merged2.is_live)

print("\nC. 监控层：开播 / 未开播 / 未知 三态分离")
# 绝不写本机真实配置（自检铁律）
LiveMonitor._save_state = lambda self: True
LiveMonitor._load_state = lambda self: 0

dm = LiveMonitor(object(), save_dir=Path(tempfile.mkdtemp(prefix="lm_status_")))
uid = "web_rid:123456789"
dm.add_target(uid, "测试主播", input_type="链接")
st = dm._targets[uid]
st.live_url = "https://live.douyin.com/123456789"

# C1 ★ 客户场景：在播、但流地址没取到 —— 旧代码会判「未开播」
dm._resolver.resolve_auto = lambda u: info(status=2, live=True)
out = dm.check_once(uid)
check("在播但无流地址 ⇒ is_live=True", out.is_live is True, str(out.stream_error))
check("在播但无流地址 ⇒ 说明录制受限而非未开播",
      "正在直播" in out.stream_error and not out.stream_error.startswith("状态检查失败"))

# C2 在播且有流地址
dm._resolver.resolve_auto = lambda u: info(
    status=2, live=True, flv={"origin": "https://example.com/live.flv"})
out = dm.check_once(uid)
check("在播且有流地址 ⇒ 缓存流地址", out.is_live and bool(out.stream_url))

# C3 明确已结束 ⇒ 才是真的未开播
dm._resolver.resolve_auto = lambda u: info(status=4, live=False)
out = dm.check_once(uid)
check("status=4 ⇒ 确认未开播", out.is_live is False and out.stream_error == "")

# C4 解析异常 ⇒ 不许伪装成未开播，保留上次状态
st.is_live = True
def _boom(u):
    raise RuntimeError("模拟风控")
dm._resolver.resolve_auto = _boom
out = dm.check_once(uid)
check("解析异常 ⇒ 保留上次状态", out.is_live is True)
check("解析异常 ⇒ 标记状态检查失败", out.stream_error.startswith("状态检查失败"),
      out.stream_error)

# C5 只拿到「未找到 roomStore」⇒ 状态未知，不是未开播
dm._resolver.resolve_auto = lambda u: info(
    status=0, live=False, err="HTML 中未找到 roomStore", room="", anchor="")
out = dm.check_once(uid)
check("无状态码+有错误 ⇒ 保留上次状态", out.is_live is True)
check("无状态码+有错误 ⇒ 标记状态检查失败", out.stream_error.startswith("状态检查失败"))

print("\nD. 前端：状态未知不许显示成「未开播」")
from PySide6.QtWidgets import QApplication                      # noqa: E402
from gui_qt.theme import apply_theme                            # noqa: E402
from gui_qt.pages._base import AppContext                       # noqa: E402
from gui_qt.pages.live_page import LivePage                     # noqa: E402

app = QApplication.instance() or QApplication([])
apply_theme(app, "light")
ctx = AppContext()
ctx.daemon = None
ctx.ensure_monitor = lambda: None
page = LivePage(ctx)
page.show()
app.processEvents()

page.table.setRowCount(0)
page._add_row("测试主播", "链接", "检查中…", "-", "-", "-", sec_uid=uid)
fake = SimpleNamespace(sec_uid=uid, nickname="测试主播", is_live=True,
                       room_id="123", stream_error="状态检查失败：模拟风控")
page._on_probe_done({"kind": "unknown", "msg": "状态检查失败：模拟风控", "st": fake})
label = page.table.item(0, 2).text() if page.table.item(0, 2) else ""
check("状态检查失败 ⇒ 表格显示「状态未知」", label == "状态未知", label)
check("状态检查失败 ⇒ 状态栏提示可重试", "重试" in page.status.text(), page.status.text())

page._on_probe_done({"kind": "offline", "msg": "当前未开播",
                     "st": SimpleNamespace(sec_uid=uid, nickname="测试主播",
                                           is_live=False, room_id="123",
                                           stream_error="")})
label2 = page.table.item(0, 2).text() if page.table.item(0, 2) else ""
check("确认未开播 ⇒ 仍显示「未开播」", label2 == "未开播", label2)
page.hide()

print("=" * 66)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  x", f)
    sys.exit(1)
print("ALL_LIVE_STATUS_CHECKS_PASS")
