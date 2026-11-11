# -*- coding: utf-8 -*-
"""官网主题完整性自检 —— 拦「模板用了类、样式里没有」这类静默事故。

为什么单独写一个
──────────────────────────────────────────────────────────────────
2026-10-17 用户发来四张截图问「官网是不是没渲染好」。实际原因是：
重写主题样式时漏掉了一批组件类（.pcard / .case / .who / .tags /
.pbadge / .psub / .dl / .wx / .t-amber …），模板照旧输出这些类，
但 CSS 里一条规则都没有 —— 浏览器退回默认样式，于是「产品矩阵」
是三列裸文字、「谁在用」是一堆挤在一行的字、「更新日志」是黑字压黑底。

这类问题**不会报错**：页面能打开、控制台干净、SEO 自检也全绿，
只有人眼看截图才发现。所以必须专门钉住。

五组检查：
  A 类名覆盖 —— HTML 里出现的每个 class 都得有样式定义
  B 深色可读 —— 共享的更新日志样式不许写死浅色（它就是那次黑字事故）
  C 无 JS 兜底 —— 揭示动画必须挂在 .js 底下，否则关脚本看到整页空白
  D 字体产物 —— 子集文件在位，且覆盖得住页面标题用到的每个字
  E 字体分工 —— 大标题必须落在宋体那一族，别被改回系统黑体
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
STANDALONE = ROOT / "installer_output" / "standalone"
THEME = TOOLS / "site_theme.css"

passed = failed = 0


def chk(ok: bool, good: str, bad: str, detail: str = "") -> bool:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✔ {good}")
    else:
        failed += 1
        print(f"  ✘ {bad}")
        if detail:
            for line in detail.splitlines():
                print(f"      {line}")
    return ok


def section(title: str) -> None:
    print(f"\n{title}")


def load_css() -> str:
    """站点实际用的样式 = 主题文件 + 更新日志那段（跟页面里拼的一致）。"""
    css = THEME.read_text(encoding="utf-8")
    try:
        sys.path.insert(0, str(TOOLS))
        import build_changelog as bclog          # noqa: PLC0415
        css += "\n" + bclog.CLOG_CSS
    except Exception as exc:                     # noqa: BLE001
        print(f"      [提示] 读不到 CLOG_CSS：{exc}")
    return css


def css_classes(css: str) -> set[str]:
    """CSS 里定义过的类名。属性值里的 .5em 之类会被 \\. 排除掉。"""
    return set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", css))


def html_classes(html: str) -> set[str]:
    out: set[str] = set()
    for attr in re.findall(r'class="([^"]+)"', html):
        for c in attr.split():
            if c and not c.startswith("$"):
                out.add(c)
    return out


# 只当 JS 钩子使唤、不需要样式的类（互动层自己按这些名字找人）
JS_ONLY = {"home", "products", "deck-pane", "deck-stat", "live", "flip",
           "rv", "rv-seq", "in", "on", "sel", "show", "open", "done"}


def main() -> int:
    css = load_css()
    pages = sorted(STANDALONE.glob("*.html"))
    pages = [p for p in pages if not p.name.startswith("_")]
    if not pages:
        print(f"[错误] 没找到页面：{STANDALONE}/*.html")
        return 2

    defined = css_classes(css)
    all_html = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in pages)

    # ── A 类名覆盖 ────────────────────────────────────────────────
    section("A 类名覆盖（HTML 用到的每个类都要有样式）")
    used = html_classes(all_html)
    missing = sorted(c for c in used - defined if c not in JS_ONLY)
    detail = ""
    if missing:
        for c in missing:
            n = len(re.findall(r'class="[^"]*\b' + re.escape(c) + r'\b', all_html))
            here = [p.name for p in pages
                    if re.search(r'class="[^"]*\b' + re.escape(c) + r'\b',
                                 p.read_text(encoding="utf-8", errors="ignore"))][:4]
            detail += f".{c} —— 出现在 {n} 处（{', '.join(here)}）\n"
    chk(not missing,
        f"{len(used)} 个类全部有样式定义",
        f"{len(missing)} 个类在 CSS 里没有定义（就是它们让区块退回默认样式）",
        detail)

    # 反向：主题里定义了一堆类，但一个页面都没用到 —— 只提醒，不算错
    unused = sorted(c for c in defined - used
                    if not c.startswith(("faq", "clog", "prose", "dlv", "pl"))
                    and c not in JS_ONLY)
    if unused:
        print(f"  · 提示：{len(unused)} 个样式类当前页面没用到（多为子页/弹窗共用，正常）")

    # ── B 深色可读 ────────────────────────────────────────────────
    section("B 深色可读（共享的更新日志样式不许写死浅色）")
    try:
        sys.path.insert(0, str(TOOLS))
        import build_changelog as bclog          # noqa: PLC0415
        clog = bclog.CLOG_CSS
        bare = []
        for line in clog.splitlines():
            if "#" not in line:
                continue
            # 十六进制只允许出现在 var(--x, #兜底) 里
            stripped = re.sub(r"var\([^)]*\)", "", line)
            if re.search(r"#[0-9a-fA-F]{3,8}\b", stripped):
                bare.append(line.strip())
        chk(not bare,
            "更新日志样式全部走 var(--clog-*, 浅色兜底)，深色页自动跟着走",
            "更新日志样式里写死了色值（在深色官网上会变成黑字压黑底）",
            "\n".join(bare))

        chk("--clog-strong" in css and "--clog-body" in css,
            "深色主题给了 --clog-* 一组值",
            "深色主题没定义 --clog-*，更新日志会退回浅色兜底值（看不见）")
    except Exception as exc:                     # noqa: BLE001
        chk(False, "", f"读不到 CLOG_CSS：{exc}")

    # ── C 无 JS 兜底 ──────────────────────────────────────────────
    section("C 无 JS 兜底（关掉脚本不能是一片空白）")
    chk(".js .rv{" in css.replace(" ", "").replace("\n", "")
        or ".js  .rv" in css or re.search(r"\.js\s*\.rv\s*\{", css) is not None,
        "揭示动画挂在 .js 之下，脚本没跑起来时内容照常显示",
        "揭示动画默认把元素设成透明 —— 关 JS 的访客会看到整块空白")
    chk(not re.search(r"(?<!\.js )\.rv\s*\{[^}]*opacity\s*:\s*0", css),
        "没有裸写的 .rv{opacity:0}",
        "存在裸写的 .rv{opacity:0}（爬虫还会把它判成隐藏文字作弊）")

    # ── D 字体产物 ────────────────────────────────────────────────
    section("D 字体产物（子集在位，且覆盖得住标题）")
    fonts = STANDALONE / "fonts"
    need = ["serif-sc-700.woff2", "sans-sc-400.woff2", "sans-sc-500.woff2",
            "sans-sc-700.woff2", "manrope-var.woff2"]
    absent = [f for f in need if not (fonts / f).is_file()]
    total = sum((fonts / f).stat().st_size for f in need if (fonts / f).is_file())
    chk(not absent,
        f"5 个字体文件在位，合计 {total/1024:.0f} KB",
        f"缺字体文件：{', '.join(absent)}（页面会退回系统黑体）")

    if not absent:
        # 页面引用的路径必须真的能对上文件
        refs = set(re.findall(r"url\((fonts/[\w.-]+\.woff2)\)", css))
        bad_ref = sorted(r for r in refs if not (STANDALONE / r).is_file())
        chk(not bad_ref, f"CSS 里的 {len(refs)} 处字体路径都能对上文件",
            f"CSS 指向了不存在的字体：{', '.join(bad_ref)}")

        # 标题字符必须被宋体子集覆盖，否则会出现「一个字两种字体」
        try:
            from fontTools.ttLib import TTFont   # noqa: PLC0415
            f = TTFont(fonts / "serif-sc-700.woff2")
            cmap = set()
            for t in f["cmap"].tables:
                cmap |= set(t.cmap.keys())
            f.close()

            def heading_text(html: str) -> str:
                out = []
                for tag in ("h1", "h2", "h3", "h4"):
                    for m in re.finditer(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S):
                        out.append(re.sub(r"<[^>]+>", "", m.group(1)))
                return "".join(out)

            miss: dict[str, list[str]] = {}
            for p in pages:
                txt = heading_text(p.read_text(encoding="utf-8", errors="ignore"))
                gap = sorted({c for c in txt
                              if c.strip() and ord(c) > 0x2000 and ord(c) not in cmap})
                if gap:
                    miss[p.name] = gap
            detail = "\n".join(f"{k}: {' '.join(v)}" for k, v in miss.items())
            chk(not miss,
                "宋体子集覆盖了所有标题用字",
                "标题里有字不在宋体子集内（会出现一个字两种字体）——"
                " 重新跑 tools/build_fonts.py",
                detail)
        except ImportError:
            print("  · 跳过：没装 fontTools")

    # ── E 字体分工 ────────────────────────────────────────────────
    section("E 字体分工（大标题必须落在宋体）")
    chk("--ff-display" in css and "--ff-body" in css and "--ff-num" in css,
        "三族字体令牌都在（宋体标题 / 无衬线正文 / 数字拉丁）",
        "缺字体令牌，排版会退回单一系统字体")
    m = re.search(r"\bh1\s*\{([^}]*)\}", css)
    chk(bool(m) and "var(--ff-display)" in (m.group(1) if m else ""),
        "h1 用宋体那一族",
        "h1 没指定 --ff-display —— 中文大标题又会退回雅黑粗体")
    chk("@font-face" in css and "font-display:swap" in css,
        "自带字体带 font-display:swap（不会白屏等字体）",
        "缺 font-display:swap，弱网下会先白屏")

    print("\n" + "=" * 66)
    print(f"  通过 {passed} 项，失败 {failed} 项")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
