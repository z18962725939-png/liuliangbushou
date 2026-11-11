# -*- coding: utf-8 -*-
"""流量捕手 · 免费版单独发版（2026-10-23）。

为什么需要一个独立脚本，而不是给 release_all.py 加个 --free 开关：
    release_all.py 是一条**很长的链**（12 道自检 + 更新日志 + 官网子页 +
    字体子集 + 打包 + 上传 + 下载页 + 端到端验证）。免费版和完整版共用
    同一份源码、同一套自检，但产物和上传目标是分开的：
        · exe / 安装包名字不同（客户可能两个都装，不能覆盖）
        · 更新源不同（version_free.json，不能指向完整版）
        · 官网下载页不同
    所以这里只做「免费版特有的那一段」：打包 → 编译安装包 → 传 COS
    → **重建一次官网**（免费版版本号到这里才落地，首页/free.html 的按钮
    都按 version_free.json 拼，不重建就会指回上一版的包）。
    自检由 release_all.py 负责，两者串起来跑：
        python tools/release_all.py --bump minor --notes "…"
        python tools/release_free.py --notes "…"

用法：
    python tools/release_free.py --notes "更新说明"
    python tools/release_free.py --notes "…" --no-upload   # 只打包，不上传
    python tools/release_free.py --no-pack                 # 只上传已有安装包
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
BRANDING = ROOT / "justoneapi_collector" / "branding.py"
SPEC = ROOT / "justoneapi_collector.spec"
ISS_FREE = ROOT / "installer_free.iss"
INSTALLER_OUT = ROOT / "installer_output"
DIST = ROOT / "dist"
EXE_DIR_NAME = "流量捕手免费版"
PY = sys.executable


def find_iscc() -> str:
    """定位 Inno Setup 编译器（ISCC.exe）。

    本机构建环境下 iscc 通常不在 PATH（程序装在
    %LOCALAPPDATA%\\Programs\\InnoSetup6）。优先 which，否则回退到标准安装位，
    避免裸调 'iscc' 在子进程里找不到而打包失败。
    """
    import shutil
    for name in ("iscc", "ISCC"):
        p = shutil.which(name)
        if p:
            return p
    localappdata = os.environ.get(
        "LOCALAPPDATA", r"C:\Users\Administrator\AppData\Local")
    for cand in (
        os.path.join(localappdata, "Programs", "InnoSetup6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ):
        if os.path.exists(cand):
            return cand
    return "iscc"


sys.path.insert(0, str(TOOLS))


# ──────────────────────────────────────────────────────────────────────
#  小工具
# ──────────────────────────────────────────────────────────────────────
def read_version() -> str:
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"',
                  BRANDING.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("[错误] branding.py 里读不到 APP_VERSION")
    return m.group(1)


def sync_iss(version: str) -> None:
    """把品牌版本号写进 installer_free.iss（release_all 只同步 installer.iss）。

    必须逐处替换而不是整文件重写 —— iss 里有大量中文注释和安装逻辑，
    整文件生成一次就会把注释丢掉，下次没人知道 AppId 为什么是这个值。
    """
    text = ISS_FREE.read_text(encoding="utf-8")
    text = re.sub(r'#define MyAppVersion "[^"]*"',
                  f'#define MyAppVersion "{version}"', text)
    text = re.sub(r'^AppVersion=.*$', f"AppVersion={version}", text, flags=re.M)
    text = re.sub(r'^AppVerName=.*$',
                  f"AppVerName=流量捕手免费版 {version}", text, flags=re.M)
    text = re.sub(r'^OutputBaseFilename=.*$',
                  f"OutputBaseFilename=流量捕手免费版_Setup_v{version}",
                  text, flags=re.M)
    ISS_FREE.write_text(text, encoding="utf-8")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], title: str, env: dict | None = None,
        tail_lines: int = 8) -> tuple[int, str]:
    print()
    print("─" * 58)
    print(f"▶ {title}")
    print("─" * 58)
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=full_env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    for ln in lines[-tail_lines:]:
        print("   " + ln)
    print(f"   … 退出码 {proc.returncode}（共 {len(lines)} 行输出）")
    return proc.returncode, out


# ──────────────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="流量捕手 免费版发版")
    ap.add_argument("--notes", default="", help="更新说明，写进 version_free.json")
    ap.add_argument("--no-pack", action="store_true", help="跳过打包，只上传现有安装包")
    ap.add_argument("--no-upload", action="store_true", help="只打包，不上传")
    args = ap.parse_args()

    version = read_version()
    setup_name = f"流量捕手免费版_Setup_v{version}.exe"
    setup_path = INSTALLER_OUT / setup_name

    print("=" * 58)
    print(" 流量捕手 · 免费版发版")
    print("=" * 58)
    print(f"  版本     v{version}")
    print(f"  产物     {setup_name}")
    print(f"  更新源   version_free.json（与完整版分开）")

    if not args.no_pack:
        sync_iss(version)
        print(f"  已同步 installer_free.iss 版本号 → {version}")

        # ★ 关键：TRAFFIC_EDITION=free 同时决定两件事
        #   ① spec 里 EXE_NAME 变成「流量捕手免费版」（产物目录名）
        #   ② 挂上 runtime hook，让 exe 运行时 APP_IS_FREE=True
        #   两者用同一个环境变量，所以不可能出现"名字是免费版、行为是完整版"。
        #   --workpath 单独开一个：和完整版共用 build/ 会命中 PyInstaller 的
        #   分析缓存，第二次打包会跳过分析、直接把上一版的 hooks 沿用过来。
        code, _ = run(
            [PY, "-m", "PyInstaller", str(SPEC), "--noconfirm",
             "--workpath", str(ROOT / "build_free")],
            "PyInstaller 打包（免费版）",
            env={"TRAFFIC_EDITION": "free"},
            tail_lines=6,
        )
        if code != 0:
            print("[中止] 打包失败。")
            return 2

        built_exe = DIST / EXE_DIR_NAME / f"{EXE_DIR_NAME}.exe"
        if not built_exe.exists():
            print(f"[中止] 没打出来 {built_exe}")
            return 2

        code, _ = run([find_iscc(), str(ISS_FREE)], "Inno Setup 生成免费版安装包",
                      tail_lines=6)
        if code != 0:
            print("[中止] 安装包编译失败。")
            return 2

    if not setup_path.exists():
        print(f"[中止] 没找到 {setup_path}")
        return 2

    size = setup_path.stat().st_size
    digest = sha256_of(setup_path)
    print()
    print(f"安装包：{setup_path.name}  ({size:,} 字节)")
    print(f"SHA256：{digest}")

    if args.no_upload:
        print("\n[--no-upload] 未上传。")
        return 0

    # ── 生成 version_free.json ──
    import publish_release as pr  # 复用同一套 COS 配置读取

    cfg = pr.load_config()
    pr.require(cfg, "secret_id", "secret_key", "bucket", "region")
    prefix = str(cfg.get("prefix") or "trafficcatcher").strip("/")
    base_url = pr.public_base(cfg)

    INSTALLER_OUT.mkdir(parents=True, exist_ok=True)
    vj_path = INSTALLER_OUT / "version_free.json"
    ascii_key = f"{prefix}/TrafficCatcher_Free_Setup_v{version}.exe"
    setup_key = f"{prefix}/{setup_path.name}"
    version_key = f"{prefix}/version_free.json"

    payload = {
        "version": version,
        "published": _dt.date.today().isoformat(),
        "notes": args.notes or f"v{version} 更新",
        "mandatory": False,
        "min_version": version,
        "edition": "free",
        "win": {
            "url": f"{base_url}/{ascii_key}",
            "size": size,
            "sha256": digest,
        },
    }
    vj_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    # ── 上传 ──
    from qcloud_cos import CosConfig, CosS3Client  # type: ignore

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
        (setup_path, ascii_key, "application/octet-stream"),
        (vj_path, version_key, "application/json"),
    ):
        mb = local.stat().st_size / 1024 / 1024
        print(f"  · {key}  ({mb:.1f} MB)")
        client.put_object_from_local_file(
            Bucket=bucket, LocalFilePath=str(local), Key=key,
            ContentType=ctype,
            Metadata={"Cache-Control": "no-store, max-age=0"},
        )

    # ── 回读校验 ──
    print("\n校验远端：")
    ok = True
    for local, key in ((setup_path, setup_key), (setup_path, ascii_key),
                       (vj_path, version_key)):
        try:
            head = client.head_object(Bucket=bucket, Key=key)
            remote = int(head.get("Content-Length", 0))
            same = remote == local.stat().st_size
            ok = ok and same
            print(f"  · {key}: {remote:,} 字节  "
                  f"{'OK' if same else f'不一致(本地 {local.stat().st_size:,})'}")
        except Exception as exc:                       # noqa: BLE001
            ok = False
            print(f"  · {key}: 校验失败 {exc}")

    # ── 重建官网（★ 2026-10-23 补的：漏这一步，官网的「下载免费版」会指回上一版）──
    #   首页下载区和 free.html 的免费版按钮都按 version_free.json 里的版本号拼。
    #   免费版包总是**晚于**完整版发布，所以 release_all 里生成页面时读到的
    #   还是上一版的 version_free.json：页面照常打开、链接也能下、零报错，
    #   只是客户点「下载免费版」拿到的是旧包（v1.24.0 实测踩到，首页指向
    #   v1.23.0）。所以免费版包上传完必须再重建一次页面。
    #   这里只管到「页面 + COS」；自有域名（EdgeOne）按铁律仍要手工部署。
    if ok:
        print()
        print("─" * 58)
        print(f"▶ 重建官网（免费版直链改成 v{version}）")
        print("─" * 58)
        # ★ 两步缺一不可（v1.24.1 又踩了一次才看清）：
        #   ① build_download_page --standalone —— **重新生成** installer_output/standalone
        #      里的页面。免费版直链是按 version_free.json 拼的，不重新生成的话
        #      文件里还是上一版。
        #   ② publish_download_page —— 只负责**上传**（读 standalone 同步到 COS），
        #      它自己不会生成。上一版补丁只补了这一步，所以 free.html 依旧指向
        #      v1.24.0 —— 页面照常打开、链接也能下、零报错，只有客户拿到旧包。
        # ①-a 栏目子页（含 **free.html** 免费版落地页）：免费版直链在这里拼，
        #       版本号必须显式传，默认读的是 version.json（完整版的号）。
        code, _ = run([PY, str(TOOLS / "build_subpages.py"), "--version", version],
                      "重新生成栏目子页（free.html 的免费版直链）", tail_lines=4)
        if code != 0:
            print("[警告] 栏目子页没重新生成 —— free.html 的免费版直链可能还是上一版，"
                  f"请手动跑一次 tools/build_subpages.py --version {version}。")
        # ①-b 首页 + 站点字体（--standalone 产出给静态托管用的自包含版）
        code, _ = run([PY, str(TOOLS / "build_download_page.py"), "--standalone"],
                      "重新生成自包含站点（首页下载区换新版本号）", tail_lines=4)
        if code != 0:
            print("[警告] 站点没重新生成 —— 免费版直链可能还是上一版，"
                  "请手动跑一次 tools/build_download_page.py --standalone。")
        code, _ = run([PY, str(TOOLS / "publish_download_page.py")],
                      "重建并同步官网", tail_lines=6)
        if code != 0:
            print("[警告] 官网没同步成功 —— 首页「下载免费版」还指向上一版的包，"
                  "请手动跑一次 tools/publish_download_page.py。")

    print()
    print("=" * 58)
    print(f" 免费版发布{'完成' if ok else '（校验未全过，请检查）'}  v{version}")
    print("=" * 58)
    print(f"  下载直链 {base_url}/{ascii_key}")
    print(f"  更新源   {base_url}/{version_key}")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
