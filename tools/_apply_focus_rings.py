"""一次性脚本：给两套 QSS 里的按钮补「键盘焦点环」。

依据：impeccable craft-floor 的 browser surfaces ——
  「text selection, the caret, custom scrollbars, focus rings, underline
   offset, and the numerals in tabular data all ship with browser defaults
   that belong to no design system. Theme them from the palette.」
改版前 QLineEdit / QComboBox 有 :focus，QPushButton 一个都没有 —— 用 Tab
走一遍键盘，焦点落到哪个按钮上完全看不出来。

★ 为什么是「边框 +1px、内边距 -1px」而不是 outline
  1. Qt 的 QSS 不保证 outline 在 QPushButton 上真的画出来（文档里有这个属性，
     但没人验证过它在按钮上的效果 —— 不能拿客户的界面做试验品）。
  2. 直接加边框会把按钮撑大 2px，Tab 一圈下来整排按钮跟着抖。
     边框 +1 / 内边距 -1 的净尺寸是 0，实测 sizeHint 一模一样
     （1px+6px14px 与 2px+5px13px 都是 38×28）。
  无边框的按钮（导航项、幽灵按钮）则用「内边距 -2」抵掉 2px 边框，
  同样是零位移 —— 不用 margin:-2px，负外边距会让它在布局里比邻居窄 4px。

★ 焦点色沿用输入框那套：浅色用墨 #0B0E14，深色用柠檬 #C8F751。
"""
import pathlib
import re

FOCUS = {"light": "#0B0E14", "dark": "#C8F751"}

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGETS = {
    "light": ROOT / "gui_qt" / "styles" / "light.qss",
    "dark": ROOT / "gui_qt" / "styles" / "dark.qss",
}

BORDER_RE = re.compile(r"^border:\s*(\d+)px\s+solid\s+(#[0-9A-Fa-f]+)\s*;?$")
PAD_RE = re.compile(r"^padding:\s*(.+?)\s*;?$")


def shrink(padding: str, n: int) -> str:
    out = []
    for tok in padding.split():
        m = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", tok)
        if not m:                       # 0 / 其它单位：原样保留
            out.append(tok)
            continue
        v = float(m.group(1))
        out.append(f"{max(0, v - n):g}px")
    return " ".join(out)


def blocks(lines):
    """返回 [(选择器, 起始行, 结束行)]（结束行是 '}' 那一行）。"""
    out, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if s.endswith("{") and not s.startswith("/*") and not s.startswith("*/"):
            j = i + 1
            while j < len(lines) and lines[j].strip() != "}":
                j += 1
            out.append((s[:-1].strip(), i, j))
            i = j
        i += 1
    return out


def apply_one(path: pathlib.Path, mode: str) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    bs = blocks(lines)
    by_sel = {sel: (a, b) for sel, a, b in bs}
    inserts = []                        # (插到第几行之前, [文本行])
    for sel, a, b in bs:
        if not sel.startswith("QPushButton") or ":" in sel or "::" in sel:
            continue
        if f"{sel}:focus" in by_sel:
            continue                    # 已经有了就别重复加
        body = [ln.strip() for ln in lines[a + 1:b]]
        border = next((ln for ln in body if BORDER_RE.match(ln)), None)
        pad = next((ln for ln in body if PAD_RE.match(ln)), None)
        if border is None:
            # 无边框按钮：加 2px 边框，用「内边距 -2」抵掉
            if not pad:
                continue                # 连 padding 都没有（图标按钮）：不动，避免撑大
            pv = PAD_RE.match(pad).group(1)
            if all(re.fullmatch(r"0(px)?", t) for t in pv.split()):
                continue                # padding:0 再减就是负数，跳过
            focus = [f"{sel}:focus {{",
                     f"    border: 2px solid {FOCUS[mode]};",
                     f"    padding: {shrink(pv, 2)};",
                     "}"]
        else:
            w = int(BORDER_RE.match(border).group(1))
            focus = [f"{sel}:focus {{",
                     f"    border: {w + 1}px solid {FOCUS[mode]};"]
            if pad:
                focus.append(f"    padding: {shrink(PAD_RE.match(pad).group(1), 1)};")
            focus.append("}")
        # 插到 :hover 之后（没有 hover 就插到基础块之后），让焦点优先于悬浮
        at = b + 1
        for s2, a2, b2 in bs:
            if s2 == f"{sel}:hover":
                at = b2 + 1
                break
        inserts.append((at, focus))
    for at, text in sorted(inserts, key=lambda x: -x[0]):
        lines[at:at] = [""] + text
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(inserts)


if __name__ == "__main__":
    for mode, p in TARGETS.items():
        n = apply_one(p, mode)
        print(f"  {p.name}：补了 {n} 个 :focus 块")
