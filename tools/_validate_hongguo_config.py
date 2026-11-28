# -*- coding: utf-8 -*-
"""门禁：红果免费直连配置模块移植正确性（纯标准库，不联网、不引 Qt）。

锚定 justoneapi_collector/hongguo_config.py 的部署配置与纯算法：
  · 配置常量与 PhoenixReadApiOptions 边界
  · 榜单参数 / 播放请求体 / 分页去重
  · 播放地址解码（明文/Base64/加密封包校验）
  · CENC 内容密钥解码（v1 已知样例 + 异常路径）
  · 清晰度打分 / 媒体筛选 / ffmpeg 命令（含 -decryption_key 在 -i 之前）
  · 签名层边界声明（必须有 signing_required，且不能出现伪造签名的实现）
  · 合规声明（非官方 / 仅供学习研究 / 版权授权）

跑法：python tools/_validate_hongguo_config.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


from justoneapi_collector import hongguo_config as hc  # noqa: E402

print("\n== 1. 部署配置常量 ==")
check("App 主接口地址正确", hc.APP_BASE_URI == "https://api5-normal-sinfonlinea.fqnovel.com/")
check("Web 兜底地址正确", hc.WEB_BASE_URI == "https://hongguoduanju.com/")
check("榜单 cell_id 正确", hc.LEADERBOARD_CELL_ID == "7470092475068071998")
check("热播榜 ID 正确", hc.LEADERBOARD_BOARD_IDS["hot_play"] == "ranklist_hot_play_sc")
check("新剧榜 ID 正确", hc.LEADERBOARD_BOARD_IDS["new_release"] == "ranklist_new_rank_sc")
check("男频频道 ID 正确", hc.LEADERBOARD_CHANNEL_IDS["male"] == "gender_male")
check("总榜不传二级频道", hc.LEADERBOARD_CHANNEL_IDS["all"] is None)
check("播放 V5 路径齐全（3 条）", len(hc.PLAYBACK_PATHS) == 3 and
      all("/multi_video_model/" in p for p in hc.PLAYBACK_PATHS))
check("video_tab 回退地址存在", "video_tab" in hc.VIDEO_TAB_URL)
check("Referer 固定正确", hc.PLAYBACK_REFERER == "https://novel.snssdk.com/")
check("设备画像锁定（aid/版本）",
      hc.DEVICE_PROFILE["aid"] == 8662 and hc.DEVICE_PROFILE["version_code"] == 73932)
check("URL 解码 64 字节常量完整", len(hc._URL_SEED_CONSTANTS) == 64 and
      hc._URL_SEED_CONSTANTS[0] == 0x4D and hc._URL_SEED_CONSTANTS[-1] == 0x58)

print("\n== 2. Options 边界 ==")
o = hc.PhoenixOptions()
check("默认超时 20s", o.request_timeout == 20.0)
check("默认重试 2 次", o.retry_count == 2)
for kw, label in (({"retry_count": 0}, "重试次数下限"),
                  ({"retry_count": 4}, "重试次数上限"),
                  ({"max_response_bytes": 1024}, "响应体下限"),
                  ({"max_pages_per_category": 0}, "页数下限")):
    try:
        hc.PhoenixOptions(**kw)
        check(f"{label}越界报错", False, "没有抛 ValueError")
    except ValueError:
        check(f"{label}越界报错", True)

print("\n== 3. 榜单参数 / 分页去重 ==")
p0 = hc.build_leaderboard_params("hot_play", "all", 0)
check("首页 offset=0 且分页状态=1", p0["offset"] == 0 and
      p0["unlimited_selector_change_type"] == 1)
check("总榜无 sub_selected_items", "sub_selected_items" not in p0)
check("limit=0（服务端决定）", p0["limit"] == 0)
p1 = hc.build_leaderboard_params("hot_play", "male", 20, session_id="S1",
                                 seen_ids=("a", "b", "a"))
check("续页分页状态=2", p1["unlimited_selector_change_type"] == 2)
check("男频带二级频道", p1.get("sub_selected_items") == "gender_male")
check("session_id 透传", p1.get("session_id") == "S1")
check("seen_ids 去重保序", p1.get("filter_ids") == "a,b")
try:
    hc.build_leaderboard_params("not_exist", "all")
    check("未知榜单报错", False)
except ValueError:
    check("未知榜单报错", True)

cur = hc.LeaderboardCursor(max_pages=5)
fresh1 = cur.consume([{"Id": "1"}, {"Id": "2"}, {"Id": "2"}],
                     next_offset=20, has_more=True, session_id="SS")
check("同页去重且保留新增", len(fresh1) == 2 and cur.offset == 20 and
      cur.session_id == "SS")
fresh2 = cur.consume([{"Id": "2"}, {"Id": "3"}], next_offset=10,
                     has_more=True)
check("跨页去重 + 回退 offset 不倒退", len(fresh2) == 1 and cur.offset == 20)
check("页数上限生效", hc.LeaderboardCursor(max_pages=2).exhausted is False)

print("\n== 4. 播放地址解码 ==")
url = "https://example.com/a.mp4"
check("明文 URL 直返", hc.decode_playback_url(url, None) == url)
import base64 as _b64
check("Base64 包装 URL 可解",
      hc.decode_playback_url(_b64.b64encode(url.encode()).decode(), None) == url)
check("空串返回空", hc.decode_playback_url("", None) == "" and
      hc.decode_playback_url(None, None) == "")
check("非法 Base64 返回空（不抛）", hc.decode_playback_url("!!!!", None) == "")
check("加密封包缺 keySeed 返回空",
      hc.decode_playback_url(_b64.b64encode(bytes([0xA8, 0, 1, 0]) + b"0" * 16).decode(), None) == "")

print("\n== 5. CENC 内容密钥解码 ==")
try:
    hc.decode_content_key("x" * 1025)
    check("超长密钥报错", False)
except ValueError:
    check("超长密钥报错", True)
for bad in ("!!!!", "AQI=", ""):
    try:
        hc.decode_content_key(bad)
        check(f"非法密钥 {bad[:6]!r} 报错", False)
    except ValueError:
        check(f"非法密钥 {bad[:6]!r} 报错", True)

# v1 已知样例：按 C# 解码的严格逆变换构造合法 spadeA，验证能还原 16 字节密钥
def _make_v1_key(key_hex: str) -> str:
    # C# 结构：len=38, num=2, num2=35；array3=a[1:36]（35字节），
    # 解码后应为 '2'(base36=2) + 32位hex + 2填充。
    plain = [ord("2")] + [ord(c) for c in key_hex] + [ord("X"), ord("X")]
    term = lambda j: (-(0x15 + bin(j).count("1"))) & 0xFF
    enc = bytearray(35)
    cur, prev = 0xFA, 0x55
    for j, val in enumerate(plain):
        if j % 2 == 0:
            enc[j] = cur ^ ((val - term(j)) & 0xFF)
            cur = enc[j]
        else:
            enc[j] = prev ^ ((val - term(j)) & 0xFF)
            prev = enc[j]
    # num = a[0]^a[1]^a[2]-48 = 2  ⇒ a[0]^enc[0]^enc[1] = 50
    a0 = 50 ^ enc[0] ^ enc[1]
    buf = bytes([a0]) + bytes(enc) + b"ab"     # 1 + 35 + 2 = 38
    return _b64.b64encode(buf).decode()

sample_key = "0123456789abcdef0123456789abcdef"
spade = _make_v1_key(sample_key)
try:
    got = hc.decode_content_key(spade)
    check("v1 密钥样例还原正确", got == sample_key, f"得到 {got}")
except ValueError as exc:
    check("v1 密钥样例还原正确", False, f"抛错 {exc}")

print("\n== 6. 媒体筛选 / ffmpeg 命令 ==")
low = hc.PlaybackMedia(url="https://x/low.mp4", height=720, codec="H264")
high = hc.PlaybackMedia(url="https://x/high.mp4", height=1080, codec="H264")
bad = hc.PlaybackMedia(url="https://x/bad.mp4", height=2160, codec="bytevc2")
check("quality_score 公式正确",
      high.quality_score == 1080 * 10 + 1 and low.quality_score == 720 * 10 + 1)
check("跳过 bytevc2 且选最高画质",
      hc.select_best_media([low, bad, high]) is high)
check("全不可用时返回 None",
      hc.select_best_media([bad]) is None)
enc = hc.PlaybackMedia(url="https://x/a.mp4", decryption_key="a" * 32,
                       referer=hc.PLAYBACK_REFERER)
cmd = hc.build_ffmpeg_command(enc, "/tmp/out.mp4")
check("ffmpeg 含 -decryption_key", "-decryption_key" in cmd and cmd[cmd.index("-decryption_key") + 1] == "a" * 32)
check("-decryption_key 在 -i 之前", cmd.index("-decryption_key") < cmd.index("-i"))
check("ffmpeg 带 Referer 头", "Referer: https://novel.snssdk.com/" in cmd[cmd.index("-headers") + 1])
check("ffmpeg 输出路径正确", cmd[-1] == "/tmp/out.mp4")

print("\n== 7. 签名边界与合规声明 ==")
src = open(os.path.join(ROOT, "justoneapi_collector", "hongguo_config.py"),
           encoding="utf-8").read()
check("提供签名层提示函数",
      callable(getattr(hc, "signing_required", None)) and
      isinstance(hc.signing_required(), str) and len(hc.signing_required()) > 10)
check("签名提示点名 x-gorgon", "x-gorgon" in hc.signing_required())
check("未伪造签名实现", "def sign_x_gorgon" not in src and "def sign_x_argus" not in src)
check("合规声明含非官方", "非官方" in src)
check("合规声明含仅供学习研究", "仅供个人学习" in src)
check("合规声明含版权授权", "版权" in src and "授权" in src)

print("\n== 8. 新剧推荐专区参数（手册 4.3） ==")
nd = hc.build_new_drama_params()
check("新剧 cell_id 正确", nd["cell_id"] == "7431550523368554558")
check("新剧 book_type/change_type 固定",
      nd["book_type"] == 0 and nd["change_type"] == 1)
check("新剧 client_req_type 固定", nd["client_req_type"] == 0)
check("新剧 selected_items 固定", nd["selected_items"] == "firstonlinetime_new")
check("新剧默认总榜 gender=2", nd["cell_gender"] == 2)
nd2 = hc.build_new_drama_params(0, 10, 50, session_id="SX")
check("新剧女频/分页/上限透传",
      nd2["cell_gender"] == 0 and nd2["offset"] == 10 and nd2["limit"] == 50
      and nd2["session_id"] == "SX")
check("新剧默认不带 session_id", "session_id" not in nd)
for kw, label in (({"gender": 3}, "性别越界"),
                  ({"gender": -1}, "性别负数"),
                  ({"offset": -1}, "offset 负数"),
                  ({"limit": 0}, "limit 下限"),
                  ({"limit": 51}, "limit 上限"),
                  ({"session_id": 123}, "session_id 类型")):
    try:
        hc.build_new_drama_params(**kw)
        check(f"新剧 {label} 报错", False, "没有抛 ValueError")
    except ValueError:
        check(f"新剧 {label} 报错", True)

print("\n== 9. x-ss-stub（手册 5.2） ==")
import hashlib as _hashlib
gz = hc.build_playback_body("vid-123")
check("x_ss_stub 是 32 位大写十六进制",
      len(hc.x_ss_stub(gz)) == 32 and hc.x_ss_stub(gz) == hc.x_ss_stub(gz).upper())
check("x_ss_stub 等于 gzip body 的 MD5 大写",
      hc.x_ss_stub(gz) == _hashlib.md5(gz).hexdigest().upper())
check("x_ss_stub 不同 body 不同",
      hc.x_ss_stub(gz) != hc.x_ss_stub(hc.build_playback_body("vid-456")))
for bad in (b"", "not-bytes"):
    try:
        hc.x_ss_stub(bad)
        check(f"x_ss_stub 非法输入 {bad!r} 报错", False)
    except ValueError:
        check(f"x_ss_stub 非法输入 {bad!r} 报错", True)

print("\n== 10. 多来源媒体收集与同档位密钥配对（手册 5.3 / 5.6） ==")
_u = lambda s: _b64.b64encode(s.encode()).decode()
srcs = hc.MEDIA_URL_SOURCES
tier_all = {name: _u(f"https://cdn.test/{i}.mp4") for i, name in enumerate(srcs)}
cands = hc.collect_media_candidates({"streams": [tier_all]})
check("六个地址来源全部收集", len(cands) == 6, f"得到 {len(cands)}")
check("仅保留 HTTP(S) 且去重",
      hc.collect_media_candidates({"MainUrls": [_u("https://a/1.mp4"),
                                                _u("https://a/1.mp4"),
                                                _u("ftp://a/2.mp4")]})[0].url
      == "https://a/1.mp4" and
      len(hc.collect_media_candidates({"MainUrls": [_u("https://a/1.mp4"),
                                                    _u("https://a/1.mp4"),
                                                    _u("ftp://a/2.mp4")]})) == 1)
check("同档位备用地址不丢",
      cands[0].backup_url.startswith("https://cdn.test/"))
check("bytevc2 档位整体跳过",
      hc.collect_media_candidates({"streams": [
          {"MainUrls": [_u("https://x/4k.mp4")], "codec": "bytevc2"},
          {"MainUrls": [_u("https://x/hd.mp4")], "codec": "H264"},
      ]})[0].url == "https://x/hd.mp4")
check("bytevc2 单条地址跳过",
      hc.collect_media_candidates({"MainUrls": [
          _u("https://x/bytevc2.mp4"), _u("https://x/ok.mp4")]})[0].url
      == "https://x/ok.mp4")

key_a = "0123456789abcdef0123456789abcdef"
key_b = "fedcba9876543210fedcba9876543210"
raw_tiers = {"medias": [
    {"MainUrls": [_u("https://cdn.test/a.mp4")], "spadeA": _make_v1_key(key_a),
     "height": 720, "codec": "H264"},
    {"MainUrls": [_u("https://cdn.test/b.mp4")], "spadeA": _make_v1_key(key_b),
     "height": 1080, "codec": "H264"},
]}
paired = hc.collect_media_candidates(raw_tiers)
by_url = {m.url: m.decryption_key for m in paired}
check("两档位密钥各自配对不串档",
      by_url.get("https://cdn.test/a.mp4") == key_a and
      by_url.get("https://cdn.test/b.mp4") == key_b, f"得到 {by_url}")
check("候选按画质降序（首项最佳）",
      paired[0].url == "https://cdn.test/b.mp4")
check("无 spadeA 时密钥为空",
      hc.collect_media_candidates({"MainUrls": [_u("https://x/n.mp4")]})[0]
      .decryption_key == "")
check("非法 spadeA 不抛异常且密钥留空",
      hc.collect_media_candidates({"MainUrls": [_u("https://x/n.mp4")],
                                   "spadeA": "!!!!"})[0].decryption_key == "")
check("definition 标签换算高度",
      hc.collect_media_candidates({"MainUrls": [_u("https://x/p.mp4")],
                                   "definition": "P1080"})[0].height == 1080)
check("HLS/DASH 标记正确",
      hc.collect_media_candidates({"MainUrls": [_u("https://x/a.m3u8"),
                                                _u("https://x/b.mpd")]})
      and any(m.is_hls for m in hc.collect_media_candidates(
          {"MainUrls": [_u("https://x/a.m3u8")]})) and
      any(m.is_dash for m in hc.collect_media_candidates(
          {"MainUrls": [_u("https://x/b.mpd")]})))
check("非 dict 输入返回空列表", hc.collect_media_candidates(None) == [])

print("\n== 11. 集数多字段兜底（手册 4.4 / 4.6） ==")
dto = {"EpisodeCount": 12, "EpisodeCountAlias": "共80集",
       "DramaChapterNumber": 30, "ChapterNumber": 5,
       "VideoDetail": {"EpisodeCount": 88, "ChapterNumberAlias": "99"}}
check("多字段取最大值含 VideoDetail",
      hc.extract_episode_count(dto) == 99, f"得到 {hc.extract_episode_count(dto)}")
check("全为 0 时返回 0（需回查详情）",
      hc.extract_episode_count({"EpisodeCount": 0}) == 0)
check("非 dict 输入返回 0", hc.extract_episode_count([]) == 0)

print("\n== 12. 直链过期判断（手册 5.3 / 9.2） ==")
check("未到期不过期", hc.is_expired(2000, now_ts=1000) is False)
check("到点即过期", hc.is_expired(1000, now_ts=1000) is True)
check("已过期判真", hc.is_expired(500, now_ts=1000) is True)
check("期限缺失判真", hc.is_expired(0, now_ts=1000) is True)
media_exp = hc.PlaybackMedia(url="https://x/a.mp4", expires_at_unix_seconds=500)
check("PlaybackMedia.is_expired 透传",
      media_exp.is_expired(1000) is True and media_exp.is_expired(100) is False)

print("\n== 13. PhoenixOptions 补齐（手册第 7 / 10 节） ==")
check("默认无会话快照路径",
      hc.PhoenixOptions().authentication_state_file_path is None and
      hc.PhoenixOptions().anonymous_state_file_path is None)
auth_opt = hc.PhoenixOptions(authentication_state_file_path="C:/tmp/auth.json",
                             device_id="123456", install_id="987654")
check("单路径 + 纯数字 ID 合法",
      auth_opt.authentication_state_file_path == "C:/tmp/auth.json"
      and auth_opt.device_id == "123456")
for kw, label in (({"authentication_state_file_path": "a.json",
                    "anonymous_state_file_path": "b.json"}, "快照路径互斥"),
                  ({"authentication_state_file_path": ""}, "空路径"),
                  ({"device_id": "12a"}, "device_id 非纯数字"),
                  ({"install_id": 123}, "install_id 非字符串")):
    try:
        hc.PhoenixOptions(**kw)
        check(f"Options {label} 报错", False, "没有抛 ValueError")
    except ValueError:
        check(f"Options {label} 报错", True)
check("快照路径仅做路径校验不读写文件",
      "禁止入库或随日志输出" in src and "只做路径合法性校验" in src)

print("\n== 14. 分层异常体系（手册第 8 章） ==")
exc = hc.PhoenixReadApiHttpException("bad", operation="SearchAsync",
                                     request_uri="https://x/?sig=hidden",
                                     status_code=503,
                                     provider_code="E503",
                                     is_transient=True)
check("异常基类继承正确",
      isinstance(exc, hc.PhoenixReadApiException) and isinstance(exc, Exception))
check("异常上下文属性齐全",
      exc.operation == "SearchAsync" and exc.status_code == 503
      and exc.provider_code == "E503" and "bad" in str(exc))
check("显式 is_transient=True 才重试",
      exc.is_transient is True and
      hc.PhoenixReadApiHttpException("x").is_transient is False)
check("传输异常默认瞬态",
      hc.PhoenixReadApiTransportException("net").is_transient is True)
check("认证异常继承 Provider 异常且不重试",
      issubclass(hc.PhoenixReadApiAuthenticationException,
                 hc.PhoenixReadApiProviderException) and
      hc.PhoenixReadApiAuthenticationException("x").is_transient is False)
for cls_name in ("Response", "Deserialization", "Media", "Fallback"):
    cls = getattr(hc, f"PhoenixReadApi{cls_name}Exception")
    check(f"{cls_name} 异常可用", issubclass(cls, hc.PhoenixReadApiException)
          and cls("x").is_transient is False)
check("异常对象禁止入库/写日志声明", "禁止入库或写日志" in src)

print("\n== 15. 能力清单（手册第 6 章） ==")
check("能力清单是 frozenset", isinstance(hc.CAPABILITY_METHODS, frozenset))
check("核心能力齐全",
      {"SearchAsync", "GetDramaDetailsAsync", "ResolveMediaAsync",
       "GetDanmakuAsync", "GetDramaReviewsAsync", "GetHistoryAsync",
       "LoginAsync", "EnumerateShortDramaLeaderboardAsync"}
      <= hc.CAPABILITY_METHODS)
check("能力清单标注需外部签名",
      hc.CAPABILITIES_REQUIRE_EXTERNAL_SIGNING is True
      and "不实现真实请求" in hc.capability_note())

print(f"\n结果：{PASS} 通过 / {FAIL} 失败  →  {PASS}/{PASS + FAIL}")
sys.exit(1 if FAIL else 0)
