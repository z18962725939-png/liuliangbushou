# -*- coding: utf-8 -*-
"""微信聊天整理门禁（2026-11-20）。

钉四件事，防止「看着接上了其实发版后静默失效」：
  ① 解析核心对真实微信 TXT 格式（·发言人 / yyyy年M月d日 HH:mm / 多行正文）正确；
  ② 安全边界：ZIP 炸弹上限、加密拒绝、路径穿越防护、失败关闭；
  ③ 三种出口（剪贴板文本 / Obsidian 笔记 / 附件留存）格式正确；
  ④ 接入清单：导航 / _PAGE_CLASS / FEATURES+BACKFILL / hiddenimports /
     guide / _validate_ux.EXPECTED / _ALIASES / require_feature 主动作第一行。

跑法：C:/Python310/python.exe tools/_validate_wechat_archive.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import wechat_archive as wa  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def read(rel: str) -> str:
    return (Path(ROOT) / rel).read_text(encoding="utf-8")


def make_zip(dirpath: Path, name: str, txt: str, extras: dict | None = None) -> Path:
    zp = dirpath / name
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("聊天记录.txt", txt)
        for k, v in (extras or {}).items():
            z.writestr(k, v)
    return zp


def _empty_zip(tmp: Path) -> Path:
    p = tmp / "empty.zip"
    with zipfile.ZipFile(p, "w"):
        pass
    return p


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                                       # noqa: BLE001
        return True


print("=" * 62)
print("微信聊天整理门禁")
print("=" * 62)

with tempfile.TemporaryDirectory(prefix="wx-gate-") as td:
    tmp = Path(td)
    real = (
        "·小林\n2026年10月3日 17:46\n这一批先确认首页布局，附件里的图片也一起保留。\n\n"
        "·阿明\n2026年10月3日 17:47\n收到，下一批补上交互说明。\n\n"
        "·小林\n2026年10月3日 17:48\n收齐后统一整理成评审资料。\n"
    )
    zp = make_zip(tmp, "ok.zip", real,
                  {"微信图片_001.jpg": b"jpg", "视频.mp4": b"mp4"})

    print("\n1) 解析核心（真实微信 TXT 格式）")
    t = wa.read_archive(zp)
    check("识别为可解析归档", t.ok is True)
    check("消息数正确（3 条）", len(t.messages) == 3, str(len(t.messages)))
    check("发言人有去重顺序", t.senders == ["小林", "阿明"], str(t.senders))
    check("发言人解析正确", t.messages[0].sender == "小林")
    check("时间解析正确", t.messages[0].at.year == 2026 and t.messages[0].at.minute == 46)
    check("多行正文合并", "首页布局" in t.messages[0].text)
    check("附件列表识别", set(t.media_names) == {"微信图片_001.jpg", "视频.mp4"})
    check("统计卡数据正确",
          wa.summarize([t]) == {"archives": 1, "messages": 3, "senders": 2, "failed": 0})

    print("\n2) 格式边界与安全（fail-closed）")
    bad = make_zip(tmp, "bad.zip", "这不是聊天记录，只是一段普通文字")
    tb = wa.read_archive(bad)
    check("不认识的 TXT → ok=False", tb.ok is False)
    check("不认识的 TXT → 原文保留", "普通文字" in tb.body)
    check("首头不在 0 位 → 拒绝",
          _raises(lambda: wa.parse_transcript_records(
              "前言\n·甲\n2026年9月5日 08:05\n正文\n")))
    check("非法时间 → 拒绝",
          _raises(lambda: wa.parse_transcript_records("·甲\n2026年99月5日 08:05\n正文\n")))
    check("超上限常量存在（16MB / 1GB）",
          wa.MAX_TEXT_ENTRY_BYTES == 16 * 1024 * 1024
          and wa.MAX_EXPANDED_BYTES == 1024 * 1024 * 1024)
    check("归档是空 → 拒绝", _raises(lambda: wa.read_archive(
        _empty_zip(tmp))))
    check("不存在文件 → 拒绝", _raises(lambda: wa.read_archive(tmp / "none.zip")))
    check("路径穿越只取 basename",
          wa._safe_basename("../../etc/passwd") == "passwd"
          and wa._safe_basename("a\\b\\..\\x.jpg") == "x.jpg")

    print("\n3) 三种出口")
    text = wa.transcript_to_text(t)
    check("剪贴板文本含时间戳与发言人", "[2026-10-03 17:46] 小林：" in text)
    note = wa.render_obsidian_note(t, chat_name="评审群", archive_name="ok.zip")
    check("笔记有 YAML frontmatter", note.startswith("---\n") and "\n---\n" in note)
    check("笔记有 source: WeChat", "source: WeChat" in note)
    check("笔记有消息数", "messages: 3" in note)
    check("笔记有双链原始归档", "[[附件/ok.zip]]" in note)
    check("笔记正文是 Markdown 加粗发言人", "**小林** · 2026-10-03 17:46" in note)
    check("笔记标题按群名推断", wa.note_title(t, "评审群") == "评审群的聊天")
    out = tmp / "vault"
    note_path = wa.export_note(t, out, chat_name="评审群")
    check("导出笔记文件落盘", note_path.exists() and note_path.suffix == ".md")
    check("原始 ZIP 留存到附件", (out / "附件" / "ok.zip").exists())
    check("媒体附件落盘", (out / "附件" / "微信图片_001.jpg").exists()
          and (out / "附件" / "视频.mp4").exists())
    again = wa.export_note(t, out, chat_name="评审群")
    check("重复导出不覆盖（加序号）", again.name != note_path.name, f"{again.name} vs {note_path.name}")

    print("\n4) 接入清单（漏一项发版就会静默失效）")
    mw = read("gui_qt/main_window.py")
    check("main_window 导入页面类", "from gui_qt.pages.wechat_chat_page import WechatChatPage" in mw)
    check("NAV_GROUPS 登记新页", '"微信聊天整理"' in mw.split("_PAGE_CLASS")[0])
    check("_PAGE_CLASS 映射新页", '"微信聊天整理": WechatChatPage' in mw)
    check("_EMPTY_HINTS 登记新页", '"微信聊天整理": [' in mw)

    lic = read("justoneapi_collector/licensing.py")
    check("FEATURES 登记 wechat_chat", '"wechat_chat": "微信聊天整理"' in lic)
    check("FEATURE_BACKFILL 标 auto", '"wechat_chat": "auto"' in lic)

    spec = read("justoneapi_collector.spec")
    check("spec 登记页面 hiddenimport", '"gui_qt.pages.wechat_chat_page"' in spec)
    check("spec 登记核心模块", '"justoneapi_collector.wechat_archive"' in spec)

    guide = read("gui_qt/widgets/guide.py")
    order_block = guide.split("GUIDE_ORDER = [", 1)[1].split("]", 1)[0]
    check("guide 顺序含新页", '"微信聊天整理"' in order_block)
    check("guide 内容含新页", '"微信聊天整理": {' in guide)

    ux = read("tools/_validate_ux.py")
    check("_validate_ux.EXPECTED 含新页", '"微信聊天整理"' in ux)

    pal = read("gui_qt/widgets/command_palette.py")
    aliases = ""
    if '"微信聊天整理": [' in pal:
        aliases = pal.split('"微信聊天整理": [', 1)[1].split("]", 1)[0]
    check("_ALIASES 至少 3 个别名", aliases.count('"') >= 6, aliases[:80])

    src = read("gui_qt/pages/wechat_chat_page.py")
    check("页面 require_feature 在主动作第一行",
          "require_feature(self, \"wechat_chat\"" in src)
    check("按钮用 lambda *_ 接信号", "clicked.connect(lambda *_:" in src)
    check("status 用 setText（不是 set）", "self.status.setText(" in src and ".status.set(" not in src)
    check("列宽走 _tune_tables",
          "def _tune_tables" in src and "self._tune_tables()" in src
          and "hdr.setSectionResizeMode(2, QHeaderView.Stretch)" in src
          and "self.table.setStretchLastSection" not in src)
    check("无裸 connect_guarded 参数误用", "on_error=self._status.set" not in src)
    check("Worker done/error 走 _wire_worker", "_wire_worker(w" in src)
    check("主进程不联网（无 httpx/requests 调用）",
          "httpx" not in src and "requests." not in src and "urlopen" not in src)

    fsync = read("tools/_validate_feature_sync.py")
    check("feature_sync 登记新页功能位", '"微信聊天整理": ("wechat_chat"' in fsync)


print()
print("=" * 62)
print(f"通过 {PASS} 项，失败 {FAIL} 项")
print("全部通过：微信聊天整理解析 / 安全 / 出口 / 清单齐备。" if not FAIL
      else "有失败项，先修好再发版。")
sys.exit(1 if FAIL else 0)
