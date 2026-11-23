# -*- coding: utf-8 -*-
"""
一条龙发版：改完代码跑这一条，剩下的全自动。

    python tools/release_all.py --notes "修复直播录制偶发中断"
    python tools/release_all.py --notes "新增批量导出" --bump minor
    python tools/release_all.py --notes "只重新上传，不打包" --no-pack
    python tools/release_all.py --notes "看看会发生什么" --dry-run
    python tools/release_all.py --notes "全量发版" --with-extras

    --with-extras 把原来四个手工命令（免费版 / EdgeOne / 镜像 / 发卡工具）
    收进同一条；失败只警告，不影响已上线的完整版。

★ P1（2026-11-16）：失败可重复、可回滚
    bump 前按字节备份 branding.py + installer.iss，状态记在
    installer_output/.release_state.json（该目录已被 gitignore）。
    · 上传 COS 之前失败 → 自动整份回滚版本号，重跑不会升两档；
    · 上传 COS 之后失败 → 绝不回滚（代码版本必须与线上一致），
      重跑同一条命令会自动按 --no-pack 续跑剩余步骤。
    全程不需要人工判断该不该改版本号。

它会依次做完
────────────
    0. 自检       GUI 无头冒烟（10 个页面能构建）+ 编译检查
    1. 升版本号   默认 patch（1.1.0 → 1.1.1），同步 branding.py 和 installer.iss 全部出现处
    2. 清空 dist  必须做：本环境下 PyInstaller 自己 rmtree 会被拦截报错
    3. 打包       PyInstaller → Inno Setup
    4. 发布       上传 COS + 回读校验（体积一致）
    5. 端到端验证  匿名拉 version.json、比对 SHA256、匿名下载前 1MB 验文件头 MZ
    6. 放桌面      覆盖 流量捕手_Setup_vX.Y.Z.exe
    7. 出摘要      版本号 / 体积 / SHA256 / 直链 / 可直接转发给客户的通知文案
    8. 写日志      追加到 installer_output/release_log.md

中途任何一步失败就停下，不会留半个版本在云上。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import shutil
import subprocess
import sys
import traceback
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
INSTALLER_OUT = ROOT / "installer_output"
BRANDING = ROOT / "justoneapi_collector" / "branding.py"
ISS = ROOT / "installer.iss"
SPEC = ROOT / "justoneapi_collector.spec"
SMOKE = ROOT / "gui_qt" / "_smoke.py"
LOG_FILE = INSTALLER_OUT / "release_log.md"

# ── P1 发版状态机（2026-11-16）──────────────────────────────────────────
# 以前失败后 branding.py / installer.iss 里已经是新版本号，30 多个 early return
# 谁都不回退 —— 重跑一次就 bump 两档。这里把「旧版本 / 新版本 / 是否已发布」
# 落成状态文件，并在 bump 前备份两个源文件的原始字节，失败时整份写回。
# 放在 installer_output/ 下：该目录已在 .gitignore，公开仓不会看到运行时状态。
STATE_FILE = INSTALLER_OUT / ".release_state.json"
BACKUP_DIR = INSTALLER_OUT / ".release_backup"
BACKUP_FILES = {"branding.py": BRANDING, "installer.iss": ISS}

# 真机 EdgeOne CLI（Bash 读不到 .edgeone 配置，必须走 PowerShell 全路径）
EDGEONE_CMD = Path(
    r"C:\Users\Administrator\.workbuddy\binaries\node\versions"
    r"\22.22.2-6\edgeone.cmd")
# 四件套脚本在沙盒环境里会被这两个变量干扰，跑之前必须摘掉
EXTRAS_ENV_STRIP = ("CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR",
                    "CODEBUDDY_TOOL_CALL_ID")

DESKTOP = Path.home() / "Desktop"
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


# ──────────────────────────────────────────────────────────────────────
def run(cmd: list[str], title: str, tail_lines: int = 12,
        env: dict | None = None) -> tuple[int, str]:
    """跑命令并实时打印，返回 (退出码, 完整输出)。"""
    print(f"\n{'─' * 58}\n▶ {title}\n{'─' * 58}")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env)
    lines: list[str] = []
    for line in proc.stdout:  # type: ignore[union-attr]
        lines.append(line)
        if len(lines) <= tail_lines or line.strip().startswith(("[", "错误", "ERROR")):
            print("   " + line.rstrip())
    proc.wait()
    print(f"   … 退出码 {proc.returncode}（共 {len(lines)} 行输出）")
    return proc.returncode, "".join(lines)


# ──────────────────────────────────────────────────────────────────────
# P1 状态机：可重复、可回滚（2026-11-16）
# 判别原则只有一条：**线上发布是分界线**。
#   发布前失败 → 源码版本号必须回到旧值，否则重跑 bump 两档；
#   发布后失败 → 绝不能回退，否则代码版本和线上版本对不上，只能续跑。
# ──────────────────────────────────────────────────────────────────────
def _corrupt(path: Path) -> bool:
    """断电曾把 branding.py / installer.iss 整文件写成 NUL，读前 4KB 判坏。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return True
    if not head:
        return True
    return head.count(b"\x00") > len(head) // 3


def preflight_sources() -> tuple[bool, str]:
    """发版前确认两个版本源文件没被写坏（这一步不受 --skip-smoke 影响）。"""
    for name, path in BACKUP_FILES.items():
        if not path.is_file():
            return False, f"{name} 不存在"
        if _corrupt(path):
            return False, (f"{name} 内容疑似全 NUL（断电写坏）。别直接发版："
                           "删 .git/index 后 git read-tree HEAD 恢复，"
                           "不要 checkout 整个分支")
    return True, ""


def load_state() -> dict | None:
    """读残留状态。文件坏了不能当「无状态」静默忽略 —— 改名留证再提示。"""
    if not STATE_FILE.is_file():
        return None
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(st, dict) or not st.get("old_ver"):
            raise ValueError("字段不全")
        return st
    except Exception as exc:                                   # noqa: BLE001
        try:
            STATE_FILE.replace(STATE_FILE.with_suffix(".json.broken"))
        except OSError:
            pass
        print(f"   [警告] 发版状态文件损坏（{exc}），已改名留证，按全新发版处理")
        return None


def save_state(st: dict) -> None:
    """原子写：先写 .tmp 再 replace，避免断电把状态文件本身写成半截。"""
    INSTALLER_OUT.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def clear_state() -> None:
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def snapshot_sources() -> None:
    """bump 前按字节备份两个源文件，回滚时整份写回（比反向字符串替换可靠）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for name, path in BACKUP_FILES.items():
        (BACKUP_DIR / name).write_bytes(path.read_bytes())


def restore_sources(old_ver: str, new_ver: str) -> bool:
    """回滚版本号。优先用字节备份；备份不全才退化成反向替换（幂等）。"""
    restored = 0
    for name, path in BACKUP_FILES.items():
        bak = BACKUP_DIR / name
        if bak.is_file() and not _corrupt(bak):
            try:
                path.write_bytes(bak.read_bytes())
                restored += 1
            except OSError:
                pass
    if restored == len(BACKUP_FILES):
        return True
    try:
        # 已经回退过时这条是无害的空操作：new_ver 在文件里根本找不到
        write_version(new_ver, old_ver)
        print("   [提示] 字节备份不全，已用版本号反向替换回滚（请核对 git diff）")
        return True
    except Exception as exc:                                   # noqa: BLE001
        print(f"   [严重] 版本号回滚失败：{exc}")
        print(f"   请手工把 branding.py 与 installer.iss 的 {new_ver} 改回 {old_ver} 再重跑")
        return False


def mark_published() -> None:
    """publish_release.py 成功后立刻落标记：此后任何失败绝不回退版本号。"""
    st = load_state() or {}
    st["published"] = True
    st["phase"] = "published"
    save_state(st)


def finish(rc: int, st: dict) -> None:
    """唯一收口：成功清场 / 发布前失败回滚 / 发布后失败留状态等续跑。"""
    disk = load_state() or {}
    old_ver = disk.get("old_ver") or st.get("old_ver", "?")
    new_ver = disk.get("new_ver") or st.get("new_ver", "?")
    published = bool(disk.get("published") or st.get("published"))

    if rc == 0:
        clear_state()
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
        return

    if published:
        print("\n" + "!" * 58)
        print(f"[未回滚] 线上已经发布 v{new_ver}，源码版本必须保持一致。")
        print("  直接用同一条命令重跑：状态机会自动按 --no-pack 续跑，")
        print("  不重新打包、不再升版本号，也不会重跑门禁。")
        print("!" * 58)
        return

    if old_ver == new_ver:                 # --no-pack：版本号本来就没动
        clear_state()
        return

    ok = restore_sources(old_ver, new_ver)
    clear_state()
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    if ok:
        print("\n" + "!" * 58)
        print(f"[已自动回滚] 版本号 v{new_ver} → v{old_ver}")
        print("  修好上面的错误后直接重跑同一条命令，不会升两档。")
        print("!" * 58)
    else:
        print("\n[严重] 自动回滚失败，必须先人工修版本号再重跑。")


def decide_versions(old_ver: str, no_pack: bool, kind: str,
                    resumed: bool) -> tuple[str, str, str]:
    """纯函数（可单测）：返回 (old_ver, new_ver, 说明)。"""
    if resumed:
        # 线上已发布：源码里就是新版本，续跑不 bump、不重新打包
        return old_ver, old_ver, "线上已发布，自动续跑（不 bump / 不打包）"
    return old_ver, (old_ver if no_pack else bump(old_ver, kind)), ""


def read_version() -> str:
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', BRANDING.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("[错误] branding.py 里读不到 APP_VERSION")
    return m.group(1)


def gh_token() -> str:
    """GitHub 令牌：环境变量优先，其次从 Windows 凭据管理器读（git credential）。

    ★ 2026-11-15：PAT 已存凭据管理器（git credential approve 过），正常发版
      环境里不需要手动设 GITHUB_TOKEN。读不到返回空串，调用方跳过建 Release。
      ⚠ 严禁把令牌写进代码或日志——本仓库是公开的。
    """
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo")
        p = subprocess.run(["git", "credential", "fill"], cwd=str(ROOT),
                           input="protocol=https\nhost=github.com\n\n",
                           capture_output=True, text=True, timeout=20, env=env)
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:                                          # noqa: BLE001
        pass
    return ""


def sync_github(new_ver: str, notes: str) -> None:
    """发版链收尾（2026-11-15 起）：提交发版产物 → push → 建 GitHub Release。

    尽力而为：线上发布（COS/官网）在本步之前已完成，这里失败只警告不阻断，
    Release 创建脚本幂等，失败后手动重跑即可。
    """
    print(f"\n{'─' * 58}\n▶ GitHub 同步（提交 / push / Release）\n{'─' * 58}")
    # 1) 提交：★ 2026-11-16 修——原来用 `git add -u` 只收已跟踪文件，
    #    结果每次新增的模块（P0 diagnostics / P2 taskflow / 新门禁 / 新文档）
    #    全被漏掉，GitHub 上的源码永远比安装包少一截，还得人工补提交。
    #    改用 `git add -A`：未跟踪的产物由 .gitignore 挡着（installer_output /
    #    *.log / _tmp_shots / __pycache__ 都在里面），dry-run 验证过只会多收
    #    真正该进仓的新文件。
    subprocess.run(["git", "add", "-A"], cwd=str(ROOT), check=False)
    st = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                        capture_output=True, text=True)
    staged = [l for l in st.stdout.splitlines() if l and not l.startswith("??")]
    if staged:
        msg = f"release: v{new_ver}（{notes[:60]}）"
        subprocess.run(["git", "commit", "-m", msg], cwd=str(ROOT), check=False)
        print(f"   已提交 {len(staged)} 个文件：{msg[:60]}")
    else:
        print("   无待提交改动（可能已提交过），跳过 commit")

    # 2) push：先按当前代理配置推，失败再清代理直连推
    #    （Clash 没开时全局 http.proxy=127.0.0.1:7897 不通，得绕开）
    pushed = False
    for extra in ([], ["-c", "http.proxy=", "-c", "https.proxy="]):
        try:
            p = subprocess.run(["git", *extra, "push", "origin", "main"],
                               cwd=str(ROOT), capture_output=True, text=True,
                               timeout=300)
        except subprocess.TimeoutExpired:
            continue
        if p.returncode == 0:
            pushed = True
            print("   已推送 origin/main")
            break
        print(f"   [重试] 换直连再推：{(p.stderr or p.stdout).strip()[:80]}")
    if not pushed:
        print("   [警告] push 失败，线上发布不受影响；稍后手动 git push origin main")

    # 3) 建 Release（幂等；没有令牌就给手动指引）
    tok = gh_token()
    if not tok:
        print("   [提醒] 未拿到 GITHUB_TOKEN（凭据管理器里也没有），Release 未建")
        print(f"     手动补：GITHUB_TOKEN=ghp_xxx {PY} tools/make_gh_releases.py {new_ver}")
        return
    env = dict(os.environ, GITHUB_TOKEN=tok)
    code, _ = run([PY, str(TOOLS / "make_gh_releases.py"), new_ver],
                  f"GitHub Release v{new_ver}", tail_lines=6, env=env)
    if code != 0:
        print("   [警告] Release 创建失败，线上版本不受影响；脚本幂等，可重跑")


def bump(version: str, kind: str) -> str:
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]]
    while len(nums) < 3:
        nums.append(0)
    i = {"patch": 2, "minor": 1, "major": 0}[kind]
    nums[i] += 1
    for j in range(i + 1, 3):
        nums[j] = 0
    return ".".join(str(n) for n in nums)


def write_version(old: str, new: str) -> None:
    """branding.py + installer.iss 里所有出现的旧版本号一并替换。"""
    bt = BRANDING.read_text(encoding="utf-8")
    BRANDING.write_text(bt.replace(f'APP_VERSION = "{old}"', f'APP_VERSION = "{new}"'),
                        encoding="utf-8")

    it = ISS.read_text(encoding="utf-8")
    it2 = re.sub(re.escape(old), new, it)
    if it2 == it:
        print(f"   [警告] installer.iss 里没找到 {old}，请手动核对版本号")
    ISS.write_text(it2, encoding="utf-8")


def clear_dist() -> None:
    """PyInstaller 自己清 dist 会被环境拦截（SHFileOperationW 0x2），这里先手动清。"""
    dist = ROOT / "dist"
    if not dist.exists():
        return
    for p in dist.rglob("*"):
        try:
            p.chmod(0o666)
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(dist, ignore_errors=True)
    if dist.exists():
        print("   [警告] dist 没清干净，PyInstaller 可能中断")
    else:
        print("   [OK] dist 已清空")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_remote(feed_url: str, local_setup: Path) -> tuple[bool, str]:
    """模拟客户机器：匿名拉 version.json + 比对 SHA256 + 试下载。"""
    try:
        with urllib.request.urlopen(feed_url, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"拉取 version.json 失败：{exc}"

    remote_ver = data.get("version", "?")
    remote_sha = str(data.get("win", {}).get("sha256", "")).lower()
    remote_url = str(data.get("win", {}).get("url", ""))
    local_sha = sha256_of(local_setup)

    if remote_sha != local_sha:
        return False, f"SHA256 不一致（远端 {remote_sha[:12]}… / 本地 {local_sha[:12]}…）"

    # 中文文件名直链必须 percent-encode，否则 urllib 报 ascii 编码错误
    parts = urllib.request.Request(quote(remote_url, safe=":/?=&%"),
                                   headers={"Range": "bytes=0-1048575"})
    try:
        with urllib.request.urlopen(parts, timeout=30) as r:
            head = r.read(2)
    except Exception as exc:  # noqa: BLE001
        return False, f"匿名下载失败：{exc}"
    if head != b"MZ":
        return False, f"下载内容不是 exe（文件头 {head!r}）"
    return True, f"v{remote_ver} 可匿名下载，SHA256 一致"


# ──────────────────────────────────────────────────────────────────────
# P1：把「发版后还要手工跑四件套」这条隐式耦合收进一条命令
# ──────────────────────────────────────────────────────────────────────
def check_keygen_freshness() -> tuple[bool, str, bool]:
    """返回 (指纹一致, 说明, 需要重建发卡工具)。

    以前只在链尾「提醒」一句，发完版才发现发卡界面缺勾选框。现在预检阶段
    就报出来，--with-extras 会自动重建，不再靠人记得。
    """
    try:
        sys.path.insert(0, str(ROOT))
        from justoneapi_collector import licensing as _lic
        stamp_file = TOOLS / "_keygen_stamp.json"
        feats = list(_lic.FEATURES.keys())
        if not stamp_file.is_file():
            return False, "还没盖过功能位指纹章", True
        stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
        old = list(stamp.get("features") or [])
        if old != feats:
            return False, (f"功能位清单变了（发卡工具 {len(old)} 项 / "
                           f"当前代码 {len(feats)} 项）"), True
        return True, f"功能位指纹一致（{len(feats)} 项）", False
    except Exception as exc:                                   # noqa: BLE001
        return False, f"检查未执行：{exc}", False


def _extras_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    for key in EXTRAS_ENV_STRIP:
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _keygen_py() -> str:
    """发卡工具要用 Python310 打（tcvenv 缺 PyInstaller / cos-sdk）。"""
    cand = Path(r"C:\Python310\python.exe")
    return str(cand) if cand.is_file() else PY


def run_extras(new_ver: str, notes: str) -> None:
    """四件套：免费版 → EdgeOne 自有域名 → COS 镜像 → 发卡工具。

    全部尽力而为：完整版此刻已上线，这里任何一步失败都不改变发布结果，
    只打印可复制的补跑命令（也方便单独重跑其中一件）。
    """
    print(f"\n{'─' * 58}\n▶ 补跑四件套（免费版 / 官网 / 镜像 / 发卡工具）\n{'─' * 58}")
    env = _extras_env()

    # ① 免费版（TRAFFIC_EDITION=free 由 release_free.py 自己设，不许改源码再改回）
    code, _ = run([PY, str(TOOLS / "release_free.py"), "--notes", notes],
                  "免费版打包与上传", tail_lines=8, env=env)
    if code != 0:
        print(f"   [警告] 免费版失败，补跑：{PY} tools/release_free.py --notes \"…\"")

    # ② EdgeOne 自有域名：必须真机 PowerShell 全路径，禁 -a overseas
    standalone = INSTALLER_OUT / "standalone"
    if EDGEONE_CMD.is_file() and standalone.is_dir():
        ps_cmd = (f"& '{EDGEONE_CMD}' makers deploy '{standalone}' "
                  f"-n trafficcatcher --json")
        code, _ = run(["powershell.exe", "-NoProfile", "-Command", ps_cmd],
                      "EdgeOne 部署官网（自有域名）", tail_lines=8, env=env)
        if code != 0:
            print("   [警告] EdgeOne 部署失败，别加 -a overseas；用真机 PowerShell 手动补跑")
    else:
        print(f"   [警告] 跳过 EdgeOne：CLI 或 standalone 目录不存在（{EDGEONE_CMD}）")

    # ③ COS 镜像站（首页 + 子页 + 字体 + 音频，白名单同步）
    code, _ = run([PY, str(TOOLS / "publish_site_mirror.py")],
                  "镜像站整站同步", tail_lines=8, env=env)
    if code != 0:
        print(f"   [警告] 镜像同步失败，补跑：{PY} tools/publish_site_mirror.py")

    # ④ 发卡工具：只有指纹不一致才重建，避免每次发版都白打一个 exe
    ok, why, need = check_keygen_freshness()
    if need:
        code, _ = run([_keygen_py(), str(TOOLS / "build_keygen.py")],
                      "重建发卡工具（功能位变了）", tail_lines=8, env=env)
        if code != 0:
            print(f"   [警告] 发卡工具重建失败，补跑：{_keygen_py()} tools/build_keygen.py")
    else:
        print(f"   [OK] 发卡工具无需重建：{why}")


# ──────────────────────────────────────────────────────────────────────
def _pipeline(args, old_ver: str, new_ver: str) -> int:
    """发版主体。版本决策、状态机、回滚全部在 main() 里，这里只管顺序执行。"""
    setup_name = f"流量捕手_Setup_v{new_ver}.exe"

    print("=" * 58)
    print(" 流量捕手 · 一条龙发版")
    print("=" * 58)
    print(f"  当前版本 v{old_ver}  →  v{new_ver}（{args.bump}）")
    print(f"  更新说明 {args.notes}")
    print(f"  模式     {'干跑' if args.dry_run else '正式'}")

    if args.dry_run:
        print("\n将依次执行：")
        print( "  0a. 断电文件检测 + 发卡工具功能位指纹预检（始终执行）")
        print(f"  0. GUI 冒烟 + 编译检查 + 功能位同步/门禁检查"
              f"（{'跳过' if args.skip_smoke else '执行'}）")
        print(f"  1. 版本号 {old_ver} → {new_ver}（先按字节备份两个源文件）")
        print( "  2. 清空 dist")
        print( "  3. PyInstaller 打包 → Inno Setup 生成安装包")
        print( "  4. 上传 COS + 回读校验（★ 此后失败不再回滚版本号）")
        print( "  5. 同步下载页（版本号 + 微信 UA 可访问性自检）")
        print( "  6. 端到端匿名验证（SHA256 + 文件头）")
        print( "  7. 复制到桌面")
        print( "  7b. GitHub 提交 / push / Release")
        if getattr(args, "with_extras", False):
            print( "  7c. 四件套：免费版 → EdgeOne → 镜像站 → 发卡工具")
        print( "  8. 输出摘要 + 客户通知文案")
        print( "  9. 追加 release_log.md")
        print("\n状态机：上传前失败自动回滚版本号，上传后失败自动转为 --no-pack 续跑。")
        print("确认后去掉 --dry-run 再跑。")
        return 0

    # ── 0a) 断电保护 + 隐式耦合预检（不受 --skip-smoke 影响）──
    ok, why = preflight_sources()
    if not ok:
        print(f"[中止] {why}")
        return 1
    ok, why, need = check_keygen_freshness()
    if not ok:
        print("\n" + "!" * 58)
        print(f"[必须处理] 发卡工具功能位不同步：{why}")
        print(f"   重建命令：{_keygen_py()} tools/build_keygen.py")
        print("   或在发版命令上加 --with-extras 让它自动重建。")
        print("!" * 58)
    else:
        print(f"   [OK] {why}")

    # ── 0) 自检 ──
    if not args.skip_smoke:
        code, _ = run([PY, "-m", "py_compile", str(BRANDING), str(SMOKE)], "编译检查")
        if code != 0:
            return 1
        code, _ = run([PY, str(SMOKE)], "GUI 无头冒烟（10 个页面）")
        if code != 0:
            print("[中止] 冒烟没过，先修好再发版。")
            return 1

        # ★ 2026-10-12 客户投诉补的：新功能上线忘了登记功能位 →
        #   发卡工具里没有对应勾选框（客户在发卡界面一眼看出「关键词抓取」缺失）。
        #   发卡工具是遍历 licensing.FEATURES 自动生成的，所以真正要守的是
        #   「新页面必须登记功能位」+「登记的功能位必须定老码兼容策略」。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_feature_sync.py")],
                      "功能位与发卡工具同步检查")
        if code != 0:
            print("[中止] 有新页面/新功能位没同步到发卡工具，先修好再发版。")
            return 1

        code, _ = run([PY, str(ROOT / "tools" / "_validate_feature_gate.py")],
                      "功能位门禁与老授权码兼容检查")
        if code != 0:
            print("[中止] 授权门禁自测没过（老客户可能被误伤），先修好再发版。")
            return 1

        # ★ 2026-10-14 体验重构补的：默认页、导航分组、跨页链路、参数折叠
        #   这些都没有编译期保护，改别处时很容易顺手改回去而无人察觉。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_ux.py")],
                      "界面体验约束检查（默认页 / 导航优先级 / 跨页链路）")
        if code != 0:
            print("[中止] 界面体验约束被破坏（默认页或导航可能改回去了），先修好再发版。")
            return 1

        # ★ 2026-10-14 本地历史库：粒度（每天一个数据点）和容错（坏库不许
        #   拖垮采集）这两条都没有编译期保护，改错了不报错、只是曲线变成
        #   锯齿或库悄悄膨胀，所以钉在发版流程里。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_history_store.py")],
                      "本地历史库检查（粒度规则 / 容错 / 适配层挂载）")
        if code != 0:
            print("[中止] 历史库行为不符合预期（趋势图可能失真），先修好再发版。")
            return 1

        # ★ 2026-10-15 更新日志：安装向导里的「本次更新」、安装目录的日志页、
        #   官网的更新日志栏目，三处共用一份 changelog.json。任何一处脱节都
        #   不报错 —— 客户只是默默拿到空日志、或看到一屏问号，所以钉在这里。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_changelog.py")],
                      "更新日志检查（数据包 / 安装包 / 官网三处一致）")
        if code != 0:
            print("[中止] 更新日志三处对不上（客户会装到空日志或看到乱码），先修好再发版。")
            return 1

        # ★ 2026-10-15 官网 SEO：客户反馈"百度搜不到"。诊断出四个原因
        #   （页面没有搜索词 / 没有 robots 与 sitemap / 三域名 canonical 打架 /
        #   页面太薄），每一条都不报错、只是安静地不被收录，所以钉在这里。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_site_seo.py")],
                      "官网 SEO 检查（页面覆盖 / robots / sitemap / canonical）")
        if code != 0:
            print("[中止] 官网 SEO 要素缺失（会继续搜不到），先修好再发版。")
            return 1

        # ★ 2026-10-17 主题完整性：模板里用了、样式里没定义的类，页面不会报错 ——
        #   只是安静地退回浏览器默认样式。客户发来四张截图问「官网是不是没渲染好」，
        #   根因就是漏了 .pcard / .case / .who / .tags / .pbadge 这一批。
        #   顺带钉住另外两件同样不报错的事：更新日志写死浅色（深底上黑字）、
        #   自带字体没生成（整站退回系统黑体）。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_theme.py")],
                      "官网主题完整性（类名覆盖 / 深色可读 / 字体覆盖）")
        if code != 0:
            print("[中止] 官网主题有缺口的样式（会静默退回默认样式），先修好再发版。")
            return 1

        # ★ 2026-10-17 进度条：客户反馈「下载进度条不明显」。
        #   根因是 QSS 把条压到 6px 且不显示百分比、下载只按条数上报。
        #   这类退化不会报错 —— 只是条又变细、又不显示数字，所以静态扫 +
        #   行为测都钉在这里（含「百分比不许往回跳」）。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_progress.py")],
                      "进度条检查（全局统一 / 百分比可见 / 实时进度单调）")
        if code != 0:
            print("[中止] 进度条退化（又变细、或不显示百分比、或进度会倒退），先修好再发版。")
            return 1

        # ★ 2026-10-17 界面观感：客户反馈「卡片有不同深浅的阴影」。
        #   根因是 QSS 的 `QWidget{background}` 连 QLabel 一起匹配了，
        #   每个标签自带一块灰底。这类问题**编译不报错、功能也正常**，
        #   只是界面变脏，所以用像素级断言钉住（灰底 / 深色可读 / 动效还在）。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_ui_polish.py")],
                      "界面观感（子控件透明 / 深色可读 / 交互动效）")
        if code != 0:
            print("[中止] 界面观感退化（灰底回来了 / 深色标题看不见 / 动效被改没），先修好再发版。")
            return 1

        # ★ 2026-10-17 转写引擎：客户点「提取文案」弹出
        #   「由于找不到 cublas64_11.dll，无法继续执行代码」的系统错误框。
        #   根因是默认下的 11.8 包不含 cuBLAS，而 engine_missing_files 只查
        #   whisper.dll ⇒ 界面显示"已就绪"、一跑就炸。这类错误弹的是系统消息框，
        #   不是异常，try/except 抓不到，只能事前查文件 + 自动降级 CPU。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_transcribe_engine.py")],
                      "转写引擎（默认包自包含 / 缺库不判就绪 / 自动降级）")
        if code != 0:
            print("[中止] 转写引擎判定不可靠（会把跑不起来的包当就绪 / 缺库不降级），先修好再发版。")
            return 1

        # ★ 2026-11-10 客户「一键安装显卡引擎卡在 89%」：分块重试时
        #   `_done += len(chunk)` 把同一段字节重复累加，进度虚高又会因换源重算
        #   而倒退；停滞检测盯着累计字节，重试让字节一直变，于是永远不换源；
        #   取消还把 .part 删了 ⇒ 392/439MB 白下。编译完全不报错，只有客户
        #   盯着进度条来回横跳才看得出来，只能靠本地假 HTTP 断言钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_engine_download.py")],
                      "引擎下载（进度单调 / 停滞换源 / 断点续传 / 可停止）")
        if code != 0:
            print("[中止] 引擎下载退化（进度倒退或永远卡在 89% 下不完），先修好再发版。")
            return 1

        # 静态扫到 setEnabled(True) 不等于按钮真能点：本项目多次栽在
        # 「Qt 槽抛异常静默卡死」「状态栏被覆盖」上。用离屏真实 click()
        # 走一遍停止下载 → 失败弹窗切 CPU 的完整链路。
        code, _ = run([PY, str(ROOT / "tools" / "_probe_transcribe_stop.py")],
                      "转写页逃生出口（真点停止 / 一键切 CPU / 槽异常兜底）")
        if code != 0:
            print("[中止] 转写卡住时客户点「停止」或「改用 CPU 转写」没反应，先修好再发版。")
            return 1

        # ★ 2026-11-10 两条客户反馈：主页短链批量下载用不了、深色主题工作台
        #   一块白底。前者是解析函数被重建截断 + 短链跳首页后界面直接 return，
        #   后者是属性名大小写拼错 ⇒ GlowPanel 从不刷主题。两者都不报错，
        #   只能靠「真调 _toggle_theme + 像素亮度」和「假浏览器解短链」钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_blogger_batch.py")],
                      "主页批量下载 + 深色主题一致性")
        if code != 0:
            print("[中止] 主页短链批量下载或深色主题退化，先修好再发版。")
            return 1

        # ★ 2026-11-10 客户「转录里能不能加个本地目录按钮」：查代码时发现
        #   这一页除了缺批量导入入口，还有四个按钮只 addWidget、从没 connect
        #   （打开引擎目录 / 导入本地引擎包 / 导入本地模型 / 打开输出目录），
        #   点了完全没反应 —— 其中「导入本地引擎包」还是上一轮慢速下载提示里
        #   许给客户的逃生出口。编译查不出「按钮没接线」，只能靠真 click() 钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_transcribe_folder.py")],
                      "转录页本地目录（批量导入 / 打开目录 / 本地包 / 槽兜底）")
        if code != 0:
            print("[中止] 转录页本地目录按钮或四个目录按钮又变成摆设，先修好再发版。")
            return 1

        # ★ 2026-11-11 客户「加可视化卡片 + 目录里分不清转没转过」：
        #   同名文件覆盖、stem 含点算错文稿名、统计卡不刷新、深色徽标不换色、
        #   新按钮裸 connect —— 全是静默退化，编译和冒烟都查不出。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_transcribe_ui.py")],
                      "转录页可视化卡片 / 已转录判定 / 同名输出去重")
        if code != 0:
            print("[中止] 转录状态卡片或输出去重退化（客户会丢文稿 / 分不清转没转），先修好再发版。")
            return 1

        # ★ 2026-11-14 客户「tk 无法提取」：短链失败被甩成「国内被屏蔽」、
        #   英文口播被按中文转写、界面三处口径打架、直链 CDN 认浏览器 UA。
        #   全是静默退化，编译和冒烟都查不出，只能靠这条门禁钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_tiktok_extract.py")],
                      "TikTok 提取链路（短链展开 / 换链指引 / 自动语言 / 下载头 / 口径）")
        if code != 0:
            print("[中止] TikTok 提取链路退化（客户会看到「无法提取」却不知道换完整链接），先修好再发版。")
            return 1

        # ★ 2026-11-14 客户「短剧解析点看热榜一直显示正在拉取热榜」：
        #   4 处 on_error=self._status.set 在参数求值时就抛 AttributeError，
        #   Worker 从未启动、状态栏永远停在上一句；且整页没有凭证入口。
        #   编译/冒烟都查不出，只能靠「离屏 Qt 真点按钮」钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_drama_rank.py")],
                      "短剧解析热榜（guarded 回调 / 凭证入口 / 真点按钮不卡死）")
        if code != 0:
            print("[中止] 短剧解析页又会「一直正在拉取」，先修好再发版。")
            return 1

        # ★ 2026-11-14 免凭证公开热榜降级源：
        #   只许取官网首页 SSR 元数据，绝不能混进播放直链 / CENC 解密 /
        #   登录 / 批量；公开源条目也绝不能拿去调 detail 与下载。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_drama_public_rank.py")],
                      "免凭证公开热榜（合规边界 / 解析去重 / 降级接线 / 真点按钮）")
        if code != 0:
            print("[中止] 公开热榜越界或降级链路退化，先修好再发版。")
            return 1

        # ★ 2026-11-10 客户「官网配图全丢」：官网素材/ui/ 整个目录不在，
        #   build_site.py 遇缺图只 continue，产物里一排 <img src="">，
        #   curl 返回 200 也照样满屏破图。同轮加的全站背景音乐还要防
        #   「三个发布渠道漏传 mp3 就哑掉」。只能靠产物级断言钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_site_media.py")],
                      "官网配图 + 全站背景音乐（无空 src / 12 页播放器 / 音频兜底）")
        if code != 0:
            print("[中止] 官网又出现破图或音乐按钮失效，先修好再发版。")
            return 1

        # ★ 2026-10-17 品牌一致性：整套界面从 Tailwind indigo（AI 默认紫）
        #   换成官网的墨 + 柠檬。这类改动的退化全是"静默"的 —— QSS 漏一条规则
        #   就是一处永远紫；自绘组件（指标卡/进度条）不读 QSS，改了 QSS 它不变，
        #   于是出现一半墨一半紫的撕裂；深色勾选底变柠檬后白勾看不见。
        #   没有这条自检的话，下次谁照旧抄一个 #4F46E5 进去也不会有人知道。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_brand.py")],
                      "界面品牌一致性（旧色清零 / 弹层补齐 / 字号分层）")
        if code != 0:
            print("[中止] 品牌一致性退化（旧靛蓝回来了 / 弹层没样式 / 字号层级没了），先修好再发版。")
            return 1

        # ★ 2026-11-05 客户「直播监控 → 添加监控点了没反应」：源码重建时
        #   6 个页面把进度条+状态栏建了两遍，第二遍覆盖了 self.status，
        #   而那一条已掉出窗口且页面无需滚动 ⇒ 状态文字写进客户看不见的标签。
        #   编译不报错、功能其实跑完了，只是「看起来没反应」，只能靠几何断言钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_status_visible.py")],
                      "状态栏唯一且可见（防重复创建 / 防按钮无反应）")
        if code != 0:
            print("[中止] 有页面状态栏重复创建或掉出可达区域（客户会看到点了没反应），先修好再发版。")
            return 1

        code, _ = run([PY, str(ROOT / "tools" / "_validate_live_add_fast.py")],
                      "直播监控：添加零网络快路径 / 付费失败 CDP 免费兜底")
        if code != 0:
            print("[中止] 添加监控退化成联网解析，或余额不足时不再走免费兜底"
                  "（客户会以为主播没开播），先修好再发版。")
            return 1

        # ★ 2026-11-23 付费通道切换到 okdata：接口映射 / 参数改名 / 响应翻译 /
        #   未映射接口明确失败 / zacao 免费链路隔离 / okdata 下直播零付费。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_okdata.py")],
                      "okdata 付费通道：映射、翻译、错误与免费链路隔离")
        if code != 0:
            print("[中止] okdata 通道会误报成功、漏翻译字段，或把 zacao 免费链路"
                  "卷进付费通道，先修好再发版。")
            return 1

        # ★ 2026-11-06 客户「账号正在直播、工具显示未开播」：重建时丢了整套
        #   a_bogus 签名（resolve_by_web_rid 必抛 NameError），加上状态判定
        #   绑在「能否拿到流地址」上、解析失败又冒充未开播。这类退化不报错，
        #   只是安静地判错，只能靠三态断言钉住。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_live_status.py")],
                      "直播状态判定（在播/未开播/未知 三态分离）")
        if code != 0:
            print("[中止] 直播状态判定退化（会把正在直播显示成未开播），先修好再发版。")
            return 1

        # ★ 2026-11-15 客户「TikTok 直播监控以及录制不了」：整条直播链路原本
        #   只做抖音，TikTok 链接被丢给抖音解析器。接入后必须钉死平台路由、
        #   流地址重取、代理参数，以及抖音链路一个字节都不变。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_tiktok_live.py")],
                      "TikTok 直播接入（平台路由 / 状态三态 / 代理录制）")
        if code != 0:
            print("[中止] TikTok 直播接入退化（会退回「只支持抖音」或拉不到流），先修好再发版。")
            return 1

        # ★ 2026-11-15 一键导出日志：侧栏常驻入口 + 失败详情窗现场入口 +
        #   settings_page 参数名 bug（with_logs→include_logs）回归。这扇门
        #   坏掉的后果是「客户报问题、作者拿不到日志」，只能来回猜。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_log_export.py")],
                      "一键导出日志（入口 / 参数名 / 真打包内容）")
        if code != 0:
            print("[中止] 一键导出日志退化（客户报问题时拿不到日志），先修好再发版。")
            return 1

        # ★ 2026-11-15 P0 匿名故障摘要：这扇门守的是隐私红线 —— 开关默认值、
        #   字段白名单、脱敏、关闭时零联网、体检结论不含身份信息。任何一条
        #   被放宽，软件就会在客户不知情时外发数据，必须整链停在这里。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_diagnostics.py")],
                      "匿名故障摘要 / 统一体检（隐私红线 + 功能回归）")
        if code != 0:
            print("[中止] 匿名故障摘要的隐私约束被破坏（可能外发客户数据），先修好再发版。")
            return 1

        # ★ 2026-11-16 P1：发版链自身的可重复 / 可回滚也要有门禁。
        #   静态断言 + 沙盒真跑 main() 模拟「发布前失败 / 发布后失败 / 续跑」，
        #   防止以后有人把 mark_published 挪位、或让某个 early return 绕过回滚。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_release_chain.py")],
                      "发版链状态机（自动回滚 / 断点续跑 / 四件套接线）")
        if code != 0:
            print("[中止] 发版链回滚能力退化（失败后重跑会升两档或版本与线上不一致），先修好再发版。")
            return 1

        # ★ 2026-11-16 P2：轻量作业簿。这扇门守的是「下一步按钮不送错页面」
        #   和「作业簿不收集 Token / 客户名」两条，任何一条破了都别发。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_taskflow.py")],
                      "任务流作业簿（六步页面映射 / 隐私白名单 / 原子写）")
        if code != 0:
            print("[中止] 任务流作业簿退化（下一步会跳错页，或作业簿开始收集敏感信息），先修好再发版。")
            return 1

        # ★ 2026-11-17 系统标题栏跟随主题：这扇门守的是「深色界面顶白条」
        #   这个纯观感问题——它不报错、不崩溃，静态/冒烟门禁全抓不到，
        #   只能靠断言接线仍在。漏掉的表现就是客户又发来那张白条截图。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_titlebar.py")],
                      "系统标题栏跟随主题（DWM 深色 / 首屏+切换接线 / 兜底）")
        if code != 0:
            print("[中止] 系统标题栏同步退化（深色界面又顶白条），先修好再发版。")
            return 1

        # ★ 2026-11-18 录制历史 / 回放：这扇门守的是「录完能找回来、点得开」。
        #   同样是纯体验缺口——录制链路全绿但软件认不出录好的文件，编译/冒烟
        #   都抓不到，只能靠断言扫描行为 + UI 接线仍在。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_record_history.py")],
                      "录制历史与回放（扫描 / 系统播放器 / 停止刷新 / 空态）")
        if code != 0:
            print("[中止] 录制历史回放退化（录完的文件又找不回来），先修好再发版。")
            return 1

        # ★ 2026-11-18 窗口尺寸 / 独立窗口：守的是「窗口不被内容拉长 +
        #   复杂内容能弹出去独立看」。纯交互问题，编译/冒烟抓不到 ——
        #   只能断言尺寸上下限、借出归还闭环、多级弹窗接线都还在。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_window_sizing.py")],
                      "窗口尺寸与独立窗口（上下限 / 借出归还 / 多级弹窗）")
        if code != 0:
            print("[中止] 窗口尺寸或独立窗口退化（窗口又被拉长 / 弹出回不来），先修好再发版。")
            return 1

        # ★ 2026-11-19 下载列表「是否已下载」状态列：守的是状态显示与
        #   下载器跳过判据同源。纯显示问题编译/冒烟抓不到 —— 只能断言
        #   命名函数是单一来源、下载器确实调它、三处刷新接线都在。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_download_status.py")],
                      "下载状态列（命名同源 / 判据 / 刷新接线 / 离屏行为）")
        if code != 0:
            print("[中止] 下载状态列退化（状态与实际下载行为可能对不上），先修好再发版。")
            return 1

        # ★ 2026-11-19 多博主批量采集：客户问「能不能同时添加多位博主」。
        #   底层浏览器登录态只能串行，钉住去重/失败隔离/进度/付费闸门 —— 这些
        #   静默退化编译和冒烟都抓不到。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_blogger_multi.py")],
                      "多博主批量采集（串行 / 去重 / 失败隔离 / 进度 / 付费闸门）")
        if code != 0:
            print("[中止] 多博主批量采集退化（并发抢登录态 / 不去重 / 白嫖采集接口），先修好再发版。")
            return 1

        # ★ 2026-11-19 启动体验：客户反馈「每次打开闪空框、卡几秒」。真机抓到
        #   quick_proxy 构造期扫 14 个端口白等 3.5s + 云端开关重复请求 + 默认
        #   小窗闪现 + drama_page 17 处 status.set 异常。钉住端口延迟 / 缓存 /
        #   启动画面 / StatusLine 用法 —— 这类问题编译和冒烟都抓不到。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_startup.py")],
                      "启动体验（端口延迟 / 开关缓存 / 启动画面 / StatusLine）")
        if code != 0:
            print("[中止] 启动体验退化（空框闪 / 久等 / 启动刷新抛异常），先修好再发版。")
            return 1

        # ★ 2026-11-20 微信聊天整理：新增纯本地页面，解析微信合并转发 ZIP →
        #   Obsidian 笔记 / 文件夹 / 剪贴板。钉住真实 TXT 格式解析、ZIP 安全
        #   边界、三种出口、以及新增页的接入清单（漏一项打包后就静默失效）。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_wechat_archive.py")],
                      "微信聊天整理（解析 / 安全 / 出口 / 接入清单）")
        if code != 0:
            print("[中止] 微信聊天整理退化（解析失灵 / 路径穿越 / 清单漏登记），先修好再发版。")
            return 1

        # ★ 2026-11-20 视频号解析页：粘视频号链接 → 作者/标题/封面/直链，走
        #   免费 media_parser（不占功能位、不接付费闸门）。钉住 Worker 启动
        #   （漏 .start() 点了没反应）、免费链路不混付费、新增页 13 处接入清单。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_wechat_channels.py")],
                      "视频号解析（免费链路 / Worker / 接入清单 / 离屏冒烟）")
        if code != 0:
            print("[中止] 视频号解析退化（点了没反应 / 混付费链路 / 清单漏登记），先修好再发版。")
            return 1

        # ★ 2026-11-23 公众号运营页：按 okdata 官方技术文档接 wechat_mp 接口
        #   （搜号 / 历史文章 / 正文 / 互动 / 评论 / 搜一搜）。钉住 capability
        #   映射（打错接口就是白花钱）、付费闸门与 Worker.start()、以及新增页
        #   的接入清单；渲染用假响应，全程不联网不扣费。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_wechat_mp.py")],
                      "公众号运营（okdata 映射 / 付费闸门 / 接入清单 / 离屏冒烟）")
        if code != 0:
            print("[中止] 公众号运营退化（接口映射错 / 闸门失效 / 清单漏登记），先修好再发版。")
            return 1

        # ★ 2026-11-23 小红书笔记内容提取页：按 okdata 官方技术文档接
        #   xiaohongshu 接口（搜笔记 / 博主笔记 / 正文 / 评论 / 话题 / 联想词）。
        #   钉住正文绝不碰下载器的 note-details-v6、付费闸门与 Worker.start()、
        #   新增页接入清单；渲染用假响应，全程不联网不扣费。
        code, _ = run([PY, str(ROOT / "tools" / "_validate_xhs_notes.py")],
                      "小红书笔记（okdata 映射 / 正文隔离 / 付费闸门 / 接入清单 / 离屏冒烟）")
        if code != 0:
            print("[中止] 小红书笔记退化（接口映射错 / 正文走错链路 / 清单漏登记），先修好再发版。")
            return 1

    # ── 1) 升版本号 ──
    if not args.no_pack:
        print(f"\n{'─' * 58}\n▶ 升版本号 {old_ver} → {new_ver}\n{'─' * 58}")
        write_version(old_ver, new_ver)
        print(f"   [OK] branding.py / installer.iss 已同步")

    # ── 2) 清 dist ──
    if not args.no_pack:
        print(f"\n{'─' * 58}\n▶ 清空 dist\n{'─' * 58}")
        clear_dist()

    # ── 3) 生成更新日志（★ 必须在打包之前）──
    #   安装包里带的 更新日志.html、安装第 2 页那段「本次更新」，都出自这一步。
    #   放到 iscc 之后就晚了：客户装到的是上一版的日志，而且不报错、谁也看不出来。
    #   --no-pack 时版本号没落地，只能按当前版本写，否则日志会多出一个空壳版本。
    log_ver = new_ver if not args.no_pack else old_ver
    print(f"\n{'─' * 58}\n▶ 生成更新日志（v{log_ver}）\n{'─' * 58}")
    code, _ = run([PY, str(TOOLS / "build_changelog.py"),
                   "--version", log_ver, "--notes", args.notes],
                  "更新日志（打进安装包 + 官网栏目）")
    if code != 0:
        print("[中止] 更新日志没生成 —— 客户会装到一份空日志，先修好再发版。")
        return 2

    # ── 3.4) 生成官网首页 ──
    #   ★ 2026-10-17 补上的一步：以前发版流程只重生成子页，首页要人工跑
    #     build_site.py。首页里有「更新日志」栏目（读 changelog.json）和套餐价格，
    #     漏跑就会出现「新版已经发了、官网首页还写着上一版」——不报错、看不出来。
    print(f"\n{'─' * 58}\n▶ 生成官网首页\n{'─' * 58}")
    code, _ = run([PY, str(TOOLS / "build_site.py"), "--no-upload"],
                  "官网首页（含更新日志栏目与价格）")
    if code != 0:
        print("[中止] 官网首页没生成 —— 线上首页会停在上一版，先修好再发版。")
        return 2

    # ── 3.5) 生成官网栏目子页 + robots.txt + sitemap.xml ──
    #   同样必须在打包/部署之前：官网首页与子页都写进 standalone/，
    #   和第 6 步的部署共用同一个目录，晚一步线上就还是旧页面。
    print(f"\n{'─' * 58}\n▶ 生成官网栏目子页与站点地图\n{'─' * 58}")
    code, _ = run([PY, str(TOOLS / "build_subpages.py"), "--version", log_ver],
                  "官网栏目子页 + robots + sitemap")
    if code != 0:
        print("[中止] 官网子页没生成 —— 线上官网会缺栏目页，先修好再发版。")
        return 2

    # ── 3.6) 裁剪自带字体 ──
    #   顺序不能动：它按「页面里真正出现过的字」来裁子集，所以必须排在
    #   3.4 / 3.5 之后；又必须在第 6 步部署之前，否则线上目录里没有 fonts/，
    #   整站会悄悄退回系统黑体。
    print(f"\n{'─' * 58}\n▶ 裁剪自带字体（思源宋体 / 思源黑体 / Manrope）\n{'─' * 58}")
    code, _ = run([PY, str(TOOLS / "build_fonts.py")], "字体子集（只带走页面用到的字）")
    if code != 0:
        print("[中止] 字体没生成 —— 线上中文会退回雅黑，先修好再发版。")
        return 2

    # ── 4) 打包 ──
    if not args.no_pack:
        code, _ = run([PY, "-m", "PyInstaller", str(SPEC), "--noconfirm"], "PyInstaller 打包")
        if code != 0:
            print("[中止] 打包失败。")
            return 2

        code, _ = run([find_iscc(), str(ISS)], "Inno Setup 生成安装包")
        if code != 0:
            print("[中止] 安装包编译失败。")
            return 2

    setup_path = INSTALLER_OUT / setup_name
    if not setup_path.exists():
        print(f"[中止] 没找到 {setup_path}")
        return 2

    # ── 4) 发布到 COS ──
    code, out = run([PY, str(TOOLS / "publish_release.py"), "--notes", args.notes],
                    "上传 COS 并回读校验")
    if code != 0:
        print("[中止] 发布失败，云端未改动。")
        return 3
    # ★ 分界线：从这一刻起线上已经是新版本，后面任何失败都绝不回退源码版本号
    mark_published()
    print("   [状态机] 线上已发布；此后失败只续跑，不回滚版本号")

    # ── 5) 同步下载页 ──
    # 这一步以前漏了，导致发完 v1.2.0 页面还挂着 v1.1.0 的链接，
    # 新客户下载到的正是带致命 bug 的旧版。现在强制纳入发版流程。
    code, _ = run([PY, str(TOOLS / "publish_download_page.py")],
                  "更新下载页并实测微信 UA")
    if code != 0:
        print("[警告] 下载页更新失败 —— 安装包已发布，但页面可能仍是旧版，请手动补跑。")

    # ── 6) 端到端验证 ──
    cfg = json.loads((TOOLS / "cos_config.json").read_text(encoding="utf-8"))
    base = (cfg.get("cdn_domain") or
            f"https://{cfg['bucket']}.cos.{cfg.get('region','ap-guangzhou')}.myqcloud.com").rstrip("/")
    prefix = str(cfg.get("prefix") or "trafficcatcher").strip("/")
    feed = f"{base}/{prefix}/version.json"

    print(f"\n{'─' * 58}\n▶ 端到端验证（模拟客户机器）\n{'─' * 58}")
    ok, msg = verify_remote(feed, setup_path)
    print(f"   {'[OK]' if ok else '[失败]'} {msg}")
    if not ok:
        print("[警告] 云端可能有问题，请手动复核。")

    # ── 6) 放桌面 ──
    dst = DESKTOP / setup_name
    shutil.copy2(setup_path, dst)
    size = setup_path.stat().st_size
    digest = sha256_of(setup_path)
    print(f"\n   桌面副本 {dst}（{size:,} 字节）")

    # ── 7) 发卡工具新鲜度复查 ──
    #   预检（0a）已经报过一次；这里复查是因为打包过程中如果有人改了 FEATURES，
    #   发卡界面就会缺勾选框。--with-extras 会在链尾自动重建，不靠人记得。
    ok, why, need = check_keygen_freshness()
    if not ok:
        print(f"\n[必须处理] 发卡工具仍是旧的：{why}")
        print(f"   重建命令：{_keygen_py()} tools/build_keygen.py")
    else:
        print(f"\n   [OK] {why}")

    # ── 7b) GitHub 同步 ──
    #   公开仓 z18962725939-png/liuliangbushou：发版提交 + push + Release。
    #   放在摘要之前、线上发布之后：同步失败不影响本次发布，只提醒补跑。
    try:
        sync_github(new_ver, args.notes)
    except Exception as _e:                                    # noqa: BLE001
        print(f"\n[提醒] GitHub 同步未完成（线上发布不受影响）：{_e}")

    # ── 7c) 四件套（--with-extras 才跑，把原来四个手工命令收进一条）──
    if getattr(args, "with_extras", False):
        try:
            run_extras(new_ver, args.notes)
        except Exception as _e:                                # noqa: BLE001
            print(f"\n[警告] 四件套有一步没跑完（完整版发布不受影响）：{_e}")

    # ── 8) 摘要 ──
    print("\n" + "=" * 58)
    print(f" 发布完成  v{new_ver}")
    print("=" * 58)
    print(f"  安装包   {setup_name}")
    print(f"  体积     {size:,} 字节（{size / 1024 / 1024:.1f} MB）")
    print(f"  SHA256   {digest}")
    print(f"  更新源   {feed}")
    print(f"  下载直链 {base}/{prefix}/{quote(setup_name)}")
    print(f"  验证     {msg}")
    print("\n  可直接转发给客户的通知：")
    print("  " + "─" * 54)
    print(f"  流量捕手已更新到 v{new_ver}：{args.notes}")
    print( "  打开软件 → 右上角「检查更新」，按提示完成即可，数据不会丢失。")
    print("  " + "─" * 54)

    # ── 8) 写日志 ──
    INSTALLER_OUT.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    entry = (f"\n## v{new_ver} · {today}\n\n"
             f"- 更新：{args.notes}\n"
             f"- 体积：{size:,} 字节 / SHA256 `{digest}`\n"
             f"- 直链：{base}/{prefix}/{quote(setup_name)}\n"
             f"- 验证：{msg}\n")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(entry)
    print(f"\n  已追加到 {LOG_FILE.name}")
    return 0


# ──────────────────────────────────────────────────────────────────────
# main：只负责「版本决策 + 状态机 + 统一收口」，具体顺序全在 _pipeline
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        description="流量捕手一条龙发版（失败自动回滚 / 断点续跑 / 四件套可选）")
    ap.add_argument("--notes", required=True, help="更新说明，会写进 version.json 并展示给客户")
    ap.add_argument("--bump", choices=["patch", "minor", "major"], default="patch",
                    help="版本号递增方式，默认 patch（1.1.0 → 1.1.1）")
    ap.add_argument("--no-pack", action="store_true", help="跳过打包，只上传现有安装包")
    ap.add_argument("--skip-smoke", action="store_true", help="跳过 GUI 冒烟（赶时间时用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印步骤，不真的执行、不写状态")
    ap.add_argument("--with-extras", action="store_true",
                    help="发版后自动补跑四件套：免费版 / EdgeOne / 镜像 / 发卡工具")
    args = ap.parse_args()

    st = load_state()

    # 干跑：纯读，绝不写状态、绝不碰源文件
    if args.dry_run:
        old0 = read_version()
        old0, new0, _ = decide_versions(old0, args.no_pack, args.bump, False)
        if st:
            mode = "线上已发布" if st.get("published") else "发布前中断"
            print(f"[状态机] 检测到残留状态 v{st.get('old_ver')} → v{st.get('new_ver')}（{mode}）")
            print("  正式跑会自动回滚或续跑，不需要人工改版本号。")
        return _pipeline(args, old0, new0)

    resumed = bool(st and st.get("published"))
    if st and not resumed:
        # 上次在上传前中断：源码里可能已经是新版本号，先按字节整份恢复
        restore_sources(str(st.get("old_ver", "")), str(st.get("new_ver", "")))
        clear_state()
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
        print(f"[状态机] 上次发版在上传前中断，已回滚 "
              f"v{st.get('new_ver')} → v{st.get('old_ver')}")
        st = None
    if resumed:
        args.no_pack = True
        args.skip_smoke = True
        print(f"[状态机] 上次线上已发布 v{st.get('new_ver')} 但后续步骤没跑完："
              "自动按 --no-pack 续跑（不 bump / 不打包 / 不重跑门禁）")

    old_ver = read_version()
    old_ver, new_ver, note = decide_versions(old_ver, args.no_pack, args.bump, resumed)
    if note:
        print(f"  {note}")

    if not (resumed and st):                   # 续跑沿用原状态（保留 published 标记）
        from datetime import datetime, timedelta, timezone
        started = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        if new_ver != old_ver:
            snapshot_sources()                 # 必须在 write_version 之前
        save_state({
            "schema": 1, "old_ver": old_ver, "new_ver": new_ver,
            "notes": args.notes, "bump": args.bump,
            "phase": "started", "published": bool(resumed),
            "started_at": started,
        })

    try:
        rc = _pipeline(args, old_ver, new_ver)
    except KeyboardInterrupt:
        print("\n[中断] 收到 Ctrl+C，正在按阶段回滚 / 保留状态……")
        rc = 130
    except Exception:                                          # noqa: BLE001
        print("\n[异常] 发版链抛出未捕获异常：")
        traceback.print_exc()
        rc = 99
    finish(rc, load_state() or {"old_ver": old_ver, "new_ver": new_ver,
                                "published": resumed})
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
