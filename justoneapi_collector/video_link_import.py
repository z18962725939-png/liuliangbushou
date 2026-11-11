# -*- coding: utf-8 -*-
"""「关键词抓取导出 Excel」→ 批量解析 → 可直接下载的视频列表。

═══════════════════════════════════════════════════════════════════════════
客户反馈（2026-10-11）
═══════════════════════════════════════════════════════════════════════════
「关键词抓取」能导出 Excel 了，但导完还得一条条把链接粘到「视频下载」页去解析，
几十条就是几十次手工操作。需要：选中 Excel → 自动批量拿到这些视频。

本模块把这段链路补上：

    Excel / CSV → 提取「链接」列（去重） → 逐条解析成 VideoInfo → 交下载器

设计要点：
1. **优先按表头找「链接」列**（关键词抓取导出的表就是 `关键词|平台|…|链接`）。
   只取那一列，避免把封面图地址、主页地址一起抓进来。
2. 表头认不出来时**全表扫描** http(s) 链接，兼容客户自己整理的任意表格
   （比如只粘了一列链接、或者加了别的列）。
3. **去重两遍**：链接去重 + 作品 ID 去重。同一视频在不同关键词下会重复出现，
   解析两次既浪费时间也重复占额度。
4. 解析失败不中断整批，逐条记下失败原因，最后一次性回报给界面。
"""
from __future__ import annotations

import csv
import random
import re
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

# 表头里出现这些字样，就当它是「链接列」
LINK_HEADER_HINTS = ("链接", "网址", "地址", "url", "link", "share_url")

# 从任意文本里抠 http(s) 链接（抖音分享文本常带 emoji 和中文说明）
_URL_RE = re.compile(r"https?://[^\s\"'<>，。、；：！？（）【】《》]+")

# ----------------------------------------------------------------------
#  批量解析节流（v1.22.0）
# ----------------------------------------------------------------------
# 2026-10-22 客户现场：批量导入 30 条链接连着发，前 10 条免费页面解析还
# 命中，之后抖音开始返风控页（HTTP 200 但页面里没有作品数据），连着几条
# 未命中又把免费通道停用了，剩下 20 条只能去调接口 —— 当时接口正好欠费，
# 于是 20 条连环失败。抖音对「同一 IP 短时间内高频抓视频页」非常敏感，
# 每条之间留 0.6~1.0 秒（带抖动，别让间隔像机器人那样完全均等），
# 命中率的差别很大。192 条也只会多等两三分钟，比整批失败划算得多。
_BATCH_PACE = 0.6
_BATCH_PACE_JITTER = 0.4


def clean_url(url: str) -> str:
    """清掉链接尾部常见的中文标点 / 引号（从表格或分享文案里抠出来时经常带）。"""
    return (url or "").strip().strip("\"'").rstrip(".,;:，。；：、）)】》")


def find_links_in_text(text: str) -> list[str]:
    """从一段文本里提取所有 http(s) 链接（保序）。"""
    if not text:
        return []
    out: list[str] = []
    for m in _URL_RE.findall(str(text)):
        u = clean_url(m)
        if u:
            out.append(u)
    return out


def _has_http(value) -> bool:
    return bool(value) and "http" in str(value).lower()


def read_links_from_rows(rows: Iterable[Sequence]) -> list[str]:
    """从二维表格数据里提取链接（保序去重）。

    策略见模块 docstring：先按表头锁定「链接」列，认不出再全表扫描。
    """
    data = [list(r) for r in rows]
    if not data:
        return []
    header = [str(c or "").strip().lower() for c in data[0]]
    link_col = -1
    for i, h in enumerate(header):
        if any(hint in h for hint in LINK_HEADER_HINTS):
            link_col = i
            break

    links: list[str] = []
    if link_col >= 0:
        for r in data[1:]:
            if link_col < len(r) and _has_http(r[link_col]):
                u = clean_url(str(r[link_col]))
                if u:
                    links.append(u)
    else:
        for r in data:
            for cell in r:
                if _has_http(cell):
                    links.extend(find_links_in_text(str(cell)))

    seen: set[str] = set()
    out: list[str] = []
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# txt 里一行可能同时有链接和说明文字吗？允许。但**每一行必须至少有一条链接**。
_TXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5")


def _read_text_any_encoding(path: str | Path) -> tuple[str, str]:
    """读一个可能为 ANSI / UTF-8 / UTF-8-BOM 的 txt。

    ★ 为什么非要自己试编码：客户整理清单90%是用**系统记事本**写的，
      Win10/11 记事本默认存 ANSI（中文机器上就是 GBK），直接 open(...,
      encoding='utf-8') 一句 UnicodeDecodeError 就把整批挡在门外，
      而报错只显示「读取失败」——客户根本想不到是文件编码的问题。
      逐个试过去，试到哪个能读就用哪个，比教客户另存为 UTF-8 现实得多。
    """
    raw = Path(path).read_bytes()
    if not raw.strip():
        return "", "文件是空的"
    # 带 BOM 的 UTF-8 直接认领，避免二次猜测
    for enc in _TXT_ENCODINGS:
        try:
            return raw.decode(enc), ""
        except UnicodeDecodeError:
            continue
    # 全都试不出来：丢掉解不出的字节也要把能用的链接挑出来，
    # 总比让客户对着一句"读取失败"干瞪眼强。
    return raw.decode("utf-8", errors="ignore"), ""


def read_links_from_txt(path: str | Path) -> tuple[list[str], str]:
    """读取纯文本清单：一行一条链接（行里夹着说明文字也认），保序去重。

    不走 CSV 解析：清单是给人看的，逗号/引号没有转义一说，
    csv.reader 会把「标题,链接」这种常见写法切坏。
    """
    p = Path(path)
    if not p.exists():
        return [], f"文件不存在：{p}"
    if p.suffix.lower() != ".txt":
        return [], f"不是 .txt 文件：{p.name}"
    try:
        text, err = _read_text_any_encoding(p)
    except Exception as e:                                  # noqa: BLE001
        return [], f"读取失败：{type(e).__name__}: {e}"
    if err:
        return [], err

    links: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        for u in find_links_in_text(line):
            if u not in seen:
                seen.add(u)
                links.append(u)
    return links, ""


def read_links_from_file(path: str | Path,
                         max_rows: int = 0) -> tuple[list[str], str]:
    """读取 .xlsx / .xlsm / .csv / .txt，返回 (links, err)。

    err 非空表示读取失败（文件不存在、格式不支持、xls 老格式等）。
    """
    p = Path(path)
    if not p.exists():
        return [], f"文件不存在：{p}"
    suffix = p.suffix.lower()

    try:
        # ★ v1.24.6：txt 走专门的读法（怕 GBK 编码、怕 csv 把逗号切坏）
        if suffix == ".txt":
            return read_links_from_txt(p)

        if suffix == ".csv":
            with open(p, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            return read_links_from_rows(rows), ""

        if suffix in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            try:
                rows: list = []
                for i, row in enumerate(wb.active.iter_rows(values_only=True)):
                    if max_rows and i >= max_rows:
                        break
                    rows.append(row)
            finally:
                wb.close()
            return read_links_from_rows(rows), ""

        if suffix == ".xls":
            return [], ("暂不支持老式 .xls：请在 Excel 里「另存为 → .xlsx」后重试")
        return [], f"不支持的文件类型：{suffix or '(无扩展名)'}"
    except Exception as e:                                  # noqa: BLE001
        return [], f"读取失败：{type(e).__name__}: {e}"


def resolve_links(
    downloader,
    urls: Sequence[str],
    on_progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    skip_ids: set[str] | None = None,
) -> tuple[list, list[tuple[str, str]]]:
    """逐条把链接解析成 VideoInfo。

    Returns:
        (videos, failures)
        failures 里每项是 (url, 失败原因)；解析失败不中断整批。

    Args:
        skip_ids: 已存在的作品 ID 集合，命中则跳过（避免重复加入下载列表）。
                  本函数会就地往这个集合里补新解析出来的 ID。
    """
    videos: list = []
    failures: list[tuple[str, str]] = []
    skip = skip_ids if skip_ids is not None else set()
    total = len(urls)

    for i, url in enumerate(urls, 1):
        if should_stop and should_stop():
            break
        if on_progress:
            try:
                on_progress(i, total, url)
            except Exception:                               # noqa: BLE001
                pass
        # ★ v1.22.0：节流。见文件顶部 _BATCH_PACE 注释 —— 不节流时抖音
        #   风控会在十几条之后开始返没有数据的页面，整批解析连环失败。
        if i > 1:
            time.sleep(_BATCH_PACE + random.uniform(0.0, _BATCH_PACE_JITTER))
        try:
            info, reason = downloader.resolve_share_link_with_reason(url)
        except Exception as e:                              # noqa: BLE001
            info, reason = None, f"{type(e).__name__}: {e}"
        if info is None:
            failures.append((url, reason or "解析失败（链接可能已失效）"))
            continue
        vid = str(getattr(info, "video_id", "") or "")
        if vid and vid in skip:
            continue
        if vid:
            skip.add(vid)
        videos.append(info)

    return videos, failures
