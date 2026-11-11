# -*- coding: utf-8 -*-
"""诊断与反馈支持：采集环境快照 + 打包日志，供用户「一键发给作者」。

零服务器方案（有意为之）
------------------------
客户点「一键发日志 / 提交反馈」→ 程序把最近的日志 + 环境快照打成 zip 放到
桌面（并尝试打开所在文件夹）→ 弹窗引导客户把 zip 通过微信发给作者。作者拿到
zip 就能复盘现场，全程不需要任何后端。

为什么不做自动上传：
    上传要么得自建服务器/对象存储，要么得把第三方 Token 打进 exe —— 一旦被挖
    出来就是别人替你花钱。打包到本地再手动发：零成本、零风险，而且客户自己
    也能看一眼包里是什么，反而更放心。

依赖：标准库 + 包内 branding / licensing / logger（transcribe 延迟导入）。
"""

from __future__ import annotations

import platform
import socket
import sys
import time
import zipfile
from pathlib import Path

from . import branding, licensing, logger


# ----------------------------------------------------------------------
#  小工具
# ----------------------------------------------------------------------

def _mask_token(tok: str) -> str:
    """Token 只留头尾，避免诊断包外泄完整凭证。"""
    t = (tok or "").strip()
    if not t:
        return "（无）"
    if len(t) <= 8:
        return t[:2] + "*" * max(0, len(t) - 2)
    return f"{t[:4]}…{t[-4:]}（共 {len(t)} 位）"


def _desktop_dir() -> Path:
    d = Path.home() / "Desktop"
    return d if d.exists() else Path.home()


def _run_quiet(fn, default=""):
    try:
        return fn()
    except Exception as e:                           # noqa: BLE001
        return f"{default}（{e}）" if default else f"（取值失败：{e}）"


# ----------------------------------------------------------------------
#  环境快照
# ----------------------------------------------------------------------

def environment_report(current_token: str = "") -> str:
    """一段人读+机读都行的环境快照，写进诊断包。

    current_token：调用方可把「当前正在用的 Token」传进来（已脱敏展示）。
    """
    import os as _os

    L: list[str] = []
    ap = L.append
    ap("=" * 66)
    ap(f"{branding.APP_NAME}（{branding.APP_NAME_EN}）诊断报告")
    ap("=" * 66)
    ap(f"生成时间    : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    ap(f"软件版本    : v{branding.APP_VERSION}")
    ap(f"发行商      : {branding.APP_PUBLISHER}")
    ap(f"运行方式    : {'打包 exe' if getattr(sys, 'frozen', False) else '源码运行'}")
    ap("")

    ap("── 系统 ─────────────────────────────────")
    ap(f"操作系统    : {platform.platform()}")
    ap(f"系统版本    : {platform.version()}")
    ap(f"机器架构    : {platform.machine()}")
    ap(f"处理器      : {platform.processor() or '（未知）'}")
    ap(f"计算机名    : {_run_quiet(socket.gethostname, '（未知）')}")
    ap(f"Python      : {sys.version.split()[0]}")
    ap("")

    ap("── 授权 ─────────────────────────────────")
    try:
        st = licensing.status()
        ap(f"授权状态    : {st.get('label') or '未激活'}")
        ap(f"状态码      : {st.get('state')}")
        ap(f"本机机器码  : {licensing.machine_id()}")
        ap(f"客户名      : {st.get('customer') or '（无）'}")
        ap(f"内嵌 Token  : {_mask_token(st.get('embedded_token') or '')}")
    except Exception as e:                           # noqa: BLE001
        ap(f"授权信息    : 读取失败（{e}）")
    tok_now = current_token or _os.environ.get("JUSTONEAPI_TOKEN", "")
    ap(f"当前 Token  : {_mask_token(tok_now)}")
    ap("")

    ap("── 转写引擎 / 显卡 ──────────────────────")
    try:
        from . import transcribe as _t
        gpu = _t.detect_gpu()
        ap(f"显卡        : {gpu.summary()}")
        ap(f"CPU 引擎    : {'已就绪' if _t.engine_ready('cpu') else '未安装'}")
        ap(f"CUDA 引擎   : {'已就绪' if _t.engine_ready('cuda') else '未安装'}")
        models = []
        for key in getattr(_t, "MODELS", {}):
            if _t.model_ready(key):
                models.append(key)
        ap(f"已下载模型  : {', '.join(models) if models else '（无）'}")
    except Exception as e:                           # noqa: BLE001
        ap(f"转写信息    : 检测失败（{e}）")
    ap("")

    ap("── 路径 ─────────────────────────────────")
    ap(f"数据目录    : {_run_quiet(lambda: str(branding.app_data_dir()))}")
    ap(f"日志目录    : {_run_quiet(lambda: str(logger.log_dir()))}")
    ap(f"当前日志    : {logger.active_log_file() or logger.log_file_path()}")
    ap("=" * 66)
    return "\n".join(L)


# ----------------------------------------------------------------------
#  打包
# ----------------------------------------------------------------------

def _iter_log_files() -> list[Path]:
    """主日志 + 轮转备份（app.log / app.log.1 ...）。"""
    base = logger.active_log_file() or logger.log_file_path()
    out: list[Path] = []
    try:
        parent = base.parent
        if not parent.is_dir():
            return out
        # 主文件优先，其余按修改时间倒序
        cands = [p for p in parent.glob(base.name + "*") if p.is_file()]
        cands.sort(key=lambda p: (p.name != base.name, -p.stat().st_mtime))
        out = cands[:6]
    except Exception:                                # noqa: BLE001
        pass
    return out


def make_support_bundle(kind: str = "反馈", note: str = "",
                        include_logs: bool = True,
                        out_dir: Path | None = None,
                        current_token: str = "") -> Path:
    """把「诊断报告 + 日志 + 用户描述」打成一个 zip 放到桌面，返回 zip 路径。

    kind：bug / 功能建议 / 其他（只用于文件名和内容标记）
    note：用户手写的描述
    """
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe_kind = "".join(ch for ch in (kind or "反馈") if ch not in '\\/:*?"<>|')[:12]
    target_dir = Path(out_dir) if out_dir else _desktop_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / f"{branding.APP_NAME}_诊断日志_{safe_kind}_{stamp}.zip"

    report = environment_report(current_token=current_token)
    feedback_text = (
        f"类型：{kind}\n"
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"联系方式（作者微信）：{branding.APP_CONTACT or '（未填写）'}\n"
        f"{'-' * 40}\n"
        f"{note or '（用户未填写具体描述）'}\n"
    )

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("诊断报告.txt", report + "\n\n" + _howto_text())
        zf.writestr("反馈内容.txt", feedback_text)
        if include_logs:
            for lf in _iter_log_files():
                try:
                    zf.write(lf, arcname=f"日志/{lf.name}")
                except Exception:                    # noqa: BLE001
                    continue
    return zip_path


def _howto_text() -> str:
    return (
        "【怎么把这个包发给作者】\n"
        f"1. 打开桌面，找到本压缩包；\n"
        f"2. 加作者微信：{branding.APP_CONTACT or '（见「设置 → 授权与激活」）'}\n"
        "3. 把压缩包直接拖进微信发过去，并简单说一句你遇到的问题。\n"
        "作者拿到后就能定位并修复，修好会推送新版本给你。\n"
    )


def open_in_explorer(path: Path) -> None:
    """在资源管理器中定位文件（Windows）；非 Windows 静默忽略。"""
    p = Path(path)
    try:
        import os
        import subprocess
        if os.name == "nt":
            if p.is_dir():
                os.startfile(str(p))                 # noqa: S606
            else:
                subprocess.Popen(["explorer", "/select,", str(p)])
    except Exception:                                # noqa: BLE001
        pass


def install_crash_hooks() -> None:
    """让未处理的异常也进日志（GUI 崩溃时可回溯）。"""
    logger.install_crash_hooks()
