"""把 DramaClip 的剪辑运行时打成「按需下载」的组件包 zip。

用法
----
    python tools/build_drama_bundle.py --src <DramaClip 仓库根> --out <zip 路径>

★ 为什么 zip 里的目录结构和上游仓库不一样
------------------------------------------
上游仓库的布局是（服务被 resources 包着一层）：

    resources/dramaclip-service/{dramaclip-service.exe, _internal/…}
    resources/{ffmpeg, fonts, face-detect, narration-styles, selftest, subtitle-presets}

我们解压后必须是：

    <bundle>/dramaclip-service/dramaclip-service.exe     ← 服务
    <bundle>/resources/ffmpeg/ffmpeg.exe                 ← 随包只读资源

原因是 drama_bundle 里两个常量就写死了这个位置，而且服务 exe 一旦被塞进
resources 里，它按 `Path(__file__).parents[3] / "resources"` 回退时会算成
`resources/dramaclip-service/resources` —— 那个目录根本不存在。
所以本脚本在打包时把服务目录提到顶层，其余资源留在 resources/ 下。

★ 光有目录结构还不够，还必须注入环境变量
------------------------------------------
服务定位资源只看 `DRAMACLIP_RESOURCES_DIR`，默认回退是从 __file__ 往上数三层，
在打包态必然算错。drama_bridge 起进程时会注入下面两个变量，所以布局与回退
逻辑解耦 —— 这条是"能跑通"的关键，别指望它自己找。

    DRAMACLIP_RESOURCES_DIR = <bundle>/resources
    DRAMACLIP_DATA_DIR      = <bundle>/data   （模型/缓存/产出/日志/BGM 都落这里）

★ 为什么不塞进主安装包
----------------------
组件实测 450MB 量级，主包 44MB。绝大多数客户用不到短剧剪辑，
让所有人背这个体积是说不过去的。

★ 内置的 ASR 模型（faster-whisper-base，145MB）
------------------------------------------------
分析第一步就是语音转写，没有模型就只能停在"扫完了集、转写不了"。
如果留着让客户现下，客户会遇到的是：HF 官方直连不通（HTTP 000），
hf-mirror 实测单线程 ~4KB/s（145MB 要 10 小时）—— 下不动就等于功能没有。
所以从 ModelScope 下（实测 10.5MB/s，145MB 只要 14 秒）直接打进包里，
客户解压完就能用，不需要再连任何模型仓库。

落点必须是 ``data/models/asr/faster-whisper/``：服务按
``DRAMACLIP_DATA_DIR / models / <placement>`` 找模型，而 base 的 placement
就是 ``asr/faster-whisper``。识别判据是四件套齐全（config.json / model.bin /
tokenizer.json / vocabulary.txt），少一个都不算装好。

用法：
    python tools/build_drama_bundle.py --src <仓库根> --models <模型目录> --out <zip>
其中 <模型目录> 下应有 asr/faster-whisper/{config.json,model.bin,...}
"""

from __future__ import annotations

import argparse
import time
import zipfile
from pathlib import Path

# 上游 resources 下这些要原样留在 zip 的 resources/ 里
_RESOURCE_KEEP = (
    "ffmpeg",
    "fonts",
    "face-detect",
    "narration-styles",
    "selftest",
    "subtitle-presets",
)
# 服务目录要提到顶层
_SERVICE_NAME = "dramaclip-service"

# ★ PyInstaller 只收 .py，把 ML 包的运行时资产落下了
# -----------------------------------------------------
# faster_whisper 的 VAD 模型（assets/silero_vad_v6.onnx，1.2MB）就没进包，
# 于是转写一开始做语音活动检测，就在
#   <service>/_internal/faster_whisper/assets/silero_vad_v6.onnx 扑空，
# 报 ONNXRuntimeError: 3 : NO_SUCHFILE —— 客户看到的是"分析失败"，
# 而真正的模型一个不缺，缺的是这 1.2MB 的打包遗漏。
# 补法：原样塞回 _internal 下它该在的位置。整个目录一起补，
# 免得上游换个版本号（v6→v7）又漏一回。
#
# 为什么不在上游 spec 里修：我们是集成方，改上游 spec 要连带重跑整个
# PyInstaller（几分钟）。在组件包里补齐对客户是等价的，且能立刻验证。
_MISSING_RUNTIME_ASSETS = ("faster_whisper/assets",)

# 已经是压缩过的二进制（DLL/so/模型），再压缩收益极小，只用默认级别求快
_COMPRESS_LEVEL = 6


def _iter_files(root: Path):
    """按目录顺序列出全部文件（保证 zip 内容可复现）。"""
    for p in sorted(root.rglob("*"), key=lambda x: str(x).lower()):
        if p.is_file():
            yield p


def build(src: Path, out: Path, models: Path | None = None) -> None:
    res_root = src / "resources"
    if not res_root.is_dir():
        raise SystemExit(f"源目录里没有 resources：{src}")

    out.parent.mkdir(parents=True, exist_ok=True)

    # 先算出要打哪些文件，顺带统计原始大小（进度条要用）
    # 第三项是"是否不压缩" —— 模型权重是量化过的，deflate 压不动多少却要多花几分钟
    jobs: list[tuple[Path, str, bool]] = []
    raw_total = 0
    svc_dir = res_root / _SERVICE_NAME
    if not (svc_dir / f"{_SERVICE_NAME}.exe").is_file():
        raise SystemExit(f"服务 exe 不在：{svc_dir / (_SERVICE_NAME + '.exe')}")
    for f in _iter_files(svc_dir):
        rel = f.relative_to(svc_dir).as_posix()
        jobs.append((f, f"{_SERVICE_NAME}/{rel}", False))
        raw_total += f.stat().st_size

    for name in _RESOURCE_KEEP:
        d = res_root / name
        if not d.is_dir():
            print(f"[warn] 缺少资源目录，跳过：{name}")
            continue
        for f in _iter_files(d):
            rel = f.relative_to(d).as_posix()
            jobs.append((f, f"resources/{name}/{rel}", False))
            raw_total += f.stat().st_size

    venv_sp = src / "service" / ".venv" / "Lib" / "site-packages"
    for rel in _MISSING_RUNTIME_ASSETS:
        d = venv_sp / rel
        if not d.is_dir():
            print(f"[warn] 补漏资产不在，跳过：{d}")
            continue
        for f in _iter_files(d):
            sub = f.relative_to(d).as_posix()
            jobs.append((f, f"{_SERVICE_NAME}/_internal/{rel}/{sub}", False))
            raw_total += f.stat().st_size

    asr_size = 0
    if models is not None:
        if not models.is_dir():
            raise SystemExit(f"模型目录不存在：{models}")
        for f in _iter_files(models):
            rel = f.relative_to(models).as_posix()
            stored = f.suffix.lower() == ".bin"
            jobs.append((f, f"data/models/{rel}", stored))
            raw_total += f.stat().st_size
            asr_size += f.stat().st_size
        print(f"内置模型：{models}（{asr_size / 1048576:.1f} MB）")

    print(f"共 {len(jobs)} 个文件，原始 {raw_total / 1048576:.1f} MB")
    print("正在压缩（deflate），这步 CPU 密集，慢是正常的…")

    t0 = time.time()
    done = 0
    with zipfile.ZipFile(
        out, "w", zipfile.ZIP_DEFLATED, compresslevel=_COMPRESS_LEVEL
    ) as zf:
        for f, arc, stored in jobs:
            zf.write(
                f, arc,
                compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED,
            )
            done += 1
            if done % 50 == 0:
                dt = time.time() - t0
                print(f"  {done}/{len(jobs)}　{dt:.0f}s", end="\r", flush=True)

    got = out.stat().st_size
    dt = time.time() - t0
    print()
    print(f"完成：{out}")
    print(f"  zip {got / 1048576:.1f} MB（压缩率 {got / raw_total * 100:.0f}%），用时 {dt:.0f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="DramaClip 仓库根目录")
    ap.add_argument("--out", required=True, help="输出 zip 路径")
    ap.add_argument("--models", help="内置模型目录（下含 asr/faster-whisper/ 四件套）")
    args = ap.parse_args()
    build(Path(args.src), Path(args.out),
          Path(args.models) if args.models else None)


if __name__ == "__main__":
    main()
