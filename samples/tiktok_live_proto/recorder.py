"""ffmpeg 录制进程封装：开录、优雅收尾、异常退出回收。

和现有抖音链路的差别：命令里多了 -http_proxy、-headers（UA + Referer），
并且刻意不加 -nostdin，这样停止时可以给 ffmpeg 发一个 q，让它把 mp4 尾巴
写完整，而不是暴力 kill 出一个打不开的文件。
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import ProtoConfig
from .core import build_ffmpeg_cmd, new_record_path


class RecorderError(RuntimeError):
    """录制启动失败。"""


class Recorder:
    """同一时刻只允许一段录制；调用方负责串行化。"""

    def __init__(self, cfg: ProtoConfig,
                 command_builder: Callable[..., list[str]] | None = None):
        self.cfg = cfg
        self.command_builder = command_builder or build_ffmpeg_cmd
        self.proc: subprocess.Popen | None = None
        self.out_path: Path | None = None
        self.stream_url = ""
        self.started_at: datetime | None = None
        self.last_error = ""
        self._log_fh = None

    @property
    def is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    @property
    def current_file(self) -> str:
        return str(self.out_path or "")

    def start(self, stream_url: str, handle: str, seq: int = 1) -> Path:
        if self.is_running:
            raise RecorderError("上一段录制还没结束")
        if not stream_url:
            raise RecorderError("没有可用的流地址")
        ffmpeg = self.cfg.ffmpeg
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise RecorderError("没找到 ffmpeg，请在设置里指定 ffmpeg.exe 路径")

        out = new_record_path(handle, self.cfg.save_dir, seq)
        cmd = self.command_builder(ffmpeg=ffmpeg, stream_url=stream_url,
                                   out_path=out, proxy=self.cfg.proxy,
                                   user_agent=self.cfg.user_agent,
                                   duration=self.cfg.record_duration)
        self._log_fh = open(out.with_suffix(".log"), "w", encoding="utf-8",
                            errors="replace")
        self._log_fh.write("命令：" + " ".join(cmd[:6]) + " …\n")
        self._log_fh.flush()
        env = os.environ.copy()
        if self.cfg.proxy:                              # 双保险：部分 ffmpeg 构建读环境变量
            env["http_proxy"] = self.cfg.proxy
            env["https_proxy"] = self.cfg.proxy
            env["HTTP_PROXY"] = self.cfg.proxy
            env["HTTPS_PROXY"] = self.cfg.proxy
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL,
                                         stderr=self._log_fh,
                                         creationflags=creationflags, env=env)
        except Exception as exc:                        # noqa: BLE001
            self._close_log()
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise RecorderError(f"ffmpeg 启动失败：{self.last_error}") from exc
        self.out_path = out
        self.stream_url = stream_url
        self.started_at = datetime.now()
        self.last_error = ""
        return out

    def _close_log(self) -> None:
        try:
            if self._log_fh:
                self._log_fh.close()
        except Exception:                               # noqa: BLE001
            pass
        self._log_fh = None

    def poll(self) -> int | None:
        """录制自己结束（时长到 / 流断）时回收资源并返回退出码。"""
        if not self.proc:
            return None
        rc = self.proc.poll()
        if rc is not None:
            self._close_log()
        return rc

    def stop(self, timeout: float = 8.0) -> int | None:
        """优雅停止：先发 q，超时再 terminate，最后 kill。返回退出码。"""
        if not self.proc:
            return None
        if self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write(b"q\n")
                    self.proc.stdin.flush()
            except Exception:                           # noqa: BLE001
                pass
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=5)
                except Exception:                       # noqa: BLE001
                    try:
                        self.proc.kill()
                        self.proc.wait(timeout=5)
                    except Exception:                   # noqa: BLE001
                        pass
        rc = self.proc.poll()
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:                               # noqa: BLE001
            pass
        self._close_log()
        self.proc = None                                # 允许下一段重新开录
        return rc

    def reset(self) -> None:
        """录制进程自己结束后清掉句柄，保留 out_path 供结果摘要使用。"""
        self._close_log()
        self.proc = None

    def result(self) -> dict:
        """一段录制的结果摘要，给界面/日志用。"""
        size = self.out_path.stat().st_size if self.out_path and self.out_path.exists() else 0
        return {
            "file": str(self.out_path or ""),
            "size": size,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S")
                          if self.started_at else "",
            "seconds": round((datetime.now() - self.started_at).total_seconds(), 1)
                       if self.started_at else 0,
        }
