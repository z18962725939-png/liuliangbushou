# -*- coding: utf-8 -*-
"""
自动更新模块：检查新版本 → 展示更新说明 → 下载安装包 → 校验 → 静默安装重启。

═══════════════════════════════════════════════════════════════════════════
你要做的事（发布新版本时）
═══════════════════════════════════════════════════════════════════════════
  1. 改 justoneapi_collector/branding.py 里的 APP_VERSION（比如 1.0.0 → 1.1.0）
  2. 重新打包 + 用 Inno Setup 编译出新安装包
  3. 把安装包 + version.json 上传到你的更新源
  4. 客户下次启动程序就会收到提示

【version.json 放哪】三种都行，填在设置页「软件更新」里：

  A. 自己的服务器 / 对象存储（推荐，最可控）
     https://your-site.com/liuliangbushou/version.json

  B. GitHub Release（免费；直接用 API 地址）
     https://api.github.com/repos/你的用户名/仓库名/releases/latest
     ⚠️ GitHub API 未登录时每小时限 60 次（按 IP），小规模够用；
        客户多了建议换成 A 或 Gitee。

  C. Gitee Release（国内快）
     https://gitee.com/api/v5/repos/你的用户名/仓库名/releases/latest

【version.json 长这样】
  {
    "version": "1.1.0",
    "published": "2026-10-10",
    "notes": "1. 新增直播录制指示灯\\n2. 修复录制时长不刷新",
    "mandatory": false,
    "win": {
      "url": "https://your-site.com/流量捕手_Setup_v1.1.0.exe",
      "size": 29360128,
      "sha256": "安装包的 sha256（可留空，留空则跳过校验）"
    }
  }
  （也兼容扁平写法：version / url / sha256 / size 直接放在最外层）

═══════════════════════════════════════════════════════════════════════════
安全/健壮性设计
═══════════════════════════════════════════════════════════════════════════
  · 下载复用 transcribe 的 8 线程分块下载器（实测比单线程快 13 倍）；
  · 拿到安装包先校验 SHA256，不匹配直接删除并报错，绝不执行半个文件；
  · 安装用 Inno Setup 的 /SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS，
    静默升级、自动关闭旧程序、装完自动拉起新版本；
  · 用户随时可以「跳过此版本」，跳过的版本号记在设置里，不再骚扰。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

from . import branding

USER_AGENT = f"{branding.APP_NAME_EN}/{branding.APP_VERSION} (Windows)"


class UpdateInfo:
    """一次检查的结果。available=False 时 error/latest 里有原因。"""

    def __init__(self) -> None:
        self.available = False
        self.version = ""
        self.latest = ""
        self.notes = ""
        self.published = ""
        self.url = ""
        self.size = 0
        self.sha256 = ""
        self.mandatory = False
        self.min_version = ""
        self.error = ""
        self.source = ""

    def summary(self) -> str:
        return f"{self.latest} → {self.version}" if self.available else (self.error or "已是最新版本")

    def notes_text(self) -> str:
        return (self.notes or "（本次更新没有提供说明）").strip()

    def size_text(self) -> str:
        try:
            from .transcribe import human_size
            return human_size(self.size) if self.size else "未知大小"
        except Exception:                                  # noqa: BLE001
            return f"{self.size / 1048576:.1f} MB" if self.size else "未知大小"


# ----------------------------------------------------------------------
#  清单解析
# ----------------------------------------------------------------------

def _pick(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def parse_manifest(data: dict | str, source: str = "") -> UpdateInfo:
    """把 version.json / GitHub Release JSON 解析成 UpdateInfo。"""
    info = UpdateInfo()
    info.source = source
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception as e:                             # noqa: BLE001
            info.error = f"更新清单不是合法 JSON：{e}"
            return info
    if not isinstance(data, dict):
        info.error = "更新清单格式不对（顶层不是对象）"
        return info

    # GitHub / Gitee Release：有 tag_name / assets 字段
    is_release_api = ("tag_name" in data) or ("assets" in data)
    if is_release_api:
        info.version = str(_pick(data, "tag_name", "tag_name_name", default="")).lstrip("vV")
        info.notes = str(_pick(data, "body", "description", default=""))
        info.published = str(_pick(data, "published_at", "created_at", default=""))[:10]
        info.min_version = str(_pick(data, "min_version", default="")).lstrip("vV")
        info.mandatory = bool(_pick(data, "mandatory", default=False))
        best = None
        for asset in (data.get("assets") or []):
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if not name.lower().endswith((".exe", ".msi", ".zip")):
                continue
            # 优先带 Setup / 安装 字样的资产
            score = 2 if ("setup" in name.lower() or "安装" in name) else 1
            if best is None or score > best[0]:
                best = (score, asset)
        if best:
            asset = best[1]
            info.url = str(_pick(asset, "browser_download_url", "download_url", default=""))
            info.size = int(_pick(asset, "size", default=0) or 0)
        info.latest = branding.APP_VERSION
        if not info.url:
            info.error = "该 Release 里没找到 .exe 安装包附件"
            return info
        if not info.version:
            info.error = "该 Release 没有版本号（tag_name）"
            return info
    else:
        # 我们自己的 version.json
        info.version = str(_pick(data, "version", "latest", default="")).lstrip("vV")
        info.notes = str(_pick(data, "notes", "changelog", "body", default=""))
        info.published = str(_pick(data, "published", "date", default=""))
        info.mandatory = bool(_pick(data, "mandatory", "force", default=False))
        info.min_version = str(_pick(data, "min_version", default="")).lstrip("vV")
        info.latest = branding.APP_VERSION
        win = data.get("win") or data.get("windows") or data
        if isinstance(win, dict) and win is not data:
            info.url = str(_pick(win, "url", "download", default=""))
            info.size = int(_pick(win, "size", default=0) or 0)
            info.sha256 = str(_pick(win, "sha256", "hash", default=""))
        if not info.url:
            info.url = str(_pick(data, "url", "download", default=""))
        if not info.size:
            info.size = int(_pick(data, "size", default=0) or 0)
        if not info.sha256:
            info.sha256 = str(_pick(data, "sha256", "hash", default=""))
        if not info.version:
            info.error = "version.json 里没有 version 字段"
            return info
        if not info.url:
            info.error = "version.json 里没有安装包下载地址（win.url）"
            return info

    info.available = branding.is_newer(info.version, branding.APP_VERSION)  # type: ignore[attr-defined]
    # `latest` 这里放"当前版本"，方便界面显示 "1.0.0 → 1.1.0"
    info.latest = branding.APP_VERSION
    return info


# ----------------------------------------------------------------------
#  联网检查
# ----------------------------------------------------------------------

def _http_get(url: str, timeout: float = 20.0) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*",
        "Cache-Control": "no-cache",
    })
    try:
        env = None
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy:
            env = {"https": proxy, "http": proxy}
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(env) if env else urllib.request.ProxyHandler({})
        ) if env is not None else urllib.request.build_opener()
        with opener.open(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "更新地址不存在（404）。请确认 version.json 已上传、地址没写错。"
        if e.code == 403:
            return False, ("被拒绝了（403）。若用的是 GitHub API，多半是每小时 60 次的"
                           "未登录限额用完了，稍后再试或换成自己的服务器地址。")
        return False, f"服务器返回 {e.code}"
    except urllib.error.URLError as e:
        return False, f"连不上更新地址：{e.reason}（检查网络/代理，或地址写错了）"
    except Exception as e:                                 # noqa: BLE001
        return False, f"检查更新失败：{e}"


def check_for_update(feed_url: str = "", timeout: float = 20.0) -> UpdateInfo:
    """检查是否有新版本。任何异常都收敛成 info.error，不抛给界面。"""
    url = (feed_url or branding.UPDATE_FEED_URL or "").strip()
    if not url:
        info = UpdateInfo()
        info.error = "尚未配置更新地址（设置页「软件更新」里填写）"
        info.latest = branding.APP_VERSION
        return info
    ok, text = _http_get(url, timeout=timeout)
    if not ok:
        info = UpdateInfo()
        info.error = text
        info.latest = branding.APP_VERSION
        return info
    info = parse_manifest(text, source=url)
    info.latest = branding.APP_VERSION
    return info


# ----------------------------------------------------------------------
#  下载 + 校验 + 安装
# ----------------------------------------------------------------------

def update_cache_dir() -> Path:
    """安装包临时目录（放用户目录，避免程序目录只读）。"""
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
    d = Path(local) / branding.DATA_DIR_NAME / "updates"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path(os.environ.get("TEMP", str(Path.home()))) / f"{branding.DATA_DIR_NAME}_updates"
        d.mkdir(parents=True, exist_ok=True)
    return d


def guess_filename(info: UpdateInfo) -> str:
    name = ""
    try:
        from urllib.parse import urlparse, unquote
        name = Path(unquote(urlparse(info.url).path)).name
    except Exception:                                      # noqa: BLE001
        name = ""
    if not name or "." not in name:
        name = f"{branding.SETUP_NAME}_v{info.version}.exe"
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    if not name.lower().endswith((".exe", ".msi")):
        name += ".exe"
    return name


def sha256_of(path: Path, progress=None) -> str:
    h = hashlib.sha256()
    total = 0
    try:
        total = path.stat().st_size
    except OSError:
        total = 0
    done = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                try:
                    progress(done, total)
                except Exception:                          # noqa: BLE001
                    pass
    return h.hexdigest()


def _encode_url(url: str) -> str:
    """把含中文 / 空格 / 特殊字符的 URL 路径做 percent-encode。

    下载直链里常带中文文件名（如 流量捕手_Setup_v1.0.0.exe），不编码的话
    部分服务器 / CDN（含 GitHub Releases）会直接 404。这里只编码 path 与
    query，scheme / netloc 以及已有的分隔符（/ & =）都保留，避免把正常
    URL 也改坏。
    """
    if not url:
        return url
    parts = urlsplit(url)
    path = quote(parts.path, safe="/")
    query = quote(parts.query, safe="=&") if parts.query else ""
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def download_update(info: UpdateInfo, progress=None, log=None,
                    dest_dir: Path | None = None) -> tuple[bool, str]:
    """下载安装包到本地并校验。成功返回 (True, 本地路径)。"""
    if not info.available and not info.url:
        return False, info.error or "没有可下载的更新"
    safe_url = _encode_url(info.url)   # 中文文件名等做 percent-encode，避免 CDN 404
    dest = (dest_dir or update_cache_dir()) / guess_filename(info)

    def say(text: str):
        if log:
            try:
                log(text)
            except Exception:                              # noqa: BLE001
                pass

    # 已经下好且校验通过 → 直接复用
    if dest.exists() and (not info.sha256 or
                          sha256_of(dest).lower() == info.sha256.lower()):
        say(f"已存在完整安装包，跳过下载：{dest.name}")
        return True, str(dest)

    say(f"开始下载：{info.url}")
    try:
        from .transcribe import Downloader
        dl = Downloader(threads=8)
    except Exception:                                      # noqa: BLE001
        dl = None

    if dl is not None:
        ok, err = dl.download([safe_url], dest, total_hint=info.size,
                              progress=progress)
    else:
        ok, err = _simple_download(safe_url, dest, progress)
    if not ok:
        return False, f"下载失败：{err}"
    say(f"下载完成：{dest.name}")

    if info.sha256:
        say("正在校验安装包完整性（SHA256）…")
        got = sha256_of(dest, progress=None)
        if got.lower() != info.sha256.strip().lower():
            try:
                dest.unlink()
            except OSError:
                pass
            return False, ("安装包校验不通过（SHA256 不匹配），已删除下载文件。\n"
                           f"　期望：{info.sha256[:16]}…\n　实际：{got[:16]}…")
        say("校验通过 ✔")
    else:
        say("提示：更新清单里没提供 sha256，已跳过完整性校验。")
    return True, str(dest)


def _simple_download(url: str, dest: Path, progress=None) -> tuple[bool, str]:
    """下载器的退化实现（万一 transcribe 模块不可用时兜底）。"""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    try:
                        progress(done, total)
                    except Exception:                      # noqa: BLE001
                        pass
        return True, ""
    except Exception as e:                                 # noqa: BLE001
        return False, str(e)


def launch_installer(path: str | Path, silent: bool = True,
                     restart: bool = False) -> tuple[bool, str]:
    """运行安装包。silent=True 用 Inno Setup 的静默升级参数。"""
    p = Path(path)
    if not p.is_file():
        return False, f"找不到安装包：{p}"
    args = [str(p)]
    if silent:
        args += ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                 "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
        if restart:
            args.append("/RESTARTAPPS")
    try:
        subprocess.Popen(args, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return True, ""
    except Exception as e:                                 # noqa: BLE001
        return False, f"启动安装程序失败：{e}\n（可直接双击这个文件手动安装：{p}）"


def find_local_setup(folder: str | Path | None = None) -> list[Path]:
    """在目录里找可用的安装包（给"手动导入更新包"用）。"""
    d = Path(folder) if folder else update_cache_dir()
    if not d.is_dir():
        return []
    out = [p for p in d.glob("*.exe") if p.is_file()]
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def open_containing_folder(path: str | Path) -> None:
    """在资源管理器里定位该文件（下载完让用户能立刻找到）。"""
    try:
        p = Path(path)
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception:                                      # noqa: BLE001
        pass


def should_prompt(state: dict, version: str, interval_days: float = 1.0) -> bool:
    """是否需要弹更新提示：跳过过该版本 / 刚刚才提醒过 就不再打扰。"""
    if not version:
        return False
    if str(state.get("update_skip_version") or "") == version:
        return False
    last = float(state.get("update_last_prompt") or 0)
    if last and (time.time() - last) < interval_days * 86400:
        return False
    return True


def mark_prompted(state: dict) -> None:
    state["update_last_prompt"] = time.time()


def mark_skipped(state: dict, version: str) -> None:
    state["update_skip_version"] = version
    state["update_last_prompt"] = time.time()


def clear_skipped(state: dict) -> None:
    """清除「跳过该版本」记录，让它重新可以弹提示。

    注意：必须连 `update_last_prompt` 一起清掉，否则刚跳过时写下的时间戳
    会让 should_prompt 继续走「刚提醒过」的节流分支，用户点了「清除」却没反应。
    """
    state.pop("update_skip_version", None)
    state.pop("update_last_prompt", None)


def version_file_template() -> str:
    """给用户参考的 version.json 模板（设置页可一键复制）。"""
    return json.dumps({
        "version": "1.1.0",
        "published": time.strftime("%Y-%m-%d"),
        "notes": "1. 新增：xxx\n2. 修复：yyy",
        "mandatory": False,
        "min_version": branding.APP_VERSION,
        "win": {
            "url": "https://你的服务器/下载地址/流量捕手_Setup_v1.1.0.exe",
            "size": 0,
            "sha256": "",
        },
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":                                  # pragma: no cover
    print("当前版本：", branding.APP_VERSION)
    print("更新地址：", branding.UPDATE_FEED_URL or "（未配置）")
    _info = check_for_update()
    print("检查结果：", _info.summary())
    if _info.available:
        print("更新说明：", _info.notes_text())
        print("下载地址：", _info.url)
