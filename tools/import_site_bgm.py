#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本地音乐（FLAC / WAV / MP3 / M4A…）导入成官网背景音乐歌单。

用法（顺序就是播放顺序）：

    python tools/import_site_bgm.py "D:/Backup/Music/a.flac" "b.flac" "c.flac"
    python tools/import_site_bgm.py a.flac b.flac --titles "情网 · 半吨兄弟|乌兰巴托的夜 · 半吨兄弟 / 张茜|..."

产出，全部在 `官网素材/bgm/`：

    bgm-1.mp3 / bgm-2.mp3 / ...   网页友好的 MP3
    playlist.json                 歌单顺序、显示名、时长、来源

之后跑 `build_site.py --local` 与 `build_subpages.py`，播放器会把歌单内联进
首页和全部子页；正式发版时 `build_site.py` 负责把 mp3 与清单拷进 standalone/assets。

★ 为什么必须转码：用户给的原盘 FLAC 一首 27~49MB，三首 120MB，手机 4G
  下点播放要等几十秒才出声，部分安卓内置浏览器还根本不认 FLAC。统一转
  96kbps 44.1kHz 立体声 MP3（约 0.7MB/分钟），三首合计 9MB 左右；播放器
  `preload="none"` 且只设置当前一首的 src，所以单次点击最多下一首。
★ 为什么输出文件名用 bgm-N.mp3：中文、空格、全角括号在 CDN、微信内置
  浏览器、不同部署渠道的 URL 转义上各有各的坑，英文数字最稳；中文歌名
  放在 playlist.json 里，由播放器直接显示。
★ 旧的 bgm.mp3（程序生成合成曲）已移出产物，只留 .bak 备份；需要单曲
  兜底时跑 gen_site_bgm.py 重新生成即可。正常链路只用本歌单三首，
  三首全失效时播放器会落回纯 Web Audio 合成曲（不需要任何外部文件）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
OUT_DIR = ROOT / "官网素材" / "bgm"
PLAYLIST = OUT_DIR / "playlist.json"
FFMPEG = ROOT / "assets" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = ROOT / "assets" / "ffmpeg" / "ffprobe.exe"
BITRATE = "96k"
SUPPORTED = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".wma", ".ape"}


def exe(name: str) -> str:
    """优先用工程自带的 ffmpeg（打包环境与开发机一致），没有就找 PATH。"""
    local = FFMPEG if name == "ffmpeg" else FFPROBE
    if local.is_file():
        return str(local)
    from shutil import which
    found = which(name)
    if found:
        return found
    raise SystemExit(f"找不到 {name}，请检查 assets/ffmpeg/ 或系统 PATH")


def display_name(src: Path, override: str = "") -> str:
    """把「歌手 - 歌名_EG.flac」洗成「歌名 · 歌手」。

    只是给个默认显示名，真正要改直接传 --titles，或在生成后编辑
    playlist.json 的 title 字段（换歌时重新运行本脚本会覆盖）。
    """
    if override.strip():
        return override.strip()
    s = re.sub(r"_[A-Za-z]{2,5}$", "", src.stem).strip()
    s = s.replace("＿", "_")
    if " - " in s:
        artist, title = s.split(" - ", 1)
        artist = re.sub(r"\s*_\s*", " / ", artist).strip()
        return f"{title.strip()} · {artist}"
    return s


def probe_duration(mp3: Path) -> float:
    try:
        r = subprocess.run(
            [exe("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(mp3)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float((r.stdout or "0").strip().splitlines()[0]), 2)
    except Exception:                                          # noqa: BLE001
        return 0.0


def convert(src: Path, dst: Path) -> None:
    cmd = [
        exe("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-map", "0:a:0",              # 只取第一条音轨，丢掉内嵌封面 / 视频流
        "-vn", "-sn", "-dn", "-map_metadata", "-1",
        "-ar", "44100", "-ac", "2",
        "-c:a", "libmp3lame", "-b:a", BITRATE,
        "-id3v2_version", "3",
        str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.is_file() or dst.stat().st_size < 20_000:
        raise RuntimeError(f"转码失败：{src.name} → {r.stderr.strip()[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="导入官网背景音乐歌单")
    ap.add_argument("sources", nargs="+", help="本地音频文件，顺序即播放顺序")
    ap.add_argument("--titles", default="",
                    help="用 | 分隔的显示名，数量与音频一致；不传则从文件名推断")
    args = ap.parse_args()

    sources = [Path(s).expanduser() for s in args.sources]
    for s in sources:
        if not s.is_file():
            raise SystemExit(f"文件不存在：{s}")
        if s.suffix.lower() not in SUPPORTED:
            raise SystemExit(f"不支持的格式：{s.name}（支持 {'/'.join(sorted(SUPPORTED))}）")

    titles = args.titles.split("|") if args.titles else []
    if titles and len(titles) != len(sources):
        raise SystemExit(f"--titles 给了 {len(titles)} 个名字，但有 {len(sources)} 首音乐")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tracks: list[dict] = []
    for i, src in enumerate(sources, 1):
        name = f"bgm-{i}.mp3"
        dst = OUT_DIR / name
        convert(src, dst)
        tracks.append({
            "file": name,
            "title": display_name(src, titles[i - 1] if titles else ""),
            "duration": probe_duration(dst),
            "source": str(src),
        })
        print(f"  [{i}/{len(sources)}] {tracks[-1]['title']}  "
              f"→ {name}  {dst.stat().st_size / 1024 / 1024:.2f} MB  "
              f"{tracks[-1]['duration']:.0f} 秒")

    # 上一轮歌单比这次长时，清掉多出来的旧编号，免得线上残留无引用文件。
    keep = {t["file"] for t in tracks}
    for old in OUT_DIR.glob("bgm-*.mp3"):
        if old.name not in keep:
            old.unlink()
            print(f"  [清理] 移除旧曲目 {old.name}")

    data = {
        "updated": date.today().isoformat(),
        "bitrate": BITRATE,
        "loop": True,
        "tracks": tracks,
    }
    PLAYLIST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum((OUT_DIR / t["file"]).stat().st_size for t in tracks)
    print(f"\n歌单 {PLAYLIST.name}：{len(tracks)} 首，合计 {total / 1024 / 1024:.2f} MB，"
          f"总时长 {sum(t['duration'] for t in tracks):.0f} 秒，顺序循环播放。")
    print("下一步：build_site.py --local → build_subpages.py（正式发版会自动上传 mp3）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
