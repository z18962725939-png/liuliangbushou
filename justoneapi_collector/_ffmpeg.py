"""统一的 ffmpeg 定位（2026-11-02）。

直播录制 / 音视频抽取 / 转写都依赖 ffmpeg。从 1.26.3 起 ffmpeg 随安装包分发，
PyInstaller 6.x 的 one-folder 会把 datas 落到 ``<exe 同级>/_internal/ffmpeg/``
（冻结态下 sys._MEIPASS 即指向该 _internal 目录）。本模块把这几处都纳入查找，
确保打包后自动发现，新手开箱即用，不用再手动指定。

查找顺序：显式指定 → PATH → %APPDATA%/TrafficCatcher/ffmpeg →
           _MEIPASS/ffmpeg（冻结态）→ exe 同级/ffmpeg → exe 同级/_internal/ffmpeg
           → 常见安装位置（C:\\ffmpeg 等）。
"""
import os
import sys
import shutil
from pathlib import Path


def find_ffmpeg(explicit: str | None = None) -> str | None:
    """返回可用的 ffmpeg 可执行文件路径，找不到返回 None。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)

    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    found = shutil.which(name)
    if found:
        return found

    appdata = os.environ.get("APPDATA")
    exe_parent = Path(sys.executable).resolve().parent
    meipass = getattr(sys, "_MEIPASS", "")

    # 候选基目录（按优先级，越靠前越精确）
    bases: list[Path] = []
    if appdata:
        bases.append(Path(appdata) / "TrafficCatcher" / "ffmpeg")   # 私有目录
    if meipass:
        bases.append(Path(meipass) / "ffmpeg")                      # 冻结态 → _internal/ffmpeg
    bases.append(exe_parent / "ffmpeg")                             # 开发态 / 解压态
    bases.append(exe_parent / "_internal" / "ffmpeg")               # 打包后（one-folder）
    bases.append(Path("C:/ffmpeg/bin"))
    bases.append(Path("C:/ffmpeg"))

    for b in bases:
        cand = b / name
        if cand.exists():
            return str(cand)
    return None


def find_ffprobe(explicit: str | None = None) -> str | None:
    """ffprobe 通常和 ffmpeg 同目录；优先按 ffmpeg 推导，其次按同名查找。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
    ff = find_ffmpeg()
    if ff:
        probe = Path(ff).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if probe.exists():
            return str(probe)
    name = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    return shutil.which(name)
