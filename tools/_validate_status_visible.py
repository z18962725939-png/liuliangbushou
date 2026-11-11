# -*- coding: utf-8 -*-
"""回归：状态栏不许被重复创建 / 不许掉出可达区域（2026-11-05 客户「按钮无反应」）。

事故复盘：源码重建时 6 个页面的 _build_ui 里「进度条+状态栏」被建了两遍，
第二次覆盖了 self.status / self.progress。离屏实测 1280×760 下，第二套掉到
y≈781 的窗口外，而 live_page 根布局最小高只有 730（不需要滚动）——
所有状态文字都写进一条客户永远看不见的标签，点「添加监控」就表现为「没反应」。

★ 判据必须贴合主窗口真实承载方式：main_window._scroll_wrap() 给每个页面
  都套了 QScrollArea(widgetResizable=True)。所以「长页状态栏 y>760」是
  正常的（滚动即可达），不能一刀切要求 y<760 —— 那会把 4 个正常长页误判。
  真正要钉死的是：
    A. 静态：每页 self.status / self.progress 最多赋值 1 次（抓重复创建）
    B. 离屏：StatusLine 类页面只能有 1 个实例，且 self.status 是可见的那个
    C. 短页（根最小高 <= 760，无需滚动）状态栏必须真的在 760 视口内
用法：PYTHONPATH=. python tools/_validate_status_visible.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f"  {extra}" if not ok else ""))


print("=" * 66)
print("A. 静态：状态栏 / 进度条只允许创建一次")
for p in sorted((ROOT / "gui_qt" / "pages").glob("*_page.py")):
    src = p.read_text(encoding="utf-8")
    ns = sum(1 for ln in src.splitlines() if ln.strip().startswith("self.status = "))
    nprog = sum(1 for ln in src.splitlines()
                if ln.strip().startswith("self.progress = FlowProgressBar"))
    check(f"{p.name}: status={ns} progress={nprog}", ns <= 1 and nprog <= 1)

print("\nB/C. 离屏：套主窗口同款滚动区，验状态栏唯一且可达")
from PySide6.QtWidgets import QApplication, QScrollArea, QFrame   # noqa: E402
from PySide6.QtCore import Qt                                     # noqa: E402
from gui_qt.theme import apply_theme                               # noqa: E402
from gui_qt.pages._base import AppContext, StatusLine              # noqa: E402

app = QApplication.instance() or QApplication([])
apply_theme(app, "light")

MODULES = [
    ("collect_page", "CollectPage"), ("batch_page", "BatchPage"),
    ("keyword_page", "KeywordPage"), ("video_download_page", "VideoDownloadPage"),
    ("live_page", "LivePage"), ("topic_page", "TopicPage"),
    ("competitor_page", "CompetitorPage"), ("comment_page", "CommentPage"),
    ("geo_page", "GeoPage"), ("watch_page", "WatchPage"),
    ("report_page", "ReportPage"), ("script_page", "ScriptPage"),
    ("transcribe_page", "TranscribePage"), ("trend_page", "TrendPage"),
    ("settings_page", "SettingsPage"), ("drama_page", "DramaPage"),
]

keep_alive = []          # 避免 Python/Qt 双重释放导致离屏段错误
built = 0
for mod, cls in MODULES:
    try:
        m = __import__(f"gui_qt.pages.{mod}", fromlist=[cls])
        ctx = AppContext()
        ctx.ensure_monitor = lambda: None
        page = getattr(m, cls)(ctx)
        # 与主窗口 _scroll_wrap() 完全一致的承载方式
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sa.setWidget(page)
        sa.resize(1280, 760)
        sa.show()
        app.processEvents()

        st = getattr(page, "status", None)
        keep_alive.append((sa, page))          # 保持引用，不显式 close()
        if st is None:
            continue
        built += 1

        # B：StatusLine 型页面只能有 1 个实例，且 self.status 就是它
        if isinstance(st, StatusLine):
            n = len(page.findChildren(StatusLine))
            check(f"{mod}: StatusLine 实例数={n}", n == 1)

        if not st.isVisible():
            # 分档页（收藏/设置等）状态栏在隐藏档里，属正常
            print(f"  [跳过] {mod}: 状态栏在隐藏档内（不可见，属分档页）")
            continue

        y = st.mapTo(page, st.rect().topLeft()).y()
        page_h = page.height()
        min_h = page.layout().minimumSize().height() if page.layout() else page_h
        check(f"{mod}: 状态栏 y={y} 在内容流内 [0,{page_h})",
              0 <= y < max(page_h, min_h))
        # C：短页（无需滚动）必须真的落在 760 视口内 —— 这是「点了没反应」的核心判据
        if min_h <= 760:
            check(f"{mod}: 短页(min={min_h}) 状态栏 y={y} < 760", y < 760)
        else:
            print(f"  [长页] {mod}: min={min_h} 可滚动，y={y} 允许超出视口")
        sa.hide()
    except Exception as e:                              # noqa: BLE001
        check(f"{mod}: 构造未抛异常", False, f"{type(e).__name__}: {e}")

check("至少覆盖 12 个页面", built >= 12, f"实际 {built}")

print("=" * 66)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  x", f)
    sys.exit(1)
print("ALL_STATUS_VISIBLE_CHECKS_PASS")
