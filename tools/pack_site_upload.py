# -*- coding: utf-8 -*-
"""把官网打包成「可以拖进静态托管的上传包」。

背景（务必记住）：官网页面原来把 4 张界面截图写成 COS 绝对地址
（`.../trafficcatcher/site/shot-*.png`）。这样部署到别的托管上还得依赖 COS，
而且以后换托管容易漏。本脚本做两件事，让整站**自包含**：

  1. 把 HTML 里的 COS 图片地址换成同目录相对文件名（`shot-xxx.png`）
  2. 把图片下载到 standalone 目录，和 HTML 放在一起
  3. 打成 zip（index.html 必须在压缩包根目录）+ 复制一份文件夹到桌面

用法：
    python tools/pack_site_upload.py            # 生成上传包
    python tools/pack_site_upload.py --no-zip   # 只要文件夹，不打包
产出：
    桌面/流量捕手官网_上传包.zip          ← 拖这个进 EdgeOne / 任意静态托管
    桌面/流量捕手官网_上传用文件夹/        ← 或用「选择文件夹」方式上传
"""
import argparse
import os
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STANDALONE = ROOT / "installer_output" / "standalone"
INDEX = STANDALONE / "index.html"
DESKTOP = Path(os.path.expanduser("~")) / "Desktop"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 页面里引用的 COS 图片前缀，命中即换成相对名
COS_IMG_RE = re.compile(
    r"https?://[^\"'()\s]+?/(?:trafficcatcher/)?site/(shot-[A-Za-z0-9_\-]+\.png)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true", help="不打包 zip，只准备文件夹")
    ap.add_argument("--to", default=str(DESKTOP), help="输出目录（默认桌面）")
    args = ap.parse_args()
    outdir = Path(args.to)
    outdir.mkdir(parents=True, exist_ok=True)

    if not INDEX.is_file():
        print(f"[中止] 找不到 {INDEX}，先跑 python tools/build_site.py --no-upload")
        return 2

    html = INDEX.read_text(encoding="utf-8")

    # ── 1) 找图片，需要的话从 COS 下载到 standalone ──
    names = sorted({m.group(1) for m in COS_IMG_RE.finditer(html)})
    print(f"页面引用的截图：{len(names)} 张")
    for n in names:
        dst = STANDALONE / n
        if dst.is_file() and dst.stat().st_size > 1024:
            print(f"   [已有] {n}（{dst.stat().st_size:,} 字节）")
            continue
        src = COS_IMG_RE.search(html)
        # 从 HTML 里还原该图的完整 COS 地址
        full = next((m.group(0) for m in COS_IMG_RE.finditer(html) if m.group(1) == n), None)
        if not full:
            continue
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read()
            dst.write_bytes(body)
            print(f"   [下载] {n}（{len(body):,} 字节）")
        except Exception as e:  # noqa: BLE001
            print(f"   [失败] {n} —— {e}")
            return 3

    # ── 2) HTML 里的绝对地址 → 相对文件名（幂等）──
    new_html, n_sub = COS_IMG_RE.subn(r"\1", html)
    if n_sub:
        INDEX.write_text(new_html, encoding="utf-8")
        print(f"已把 {n_sub} 处图片地址改为相对路径")
    else:
        print("图片地址已是相对路径，无需改动")

    left = [u for u in sorted(set(re.findall(r"https?://[^\"'\s]+", new_html)))
            if "/svg" not in u]
    print("剩余绝对外链（安装包直链属正常，它就该指向 COS）：")
    for u in left:
        print("   ", u)

    # ── 3) 复制文件夹 + 打包 ──
    zpath = outdir / "流量捕手官网_上传包.zip"
    if zpath.exists():
        zpath.unlink()
    files = sorted(f for f in os.listdir(STANDALONE) if (STANDALONE / f).is_file())
    n_missing = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in files:
            z.write(STANDALONE / fn, fn)  # 平铺，index.html 在根
    with zipfile.ZipFile(zpath) as z:
        for i in z.infolist():
            print(f"   {i.file_size:>9,}  {i.filename}")
    print(f"zip 已生成：{zpath}（{zpath.stat().st_size/1024:.0f} KB）")

    dstdir = outdir / "流量捕手官网_上传用文件夹"
    if dstdir.exists():
        shutil.rmtree(dstdir)
    shutil.copytree(STANDALONE, dstdir)
    print(f"文件夹副本：{dstdir}")

    # index.html 是否在根（托管平台的要求）
    if "index.html" not in files:
        print("[警告] standalone 里没有 index.html！")
        return 4
    if not names:
        print("[提醒] 页面没有引用任何截图，确认是否正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
