# -*- coding: utf-8 -*-
"""发版前冒烟自检：把每个页面都建出来、都刷新一遍，且覆盖每种授权状态。

★ 为什么必须有这个脚本（2026-10-19 客户现场报障）
  一位授权已过期的客户一打开软件就是「启动失败」红框：
      StatCard.set_value() takes from 2 to 4 positional arguments
      but 5 were given
  根因是旧版首页在「试用期 / 已过期」两个分支里给指标卡多传了一个参数。
  这两个分支**只在特定授权状态下才会走到**，而我们此前的自检全部跑在
  未激活状态下，于是这个 bug 一路绿灯发到了客户手上。
  本脚本把「建页面 + 刷新页面」按 授权状态 × 主题 全部跑一遍，
  任何一页抛异常都算失败 —— 这类"某个分支才炸"的问题从此在发版前就会被抓住。

用法：
    C:/Python310/python.exe tools/_smoke_pages.py
"""
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 关掉全部动效：本脚本会反复建/关页面，淡入动画挂在已删除的
# QGraphicsOpacityEffect 上会刷一屏 libshiboken 噪音（不是产品缺陷）。
os.environ.setdefault("TRAFFIC_NO_ANIM", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFont                                       # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

# ⚠️ 同 _probe_pages_height.py：offscreen 下 addApplicationFont 加载 .ttc
#    会直接段错误（无 traceback，try/except 也挡不住），不要在这里注册字体。
from gui_qt.pages._base import AppContext                             # noqa: E402
from gui_qt.pages.batch_page import BatchPage                         # noqa: E402
from gui_qt.pages.collect_page import CollectPage                     # noqa: E402
from gui_qt.pages.comment_page import CommentPage                     # noqa: E402
from gui_qt.pages.competitor_page import CompetitorPage               # noqa: E402
from gui_qt.pages.geo_page import GeoPage                             # noqa: E402
from gui_qt.pages.home_page import HomePage                           # noqa: E402
from gui_qt.pages.keyword_page import KeywordPage                     # noqa: E402
from gui_qt.pages.live_page import LivePage                           # noqa: E402
from gui_qt.pages.report_page import ReportPage                       # noqa: E402
from gui_qt.pages.script_page import ScriptPage                       # noqa: E402
from gui_qt.pages.settings_page import SettingsPage                   # noqa: E402
from gui_qt.pages.topic_page import TopicPage                         # noqa: E402
from gui_qt.pages.transcribe_page import TranscribePage               # noqa: E402
from gui_qt.pages.trend_page import TrendPage                         # noqa: E402
from gui_qt.pages.video_download_page import VideoDownloadPage        # noqa: E402
from gui_qt.pages.watch_page import WatchPage                         # noqa: E402
from gui_qt.theme import apply_theme                                  # noqa: E402
from gui_qt.main_window import NAV_GROUPS, _PAGE_CLASS                 # noqa: E402

# 与 main_window.NAV_GROUPS 保持同一份清单（从那里取，避免两边漂移）
PAGES = [(name, _PAGE_CLASS[name]) for _g, items in NAV_GROUPS for name in items]

# ★ licensing.status() 的全部返回值形态。首页 refresh() 会按 state 走五个
#   分支，缺一个分支就等于漏掉一类客户。days_left=None 也要测：那是
#   "永久/不限时"授权，代码里走的是 None 分支而非 >3 分支。
LICENSE_STATES = [
    {"state": "none"},
    {"state": "trial", "days_left": 7, "label": "试用期"},
    {"state": "trial", "days_left": 0, "label": "试用期"},
    {"state": "trial", "days_left": None, "label": "试用期"},
    {"state": "free"},
    {"state": "expired", "days_left": -3, "label": "授权已到期"},
    {"state": "licensed", "days_left": 30, "label": "有效期至 2026-11-18"},
    {"state": "licensed", "days_left": 2, "label": "有效期至 2026-10-21"},
    {"state": "licensed", "days_left": None, "label": "永久有效"},
]

MODES = ["light", "dark"]


def _patch_license(status: dict):
    """把 licensing.status() 换成给定返回值，返回还原用的 callable。"""
    from justoneapi_collector import licensing
    origin = licensing.status
    licensing.status = lambda: dict(status)
    return lambda: setattr(licensing, "status", origin)


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))

    failures = []
    checks = 0
    ctx = AppContext()

    print("=" * 90)
    print("冒烟自检：%d 个页面 × %d 种授权状态 × %d 种主题" % (
        len(PAGES), len(LICENSE_STATES), len(MODES)))
    print("=" * 90)

    # ---------- 第一轮：每个页面都能建出来、都能刷新（未激活态） ----------
    for mode in MODES:
        apply_theme(app, mode)
        for name, cls in PAGES:
            checks += 1
            try:
                page = cls(ctx)
                page.resize(1180, 760)
                page.show()
                app.processEvents()
                if hasattr(page, "refresh"):
                    page.refresh()
                    app.processEvents()
                if hasattr(page, "apply_theme"):
                    page.apply_theme(mode)
                page.close()
            except Exception as exc:                            # noqa: BLE001
                failures.append((f"{name} / 主题 {mode}", "构造或刷新", exc))
                print("  [失败] %-12s 主题 %-5s %r" % (name, mode, exc))

    # ---------- 第二轮：首页在每种授权状态下刷新 ----------
    # 客户那次崩的正是「已过期」分支，所以这一轮是重点。
    for mode in MODES:
        apply_theme(app, mode)
        home = HomePage(ctx)
        home.resize(1180, 760)
        home.show()
        app.processEvents()
        for st in LICENSE_STATES:
            checks += 1
            restore = _patch_license(st)
            try:
                home.refresh()
                app.processEvents()
                home.apply_theme(mode)
                app.processEvents()
            except Exception as exc:                            # noqa: BLE001
                failures.append(
                    (f"工作台 / 授权 {st.get('state')}/{st.get('days_left')}"
                     f" / 主题 {mode}", "refresh", exc))
                print("  [失败] 工作台 授权=%-9s days=%-5s 主题=%-5s %r"
                      % (st.get("state"), st.get("days_left"), mode, exc))
            finally:
                restore()
        home.close()

    # ---------- 第三轮：额度分支 ----------
    # 首页额度区有四条分支（免费 / 不限量 / 有余量 / 已用光），
    # 「已用光」那条会走 tone='bad'，也要覆盖。
    from justoneapi_collector import quota
    for used, limit, unlimited in ((0, 5, False), (4, 5, False), (5, 5, False),
                                   (0, 0, True)):
        checks += 1
        home = HomePage(ctx)
        home.resize(1180, 760)
        home.show()
        app.processEvents()
        origin = quota.summary
        quota.summary = lambda u=used, l=limit, un=unlimited: {
            "used": u, "limit": l, "unlimited": un}
        try:
            home.refresh()
            app.processEvents()
        except Exception as exc:                                # noqa: BLE001
            failures.append((f"工作台 / 额度 {used}/{limit}/{unlimited}",
                             "refresh", exc))
            print("  [失败] 工作台 额度=%s/%s/unlimited=%s %r"
                  % (used, limit, unlimited, exc))
        finally:
            quota.summary = origin
            home.close()

    print("-" * 90)
    print("共执行 %d 项检查，失败 %d 项" % (checks, len(failures)))
    if failures:
        print()
        for where, stage, exc in failures:
            print("### %s —— %s" % (where, stage))
            print("    %r" % (exc,))
            print("    " + traceback.format_exc().strip().splitlines()[-1])
        print()
        print("结论：不通过。有页面在特定状态下会抛异常，发版前必须修掉。")
        return 1
    print("结论：通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
