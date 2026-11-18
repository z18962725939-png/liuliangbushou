"""录制历史索引：把保存目录里的 mp4 列成一份可回放的清单。

★ 为什么单独一个模块（2026-11-18）
  录制链路（live_monitor）只管「录」，录完把 .mp4.part 改名成 .mp4 就撒手了。
  live_recordings() 只列「正在录」的任务，一停手就从登记表清掉；软件重启后
  对已经录好的文件一无所知。客户想「回看」时，文件躺在文件夹里，却没有任何
  入口能把它找出来、打开它。这个模块补的就是这一段：扫描保存目录 → 列出
  可回放文件 → 界面据此渲染「录制历史」表。

★ 纯标准库、无 Qt、无网络
  只做三件事：扫目录、读文件元数据、格式化显示。所以能脱离 GUI 单测，
  门禁可以直接造临时目录验证。UI 层负责表格与「系统播放器打开」。

安全边界：
  - 只列 *.mp4，**排除 *.part**（录制中/录失败的残片，打不开也不该给人看）；
  - 只列普通文件，跳过子目录与符号链接指向的目录；
  - 单个文件 stat 失败就跳过，不让一个坏文件毁掉整张表；
  - 只读，绝不修改/删除/移动任何录制文件。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 录制产物后缀（与 live_monitor 的 .mp4.part → .mp4 改名链保持一致）
_VIDEO_SUFFIXES = (".mp4",)


@dataclass(frozen=True)
class Recording:
    """一条可回放的录制记录。"""
    path: str          # 绝对路径，供 QDesktopServices 打开
    name: str          # 文件名（含后缀），展示用
    stem: str          # 不含后缀，通常是「主播名_时间戳」
    size: int          # 字节
    size_text: str     # 人类可读体积
    mtime: float       # 修改时间戳
    time_text: str     # 人类可读时间
    date_text: str     # 仅日期，便于分组/排序展示


def format_size(n: int) -> str:
    """字节 → 人类可读体积（与 video_download_page 的口径一致）。"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "-"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024 / 1024 / 1024:.2f} GB"


def scan_recordings(save_dir: str | os.PathLike | None,
                    *, limit: int = 200) -> list[Recording]:
    """扫描录制保存目录，返回按修改时间倒序的可回放文件列表。

    Args:
        save_dir: 目录路径；None / 空串 / 不存在的目录 → 返回空列表（不抛）。
        limit: 最多返回多少条，防止陈年目录里几千个文件把界面卡死。

    Returns:
        Recording 列表，最新的排最前。任何单个文件读失败都跳过它。
    """
    if not save_dir:
        return []
    try:
        root = Path(save_dir).expanduser()
    except Exception:                                        # noqa: BLE001
        return []
    if not root.is_dir():
        return []

    found: list[Recording] = []
    try:
        entries = list(root.iterdir())
    except OSError:                                          # 目录无权读等
        return []

    for p in entries:
        try:
            if not p.is_file():                              # 跳过目录/残片目录
                continue
            if p.suffix.lower() not in _VIDEO_SUFFIXES:      # 只要 mp4，天然排除 .part
                continue
            st = p.stat()
            if st.st_size <= 0:                              # 空文件不算有效录制
                continue
            dt = datetime.fromtimestamp(st.st_mtime)
            found.append(Recording(
                path=str(p.resolve()),
                name=p.name,
                stem=p.stem,
                size=int(st.st_size),
                size_text=format_size(st.st_size),
                mtime=float(st.st_mtime),
                time_text=dt.strftime("%Y-%m-%d %H:%M"),
                date_text=dt.strftime("%Y-%m-%d"),
            ))
        except (OSError, ValueError, OverflowError):         # 单个坏文件跳过
            continue

    found.sort(key=lambda r: r.mtime, reverse=True)
    return found[:max(0, int(limit))]


def latest_recording(save_dir: str | os.PathLike | None) -> Recording | None:
    """取最新的一条录制记录；没有就返回 None。供「录完提示产物」用。"""
    items = scan_recordings(save_dir, limit=1)
    return items[0] if items else None
