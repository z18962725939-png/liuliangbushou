"""还原器调试 2：递归定位「还原后 vs 原 pyc」到底哪里不一致。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _restore_case import (GUI, ROOT, build_dict, build_own,  # noqa: E402
                           load_pyc, restore_text)


def short(v, n=110):
    s = repr(v)
    return s if len(s) <= n else s[:n] + "…"


def cmp_code(old, new, path, out):
    for field in ("co_argcount", "co_kwonlyargcount", "co_flags", "co_code"):
        a, b = getattr(old, field, None), getattr(new, field, None)
        if a != b:
            out.append(f"{path}.{field}\n    原={short(a)}\n    新={short(b)}")
    for field in ("co_names", "co_varnames"):
        a, b = getattr(old, field), getattr(new, field)
        if a != b:
            only_a = [x for x in a if x not in b]
            only_b = [x for x in b if x not in a]
            out.append(f"{path}.{field} 多出={only_b} 少了={only_a}")
    a, b = old.co_consts, new.co_consts
    if len(a) != len(b):
        out.append(f"{path}.co_consts 长度 {len(a)} vs {len(b)}")
        return
    for i, (x, y) in enumerate(zip(a, b)):
        if hasattr(x, "co_code") and hasattr(y, "co_code"):
            cmp_code(x, y, f"{path}.consts[{i}]<{x.co_name}>", out)
        elif x != y:
            out.append(f"{path}.co_consts[{i}]\n    原={short(x)}\n    新={short(y)}")


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "widgets/alert.py"
    py = GUI / name
    d = build_dict(sorted(GUI.rglob("*.py")))
    raw = py.read_text(encoding="utf-8")
    new, miss = restore_text(raw, d, build_own(py))
    cache = py.parent / "__pycache__" / (py.stem + ".cpython-310.pyc")
    old = load_pyc(cache)
    try:
        cur = compile(new, str(py), "exec")
    except SyntaxError as exc:
        print("语法错误：", exc)
        return
    out = []
    cmp_code(old, cur, "<module>", out)
    print(f"{name}: 差异 {len(out)} 处，未命中 {miss} 个 token")
    for o in out[:12]:
        print("  " + o)


if __name__ == "__main__":
    main()
