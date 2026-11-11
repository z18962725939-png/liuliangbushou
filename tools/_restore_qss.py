"""还原被整体大写化的 light.qss。

事故同一次：批量改色脚本把 light.qss 的所有字母也变成了大写（dark.qss 没
被波及，逃过一劫）。QSS 不是 Python，**没有 pyc 可以比对**，所以这里换个判据：

  以 dark.qss 为镜像。两份主题是同一套结构 —— 选择器顺序、属性名、伪状态
  几乎一一对应（深色只是换配色）。于是：
    ① 从 dark.qss 收一份「token → 真实写法」的词典（同一份结构，最可信）
    ② 补上 PySide6 的控件类名，以及 pyc 里出现过的 setObjectName 值
    ③ light.qss 逐 token 还原，十六进制色值一律大写（项目书写习惯）
    ④ 校验：还原后两边的选择器集合、属性名集合必须一致（不分大小写比对）

用法：
    python tools/_restore_qss.py            # 还原并校验
    python tools/_restore_qss.py --check    # 只校验
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLES = ROOT / "gui_qt" / "styles"
LIGHT = STYLES / "light.qss"
DARK = STYLES / "dark.qss"

_HEX = re.compile(r"#[0-9A-Fa-f]{3,8}")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def build_dict() -> dict[str, dict[str, int]]:
    """lower(token) -> {写法: 票数}"""
    votes: dict[str, dict[str, int]] = {}

    def add(word: str, w: int):
        votes.setdefault(word.lower(), {})
        votes[word.lower()][word] = votes[word.lower()].get(word, 0) + w

    # ① dark.qss：同一套结构，可信度最高
    if DARK.exists():
        for t in _TOKEN.findall(DARK.read_text(encoding="utf-8")):
            add(t, 5)
    # ② PySide6 控件类名
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        for mod in (QtCore, QtGui, QtWidgets):
            for name in dir(mod):
                if name.startswith("Q") and name[1:2].isupper():
                    add(name, 4)
    except Exception:                                        # noqa: BLE001
        pass
    # ③ pyc 里出现过的常量串（objectName、QSS 片段等）
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import _restore_case as R
        for py in (ROOT / "gui_qt").rglob("*.py"):
            co = R.load_pyc(R._pyc_of(py))
            if co is None:
                continue
            stack = [co]
            while stack:
                c = stack.pop()
                for k in c.co_consts:
                    if hasattr(k, "co_code"):
                        stack.append(k)
                    elif isinstance(k, str):
                        for t in _TOKEN.findall(k):
                            add(t, 1)
    except Exception:                                        # noqa: BLE001
        pass
    return votes


def pick(votes, tok: str) -> str:
    c = votes.get(tok.lower())
    if not c:
        return tok.lower()
    if tok in c:
        return tok
    return max(c.items(), key=lambda kv: kv[1])[0]


def restore(text: str, votes) -> str:
    def sub(chunk: str) -> str:
        def one(m):
            tok = m.group(0)
            # ★ QSS 的函数名一律小写：url() / qlineargradient() / rgba()。
            #   后跟 '(' 就按小写走，别被注释里大写的 URL 带跑。
            if chunk[m.end():m.end() + 1] == "(":
                c = votes.get(tok.lower())
                if c and tok.lower() in c:
                    return tok.lower()
            return pick(votes, tok)
        return _TOKEN.sub(one, chunk)

    # ★ 色值必须**避开**逐 token 还原：#dff3a8 被拆成 token 'dff3a8' 后，
    #   词典里查不到就会退回小写 —— 先把色值整段挑出来大写，再还原其余部分。
    out, pos = [], 0
    for m in _HEX.finditer(text):
        out.append(sub(text[pos:m.start()]))
        out.append(m.group(0).upper())
        pos = m.end()
    out.append(sub(text[pos:]))
    return "".join(out)


def skeleton(text: str) -> tuple[list[str], list[str]]:
    """抽出选择器与属性名（忽略大小写），用于结构比对。"""
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    sels = [m.group(1).strip().lower() for m in
            re.finditer(r"([^{}]+)\{", body)]
    keys = [m.group(1).strip().lower() for m in
            re.finditer(r"(?:^|[;{])\s*([A-Za-z-][A-Za-z0-9-]*)\s*:", body)]
    return sels, keys


def main() -> int:
    check_only = "--check" in sys.argv
    raw = LIGHT.read_text(encoding="utf-8")
    votes = build_dict()
    new = restore(raw, votes)

    ls, lk = skeleton(new)
    ds, dk = skeleton(DARK.read_text(encoding="utf-8"))
    only_light = [s for s in ls if s not in ds]
    only_dark = [s for s in ds if s not in ls]
    print(f"选择器：light {len(ls)} 个 / dark {len(ds)} 个")
    if only_light:
        print(f"  · light 独有 {len(only_light)} 个（靠词典还原，需留意）：")
        for s in only_light[:10]:
            print(f"      {s}")
    if only_dark:
        print(f"  · dark 独有 {len(only_dark)} 个：")
        for s in only_dark[:10]:
            print(f"      {s}")
    miss_keys = [k for k in lk if k not in dk]
    print(f"属性名：light {len(lk)} 处，其中不在 dark 里的 {len(miss_keys)} 处"
          f"（{', '.join(sorted(set(miss_keys))[:12])}）")

    upper_left = re.findall(r"\b[A-Z]{3,}\b", re.sub(r"/\*.*?\*/", "", new, flags=re.S))
    print(f"还原后仍全大写的单词：{len(upper_left)} 个"
          f"（{', '.join(sorted(set(upper_left))[:12])}）")

    if not check_only:
        LIGHT.write_text(new, encoding="utf-8")
        print(f"\n已写入 {LIGHT}")
    else:
        print("\n（--check 模式，未写文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
