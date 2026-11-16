# -*- coding: utf-8 -*-
"""匿名故障摘要 + 统一运行体检（P0，2026-11-15）。

为什么单独一个模块
------------------
线上出问题时，作者拿到的只有「客户手动打包发来的日志」——没有分母，不知道
100 个用户里多少人卡在代理、多少人没激活、哪个版本失败率最高。下一版修什么
全靠最会吐槽的那位客户决定。

这个模块补两件事：
  1) 统一体检结论：把散在两处的「授权凭证诊断」和「网络出口诊断」合成一段
     可复制的文字，失败弹窗自动附带，客户不必再描述「我用不了」；
  2) 结构化故障摘要：只记「发生了什么、在哪一步」，本地落盘 + 可选匿名上报。

★ 隐私红线（写死，不许放宽）
----------------------------
`support.environment_report()` 是给客户**主动打包发作者**的诊断包，里面有
机器码、客户名、计算机名、本地路径、Token 掩码——那没问题，因为客户知情且
主动。但自动上报是软件自己发出去的，绝不能复用那份报告。

所以本模块：
  · 字段走 `EVENT_FIELDS` 白名单，调用方**无法**塞进任意键；
  · 所有值过 `scrub()`：URL / 本地路径 / 长十六进制 / 邮箱 / 手机号一律
    替换成「已隐去」，再截断到 120 字符；
  · 不记视频链接、账号、文案、机器码、授权码、客户名、文件路径；
  · `anon_id` 是首次随机生成的 16 位十六进制，**与机器无关**；
  · 上报开关默认关闭，且**必须同时**配好 endpoint 才可能发请求；
  · endpoint 只接受 https 或本机回环 http，明文外发一律拒绝；
  · 关闭或没配 endpoint 时，`flush()` 连 socket 都不会碰（门禁会断言）。

★ 为什么本模块不 import PySide6
--------------------------------
保持纯标准库，门禁可以直接跑运行时断言（不需要 QApplication / 离屏环境），
也避免事件记录被 Qt 生命周期绑住。UI 接入在 settings_page.py 与
log_export.py，那边才碰剪贴板、按钮和 Worker。

★ 部署口径
----------
客户端里**没有任何写死的服务器地址**。要真正收到数据，由作者在客户机的
`config.json` 写 `diag_endpoint`，或设环境变量 `TRAFFIC_DIAG_ENDPOINT`；
界面只显示「通道已配置 / 未配置」，不让客户填地址。没配就是纯本地记录。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

# 顶部 import：PyInstaller 静态分析能看到，不需要塞 hiddenimports 兜底。
# 这几个模块本身已被工程其它位置引用，打包里必然存在。
from justoneapi_collector import branding, logger

try:
    from justoneapi_collector import licensing, tiktok_live
except Exception:                                            # noqa: BLE001
    licensing = None                                         # type: ignore[assignment]
    tiktok_live = None                                       # type: ignore[assignment]


# --------------------------------------------------------------------------
#  字段白名单（★ 唯一允许落盘 / 外发的键）
# --------------------------------------------------------------------------

EVENT_FIELDS: tuple[str, ...] = (
    "ts",        # Unix 秒（整数）
    "ver",       # 软件版本
    "edition",   # full / free
    "anon_id",   # 随机匿名 ID，与机器无关
    "kind",      # 事件类别（枚举）
    "step",      # 失败步骤（枚举，不是自由文本）
    "code",      # 错误码 / 简短结论
    "egress",    # 出口模式：proxy / direct / none / unknown
    "result",    # ok / fail / unknown
)

# 这些词一旦出现在字段名里，就说明有人想绕过白名单 —— 门禁会扫描源码。
FORBIDDEN_FIELD_WORDS = (
    "machine", "customer", "token", "cookie", "url", "link", "path",
    "account", "text", "detail", "message", "content", "license", "code_text",
)

_MAX_VALUE_LEN = 120
_MAX_FILE_BYTES = 256 * 1024
_MAX_KEEP = 3
_MAX_BATCH = 50
_FLUSH_TIMEOUT = 3

# 第二道防线：即便调用方往 step/code 里塞了自由文本，也先抹掉敏感片段。
_SENSITIVE_PATTERNS = (
    re.compile(r"https?://[^\s,;]+", re.I),                  # 任何链接
    re.compile(r"[A-Za-z]:[\\/][^\s,;]+"),                   # Windows 本地路径
    re.compile(r"(?<!\w)/(?:Users|home|AppData)/[^\s,;]+", re.I),
    re.compile(r"\b[A-F0-9]{16,}\b", re.I),                   # 机器码 / 长十六进制
    re.compile(r"\b[\w.+-]+@[\w-]+\.\w+\b"),                  # 邮箱
    re.compile(r"\b1[3-9]\d{9}\b"),                           # 手机号
    re.compile(r"\b(?:ghp|sk|AKID)[A-Za-z0-9_\-]{8,}\b"),    # 常见凭据前缀
)

_scrub_placeholder = "已隐去"
_flush_lock = threading.Lock()


def scrub(value, limit: int = _MAX_VALUE_LEN) -> str:
    """把任意值变成「可以安全落盘 / 外发」的短字符串。"""
    s = str(value if value is not None else "")
    for pat in _SENSITIVE_PATTERNS:
        s = pat.sub(_scrub_placeholder, s)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s[:limit]


# --------------------------------------------------------------------------
#  配置
# --------------------------------------------------------------------------

def _setting(ctx, key: str, default=None):
    if ctx is None:
        return default
    try:
        return ctx.get_setting(key, default)
    except Exception:                                        # noqa: BLE001
        return default


def endpoint(ctx=None) -> str:
    """读取上报地址；没有就返回空串。

    ★ 客户端源码里没有任何写死的服务器地址。只允许 https，或本机回环的
      http（本地自测用）。明文 http 往外发一律拒绝，避免谁手滑配错。
    """
    raw = str(os.environ.get("TRAFFIC_DIAG_ENDPOINT") or "").strip()
    if not raw:
        raw = str(_setting(ctx, "diag_endpoint", "") or "").strip()
    if not raw:
        return ""
    if raw.startswith("https://"):
        return raw
    if raw.startswith("http://127.0.0.1") or raw.startswith("http://localhost"):
        return raw
    return ""


def report_enabled(ctx=None) -> bool:
    """双条件：用户开了开关，并且确实配了合法地址。默认一定是 False。"""
    return bool(_setting(ctx, "diag_report_enabled", False)) and bool(endpoint(ctx))


def _anon_id(ctx) -> str:
    if ctx is None:
        return "unknown"
    val = str(_setting(ctx, "diag_anon_id", "") or "").strip()
    if val:
        return val
    val = uuid.uuid4().hex[:16]
    try:
        ctx.set_setting("diag_anon_id", val)
        ctx.save_settings()
    except Exception:                                        # noqa: BLE001
        pass
    return val


# --------------------------------------------------------------------------
#  本地记录（脱敏 JSONL + 体积轮转）
# --------------------------------------------------------------------------

def events_dir() -> Path:
    return Path(logger.log_dir()) / "diagnostics"


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > _MAX_FILE_BYTES:
            _rotate(path)
    except OSError:
        pass
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def _rotate(path: Path) -> None:
    """events.jsonl → .1 → .2 → .3，超过就丢最老的一份。"""
    oldest = path.with_suffix(path.suffix + f".{_MAX_KEEP}")
    if oldest.exists():
        oldest.unlink()
    for i in range(_MAX_KEEP - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        if src.exists():
            src.rename(path.with_suffix(path.suffix + f".{i + 1}"))
    if path.exists():
        path.rename(path.with_suffix(path.suffix + ".1"))


def build_event(kind, *, step: str = "", code: str = "", egress: str = "",
                result: str = "", ctx=None) -> dict:
    """构造一条事件。★ 只接受固定枚举参数，调用方塞不进任意字段。"""
    ev = {
        "ts": int(time.time()),
        "ver": str(getattr(branding, "APP_VERSION", "?") or "?"),
        "edition": "free" if getattr(branding, "APP_IS_FREE", False) else "full",
        "anon_id": _anon_id(ctx),
        "kind": scrub(kind, 40),
        "step": scrub(step, 60),
        "code": scrub(code, 60),
        "egress": scrub(egress, 20),
        "result": scrub(result, 20),
    }
    # 最后再过一次白名单：任何未来新增的键，只要不在 EVENT_FIELDS 就丢掉。
    return {k: v for k, v in ev.items() if k in EVENT_FIELDS}


def record_event(ctx, kind, *, step: str = "", code: str = "",
                 egress: str = "", result: str = "", out_dir=None) -> dict:
    """记一条故障摘要。永远先写本地；只有双条件满足才排队待发。"""
    ev = build_event(kind, step=step, code=code, egress=egress,
                     result=result, ctx=ctx)
    base = Path(out_dir) if out_dir else events_dir()
    try:
        _append_jsonl(base / "events.jsonl", ev)
    except Exception:                                        # noqa: BLE001
        pass                                                  # 记录失败绝不影响主流程
    if report_enabled(ctx):
        try:
            _append_jsonl(base / "pending.jsonl", ev)
        except Exception:                                    # noqa: BLE001
            pass
        flush_async(ctx, out_dir=base)
    return ev


# --------------------------------------------------------------------------
#  可插拔上报通道（默认关闭）
# --------------------------------------------------------------------------

def flush(ctx=None, *, out_dir=None, transport=None) -> tuple[int, str]:
    """发送待发队列。返回 (发送条数, 状态)。

    ★ 未启用时**直接返回**，绝不创建 Request、绝不碰 socket —— 门禁会用一个
      会抛异常的假 transport 来断言这一点。
    """
    if not report_enabled(ctx):
        return 0, "disabled"
    base = Path(out_dir) if out_dir else events_dir()
    pending = base / "pending.jsonl"
    if not pending.exists():
        return 0, "empty"
    try:
        lines = pending.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, "read_error"
    lines = [ln for ln in lines if ln.strip()][:_MAX_BATCH]
    if not lines:
        return 0, "empty"

    body = json.dumps({"events": [json.loads(ln) for ln in lines]},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint(ctx), data=body,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "TrafficCatcher-Diagnostics/1.0"},
        method="POST")
    sender = transport or urllib.request.urlopen
    try:
        resp = sender(req, _FLUSH_TIMEOUT)
        try:
            resp.read(1)
        finally:
            try:
                resp.close()
            except Exception:                                # noqa: BLE001
                pass
    except Exception as exc:                                 # noqa: BLE001
        return 0, f"send_error:{type(exc).__name__}"

    # 只删已成功发送的那几行，剩下的留到下次（失败重试不做风暴）。
    try:
        rest = pending.read_text(encoding="utf-8").splitlines()[len(lines):]
        if rest:
            pending.write_text("\n".join(rest) + "\n", encoding="utf-8")
        else:
            pending.unlink()
    except OSError:
        pass
    return len(lines), "ok"


def flush_async(ctx=None, *, out_dir=None) -> None:
    """后台发送；进程退出时不等它，绝不拖慢 UI 或关机。"""
    def _run():
        with _flush_lock:
            try:
                flush(ctx, out_dir=out_dir)
            except Exception:                                # noqa: BLE001
                pass
    threading.Thread(target=_run, name="diag-flush", daemon=True).start()


# --------------------------------------------------------------------------
#  统一运行体检结论
# --------------------------------------------------------------------------

def _license_health() -> tuple[str, str, str]:
    """返回 (授权状态, 凭证有无, 出口模式默认值)。不输出任何身份信息。"""
    state, token_state = "未知", "未知"
    try:
        st = licensing.status() if licensing else {}
        state = str(st.get("state") or "unknown")
        # 只报「有没有」，绝不报值
        token_state = "已配置" if st.get("embedded_token") else "未配置"
    except Exception:                                        # noqa: BLE001
        pass
    return state, token_state, "unknown"


def _network_health(probe: bool) -> tuple[str, str]:
    """返回 (出口模式, 是否实测)。不输出代理地址与端口。"""
    if not probe or tiktok_live is None:
        try:
            cfg = tiktok_live.quick_proxy() if tiktok_live else ""
        except Exception:                                    # noqa: BLE001
            cfg = ""
        return ("proxy-configured" if cfg else "direct-configured"), "未实测"
    try:
        _proxy, mode = tiktok_live.detect_egress(timeout=2.0, budget=5.0)
        return str(mode or "none"), "已实测"
    except Exception:                                        # noqa: BLE001
        return "unknown", "实测失败"


def health_report(ctx=None, *, probe_network: bool = False) -> str:
    """一段人读的体检结论，可安全复制发作者。

    ★ 与 environment_report 的区别：这里**没有**机器码、客户名、计算机名、
      本地路径、Token、日志路径。所以既能贴进失败弹窗，也能给客户直接发。
    """
    state, token_state, _ = _license_health()
    egress, probed = _network_health(probe_network)

    try:
        log_ok = os.access(str(logger.log_dir()), os.W_OK)
    except Exception:                                        # noqa: BLE001
        log_ok = False
    try:
        import shutil
        ffmpeg_ok = bool(shutil.which("ffmpeg")) or bool(
            _setting(ctx, "ffmpeg_path", ""))
    except Exception:                                        # noqa: BLE001
        ffmpeg_ok = False

    ver = str(getattr(branding, "APP_VERSION", "?") or "?")
    edition = "免费版" if getattr(branding, "APP_IS_FREE", False) else "完整版"
    run_mode = "打包运行" if getattr(sys, "frozen", False) else "源码运行"
    channel = "已开启" if report_enabled(ctx) else "未开启（仅本地记录）"

    return "\n".join([
        "流量捕手 · 运行体检结论",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"软件版本：v{ver}　档位：{edition}　运行方式：{run_mode}",
        "",
        "── 授权与凭证 ──",
        f"授权状态：{state}",
        f"采集凭证：{token_state}（只显示有无，不显示内容）",
        "",
        "── 网络与运行环境 ──",
        f"出口模式：{egress}　网络探测：{probed}",
        f"ffmpeg：{'已找到' if ffmpeg_ok else '未找到'}",
        f"日志目录可写：{'是' if log_ok else '否'}",
        "",
        "── 故障摘要 ──",
        f"匿名上报：{channel}",
        "",
        "本结论不含机器码、客户名、账号、视频链接、文案与本地路径，可安全复制发送。",
        "如需完整运行日志，请点「一键导出日志」；机器码在「设置 → 授权与激活」复制。",
    ])


def quick_health_block(ctx=None) -> str:
    """失败弹窗里追加的最短结论（不联网、毫秒级，绝不拖慢弹窗）。"""
    try:
        return "\n\n" + "─" * 46 + "\n" + health_report(ctx, probe_network=False)
    except Exception:                                        # noqa: BLE001
        return ""
