# -*- coding: utf-8 -*-
"""
品牌与全局常量（唯一事实来源）。

为什么要单独一个模块：
    软件名会出现在窗口标题、exe 文件名、安装包名、开始菜单、安装目录、
    用户数据目录、注册表卸载项等 8 个以上地方。散落在各处改一次漏一次，
    所以全部收敛到这里，以后要改名只改这一个文件。

注意：本模块【只能依赖标准库】。
    gui.py / transcribe.py / browser_login.py / licensing.py / updater.py
    都会 import 它，任何包内依赖都可能引入循环导入。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ----------------------------------------------------------------------
#  品牌信息 —— 改名只需动这一段
# ----------------------------------------------------------------------

APP_NAME = "流量捕手"
APP_NAME_EN = "TrafficCatcher"          # 纯 ASCII：用于数据目录、注册表键、文件名

# ★ 免费版开关（v1.23.0 起「一份源码两个包」）
#   实现方式：同一份源码打两个包，靠 PyInstaller 的 runtime hook
#   （tools/free_edition_hook.py）在启动前注入 TRAFFIC_EDITION=free。
#   好处是**不用在打包前改源码再改回来** —— 那种做法只要有一次忘了还原，
#   完整版就会被打成免费版，而且不会有任何报错。
#   ★ 2026-10-31 重建补回：这三个常量在源码重建中丢失，界面一 import 就
#     AttributeError（main_window / settings_page / guide 都读它们）。
APP_EDITION = (os.environ.get("TRAFFIC_EDITION") or "full").strip().lower()
APP_IS_FREE = APP_EDITION == "free"
# 窗口标题 / 侧栏品牌名用它，别在界面里写死「流量捕手」
APP_DISPLAY_NAME = f"{APP_NAME} 免费版" if APP_IS_FREE else APP_NAME
APP_SLOGAN_FREE = "抖音视频下载 · 直播录制"
APP_SLOGAN = "短视频运营 · 直播监控录制 · 音视频转文字"
APP_VERSION = "1.28.3"
APP_PUBLISHER = "流量捕手工作室"
APP_COPYRIGHT = f"Copyright © 2026 {APP_PUBLISHER}"

# 想让客户怎么找你？把下面两项改成真实信息，会显示在「关于 / 购买」里。
# 留空则不显示对应行。
APP_HOMEPAGE = ""        # 例：https://your-site.com/liuliangbushou（官网暂未开放）
APP_CONTACT = "微信：zsx807108536"   # 已填写：客户加微信联系购买 / 授权

# 默认更新源（可在设置页改）。指向一个返回 version.json 的地址，支持
# GitHub/Gitee Release、对象存储、任意静态服务器。留空 = 默认不自动检查。
# 这里用腾讯云 COS 广州桶（发版脚本自动上传），比 GitHub Releases 稳：
# 客户在国内拉 GitHub 经常超时，拉不动就永远看不到更新提示。
_FEED_BASE = "https://liuliangbushou-1258850027.cos.ap-guangzhou.myqcloud.com/trafficcatcher"
# ★ 免费版必须指向 version_free.json。用同一个文件的话，免费版客户会
#   被更新成完整版 exe，装完打开全是点不动的付费功能（2026-10-23 投诉）。
UPDATE_FEED_URL = _FEED_BASE + ("/version_free.json" if APP_IS_FREE else "/version.json")

# 安装包 / exe 文件名（不含扩展名）
# ★ 免费版的 exe 名必须带「免费版」：两个包装在同一台机器上，名字一样的话
#   后装的会覆盖先装的，客户完整版直接消失。
EXE_NAME = APP_NAME + ("免费版" if APP_IS_FREE else "")   # 流量捕手.exe / 流量捕手免费版.exe
SETUP_NAME = f"{EXE_NAME}_Setup"        # 流量捕手_Setup_… / 流量捕手免费版_Setup_…

# Inno Setup 的 AppId —— 【发布之后绝对不能再改】。
# Inno 靠它判断"装的是不是同一个软件"。改了它：
#   · 升级时不会覆盖旧版本，而是装出第二套；
#   · 「添加/删除程序」里出现两条记录，卸载永远卸不干净。
# 所以要换软件名、换发行商、换版本都行，唯独这个值必须原样留着。
SETUP_APP_ID = "5BDDBD5C-2236-4B26-83EE-B1BC915CD180"
# ★ 免费版用另一个 AppId：Inno 靠它判断"是不是同一个软件"，共用一个的话
#   免费版会被当成完整版的升级，装完覆盖掉完整版。两个 AppId 都要钉死。
SETUP_APP_ID_FREE = "A7F3C1D9-4E2B-4F8A-9C56-2D8B7E1A5C93"

# ----------------------------------------------------------------------
#  素材保存目录（桌面下）
# ----------------------------------------------------------------------

# 桌面上的总目录：{流量捕手}采集 / 视频下载、直播录制
DESKTOP_WORK_DIR = f"{APP_NAME}采集"
# 老版本用过的目录名：只要它还在，就继续往里存，避免用户的素材"凭空换地方"
LEGACY_DESKTOP_WORK_DIR = "JustOneAPI采集"

# 没有显式指定保存目录时的默认子目录
VIDEO_SUBDIR = f"{APP_NAME}_视频"
LIVE_SUBDIR = f"{APP_NAME}_直播录制"

# ----------------------------------------------------------------------
#  数据目录
# ----------------------------------------------------------------------

# 用户级数据目录名（%APPDATA%\<这个名字>）。刻意用 ASCII：
# 中文目录名在某些第三方库（比如 ffmpeg 参数、浏览器 user-data-dir）里
# 会因为代码页问题被截断，踩过一次就不想再踩。
DATA_DIR_NAME = APP_NAME_EN

# 更名前用过的目录名。首次启动时自动把里面的东西搬到新目录，
# 老用户升级后不用重新填 Token、重新扫码登录、重新下载转写引擎。
LEGACY_DATA_DIR_NAMES = ("JustOneAPICollector",)

_MIGRATE_MARKER = ".migrated_from_legacy"


def _appdata_root() -> Path:
    """%APPDATA% 根目录（非 Windows 或变量缺失时退化为 home）。"""
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or ""
    return Path(base) if base else Path.home()


def app_data_dir(name: str = DATA_DIR_NAME) -> Path:
    """当前品牌的数据目录（不创建、不迁移，纯计算）。"""
    return _appdata_root() / name


def legacy_data_dirs() -> list[Path]:
    """历史品牌留下的数据目录（存在才返回）。"""
    out: list[Path] = []
    for n in LEGACY_DATA_DIR_NAMES:
        if n == DATA_DIR_NAME:
            continue
        p = _appdata_root() / n
        try:
            if p.is_dir():
                out.append(p)
        except OSError:
            continue
    return out


def migrate_legacy_data(verbose: bool = False) -> str:
    """把旧品牌数据目录搬到新目录（只做一次）。

    策略：优先整体 move（快，几 GB 的转写引擎也是秒级）；
    move 失败（跨盘 / 被占用 / 权限）退化为逐项复制；
    单个子项失败不影响其他项 —— 宁可少搬一个缓存目录，也不能让程序起不来。

    返回一句话说明，便于写日志/提示用户。已经搬过或没有旧目录时返回 ""。
    """
    dst = app_data_dir()
    if (dst / _MIGRATE_MARKER).exists():
        return ""
    srcs = [p for p in legacy_data_dirs() if p != dst]
    if not srcs:
        # 打个标记，省得每次启动都去 file-system 里找一圈
        try:
            dst.mkdir(parents=True, exist_ok=True)
            (dst / _MIGRATE_MARKER).write_text(
                "no legacy data\n", encoding="utf-8")
        except OSError:
            pass
        return ""

    moved: list[str] = []
    failed: list[str] = []
    for src in srcs:
        # 1) 整体搬（最快，且不会留下半套数据）
        try:
            if not dst.exists():
                src.replace(dst)
                moved.append(src.name)
                continue
        except OSError:
            pass

        # 2) 逐项搬
        try:
            dst.mkdir(parents=True, exist_ok=True)
            for child in list(src.iterdir()):
                target = dst / child.name
                if target.exists():
                    continue                      # 新目录已有的不覆盖
                try:
                    if child.is_dir():
                        shutil.copytree(child, target, dirs_exist_ok=True)
                    else:
                        shutil.copy2(child, target)
                    moved.append(f"{src.name}/{child.name}")
                except Exception as e:            # noqa: BLE001
                    failed.append(f"{child.name}({e})")
        except Exception as e:                    # noqa: BLE001
            failed.append(f"{src.name}({e})")

    try:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / _MIGRATE_MARKER).write_text(
            f"moved={moved}\nfailed={failed}\n", encoding="utf-8")
    except OSError:
        pass

    if not moved and not failed:
        return ""
    msg = f"已把旧版数据迁移到 {dst}"
    if failed:
        msg += f"（{len(failed)} 项未迁移：{', '.join(failed[:3])}）"
    if verbose:
        print(msg, file=sys.stderr)
    return msg


# ----------------------------------------------------------------------
#  版本比较
# ----------------------------------------------------------------------

def version_tuple(text: str) -> tuple:
    """把 "1.2.3" / "v1.2.3-beta" 转成可比较的元组。

    非数字部分统一按 0 处理，保证不会因为奇怪的后缀抛异常。
    """
    parts: list[int] = []
    for chunk in str(text or "").strip().lstrip("vV").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    """远端版本是否比本地新。"""
    return version_tuple(remote) > version_tuple(local)
