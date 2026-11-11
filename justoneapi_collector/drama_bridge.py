"""DramaClip 剪辑服务桥接：进程托管 + JSON-RPC 2.0 客户端。

★ 连接方向是反的（2026-10-25 实测所得，别凭直觉改回去）
----------------------------------------------------------
Python 服务是**出站连接方**：它 socket.create_connection 主动连过来。
我们才是监听方。所以顺序必须是：先 listen 拿到端口 → 再 spawn 服务。
顺序反了服务连不上，且不报任何错，只表现为"一直连不上"。

★ 协议五条（全部来自源码实证，不是文档抄的）
---------------------------------------------
1. 服务连入后首条是 {"type":"hello", token, service_version, protocol_version}
2. 主进程必须回 {"type":"hello-ack"}，**10 秒内不回服务端主动断开**（protocol/schemas/common.json）
3. 之后是标准 JSON-RPC 2.0，NDJSON 分帧（一行一个 JSON，\\n 结尾，单行上限 16MB）
4. 服务端会主动推 progress.update / log.append —— 这些包**没有 id**，是通知不是响应
5. 长任务（分析/导出/下载模型/工具箱）一律异步：提交拿 job_id，轮询 jobs.get 到终态

★ 终态判定
-----------
job 状态：pending / running / done / failed / cancelled
只有这四个之外…… 不，只有后三个是终态。"任务已终态"的判定在服务端用
infra.jobs.is_terminal，客户端必须对齐，否则会一直轮询下去。

★ 线程模型
-----------
读线程常驻收包，收发都在主线程/调用方线程；回调可能来自读线程，
所以本模块对外抛的一切回调都包了 try/except —— 回调里炸了不能连累读线程
（Qt 槽抛异常是静默卡死，项目老伤）。
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ── 协议常量（与源码对齐，别自创）──
MAX_LINE_BYTES = 16 * 1024 * 1024
_READ_CHUNK = 65536
_CONNECT_TIMEOUT_S = 10.0
# ★ 成功态是 completed，不是 done —— 字面量照抄服务端 infra/jobs.py 的
#   _TERMINAL_STATUSES，一个字都不能自创。
#   这里写成 "done" 的后果：任务早就跑完了，is_terminal() 却永远返回 False，
#   页面就一直轮询到超时上限，客户看到的是「分析超时了（超过 2 小时）」，
#   而活儿其实 15 分钟前就干完了。第一次真跑到终态才撞出来。
_TERMINAL_JOB_STATES = ("completed", "failed", "cancelled")
_HELLO_TIMEOUT_S = 20.0

# 服务端在收到 disconnect 时会自己退出，所以正常关闭走 system.shutdown，
# 超时了才 kill —— 直接 kill 会让它来不及收尾（正在合成的段落没落完）。
_SHUTDOWN_GRACE_S = 6.0


class DramaBridgeError(RuntimeError):
    """桥接层对外统一的错误类型。"""


def _kill_tree(pid: int) -> None:
    """Windows 下按进程树强杀。

    Popen.kill() 只终止目标进程本身，它拉起来的子进程会变孤儿继续跑，
    于是「软件关了，后台还挂着一个 100MB+ 的剪辑服务」；
    更糟的是这些孤儿握着组件目录的文件句柄，重装组件包解压必撞 PermissionError。
    taskkill /F /T 一次把整棵树带走。进程已经没了是正常情况，不看返回值。
    """
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


# ★ 兜底：客户直接关窗口、或者哪条分支忘了 stop，进程不能留在后台。
#   WeakSet 不阻止实例被回收，解释器退出时把还活着的桥逐一收干净。
_LIVE: "weakref.WeakSet[DramaBridge]" = weakref.WeakSet()


@atexit.register
def _stop_all() -> None:
    for br in list(_LIVE):
        try:
            br.stop()
        except Exception:  # noqa: BLE001 - 收尾阶段抛出会连累别的清理
            pass


class LineAssembler:
    """字节流 → 完整行（跨块缓冲，跳过空行）。"""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer.extend(chunk)
        lines: list[str] = []
        while True:
            nl = self._buffer.find(b"\n")
            if nl < 0:
                break
            line = self._buffer[:nl].decode("utf-8", errors="replace")
            del self._buffer[: nl + 1]
            if line:
                lines.append(line)
        return lines


class DramaBridge:
    """一个 bridge 实例 = 一个被托管的服务进程 + 一条长连接。

    用法（同步侧）：
        br = DramaBridge(exe=..., resources_dir=..., data_dir=...)
        br.start()
        br.call("project.create", {"name": "xx", "source_path": "..."})
        br.stop()

    异步通知通过构造参数 on_progress / on_log 回调给上层。
    """

    def __init__(
        self,
        exe: str | Path | None = None,
        *,
        python_exe: str | Path | None = None,
        service_dir: str | Path | None = None,
        resources_dir: str | Path | None = None,
        data_dir: str | Path | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        on_log: Callable[[dict[str, Any]], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        # exe：打包好的服务可执行文件（组件包形态）
        # python_exe + service_dir：源码形态（本机开发/自测用），二选一
        self._exe = Path(exe) if exe else None
        self._python_exe = Path(python_exe) if python_exe else None
        self._service_dir = Path(service_dir) if service_dir else None
        self._resources_dir = Path(resources_dir) if resources_dir else None
        self._data_dir = Path(data_dir) if data_dir else None

        self._on_progress = on_progress
        self._on_log = on_log
        self._on_disconnect = on_disconnect

        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._write_lock = threading.Lock()
        self._closed = threading.Event()
        self._handshake_done = threading.Event()
        self._seq = 0
        self._seq_lock = threading.Lock()

        # 响应按 id 归档，调用方各取各的（并发调用互不串台）
        self._responses: dict[str, queue.Queue] = {}
        self._responses_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None

        # 登记进全局表：解释器退出时兜底收进程（见 _stop_all）
        _LIVE.add(self)

    # ── 生命周期 ──────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._sock is not None and not self._closed.is_set()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def start(self, timeout: float = _CONNECT_TIMEOUT_S) -> None:
        """监听 → spawn → 等连入 → 握手。任一步失败都要把已起的东西收干净。"""
        if self._exe is None and self._python_exe is None:
            raise DramaBridgeError("必须给 exe 或 python_exe 之一")

        # ① 先监听（随机端口），端口拿到手才能告诉服务往哪连
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
        except OSError as exc:
            srv.close()
            raise DramaBridgeError(f"监听环回端口失败：{exc}") from exc

        self._token = secrets.token_urlsafe(24)
        env = dict(os.environ)
        env["DRAMACLIP_SERVICE_ADDRESS"] = f"127.0.0.1:{port}"
        env["DRAMACLIP_AUTH_TOKEN"] = self._token
        env["HF_HUB_OFFLINE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        if self._data_dir:
            env["DRAMACLIP_DATA_DIR"] = str(self._data_dir)
        if self._resources_dir:
            env["DRAMACLIP_RESOURCES_DIR"] = str(self._resources_dir)

        try:
            self._spawn(env)
        except OSError as exc:
            srv.close()
            raise DramaBridgeError(f"启动服务进程失败：{exc}") from exc

        # ② 等服务连进来
        srv.settimeout(timeout)
        try:
            conn, _addr = srv.accept()
        except socket.timeout:
            srv.close()
            self._kill_proc()
            raise DramaBridgeError(
                f"{timeout:.0f} 秒内服务没有连入 —— 进程可能启动即失败"
            )
        finally:
            try:
                srv.close()
            except OSError:
                pass

        conn.settimeout(None)
        self._sock = conn
        self._closed.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, name="drama-ipc-reader", daemon=True
        )
        self._reader_thread.start()

        # ③ 等 hello，回 ack。10 秒是服务端硬约束，留到 20 秒是给慢机器余量
        hello = self._wait_hello(_HELLO_TIMEOUT_S)
        if hello is None:
            self.stop()
            raise DramaBridgeError("服务连入了但没给 hello 包 —— 版本或协议对不上")
        self._service_version = str(hello.get("service_version") or "")
        self._protocol_version = hello.get("protocol_version")
        self._send_raw({"type": "hello-ack"})
        self._handshake_done.set()

    def _spawn(self, env: dict[str, str]) -> None:
        cwd = None
        if self._exe is not None:
            argv: list[str] = [str(self._exe)]
        else:
            assert self._python_exe is not None
            argv = [str(self._python_exe), "-m", "dramaclip"]
            cwd = str(self._service_dir) if self._service_dir else None

        # 起进程时别让它弹黑框（客户看到会以为软件坏了）
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
        self._proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
        )

    def stop(self) -> None:
        """先礼貌请它自己退（让它收尾落盘），超时了才 kill。"""
        if self._sock is not None and not self._closed.is_set():
            try:
                # 不等回包：它收到 shutdown 就会走自己的收尾流程
                self._send_raw({
                    "jsonrpc": "2.0", "id": "shutdown",
                    "method": "system.shutdown", "params": {},
                })
            except (OSError, ValueError):
                pass
        deadline = time.time() + _SHUTDOWN_GRACE_S
        while self._proc is not None and time.time() < deadline:
            if self._proc.poll() is not None:
                break
            time.sleep(0.1)
        self._kill_proc()
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._closed.set()
        self._sock = None
        self._proc = None

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.kill()
                self._proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        # ★ Windows 的 kill() 只终止这一个进程，它拉起来的子进程会变孤儿。
        #   留着的后果很具体：子进程继续占着组件目录里的文件句柄，
        #   客户下次重装组件包解压时直接 PermissionError（_bz2.pyd 这类），
        #   软件看起来像坏了，其实只是上次没关干净。所以补一记进程树强杀。
        if self._proc is not None and self._proc.poll() is None:
            _kill_tree(self._proc.pid)
        self._proc = None

    # ── 收发 ──────────────────────────────────────────────────

    def _send_raw(self, payload: dict[str, Any]) -> None:
        if self._sock is None:
            raise DramaBridgeError("连接未建立")
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        if len(line) > MAX_LINE_BYTES:
            raise ValueError(f"出站消息超过 {MAX_LINE_BYTES} 字节上限")
        with self._write_lock:
            view = memoryview(line)
            while view:
                written = self._sock.send(view)
                if written <= 0:
                    raise OSError("套接字写入失败")
                view = view[written:]

    def _read_loop(self) -> None:
        assembler = LineAssembler()
        try:
            while True:
                chunk = self._sock.recv(_READ_CHUNK)
                if not chunk:
                    break
                for line in assembler.feed(chunk):
                    self._dispatch_line(line)
        except OSError:
            pass
        finally:
            self._closed.set()
            # 通知上层（回调可能来自 Qt 线程，一律防御）
            if self._on_disconnect is not None:
                try:
                    self._on_disconnect()
                except Exception:  # noqa: BLE001
                    pass

    def _dispatch_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return  # 坏行跳过；连接健康由调用方的超时判定
        if not isinstance(payload, dict):
            return

        # 握手包先扣下，不进响应队列
        if payload.get("type") == "hello":
            with self._responses_lock:
                q = self._responses.setdefault("__hello__", queue.Queue())
            q.put(payload)
            return

        rid = payload.get("id")
        if rid is None:
            self._handle_notification(payload)
            return
        with self._responses_lock:
            q = self._responses.get(str(rid))
        if q is not None:
            q.put(payload)

    def _handle_notification(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        params = payload.get("params") or {}
        target = self._on_progress if method == "progress.update" else (
            self._on_log if method == "log.append" else None
        )
        if target is None:
            return
        try:
            target(params)
        except Exception:  # noqa: BLE001
            # 回调里炸了不能连累读线程：读线程死了整个桥接就哑了
            pass

    def _next_id(self) -> str:
        with self._seq_lock:
            self._seq += 1
            return f"d{self._seq}"

    def _wait_hello(self, timeout: float) -> dict[str, Any] | None:
        with self._responses_lock:
            q = self._responses.setdefault("__hello__", queue.Queue())
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return None

    def call(
        self, method: str, params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """发一个 JSON-RPC 请求，等响应。**会阻塞**，别在 Qt 主线程直接调长任务。"""
        if self._sock is None or self._closed.is_set():
            raise DramaBridgeError("服务未连接")
        rid = self._next_id()
        q: queue.Queue = queue.Queue()
        with self._responses_lock:
            self._responses[rid] = q
        try:
            self._send_raw({
                "jsonrpc": "2.0", "id": rid, "method": method,
                "params": params or {},
            })
            try:
                payload = q.get(timeout=timeout)
            except queue.Empty as exc:
                raise DramaBridgeError(f"{method} 等待响应超时（{timeout:.0f}s）") from exc
        finally:
            with self._responses_lock:
                self._responses.pop(rid, None)

        if "error" in payload and payload.get("error"):
            err = payload["error"]
            raise DramaBridgeError(
                f"{method}：{err.get('message', '未知错误')}（代码 {err.get('code')}）"
            )
        return payload.get("result")

    def is_alive(self, timeout: float = 5.0) -> bool:
        """心跳探测。system.ping 是服务端快路径，不排队，适合做心跳。"""
        try:
            self.call("system.ping", {}, timeout=timeout)
            return True
        except (DramaBridgeError, OSError):
            return False

    # ── 长任务：提交 → 轮询到终态 ───────────────────────────────

    def submit_job(
        self, method: str, params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> str:
        result = self.call(method, params, timeout=timeout)
        job_id = (result or {}).get("job_id")
        if not job_id:
            raise DramaBridgeError(f"{method} 没有返回 job_id：{result}")
        return str(job_id)

    def job_state(self, job_id: str, timeout: float = 15.0) -> dict[str, Any]:
        result = self.call("jobs.get", {"job_id": job_id}, timeout=timeout)
        return (result or {}).get("job") or {}

    def cancel_job(self, job_id: str) -> bool:
        try:
            result = self.call("jobs.cancel", {"job_id": job_id}, timeout=10.0)
        except DramaBridgeError:
            return False
        return bool((result or {}).get("cancelling"))


def is_terminal(state: str | None) -> bool:
    """终态判定必须与服务端 infra.jobs.is_terminal 一致，否则会一直轮询。"""
    return state in _TERMINAL_JOB_STATES


# 组件包里只带了这一个语音模型，起服务后必须把档位钉成它
_ASR_MODEL = "base"


def apply_defaults(bridge: "DramaBridge") -> None:
    """起服务后把 ASR 档位钉成组件包里带的那一档。

    ★ 为什么必须主动钉一次
    ----------------------
    上游默认档位是 small（infra/config.py 的 DEFAULTS["asr.model"]）。
    组件包里带的是 base（145MB）——带 small 会让包从 414MB 涨到 900MB 量级，
    让每个客户都为用不上的精度背一倍流量是说不过去的。
    不钉住的话，客户一点「分析」就报「未找到本地 Whisper 模型（small）」，
    他看到的是"组件装了却用不了"，其实是默认档指向了一个包里没带的模型。

    写库+热更新一次即可（settings 是持久的），所以这里重复调用是幂等的。
    """
    try:
        bridge.call(
            "settings.update",
            {"values": {"asr.model": _ASR_MODEL}},
            timeout=20.0,
        )
    except Exception:  # noqa: BLE001 - 设置写不进去不该连累起服务
        pass


def find_active_job(bridge: "DramaBridge", kind: str) -> str | None:
    """找某类「已经在跑」的任务，拿到就跟着它，别再提交一次。

    ★ 为什么必须先看一眼
    --------------------
    扫描完剧集后服务会立刻自动起分析（服务端的 autostart_after_scan）。
    这时候客户再点一次「开始分析」，提交会撞 -32205「已有进行中的任务」——
    把这条报成失败，客户看到的就是「分析失败」，可实际上活儿正在跑。
    所以主动作前先找现成的：有就跟着它轮询，没有才提交新的。
    """
    try:
        res = bridge.call("jobs.list", {"limit": 50, "active_only": True},
                          timeout=20.0)
    except Exception:  # noqa: BLE001 - 查不到就当没有，回到提交路径
        return None
    for job in (res or {}).get("jobs") or []:
        if job.get("type") != kind:
            continue
        state = job.get("status")
        if state in _TERMINAL_JOB_STATES:
            continue
        jid = job.get("id")
        if jid:
            return str(jid)
    return None
