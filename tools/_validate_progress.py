"""进度条自检（v1.14.4 立）。

【为什么要有这个脚本】
2026-10-17 客户反馈「下载进度条不够明显」——查下去发现三个问题叠在一起：
  1. QSS 把进度条压到 6px 高、`text: none`，根本不显示百分比；
  2. 下载只按「第几条下完」上报，一条 50 MB 的视频要下十几秒，条全程不动；
  3. 页面各自 new QProgressBar()，哪天有人新加页面又 new 一个，就又回到 6px。

这三件事都不会编译报错，只能靠脚本钉住。所以：
  - 静态扫：gui_qt 下除 widgets/progress.py 外，不许再出现 QProgressBar；
  - 行为测：四种状态（0% / 进行中 / 100% / 忙碌）的文字与动画开关；
  - 行为测：下载进度回调必须实时、节流、单调、最后到 100%。

跑法：C:/Python310/python.exe tools/_validate_progress.py
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  ✗ {name}  {detail}")


print("=" * 62)
print("进度条自检")
print("=" * 62)

# ---------------------------------------------------------------- 1. 静态扫描
print()
print("1. 全局统一（不许有人偷偷 new 回原生进度条）")
_own = ROOT / "gui_qt" / "widgets" / "progress.py"
_offenders: list[str] = []
_flow_import_missing: list[str] = []
for _p in sorted((ROOT / "gui_qt").rglob("*.py")):
    if _p == _own:
        continue
    _src = _p.read_text(encoding="utf-8")
    for _i, _line in enumerate(_src.splitlines(), 1):
        if "QProgressBar" in _line and not _line.strip().startswith("#"):
            _offenders.append(f"{_p.relative_to(ROOT)}:{_i}")
    if "FlowProgressBar(" in _src and "import FlowProgressBar" not in _src:
        _flow_import_missing.append(str(_p.relative_to(ROOT)))

check("gui_qt 下无残留 QProgressBar（progress.py 自身除外）", not _offenders,
      f"发现：{_offenders}")
check("用 FlowProgressBar 的文件都 import 了它", not _flow_import_missing,
      f"缺 import：{_flow_import_missing}")

_flow_users = len([1 for _p in (ROOT / "gui_qt").rglob("*.py")
                   if "FlowProgressBar(" in _p.read_text(encoding="utf-8")
                   and _p != _own])
check("至少 10 个文件已换用新控件", _flow_users >= 10, f"实际 {_flow_users} 个")

# ---------------------------------------------------------------- 2. 接口兼容
print()
print("2. 接口兼容（老代码的 setValue / setRange 不用改）")
from gui_qt.widgets.progress import FlowProgressBar, _PCT_W  # noqa: E402

for _m in ("setValue", "setRange", "value", "minimum", "maximum", "reset",
           "setVisible", "setText", "setTextVisible", "set_note"):
    check(f"FlowProgressBar.{_m} 存在", hasattr(FlowProgressBar, _m))

# ---------------------------------------------------------------- 3. 四态行为
print()
print("3. 四种状态（文字 + 动画开关）")
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

_b = FlowProgressBar()

_b.setRange(0, 100)
_b.setValue(0)
check("0%：显示 0%", _b._pct.text() == "0%", _b._pct.text())
check("0%：不流动（省 CPU、不晃眼）", not _b._bar.animating())

_b.setValue(45)
check("45%：显示 45%", _b._pct.text() == "45%", _b._pct.text())
check("45%：条纹流动中", _b._bar.animating())
check("45%：未标记完成", not _b._bar._done)

_b.setValue(100)
check("100%：显示 100%", _b._pct.text() == "100%", _b._pct.text())
check("100%：转绿（done 标记）", _b._bar._done)
check("100%：停止流动", not _b._bar.animating())

_b.setRange(0, 0)
check("忙碌态：文字为「处理中」", _b._pct.text() == "处理中", _b._pct.text())
check("忙碌态：来回扫光", _b._bar.animating() and _b._bar._busy)

_b.setRange(0, 100)
_b.setValue(30)
_b.setText("12.3 MB / 48.1 MB")
check("setText 作为说明文字显示", _b.note() == "12.3 MB / 48.1 MB")
_b.setText("")
check("setText('') 隐藏说明文字", not _b.note())

# 宽度：100% 这个最长的字符串不能被切掉
from PySide6.QtGui import QFontMetrics  # noqa: E402

_fm = QFontMetrics(_b._pct.font())
check("百分比区宽度容得下「100%」",
      _b._pct.width() >= _fm.horizontalAdvance("100%"),
      f"区宽 {_b._pct.width()} < 文本宽 {_fm.horizontalAdvance('100%')}")

# 百分比数字必须比原来看得清：字号 >= 12pt 且加粗
check("百分比字号 >= 12pt", _b._pct.font().pointSize() >= 12,
      str(_b._pct.font().pointSize()))
check("百分比加粗", _b._pct.font().bold())

# ---------------------------------------------------------------- 4. 主题
print()
print("4. 明暗主题")
from gui_qt import theme  # noqa: E402

_colors = {}
for _mode in ("light", "dark"):
    theme.apply_theme(_app, _mode)
    _x = FlowProgressBar()
    _x.setRange(0, 100)
    _x.setValue(50)
    _colors[_mode] = _x._pct.styleSheet()
check("深浅主题数字颜色不同（不是一套硬编码）",
      _colors["light"] != _colors["dark"])

# ---------------------------------------------------------------- 5. 下载实时进度
print()
print("5. 下载实时进度（实时 / 节流 / 单调 / 到 100）")
import time  # noqa: E402

from gui_qt.pages.video_download_page import _download_task  # noqa: E402


class _V:
    def __init__(self, vid):
        self.video_id = vid
        self.desc = f"作品{vid}"
        self.platform = "douyin"
        self.no_watermark_url = "https://example.invalid/x.mp4"


class _W:
    def __init__(self):
        self.events = []

    def emit_progress(self, pct, msg):
        self.events.append((round(float(pct), 2), msg))

    def emit_log(self, _m):
        pass


class _DL:
    chunk_calls = 0

    def download_videos(self, videos, on_progress=None, on_chunk=None, media_type="video"):
        size = 20 * 1024 * 1024
        for idx, v in enumerate(videos):
            sent = 0
            while sent < size:
                sent += 64 * 1024
                _DL.chunk_calls += 1
                if on_chunk:
                    on_chunk(v, sent, size)
                time.sleep(0.002)
            if on_progress:
                on_progress(idx + 1, len(videos), v, None, "")
        return [(v, "x.mp4", "") for v in videos]


_w = _W()
_t0 = time.time()
_res = _download_task(_w, _DL(), [_V("a"), _V("b")], "video")
_elapsed = time.time() - _t0
_pcts = [p for p, _ in _w.events]

check("下载器确实被要求回调字节（on_chunk 生效）", len(_w.events) >= 5,
      f"只有 {len(_w.events)} 次上报")
# ★ 这条原来写死"上报 <= 40 次"，在计时精度差的机器上会假失败（2026-10-17 实测）：
#   Windows 的 sleep(0.002) 在没程序请求高精度定时器时实际约 15ms，
#   640 次回调的总耗时从 1.3 秒变成 9.6 秒，节流窗口自然放行了 80 多次 ——
#   节流本身是对的，是断言把"墙钟"当常量了。
#   现在按**实际耗时**算理论上限（窗口 0.12 秒 + 一点余量），
#   既不受机器快慢影响，又仍然能抓住"根本没过节流"的回归（那会是 600+ 次）。
_cap = int(_elapsed / 0.12) + 8
check(f"节流生效（{_DL.chunk_calls} 次回调 → 上报 {len(_w.events)} 次，"
      f"理论上限 {_cap}）", len(_w.events) <= _cap,
      f"上报 {len(_w.events)} 次，耗时 {_elapsed:.1f}s")
check("百分比只增不减（不会当着客户的面往回跳）",
      all(b >= a - 0.01 for a, b in zip(_pcts, _pcts[1:])),
      f"序列：{_pcts}")
check("结束时正好 100%", bool(_pcts) and _pcts[-1] >= 99.99, f"末尾 {_pcts[-1:]}")
check("文案带条数与 MB",
      any("条" in m and "MB" in m for _, m in _w.events))

# 源码级：_download_task 必须把 on_chunk 传给 download_videos
_src = (ROOT / "gui_qt" / "pages" / "video_download_page.py").read_text(encoding="utf-8")
check("_download_task 传了 on_chunk=on_chunk",
      re.search(r"download_videos\([^)]*on_chunk=on_chunk", _src, re.S) is not None)
check("下载页用了新的进度条控件", "self.progress = FlowProgressBar()" in _src)

# ---------------------------------------------------------------- 6. 视觉参数
print()
print("6. 视觉参数（够不够明显）")
check("条厚 >= 14px（原来是 6px）", _b._bar.height() >= 14, f"{_b._bar.height()}px")
check("百分比占位宽 >= 68px", _PCT_W >= 68, str(_PCT_W))
_busy = FlowProgressBar()
_busy.setRange(0, 0)
check("忙碌态有独立文案", _busy._pct.text() == "处理中", _busy._pct.text())

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for _f in FAILED:
        print("  ✗", _f)
    sys.exit(1)
print("全部通过：进度条统一、百分比可见、动画在跑。")
