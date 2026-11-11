# -*- coding: utf-8 -*-
"""一键重建「授权码签发」工具（桌面版 exe）。

★ 为什么必须有这个脚本（2026-10-12 客户投诉）：
  桌面上的发卡工具是【打包好的 exe】，改 tools/license_keygen.py 不会自动生效。
  更隐蔽的是：勾选框、「包含功能」清单全部来自 licensing.FEATURES ——
  新功能上线时如果没重建这个 exe，你在发卡界面就看不到新的功能位。
  （客户就是发现「关键词抓取」没有勾选框才来问的。）

  所以规矩是：**licensing.FEATURES 一变，就跑一次本脚本。**

用法：
    C:/Python310/python.exe tools/build_keygen.py
    C:/Python310/python.exe tools/build_keygen.py --out "D:/某个目录"
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "license_keygen.py"
SPEC_SRC = ROOT / "tools" / "license_keygen.py"
DEFAULT_OUT = Path.home() / "Desktop" / "流量捕手_发卡工具"
EXE_NAME = "流量捕手_发卡工具.exe"
KEY_NAME = "license_private.key"
BUILD_NAME = "LicenseKeygen"          # 打包时用英文名，避免 PyInstaller 中文编码问题


def build(out_dir: Path) -> int:
    if not SRC.is_file():
        print(f"[中止] 找不到源码：{SRC}")
        return 1

    dist = ROOT / "tools" / "_keygen_dist"
    work = ROOT / "tools" / "_keygen_build"
    if dist.exists():
        shutil.rmtree(dist, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--windowed",
        "--name", BUILD_NAME,
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
        # 授权逻辑是运行时按需 import 的，显式声明避免打包后被裁掉
        "--hidden-import", "justoneapi_collector.licensing",
        str(SPEC_SRC),
    ]
    print("▶ 打包中（约 1~2 分钟）…")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        print(f"[中止] PyInstaller 打包失败（退出码 {proc.returncode}）")
        return proc.returncode

    built = dist / f"{BUILD_NAME}.exe"
    if not built.is_file():
        print(f"[中止] 没找到打包产物：{built}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / EXE_NAME
    if target.exists():
        # 留一份上一次的备份，万一新包有问题可以马上退回去
        backup = target.with_suffix(".exe.bak")
        shutil.copy2(target, backup)
        print(f"   已备份旧版 → {backup.name}")
    shutil.copy2(built, target)

    size_mb = target.stat().st_size / 1024 / 1024
    print(f"   [OK] {target}  （{size_mb:.1f} MB，耗时 {time.time() - t0:.0f}s）")

    key = out_dir / KEY_NAME
    if key.is_file():
        print(f"   [OK] 私钥在位：{key}（不会被覆盖，也不会打进 exe）")
    else:
        src_key = ROOT / "tools" / KEY_NAME
        if src_key.is_file():
            shutil.copy2(src_key, key)
            print(f"   [补] 从 tools/ 复制私钥到 {key}")
        else:
            print(f"   [警告] {out_dir} 里没有 {KEY_NAME}，发卡工具启动会报错，请放一份。")

    # 清掉中间产物，只留桌面成品（work 目录里只有本次的 spec/toc，可随时重建）
    shutil.rmtree(dist, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)

    # 盖一个「功能位指纹」章：发版流程靠它判断发卡工具是否已经跟上最新功能清单
    # （没跟上的话，发卡界面里看不到新功能位 —— 客户就是这么发现的）
    _write_stamp()

    print("\n完成。现在打开这个 exe，「包含功能」里应当已经能看到全部功能位"
          "（新版含「关键词抓取」「评论区截流」，GEO 带 ★ 且默认不勾）。")
    return 0


STAMP = ROOT / "tools" / "_keygen_stamp.json"


def _write_stamp() -> None:
    import json
    sys.path.insert(0, str(ROOT))
    from justoneapi_collector import licensing
    payload = {
        "features": list(licensing.FEATURES.keys()),
        "backfill": dict(licensing.FEATURE_BACKFILL),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    STAMP.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   [OK] 功能位指印章已更新（{len(payload['features'])} 个功能位）")


def main() -> int:
    ap = argparse.ArgumentParser(description="重建桌面版发卡工具")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    args = ap.parse_args()
    return build(args.out)


if __name__ == "__main__":
    sys.exit(main())
