"""转写引擎完整性自检（v1.14.6 立）。

【为什么要有这个脚本】
2026-10-17 客户在「文案仿写」里点提取，弹出 Windows 系统错误框：

    由于找不到 cublas64_11.dll，无法继续执行代码。

查下去发现是个**设计层面的坑**，不是偶发 bug：

  1. 默认下载的 CUDA 11.8 引擎包（59 MB）**不含 cuBLAS**。
     包内只有 cudart64_110 / nvrtc / ggml-cuda，cublas64_11.dll 和
     cublasLt64_11.dll 得由本机已装的 CUDA Toolkit 11.8 提供。
     而普通客户根本不会去装 3 GB 的 Toolkit。
  2. 12.4 包（439 MB）**自带** cublas64_12.dll(67MB) + cublasLt64_12.dll(313MB)
     + cudart64_12.dll，装完即用。以前只有 RTX 50 系才推它，等于把绝大多数
     用户推向了跑不起来的那个包。
  3. engine_missing_files() 只查 whisper.dll，不查 CUDA 运行库 ⇒ 界面上显示
     "引擎已就绪"，一点转写就炸。
  4. 更麻烦的是这类错误**弹的是系统消息框**，不是 Python 异常：进程卡在那里等
     用户点「确定」，try/except 一点用都没有。所以只能**事前查文件**。

本脚本把四件事钉死：
  - 静态：默认档必须是自包含的；11.8 必须明确标注不含 cuBLAS；
  - 行为：临时目录造引擎，缺 cublas 必须判"不完整"、补齐必须判"就绪"；
  - 行为：缺库时文案要说清"缺什么、多大、怎么修"，不许只写"未安装"；
  - 静态：转写入口必须有 CPU 降级，不许让任务直接失败。

跑法：C:/Python310/python.exe tools/_validate_transcribe_engine.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import transcribe as T   # noqa: E402
from justoneapi_collector.transcribe import GpuInfo  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append(f"{name}  {detail}")
        print(f"  ✗ {name}  {detail}")


print("=" * 62)
print("转写引擎完整性自检")
print("=" * 62)

# ---------------------------------------------------------------- 1. 包的选择
print()
print("1. 默认档必须是「自带运行库」的那个包")
bundle124 = T.CUDA_BUNDLES.get("12.4", {})
bundle118 = T.CUDA_BUNDLES.get("11.8", {})
check("12.4 在档位表里", bool(bundle124))
check("12.4 标记为自包含（自带 cuBLAS）", bundle124.get("self_contained") is True,
      str(bundle124.get("self_contained")))
check("11.8 明确标记为不自包含", bundle118.get("self_contained") is False,
      str(bundle118.get("self_contained")))
check("默认档 = 12.4", T.DEFAULT_CUDA_BUNDLE == "12.4", T.DEFAULT_CUDA_BUNDLE)
check("默认档自包含", T.CUDA_BUNDLES[T.DEFAULT_CUDA_BUNDLE].get("self_contained") is True)
check("11.8 的说明里写清不含 cuBLAS",
      "cublas" in str(bundle118.get("note", "")).lower().replace("cublas", "cublas"),
      str(bundle118.get("note"))[:60])

print()
print("2. 运行库清单（11 与 12 两套都要有 cublas）")
for _ver, _need in T._CUDA_RUNTIME.items():
    low = [x.lower() for x in _need]
    check(f"CUDA {_ver} 清单含 cudart", any("cudart" in x for x in low), str(_need))
    check(f"CUDA {_ver} 清单含 cublas64", any(x.startswith("cublas64") for x in low), str(_need))
    check(f"CUDA {_ver} 清单含 cublasLt64",
          any(x.startswith("cublaslt64") for x in low), str(_need))

print()
print("3. 按显卡挑包")
_rtx3060 = GpuInfo(name="NVIDIA GeForce RTX 3060", driver="617.14", cuda="13.4",
                   mem_mb=12288, found=True)
_key, _why = T.pick_cuda_bundle(_rtx3060)
check("3060（驱动 CUDA 13.4）→ 12.4 包", _key == "12.4", f"{_key} / {_why[:40]}")
check("说明里提到不需要另装 Toolkit", "Toolkit" in _why, _why[:60])

_old = GpuInfo(name="GTX 1050", driver="441.66", cuda="10.2", mem_mb=4096, found=True)
_key2, _why2 = T.pick_cuda_bundle(_old)
check("驱动太旧（CUDA 10.2）→ 不给包", _key2 == "", f"{_key2} / {_why2[:40]}")
check("驱动太旧时给出人类可读原因", "驱动" in _why2 and "CPU" in _why2, _why2[:60])

# ---------------------------------------------------------------- 4. 缺库检测
print()
print("4. 缺 cuBLAS 必须判「不完整」（临时目录造引擎，不动真实目录）")
_real_engine_dir = T.engine_dir
_tmp = Path(tempfile.mkdtemp(prefix="tc_engine_"))


def _fake_engine_dir(kind: str) -> Path:
    d = _tmp / kind
    (d / "Release").mkdir(parents=True, exist_ok=True)
    return d


T.engine_dir = _fake_engine_dir


def _touch(kind: str, *names: str) -> None:
    for n in names:
        (_fake_engine_dir(kind) / "Release" / n).write_bytes(b"x")


try:
    # 4.1 只有 exe + whisper.dll：旧逻辑会判"就绪"，新逻辑必须报缺库
    _touch("cuda", "whisper-cli.exe", "whisper.dll", "ggml-cuda.dll", "cudart64_110.dll")
    _miss = T.engine_missing_files("cuda")
    check("只有 11.8 的 cudart 时，报缺 cublas64_11",
          "cublas64_11.dll" in _miss, str(_miss))
    check("只有 11.8 的 cudart 时，报缺 cublasLt64_11",
          "cublasLt64_11.dll" in _miss, str(_miss))
    check("此时判定为未就绪", T.engine_ready("cuda") is False)
    _txt = T.status_text("cuda", "tiny")
    check("状态文案不许显示「已就绪」", "已就绪" not in _txt.split("模型")[0], _txt)
    check("状态文案显示「不完整」", "不完整" in _txt, _txt)

    _prob = T.engine_problem("cuda")
    check("问题说明非空", bool(_prob))
    check("问题说明点出 cuBLAS", "cublas" in _prob.lower(), _prob[:60])
    check("问题说明给出体积（439）或档位（12.4）", "439" in _prob or "12.4" in _prob, _prob[:80])
    check("问题说明给了动作（点按钮）", "一键安装" in _prob or "重新下载" in _prob, _prob[:80])

    # 4.2 补齐 11 那套 → 应该就绪
    _touch("cuda", "cublas64_11.dll", "cublasLt64_11.dll")
    check("补齐 11 那套后判定就绪", T.engine_ready("cuda") is True,
          str(T.engine_missing_files("cuda")))
    check("就绪后问题说明为空", T.engine_problem("cuda") == "", T.engine_problem("cuda"))

    # 4.3 12 那套的同名文件不算数（版本必须对得上）
    shutil.rmtree(_tmp / "cuda", ignore_errors=True)
    _touch("cuda", "whisper-cli.exe", "whisper.dll", "ggml-cuda.dll", "cudart64_12.dll")
    _miss12 = T.engine_missing_files("cuda")
    check("12 的 cudart 要求 12 的 cublas", "cublas64_12.dll" in _miss12, str(_miss12))
    _touch("cuda", "cublas64_12.dll", "cublasLt64_12.dll")
    check("补齐 12 那套后判定就绪", T.engine_ready("cuda") is True)

    # 4.4 缺 ggml-cuda.dll：会悄悄退回 CPU，必须报出来
    (_fake_engine_dir("cuda") / "Release" / "ggml-cuda.dll").unlink()
    check("缺 GPU 后端 ggml-cuda.dll 要报出来",
          "ggml-cuda.dll" in T.engine_missing_files("cuda"),
          str(T.engine_missing_files("cuda")))

    # 4.5 CPU 档不许被 cublas 规则误伤
    _touch("cpu", "whisper-cli.exe", "whisper.dll")
    check("CPU 档不受 CUDA 规则影响", T.engine_ready("cpu") is True,
          str(T.engine_missing_files("cpu")))
finally:
    T.engine_dir = _real_engine_dir
    shutil.rmtree(_tmp, ignore_errors=True)

# ---------------------------------------------------------------- 5. 安装逻辑
print()
print("5. 安装流程（装前清旧、装后复验）")
_src = (ROOT / "justoneapi_collector" / "transcribe.py").read_text(encoding="utf-8")
check("有 _clear_engine_dir（防新旧文件混在一起）", "def _clear_engine_dir" in _src)
check("install_engine 装前清目录", "_clear_engine_dir(kind)" in _src)
check("install_engine 装后复验", "problem = engine_problem(kind)" in _src)
check("import_engine_zip 也复验", _src.count("problem = engine_problem(kind)") >= 2,
      str(_src.count("problem = engine_problem(kind)")))
check("装完不通过要返回失败原因", 'return False, problem' in _src)

# ---------------------------------------------------------------- 6. 降级
print()
print("6. 显卡不可用时必须自动降级 CPU，不许直接失败")
_vse = (ROOT / "justoneapi_collector" / "video_script_extract.py").read_text(encoding="utf-8")
check("转写器有降级分支", "degraded" in _src)
check("降级时把 device 改成 cpu", 'device, exe = "cpu", cpu_exe' in _src)
check("转写结果里说明本次用了 CPU", "本次用 CPU 转写" in _src)
check("仿写 preflight 会降级", "self._degraded = problem" in _vse, "")
check("仿写 preflight 降级后改 device", 'self.opt.device = "cpu"' in _vse)
check("仿写单条转写也会降级", _vse.count('self.opt.device = "cpu"') >= 2)
check("退出码有翻译（DLL 找不到）", "-1073741515" in _src)
# ★ 2026-10-17 补：体检必须在「取 exe」之前。否则显卡引擎连 exe 都没有时
#   （客户机器上最常见的状态）会先撞上「引擎还没安装」直接返回，
#   降级分支根本轮不到执行 —— 这个顺序错一次就退回老毛病。
_i_exe = _src.index("exe = engine_exe(device)") if "exe = engine_exe(device)" in _src else -1
_i_deg = _src.index("degraded = \"\"")
check("显卡体检排在取 exe 之前", _i_exe == -1 or _i_deg < _i_exe,
      "否则缺 exe 时会先返回「还没安装」，降级永不生效")
check("显卡引擎没 exe 时也算不可用",
      '"显卡(CUDA)引擎还没安装"' in _src)
check("CPU 也没装时提示装引擎（不说显卡缺库）", "转写引擎还没安装" in _src)
# ★ 客户若从没打开「音视频转文本」页，随包自带的 CPU 引擎还没释放，
#   降级会因为找不到 CPU 引擎而落空 —— 用之前必须先 bootstrap 一次。
check("降级前先释放随包 CPU 引擎", _src.count("bootstrap_engine(\"cpu\")") >= 2)
check("仿写两条链路都先释放 CPU 引擎",
      _vse.count("bootstrap_engine(\"cpu\")") >= 2 and
      "bootstrap_engine," in _vse,
      "否则客户没开过转写页时降级落空")

# ---------------------------------------------------------------- 7. 界面
print()
print("7. 界面要把原因说出来")
_tp = (ROOT / "gui_qt" / "pages" / "transcribe_page.py").read_text(encoding="utf-8")
_sp = (ROOT / "gui_qt" / "pages" / "script_page.py").read_text(encoding="utf-8")
check("转写页有专门的缺库提示标签", "_problem_label" in _tp)
check("转写页开跑前预检", 'problem = engine_problem("cuda")' in _tp)
check("转写页安装前告知体积", "bundle['size_mb']" in _tp)
check("转写页显示安装失败原因（不只写日志）", "引擎安装失败" in _tp and "QMessageBox.warning" in _tp)
check("仿写页状态栏带缺库提示", "engine_problem" in _sp)
check("仿写页降级后同步下拉框（且不写偏好）", "blockSignals(True)" in _sp)

# ---------------------------------------------------------------- 8. 下载完整性
print()
print("8. 下载完整性（体积对、内容坏 = 最坑的一种失败）")
import inspect  # noqa: E402

_sig = inspect.signature(T.Downloader.download)
check("download 支持 expect_sha256", "expect_sha256" in _sig.parameters,
      str(list(_sig.parameters)))
_sha = T.ASSET_SHA256.get("whisper-cublas-12.4.0-bin-x64.zip", "")
check("12.4 包有官方 SHA256", len(_sha) == 64, _sha[:16])
check("11.8 包有官方 SHA256",
      len(T.ASSET_SHA256.get("whisper-cublas-11.8.0-bin-x64.zip", "")) == 64)
check("CPU 包有官方 SHA256",
      len(T.ASSET_SHA256.get("whisper-blas-bin-x64.zip", "")) == 64)
check("install_engine 把哈希传给下载器",
      "expect_sha256=ASSET_SHA256.get(asset" in _src)
_dl_src = inspect.getsource(T.Downloader.download)
_blk = _dl_src.split("expect_sha256")[-1] if "expect_sha256" in _dl_src else ""
check("哈希不匹配会换源重下（不是直接失败）",
      "校验不通过" in _blk and "continue" in _blk, _blk[:60].replace("\n", " "))
check("Range 响应要验 206", "code != 206" in _src)
check("要验 Content-Range 的起点", "Content-Range" in _src and "startswith" in _src)
check("要验实际写入字节数（防提前掐断）", "got != want" in _src)
check("Range 被忽略时退化为单线程", "_RangeIgnored" in _src and "range_bad" in _src)
check("模型下载后复核体积", "模型文件不完整" in _src)
_fs = T.file_sha256
check("file_sha256 可调用", callable(_fs))
import tempfile as _tf  # noqa: E402
with _tf.NamedTemporaryFile(delete=False) as _f:
    _f.write(b"hello")
    _fp = Path(_f.name)
try:
    check("file_sha256 结果正确",
          _fs(_fp) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
          _fs(_fp)[:16])
finally:
    _fp.unlink(missing_ok=True)

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for _f in FAILED:
        print("  ✗", _f)
    sys.exit(1)
print("全部通过：默认包自包含、缺库不判就绪、缺库会自动降级 CPU。")
