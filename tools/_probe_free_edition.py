# -*- coding: utf-8 -*-
"""免费版专项自检（v1.23.0，2026-10-23）。

跑法：
    set TRAFFIC_EDITION=free && python tools/_probe_free_edition.py
    python tools/_probe_free_edition.py          # 脚本内部会自己设环境变量

为什么需要它：
    免费版的行为差异全挂在 branding.APP_IS_FREE 上，而这些差异**编译期
    一点错都不会报** —— 忘了裁导航，客户打开还是一排点不动的付费功能；
    忘了隐藏授权区，客户会以为"要填激活码才能用"；忘了强制免费档，
    视频下载会去调已经欠费的接口，客户看到「余额不足」。
    这三条正好是 2026-10-23 客户投诉的全部内容，所以逐条钉住。

产出：
    控制台 OK/FAIL 明细 + _tmp_shots/free_*.png（三页截图，人工看一眼）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import branding 之前设：branding 在模块加载时就读这个变量
os.environ.setdefault("TRAFFIC_EDITION", "free")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

SHOTS = ROOT / "_tmp_shots"
FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'[OK ]' if ok else '[FAIL]'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from justoneapi_collector import branding
    from gui_qt.main_window import MainWindow, NAV_GROUPS
    from gui_qt.pages._base import AppContext
    from gui_qt.theme import apply_theme

    print("=" * 60)
    print("免费版专项自检")
    print("=" * 60)
    print(f"  APP_EDITION      = {branding.APP_EDITION}")
    print(f"  APP_IS_FREE      = {branding.APP_IS_FREE}")
    print(f"  APP_DISPLAY_NAME = {branding.APP_DISPLAY_NAME}")
    print(f"  EXE_NAME         = {branding.EXE_NAME}")
    print(f"  UPDATE_FEED_URL  = {branding.UPDATE_FEED_URL}")
    print()

    # ── 1) 身份与产物命名 ──
    print("1) 身份")
    check("APP_IS_FREE 为真", branding.APP_IS_FREE)
    check("显示名带「免费版」", "免费版" in branding.APP_DISPLAY_NAME)
    check("exe 名带「免费版」（不会覆盖完整版）", "免费版" in branding.EXE_NAME)
    check("更新源指向 version_free.json（不会把免费版更成完整版）",
          branding.UPDATE_FEED_URL.endswith("version_free.json"))

    # ── 2) 导航只有三个入口 ──
    print("\n2) 导航")
    flat = [name for _g, items in NAV_GROUPS for name in items]
    check("导航项 = 视频下载 / 直播监控 / 设置", flat == ["视频下载", "直播监控", "设置"],
          str(flat))
    check("默认打开的是「视频下载」（装它就是为这个）",
          NAV_GROUPS[0][1][0] == "视频下载")
    check("没有工作台（免费版没有授权与额度可看）", "工作台" not in flat)
    for paid in ("数据采集", "关键词抓取", "文案仿写", "GEO 监测", "评论区截流", "音视频转文本"):
        check(f"没有付费入口「{paid}」", paid not in flat)

    # ── 3) 免费档强制生效（免费版永远不碰付费接口）──
    print("\n3) 免费档（不许调接口）")
    from justoneapi_collector import licensing
    check("is_free_tier() 为真（哪怕贴了完整版授权码）", licensing.is_free_tier())

    app = QApplication.instance() or QApplication(sys.argv)
    ctx = AppContext()
    win = MainWindow(ctx)
    win.resize(1180, 800)
    apply_theme(app, "light")
    app.processEvents()

    check("窗口标题带「免费版」", "免费版" in win.windowTitle(),
          win.windowTitle())
    pages = getattr(win, "_pages", {})
    check("实际构建了 3 个页面", len(pages) == 3, str(sorted(pages)))

    # 视频下载页：必须判定为免费模式，且状态栏说清"不限次数"
    dl = pages.get("视频下载")
    if dl is not None:
        check("视频下载页判定为免费模式（_free_mode）", dl._free_mode())
        st = dl._free_status()
        check("状态栏写明不限次数", "不限次数" in st, st)
        check("状态栏不提「接口额度」", "额度" not in st or "不扣" in st, st)

    # 设置页：授权输入框必须藏起来
    st_page = pages.get("设置")
    if st_page is not None:
        check("设置页隐藏激活码输入框",
              not getattr(st_page, "_lic_dev_box", None).isVisible()
              if getattr(st_page, "_lic_dev_box", None) is not None else False)
        check("设置页标题改成「关于这个免费版」",
              "免费版" in st_page.lic_status.text()
              or "免费版" in (st_page.lic_hint.text() or ""),
              st_page.lic_status.text())
        check("设置页不显示「采集服务未连接」红字",
              "未连接" not in (st_page.svc_status.text() or ""),
              st_page.svc_status.text() or "(空)")
        check("额度行说清不消耗接口额度",
              "不限量" in st_page.quota_label.text(),
              st_page.quota_label.text())

    # ── 4) 截图（人工看一眼排版有没有塌）──
    #   切页带淡入动效（motion.fade_in），立刻 grab 会拍到还没有内容的一帧
    #   —— 之前就拍到过一张"右半边全白"的图，看着像页面崩了，其实是拍早了。
    print("\n4) 截图")
    from PySide6.QtCore import QEventLoop, QTimer

    def _settle(ms: int = 480) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    SHOTS.mkdir(exist_ok=True)
    for idx, name in enumerate(["视频下载", "直播监控", "设置"]):
        if name not in pages:
            continue
        win._switch(name) if hasattr(win, "_switch") else None
        _settle()
        out = SHOTS / f"free_{idx + 1}_{name}.png"
        ok = win.grab().save(str(out))
        check(f"截图 {name} → {out.name}", bool(ok))

    print()
    print("=" * 60)
    if FAILS:
        print(f"免费版自检未通过：{len(FAILS)} 项")
        for f in FAILS:
            print("  ✗ " + f)
        return 1
    print("FREE_EDITION_OK：导航 3 页 / 永远免费档 / 授权区已隐藏")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
