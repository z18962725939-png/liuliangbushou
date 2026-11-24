# -*- coding: utf-8 -*-
"""发版门禁：小红书笔记内容提取页（2026-11-23）。

钉死四类最容易退化的地方：
① okdata 官方 capability 映射错/漏 → 付费请求打到不存在的接口或白花钱；
   尤其钉死「正文走 note-content-v1，绝不碰下载器的 note-details-v6」；
② 付费闸门与 Worker 接线退化 → 免费用户能点付费接口，或点了按钮没反应；
③ 新页注册点漏一处 → 打包后快捷搜索找不到、离线空态没有引导；
④ 假数据渲染 → 正文/评论/联想词上表，主题切换不炸。

全程不联网：接口映射只查表，渲染用假响应对象，只有离屏构造用真 Qt。
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["TRAFFIC_NO_ANIM"] = "1"

PASS = 0
FAIL = 0


def chk(name: str, cond: bool, why: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"OK   {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}" + (f"  — {why}" if why else ""))


print("== 1. okdata 官方接口映射（按 2026-11-23 官方文档核对）==")
from justoneapi_collector.okdata_client import (  # noqa: E402
    OkdataClient, PLATFORM_ALIAS, ENDPOINT_MAP,
)

chk("平台别名 xiaohongshu-rednote → xiaohongshu",
    PLATFORM_ALIAS.get("xiaohongshu-rednote") == "xiaohongshu")
EXPECTED = {
    "note-search-v2": "xiaohongshu.notes.search",
    "user-published-notes-v4": "xiaohongshu.users.notes.list",
    "note-content-v1": "xiaohongshu.notes.get",
    "note-comments-v2": "xiaohongshu.notes.comments.list",
    "topic-notes-v1": "xiaohongshu.topics.notes.list",
    "keyword-related-v1": "xiaohongshu.keywords.related",
}
for ep, cap in EXPECTED.items():
    chk(f"{ep} → {cap}",
        OkdataClient.capability_for("xiaohongshu-rednote", ep) == cap)
chk("★ 正文走 notes.get，绝不映射下载器的 note-details-v6",
    ("xiaohongshu", "note-details-v6") not in ENDPOINT_MAP
    and OkdataClient.capability_for("xiaohongshu-rednote", "note-details-v6") is None)
chk("未登记接口仍明确不支持（不发请求不扣费）",
    OkdataClient.capability_for("xiaohongshu-rednote", "not-real-v1") is None)
q = OkdataClient._query({"keyword": "防晒", "id": "abc", "cursor": "c1",
                         "view": "full", "sort": "popular"})
chk("老参数 keyword → 官方 query", q.get("query") == "防晒")
chk("id/view/sort 白名单透传",
    q.get("id") == "abc" and q.get("view") == "full" and q.get("sort") == "popular")
chk("cursor 首页不传 0 值", "cursor" not in OkdataClient._query({"cursor": 0}))

print("\n== 2. 页面代码规范 ==")
PAGE = "小红书笔记"
page_file = ROOT / "gui_qt" / "pages" / "xhs_notes_page.py"
chk("新页文件存在", page_file.exists())
src = page_file.read_text(encoding="utf-8") if page_file.exists() else ""
try:
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def call_name(call) -> str:
        f = call.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
        return ""

    def first_call_name(fn) -> str:
        """只看函数第一条语句：可以是 require_feature(...) 独立调用，
        也可以是工程标准写法 `if not require_feature(...): return`。"""
        body = list(fn.body)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        if not body:
            return ""
        node = body[0]
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return call_name(node.value)
        if isinstance(node, ast.If):
            test = node.test
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                test = test.operand
            if isinstance(test, ast.Call):
                return call_name(test)
        return ""

    for fname in ("_start", "_read_content"):
        fn = funcs.get(fname)
        chk(f"{fname} 存在", fn is not None)
        if fn is None:
            continue
        chk(f"{fname} 第一行过付费闸门", first_call_name(fn) == "require_feature",
            "主动作第一行必须是 require_feature")
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
        creates = [c for c in calls if isinstance(c.func, ast.Name)
                   and c.func.id == "Worker"]
        starts = [c for c in calls if isinstance(c.func, ast.Attribute)
                  and c.func.attr == "start"]
        chk(f"{fname} 创建 Worker", bool(creates))
        chk(f"{fname} 调 start()（漏了点了没反应）", bool(starts))
    chk("付费闸门写的是 xhs_notes 功能位", 'require_feature(self, "xhs_notes"' in src)
    chk("无裸 connect", ".done.connect(" not in src and ".error.connect(" not in src)
    chk("无 status.set（StatusLine 没这方法，参数求值就抛）", "status.set(" not in src)
    chk("按钮统一走 connect_guarded + lambda *_",
        "lambda *_: slot()" in src and "connect_guarded(btn.clicked" in src)
    chk("成本提示上屏", "$0.02" in src)
    chk("失败原因上屏（不伪装空结果）", "resp.message" in src and '"error"' in src)
    chk("分享链接能解析出笔记 ID", "_note_id_from" in src and "[0-9a-fA-F]{24}" in src)
except SyntaxError as exc:
    chk("AST 解析", False, str(exc))

print("\n== 2b. 内容提取健壮性（provider 守卫 / 正文字段兼容）==")
chk("两个付费入口都有 okdata provider 守卫",
    src.count('getattr(client, "provider", "") != "okdata"') >= 2)
chk("provider 不符时提示切回 okdata", "这两个功能仅支持 okdata 通道" in src)
okc_src = (ROOT / "justoneapi_collector" / "okdata_client.py").read_text(encoding="utf-8")
chk("正文兼容链 text>content>body>desc",
    'obj.get("text") or obj.get("content") or obj.get("body")' in okc_src
    and 'or obj.get("desc")' in okc_src
    and '_set(d, "text", text)' in okc_src)
chk("余额不足提示带精确充值地址", "OkdataClient.recharge_hint" in okc_src)
chk("空结果明示已计费", "_BILLED_EMPTY" in src and "（本次查询已计费）" in src)

print("\n== 3. 接入清单（漏一处打包后静默失效）==")
def read(rel: str) -> str:
    try:
        return (ROOT / rel).read_text(encoding="utf-8")
    except OSError:
        return ""

main_src = read("gui_qt/main_window.py")
home_src = read("gui_qt/pages/home_page.py")
pal_src = read("gui_qt/widgets/command_palette.py")
icon_src = read("gui_qt/icons.py")
guide_src = read("gui_qt/widgets/guide.py")
spec_src = read("justoneapi_collector.spec")
fsync_src = read("tools/_validate_feature_sync.py")
ux_src = read("tools/_validate_ux.py")
lic_src = read("justoneapi_collector/licensing.py")
nav_part = main_src.split("NAV_GROUPS = [")[1].split("FREE_NAV_GROUPS")[0]
free_part = main_src.split("FREE_NAV_GROUPS = [")[1].split("if branding")[0]

chk("NAV_GROUPS 有入口", f'"{PAGE}"' in nav_part)
chk("_PAGE_CLASS 有映射", f'"{PAGE}": XhsNotesPage' in main_src)
chk("import 页面类", "from gui_qt.pages.xhs_notes_page import XhsNotesPage" in main_src)
hints = main_src.split(f'"{PAGE}": [')[1].split("],")[0] if f'"{PAGE}": [' in main_src else ""
chk("_EMPTY_HINTS 有五条空态",
    all(x in hints for x in ("_fill_search_demo", "_goto_user", "_goto_content",
                             "_fill_topic_demo")) and hints.count('("') >= 5, hints[:200])
chk("工作台首页有入口卡", f'("{PAGE}",' in home_src)
chk("快捷搜索别名≥3个", f'"{PAGE}": [' in pal_src
    and len(pal_src.split(f'"{PAGE}": [')[1].split("]")[0].split(",")) >= 3)
chk("导航图标已登记", f'"{PAGE}":' in icon_src)
chk("GUIDE_ORDER 有条目", f'"{PAGE}"' in guide_src)
chk("GUIDE_CONTENT 有条目", f'"{PAGE}": {{' in guide_src)
chk("打包 hiddenimports 有页面", "gui_qt.pages.xhs_notes_page" in spec_src)
chk("功能位登记 FEATURES", '"xhs_notes": "小红书笔记"' in lic_src)
chk("老码兼容 BACKFILL", '"xhs_notes": "auto"' in lic_src)
chk("feature_sync 登记页面", f'"{PAGE}": ("xhs_notes"' in fsync_src)
chk("UX 门禁钉住入口", f'"{PAGE}"' in ux_src)
chk("免费版导航未加回入口（客户铁律）", f'"{PAGE}"' not in free_part)
chk("发版链已挂本门禁", "_validate_xhs_notes.py" in read("tools/release_all.py"))

print("\n== 4. 离屏构造 + 假数据渲染 ==")
try:
    from PySide6.QtWidgets import QApplication, QTableView
    app = QApplication.instance() or QApplication([])
    from gui_qt.pages._base import AppContext
    from gui_qt.pages.xhs_notes_page import XhsNotesPage

    class Resp:
        success = True
        message = ""

        def __init__(self, data):
            self.data = data

    ctx = AppContext()
    page = XhsNotesPage(ctx)
    page.resize(1180, 760)
    tables = page.findChildren(QTableView)
    chk("五张结果表都在", len(tables) == 5, f"实际 {len(tables)} 张")
    chk("四个工作区按钮都在", len(page._mode_btns) == 4)

    page._on_search({"resp": Resp({"items": [
        {"id": "6650a1b2000000001e00abcd", "title": "防晒测评",
         "author": {"name": "测评君"}, "published_at": "2026-11-23T08:00:00Z",
         "metrics": {"likes": 1000, "collects": 200, "comments": 30},
         "url": "https://www.xiaohongshu.com/explore/6650a1b2000000001e00abcd"}
    ], "page": {"next_cursor": "c2", "has_more": True}}), "params": {}})
    chk("搜笔记结果上表", page.search_table.rowCount() == 1
        and page.search_table.item(0, 1).text() == "防晒测评")
    chk("搜索翻页游标已记住", page._cursor["search"] == "c2"
        and page.next_search_btn.isEnabled())
    chk("收藏数列渲染", page.search_table.item(0, 4).text() == "200")

    page._on_user_notes({"resp": Resp({"items": [
        {"id": "n2", "title": "博主笔记一", "metrics": {"likes": 5}},
    ]}), "params": {}})
    chk("博主笔记上表", page.user_notes_table.rowCount() == 1)

    page._on_content({
        "content": Resp({"items": [{
            "id": "6650a1b2000000001e00abcd", "title": "防晒全文", "text": "笔记正文内容",
            "author": {"name": "测评君"}, "url": "https://www.xiaohongshu.com/explore/x",
            "metrics": {"likes": 2000, "collects": 400, "comments": 50},
        }]}),
        "comments": Resp({"items": [{"text": "好用", "author": {"name": "读者"},
                                      "metrics": {"likes": 2},
                                      "created_at": "2026-11-23T09:00:00Z"}]}),
        "id": "6650a1b2000000001e00abcd",
    })
    chk("正文渲染", "笔记正文内容" in page.body_edit.toPlainText())
    chk("互动数字渲染", "2,000" in page.metric_label.text())
    chk("评论上表", page.comment_table.rowCount() == 1)

    page._on_topic_notes({"resp": Resp({"items": [
        {"id": "t1", "title": "露营笔记", "metrics": {"likes": 9}},
    ]})})
    chk("话题笔记上表", page.topic_table.rowCount() == 1)

    page._on_keywords({"resp": Resp({"items": ["露营装备", "露营帐篷", "露营灯"]})})
    chk("联想词上表", page.keyword_table.rowCount() == 3
        and page.keyword_table.item(1, 1).text() == "露营帐篷")

    chk("分享链接取 ID", XhsNotesPage._note_id_from(
        "https://www.xiaohongshu.com/explore/6650a1b2000000001e00abcd")
        == "6650a1b2000000001e00abcd")

    for mode in ("light", "dark"):
        page.apply_theme(mode)
        chk(f"{mode} 主题无异常", True)
except Exception as exc:                                  # noqa: BLE001
    chk("离屏构造与渲染", False, f"{type(exc).__name__}: {exc}")

print("\n" + "=" * 60)
print(f"通过 {PASS} 项，失败 {FAIL} 项")
if FAIL:
    print("小红书笔记门禁未通过，发版中止。")
    sys.exit(1)
print("全部通过：小红书笔记内容提取页接通正常。")
