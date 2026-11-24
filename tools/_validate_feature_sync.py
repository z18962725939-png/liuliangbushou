"""功能位一致性自检（2026-10-12 立）。

【为什么要有这个脚本】
客户在发卡工具里发现「关键词抓取」没有勾选框。根因不是发卡工具漏同步 ——
发卡工具的勾选框是【遍历 licensing.FEATURES 自动生成】的（license_keygen.py），
真正的原因是「关键词抓取」「评论区截流」上线时忘了往 licensing.FEATURES 登记。

所以「新功能上线要记得同步发卡工具」这件事，机器能替人守住。检查四件事：
  1. 导航里每个页面（除设置），是否都登记了功能位
  2. 登记的功能位，是否都在 licensing.FEATURES 里（不在 → 发卡工具里看不到）
  3. 每个功能位是否都定了「老授权码兼容策略」（没定 → 补登记会误伤老客户）
  4. 独立付费模块（deny 策略）是否真有门禁（没门禁 → 假开关，收钱的功能谁都能用）

跑法：C:/Python310/python.exe tools/_validate_feature_sync.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import licensing                      # noqa: E402

# ---------------------------------------------------------------------------
# 导航页面 → (功能位, 页面模块文件名)
# ★ 新增页面必须在这里登记一行，否则本脚本会报「未登记的功能位」。
# ---------------------------------------------------------------------------
# >>> PAGE-REGISTRY BEGIN feature_sync >>>
PAGE_FEATURE: dict[str, tuple[str, str]] = {
    "选题挖掘": ("analyze", "topic_page"),
    "关键词抓取": ("keyword", "keyword_page"),
    "文案仿写": ("script", "script_page"),
    "视频下载": ("download", "video_download_page"),
    "短剧解析": ("drama_parse", "drama_parse_page"),
    "短剧成片": ("drama", "drama_page"),
    "公众号运营": ("wechat_mp", "wechat_mp_page"),
    "小红书笔记": ("xhs_notes", "xhs_notes_page"),
    "竞品体检": ("analyze", "competitor_page"),
    "评论区截流": ("comment", "comment_page"),
    "复盘监控": ("analyze", "watch_page"),
    "直播监控": ("live", "live_page"),
    "GEO 监测": ("geo", "geo_page"),
    "报告": ("report", "report_page"),
    "音视频转文本": ("transcribe", "transcribe_page"),
    "微信聊天整理": ("wechat_chat", "wechat_chat_page"),
}

PAGE_FEATURE_EXTRA: dict[str, str] = {
    "export": "各页面的「导出」按钮共用，无独立页面",
    "collect": "历史授权位：对应页面已下线，保留兼容已发授权码",
    "batch": "历史授权位：对应页面已下线，保留兼容已发授权码",
}

NAV_NO_FEATURE: set[str] = {
    "工作台",
    "视频号解析",
    "数据趋势",
}
# >>> PAGE-REGISTRY END feature_sync <<<
PASSED: list[str] = []
FAILED: list[str] = []

def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")

def nav_pages() -> list[str]:
    """从 main_window.py 里读出导航结构（用 AST，不启动界面）。"""
    src = (ROOT / "gui_qt" / "main_window.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "NAV_GROUPS":
                    groups = ast.literal_eval(node.value)
                    return [p for _g, items in groups for p in items]
    raise RuntimeError("没在 main_window.py 里找到 NAV_GROUPS")

print("1) 导航页面 ↔ 功能位登记")
pages = nav_pages()
check("导航结构读取成功", bool(pages), str(pages))

ui_pages = [p for p in pages if p != "设置" and p not in NAV_NO_FEATURE]
missing = [p for p in ui_pages if p not in PAGE_FEATURE]
check("导航里每个页面都登记了功能位", not missing,
      f"这些页面没登记：{missing}（新页面上线请补进本脚本 PAGE_FEATURE）")

stale = [p for p in PAGE_FEATURE if p not in ui_pages]
check("登记表里没有已下线的页面", not stale, f"这些页面已不在导航里：{stale}")

print()
print("2) 功能位 ↔ licensing.FEATURES（决定发卡工具能不能勾选）")
used = {feat for feat, _mod in PAGE_FEATURE.values()}
undeclared = sorted(f for f in used if f not in licensing.FEATURES)
check("用到的功能位都已在 licensing.FEATURES 注册", not undeclared,
      f"这些位没注册，发卡工具里不会出现勾选框：{undeclared}")

covered = set(used) | set(PAGE_FEATURE_EXTRA)
uncovered = sorted(set(licensing.FEATURES) - covered)
check("FEATURES 里每个位都有页面或被登记为例外", not uncovered,
      f"这些位没有对应页面：{uncovered}（若属跨页面能力，补进 PAGE_FEATURE_EXTRA）")

print()
print("3) 老授权码兼容策略（防止补登记把已售出的功能收回）")
base = set(getattr(licensing, "BASE_FEATURES", ()))
no_policy = sorted(k for k in licensing.FEATURES
                   if k not in base and k not in licensing.FEATURE_BACKFILL)
check("后来新增的功能位都定了 auto / deny 策略", not no_policy,
      f"这些位没定策略：{no_policy}（在 licensing.FEATURE_BACKFILL 里补，"
      "否则加门禁会把已发码的老客户挡在外面）")

bad_base = sorted(k for k in base if k not in licensing.FEATURES)
check("BASE_FEATURES 里的键都在 FEATURES 里", not bad_base, f"脏键：{bad_base}")

bad_policy = sorted(k for k, v in licensing.FEATURE_BACKFILL.items() if v not in ("auto", "deny"))
check("策略取值只有 auto / deny", not bad_policy, f"异常取值：{bad_policy}")

unknown_key = sorted(k for k in licensing.FEATURE_BACKFILL if k not in licensing.FEATURES)
check("策略表里没有脏键", not unknown_key, f"这些键不在 FEATURES 里：{unknown_key}")

print()
print("4) 独立付费模块必须有真门禁（否则是假开关）")
deny_keys = [k for k, v in licensing.FEATURE_BACKFILL.items() if v == "deny"]
check("存在独立付费模块时才有本项检查", True, str(deny_keys))
pages_dir = ROOT / "gui_qt" / "pages"
for key in deny_keys:
    hits = []
    for py in pages_dir.glob("*.py"):
        if re.search(rf'licensing\.check\(\s*["\']{key}["\']', py.read_text(encoding="utf-8")):
            hits.append(py.name)
    check(f"功能位「{key}」在界面里有 licensing.check 门禁", bool(hits),
          "没有任何页面调用 licensing.check，等于这个位不生效（假开关）")

print()
print("5) 发卡工具确实是「自动跟随 FEATURES」")
keygen = (ROOT / "tools" / "license_keygen.py").read_text(encoding="utf-8")
check("发卡工具遍历 licensing.FEATURES 生成勾选框",
      bool(re.search(r"licensing\.FEATURES\.items\(\)", keygen)),
      "没找到遍历逻辑，说明勾选框被写死了，需要手工维护")
check("发卡工具未硬编码功能位清单",
      not re.search(r"数据采集.*批量采集.*视频下载", keygen),
      "发现硬编码的功能位文字，一旦写死就会漏同步")

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过：新功能位与发卡工具已同步。")
