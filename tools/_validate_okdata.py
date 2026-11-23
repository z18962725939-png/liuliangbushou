"""门禁：okdata 付费通道接入正确性与 zacao 免费链路隔离。

全部离线执行，不发真实请求、不花一分钱。挂到 release_all.py 发版链。
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from justoneapi_collector.models import ApiResponse  # noqa: E402
from justoneapi_collector.okdata_client import (  # noqa: E402
    ENDPOINT_MAP, PARAM_ALIAS, PLATFORM_ALIAS, OkdataClient, _translate,
)
_query = OkdataClient._query
from justoneapi_collector.live_monitor import LiveMonitor  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'OK' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))


class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeHttp:
    """记录调用次数；未映射接口若发请求会立刻暴露。"""
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, params: dict | None = None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            raise AssertionError("测试未准备响应却发起了请求")
        return self.responses.pop(0)


def _raises_value_error() -> bool:
    try:
        OkdataClient("")
    except ValueError:
        return True
    return False


def _config_writable() -> bool:
    c = OkdataClient("okd_test")
    try:
        c.config.timeout = 99
        c.config.max_retries = 2
        return c.config.timeout == 99
    finally:
        c.close()


def make_client(payload: dict, status: int = 200, retries: int = 0) -> tuple[OkdataClient, FakeHttp]:
    c = OkdataClient("okd_test_key", max_retries=retries)
    http = FakeHttp([FakeResponse(status, payload)])
    http.headers = c._client.headers      # type: ignore[attr-defined]
    c._client = http                      # type: ignore[assignment]
    return c, http


# ------------------------------------------------------------------ 映射
check("平台别名：抖音老 slug 归一",
      PLATFORM_ALIAS.get("douyin-tiktok-china") == "douyin")
check("平台别名：小红书老 slug 归一",
      PLATFORM_ALIAS.get("xiaohongshu-rednote") == "xiaohongshu")
check("平台别名：视频号老 slug 归一",
      PLATFORM_ALIAS.get("wechat-channels") == "wechat_channels")
for platform, endpoint, expected in [
    ("douyin-tiktok-china", "user-profile-v3", "douyin.users.get"),
    ("douyin-tiktok-china", "user-published-videos-v1", "douyin.users.videos.list"),
    ("douyin", "video-comments-v1", "douyin.videos.comments.list"),
    ("douyin", "comment-replies-v1", "douyin.comments.replies.list"),
    ("xiaohongshu-rednote", "note-search-v2", "xiaohongshu.notes.search"),
    ("kuaishou", "user-published-videos-v2", "kuaishou.users.videos.list"),
    ("wechat-channels", "live-user-status-v1", "wechat_channels.live.users.get"),
]:
    got = OkdataClient.capability_for(platform, endpoint)
    check(f"接口映射 {platform}/{endpoint} → {expected}", got == expected, str(got))

check("映射表无空值", all(isinstance(v, str) and v.count(".") >= 2 for v in ENDPOINT_MAP.values()))

# ★ 视频详情接口刻意不映射：okdata 的 Post 不含播放直链，映射过去就是白花钱。
for platform, endpoint in [
    ("douyin-tiktok-china", "video-details-v2"),
    ("douyin-tiktok-china", "video-details-v1"),
    ("xiaohongshu-rednote", "note-details-v6"),
    ("kuaishou", "video-details-v2"),
    ("tiktok", "post-details-v1"),
    ("douyin-tiktok-china", "share-link-resolution-v1"),
]:
    check(f"视频直链类接口不走 okdata：{platform}/{endpoint}",
          OkdataClient.capability_for(platform, endpoint) is None)

# ------------------------------------------------------------------ 参数
check("参数别名：secUid → id", PARAM_ALIAS.get("secUid") == "id")
check("参数别名：awemeId → id", PARAM_ALIAS.get("awemeId") == "id")
check("参数别名：keyword → query", PARAM_ALIAS.get("keyword") == "query")
q = _query({"secUid": "MS4wLjABAAAA", "count": 20, "cursor": 0, "pageSize": 20})
check("参数过滤：游标首页不传、无关参数丢弃", q == {"id": "MS4wLjABAAAA"}, str(q))
q2 = _query({"userId": "abc", "maxCursor": "c1.next", "view": "standard"})
check("参数转换：翻页游标原样回传", q2 == {"id": "abc", "cursor": "c1.next", "view": "standard"}, str(q2))
q3 = _query({"keyword": ["露营", "装备"]})
check("参数转换：列表值逗号拼接", q3 == {"query": "露营,装备"}, str(q3))

# ------------------------------------------------------------------ 响应翻译
post = {
    "id": "7436185237401447690", "text": "露营装备清单 #户外", "title": None,
    "url": "https://www.douyin.com/video/7436185237401447690",
    "author": {"id": "MS4wLjABAAAA", "name": "轻装徒步", "followers": 182300},
    "metrics": {"likes": 48200, "comments": 1312, "shares": 2210, "views": None},
    "images": ["https://cdn.example.com/c.jpg"], "published_at": "2026-11-02T09:30:00Z",
}
raw_list = {
    "code": "ok", "data": {"items": [post]},
    "page": {"next_cursor": "c1.k3Vq", "has_more": True, "returned": 1},
    "meta": {"billing": {"amount": "0.002", "currency": "USD"}},
}
d = _translate("douyin.users.videos.list", raw_list)
for key in ("items", "feeds", "notes", "videoList", "aweme_list", "list", "data"):
    check(f"列表别名存在：{key}", key in d)
check("列表首条补老字段 aweme_id", d["feeds"][0].get("aweme_id") == post["id"])
check("列表首条补老字段 digg_count", d["feeds"][0].get("digg_count") == 48200)
check("列表首条补老字段 nickname", d["feeds"][0].get("nickname") == "轻装徒步")
check("列表首条补封面", d["feeds"][0].get("cover") == post["images"][0])
check("分页游标映射 max_cursor", d.get("max_cursor") == "c1.k3Vq")
check("分页 has_more 映射", d.get("has_more") is True)
check("计费信息保留", d.get("_billing", {}).get("amount") == "0.002")

account = {
    "id": "MS4wLjABAAAA", "name": "轻装徒步", "username": "qingzhuang",
    "followers": 182300, "posts": 486, "likes": 2310000,
    "bio": "走远路", "avatar_url": "https://cdn.example.com/a.jpg",
}
a = _translate("douyin.users.get", {"code": "ok", "data": {"item": account}})["item"]
check("账号别名 nickname", a.get("nickname") == "轻装徒步")
check("账号别名 follower_count", a.get("follower_count") == 182300)
check("账号别名 aweme_count", a.get("aweme_count") == 486)
check("账号别名 total_favorited", a.get("total_favorited") == 2310000)
check("账号别名 user.userid", a.get("user", {}).get("userid") == "MS4wLjABAAAA")

live = {
    "id": "1214317309080180172", "status": "live", "title": "周末露营",
    "host": {"id": "v2_xxx@finder", "name": "轻装徒步"},
    "metrics": {"total_viewers": 174694, "likes": 110443},
}
l = _translate("wechat_channels.live.users.get", {"code": "ok", "data": {"item": live}})["item"]
check("直播状态 live → live_status=1", l.get("live_status") == 1)
check("直播状态 live → is_live", l.get("is_live") is True)
check("直播间 ID 映射 room_id", l.get("room_id") == live["id"])
check("主播名映射 nickname", l.get("nickname") == "轻装徒步")
off = _translate("wechat_channels.live.users.get",
                 {"code": "ok", "data": {"item": {"status": "offline", "id": None}}})["item"]
check("直播状态 offline → live_status=0", off.get("live_status") == 0)
check("直播状态 offline → is_live=False", off.get("is_live") is False)

comment = {
    "id": "c1", "text": "好内容", "created_at": "2026-11-20T08:00:00Z",
    "author": {"id": "u1", "name": "观众"}, "metrics": {"likes": 12},
}
c = _translate("douyin.videos.comments.list", {"code": "ok", "data": {"items": [comment]}})["comments"][0]
check("评论内容别名 content", c.get("content") == "好内容")
check("评论点赞别名 digg_count", c.get("digg_count") == 12)

# ------------------------------------------------------------------ 调用与错误
c, http = make_client(raw_list)
resp = c.request("douyin-tiktok-china", "user-published-videos-v1", {"secUid": "abc"})
check("成功响应 success=True", resp.success is True)
check("请求 URL 正确", http.calls[0][0].endswith("/v1/douyin/users/videos/list"), http.calls[0][0])
check("请求带转换后的 id 参数", http.calls[0][1] == {"id": "abc"}, str(http.calls[0][1]))
check("Authorization 头用 Bearer", c._client.headers.get("Authorization") == "Bearer okd_test_key")

c, http = make_client({"code": "invalid_params", "message": "bad", "charged": False}, status=400)
resp = c.request("douyin", "not-mapped-endpoint", {})
check("未映射接口明确失败", resp.success is False and resp.code == "UNSUPPORTED")
check("未映射接口不发请求（不扣费）", not http.calls)

c, http = make_client({"code": "unauthorized", "charged": False}, status=401)
resp = c.request("douyin", "user-profile-v3", {"secUid": "abc"})
check("401 失败", resp.success is False)
check("401 标记 Key 无效", c.token_invalid is True)
check("401 提示去设置页改 Key", "设置页" in resp.message)

c, http = make_client({"code": "insufficient_balance", "charged": False}, status=402)
resp = c.request("douyin", "user-profile-v3", {"secUid": "abc"})
check("402 失败", resp.success is False)
check("402 提示 okdata 余额不足", "okdata 余额不足" in resp.message)

c, http = make_client({"code": "ok", "data": {}}, status=200)
resp = c.request("douyin", "user-profile-v3", {"secUid": "abc"})
check("200 但 data 为空时不崩", resp.success is True and isinstance(resp.data, dict))

check("无 Key 构造抛错", _raises_value_error())
check("config 对象可写（设置页保存不炸）", _config_writable())
check("充值提示指向 okdata", "okdata" in OkdataClient.recharge_hint)
check("provider 标识为 okdata", OkdataClient.provider == "okdata")

# ------------------------------------------------------------------ 免费链路隔离
mp_src = (ROOT / "justoneapi_collector" / "media_parser.py").read_text(encoding="utf-8")
check("zacao 免费解析地址未变", 'BASE_URL = "https://video.zacao.top"' in mp_src)
check("zacao 解析模块不引用 okdata", "okdata" not in mp_src.lower())

base_src = (ROOT / "gui_qt" / "pages" / "_base.py").read_text(encoding="utf-8")
tree = ast.parse(base_src)
defaults = None
for node in ast.walk(tree):
    if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_SETTINGS_DEFAULTS":
        defaults = ast.literal_eval(node.value)
        break
check("设置默认值 provider=okdata", bool(defaults) and defaults.get("api_provider") == "okdata")
check("设置默认值含 okdata_key", bool(defaults) and defaults.get("okdata_key") == "")
check("默认仍保留 JustOneAPI 地址", bool(defaults) and "justoneapi.com" in defaults.get("base_url", ""))
check("connect_real_client 动态导入 okdata", "from justoneapi_collector.okdata_client import OkdataClient" in base_src)
check("okdata 通道不套 JustOneAPI 额度闸门",
      "不套 JustOneAPI 授权码额度闸门" in base_src)

set_src = (ROOT / "gui_qt" / "pages" / "settings_page.py").read_text(encoding="utf-8")
check("设置页有服务商下拉", "provider_combo.addItem" in set_src and "justoneapi" in set_src)
check("设置页有 okdata Key 输入框", "okdata_key_edit" in set_src)
check("设置页保存 provider/key", 's["api_provider"]' in set_src and 's["okdata_key"]' in set_src)
check("设置页说明视频走 zacao 免费通道", "zacao" in set_src)

spec_src = (ROOT / "justoneapi_collector.spec").read_text(encoding="utf-8")
check("打包配置登记 okdata_client", "justoneapi_collector.okdata_client" in spec_src)

lm_src = (ROOT / "justoneapi_collector" / "live_monitor.py").read_text(encoding="utf-8")
check("直播监控 okdata 通道直走免费 CDP", 'getattr(self.client, "provider", "") == "okdata"' in lm_src)
check("直播监控充值提示按通道变化", "recharge_hint" in lm_src)

# ---------------------------------------------------- okdata 下不花付费查直播
class _OkdataStub:
    provider = "okdata"
    token_invalid = False
    calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("okdata 通道查开播状态不该走付费接口")


class _Snap:
    ok = True
    live_status = 1
    room_id = "1234567890123456789"
    web_rid = "870887192950"
    nickname = "央视网"
    error = ""


class _HomeCDP:
    def fetch(self, sec_uid):
        return _Snap()


dm = LiveMonitor(_OkdataStub(), save_dir=Path(tempfile.mkdtemp(prefix="okdata_live_")))
dm._save_state = lambda: None            # type: ignore[method-assign]
dm.home_cdp = lambda: _HomeCDP()          # type: ignore[method-assign]
uid = "MS4wLjABAAAAi7XpOpFncGmD6WPBbdB2lAR9R3rI51OX30NzzDS6JOorE4fbzTA3AwuQeORg1tEp"
st = dm.check_once(uid)
check("okdata 直播检查零付费调用", dm.client.calls == 0)
check("okdata 直播检查识别在播", st is not None and st.is_live is True)
check("okdata 直播检查回填 web_rid", st is not None and st.web_rid == "870887192950")
check("okdata 直播检查无错误提示", st is not None and not st.stream_error)

print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for name in FAIL:
        print("  ✗", name)
    sys.exit(1)
