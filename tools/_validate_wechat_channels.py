# -*- coding: utf-8 -*-
"""发版门禁：视频号解析页（2026-11-20）。

新页面最容易出的两类退化：① 注册点漏一处 → 打包后点开是占位页/快捷搜索找不到；
② Worker 建了却忘 .start() → 点按钮毫无反应（编译、import 都查不出来，唯有
真跑一次才现形）。本门禁把这两类钉死，并核住「免费链路不许混付费」。

不联网：解析/下载用假对象，只有离屏页面构造用真 Qt。
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TRAFFIC_NO_ANIM"] = "1"

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


PAGE_FILE = ROOT / "gui_qt" / "pages" / "wechat_channels_page.py"
PAGE = "视频号解析"

print("== 1. 页面文件与解析链路 ==")
chk("新页文件存在", PAGE_FILE.exists())
src = PAGE_FILE.read_text(encoding="utf-8") if PAGE_FILE.exists() else ""

chk("走免费 media_parser.parse", "media_parser.parse(" in src,
    "必须复用 video.zacao.top 免费链路")
chk("不调 JustOneAPI client", "cli.request(" not in src and "client.request(" not in src
    and "JustOneAPIClient(" not in src, "付费解析与免费解析不许混")
# 只看「真实会被执行的内容」：AST 里的函数调用名 + 非 docstring 字符串常量。
# 顶部说明里写「不接付费闸门 require_feature」「不许出现欠费字眼」是注释和
# 模块 docstring，纯字符串匹配会把它们误伤成违规。
parsed = ast.parse(src)
calls_named = {c.func.id for c in ast.walk(parsed)
               if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
docstrings = {id(n.body[0].value) for n in ast.walk(parsed)
              if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                ast.AsyncFunctionDef))
              and n.body and isinstance(n.body[0], ast.Expr)
              and isinstance(n.body[0].value, ast.Constant)
              and isinstance(n.body[0].value.value, str)}
live_strings = [n.value for n in ast.walk(parsed)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstrings]
chk("不接付费闸门 require_feature", "require_feature" not in calls_named,
    "免费能力不占功能位，不应弹「需要激活」")
# 界面文案不许把免费链路跟额度/欠费挂钩（只看会显示给客户的字符串）
for word in ("欠费", "额度不足", "次数用完", "余额不足"):
    chk(f"文案无「{word}」", not any(word in s for s in live_strings),
        "media_parser 家规：免费链路不许出现额度/欠费字眼")
chk("互动数据不编造（页面明确不展示点赞评论转发数字）",
    "digg_count" not in src and "comment_count" not in src,
    "付费接口才有的互动数字不应出现在免费页")

print("\n== 2. Worker 接线（点了必须有反应）==")
try:
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for fname in ("_parse", "_download_selected"):
        fn = funcs.get(fname)
        chk(f"{fname} 存在", fn is not None)
        if fn is None:
            continue
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
        creates = [c for c in calls if isinstance(c.func, ast.Name)
                   and c.func.id == "Worker"]
        starts = [c for c in calls if isinstance(c.func, ast.Attribute)
                  and c.func.attr == "start"
                  and isinstance(c.func.value, ast.Name)]
        chk(f"{fname} 创建了 Worker", bool(creates))
        chk(f"{fname} 调用了 w.start()（漏了点了没反应）", bool(starts))
    chk("逐条完成信号已接线", "w.item_done" in src and "self._on_item" in src)
except SyntaxError as exc:
    chk("AST 解析", False, str(exc))

print("\n== 3. 全部注册点（漏一个发版中止）==")
main_src = (ROOT / "gui_qt" / "main_window.py").read_text(encoding="utf-8")
home_src = (ROOT / "gui_qt" / "pages" / "home_page.py").read_text(encoding="utf-8")
pal_src = (ROOT / "gui_qt" / "widgets" / "command_palette.py").read_text(encoding="utf-8")
icon_src = (ROOT / "gui_qt" / "icons.py").read_text(encoding="utf-8")
guide_src = (ROOT / "gui_qt" / "widgets" / "guide.py").read_text(encoding="utf-8")
spec_src = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
fsync_src = (ROOT / "tools" / "_validate_feature_sync.py").read_text(encoding="utf-8")
ux_src = (ROOT / "tools" / "_validate_ux.py").read_text(encoding="utf-8")

chk("NAV_GROUPS 有入口", f'"{PAGE}"' in main_src.split("NAV_GROUPS = [")[1].split("FREE_NAV_GROUPS")[0])
chk("_PAGE_CLASS 有映射", f'"{PAGE}": WechatChannelsPage' in main_src)
chk("import 页面类", "from gui_qt.pages.wechat_channels_page import" in main_src)
chk("_EMPTY_HINTS 有空态引导", f'"{PAGE}": [' in main_src and "_fill_demo" in main_src)
chk("工作台首页有入口卡", f'("{PAGE}",' in home_src)
chk("快捷搜索别名≥3个",
    f'"{PAGE}": [' in pal_src and
    len(pal_src.split(f'"{PAGE}": [')[1].split("]")[0].split(",")) >= 3)
chk("导航图标已登记", f'"{PAGE}":' in icon_src)
chk("引导教程 GUIDE_ORDER 有条目", f'"{PAGE}"' in guide_src)
chk("引导内容 GUIDE_CONTENT 有条目", f'"{PAGE}": {{' in guide_src)
chk("打包 hiddenimports 有页面", "gui_qt.pages.wechat_channels_page" in spec_src)
chk("免功能位登记（免费能力）", f'"{PAGE}"' in fsync_src and "NAV_NO_FEATURE" in fsync_src)
chk("UX 门禁清单钉住入口", f'"{PAGE}"' in ux_src)
chk("免费版导航未加回入口（客户铁律）",
    f'"{PAGE}"' not in main_src.split("FREE_NAV_GROUPS = [")[1].split("if branding")[0])

print("\n== 4. 离屏构造 + 真链接解析冒烟 ==")
try:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gui_qt.pages._base import AppContext
    from gui_qt.pages.wechat_channels_page import WechatChannelsPage
    ctx = AppContext()
    ctx.connect_real_client()
    page = WechatChannelsPage(ctx)
    page.resize(1180, 760)
    chk("页面可构造", page.table.columnCount() == 6)
    chk("有空态引导回调 _fill_demo", callable(getattr(page, "_fill_demo", None)))
    # 真解析（免费链路，network 通就验数据，不通也不算功能失败——门禁只验接通）
    from justoneapi_collector import media_parser
    data, reason = media_parser.parse("https://weixin.qq.com/sph/AsFrSACTaL")
    got_author = bool(data and data.get("author"))
    chk("真实视频号链接可解析出作者", got_author, reason[:80] if not got_author else "")
    if got_author:
        page._on_item({
            "link": "https://weixin.qq.com/sph/AsFrSACTaL",
            "author": data.get("author", ""), "title": data.get("title", ""),
            "video_id": data.get("video_id", ""),
            "platform_name": data.get("platform_name", ""),
            "cover_url": data.get("cover_url", ""),
            "video_url": data.get("video_url", ""),
        })
        chk("解析结果正确入表", page.table.rowCount() == 1
            and page.table.item(0, 0).text() == data["author"])
        page.table.selectRow(0)                    # 模拟客户点选这一行
        chk("复制直链有内容", bool(page._selected_url()))
    page.apply_theme("dark")
    page.apply_theme("light")
    chk("双主题切换不崩", True)
except Exception as exc:                                   # noqa: BLE001
    import traceback
    traceback.print_exc()
    chk("离屏冒烟", False, f"{type(exc).__name__}: {exc}")

print("\n== 5. 下载目录类型与失败原因（2026-11-21 客户反馈回归）==")
chk("保存目录先转 Path 再交给下载器",
    "Path(save_text).expanduser()" in src and "downloader.save_dir = save_dir" in src,
    "QLineEdit 的文本是 str，下载器内部按 Path 拼接，误传会全军覆没")
chk("Worker 不再把字符串目录传给后台",
    "Worker(_download_task, downloader, vids)" in src)
chk("下载失败原因会上屏", "失败原因：" in src)
dl_src = (ROOT / "justoneapi_collector" / "video_downloader.py").read_text(encoding="utf-8")
chk("下载器兜底把字符串目录转 Path", "if not isinstance(self.save_dir, Path):" in dl_src)
chk("全失败路径返回真实原因（不吞错误）", 'return None, "；".join(uniq)' in dl_src)
chk("视频号过期直链走免费重解析", 'plat in ("weixin_channels", "weixin_mp")' in dl_src)
try:
    page._on_downloaded([(object(), None, "模拟失败：HTTP 404")])
    chk("失败原因显示在状态栏",
        "失败原因：模拟失败：HTTP 404" in page.status.text(),
        page.status.text()[:100])
except Exception as exc:                                   # noqa: BLE001
    chk("失败原因上屏冒烟", False, f"{type(exc).__name__}: {exc}")

print(f"\n==== 视频号解析门禁：{_PASSED}/{_PASSED + _FAILED} ====")
sys.exit(1 if _FAILED else 0)
