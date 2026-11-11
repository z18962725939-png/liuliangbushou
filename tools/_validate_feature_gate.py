# -*- coding: utf-8 -*-
"""功能位门禁与老授权码兼容性的离线自测（2026-10-12 立）。

背景（客户投诉）：发卡工具里没有「关键词抓取」勾选框。
根因是「关键词抓取」「评论区截流」上线时忘了往 licensing.FEATURES 登记。
补登记后立刻冒出一个新风险：**功能位是后补的，老授权码里没有这一位**
—— 若直接判定「不可用」，等于把已经卖给客户的功能收回。

本脚本把两条都要守住：
  A. 老客户码不能失效   —— 已随套餐售出的位（auto）对老码一律放行
  B. 老客户不能白得新模块 —— 独立付费模块（deny，如 GEO）对老码真的拦住

跑法：C:/Python310/python.exe tools/_validate_feature_gate.py
"""
from __future__ import annotations

import json
import sys
import time as _time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from justoneapi_collector import licensing      # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> bool:
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✔' if cond else '✘'} {name}" + (f"   [{extra}]" if extra else ""))
    return cond


# ---------------------------------------------------------------- 造码工具
_REAL_VERIFY = licensing.ed25519_verify
# 自测不需要真私钥：只验证「载荷 → 功能位」这段逻辑，验签环节打桩放行。
licensing.ed25519_verify = lambda *a, **k: True


def make_code(features: str = "", days: int = 365) -> str:
    payload = licensing.build_payload(license_token="tok-0000000000",
                                      machine=licensing.ANY_MACHINE,
                                      days=days, customer="自测客户",
                                      features=features)
    return (f"{licensing.LICENSE_PREFIX}-"
            f"{licensing._b64e(payload.encode('utf-8'))}."
            f"{licensing._b64e(b'sig' * 21 + b'x')}")


_ORIG_STATUS = licensing.status


def fake_status(features: list[str], licensed: bool = True, state: str = "licensed") -> None:
    licensing.status = lambda *a, **k: {
        "state": state, "label": "", "licensed": licensed, "days_left": 100,
        "customer": "自测客户", "expires_at": 0, "machine_id": "AAAA-BBBB-CCCC-DDDD",
        "features": list(features), "embedded_token": "tok", "tamper": 0,
        "trial_start": 0.0, "message": "", "daily_quota": 30, "plan": "标准版",
    }


def feats_of(code: str) -> list[str]:
    return list(licensing.verify_license(code).get("features") or [])


OLD_BASE = "collect,batch,download,live,transcribe,export,analyze,report"   # GEO 出现前签的码
NEW_SET = "collect,keyword,batch,download,live,transcribe,export,analyze,comment,report"
NEW_ALL = NEW_SET + ",geo"

print("1) 解析：三类授权码算出来的功能范围")
f_empty = feats_of(make_code(""))
check("老码（全选签发、无 f 字段）解析成功", bool(f_empty), str(len(f_empty)))
check("老码自动获得后来补登记的常规位 keyword/comment",
      "keyword" in f_empty and "comment" in f_empty)
check("老码不会白得独立付费模块 geo", "geo" not in f_empty)
check("老码仍含首批位 collect", "collect" in f_empty)
# 动态取「auto 补登位」：以后再新增随套餐赠送的功能位，本断言不用改
AUTO_BACKFILL = sorted(k for k, v in licensing.FEATURE_BACKFILL.items() if v == "auto")
check("老码算出的位 = 首批位 + 所有 auto 补登位",
      set(f_empty) == set(licensing.BASE_FEATURES) | set(AUTO_BACKFILL),
      f"期望 {sorted(set(licensing.BASE_FEATURES) | set(AUTO_BACKFILL))}，"
      f"实际 {sorted(f_empty)}")

f_old = feats_of(make_code(OLD_BASE))
check("老码（逐项勾选、首批 8 位）解析为 8 项", len(f_old) == 8, str(f_old))
check("该老码不含 keyword/comment/geo",
      not {"keyword", "comment", "geo"} & set(f_old))

f_new = feats_of(make_code(NEW_SET))
check("新码（采集套件 10 项）不含 geo", "geo" not in f_new and "keyword" in f_new)

f_geo_only = feats_of(make_code("geo"))
check("GEO 单卖码只含 geo", f_geo_only == ["geo"], str(f_geo_only))

print()
print("2) 门禁 check()：老客户不能被收回、也不能白嫖")
fake_status(["collect", "batch", "download", "live", "transcribe",
             "export", "analyze", "report"])          # 典型老客户码
ok_kw, _why = licensing.check("keyword")
check("老客户仍可用「关键词抓取」（auto 兼容）", ok_kw is True)
ok_cm, _why = licensing.check("comment")
check("老客户仍可用「评论区截流」（auto 兼容）", ok_cm is True)
ok_sc, _why = licensing.check("script")
check("老客户可用「短视频文案仿写」（随套餐赠送，走客户自己的 Key）", ok_sc is True)
ok_old, why_old = licensing.check("collect")
check("老客户可用首批位 collect", ok_old is True)
ok_geo, why_geo = licensing.check("geo")
check("老客户【不能】直接用 GEO（独立付费模块）", ok_geo is False, why_geo)
check("GEO 被拦时给出明确原因", bool(why_geo) and "GEO" in why_geo, why_geo)

fake_status(["geo"])
check("GEO 单卖客户可用 GEO", licensing.check("geo")[0] is True)
check("GEO 单卖客户不可用「数据采集」（独立产品，不打包）",
      licensing.check("collect")[0] is False)

fake_status([], licensed=True)                        # 异常兜底：features 为空
check("features 为空的已授权码视为全开（历史兜底行为不变）",
      licensing.check("collect")[0] is True and licensing.check("geo")[0] is True)

fake_status([], licensed=False, state="trial")
check("试用期功能全开", licensing.check("collect")[0] is True)

print()
print("3) 注册表自身的一致性")
check("FEATURES 里含 keyword", "keyword" in licensing.FEATURES)
check("FEATURES 里含 comment", "comment" in licensing.FEATURES)
check("keyword/comment 都标了 auto 策略",
      licensing.FEATURE_BACKFILL.get("keyword") == "auto"
      and licensing.FEATURE_BACKFILL.get("comment") == "auto")
check("geo 标的是 deny 策略", licensing.FEATURE_BACKFILL.get("geo") == "deny")
check("BASE_FEATURES 与 FEATURE_BACKFILL 不重叠",
      not (set(licensing.BASE_FEATURES) & set(licensing.FEATURE_BACKFILL)))
check("BASE_FEATURES 覆盖首批 8 个位",
      set(licensing.BASE_FEATURES) == {"collect", "batch", "download", "live",
                                       "transcribe", "export", "analyze", "report"})

print()
print("4) 发卡工具的省略写法必须已经删掉（否则全勾会吞掉 GEO）")
kg = (ROOT / "tools" / "license_keygen.py").read_text(encoding="utf-8")
check("发卡工具不再有「全勾 = 留空」的省略逻辑",
      "if len(picked) == len(licensing.FEATURES)" not in kg)
check("发卡工具对独立付费模块默认不勾",
      'licensing.FEATURE_BACKFILL.get(k) == "deny"' in kg)
check("发卡工具有「一个功能都没勾」的拦截", "_confirm_no_feature" in kg)

print()
print("5) 免费版（v1.14.0）：开放两个零成本功能，但不能收回老客户权益")
check("FREE_FEATURES 已登记", hasattr(licensing, "FREE_FEATURES"))
check("免费版只含 download 与 live（这两条链路本身不花钱）",
      tuple(licensing.FREE_FEATURES) == ("download", "live"),
      str(getattr(licensing, "FREE_FEATURES", None)))

fake_status(list(licensing.FREE_FEATURES), licensed=False, state="free")
check("免费版：视频下载可用", licensing.check("download")[0])
check("免费版：直播录制可用", licensing.check("live")[0])
for _ff in ("collect", "keyword", "batch", "comment", "geo", "script"):
    check(f"免费版：{_ff} 不可用（付费功能不白送）", not licensing.check(_ff)[0])
check("免费档 is_free_tier() 为真", licensing.is_free_tier())

licensing.status = lambda *a, **k: {
    "state": "expired", "label": "检测到多次修改系统时间", "licensed": False,
    "features": [], "tamper": 3, "daily_quota": 0}
check("改系统时间被抓到的，连免费功能也不给（否则试用形同虚设）",
      not licensing.check("download")[0])

# ★ 这一组是整个改动里最要紧的：免费版是「新增」档位，
#   任何一个已付费客户因为这次改动少了一个功能，都是事故。
fake_status(list(licensing.ALL_FEATURES), licensed=True, state="licensed")
_paid_bad = [_ff for _ff in licensing.ALL_FEATURES if not licensing.check(_ff)[0]]
check("★ 已授权客户：全部功能位仍然可用（免费版没收回任何权益）",
      not _paid_bad, str(_paid_bad))
check("★ 已授权客户不会被当成免费档", not licensing.is_free_tier())

fake_status(list(licensing.ALL_FEATURES), licensed=False, state="trial")
_trial_bad = [_ff for _ff in licensing.ALL_FEATURES if not licensing.check(_ff)[0]]
check("★ 试用期内仍然是全功能", not _trial_bad, str(_trial_bad))
check("试用期不算免费档（试用期有接口额度）", not licensing.is_free_tier())

print()
print("6) 解析不许替用户烧接口额度")
vd = (ROOT / "justoneapi_collector" / "video_downloader.py").read_text(encoding="utf-8")
_i_def = vd.find("def get_video_detail")
# ★ 2026-10-23（v1.24.0）：短路判据从「免费档」升级成「整条解析链路」——
#   现在免费版和完整版都不走付费接口，所以这里查 _paid_resolve_ok，
#   并额外钉住 PAID_FALLBACK_ENABLED 这个总闸必须是关的。
_i_guard = vd.find("_paid_resolve_ok", _i_def)
_i_req = vd.find("self.client.request", _i_def)
check("get_video_detail 有解析短路", _i_guard > 0)
check("★ 短路排在真正发请求之前（排在后面＝先花钱再判断）",
      _i_def < _i_guard < _i_req, f"def={_i_def} guard={_i_guard} req={_i_req}")
check("★ 解析付费兜底总闸是关的（PAID_FALLBACK_ENABLED = False）",
      "PAID_FALLBACK_ENABLED = False" in vd)
_g = vd[vd.find("def _free_tier_blocked"):]
check("读不到授权状态时按「不拦」处理（宁可放过一次，也不误伤付费用户）",
      "return False" in _g[:_g.find("def ", 10)])

print()
print("7) 官网免费版入口")
_st = ROOT / "installer_output" / "standalone"
_free = _st / "free.html"
check("free.html 已生成", _free.is_file())
if _free.is_file():
    _fh = _free.read_text(encoding="utf-8")
    for _w in ("免费", "视频下载", "直播录制", "无需激活"):
        check(f"free.html 命中搜索词「{_w}」", _w in _fh)
    check("free.html 带 canonical", 'rel="canonical"' in _fh)
check("sitemap 已登记 free.html",
      "free.html" in (_st / "sitemap.xml").read_text(encoding="utf-8"))
check("首页有免费版入口",
      'href="free.html"' in (_st / "index.html").read_text(encoding="utf-8"))
check("价格页有免费档", "永久免费" in (_st / "price.html").read_text(encoding="utf-8"))

print()
print("8) 免费档必须真能用（v1.14.1 客户反馈：免费版一点解析就弹「请填 API Token」）")
import importlib          # noqa: E402
import tempfile           # noqa: E402

_vdp = importlib.import_module("gui_qt.pages.video_download_page")
_src = (ROOT / "gui_qt" / "pages" / "video_download_page.py").read_text(encoding="utf-8")

fake_status(list(licensing.FREE_FEATURES), licensed=False, state="free")
check("免费档：页面识别为免费模式", _vdp._free_tier_active() is True)

# —— 门禁：免费档根本不需要 API Token，不许被弹窗拦住 ——
check("页面有 _ensure_ready 门禁（免费档直接放行）",
      hasattr(_vdp.VideoDownloadPage, "_ensure_ready"))
_body = _src[_src.find("def _ensure_ready"):]
_body = _body[:_body.find("def ", 10)]
check("★ 门禁在免费档直接放行（不再要求 Token）",
      "_free_mode()" in _body and "return True" in _body)
_gates = sum(1 for i in range(len(_src)) if _src.startswith("if not self._ensure_ready()", i))
check("★ 解析 / 批量导入 / 下载选中三处门禁都换成了 _ensure_ready",
      _gates >= 3, f"{_gates} 处")
check("老的「没有真实 client 就弹 Token」判断还在（付费客户要能看到它）",
      "if not self._ensure_real_downloader():" in _src)
check("付费功能（博主列表）在免费档给的是「需激活」而不是「填 Token」",
      "_prompt_paid_feature(" in _src)

# —— 占位客户端：免费档撞到付费路径也不能抛 AttributeError ——
_mc = _vdp._MockClient()
_r = _mc.request("douyin-tiktok-china", "video-details-v2", {})
check("占位客户端有 request()，返回失败而不是抛异常",
      getattr(_r, "success", True) is False)

# —— 下载器层：点小红书/快手 = 一句人话，且一分钱不花 ——
#   ★ 2026-10-23：解析已彻底不调付费接口，所以这不再只是"免费档"的行为，
#     对完整版客户也一样。断言改成查"只支持抖音"这个结论，不再查"免费版"字样。
# —— 下载器层：小红书 / 快手改走服务端解析，**一步都不许碰付费接口** ——
#   ★ 2026-10-25（v1.24.6）：以前这两个平台只能花钱走 JustOneAPI 详情接口，
#     而我们早已对外承诺「视频解析不消耗额度」，于是界面只能写「不支持」。
#     现在解析搬到服务端，两边都对得上：能解析，且不扣客户的接口钱。
#   ★ 这个断言用「数接口调用次数」来钉死，别用文案匹配 —— 文案改了就漏。
vdm = importlib.import_module("justoneapi_collector.video_downloader")

class _CountClient(_vdp._MockClient):
    """数一数到底有没有去问 JustOneAPI。"""

    def __init__(self):
        self.calls = 0

    def request(self, *a, **k):
        self.calls += 1
        return super().request(*a, **k)


_real_svc = vdm.VideoDownloader._media_service_resolve
try:
    # ① 服务端解析成功 → 直接采用，且不查付费接口
    vdm.VideoDownloader._media_service_resolve = lambda self, t: (
        vdm.VideoInfo(video_id="svc1", platform="xiaohongshu"), "")
    _dl = vdm.VideoDownloader(_CountClient(), save_dir=None)
    _vi, _why = _dl._resolve_share_link_with_reason_p(
        "xiaohongshu", "https://www.xiaohongshu.com/explore/abc123")
    check("★ 服务端解析成功就直接采用（一次付费接口都不查）",
          _vi is not None and _why == "" and _dl.client.calls == 0,
          f"vi={_vi} calls={_dl.client.calls}")
    _v2, _why2 = _dl._resolve_share_link_with_reason_p(
        "kuaishou", "https://v.kuaishou.com/abc")
    check("★ 快手同样走服务端解析", _v2 is not None and _dl.client.calls == 0,
          f"calls={_dl.client.calls}")

    # ② 服务端也拿不到 → 给人话 + 绝不退回付费接口
    vdm.VideoDownloader._media_service_resolve = lambda self, t: (
        None, "这条内容可能已被作者删除或设为私密")
    _vi, _why = _dl._resolve_share_link_with_reason_p(
        "xiaohongshu", "https://www.xiaohongshu.com/explore/abc123")
    check("★ 服务端拿不到也不许退回去烧付费接口", _dl.client.calls == 0,
          f"calls={_dl.client.calls}")
    check("★ 失败原因是一句人话且带了服务端给的原因",
          _vi is None and len(str(_why)) > 20 and "抖音" in str(_why), str(_why))
finally:
    _dl.close()
    vdm.VideoDownloader._media_service_resolve = _real_svc

# —— 服务端解析的新模块必须健在（key、错误码人话、平台名映射）——
_mp = (ROOT / "justoneapi_collector" / "media_parser.py").read_text(encoding="utf-8")
check("新解析模块内置了作者的 Key", "mp_gSUiBNZBV7TRhcXRWaPmPk3U0wbuEVny" in _mp)
check("★ 错误码全翻成了人话（404/429/500/502 都要有）",
      all(f"{c}:" in _mp for c in (400, 403, 404, 429, 500, 502)))
check("平台名映射覆盖抖音/小红书/快手",
      all(x in _mp for x in ('"抖音"', '"小红书"', '"快手"')))
check("★ 参照系：抖音解析排在本机通道之前（Step 1.8）",
      "服务端解析" in (ROOT / "justoneapi_collector" / "video_downloader.py"
                    ).read_text(encoding="utf-8"))

# —— TXT 导入入口（v1.24.6 客户要的：大批量直接给清单）——
check("视频下载页有独立的 TXT 导入按钮",
      "导入 TXT 链接清单" in _src)
_imp_early = (ROOT / "justoneapi_collector" / "video_link_import.py").read_text(
    encoding="utf-8")
check("★ txt 读取容忍 GBK（记事本默认就存 ANSI）",
      "gb18030" in _imp_early and "def read_links_from_txt" in _imp_early)

# —— 打包 PyObject：函数体里 import 的模块必须写进 hiddenimports ——
#   ★ 这条是出了人命的老坑：静态分析扫不到函数体 import，漏登记的后果是
#     「源码跑得好好的，客户装完包一步都动不了」，而且**零报错**。
_spec_txt = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
check("★ spec 登记了服务端解析模块（函数体 import，漏了安装包直接不解析）",
      "justoneapi_collector.media_parser" in _spec_txt)
check("★ spec 仍登记着浏览器渲染模块（同上）",
      "justoneapi_collector.douyin_page_resolver" in _spec_txt)

# —— 免费档下载/录制【不限量】（v1.20.0）：计数仍记，但绝不拿它拦人 ——
# 背景：早期给免费档卡「每天 5 次」，官网也这么写。后来发现这条路一分钱
# 接口费都不花（页面解析 + 抖音 webcast），卡次数只是把用户赶去用别的工具，
# 于是改成不限量。计数器保留下来只做诊断（状态栏「今日已用 N 次」）。
from justoneapi_collector import quota as _q       # noqa: E402
from justoneapi_collector.licensing import UNLIMITED   # noqa: E402
_tmp = Path(tempfile.mkdtemp())
_orig_paths = _q._state_paths
_q._state_paths = lambda: [_tmp / "state.json"]
check("★ 免费档下载上限 = 不限量（v1.20.0 起不再按天卡次数）",
      _q.free_limit() == UNLIMITED, str(_q.free_limit()))
check("★ 剩余次数同样是不限量（不能退化成 0 把免费用户拦死）",
      _q.free_remaining() == UNLIMITED, str(_q.free_remaining()))
_q.consume_free(2)
check("计数仍在记（纯诊断，不参与拦人）", _q.free_used_today() == 2,
      str(_q.free_used_today()))
check("用掉 2 次后剩余仍是不限量", _q.free_remaining() == UNLIMITED,
      str(_q.free_remaining()))
_q.consume_free(500)
check("★ 狂用 500 次也不会变负数、更不会拦住用户",
      _q.free_remaining() == UNLIMITED and _q.free_used_today() == 502,
      f"remaining={_q.free_remaining()} used={_q.free_used_today()}")
(_tmp / "state.json").write_text(
    '{"date": "2000-01-01", "count": 99, "free_count": 99}', encoding="utf-8")
check("跨天后计数归零（不限量不变）",
      _q.free_used_today() == 0 and _q.free_remaining() == UNLIMITED)
_q._state_paths = lambda: []          # 模拟两个落点都写不进去
check("写盘不可用时也不拦住用户（宁可少记一次）", _q.consume_free(1) >= 0)
_q._state_paths = _orig_paths

# —— 入口不再按次数拦人（v1.20.0）——
check("★ 解析入口不再按「今日剩余」拦免费档",
      "_prompt_free_quota" not in _src)
check("★ 批量解析入口不再按「免费次数」提前返回",
      "今天的免费次数不够这一批" not in _src)

# —— 官网文案：免费版做不到的就别写在免费版里 ——
_site = (ROOT / "tools" / "build_site.py").read_text(encoding="utf-8")
# ★ v1.24.6：解析搬到服务端之后小红书 / 快手两个版本都能下了，
#   对照表必须跟着改口 —— 继续写「暂不支持」就是把已经能用的功能藏起来。
check("★ 首页对照表如实说明小红书/快手下载已支持（v1.24.6 服务端解析）",
      "支持（服务端解析，不用登录，同样不限次数）" in _site
      and "暂不支持" not in _site)
# ★ v1.24.7：补上微信视频号，改成四个平台。这条断言钉的是"能力清单跟着
#   实际能力走"这件事本身 —— 哪天又多支持一个平台，这里必须跟着改口，
#   否则官网会一直停留在旧版本，客户照着旧清单以为不能用。
check("★ 视频下载详情块写明了全部已支持平台（含视频号）",
      'desc="抖音、小红书、快手、微信视频号都能下。' in _site)
check("★ 官网不再声称免费版「每天 5 次」（v1.20.0 已改成不限量）",
      "每天 5 次" not in _site)
check("★ 官网免费版清单写明「不限次数」",
      "不限次数" in _site or "不限量" in _site)

# —— v1.14.2：客户第二次反馈「还是弹 Token」，根因是判据用了 is_free_tier ——
print()
print("9) 试用机 / 没配 Token 的机器也必须能下载（v1.14.2）")
check("★ 门禁改用「下载功能是否被放开」，不再只看授权档",
      "def _download_channel_ok" in _src and "_feature_ok(\"download\")" in _src)
fake_status([], licensed=False, state="trial")          # 试用期、没配 Token 的机器
_page_probe = _vdp.VideoDownloadPage.__new__(_vdp.VideoDownloadPage)   # 不建界面
_page_probe.downloader = type("_D", (), {"client": _vdp._MockClient()})()
check("★ 试用期 + 无 Token：_free_mode 为真（改走零成本通道，不再要求 Token）",
      _page_probe._free_mode() is True)
check("★ 试用期 + 无 Token：_ensure_ready 放行",
      _page_probe._ensure_ready() is True)
licensing.status = lambda *a, **k: {
    "state": "expired", "label": "检测到多次修改系统时间", "licensed": False,
    "features": [], "tamper": 3, "daily_quota": 0}
check("改系统时间作弊的机器仍然拦住（这条不能一起放开）",
      _page_probe._free_mode() is False)

# 占位客户端统一到 _base.NullClient，且两个零成本引擎都能用它构造
_b = importlib.import_module("gui_qt.pages._base")
_nc = _b.NullClient()
check("_base.NullClient 有 request() 且返回失败", _nc.request("x", "y", {}).success is False)
_base_src = (ROOT / "gui_qt" / "pages" / "_base.py").read_text(encoding="utf-8")
for _fn in ("def ensure_downloader", "def ensure_monitor"):
    _seg = _base_src[_base_src.find(_fn):]
    _seg = _seg[:_seg.find("\n    def ", 10)]
    check(f"★ {_fn[4:]} 在无 client 时用 NullClient 兜底（不再直接返回 None）",
          "NullClient()" in _seg)
check("★ 引导文案不再一律叫人去填 Token（改走统一诊断）",
      "connection_diagnosis()" in _src
      and "请在左侧「设置 → API 配置」中填写 API Token" not in _src)

# —— v1.14.3：客户「能解析但下载失败」，界面只报「失败 1 条」 ——
print()
print("10) 下载失败必须说得出原因（v1.14.3）")
_cands = vdm.VideoDownloader._download_candidates(
    "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=abc&ratio=720p")
check("★ 播放地址优先换成无水印的 play（原地址 playwm 带水印）",
      _cands and "/play/" in _cands[0] and "/playwm/" not in _cands[0], str(_cands))
check("原地址保留作兜底（第二条腿）", len(_cands) == 2 and "/playwm/" in _cands[1])
check("候选地址去重", len(set(_cands)) == len(_cands))
_dl_src = (ROOT / "justoneapi_collector" / "video_downloader.py").read_text(encoding="utf-8")
# ★ 2026-10-23：原实现改名为 _download_video_file_once（外层多了「地址失效
#   自动重解析」的包装）。切片起点必须跟着改，否则切到的是包装器 —— 里面
#   没有下载循环，这两条断言会假失败。
_seg = _dl_src[_dl_src.find("def _download_video_file_once"):]
_seg = _seg[:_seg.find("\n    @staticmethod", 10)]
check("★ 下载会逐条候选 + 直连/代理各试一次",
      "_download_candidates" in _seg and "for trust_env in (False, True)" in _seg)
check("★ 全部失败时把原因带回去（不再是空字符串）",
      "return None, \"；\".join(uniq)" in _seg)
# ★ v1.23.0：抖音播放地址带 expire，过期后 CDN 直接 404。客户 2026-10-23
#   截图里 20 条链接有 5 条四个候选地址全 404 —— 那时候软件只会说「链接可能
#   已过期」，让客户自己一条条重新解析。现在软件自己重试一次。
_wrap_start = _dl_src.find("def _download_video_file(")
_wrap_end = _dl_src.find("def _download_video_file_once")
_wrap = _dl_src[_wrap_start:_wrap_end]
check("★ 播放地址失效会自动重新解析一次（不用客户手动重试）",
      "_reparse_for_fresh_url" in _wrap and "_reparse_tried" in _wrap)
check("★ 只有「链接失效」类失败才重解析（写盘/断网不白跑一次请求）",
      "_looks_like_dead_url" in _wrap)
check("★ 重解析拿回同一条死链就不再下第二次（省一次请求）",
      "== video.no_watermark_url" in _dl_src)
_ui_seg = _src[_src.find("def _on_downloaded"):]
_ui_seg = _ui_seg[:_ui_seg.find("\n    def ", 10)]
check("★ 界面上把失败原因显示出来（状态栏 + 弹窗）",
      "原因：" in _ui_seg and "下载未完成" in _ui_seg)
# ★ v1.24.6：解析搬到服务端后，"没登录抖音导致下载失败"这条已经不成立了
#   —— 下载走的是 CDN 直链，跟本机登录态无关，留着它只会把客户推向扫码。
#   改成「过期 / 网络 / 写盘」三条按真实原因排序，每条都带下一步。
check("失败弹窗给出可自查的原因与下一步",
      "重新解析一次再下载" in _ui_seg
      and ("写入失败" in _ui_seg or "网络或代理拦截" in _ui_seg))
check("★ 下载失败弹窗不再甩锅给「没登录」（v1.24.6 服务端解析与登录态无关）",
      "没登录抖音" not in _ui_seg)

# ──────────────────────────────────────────────────────────────────────
print()
print("11) 额度日界必须是 UTC+8 的 0 点，不是 16:00（v1.20.1 客户报障）")
# 背景：老代码写 `localtime(time.time() + 8h)`，在中国机器上等于 UTC+16，
# 日界落在下午 16:00 —— 客户 16:00 之后的调用全被记到"明天"，每天平白
# 丢掉 8 小时额度；首页因此写着「每天 16:00 重置」，而弹窗写「0 点」，
# 自相矛盾。现在改用 gmtime，并带旧数据兼容（不许让客户白拿一整天）。
import time as _time                                             # noqa: E402

_q_src = (ROOT / "justoneapi_collector" / "quota.py").read_text(encoding="utf-8")
check("★ 日界用 gmtime(t+8h)，不再用 localtime（localtime 会二次加机器时区）",
      "time.gmtime(time.time() + _UTC8_OFFSET)" in _q_src
      and "time.localtime(time.time() + _UTC8_OFFSET)" not in _q_src)
check("★ 导入 datetime（旧数据折算要用）",
      "from datetime import" in _q_src)

_UTC8 = 8 * 3600
_real_time = _time.time
_tmp2 = Path(tempfile.mkdtemp())
_q._state_paths = lambda: [_tmp2 / "state.json"]


def _at(y, mo, d, h, mi=0):
    """UTC+8 墙上时刻 → epoch。自检跑在中国时区，本地墙钟即 UTC+8。

    tm_isdst 传 9（Windows 上 -1 会被拒），让 mktime 自己判断夏令时。
    """
    return _time.mktime((y, mo, d, h, mi, 0, 0, 9, -1))


def _freeze(epoch):
    _q.time.time = lambda: epoch


def _utc8(epoch):
    return _time.strftime("%Y-%m-%d", _time.gmtime(epoch + _UTC8))


try:
    # —— 16:00 不再是日界 ——
    # ⚠️ check() 的签名是 check(名称, 条件, 附加说明)，条件必须是布尔表达式。
    #    曾经在这里把「实际值」当成条件传进去，字符串恒为真 ⇒ 断言空转，
    #    真的回归反而测不出来。现在一律写成 `x == y` 并把实际值附上。
    _e = _at(2026, 10, 20, 16, 30)
    _freeze(_e)
    check("★ 16:30 仍算当天（旧代码会算成明天）",
          _q._today() == "2026-10-20", _q._today())
    _e = _at(2026, 10, 20, 15, 59)
    _freeze(_e)
    check("15:59 算当天", _q._today() == "2026-10-20", _q._today())
    # —— 0 点前后必须翻篇 ——
    _e1 = _at(2026, 10, 20, 23, 59)
    _freeze(_e1)
    check("23:59 属于当天", _q._today() == "2026-10-20", _q._today())
    _q._bump(1)
    _written = json.loads((_tmp2 / "state.json").read_text(encoding="utf-8"))
    check("23:59 的调用记在当天", _written.get("date") == "2026-10-20",
          str(_written.get("date")))
    _e2 = _at(2026, 10, 21, 0, 1)
    _freeze(_e2)
    check("00:01 属于次日", _q._today() == "2026-10-21", _q._today())
    check("★ 跨过 0 点计数归零", _q.today_count() == 0, str(_q.today_count()))
    # —— 旧数据兼容：不许让客户白拿一整天额度 ——
    _e = _at(2026, 10, 20, 22, 30)
    _freeze(_e)
    _today, _tom = _utc8(_e), _utc8(_e + 86400)
    (_tmp2 / "state.json").write_text(
        json.dumps({"date": _tom, "count": 30, "free_count": 3}), encoding="utf-8")
    check("★ 旧版写下的『明天』文件仍计入今天（不白拿额度）",
          _q.today_count() == 30, str(_q.today_count()))
    check("★ free_count 同样折算", _q.free_used_today() == 3, str(_q.free_used_today()))
    _q._bump(1)
    _st = json.loads((_tmp2 / "state.json").read_text(encoding="utf-8"))
    check("★ 下次调用把旧文件改回今天（自愈）", _st.get("date") == _today, str(_st))
    check("计数接续不丢", _st.get("count") == 31, str(_st.get("count")))
    check("免费计数不被冲掉", _st.get("free_count") == 3, str(_st.get("free_count")))
    # —— 改时钟防护没有被兼容逻辑打穿 ——
    _e = _at(2026, 10, 21, 9, 0)
    _freeze(_e)
    (_tmp2 / "state.json").write_text(
        json.dumps({"date": "2026-10-23", "count": 30}), encoding="utf-8")
    check("★ 比明天还新的文件仍然不计（改时钟刷额度照样防住）",
          _q.today_count() == 0, str(_q.today_count()))
    # —— 昨天的不计 ——
    (_tmp2 / "state.json").write_text(
        json.dumps({"date": "2026-10-20", "count": 30}), encoding="utf-8")
    check("昨天的计数不计入今天", _q.today_count() == 0, str(_q.today_count()))
finally:
    _q.time.time = _real_time
    _q._state_paths = _orig_paths

# 界面文案必须和真实行为一致（这次报障的直接观感）
check("★ 首页不再写「16:00 重置」", "16:00 重置" not in _src)
check("★ 使用指南不再写「16:00 重置」",
      "16:00 重置" not in (ROOT / "gui_qt" / "widgets" / "guide.py").read_text(encoding="utf-8"))
check("额度用完的提示写 0 点自动重置（与修正后的行为一致）",
      "0 点自动重置" in (ROOT / "justoneapi_collector" / "video_downloader.py"
                        ).read_text(encoding="utf-8"))

# ──────────────────────────────────────────────────────────────────────
print()
print("12) 付费闸门统一（v1.21.0）：点任何付费功能都弹同一张「付费须知」")
# 背景（用户原话）：「先用免费功能来引流……把所有付费功能的界面，只要用户
# 点击使用，都有付费须知弹窗出来」。改之前的两种烂做法：
#   a) 8 个付费页完全没有门禁 —— 免费用户点进去跑出一堆失败和空表，
#      第一反应是"这软件坏了"，引流入口变成差评来源；
#   b) 有门禁的页用「禁用按钮」—— 点了没反应，同样像坏了。
# 现在统一成 _base.require_feature()：按钮保持可点，点了弹须知，说清
# 「这项要钱 / 免费能给什么 / 多少钱 / 去哪买」，并有一键跳转激活页。

_PAGES = ROOT / "gui_qt" / "pages"


def _read(name: str) -> str:
    return (_PAGES / name).read_text(encoding="utf-8")


def _method(src: str, name: str) -> str:
    """抠出一个方法体（同級别缩进的下一个 def 之前）。找不到返回空串。"""
    i = src.find(f"def {name}(")
    if i < 0:
        return ""
    j = src.find("\n    def ", i + 10)
    return src[i:j if j > 0 else len(src)]


_base_full = _read("_base.py")
for _fn in ("def paid_notice", "def require_feature", "def _goto_activation"):
    check(f"_base 提供 {_fn[4:]}", _fn in _base_full)
check("付费须知里写明免费版能一直用的两项",
      "_FREE_FEATURE_BLURB" in _base_full and "不限次数" in _base_full)
check("付费须知里列全三档价格（与官网/发卡机一致）",
      all(p in _base_full for p in ("19.9", "29.9", "59.9")))
check("付费须知带本机机器码（客户截图我们不用再问）",
      "licensing.machine_id()" in _base_full)
check("须知弹窗有「去激活授权码」按钮并跳转设置页",
      "去激活授权码" in _base_full and 'switch_to("设置")' in _base_full)
check("读不到授权态时不拦（宁可放过一次，也不误伤付费用户）",
      "return True" in _method(_base_full, "require_feature"))

# —— 每个付费页的主动作都要挂闸门，且挂在最前面 ——
#     「挂在最前面」用位置断言守住：闸门必须出现在 self.ctx.demo /
#     self.status.setText 之前，否则会先跑一段再弹窗。
_GATE_MAP = [
    ("collect_page.py", "_execute", "collect", "执行采集"),
    ("batch_page.py", "_execute", "batch", "执行批量采集"),
    ("topic_page.py", "_mine", "analyze", "爆款选题挖掘"),
    ("topic_page.py", "_mine_comments", "analyze", "评论区痛点挖掘"),
    ("competitor_page.py", "_run", "analyze", "竞品账号体检"),
    ("watch_page.py", "_add", "analyze", "加入复盘监控"),
    ("watch_page.py", "_refresh", "analyze", "刷新监控快照"),
    ("report_page.py", "_generate", "report", "生成数据报告"),
    ("transcribe_page.py", "_start", "transcribe", "音视频转文字"),
    ("comment_page.py", "_run", "comment", "评论区截流"),
    ("geo_page.py", "_run", "geo", "AI 可见度体检"),
    ("keyword_page.py", "_run", "keyword", "关键词视频抓取"),
    ("script_page.py", "_extract_start", "script", "提取原文口播稿"),
    ("script_page.py", "_run", "script", "文案仿写"),
]
for _fname, _mname, _feat, _what in _GATE_MAP:
    _body = _method(_read(_fname), _mname)
    # 断言串不带右括号：单行 require_feature(self, "x", "y") 和多行
    # require_feature(\n self, "x", "y",\n extra=…) 两种写法都要能命中。
    _needle = f'require_feature(self, "{_feat}", "{_what}"'
    # 去掉所有空白后再找：多行调用（如 GEO 那条带了 extra）不该被判成没挂闸门
    _flat = "".join(_body.split())
    _needle_flat = "".join(_needle.split())
    check(f"{_fname}::{_mname} 挂了「{_what}」闸门", _needle_flat in _flat)
    _i_gate = _body.find("require_feature(")
    _i_work = min([p for p in (_body.find("self.ctx.demo"),
                               _body.find("self.status.setText"),
                               _body.find("self._stop = False"))
                   if p > 0] or [len(_body)])
    check(f"　└ {_mname} 闸门挂在最前面（先弹窗再干活）",
          _i_gate >= 0 and _i_gate < _i_work,
          f"gate={_i_gate} work={_i_work}")

# —— 旧的两种烂做法必须已经绝迹 ——
for _fname in ("comment_page.py", "geo_page.py", "keyword_page.py", "script_page.py"):
    _s = _read(_fname)
    check(f"{_fname} 不再用「禁用按钮」当门禁", "setEnabled(bool(ok))" not in _s)
    check(f"{_fname} 不再弹旧文案「功能未开通」", "功能未开通" not in _s)
_vd_src = _read("video_download_page.py")
check("视频下载页的付费附加动作改走统一须知",
      "paid_notice(self, \"collect\"" in _vd_src
      and "这一项不在免费版里" not in _vd_src)

# —— 免费两条路一根手指都不许碰 ——
_live_src = _read("live_page.py")
check("★ 直播录制页完全没有门禁（免费不限量）",
      "require_feature" not in _live_src and "licensing.check" not in _live_src)
check("★ 视频下载主链路不挂闸门（只拦博主列表那类附加动作）",
      'require_feature(self, "download"' not in _vd_src)
check("★ 视频下载页不再有按次数拦人的旧函数", "_prompt_free_quota" not in _vd_src)
check("设置页有 focus_activation（须知弹窗的一键跳转目标）",
      "def focus_activation" in _read("settings_page.py"))

# —— 数据趋势页故意不设闸门，理由必须写在代码里（防止后来者"修"回去）——
_trend_src = _read("trend_page.py")
check("数据趋势页不挂闸门（只读本机账本，零接口成本）",
      "require_feature" not in _trend_src)
check("　└ 且代码里写明了为什么不拦（防误改）",
      "为什么这一页不设付费闸门" in _trend_src)

print()
print("13) 视频下载页不许被付费额度拦（v1.22.0 客户报障：导入 192 条被「只能完成前 30 条」拦下）")
# 背景：下载页是免费引流入口，「视频下载不限量」是 v1.20.0 对客户的承诺。
# 但批量导入曾有个「额度可能不够」的预检闸门，三条罪：
#   ① 它假设每条链接都花 1 次额度 —— 实际上解析优先走**免费**页面通道；
#   ② 它把免费路径一起堵了（付费客户额度见底时连批量导入都启动不了），
#      而免费用户从来没有这个闸门 ⇒ 付费客户比免费客户受更多限制；
#   ③ 该说清的是**每条**链接为什么失败，而不是动手前把整批拦下。
# 现在：不拦截、批量照跑、事后算账；失败原因透明化（欠费/额度/无数据）。

_vd = _read("video_download_page.py")
check("★ 下载页没有任何 _quota_confirmed 调用（预检闸门已拆）",
      "_quota_confirmed(" not in _vd)
_start = _method(_vd, "_start_batch_resolve")
check("　└ _start_batch_resolve 不再拿额度预检拦人",
      "quota.summary" not in _start and "_quota_confirmed(" not in _start)
check("　└ 且改成事后算账（_import_quota_before）", "_import_quota_before" in _start)
# ★ v1.24.6：引导改成**看失败原因**再决定 —— 服务端解析跟登录态无关，
#   无脑劝扫码就是把客户推向一条走不通的路（上一轮投诉的成因）。
_resolved = _method(_vd, "_on_batch_resolved")
check("批量结果弹窗按失败原因给下一步",
      "重新「分享 → 复制链接」" in _resolved)
check("★ 批量结果不再无脑劝扫码（只有真缺登录态时才提）",
      _resolved.count("平台登录") <= 1)
check("付费档批量结果算接口兜底消耗", "接口兜底消耗" in _vd)

# —— 失败原因透明化：接口欠费不许再说成「无结果」——
_dl = (ROOT / "justoneapi_collector" / "video_downloader.py").read_text(encoding="utf-8")
check("详情接口失败会按业务码翻译原因", "def _explain_api_failure" in _dl)
check("　└ 欠费 601 说成「余额不足 + 联系作者」",
      "余额不足" in _dl and "联系作者" in _dl)
check("　└ 本机额度闸门 -9001 说成「0 点自动重置」", "0 点自动重置" in _dl)
check("　└ 通了但没数据说成「可能已删除」", "可能已删除" in _dl)
check("确定性业务错误不再重试 5 个参数变体", "def _is_final_api_failure" in _dl)
check("免费通道未命中限值放宽到 6（3 会把整批打死）", "FREE_MISS_LIMIT = 6" in _dl)

# —— 批量解析节流：别把抖音风控惹起来 ——
_imp = (ROOT / "justoneapi_collector" / "video_link_import.py").read_text(encoding="utf-8")
check("批量解析每条之间节流（防抖音风控页）",
      "_BATCH_PACE" in _imp and "time.sleep" in _imp)

# 还原
licensing.ed25519_verify = _REAL_VERIFY
licensing.status = _ORIG_STATUS

print()
print("14) 微信视频号支持（v1.24.7：客户直接丢过来一条 https://weixin.qq.com/sph/ 短链）")
# 背景：解析服务能给出直链（实测 1.0 秒拿到无水印地址），下载也 200 通得过 ——
# 也就是"技术上本来就支持"。但拿到一条视频号链接跑一遍，有三处会让它
# 在客户手上变成「能用但很难受」，而且**一条都不会报错**：
#   ① 列表的「平台」列直接显示英文内部键 weixin_channels（违反界面契约）；
#   ② 落盘文件名 96 字符，后半截全是 #话题标签；
#   ③ 服务端没回 platform 时，按域名兜底认不出微信域名。
# 这三条不是 bug，是"不讲究"，但它决定了客户觉得这个软件糙不糙。

# —— 界面不许出现内部键 ——
_b14 = importlib.import_module("gui_qt.pages._base")
check("★ 视频号在界面上显示中文名（不许是 weixin_channels）",
      _b14.platform_label("weixin_channels") == "视频号",
      _b14.platform_label("weixin_channels"))
check("　└ 公众号同样有中文名", _b14.platform_label("weixin_mp") == "公众号",
      _b14.platform_label("weixin_mp"))
check("　└ 已有平台没被这次改动带歪（抖音仍叫抖音）",
      _b14.platform_label("douyin") == "抖音", _b14.platform_label("douyin"))

# —— 按域名认得出微信（服务端没给 platform 时的兜底）——
_mp14 = importlib.import_module("justoneapi_collector.media_parser")
check("★ 按域名认得出视频号短链（/sph/）",
      _mp14.platform_from_url("https://weixin.qq.com/sph/AM94sIUqvX")
      == "weixin_channels")
check("★ 按域名认得出公众号文章（mp.weixin.qq.com/s/）",
      _mp14.platform_from_url("https://mp.weixin.qq.com/s/abc123") == "weixin_mp")
check("★ 下载器侧的平台识别与解析侧一致（两处判据不许分叉）",
      vdm.platform_from_url("https://weixin.qq.com/sph/AM94sIUqvX")
      == "weixin_channels")

# —— 文件名不许塞话题标签 ——
class _V14:                                            # noqa: N801
    author = ""
    desc = ("AI做的UI总有股AI味？这5个Skill直接救命\n"
            "#ai #ai工具 #ui设计 #UI设计师 #AI设计#设计师  #vibecoding #Ai教程")
    video_id = "AM94sIUqvX"


_stem14 = vdm._video_stem(_V14())
check("★ 文件名不夹带 #话题标签", "#" not in _stem14, _stem14)
check("★ 文件名不夹带换行", "\n" not in _stem14 and "\r" not in _stem14)
check("★ 文件名短到能放进深层目录（≤ 60 字符）", len(_stem14) <= 60,
      f"{len(_stem14)} 字符：{_stem14}")
check("　└ 但标题本身没被砍没（只切标签不切内容）", "AI做的UI总有股AI味" in _stem14,
      _stem14)


class _V14b:                                           # noqa: N801
    author = ""
    desc = "#美妆 #教程 #2026"
    video_id = "xyz789"


_stem14b = vdm._video_stem(_V14b())
check("★ 标题全是话题标签时也不退化成空（保留原文而非一律 untitled）",
      "美妆" in _stem14b, _stem14b)

# —— 进表格的标题必须是单行（换行符会被画成缺字形的方块）——
_row14 = _method(_src, "_add_video_info_row")
check("★ 列表标题压成单行（视频号标题带换行）", ".split())" in _row14, _row14[:80])
# ★ 两个坑（都是我自己踩的）：
#   ① 别在源码里找 "#" 字符 —— 注释里就写着 #话题标签，那样断言的是注释；
#   ② 也别光找函数名 —— 指路型注释里也会提到它。
#   只匹配**真实调用形式**（函数名紧跟半角左括号），注释里那句是全角括号收尾，
#   不会误命中。
check("　└ 表格里保留话题标签（只写文件名时才剥）",
      "_clean_desc_for_name(" not in _row14)

# —— 界面文案得提视频号（不然客户以为不支持，白白去问）——
check("视频下载页文案提到了视频号", "视频号" in _src)
_guide_src = (ROOT / "gui_qt" / "widgets" / "guide.py").read_text(encoding="utf-8")
check("使用指导里提到了视频号", "视频号" in _guide_src)

# —— 官网文案同步（官网与软件说法不一致 = 客户照着旧说法去做无效动作）——
check("官网首页提到了视频号",
      "视频号" in (ROOT / "tools" / "build_site.py").read_text(encoding="utf-8"))
_sub_src = (ROOT / "tools" / "build_subpages.py").read_text(encoding="utf-8")
check("官院子页提到了视频号", "视频号" in _sub_src)
check("★ 官网子页不再劝扫码登录（v1.24.6 已作废的旧说法，留着就是坑人）",
      "到「设置 → 平台登录」扫码登录后再试" not in _sub_src)

print()
print("=" * 62)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("\n未通过明细：")
    for f in FAILED:
        print("  ✗", f)
    sys.exit(1)
print("全部通过：老客户权益不被收回，独立付费模块也不会被白送。")
