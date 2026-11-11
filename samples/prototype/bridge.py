"""网页 ↔ Python 桥接层（pywebview 的 js_api）。

这一层就是建议里「业务内核」的边界：网页只负责画，真实数据、任务状态、
设计令牌全部由 Python 给。原型只读真实数据 + 模拟任务进度推送，
不联网、不调用任何付费接口、不启动采集、不写任何下载文件。

两条页面共用本类：
  · 工作台：get_snapshot / get_tasks / start_demo_task
  · 视频下载：get_download_contract / get_output_stats / get_download_rows /
              start_batch_download / pause_batch_download / resume_batch_download

★ 视频下载页的进度不是随便 range(0,101,5)：它照抄
  gui_qt/pages/video_download_page.py 的真实口径（契约里抽出来的），
  完成一条记满 1.0、未完成按字节比例加分、全程只增不减。
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import tokens  # noqa: E402  （由 design_tokens.json 生成，与网页同源）
import transcribe_status as ts  # noqa: E402  （只读状态判定，规则校验自 transcribe.py）

CONTRACT_FILE = HERE / "download_contract.json"
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".avi", ".m4v", ".mp3"}


def _app_version() -> str:
    source = ROOT / "justoneapi_collector" / "branding.py"
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "未读到"
    matched = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)', text)
    return matched.group(1) if matched else "未读到"


def _contract() -> dict:
    """契约由 extract_contract.py 从 Qt 源码抽取，网页与 Python 读同一份。"""
    return json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))


def _fmt_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} GB"


def _output_stats() -> dict:
    """只读统计本机真实下载目录（config.json 顶层，不递归、不写、不打开文件）。"""
    cfg = Path(os.environ.get("APPDATA", str(Path.home()))) / "TrafficCatcher" / "config.json"
    result = {
        "configured": False, "dir": "", "exists": False, "files": 0,
        "videos": 0, "bytes": 0, "size_text": "0 B", "capped": False,
        "source": "%APPDATA%/TrafficCatcher/config.json",
    }
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        folder = str(data.get("dl_save_dir") or "").strip()
    except (OSError, ValueError, AttributeError):
        return result
    if not folder:
        return result
    result["configured"] = True
    result["dir"] = folder
    path = Path(folder)
    if not path.is_dir():
        return result
    result["exists"] = True
    total = videos = files = 0
    # 顶层只读、封顶 500 项：避免原型误扫客户的大目录。
    # ★ 触顶必须标记 capped，界面显示「≥」，否则会把封顶值当成真实总数。
    limit = 500
    for i, item in enumerate(path.iterdir()):
        if i >= limit:
            result["capped"] = True
            break
        try:
            if not item.is_file():
                continue
            size = item.stat().st_size
        except OSError:
            continue
        files += 1
        total += size
        if item.suffix.lower() in VIDEO_EXT:
            videos += 1
    result.update(files=files, videos=videos, bytes=total, size_text=_fmt_size(total))
    return result


def _rows() -> list:
    """前 3 行来自 Qt 源码 _load_demo_rows，后 2 行标明「原型补充」。"""
    contract = _contract()
    labels = {p["key"]: p["label"] for p in contract["platforms"]}
    sizes = [8_600_000, 12_400_000, 20_100_000, 9_800_000, 15_300_000]
    rows = []
    for i, raw in enumerate(contract["demo_rows"]):
        rows.append(_row(i, raw["platform"], labels, raw["title"],
                         raw["duration"], sizes[i], "源码演示行"))
    extra = [
        ("weixin_channels", "门店探店 30 秒实拍", "00:30"),
        ("douyin", "同行爆款拆解 · 第 2 批", "01:05"),
    ]
    for i, (key, title, duration) in enumerate(extra, start=3):
        rows.append(_row(i, key, labels, title, duration, sizes[i], "原型补充"))
    return rows


def _row(i: int, key: str, labels: dict, title: str,
         duration: str, size: int, origin: str) -> dict:
    return {
        "id": f"r{i + 1}", "platform": key,
        "platform_label": labels.get(key, key),
        "title": title, "duration": duration,
        "size": size, "size_text": _fmt_size(size),
        "origin": origin, "state": "排队中", "percent": 0,
        "bytes": 0, "reason": "",
    }


class Bridge:
    """暴露给 JS 的方法都会自动挂到 window.pywebview.api 上。"""

    def __init__(self) -> None:
        # ★ 必须以下划线开头：pywebview 会递归扫描 js_api 的公开属性来生成
        #   JS 代理；若把 Window 存成公开属性，它会一路扫到 WebView2 的 COM
        #   对象，在非 UI 线程爆出 "CoreWebView2 can only be accessed from
        #   the UI thread" 一大串无害但吓人的异常日志。
        self._window = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._batch_stop = threading.Event()
        self._batch_pause = threading.Event()
        self._batch_thread: threading.Thread | None = None
        self._batch_rows: list[dict] = []
        self._tc_stop = threading.Event()
        self._tc_thread: threading.Thread | None = None

    def bind(self, window) -> None:
        self._window = window

    # ── 工作台页：JS → Python ────────────────────────────────────
    def get_snapshot(self) -> dict:
        return {
            "version": _app_version(),
            "qt_pages": len(list((ROOT / "gui_qt" / "pages").glob("*.py"))),
            "site_pages": len(list((ROOT / "installer_output" / "standalone").glob("*.html"))),
            "brand_light": tokens.color("--brand", "light"),
            "brand_dark": tokens.color("--brand", "dark"),
            "token_version": tokens.TOKENS["version"],
            "time": datetime.now().strftime("%H:%M:%S"),
            "source": "只读真实数据：branding.py / gui_qt/pages / 官网产物 / design_tokens.json",
        }

    def get_tasks(self) -> list:
        return [
            {"name": "抖音 · 冬季护肤赛道 50 条视频", "percent": 68, "state": "下载中"},
            {"name": "文案仿写 · 口播稿 12 篇", "percent": 75, "state": "处理中"},
            {"name": "短剧成片 · 第 3 集合成", "percent": 31, "state": "渲染中"},
            {"name": "直播间录制 · 晚间场", "percent": 0, "state": "排队中"},
        ]

    def get_tokens(self, theme: str = "light") -> dict:
        return dict(tokens.TOKENS.get(theme, tokens.TOKENS["light"]))

    # ── 工作台页：Python → JS 进度推送 ────────────────────────────
    def start_demo_task(self, name: str = "演示任务") -> dict:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "已有任务在跑"}
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(name,), daemon=True)
        self._thread.start()
        return {"started": True, "name": name}

    def stop_demo_task(self) -> dict:
        self._stop.set()
        return {"stopped": True}

    # ── 视频下载页：JS → Python，全是只读真实数据 ──────────────────
    def get_download_contract(self) -> dict:
        """列名 / 平台中文名 / 媒体类型 / 统计卡 / 进度口径，来自 Qt 源码。"""
        return _contract()

    def get_output_stats(self) -> dict:
        """本机 config.json 里的下载目录与顶层文件统计（只读）。"""
        return _output_stats()

    def get_download_rows(self) -> list:
        return _rows()

    # ── 视频下载页：批量任务（模拟字节流，绝不落盘、绝不联网） ──────
    def start_batch_download(self, media_type: str = "video") -> dict:
        if self._batch_thread and self._batch_thread.is_alive():
            return {"started": False, "reason": "已有批次在跑"}
        self._batch_stop.clear()
        self._batch_pause.clear()
        self._batch_rows = _rows()
        self._batch_thread = threading.Thread(
            target=self._run_batch, args=(str(media_type),), daemon=True)
        self._batch_thread.start()
        return {"started": True, "media_type": media_type, "total": len(self._batch_rows)}

    def pause_batch_download(self) -> dict:
        self._batch_pause.set()
        return {"paused": True}

    def resume_batch_download(self) -> dict:
        self._batch_pause.clear()
        return {"resumed": True}

    def stop_batch_download(self) -> dict:
        self._batch_stop.set()
        return {"stopped": True}

    def _push(self, script: str) -> None:
        if self._window is None:
            return
        try:
            self._window.evaluate_js(script)
        except Exception:
            pass  # 窗口已关闭时静默跳过

    def _emit(self, hook: str, payload: dict) -> None:
        blob = json.dumps(payload, ensure_ascii=False)
        self._push(f"window.{hook} && window.{hook}({blob})")

    # ── 转录任务可视化页：全部只读，状态判据来自 transcribe.py ──────
    def get_transcribe_contract(self) -> dict:
        """状态枚举、表格列名、输出命名规则（源码校验过，不是手写）。"""
        return ts.contract()

    def get_transcribe_overview(self, real: bool = True, out_dir: str = "") -> dict:
        """本机目录扫描 + 显卡实时状态 + 当前转录进程，一次给齐。"""
        return ts.overview(real=bool(real), out_dir=str(out_dir or ""))

    def get_demo_transcribe_rows(self) -> list:
        """明确标注演示数据的四态样例（不冒充真实文件）。"""
        return ts.demo_rows()

    def get_gpu_status(self) -> dict:
        return ts.gpu_status()

    def start_demo_transcribe(self) -> dict:
        """模拟一次转录的阶段推进，用于演示「任务即时状况」卡片。

        只推数字、不写文件、不联网、不启动 whisper-cli；界面必须标注演示。
        """
        if self._tc_thread and self._tc_thread.is_alive():
            return {"started": False, "reason": "已有演示任务在跑"}
        self._tc_stop.clear()
        self._tc_thread = threading.Thread(target=self._run_transcribe, daemon=True)
        self._tc_thread.start()
        return {"started": True, "demo": True}

    def stop_demo_transcribe(self) -> dict:
        self._tc_stop.set()
        return {"stopped": True}

    # ── 线程体 ──────────────────────────────────────────────────
    def _run_transcribe(self) -> None:
        gpu = ts.gpu_status()
        t0 = time.time()
        files = "访谈_第 03 期.m4a"
        for percent in range(0, 101, 2):
            if self._tc_stop.is_set():
                self._emit("onTranscribeDone", {
                    "percent": percent, "phase": "已停止", "file": files,
                    "elapsed": round(time.time() - t0, 1), "demo": True,
                    "message": "演示任务已停止",
                })
                return
            phase = ("提取音频" if percent < 15 else
                     "显卡转录" if percent < 92 else "写出文本")
            self._emit("onTranscribeProgress", {
                "percent": percent, "phase": phase, "file": files,
                "elapsed": round(time.time() - t0, 1), "demo": True,
                "gpu": gpu,
            })
            time.sleep(0.07)
        self._emit("onTranscribeDone", {
            "percent": 100, "phase": "已完成", "file": files,
            "elapsed": round(time.time() - t0, 1), "demo": True,
            "message": "转写完成：成功 1 条，失败 0 条",
        })

    def _run(self, name: str) -> None:
        for percent in range(0, 101, 5):
            if self._stop.is_set():
                self._emit("onTaskDone", {"name": name, "percent": percent, "state": "已停止"})
                return
            self._emit("onProgress", {
                "name": name, "percent": percent,
                "state": "已完成" if percent == 100 else "运行中",
            })
            time.sleep(0.12)
        self._emit("onTaskDone", {"name": name, "percent": 100, "state": "已完成"})

    def _run_batch(self, media_type: str) -> None:
        """按真实口径推进度：pct = (done + Σ未完成字节比例) / 总条数。

        与 Qt 源码一致的三个细节：
          ① 完成一条就记满 1.0（失败条也计入 done，避免进度回跳）；
          ② 未完成条目按 d/t 加分，且上限 0.999（写盘校验还没完）；
          ③ 每 0.12 秒才向界面推一次。
        这里只模拟字节数字，不写文件、不发请求。
        """
        contract = _contract()
        rule = contract["progress"]
        cap = rule["partial_cap"]
        throttle = rule["throttle_seconds"]
        rows = self._batch_rows
        total = len(rows)
        done = failed = 0
        frac: dict[str, float] = {}
        d_sum = 0
        t_sum = sum(r["size"] for r in rows)
        last_emit = 0.0

        def snapshot(state: str, message: str) -> dict:
            pct = min(100.0, (done + sum(frac.values())) * 100.0 / total) if total else 100.0
            return {
                "percent": round(pct, 1), "done": done, "failed": failed,
                "total": total, "d_sum": d_sum, "t_sum": t_sum,
                "size_text": f"{_fmt_size(d_sum)} / {_fmt_size(t_sum)}",
                "state": state, "message": message, "media_type": media_type,
                "rows": [{k: r[k] for k in ("id", "state", "percent", "bytes", "reason")}
                         for r in rows],
            }

        self._emit("onBatchStart", snapshot("下载中", f"批次启动 · 共 {total} 条"))
        for row in rows:
            if self._batch_stop.is_set():
                self._emit("onBatchDone", snapshot("已停止", "批次已停止"))
                return
            row["state"] = "下载中"
            self._emit("onDownloadProgress", snapshot(
                "下载中", f"开始下载：{row['title'][:18]}"))
            steps = 8
            for step in range(1, steps + 1):
                while self._batch_pause.is_set() and not self._batch_stop.is_set():
                    time.sleep(0.05)
                if self._batch_stop.is_set():
                    self._emit("onBatchDone", snapshot("已停止", "批次已停止"))
                    return
                chunk = int(row["size"] * step / steps)
                # 第 4 条（视频号）在 60% 处模拟一次失败：验证失败态与统计卡
                if row["id"] == "r4" and step >= 5:
                    row["bytes"] = chunk
                    d_sum += chunk - int(row["size"] * 4 / steps)
                    frac.pop(row["id"], None)
                    row["state"] = "失败"
                    row["reason"] = "模拟网络中断（原型演示，不是真实下载）"
                    row["percent"] = 0
                    done += 1          # ★ 失败也记满 1.0，进度绝不回跳
                    failed += 1
                    break
                delta = chunk - row["bytes"]
                d_sum += delta
                row["bytes"] = chunk
                frac[row["id"]] = min(cap, chunk / row["size"])
                row["percent"] = round(min(cap, chunk / row["size"]) * 100, 1)
                now = time.time()
                if now - last_emit >= throttle:
                    last_emit = now
                    self._emit("onDownloadProgress", snapshot(
                        "下载中", f"已完成 {done}/{total} 条 · {_fmt_size(d_sum)} / {_fmt_size(t_sum)}"))
                time.sleep(0.055)
            if row["state"] == "下载中":
                frac.pop(row["id"], None)
                row["state"] = "已完成"
                row["percent"] = 100
                row["bytes"] = row["size"]
                done += 1
            last_emit = 0.0
            self._emit("onDownloadProgress", snapshot(
                "下载中", f"已完成 {done}/{total} 条：{row['title'][:18]}"))

        text = contract["done_text"].replace("{ok}", str(total - failed)).replace(
            "{fail}", str(failed)).split("——")[0].strip()
        self._emit("onBatchDone", snapshot("已完成", text))


if __name__ == "__main__":
    # 不开窗口也能验契约与数据：python bridge.py
    print("契约版本", _contract()["version"], "列名", _contract()["columns"])
    print("任务行", len(_rows()), "条，输出目录", _output_stats()["dir"] or "未配置")
