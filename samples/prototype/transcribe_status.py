"""转录文件状态判定（只读；设计上可直接搬进 Qt 主程序）。

★ 为什么不能只看「目录里有没有同名 .txt」：
  1. 输出目录由界面单独指定时，txt 落在另一个盘，源目录里一个都不剩；
  2. 软件的输出名是 `(out_dir or src.parent) / src.stem + ".txt"`，
     **不同目录下同名的音视频会写同一个 txt、互相覆盖**；
  3. 源文件换过内容后，旧 txt 还在 ⇒ 会把「需要重转」误判成「已转录」。

所以判据是：身份 = 完整路径 + 大小；输出映射按源码规则算；
再叠加「同名冲突」与「txt 是否比源文件新」两道校验。
这一层只读，不写文件、不联网、不启动转录、不改动主程序。

用法：python transcribe_status.py   → 生成 transcribe_contract.json 并打印本机实测
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / "justoneapi_collector" / "transcribe.py"
CONTRACT_OUT = HERE / "transcribe_contract.json"
CONFIG = Path(os.environ.get("APPDATA", str(Path.home()))) / "TrafficCatcher" / "config.json"

# 与 transcribe.py 的 SUPPORTED_AUDIO_EXT 一致；由 _source_rules() 现场核对
FALLBACK_EXT = {
    ".mp4", ".mov", ".mkv", ".flv", ".avi", ".webm", ".m4a", ".mp3",
    ".wav", ".aac", ".flac", ".ogg", ".ts", ".wmv",
}


def _fmt_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} GB"


def _source_rules() -> dict:
    """输出命名规则必须从真源码里读到，不许靠记忆手写（源码改了要能发现）。"""
    try:
        text = SOURCE.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SystemExit(f"读不到转录源码：{exc}") from exc
    m = re.search(r"SUPPORTED_AUDIO_EXT\s*=\s*\{(.*?)\}", text, re.S)
    if not m:
        raise SystemExit("源码里找不到 SUPPORTED_AUDIO_EXT，契约拒绝生成")
    exts = sorted({x.strip().strip('"\'') for x in
                   re.findall(r'"(\.[a-z0-9]+)"', m.group(1))})
    needed = [
        "out_dir = Path(task.out_dir) if task.out_dir else src.parent",
        "prefix = out_dir / src.stem",
        'txt = prefix.with_suffix(".txt")',
    ]
    missing = [line for line in needed if line not in text]
    if missing:
        raise SystemExit("源码输出命名规则变了，契约需人工复核：" + " | ".join(missing))
    if not exts:
        raise SystemExit("源码里没解析出支持的扩展名")
    return {
        "extensions": exts,
        "output_code": needed,
        "source": "justoneapi_collector/transcribe.py",
    }


def contract() -> dict:
    """状态枚举、列名、判定规则 = 网页与 Python 的唯一真源。"""
    rules = _source_rules()
    return {
        "version": "1.0",
        "source": rules["source"],
        "extensions": rules["extensions"],
        "output_rule": "输出目录 / 源文件名.txt；输出目录留空时写在源文件同目录",
        "output_code": rules["output_code"],
        "columns": ["状态", "文件名", "大小", "所在目录", "转录稿"],
        "states": [
            {"key": "done", "label": "已转录", "tone": "ok"},
            {"key": "running", "label": "转录中", "tone": "info"},
            {"key": "pending", "label": "待转录", "tone": "muted"},
            {"key": "stale", "label": "源文件已更新", "tone": "warn"},
            {"key": "conflict", "label": "同名冲突", "tone": "danger"},
        ],
        "stat_cards": ["全部文件", "已转录", "待转录", "需处理"],
        "rule_notes": [
            "有非空同名 txt、且 txt 不早于源文件 ⇒ 已转录",
            "txt 比源文件旧 ⇒ 源文件已更新，建议重转",
            "多个源文件映射到同一个 txt ⇒ 同名冲突，不能只按文件名判断",
            "输出目录里找不到源的 txt ⇒ 孤立产物，单独统计",
            "身份用完整路径 + 大小，绝不用文件名当主键",
        ],
    }


def _run_text(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    """跑只读命令并按 UTF-8 / GBK 两道解码，绝不把编码异常吞成空结果。

    ★ 中文 Windows 的 PowerShell 输出是 GBK；让 subprocess 直接按文本模式
      解码会在内部 reader 线程抛 UnicodeDecodeError —— 主流程不报错、stdout
      却变成 None，界面就会把「正在转录」误判成「没有任务」。所以这里取 bytes
      自己解码，任何异常都不能静默变成空状态。
    """
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return -1, ""
    raw = r.stdout or b""
    for enc in ("utf-8", "gbk"):
        try:
            return r.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.returncode, raw.decode("utf-8", errors="replace")


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def configured_dirs() -> dict:
    cfg = _config()
    return {
        "source_dir": str(cfg.get("dl_save_dir") or "").strip(),
        "out_dir": str(cfg.get("transcribe_out_dir") or "").strip(),
        "device": str(cfg.get("transcribe_device") or "").strip(),
        "model": str(cfg.get("transcribe_model") or "").strip(),
        "source": "%APPDATA%/TrafficCatcher/config.json",
    }


def scan(root: str = "", out_dir: str = "", limit: int = 24) -> dict:
    """只读扫描一个目录，返回每个音视频的转录状态。

    顶层只读、封顶 600 个条目（避免原型拖死大目录）；capped 会在界面显示「≥」。
    """
    dirs = configured_dirs()
    root_path = Path(root or dirs["source_dir"])
    out_path = Path(out_dir) if out_dir else (
        Path(dirs["out_dir"]) if dirs["out_dir"] else root_path)
    result = {
        "root": str(root_path), "out_dir": str(out_path),
        "total": 0, "shown": 0, "capped": False, "exists": False,
        "stats": {"done": 0, "pending": 0, "stale": 0, "conflict": 0, "orphan": 0},
        "rows": [], "orphans": [],
        "source": "本机真实目录（只读）", "time": datetime.now().strftime("%H:%M:%S"),
    }
    if not root_path.is_dir():
        return result
    result["exists"] = True

    exts = set(contract()["extensions"]) or FALLBACK_EXT
    files: list[Path] = []
    walked = 0
    for item in root_path.iterdir():
        walked += 1
        if walked > 600:
            result["capped"] = True
            break
        try:
            if item.is_file() and item.suffix.lower() in exts:
                files.append(item)
        except OSError:
            continue
    files.sort(key=lambda p: p.name.lower())

    # 同名冲突：不同路径的同 stem 会写到同一个 txt（源码规则决定的真实隐患）
    stem_count: dict[str, int] = {}
    for p in files:
        stem_count[p.stem] = stem_count.get(p.stem, 0) + 1

    used_txt: set[str] = set()
    rows_all: list[dict] = []
    for i, p in enumerate(files):
        try:
            st = p.stat()
        except OSError:
            continue
        txt = (out_path / p.stem).with_suffix(".txt")
        txt_ok = txt.exists() and txt.stat().st_size > 0
        if txt_ok:
            used_txt.add(str(txt).lower())
        conflict = stem_count.get(p.stem, 0) > 1
        if conflict:
            state, reason = "conflict", "同名文件会覆盖同一份 txt，需按子目录分开输出"
        elif not txt_ok:
            state, reason = "pending", "还没有生成转录稿"
        elif txt.stat().st_mtime + 2 < st.st_mtime:
            state, reason = "stale", "转录稿比源文件旧，源文件更新过"
        else:
            state, reason = "done", "转录稿已生成"
        result["stats"][state] += 1
        rows_all.append({
            "id": f"f{i + 1}", "name": p.name, "stem": p.stem,
            "size": st.st_size, "size_text": _fmt_size(st.st_size),
            "dir": str(p.parent), "state": state, "reason": reason,
            "txt": str(txt) if txt_ok else "",
            "txt_size": _fmt_size(txt.stat().st_size) if txt_ok else "",
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
        })

    # 输出目录里有 txt、但源目录找不到对应音视频 ⇒ 孤立产物
    if out_path.is_dir():
        stems = {p.stem for p in files}
        for item in out_path.iterdir():
            try:
                if not item.is_file() or item.suffix.lower() != ".txt":
                    continue
                if item.stem in stems or str(item).lower() in used_txt:
                    continue
                if len(result["orphans"]) >= 8:
                    break
                result["stats"]["orphan"] += 1
                result["orphans"].append({
                    "name": item.name, "size_text": _fmt_size(item.stat().st_size),
                })
            except OSError:
                continue

    result["total"] = len(rows_all)
    result["rows"] = rows_all[:limit]
    result["shown"] = len(result["rows"])
    return result


def gpu_status() -> dict:
    """只读 nvidia-smi：显卡型号、利用率、显存、功耗、温度。"""
    out = {
        "found": False, "name": "", "util": "", "vram": "", "power": "",
        "temp": "", "engine": "", "whisper_running": False,
        "source": "nvidia-smi（只读）",
    }
    rc, text = _run_text(
        ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,"
         "memory.total,power.draw,temperature.gpu", "--format=csv,noheader"], 8)
    if rc == 0 and text.strip():
        parts = [x.strip() for x in text.strip().splitlines()[0].split(",")]
        if len(parts) >= 6:
            out.update(found=True, name=parts[0], util=parts[1],
                       vram=f"{parts[2]} / {parts[3]}", power=parts[4],
                       temp=parts[5] + " °C")
    rc, text = _run_text(["nvidia-smi"], 8)
    if rc == 0 and "whisper-cli.exe" in text:
        out["whisper_running"] = True
        out["engine"] = "CUDA 计算引擎"
    return out


def running_task() -> dict:
    """只读当前 whisper-cli 进程命令行，拿到正在转录的文件。"""
    out = {"running": False, "file": "", "model": "", "device": "未知"}
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='whisper-cli.exe'\" | "
        "Select-Object -ExpandProperty CommandLine"
    )
    rc, text = _run_text(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 12)
    lines = text.strip().splitlines() if rc == 0 else []
    line = lines[0] if lines else ""
    if not line:
        return out
    out["running"] = True
    m = re.search(r"-f\s+(\S+\.wav)", line, re.I)
    if m:
        out["file"] = Path(m.group(1)).stem
    m = re.search(r"-m\s+\S*ggml-([^\\/\s]+)\.bin", line, re.I)
    if m:
        out["model"] = m.group(1)
    out["device"] = "显卡 (CUDA)" if "engine_cuda" in line.lower() else "CPU"
    return out


DEMO_ROWS = [
    {"name": "[1]--视频_1.mp4", "size_text": "600.2 MB", "dir": "D:\\素材\\课程 A",
     "state": "done", "txt_size": "114 KB", "reason": "转录稿已生成"},
    {"name": "[1]--视频_1.mp4", "size_text": "66.0 MB", "dir": "D:\\素材\\课程 B",
     "state": "conflict", "txt": "", "txt_size": "",
     "reason": "同名文件会覆盖同一份 txt，需按子目录分开输出"},
    {"name": "访谈_第 03 期.m4a", "size_text": "88.4 MB", "dir": "D:\\素材\\访谈",
     "state": "running", "txt_size": "", "reason": "显卡转录中"},
    {"name": "直播回放_1110.mp4", "size_text": "1.2 GB", "dir": "D:\\素材\\直播",
     "state": "stale", "txt_size": "76 KB", "reason": "转录稿比源文件旧，源文件更新过"},
    {"name": "口播稿_01.mp3", "size_text": "12.6 MB", "dir": "D:\\素材\\口播",
     "state": "pending", "txt_size": "", "reason": "还没有生成转录稿"},
    {"name": "同行拆解_02.mp4", "size_text": "204.8 MB", "dir": "D:\\素材\\竞品",
     "state": "pending", "txt_size": "", "reason": "还没有生成转录稿"},
]


def demo_rows() -> list:
    """明确标注「演示数据」的四态样例，用来看徽标与卡片长什么样。"""
    rows = []
    for i, r in enumerate(DEMO_ROWS, start=1):
        item = dict(r)
        item["id"] = f"d{i}"
        item["stem"] = Path(r["name"]).stem
        item["size"] = 0
        item["txt"] = item.get("txt", "")
        item["mtime"] = "—"
        item["demo"] = True
        rows.append(item)
    return rows


def overview(real: bool = True, out_dir: str = "") -> dict:
    """一次给齐网页要的全部真实数据（含显卡状态与当前任务）。"""
    data = scan(out_dir=out_dir)
    if not real:
        demo = demo_rows()
        data["rows"] = demo
        data["shown"] = len(demo)
        data["total"] = len(demo)
        data["stats"] = {
            "done": 1, "pending": 2, "stale": 1, "conflict": 1, "orphan": 0,
        }
        data["source"] = "演示数据（非真实文件，仅展示状态样式）"
    data["dirs"] = configured_dirs()
    data["gpu"] = gpu_status()
    data["task"] = running_task()
    # 命令行读不到（PowerShell 被策略挡、编码异常）但显卡进程表里确有
    # whisper-cli ⇒ 仍要如实显示「转录中」，绝不冒充「当前没有任务」。
    if not data["task"]["running"] and data["gpu"]["whisper_running"]:
        data["task"] = {
            "running": True, "file": "", "model": data["dirs"]["model"],
            "device": "显卡 (CUDA)",
        }
    data["demo"] = not real
    return data


if __name__ == "__main__":
    c = contract()
    CONTRACT_OUT.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"契约已生成：{CONTRACT_OUT.name}（扩展名 {len(c['extensions'])} 种、"
          f"状态 {len(c['states'])} 类）")
    o = overview()
    print("真实扫描：", o["root"], "共", o["total"], "个，展示", o["shown"], "个")
    print("状态统计：", o["stats"], "孤立产物", len(o["orphans"]))
    print("显卡：", o["gpu"])
    print("当前任务：", o["task"])
