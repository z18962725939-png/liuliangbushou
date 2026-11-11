"""大写化事故还原后的**独立**校验。

`_restore_case.py` 只能对「pyc 还新鲜」的文件做字节码比对，gui_qt 下有
二十来个文件的 pyc 停在旧版本，比不了 —— 而这些文件恰恰没有任何真值可依。
所以另开一道检查，不依赖 pyc，只依赖"这个名字在真实模块里存不存在"：

  ① 语法：每个 .py 都能编译（大写化会把 `\\n` 变成 `\\N`，先过这一关）
  ② 标准库属性：`os.path.dirname`、`sys.exit` 这类**必须真实存在**
     （还原器曾把 dirname 还原成 dirName —— 能编译、能导入，运行时才炸）
  ③ Qt 属性：`Qt.Key_Return`、`QFrame.Shape` 这类必须能在 PySide6 反射到

用法：
    python tools/_validate_restore.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui_qt"

# 只查"确定性来源"：标准库模块的属性不存在就是写错了
_STDLIB = ["os", "os.path", "sys", "json", "re", "time", "math", "shutil",
           "subprocess", "datetime", "collections", "pathlib", "itertools",
           "functools", "typing", "socket", "platform", "traceback"]
_MODULES = {}
for _name in _STDLIB:
    try:
        _MODULES[_name] = __import__(_name, fromlist=["x"])
    except Exception:                                        # noqa: BLE001
        pass

_QT_MEMBERS: dict[str, set] = {}


def _members_of(obj) -> set:
    """★ 不能只用 dir()：PySide6 的枚举成员（Qt.UserRole、QMessageBox.Yes）
    hasattr 为真但不在 dir() 里，用 dir() 会满屏误报。"""
    names = set(dir(obj))
    for name in list(names):
        try:
            sub = getattr(obj, name)
        except Exception:                                    # noqa: BLE001
            continue
        mem = getattr(sub, "__members__", None)
        if isinstance(mem, dict):
            names |= set(mem)
    return names


try:
    from PySide6 import QtCore, QtGui, QtWidgets
    _QT_MEMBERS["Qt"] = _members_of(QtCore.Qt)
    for _mod in (QtCore, QtGui, QtWidgets):
        for _cname in dir(_mod):
            if not _cname.startswith("Q"):
                continue
            _cls = getattr(_mod, _cname, None)
            if isinstance(_cls, type):
                _QT_MEMBERS.setdefault(_cname, _members_of(_cls))
except Exception:                                            # noqa: BLE001
    pass

_ATTR_RE = re.compile(
    r"\b(?P<own>" + "|".join(re.escape(m) for m in
                             sorted(_MODULES, key=len, reverse=True)) +
    r")\.(?P<attr>[A-Za-z_][A-Za-z0-9_]*)")
_QT_RE = re.compile(r"\b(?P<own>Qt|Q[A-Z][A-Za-z0-9_]*)\.(?P<attr>[A-Za-z_][A-Za-z0-9_]*)")

# 跟着左括号的是调用（Qt.Tool 这种枚举不会带括号），行尾注释里的不查
_SKIP_ATTR_OK = {"__name__", "__file__"}


def check_file(py: Path) -> list[str]:
    errs = []
    try:
        src = py.read_text(encoding="utf-8")
    except Exception as exc:                                 # noqa: BLE001
        return [f"读取失败 {exc}"]
    try:
        compile(src, str(py), "exec", dont_inherit=True)
    except SyntaxError as exc:
        return [f"语法错误 行{exc.lineno}: {exc.msg}"]

    # ★ 模块名被局部变量遮蔽时不查：`platform.currentText` 里的 platform 是
    #   一个 QComboBox 局部变量，不是标准库 platform 模块。
    shadowed = set()
    for m in re.finditer(r"^\s*(?:for|def|class|async def)\s+([A-Za-z_]\w*)",
                         src, re.M):
        shadowed.add(m.group(1))
    for m in re.finditer(r"(?<![=!<>])([A-Za-z_]\w*)\s*(?:=[^=]|,|\))", src):
        shadowed.add(m.group(1))
    for m in re.finditer(r"^\s*from\s+\S+\s+import\s+(.+)", src, re.M):
        for part in m.group(1).split(","):
            shadowed.add(part.strip().split(" as ")[-1].strip())

    for lineno, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        for m in _ATTR_RE.finditer(code):
            own, attr = m.group("own"), m.group("attr")
            if attr in _SKIP_ATTR_OK or own.split(".")[0] in shadowed:
                continue
            mod = _MODULES.get(own)
            if mod is not None and not hasattr(mod, attr):
                errs.append(f"行{lineno}: {own}.{attr} 不存在")
        if _QT_MEMBERS:
            for m in _QT_RE.finditer(code):
                own, attr = m.group("own"), m.group("attr")
                members = _QT_MEMBERS.get(own)
                if members is not None and attr not in members:
                    # 后面跟 '(' 的是方法调用，PySide6 反射里方法也在 dir() 中，
                    # 仍然对不上就说明名字真被还原错了
                    errs.append(f"行{lineno}: {own}.{attr} 不在 PySide6 的成员里")
    return errs


def main() -> int:
    pys = sorted(GUI.rglob("*.py"))
    bad = 0
    total = 0
    for py in pys:
        errs = check_file(py)
        if errs:
            bad += 1
            total += len(errs)
            print(f"  ✗ {py.relative_to(ROOT)}")
            for e in errs[:8]:
                print(f"      {e}")
            if len(errs) > 8:
                print(f"      … 另有 {len(errs) - 8} 处")
    print("\n" + "=" * 60)
    if bad:
        print(f"  {len(pys)} 个文件中 {bad} 个有问题，共 {total} 处")
        return 1
    print(f"  {len(pys)} 个文件全部通过：语法可编译、标准库与 Qt 属性均真实存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
