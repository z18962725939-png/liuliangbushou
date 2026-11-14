"""TikTok 提取链路门禁（2026-11-14 客户「tk 无法提取」）。

★ 为什么必须单独一条门禁：这轮的退化全是「静默」的，编译和冒烟都查不出 ——
  ① App 分享短链解析失败时，软件甩「TikTok 在国内被屏蔽，本机无法解析」。
    但服务端实测能打开完整作品页并给出无水印直链，客户被误导成「不支持」；
  ② 文案提取的 whisper 语言写死 zh，英文口播被按中文识别 ⇒ 乱码 / 空文案；
  ③ 界面三处口径打架（文案页 / 指南说支持 TikTok，下载页清单里却没有）；
  ④ 直链 CDN 认浏览器 UA，裸请求回 204 空文件，看起来像「解析成功但没内容」。
只有源码断言 + patch 真跑解析分支 + 请求头断言三层一起才钉得住。

跑法：C:\\Python310\\python.exe tools/_validate_tiktok_extract.py
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_PASSED = 0
_FAILED = 0


def chk(name: str, ok, detail: str = "") -> None:
    """★ 参数顺序是 (名称, 判定, 明细)。写反会变成「字符串恒真」的假绿门禁。"""
    global _PASSED, _FAILED
    if ok:
        _PASSED += 1
        print(f"  [OK] {name}")
    else:
        _FAILED += 1
        print(f"  [!!] {name}" + (f"  → {detail}" if detail else ""))


def src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


MP = src("justoneapi_collector/media_parser.py")
VD = src("justoneapi_collector/video_downloader.py")
VSE = src("justoneapi_collector/video_script_extract.py")
SP = src("gui_qt/pages/script_page.py")
VDP = src("gui_qt/pages/video_download_page.py")
GUIDE = src("gui_qt/widgets/guide.py")

from justoneapi_collector import media_parser as mp
from justoneapi_collector import video_downloader as vd
from justoneapi_collector import video_script_extract as vse

print("=" * 68)
print("TikTok 提取链路门禁 · 2026-11-14")
print("=" * 68)

# ---------------------------------------------------------------- 1 短链展开
print("\n[1] 短链识别与展开（不发外网也能验）")
chk("media_parser 有 is_tiktok_shortlink / expand_tiktok_shortlink",
    "def is_tiktok_shortlink" in MP and "def expand_tiktok_shortlink" in MP)
chk("parse() 在发请求前调用短链展开",
    "payload_text = expand_tiktok_shortlink(payload_text)" in MP and
    MP.index("payload_text = expand_tiktok_shortlink(payload_text)")
    < MP.index('r = c.post(f"{BASE_URL}/api/parse"'))

for u in ("https://vm.tiktok.com/ZMh123/", "https://vt.tiktok.com/ZSabc/",
          "https://t.tiktok.com/abc/", "https://www.tiktok.com/t/ZT8abc/"):
    chk(f"认短链 {u.split('/')[2]}", mp.is_tiktok_shortlink(u), u)
for u in ("https://www.tiktok.com/@tiktok/video/7106594312292453675",
          "https://v.douyin.com/iRNBhoY/", "https://xhslink.com/a/abc",
          "https://www.kuaishou.com/f/abc"):
    chk(f"不误判长链 {u.split('/')[2]}", not mp.is_tiktok_shortlink(u), u)

chk("非 TikTok 链接零网络开销原样返回",
    mp.expand_tiktok_shortlink("https://v.douyin.com/iRNBhoY/")
    == "https://v.douyin.com/iRNBhoY/")

# 只跟短链这一跳，绝不去 GET 长链页面
chk("展开用 follow_redirects=False（只取 302 的 Location）",
    "follow_redirects=False" in MP)
chk("展开超时上限 5 秒，失败静默",
    "timeout: float = 5.0" in MP and "except Exception" in MP)
chk("展开只认含 /video/ 的 tiktok.com 落地页",
    '"/video/" in low' in MP and '"tiktok.com" in low' in MP)


class _Resp:
    headers = {"location": "https://www.tiktok.com/@tiktok/video/7106594312292453675?_r=1&u_code=abc"}


class _Cli:
    def __init__(self, *a, **kw):
        self.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, _u):
        return _Resp()


with patch("httpx.Client", _Cli):
    got = mp.expand_tiktok_shortlink("https://vm.tiktok.com/ZMh123/")
chk("模拟 302 → 展开成完整作品页且去掉查询参数",
    got == "https://www.tiktok.com/@tiktok/video/7106594312292453675", got)

with patch("httpx.Client", side_effect=OSError("连接被重置")):
    got2 = mp.expand_tiktok_shortlink("https://vm.tiktok.com/ZMh123/")
chk("连不上时原样返回、不抛异常（不拖垮批量）",
    got2 == "https://vm.tiktok.com/ZMh123/", got2)

# ---------------------------------------------------------------- 1b 502 重发
print("\n[1b] 服务端间歇性 502：TikTok 失败自动重发一次（实测 3 次里 1 次 502）")
chk("parse 认出 TikTok 链接", '_is_tk = "tiktok.com" in payload_text.lower()' in MP)
chk("parse 对 TikTok 500/502 重发一次",
    "_is_tk and _code in (500, 502) and not _retried_5xx" in MP)
chk("429 仍只重发一次（没把重试放开成无限）",
    "_code == 429 and attempt == 1" in MP)
chk("请求上限 3 次（429 一次 + 502 一次）", "for attempt in (1, 2, 3):" in MP)

LONG = "https://www.tiktok.com/@tiktok/video/7106594312292453675"


class _PResp:
    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


class _Flaky:
    calls = 0

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, _url, json=None):
        _Flaky.calls += 1
        if _Flaky.calls == 1:
            return _PResp({"code": 502, "succ": False,
                           "message": "链接不可访问", "data": None})
        return _PResp({"code": 200, "succ": True, "data": {
            "platform": "tiktok", "title": "demo",
            "video_url": "https://tikcdn.io/ssstik/123"}})


_Flaky.calls = 0
with patch("httpx.Client", _Flaky), patch.object(mp.time, "sleep", lambda *_: None):
    _data, _reason = mp.parse(LONG)
chk("真跑：首条 502 → 自动重发 → 第二条救回",
    _data is not None and _Flaky.calls == 2 and
    _data.get("video_url", "").startswith("https://tikcdn.io/"),
    f"calls={_Flaky.calls} reason={_reason}")


class _Always502(_Flaky):
    calls = 0

    def post(self, _url, json=None):
        _Always502.calls += 1
        return _PResp({"code": 502, "succ": False,
                       "message": "链接不可访问", "data": None})


_Always502.calls = 0
with patch("httpx.Client", _Always502), patch.object(mp.time, "sleep", lambda *_: None):
    _d2, _r2 = mp.parse("https://v.douyin.com/abc/")
chk("非 TikTok 的 502 不重发（不拖慢几百条的批量）",
    _d2 is None and _Always502.calls == 1, f"calls={_Always502.calls}")

# ---------------------------------------------------------------- 2 解析失败文案
print("\n[2] 解析分支：误导文案已换掉，付费闸门仍在")
chk("不再出现「在国内被屏蔽，本机无法解析」",
    "在国内被屏蔽，本机无法解析" not in VD)
chk("TikTok 解析失败给可操作换链指引",
    "完整作品链接" in VD and "tiktok.com/@作者/video/数字ID" in VD)
chk("指引里点明抖音/小红书/快手/视频号可直接粘贴",
    "抖音 / 小红书 / 快手 / 视频号 的分享链接可以直接粘贴" in VD)
chk("domestic_blocked 仍作付费接口闸门（未被误删）",
    VD.count('cfg.get("domestic_blocked")') >= 6,
    f"实际 {VD.count('cfg.get(')} 处 cfg.get")

d = vd.VideoDownloader.__new__(vd.VideoDownloader)
d.cookies = {}

with patch.object(mp, "parse", return_value=(None, "服务端打不开")):
    v, why = d.resolve_share_link_with_reason("https://vm.tiktok.com/FAKE/")
chk("真跑：短链失败返回给客户的是换链指引",
    v is None and "完整作品链接" in why and "国内被屏蔽" not in why, why)

_fake = {"platform": "tiktok", "video_id": "7106594312292453675",
         "title": "how many frogs", "author": "",
         "video_url": "https://tikcdn.io/ssstik/7106594312292453675?st=x",
         "cover_url": "", "audio_url": "", "images": [], "videos": []}
with patch.object(mp, "parse", return_value=(_fake, "")):
    v2, why2 = d.resolve_share_link_with_reason(
        "https://www.tiktok.com/@tiktok/video/7106594312292453675")
chk("真跑：完整链接解析成功、platform=tiktok、直链透传",
    v2 is not None and v2.platform == "tiktok"
    and "tikcdn.io" in (v2.no_watermark_url or ""), why2)

# ---------------------------------------------------------------- 3 下载请求头
print("\n[3] 下载：CDN 认浏览器 UA，不能裸请求（裸请求回 204 空文件）")
chk("_PLATFORM_REFERER 登记了 tiktok",
    '"tiktok": "https://www.tiktok.com/"' in VD)
h = d._dl_headers_for("tiktok")
chk("TikTok 下载头带浏览器 UA", "Mozilla/5.0" in h.get("User-Agent", ""), str(h))
chk("TikTok 下载头用本站 Referer，不是抖音的",
    h.get("Referer") == "https://www.tiktok.com/", str(h))
chk("认不出的平台不发空 Referer（老规矩）",
    "Referer" not in d._dl_headers_for("weixin_channels"))

# ---------------------------------------------------------------- 4 转写语言
print("\n[4] 文案提取：海外平台自动判语言，中文平台保持 zh")
chk("有 lang_for_platform 且登记了非中文平台",
    "def lang_for_platform" in VSE and "_NON_CN_PLATFORMS" in VSE)
chk("TikTok → auto", vse.lang_for_platform("tiktok") == "auto")
chk("YouTube / Instagram → auto",
    vse.lang_for_platform("youtube") == "auto"
    and vse.lang_for_platform("instagram") == "auto")
chk("抖音 / 小红书 / 视频号 / 未知平台沿用默认 zh",
    all(vse.lang_for_platform(p) == "zh" for p in
        ("douyin", "xiaohongshu", "weixin_channels", "")))
chk("自定义默认语言不被覆盖", vse.lang_for_platform("douyin", "en") == "en")
chk("transcribe() 接收 lang 参数",
    "lang" in inspect.signature(vse.ScriptExtractor.transcribe).parameters)
chk("构造 TranscribeTask 时用的是传入的 lang",
    "lang=(lang or self.opt.lang)" in VSE)
chk("extract() 按 res.platform 传语言",
    "lang=lang_for_platform(res.platform, self.opt.lang)" in VSE)
chk("界面默认仍是中文（没有偷偷改成 auto 拖累中文准确率）",
    'lang="zh"' in SP)

# ---------------------------------------------------------------- 5 界面口径
print("\n[5] 界面 / 指南口径统一，且不许出现劝退词")
for name, text in (("文案页", SP), ("下载页", VDP), ("指南", GUIDE)):
    # 只看 TikTok 相关行：下载页另有一处「拉取列表需激活后使用」的付费提示，
    # 属另一条历史文案，不在本门禁范围，别让它把整页判定染红。
    tk_lines = [ln for ln in text.splitlines()
                if "TikTok" in ln or "tiktok.com" in ln]
    chk(f"{name} 的 TikTok 文案不含劝退词",
        bool(tk_lines) and not any(
            w in ln for ln in tk_lines for w in
            ("暂不支持", "需激活", "国内网络不可用", "已禁用", "国内被屏蔽")),
        str(tk_lines[:2]))
    chk(f"{name} 提到 TikTok 完整链接",
        "完整" in text and "TikTok" in text)
chk("文案页不再无差别承诺「TikTok 作品链接」",
    "粘贴抖音 / 小红书 / 快手 / TikTok 作品链接" not in SP)
chk("指南不再写「TikTok 都支持」",
    "抖音 / 小红书 / 快手 / TikTok 都支持" not in GUIDE)
chk("下载页清单补上 TikTok", "TikTok" in VDP)

print("\n" + "=" * 68)
print(f"通过 {_PASSED} / {_PASSED + _FAILED}")
if _FAILED:
    print("TikTok 提取链路门禁未通过")
    sys.exit(1)
print("TikTok 提取链路门禁全绿")
sys.exit(0)
