"""一次性脚本：给两套 QSS 里所有 :pressed 块补「文字下沉 1px」的按下反馈。

依据：Emil Kowalski《animate》的 press feedback（100–160ms 内的 scale 0.97）。
QPushButton 不自绘，Qt 里没有等价写法；用「上内边距 +1 / 下内边距 -1」逼近：
总高不变，按钮不跳，只有字往下让 1px。

注意：大多数 :pressed 块里只有 background/border，padding 是从基础选择器继承
来的 —— 所以要先读出基础块的 padding，再按同值改写后插进 pressed 块。
"""
import pathlib

NOTE = [
    "    /* 按下反馈：文字下沉 1px（上下内边距一加一减，总高不变）。",
    "       Emil 的 press feedback 是 scale(0.97)，但 QPushButton 不自绘，",
    "       Qt 里没有等价写法 —— 用这个最接近：按钮尺寸一格不动，只有字",
    "       往下让 1px，手感是「按下去了」，布局上是「什么都没发生」。 */",
]


def parse_blocks(lines):
    """返回 [(选择器, [块内行]), ...]。"""
    blocks, i = [], 0
    while i < len(lines):
        s = lines[i].strip()
        if s.endswith("{") and not s.startswith("/*"):
            sel = s[:-1].strip()
            j = i + 1
            body = []
            while j < len(lines) and lines[j].strip() != "}":
                body.append(lines[j])
                j += 1
            blocks.append((sel, body, i, j))
            i = j + 1
            continue
        i += 1
    return blocks


def find_padding(body):
    for b in body:
        s = b.strip()
        if s.startswith("padding:") and "/*" not in s:
            body_txt = s[len("padding:"):].strip().rstrip(";")
            parts = body_txt.split()
            if parts and parts[0].endswith("px"):
                return parts
    return None


def main():
    for fn in ("light.qss", "dark.qss"):
        p = pathlib.Path("gui_qt/styles") / fn
        lines = p.read_text(encoding="utf-8").splitlines()
        blocks = parse_blocks(lines)
        base_pad = {}
        for sel, body, _, _ in blocks:
            if ":pressed" in sel:
                continue
            pad = find_padding(body)
            if pad:
                base_pad[sel] = pad

        generic = base_pad.get("QPushButton", ["6px", "14px"])
        edits = []          # (行号, 插入内容)
        for sel, body, start, end in blocks:
            if not sel.endswith(":pressed"):
                continue
            if find_padding(body):
                continue
            base = sel[: -len(":pressed")]
            pad = base_pad.get(base) or base_pad.get(base.split(":")[0]) or generic
            top = int(pad[0][:-2])
            side = pad[1] if len(pad) > 1 else pad[0]
            ins = [f"    padding: {top + 1}px {side} {top - 1}px;"] + NOTE
            edits.append((end, ins))

        for end, ins in reversed(edits):
            lines[end:end] = ins
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{fn}: 补了 {len(edits)} 处按下反馈")


if __name__ == "__main__":
    main()
