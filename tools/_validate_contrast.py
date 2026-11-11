"""界面观感自检 · 对比度（impeccable colorize 的硬要求：不要靠眼睛判断对比）。

WCAG AA：正文 4.5:1，大字号（≥18px 或 ≥14px 粗体）与控件/图标 3:1。
禁用态没有要求（WCAG 明确豁免），所以 disabled 一律不进这张表。

★ 为什么要单独一个脚本
  明度差"看着差不多"和"算出来达标"经常不是一回事：#8B93A1 在白底上肉眼
  觉得挺清楚，实测只有 3.1:1 —— 客户在户外/笔记本低亮度下就是看不清。
  这类问题不会报错、不会崩，只会被当成"这软件字有点淡"，所以必须量。

★ 色值不从代码里硬抄：全部从两套 QSS 里按选择器抓，改了样式也不会失真。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui_qt" / "styles"
LIGHT = GUI / "light.qss"
DARK = GUI / "dark.qss"

_ok = 0
_bad = 0


def chk(cond: bool, ok_msg: str, bad_msg: str, extra: str = ""):
    global _ok, _bad
    if cond:
        _ok += 1
        print(f"  \033[32m✔\033[0m {ok_msg}" + (f"　{extra}" if extra else ""))
    else:
        _bad += 1
        print(f"  \033[31m✗\033[0m {bad_msg}" + (f"　{extra}" if extra else ""))


def section(title: str):
    print(f"\n\033[1m{title}\033[0m")


# ---------------------------------------------------------------- 计算
def _srgb(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb(r) + 0.7152 * _srgb(g) + 0.0722 * _srgb(b)


def ratio(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def block(text: str, selector: str) -> str:
    """抓一个选择器块的内容（只取第一个匹配的）。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", text, re.I)
    return m.group(1) if m else ""


def color_of(text: str, selector: str) -> str:
    m = re.search(r"(?<![a-z-])color\s*:\s*(#[0-9A-Fa-f]{6})", block(text, selector), re.I)
    return m.group(1) if m else ""


def bg_of(text: str, selector: str) -> str:
    b = block(text, selector)
    m = re.search(r"background\s*:\s*(#[0-9A-Fa-f]{6})", b, re.I)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 检查表
# (名字, 前景选择器, 背景选择器, 最低比值, 说明)
# 背景选择器写成 "QWidget" 时取的是页面底；写成 "QFrame#Card" 是卡片面。
LIGHT_PAIRS = [
    ("页面标题 on 画布", "QLabel#H1", "QWidget", 4.5),
    ("小节标题 on 卡片", "QLabel#H2", "QFrame#Card", 4.5),
    ("正文 on 画布", "QLabel#Body", "QWidget", 4.5),
    ("正文 on 卡片", "QLabel#Body", "QFrame#Card", 4.5),
    ("说明文字 on 卡片", "QLabel#Caption", "QFrame#Card", 4.5),
    ("元信息 on 卡片", "QLabel#Meta", "QFrame#Card", 4.5),
    ("分区标题 on 卡片", "QLabel#SectionLabel", "QFrame#Card", 4.5),
    ("大数字 on 卡片", "QLabel#StatValue", "QFrame#Card", 4.5),
    ("主按钮字 on 柠檬底", "QPushButton#Primary", "QPushButton#Primary", 4.5),
]

DARK_PAIRS = [
    ("页面标题 on 画布", "QLabel#H1", "QWidget", 4.5),
    ("小节标题 on 面板", "QLabel#H2", "QFrame#Panel", 4.5),
    ("正文 on 画布", "QLabel#Body", "QWidget", 4.5),
    ("正文 on 面板", "QLabel#Body", "QFrame#Panel", 4.5),
    ("说明文字 on 面板", "QLabel#Caption", "QFrame#Panel", 4.5),
    ("元信息 on 面板", "QLabel#Meta", "QFrame#Panel", 4.5),
    ("分区标题 on 面板", "QLabel#SectionLabel", "QFrame#Panel", 4.5),
    ("大数字 on 面板", "QLabel#StatValue", "QFrame#Panel", 4.5),
]


def run_pairs(name: str, text: str, pairs):
    section(f"{name}（WCAG AA：正文 4.5:1）")
    for label, fg_sel, bg_sel, floor in pairs:
        fg = color_of(text, fg_sel)
        if bg_sel == fg_sel:                      # 同一块：前景是 color，背景是 background
            bg = bg_of(text, bg_sel)
        else:
            bg = bg_of(text, bg_sel) or bg_of(text, "QWidget")
        if not fg or not bg:
            chk(False, "", f"{label}：取不到色值", f"fg={fg or '-'} bg={bg or '-'}")
            continue
        r = ratio(fg, bg)
        chk(r >= floor, f"{label} {r:.2f}:1",
            f"{label} 只有 {r:.2f}:1（要 {floor}:1）", f"{fg} on {bg}")


def main() -> int:
    print("=" * 62)
    print("  界面观感自检 · 对比度")
    print("=" * 62)
    light = LIGHT.read_text(encoding="utf-8")
    dark = DARK.read_text(encoding="utf-8")

    run_pairs("浅色主题", light, LIGHT_PAIRS)
    run_pairs("深色主题", dark, DARK_PAIRS)

    # 选区色：也要能看清（很多人忘了它是界面的一部分）
    section("选区（impeccable craft-floor：你没画的那部分也是设计）")
    for name, txt in (("浅色", light), ("深色", dark)):
        m = re.search(r"selection-background-color\s*:\s*(#[0-9A-Fa-f]{6})", txt, re.I)
        m2 = re.search(r"selection-color\s*:\s*(#[0-9A-Fa-f]{6})", txt, re.I)
        if m and m2:
            r = ratio(m2.group(1), m.group(1))
            chk(r >= 4.5, f"{name}选区文字 {r:.2f}:1",
                f"{name}选区文字只有 {r:.2f}:1", f"{m2.group(1)} on {m.group(1)}")
        else:
            chk(False, "", f"{name}主题没有定义选区色（会用系统蓝）")

    print("\n" + "=" * 62)
    print(f"  通过 {_ok} 项，失败 {_bad} 项")
    print("=" * 62)
    if _bad:
        print("  对比度未达标：请把偏的那几档色阶往上/往下调一档再跑一次。")
        return 1
    print("  全部达标：明暗两套主题的文字都达到 AA。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
