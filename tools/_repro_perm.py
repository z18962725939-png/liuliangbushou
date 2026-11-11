"""复现「保存目录不可写(PermissionError)」—— 精确到 errno / winerror / filename。

只跑单条，打印每个候选地址每次尝试的真实异常。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector.client import JustOneAPICollector           # noqa: E402
from justoneapi_collector.video_downloader import (                  # noqa: E402
    VideoDownloader, _safe_filename,
)

CFG = Path.home() / "AppData/Roaming/TrafficCatcher/config.json"
cfg = json.load(io.open(CFG, encoding="utf-8"))
print("dl_save_dir =", cfg.get("dl_save_dir"))

client = JustOneAPICollector(token=cfg.get("token"), base_url=cfg.get("base_url"))
dl = VideoDownloader(client, save_dir=Path(cfg.get("dl_save_dir")),
                     cookie=cfg.get("douyin_cookie") or "",
                     ffmpeg_path=cfg.get("ffmpeg_path") or None)
print("save_dir =", dl.save_dir, "exists =", dl.save_dir.exists(), "writable? ", end="")
try:
    t = dl.save_dir / "_w_probe.tmp"
    t.write_bytes(b"x")
    t.unlink()
    print("YES")
except OSError as e:
    print("NO", type(e).__name__, e)

share = "https://v.douyin.com/mVifiZn2TQA/"
video = dl.resolve_share_link(share)
if not video:
    print("!! 解析失败")
    raise SystemExit(1)
print("\n--- VideoInfo ---")
for f in ("video_id", "author", "desc", "platform", "no_watermark_url"):
    print(f"{f} = {getattr(video, f, None)!r}")

author = _safe_filename(video.author, 30) or "unknown"
desc = _safe_filename(video.desc, 50)
filename = f"{author}_{desc}_{video.video_id}.mp4"
save_path = dl.save_dir / filename
print("\nfilename  =", filename)
print("len(path) =", len(str(save_path)))
print("exists    =", save_path.exists())

try:
    with open(save_path, "wb") as f:
        f.write(b"")
    print("open(wb) OK")
    save_path.unlink()
except OSError as e:
    print("open(wb) FAIL:", type(e).__name__, "errno=", e.errno,
          "winerror=", getattr(e, "winerror", None),
          "filename=", getattr(e, "filename", None))

print("\n--- 真实下载链路 ---")
path, err = dl.download_video(video, media_type="video")
print("path:", path)
print("err :", err)
