# -*- coding: utf-8 -*-
"""短视频链接 → 文案提取 自检（离线，不联网、不启动界面）。

跑法：C:/Python310/python.exe tools/_validate_video_script.py

守住这几条底线（都是真机验证里踩出来的）：
  · 时间轴解析必须对：00:00:02,400 就是 2.4 秒，错一格后面全错
  · 超出视频长度的标签必须裁掉 —— whisper 会编出落在音频之外的收尾句，
    不裁的话「实测口播时长 / 真实语速」直接翻倍，而这两个数是要写进
    仿写提示词里控制目标长度的
  · 复读式幻觉必须清：同一句连写十几遍会把钩子判定和字数统计全绑架
  · 幻觉模板句必须整句丢（真机实测那句「请不吝点赞 订阅 转发 打赏支持…」）
  · 每个阶段的失败都要说清该干什么，不能只报「失败」
  · 界面 / 教程 / 打包三条配套不能漏
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from justoneapi_collector import video_script_extract as vse          # noqa: E402
from justoneapi_collector import script_rewriter as sr                # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        FAILED.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


SRT = """1
00:00:00,400 --> 00:00:01,900
汇报工作说结果

2
00:00:01,900 --> 00:00:04,760
老板 这件事我做到了1234

3
00:00:04,760 --> 00:00:07,300
没有功劳 通常没人在意你的苦劳

4
00:00:07,300 --> 00:00:09,200
请示工作说方案
"""

SRT_HALLU = SRT + """
5
00:00:29,900 --> 00:00:59,880
请不吝点赞 订阅 转发 打赏支持明镜与点点栏目
"""

print("1) SRT 时间轴解析")
segs = vse.parse_srt(SRT)
check("解析出 4 段", len(segs) == 4, str(len(segs)))
check("首段起点 = 0.4 秒", abs((segs[0].start if segs else -1) - 0.4) < 1e-6,
      str(segs[0].start if segs else None))
check("第二段起点 = 1.9 秒", abs((segs[1].start if len(segs) > 1 else -1) - 1.9) < 1e-6,
      str(segs[1].start if len(segs) > 1 else None))
check("毫秒换算正确（4.76 秒）",
      abs((segs[1].end if len(segs) > 1 else -1) - 4.76) < 1e-6,
      str(segs[1].end if len(segs) > 1 else None))
check("乱七八糟的输入不会抛异常", vse.parse_srt("随便一段字\n-->\nzzz") == [])
check("空输入返回空", vse.parse_srt("") == [])

print()
print("2) 超出视频长度的时间戳要裁掉（whisper 收尾幻觉）")
hallu = vse.parse_srt(SRT_HALLU)
check("幻觉模板句在解析阶段就被丢掉", len(hallu) == 4, str([s.text for s in hallu]))
check("丢掉之后末段回到真实位置", abs(hallu[-1].end - 9.2) < 0.01, str(hallu[-1].end))

# 再模拟一次「时间戳伸到音频之外」但不是幻觉句的情况：这类只能靠裁 footsteps
SRT_LONG = SRT + """
5
00:00:20,000 --> 00:00:45,000
提前划定工作边界
"""
long_segs = vse.parse_srt(SRT_LONG)
check("末段时间戳确实超出视频长度", long_segs[-1].end == 45.0, str(long_segs[-1].end))
clamped = vse.clamp_segments(long_segs, 31.5)
check("裁完后不超过视频长度", all(s.end <= 31.5 for s in clamped),
      str(max(s.end for s in clamped)))
check("起始就在长度之外的整条丢掉", len(clamped) == 4, str(len(clamped)))
check("长度不明时保持原样", len(vse.clamp_segments(hallu, 0)) == len(hallu))

print()
print("3) 转写稿清洗")
repeat = "\n".join(["同一句"] * 6)
check("复读被压到最多两次", vse.clean_asr_text(repeat).count("同一句") == 2,
      vse.clean_asr_text(repeat))
check("音乐标记被去掉", "[音乐]" not in vse.clean_asr_text("[音乐]\n真的假的"))
check("幻觉模板句整句丢掉",
      "明镜与点点栏目" not in vse.clean_asr_text("讲完了\n请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"))
check("正常内容不被误删",
      vse.clean_asr_text("三二一上链接，今天原价199").startswith("三二一上链接"))
check("空输入不炸", vse.clean_asr_text("") == "")

print()
print("4) 带时间轴的原文拆解（这些数纯文本永远算不出来）")
text = "\n".join(s.text for s in clamped)
a = sr.analyze_source(text, 4.5, clamped)
check("实测口播秒数来自时间轴", abs(a.speech_seconds - 9.2) < 0.2, str(a.speech_seconds))
check("真实语速算得出", a.real_wps > 0, str(a.real_wps))
check("前 3 秒钩子窗口有内容", bool(a.hook_window), a.hook_window)
check("节奏分桶非空", len(a.pace) >= 2, str(a.pace))
check("实测值优先于估算值", abs(a.est_seconds - a.speech_seconds) < 1e-6,
      f"{a.est_seconds} vs {a.speech_seconds}")
check("summary 里带实测信息", "实测口播" in a.summary(), a.summary())
plain = sr.analyze_source(text, 4.5)
check("纯文本不伪造实测值（保持 0）", plain.speech_seconds == 0 and not plain.pace,
      f"{plain.speech_seconds} {plain.pace}")
check("提问式开场也算钩子",
      sr.analyze_source("你是不是也这样觉得？我来讲讲").hook_hits == ["提问开场"],
      str(sr.analyze_source("你是不是也这样觉得？我来讲讲").hook_hits))

print()
print("5) 体裁识别（客户不该自己判断是什么类型）")
cases = [
    ("三二一上链接，今天原价199，直播间只要99", "sell"),
    ("用了三个月才敢测评，这个到底是不是智商税", "review"),
    ("第一步先把水烧开，第二步放料", "tutorial"),
    ("今天带你们去这家开了二十年的店探店打卡", "vlog"),
]
for text_v, want in cases:
    got = sr.guess_genre(text_v, "")
    check(f"「{text_v[:12]}…」→ {want}", got == want, f"实际 {got}")
check("职场干货不该被判成剧情（曾经误判的坑）",
      sr.guess_genre("汇报工作说结果，老板这件事我做到了") != "story",
      sr.guess_genre("汇报工作说结果，老板这件事我做到了"))
check("认不出来退回口播", sr.guess_genre("嗯。") == "talk",
      sr.guess_genre("嗯。"))
check("猜出来的体裁一定在 GENRES 里",
      all(sr.guess_genre(t, "") in sr.GENRES for t, _w in cases))

print()
print("6) 链接切分（界面输入框是一行一个）")
link_text = ("https://v.douyin.com/iAbCdEf/\n"
             "看看这个 https://www.douyin.com/video/7494166718776986914 不错\n"
             "https://www.iesdouyin.com/share/video/7166170264458382606/?region=CN")
links = vse.split_links(link_text)
check("三条链接全抠出来", len(links) == 3, str(links))
check("短链在前且保序", links[0].startswith("https://v.douyin.com/"), links[0])
check("纯文本能当作品 ID 兜底", vse.split_links("7494166718776986914") == ["7494166718776986914"])
check("空输入返回空", vse.split_links("") == [])

print()
print("7) 结果对象与预检查")
r = vse.ExtractedCopy(ok=False, source="asr")
check("来源标签可读", r.source_label == "语音转写", r.source_label)
check("desc 兜底来源标注明确",
      vse.ExtractedCopy(source="desc").source_label == "仅标题", "")
check("时长毫秒换算成秒", vse.ExtractedCopy(duration_ms=31500).video_seconds == 31.5, "")
opt = vse.ExtractOptions()
check("默认走缓存（同一作品不重复转写）", opt.use_cache is True)
check("默认转完删除视频（省硬盘）", opt.keep_video is False)
check("缓存目录能建出来", vse.cache_dir().exists(), str(vse.cache_dir()))
ex = vse.ScriptExtractor(None, opt)
_real_find = vse.find_ffmpeg
try:
    vse.find_ffmpeg = lambda extra=None: None
    flag1, why1 = vse.ScriptExtractor(None, opt).preflight()
finally:
    vse.find_ffmpeg = _real_find
check("没 ffmpeg 时给出可执行的处理方案（而不是只报失败）",
      (not flag1) and ("ffmpeg" in why1), why1)
check("有 ffmpeg 但没模型时，同样指到「安装模型」上",
      isinstance(ex.preflight(), tuple) and len(ex.preflight()[1]) > 0, str(ex.preflight()))

print()
print("8) 界面 / 教程 / 打包配套不能漏")
sp = (ROOT / "gui_qt" / "pages" / "script_page.py").read_text(encoding="utf-8")
for token, name in (("_build_extract_card", "提取卡片"),
                    ("_extract_start", "提取执行"),
                    ("_load_selected", "载入选中到原文"),
                    ("auto_fit_cb", "自动套用体裁与时长"),
                    ("_install_model", "模型安装入口"),
                    ("_choose_ffmpeg", "ffmpeg 指定入口"),
                    ("EXTRACT_COLUMNS", "提取结果表")):
    check(f"页面包含 {name}", token in sp, token)
guide = (ROOT / "gui_qt" / "widgets" / "guide.py").read_text(encoding="utf-8")
check("教程里写了「提取文案」这一步", "提取文案" in guide)
spec = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
check("打包清单加了 video_script_extract",
      "video_script_extract" in spec)

print()
print("=" * 58)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    for f in FAILED:
        print("  ×", f)
    sys.exit(1)
print("全部通过：链接 → 文案 → 拆结构链路可用。")
