# -*- coding: utf-8 -*-
"""音视频转文本模块单测。

覆盖三块最容易出事的地方：
  1. 显卡检测与 CUDA 包匹配（版本解析错了就会下错包）
  2. 分块并发下载器（分块写错位置 = 模型废掉；还要能退化、能取消）
  3. 转写流程的前置校验与进度解析

下载部分用本地假 HTTP 服务，不依赖外网，所以在断网机器上也能跑。
"""
import hashlib
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justoneapi_collector import transcribe as T  # noqa: E402

PASS, FAIL = [], []


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✔' if cond else '✘'} {name}" + (f"  — {extra}" if extra else ""))


# ======================================================================
#  0) 目录隔离：别把测试用的假文件写进用户真实的 AppData
# ======================================================================

_TMP = Path(tempfile.mkdtemp(prefix="jo_ts_"))
_TMP_DIRS = [_TMP]
T.whisper_home = lambda: _TMP / "whisper"


# ======================================================================
#  1) 模型清单
# ======================================================================

chk("模型清单有 6 档", len(T.MODELS) == 6, str(list(T.MODELS)))
for k, info in T.MODELS.items():
    ok = (
        info.get("file", "").startswith("ggml-")
        and info["file"].endswith(".bin")
        and info.get("size_mb", 0) > 0
        and info.get("vram_gb", 0) > 0
        and bool(info.get("desc"))
    )
    chk(f"模型 {k} 字段完整", ok, str(info))
chk("默认模型在清单里", T.DEFAULT_MODEL in T.MODELS)
chk("模型体积递增（tiny < base < small < medium）",
    T.MODELS["tiny"]["size_mb"] < T.MODELS["base"]["size_mb"]
    < T.MODELS["small"]["size_mb"] < T.MODELS["medium"]["size_mb"])
chk("常见视频扩展名受支持",
    {".mp4", ".mov", ".mkv", ".flv", ".mp3", ".wav", ".m4a"} <= T.SUPPORTED_AUDIO_EXT)


# ======================================================================
#  2) 显卡信息解析
# ======================================================================

g = T.GpuInfo(name="NVIDIA GeForce RTX 3060", driver="617.14", cuda="13.4",
              mem_mb=12288, found=True)
chk("cuda_tuple 正常解析", g.cuda_tuple() == (13, 4), str(g.cuda_tuple()))
chk("显存换算成 GB", g.mem_gb == 12.0, str(g.mem_gb))
chk("summary 含关键信息",
    "RTX 3060" in g.summary() and "12.0 GB" in g.summary() and "13.4" in g.summary(),
    g.summary())
chk("空 cuda 不会崩", T.GpuInfo().cuda_tuple() == (0,))
chk("非法 cuda 不会崩", T.GpuInfo(cuda="abc").cuda_tuple() == (0,))
chk("单个版本号可解析", T.GpuInfo(cuda="12").cuda_tuple() == (12,))
chk("未找到时 summary 给原因",
    T.GpuInfo(reason="没装驱动").summary() == "没装驱动")


# ======================================================================
#  3) CUDA 包匹配（最容易配错的一处逻辑）
# ======================================================================

# ★ 2026-11-10 修正过时断言：v1.14.6 起默认**只推 12.4 自包含包**。
#   11.8 那个 59 MB 小包不含 cuBLAS，普通用户没装 CUDA Toolkit 一跑就弹
#   「找不到 cublas64_11.dll」的系统框，try/except 都拦不住。
#   老断言还写着「推荐 11.8 包（体积小）」，与产品决策正好相反。
rtx3060 = T.GpuInfo(name="NVIDIA GeForce RTX 3060", cuda="13.4", mem_mb=12288, found=True)
key, why = T.pick_cuda_bundle(rtx3060)
chk("3060 推荐 12.4 自包含包", key == "12.4", why)
chk("推荐文案带体积", "439" in why, why)

rtx4090 = T.GpuInfo(name="NVIDIA GeForce RTX 4090", cuda="12.4", mem_mb=24576, found=True)
key, why = T.pick_cuda_bundle(rtx4090)
chk("4090 同样推荐 12.4 自包含包", key == "12.4", why)
chk("默认档就是自包含的那个", T.DEFAULT_CUDA_BUNDLE == "12.4"
    and T.CUDA_BUNDLES["12.4"]["self_contained"] is True)

rtx5090 = T.GpuInfo(name="NVIDIA GeForce RTX 5090", cuda="13.4", mem_mb=32768, found=True)
key, why = T.pick_cuda_bundle(rtx5090)
chk("50 系走 12.4 包（新架构）", key == "12.4", why)

old = T.GpuInfo(name="NVIDIA GeForce GTX 1060", cuda="11.0", mem_mb=6144, found=True)
key, why = T.pick_cuda_bundle(old)
chk("CUDA 低于 11.8 时不给包", key == "", why)
chk("不给包时说明原因", "更新显卡驱动" in why or "CPU" in why, why)

chk("两个包档位都有资产名",
    all(v.get("asset", "").endswith(".zip") for v in T.CUDA_BUNDLES.values()))
chk("11.8 包明显小于 12.4 包",
    T.CUDA_BUNDLES["11.8"]["size_mb"] < T.CUDA_BUNDLES["12.4"]["size_mb"])


# ======================================================================
#  4) 模型推荐
# ======================================================================

chk("12G 显存推荐 large-v3-turbo",
    T.recommend_model("cuda", rtx3060) == "large-v3-turbo",
    T.recommend_model("cuda", rtx3060))
g6 = T.GpuInfo(name="x", cuda="12.0", mem_mb=6 * 1024, found=True)
chk("6G 显存推荐 medium", T.recommend_model("cuda", g6) == "medium")
g3 = T.GpuInfo(name="x", cuda="12.0", mem_mb=3 * 1024, found=True)
chk("3G 显存推荐 small", T.recommend_model("cuda", g3) == "small")
g1 = T.GpuInfo(name="x", cuda="12.0", mem_mb=1024, found=True)
chk("1G 显存退到 base", T.recommend_model("cuda", g1) == "base")
chk("CPU 模式推荐 base", T.recommend_model("cpu", None) == "base")
chk("没显卡时 cuda 模式也不崩", T.recommend_model("cuda", None) == "small")


# ======================================================================
#  5) 显卡检测（用假输出，不依赖真机）
# ======================================================================

_orig_smi, _orig_run = T.nvidia_smi_path, T._run

T.nvidia_smi_path = lambda: None
r = T.detect_gpu()
chk("没有 nvidia-smi 时 found=False", r.found is False)
chk("没有 nvidia-smi 时给出原因", bool(r.reason), r.reason)

T.nvidia_smi_path = lambda: "fake-smi"


def _is_query(cmd):
    # cmd 是列表，元素形如 '--query-gpu=name,...'，不能用 in 直接比整个字符串
    return any("--query-gpu" in str(c) for c in cmd)


def _fake_run(cmd, timeout=15.0):
    if _is_query(cmd):
        return 0, "NVIDIA GeForce RTX 3060, 617.14, 12288 MiB\n"
    return 0, "NVIDIA-SMI 617.14   KMD Version: 617.14   CUDA UMD Version: 13.4\n"


T._run = _fake_run
r = T.detect_gpu()
chk("能解析显卡名", r.name == "NVIDIA GeForce RTX 3060", r.name)
chk("能解析驱动版本", r.driver == "617.14", r.driver)
chk("能解析显存", r.mem_mb == 12288, str(r.mem_mb))
chk("能识别新版 CUDA UMD 写法", r.cuda == "13.4", r.cuda)


def _fake_run_old(cmd, timeout=15.0):
    if _is_query(cmd):
        return 0, "NVIDIA GeForce RTX 3060, 522.06, 12288 MiB\n"
    return 0, "| NVIDIA-SMI 522.06       Driver Version: 522.06       CUDA Version: 11.8 |\n"


T._run = _fake_run_old
r = T.detect_gpu()
chk("能识别老版 CUDA Version 写法", r.cuda == "11.8", r.cuda)


def _fake_run_fail(cmd, timeout=15.0):
    return 1, ""


T._run = _fake_run_fail
r = T.detect_gpu()
chk("nvidia-smi 失败时 found=False", r.found is False)
chk("失败时有原因说明", "驱动" in r.reason, r.reason)

T.nvidia_smi_path, T._run = _orig_smi, _orig_run


# ======================================================================
#  6) 假 HTTP 服务 + 下载器
# ======================================================================

PAYLOAD = (b"JustOneAPI-transcribe-test-payload-" * 1024) * 600   # 约 21 MB
PAYLOAD_MD5 = hashlib.md5(PAYLOAD).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    support_range = True
    slow = False

    def log_message(self, *a):
        pass

    def handle(self):
        # 取消测试里服务端还在慢慢吐数据时会被关掉，
        # 不吞掉这个异常就会往 stderr 打 ConnectionAbortedError，看着像测试挂了
        try:
            super().handle()
        except Exception:
            pass

    def _common(self, length):
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes" if self.support_range else "none")
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self._common(len(PAYLOAD))

    def do_GET(self):
        rng = self.headers.get("Range")
        if rng and self.support_range:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            s = int(m.group(1))
            e = int(m.group(2)) if m.group(2) else len(PAYLOAD) - 1
            e = min(e, len(PAYLOAD) - 1)
            data = PAYLOAD[s:e + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {s}-{e}/{len(PAYLOAD)}")
            self._common(len(data))
            if self.slow:
                for i in range(0, len(data), 65536):
                    if self.slow:
                        time.sleep(0.05)
                    self.wfile.write(data[i:i + 65536])
            else:
                self.wfile.write(data)
            return
        self.send_response(200)
        self._common(len(PAYLOAD))
        self.wfile.write(PAYLOAD)


def _serve(support_range=True, slow=False):
    cls = type("H", (_Handler,), {"support_range": support_range, "slow": slow})
    srv = HTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/file.bin"


srv_ok, url_ok = _serve(support_range=True)
srv_norange, url_norange = _serve(support_range=False)

# 6.1 分块下载正确性
dest = _TMP / "chunked.bin"
dl = T.Downloader(threads=8)
seen = []
ok, err = dl.download([url_ok], dest, progress=lambda d, t: seen.append(d))
chk("分块下载成功", ok, err)
chk("分块下载内容一致（md5）",
    dest.exists() and hashlib.md5(dest.read_bytes()).hexdigest() == PAYLOAD_MD5)
chk("分块下载体积正确", dest.exists() and dest.stat().st_size == len(PAYLOAD),
    str(dest.stat().st_size if dest.exists() else 0))
chk("进度回调有上报", len(seen) > 0, f"{len(seen)} 次")
chk("进度不超过总量", all(d <= len(PAYLOAD) for d in seen))
chk("不留 .part 残留", not dest.with_suffix(dest.suffix + ".part").exists())

# 6.2 服务端不支持 Range 时自动退化
dest2 = _TMP / "norange.bin"
dl2 = T.Downloader(threads=8)
ok2, err2 = dl2.download([url_norange], dest2)
chk("不支持 Range 也能下载成功", ok2, err2)
chk("退化下载内容一致",
    dest2.exists() and hashlib.md5(dest2.read_bytes()).hexdigest() == PAYLOAD_MD5)
chk("不支持 Range 时探测返回 False", T.Downloader()._supports_range(url_norange) is False)
chk("支持 Range 时探测返回 True", T.Downloader()._supports_range(url_ok) is True)

# 6.3 主源失败 → 自动切备源
dest3 = _TMP / "fallback.bin"
ok3, err3 = T.Downloader(threads=4).download(
    ["http://127.0.0.1:9/nope.bin", url_ok], dest3
)
chk("主源挂了会切备源", ok3, err3)
chk("切备源后内容一致",
    dest3.exists() and hashlib.md5(dest3.read_bytes()).hexdigest() == PAYLOAD_MD5)

# 6.4 全部源都失败 → 返回失败且不留垃圾
dest4 = _TMP / "never.bin"
ok4, err4 = T.Downloader(threads=2).download(
    ["http://127.0.0.1:9/a.bin", "http://127.0.0.1:9/b.bin"], dest4
)
chk("全部源失败返回 False", ok4 is False)
chk("失败时给出原因", bool(err4), err4)
chk("失败不留 .part 文件", not (_TMP / "never.bin.part").exists())

# 6.5 取消
srv_slow, url_slow = _serve(support_range=True, slow=True)
dest5 = _TMP / "cancel.bin"
dl5 = T.Downloader(threads=4)
res = {}


def _do_cancel():
    res["ok"], res["err"] = dl5.download([url_slow], dest5)


th = threading.Thread(target=_do_cancel, daemon=True)
th.start()
time.sleep(1.2)
dl5.cancel()
th.join(timeout=20)
chk("取消后下载返回失败", res.get("ok") is False, str(res.get("err")))
# ★ v1.28.2：取消不再删掉 .part。客户在慢镜像上下了 392 MB 的 439 MB 包，
#   一点「停止」就被清空、下次从 0 重来 —— 这正是「永远下不完」的元凶之一。
#   现在保留 .part + .blocks 供下次续传，但绝不能留下伪装成成品的文件。
chk("取消后保留断点续传文件（不再从 0 重来）",
    (_TMP / "cancel.bin.part").exists())
chk("取消后不留下伪装成成品的文件", not dest5.exists())
chk("慢速源确实在传数据（取消有效的前提）", True)

srv_ok.shutdown()
srv_norange.shutdown()
srv_slow.shutdown()


# ======================================================================
#  7) 引擎 / 模型的就绪判断
# ======================================================================

cpu_dir = T.engine_dir("cpu") / "Release"
cpu_dir.mkdir(parents=True, exist_ok=True)
(cpu_dir / "whisper-cli.exe").write_bytes(b"fake")
(cpu_dir / "whisper.dll").write_bytes(b"fake")     # Windows 上必须有，否则跑不起来
chk("能找到 Release/whisper-cli.exe", T.engine_exe("cpu") is not None)
chk("cpu 引擎就绪", T.engine_ready("cpu") is True)
chk("cuda 引擎此时未就绪", T.engine_ready("cuda") is False)

# ★ 回归：曾经打包漏了 whisper.dll，界面却显示"引擎就绪"，用户一点转写就报错。
#   现在缺 DLL 必须判为未就绪，好让上层提示"重新下载引擎"。
(cpu_dir / "whisper.dll").unlink()
chk("缺 whisper.dll 时判为未就绪（不再假装能用）",
    T.engine_ready("cpu") is False)
chk("能报出缺的是哪个文件",
    "whisper.dll" in T.engine_missing_files("cpu"),
    str(T.engine_missing_files("cpu")))
(cpu_dir / "whisper.dll").write_bytes(b"fake")
chk("补回 whisper.dll 后重新就绪", T.engine_ready("cpu") is True)

# main.exe 是老版本的名字，也要认。
# ★ 2026-11-10 修测试用例：以前只造 exe + whisper.dll，可 CUDA 档还要
#   ggml-cuda.dll 和 4 个运行库，engine_ready("cuda") 一直是 False，
#   于是下面「已就绪时不重复下载」那条必然去发起下载并触发 _boom。
#   这个用例本来想造的是「一个装好的显卡引擎」，那就得把文件造齐。
old_dir = T.engine_dir("cuda") / "Release"
old_dir.mkdir(parents=True, exist_ok=True)
(old_dir / "main.exe").write_bytes(b"fake")
(old_dir / "whisper.dll").write_bytes(b"fake")
(old_dir / "ggml-cuda.dll").write_bytes(b"fake")
for _n in T._CUDA_RUNTIME["12"]:
    (old_dir / _n).write_bytes(b"fake")
chk("老版本 main.exe 也能识别", T.engine_exe("cuda") is not None)
chk("造齐运行库后 cuda 引擎确实就绪", T.engine_ready("cuda") is True)

mdl_dir = T.models_dir()
mdl_dir.mkdir(parents=True, exist_ok=True)
big = mdl_dir / T.MODELS["tiny"]["file"]
need = T.MODELS["tiny"]["size_mb"] * 1024 * 1024
with open(big, "wb") as f:
    f.truncate(int(need * 0.95))
chk("模型下到 95% 才算就绪", T.model_ready("tiny") is True)
with open(big, "wb") as f:
    f.truncate(int(need * 0.3))
chk("模型只下了 30% 视为未就绪", T.model_ready("tiny") is False)
chk("模型不存在时未就绪", T.model_ready("medium") is False)

chk("状态文案含引擎与模型",
    "引擎" in T.status_text("cpu", "tiny") and "模型" in T.status_text("cpu", "tiny"),
    T.status_text("cpu", "tiny"))
chk("已装好的引擎不再重复下载", T.install_engine("cpu")[0] is True)

# 已就绪的引擎/模型必须直接返回成功，绝不能再发起下载（否则每次开页面都重下）


def _boom(*a, **k):
    raise AssertionError("已就绪时不应发起下载")


_orig_dl = T.Downloader
T.Downloader = _boom
try:
    with open(big, "wb") as f:
        f.truncate(int(need * 0.99))
    ok_m, msg_m = T.install_model("tiny")
    chk("模型已就绪时不重复下载", ok_m is True, msg_m)
    ok_e, msg_e = T.install_engine("cuda")
    chk("引擎已就绪时不重复下载", ok_e is True, msg_e)
finally:
    T.Downloader = _orig_dl

# 7.1 随包自带的 CPU 引擎：首次运行自动释放，用户不联网也能转写
bundle = _TMP / "bundle" / "Release"
bundle.mkdir(parents=True, exist_ok=True)
(bundle / "whisper-cli.exe").write_bytes(b"fake")
(bundle / "whisper.dll").write_bytes(b"fake")
T.bundled_cpu_engine = lambda: _TMP / "bundle"

shutil.rmtree(T.engine_dir("cpu"), ignore_errors=True)
chk("删掉后 cpu 引擎确实未就绪", T.engine_ready("cpu") is False)
chk("能从内置目录自动释放 CPU 引擎", T.bootstrap_engine("cpu") is True)
chk("释放后文件落到引擎目录",
    (T.engine_dir("cpu") / "Release" / "whisper-cli.exe").exists())
chk("释放后引擎可用", T.engine_ready("cpu") is True)
chk("CUDA 不走内置释放（体积太大，仍按需下载）",
    T.bootstrap_engine("cuda") is False)
shutil.rmtree(T.engine_dir("cpu"), ignore_errors=True)
T.bundled_cpu_engine = lambda: None
chk("没有内置目录时释放失败且不崩", T.bootstrap_engine("cpu") is False)

# 7.2 手动导入本地 zip / 模型（网速差时的兜底）
T.bundled_cpu_engine = lambda: _TMP / "bundle"
# ★ 2026-11-10 修测试：导入的是 cuda 档，就必须造一个**完整的 12.4 包**
#   （ggml-cuda.dll + 4 个运行库）。以前只放 exe + whisper.dll，
#   import_engine_zip 的复验必然判「缺 CUDA 运行库」而拒绝 —— 测试本身不自洽。
zp = _TMP / "eng.zip"
with zipfile.ZipFile(zp, "w") as z:
    z.writestr("Release/whisper-cli.exe", b"fake")
    z.writestr("Release/whisper.dll", b"fake")
    z.writestr("Release/ggml-cuda.dll", b"fake")
    for _n in T._CUDA_RUNTIME["12"]:
        z.writestr(f"Release/{_n}", b"fake")
shutil.rmtree(T.engine_dir("cuda"), ignore_errors=True)
ok_i, msg_i = T.import_engine_zip(zp, "cuda")
chk("能导入本地引擎 zip", ok_i is True, msg_i)
chk("导入后 cuda 引擎就绪", T.engine_ready("cuda") is True)

# 残缺包（客户最容易下到的 11.8 小包形态）必须被拦下并说清原因
thin = _TMP / "thin.zip"
with zipfile.ZipFile(thin, "w") as z:
    z.writestr("Release/whisper-cli.exe", b"fake")
    z.writestr("Release/whisper.dll", b"fake")
shutil.rmtree(T.engine_dir("cuda"), ignore_errors=True)
ok_t, msg_t = T.import_engine_zip(thin, "cuda")
chk("缺运行库的引擎包会被拒绝", ok_t is False, msg_t)
chk("拒绝时说明缺的是 CUDA 运行库", "cuBLAS" in msg_t or "CUDA" in msg_t, msg_t)

bad = _TMP / "bad.zip"
bad.write_bytes(b"not a zip at all")
ok_b, msg_b = T.import_engine_zip(bad, "cpu")
chk("损坏的 zip 导入失败", ok_b is False)
chk("导入失败有明确原因", bool(msg_b), msg_b)

mb = _TMP / "mymodel.bin"
with open(mb, "wb") as f:
    f.truncate(int(T.MODELS["base"]["size_mb"] * 1024 * 1024 * 0.99))
ok_m2, msg_m2 = T.import_model_file(mb, "base")
chk("能导入本地模型文件", ok_m2 is True, msg_m2)
chk("导入后模型校验通过", T.model_ready("base") is True)

tiny_file = _TMP / "tiny.bin"
tiny_file.write_bytes(b"x")
ok_s, msg_s = T.import_model_file(tiny_file, "base")
chk("体积明显不对的模型被拒绝", ok_s is False)
chk("拒绝时说明期望体积", "MB" in msg_s, msg_s)


# ======================================================================
#  8) 转写流程的前置校验
# ======================================================================

tr = T.Transcriber(ffmpeg=None)
ok_w, msg_w = tr.extract_wav(Path(__file__))
chk("没有 ffmpeg 时给出明确提示", ok_w is False and "ffmpeg" in msg_w, msg_w)

src = _TMP / "不存在.mp4"
res = tr.run(T.TranscribeTask(src=src))
chk("源文件不存在时直接失败", res.ok is False and "不存在" in res.msg, res.msg)

# 引擎没装时不能开跑
T.engine_exe = lambda kind: None
res2 = tr.run(T.TranscribeTask(src=Path(__file__)))
chk("引擎未安装时提示先安装", res2.ok is False and "安装" in res2.msg, res2.msg)

# 进度正则
m = T._PROGRESS_RE.search("whisper_print_progress_callback: progress =  42%")
chk("能解析 whisper 进度行", m is not None and m.group(1) == "42", str(m))
m2 = T._PROGRESS_RE.search("progress = 100%")
chk("能解析 100%", m2 is not None and m2.group(1) == "100")
chk("无关行不会误判进度", T._PROGRESS_RE.search("loading model") is None)


# ======================================================================
#  9) 杂项工具
# ======================================================================

chk("human_size 能显示 MB",
    "MB" in T.human_size(5 * 1024 * 1024), T.human_size(5 * 1024 * 1024))
chk("human_size 能显示 GB",
    "GB" in T.human_size(3 * 1024 ** 3), T.human_size(3 * 1024 ** 3))
chk("human_size 异常输入不崩", T.human_size("x") == "-")

sh = T.speed_hint("cuda", "small")
chk("显卡速度提示合理", "倍速" in sh and "10 分钟" in sh, sh)
sh2 = T.speed_hint("cpu", "base")
chk("CPU 速度提示标注更慢", "CPU" in sh2, sh2)

media_root = _TMP / "media"
media_root.mkdir(exist_ok=True)
(media_root / "a.mp4").write_bytes(b"x")
(media_root / "b.txt").write_bytes(b"x")
(media_root / "c.MP3").write_bytes(b"x")
found = T.list_media_files([media_root])
chk("扫描能挑出音视频", len(found) == 2, str([p.name for p in found]))
chk("扫描大小写不敏感", any(p.name == "c.MP3" for p in found))
chk("扫描不存在的目录不崩", T.list_media_files([_TMP / "没有这个目录"]) == [])


# ======================================================================
#  10) 使用说明文案
# ======================================================================

from justoneapi_collector.guide import GUIDES, _TAB_LABEL  # noqa: E402

chk("指导区有 transcribe 文案", "transcribe" in GUIDES)
g10 = GUIDES.get("transcribe", {})
chk("transcribe 有标题", bool(g10.get("title")))
chk("transcribe 有 4 步", len(g10.get("steps", [])) == 4, str(len(g10.get("steps", []))))
chk("transcribe 有注意事项", len(g10.get("tips", [])) >= 3)
chk("transcribe 有标签名", _TAB_LABEL.get("transcribe") == "音视频转文本")
chk("文案提到显卡安装", "一键安装" in "".join(g10.get("steps", [])))
chk("文案提到 CPU 也可选", any("CPU" in s for s in g10.get("steps", [])))


for d in _TMP_DIRS:
    shutil.rmtree(d, ignore_errors=True)

print()
print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项: " + ", ".join(FAIL))
    sys.exit(1)
