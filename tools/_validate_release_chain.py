# -*- coding: utf-8 -*-
"""发版链门禁（P1，2026-11-16）：可重复、可回滚、隐式耦合自动化。

这条门禁守的不是界面，是「发版失败一次之后会不会把事情搞得更糟」。
以前 branding.py / installer.iss 在 546 行就被写成新版本号，后面 30 多个
early return 谁都不回退 —— 重跑一次直接 bump 两档；更危险的是线上已经
发布之后再回退，代码版本会和线上对不上。所以这里同时做两类断言：

  A. 静态：分界线（mark_published）位置、状态机接线、中止判据数量、四件套
  B. 运行时：在临时沙盒里真跑 main()，模拟「发布前失败」「发布后失败」「续跑」

全部只读工程文件；沙盒写在系统临时目录，绝不碰真实版本号、绝不联网。
"""
from __future__ import annotations

import ast
import importlib.util
import io
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAIN = ROOT / "tools" / "release_all.py"

PASS = 0
FAIL = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK]   {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  → {detail}" if detail else ""))
    return ok


# ── A. 静态断言 ────────────────────────────────────────────────────────
print("=" * 68)
print(" A. 发版链静态结构")
print("=" * 68)

src = CHAIN.read_text(encoding="utf-8")
tree = ast.parse(src)

funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
for name in ("preflight_sources", "load_state", "save_state", "clear_state",
             "snapshot_sources", "restore_sources", "mark_published",
             "finish", "decide_versions", "check_keygen_freshness",
             "run_extras", "_pipeline", "main"):
    check(name in funcs, f"函数存在：{name}()")

# 分界线：mark_published() 必须紧跟在 publish_release.py 成功之后，且在
# 任何「后续步骤」之前。用源码位置断言，避免有人把它挪到末尾（那样发布后
# 失败会被误判成「发布前失败」并回滚版本号）。
m_pub = src.find("publish_release.py")
m_mark = src.find("mark_published()", m_pub)   # 从上传之后找调用，别撞上函数定义
m_page = src.find("publish_download_page.py")
check(m_pub > 0 and m_mark > m_pub and m_page > m_mark,
      "分界线正确：上传 COS 成功后立刻标记 published",
      f"publish_release@{m_pub} / mark@{m_mark} / 下载页@{m_page}")

# main() 必须统一收口，不能让某个 early return 绕过回滚
main_src = ast.get_source_segment(src, funcs["main"]) if "main" in funcs else ""
check("finish(" in main_src and "try:" in main_src,
      "main() 用 try + finish() 统一收口（所有失败都经过回滚判别）")
check("_pipeline(args, old_ver, new_ver)" in main_src,
      "版本决策与主体执行已分离（_pipeline 不再自己 bump）")
check("preflight_sources" in src and "_corrupt" in src,
      "断电 NUL 文件预检存在")
check("snapshot_sources()" in main_src and main_src.find("snapshot_sources()")
      < main_src.find("save_state({"),
      "字节备份排在状态落盘之前")

# 33 个中止判据不许被改动误删
n_abort = len(re.findall(r'print\("\[中止\]', src))
check(n_abort >= 33, f"中止判据未减少（{n_abort} 个，基线 33）")

# 状态文件必须落在已被 gitignore 的目录，公开仓看不到运行时状态
check('INSTALLER_OUT / ".release_state.json"' in src,
      "状态文件落在 installer_output/（已 gitignore）")
gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
check("installer_output/" in gitignore, ".gitignore 确实忽略 installer_output/")

# 四件套与隐式耦合
check('"--with-extras"' in src, "--with-extras 参数存在")
for script in ("release_free.py", "publish_site_mirror.py", "build_keygen.py"):
    check(script in src, f"四件套含 {script}")
extras_src = ast.get_source_segment(src, funcs["run_extras"]) or ""
deploy_cmd = "\n".join(l for l in extras_src.splitlines()
                       if "makers deploy" in l or "-n trafficcatcher" in l)
check("makers deploy" in deploy_cmd and "-n trafficcatcher --json" in deploy_cmd,
      "EdgeOne 部署命令完整（-n trafficcatcher --json）")
check("-a overseas" not in deploy_cmd, "部署命令里没有禁用的 -a overseas")
check("别加 -a overseas" in src, "保留了「别加 -a overseas」的人工补跑提示")
check("EXTRAS_ENV_STRIP" in src and "CODEBUDDY_TOOL_CALL_ID" in src,
      "四件套环境变量已摘除沙盒干扰项")
check("preflight_sources()" in ast.get_source_segment(src, funcs["_pipeline"]),
      "预检挂在 _pipeline 里（--skip-smoke 也绕不过）")

# 发版链里不能出现写死的凭据
check("ghp_" not in src.replace("GITHUB_TOKEN=ghp_xxx", ""),
      "发版链源码没有写死 PAT")

# ★ 2026-11-16 v1.28.8 真机踩坑：sync_github 原来用 `git add -u`，
#   新增模块（diagnostics / taskflow / 新门禁 / 新文档）全被漏提交，
#   GitHub 源码永远比安装包少一截。钉死必须用 -A。
check('["git", "add", "-A"]' in src and '["git", "add", "-u"]' not in src,
      "GitHub 同步用 git add -A（新增模块不会被漏提交）",
      "又退回 git add -u：新增文件会漏进不了发版提交")


# ── B. 运行时沙盒 ──────────────────────────────────────────────────────
print("\n" + "=" * 68)
print(" B. 沙盒运行时（临时目录，不碰真实版本号 / 不联网）")
print("=" * 68)

spec = importlib.util.spec_from_file_location("_ra_gate", CHAIN)
ra = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ra)
REAL_PIPELINE = ra._pipeline          # 干跑场景要用真函数，不能用模拟失败的替身

with tempfile.TemporaryDirectory(prefix="tc_release_gate_") as td:
    tmp = Path(td)
    branding = tmp / "branding.py"
    iss = tmp / "installer.iss"
    branding.write_text('# x\nAPP_VERSION = "1.28.7"\n', encoding="utf-8")
    iss.write_text('#define MyAppVersion "1.28.7"\nAppVerName=流量捕手 {#MyAppVersion}\n',
                   encoding="utf-8")

    ra.BRANDING = branding
    ra.ISS = iss
    ra.INSTALLER_OUT = tmp / "installer_output"
    ra.STATE_FILE = ra.INSTALLER_OUT / ".release_state.json"
    ra.BACKUP_DIR = ra.INSTALLER_OUT / ".release_backup"
    ra.BACKUP_FILES = {"branding.py": branding, "installer.iss": iss}

    def ver() -> str:
        return ra.read_version()

    def iss_ver() -> str:
        return re.search(r'"([\d.]+)"', iss.read_text(encoding="utf-8")).group(1)

    def run_main(extra: list[str] | None = None):
        argv = ["release_all.py", "--notes", "门禁自测", "--skip-smoke"]
        argv += extra or []
        old = sys.argv[:]
        sys.argv = argv
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = ra.main()
        finally:
            sys.argv = old
        return rc, buf.getvalue()

    # 场景 1：纯函数版本决策
    o, n, _ = ra.decide_versions("1.28.7", False, "patch", False)
    check((o, n) == ("1.28.7", "1.28.8"), "全新发版 patch：1.28.7 → 1.28.8")
    o, n, _ = ra.decide_versions("1.28.8", False, "patch", True)
    check((o, n) == ("1.28.8", "1.28.8"), "续跑不 bump：仍是 1.28.8")
    o, n, _ = ra.decide_versions("1.28.7", True, "patch", False)
    check((o, n) == ("1.28.7", "1.28.7"), "--no-pack 不 bump")

    # 场景 2：发布前失败 → 自动回滚
    def fail_before_publish(args, old, new):
        ra.write_version(old, new)        # 模拟 _pipeline 第 1 步真的写了版本号
        return 2                          # 模拟第 3 步打包失败

    ra._pipeline = fail_before_publish
    rc, out = run_main()
    check(rc == 2, "发布前失败返回非零")
    check(ver() == "1.28.7" and iss_ver() == "1.28.7",
          "★ 自动回滚：branding.py 与 installer.iss 都回到 1.28.7",
          f"branding={ver()} iss={iss_ver()}")
    check(not ra.STATE_FILE.exists(), "回滚后状态文件已清理")
    check("已自动回滚" in out, "打印了明确的回滚提示")

    # 场景 3：紧接着重跑 → 不升两档
    seen: list[tuple[str, str]] = []

    def ok_pipeline(args, old, new):
        seen.append((old, new))
        if old != new:
            ra.write_version(old, new)
        ra.mark_published()
        return 0

    ra._pipeline = ok_pipeline
    rc, _ = run_main()
    check(rc == 0, "修复后重跑成功")
    check(seen and seen[0] == ("1.28.7", "1.28.8"),
          "★ 重跑仍是 1.28.7 → 1.28.8（没有升两档）", str(seen))
    check(ver() == "1.28.8", "新版本号正确落地")
    check(not ra.STATE_FILE.exists(), "成功后状态与备份已清场")

    # 场景 4：发布后失败 → 绝不回滚，重跑自动续跑
    branding.write_text('# x\nAPP_VERSION = "1.28.7"\n', encoding="utf-8")
    iss.write_text('#define MyAppVersion "1.28.7"\n', encoding="utf-8")
    ra.clear_state()

    def fail_after_publish(args, old, new):
        if old != new:
            ra.write_version(old, new)
        ra.mark_published()               # 模拟上传 COS 成功
        return 3                          # 模拟随后某步失败

    ra._pipeline = fail_after_publish
    rc, out = run_main()
    check(rc == 3 and ver() == "1.28.8",
          "★ 发布后失败：源码版本保持 1.28.8（不回滚）", f"ver={ver()}")
    st = ra.load_state()
    check(bool(st and st.get("published")), "状态文件保留 published=True")
    check("未回滚" in out, "打印了续跑指引")

    seen.clear()
    ra._pipeline = ok_pipeline
    rc, out = run_main()
    check(rc == 0, "续跑成功")
    check(seen and seen[0] == ("1.28.8", "1.28.8"),
          "★ 续跑自动按 --no-pack：old/new 都是 1.28.8", str(seen))
    check("自动按 --no-pack 续跑" in out, "续跑模式有明确提示")
    check(not ra.STATE_FILE.exists(), "续跑成功后清场")

    # 场景 5：断电 NUL 保护 + 状态文件损坏容错
    branding.write_bytes(b"\x00" * 512)
    ok, why = ra.preflight_sources()
    check(not ok and "NUL" in why, "断电写坏的源文件被拦下")
    branding.write_text('# x\nAPP_VERSION = "1.28.8"\n', encoding="utf-8")
    ra.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ra.STATE_FILE.write_text("{坏掉的 JSON", encoding="utf-8")
    check(ra.load_state() is None, "损坏的状态文件按无状态处理且改名留证")

    # 场景 6：干跑不写状态、不改版本
    ra._pipeline = REAL_PIPELINE
    ra.clear_state()
    before = branding.read_bytes()
    rc, _ = run_main(["--dry-run"])
    check(rc == 0 and branding.read_bytes() == before
          and not ra.STATE_FILE.exists(), "--dry-run 纯读，不写状态不改文件")

print("\n" + "=" * 68)
print(f" 结果：{PASS} 通过 / {FAIL} 失败")
print("=" * 68)
sys.exit(1 if FAIL else 0)
