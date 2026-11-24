# -*- coding: utf-8 -*-
"""发版门禁：公众号运营页（2026-11-23）。

钉死三类最容易退化的地方：
① okdata 官方 capability 映射错/漏 → 付费请求打到不存在的接口或白花钱；
② 付费闸门与 Worker 接线退化 → 免费用户能点付费接口，或点了按钮没反应；
③ 新页注册点漏一处 → 打包后快捷搜索找不到、离线空态没有引导。

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
from justoneapi_collector.okdata_client import OkdataClient, PLATFORM_ALIAS  # noqa: E402

chk("平台别名 wechat-official-accounts → wechat_mp",
    PLATFORM_ALIAS.get("wechat-official-accounts") == "wechat_mp")
EXPECTED = {
    "account-search-v1": "wechat_mp.accounts.search",
    "account-historical-articles-v1": "wechat_mp.accounts.articles.list",
    "account-historical-articles-v2": "wechat_mp.accounts.articles.list",
    "article-details-v1": "wechat_mp.articles.get",
    "article-metrics-v1": "wechat_mp.articles.metrics",
    "article-comments-v1": "wechat_mp.articles.comments.list",
    "article-search-v1": "wechat.posts.search",
}
for ep, cap in EXPECTED.items():
    chk(f"{ep} → {cap}",
        OkdataClient.capability_for("wechat-official-accounts", ep) == cap)
chk("未登记接口仍明确不支持（不发请求不扣费）",
    OkdataClient.capability_for("wechat-official-accounts", "not-real-v1") is None)
q = OkdataClient._query({"keyword": "健身", "id": "gh_abc", "tab": "articles",
                         "published_within": "week", "type": "article",
                         "cursor": "c1", "view": "brief"})
chk("老参数 keyword → 官方 query", q.get("query") == "健身")
chk("栏目/时间窗/类型白名单透传",
    q.get("tab") == "articles" and q.get("published_within") == "week"
    and q.get("type") == "article")
chk("cursor 首页不传 0 值", "cursor" not in OkdataClient._query({"cursor": 0}))

print("\n== 2. 页面代码规范 ==")
PAGE = "公众号运营"
page_file = ROOT / "gui_qt" / "pages" / "wechat_mp_page.py"
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
        # docstring 不算「第一行动作」，跳过它再看闸门
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

    for fname in ("_start", "_diagnose"):
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
    chk("付费闸门写的是 wechat_mp 功能位", 'require_feature(self, "wechat_mp"' in src)
    chk("无裸 connect", ".done.connect(" not in src and ".error.connect(" not in src)
    chk("无 status.set（StatusLine 没这方法，参数求值就抛）", "status.set(" not in src)
    chk("按钮统一走 connect_guarded + lambda *_",
        "lambda *_: slot()" in src and "connect_guarded(btn.clicked" in src)
    chk("成本提示上屏", "$0.058" in src and "$0.02" in src)
    chk("失败原因上屏（不伪装空结果）", "resp.message" in src and '"error"' in src)
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
chk("_PAGE_CLASS 有映射", f'"{PAGE}": WechatMpPage' in main_src)
chk("import 页面类", "from gui_qt.pages.wechat_mp_page import WechatMpPage" in main_src)
chk("_EMPTY_HINTS 有四条空态", f'"{PAGE}": [' in main_src
    and all(x in main_src.split(f'"{PAGE}": [')[1].split("],")[0]
            for x in ("_fill_demo", "_goto_accounts", "_goto_article", "_fill_search_demo")))
chk("工作台首页有入口卡", f'("{PAGE}",' in home_src)
chk("快捷搜索别名≥3个", f'"{PAGE}": [' in pal_src
    and len(pal_src.split(f'"{PAGE}": [')[1].split("]")[0].split(",")) >= 3)
chk("导航图标已登记", f'"{PAGE}":' in icon_src)
chk("GUIDE_ORDER 有条目", f'"{PAGE}"' in guide_src)
chk("GUIDE_CONTENT 有条目", f'"{PAGE}": {{' in guide_src)
chk("打包 hiddenimports 有页面", "gui_qt.pages.wechat_mp_page" in spec_src)
chk("功能位登记 FEATURES", '"wechat_mp": "公众号运营"' in lic_src)
chk("老码兼容 BACKFILL", '"wechat_mp": "auto"' in lic_src)
chk("feature_sync 登记页面", f'"{PAGE}": ("wechat_mp"' in fsync_src)
chk("UX 门禁钉住入口", f'"{PAGE}"' in ux_src)
chk("免费版导航未加回入口（客户铁律）", f'"{PAGE}"' not in free_part)
chk("发版链已挂本门禁", "_validate_wechat_mp.py" in read("tools/release_all.py"))

print("\n== 4. 离屏构造 + 假数据渲染 ==")
try:
    from PySide6.QtWidgets import QApplication, QTableView
    app = QApplication.instance() or QApplication([])
    from gui_qt.pages._base import AppContext
    from gui_qt.pages.wechat_mp_page import WechatMpPage

    class Resp:
        success = True
        message = ""

        def __init__(self, data):
            self.data = data

    ctx = AppContext()
    page = WechatMpPage(ctx)
    page.resize(1180, 760)
    tables = page.findChildren(QTableView)
    chk("四张结果表都在", len(tables) == 4, f"实际 {len(tables)} 张")
    chk("四个工作区按钮都在", len(page._mode_btns) == 4)

    page._on_accounts({"resp": Resp({"items": [
        {"id": "gh_abc", "name": "测试号", "username": "test_mp",
         "verified": True, "posts": 12, "bio": "简介"}
    ]})})
    chk("搜号结果上表", page.account_table.rowCount() == 1
        and page.account_table.item(0, 2).text() == "gh_abc")

    page._on_articles({"resp": Resp({"items": [
        {"id": "a1", "title": "文章一", "published_at": "2026-11-23T08:00:00Z",
         "author": {"name": "测试号"}, "url": "https://mp.weixin.qq.com/s/abcd",
         "metrics": {"views": 1000}}
    ], "page": {"next_cursor": "c2", "has_more": True}}), "params": {}})
    chk("历史文章上表", page.article_table.rowCount() == 1)
    chk("翻页游标已记住", page._cursor["article"] == "c2"
        and page.next_article_btn.isEnabled())

    page._on_search({"resp": Resp({"items": [
        {"id": "s1", "type": "article", "title": "选题一",
         "published_at": "2026-11-23T08:00:00Z", "url": "https://mp.weixin.qq.com/s/efgh"}
    ]}), "params": {}})
    chk("搜一搜结果上表", page.search_table.rowCount() == 1)

    page._on_diag({
        "detail": Resp({"items": [{"title": "诊断文章", "text": "正文内容",
                                    "author": {"name": "测试号"},
                                    "url": "https://mp.weixin.qq.com/s/abcd"}]}),
        "metrics": Resp({"items": [{"metrics": {"views": 2000, "likes": 30,
                                                 "comments": 4, "shares": 5,
                                                 "favorites": 6, "recommends": 7}}]}),
        "comments": Resp({"items": [{"text": "好文", "author": {"name": "读者"},
                                      "metrics": {"likes": 2},
                                      "created_at": "2026-11-23T09:00:00Z"}]}),
        "url": "https://mp.weixin.qq.com/s/abcd",
    })
    chk("正文渲染", "正文内容" in page.body_edit.toPlainText())
    chk("互动数字渲染", "2,000" in page.metric_label.text())
    chk("评论上表", page.comment_table.rowCount() == 1)

    for mode in ("light", "dark"):
        page.apply_theme(mode)
        chk(f"{mode} 主题无异常", True)
except Exception as exc:                                  # noqa: BLE001
    chk("离屏构造与渲染", False, f"{type(exc).__name__}: {exc}")

print("\n" + "=" * 60)
print(f"通过 {PASS} 项，失败 {FAIL} 项")
if FAIL:
    print("公众号运营门禁未通过，发版中止。")
    sys.exit(1)
print("全部通过：公众号运营页接通正常。")
