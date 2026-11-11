"""短剧剪辑组件包：按需下载、解压、校验、定位。

★ 为什么做成「按需下载」而不是随安装包分发
---------------------------------------------
剪辑组件含 ML 运行时与内置 ffmpeg，实测 450MB 量级（服务 252MB + ffmpeg 194MB）。
直接塞进安装包，主程序体积会从 44MB 涨到十几倍 —— 而绝大多数客户装流量捕手
是为了采集和下载，一辈子用不到短剧剪辑。让不需要的人背这个体积是说不过去的。
所以主包保持原样，客户第一次打开短剧成片页时才下载。

★ 断点续传怎么落
-----------------
不是"重新下一次"，而是按块记账：
  · 每 4MB 一块，下完的块记进 .parts.json；
  · 中途关掉软件/断网，下次打开从没下完的块继续，已下的不动；
  · 全部到齐后按块顺序合并成 .zip，再校验总字节数。
实测单线程拉 HF 源只有 ~4KB/s（145MB 要 10 小时），分块并发能跑满带宽 ——
所以并发不是优化，是能不能用的问题。

★ 为什么不边下边解压
---------------------
zip 的中心目录在文件尾部，流式解压要先拿到尾部的中央目录才能定位。
分块并发下完再整体解压，比流式简单且可校验。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 组件包在应用数据目录下的子目录名
BUNDLE_DIR_NAME = "drama_clip"
# 解压后服务 exe 的相对路径（PyInstaller onedir 结构）
SERVICE_EXE_REL = "dramaclip-service/dramaclip-service.exe"
# 随包分发的只读资源根
RESOURCES_REL = "resources"
# 服务的可写数据根（模型/缓存/产出/日志/BGM 都落这里）
DATA_REL = "data"
# ★ 内置 ASR 模型（faster-whisper-base）的落点。
#   【这条嵌套是踩出来的，不是随手写的，改一格就全废】
#   whisper 系引擎在 registry.detect_status 里走**专属分支**，不认扁平目录：
#   它只认 HF 缓存布局
#       models/asr/faster-whisper/
#         models--Systran--faster-whisper-base/
#           refs/main                ← 内容就是快照目录名（这里是 main）
#           snapshots/main/{config.json,model.bin,tokenizer.json,vocabulary.txt}
#   直接把四件套摊在 models/asr/faster-whisper/ 下，引擎的通用分支（*.bin 递归）
#   对 whisper 不生效 —— 结果就是文件一个不少、models.list 仍报 not_installed，
#   分析一到转写就失败。refs/main 缺失也能跑（按目录名倒序兜底），但写上能让
#   「体检报的修订」与「引擎真加载的修订」是同一份。
#   这四件套直接打进组件包，客户不联网也能转写 —— 让他们现下的话，
#   HF 官方直连不通、hf-mirror 只有 4KB/s（145MB 要 10 小时），等于功能不存在。
ASR_REL = ("data/models/asr/faster-whisper/"
           "models--Systran--faster-whisper-base/snapshots/main")
ASR_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")

BLOCK = 4 << 20          # 4MB 一块
DEFAULT_THREADS = 8
_UA = "TrafficCatcher/1.0"


class BundleError(RuntimeError):
    """组件包相关的统一错误类型。"""


def _appdata_root() -> Path:
    """跟流量捕手主程序共用数据根目录（卸载时一起清掉）。"""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "TrafficCatcher"


def bundle_dir() -> Path:
    return _appdata_root() / BUNDLE_DIR_NAME


def zip_path() -> Path:
    return bundle_dir() / "drama_clip.zip"


def parts_path() -> Path:
    return bundle_dir() / "drama_clip.parts.json"


def service_exe() -> Path:
    return bundle_dir() / SERVICE_EXE_REL


def resources_dir() -> Path:
    return bundle_dir() / RESOURCES_REL


def data_dir() -> Path:
    return bundle_dir() / DATA_REL


def asr_model_dir() -> Path:
    return bundle_dir() / ASR_REL


def asr_ready() -> bool:
    """语音模型四件套是否齐全。"""
    d = asr_model_dir()
    return all((d / name).is_file() for name in ASR_FILES)


def is_installed() -> bool:
    """组件是否可用。

    三样缺一不可：exe（起不来）、resources（缺 ffmpeg 扫描就失败）、
    ASR 模型（缺了能扫集但转写不了，等于分析走到一半断掉）。
    """
    return (service_exe().is_file() and resources_dir().is_dir()
            and asr_ready())


def _head(url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", _UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return int(resp.headers.get("Content-Length") or 0), resp.geturl()


def _fetch_block(url: str, idx: int, start: int, end: int, dest: Path) -> int:
    req = urllib.request.Request(url)
    req.add_header("Range", f"bytes={start}-{end}")
    req.add_header("User-Agent", _UA)
    got = 0
    with urllib.request.urlopen(req, timeout=180) as resp:
        if resp.status == 200:
            raise BundleError("服务端忽略了分段请求，无法分块下载")
        tmp = dest.with_suffix(f".part{idx}")
        with open(tmp, "wb") as f:
            while True:
                buf = resp.read(1 << 18)
                if not buf:
                    break
                f.write(buf)
                got += len(buf)
    return got


def download(
    url: str,
    dest: Path,
    *,
    threads: int = DEFAULT_THREADS,
    on_progress: Callable[[float, str], None] | None = None,
) -> None:
    """分块并发下载，支持断点续传。"""
    total, _real = _head(url)
    if total <= 0:
        raise BundleError("拿不到组件包大小")

    dest.parent.mkdir(parents=True, exist_ok=True)
    blocks = [(i, s, min(s + BLOCK - 1, total - 1))
              for i, s in enumerate(range(0, total, BLOCK))]

    # 读记账本：下过的块直接跳过
    done: set[int] = set()
    if parts_path().is_file():
        try:
            meta = json.loads(parts_path().read_text(encoding="utf-8"))
            if meta.get("url") == url and meta.get("size") == total:
                for i, _s, _e in blocks:
                    f = dest.with_suffix(f".part{i}")
                    if meta.get("done") and i in meta["done"] and f.is_file():
                        done.add(i)
        except (json.JSONDecodeError, OSError):
            done = set()

    pending = [b for b in blocks if b[0] not in done]
    finished = len(done)
    total_blocks = len(blocks)
    lock = threading.Lock()
    t0 = time.time()

    def _save_meta(done_set: set[int]) -> None:
        try:
            parts_path().write_text(
                json.dumps({"url": url, "size": total,
                            "done": sorted(done_set)}),
                encoding="utf-8")
        except OSError:
            pass

    try:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            futs = {
                pool.submit(_fetch_block, url, i, s, e, dest): (i, s, e)
                for i, s, e in pending
            }
            for fut in as_completed(futs):
                i, s, e = futs[fut]
                fut.result()
                with lock:
                    done.add(i)
                    finished = len(done)
                    _save_meta(done)
                if on_progress is not None:
                    pct = finished / total_blocks * 100.0
                    dt = time.time() - t0
                    speed = (finished * BLOCK) / 1048576 / dt if dt > 0 else 0
                    on_progress(pct, f"{finished}/{total_blocks} 块　{speed:.1f} MB/s")
    except Exception as exc:
        raise BundleError(f"下载中断：{type(exc).__name__}: {exc}") from exc

    # 合并
    if on_progress is not None:
        on_progress(100.0, "正在合并…")
    with open(zip_path(), "wb") as out:
        for i, _s, _e in blocks:
            part = dest.with_suffix(f".part{i}")
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out, 1 << 20)
            part.unlink()
    if zip_path().stat().st_size != total:
        raise BundleError(
            f"合并后大小不符（应 {total}，实 {zip_path().stat().st_size}）")
    try:
        parts_path().unlink()
    except OSError:
        pass


def install(
    *,
    on_progress: Callable[[float, str], None] | None = None,
) -> None:
    """解压组件包到数据目录。"""
    zp = zip_path()
    if not zp.is_file():
        raise BundleError("组件包不存在，请先下载")
    if on_progress is not None:
        on_progress(0.0, "正在解压…")
    target = bundle_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zp) as zf:
            members = zf.namelist()
            for n, name in enumerate(members, start=1):
                zf.extract(name, target)
                if on_progress is not None and n % 20 == 0:
                    on_progress(n / len(members) * 100.0,
                                f"解压 {n}/{len(members)}")
    except zipfile.BadZipFile as exc:
        raise BundleError(f"组件包损坏（{exc}），请删除后重新下载") from exc
    if on_progress is not None:
        on_progress(100.0, "解压完成")


def clear_partials() -> None:
    """清掉半成品（换源或组件包升级时用）。"""
    if parts_path().is_file():
        try:
            parts_path().unlink()
        except OSError:
            pass
    for p in bundle_dir().glob("drama_clip.part*"):
        try:
            p.unlink()
        except OSError:
            pass


def uninstall() -> None:
    """整包移除（客户想省磁盘时用）。"""
    d = bundle_dir()
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
