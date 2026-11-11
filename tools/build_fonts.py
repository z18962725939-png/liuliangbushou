# -*- coding: utf-8 -*-
"""字体子集化 —— 把官网真正用到的字挑出来，做成几个几十 KB 的 woff2。

为什么必须做这一步
──────────────────────────────────────────────────────────────────
官网要在 Windows 上好看，就不能指望客户端字体：Win10/11 没有苹方，
中文只会落到「微软雅黑」，而雅黑的粗体在大字号下笔画呆板、字面拥挤，
一眼就是"没设计过的站"。所以字体必须自带。

但思源宋体/黑体的完整中文包一个就是 1.5MB，四个字重 6MB —— 塞进
官网等于把首屏拖死。真正常用的做法是：**只把页面上真正出现过的字
打进子集**，成品通常只剩几十 KB。

对应关系（三族各司其职，见 site_theme.css 头部）
  · Noto Serif SC（思源宋体）→ 只给大标题用，字符集 = 标题类文字
  · Noto Sans SC（思源黑体） → 正文用，字符集 = 全站所有文字
  · Manrope                  → 拉丁与数字，整份照搬（才 24KB）

★ 顺序要求：必须在页面生成之后跑（它读的是生成出来的 HTML）。
  发版流程里排在 build_site / build_subpages 之后、上传之前。

★ 覆盖性由自检兜底：tools/_validate_theme.py 会检查「每个页面的标题
  字符是否都在子集里」，漏字直接报错，不会静默变成两种字体混排。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
SRC = TOOLS / "fonts_src"
STANDALONE = ROOT / "installer_output" / "standalone"
OUTDIR = STANDALONE / "fonts"

# 源文件 → 输出文件。weight 只影响 @font-face，不影响子集化本身
JOBS = [
    # (源文件,           输出文件名,               字符集,      说明)
    ("serif-700.woff2", "serif-sc-700.woff2", "display", "思源宋体 大标题"),
    ("sans-400.woff2", "sans-sc-400.woff2", "all", "思源黑体 正文"),
    ("sans-500.woff2", "sans-sc-500.woff2", "all", "思源黑体 小标题/按钮"),
    ("sans-700.woff2", "sans-sc-700.woff2", "all", "思源黑体 加粗"),
]

# 无条件带上的字符：拉丁、数字、常见中英标点。
# 少了它们，标题里的 "GEO 优化"、"v1.14.0" 会掉成另一种字体。
BASE_CHARS = (
    "".join(chr(c) for c in range(0x20, 0x7F))
    + "　、。〈〉《》「」『』【】〔〕！＃％＆（），．：；？￥·—…‘’“”※←→↑↓×÷≈≤≥±°●○◆◇■□▲▼★☆"
    + "，。！？；：（）【】《》—…·“”‘’"
    + "۰۱۲۳۴۵۶۷۸۹"
    + "①②③④⑤⑥⑦⑧⑨⑩"
    + "／＼｜＋－＝＿"
)

# 判断「这块文字算不算标题类」用的 class / 标签
DISPLAY_CLASSES = {
    "kick", "pill", "ver", "q", "summary", "hd", "sec-head",
    "deck-tab", "btn", "pbadge", "ptag", "tag", "pf-tag", "amt",
    "pf-h", "logo", "who", "pn", "mt", "tstat", "tt", "chips", "sib",
}
DISPLAY_TAGS = {"h1", "h2", "h3", "h4", "h5", "summary", "b", "strong", "em", "button"}


def _strip_tags(html: str) -> str:
    """粗剥标签拿纯文字。这里不需要精确解析，只要字符集别漏。"""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
              .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    return txt


def display_text(html: str) -> str:
    """只取「会用到宋体」的那部分文字：标题、标签、按钮、徽章。"""
    chunks: list[str] = []

    # 1) 标题标签整段
    for tag in ("h1", "h2", "h3", "h4"):
        for m in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", html, flags=re.S | re.I):
            chunks.append(_strip_tags(m.group(1)))

    # 2) 带指定 class 的元素（只取开标签到最近的闭合尖括号，够用）
    for m in re.finditer(r'class="([^"]+)"[^>]*>(.*?)<', html, flags=re.S):
        cls = set(m.group(1).split())
        if cls & DISPLAY_CLASSES:
            chunks.append(_strip_tags(m.group(2)))
    return " ".join(chunks)


def keep_char(ch: str) -> bool:
    """丢掉控制字符与代理区；其余全留（CJK、拉丁、标点都要）。"""
    if ch in ("\n", "\r", "\t"):
        return False
    return unicodedata.category(ch)[0] != "C"


def collect_charset(pages: list[Path]) -> tuple[str, str]:
    all_text, disp_text = [], []
    for p in pages:
        try:
            html = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        all_text.append(_strip_tags(html))
        disp_text.append(display_text(html))
    every = BASE_CHARS + "".join(all_text)
    disp = BASE_CHARS + "".join(disp_text)
    every = "".join(sorted({c for c in every if keep_char(c)}))
    disp = "".join(sorted({c for c in disp if keep_char(c)}))
    return every, disp


def find_pyftsubset() -> list[str]:
    """优先用模块方式调 fontTools，比依赖 PATH 上的 exe 可靠。"""
    return [sys.executable, "-m", "fontTools.subset"]


def subset(src: Path, dst: Path, chars: str) -> tuple[bool, str]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    txt = SRC / "_charset.tmp.txt"
    txt.write_text(chars, encoding="utf-8")
    cmd = find_pyftsubset() + [
        str(src),
        f"--text-file={txt}",
        f"--output-file={dst}",
        "--flavor=woff2",
        "--layout-features=*",       # 保留 kern / locl 等，中文标点的间距靠它
        "--no-hinting",
        "--desubroutinize",
        "--drop-tables+=DSIG",
        "--name-IDs=*",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    txt.unlink(missing_ok=True)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "").strip()[-400:]
    return True, ""


def human(n: int) -> str:
    return f"{n/1024:.0f} KB" if n >= 1024 else f"{n} B"


def main() -> int:
    ap = argparse.ArgumentParser(description="为官网子集化字体")
    ap.add_argument("--check", action="store_true", help="只报告不写文件")
    args = ap.parse_args()

    if not SRC.is_dir():
        print(f"[错误] 源字体目录不存在：{SRC}")
        print("       需要 serif-700 / sans-400 / sans-500 / sans-700 / manrope-var")
        return 2

    pages = sorted(STANDALONE.glob("*.html"))
    if not pages:
        print(f"[错误] 没找到页面：{STANDALONE}/*.html —— 请先生成官网")
        return 2

    every, disp = collect_charset(pages)
    print(f"待处理页面 {len(pages)} 个")
    print(f"  全站去重字符 {len(every)} 个 · 其中标题类字符 {len(disp)} 个")
    print()

    if args.check:
        return 0

    total_src = total_dst = 0
    rows: list[tuple[str, str, int, int]] = []
    for src_name, out_name, which, label in JOBS:
        src = SRC / src_name
        if not src.is_file():
            print(f"  [跳过] {label}：缺 {src_name}")
            continue
        charset = disp if which == "display" else every
        dst = OUTDIR / out_name
        ok, err = subset(src, dst, charset)
        if not ok:
            print(f"  [失败] {label}：{err}")
            return 1
        a, b = src.stat().st_size, dst.stat().st_size
        total_src += a
        total_dst += b
        rows.append((label, out_name, a, b))

    # Manrope 本来就是拉丁小包，直接搬
    man_src = SRC / "manrope-var.woff2"
    if man_src.is_file():
        shutil.copy2(man_src, OUTDIR / "manrope-var.woff2")
        rows.append(("Manrope 数字/拉丁", "manrope-var.woff2",
                     man_src.stat().st_size, man_src.stat().st_size))
        total_src += man_src.stat().st_size
        total_dst += man_src.stat().st_size

    print(f"{'字体':<22}{'文件':<24}{'原始':>10}{'子集后':>10}")
    print("─" * 68)
    for label, name, a, b in rows:
        print(f"{label:<22}{name:<24}{human(a):>10}{human(b):>10}")
    print("─" * 68)
    print(f"{'合计':<22}{'':<24}{human(total_src):>10}{human(total_dst):>10}")
    print()
    note = "（客户端只需下载子集后的体积）"
    if total_dst > 520 * 1024:
        print(f"⚠ 字体总计 {human(total_dst)} 偏大 {note}，考虑砍掉一个中文字重")
    else:
        print(f"✅ 字体总计 {human(total_dst)} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
