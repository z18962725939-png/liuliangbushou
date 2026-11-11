# -*- coding: utf-8 -*-
"""真机探针：真实抖音链接 → 解析 → 下载 → 本地转写 → 结构拆解。

跑法（可选参数为作品 ID 或链接）：
    C:/Python310/python.exe tools/_probe_link_extract.py
    C:/Python310/python.exe tools/_probe_link_extract.py https://www.douyin.com/video/xxxx tiny

会联网：调采集接口解析作品、下载视频。不调用任何 AI 接口。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector.client import JustOneAPICollector          # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader    # noqa: E402
from justoneapi_collector import video_script_extract as vse         # noqa: E402
from justoneapi_collector import script_rewriter as sr               # noqa: E402


def load_cfg() -> dict:
    p = Path.home() / "AppData" / "Roaming" / "TrafficCatcher" / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main():
    cfg = load_cfg()
    token = (cfg.get("token") or "").strip()
    if not token:
        print("× 本机没有配置 API Token，无法解析链接")
        return 1

    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.douyin.com/video/7494166718776986914"
    model = sys.argv[2] if len(sys.argv) > 2 else "large-v3-turbo"

    client = JustOneAPICollector(token=token)
    dm = VideoDownloader(client, ffmpeg_path=cfg.get("ffmpeg_path") or None)
    opt = vse.ExtractOptions(model=model, device="cpu", lang="zh",
                             ffmpeg=cfg.get("ffmpeg_path") or None)
    if len(sys.argv) > 3 and sys.argv[3] == "fresh":
        opt.use_cache = False
    ex = vse.ScriptExtractor(dm, opt)

    print(f"[0] ffmpeg: {vse.find_ffmpeg(opt.ffmpeg)}")
    ok, why = ex.preflight()
    print(f"[0] 前置检查: ok={ok} {why}")

    t0 = time.time()
    res = ex.extract(url, progress=lambda p, m: print(f"    {p:5.1f}%  {m}"),
                     log=lambda m: print(f"    log: {m}"))
    print()
    print("=" * 68)
    print(f"作品：{res.author} / {res.platform_label} / id={res.video_id}")
    print(f"时长：{res.video_seconds:.1f} 秒　点赞：{res.digg_count}")
    print(f"ok={res.ok}  来源={res.source_label}  阶段={res.fail_stage}  耗时={res.cost_seconds}s")
    print(f"字数：{res.chars}　实测口播：{res.speech_seconds} 秒　语速：{res.real_wps}")
    print(f"片段数：{len(res.segments)}")
    if res.msg:
        print(f"备注：{res.msg}")
    print("-" * 68)
    print(res.text[:900] or "（无文本）")
    print("-" * 68)

    if res.ok and res.text:
        for key in sr.GENRE_ORDER:
            pass
        g = sr.guess_genre(res.text, res.desc)
        print(f"体裁建议：{g} / {sr.GENRES[g].label}")
        a = sr.analyze_source(res.text, sr.GENRES[g].wps, res.segments)
        print("拆解：", a.summary())
    print(f"总耗时 {time.time() - t0:.1f}s")
    return 0 if res.ok else 2


if __name__ == "__main__":
    sys.exit(main())
