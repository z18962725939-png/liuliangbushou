# -*- coding: utf-8 -*-
"""发版门禁：转录页「本地目录」按钮 + 四个原本没接线的按钮。

为什么要有这个门禁（2026-11-10 客户提问「转录功能中，是否可以添加本地目录按钮」）：
    查代码时发现两件事，都属于**编译不报错、客户只看到「点了没反应」**：
    1. 待转文件只有「添加文件」（一个个选）和「扫描录制/下载目录」（只扫软件
       自己的目录），素材放在任意本地文件夹时没有批量入口；
       _refresh_empty() 读的 file_empty 控件在源码重建时丢了，空列表是一块白板。
    2. 「打开引擎目录」「导入本地引擎包」「导入本地模型」「打开输出目录」
       四个按钮只 addWidget、从没 connect —— 尤其「导入本地引擎包」是上一轮
       显卡引擎下载慢时写进提示文案里许给客户的逃生出口，点了却完全不动。

只用静态断言挡不住：按钮存在 ≠ 有槽，有槽 ≠ 点下去不抛异常。所以这里
用 offscreen 真 click() 走一遍，并把目录扫描做成纯函数单独验。
全程不联网、不写真用户目录、不弹窗；文件对话框 / 资源管理器 / 导入函数全部替换成假的。
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

_PASSED = 0
_FAILED = 0


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


UI_SRC = src("gui_qt/pages/transcribe_page.py")
CORE_SRC = src("justoneapi_collector/transcribe.py")

# ─────────────────────────────────────────── ① 静态：接线与防呆
chk("核心有 scan_media_folder 纯函数", "def scan_media_folder(" in CORE_SRC)
chk("扫描用 os.walk 剪枝而不是无限 rglob",
    "os.walk(str(root))" in CORE_SRC and "dirs[:]" in CORE_SRC)
chk("扫描有遍历条目上限", "max_entries" in CORE_SRC and "walked > max_entries" in CORE_SRC)
chk("扫描有结果数量上限", "max_results" in CORE_SRC and "len(out) >= max_results" in CORE_SRC)

chk("界面新增「添加本地目录」按钮", 'QPushButton("添加本地目录")' in UI_SRC)
chk("批量导入后列表支持多选移除", "ExtendedSelection" in UI_SRC)
chk("按钮确实调用了扫描函数", "scan_media_folder(Path(d))" in UI_SRC)
chk("空态控件被真正创建", "self.file_empty = QLabel(" in UI_SRC)
chk("空态走 QSS 语义类（深色可读）",
    'self.file_empty.setObjectName("Caption")' in UI_SRC
    and "file_empty.setStyleSheet" not in UI_SRC)
chk("空态在构造末尾被刷新", "self._refresh_empty()" in UI_SRC)
_build_src = ""
try:
    from gui_qt.pages.transcribe_page import TranscribePage  # noqa: E402
    _build_src = inspect.getsource(TranscribePage._build_ui)
except Exception as e:                                      # noqa: BLE001
    print(f"（页面导入失败：{e}）")
chk("状态栏只创建一次", _build_src.count("self.status =") == 1,
    f"实际 {_build_src.count('self.status =')} 次")
chk("进度条只创建一次", _build_src.count("self.progress =") == 1,
    f"实际 {_build_src.count('self.progress =')} 次")

# 这一页历史上四个按钮全是「有控件没信号」。钉死：不准再出现裸 clicked.connect。
chk("没有任何裸 clicked.connect（槽异常会静默卡死）",
    ".clicked.connect(" not in UI_SRC)
for label, needle in (
    ("打开引擎目录", "wire(b2, self._open_engine_dir"),
    ("导入本地引擎包", "wire(b3, self._import_engine_zip"),
    ("导入本地模型", "wire(bmi, self._import_model"),
    ("打开输出目录", "wire(b_open, self._open_out_dir"),
    ("添加本地目录", "wire(self.add_folder_btn, self._add_folder"),
):
    chk(f"「{label}」按钮已接上真实槽", needle in UI_SRC)
chk("导入走后台 Worker 而非卡主线程解压", "_start_import(" in UI_SRC
    and "w = Worker(job)" in UI_SRC)
chk("添加文件过滤器与目录扫描用同一扩展名集合",
    "SUPPORTED_AUDIO_EXT" in UI_SRC and "音视频 ({exts})" in UI_SRC)

# ─────────────────────────────────────────── ② 纯函数：扫描行为
from justoneapi_collector.transcribe import (  # noqa: E402
    SUPPORTED_AUDIO_EXT, scan_media_folder,
)

TMP = Path(tempfile.mkdtemp(prefix="tc_folder_gate_"))
DATA = TMP / "素材"
(DATA / "子目录" / "再一层").mkdir(parents=True)
for rel in ("a.mp4", "子目录/b.mkv", "子目录/再一层/c.MP3", "无关.txt"):
    p = DATA / rel
    p.write_bytes(b"x")
for rel in ("node_modules/不该进.wav", "AppData/也不该进.mp4", ".git/隐藏.mp4"):
    p = DATA / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")

found, note = scan_media_folder(DATA)
names = sorted(p.name for p in found)
chk("递归扫到三层子目录里的音视频", names == ["a.mp4", "b.mkv", "c.MP3"], str(names))
chk("扫描正常完成时不给多余提示", note == "", repr(note))
chk("无关扩展名被过滤", all(p.suffix.lower() in SUPPORTED_AUDIO_EXT for p in found))
# 只看相对素材根目录的部分：临时目录本身就在 .../AppData/Local/Temp 下，
# 用完整 parts 判断会把父路径里的 AppData 误算成"没剪干净"。
chk("系统/依赖/隐藏目录被剪掉",
    all(not any(x in p.relative_to(DATA).parts
                for x in ("node_modules", "AppData", ".git"))
        for p in found))
chk("结果不重复", len({str(p).lower() for p in found}) == len(found))

empty_dir = TMP / "空目录"
empty_dir.mkdir()
got, note = scan_media_folder(empty_dir)
chk("空目录返回空清单且提示为空（界面再给人话）", got == [] and note == "", repr(note))
got, note = scan_media_folder(TMP / "根本不存在")
chk("不存在的目录给可操作提示", got == [] and "重新选一个" in note, repr(note))

got, note = scan_media_folder(DATA, max_results=2)
chk("结果超上限时截断并说明分几次选", len(got) == 2 and "分几次选子文件夹" in note, repr(note))
got, note = scan_media_folder(DATA, max_entries=2)
chk("遍历超上限时截断并建议选更里层目录", "建议选更靠里一层" in note, repr(note))

# ─────────────────────────────────────────── ③ 离屏真按钮点击
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

app = QApplication([])

from gui_qt.theme import apply_theme  # noqa: E402
apply_theme(app, "light")

from gui_qt.pages._base import AppContext  # noqa: E402
from gui_qt.pages import transcribe_page as TP  # noqa: E402


def find_btn(page, text: str):
    for b in page.findChildren(QPushButton):
        if b.text() == text:
            return b
    return None


class FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for s in list(self.slots):
            s(*args)


class SyncWorker:
    """同步版 Worker：不起线程、不碰网络，点击后立刻能断言结果。"""

    created = []

    def __init__(self, fn, *a, **k):
        self.fn = fn
        for sig in ("done", "error", "log", "progress", "finished"):
            setattr(self, sig, FakeSignal())
        SyncWorker.created.append(self)

    def emit_log(self, m):
        self.log.emit(m)

    def emit_progress(self, p, m):
        self.progress.emit(p, m)

    def start(self):
        try:
            self.done.emit(self.fn(self))
        except Exception as e:                              # noqa: BLE001
            self.error.emit(f"{type(e).__name__}: {e}")


import_calls: list = []
open_urls: list = []
boot_calls: list = []


def fake_bootstrap(kind):
    boot_calls.append(kind)


def fake_import_engine(path, kind):
    import_calls.append(("engine", Path(path).name, kind))
    return True, str(TMP / "假引擎.exe")


def fake_import_model(path, key):
    import_calls.append(("model", Path(path).name, key))
    return True, str(TMP / "假模型.bin")


def fake_open_url(url):
    open_urls.append(url.toLocalFile() if hasattr(url, "toLocalFile") else str(url))
    return True


def no_problem(_dev):
    return None


with patch.object(TP, "bootstrap_engine", fake_bootstrap), \
     patch.object(TP, "Worker", SyncWorker), \
     patch.object(TP, "whisper_home", lambda: TMP / "假引擎目录"), \
     patch.object(TP, "import_engine_zip", fake_import_engine), \
     patch.object(TP, "import_model_file", fake_import_model), \
     patch.object(TP, "engine_problem", no_problem), \
     patch.object(TP, "status_text", lambda *a: "引擎状态（假）"), \
     patch.object(TP, "speed_hint", lambda *a: "速度提示（假）"), \
     patch.object(TP.QDesktopServices, "openUrl", fake_open_url), \
     patch("justoneapi_collector.logger.get_logger",
           lambda *_a, **_k: __import__("logging").getLogger("gate")):

    page = TP.TranscribePage(AppContext())
    chk("转录页离屏构造成功", page is not None)
    folder_btn = find_btn(page, "添加本地目录")
    chk("能找到「添加本地目录」按钮", folder_btn is not None)
    chk("初始空态可见、列表收起",
        not page.file_empty.isHidden() and page.file_list.isHidden())

    # ── 真点击：选一个本地素材文件夹 ──────────────────────────────
    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=str(DATA)):
        folder_btn.click()
    chk("★ 真点「添加本地目录」批量导入了 3 个音视频",
        len(page.files) == 3 and page.file_list.count() == 3,
        f"files={len(page.files)} rows={page.file_list.count()}")
    chk("导入后空态收起、列表显示",
        page.file_empty.isHidden() and not page.file_list.isHidden())
    chk("状态栏回报导入数量", "3 个音视频" in page.status.text(), page.status.text())
    chk("运行日志留下目录来源", "添加本地目录" in page.log.toPlainText())

    # ── 再点同一目录：不重复 ──────────────────────────────────────
    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=str(DATA)):
        folder_btn.click()
    chk("★ 重复导入同一目录不会重复添加", len(page.files) == 3, str(len(page.files)))
    chk("重复导入时明确说明跳过", "跳过已存在 3 个" in page.status.text(),
        page.status.text())

    # ── 取消对话框：列表不动 ──────────────────────────────────────
    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=""):
        folder_btn.click()
    chk("取消选择后列表不变", len(page.files) == 3, str(len(page.files)))

    # ── 空目录给可操作提示 ────────────────────────────────────────
    page._clear()
    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=str(empty_dir)):
        folder_btn.click()
    chk("空目录时状态栏说明「没找到」", "没找到" in page.status.text(),
        page.status.text())
    chk("空目录后空态重新出现", not page.file_empty.isHidden())

    # ── 「打开输出目录」三种情况 ──────────────────────────────────
    out_btn = find_btn(page, "打开输出目录")
    chk("能找到「打开输出目录」按钮", out_btn is not None)
    target = TMP / "输出"
    page.out_dir.setText(str(target))
    open_urls.clear()
    out_btn.click()
    chk("★ 真点「打开输出目录」真的打开了目录",
        len(open_urls) == 1 and Path(open_urls[0]) == target, str(open_urls))
    chk("输出目录不存在时先建好再打开", target.is_dir())
    chk("打开后状态栏有反馈", "已打开输出目录" in page.status.text(), page.status.text())

    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=str(DATA)):
        folder_btn.click()
    page.out_dir.setText("")
    open_urls.clear()
    out_btn.click()
    chk("输出目录留空时打开源视频所在目录",
        len(open_urls) == 1 and Path(open_urls[0]) == DATA, str(open_urls))
    chk("留空时说明结果就放在源视频旁边", "留空" in page.status.text(),
        page.status.text())

    page._clear()
    open_urls.clear()
    out_btn.click()
    chk("既没输出目录又没文件时给可操作提示、不乱开目录",
        open_urls == [] and "还没选输出目录" in page.status.text(),
        page.status.text())

    # ── 「打开引擎目录」 ──────────────────────────────────────────
    eng_btn = find_btn(page, "打开引擎目录")
    chk("能找到「打开引擎目录」按钮", eng_btn is not None)
    open_urls.clear()
    eng_btn.click()
    chk("★ 真点「打开引擎目录」打开 whisper_home（且不写真实用户目录）",
        len(open_urls) == 1 and Path(open_urls[0]) == TMP / "假引擎目录",
        str(open_urls))

    # ── 两个「导入本地…」按钮：后台不卡界面、结果有反馈 ──────────
    zip_btn = find_btn(page, "导入本地引擎包")
    model_btn = find_btn(page, "导入本地模型")
    chk("能找到「导入本地引擎包」按钮", zip_btn is not None)
    chk("能找到「导入本地模型」按钮", model_btn is not None)
    fake_zip = TMP / "whisper-cuda-12.4.zip"
    fake_zip.write_bytes(b"x")
    with patch.object(TP.QFileDialog, "getOpenFileName", return_value=(str(fake_zip), "")):
        zip_btn.click()
    chk("★ 真点「导入本地引擎包」真的调了导入函数",
        any(c[0] == "engine" and c[1] == fake_zip.name for c in import_calls),
        str(import_calls))
    chk("引擎包导入结果写进状态栏", "导入并校验通过" in page.status.text(),
        page.status.text())

    fake_bin = TMP / "ggml-small.bin"
    fake_bin.write_bytes(b"x")
    with patch.object(TP.QFileDialog, "getOpenFileName", return_value=(str(fake_bin), "")):
        model_btn.click()
    chk("★ 真点「导入本地模型」用当前选中的模型 key",
        any(c[0] == "model" and c[2] == page._cur_model() for c in import_calls),
        str(import_calls))

    with patch.object(TP, "import_model_file",
                      lambda p, k: (False, "体积校验未通过")):
        with patch.object(TP.QFileDialog, "getOpenFileName",
                          return_value=(str(fake_bin), "")):
            model_btn.click()
    chk("导入失败时状态栏给出原因而不是静默", "体积校验未通过" in page.status.text(),
        page.status.text())

    # ── 槽异常兜底：按钮点了不能变成「没反应」 ───────────────────
    print("（下一段 traceback 是故意注入的槽异常，用于验证兜底，属预期）")
    with patch.object(TP, "scan_media_folder",
                      side_effect=RuntimeError("模拟扫描炸了")):
        with patch.object(TP.QFileDialog, "getExistingDirectory",
                          return_value=str(DATA)):
            folder_btn.click()
    chk("★ 槽抛异常时写进状态栏而非静默卡死",
        "添加本地目录出错" in page.status.text(), page.status.text())

    # ── 清空 / 移除后空态回来 ────────────────────────────────────
    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=str(DATA)):
        folder_btn.click()
    # 离屏且未 show 时 selectAll() 不一定真的选中，逐项 setSelected 才有真链路
    for i in range(page.file_list.count()):
        page.file_list.item(i).setSelected(True)
    chk("列表项确实处于选中态", len(page.file_list.selectedItems()) == 3,
        str(len(page.file_list.selectedItems())))
    find_btn(page, "移除选中").click()
    chk("移除选中后空态回来", len(page.files) == 0 and not page.file_empty.isHidden())
    with patch.object(TP.QFileDialog, "getExistingDirectory", return_value=str(DATA)):
        folder_btn.click()
    find_btn(page, "清空").click()
    chk("清空后空态回来", len(page.files) == 0 and not page.file_empty.isHidden())

    # ── 深色主题：空态文字不能变深色看不见 ───────────────────────
    apply_theme(app, "dark")
    page.apply_theme("dark")
    chk("深色下空态仍走语义类（不靠内联颜色）",
        page.file_empty.objectName() == "Caption" and not page.file_empty.styleSheet())

print("=" * 66)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print("转录页本地目录门禁未通过")
else:
    print("转录页本地目录 / 打开目录 / 本地包导入门禁全绿")
sys.exit(0 if _FAILED == 0 else 1)
