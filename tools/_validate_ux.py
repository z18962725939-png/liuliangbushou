"""界面体验自检（2026-10-14 立）。

【为什么要有这个脚本】
这次改版做了三件容易"改回去"的事：
  1. 把默认页从「数据采集」（开发者调接口的页面）换成「工作台」；
  2. 把导航按使用频率重排，低频功能收进「更多工具」；
  3. 把「找对标 → 扒文案」用「写同款文案」按钮串起来，并把
     仿写页 20 多个参数收进「高级选项」。

这些约束没有编译期保护：以后随手往常用分组里塞一个页面、
或者把默认页改回数据采集，程序照跑不误，但体验会退回去。
所以用这个脚本钉住，发版前跑一遍。

跑法：C:/Python310/python.exe tools/_validate_ux.py
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
print("1) 导航结构与默认页")

mw = read("gui_qt/main_window.py")
tree = ast.parse(mw)
nav_groups = None
page_class: dict = {}
for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "NAV_GROUPS":
                nav_groups = ast.literal_eval(node.value)
            if isinstance(t, ast.Name) and t.id == "_PAGE_CLASS":
                # 值是类名（ast.Name），literal_eval 会失败 —— 只取键就够了
                page_class = {k.value: None for k in node.value.keys}

check("能读到 NAV_GROUPS", bool(nav_groups), "没找到导航定义")

groups = {g: items for g, items in (nav_groups or [])}
all_pages = [p for _g, items in (nav_groups or []) for p in items]
first_group_first_page = all_pages[0] if all_pages else ""

check("第一个导航项是「工作台」（打开软件第一眼是总览，不是接口调试器）",
      first_group_first_page == "工作台", f"实际是「{first_group_first_page}」")

check("默认页取自导航第一项（_build_ui 里用 first_name 初始化）",
      "self._switch(first_name)" in mw,
      "找不到初始跳转，默认页可能被写死成别的")

freq = groups.get("常用", [])
for must in ("选题挖掘", "关键词抓取", "文案仿写"):
    check(f"高频功能「{must}」在「常用」分组里", must in freq, str(freq))

for low in ("数据采集", "批量采集", "直播监控", "GEO 监测", "报告"):
    check(f"低频功能「{low}」已收进「更多工具」（不再占前排）",
          low in groups.get("更多工具", []) and low not in freq,
          f"常用={freq} 更多={groups.get('更多工具')}")

check("工作台在 _PAGE_CLASS 里有页面类", "工作台" in page_class,
      str(sorted(page_class))[:120])
check("所有导航项都有对应页面类",
      all(p in page_class for p in all_pages),
      f"缺页面：{[p for p in all_pages if p not in page_class]}")

# ---------------------------------------------------------------------------
print()
print("2) 功能一个都没丢（改导航不能弄丢入口）")

EXPECTED = ["数据采集", "关键词抓取", "批量采集", "视频下载", "直播监控",
            "选题挖掘", "竞品体检", "评论区截流", "GEO 监测", "复盘监控",
            "报告", "文案仿写", "音视频转文本", "数据趋势", "设置",
            "短剧成片", "微信聊天整理", "视频号解析"]
missing = [p for p in EXPECTED if p not in all_pages]
check("原有入口全在导航里（一个都没丢）", not missing, f"丢了：{missing}")
check("导航项没有重复", len(all_pages) == len(set(all_pages)),
      f"重复项：{[p for p in all_pages if all_pages.count(p) > 1]}")

# ---------------------------------------------------------------------------
print()
print("3) 工作台页面")

home = read("gui_qt/pages/home_page.py")
check("工作台含状态区（授权 / 额度 / 登录三张指标卡）",
      all(k in home for k in ("授权状态", "今日额度", "抖音登录")),
      "三张状态卡不全")
check("工作台读真实额度与授权（不是写死的假数据）",
      "quota.summary()" in home and "licensing.status()" in home,
      "没读到限额或授权接口")
check("工作台能跟着主题换色（apply_theme）", "def apply_theme" in home, "")
check("工作台切回时自动刷新状态（showEvent → refresh）",
      "def showEvent" in home and "self.refresh()" in home, "")
check("工作台有跳转入口（不依赖写死的索引）",
      "def _goto" in home and "switch_to" in mw, "")

# ---------------------------------------------------------------------------
print()
print("4) 跨页链路：找对标 → 扒文案")

base = read("gui_qt/pages/_base.py")
check("_base 提供 send_to_script 跳转函数", "def send_to_script" in base, "")
check("_base 提供结果表取链接函数", "def selected_row_links" in base, "")
check("send_to_script 会切页（不是只填数据不动界面）",
      "switch_to" in base, "")

for page, label in (("gui_qt/pages/topic_page.py", "选题挖掘"),
                    ("gui_qt/pages/keyword_page.py", "关键词抓取")):
    src = read(page)
    check(f"{label}页有「写同款文案」按钮", "写同款文案" in src, "")
    check(f"{label}页接了 send_to_script", "send_to_script(" in src, "")

sp = read("gui_qt/pages/script_page.py")
check("文案仿写页提供 accept_links 接收外部链接", "def accept_links" in sp, "")

# ---------------------------------------------------------------------------
print()
print("5) 结果可视化与参数精简")

stat = read("gui_qt/widgets/stat.py")
for cls, label in (("class StatCard", "指标卡"), ("class UsageBar", "额度条"),
                   ("class TopBars", "条形榜"), ("class ActionCard", "功能卡")):
    check(f"可视化组件{label}存在", cls in stat, "")

# 注意看的是「真的 import 了」而不是文中出现过这个词 ——
# stat.py 的注释里就写着"为什么不引 matplotlib"，按字样判会误报。
_bad_imports = [m for m in ("matplotlib", "pyqtgraph", "seaborn", "plotly")
                if re.search(rf"^\s*(?:import|from)\s+{m}", stat, re.M)]
check("可视化不依赖额外绘图库（只用 PySide6 自绘）", not _bad_imports,
      f"引入了图表库：{_bad_imports}（打包体积和兼容性都会受影响）")
check("大数字做了中文可读处理（1.2万 / 1.3亿）",
      "亿" in stat and "万" in stat, "")

check("文案仿写页有「高级选项」折叠区", "CollapseBox" in sp and "高级选项" in sp, "")
check("折叠区默认收起（不把表单全摊在首屏）",
      "collapsed: bool = True" in base and "class CollapseBox" in base, "")
check("仿写页用默认收起方式建折叠区（没传 collapsed=False）",
      re.search(r"CollapseBox\((?!.*collapsed=False)", sp) is not None, "")
# ★ 2026-10-17 三次改版：目标时长从下拉框（dur_box）换成刻度尺（dur_ruler），
#   断言的键名要跟着改 —— 否则"这几项必须留在主区"就没人看着了。
for keep in ("genre_box", "dur_ruler", "product", "points"):
    check(f"核心必填项「{keep}」留在主区（没被折进去）", keep in sp, "")

for page, label in (("gui_qt/pages/topic_page.py", "选题挖掘"),
                    ("gui_qt/pages/keyword_page.py", "关键词抓取")):
    src = read(page)
    check(f"{label}页有结果概览（指标卡 + 条形榜）",
          "StatCard(" in src and "TopBars(" in src, "")

# ---------------------------------------------------------------------------
print()
print("6) 教程与自检文案同步")

guide = read("gui_qt/widgets/guide.py")
check("教程里新增「工作台」条目", '"工作台": {' in guide, "")
check("教程提到「写同款文案」捷径", "写同款文案" in guide, "")
check("教程里所有导航页面都有条目",
      all(f'"{p}": {{' in guide for p in EXPECTED),
      f"缺教程：{[p for p in EXPECTED if chr(34)+p+chr(34)+': {' not in guide]}")

# ---------------------------------------------------------------------------
# ★ StatCard.set_value 只有 (value, sub, tone) 三个位置参数。
#   2026-10-16 发免费版时撞见：首页四个状态分支里有三个传了四个参数，
#   平时走不到，一旦客户试用到期或授权过期 —— 打开首页直接 TypeError 白屏。
#   这种"分支里的参数错误"编译期查不出来，只能靠静态扫。
#   ★ 2026-10-19 客户现场报障后扩大扫描范围：原来只扫 home_page.py，
#     别的页面照样可能犯同样的错（而且首页那次已经证明，只在特定
#     授权状态下才走到的分支最容易漏）。现在扫全部页面。
#     阈值取「位置参数 > 3」：StatCard 是 3 个上限，MetricCell /
#     RulerSlider 的位置参数上限更低（2 个），所以这个阈值对它们同样安全。
print()
print("状态卡调用参数数（多传会白屏）")
_bad: list[str] = []
for _p in sorted((ROOT / "gui_qt" / "pages").glob("*.py")):
    try:
        _tree = ast.parse(_p.read_text(encoding="utf-8"))
    except SyntaxError as _e:
        _bad.append(f"{_p.name}: 语法错误 {_e}")
        continue
    for _n in ast.walk(_tree):
        if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
                and _n.func.attr == "set_value" and len(_n.args) > 3):
            _bad.append(f"{_p.name}:{_n.lineno}")
check("★ 所有页面 set_value 位置参数不超过 3 个", not _bad, f"越界的位置：{_bad}")

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过：界面体验约束未被破坏。")
