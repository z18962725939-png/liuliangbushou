# -*- coding: utf-8 -*-
"""更新日志：把「每个版本改了什么」变成一份数据，再分发到三个地方。

为什么要有它
────────────
以前发版说明只活在两处：version.json 的 notes（只留最新一条）和
release_log.md（作者自己看的流水账）。结果是：客户装完根本不知道自己
装了什么，更新完也无从判断"这次值不值得装"。

现在改成单一数据源 installer_output/changelog.json，再向三处分发：
    1. installer_output/更新日志.html      → 打进安装包，完成时默认勾开
    2. installer_output/changelog_recent.txt → 安装包第 2 页直接显示本次更新
    3. installer_output/standalone/…       → 官网「更新日志」栏目

用法
────
    python tools/build_changelog.py                      # 按 version.json 同步最新一版
    python tools/build_changelog.py --version 1.13.0 --notes "新增…"   # 打包前写入待发版
    python tools/build_changelog.py --rebuild            # 从 release_log.md 全量重建
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html import escape as html_escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_OUT = ROOT / "installer_output"
STANDALONE = INSTALLER_OUT / "standalone"

CHANGELOG_JSON = INSTALLER_OUT / "changelog.json"
RELEASE_LOG = INSTALLER_OUT / "release_log.md"
VERSION_JSON = INSTALLER_OUT / "version.json"
LOCAL_HTML = INSTALLER_OUT / "更新日志.html"
RECENT_TXT = INSTALLER_OUT / "changelog_recent.txt"

# 早期版本没有归档说明，如实标注，不编造
EARLIEST_NOTE = "v1.2.0 之前的版本没有留下说明记录。"

_HEAD_RE = re.compile(r"^##\s+v(?P<ver>\d+(?:\.\d+)+)\s*[·•]\s*(?P<date>\d{4}-\d{2}-\d{2})")
_NOISE_RE = re.compile(r"^-\s*(体积|直链|验证)\s*[:：]")
_ITEM_RE = re.compile(r"^\d+[\.、]\s*")
# 流水账标题是「## v1.11.0 · 2026-10-14 22:49」，切块后时间会掉进正文里；
# 没有这条过滤，日志里就会出现一条光秃秃的「· 22:49」。
_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


# ──────────────────────────────────────────────────────────────────────
#  解析历史流水账
# ──────────────────────────────────────────────────────────────────────
def _clean_notes(text: str) -> str:
    """把流水账里的一段说明洗成「一行一条」的纯条目。

    原始形态长这样（"更新：" 后面可能还挂若干续行）：
        - 更新：1. 修了某某问题
        2. 新增某某功能
        3. 界面优化
        - 体积：……
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _NOISE_RE.match(line):
            continue
        line = re.sub(r"^[-*]\s*", "", line)
        line = re.sub(r"^更新\s*[:：]\s*", "", line)
        if not line:
            continue
        # "1. xxx 2. yyy" 写在同一行的情况也要拆开
        pieces = re.split(r"(?=\d+[\.、]\s)", line)
        for piece in pieces:
            piece = _ITEM_RE.sub("", piece).strip(" ;；")
            if not piece or _TIME_ONLY_RE.match(piece):
                continue
            # 既没中文也没字母的（纯时间、纯符号）一律不是给人看的内容
            if not re.search(r"[\u4e00-\u9fa5A-Za-z]", piece):
                continue
            lines.append(piece)
    # 去重（保留顺序）：流水账里有重复回写的条目
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return "\n".join(out)


def parse_release_log() -> list[dict]:
    """从 release_log.md 解析出历史版本（旧 → 新）。"""
    if not RELEASE_LOG.exists():
        return []
    blocks = re.split(r"^##\s+", RELEASE_LOG.read_text(encoding="utf-8"), flags=re.M)
    items: list[dict] = []
    for block in blocks:
        m = _HEAD_RE.match("## " + block)
        if not m:
            continue
        body = block[m.end() - 3:]
        notes = _clean_notes(body)
        if not notes:
            continue
        items.append({"version": m.group("ver"),
                      "date": m.group("date"),
                      "notes": notes})
    return items


# ──────────────────────────────────────────────────────────────────────
#  读写 changelog.json
# ──────────────────────────────────────────────────────────────────────
def _vkey(ver: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", ver)) or (0,)


def load_changelog() -> list[dict]:
    if not CHANGELOG_JSON.exists():
        return []
    try:
        data = json.loads(CHANGELOG_JSON.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return []
    return [r for r in (data.get("releases") or []) if r.get("version")]


def save_changelog(releases: list[dict]) -> None:
    """存盘。**永远按版本号倒序** —— 三个消费方都默认第一条是最新版。"""
    uniq: dict[str, dict] = {}
    for r in releases:
        ver = str(r.get("version") or "").strip()
        if not ver:
            continue
        item = {"version": ver,
                "date": str(r.get("date") or ""),
                "notes": str(r.get("notes") or "").strip()}
        if item["notes"]:
            uniq[ver] = item                            # 同版本后写覆盖前写
    ordered = sorted(uniq.values(), key=lambda r: _vkey(r["version"]), reverse=True)
    INSTALLER_OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": ordered[0]["version"] if ordered else "",
        "count": len(ordered),
        "releases": ordered,
    }
    CHANGELOG_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert(version: str, date: str, notes: str, releases: list[dict]) -> list[dict]:
    """写入（或覆盖）一条版本记录。"""
    version = (version or "").strip().lstrip("vV")
    notes = _clean_notes(notes or "")
    if not version:
        return releases
    # ★ 别把「没给说明」当成「要删掉这一版」。
    #   2026-10-15 踩过：脚本不带 --notes 重跑一次（只是想刷新页面），
    #   upsert 先按版本号剔除、又因为 notes 为空不补回，最新版就这样从
    #   更新日志里消失了 —— 而且没有任何报错。没给新说明就原样保留。
    old = [r for r in releases if r.get("version") == version]
    out = [r for r in releases if r.get("version") != version]
    if notes:
        out.append({"version": version,
                    "date": date or (old[0].get("date") if old else ""),
                    "notes": notes})
    elif old:
        out.append(old[0])
    return out


# ──────────────────────────────────────────────────────────────────────
#  渲染（官网栏目与本地日志页共用一套，两处长得一模一样）
# ──────────────────────────────────────────────────────────────────────
CLOG_CSS = """
  .clog{{margin-top:4px}}
  .clog .item{{position:relative;padding:0 0 14px 18px;border-left:2px solid var(--clog-line,#e6e8eb)}}
  .clog .item:last-child{{border-left-color:transparent;padding-bottom:0}}
  .clog .item:before{{content:"";position:absolute;left:-6px;top:6px;width:10px;height:10px;
    background:var(--clog-accent,#1a73e8);border-radius:50%;box-shadow:0 0 0 3px var(--clog-accent-soft,#e8f0fe)}}
  .clog .hd{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}}
  .clog .ver{{font-size:15px;font-weight:700;color:var(--clog-strong,#1a1a1a)}}
  .clog .dt{{font-size:12px;color:var(--clog-dim,#999)}}
  .clog .newest{{background:var(--clog-hot-bg,#e8453c);color:var(--clog-hot-ink,#fff);font-size:11px;padding:1px 6px;
    border-radius:4px;margin-left:2px}}
  .clog ul{{margin:6px 0 0;padding-left:16px}}
  .clog li{{font-size:14px;color:var(--clog-body,#555);margin-bottom:3px;line-height:1.65}}
  .clog .more{{margin-top:10px}}
  .clog summary{{font-size:14px;color:var(--clog-accent,#1a73e8);cursor:pointer;padding:6px 0}}
  .clog summary:hover{{text-decoration:underline}}
  .clog-empty{{color:var(--clog-dim,#999);font-size:14px}}
"""


def render_entries_html(releases: list[dict], newest: str = "",
                        fold_after: int = 0) -> str:
    """渲染更新日志条目。

    fold_after > 0 时：只直接显示前 N 条，更早的收进「展开更早的版本」。
    官网首页要控制长度（二十几个版本全铺开会很长），本地日志页则全部展开。
    """
    if not releases:
        return '<p class="clog-empty">暂时没有更新记录。</p>'

    def one(r: dict, idx: int) -> str:
        tag = (f'<span class="newest">最新</span>'
               if (newest and r["version"] == newest and idx == 0) else "")
        items = "".join(f"<li>{_esc(line)}</li>"
                        for line in r["notes"].splitlines() if line.strip())
        return (f'<div class="item">'
                f'<div class="hd"><span class="ver">v{_esc(r["version"])}</span>{tag}'
                f'<span class="dt">{_esc(r["date"])}</span></div>'
                f'<ul>{items}</ul></div>')

    head = "".join(one(r, i) for i, r in enumerate(releases[:fold_after])) \
        if fold_after else ""
    rest = releases[fold_after:] if fold_after else releases

    if fold_after and rest:
        folded = "".join(one(r, i) for i, r in enumerate(rest, start=fold_after))
        return (f'<div class="clog">{head}'
                f'<details class="more"><summary>展开更早的 {len(rest)} 个版本</summary>'
                f'{folded}</details></div>')
    return f'<div class="clog">{head or "".join(one(r, i) for i, r in enumerate(rest))}</div>'


def _esc(text: str) -> str:
    """转义交给标准库 —— 手写实体容易在编辑器/工具链里被吞成原字符，
    那样转义函数会变成"自己替换自己"，看着像防住了实际什么都没做。"""
    return html_escape(str(text), quote=True)


def render_local_html(releases: list[dict], app_name: str = "流量捕手") -> str:
    """随安装包带过去的更新日志页（双击就能看，不联网）。"""
    newest = releases[0]["version"] if releases else ""
    body = render_entries_html(releases, newest=newest, fold_after=0)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(app_name)} · 更新日志</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
       background:#f5f6f8;color:#1a1a1a;line-height:1.7;padding:20px 16px 40px}}
  .wrap{{max-width:680px;margin:0 auto;background:#fff;border-radius:14px;padding:24px;
        box-shadow:0 2px 12px rgba(0,0,0,.06)}}
  h1{{font-size:22px;margin-bottom:4px}}
  .sub{{color:#888;font-size:13px;margin-bottom:18px}}
  .tip{{background:#e8f3ff;border-radius:10px;padding:11px 13px;font-size:13px;
       color:#1557b0;margin-bottom:18px}}
  .foot{{margin-top:18px;padding-top:14px;border-top:1px dashed #eee;
        font-size:12px;color:#aaa}}{CLOG_CSS}
</style>
</head>
<body>
<div class="wrap">
  <h1>{_esc(app_name)} · 更新日志</h1>
  <p class="sub">共 {len(releases)} 个版本 · 最新版 v{_esc(newest)}</p>
  <div class="tip">这份文件保存在你的电脑上，记录着从第一个版本到现在每一次更新改了什么。软件升级不会删掉它。</div>
  {body}
  <p class="foot">{EARLIEST_NOTE} 软件里「设置 → 检查更新」可以随时升级到最新版。</p>
</div>
</body>
</html>
"""


def render_recent_txt(releases: list[dict], app_name: str = "流量捕手") -> str:
    """安装程序第 2 页显示的「本次更新内容」。

    Inno 读 .txt 时按系统编码，中文必须带 UTF-8 BOM 才不会变乱码 —— 这一步
    不能省，否则客户看到的是一堆问号。
    """
    if not releases:
        return f"{app_name} 安装程序\r\n\r\n暂无更新说明。\r\n"
    top = releases[0]
    lines = [f"{app_name} v{top['version']}（{top['date']}）本次更新：", ""]
    for line in top["notes"].splitlines():
        if line.strip():
            lines.append("  · " + line.strip())
    lines += ["", "─" * 46, "",
              "想看从第一个版本至今的全部更新记录，安装完成后",
              "勾选「查看更新日志」即可，也可以随时打开安装目录里的 更新日志.html。",
              "", "点「下一步」继续安装。"]
    return "\r\n".join(lines) + "\r\n"


# ──────────────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────────────
def build(version: str = "", notes: str = "", date: str = "",
          rebuild: bool = False) -> tuple[list[dict], dict]:
    """返回（倒序的版本列表，本次动作摘要）。"""
    releases = [] if rebuild else load_changelog()

    # 历史补录：只补没有的，不覆盖已手工修过的条目
    for item in parse_release_log():
        if not any(r["version"] == item["version"] for r in releases):
            releases.append(item)

    # 待发版 / version.json 里的当前版本
    if not version and VERSION_JSON.exists():
        try:
            vj = json.loads(VERSION_JSON.read_text(encoding="utf-8"))
            version = str(vj.get("version") or "")
            notes = notes or str(vj.get("notes") or "")
            date = date or str(vj.get("published") or "")
        except Exception:                               # noqa: BLE001
            pass
    if version:
        releases = upsert(version, date, notes, releases)

    save_changelog(releases)
    ordered = load_changelog()

    # ① 安装包内的日志页
    LOCAL_HTML.write_text(render_local_html(ordered), encoding="utf-8")
    # ② 安装前显示的本次更新（UTF-8 BOM，中文不乱码）
    RECENT_TXT.write_text(render_recent_txt(ordered), encoding="utf-8-sig")
    # ③ 官网静态目录（部署时用）
    STANDALONE.mkdir(parents=True, exist_ok=True)
    (STANDALONE / "changelog.json").write_text(
        CHANGELOG_JSON.read_text(encoding="utf-8"), encoding="utf-8")

    summary = {"count": len(ordered),
               "latest": ordered[0]["version"] if ordered else "",
               "html": str(LOCAL_HTML), "recent": str(RECENT_TXT)}
    return ordered, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="生成更新日志（数据包 / 安装包 / 官网）")
    ap.add_argument("--version", help="版本号（默认读 version.json）")
    ap.add_argument("--notes", help="更新说明（默认读 version.json）")
    ap.add_argument("--date", help="发布日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--rebuild", action="store_true", help="从 release_log.md 全量重建")
    args = ap.parse_args()

    date = args.date
    if args.version and not date:
        from datetime import datetime, timedelta, timezone
        date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    ordered, summary = build(version=args.version or "", notes=args.notes or "",
                             date=date, rebuild=args.rebuild)
    if not ordered:
        print("[警告] 一条更新记录都没有，检查 release_log.md / version.json。")
        return 1
    print(f"更新日志：{summary['count']} 个版本，最新 v{summary['latest']}")
    print(f"  数据包    {CHANGELOG_JSON}")
    print(f"  安装包页  {LOCAL_HTML}")
    print(f"  安装提示  {RECENT_TXT}（带 UTF-8 编码标记，中文不乱码）")
    print(f"  官网数据  {STANDALONE / 'changelog.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
