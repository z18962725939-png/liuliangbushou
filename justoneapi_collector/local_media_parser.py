"""本机 media-parser 解析引擎客户端（可选通道，完全免费）。

★ 链路边界（不许混）：
  - 本模块只做「分享链接 → 无水印直链 / 图集」的本地解析，不碰 okdata 额度，
    不读 okd_ Key，不碰 JustOneAPI 采集，不改 media_parser.py 的 zacao 地址。
  - 上游开源项目 ucmao/media-parser（MIT），本机自部署、API_ONLY=true 免鉴权。
  - 服务没部署 / 没启动 / 解析失败时，调用方必须原样回退到既有链路。

协议依据：https://github.com/ucmao/media-parser/blob/main/docs/api.md
  POST /api/v1/parse  {"text": "分享文案或链接"} → retcode/succ/data/error_code
  GET  /api/health    → {"status":"ok"}
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

# 服务默认地址（本机回环，不联网、不花额度）
DEFAULT_BASE_URL = "http://127.0.0.1:8051"
DEFAULT_PORT = 8051
HEALTH_PATH = "/api/health"
PARSE_PATH = "/api/v1/parse"

# 部署来源：留本地快照，开源项目公告 2027-01-01 前会移除大部分解析源码
SOURCE_ZIP = "https://github.com/ucmao/media-parser/archive/refs/heads/main.zip"
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_SERVICE_DIR = "media-parser"          # 默认落在软件工程同级目录

# ★ 客户机免安装：安装包自带 33MB 便携包（Python 3.10.11 + 全部依赖 + 源码快照）。
#   客户不需要装 Python / 不需要联网 / 2027 上游删源码也不受影响。
#   该 zip 由 PyInstaller datas 带进 _internal/local_parser_portable.zip。
BUNDLED_ZIP_NAME = "local_parser_portable.zip"
# ★ V8 引擎（py_mini_racer）加载快照时**不认中文路径**，客户机用户名常见的
#   C:\Users\张三\... 会直接让 Python 进程崩溃。部署根目录强制纯 ASCII。
ASCII_ROOT_NAME = "TrafficCatcherMediaParser"

# 业务错误码 → 人话（用户看不懂英文码，也不该看到 traceback）
_ERROR_HINT = {
    "XIAOHONGSHU_COOKIE_REQUIRED": "小红书触发风控：请在设置页配置小红书 Cookie，或稍后重试",
    "PINDUODUO_COOKIE_REQUIRED": "拼多多需要登录 Cookie：请在设置页配置后重试",
    "WECHAT_CHANNELS_COOKIE_REQUIRED": "视频号需要元宝 Cookie：请在设置页配置后重试",
    "WECHAT_MP_VERIFY_REQUIRED": "公众号触发安全验证，请稍后重试或换条网络",
    "MEDIA_DELETED_OR_PRIVATE": "作品已删除或设为仅自己可见，无法解析",
    "MEDIA_UNDER_REVIEW": "作品刚发布还在审核中，请几分钟后重试",
    "ITEM_NOT_FOUND": "商品已下架或删除，无法解析",
    "NO_MEDIA_IN_CONTENT": "这段分享只有文字，没有图片或视频",
    "MEDIA_NOT_FOUND": "没有从这条内容里提取到可下载的资源",
    "PLATFORM_NOT_SUPPORTED": "本机解析引擎暂不支持这个平台",
    "INVALID_TEXT": "请先粘贴链接或包含链接的分享文案",
    "TEXT_TOO_LONG": "分享文案太长（上限 2048 字），请精简后重试",
    "URL_NOT_FOUND": "没有从文案里找到有效的 http(s) 链接",
    "REDIRECT_FAILED": "短链跳转失败，可能链接已失效或网络不通",
    "RATE_LIMITED": "本机解析引擎正忙，请稍后重试",
    "PLATFORM_RATE_LIMITED": "该平台请求过于频繁，请稍后重试",
    "API_DISABLED": "本机解析引擎已暂停",
    "PLATFORM_DISABLED": "该平台解析暂时维护中",
    "INTERNAL_ERROR": "解析引擎内部出错，请稍后重试",
}

_URL_RE = re.compile(r"https?://[^\s，。；;\"'<>）)]+", re.I)


# ------------------------------------------------------------------ 配置
def _settings_file() -> Path:
    """与 AppContext.save_settings() 写的是同一个 config.json。"""
    try:
        from . import branding
        return Path(branding.app_data_dir()) / "config.json"
    except Exception:                                       # noqa: BLE001
        return Path(os.environ.get("APPDATA", ".")) / "TrafficCatcher" / "config.json"


def load_config() -> dict[str, Any]:
    """读配置；缺键一律给安全默认值（关 / 本机地址 / 空 Key）。"""
    data: dict[str, Any] = {}
    try:
        raw = _settings_file().read_text(encoding="utf-8")
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:                                       # noqa: BLE001
        pass
    base = str(data.get("local_parser_base_url") or DEFAULT_BASE_URL).strip().rstrip("/")
    return {
        "enabled": bool(data.get("local_parser_enabled", False)),
        "base_url": base or DEFAULT_BASE_URL,
        "api_key": str(data.get("local_parser_api_key") or "").strip(),
        "service_dir": str(data.get("local_parser_dir") or "").strip(),
    }


def save_config(*, enabled: bool | None = None, base_url: str | None = None,
                api_key: str | None = None, service_dir: str | None = None
                ) -> tuple[bool, str]:
    """只改本通道的四个键，其它设置原样保留。"""
    try:
        path = _settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                data = loaded
        if enabled is not None:
            data["local_parser_enabled"] = bool(enabled)
        if base_url is not None:
            data["local_parser_base_url"] = str(base_url).strip().rstrip("/")
        if api_key is not None:
            data["local_parser_api_key"] = str(api_key).strip()
        if service_dir is not None:
            data["local_parser_dir"] = str(service_dir).strip()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, "已保存"
    except Exception as e:                                  # noqa: BLE001
        return False, f"保存失败：{type(e).__name__}"


def is_enabled() -> bool:
    return load_config()["enabled"]


# ------------------------------------------------------------------ HTTP
def _opener():
    """本机回环必须直连：绕开系统代理，否则 Clash 会把 127.0.0.1 也代理掉。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(base_url: str = "", timeout: float = 2.5) -> tuple[bool, str]:
    """探活。返回 (可用, 人话)。不抛异常。"""
    base = (base_url or load_config()["base_url"]).strip().rstrip("/")
    if not base.startswith("http://127.0.0.1") and not base.startswith("http://localhost"):
        return False, "本机解析引擎只接受 127.0.0.1 / localhost 地址"
    try:
        with _opener().open(base + HEALTH_PATH, timeout=timeout) as r:
            body = r.read(512).decode("utf-8", "replace")
        if r.status != 200:
            return False, f"服务返回 HTTP {r.status}"
        try:
            ok = json.loads(body).get("status") == "ok"
        except Exception:                                   # noqa: BLE001
            ok = "ok" in body
        return (True, "服务正常") if ok else (False, "服务返回异常，请重启服务")
    except Exception as e:                                  # noqa: BLE001
        cls = type(e).__name__
        if "Timeout" in cls or "URLError" in cls:
            return False, "本机解析引擎未启动（连不上服务地址）"
        return False, f"探活失败（{cls}）"


def extract_first_url(text: str) -> str:
    m = _URL_RE.search(str(text or ""))
    return m.group(0).rstrip(".,!?") if m else ""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """上游字段 → 软件内部字段（video_downloader._media_to_video_info 的契约）。"""
    if not isinstance(data, dict):
        return {}
    author = data.get("author")
    author_name = ""
    if isinstance(author, dict):
        author_name = str(author.get("nickname") or author.get("author_id") or "").strip()
    else:
        author_name = str(author or "").strip()

    images: list[str] = []
    for item in (data.get("image_list") or []):
        if isinstance(item, str) and item.strip():
            images.append(item.strip())
        elif isinstance(item, dict):
            u = str(item.get("url") or "").strip()
            if u:
                images.append(u)

    videos: list[tuple[str, str]] = []
    cover = str(data.get("cover_url") or "").strip()
    for item in (data.get("video_list") or []):
        if isinstance(item, str) and item.strip():
            videos.append((item.strip(), cover))
        elif isinstance(item, dict):
            u = str(item.get("url") or "").strip()
            if u:
                videos.append((u, str(item.get("cover") or cover).strip()))

    return {
        "platform": str(data.get("platform") or "").strip(),
        "video_id": str(data.get("video_id") or "").strip(),
        "title": str(data.get("title") or "").strip(),
        "desc": str(data.get("desc") or "").strip(),
        "author": author_name,
        "cover_url": cover,
        "video_url": str(data.get("video_url") or "").strip(),
        "images": images,
        "videos": videos,
        # 以下软件暂不用，但保留给后续字幕 / 音频功能
        "audio_url": str(data.get("audio_url") or "").strip(),
        "subtitles": data.get("subtitles") or [],
    }


def parse(text: str, base_url: str = "", api_key: str = "", timeout: float = 45.0
          ) -> tuple[dict[str, Any] | None, str]:
    """解析一条分享链接。失败返回 (None, 中文原因)，绝不抛异常。

    ★ 业务错误码优先于 retdesc：上游的 retdesc 可能是平台原文（英文），
      给用户看的必须是本地化过的人话。
    """
    url = extract_first_url(text)
    if not url:
        return None, "没有可解析的内容（请粘贴链接或包含链接的分享文案）"
    cfg = load_config()
    base = (base_url or cfg["base_url"]).strip().rstrip("/")
    key = (api_key if api_key is not None else cfg["api_key"]).strip()

    payload = json.dumps({"text": url}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Accept": "application/json"}
    if key:                       # 仅自建 SaaS 模式需要；默认 API_ONLY 免鉴权
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + PARSE_PATH, data=payload,
                                 headers=headers, method="POST")
    try:
        with _opener().open(req, timeout=timeout) as r:
            status = r.status
            raw = r.read()
    except urllib.error.HTTPError as e:
        return None, f"本机解析引擎返回 HTTP {e.code}（请检查服务状态）"
    except Exception as e:                                  # noqa: BLE001
        cls = type(e).__name__
        if "Timeout" in cls:
            return None, "本机解析引擎响应超时，请稍后重试"
        return None, f"连不上本机解析引擎（{cls}）——可在设置页点「启动服务」"

    if status == 204:
        return None, "本机解析引擎返回空结果(204)，这条内容可能不可用"
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except Exception:                                       # noqa: BLE001
        return None, f"本机解析引擎返回了非 JSON 响应（HTTP {status}）"
    if not isinstance(body, dict):
        return None, "本机解析引擎返回结构异常"

    code = str(body.get("error_code") or "").strip()
    if code:
        return None, _ERROR_HINT.get(code, str(body.get("retdesc") or "解析失败"))
    if not body.get("succ", True) or int(body.get("retcode") or 0) != 200:
        return None, str(body.get("retdesc") or "解析失败") or "解析失败"

    normalized = normalize(body.get("data"))
    if not normalized.get("video_url") and not normalized.get("images"):
        return None, "解析引擎返回了结果，但没有可用的下载地址"
    return normalized, ""


# ------------------------------------------------------------------ 部署 / 进程
def _is_ascii_path(p: Path) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def bundled_zip() -> Path:
    """安装包自带的便携包路径（源码态 = assets/，打包态 = _MEIPASS/）。"""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            roots.append(Path(meipass))
        roots.append(Path(sys.argv[0]).resolve().parent / "_internal")
    here = Path(__file__).resolve()
    roots.append(here.parent.parent / "assets")
    roots.append(Path.cwd() / "assets")
    for root in roots:
        candidate = root / BUNDLED_ZIP_NAME
        if candidate.is_file():
            return candidate
    return Path()                       # 没有自带包 → 走老的在线部署


def service_dir() -> Path:
    configured = load_config()["service_dir"]
    if configured:
        path = Path(configured).expanduser()
        # 老配置若落在中文路径，自动迁到纯英文根，避免 V8 崩溃
        if _is_ascii_path(path):
            return path
    # ★ 默认部署到系统盘的纯英文目录（客户中文用户名也不受影响）
    if sys.platform == "win32":
        for env_key in ("LOCALAPPDATA", "ProgramData", "SystemDrive"):
            env_val = os.environ.get(env_key, "")
            if not env_val:
                continue
            if env_key == "SystemDrive":
                root = Path(env_val.rstrip("\\/") + "\\")
            else:
                root = Path(env_val)
            if _is_ascii_path(root):
                return root / ASCII_ROOT_NAME / DEFAULT_SERVICE_DIR
    try:
        base = Path(sys.argv[0]).resolve().parent
    except Exception:                                       # noqa: BLE001
        base = Path.cwd()
    return base / DEFAULT_SERVICE_DIR


def bundled_python(root: Path) -> Path:
    """便携包自带 Python（优先）→ 回退到 venv（旧部署）。"""
    portable = root / "python.exe"
    if portable.is_file():
        return portable
    venv = root / ".venv" / "Scripts" / "python.exe"
    if sys.platform != "win32":
        venv = root / ".venv" / "bin" / "python"
    return venv


def _find_python() -> str:
    for name in ("python.exe", "python"):
        p = shutil.which(name)
        if p and Path(p).resolve() != Path(sys.executable).resolve():
            return p
    return sys.executable if sys.executable.lower().endswith(".exe") else ""


def ensure_deployed(target_dir: str = "", progress: Any = None) -> tuple[bool, str]:
    """下载开源快照 + 建 venv + 装依赖 + 打 Windows 兼容补丁。

    ★ 只在本机落盘，不写软件自身代码，不联网回传任何数据。
      progress 是可选回调 progress(0~100, 文案)，供 Worker 上屏。
    """
    def step(pct: int, msg: str) -> None:
        if callable(progress):
            try:
                progress(pct, msg)
            except Exception:                               # noqa: BLE001
                pass

    root = Path(target_dir).expanduser() if target_dir else service_dir()
    app_file = root / "app.py"
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    if sys.platform != "win32":
        venv_py = root / ".venv" / "bin" / "python"

    try:
        if not app_file.exists():
            bundled = bundled_zip()
            if bundled:
                # ★ 首选：软件自带离线包，直接解压（不联网、不需 Python、不受 2027 停更影响）
                step(5, "正在安装自带解析引擎（离线）…")
                root.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(bundled) as zf:
                    zf.extractall(root)
                step(30, "正在校验…")
                if not app_file.exists():
                    return False, "自带解析包不完整，请重新安装软件"
                # 便携包里的 bat 为开发机路径写死，这里按实际部署路径重写
                bat = root / "启动本机解析引擎.bat"
                if bat.exists():
                    bat.write_text(
                        "@echo off\r\ncd /d %~dp0app\r\nset API_ONLY=true\r\n"
                        "set WEB_PORT=8051\r\n%~dp0python.exe app.py\r\npause\r\n",
                        encoding="gbk")
            else:
                step(5, "正在下载开源快照…")
                root.parent.mkdir(parents=True, exist_ok=True)
                zip_path = root.parent / ".media-parser-main.zip"
                with _opener().open(SOURCE_ZIP, timeout=90) as r, open(zip_path, "wb") as f:
                    shutil.copyfileobj(r, f)
                step(25, "正在解压…")
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(root.parent)
                extracted = root.parent / "media-parser-main"
                if extracted.exists():
                    if root.exists():
                        shutil.rmtree(root)
                    extracted.rename(root)
                zip_path.unlink(missing_ok=True)
                if not app_file.exists():
                    return False, "下载完成但没找到 app.py，请检查部署目录"

        # 自带包已有完整环境，无需 venv / pip
        if bundled_python(root).is_file():
            step(100, "部署完成（自带运行环境，无需 Python）")
            save_config(service_dir=str(root))
            return True, f"部署完成：{root}"

        step(35, "正在准备 Python 环境…")
        if not venv_py.exists():
            py = _find_python()
            if not py:
                return False, "没找到可用的 Python 3.10+，请先安装 Python 并勾选 Add to PATH"
            subprocess.run([py, "-m", "venv", str(root / ".venv")],
                           check=True, capture_output=True, timeout=180)

        step(50, "正在安装依赖（国内镜像）…")
        cmd = [str(venv_py), "-m", "pip", "install", "--disable-pip-version-check",
               "-i", PIP_INDEX, "-r", str(root / "requirements.txt"), "tzdata"]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        step(85, "正在打 Windows 兼容补丁…")

        src = app_file.read_text(encoding="utf-8")
        if "import fcntl\n" in src and "except ImportError:" not in src.split("def ")[0]:
            src = src.replace(
                "import fcntl\n",
                "try:\n    import fcntl\nexcept ImportError:\n    fcntl = None\n", 1)
            src = src.replace(
                "            fcntl.flock(secret_file.fileno(), fcntl.LOCK_EX)\n",
                "            if fcntl is not None:\n"
                "                fcntl.flock(secret_file.fileno(), fcntl.LOCK_EX)\n", 1)
            app_file.write_text(src, encoding="utf-8")
        step(100, "部署完成")
        save_config(service_dir=str(root))
        return True, f"部署完成：{root}"
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", "replace")[-300:]
        return False, f"依赖安装失败：{tail or '请检查网络'}"
    except Exception as e:                                  # noqa: BLE001
        return False, f"部署失败（{type(e).__name__}）：{str(e)[:200]}"


def start_service(base_url: str = "", target_dir: str = "") -> tuple[bool, str]:
    """在后台拉起服务。只启动自己部署的那份，不碰其它进程。"""
    ok, _ = health(base_url, timeout=1.5)
    if ok:
        return True, "服务已经在运行"
    root = Path(target_dir).expanduser() if target_dir else service_dir()
    app_file = root / "app.py"
    if not app_file.exists():
        return False, "还没部署本机解析引擎，请先点「一键部署」"
    venv_py = bundled_python(root)
    if not venv_py.is_file():
        return False, "运行环境缺失，请重新点「一键部署」"
    if not _is_ascii_path(root):
        return False, "部署目录含中文，解析引擎会崩溃；请点「一键部署」自动迁移到英文目录"

    port = str(DEFAULT_PORT)
    try:
        parsed = urllib.parse.urlparse(base_url or load_config()["base_url"])
        if parsed.port:
            port = str(parsed.port)
    except Exception:                                       # noqa: BLE001
        pass

    env = dict(os.environ)
    env["API_ONLY"] = "true"          # 免鉴权微服务模式，不产生任何积分费用
    env["WEB_PORT"] = port
    # ★ 必须真正脱离父进程：否则软件 / 命令行一退出，服务就被系统回收，
    #   下次进来又变成「未启动」。三个标志合用（Windows），Linux 下用 start_new_session。
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        kwargs: dict[str, Any] = {
            "cwd": str(root), "env": env, "creationflags": flags,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if sys.platform != "win32":
            kwargs.pop("creationflags")
            kwargs["start_new_session"] = True
        proc = subprocess.Popen([str(venv_py), str(app_file)], **kwargs)
    except Exception as e:                                  # noqa: BLE001
        return False, f"启动失败（{type(e).__name__}）"
    for _ in range(20):                                     # 最多等 10 秒
        time.sleep(0.5)
        ok, _msg = health(base_url, timeout=1.0)
        if ok:
            save_config(base_url=(base_url or load_config()["base_url"]), service_dir=str(root))
            return True, f"服务已启动（进程 {proc.pid}）"
        if proc.poll() is not None:
            return False, f"服务启动后立即退出（代码 {proc.returncode}），请检查部署目录"
    return False, "服务启动超时，请稍后再点一次「检查服务」"


def stop_service(target_hint: str = "") -> tuple[bool, str]:
    """只停止本机 8051 端口上监听的 Python 服务，不误杀其它进程。"""
    port = str(DEFAULT_PORT)
    cfg = load_config()
    try:
        parsed = urllib.parse.urlparse(cfg["base_url"] or cfg.get("base_url", ""))
        if parsed.port:
            port = str(parsed.port)
    except Exception:                                       # noqa: BLE001
        pass
    if sys.platform != "win32":
        return False, "当前系统请手动停止服务"
    try:
        # 中文 Windows 的 netstat 输出是 GBK，别用 text=True（默认 utf-8 会炸）
        raw = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, timeout=15).stdout
        out = raw.decode("gbk", "replace")
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
        if not pids:
            return True, "服务没有在运行"
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=15)
        return True, f"已停止服务（进程 {', '.join(sorted(pids))}）"
    except Exception as e:                                  # noqa: BLE001
        return False, f"停止失败（{type(e).__name__}）"
