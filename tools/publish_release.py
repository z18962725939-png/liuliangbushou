# -*- coding: utf-8 -*-
"""
一键发版：把安装包 + version.json 推到腾讯云 COS，供客户端自动更新拉取。

典型用法
────────
    # 1) 先干跑，只生成本地 version.json，不联网（确认内容无误）
    python tools/publish_release.py --dry-run

    # 2) 真正发布
    python tools/publish_release.py --notes "修复直播录制偶发中断"

    # 3) 发布并顺手把更新地址写回 branding.py
    python tools/publish_release.py --set-feed

它做的事
────────
    1. 找到 installer_output 下最新的 流量捕手_Setup_vX.Y.Z.exe（也可 --setup 指定）
    2. 计算体积与 SHA256
    3. 生成/更新 installer_output/version.json
    4. 上传安装包与 version.json 到 COS（公有读桶）
    5. 回读校验远端体积一致
    6. 打印两个直链，供你填入 branding.py 的 UPDATE_FEED_URL

配置从哪来（优先级从高到低）
──────────────────────────
    命令行参数  >  环境变量  >  tools/cos_config.json
    密钥文件已加入 .gitignore，不会随代码外泄。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
INSTALLER_OUT = ROOT / "installer_output"
CONFIG_FILE = TOOLS / "cos_config.json"

DEFAULT_PREFIX = "trafficcatcher"
DEFAULT_REGION = "ap-guangzhou"


# ──────────────────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────────────────
def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] cos_config.json 解析失败：{exc}")
        # 兼容 .env 风格键名
        for old, new in (("secret_id", "secret_id"), ("SecretId", "secret_id"),
                         ("SecretKey", "secret_key"), ("Bucket", "bucket"),
                         ("Region", "region")):
            if old in cfg and old != new:
                cfg.setdefault(new, cfg[old])
    env_map = {
        "secret_id": ("COS_SECRET_ID",),
        "secret_key": ("COS_SECRET_KEY",),
        "bucket": ("COS_BUCKET",),
        "region": ("COS_REGION",),
        "prefix": ("COS_PREFIX",),
        "cdn_domain": ("COS_CDN_DOMAIN",),
    }
    for key, names in env_map.items():
        for name in names:
            val = os.environ.get(name, "").strip()
            if val:
                cfg[key] = val
                break
    cfg.setdefault("region", DEFAULT_REGION)
    cfg.setdefault("prefix", DEFAULT_PREFIX)
    return cfg


def require(cfg: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not str(cfg.get(k, "")).strip()]
    if missing:
        print("=" * 60)
        print("缺少 COS 配置：" + "、".join(missing))
        print()
        print("请把密钥写进 tools/cos_config.json（该文件已在 .gitignore 中）：")
        print(json.dumps({
            "secret_id": "AKIDxxxxxxxxxxxxxxxx",
            "secret_key": "xxxxxxxxxxxxxxxxxxxx",
            "bucket": "你的桶名-1250000000",
            "region": "ap-guangzhou",
            "prefix": "trafficcatcher",
        }, ensure_ascii=False, indent=2))
        print("=" * 60)
        sys.exit(2)


# ──────────────────────────────────────────────────────────────────────
#  版本与产物定位
# ──────────────────────────────────────────────────────────────────────
def read_app_version() -> str:
    """从 branding.py 里读 APP_VERSION，不导入包（避免拉起第三方依赖）。"""
    import importlib.util
    p = ROOT / "justoneapi_collector" / "branding.py"
    spec = importlib.util.spec_from_file_location("_branding_pub", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(mod.APP_VERSION)


def find_latest_setup() -> Path | None:
    if not INSTALLER_OUT.exists():
        return None
    cands = sorted(
        INSTALLER_OUT.glob("流量捕手_Setup_v*.exe"),
        key=lambda p: version_tuple(p.stem.split("_v")[-1]),
    )
    return cands[-1] if cands else None


def version_tuple(text: str) -> tuple:
    nums = re.findall(r"\d+", text)
    return tuple(int(n) for n in nums[:4]) or (0,)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ──────────────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="流量捕手一键发版到腾讯云 COS")
    ap.add_argument("--setup", help="安装包路径（默认取 installer_output 下最新版）")
    ap.add_argument("--version", help="版本号（默认读 branding.APP_VERSION）")
    ap.add_argument("--notes", default="", help="更新说明，会写进 version.json")
    ap.add_argument("--mandatory", action="store_true", help="标记为强制更新")
    ap.add_argument("--dry-run", action="store_true", help="只生成本地文件，不上传")
    ap.add_argument("--no-page", action="store_true", help="不同步客户下载页")
    ap.add_argument("--set-feed", action="store_true", help="把更新地址写回 branding.py")
    ap.add_argument("--bucket", help="覆盖配置里的桶名")
    ap.add_argument("--region", help="覆盖配置里的地域")
    args = ap.parse_args()

    version = args.version or read_app_version()
    setup_path = Path(args.setup) if args.setup else find_latest_setup()
    if not setup_path or not setup_path.exists():
        print("[错误] 没找到安装包。先跑打包，或用 --setup 指定路径。")
        return 1

    size = setup_path.stat().st_size
    digest = sha256_of(setup_path)
    cfg = load_config()
    if args.bucket:
        cfg["bucket"] = args.bucket
    if args.region:
        cfg["region"] = args.region

    prefix = str(cfg.get("prefix") or DEFAULT_PREFIX).strip("/")
    setup_key = f"{prefix}/{setup_path.name}"
    version_key = f"{prefix}/version.json"
    # 中文文件名直链会变成一长串 %E6%B5%81…，发微信群里像钓鱼链接、看着就不敢点。
    # 多传一份纯英文名的副本，下载页和 version.json 都指向它，客户端下载也少一层编码风险。
    ascii_key = f"{prefix}/TrafficCatcher_Setup_v{version}.exe"

    # ── 生成本地 version.json ──
    INSTALLER_OUT.mkdir(parents=True, exist_ok=True)
    vj_path = INSTALLER_OUT / "version.json"
    try:
        old = json.loads(vj_path.read_text(encoding="utf-8")) if vj_path.exists() else {}
    except Exception:  # noqa: BLE001
        old = {}

    base_url = public_base(cfg)
    payload = {
        "version": version,
        "published": today(),
        "notes": args.notes or old.get("notes") or f"v{version} 更新",
        "mandatory": bool(args.mandatory),
        "min_version": old.get("min_version") or version,
        "win": {
            # 指向纯英文名副本：直链干净、不用 percent-encode，客户下载更稳
            "url": (f"{base_url}/{ascii_key}" if base_url
                    else "未配置 COS，请先填写配置"),
            "size": size,
            "sha256": digest,
        },
    }
    vj_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"版本：v{version}")
    print(f"安装包：{setup_path.name}  ({size:,} 字节)")
    print(f"SHA256：{digest}")
    print(f"version.json：{vj_path}")

    if args.dry_run:
        print("\n[dry-run] 未上传。确认无误后去掉 --dry-run 再跑一次。")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # ── 上传 ──
    require(cfg, "secret_id", "secret_key", "bucket", "region")
    try:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore
    except ImportError:
        print("[错误] 缺少 SDK，请先安装：pip install cos-python-sdk-v5")
        return 1

    client = CosS3Client(CosConfig(
        Region=cfg["region"],
        SecretId=cfg["secret_id"],
        SecretKey=cfg["secret_key"],
        Scheme="https",
    ))
    bucket = cfg["bucket"]

    print(f"\n上传 → {bucket}（{cfg['region']}）")
    for local, key, ctype in (
        (setup_path, setup_key, "application/octet-stream"),
        (setup_path, ascii_key, "application/octet-stream"),   # 同一份文件，英文名副本
        (vj_path, version_key, "application/json"),
    ):
        mb = local.stat().st_size / 1024 / 1024
        print(f"  · {key}  ({mb:.1f} MB)")
        client.put_object_from_local_file(
            Bucket=bucket,
            LocalFilePath=str(local),
            Key=key,
            ContentType=ctype,
            # 更新检查最怕拿到旧 version.json，明确禁止中间层缓存
            Metadata={"Cache-Control": "no-store, max-age=0"},
        )

    # ── 回读校验 ──
    print("\n校验远端：")
    ok = True
    for local, key in ((setup_path, setup_key), (setup_path, ascii_key), (vj_path, version_key)):
        try:
            head = client.head_object(Bucket=bucket, Key=key)
            remote = int(head.get("Content-Length", 0))
            flag = "OK" if remote == local.stat().st_size else f"不一致(本地 {local.stat().st_size} / 远端 {remote})"
            if remote != local.stat().st_size:
                ok = False
            print(f"  · {key}: {remote:,} 字节  {flag}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  · {key}: 校验失败 {exc}")

    print("\n直链：")
    print(f"  更新源   {base_url}/{version_key}")
    print(f"  安装包   {base_url}/{ascii_key}   ← 发客户用这个（英文名，干净）")
    print(f"  原名副本 {base_url}/{setup_key}")

    if args.set_feed:
        write_feed(f"{base_url}/{version_key}")
        print(f"\n已把 UPDATE_FEED_URL 写回 branding.py")
        print("注意：改了 branding.py 需要重新打包，否则已发出的客户端仍用旧地址。")

    # ── 同步客户下载页：发微信群的链接指向它，版本号/体积自动跟着更新 ──
    if not args.no_page:
        page = TOOLS / "build_download_page.py"
        if page.exists():
            print("\n更新客户下载页…")
            code = subprocess.run([sys.executable, str(page)]).returncode
            if code != 0:
                print("  [警告] 下载页更新失败，不影响发版，可手动跑 build_download_page.py")
        else:
            print("\n[提示] 没找到 build_download_page.py，跳过下载页更新")

    if cfg.get("cdn_domain"):
        print("\n你配了 CDN 域名，记得去控制台刷新该路径缓存，否则客户拿到的还是旧 version.json。")

    return 0 if ok else 3


def public_base(cfg: dict[str, Any]) -> str:
    """返回可公网访问的基础地址（COS 默认域名或自定义 CDN 域名）。"""
    cdn = str(cfg.get("cdn_domain") or "").strip()
    if cdn:
        return cdn.rstrip("/")
    bucket = str(cfg.get("bucket") or "").strip()
    region = str(cfg.get("region") or DEFAULT_REGION).strip()
    return f"https://{bucket}.cos.{region}.myqcloud.com" if bucket else ""


def write_feed(url: str) -> None:
    p = ROOT / "justoneapi_collector" / "branding.py"
    text = p.read_text(encoding="utf-8")
    new = re.sub(r'^UPDATE_FEED_URL\s*=\s*".*"', f'UPDATE_FEED_URL = "{url}"',
                 text, count=1, flags=re.M)
    if new == text:
        print(f"[警告] branding.py 里没找到 UPDATE_FEED_URL，请手动填：{url}")
        return
    p.write_text(new, encoding="utf-8")


def today() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone(timedelta(hours=8)))).strftime("%Y-%m-%d")


if __name__ == "__main__":
    raise SystemExit(main())
