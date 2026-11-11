"""转录页可视化卡片 + 已转录/未转录判定 + 输出去重 门禁（v1.28.3）。

★ 为什么必须单独一条门禁：这一轮的退化全是「静默」的 ——
  · 同名文件又写回同一个 stem.txt，客户转了 4 个视频只得到 1 份稿，不报错；
  · stem 里带点时用 with_suffix 算错路径，状态永远显示「待转录」，不报错；
  · 统计卡数字不刷新、筛选点了没反应、深色下徽标颜色没换，编译全过；
  · 新加的按钮裸 connect，槽一抛异常就是「点了没反应」，打包后没控制台。
只有源码断言 + 真跑状态模块 + 离屏真构造页面三层一起才钉得住。

跑法：C:\\Python310\\python.exe tools/_validate_transcribe_ui.py
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    _fontdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_fontdir):
        os.environ.setdefault("QT_QPA_FONTDIR", _fontdir)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_PASSED = 0
_FAILED = 0


def chk(name: str, ok, detail: str = "") -> None:
    """★ 参数顺序是 (名称, 判定, 明细)。写反会变成「字符串恒真」的假绿门禁，
    输出一片 `[OK] True` 却什么都没验 —— 这种门禁比没有更危险。"""
    global _PASSED, _FAILED
    if ok:
        _PASSED += 1
        print(f"  [OK] {name}")
    else:
        _FAILED += 1
        print(f"  [!!] {name}" + (f"  → {detail}" if detail else ""))


def src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


UI = src("gui_qt/pages/transcribe_page.py")
CORE = src("justoneapi_collector/transcribe.py")
STATE = src("justoneapi_collector/transcribe_state.py")
MW = src("gui_qt/main_window.py")
SPEC = src("justoneapi_collector.spec")

print("=" * 68)
print("转录页可视化 / 状态判定 / 输出去重 门禁")
print("=" * 68)

# ─────────────────────────────── ① 静态：接线、唯一性、防呆
print("\n1. 源码断言")
chk("状态模块存在且五态齐全",
    all(k in STATE for k in ("def output_prefix(", "def txt_of(",
                             "def record_result(", "def classify(",
                             "def stats(", '"done"', '"stale"', '"conflict"')))
chk("转写主流程改用去重前缀", "prefix = _state.output_prefix(out_dir, src)" in CORE)
chk("★ 不再用 with_suffix 算 txt（stem 带点会被吞）",
    "txt = _state.txt_of(prefix)" in CORE and 'prefix.with_suffix(".txt")' not in CORE)
chk("成功后登记源文件→文稿映射", "_state.record_result(src, txt," in CORE)
chk("映射清单写在 AppData 且原子替换",
    "transcribe_manifest.json" in STATE and "os.replace(tmp, p)" in STATE)
chk("清单损坏时降级为空而不抛异常",
    "except (OSError, ValueError, TypeError):" in STATE and
    "return _empty_manifest()" in STATE)
chk("spec 登记了新模块", '"justoneapi_collector.transcribe_state"' in SPEC)
chk("界面引入状态模块", "from justoneapi_collector import transcribe_state as tstate" in UI)
chk("表格是五行状态表（不再是一行文字）",
    "QTableWidget(0, 5" in UI and '["转录状态", "文件名", "大小", "所在目录", "转录稿"]' in UI)
chk("四张统计卡键齐全",
    all(f'"{k}"' in UI for k in ("all", "done", "pending", "attention")))
chk("任务三阶段 + 当前任务 + 设备都有控件",
    "① 提取音频" in UI and "② 转录" in UI and "③ 写出文稿" in UI and
    "self.task_file" in UI and "self.task_device" in UI)
chk("筛选与刷新状态按钮都接了 guarded",
    "connect_guarded(self.filter_box.currentIndexChanged" in UI and
    "connect_guarded(b_ref.clicked" in UI and
    "connect_guarded(self.out_dir.textChanged" in UI)
chk("★ 没有裸 clicked.connect（槽异常会静默卡死）",
    ".clicked.connect(" not in UI, "发现裸 connect")
_build_ui = ""
try:
    from gui_qt.pages.transcribe_page import TranscribePage  # noqa: E402
    _build_ui = inspect.getsource(TranscribePage._build_ui)
except Exception as exc:                                    # noqa: BLE001
    print(f"  （页面导入失败：{exc}）")
chk("★ 状态栏只创建一次", _build_ui.count("self.status =") == 1,
    f"实际 {_build_ui.count('self.status =')}")
chk("★ 进度条只创建一次", _build_ui.count("self.progress =") == 1,
    f"实际 {_build_ui.count('self.progress =')}")
chk("批量导入用 refresh=False 避免 O(n²) 扫盘",
    "def _add_file(self, p: Path, *, refresh: bool = True)" in UI and
    UI.count("_add_file(p, refresh=False)") >= 2)
chk("主题切换重刷徽标前景色", "_repaint_state_colors()" in UI)
chk("转写进度回调统一 guarded", "connect_guarded(w.progress, on_progress" in UI)
chk("★ 修掉 whisper 进度 lambda 参数不匹配",
    "progress=lambda pc: worker.emit_progress(" in UI and
    "progress=lambda pc, m:" not in UI)
chk("主窗口列宽表收录新列名",
    all(f'"{k}"' in MW for k in ("转录状态", "大小", "所在目录", "转录稿")))
chk("文件名列交给统一拉伸", '_TABLE_STRETCH = ("文件名"' in MW)

# ─────────────────────────────── ② 真跑状态模块（临时目录，不碰客户数据）
print("\n2. 状态判定真跑（临时目录）")
from justoneapi_collector import transcribe_state as ts  # noqa: E402
import justoneapi_collector.branding as branding          # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tc_ui_gate_"))
_orig_app_dir = branding.app_data_dir
branding.app_data_dir = lambda: TMP
try:
    out = TMP / "输出"
    out.mkdir()
    a, b = TMP / "A", TMP / "B"
    a.mkdir(); b.mkdir()
    f1 = a / "[1]--视频_1.mp4"; f1.write_bytes(b"a" * 100)
    f2 = b / "[1]--视频_1.mp4"; f2.write_bytes(b"b" * 200)
    chk("无清单时两个同名文件判为同名冲突",
        [r["label"] for r in ts.classify([f1, f2], out)] == ["同名冲突", "同名冲突"])

    p1 = ts.output_prefix(out, f1)
    t1 = ts.txt_of(p1)
    t1.write_text("第一份文稿", encoding="utf-8")
    chk("第一个文件保持原名（兼容老版本）", t1.name == "[1]--视频_1.txt", t1.name)
    chk("★ 老稿存在但没清单时，同名两文件不许冒充「已转录」",
        [r["label"] for r in ts.classify([f1, f2], out)] == ["同名冲突", "同名冲突"])
    chk("登记映射成功", ts.record_result(f1, t1, out_dir=out, model="small", device="cuda"))

    p2 = ts.output_prefix(out, f2)
    t2 = ts.txt_of(p2)
    chk("★ 第二个同名文件自动加短哈希，不覆盖第一份",
        t2 != t1 and len(t2.stem) > len(f2.stem), t2.name)
    t2.write_text("第二份文稿", encoding="utf-8")
    ts.record_result(f2, t2, out_dir=out, model="small", device="cuda")
    rows = ts.classify([f1, f2], out)
    chk("两份同名文件都判为已转录且各自指向自己的稿",
        all(r["label"] == "已转录" for r in rows) and
        rows[0]["txt"] != rows[1]["txt"])
    chk("重转同一文件复用自己的前缀", ts.output_prefix(out, f1) == p1)
    chk("统计卡口径正确", ts.stats(rows)["done"] == 2 and ts.stats(rows)["all"] == 2)

    dotted = TMP / "第01集.最终版.mp4"
    dotted.write_bytes(b"x" * 50)
    chk("★ stem 含点时文稿名不被 with_suffix 吞掉",
        ts.txt_of(out / dotted.stem).name == "第01集.最终版.txt")

    stale_txt = ts.txt_of(out / dotted.stem)
    stale_txt.write_text("旧稿", encoding="utf-8")
    ts.record_result(dotted, stale_txt, out_dir=out)
    time.sleep(0.05)
    dotted.write_bytes(b"y" * 9000)
    os.utime(stale_txt, (stale_txt.stat().st_atime, time.time() - 100))
    chk("源文件更新后判为「源文件已更新」",
        ts.classify([dotted], out)[0]["state"] == "stale")

    (TMP / "transcribe_manifest.json").write_text("{坏掉的 JSON", encoding="utf-8")
    chk("清单损坏不抛异常、降级为空", isinstance(ts.load_manifest()["records"], dict))
    chk("空列表统计不炸", ts.stats([])["all"] == 0)
    chk("状态栏一句话含总数与已转录", "共" in ts.note_text(rows) and "已转录" in ts.note_text(rows))
finally:
    branding.app_data_dir = _orig_app_dir

# ─────────────────────────────── ③ 离屏真构造页面
print("\n3. 离屏真构造 + 真点击")
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
from gui_qt.theme import apply_theme                     # noqa: E402
from gui_qt.pages._base import AppContext                # noqa: E402
from gui_qt.pages import transcribe_page as TP           # noqa: E402

app = QApplication([])
apply_theme(app, "light")


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for s in list(self.slots):
            s(*args)


class SyncWorker:
    def __init__(self, fn, *a, **k):
        self.fn = fn
        for sig in ("done", "error", "log", "progress", "finished"):
            setattr(self, sig, FakeSignal())

    def emit_log(self, m):
        self.log.emit(m)

    def emit_progress(self, p, m):
        self.progress.emit(p, m)

    def start(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as e:                              # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


def find_btn(page, text):
    for b in page.findChildren(QPushButton):
        if b.text() == text:
            return b
    return None


UI_TMP = Path(tempfile.mkdtemp(prefix="tc_ui_page_"))
UI_OUT = UI_TMP / "输出"
UI_OUT.mkdir()
(UI_OUT / "已转好.txt").write_text("已有文稿", encoding="utf-8")
f_done = UI_TMP / "已转好.mp4"; f_done.write_bytes(b"d" * 100)
f_wait = UI_TMP / "没转过.mp4"; f_wait.write_bytes(b"w" * 200)

with patch.object(TP, "bootstrap_engine", lambda kind: None), \
     patch.object(TP, "Worker", SyncWorker), \
     patch.object(TP, "whisper_home", lambda: UI_TMP / "假引擎目录"), \
     patch.object(TP, "engine_problem", lambda dev: None), \
     patch.object(TP, "status_text", lambda *a: "引擎状态（假）"), \
     patch.object(TP, "speed_hint", lambda *a: "速度提示（假）"), \
     patch.object(TP.QDesktopServices, "openUrl", lambda url: True), \
     patch("justoneapi_collector.logger.get_logger",
           lambda *_a, **_k: __import__("logging").getLogger("gate")):

    page = TP.TranscribePage(AppContext())
    chk("转录页离屏构造成功", page is not None)
    chk("表格 5 列且表头正确",
        page.file_list.columnCount() == 5 and
        [page.file_list.horizontalHeaderItem(i).text() for i in range(5)] ==
        ["转录状态", "文件名", "大小", "所在目录", "转录稿"])
    chk("初始空态可见、表格收起",
        not page.file_empty.isHidden() and page.file_list.isHidden())
    chk("四张统计卡初值都是 0",
        all(v.text() == "0" for v in page._stat_values.values()))
    chk("三阶段标签都在且初始不高亮",
        len(page._phase_labels) == 3 and
        all(x.objectName() == "Meta" for x in page._phase_labels))

    page.out_dir.setText(str(UI_OUT))
    page._add_file(f_done)
    page._add_file(f_wait)
    chk("加两个文件后表格两行", page.file_list.rowCount() == 2,
        str(page.file_list.rowCount()))
    states = [page.file_list.item(i, 0).text() for i in range(2)]
    chk("★ 已转录 / 待转录在首列分得开", states == ["已转录", "待转录"], str(states))
    chk("★ 统计卡跟着真实数据刷新",
        page._stat_values["all"].text() == "2" and
        page._stat_values["done"].text() == "1" and
        page._stat_values["pending"].text() == "1",
        str({k: v.text() for k, v in page._stat_values.items()}))
    chk("转录稿列显示已有文稿大小", page.file_list.item(0, 4).text() != "—")

    light_color = page.file_list.item(0, 0).foreground().color().name()
    apply_theme(app, "dark")
    page.apply_theme("dark")
    dark_color = page.file_list.item(0, 0).foreground().color().name()
    chk("★ 真切深色后徽标颜色跟着换", light_color != dark_color and page._mode == "dark",
        f"{light_color} → {dark_color}")
    apply_theme(app, "light")
    page.apply_theme("light")

    page.filter_box.setCurrentIndex(1)
    chk("★ 真点「只看待转录」确实隐藏已转录那行",
        page.file_list.isRowHidden(0) and not page.file_list.isRowHidden(1))
    page.filter_box.setCurrentIndex(2)
    chk("真点「只看已转录」反向生效",
        not page.file_list.isRowHidden(0) and page.file_list.isRowHidden(1))
    page.filter_box.setCurrentIndex(0)

    btn = find_btn(page, "刷新状态")
    chk("能找到「刷新状态」按钮", btn is not None)
    if btn is not None:
        btn.click()
        chk("★ 真点「刷新状态」后表格仍在、统计不变",
            page.file_list.rowCount() == 2 and page._stat_values["done"].text() == "1")

    page.file_list.selectRow(0)
    page._remove_selected()
    chk("移除选中后行数和 files 同步", len(page.files) == 1 and page.file_list.rowCount() == 1)
    page._clear()
    chk("清空后空态回来、统计归零",
        page.file_list.rowCount() == 0 and not page.file_empty.isHidden() and
        page._stat_values["all"].text() == "0")

    page._set_phase(1)
    chk("阶段切换点亮第二步",
        [x.objectName() for x in page._phase_labels] == ["Meta", "Badge", "Meta"])
    page._set_phase(3)
    chk("完成态不带高亮且设备文案收尾",
        all(x.objectName() == "Meta" for x in page._phase_labels) and
        "已完成" in page.task_device.text())

print("\n" + "=" * 68)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print("转录页可视化门禁未通过")
    sys.exit(1)
print("转录页可视化门禁全绿")
sys.exit(0)
