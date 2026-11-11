#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 Edge 扩展打成免审核安装包，并上传到 COS。

为什么需要这个脚本：
    扩展在 Edge 商店审核期间，商店详情页**根本还不存在**，官网那个
    「Edge 商店免费安装 →」按钮点下去就是 404 Not Found
    （2026-10-24 客户现场就是这样，客户只会以为网站坏了）。
    所以在过审之前，主入口必须换成"下载压缩包 → 开发者模式加载"，
    这条路当天就能用、功能完全一样。

★ 关键点：**必须排除 `_metadata/` 目录**
    那是 Chrome / Edge 打包后写进去的中间产物，目录名以下划线开头。
    「加载解压缩的扩展」遇到以 `_` 开头的文件或目录会直接拒绝：
        Cannot load extension with file or directory name "_metadata".
        Filenames starting with "_" are reserved for use by the system.
    不排除的话，客户照着说明做，第一步就报错。

用法：
    python tools/publish_ext_zip.py              # 打包 + 上传
    python tools/publish_ext_zip.py --no-upload  # 只打包，看产物
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "tools" / "cos_config.json"
OUT_DIR = ROOT / "installer_output"

# 扩展源码目录（不在本工程里，单独维护）
EXT_DIR = Path(r"D:\workbuddy\2026-10-03-23-06-16\抖音无水印下载插件")

# 打进 zip 的条目（白名单，宁缺勿滥：README 里有上架计划与内部说明，不给客户看）
INCLUDE = [
    "manifest.json",
    "rules.json",
    "icons",
    "popup",
    "scripts",
]

INSTALL_TXT = """流量捕手 · 抖音无水印下载（Edge 扩展）  v{ver}

Edge 商店还在审核中。审核期间用下面这种方式安装，装上之后功能完全一样，
以后也不需要再重装。


【Edge 安装，三步】

  1. 把这个压缩包解压出来（右键 → 全部解压缩），会得到一个文件夹
  2. Edge 地址栏输入：edge://extensions
  3. 打开左下角「开发人员模式」，点「加载解压缩的扩展」，
     选中刚才解压出来的那个文件夹
     （要选里面能直接看到 manifest.json 的那一层）

装好后打开任意抖音视频页，右下角就多一个「下载无水印」按钮，点一下存原片。


【Chrome 用户】

  地址栏 chrome://extensions → 右上角打开「开发者模式」
  → 点「加载已解压的扩展程序」→ 选同一个文件夹


【注意】

  · 解压出来的文件夹别删、别改名 —— 删了扩展就失效了，浏览器读的就是它
  · 这个扩展只申请「下载」一个权限，读不到你在看哪些其他网页
  · 微信 / QQ 传过来的压缩包有时会被重命名或加上 (1)，确认解压后能
    直接看到 manifest.json 即可


【怎么用】

  单个视频   打开视频页 → 右下角点「下载无水印」
  整号批量   博主主页往下滚一滚 → 点工具栏上的插件图标 → 勾选 → 下载选中
  图文笔记   同上，会存成 01.jpg、02.jpg 这样的同名文件夹

文件落在浏览器默认下载目录，文件名是「作者_标题_作品ID后8位.mp4」。
"""


def build_zip() -> Path:
    if not EXT_DIR.is_dir():
        sys.exit(f"找不到扩展目录：{EXT_DIR}")

    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    ver = manifest.get("version") or "0.0.0"
    name = manifest.get("name") or "ext"
    if "流量捕手" not in name:
        print(f"   [警告] manifest 里的名字不含品牌：{name!r}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUT_DIR / f"TrafficCatcher_Edge_Ext_v{ver}.zip"
    dst.unlink(missing_ok=True)

    added = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for item in INCLUDE:
            src = EXT_DIR / item
            if not src.exists():
                print(f"   [跳过] 不存在：{item}")
                continue
            if src.is_file():
                z.write(src, item)
                added += 1
                continue
            for f in sorted(src.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(EXT_DIR)
                if any(p.startswith("_") or p.startswith(".") for p in rel.parts):
                    continue                     # _metadata / .DS_Store 之类
                z.write(f, rel.as_posix())
                added += 1
        # 安装说明放在根目录：客户解压后第一眼就能看到
        z.writestr("安装说明.txt", INSTALL_TXT.format(ver=ver).encode("utf-8-sig"))
        added += 1

    print(f"   [OK] {dst.name}  {dst.stat().st_size/1024:.0f} KB / {added} 个文件")
    # 自检：zip 里绝不能出现下划线开头的东西
    with zipfile.ZipFile(dst) as z:
        bad = [n for n in z.namelist()
               if any(p.startswith("_") for p in n.split("/"))]
    if bad:
        sys.exit(f"打包失败：zip 里仍有下划线开头的条目 {bad}")
    print("   [OK] 已确认没有 _metadata 这类会导致加载失败的条目")
    return dst


def upload(zip_path: Path) -> str:
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    client = CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                   SecretKey=cfg["secret_key"], Scheme="https"))
    prefix = str(cfg.get("prefix") or "trafficcatcher").strip("/")
    base = (cfg.get("cdn_domain") or
            f"https://{cfg['bucket']}.cos.{cfg['region']}.myqcloud.com").rstrip("/")
    key = f"{prefix}/ext/{zip_path.name}"
    client.put_object_from_local_file(
        Bucket=cfg["bucket"], LocalFilePath=str(zip_path), Key=key,
        ContentType="application/zip",
    )
    url = f"{base}/{key}"
    print(f"   [OK] 上传 {key}")
    print(f"   下载地址 {url}")
    return url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true", help="只打包不上传")
    args = ap.parse_args()

    print("=== 打扩展安装包 ===")
    z = build_zip()
    if args.no_upload:
        print("\n（--no-upload，未上传）")
        return
    print("=== 上传 COS ===")
    upload(z)


if __name__ == "__main__":
    main()
