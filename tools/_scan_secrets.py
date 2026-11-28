# -*- coding: utf-8 -*-
"""扫密钥：提交前/CI 用。命中即退出码 1，输出打码，不打印完整密钥。

用法：
  python tools/_scan_secrets.py             # 扫全部被 git 跟踪的文件
  python tools/_scan_secrets.py --staged    # 只扫暂存区（pre-commit 用）
  python tools/_scan_secrets.py file.py ... # 扫指定文件
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = [
    ("解析服务 Key", re.compile(r"mp_[A-Za-z0-9]{16,}")),
    ("腾讯云 SecretId", re.compile(r"AKID[0-9A-Za-z]{20,}")),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS AccessKey", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("私钥", re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")),
    ("Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{24,}")),
]

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".exe", ".dll",
               ".pyd", ".zip", ".7z", ".mp3", ".mp4", ".woff", ".woff2", ".ttf", ".pyc"}
ALLOW_HINTS = ("***", "<", "…", "xxx", "AKID***", "mp_***")


def git_files(staged: bool) -> list[Path]:
    args = ["git", "diff", "--cached", "--name-only", "-z"] if staged else ["git", "ls-files", "-z"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, check=False).stdout
    names = [n for n in out.decode("utf-8", "replace").split("\0") if n]
    return [ROOT / n for n in names]


def scan(path: Path) -> list[tuple[int, str, str]]:
    if path.suffix.lower() in SKIP_SUFFIX or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(h in line for h in ALLOW_HINTS):
            continue
        for name, pat in PATTERNS:
            m = pat.search(line)
            if m:
                raw = m.group(0)
                shown = (raw[:6] + "…") if len(raw) > 6 else "***"
                hits.append((i, name, shown))
                break
    return hits


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    staged = "--staged" in sys.argv
    files = [Path(a) for a in args] if args else git_files(staged)

    bad = 0
    for f in files:
        for line, name, shown in scan(f):
            rel = f.relative_to(ROOT) if str(f).startswith(str(ROOT)) else f
            print(f"[命中] {rel}:{line}  {name}  {shown}")
            bad += 1

    if bad:
        print(f"\n[失败] 发现 {bad} 处疑似密钥。删除/改为环境变量后再提交。")
        return 1
    print(f"[OK] 扫描 {len(files)} 个文件，未发现明文密钥。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
