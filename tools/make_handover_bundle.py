"""把项目打包成一份「能直接上传、接手就能继续干活」的备份包。

用法：
    python tools/make_handover_bundle.py                     # 默认输出到桌面
    python tools/make_handover_bundle.py --out D:/备份/xx     # 指定目录
    python tools/make_handover_bundle.py --no-zip            # 只出文件夹，不打 zip

设计原则（踩过坑才这么写）：
1. **白名单复制**：只搬源码、配置模板和打包必需资产，不搬产物。
   项目里有 564 MB 安装包历史、126 MB PyInstaller 产物、几十 MB 发卡 exe，
   全拷进去既传不动，也会让新会话在 dist/ 里翻到错误的"源码"。
2. **密钥零容忍**：授权私钥、腾讯云子账号密钥、真实授权码一律不进包，
   复制完再全量扫一遍关键词兜底（见 _audit_secrets）。
3. **打包必需资产不能漏**：build/app_icon.ico 被 spec 和 Inno 脚本共用，
   assets/whisper_cpu/Release 是内置转写引擎，漏了打包直接中断。
4. **自带接手说明**：新环境没人知道这里的铁律，文档必须随包走。
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 复制范围

# 整个目录搬走（内部再按 EXCLUDE_DIR_NAMES 过滤）
COPY_DIRS = [
    "justoneapi_collector",
    "gui_qt",
    "tests",
    "docs",
    "examples",
    "bot",
]

# 只搬这些子路径（父目录本身太大或混杂产物）
COPY_DIRS_PARTIAL = [
    "tools",                       # 剔除 _keygen_build / _keygen_dist / pay_qr / 密钥
    "deploy/scf_site",             # 官网站点源码（zip 产物不要）
    "assets/whisper_cpu/Release",  # 内置 whisper CPU 引擎，spec 里 datas 引用
]

# 根目录单文件白名单
COPY_ROOT_FILES = [
    "README.md",
    "DESIGN.md",
    "requirements.txt",
    "pyproject.toml",
    "installer.iss",
    "installer_test.iss",
    "justoneapi_collector.spec",
    "gui_main.py",
    ".env.example",
    ".gitignore",
    "build_exe.bat",
    "一键打包.bat",
]

# build/ 里 PyInstaller 中间产物上百 MB，只取这两个（★ app_icon.ico 被 spec 与
# installer.iss 同时引用，删了 Inno 会中断）
COPY_BUILD_FILES = [
    "build/app_icon.ico",
    "build/app_icon_preview.png",
]

EXCLUDE_DIR_NAMES = {
    "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache",
    "_keygen_build", "_keygen_dist", "pay_qr", ".edgeone", ".workbuddy",
    # 浏览器自动化残留：tools/.edge_poster_profile 里塞着整份 Edge 扩展缓存，
    # 与项目无关却有几 MB，按名字点名 + 通用规则双保险
    ".edge_poster_profile", "_shots", "_tmp", "_tmp_shots", "_tmp_script_out",
}

# 任何以点开头的目录都当工具状态处理（.github 这类协作目录本项目没有）
EXCLUDE_DOT_DIRS = True

# 敏感文件：绝不进备份包（相对项目根）
EXCLUDE_FILES = {
    "tools/license_private.key",        # ★ 授权私钥，泄露 = 别人能自己造授权码
    "tools/.token_seed",
    "tools/cos_config.json",            # 腾讯云子账号密钥
    "tools/cos_config.json.bak",
    "tools/作者自用_不限量授权码.txt",     # 真实授权码（内嵌 Token）
    ".env",
}

EXCLUDE_SUFFIX = {".pyc", ".pyo", ".pyd", ".tmp", ".log"}

# 单文件超过这个体积就不搬（产物误入时的兜底）
MAX_FILE_MB = 40

# ---------------------------------------------------------------- 敏感扫描

# 第一层：文件名精确命中即报警（源码里"提到"这些文件名是正常的，不算）
SECRET_FILE_NAMES = {
    "license_private.key": "授权私钥",
    ".token_seed": "授权码派生种子",
    "cos_config.json": "腾讯云密钥",
    "cos_config.json.bak": "腾讯云密钥备份",
    "作者自用_不限量授权码.txt": "真实授权码",
    ".env": "环境变量（含接口 Token）",
}

# 第二层：内容命中真实密钥特征才报警。
# 注意别写成关键词匹配 —— 源码里到处都在引用 cos_config.json 这个文件名，
# 按关键词扫会刷出上百条误报，真正的风险是"值"泄露。
SECRET_VALUE_PATTERNS = {
    "腾讯云 SecretId": re.compile(r"AKID([A-Za-z0-9]{20,})"),
    "腾讯云 SecretKey": re.compile(r"\"secret_key\"\s*:\s*\"([A-Za-z0-9+/=]{20,})\""),
    "私钥 PEM": re.compile(r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----"),
    "接口 Key": re.compile(r"sk-([A-Za-z0-9]{24,})"),
}

# 模板与文档里出现这些字样是正常的（说明性文字）
SECRET_WHITELIST = {"cos_config.json.example", ".env.example"}
SCAN_SUFFIX = {".py", ".json", ".txt", ".md", ".cfg", ".ini", ".example",
               ".yaml", ".yml", ".toml", ".key", ".pem"}
SCAN_SKIP_DIRS = {"tests"}      # 测试里用假 token，不算泄露


# ---------------------------------------------------------------- 工具

def _copy_file(src: Path, dst: Path) -> bool:
    """复制单个文件，返回是否真的复制了。"""
    if not src.is_file():
        return False
    if src.suffix.lower() in EXCLUDE_SUFFIX:
        return False
    if src.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _copy_tree(src: Path, dst: Path) -> int:
    """递归复制目录，按规则过滤，返回复制的文件数。

    排除判断一律以「相对项目根」为准（EXCLUDE_FILES / EXCLUDE_DIR_NAMES 都是
    相对根的写法），目标路径则相对 src，这样 deploy/scf_site 这类二级目录
    才不会被复制成 deploy/scf_site/scf_site/…。
    """
    n = 0
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        parts = p.relative_to(PROJECT_ROOT).parts
        if any(part in EXCLUDE_DIR_NAMES for part in parts):
            continue
        if EXCLUDE_DOT_DIRS and any(
                part.startswith(".") and part != p.name for part in parts):
            continue
        rel_root = "/".join(parts)
        if rel_root in EXCLUDE_FILES:
            continue
        if p.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        if _copy_file(p, dst / p.relative_to(src)):
            n += 1
    return n


def _first_docstring(path: Path, limit: int = 90) -> str:
    """取模块或主类的首段说明，用于生成模块速查表。"""
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:                                     # noqa: BLE001
        return ""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                doc = ast.get_docstring(node)
                if doc:
                    break
    if not doc:
        return ""
    line = " ".join(doc.split())
    return line[:limit] + ("…" if len(line) > limit else "")


# 自动抓的说明照录，但这两个必须人工点名，否则新会话会踩坑
MANUAL_NOTES = {
    "justoneapi_collector/gui.py":
        "★ 废弃的 Tkinter 版界面，不要改它 —— 界面一律改 gui_qt/",
    "tools/license_keygen.py":
        "★ 发卡工具源码。改完必须跑 build_keygen.py 重建 exe，否则桌面版不生效",
    "tools/cos_config.json.example":
        "对象存储配置模板，复制成 cos_config.json 后填真实密钥",
}


def _mark_lang(note: str) -> str:
    """早期模块的首段说明是英文原文，标出来免得看不懂还以为是乱码。"""
    if not note:
        return note
    zh = sum(1 for ch in note if "\u4e00" <= ch <= "\u9fff")
    if zh >= 2:
        return note
    return note + "　〔英文原文，未译〕"


def _loc(path: Path) -> int:
    """统计行数，让速查表能看出模块体量。"""
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except Exception:                                     # noqa: BLE001
        return 0


# ---------------------------------------------------------------- 文档生成

def build_readme(out: Path, version: str, stamp: str) -> None:
    core = sorted((PROJECT_ROOT / "justoneapi_collector").glob("*.py"))
    pages = sorted((PROJECT_ROOT / "gui_qt" / "pages").glob("*.py"))

    def core_row(p: Path) -> str:
        rel = p.relative_to(PROJECT_ROOT).as_posix()
        note = MANUAL_NOTES.get(rel) or _mark_lang(_first_docstring(p)) or "—"
        return f"| `{p.name}` | {_loc(p)} | {note} |"

    core_rows = "\n".join(core_row(p) for p in core if p.name != "__init__.py")
    page_rows = "\n".join(
        f"| `{p.name}` | {_loc(p)} | {_mark_lang(_first_docstring(p)) or '—'} |"
        for p in pages if p.name != "__init__.py"
    )

    text = f"""# 00 · 先看这里（流量捕手 项目接手包）

> 生成时间：{stamp}　|　备份自版本：**v{version}**　|　原始工程：`justoneapi-collector`

这份包是**可直接上传、接手就能继续干活**的精简备份：只含源码、配置模板、
打包必需资产和本文档。安装包历史、PyInstaller 产物、缓存和**所有密钥**已被剔除
（详见 `脱敏说明.md`）。

---

## 1. 这是什么

面向抖音 / 小红书 / 快手 / TikTok 的 **Windows 桌面数据采集与内容生产工具**，
商业软件形态：PySide6 界面 + PyInstaller 打包 + Inno Setup 安装包 + 云端自动更新 + 授权码激活。

当前功能矩阵：选题挖掘、关键词抓取、竞品体检、评论区截流、GEO（AI 可见度监测）、
复盘监控、报告、音视频转文本、**短视频文案仿写（含从链接提取文案）**、批量导入下载。

---

## 2. 新环境怎么跑起来

| 步骤 | 命令 / 说明 |
|---|---|
| Python | **3.10**（本机用 `C:/Python310/python.exe`）。更高版本 PySide6  wheels 未必齐 |
| 装依赖 | `pip install -r requirements.txt` |
| 另需装 | `pip install PySide6==6.11.2 PyInstaller==6.22.3`（requirements 里没写，是打包/界面依赖） |
| 启动界面 | `python gui_main.py` |
| 无界面冒烟 | `QT_QPA_PLATFORM=offscreen python gui_qt/_smoke.py`（14 个页面能否构建） |
| 打包 | `python tools/release_all.py --bump minor --notes "更新说明"`（一条龙，见第 6 节） |

打包还需要 **Inno Setup 6** 装在默认路径（生成安装包那一步要用）。

---

## 3. 目录地图

| 目录 | 是什么 |
|---|---|
| `justoneapi_collector/` | 核心业务层：API 客户端、平台适配、采集、导出、授权、转写、文案仿写 |
| `justoneapi_collector/platforms/` | 40 个平台的接口定义与适配器 |
| `gui_qt/` | **界面唯一入口**（PySide6）。`app.py` 启动、`main_window.py` 导航、`pages/` 各功能页 |
| `gui_qt/pages/_base.py` | 页面公共组件与**统一报错诊断入口** |
| `tools/` | 发版、发布、发卡、自检、探针脚本（67 个 py） |
| `tests/` | 全量回归测试（`python tests/run_all.py`） |
| `docs/` | 证书与子 Token 配置说明 |
| `assets/whisper_cpu/Release/` | 内置 whisper CPU 转写引擎，spec 的 datas 会打进安装包 |
| `build/app_icon.ico` | 应用图标，**spec 与 installer.iss 共用**，删了打包中断 |
| `bot/` | 配套 Node 服务（server.mjs） |

---

## 4. 核心模块速查

### justoneapi_collector/

| 文件 | 行数 | 作用 |
|---|---|---|
{core_rows}

### gui_qt/pages/

| 文件 | 行数 | 作用 |
|---|---|---|
{page_rows}

---

## 5. ★ 铁律（改代码前必读，都是踩坑换来的）

1. **界面只改 `gui_qt/`**。`justoneapi_collector/gui.py` 是废弃的 Tkinter 版，别动。
2. **长页面必须包 `QScrollArea`**（`main_window._scroll_wrap()`）。
   新增/改动页面后跑 `python tools/_probe_pages_height.py`，超 760px 必须有滚动区
   （新页面要手动加进该脚本的清单）。
3. **绝不把示例数据填进真实结果表**——曾灌假帖被客户判定"搜不到相关视频"。
4. **不用 `hasattr` 兜底**隐藏 bug——曾导致整表空白且无任何报错。
5. **运行时动态 import 的模块必须写进 `justoneapi_collector.spec` 的 `hiddenimports`**，
   否则打包后功能静默失效（已登记：quota、login_gate、geo_monitor、script_rewriter、
   video_script_extract 等）。
6. **改 `branding.py` 常量必须重新打包**；验证 exe 内常量要解 PYZ（明文搜不到）。
7. **中文文件名直链必须 percent-encode**（`updater._encode_url`），否则更新下载 404。
8. **`BrowserLoginSession.poll()` 返回三元组** `(state, cookie_header, info)`，
   必须显式判 `state == "logged_in"`，不能写 `if ok:`。
9. **"界面上有开关" ≠ "功能接上了"**：新增开关后要回溯「开关 → 实际调用点」。
10. **报错文案必须说清到底哪一步坏了**：统一走 `gui_qt/pages/_base.py` 的
    `connection_diagnosis()`。四种状态分开：已激活缺凭证 / 试用期 / 已过期 / 未激活，
    **只有"未激活"才引导去授权页**。把两种原因糊成一句话 = 把客户困死。
11. **★ 一条龙交付**：客户 bug / 需求不逐项请示，改码 → 自测 → 打包 → 发布 → 汇报，
    用户自己通知客户。中途除非涉及钱 / 不可逆删除 / 要凭据，否则别打断。
12. **★ 文案禁止夹带外文单词**：发版说明、更新弹窗、官网文案写完先扫一遍
    （曾把孤立英文词写进 `--notes`，直接进了客户可见的更新弹窗）。
13. **QSS 的 `url()` 只认真实文件路径**，不能写 `file:///`；自定义 `::indicator` 后
    Qt 不再画对勾，须自带勾图（`tools/gen_check_icon.py`）。
14. **手动清 `dist/*`，绝不删 `build/`**（里面有 app_icon.ico）。
15. **换环境或交给别人继续做**：跑 `python tools/make_handover_bundle.py`，
    会生成一份剔除密钥与打包产物的接手包（含本文档、模块速查、脱敏说明、开场提示词）。

---

## 6. 发版一条龙

```bash
# 常规（新功能用 minor，修 bug 默认 patch）
python tools/release_all.py --bump minor --notes "更新说明"

# 只改云端不想重打包
python tools/release_all.py --no-pack

# 预演，不实际执行
python tools/release_all.py --dry-run
```

流程内部会自动：冒烟 → 功能位自检（`_validate_feature_sync` / `_validate_feature_gate`，
**不过直接中止**）→ 升版本 → 清 dist → PyInstaller → Inno → 传 COS → 端到端验证 → 出客户文案。

### ★ 发版后必须同步三套托管

| 托管 | 命令 |
|---|---|
| COS 静态站 | `python tools/publish_download_page.py`（release_all 已含） |
| WorkBuddy 旧域名 | `workbuddy_sites_deploy` 部署 `installer_output/standalone`（微信 UA 403，只给电脑端老客户） |
| 自有域名 流量捕手.cn | `edgeone makers deploy "installer_output/standalone" -n trafficcatcher -a overseas --json` |

对外宣发一律用自有域名（`https://xn--kpuzw91vr71b.cn/`，Punycode，2027-10-10 到期）。

---

## 7. 授权与功能位

- 授权体系在 `justoneapi_collector/licensing.py`：`FEATURES`（12 个功能位）、
  `FEATURE_BACKFILL`（老码补位策略）、`check()` 门禁。
- **★ 新增功能位必须同步三件事**：
  1. `FEATURES` 登记（不登记 = 发卡工具里没勾选框）
  2. `FEATURE_BACKFILL` 定老码策略：`auto` = 老码自动获得（随套餐售出的常规功能，**绝不能收回**）；
     `deny` = 老码不含即不可用（独立付费模块，如 GEO）
  3. 用 `deny` 的位，页面必须有 `licensing.check("该位")` 真门禁
- **★ 发卡工具是打包好的 exe，改 `tools/license_keygen.py` 源码不生效，
  必须跑 `python tools/build_keygen.py` 重建**（约 65 秒，自动备份旧版 + 盖指纹章）。
- **★ 出码必须填「接口 Token」**：客户端没有 Token 输入框，凭证只能随码下发，
  码里没 Token 的客户一定卡在"未连接采集服务"。

---

## 8. 自测命令表

| 目的 | 命令 |
|---|---|
| 全量回归 | `python tests/run_all.py` |
| 功能位同步（导航↔FEATURES↔门禁↔发卡） | `python tools/_validate_feature_sync.py` |
| 老客户权益不被收回 | `python tools/_validate_feature_gate.py` |
| 授权诊断文案 | `python tools/_validate_license_diag.py` |
| GEO 引擎 | `python tools/_validate_geo.py` |
| 文案仿写 | `python tools/_validate_script_rewriter.py` |
| 链接→文案提取 | `python tools/_validate_video_script.py` |
| 页面高度 | `QT_QPA_PLATFORM=offscreen python tools/_probe_pages_height.py` |
| 界面冒烟 | `QT_QPA_PLATFORM=offscreen python gui_qt/_smoke.py` |
| GEO 模型名真机探测 | `python tools/_probe_geo_models.py` |

真机探针（`_probe_*.py`）会真实联网 / 调 API，跑之前确认 Key 在
`%APPDATA%/TrafficCatcher/config.json`。

---

## 9. 已知遗留（等拍板）

1. `www.流量捕手.cn` 无 DNS 记录（证书 SAN 已含 www）。
2. HTTP 未强制跳 HTTPS。
3. 全客户共用一个内嵌 Token，建议改「每客户独立 Token + 预算上限」。
4. 小红书 / 快手评论链路未真机验证（抖音已实测）。
5. GEO 引用信源非联网通道基本为空，**不可写进合同硬承诺**。
6. 豆包引擎走 CDP，通道未开放。

> ✅ v1.20.1 已修：额度日界以前落在下午 16:00（`localtime(t+8h)` 在中国
> 机器上等于 UTC+16），客户每天平白丢掉 16:00–24:00 这段额度，且首页写
> 「16:00 重置」而弹窗写「0 点」，自相矛盾。现已改成 `gmtime(t+8h)`，
> 日界回到 0 点，并带旧数据兼容（见 `quota._today` / `_read_count` 注释）。

---

## 10. 继续干活的起点

- 想改功能：先 `模块速查.md` 定位文件 → 看第 5 节铁律 → 改 → 跑第 8 节对应自测。
- 想发版：第 6 节，一条龙跑完记得同步三套托管。
- 缺密钥：见 `脱敏说明.md`，私钥和云密钥不在这份包里，需从本机单独取。
"""
    (out / "00_先看这里.md").write_text(text, encoding="utf-8")


def build_module_index(out: Path) -> None:
    """按目录列出所有 py 文件与首行说明，便于新会话快速定位。"""
    lines = ["# 模块速查（自动生成）", "",
             "> 列出备份包内每个 Python 文件的行数与首段说明，用来快速定位该改哪里。", ""]
    groups = [
        ("核心业务层", sorted((PROJECT_ROOT / "justoneapi_collector").rglob("*.py"))),
        ("界面层", sorted((PROJECT_ROOT / "gui_qt").rglob("*.py"))),
        ("工具与自检", sorted((PROJECT_ROOT / "tools").rglob("*.py"))),
        ("测试", sorted((PROJECT_ROOT / "tests").rglob("*.py"))),
    ]
    for title, files in groups:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| 文件 | 行数 | 说明 |")
        lines.append("|---|---:|---|")
        for p in files:
            if p.name == "__init__.py":
                continue
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            note = MANUAL_NOTES.get(rel) or _mark_lang(_first_docstring(p)) or "—"
            lines.append(f"| `{rel}` | {_loc(p)} | {note} |")
        lines.append("")
    (out / "模块速查.md").write_text("\n".join(lines), encoding="utf-8")


def build_secret_note(out: Path, stamp: str) -> None:
    text = f"""# 脱敏说明（这份包里少了什么、怎么补回）

生成时间：{stamp}

## 一、被剔除的敏感文件

这些**故意不带**，泄露任一项都会造成实际损失：

| 文件 | 风险 | 备份包里的替代 |
|---|---|---|
| `tools/license_private.key` | ★ 授权私钥。泄露 = 别人能自己造授权码 | 无。产品里只需公钥 `licensing.LICENSE_PUBLIC_KEY_HEX`（已在源码中） |
| `tools/.token_seed` | 授权码派生种子 | 无 |
| `tools/cos_config.json` / `.bak` | 腾讯云子账号 SecretId/Key，可操作对象存储 | `tools/cos_config.json`（模板已复制，见下） |
| `tools/作者自用_不限量授权码.txt` | 真实授权码（内嵌接口 Token） | 无 |
| `.env` | 本机接口 Token 等环境变量 | `.env.example`（已复制） |
| `tools/pay_qr/` | 收款二维码图片 | 无 |

## 二、被剔除的体积大户（都可重新生成，不必备份）

| 目录 / 文件 | 体积 | 怎么恢复 |
|---|---|---|
| `installer_output/` | 564 MB | 历次安装包 + 官网静态站，重跑 `release_all.py` 即可；最新版也能从 COS 下载 |
| `dist/` | 126 MB | PyInstaller 产物，重跑打包 |
| `installer/`、`installer_test/` | 76 MB | 同上 |
| `build_keygen/`、`tools/_keygen_build/`、`tools/_keygen_dist/` | 85 MB | `python tools/build_keygen.py` 重建（约 65 秒） |
| `_archive/`、`.justoneapi_cache/`、`_tmp_script_out/` | 36 MB | 归档 / 接口缓存 / 临时产物 |
| `_douyin_live_recorder_src/` | 1 MB | 第三方参考源码，与本项目无关 |
| 官网素材/、宣传素材/ | 5 MB | 图片素材 |
| `流量捕手安装包_v1.0.0.exe` | 38 MB | 最早的 v1.0.0 安装包 |

## 三、在新环境补齐配置

1. **接口 Token**：复制 `.env.example` → `.env`，填 `JUSTONEAPI_TOKEN`。
   或在软件「设置」里填（会存到 `%APPDATA%/TrafficCatcher/config.json`）。
2. **对象存储（发版上传用）**：复制 `tools/cos_config.json.example` → `tools/cos_config.json`，
   填 `secret_id` / `secret_key` / `bucket` / `region` / `prefix`。
   子账号名：`trafficcatcher-publisher`。
3. **AI 引擎 Key（GEO / 文案仿写用）**：在软件「GEO 监测」页填，存在本机 `geo_api_keys`。
4. **授权私钥**：**不要**随包传输。确需在新环境发码，用安全信道单独拷贝
   `tools/license_private.key`，用完即删。

## 四、自检

打包脚本复制完成后会自动扫一遍关键词，确认包内没有上述敏感文件残留。
你也可以自己复核：

```bash
python -c "from pathlib import Path;print([str(p) for p in Path('.').rglob('*') if p.is_file() and p.name in {'license_private.key','cos_config.json','.env'}])"
```
"""
    (out / "脱敏说明.md").write_text(text, encoding="utf-8")


def build_manifest(out: Path, version: str, stamp: str) -> int:
    files = sorted(p for p in out.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    lines = [
        f"# 文件清单 · 流量捕手项目备份 v{version}",
        f"生成时间：{stamp}",
        f"文件数：{len(files)}　总体积：{total/1024/1024:.1f} MB",
        "",
    ]
    for p in files:
        rel = p.relative_to(out).as_posix()
        size = p.stat().st_size
        unit = f"{size/1024:.1f} KB" if size < 1024 * 1024 else f"{size/1024/1024:.2f} MB"
        lines.append(f"{unit:>10}  {rel}")
    (out / "文件清单.txt").write_text("\n".join(lines), encoding="utf-8")
    return len(files)


def _is_placeholder(value: str) -> bool:
    """xxxx…、AKIDxxxx 这类模板值不算泄露。"""
    v = (value or "").strip().lower()
    if not v:
        return True
    if len(set(v)) <= 2:                       # 全 x、全 abab 之类
        return True
    return any(k in v for k in ("xxx", "your", "placeholder", "example",
                                "示例", "占位", "待填", "改成"))


def _audit_secrets(out: Path) -> list[str]:
    """复制完再全量扫一遍，防止规则漏网。返回报警列表。

    两层：先看有没有把敏感文件本身搬进来（最致命），
    再看内容里有没有真实密钥值（源码引用文件名不算）。
    """
    warns: list[str] = []
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out).as_posix()

        if p.name in SECRET_FILE_NAMES:
            warns.append(f"★ 敏感文件被搬进包里（{SECRET_FILE_NAMES[p.name]}）：{rel}")
            continue
        if rel in EXCLUDE_FILES:
            warns.append(f"★ 排除规则失效：{rel}")
            continue
        if p.name in SECRET_WHITELIST:
            continue
        if rel.split("/")[0] in SCAN_SKIP_DIRS:
            continue
        if p.suffix.lower() not in SCAN_SUFFIX:
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:                                  # noqa: BLE001
            continue
        for label, pat in SECRET_VALUE_PATTERNS.items():
            hit = pat.search(body)
            if not hit:
                continue
            # 取正则里的值分组判断是不是模板占位；没分组就取整条匹配
            val = hit.group(1) if hit.groups() else hit.group(0)
            if _is_placeholder(val):
                continue
            warns.append(f"★ {label} 疑似明文残留：{rel}")
            break
    return warns


def build_kickoff(out: Path, version: str) -> None:
    """给新会话的开场白：粘进对话里，省掉一轮摸底。"""
    text = f"""下面是接手「流量捕手」项目时要先说的一段话，直接粘到新对话里即可。
（也可以只上传 00_先看这里.md 再发这段）

—————————————— 复制以下部分 ——————————————

我上传了「流量捕手」项目的完整源码备份（当前版本 v{version}）。
这是一个面向抖音 / 小红书 / 快手 的 Windows 桌面数据采集与内容生产工具：
PySide6 界面 + PyInstaller 打包 + Inno Setup 安装包 + 云端自动更新 + 授权码激活。

请你先读根目录下的 `00_先看这里.md`，它讲清了：项目定位、怎么在新环境跑起来、
目录地图、模块速查、14 条改代码前必读的铁律、发版一条龙流程、自测命令表、
以及已知的遗留问题。读完再看 `模块速查.md` 定位具体文件。

之后我会直接给你需求（通常是客户的 bug 反馈或新功能要求）。请你遵守：

1. 全程用简体中文回复，代码和专有名词除外；写给用户看的文案里不许夹带孤立的外文单词。
2. 一条龙交付：定位 → 改码 → 自测（跑 `tests/run_all.py` 和对应的
   `tools/_validate_*.py`）→ 打包（`tools/release_all.py`）→ 汇报结果 + 给一段
   可以直接转发给客户的通知文案。中途不要停下来问我，除非涉及钱、不可逆删除、
   或者需要我提供凭据。
3. 改任何界面代码只看 `gui_qt/`；`justoneapi_collector/gui.py` 是废弃版本，别动。
4. 遇到报错排查不动时，先说明你查到哪一步、卡在哪，再给下一步建议。

先回我一句「已读完，可以开始」，然后等我给需求。

—————————————— 复制以上部分 ——————————————

补充说明（这段不用发给它）：
- 备份里没有授权私钥和云密钥，需要发版 / 发卡时再单独配置，见 `脱敏说明.md`。
- 真机验证类的探针脚本（`tools/_probe_*.py`）会真实联网或调 API，
  让它跑之前确认本机已填好接口 Key。
"""
    (out / "豆包开场提示词.txt").write_text(text, encoding="utf-8")


def _read_version() -> str:
    try:
        src = (PROJECT_ROOT / "justoneapi_collector" / "branding.py").read_text(
            encoding="utf-8", errors="ignore")
        for line in src.splitlines():
            if line.strip().startswith("APP_VERSION"):
                return line.split("=")[1].strip().strip('"\'')
    except Exception:                                      # noqa: BLE001
        pass
    return "未知"


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="生成项目接手备份包")
    ap.add_argument("--out", default="",
                    help="输出目录，默认桌面 流量捕手_项目备份_YYYYMMDD")
    ap.add_argument("--no-zip", action="store_true", help="只生成文件夹，不打包 zip")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    version = _read_version()

    if args.out:
        out = Path(args.out)
    else:
        desk = Path.home() / "Desktop"
        out = desk / f"流量捕手_项目备份_{datetime.now():%Y%m%d}"

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print(f"备份目标：{out}")
    print(f"备份版本：v{version}\n")

    n = 0
    for d in COPY_DIRS:
        src = PROJECT_ROOT / d
        if src.exists():
            c = _copy_tree(src, out / d)
            print(f"  ✓ {d:<26} {c:>4} 个文件")
            n += c

    for d in COPY_DIRS_PARTIAL:
        src = PROJECT_ROOT / d
        if not src.exists():
            print(f"  ! 缺失 {d}")
            continue
        c = _copy_tree(src, out / d)
        print(f"  ✓ {d:<26} {c:>4} 个文件")
        n += c

    for f in COPY_ROOT_FILES + COPY_BUILD_FILES:
        if _copy_file(PROJECT_ROOT / f, out / f):
            n += 1
    print(f"  ✓ 根目录与 build 资产      {len(COPY_ROOT_FILES) + len(COPY_BUILD_FILES)} 项（逐个核对）")

    print("\n生成接手文档…")
    build_readme(out, version, stamp)
    build_module_index(out)
    build_secret_note(out, stamp)
    build_kickoff(out, version)

    print("\n敏感文件扫描…")
    warns = _audit_secrets(out)
    if warns:
        for w in warns:
            print(f"  ⚠ {w}")
    else:
        print("  ✓ 无私钥 / 云密钥 / 真实授权码残留")

    count = build_manifest(out, version, stamp)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"\n备份完成：{count} 个文件，{total/1024/1024:.1f} MB")

    if not args.no_zip:
        zp = out.with_suffix(".zip")
        if zp.exists():
            zp.unlink()
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(out).as_posix())
        print(f"压缩包：{zp}（{zp.stat().st_size/1024/1024:.1f} MB）")

    print(f"\n上传时把「00_先看这里.md」一起给到新会话，它能省掉大半摸底时间。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
