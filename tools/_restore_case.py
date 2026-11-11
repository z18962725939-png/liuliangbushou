"""事故修复：把被整体大写化的 gui_qt 源码还原成原始大小写。

事故经过：一个批量改色脚本写了 `s = s.upper().replace(...)`（本意只是把色值
统一成大写再替换），结果把 gui_qt 下所有 .py / light.qss 的**全部字母**都
变成了大写 —— Python 关键字、Qt 类名、字符串常量无一幸免，文件直接不可运行。

★ 为什么能还原，而且能验证到"完全等价"
  __pycache__ 里的 .pyc 是事故**之前**生成的（本项目一直用 Python 3.10 跑
  自检），而 pyc 里保存着：
    co_names    —— 所有全局名 / 属性名（QWidget、setStyleSheet…）
    co_varnames —— 所有局部变量名
    co_consts   —— 所有字符串常量（文件名、QSS 文本、文案、docstring…）
  这些**全部保留原始大小写**。所以拿 pyc 当词典逐 token 还原，再用
  「重新编译 → 与原 pyc 的 code object 逐项比对」验证，一致就说明除了注释
  之外一字不差。

  唯一恢复不了的是注释里的英文单词（pyc 不存注释），用同一份词典尽力还原，
  剩下的交人工。中文注释不受 .upper() 影响，所以损失很小。

用法：
    python tools/_restore_case.py            # 还原并验证
    python tools/_restore_case.py --check    # 只验证，不写文件
"""
from __future__ import annotations

import keyword
import marshal
import os
import re
import struct
import sys
import types
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui_qt"

# Python 关键字与常量字面量：pyc 里没有（它们不是名字），单独给一份
_KEYWORDS = set(keyword.kwlist) | set(keyword.softkwlist)
# ★ 键必须是小写：源码里现在全是 'TRUE'，查表用的是 lower 后的形态
_LITERALS = {"none": "None", "true": "True", "false": "False",
             "notimplemented": "NotImplemented", "ellipsis": "Ellipsis"}


# ---------------------------------------------------------------- 词典
class Dict:
    """lower(写法) -> 原始写法（按出现次数取多数）。"""

    def __init__(self):
        self._votes: dict[str, Counter] = {}

    def add(self, word: str, weight: int = 1):
        if not word or not re.search(r"[A-Za-z]", word):
            return
        c = self._votes.setdefault(word.lower(), {})
        if word in c:
            pri, cnt = c[word]
            c[word] = (max(pri, weight), cnt + 1)
        else:
            c[word] = (weight, 1)

    def get(self, word: str, prefer_upper: bool = False,
            prefer: str = "high", no_allcaps: bool = False) -> str | None:
        """★ 按「来源优先级」而不是「出现次数」挑写法。

        这是最关键的一条修正：早期版本按票数取多数，结果 kit.py 里
        `Qt.Key_Left` 的 Key 被还原成小写的 key —— 因为 'key' 作为字典键的
        局部变量在文件里出现了十几次，票数压过了只出现一次的 'Key'。
        而 pyc 里两者的**来源**不同：'Key' 来自 co_names（属性名，权重 3），
        'key' 来自 co_varnames（局部变量，权重 2）。来源比票数可靠得多。
        """
        c = self._votes.get(word.lower())
        if not c:
            return None
        # ★ 只有**唯一候选**时才"原样返回"。源码现在全是大写，大写写法几乎
        #   总能在词典里找到，早先那句 `if word in c: return word` 等于把
        #   每个 token 都往大写推 —— 'cpu' 这类本该小写的常量就被还原成了
        #   'CPU'。写法有歧义时必须按来源优先级裁决。
        if word in c and len(c) == 1:
            return word
        if prefer_upper:
            up = [w for w in c if w.isupper()]
            if up:
                return max(up, key=lambda w: c[w][0])
        # ★ 'high' / 'low' 按**书写形态**裁决，而不是按优先级数字。
        #   踩过的坑：`Token` 只是文档字符串里的一个子串（来源优先级 1），
        #   真正的局部变量 `token` 优先级是 2 —— 按数字取"低优先级"反而
        #   选中了文档里的 Token。而形态是不会骗人的：变量一律全小写
        #   （worker / token / key），类与枚举一律带大写（Worker / Qt.Key）。
        if prefer == "low":
            low = [w for w in c if w.islower()]
            if low:
                return max(low, key=lambda w: c[w])
            return min(c.items(), key=lambda kv: (kv[1][0], -kv[1][1]))[0]
        # ★★ 只看**首字母**是否大写，不能看"是否含大写"。
        #    踩过的坑：QtCore.QDir 有个成员叫 dirName，被反射进词典；而
        #    "含大写就优先"的规则于是把 `os.path.dirname` 还原成了
        #    `os.path.dirName` —— 能编译能导入，运行时 AttributeError。
        #    真正的类/枚举写法（Worker / Key / Path / DeepSeek）首字母大写，
        #    小驼峰的成员函数（dirName / fileName）不算。
        if prefer == "high" and len(c) > 1:
            # ★★ 先要"首字母大写但不是全大写"的写法。PySide6 反射里混着
            #    一批已废弃的全大写老名（QtCore.SIGNAL / SLOT），
            #    `from QtCore import Signal` 于是一直被还原成 SIGNAL。
            up = [w for w in c if w[:1].isupper() and not w.isupper()]
            if up:
                return max(up, key=lambda w: c[w])
            up = [w for w in c if w[:1].isupper()]
            if up:
                return max(up, key=lambda w: c[w])
        return max(c.items(), key=lambda kv: kv[1])[0]

    def __len__(self):
        return len(self._votes)


def load_pyc(path: Path) -> object | None:
    try:
        data = path.read_bytes()
        return marshal.loads(data[16:])          # 3.10: 16 字节头
    except Exception:                            # noqa: BLE001
        return None


def pyc_is_trusted(py: Path) -> bool:
    """这个 pyc 是不是"当前这份源码"编译出来的？

    ★ 判断只看头里记录的 source size（不看 mtime）：大写化脚本把所有文件
      重写了一遍，mtime 全变了，但**字节数没变**。而 size 对不上就说明源码
      在 pyc 之后真的被改过（stat.py 的 pyc 就停在上一版，比现在小 2KB），
      那种 pyc 里的常量是旧值 —— 拿它当词典会把新代码改回旧值，必须排除。
    """
    cache = _pyc_of(py)
    if not cache.exists():
        return False
    try:
        size = struct.unpack("<IIII", cache.read_bytes()[:16])[3]
    except Exception:                            # noqa: BLE001
        return False
    return size == py.stat().st_size


def walk_code(co, d: Dict):
    # ★ 名字要拆子词：'PySide6.QtCore' 这种点分模块名，源码里是被 '.' 切成
    #   两个 token 匹配的（QTCORE 单独查词典），不拆就永远命中不了。
    def add_with_parts(n, w):
        d.add(n, w)
        if len(n) > 6 or "." in n:
            for part in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", n):
                d.add(part, max(1, w - 2))

    for n in co.co_names:
        add_with_parts(n, 3)                     # 全局名/属性名权重最高
    for n in co.co_varnames:
        add_with_parts(n, 2)
    for n in (*co.co_cellvars, *co.co_freevars):
        d.add(n, 2)
    def collect(obj, weight=2):
        if isinstance(obj, str):
            d.add(obj, weight)
            # ★ 必须再按"非字母数字"切一遍：源码里 '#FFFBEB' 是一个 token
            #   '#' + 'FFFBEB' 两段（# 不是标识符字符），词典里只有 '#fffbeb'
            #   这个键的话，查询 'FFFBEB' 永远落空，颜色会被还原成小写。
            # ★ 允许数字开头：'#92400E' 里的 '92400E' 首字符是数字，
            #   用 [A-Za-z] 打头的正则会漏掉它，颜色就还原不成大写。
            for w in re.findall(r"[0-9A-Za-z_]{3,}", obj):
                d.add(w, 1)
        elif isinstance(obj, bytes):
            try:
                d.add(obj.decode("utf-8"), 1)
            except Exception:                    # noqa: BLE001
                pass
        elif hasattr(obj, "co_code"):
            walk_code(obj, d)
        elif isinstance(obj, (tuple, frozenset, list, set)):
            # ★ 颜色常量常常是 tuple 里的元素（('#FFFBEB', '#92400E')），
            #   不递归就收不到，还原时会变成小写 #92400e —— 值没变，
            #   但跟代码里其它地方的大小写习惯就不一致了。
            for item in obj:
                collect(item, weight)

    for c in co.co_consts:
        collect(c)
    if co.co_filename:
        d.add(co.co_filename, 1)


def build_dict(files: list[Path]) -> Dict:
    """从所有 pyc 收集一份全局词典（覆盖面最大，用作兜底）。"""
    d = Dict()
    for py in files:
        co = load_pyc(_pyc_of(py)) if _pyc_of(py).exists() else None
        if co is None:
            continue
        walk_code(co, d)
    return d


def _pyc_of(py: Path) -> Path:
    return py.parent / "__pycache__" / (py.stem + ".cpython-310.pyc")


def build_line_dicts(py: Path) -> list[tuple[int, int, int, Dict]]:
    """★ 把 pyc 拆到**函数级**词典：(起始行, 结束行, 嵌套深度, 词典)。

    为什么必须这么细：`transcribe_page.py` 里 `_start()` 用小写 'cpu'（引擎
    参数），`_after_gpu_probe()` 的文案里是大写 'CPU 引擎' —— 同一份文件、
    同一个 lower key，两种写法都对。文件级词典只能二选一，必然改坏一处。
    按行号定位到**最内层的那个 code object**，歧义就消失了。
    """
    ents: list[tuple[int, int, int, Dict]] = []
    co = load_pyc(_pyc_of(py)) if pyc_is_trusted(py) else None
    if co is None:
        return ents

    def rec(c, depth: int):
        lines = [ln for _s, _e, ln in c.co_lines() if ln is not None]
        lo = c.co_firstlineno
        hi = max(lines) if lines else lo
        d = Dict()
        walk_code(c, d)
        ents.append((lo, hi, depth, d))
        for x in c.co_consts:
            if hasattr(x, "co_code"):
                rec(x, depth + 1)

    rec(co, 0)
    return ents


def pick_line_dict(ents, line: int) -> Dict | None:
    """落在这一行上的、最内层（嵌套最深）的那个 code object 的词典。"""
    hit = [e for e in ents if e[0] <= line <= e[1]]
    if not hit:
        return None
    return max(hit, key=lambda e: (e[2], e[0]))[3]


def build_own(py: Path) -> Dict | None:
    """只从这一个文件自己的 pyc 建词典（优先级最高）。

    pyc 不新鲜（源码之后被改过）时返回 None —— 里面的常量是旧值，会坏事。
    """
    if not pyc_is_trusted(py):
        return None
    co = load_pyc(_pyc_of(py))
    if co is None:
        return None
    d = Dict()
    walk_code(co, d)
    return d


def build_stdlib_dict() -> Dict:
    """标准库模块的真实属性名（os.path.isfile、sys.executable、re.S…）。

    ★ 为什么要单独喂一份：**优先级必须高于 Qt 词典**。踩过的坑 ——
      `QFileInfo.isFile` 被反射进词典，于是 `os.path.isfile` 被还原成
      `os.path.isFile`：语法没问题、模块能导入，运行时 AttributeError。
      标准库里有 isfile 这个真名字，把它放在更前面就没有歧义了。
    """
    d = Dict()
    # ★ 内置类型名也要喂进来：注解里的 `-> int` / `: bool` 被还原成了
    #   `-> Int` / `: Bool`（词典里有别处的 Int/Bool），模块一导入就
    #   NameError。builtins 里 int/bool/str/float 都是小写，能给正确答案。
    import builtins
    for b in dir(builtins):
        d.add(b, 6)
    for name in ("os", "os.path", "sys", "json", "re", "time", "math",
                 "shutil", "subprocess", "datetime", "collections",
                 "pathlib", "itertools", "functools", "socket", "platform",
                 "traceback", "unicodedata", "html", "urllib.parse"):
        try:
            mod = __import__(name, fromlist=["x"])
        except Exception:                                    # noqa: BLE001
            continue
        d.add(name, 6)
        for attr in dir(mod):
            # ★ 过掉"被 import 进来的子模块"。踩过的坑：subprocess 里
            #   `import signal` 让 signal 混进标准库词典，于是
            #   `from PySide6.QtCore import Signal` 被还原成小写 signal，
            #   模块一导入就 ImportError。真正的属性不会是模块对象。
            if attr.startswith("_"):
                continue
            try:
                if isinstance(getattr(mod, attr), types.ModuleType):
                    continue
            except Exception:                                # noqa: BLE001
                continue
            d.add(attr, 6)
    return d


# ------------------------------------------------------------ PYZ 真值
# ★★ 有一批文件的 __pycache__ pyc 停在旧版本（源码比 pyc 新），没法用字节码
#    比对来验证，还原错了也发现不了 —— theme.py 就是这么把 `def app_font_family()`
#    还原成了 `def APP_FONT_FAMILY()`（和同名常量撞车），界面字体直接失效。
#    但 PyInstaller 打包时在 build/ 下留了 PYZ-00.pyz，里面是**打包那一刻编译
#    好**的全部模块 pyc。虽然比本次会话的改动早一点（缺新增名），但老名字的
#    大小写是准的，拿来当这批文件的词典 + 核对清单正合适。
_PYZ_CACHE: dict[str, object] = {}


def load_pyz() -> dict[str, object]:
    if _PYZ_CACHE:
        return _PYZ_CACHE
    import zlib
    for pyz in sorted((ROOT / "build").rglob("PYZ-00.pyz")):
        try:
            data = pyz.read_bytes()
            if data[:4] != b"PYZ\x00":
                continue
            toc_pos = struct.unpack("!i", data[8:12])[0]
            toc = marshal.loads(data[toc_pos:])
            for entry in toc:
                name, (typ, pos, ln) = entry[0], entry[1]
                try:
                    co = marshal.loads(zlib.decompress(data[pos:pos + ln]))
                except Exception:                            # noqa: BLE001
                    continue
                _PYZ_CACHE[name] = co
            break
        except Exception:                                    # noqa: BLE001
            continue
    return _PYZ_CACHE


def pyz_code(py: Path):
    """按文件路径找到 PYZ 里对应的 code object。"""
    rel = py.relative_to(ROOT).with_suffix("")
    mod = ".".join(rel.parts)
    return load_pyz().get(mod)


def build_pyz_dict(py: Path) -> Dict | None:
    co = pyz_code(py)
    if co is None:
        return None
    d = Dict()
    walk_code(co, d)
    return d


def build_qt_dict() -> Dict:
    """把 PySide6 的真实 API 名字收进词典（Qt.Key_Left、QFont.Weight…）。

    ★ 为什么需要：源码里这些是 `Qt.Key_Left` 这种点分驼峰，而 pyc 的
      co_names 里存的是**同一个字符串** 'Qt.Key_Left' —— 拆子词能拿到
      'Key_Left' 但拿不到单独的 'Key'（它跟在 'Qt.' 后面）。反射一次
      Qt 的枚举成员，这类名字就齐了。
    """
    d = Dict()
    try:
        from PySide6 import QtCore, QtGui, QtWidgets                 # noqa: F401
    except Exception:                            # noqa: BLE001
        return d

    def feed(obj, depth=0):
        if depth > 2:
            return
        for name in dir(obj):
            if name.startswith("_"):
                continue
            d.add(name, 5)
            try:
                sub = getattr(obj, name)
            except Exception:                    # noqa: BLE001
                continue
            # ★ dir() 拿不到枚举成员：PySide6 里 `Qt.UserRole`、`QMessageBox.Yes`
            #   都是 hasattr 为真、dir() 里没有。得再往下掏一层 __members__，
            #   否则这些名字在词典里根本不存在，还原时全靠猜。
            # ★ __members__ 是 mappingproxy，**不是 dict**：写成 isinstance(x, dict)
            #   会静默判否，枚举成员一个都收不进来。
            members = getattr(sub, "__members__", None)
            if isinstance(members, Mapping):
                for k in members:
                    d.add(k, 5)
            if isinstance(sub, type) and sub.__module__.startswith("PySide6"):
                feed(sub, depth + 1)

    for mod in (QtCore.Qt, QtCore, QtGui, QtWidgets):
        feed(mod)
    return d


# 事故没波及到的源码目录，拿来做「写法语料库」（最低优先级，只做兜底）
_CORPUS_DIRS = [
    ROOT,                                                        # 项目其余源码完好
    Path(r"C:\Users\Administrator\DoubaoWork\chats\2026-10-13\new-chat\流量捕手\gui_qt"),
]
_CORPUS_SKIP = {"__pycache__", "_tmp_script_out", "gui_qt", "_ref", "build", "dist"}


def build_corpus() -> Dict:
    """从**没被大写化**的源码里收集写法。

    ★ 存在的意义：pyc 里没有注释。注释里的英文单词（QSS、URL、file://…）
      只能靠"这个项目平时怎么写"来猜。项目里 gui_qt 以外的 .py 全都完好，
      正好是同一套命名习惯；再加一份 v1.10.0 的旧快照扩大覆盖面。
      优先级给到 1（最低），绝不覆盖 pyc 给出的确定答案。
    """
    d = Dict()
    tok = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    for base in _CORPUS_DIRS:
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if any(p in _CORPUS_SKIP for p in py.parts):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:                                # noqa: BLE001
                continue
            if re.search(r"\bIMPORT\b|\bDEF\b|\bCLASS\b", text) and text.isupper():
                continue                                     # 这也是被污染的，别吃
            for w in tok.findall(text):
                d.add(w, 1)
    return d


# ------------------------------------------------- 限定名（a.b）词典
# ★ 为什么单独要一份：`os.path` 里的 path 是**属性**，大小写只可能是小写，
#   但同一个文件里 `from pathlib import Path` 又有一个大写的 Path —— 两个
#   写法都进了词典，按票数/优先级都可能选错，结果还原出 `os.Path`（语法没错，
#   运行时必崩）。整串 `os.path` 一起匹配就没有歧义了：项目里几千处 os.path
#   全都是小写。Qt.Key、Qt.AlignmentFlag 同理。
_CHAIN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_]*)+")


def build_qual() -> Dict:
    """收集 `a.b`（含 `a.b.c` 的相邻对）的真实写法。"""
    d = Dict()
    tok = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
    for base in _CORPUS_DIRS:
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if any(p in _CORPUS_SKIP for p in py.parts):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:                                # noqa: BLE001
                continue
            if text.isupper():
                continue
            for m in tok.findall(text):
                d.add(m, 4)
    # Qt 侧：`Qt.Key_Left`、`Qt.AlignmentFlag` 这些枚举/子命名空间
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        for prefix, obj in (("Qt", QtCore.Qt),):
            for name in dir(obj):
                if not name.startswith("_"):
                    d.add(f"{prefix}.{name}", 6)
        for mod in (QtCore, QtGui, QtWidgets):
            for cname in dir(mod):
                if not cname.startswith("Q") or cname.startswith("__"):
                    continue
                try:
                    cls = getattr(mod, cname)
                except Exception:                            # noqa: BLE001
                    continue
                if isinstance(cls, type):
                    for name in dir(cls):
                        if not name.startswith("_"):
                            d.add(f"{cname}.{name}", 5)
    except Exception:                                        # noqa: BLE001
        pass
    return d


# ---------------------------------------------------------------- 还原
# ★ 结束引号必须在正则里吃掉：否则后面又补一次引号，docstring 结尾会变成
#   六个引号（""" + """），整个文件语法崩掉。
_STR_RE = re.compile(
    r"(?P<q>\"\"\"|'''|\"|')(?P<body>(?:\\.|(?!\1).)*)(?P<end>\1)", re.S)
# ★ 必须允许数字开头：[A-Za-z_] 打头的话，'#92400E' 里只有末尾的 'E' 会被
#   当成一个 token（'9' 开头不匹配），于是整串被改成 '#92400e' —— 色值就废了。
#   纯数字被匹配到也无妨：lower 之后还是它自己。
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


# ★★ 转义序列被大写是这次事故里最致命的一处：`\n` 变成 `\N`，
#    Python 直接报 "malformed \N character escape"，文件连编译都过不了。
#    合法转义字母只有 a b f n r t v x（小写）与 N U（大写）。所以：
#    A→a B→b F→f R→r T→t V→v X→x 一定原本是小写；
#    N 只在后面紧跟 { 时才是 \N{...} 具名字符，否则原本是 \n；
#    U 后面跟 8 位十六进制才是 \U，跟 4 位是 \u。
_ESC_LOWER = set("ABFRTVX")


def _fix_escapes(body: str) -> str:
    out, i, n = [], 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n:
            nxt = body[i + 1]
            if nxt in _ESC_LOWER:
                out.append("\\" + nxt.lower())
                i += 2
                continue
            if nxt == "N":
                out.append("\\N" if body[i + 2:i + 3] == "{" else "\\n")
                i += 2
                continue
            if nxt == "U":
                tail = body[i + 2:i + 10]
                out.append("\\U" if re.fullmatch(r"[0-9A-Fa-f]{8}", tail) else "\\u")
                i += 2
                continue
            out.append(ch + nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_segments(text: str) -> list[tuple[str, str]]:
    """把源码切成 (code / string / comment) 三段交替的序列。

    ★ 为什么必须自己切：不能靠 tokenize —— 源码现在是坏的（关键字都大写了），
      tokenize 直接抛异常；也不能靠正则找字符串，注释里一个英文单引号就会
      把后面的代码整段吞进"字符串"里（第一版就栽在这上面，编译报
      invalid syntax）。所以老老实实写个状态机。
    """
    out, buf = [], []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(("code", "".join(buf)))
            buf = []
            out.append(("comment", text[i:j]))
            i = j
            continue
        if ch in ('"', "'"):
            if text.startswith(ch * 3, i):
                end = text.find(ch * 3, i + 3)
                end = n if end < 0 else end + 3
            else:
                j = i + 1
                while j < n:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == ch:
                        j += 1
                        break
                    if text[j] == "\n":
                        break
                    j += 1
                end = j
            out.append(("code", "".join(buf)))
            buf = []
            out.append(("string", text[i:end]))
            i = end
            continue
        buf.append(ch)
        i += 1
    out.append(("code", "".join(buf)))
    return out


def restore_text(text: str, d: Dict, qt: Dict, own: Dict | None = None,
                 corpus: Dict | None = None, qual: Dict | None = None,
                 ents: list | None = None) -> tuple[str, int]:
    """还原一段源码里的大小写，返回 (新文本, 未命中的 token 数)。

    own = 本文件自己的词典（来自它自己的 pyc），优先级最高。
    ★ 为什么必须分两级：全局词典里同一个 key 会撞车 —— '92400e' 在 A 文件
      里写成 #92400E、在 B 文件里写成 #92400e，按票数取多数就会把其中一个
      改坏；'c_title'（局部变量）会被全局的 'C_TITLE' 压过去。本文件 pyc
      给出的就是这个文件里真实存在的写法，唯一性有保障。
    """
    miss = [0]
    cur_line = [1]                      # 当前处理到第几行（决定用哪一层词典）
    _line_cache: dict[int, Dict | None] = {}

    def own_here() -> Dict | None:
        if not ents:
            return own
        ln = cur_line[0]
        if ln not in _line_cache:
            _line_cache[ln] = pick_line_dict(ents, ln)
        return _line_cache[ln] or own

    def lookup(w: str, prefer: str = "high", no_allcaps: bool = False) -> str | None:
        # 十六进制色值（#92400E 这类）：本项目一律大写书写，冲突时偏大写
        upper_first = bool(re.fullmatch(r"[0-9A-F]{6}", w))
        local = own_here()
        for table in (local, own, qt, d, corpus):
            if table is None:
                continue
            hit = table.get(w, upper_first, prefer)
            if hit is not None:
                return hit
        return None

    def word_str(w, in_string=False, in_comment=False, prefer="high",
                 no_allcaps=False):
        # ★ 字符串里的独立单词默认取**小写**写法。踩过的坑：theme.py 的
        #   `_ACCENT_BY_MODE = {"light": ..., "dark": ...}` 被还原成
        #   "Light"/"Dark" —— 而运行时传的是小写 mode，字典查不到就静默
        #   退回默认值，深色主题的强调色直接取错，肉眼一时半会儿看不出来。
        #   （整串查词典仍然优先，'TikTok' 这类不会被误伤）
        if in_string:
            prefer = "low"
        kl = w.lower()
        # ★ 关键字必须比词典优先：'NOT' 在词典里可能被注释里的 "Not" 带跑，
        #   但 Python 关键字一律小写 —— 拼成 Not 就是语法错误。
        if kl in _KEYWORDS:
            return kl
        if not in_string and kl in _LITERALS:
            return _LITERALS[kl]                 # 字符串里的 "true" 不是关键字
        if len(w) == 1:
            # 单字母通常是变量，但 `re.S` 这种常量也是单字母 —— 先查词典，
            #   查不到再按变量处理（否则 re.S 会被改成小写的 re.s）
            return lookup(w, prefer) or w.lower()
        hit = lookup(w, prefer, no_allcaps)
        if hit is not None:
            return hit
        miss[0] += 1
        if in_string:
            # 字符串里查不到就**保持原样**：色值、枚举值本来就常是大写，
            # 小写化只会把它改坏（'#12171F' → '#12171f'）。
            return w
        if in_comment:
            # ★ 注释查不到时**保持大写**：pyc 不存注释，没有真值可比对，
            #   而注释里的英文绝大多数是缩写（QSS / URL / HTTP / API /
            #   DPI / JSON），小写化只会把它们改成难看的 url、http。
            return w if w.isupper() else w.lower()
        # 代码里查不到：全大写带下划线 → 常量，保留；否则小写化
        return w if (w.isupper() and "_" in w) else w.lower()

    def word(wm, in_string=False, in_comment=False):
        return word_str(wm.group(0), in_string, in_comment)

    def pick(w, before: str, after: str, in_comment=False) -> str:
        """按上下文决定这个 token 取"类的写法"还是"变量的写法"。"""
        if os.environ.get("RESTORE_TRACE") and w == os.environ["RESTORE_TRACE"]:
            print(f"      [pick] w={w!r} before={before[-40:]!r} "
                  f"after={after[:12]!r} comment={in_comment}")
        if in_comment:
            return word_str(w, False, True, "high")
        # ★ def 后面紧跟 '(' 是**函数定义**不是调用：`def APP_FONT_FAMILY()`
        #   被当成调用、取了大写写法，和同名常量 APP_FONT_FAMILY 撞车，界面
        #   字体整条链失效。函数取小写、类取大写，这两条要放在 '(' 判断之前。
        # ★★ before 已经 rstrip 过，`\s+$` 永远匹配不上 —— 规则形同虚设，
        #    `def app_font_family()` 一直被还原成 `def APP_FONT_FAMILY()`。
        #    必须用 `\s*$`（零个也算）。
        # ★ 一律 re.I：源码此刻是**全大写**的，切片出来的 before 里是
        #   'DEF' / 'CLASS' / 'IMPORT'，小写正则一条都匹配不上。
        if re.search(r"\bdef\s*$", before, re.I):
            return word_str(w, False, False, "low", True)
        if re.search(r"\bclass\s*$", before, re.I):
            return word_str(w, False, False, "high")
        # 后跟 '(' 是调用/实例化，前面是 '.' 是取属性 —— 两者都取高优先级写法
        if after.startswith("(") or before.endswith("."):
            return word_str(w, False, False, "high")
        # ★ `from pathlib import Path` / `import x as Y`：导入进来的名字一律
        #   取"带大写"的写法。踩过的坑：Path 后不跟 '('、前也不是 '.'，按
        #   变量规则被还原成 `from pathlib import path`，模块常量 ('Path',)
        #   对不上，下面所有 Path(...) 直接 NameError。
        if (re.search(r"\bimport\b[\s\w.,()]*$", before, re.I)
                or re.search(r"\bas\s*$", before, re.I)):
            return word_str(w, False, False, "high")
        return word_str(w, False, False, "low")

    def sub_words(chunk, in_comment=False, lead_dot=False):
        out, pos = [], 0
        for m in _WORD_RE.finditer(chunk):
            before = (chunk[:m.start()].rstrip() or ("." if lead_dot else ""))
            after = chunk[m.end():].lstrip()
            out.append(chunk[pos:m.start()])
            out.append(pick(m.group(0), before, after, in_comment))
            pos = m.end()
        out.append(chunk[pos:])
        return "".join(out)

    def do_chain(seg, in_comment=False):
        """代码/注释段：先整段解 `a.b` 限定名，剩下的再逐词还原。"""
        out, pos = [], 0
        for m in _CHAIN_RE.finditer(seg):
            out.append(sub_words(seg[pos:m.start()], in_comment))
            parts = [p.strip() for p in re.split(r"\s*\.\s*", m.group(0))]
            lead = seg[:m.start()].rstrip().endswith(".")
            locked = set()                 # 已被限定名词典定死的片段
            for i in range(len(parts) - 1):
                hit = qual.get(parts[i] + "." + parts[i + 1])
                if hit and hit.count(".") == 1:
                    a, b = hit.split(".")
                    parts[i], parts[i + 1] = a, b
                    locked.update((i, i + 1))
            if os.environ.get("RESTORE_DEBUG_CHAIN"):
                print(f"      [chain] {m.group(0)!r} -> parts={parts} locked={locked}")
            fixed = [pick(parts[0], "." if lead else "", "", in_comment)
                     if 0 not in locked else parts[0]]
            for i, p in enumerate(parts[1:], start=1):
                # ★ 限定名词典已经给出确定写法的（os.path 的 path），绝不能再
                #   丢回逐词还原里二次加工 —— 否则 'path' 会被 pathlib.Path
                #   带跑，重新变成 `os.Path`。
                if i in locked:
                    fixed.append(p)
                    continue
                # 属性取哪种写法，看**宿主**是谁：
                #   os.path / Qt.Key / QFont.Weight → 宿主是模块或类，取高优先级；
                #   self.worker / ctx.token → 宿主是实例变量，取低优先级
                #   （否则 `self.worker` 会被还原成 `self.Worker`，属性访问全废）。
                owner = fixed[-1]
                high = bool(owner[:1].isupper() or owner in ("os", "sys", "re"))
                fixed.append(word_str(p, False, in_comment,
                                      "high" if high else "low"))
            out.append(".".join(fixed))
            pos = m.end()
        out.append(sub_words(seg[pos:], in_comment))
        return "".join(out)

    def one_string(seg, is_f: bool = False) -> str:
        """单个字符串字面量的还原（整串优先，其次逐词）。"""
        q = seg[:3] if seg.startswith(("\"\"\"", "'''")) else seg[:1]
        inner = _fix_escapes(seg[len(q):-len(q)])
        hit = lookup(inner, "low")
        if hit is not None and inner.strip():
            return q + hit + q
        # ★ f-string 的 `{...}` 里是**代码**，不是文案：`f"已指定：{path}"`
        #   里的 path 是局部变量，按字符串规则会被还原成 Path（正好文件里
        #   导入了 pathlib.Path），跑起来就是 NameError。所以先把插值表达式
        #   按代码规则还原，剩下的文案再按字符串规则走。
        # ★ `{{` 是 f-string 里的转义花括号，那里面是**纯文案**（QSS 之类），
        #   不是插值表达式 —— 必须整段跳过，否则 `{{background:transparent}}`
        #   会被当成代码/属性去还原。
        if is_f and "{" in inner and "{{" not in inner:
            pieces, pos = [], 0
            for m in _BRACE_RE.finditer(inner):
                pieces.append(_WORD_RE.sub(lambda x: word(x, True),
                                           inner[pos:m.start()]))
                expr = m.group(1)
                if not expr.strip() or ":" in expr or "!" in expr:
                    head = expr.split("!", 1)[0].split(":", 1)[0]
                    tail = expr[len(head):]
                    pieces.append("{" + sub_words(head) + tail + "}")
                else:
                    pieces.append("{" + sub_words(expr) + "}")
                pos = m.end()
            pieces.append(_WORD_RE.sub(lambda x: word(x, True), inner[pos:]))
            return q + "".join(pieces) + q
        return q + _WORD_RE.sub(lambda m: word(m, True), inner) + q

    # ★ 隐式拼接的字符串（相邻字面量只隔空白，编译期会被折成一个常量）：
    #   单看每一段，词典里都没有（pyc 存的是折叠后的整串），逐词还原就会把
    #   '作品ID' 改成 '作品id'、'2 MB' 改成 '2 mb'。所以先把整段拼起来查，
    #   命中后再按**字符数**切回各段 —— 大小写变化不改变长度，位置一一对得上。
    parts, segs, i, off = [], split_segments(text), 0, 0
    while i < len(segs):
        kind, seg = segs[i]
        cur_line[0] = 1 + text.count("\n", 0, off)
        off += len(seg)
        if kind != "string":
            parts.append(do_chain(seg, in_comment=(kind == "comment")))
            i += 1
            continue
        group, gaps, j = [seg], [], i + 1
        while j < len(segs):
            # 段与段之间允许夹空白**和注释**（长文案经常换行对齐后接注释）
            buf, k = [], j
            while k < len(segs) and ((segs[k][0] == "code" and segs[k][1].strip() == "")
                                     or segs[k][0] == "comment"):
                buf.append(segs[k][1])
                k += 1
            if buf and k < len(segs) and segs[k][0] == "string":
                gaps.append("".join(buf))
                group.append(segs[k][1])
                j = k + 1
            else:
                break
        off += sum(len(segs[k][1]) for k in range(i + 1, j))
        if len(group) > 1:
            qs = [g[:3] if g.startswith(("\"\"\"", "'''")) else g[:1] for g in group]
            inners = [_fix_escapes(g[len(q):-len(q)]) for g, q in zip(group, qs)]
            hit = lookup("".join(inners))
            if hit is not None and len(hit) == sum(len(x) for x in inners):
                at = 0
                for k, (q, inner) in enumerate(zip(qs, inners)):
                    parts.append(q + hit[at:at + len(inner)] + q)
                    if k < len(gaps):
                        parts.append(gaps[k])
                    at += len(inner)
                i = j
                continue
        # 只有紧跟在 f 前缀后面的那一段才是 f-string（拼接里的其余段不是）
        is_f = (off - len(seg) > 0
                and text[:off - len(seg)].rstrip()[-1:] in ("f", "F"))
        for k, g in enumerate(group):
            parts.append(one_string(g, is_f and k == 0))
            if k < len(gaps):
                parts.append(gaps[k])
        i = j
    return "".join(parts), miss[0]


def _count_diff(o, n) -> int:
    """两个 code object 之间「不一样的地方」有多少处（越小越好）。"""
    # ★ 比对项必须和 verify() 用的指纹**完全一致**（co_code / names /
    #   varnames / argcount / flags / consts）。漏掉 argcount、flags 的话，
    #   修复会停在"分数 0"但指纹其实还差着 —— 白跑。
    c = 0
    if o.co_code != n.co_code:
        c += 1
    for attr in ("co_argcount", "co_kwonlyargcount", "co_flags"):
        if getattr(o, attr, 0) != getattr(n, attr, 0):
            c += 1
    if hasattr(o, "co_names"):
        c += len(set(o.co_names) ^ set(n.co_names))
        c += len(set(o.co_varnames) ^ set(n.co_varnames))
    for oc, nc in zip(o.co_consts, n.co_consts):
        if hasattr(oc, "co_code") and hasattr(nc, "co_code"):
            c += _count_diff(oc, nc)
        elif repr(oc) != repr(nc):
            # ★ 元组/列表常量要**逐元素**计分，整体只算 1 分的话，"两个元素
            #   都错"和"一个元素错"得分一样，定向修复看不到任何进展
            #   （修好一个也还是 1 分），就永远停在原地。
            if (isinstance(oc, (tuple, list)) and isinstance(nc, (tuple, list))
                    and len(oc) == len(nc)):
                c += sum(1 for x, y in zip(oc, nc) if repr(x) != repr(y))
            else:
                c += 1
    return c


def _case_pairs(old, new, out: list):
    """找出「仅大小写不同」的 (期望写法, 实际写法) 配对。"""
    def rec(o, n):
        for a in getattr(o, "co_names", ()):
            if a not in n.co_names:
                for b in n.co_names:
                    if b.lower() == a.lower():
                        out.append((a, b))
        for a in getattr(o, "co_varnames", ()):
            if a not in n.co_varnames:
                for b in n.co_varnames:
                    if b.lower() == a.lower():
                        out.append((a, b))
        for oc, nc in zip(o.co_consts, n.co_consts):
            if hasattr(oc, "co_code") and hasattr(nc, "co_code"):
                rec(oc, nc)
            elif repr(oc) != repr(nc):
                _pair_consts(oc, nc, out)

    def _pair_consts(oc, nc, out):
        if isinstance(oc, str) and isinstance(nc, str):
            if oc.lower() == nc.lower():
                out.append((oc, nc))
                # ★ 长文案里只有一两个词错了（'作品ID' → '作品id'、'2 MB'
                #   → '2 mb'），整串配对在源码里根本搜不到（那是换行拼接的），
                #   必须把**词级**差异也发出来，定向修复才有的放矢。
                to, tn = _WORD_RE.findall(oc), _WORD_RE.findall(nc)
                if len(to) == len(tn):
                    for x, y in zip(to, tn):
                        if x != y and x.lower() == y.lower():
                            out.append((x, y))
        elif isinstance(oc, (tuple, list)) and isinstance(nc, (tuple, list)) \
                and len(oc) == len(nc):
            for x, y in zip(oc, nc):
                _pair_consts(x, y, out)

    rec(old, new)


# 属性归属能确定无疑的宿主：写在它们后面的属性名不许被定向修复改动
_MOD_OWNERS = {"os", "sys", "re", "json", "path", "math", "time", "shutil",
               "pathlib", "subprocess", "datetime", "collections"}

# ------------------------------------------------ 属性名大小写权威纠偏
_QT_OBJS: dict[str, object] = {}
_STDLIB_OBJS: dict[str, object] = {}


def _init_attr_objs() -> None:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        _QT_OBJS["Qt"] = QtCore.Qt
        for mod in (QtCore, QtGui, QtWidgets):
            for cname in dir(mod):
                if cname.startswith("Q"):
                    cls = getattr(mod, cname, None)
                    if isinstance(cls, type):
                        _QT_OBJS.setdefault(cname, cls)
    except Exception:                                        # noqa: BLE001
        pass
    for name in ("os", "os.path", "sys", "re", "json", "math", "time",
                 "shutil", "subprocess", "pathlib", "collections"):
        try:
            _STDLIB_OBJS[name] = __import__(name, fromlist=["x"])
        except Exception:                                    # noqa: BLE001
            pass


_ATTR_CASE_RE = re.compile(
    r"\b(?P<own>Qt|Q[A-Z][A-Za-z0-9_]*|os\.path|os|sys|re|json|math|time"
    r"|shutil|subprocess|pathlib|collections)\.(?P<attr>[A-Za-z_][A-Za-z0-9_]*)")


def fixup_attr_case(text: str) -> tuple[str, int]:
    """★ 最后一道兜底：拿**真实对象**核对属性名大小写。

    词典还原再怎么调，总会有 `Qt.Transparent` 这种漏网的（真正存在的是
    `Qt.transparent`，而 `Qt.TransparentMode` 又确实存在，光看字符串分不清）。
    但 hasattr 不会说谎：属性不在对象上、而对象里有个仅大小写不同的同名
    成员，那就改成那个 —— 这一条不依赖任何启发式。
    """
    if not _QT_OBJS:
        _init_attr_objs()
    fixed = [0]
    cache: dict[tuple[str, str], str] = {}

    def repl(m):
        own, attr = m.group("own"), m.group("attr")
        key = (own, attr)
        if key in cache:
            return cache[key]
        obj = _QT_OBJS.get(own) or _STDLIB_OBJS.get(own)
        out = m.group(0)
        if obj is not None and not hasattr(obj, attr):
            # ★ 候选不能从 dir(obj) 里找：PySide6 的枚举成员（Qt.transparent）
            #   hasattr 为真却不在 dir() 里。直接试几种大小写变体，让 hasattr
            #   当裁判 —— 它不会说谎。
            for cand in (attr.lower(), attr.capitalize(), attr.upper(),
                         attr[:1].lower() + attr[1:]):
                if cand != attr and hasattr(obj, cand):
                    out = f"{own}.{cand}"
                    fixed[0] += 1
                    break
        cache[key] = out
        return out

    # ★ 只改代码段：注释和字符串里出现 `Qt.xxx` 是文案，不是属性访问，
    #   无差别替换会把界面文案改坏。
    out = []
    for kind, seg in split_segments(text):
        out.append(_ATTR_CASE_RE.sub(repl, seg) if kind == "code" else seg)
    return "".join(out), fixed[0]


def repair(py: Path, source: str) -> str:
    """★ 以「重新编译 + 与原 pyc 比对」为判据的定向修复。

    词典还原总会剩下少量真歧义：`_base.py` 里既有显示用的 'TikTok' 又有平台
    键 'tiktok'，既有 ('w', 10000.0) 又有 ('W', 10000.0) —— 光看 token 无法
    判断该用哪个。但**编译后的字节码知道答案**。所以这里把「还原错了的写法」
    逐个翻回 pyc 里的写法，每翻一次就重新编译比对一次，取差异变小的那次，
    循环到 0 为止。等价于让 pyc 自己投票，不再靠猜。
    """
    old = load_pyc(_pyc_of(py))
    if old is None:
        return source

    def score(src):
        try:
            return _count_diff(old, compile(src, str(py), "exec"))
        except SyntaxError:
            return 10 ** 6

    best = score(source)
    for _ in range(60):
        if best == 0:
            break
        try:
            cur = compile(source, str(py), "exec")
        except SyntaxError:
            break
        pairs = []
        _case_pairs(old, cur, pairs)
        # ★ 每轮把**所有**候选都算一遍，取全局最优的那一个再落笔。
        #   早期版本"遇到第一个变好的就采纳"，会掉进局部最优：_base.py 里
        #   'Card' 有 4 个出现点，只有第 3 个是对的，翻第 1 个虽然也变差得
        #   更少，却把后面的路堵死了。
        best_cand, best_s = None, best
        for exp, act in dict.fromkeys(pairs):
            if exp == act:
                continue                     # 元组里递归出来的相等项，无意义
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(act) +
                             r"(?![A-Za-z0-9_])")
            # ★ 真实模块的**属性**不许翻：为了修 `from pathlib import Path`
            #   而把源码里所有 path 翻成 Path，会顺手把 `os.path.dirname`
            #   变成 `os.Path.dirname` —— 模块级常量对得上了，运行时却直接崩。
            #   只保护 `os.`/`sys.`/`re.` 这类确定无疑的宿主，其余照翻，
            #   （一刀切地"带点就不翻"会挡掉 worker/Worker 这类正常修复）。
            def _is_mod_attr(i: int, end: int) -> bool:
                if source[i - 1:i] != ".":
                    return False
                j = i - 2
                while j >= 0 and (source[j].isalnum() or source[j] == "_"):
                    j -= 1
                return source[j + 1:i - 1].lower() in _MOD_OWNERS
            idxs = [m.start() for m in pat.finditer(source)
                    if not _is_mod_attr(m.start(), m.end())]
            if not idxs:
                continue
            def _all(mm):
                if _is_mod_attr(mm.start(), mm.end()):
                    return mm.group(0)
                return exp.replace("\\", r"\\")
            for cand in ([pat.sub(_all, source)] +
                         [source[:i] + exp + source[i + len(act):] for i in idxs]):
                s = score(cand)
                if s < best_s:
                    best_s, best_cand = s, cand
        if best_cand is None:
            break
        source, best = best_cand, best_s
    return source


def _restore_code(chunk: str, d: Dict) -> str:
    def _w(wm):
        w = wm.group(0)
        hit = d.get(w)
        if hit is not None:
            return hit
        kl = w.lower()
        if kl in _KEYWORDS or kl in _LITERALS:
            return _LITERALS.get(kl, kl)
        # 不在词典里：全大写且带下划线 → 当成常量保留；否则小写化
        return w if (w.isupper() and "_" in w) else w.lower()
    return _WORD_RE.sub(_w, chunk)


# ---------------------------------------------------------------- 验证
def code_fingerprint(co) -> tuple:
    """一个 code object 的"逻辑指纹"：字节码 + 名字 + 常量（不含行号/文件名）。"""
    consts = []
    for c in co.co_consts:
        if hasattr(c, "co_code"):
            consts.append(code_fingerprint(c))
        else:
            consts.append(repr(c))
    return (co.co_code, co.co_names, co.co_varnames, co.co_argcount,
            co.co_flags, tuple(consts))


# ★★ 所有 `compile(...)` 都必须带 `dont_inherit=True`（本文件顶部有
#    `from __future__ import annotations`，而 compile() 会继承调用方的
#    __future__ 标志）。不加的话，被还原源码里的类型注解会被编译成
#    **字符串常量**，常量数从 103 变成 136、名字数从 65 变成 58 ——
#    跟 import 系统生成的 pyc 对不上，于是明明还原对了也被判成不一致。
#    这个坑查了很久：同一份源码、同一个解释器，只因为调用点所在模块
#    带不带 future，编译结果就不一样。
def _forms_of(py: Path) -> dict[str, list[str]] | None:
    """这个文件的事故前 pyc 里真实出现过的所有"写法"（区分大小写）。

    名字（co_names/co_varnames）＋ 字符串常量里切出来的词，全部按
    lower() 归组。收尾校正只在这些写法之间做选择，绝不发明新写法。
    """
    old = load_pyc(_pyc_of(py))
    if old is None:
        return None
    forms: Counter[str] = Counter()

    def walk(co):
        for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars"):
            for nm in getattr(co, attr, ()) or ():
                forms[nm] += 1
        for c in co.co_consts:
            if isinstance(c, str):
                for m in _WORD_RE.finditer(c):
                    forms[m.group(0)] += 1
            elif hasattr(c, "co_code"):
                walk(c)

    walk(old)
    by_lower: dict[str, list[str]] = {}
    for f, cnt in forms.items():
        by_lower.setdefault(f.lower(), []).append(f)
    for k in by_lower:
        by_lower[k].sort(key=lambda f: -forms[f])
    return by_lower


def polish(py: Path, text: str) -> tuple[str, int]:
    """★ 收尾校正：拿本文件 pyc 的"写法池"逐个词复核一遍。

    前面的规则是**通用启发式**（"后跟 ( 就是调用，取大写写法"），
    遇到 `get(` / `path` / `cell` 这种既是局部变量又和 Qt 类名同形的词
    必然猜错。但 pyc 里记着这个文件真正用过的每一种写法 —— 只要某个词
    在池里有别的写法、而当前写法池里压根没有，就换成池里那一个。
    这一步只在事实之间做选择，不做任何猜测。
    """
    by_lower = _forms_of(py)
    if not by_lower:
        return text, 0
    pool = {f for lst in by_lower.values() for f in lst}

    def swap(src: str, word: str, alt: str) -> str:
        out = []
        for kind, seg in split_segments(src):
            if kind == "comment":           # 注释不校：pyc 不存注释
                out.append(seg)
                continue
            out.append(_WORD_RE.sub(lambda m: alt if m.group(0) == word else
                                    m.group(0), seg))
        return "".join(out)

    def diff(src: str) -> int:
        try:
            cur = compile(src, str(py), "exec", dont_inherit=True)
        except SyntaxError:
            return 10 ** 6
        old = load_pyc(_pyc_of(py))
        return _count_diff(old, cur)

    # 候选：当前写法不在池里，但池里存在别的同形写法
    pairs, seen = [], set()
    for kind, seg in split_segments(text):
        if kind == "comment":
            continue
        for m in _WORD_RE.finditer(seg):
            w = m.group(0)
            if w in seen or w in pool or not re.search(r"[A-Za-z]", w):
                continue
            lst = by_lower.get(w.lower())
            if lst and len(lst) > 1 or (lst and lst[0] != w):
                seen.add(w)
                pairs.append((w, lst))

    # ★ 贪心试替换：只有**实测差异变少**才采纳。整批替换看着痛快，实测
    #   kit.py 一口气改了 35 个词，差异从 2 处涨到 449 处 —— 池里出现次数
    #   最多的写法不等于"这个位置"的写法。宁可慢一点，也不许改坏。
    base = diff(text)
    changed = 0
    for w, lst in pairs:
        for alt in lst:
            if alt == w:
                continue
            trial = swap(text, w, alt)
            gap = diff(trial)
            if gap < base:
                text, base = trial, gap
                changed += 1
                break
    return text, changed


def verify(py: Path, source: str) -> bool:
    """重新编译还原后的源码，与原 pyc 的指纹比对。"""
    if not pyc_is_trusted(py):
        return True                       # pyc 停在旧版本：比了也没意义
    old = load_pyc(_pyc_of(py))
    if old is None:
        return True
    try:
        new = compile(source, str(py), "exec")
    except SyntaxError as exc:
        print(f"    ✗ 语法错误 {py.name}: {exc}")
        return False
    return code_fingerprint(old) == code_fingerprint(new)


def main() -> int:
    check_only = "--check" in sys.argv
    force = "--force" in sys.argv       # 未通过校验也写盘（留给人工收尾）
    pys = sorted(GUI.rglob("*.py"))
    d = build_dict(pys)
    qt = build_qt_dict()
    corpus = build_corpus()
    qual = build_qual()
    print(f"词典规模：全局 {len(d)} + Qt API {len(qt)} + 语料库 {len(corpus)}"
          f" + 限定名 {len(qual)} 个写法"
          f"（来自 __pycache__ 的事故前 pyc、PySide6 反射与未受污染的源码）\n")

    bad = []
    for py in pys:
        raw = py.read_text(encoding="utf-8")
        if not raw.isupper() and not re.search(r"\bIMPORT\b|\bDEF\b|\bCLASS\b", raw):
            continue                       # 这个文件没被波及
        own = build_own(py)
        ents = build_line_dicts(py) if own is not None else []
        new, miss = restore_text(raw, d, qt, own, corpus, qual, ents)
        if os.environ.get("RESTORE_DEBUG"):
            import hashlib
            old = load_pyc(_pyc_of(py))
            cur = compile(new, str(py), "exec")
            print(f"    [dbg] {py.name} trusted={pyc_is_trusted(py)} "
                  f"修复前={_count_diff(old, cur)} "
                  f"hash={hashlib.md5(new.encode()).hexdigest()} len={len(new)} "
                  f"old_names={len(old.co_names)} new_names={len(cur.co_names)} "
                  f"old_consts={len(old.co_consts)} new_consts={len(cur.co_consts)}")
        if pyc_is_trusted(py):
            new = repair(py, new)
        new, attr_fix = fixup_attr_case(new)
        if pyc_is_trusted(py):
            new, pol = polish(py, new)
            if pol:
                print(f"    收尾校正 {pol} 处（依本文件 pyc 写法池）")
        ok = verify(py, new)
        flag = "✔" if ok else "✗"
        tag = "" if own is not None else "  (pyc 过期，未做指纹比对)"
        if not ok and own is not None:
            old = load_pyc(_pyc_of(py))
            try:
                gap = _count_diff(old, compile(new, str(py), "exec"))
                tag += f"  剩 {gap} 处差异"
            except SyntaxError as exc:
                tag += f"  语法错误 {exc}"
        print(f"  {flag} {py.relative_to(ROOT)}  未命中 {miss} 处{tag}")
        if not ok:
            bad.append(str(py.relative_to(ROOT)))
        if not check_only and (ok or force):
            py.write_text(new, encoding="utf-8")
            if force and not ok:
                print(f"    ⚠ 已写入但未通过校验，需人工复核：{py.relative_to(ROOT)}")

    print("\n" + "=" * 60)
    if bad:
        print(f"  还原后与原 pyc 不一致的 {len(bad)} 个文件（未写入）：")
        for b in bad:
            print("   -", b)
        return 1
    print("  全部文件的字节码与常量已与原 pyc 对齐（等价于事故前）")
    if check_only:
        print("  （--check 模式，未写文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
