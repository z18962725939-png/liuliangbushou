"""转写文件状态判定 + 输出去重 + 源文件→文稿映射清单（v1.28.3）。

★ 为什么不能只看「目录里有没有同名 .txt」：
  1. 输出目录由界面单独指定时，txt 落在另一个盘，源目录里一个都不剩；
  2. 旧版输出名是 `(out_dir or src.parent) / src.stem + ".txt"`，
     **不同目录下同名的音视频会写同一个 txt、互相覆盖**（客户真实场景：
     四个 `[1]--视频_1.mp4` 大小各不相同）；
  3. 源文件换过内容后旧 txt 还在 ⇒ 会把「需要重转」误判成「已转录」。

所以：身份 = 完整路径 + 大小；每次成功转录都把「源文件→实际文稿」写进
`transcribe_manifest.json`；遇到同名且清单里已属于别的源，就给新文稿加短哈希
后缀，老稿保持原名不动（不影响 v1.28.2 及以前生成的文件）。

这一层不联网、不启动转录、不改任何媒体文件；清单读写作了容错，写失败绝不能
让一次成功的转录变成失败。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

# 五态的中文名与配色（深色/浅色各一套，Qt 侧直接取用）
STATES: dict[str, dict[str, str]] = {
    "done":     {"label": "已转录",       "light": "#087F5B", "dark": "#7BE0A8"},
    "running":  {"label": "转录中",       "light": "#0052D9", "dark": "#8FB8FF"},
    "pending":  {"label": "待转录",       "light": "#6B7280", "dark": "#9AA3B2"},
    "stale":    {"label": "源文件已更新", "light": "#B45309", "dark": "#F5B455"},
    "conflict": {"label": "同名冲突",     "light": "#C0392B", "dark": "#FF8E82"},
}

_LOCK = threading.Lock()
_MANIFEST_VERSION = 1


# ---------------------------------------------------------------- 基础工具

def app_dir() -> Path:
    """和 transcribe.py 用同一个 AppData 目录，避免两边各存一份。"""
    from . import branding
    return branding.app_data_dir()


def manifest_path() -> Path:
    return app_dir() / "transcribe_manifest.json"


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{int(num)} B" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def identity(src: Path, size: int | None = None) -> str:
    """身份＝完整路径（小写、规范化）＋大小。绝不用文件名当主键。"""
    try:
        p = Path(src).resolve()
    except OSError:
        p = Path(src)
    if size is None:
        try:
            size = Path(src).stat().st_size
        except OSError:
            size = -1
    return f"{str(p).lower()}|{size}"


def _key(path: Path) -> str:
    try:
        return str(Path(path).resolve()).lower()
    except OSError:
        return str(path).lower()


def _empty_manifest() -> dict:
    return {"version": _MANIFEST_VERSION, "records": {}, "by_identity": {}}


def load_manifest() -> dict:
    """清单损坏 / 读到一半绝不抛异常：重建空清单，界面仍能按文件名规则判定。"""
    p = manifest_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_manifest()
        records = data.get("records")
        by_id = data.get("by_identity")
        if not isinstance(records, dict) or not isinstance(by_id, dict):
            return _empty_manifest()
        return {"version": data.get("version", 1),
                "records": records, "by_identity": by_id}
    except (OSError, ValueError, TypeError):
        return _empty_manifest()


def save_manifest(data: dict) -> bool:
    """原子写（临时文件 + os.replace），避免转录中断留下半截 JSON。"""
    p = manifest_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
        return True
    except (OSError, ValueError, TypeError):
        try:
            tmp.unlink(missing_ok=True)                # type: ignore[union-attr]
        except (OSError, UnboundLocalError):
            pass
        return False


# ---------------------------------------------------------------- 输出去重

def txt_of(prefix: Path) -> Path:
    """whisper-cli 的 `-of prefix -otxt` 产出的是 `str(prefix) + ".txt"`。

    ★ 旧代码用 `prefix.with_suffix(".txt")`，遇到「第01集.最终版.mp4」这类
      stem 里带点的文件会被 Path 当成后缀替换掉，算出 `第01集.txt`，而真实
      产物叫 `第01集.最终版.txt` ⇒ 状态永远判成「待转录」。这里必须用拼接。
    """
    return Path(str(prefix) + ".txt")


def output_prefix(out_dir: Path, src: Path) -> Path:
    """给这次转录算一个不覆盖别人文稿的 `-of` 前缀。

    规则（保守优先，绝不破坏老文件）：
      · 同一个源文件重转 ⇒ 复用清单里记录的前缀，覆盖自己的旧稿；
      · 首次转录且原名没人占 ⇒ 保持 `stem`，和 v1.28.2 之前完全一致；
      · 原名已存在、且清单明确记录它属于**另一个**源文件 ⇒ 加 8 位短哈希；
      · 原名已存在但清单没记录（老版本产物）⇒ 仍用原名，不突然多出怪文件。
    """
    out_dir = Path(out_dir)
    src = Path(src)
    ident = identity(src)
    manifest = load_manifest()

    prev = manifest["by_identity"].get(ident)
    if isinstance(prev, str) and prev:
        # 重转同一文件：沿用自己的文稿名；输出目录换了才重新分配
        old = Path(prev)
        if old.parent == out_dir and str(prev).lower().endswith(".txt"):
            return Path(str(old)[:-4])

    base = out_dir / src.stem
    wanted = txt_of(base)
    owner = manifest["records"].get(_key(wanted))
    occupied = wanted.exists() or isinstance(owner, dict)
    belongs_to_other = (
        isinstance(owner, dict) and owner.get("identity") not in (None, ident)
    )
    if belongs_to_other or (occupied and not isinstance(owner, dict)
                            and _looks_like_other_file(wanted, src)):
        digest = hashlib.sha1(ident.encode("utf-8", errors="replace")).hexdigest()[:8]
        candidate = out_dir / f"{src.stem}-{digest}"
        n = 2
        while txt_of(candidate).exists():
            candidate = out_dir / f"{src.stem}-{digest}-{n}"
            n += 1
        return candidate
    return base


def _looks_like_other_file(txt: Path, src: Path) -> bool:
    """清单没记录、但同名 txt 明显不是本文件产物时才另起名字。

    这里刻意保守：宁可让极少数无清单的老稿被同名覆盖，也不能让客户
    一升级就发现「同一个文件突然多出三个带哈希的 txt」。
    """
    try:
        # 源文件名和 txt 完全同名、且源比 txt 新很多 ⇒ 可能是别的视频留下的稿
        return txt.stat().st_mtime + 2 < src.stat().st_mtime and \
            txt.stat().st_size < 8
    except OSError:
        return False


def record_result(src: Path, txt: Path, *, out_dir: Path | None = None,
                  model: str = "", device: str = "") -> bool:
    """转录成功后登记映射。写失败吞掉（不能让成功转录报错）。"""
    try:
        src, txt = Path(src), Path(txt)
        ident = identity(src)
        with _LOCK:
            data = load_manifest()
            rec = {
                "src": str(src), "src_size": src.stat().st_size,
                "stem": src.stem, "identity": ident,
                "out_dir": str(out_dir) if out_dir else str(txt.parent),
                "model": model, "device": device,
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
            # 旧记录指向别的文件名时清掉，避免一个源同时占两个 txt
            old = data["by_identity"].get(ident)
            if isinstance(old, str) and _key(Path(old)) != _key(txt):
                data["records"].pop(_key(Path(old)), None)
            data["records"][_key(txt)] = rec
            data["by_identity"][ident] = str(txt)
            data["version"] = _MANIFEST_VERSION
            return save_manifest(data)
    except (OSError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 状态判定

def planned_txt(src: Path, out_dir: Path, manifest: dict | None = None) -> Path:
    """清单里有记录就信清单；没有就按输出目录 + 源文件名的规则推算。"""
    data = manifest if manifest is not None else load_manifest()
    rec = data["by_identity"].get(identity(src))
    if isinstance(rec, str) and rec:
        return Path(rec)
    return txt_of(Path(out_dir) / Path(src).stem)


def classify(files: list[Path], out_dir: Path | str | None = None,
             running: Path | None = None, *, limit: int = 0) -> list[dict]:
    """给待转列表里的每个文件判状态（只读，不写文件）。

    out_dir 为空时按源码规则落在各自源文件同目录。
    running 是「当前正在转录」的那个文件（由界面传入）。
    """
    manifest = load_manifest()
    out_raw = str(out_dir).strip() if out_dir is not None else ""
    items = [Path(p) for p in files]
    running_key = _key(running) if running is not None else ""

    # 先按「预期文稿」分组，抓出同名会互相覆盖的组合
    planned: dict[str, Path] = {}
    groups: dict[str, list[str]] = {}
    for p in items:
        ident = identity(p)
        planned[ident] = planned_txt(p, Path(out_raw) if out_raw else p.parent,
                                     manifest)
        groups.setdefault(_key(planned[ident]), []).append(ident)

    rows: list[dict] = []
    for p in items:
        try:
            st = p.stat()
        except OSError:
            continue
        ident = identity(p, st.st_size)
        txt = planned[ident]
        txt_exists = txt.exists() and txt.stat().st_size > 0
        # 清单已给这个源分配了独立文稿名 ⇒ 不再算同名冲突
        has_own = isinstance(manifest["by_identity"].get(ident), str)
        # ★ 只要清单没给这个源分配过独立文稿名，而多个源又指向同一份 txt，
        #   就不能冒充「已转录」—— 哪怕那份 txt 已经存在（老版本留下的），
        #   也无从判断它到底属于谁。转一次之后清单会拆开，冲突自动消失。
        conflict = not has_own and len(groups.get(_key(txt), [])) > 1

        if running_key and _key(p) == running_key:
            state, reason = "running", "正在转录这个文件"
        elif conflict:
            state, reason = "conflict", "同名文件会写同一份文稿，将自动分开命名"
        elif not txt_exists:
            state, reason = "pending", "还没有生成转录稿"
        else:
            try:
                stale = txt.stat().st_mtime + 2 < st.st_mtime
            except OSError:
                stale = False
            if stale:
                state, reason = "stale", "文稿比源文件旧，源文件更新过，建议重转"
            else:
                state, reason = "done", "转录稿已生成"
        rows.append({
            "path": p, "name": p.name, "size": st.st_size,
            "size_text": human_size(st.st_size), "dir": str(p.parent),
            "txt": str(txt) if txt_exists else "",
            "txt_size": human_size(txt.stat().st_size) if txt_exists else "",
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            "state": state, "label": STATES[state]["label"], "reason": reason,
            "identity": ident,
        })
    if limit and len(rows) > limit:
        return rows[:limit]
    return rows


def stats(rows: list[dict]) -> dict:
    out = {"all": len(rows), "done": 0, "pending": 0, "running": 0,
           "stale": 0, "conflict": 0, "attention": 0}
    for r in rows:
        out[r["state"]] = out.get(r["state"], 0) + 1
    out["attention"] = out["stale"] + out["conflict"]
    return out


def note_text(rows: list[dict]) -> str:
    """给状态栏用的一句话：把「需处理」讲成人话。"""
    s = stats(rows)
    if not s["all"]:
        return "添加音视频后，这里会显示每个文件的转录状态"
    parts = [f"共 {s['all']} 个文件", f"已转录 {s['done']}",
             f"待转录 {s['pending']}"]
    if s["running"]:
        parts.append(f"转录中 {s['running']}")
    if s["attention"]:
        parts.append(f"需处理 {s['attention']}（源文件更新 / 同名冲突）")
    return " · ".join(parts)
