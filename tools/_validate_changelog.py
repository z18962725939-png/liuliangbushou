# -*- coding: utf-8 -*-
"""自检：更新日志（32 项）—— 数据包 / 安装包 / 官网三处是否同源、是否都还在。

为什么单独守一份自检（2026-10-15）：
    这三处错任何一处**都不会报错**，只是客户默默拿到一份空日志、或一屏问号：
      · 日志在打包之后才生成  → 客户装到的是上一版内容
      · 安装向导的提示文本没带 UTF-8 编码标记 → 中文全变问号
      · [Run] 里写了 checked         → 安装脚本直接编译失败（[Run] 没这个标志）
      · 静默更新没跳过              → 客户每次自动更新都被强行弹个浏览器窗口
      · 官网自己抄了一份数据        → 两边说法不一致

    python tools/_validate_changelog.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "installer_output"
STANDALONE = OUT / "standalone"
CHANGELOG_JSON = OUT / "changelog.json"
VERSION_JSON = OUT / "version.json"
LOCAL_HTML = OUT / "更新日志.html"
RECENT_TXT = OUT / "changelog_recent.txt"
ISS = ROOT / "installer.iss"
SITE = ROOT / "tools" / "build_site.py"
RELEASE_ALL = ROOT / "tools" / "release_all.py"
PUBLISH_PAGE = ROOT / "tools" / "publish_download_page.py"

sys.path.insert(0, str(ROOT / "tools"))

PASS: list[str] = []
FAIL: list[str] = []


def chk(cond, name: str, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + (("  -- " + str(detail)) if (detail and not cond) else ""))
    return bool(cond)


def sec(title: str) -> None:
    print("\n" + title)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:                                   # noqa: BLE001
        return ""


def vkey(ver: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", ver)) or (0,)


def _days(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except Exception:                                   # noqa: BLE001
        return 9999


print("=" * 70)
print("自检：更新日志（数据源 / 安装包 / 官网）")
print("=" * 70)

# --------------------------------------------------------- 1. 数据源（6）
sec("【1】数据源 changelog.json")
if not CHANGELOG_JSON.exists():
    chk(False, "存在", "先跑 tools/build_changelog.py")
    print("\n没过：数据源都没有，后面无从检查。")
    sys.exit(1)
raw = read(CHANGELOG_JSON)
try:
    data = json.loads(raw)
    ok_json = True
except Exception as exc:                                # noqa: BLE001
    ok_json = False
    data = {}
    print("解析失败：" + str(exc))
chk(ok_json, "是合法的 JSON", "解析不了")
rels: list[dict] = data.get("releases") or []
chk(len(rels) >= 2, f"收录了多个版本（{len(rels)} 个）", "只有一两条")
chk(all(r.get("version") and r.get("date") and r.get("notes") for r in rels),
    "每条都有版本 / 日期 / 说明", "有字段是空的")
vers = [r["version"] for r in rels]
chk(vers == sorted(vers, key=vkey, reverse=True), "按版本号倒序（第一条就是最新）", vers[:4])
chk(len(vers) == len(set(vers)), "没有重复版本", "有重复")
chk(all(re.match(r"^\d+\.\d+\.\d+$", v) for v in vers), "版本号格式规范", vers[:3])

# ------------------------------------------------ 2. 与 version.json 对齐（4）
sec("【2】与安装包版本对齐")
vj: dict = json.loads(read(VERSION_JSON)) if VERSION_JSON.exists() else {}
chk(bool(vj), "version.json 存在", "先跑一次发版")
if vj:
    sys.path.insert(0, str(ROOT / "tools"))
    import build_changelog as bclog                     # noqa: E402
    chk(rels[0]["version"] == vj.get("version"),
        "日志最新版 = 安装包版本", (rels[0]["version"], vj.get("version")))
    chk(bclog._clean_notes(rels[0]["notes"]) == bclog._clean_notes(vj.get("notes", "")),
        "最新版说明与更新弹窗里的一致", rels[0]["notes"][:30])
    chk(_days(rels[0]["date"], vj.get("published", "")) <= 1,
        "日期与发布日一致（跨零点允许差一天）", (rels[0]["date"], vj.get("published")))
    chk(all(_days(r["date"], vj.get("published", "")) <= 400 for r in rels),
        "历史日期都在合理范围", "有离谱的日期")
else:
    chk(False, "对齐检查", "缺 version.json")

# ------------------------------------------------------ 3. 安装包产物（6）
sec("【3】安装包里的两份日志")
chk(LOCAL_HTML.exists(), "更新日志.html 已生成（会打进安装目录）", "缺")
html = read(LOCAL_HTML)
chk("更新日志" in html and "<!doctype html>" in html.lower(), "是一份正常的网页", html[:30])
chk(all(("v" + v) in html for v in vers), "每个版本都在里面", "漏了版本")
chk(html.count('class="item"') >= len(vers), "每个版本都渲染成条目",
    (html.count('class="item"'), len(vers)))
chk(RECENT_TXT.exists(), "安装向导提示文本已生成", "缺")
raw_txt = RECENT_TXT.read_bytes() if RECENT_TXT.exists() else b""
chk(raw_txt.startswith(b"\xef\xbb\xbf"),
    "★ 提示文本带 UTF-8 编码标记（不带则安装向导里中文全是问号）", raw_txt[:8])
txt = raw_txt.decode("utf-8-sig", errors="replace")
chk(("v" + vers[0]) in txt and "本次更新" in txt, "内容讲的是本次这一版", txt[:50])

# --------------------------------------------------------- 4. Inno 脚本（5）
sec("【4】安装脚本 installer.iss")
iss = read(ISS)
m = re.search(r"^InfoBeforeFile\s*=\s*(.+)$", iss, re.M)
chk(bool(m) and "changelog_recent" in (m.group(1) if m else ""),
    "安装时先显示本次更新（InfoBeforeFile）", m.group(1) if m else "没配")
chk(re.search(r"^Source:.*更新日志\.html", iss, re.M) is not None,
    "日志页被打进安装目录", "没打进去")
log_line = ""
for line in iss.splitlines():
    if line.strip().startswith("Filename:") and "更新日志" in line:
        log_line = line
        break
chk(bool(log_line), "完成页有「查看更新日志」这一项", "没配")
if log_line:
    flags = log_line[log_line.find("Flags:") + 6:] if "Flags:" in log_line else ""
    chk("postinstall" in flags, "显示为完成页的勾选项", flags.strip())
    chk("unchecked" not in flags, "没有被设成默认不勾（客户要的是默认能看到）", flags.strip())
    chk("skipifsilent" in flags,
        "★ 静默自动更新时跳过（否则客户每次更新都被强行开个网页）", flags.strip())
    chk("checked" not in flags,
        "★ 没误写 checked（[Run] 段没这个标志，写了编译直接失败）", flags.strip())

# ------------------------------------------------------------- 5. 官网（4）
sec("【5】官网栏目")
site = read(SITE)
chk("build_changelog" in site, "官网读的是同一份数据（没有另抄一份）", "没接上")
chk("$CHANGELOG_SECTION" in site, "页面模板留了栏目位置", "没占位符")
# 2026-10-15 起样式统一走 tools/site_theme.css，更新日志那段由 load_css 追加，
# 不再在首页模板里留 $CLOG_CSS 占位 —— 所以改查「有没有真的接进共享主题」，
# 并到产物里确认样式确实落地了（不然线上是一条没有时间线的裸列表）。
chk("load_css" in site and "CLOG_CSS" in site,
    "时间线样式已接入（经共享主题文件）", "没接上 load_css(CLOG_CSS)")
_idx_html = read(STANDALONE / "index.html")
# 必须查单花括号：写成 {{ 时是无效 CSS，样式会静默失效（曾经线上就是这样）
chk(".clog .item{" in _idx_html,
    "产物页面里确实带上了时间线样式（单花括号）", "产物缺 clog 样式，或仍是双花括号")
chk((STANDALONE / "changelog.json").exists(), "部署目录里也放了一份数据", "缺")

# --------------------------------------------------------- 6. 发版流程（4）
sec("【6】发版流程")
ra = read(RELEASE_ALL)
# 锚点必须落在「真正执行」的那一行：光搜关键字会被文件头的说明文字骗过去
# （PyInstaller 在文档注释里就出现过，位置比真正的调用点还靠前，照那样比会误判）。
i_log = ra.find('str(TOOLS / "build_changelog.py")')
i_pack = ra.find('"-m", "PyInstaller"')
chk(i_log > 0, "发版流程里会生成更新日志", "调用点变了，检查也要跟着改")
chk(0 < i_log < i_pack,
    "★ 生成日志排在打包之前（排在后面＝客户装到上一版的日志）", (i_log, i_pack))
pub = read(PUBLISH_PAGE)
chk("changelog.json" in pub, "上传环节会把数据一起传上去", "漏传")

# --------------------------------------------------------- 7. 内容质量（3）
sec("【7】内容质量")
junk = [(r["version"], ln) for r in rels for ln in r["notes"].splitlines()
        if re.match(r"^\d{1,2}:\d{2}$", ln.strip())]
chk(not junk, "说明里没混进发布时刻（曾出现过一条光秃秃的『· 22:49』）", junk[:2])
chk(all(re.search(r"[\u4e00-\u9fa5]", r["notes"]) for r in rels),
    "每条说明都是中文内容", "有外文/空壳条目")
idx = read(STANDALONE / "index.html")
chk("每个版本改了什么" in idx and 'class="clog"' in idx,
    "官网页面里真的有这个栏目（若报错：重新跑 build_site.py）", "页面是旧的")

# ------------------------------------------------------------------ 收尾
print("\n" + "=" * 70)
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("\n没过的：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过：更新日志三处（数据源 / 安装包 / 官网）同源且都在。")
