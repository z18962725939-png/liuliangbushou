# -*- coding: utf-8 -*-
"""自测：按接口单价扣额度 + 解析改为「免费页面解析优先」（v1.5.0）。

背景：JustOneAPI 各接口单价不同（热榜 / 图文搜索 0.2 元，其余 0.1 元），
以前一律 +1 次等于把高价接口半价卖；同时解析顺序是「先花钱查接口、
再试免费的页面解析」，批量导入几十条链接时白花一整批的钱。

v1.22.0 增补：批量导入的付费额度**预检闸门已拆除**（客户导入 192 条链接
被「只能完成前 30 条」拦下，而下载页是免费引流入口），改为不拦截 +
事后算账；接口失败原因透明化（欠费 601 / 额度用完 / 无数据分开说）。

本脚本全部离线（假 client + 假 httpx），不消耗任何真实额度：
  A. 接口成本表
  B. 闸门按成本扣（高价接口一次扣 2 分）
  C. _free_html_resolve 零成本通道
  D. _douyin_resolve_impl 免费优先 / API 兜底
  E. 批量导入不再预检额度 / 失败原因透明化
"""
import os
import sys
import tempfile
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from justoneapi_collector import quota                        # noqa: E402
from justoneapi_collector.models import ApiResponse           # noqa: E402

# 把用量状态重定向到临时目录，绝不碰本机真实 usage.json
_TMP = tempfile.mkdtemp(prefix="cost_resolve_test_")
import pathlib                                                # noqa: E402
quota._state_paths = lambda: [                                # type: ignore[assignment]
    pathlib.Path(_TMP) / "usage.json",
    pathlib.Path(_TMP) / "local" / "usage.json",
]

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


# ======================================================================
#  A. 接口成本表
# ======================================================================
print("\n=== A. 接口成本表（1 分 = 0.1 元）===")
check("热榜 hot-search-v1 记 2 分", quota.endpoint_cost("hot-search-v1") == 2,
      f"={quota.endpoint_cost('hot-search-v1')}")
check("图文搜索 search-image-v1 记 2 分", quota.endpoint_cost("search-image-v1") == 2,
      f"={quota.endpoint_cost('search-image-v1')}")
check("视频详情 video-details-v2 记 1 分", quota.endpoint_cost("video-details-v2") == 1)
check("关键词搜索 video-search-v4 记 1 分", quota.endpoint_cost("video-search-v4") == 1)
check("未登记接口默认 1 分", quota.endpoint_cost("some-new-endpoint-v9") == 1)
check("空 / None 兜底为 1 分",
      quota.endpoint_cost("") == 1 and quota.endpoint_cost(None) == 1)
check("大小写不敏感", quota.endpoint_cost("HOT-SEARCH-V1") == 2)


# ======================================================================
#  B. 闸门按成本扣
# ======================================================================
print("\n=== B. 闸门按成本扣 ===")


class _FakeClient:
    """假客户端：只记调用次数，永远返回成功。"""

    def __init__(self):
        self.calls = 0
        self.last_endpoint = ""

    def request(self, platform, endpoint, params=None):
        self.calls += 1
        self.last_endpoint = endpoint
        return ApiResponse(success=True, code=0, message="", data={"ok": 1},
                           raw_json={"code": 0, "data": {"ok": 1}})


orig_limit = quota.limit
quota.reset_today()
quota.limit = lambda: 10                                     # type: ignore[assignment]

fc = _FakeClient()
quota.install(fc)

fc.request("douyin-tiktok-china", "video-details-v2", {"videoId": "1"})
check("0.1 元接口扣 1 分", quota.today_count() == 1, f"count={quota.today_count()}")

fc.request("douyin-tiktok-china", "hot-search-v1", {})
check("0.2 元接口扣 2 分", quota.today_count() == 3, f"count={quota.today_count()}")

fc.request("douyin-tiktok-china", "hot-search-v1", {})
check("连续两次热榜扣 4 分", quota.today_count() == 5, f"count={quota.today_count()}")

# 已用 7 分（上限 10）：再刷一次热榜 → 9 分，只剩 1 分
fc.request("douyin-tiktok-china", "hot-search-v1", {})
check("第四次热榜后剩 3 分", quota.today_count() == 7, f"count={quota.today_count()}")
fc.request("douyin-tiktok-china", "hot-search-v1", {})
check("第五次热榜后只剩 1 分", quota.today_count() == 9, f"count={quota.today_count()}")

# 只剩 1 分，却要调 0.2 元的接口 → 必须拦下（否则就会超支 1 分）
calls_before = fc.calls
resp = fc.request("douyin-tiktok-china", "hot-search-v1", {})
check("★ 余量不足高价接口时拦下", resp.code == quota.QUOTA_EXCEEDED_CODE,
      f"code={resp.code}")
check("拦下时不再发起请求", fc.calls == calls_before, f"calls={fc.calls}")

# 剩 1 分，0.1 元的接口还能跑
resp2 = fc.request("douyin-tiktok-china", "video-details-v2", {"videoId": "2"})
check("余量 1 分时 0.1 元接口仍可用", resp2.success, f"count={quota.today_count()}")
check("低价接口只扣 1 分", quota.today_count() == 10, f"count={quota.today_count()}")

quota.limit = orig_limit                                     # 还原


# ======================================================================
#  C. _free_html_resolve 零成本通道
# ======================================================================
print("\n=== C. 免费页面解析通道 ===")
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402


class _Resp:
    def __init__(self, code, text, url):
        self.status_code = code
        self.text = text
        self.url = url


class _FakeHttp:
    def __init__(self, resp):
        self.resp = resp
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


_SAVE = pathlib.Path(_TMP) / "downloads"
dl = VideoDownloader(_FakeClient(), save_dir=_SAVE)

GOOD_HTML = (
    '<html><body><script>window._ROUTER_DATA = {'
    '"aweme_id":"7166170264458382606","desc":"测试作品"}</script>'
    '<a href="https://www.douyin.com/video/7166170264458382606">本作品</a>'
    '"play_addr":{"uri":"v0300","url_list":["https://api.douyinvod.com/a.mp4"]}'
    '</body></html>'
)
HOME_HTML = "<html><body>推荐流</body></html>"

# C1 正常解析
dl._redirect_client = _FakeHttp(_Resp(200, GOOD_HTML, "https://www.douyin.com/video/7166170264458382606"))
v, vid, note = dl._free_html_resolve("https://www.douyin.com/video/7166170264458382606")
check("HTML 能解析出作品", v is not None and v.no_watermark_url.endswith(".mp4"),
      f"url={getattr(v, 'no_watermark_url', '')[:40]}")
check("解析成功时不带失败原因", note == "", f"note={note}")
check("能抠出作品 ID", vid == "7166170264458382606", f"vid={vid}")

# C2 跳回首页 = 链接失效
dl._redirect_client = _FakeHttp(_Resp(200, HOME_HTML, "https://www.douyin.com/"))
v2, _vid2, note2 = dl._free_html_resolve("https://www.douyin.com/video/7166170264458382606")
check("跳回首页判定为失效", v2 is None and "跳回抖音首页" in note2, f"note={note2[:40]}")

# C3 非 200
dl._redirect_client = _FakeHttp(_Resp(500, "", "https://www.douyin.com/video/1"))
v3, _vid3, note3 = dl._free_html_resolve("https://www.douyin.com/video/1")
check("HTTP 500 返回失败原因", v3 is None and "页面HTTP 500" in note3, f"note={note3}")

# C4 网络异常不炸
dl._free_miss, dl._free_disabled = 0, False
dl._redirect_client = _FakeHttp(RuntimeError("boom"))
v4, _vid4, note4 = dl._free_html_resolve("https://www.douyin.com/video/1")
check("请求异常被兜住", v4 is None and "页面请求异常" in note4, f"note={note4}")

# C5 自适应短路：连续未命中后停用，别拿批量导入的速度去换不稳定的省钱
dl._free_miss, dl._free_disabled = 0, False
dl._redirect_client = _FakeHttp(_Resp(500, "", "https://www.douyin.com/video/1"))
for _ in range(dl.FREE_MISS_LIMIT):
    dl._free_html_resolve("https://www.douyin.com/video/1")
check("连续未命中后自动停用免费通道", dl._free_disabled is True,
      f"miss={dl._free_miss}")

class _CountHttp(_FakeHttp):
    def get(self, url, headers=None):
        super().get(url, headers)
        return self.resp


counter = _CountHttp(_Resp(200, GOOD_HTML, PAGE_URL if "PAGE_URL" in dir() else "x"))
dl._redirect_client = counter
before = len(counter.urls)
_v5, _vid5, note5 = dl._free_html_resolve("https://www.douyin.com/video/1")
check("停用后不再发页面请求", len(counter.urls) == before, f"urls={len(counter.urls)}")
check("停用原因可识别", "本轮已停用" in note5, f"note={note5}")

# C6 又命中一次就立刻恢复
dl._free_disabled = False
dl._free_miss = 0
dl._redirect_client = _FakeHttp(_Resp(200, GOOD_HTML,
                                      "https://www.douyin.com/video/7166170264458382606"))
v6, _vid6, _n6 = dl._free_html_resolve("https://www.douyin.com/video/7166170264458382606",
                                       desktop=True)
check("命中后恢复免费通道", v6 is not None and dl._free_disabled is False)


# ======================================================================
#  D. 解析顺序：免费优先，API 兜底
# ======================================================================
print("\n=== D. 解析顺序（免费优先 / API 兜底）===")

VID = "7166170264458382606"
PAGE_URL = f"https://www.douyin.com/video/{VID}"


def _api_ok(client_self=None):
    return ApiResponse(
        success=True, code=0, message="",
        data={"aweme_detail": {
            "aweme_id": VID, "desc": "来自接口",
            "video": {"play_addr": {"url_list": ["https://api.douyinvod.com/api.mp4"]}},
            "author": {"nickname": "作者"}, "statistics": {"digg_count": 1}}},
        raw_json={"code": 0})


class _CountingClient(_FakeClient):
    def request(self, platform, endpoint, params=None):
        super().request(platform, endpoint, params)
        return _api_ok()


# D1 视频页 + HTML 成功 → 一次接口都不该打
c1 = _CountingClient()
dl2 = VideoDownloader(c1, save_dir=_SAVE)
dl2._redirect_client = _FakeHttp(_Resp(200, GOOD_HTML, PAGE_URL))
got, reason = dl2._douyin_resolve_impl(PAGE_URL)
check("视频页走免费通道解析成功", got is not None and got.no_watermark_url.endswith(".mp4"),
      f"reason={reason[:40]}")
check("★ 免费通道命中时零 API 调用", c1.calls == 0, f"calls={c1.calls}")

# D2 视频页 + HTML 失败 → 必须回落到接口
c2 = _CountingClient()
dl3 = VideoDownloader(c2, save_dir=_SAVE)
dl3._redirect_client = _FakeHttp(_Resp(500, "", PAGE_URL))
got2, reason2 = dl3._douyin_resolve_impl(PAGE_URL)
check("HTML 失败时回落到接口", got2 is not None and c2.calls >= 1,
      f"calls={c2.calls}, reason={reason2[:40]}")

# D3 纯作品 ID 也先走免费通道
c3 = _CountingClient()
dl4 = VideoDownloader(c3, save_dir=_SAVE)
dl4._redirect_client = _FakeHttp(_Resp(200, GOOD_HTML, PAGE_URL))
got3, _r3 = dl4._douyin_resolve_impl(VID)
check("纯 ID 输入也先走免费通道", got3 is not None and c3.calls == 0, f"calls={c3.calls}")

# D4 免费通道失效（跳首页）→ 仍要回落接口，行为与旧版一致
c4 = _CountingClient()
dl5 = VideoDownloader(c4, save_dir=_SAVE)
dl5._redirect_client = _FakeHttp(_Resp(200, HOME_HTML, "https://www.douyin.com/"))
got4, _r4 = dl5._douyin_resolve_impl(PAGE_URL)
check("跳首页后仍回落接口兜底", c4.calls >= 1, f"calls={c4.calls}")

# D5 短链：本地跟随后拿到落地页，同样先走免费通道
def _fake_short(self, url):
    return PAGE_URL


dl6_client = _CountingClient()
dl6 = VideoDownloader(dl6_client, save_dir=_SAVE)
dl6._redirect_client = _FakeHttp(_Resp(200, GOOD_HTML, PAGE_URL))
dl6.resolve_short_link = types.MethodType(_fake_short, dl6)   # type: ignore[assignment]
got5, _r5 = dl6._douyin_resolve_impl("https://v.douyin.com/AbCdEf/")
check("短链解析走免费通道", got5 is not None and dl6_client.calls == 0,
      f"calls={dl6_client.calls}")


# ======================================================================
#  E. 批量导入：付费额度预检闸门已拆除 + 失败原因透明化（v1.22.0）
# ======================================================================
# 2026-10-22 客户现场：导入 192 条链接被「今天的额度只剩 30 次，大约只能
# 完成前 30 条」的预检弹窗拦下，客户只能手动删到 30 条；而跑起来的 30 条里
# 20 条失败，显示的是「详情接口无结果」—— 真因是接口欠费（601）。
# 本节把新契约钉住：下载页永不因额度预检拦人；失败原因必须说人话。
print("\n=== E. 批量导入额度预检已拆除 / 失败原因透明化 ===")
import inspect                                                   # noqa: E402

from gui_qt.pages.video_download_page import VideoDownloadPage    # noqa: E402
from justoneapi_collector.video_downloader import VideoDownloader  # noqa: E402
from justoneapi_collector.video_link_import import resolve_links  # noqa: E402


def _code_only(src: str) -> str:
    """去掉整行注释 —— 「注释里提到旧名字」不该被误判成还在调用。"""
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))


# —— E1 闸门方法本身已不存在 ——
check("预检闸门 _quota_confirmed 已删除",
      not hasattr(VideoDownloadPage, "_quota_confirmed"))

# —— E2 _start_batch_resolve 不再拿额度拦人 ——
_src_start = _code_only(inspect.getsource(VideoDownloadPage._start_batch_resolve))
check("批量导入不再调用 _quota_confirmed", "_quota_confirmed" not in _src_start)
check("批量导入不再读 quota.summary 做预检", "quota.summary" not in _src_start)
check("批量导入改为事后算账（记 _import_quota_before）",
      "_import_quota_before" in _src_start)

# —— E3 结果里给交代：花了多少额度 + 引导登录 ——
_src_done = inspect.getsource(VideoDownloadPage._on_batch_resolved)
check("失败弹窗引导去「平台登录」", "平台登录" in _src_done)
check("付费档结果里算接口兜底消耗", "接口兜底消耗" in _src_done)

# —— E4 接口失败原因透明化（功能测试：假 client 返各种业务码）——
class _CodeClient:
    """按给定业务码返回的假客户端，记录调用次数。"""

    def __init__(self, code, message="", data=None):
        self.code = code
        self.message = message
        self.data = data
        self.calls = 0

    def request(self, platform, endpoint, params=None):
        self.calls += 1
        ok = self.code == 0
        return ApiResponse(success=ok, code=self.code, message=self.message,
                           data=self.data if ok else None,
                           raw_json={"code": self.code, "message": self.message,
                                     "data": self.data if ok else None})


c601 = _CodeClient(601, "INSUFFICIENT BALANCE")
dl601 = VideoDownloader(c601, save_dir=_SAVE)
dl601._free_tier_blocked = lambda: False               # 模拟付费档
check("接口欠费时取不到详情", dl601.get_video_detail("7695449147242974527") is None)
check("欠费原因说人话（余额不足 + 联系作者）",
      "余额不足" in dl601._last_detail_reason
      and "联系作者" in dl601._last_detail_reason,
      dl601._last_detail_reason)
check("欠费后不再把 5 个参数变体全跑一遍", c601.calls == 1, f"calls={c601.calls}")

cq = _CodeClient(-9001, "今日调用额度已用完")
dlq = VideoDownloader(cq, save_dir=_SAVE)
dlq._free_tier_blocked = lambda: False
dlq.get_video_detail("7695449147242974527")
check("额度用完的原因说人话（0 点重置）",
      "0 点" in dlq._last_detail_reason, dlq._last_detail_reason)

c400 = _CodeClient(400, "param [videoId] can not be empty")
dl400 = VideoDownloader(c400, save_dir=_SAVE)
dl400._free_tier_blocked = lambda: False
dl400.get_video_detail("7695449147242974527")
check("参数错误仍要把变体试完（换个名字也许能救）", c400.calls == 5,
      f"calls={c400.calls}")

cempty = _CodeClient(0, "", data={"code": 0})
dle = VideoDownloader(cempty, save_dir=_SAVE)
dle._free_tier_blocked = lambda: False
dle.get_video_detail("7695449147242974527")
check("接口通了但没数据 → 说明可能已删除",
      "已删除" in dle._last_detail_reason, dle._last_detail_reason)

check("_is_final_api_failure：601/429/500 不再重试，400 还要换参数",
      VideoDownloader._is_final_api_failure(_CodeClient(601, "")) is True
      and VideoDownloader._is_final_api_failure(_CodeClient(429, "")) is True
      and VideoDownloader._is_final_api_failure(_CodeClient(500, "")) is True
      and VideoDownloader._is_final_api_failure(_CodeClient(400, "")) is False)

# —— E5 免费通道容错限值放宽（3 会把整批打死）——
check("免费通道连续未命中限值 ≥ 6",
      VideoDownloader.FREE_MISS_LIMIT >= 6,
      f"={VideoDownloader.FREE_MISS_LIMIT}")

# —— E6 批量解析节流（防抖音风控页）——
_src_links = inspect.getsource(resolve_links)
check("批量解析每条之间节流",
      "time.sleep" in _src_links and "_BATCH_PACE" in _src_links)


# ======================================================================
print(f"\n{'=' * 60}")
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for name in FAIL:
        print(f"  ✗ {name}")
    raise SystemExit(1)
print("ALL_COST_RESOLVE_CHECKS_PASS")
