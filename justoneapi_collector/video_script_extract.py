# -*- coding: utf-8 -*-
"""短视频链接 → 口播文案（本地语音转写）→ 结构拆解。

═══════════════════════════════════════════════════════════════════════════
客户想要什么（2026-10-12）
═══════════════════════════════════════════════════════════════════════════
「这个文案功能，可以增加一个从抖音等短视频链接直接解析，并直接提取文案，
  然后拆结构这些。」

一句话链路：

    链接 / 短链 / 分享文案
      → 解析成作品信息（复用 video_downloader 的解析，抖音/小红书/快手/TikTok 都支持）
      → 下载无水印视频到缓存目录
      → ffmpeg 抽 16 kHz 单声道音轨
      → whisper.cpp **本地**转写（视频不出本机）
      → 清洗 + 按 SRT 时间轴切段
      → 交给 script_rewriter 拆结构

═══════════════════════════════════════════════════════════════════════════
为什么要把音频转一遍，而不是直接拿作品的 desc
═══════════════════════════════════════════════════════════════════════════
desc 是作者写的标题 + 话题标签，常常只有一句话，"第一人称口播、三句痛点、
一个促单"这种结构全在里面吗？不在。**真正的脚本在音轨里**。
只有把声音转成字，才能知道：钩子落在第几秒、一句话占几秒、哪里留白换镜头、
总共说了多少字（真实语速）。这些才是能复用的东西 —— 光靠一个标题仿不出节奏。

代价是转写要花时间（CPU 上大约 1 分钟视频 ≈ 20~40 秒），所以做了两点：
  1. **按作品 ID 缓存**：同一个视频第二次粘贴秒出，不再转第二遍
  2. **没引擎/没模型/没 ffmpeg 时提前说清**：给出可点的解决路径，
     而不是让客户等半天然后撞上一个看不懂的报错

为什么坚持本地转写：客户的对标视频常常是同行爆款，把源文件传到第三方
ASR 平台等于替对手做备份，还可能有版权与合规风险。本地跑完就删。
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import httpx

from . import branding
from .transcribe import MODELS, DEFAULT_MODEL, Transcriber, TranscribeTask


# 下载的 mp4 边长常常是几十 MB；超过这个秒数先在界面里打个招呼，
# 免得客户以为卡死（CPU 转写长视频确实慢，得让他有预期）。
SLOW_HINT_SECONDS = 180.0


def cache_dir() -> Path:
    """提取出来的文案与临时视频放在这里（%APPDATA%/TrafficCatcher/script_source）。"""
    p = branding.app_data_dir() / "script_source"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:                                      # noqa: BLE001
        pass
    return p


def tmp_dir() -> Path:
    d = cache_dir() / "_tmp"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:                                      # noqa: BLE001
        pass
    return d


def find_ffmpeg(extra: str | None = None) -> str | None:
    """定位 ffmpeg：优先用调用方指定的路径，其次 PATH，最后程序私有目录。"""
    from justoneapi_collector._ffmpeg import find_ffmpeg as _locate
    return _locate(extra)


# ----------------------------------------------------------------------
#  数据结构
# ----------------------------------------------------------------------

@dataclass
class ExtractOptions:
    """一次提取的配置。"""
    model: str = DEFAULT_MODEL        # whisper 模型名（见 transcribe.MODELS）
    device: str = "cpu"               # 'cpu' | 'cuda'
    lang: str = "zh"                  # 转写语言
    keep_video: bool = False          # 转完是否保留 mp4（默认删，省空间）
    ffmpeg: str | None = None         # 手动指定的 ffmpeg 路径
    use_cache: bool = True            # 同一作品是否复用已有结果


@dataclass
class Segment:
    """带明确时间轴的一句话（来自 SRT）。"""
    start: float = 0.0
    end: float = 0.0
    text: str = ""

    def __str__(self) -> str:
        return f"[{self.start:.1f}-{self.end:.1f}s] {self.text}"


@dataclass
class ExtractedCopy:
    """一条链接的提取结果。ok=False 时看 msg / fail_stage 定位。"""
    ok: bool = False
    url: str = ""
    video_id: str = ""
    platform: str = "douyin"
    platform_label: str = ""
    author: str = ""
    desc: str = ""
    duration_ms: int = 0
    digg_count: int = 0

    text: str = ""                                   # 清洗后的完整口播稿
    segments: list[Segment] = field(default_factory=list)
    source: str = ""                                 # asr | cache | desc
    model: str = ""
    chars: int = 0
    speech_seconds: float = 0.0                      # 实测说话秒数
    real_wps: float = 0.0                            # 实测语速
    cost_seconds: float = 0.0                        # 本次花了多少秒
    cached: bool = False
    fail_stage: str = ""                             # resolve | download | transcribe
    msg: str = ""

    @property
    def source_label(self) -> str:
        return {
            "asr": "语音转写",
            "cache": "缓存命中",
            "desc": "仅标题",
        }.get(self.source, "—")

    @property
    def video_seconds(self) -> float:
        return (self.duration_ms or 0) / 1000.0


# ----------------------------------------------------------------------
#  SRT 解析与清洗
# ----------------------------------------------------------------------

_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")

# whisper 在音乐/静音段会输出这些标记，清洗时连同括号一起去掉
_NOISE_TAGS = re.compile(
    r"[（(\[【]\s*(?:音乐|Music|BLANK_AUDIO|掌声| Applause|噪音|辨)"
    r"[^）)\]】]{0,10}\s*[）)\]】]")

# whisper 的"幻觉模板句"：听不清或音轨收尾时会自己编一段套话。
# 实测最典型的是「请不吝点赞 订阅 转发 打赏支持明镜与点点栏目」——它甚至能被
# 编到视频长度之外去（见机上验证日志），必须整句丢掉，否则它会污染
# 钩子判定、字数统计和结尾促单识别。
_HALLU_LINES = (
    "请不吝点赞", "打赏支持", "明镜与点点栏目", "字幕志愿者", "字幕由",
    "感谢收看", "感谢观看", "请点赞订阅", "订阅并关注", "字幕组",
    "Please subscribe", "Subtitles by", "Thanks for watching",
)


def parse_srt(text: str) -> list[Segment]:
    """把 SRT 文本切成带时间轴的片段。解析不出时间轴就返回空表（调用方会兜底用纯文本）。"""
    if not text:
        return []
    out: list[Segment] = []
    blocks = re.split(r"\n\s*\n", str(text).replace("\r\n", "\n").replace("\r", "\n"))
    for blk in blocks:
        lines = [x.strip() for x in blk.split("\n") if x.strip()]
        if not lines:
            continue
        m = None
        body_start = 1
        for i, ln in enumerate(lines[:3]):
            m = _SRT_TIME.search(ln)
            if m:
                body_start = i + 1
                break
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (float(g) for g in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + (ms1 / (1000.0 if len(m.group(4)) == 3 else 100.0))
        end = h2 * 3600 + m2 * 60 + s2 + (ms2 / (1000.0 if len(m.group(8)) == 3 else 100.0))
        body = " ".join(lines[body_start:]).strip()
        body = re.sub(r"<[^>]{1,20}>", "", body)          # 去掉 <u> <i> 之类的富文本标签
        if body and not any(h in body for h in _HALLU_LINES):
            out.append(Segment(round(start, 2), round(end, 2), body))
    return out


def clean_asr_text(text: str) -> str:
    """洗掉转写稿里的噪声：空行、音乐标记、以及「复读」式幻觉。

    whisper 在听不清时会开始复读（同一句连续重复十几遍），不处理的话
    下游的字数、语速、钩子判定会全被这一句绑架。
    """
    if not text:
        return ""
    lines = [x.strip() for x in str(text).replace("\r", "\n").split("\n")]
    lines = [_NOISE_TAGS.sub("", x).strip() for x in lines]
    lines = [re.sub(r"\s{2,}", " ", x) for x in lines if x]

    out: list[str] = []
    repeat = 0
    for ln in [x for x in lines if not any(h in x for h in _HALLU_LINES)]:
        if out and ln == out[-1]:
            repeat += 1
            if repeat >= 2:            # 同一句最多允许出现两次，第三次起丢掉
                continue
        else:
            repeat = 0
        out.append(ln)
    return "\n".join(out).strip()


def clamp_segments(segments: Sequence[Segment], media_seconds: float) -> list[Segment]:
    """把时间轴裁到视频真实长度以内。

    whisper 偶尔会把最后一句的时间戳伸到音频之外（真机实测：31 秒的视频里
    编出一句落在 29.9~59.9 秒的套话）。不裁的话「实测口播时长」「真实语速」
    会直接翻倍 —— 那两个数是要写进仿写提示词里去控制长度目标的关键参数。
    """
    if not segments or media_seconds <= 1:
        return list(segments)
    out: list[Segment] = []
    for s in segments:
        if s.start >= media_seconds:
            continue
        end = min(s.end, media_seconds)
        if end <= s.start:
            continue
        out.append(Segment(s.start, round(end, 2), s.text))
    return out


def segments_text(segments: Sequence[Segment]) -> str:
    """把时间轴片段拼成纯文稿（没有 SRT 时用它顶上）。"""
    return "\n".join(s.text for s in segments if s.text).strip()


# ----------------------------------------------------------------------
#  元信息
# ----------------------------------------------------------------------

def _platform_label(key: str) -> str:
    try:
        from .video_downloader import DOWNLOAD_PLATFORMS
        return str(DOWNLOAD_PLATFORMS.get(key, {}).get("label") or key)
    except Exception:                                      # noqa: BLE001
        return key


def split_links(text: str) -> list[str]:
    """一行一个链接；也支持从分享文案里抠（抖音常带 emoji 和说明文字）。"""
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        from .video_link_import import find_links_in_text
        found = find_links_in_text(raw)
    except Exception:                                      # noqa: BLE001
        found = []
    out: list[str] = []
    for item in found:
        if item not in out:
            out.append(item)
    if out:
        return out
    # 没抠到 http 链接：可能是纯作品 ID，按行原样返回交给解析器去判
    for line in re.split(r"[\r\n,，;；]+", raw):
        s = line.strip()
        if s and s not in out:
            out.append(s)
    return out


# ----------------------------------------------------------------------
#  提取器
# ----------------------------------------------------------------------

class ScriptExtractor:
    """把「链接」变成「带时间轴的口播稿」。

    一台机器上一个就够：持有下载器配置，也能整体 cancel（UI 点「停止」时
    连正在跑的 whisper 进程一起收掉，不留孤儿进程占 CPU）。
    """

    def __init__(self, downloader, opt: ExtractOptions | None = None):
        self.downloader = downloader
        self.opt = opt or ExtractOptions()
        self._cancel = threading.Event()
        self._transcriber: Transcriber | None = None
        # 显卡引擎不可用、自动改用 CPU 时记下原因，界面拿它提示用户
        self._degraded = ""

    # -- 控制 ------------------------------------------------------

    def cancel(self):
        self._cancel.set()
        try:
            if self._transcriber is not None:
                self._transcriber.cancel()
        except Exception:                                  # noqa: BLE001
            pass

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # -- 前置检查 --------------------------------------------------

    def preflight(self) -> tuple[bool, str]:
        """开跑前把三样东西查一遍：ffmpeg、whisper 引擎、模型。

        返回 (能否开跑, 说明)。说明里会给下一步该干什么 ——
        只说「失败」不说怎么办，等于让客户干瞪眼。
        """
        from .transcribe import (bootstrap_engine, engine_problem,
                                 engine_ready, model_ready)

        ff = find_ffmpeg(self.opt.ffmpeg)
        if not ff:
            return False, ("没找到 ffmpeg（抽音轨要用）。\n"
                           "点右边「指定 ffmpeg」选一个本机 ffmpeg.exe，"
                           "或把它放到 %APPDATA%\\TrafficCatcher\\ffmpeg\\ 下。")
        info = MODELS.get(self.opt.model)
        if info is None:
            return False, f"未知模型：{self.opt.model}"
        if not model_ready(self.opt.model):
            return False, (f"「{info['desc']}」这个模型还没下载（约 {info['size_mb']} MB）。\n"
                           "点上面的「安装模型」，下载一次就永久可用。")
        if not engine_ready(self.opt.device):
            problem = engine_problem(self.opt.device)
            # ★ 显卡引擎缺运行库时别让整条链路停摆：CPU 引擎能用就降级跑。
            #   客户要的是「把文案提取出来」，不是「用显卡跑」。
            #   降级前先确保 CPU 引擎已释放（客户可能从没打开过转写页，
            #   随包自带的 2 MB CPU 引擎还没落地，不补这一步降级会落空）。
            if self.opt.device == "cuda":
                bootstrap_engine("cpu")
            if (self.opt.device == "cuda" and engine_ready("cpu")):
                self._degraded = problem
                self.opt.device = "cpu"
            else:
                want = "显卡(CUDA)" if self.opt.device == "cuda" else "CPU"
                return False, (problem or (
                    f"{want}版转写引擎还没装。\n"
                    "先到「音视频转文本」页点「检测显卡并一键安装」，"
                    "CPU 版引擎是随安装包自带的，点一下就会自动释放。"))
        return True, ""

    # -- 单条：解析 ------------------------------------------------

    def resolve(self, url: str) -> tuple[object | None, str]:
        """链接 → VideoInfo。解析不了就把原因带回来（返回 None 时 reason 一定非空）。"""
        dm = self.downloader
        if dm is None:
            return None, "未连接采集服务（请在「设置」里填好 API Token 后重试）"
        try:
            info, reason = dm.resolve_share_link_with_reason(url)
        except Exception as e:                             # noqa: BLE001
            return None, f"{type(e).__name__}: {e}"
        if info is None:
            return None, reason or "解析不出作品信息（链接失效或平台不支持）"
        return info, ""

    # -- 单条：下载 ------------------------------------------------

    def download(self, info, dest_dir: Path,
                 on_frac: Callable[[float, int], None] | None = None
                 ) -> tuple[Path | None, str]:
        """把无水印视频拉到本地。按平台匹配 Cookie，否则抖音 CDN 常返 403。"""
        url = str(getattr(info, "no_watermark_url", "") or "").strip()
        if not url:
            return None, "没有可用的视频下载地址"
        dest_dir.mkdir(parents=True, exist_ok=True)
        vid = str(getattr(info, "video_id", "") or "link")
        dest = dest_dir / f"{vid}.mp4"
        if dest.exists() and dest.stat().st_size > 0:
            return dest, ""

        plat = str(getattr(info, "platform", "") or "douyin")
        cookies = getattr(self.downloader, "cookies", {}) or {}
        headers = dict(getattr(type(self.downloader), "_DL_HEADERS", {}) or {})
        ck = str(cookies.get(plat) or "").strip()
        if ck:
            headers["Cookie"] = ck
        if getattr(info, "no_watermark_url", "") and plat != "douyin":
            headers["Referer"] = {"xiaohongshu": "https://www.xiaohongshu.com/",
                                  "kuaishou": "https://www.kuaishou.com/",
                                  "tiktok": "https://www.tiktok.com/"}.get(plat, "")

        try:
            with httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0),
                              follow_redirects=True, headers=headers) as cli:
                with cli.stream("GET", url) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0) or 0)
                    done = 0
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            if self.cancelled:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            if on_frac and total:
                                on_frac(min(1.0, done / total), done)
        except httpx.HTTPStatusError as e:
            _unlink(dest)
            return None, f"下载被拒：HTTP {e.response.status_code}（可扫码登录后重试）"
        except Exception as e:                             # noqa: BLE001
            _unlink(dest)
            return None, f"下载失败：{type(e).__name__}"

        if self.cancelled:
            _unlink(dest)
            return None, "已取消"
        if not dest.exists() or dest.stat().st_size == 0:
            _unlink(dest)
            return None, "下载到了空文件"
        return dest, ""

    # -- 单条：转写 ------------------------------------------------

    def transcribe(self, media: Path, stem: str,
                   progress: Callable[[int, str], None] | None = None,
                   log: Callable[[str], None] | None = None):
        """跑 whisper，返回 (txt_path, srt_path|None, err)。"""
        from .transcribe import (bootstrap_engine, engine_problem,
                                 engine_ready, model_ready)

        if not engine_ready(self.opt.device):
            problem = engine_problem(self.opt.device)
            if self.opt.device == "cuda":
                bootstrap_engine("cpu")
            if self.opt.device == "cuda" and engine_ready("cpu"):
                # 显卡引擎坏了不该让这一条直接失败，降级把结果先拿到手
                self._degraded = problem
                self.opt.device = "cpu"
                if log:
                    log(problem)
                    log("→ 已自动改用 CPU 引擎继续（结果一样，只是慢一些）")
            else:
                return None, None, (problem or "转写引擎未就绪")
        if not model_ready(self.opt.model):
            return None, None, f"模型 {self.opt.model} 未下载"
        ff = find_ffmpeg(self.opt.ffmpeg)
        if not ff:
            return None, None, "没有 ffmpeg，无法抽取音轨"

        out = cache_dir()
        try:
            self._transcriber = Transcriber(ffmpeg=ff, work_dir=tmp_dir())
            res = self._transcriber.run(
                TranscribeTask(src=media, model=self.opt.model, device=self.opt.device,
                               lang=self.opt.lang, out_dir=out, want_srt=True),
                progress=lambda p: progress(p, "语音转写中") if progress else None,
                log=log)
            if getattr(res, "txt", None) and Path(res.txt).exists():
                _rename_over(Path(res.txt), out / f"{stem}.txt")
            srt_file = getattr(res, "srt", None)
            if srt_file and Path(srt_file).exists():
                _rename_over(Path(srt_file), out / f"{stem}.srt")
                srt_file = out / f"{stem}.srt"
            return res.txt, srt_file, ("" if res.ok else (res.msg or "转写失败"))
        except Exception as e:                             # noqa: BLE001
            return None, None, f"转写出错：{type(e).__name__}: {e}"
        finally:
            self._transcriber = None

    # -- 单条：全流程 ----------------------------------------------

    def extract(self, url: str,
                progress: Callable[[float, str], None] | None = None,
                log: Callable[[str], None] | None = None) -> ExtractedCopy:
        """最外层：解析 → 下载 → 转写 → 清洗 → 时间轴。

        progress 收到的是 0~100 的整体百分比；单条跑完必返回 ExtractedCopy，
        不抛异常 —— 批量时一条炸了不能连坐后面的。
        """
        t0 = time.time()
        res = ExtractedCopy(url=url, model=self.opt.model)
        emit = (lambda p, m: progress(p, m)) if progress else (lambda *_: None)

        # 1. 解析
        emit(5, "解析链接…")
        info, reason = self.resolve(url)
        if info is None:
            res.fail_stage = "resolve"
            res.msg = reason
            return res
        res.video_id = str(getattr(info, "video_id", "") or "")
        res.platform = str(getattr(info, "platform", "") or "douyin")
        res.platform_label = _platform_label(res.platform)
        res.author = str(getattr(info, "author", "") or "")
        res.desc = str(getattr(info, "desc", "") or "")
        res.duration_ms = int(getattr(info, "duration", 0) or 0)
        res.digg_count = int(getattr(info, "digg_count", 0) or 0)

        # 2. 缓存
        cache_txt = cache_dir() / f"{res.video_id or 'unknown'}.txt"
        cache_srt = cache_dir() / f"{res.video_id or 'unknown'}.srt"
        if self.opt.use_cache and res.video_id and cache_txt.exists():
            try:
                text = clean_asr_text(cache_txt.read_text(encoding="utf-8", errors="ignore"))
                if text:
                    res.text = text
                    res.source = "cache"
                    res.cached = True
                    res.ok = True
                    res.segments = clamp_segments(
                        parse_srt(cache_srt.read_text(encoding="utf-8", errors="ignore")),
                        res.video_seconds,
                    ) if cache_srt.exists() else []
                    res.chars = len(re.sub(r"\s+", "", text))
                    if res.segments:
                        res.speech_seconds = round(max(s.end for s in res.segments), 1)
                        if res.speech_seconds > 1:
                            res.real_wps = round(res.chars / res.speech_seconds, 2)
                    res.cost_seconds = round(time.time() - t0, 1)
                    emit(100, "命中缓存，直接复用")
                    return res
            except Exception:                              # noqa: BLE001
                pass                                       # 读坏了就重新转

        # 3. 下载
        emit(12, "下载视频…")
        media, err = self.download(info, tmp_dir(),
                                   on_frac=lambda f, _n: emit(12 + f * 28,
                                                              f"下载视频 {int(f * 100)}%"))
        if media is None:
            # 拿不到视频不等于没辙：退化到作者写的文案（会标清楚来源）
            if res.desc.strip():
                res.text = res.desc.strip()
                res.source = "desc"
                res.chars = len(re.sub(r"\s+", "", res.text))
                res.ok = True
                res.fail_stage = "download"
                res.msg = (f"拿不到视频文件（{err}），只用标题文案兜底 —— "
                           "这类其实等于没提取，建议检查网络或扫码登录后重试")
                return res
            res.fail_stage = "download"
            res.msg = err
            return res

        if res.video_seconds > SLOW_HINT_SECONDS and log:
            log(f"这个视频有 {res.video_seconds/60:.1f} 分钟，"
                f"{'CPU' if self.opt.device == 'cpu' else '显卡'}转写可能要几分钟，别急着以为是卡死")

        # 4. 转写
        # whisper 按输入文件名输出，先把 mp4 改成 _<id>.mp4：
        # 万一转一半被中止，冲掉的是临时文件而不是上一份能用的缓存。
        tmp_media = tmp_dir() / f"_{res.video_id or 'tmp'}.mp4"
        _rename_over(media, tmp_media)
        media = tmp_media
        stem = media.stem
        for ext in (".txt", ".srt"):
            _unlink(cache_dir() / (stem + ext))

        emit(42, "语音转写中…")
        _txt, srt_file, terr = self.transcribe(
            media, stem,
            progress=lambda p, m: emit(42 + p * 0.55, f"语音转写 {p}%"),
            log=log)
        txt_file = cache_dir() / f"{stem}.txt"
        if terr or (not txt_file.exists()):
            res.fail_stage = "transcribe"
            res.msg = terr or "转写没有产出文本"
            if not self.opt.keep_video:
                _unlink(media)
            return res

        # 5. 收尾
        try:
            raw = txt_file.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:                             # noqa: BLE001
            res.fail_stage = "transcribe"
            res.msg = f"读转写结果失败：{e}"
            return res
        text = clean_asr_text(raw)
        if not text.strip():
            res.fail_stage = "transcribe"
            res.msg = "视频里没听出人话（可能是纯音乐 / 无人声）"
            if not self.opt.keep_video:
                _unlink(media)
            return res

        _rename_over(txt_file, cache_dir() / f"{res.video_id or stem}.txt")
        segs: list[Segment] = []
        if srt_file and Path(srt_file).exists():
            try:
                segs = parse_srt(Path(srt_file).read_text(encoding="utf-8", errors="ignore"))
            except Exception:                              # noqa: BLE001
                segs = []
        if segs:
            _rename_over(Path(srt_file), cache_dir() / f"{res.video_id or stem}.srt")
            # 裁掉超出视频长度的时间戳（whisper 收尾幻觉），并按裁完的片段重建正文：
            # 幻觉句被丢 之后，字数/语速/钩子才跟视频真实内容对得上。
            segs = clamp_segments(
                [Segment(s.start, s.end, clean_asr_text(s.text).replace("\n", " "))
                 for s in segs], res.video_seconds)
            segs = [s for s in segs if s.text]
            res.segments = segs
            if segs:
                rebuilt = clean_asr_text("\n".join(s.text for s in segs))
                # SRT 偶尔比 txt 少一两句，遇到大幅缩水就还是用 txt 的原文
                if len(re.sub(r"\s+", "", rebuilt)) >= len(re.sub(r"\s+", "", text)) * 0.8:
                    text = rebuilt

        res.text = text
        res.source = "asr"
        res.chars = len(re.sub(r"\s+", "", text))
        res.ok = True
        if res.segments:
            res.speech_seconds = round(max(s.end for s in res.segments), 1)
            if res.speech_seconds > 1:
                res.real_wps = round(res.chars / res.speech_seconds, 2)
        res.cost_seconds = round(time.time() - t0, 1)

        if not self.opt.keep_video:
            _unlink(media)
        else:
            res.msg = f"视频已保留在：{media}"
        emit(100, f"提取完成：{res.chars} 字")
        return res

    # -- 批量 ------------------------------------------------------

    def extract_many(self, urls: Sequence[str],
                     progress: Callable[[float, str], None] | None = None,
                     log: Callable[[str], None] | None = None
                     ) -> list[ExtractedCopy]:
        """一行一个链接批量提。单条失败不中断整批。"""
        out: list[ExtractedCopy] = []
        total = len(urls)
        for i, url in enumerate(urls, 1):
            if self.cancelled:
                if log:
                    log("用户已停止")
                break
            item = self.extract(
                url,
                progress=lambda p, m, _i=i: (
                    progress(((_i - 1) + p / 100.0) / total * 100.0,
                             f"({_i}/{total}) {m}") if progress else None),
                log=log)
            out.append(item)
        return out


# ----------------------------------------------------------------------
#  小工具
# ----------------------------------------------------------------------

def _unlink(p: Path | None):
    try:
        if p and Path(p).exists():
            Path(p).unlink()
    except Exception:                                      # noqa: BLE001
        pass


def _rename_over(src: Path, dst: Path):
    """同目录改名覆盖（Windows 的 os.rename 遇到已存在会直接失败）。"""
    try:
        src = Path(src)
        dst = Path(dst)
        if src.resolve() == dst.resolve():
            return
        import os
        if dst.exists():
            dst.unlink()
        os.replace(src, dst)
    except Exception:                                      # noqa: BLE001
        pass


def list_cached(limit: int = 50) -> list[Path]:
    """列出已经提取过的文案（不含内部临时文件），按修改时间倒序。"""
    try:
        files = [p for p in cache_dir().glob("*.txt") if not p.name.startswith("_")]
    except Exception:                                      # noqa: BLE001
        return []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def clear_cache(video_ids: Iterable[str] | None = None) -> int:
    """清掉缓存（指定作品 ID 只清那几条）。返回删掉的文件数。"""
    n = 0
    ids = set(video_ids or [])
    for ext in (".txt", ".srt"):
        for p in cache_dir().glob(f"*{ext}"):
            if ids and p.stem not in ids:
                continue
            try:
                p.unlink()
                n += 1
            except Exception:                              # noqa: BLE001
                pass
    return n
