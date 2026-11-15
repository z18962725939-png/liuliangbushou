"""把本地 changelog.json 的版本说明同步成 GitHub Release。

用法：
    GITHUB_TOKEN=ghp_xxx python tools/make_gh_releases.py [版本号 ...]

说明：
- 不传版本号时，默认同步 TARGETS 里配置的版本（按 BRANDING 版本区间手工维护）。
- 版本 → 提交的映射必须写全 SHA（GitHub 的 target_commitish 不认短 SHA，会 422）。
- 令牌只从环境变量读，绝不硬编码（本仓库是公开的）。
- 同一 tag 已存在则跳过，可反复执行。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPO", "z18962725939-png/liuliangbushou")
API = f"https://api.github.com/repos/{REPO}"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

# 版本 → 发版提交（短 SHA 会由 full_sha() 解析成完整 SHA）
TARGETS = {
    "1.28.7": "3345085",
    "1.28.6": "1a6471e",
    "1.28.5": "e74fa93",
    "1.28.4": "80fd511",
    "1.28.3": "bb7d773",
}
LATEST = "1.28.7"

SITE = "https://xn--kpuzw91vr71b.cn/"
COS = "https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com/trafficcatcher"


def full_sha(short: str) -> str:
    """target_commitish 只认分支名或完整 SHA，短 SHA 会被 422 拒掉。"""
    out = subprocess.run(["git", "rev-parse", short], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def call(method: str, url: str, payload: dict | None = None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"__error__": exc.code, "body": exc.read().decode("utf-8", "ignore")[:400]}


def build_body(version: str, notes: str) -> str:
    bullets = [s.strip() for s in re.split(r"[；;]", notes) if s.strip()]
    lines = ["## 本次更新", ""]
    lines += [f"- {b}" for b in bullets]
    lines += [
        "",
        "## 下载",
        "",
        "| 版本 | 获取方式 |",
        "|---|---|",
        f"| 完整版 v{version} | [官网下载]({SITE})（需授权码解锁功能位） |",
        f"| 免费版 v{version} | [官网下载]({SITE}) |",
        "",
        f"- 完整版直链：`{COS}/流量捕手_Setup_v{version}.exe`",
        f"- 免费版直链：`{COS}/TrafficCatcher_Free_Setup_v{version}.exe`",
        "",
        "## 升级说明",
        "",
        "直接运行新安装包覆盖安装即可，**下载过的文件、登录状态、监控列表都不会丢**。",
        "也可以在软件里点右上角「检查更新」自动升级。",
        "",
        "---",
        "",
        "- 官网：https://流量捕手.cn",
        f"- 内测招募：[内测招募.md](https://github.com/{REPO}/blob/main/内测招募.md)",
        f"- 界面截图：[界面截图.md](https://github.com/{REPO}/blob/main/界面截图.md)",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not TOKEN:
        print("缺少环境变量 GITHUB_TOKEN")
        return 2

    cl = json.loads((ROOT / "installer_output" / "changelog.json").read_text(encoding="utf-8"))
    by_ver = {r["version"]: r for r in cl["releases"]}

    existing = call("GET", f"{API}/releases?per_page=50")
    have = {r["tag_name"] for r in existing} if isinstance(existing, list) else set()
    print("现有 release:", have or "(无)")

    wanted = argv or list(TARGETS)
    for ver in wanted:
        tag = f"v{ver}"
        if tag in have:
            print("跳过（已存在）", tag)
            continue
        short = TARGETS.get(ver)
        if not short:
            print("跳过（未配置提交）", tag)
            continue
        info = by_ver.get(ver)
        if not info:
            print("跳过（无更新日志）", tag)
            continue
        payload = {
            "tag_name": tag,
            "target_commitish": full_sha(short),
            "name": tag,
            "body": build_body(ver, info["notes"]),
            "draft": False,
            "prerelease": False,
            "make_latest": "true" if ver == LATEST else "false",
        }
        res = call("POST", f"{API}/releases", payload)
        if "__error__" in res:
            print("失败", tag, res["__error__"], res["body"])
        else:
            print("已创建", tag, "->", res["html_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
