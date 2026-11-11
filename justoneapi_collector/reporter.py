"""
交付侧：一键周报 / 月报。

代运营最耗时间的交付物就是给客户写报告。本模块把监控塔里攒的数据
自动汇总成一份能直接发出去的报告，包含：

  - 概览：作品数、涨粉、爆款率、互动总量
  - 账号表现：每个号涨粉、新增作品、点赞中位数、爆款率
  - 作品明细：逐条作品的数据与是否爆款
  - 异动预警：未进推荐池 / 疑似限流 / 数据停滞
  - 竞品动态：对标账号新作品与爆款
  - 最佳发布时段：按小时统计中位点赞，给出"几点发最好"
  - 自动结论：像人写的总结，不是流水账

产出两种格式：Excel（多页签，客户好改）和 HTML（打开就能看，适合直接发）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .platforms.adapter import PLATFORM_DISPLAY
from .projects import Project
from .watchtower import Anomaly, WatchTower


@dataclass
class ReportData:
    project_name: str = ""
    client: str = ""
    period_days: int = 7
    generated_at: float = 0.0
    accounts: list[dict] = field(default_factory=list)
    posts: list[dict] = field(default_factory=list)
    anomalies: list[Anomaly] = field(default_factory=list)
    competitor_posts: list[dict] = field(default_factory=list)
    keywords: list[tuple[str, int]] = field(default_factory=list)
    best_hours: list[tuple[int, int, int]] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)

    @property
    def period_text(self) -> str:
        return f"近 {self.period_days} 天"

    @property
    def total_posts(self) -> int:
        return len(self.posts)

    @property
    def total_fans_delta(self) -> int:
        return sum(a.get("涨粉", 0) for a in self.accounts)

    @property
    def viral_count(self) -> int:
        return sum(1 for p in self.posts if p.get("是否爆款") == "是")

    @property
    def viral_rate_text(self) -> str:
        if not self.posts:
            return "0%"
        return f"{self.viral_count / len(self.posts) * 100:.0f}%"

    @property
    def date_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.generated_at))


# ---------------------------------------------------------------- 时段分析

def analyze_best_hours(posts: list[dict], min_samples: int = 2) -> list[tuple[int, int, int]]:
    """按发布小时统计中位点赞，返回 [(小时, 样本数, 中位点赞)] 降序。"""
    buckets: dict[int, list[int]] = {}
    for p in posts:
        ts = p.get("发布时间戳") or 0
        if not ts:
            continue
        hour = time.localtime(ts).tm_hour
        buckets.setdefault(hour, []).append(int(p.get("点赞", 0)))

    out = []
    for hour, vals in buckets.items():
        if len(vals) < min_samples:
            continue
        s = sorted(vals)
        mid = len(s) // 2
        median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) // 2
        out.append((hour, len(vals), median))
    out.sort(key=lambda x: -x[2])
    return out


def _median(vals: list[int]) -> int:
    if not vals:
        return 0
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) // 2


# ---------------------------------------------------------------- 自动结论

def build_highlights(data: ReportData) -> list[str]:
    """把数据翻译成人话结论——这是报告里唯一客户会认真读的部分。"""
    lines: list[str] = []

    if not data.posts:
        lines.append("本周期没有采集到作品数据，请先对账号做一次「立即检查」再生成报告。")
        return lines

    lines.append(
        f"{data.period_text}共采集 {data.total_posts} 条作品，"
        f"其中 {data.viral_count} 条达到爆款水平（点赞 ≥ 账号中位数 3 倍），"
        f"爆款率 {data.viral_rate_text}。")

    if data.accounts:
        best = max(data.accounts, key=lambda a: a.get("涨粉", 0))
        if best.get("涨粉"):
            lines.append(
                f"涨粉表现最好的是「{best.get('账号')}」，净增 "
                f"{best.get('涨粉')} 粉，当前粉丝 {best.get('粉丝')}。")
        worst = [a for a in data.accounts if a.get("涨粉", 0) <= 0]
        if worst and len(worst) < len(data.accounts):
            names = "、".join(str(a.get("账号")) for a in worst[:3])
            lines.append(f"「{names}」本周期粉丝无明显增长，建议重点复盘内容方向。")

    if data.best_hours:
        h, n, med = data.best_hours[0]
        all_med = _median([int(p.get("点赞", 0)) for p in data.posts])
        if all_med and med > all_med:
            lift = (med - all_med) / all_med * 100
            lines.append(
                f"最佳发布时段是 {h:02d}:00-{h+1:02d}:00"
                f"（{n} 条样本，中位点赞 {med}），"
                f"比全天中位数高 {lift:.0f}%，建议把重点内容排在这个时段。")
        else:
            lines.append(
                f"发布时段中 {h:02d}:00-{h+1:02d}:00 表现最好"
                f"（{n} 条样本，中位点赞 {med}）。")

    danger = [a for a in data.anomalies if a.level == "danger"]
    if danger:
        kinds: dict[str, int] = {}
        for a in danger:
            kinds[a.kind] = kinds.get(a.kind, 0) + 1
        desc = "、".join(f"{k} {v} 条" for k, v in kinds.items())
        lines.append(f"⚠ 有 {len(danger)} 条作品数据异常（{desc}），建议尽快排查内容或账号状态。")
    elif data.anomalies:
        lines.append(f"有 {len(data.anomalies)} 条作品增长偏弱，可关注后续走势。")

    if data.competitor_posts:
        top = max(data.competitor_posts, key=lambda p: int(p.get("点赞", 0)))
        lines.append(
            f"对标账号本周期新增 {len(data.competitor_posts)} 条作品，"
            f"最高的是「{str(top.get('标题'))[:24]}」（{top.get('点赞')} 赞），值得拆解。")

    if data.keywords:
        top_kw = "、".join(w for w, _ in data.keywords[:5])
        lines.append(f"评论区高频词：{top_kw}，可直接作为下一轮选题方向。")

    return lines


# ---------------------------------------------------------------- 组装报告

def build_report(project: Project, tower: WatchTower,
                 period_days: int = 7,
                 competitor_posts: list | None = None,
                 keywords: list[tuple[str, int]] | None = None) -> ReportData:
    """从监控塔的数据里组装一份报告。"""
    cutoff = time.time() - period_days * 86400
    data = ReportData(project_name=project.name, client=project.client,
                      period_days=period_days, generated_at=time.time())
    data.keywords = list(keywords or [])
    data.competitor_posts = list(competitor_posts or [])

    for tr in tower.all_tracks():
        # 只统计本项目的账号：自营账号清单优先，没有就全算
        summary = tower.author_summary(tr.platform, tr.author_id)
        if not summary:
            continue
        summary["涨粉"] = tr.fans_delta
        data.accounts.append(summary)

        diggs = [int((t.latest or {}).get("digg", 0)) for t in tr.posts.values()
                 if (t.publish_ts or t.first_seen) >= cutoff]
        med = _median(diggs)

        for t in tr.posts.values():
            base = t.publish_ts or t.first_seen
            if base < cutoff:
                continue
            cur = int((t.latest or {}).get("digg", 0))
            prev = int(t.history[-2].get("digg", 0)) if len(t.history) > 1 else cur
            data.posts.append({
                "平台": PLATFORM_DISPLAY.get(tr.platform, tr.platform),
                "账号": tr.nickname or tr.author_id,
                "标题": t.title,
                "发布时间戳": t.publish_ts or int(t.first_seen),
                "发布时间": time.strftime("%m-%d %H:%M",
                                          time.localtime(t.publish_ts or t.first_seen)),
                "点赞": cur,
                "评论": int((t.latest or {}).get("comment", 0)),
                "收藏": int((t.latest or {}).get("collect", 0)),
                "分享": int((t.latest or {}).get("share", 0)),
                "本期新增赞": max(cur - int(t.history[0].get("digg", 0))
                                  if t.history else 0, 0),
                "环比": f"+{cur - prev}" if cur >= prev else str(cur - prev),
                "是否爆款": "是" if med and cur >= med * 3 else "否",
                "链接": t.url,
            })

    data.posts.sort(key=lambda p: -int(p.get("点赞", 0)))
    data.anomalies = tower.anomalies()
    data.best_hours = analyze_best_hours(data.posts)
    data.highlights = build_highlights(data)
    return data


# ---------------------------------------------------------------- Excel

def export_excel_report(data: ReportData, path: Path) -> Path | None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception:
        return None

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="2F5597")
    head_font = Font(color="FFFFFF", bold=True, size=11)

    def sheet(title, columns, rows, widths=None):
        ws = wb.create_sheet(title)
        ws.append(columns)
        for c in range(1, len(columns) + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in rows:
            ws.append(r)
        for i, col in enumerate(columns, start=1):
            w = (widths or {}).get(col)
            if not w:
                sample = [len(str(col))] + [len(str(r[i - 1])) for r in rows[:50]]
                w = min(max(sample) + 4, 46)
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        return ws

    # -- 概览 --
    ws = wb.active
    ws.title = "报告概览"
    ws["A1"] = f"{data.client or data.project_name}　短视频运营{data.period_text}报告"
    ws["A1"].font = Font(size=15, bold=True)
    ws["A2"] = f"生成时间 {data.date_text}"
    ws["A2"].font = Font(size=10, color="808080")

    row = 4
    ws.cell(row=row, column=1, value="核心指标").font = Font(bold=True, size=12)
    row += 1
    metrics = [
        ("统计周期", data.period_text),
        ("账号数", len(data.accounts)),
        ("作品数", data.total_posts),
        ("爆款数", data.viral_count),
        ("爆款率", data.viral_rate_text),
        ("净涨粉", data.total_fans_delta),
        ("异常作品", len(data.anomalies)),
    ]
    for k, v in metrics:
        ws.cell(row=row, column=1, value=k).font = Font(bold=True)
        ws.cell(row=row, column=2, value=v)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="自动结论").font = Font(bold=True, size=12)
    row += 1
    for line in data.highlights:
        ws.cell(row=row, column=1, value=line).alignment = Alignment(wrap_text=True)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
        ws.row_dimensions[row].height = 30
        row += 1

    if data.best_hours:
        row += 1
        ws.cell(row=row, column=1, value="最佳发布时段").font = Font(bold=True, size=12)
        row += 1
        ws.cell(row=row, column=1, value="时段")
        ws.cell(row=row, column=2, value="样本数")
        ws.cell(row=row, column=3, value="中位点赞")
        row += 1
        for h, n, med in data.best_hours[:6]:
            ws.cell(row=row, column=1, value=f"{h:02d}:00-{h+1:02d}:00")
            ws.cell(row=row, column=2, value=n)
            ws.cell(row=row, column=3, value=med)
            row += 1

    ws.column_dimensions["A"].width = 46
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 16

    # -- 其余页签 --
    if data.accounts:
        cols = ["平台", "账号", "粉丝", "涨粉", "作品数", "点赞中位数",
                "最高点赞", "爆款数", "爆款率", "最近检查"]
        sheet("账号表现", cols,
              [[a.get(c, "") for c in cols] for a in data.accounts])

    if data.posts:
        cols = ["平台", "账号", "标题", "发布时间", "点赞", "评论", "收藏",
                "分享", "本期新增赞", "环比", "是否爆款", "链接"]
        sheet("作品明细", cols,
              [[p.get(c, "") for c in cols] for p in data.posts],
              widths={"标题": 46, "链接": 34})

    if data.anomalies:
        sheet("异动预警", ["类型", "级别", "作品", "说明", "链接"],
              [[a.kind, a.level_text, a.title[:40], a.message, a.url]
               for a in data.anomalies],
              widths={"说明": 52, "作品": 30})

    if data.competitor_posts:
        cols = ["平台", "标题", "作者", "作者粉丝", "点赞", "评论", "发布时间", "链接"]
        sheet("竞品动态", cols,
              [[p.get(c, "") for c in cols] for p in data.competitor_posts],
              widths={"标题": 44})

    if data.keywords:
        sheet("评论高频词", ["关键词", "出现次数"],
              [[w, c] for w, c in data.keywords])

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


# ---------------------------------------------------------------- HTML

def export_html_report(data: ReportData, path: Path) -> Path:
    """生成一份离线可看、样式自洽的 HTML 报告（无任何外部依赖）。"""
    def esc(s) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def table(columns, rows):
        if not rows:
            return "<p class='empty'>本周期暂无数据</p>"
        th = "".join(f"<th>{esc(c)}</th>" for c in columns)
        tr = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in r) + "</tr>"
                     for r in rows)
        return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"

    metric_cards = "".join(
        f"<div class='card'><div class='num'>{esc(v)}</div><div class='lbl'>{esc(k)}</div></div>"
        for k, v in [("作品数", data.total_posts), ("爆款数", data.viral_count),
                     ("爆款率", data.viral_rate_text), ("净涨粉", data.total_fans_delta),
                     ("异常作品", len(data.anomalies))])

    highlights = "".join(f"<li>{esc(h)}</li>" for h in data.highlights)

    hour_rows = [(f"{h:02d}:00-{h+1:02d}:00", n, med) for h, n, med in data.best_hours[:6]]
    hours_table = table(["时段", "样本数", "中位点赞"], hour_rows) if hour_rows else ""

    title = f"{data.client or data.project_name} 短视频运营报告"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:32px;background:#f5f6f8;color:#1f2328;
font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.6}}
.wrap{{max-width:1080px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px;font-weight:600}}
.sub{{color:#6b7280;font-size:13px;margin-bottom:24px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
.card{{flex:1;min-width:120px;background:#fff;border:1px solid #e5e7eb;
border-radius:10px;padding:16px;text-align:center}}
.num{{font-size:24px;font-weight:600;color:#2F5597}}
.lbl{{font-size:12px;color:#6b7280;margin-top:4px}}
section{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;
padding:18px 20px;margin-bottom:16px}}
h2{{font-size:15px;margin:0 0 12px;font-weight:600;color:#111827}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#f3f4f6;text-align:left;padding:8px 10px;font-weight:600;
border-bottom:1px solid #e5e7eb;white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid #f1f2f4;vertical-align:top}}
tr:last-child td{{border-bottom:none}}
ul{{margin:0;padding-left:20px}}
li{{margin-bottom:8px;font-size:14px}}
.empty{{color:#9ca3af;font-size:13px}}
.bad{{color:#b91c1c}} .warn{{color:#b45309}}
</style></head><body><div class="wrap">
<h1>{esc(title)}</h1>
<div class="sub">{esc(data.period_text)}　生成于 {esc(data.date_text)}</div>
<div class="cards">{metric_cards}</div>
<section><h2>本期结论</h2><ul>{highlights}</ul></section>
{f"<section><h2>最佳发布时段</h2>{hours_table}</section>" if hours_table else ""}
<section><h2>账号表现</h2>{table(
["平台","账号","粉丝","涨粉","作品数","点赞中位数","爆款率"],
[[a.get("平台",""),a.get("账号",""),a.get("粉丝",""),a.get("涨粉",""),
  a.get("作品数",""),a.get("点赞中位数",""),a.get("爆款率","")]
 for a in data.accounts])}</section>
<section><h2>作品明细</h2>{table(
["平台","账号","标题","发布时间","点赞","评论","收藏","是否爆款"],
[[p.get("平台",""),p.get("账号",""),str(p.get("标题",""))[:40],
  p.get("发布时间",""),p.get("点赞",""),p.get("评论",""),p.get("收藏",""),
  p.get("是否爆款","")] for p in data.posts])}</section>
<section><h2>数据异动预警</h2>{table(
["类型","级别","作品","说明"],
[[a.kind,a.level_text,a.title[:36],a.message] for a in data.anomalies])}</section>
{f"<section><h2>竞品动态</h2>{table(['平台','标题','作者','点赞','发布时间'],[[p.get('平台',''),str(p.get('标题',''))[:36],p.get('作者',''),p.get('点赞',''),p.get('发布时间','')] for p in data.competitor_posts])}</section>"
 if data.competitor_posts else ""}
{f"<section><h2>评论区高频词</h2>{table(['关键词','次数'],[[w,c] for w,c in data.keywords])}</section>"
 if data.keywords else ""}
</div></body></html>"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
