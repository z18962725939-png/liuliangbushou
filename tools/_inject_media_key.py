# -*- coding: utf-8 -*-
"""构建前注入解析服务 Key：生成 justoneapi_collector/_builtin_key.py（已 gitignore）。

优先级：
  1. 环境变量 TRAFFIC_MEDIA_KEY
  2. ~/.trafficcatcher/secrets.env 里的 TRAFFIC_MEDIA_KEY=...
  3. 已存在的 _builtin_key.py（--check 模式只校验它）

用法：
  python tools/_inject_media_key.py            # 生成/覆盖
  python tools/_inject_media_key.py --check    # 只校验，缺失就退出码 1（发版前用）

★ 为什么不在源码里写 Key：仓库是公开的，任何字面量都等于公开。
  真正安全的边界只有"仓库里没有"，客户端二进制里的 Key 仍然可被提取。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "justoneapi_collector" / "_builtin_key.py"
SECRETS = Path.home() / ".trafficcatcher" / "secrets.env"


def _from_secrets_file() -> str:
    if not SECRETS.is_file():
        return ""
    for line in SECRETS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "TRAFFIC_MEDIA_KEY":
            return v.strip().strip('"').strip("'")
    return ""


def resolve_key() -> str:
    return (os.getenv("TRAFFIC_MEDIA_KEY", "").strip()
            or _from_secrets_file()
            or (read_existing() if TARGET.is_file() else ""))


def read_existing() -> str:
    try:
        ns: dict = {}
        exec(TARGET.read_text(encoding="utf-8"), ns)  # noqa: S102  本机生成文件
        return str(ns.get("MEDIA_KEY", "")).strip()
    except Exception:  # noqa: BLE001
        return ""


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:4] + "…" + key[-4:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args()

    key = resolve_key()
    if not key:
        print("[失败] 找不到解析 Key：设置 TRAFFIC_MEDIA_KEY，或写 ~/.trafficcatcher/secrets.env")
        return 1

    if args.check:
        print(f"[OK] 解析 Key 在位：{mask(key)}")
        return 0

    TARGET.write_text(
        "# 本文件由 tools/_inject_media_key.py 生成，已被 .gitignore 忽略。\n"
        "# 不要提交、不要贴进聊天/日志。换串后重新运行注入脚本。\n"
        f'MEDIA_KEY = "{key}"\n',
        encoding="utf-8",
    )
    print(f"[OK] 已写入 {TARGET.relative_to(ROOT)}：{mask(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
