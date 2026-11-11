#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 installer_output/standalone 整站同步到 COS 桶根（镜像站）。

为什么需要这个脚本（2026-10-24 补上）：
    build_site.py 只传首页 index.html，build_subpages.py 只把子页写进
    standalone 目录、根本不传。结果 COS 这个镜像站上的子页长期停在
    某一次手工同步的版本 —— 首页有新入口、点进子页却是旧版，
    而 COS 又是「微信里直接打开」的那个地址，最容易被人看到。

★ 正主仍然是自有域名（site_seo.CANONICAL_HOST）：
  COS 只是镜像，靠 canonical 把权重归给自有域名。
  但既然是给人看的地址，内容就不能比正主旧。

用法：
    python tools/publish_site_mirror.py            # 同步整站
    python tools/publish_site_mirror.py --dry-run  # 只看会传哪些文件

上传范围（白名单，不是整目录）：
    *.html / robots.txt / sitemap.xml / fonts/*.woff2 / 音频与图片
    正式模式下的界面截图走 {prefix}/site/ 那套 CDN 绝对路径，页面里写的
    是绝对地址；但本地模式（--local）会写相对路径的 shot-*.png，加上
    2026-11-10 新增的 assets/bgm-1..3.mp3 三曲歌单与 playlist.json，
    都属于「漏传就哑掉/破图」的静态
    资源，所以一并纳入同步。之前白名单只收页面，音乐文件会被静默丢下。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
STANDALONE = ROOT / "installer_output" / "standalone"
CONFIG_FILE = TOOLS / "cos_config.json"

# 扩展名 → Content-Type。漏一个就按 octet-stream 发出去，浏览器会下载而不是打开。
CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml; charset=utf-8",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
    # ★ 2026-11-10：音乐与图片漏一种 Content-Type，浏览器就当下载而不是播放/显示。
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# 播放器音频、界面截图、演示动图这些「少一个页面就残缺」的静态资源。
MEDIA_EXT = {k for k in CTYPES if k not in
             (".html", ".txt", ".xml", ".woff2", ".json")}


def collect() -> list[Path]:
    """白名单收集：页面 + 字体 + 媒体资源，不传调试图/隐藏目录。"""
    out: list[Path] = []
    for p in sorted(STANDALONE.glob("*.html")):
        out.append(p)
    out.append(STANDALONE / "robots.txt")      # robots 与 sitemap 是爬虫入口
    out.append(STANDALONE / "sitemap.xml")
    out.extend(sorted((STANDALONE / "fonts").glob("*.woff2")))
    # 歌单清单播放器不请求（顺序已内联进每页），但线上留一份便于人工核对：
    # 三首歌到底传齐没有，打开 /assets/playlist.json 一眼就知道。
    playlist = STANDALONE / "assets" / "playlist.json"
    if playlist.is_file():
        out.append(playlist)
    for p in sorted(STANDALONE.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in MEDIA_EXT:
            continue
        rel = p.relative_to(STANDALONE).as_posix()
        if rel.startswith(".") or "/." in rel:     # .edgeone / .tef_dist 等
            continue
        if p not in out:
            out.append(p)
    return [p for p in out if p.is_file()]


def cache_header(p: Path) -> str:
    """页面必须 no-store：改版后被人看到旧页面是最常见的投诉。
    字体与媒体资源文件名固定，给一周缓存；音乐换了内容就同步换文件名。"""
    if p.suffix.lower() in MEDIA_EXT or p.suffix == ".woff2":
        return "public, max-age=604800"
    return "no-store, max-age=0"


def main() -> int:
    ap = argparse.ArgumentParser(description="同步整站到 COS 镜像")
    ap.add_argument("--dry-run", action="store_true", help="只列出文件，不上传")
    args = ap.parse_args()

    files = collect()
    if not files:
        print(f"[错误] {STANDALONE} 里没有可同步的页面，先跑 build_site / build_subpages")
        return 1

    print(f"待同步 {len(files)} 个文件 → COS 桶根")
    if args.dry_run:
        for p in files:
            print(f"   {p.relative_to(STANDALONE)}  {p.stat().st_size:,} B")
        return 0

    from qcloud_cos import CosConfig, CosS3Client  # type: ignore

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    client = CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                  SecretKey=cfg["secret_key"], Scheme="https"))

    ok = 0
    for p in files:
        key = p.relative_to(STANDALONE).as_posix()
        ctype = CTYPES.get(p.suffix.lower(), "application/octet-stream")
        try:
            client.put_object_from_local_file(
                Bucket=cfg["bucket"], LocalFilePath=str(p), Key=key,
                ContentType=ctype,
                Metadata={"Cache-Control": cache_header(p)},
            )
            print(f"   [OK] {key}  ({p.stat().st_size:,} B)")
            ok += 1
        except Exception as exc:                     # noqa: BLE001
            print(f"   [失败] {key}：{exc}")

    print(f"\n完成 {ok}/{len(files)}")
    site = f"https://{cfg['bucket']}.cos-website.{cfg['region']}.myqcloud.com/"
    print(f"镜像站：{site}")
    return 0 if ok == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
