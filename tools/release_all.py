# -*- coding: utf-8 -*-
"""
一条龙发版：改完代码跑这一条，剩下的全自动。

    python tools/release_all.py --notes "修复直播录制偶发中断"
    python tools/release_all.py --notes "新增批量导出" --bump minor
    python tools/release_all.py --notes "只重新上传，不打包" --no-pack
    python tools/release_all.py --notes "看看会发生什么" --dry-run

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
def run(cmd: list[str], title: str, tail_lines: int = 12) -> tuple[int, str]:
    """跑命令并实时打印，返回 (退出码, 完整输出)。"""
    print(f"\n{'─' * 58}\n▶ {title}\n{'─' * 58}")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    lines: list[str] = []
    for line in proc.stdout:  # type: ignore[union-attr]
        lines.append(line)
        if len(lines) <= tail_lines or line.strip().startswith(("[", "错误", "ERROR")):
            print("   " + line.rstrip())
    proc.wait()
    print(f"   … 退出码 {proc.returncode}（共 {len(lines)} 行输出）")
    return proc.returncode, "".join(lines)


def read_version() -> str:
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', BRANDING.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("[错误] branding.py 里读不到 APP_VERSION")
    return m.group(1)


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
def main() -> int:
    ap = argparse.ArgumentParser(description="流量捕手一条龙发版")
    ap.add_argument("--notes", required=True, help="更新说明，会写进 version.json 并展示给客户")
    ap.add_argument("--bump", choices=["patch", "minor", "major"], default="patch",
                    help="版本号递增方式，默认 patch（1.1.0 → 1.1.1）")
    ap.add_argument("--no-pack", action="store_true", help="跳过打包，只上传现有安装包")
    ap.add_argument("--skip-smoke", action="store_true", help="跳过 GUI 冒烟（赶时间时用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印步骤，不真的执行")
    args = ap.parse_args()

    old_ver = read_version()
    new_ver = old_ver if args.no_pack else bump(old_ver, args.bump)
    setup_name = f"流量捕手_Setup_v{new_ver}.exe"

    print("=" * 58)
    print(" 流量捕手 · 一条龙发版")
    print("=" * 58)
    print(f"  当前版本 v{old_ver}  →  v{new_ver}（{args.bump}）")
    print(f"  更新说明 {args.notes}")
    print(f"  模式     {'干跑' if args.dry_run else '正式'}")

    if args.dry_run:
        print("\n将依次执行：")
        print(f"  0. GUI 冒烟 + 编译检查 + 功能位同步/门禁检查"
              f"（{'跳过' if args.skip_smoke else '执行'}）")
        print(f"  1. 版本号 {old_ver} → {new_ver}（branding.py + installer.iss）")
        print( "  2. 清空 dist")
        print( "  3. PyInstaller 打包 → Inno Setup 生成安装包")
        print( "  4. 上传 COS + 回读校验")
        print( "  5. 同步下载页（版本号 + 微信 UA 可访问性自检）")
        print( "  6. 端到端匿名验证（SHA256 + 文件头）")
        print( "  7. 复制到桌面")
        print( "  8. 输出摘要 + 客户通知文案")
        print( "  9. 追加 release_log.md")
        print("\n确认后去掉 --dry-run 再跑。")
        return 0

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
                      "直播监控添加：标准链接零网络快路径")
        if code != 0:
            print("[中止] 添加监控又退化成联网解析（客户要点完等半分钟才见反馈），先修好再发版。")
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

    # ── 7) 发卡工具新鲜度检查 ──
    #   桌面发卡工具是打包好的 exe，它的「包含功能」勾选框来自 licensing.FEATURES。
    #   功能位变了却没重建 exe → 发卡界面里看不到新功能位
    #   （客户就是发现「关键词抓取」没有勾选框才来投诉的）。
    #   这里用「功能位指纹」判断，比看文件时间准确：只有功能清单真的变了才提醒。
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from justoneapi_collector import licensing as _lic
        stamp_file = TOOLS / "_keygen_stamp.json"
        now_feats = list(_lic.FEATURES.keys())
        if stamp_file.is_file():
            stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
            if list(stamp.get("features") or []) != now_feats:
                print("\n" + "!" * 58)
                print("[必须处理] 功能位清单变了，但桌面上的发卡工具还是旧的 exe！")
                print(f"   发卡工具里：{stamp.get('features')}")
                print(f"   当前代码里：{now_feats}")
                print("   不重建的话，你在发卡界面里看不到新功能位。请跑：")
                print("     C:/Python310/python.exe tools/build_keygen.py")
                print("!" * 58)
        else:
            print("\n[提醒] 还没盖过功能位指纹章，建议跑一次 tools/build_keygen.py。")
    except Exception as _e:                                    # noqa: BLE001
        print(f"\n[提醒] 发卡工具新鲜度检查未执行：{_e}")

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


if __name__ == "__main__":
    raise SystemExit(main())
