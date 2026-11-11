"""还原器调试：看单个文件还原成什么样、为什么指纹对不上。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _restore_case import (GUI, ROOT, build_dict, code_fingerprint,  # noqa: E402
                           load_pyc, restore_text)

name = sys.argv[1] if len(sys.argv) > 1 else "widgets/alert.py"
py = GUI / name
pys = sorted(GUI.rglob("*.py"))
d = build_dict(pys)
raw = py.read_text(encoding="utf-8")
new, miss = restore_text(raw, d)
print("=== 还原后前 45 行 ===")
for i, line in enumerate(new.splitlines()[:45], 1):
    print(f"{i:4d} {line}")

cache = py.parent / "__pycache__" / (py.stem + ".cpython-310.pyc")
old = load_pyc(cache)
try:
    cur = compile(new, str(py), "exec")
except SyntaxError as exc:
    print("\n=== 语法错误 ===", exc)
    sys.exit(1)

print("\n=== 指纹差异 ===")
a, b = code_fingerprint(old), code_fingerprint(cur)
labels = ["co_code", "co_names", "co_varnames", "co_argcount", "co_flags", "co_consts"]
for lab, x, y in zip(labels, a, b):
    if x != y:
        print(f"  {lab}: 不一致")
        if lab in ("co_names", "co_varnames"):
            print("    原:", x[:12])
            print("    新:", y[:12])
        if lab == "co_consts":
            for i, (p, q) in enumerate(zip(x, y)):
                if p != q:
                    print(f"    [{i}] 原={str(p)[:90]!r}")
                    print(f"    [{i}] 新={str(q)[:90]!r}")
                    break
            else:
                print("    长度不同:", len(x), len(y))
        if lab == "co_code":
            print("    字节码长度:", len(x), len(y))
    else:
        print(f"  {lab}: 一致")
