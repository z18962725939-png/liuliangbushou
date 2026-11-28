# -*- coding: utf-8 -*-
"""红果短剧「免费直连」协议配置与纯算法（Python 版）。

来源：PhoenixReadApi（LanZhan-Harmony/PhoenixReadApi，MIT）复用手册，
依据提交 5687ad5（2026-11-25）。本模块把该 C# SDK 的**部署配置层**与
**纯协议算法**移植到 Python，供本软件在「不花付费 Key」的前提下复用其
配置知识；不涉及 C# 运行时的部分一律用 Python 表达。

## 边界（务必读完再用）
1. **签名层不在此模块**：x-gorgon / x-argus / x-ladon / x-medusa / x-helios
   是 C# 侧约 60KB 的查表 + 字节变换算法，依赖设备注册与时钟同步。
   纯 Python 复刻需逐字节比对验证，非本次范围。本模块只提供
   ``signing_required()`` 提示：真正发请求前必须接入外部签名服务
   （.NET 微服务 / 已签名的本地代理），**禁止**把无签名的请求直接打到
   红果接口——必失败且会连累账号/设备指纹。
2. **能本地跑通的部分**：配置常量、榜单请求参数拼装、播放请求体拼装、
   播放地址解码、CENC 内容密钥解码、清晰度打分/媒体筛选、ffmpeg 命令
   拼装、分页去重游标、新剧推荐专区参数、x-ss-stub 摘要、多来源媒体候选
   收集与同档位密钥配对、集数多字段兜底提取、直链过期判断、分层异常、
   能力清单。这些是纯函数/纯类，配套门禁逐条验证。
3. **合规（手册第 10 节要求原样转达）**：PhoenixReadApi 是非官方开源客户端，
   仅供个人学习、技术研究、自动化测试；使用需遵守红果/字节的服务协议与
   法律法规，不得用于非法用途或商业竞争；批量下载/分发涉及版权，开发测试
   之外须先取得授权；会话快照含 Cookie/Token，禁止入库或写日志。

## 依赖
仅标准库；AES-CBC 解密在 ``pycryptodome``（import 名 Crypto）可用时才启用，
缺失时 decode_playback_url 对加密封包返回明确错误码，不影响其余纯函数。
"""
from __future__ import annotations

import base64
import hashlib
import json
import gzip
import time
from dataclasses import dataclass
from typing import Any, Iterable, Iterator

# ---------------------------------------------------------------- 部署配置

WEB_BASE_URI = "https://hongguoduanju.com/"
APP_BASE_URI = "https://api5-normal-sinfonlinea.fqnovel.com/"
AUTHENTICATION_BASE_URI = "https://api5-normal-hl.fqnovel.com/"

# 榜单接口（App 通道）
LEADERBOARD_PATH = "/reading/bookapi/bookmall/cell/change/v1/"
LEADERBOARD_CELL_ID = "7470092475068071998"
LEADERBOARD_TAB_TYPE = "26"
NEW_DRAMA_CELL_ID = "7431550523368554558"

# 新剧推荐专区（手册 4.3）：与榜单共用一个 endpoint，但 cell/参数固定不同
NEW_DRAMA_BOOK_TYPE = 0
NEW_DRAMA_CHANGE_TYPE = 1
NEW_DRAMA_CLIENT_REQ_TYPE = 0
NEW_DRAMA_SELECTED_ITEMS = "firstonlinetime_new"
NEW_DRAMA_GENDERS = (0, 1, 2)          # 0 女频 / 1 男频 / 2 总榜
NEW_DRAMA_LIMIT_MIN = 1
NEW_DRAMA_LIMIT_MAX = 50

# 媒体地址来源（手册 5.3）：同一档位内全部收集、解码、去重、仅留 HTTP(S)
MEDIA_URL_SOURCES = (
    "MainUrls", "BackupUrls", "BackupUrls1", "BackupUrls2",
    "BackupUrlCollection", "UrlList",
)
# 档位内 download_url 别名（手册 5.6 要求它与同档位 key 配对，不可跨档位）
MEDIA_URL_ALIASES = ("download_url", "DownloadUrl", "downloadUrl")

# 集数多字段兜底（手册 4.4 第 6 点 / 4.6）：取所有字段最大值；0 表示需回查详情
EPISODE_COUNT_FIELDS = (
    "EpisodeCount", "EpisodeCountAlias", "DramaChapterNumber", "ChapterNumber",
)

# definition 标签 → 高度（手册 5.3：definition 标签可换算为高度）
DEFINITION_HEIGHTS = {
    "p240": 240, "p360": 360, "p480": 480, "p540": 540,
    "p720": 720, "p1080": 1080, "p1440": 1440, "p2160": 2160,
    "p4k": 2160,
}

# 榜单 ID 映射（selected_items）
LEADERBOARD_BOARD_IDS = {
    "recommend": "ranklist_hot_sc",     # 推荐榜（个性化流，不保证稳定排名）
    "hot_play": "ranklist_hot_play_sc",  # 热播榜（前一日播放热度）
    "new_release": "ranklist_new_rank_sc",
    "hot_search": "ranklist_hot_search_sc",
    "celebrity": "ranklist_celebrity",
}
# 频道映射（sub_selected_items；总榜不传）
LEADERBOARD_CHANNEL_IDS = {
    "all": None,
    "female": "gender_female",
    "male": "gender_male",
}

# 播放接口 V5 路径 + video_tab 回退
PLAYBACK_PATHS = (
    "/novel/player/multi_video_model/player/v1",
    "/novel/player/multi_video_model/preload/v1",
    "/novel/player/multi_video_model/v1",
)
VIDEO_TAB_URL = (
    "https://reading.snssdk.com/reading/bookapi/video_tab/"
    "multi_video_model/v1/"
)
PLAYBACK_REFERER = "https://novel.snssdk.com/"

# 设备画像（来自 PhoenixReadDeviceProfile，锁定版本以保证签名可复现）
DEVICE_PROFILE = {
    "device_model": "WKL-AN20",
    "device_platform": "android",
    "os_version": "17",
    "rom_version": "CP2A.260605.012",
    "build_id": "CP2A.260605.012",
    "resolution": "1320*2868",
    "dpi": 360,
    "cpu_abi": "arm64-v8a",
    "access": "wifi",
    "aid": 8662,
    "app_name": "novelread",
    "version_code": 73932,
    "channel": "update_64",
}
USER_AGENT = (
    "com.phoenix.read/73932 (Linux; U; Android 17; zh_CN; WKL-AN20; "
    "Build/CP2A.260605.012; Cronet/TTNetVersion:8d40f833 2026-03-03 "
    "QuicVersion:21ac1950 2025-11-18)"
)
PLAYBACK_USER_AGENT = (
    "com.phoenix.read/73932 (Linux; U; Android 17; zh_CN_#Hans; WKL-AN20; "
    "Build/CP2A.260605.012; tt-ok/3.12.13.20)"
)

# URL 解码常量：原样保留，改一个字节就会解不开播放地址
_URL_SEED_CONSTANTS = bytes([
    0x4d, 0xd4, 0xc2, 0xe6, 0xb8, 0x31, 0x62, 0x09,
    0x0e, 0x52, 0xb3, 0xc7, 0xa6, 0x73, 0x3b, 0xa4,
    0x1c, 0xb2, 0x46, 0x2b, 0x82, 0x9a, 0xb5, 0x8a,
    0x19, 0x6b, 0x39, 0xdb, 0x57, 0x17, 0x75, 0x24,
    0xf4, 0x9b, 0xaf, 0x7f, 0x08, 0xe8, 0xd6, 0x8d,
    0x26, 0xa7, 0x2e, 0x37, 0xc1, 0xa9, 0x5a, 0x2f,
    0x1f, 0x05, 0xa5, 0x18, 0x92, 0xae, 0xf2, 0x94,
    0x97, 0x32, 0xb6, 0x2a, 0x38, 0xaa, 0xdd, 0x58,
])


@dataclass
class PhoenixOptions:
    """对应 C# 的 PhoenixReadApiOptions（可配置部署参数）。"""
    web_base_uri: str = WEB_BASE_URI
    app_base_uri: str = APP_BASE_URI
    authentication_base_uri: str = AUTHENTICATION_BASE_URI
    request_timeout: float = 20.0
    retry_count: int = 2          # 1–3，含首次
    max_response_bytes: int = 8 * 1024 * 1024
    enable_web_fallback: bool = True
    complete_search_seasons: bool = True
    max_pages_per_category: int = 50
    # 会话快照路径：含 Cookie / Passport Token / 短信票据等敏感凭据，
    # 禁止入库或随日志输出（手册第 10 节）；本模块只做路径合法性校验，
    # 绝不读写快照文件。两者互斥。
    authentication_state_file_path: str | None = None
    anonymous_state_file_path: str | None = None
    device_id: str | None = None  # 纯数字，跨进程持久化保持设备身份
    install_id: str | None = None
    user_agent: str = USER_AGENT

    def __post_init__(self):
        if not 1 <= self.retry_count <= 3:
            raise ValueError("retry_count 必须在 1–3")
        if not 16 * 1024 <= self.max_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("max_response_bytes 必须在 16KB–64MB")
        if not 1 <= self.max_pages_per_category <= 500:
            raise ValueError("max_pages_per_category 必须在 1–500")
        if self.authentication_state_file_path and self.anonymous_state_file_path:
            raise ValueError("登录会话快照与匿名设备快照路径互斥")
        for path in (self.authentication_state_file_path,
                     self.anonymous_state_file_path):
            if path is not None:
                _validate_state_path(path)
        for name, value in (("device_id", self.device_id),
                            ("install_id", self.install_id)):
            if value is not None and not (isinstance(value, str) and value.isdigit()):
                raise ValueError(f"{name} 必须是纯数字字符串")


@dataclass
class PlaybackMedia:
    """对应 C# 的 PlaybackMedia（对外稳定模型）。"""
    url: str
    referer: str = PLAYBACK_REFERER
    backup_url: str = ""
    definition: str = ""
    quality_name: str = ""
    width: int = 0
    height: int = 0
    codec: str = ""
    bitrate: int = 0
    size: int = 0
    duration: float = 0.0
    decryption_key: str = ""
    audio_url: str = ""
    expires_at_unix_seconds: int = 0
    is_hls: bool = False
    is_dash: bool = False

    @property
    def quality_score(self) -> int:
        """清晰度打分：height*10 + (H264 ? 1 : 0)，与 C# SelectAppMedia 一致。"""
        return self.height * 10 + (1 if self.codec.upper() == "H264" else 0)

    def is_expired(self, now_ts: float | None = None) -> bool:
        """直链是否已过期；过期需重新 ResolveMediaAsync（手册 5.3 / 9.2）。"""
        return is_expired(self.expires_at_unix_seconds, now_ts)


# ---------------------------------------------------------------- 纯算法

def _validate_state_path(path: str) -> None:
    """会话快照路径合法性校验（不做任何读写；禁止入库/写日志）。"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("会话快照路径不能为空")
    if "\x00" in path:
        raise ValueError("会话快照路径含非法字符")


def x_ss_stub(gzipped_body: bytes) -> str:
    """gzip body 的 MD5 **大写**十六进制（手册 5.2 的 x-ss-stub）。

    这里只算摘要，不生成任何签名头；签名头仍需外部签名服务。
    """
    if not isinstance(gzipped_body, (bytes, bytearray, memoryview)):
        raise ValueError("gzipped_body 必须是 bytes")
    if not gzipped_body:
        raise ValueError("gzipped_body 不能为空")
    return hashlib.md5(bytes(gzipped_body)).hexdigest().upper()


def build_new_drama_params(gender: int = 2, offset: int = 0, limit: int = 20,
                           session_id: str | None = None) -> dict[str, Any]:
    """拼装新剧推荐专区 query（手册 4.3），含完整边界校验。

    ``gender``：0 女频 / 1 男频 / 2 总榜；``limit`` 限定 1–50。
    """
    if gender not in NEW_DRAMA_GENDERS:
        raise ValueError("cell_gender 只能是 0（女频）/1（男频）/2（总榜）")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset 必须是非负整数")
    if not isinstance(limit, int) or isinstance(limit, bool) \
            or not NEW_DRAMA_LIMIT_MIN <= limit <= NEW_DRAMA_LIMIT_MAX:
        raise ValueError("limit 必须在 1–50")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("session_id 必须是字符串")

    params: dict[str, Any] = {
        "book_type": NEW_DRAMA_BOOK_TYPE,
        "cell_id": NEW_DRAMA_CELL_ID,
        "change_type": NEW_DRAMA_CHANGE_TYPE,
        "client_req_type": NEW_DRAMA_CLIENT_REQ_TYPE,
        "selected_items": NEW_DRAMA_SELECTED_ITEMS,
        "cell_gender": gender,
        "offset": offset,
        "limit": limit,
    }
    if session_id:
        params["session_id"] = session_id
    return params


def is_expired(expires_at_unix_seconds: int, now_ts: float | None = None) -> bool:
    """直链过期判断（手册 5.3 第 6 点 / 9.2 第 4 点）。

    ``expires_at_unix_seconds<=0`` 表示没有有效期限，必须重新解析；
    否则 ``now_ts >= 过期时间`` 判为过期。传入 ``now_ts`` 可保持纯函数语义。
    """
    try:
        expires = int(expires_at_unix_seconds)
    except (TypeError, ValueError):
        return True
    if expires <= 0:
        return True
    now = time.time() if now_ts is None else float(now_ts)
    return now >= expires


def extract_episode_count(dto: dict[str, Any]) -> int:
    """从榜单 DTO 多字段取集数最大值（手册 4.4 第 6 点 / 4.6）。

    覆盖 ``EpisodeCount / EpisodeCountAlias / DramaChapterNumber /
    ChapterNumber`` 以及 ``VideoDetail.*`` 下同名字段。返回 0 表示需
    回查 ``GetDramaDetailsAsync`` 用 ``Episodes.Count`` 补齐（本函数只做提取）。
    """
    if not isinstance(dto, dict):
        return 0
    best = 0
    for key, value in dto.items():
        if key in EPISODE_COUNT_FIELDS or key.lower() in {
                f.lower() for f in EPISODE_COUNT_FIELDS}:
            best = max(best, _coerce_count(value))
    detail = dto.get("VideoDetail") or dto.get("videoDetail") or {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            if key.lower() in {f.lower() for f in EPISODE_COUNT_FIELDS} \
                    or "episode" in key.lower() or "chapter" in key.lower():
                best = max(best, _coerce_count(value))
    return best


def _coerce_count(value: Any) -> int:
    """把集数 tolerate 数字 / 数字字符串 / '共80集' 文案，失败返回 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        return int(digits) if digits else 0
    return 0


def _coerce_float(value: Any) -> float:
    """把时长等数值字段安全转为 float（不可解析时返回 0.0）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iter_url_values(value: Any) -> Iterator[str]:
    """递归展开档位内某个 URL 字段的值（str / list / dict 混合）。"""
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_url_values(item)
    elif isinstance(value, dict):
        for key in ("url", "Url", "URL", "download_url", "DownloadUrl",
                    "downloadUrl", "main_url", "MainUrl"):
            if key in value:
                yield from _iter_url_values(value[key])


def _iter_tier_dicts(value: Any) -> Iterator[dict[str, Any]]:
    """递归找出含地址字段的档位对象（streams / medias / data 等外层均可）。"""
    if isinstance(value, dict):
        keys = {str(k) for k in value}
        if any(k in keys for k in MEDIA_URL_SOURCES + MEDIA_URL_ALIASES):
            yield value
            return
        for child in value.values():
            yield from _iter_tier_dicts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_tier_dicts(child)


def _pick(tier: dict[str, Any], *names: str, default: Any = "") -> Any:
    """大小写不敏感地从档位对象取字段（兼容 C# DTO 命名）。"""
    lowered = {str(k).lower(): v for k, v in tier.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] is not None:
            return lowered[name.lower()]
    return default


def collect_media_candidates(raw: dict[str, Any],
                             key_seed: str | None = None) -> list[PlaybackMedia]:
    """从播放响应收集全部档位候选（手册 5.3 / 5.6）。

    - 地址来源：``MainUrls / BackupUrls / BackupUrls1 / BackupUrls2 /
      BackupUrlCollection / UrlList``（以及 download_url 别名），全部解码、
      去重、仅保留 HTTP(S)。
    - **同一档位**的 URL 与 ``spadeA`` / ``encrypt`` 密钥严格配对，绝不跨档位
      混用；每个候选 URL 继承其所属档位的密钥。
    - ``bytevc2`` 编码或带该标记的档位整体跳过。
    - 返回按 ``quality_score`` 降序（稳定排序），首项即最佳画质。
    """
    if not isinstance(raw, dict):
        return []
    seed = key_seed
    if not seed:
        seed = str(_pick(raw, "keySeed", "key_seed", "KeySeed", default="") or "")
    candidates: list[PlaybackMedia] = []
    seen_urls: set[str] = set()

    for tier in _iter_tier_dicts(raw):
        codec = str(_pick(tier, "codec", "Codec", "video_codec", default=""))
        definition = str(_pick(tier, "definition", "Definition", default=""))
        quality_name = str(_pick(tier, "qualityName", "quality_name",
                                 "QualityName", default=""))
        tier_mark = f"{codec} {definition} {quality_name}".lower()
        if "bytevc2" in tier_mark:
            continue

        urls: list[str] = []
        for field_name in MEDIA_URL_SOURCES + MEDIA_URL_ALIASES:
            if field_name not in tier:
                continue
            for item in _iter_url_values(tier[field_name]):
                if "bytevc2" in item.lower():
                    continue
                decoded = decode_playback_url(item, seed or None)
                if "bytevc2" in decoded.lower():
                    continue
                if not decoded.lower().startswith(("https://", "http://")) \
                        or decoded in urls:
                    continue
                urls.append(decoded)
        if not urls:
            continue

        # 密钥只从当前档位取，失败时留空（MediaException 由上层决定是否抛出）
        decryption_key = ""
        spade_a = str(_pick(tier, "spadeA", "SpadeA", default="") or "")
        encrypted = str(_pick(tier, "encryption_method", "EncryptionMethod",
                              default="")).lower() == "cenc-aes-ctr"
        encrypt_flag = _pick(tier, "encrypt", "Encrypt", default=False)
        encrypt_flag = encrypt_flag is True or str(encrypt_flag).lower() in ("1", "true")
        if spade_a or encrypted or encrypt_flag:
            if spade_a:
                try:
                    decryption_key = decode_content_key(spade_a)
                except ValueError:
                    decryption_key = ""

        height = _coerce_count(_pick(tier, "height", "Height", "video_height",
                                     default=0))
        if height <= 0:
            height = DEFINITION_HEIGHTS.get(definition.lower(), 0)
        width = _coerce_count(_pick(tier, "width", "Width", "video_width", default=0))
        audio = _pick(tier, "audioUrl", "AudioUrl", "audio_url", default="")
        if isinstance(audio, str):
            audio = decode_playback_url(audio, seed or None) \
                if audio and not audio.lower().startswith(("http://", "https://")) else audio
        else:
            audio = ""

        for index, url in enumerate(urls):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append(PlaybackMedia(
                url=url,
                backup_url=urls[index + 1] if index + 1 < len(urls) else "",
                definition=definition,
                quality_name=quality_name,
                width=width,
                height=height,
                codec=codec,
                bitrate=_coerce_count(_pick(tier, "bitrate", "Bitrate", default=0)),
                size=_coerce_count(_pick(tier, "size", "Size", default=0)),
                duration=_coerce_float(_pick(tier, "duration", "Duration", default=0)),
                decryption_key=decryption_key,
                audio_url=audio if "http" in audio.lower() else "",
                expires_at_unix_seconds=_coerce_count(
                    _pick(tier, "expiresAtUnixSeconds", "ExpiresAtUnixSeconds",
                          "expires_at_unix_seconds", default=0)),
                is_hls=".m3u8" in url.lower(),
                is_dash=".mpd" in url.lower(),
            ))

    candidates.sort(key=lambda media: media.quality_score, reverse=True)
    return candidates

def b64decode_hongguo(value: str) -> bytes:
    """红果 Base64 变体：兼容标准 Base64 / Base64URL / 缺 padding。"""
    s = (value or "").strip()
    if not s:
        return b""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s, validate=True)


def decode_playback_url(value: str | None, key_seed: str | None) -> str:
    """解码播放地址：明文直返；Base64 解出 URL；0xa8 封包用 AES-CBC 解密。

    返回空串表示无法解码。加密封包需要 pycryptodome；缺失时返回空串，
    由调用方走 Web 兜底（只能拿公开试看片）。
    """
    if not value or not value.strip():
        return ""
    trimmed = value.strip()
    if trimmed.lower().startswith(("https://", "http://")):
        return trimmed
    try:
        raw = b64decode_hongguo(trimmed)
    except Exception:
        return ""
    if not raw:
        return ""

    if raw[0] == 0xA8:
        # 结构校验：长度>=20、raw[2]==1、raw[3]==0、密文长度 16 的倍数、有 keySeed
        if len(raw) < 20 or raw[2] != 1 or raw[3] != 0 \
                or (len(raw) - 4) % 16 != 0 or not key_seed:
            return ""
        try:
            seed = b64decode_hongguo(key_seed.strip())
        except Exception:
            return ""
        if not seed:
            return ""
        first = hashlib.sha512(seed).digest()
        key_hash = hashlib.sha512(first + _URL_SEED_CONSTANTS).digest()
        try:
            from Crypto.Cipher import AES  # type: ignore
        except ImportError:
            return ""  # 无 AES 库：明确失败，调用方走 Web 兜底
        cipher = AES.new(key_hash[:16], AES.MODE_CBC, iv=key_hash[16:32])
        plaintext = cipher.decrypt(raw[4:])
        pad = plaintext[-1]
        if 0 < pad <= 16:
            plaintext = plaintext[:-pad]
        raw = plaintext

    decoded = raw.decode("utf-8", errors="ignore").strip("\0 \r\n")
    if decoded.lower().startswith(("https://", "http://")):
        return decoded
    return ""


def decode_content_key(spade_a: str) -> str:
    """解码 CENC 内容密钥，返回 32 位小写十六进制；失败抛 ValueError。

    完整移植 C# ContentKeyDecoder 的 v1/v2 字节变换，便于把红果返回的
    ``spadeA`` 直接转成 ffmpeg ``-decryption_key`` 可用的值。
    """
    if len(spade_a) > 1024:
        raise ValueError("媒体密钥数据过长")
    try:
        buf = bytearray(b64decode_hongguo(spade_a))
    except Exception as exc:
        raise ValueError("媒体密钥编码无效") from exc
    if len(buf) < 3:
        raise ValueError("媒体密钥结构无效")

    n = (buf[0] ^ buf[1] ^ buf[2]) - 48
    m = len(buf) - n - 1
    if n < 1 or m < 33 or m >= len(buf) or len(buf) - n - 2 < 0:
        raise ValueError("媒体密钥结构无效")

    b = buf[len(buf) - n - 2] ^ buf[len(buf) - n - 1]
    tag = bytes(x ^ b for x in buf[len(buf) - n:])
    is_v2 = tag.startswith(b"app_v2") or tag.startswith(b"web_v2")

    arr = bytearray(buf[1:1 + m])
    if is_v2:
        last = len(arr) - 1
        i = 0
        while i + 1 < len(arr):
            arr[i], arr[i + 1] = arr[i + 1], arr[i]
            if i == last - 2:
                break
            i += 2

    cur, prev = 0xFA, 0x55
    for j in range(m):
        val = arr[j]
        term = (-(0x15 + bin(j).count("1"))) & 0xFF
        if j % 2 == 0:
            arr[j] = ((cur ^ val) - prev + term) & 0xFF if is_v2 \
                else (term + (cur ^ val)) & 0xFF
            cur = val
        else:
            arr[j] = ((prev ^ val) - prev + term) & 0xFF if is_v2 \
                else (term + (prev ^ val)) & 0xFF
            prev = val

    def parse36(c: int) -> int:
        ch = chr(c)
        if "0" <= ch <= "9":
            return ord(ch) - 48
        if "a" <= ch <= "z":
            return ord(ch) - 97 + 10
        if "A" <= ch <= "Z":
            return ord(ch) - 65 + 10
        return -1

    def hexval(c: int) -> int:
        ch = chr(c)
        if "0" <= ch <= "9":
            return ord(ch) - 48
        if "a" <= ch <= "f":
            return ord(ch) - 97 + 10
        if "A" <= ch <= "F":
            return ord(ch) - 65 + 10
        return -1

    first = parse36(arr[0])
    if first < 0 or m - first - 1 != 32:
        raise ValueError("媒体密钥内容无效")
    out = bytearray()
    for i in range(16):
        hi, lo = hexval(arr[1 + i * 2]), hexval(arr[2 + i * 2])
        if hi < 0 or lo < 0:
            raise ValueError("媒体密钥不是有效的 AES-128 密钥")
        out.append((hi << 4) | lo)
    return out.hex()


def build_leaderboard_params(board: str = "hot_play", channel: str = "all",
                             offset: int = 0, session_id: str | None = None,
                             seen_ids: Iterable[str] = ()) -> dict[str, Any]:
    """拼装榜单请求 query（不含签名所需的公共参数，由签名服务补）。"""
    board_id = LEADERBOARD_BOARD_IDS.get(board)
    if not board_id:
        raise ValueError(f"未知榜单类型：{board}")
    sub = LEADERBOARD_CHANNEL_IDS.get(channel, ...)
    if channel not in LEADERBOARD_CHANNEL_IDS:
        raise ValueError(f"未知频道：{channel}")
    params: dict[str, Any] = {
        "cell_id": LEADERBOARD_CELL_ID,
        "tab_type": LEADERBOARD_TAB_TYPE,
        "selected_items": board_id,
        "offset": offset,
        "limit": 0,                       # 服务端决定每页条数
        "client_req_type": 2,
        "client_template": 2,
        "unlimited_selector_change_type": 1 if offset == 0 else 2,
    }
    if sub is not None:
        params["sub_selected_items"] = sub
    seen = list(dict.fromkeys(seen_ids))   # 去重且保序
    if seen:
        params["filter_ids"] = ",".join(seen)
    if session_id:
        params["session_id"] = session_id
    return params


def build_playback_body(video_id: str, *, detail_page_version: int = 0,
                        device_level: int = 3,
                        need_all_definition: bool = True) -> bytes:
    """拼装播放请求的 gzip 压缩 JSON body（Java gzip 语义，返回字节）。"""
    body = {
        "dr_scene": "player",
        "biz_param": {
            "detail_page_version": detail_page_version,
            "device_level": device_level,
            "need_all_video_definition": need_all_definition,
            "need_mp4_align": False,
            "use_os_player": False,
            "use_server_dns": False,
            "video_platform": 1024,
        },
        "mixed_video_id_map": {"1004": [video_id]},
        "video_id": video_id,
        "content_type": 1,
    }
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw, mtime=0)


def select_best_media(medias: list[PlaybackMedia]) -> PlaybackMedia | None:
    """按 C# SelectAppMedia 规则筛选：跳过 bytevc2，按 quality_score 降序。

    注意：这里只做**已解析出来**的媒体对象排序；URL 混淆解码发生在解析阶段
    （decode_playback_url），不在本函数内。
    """
    usable = [m for m in medias
              if m.url and "bytevc2" not in m.codec.lower() and "bytevc2" not in m.url.lower()]
    if not usable:
        return None
    return max(usable, key=lambda m: m.quality_score)


def build_ffmpeg_command(media: PlaybackMedia, dest: str) -> list[str]:
    """生成 ffmpeg 下载/解密命令（列表形式，避免 shell 转义问题）。

    ``-decryption_key`` 是输入选项，必须放在 ``-i`` 之前；同一档位的 URL 与
    key 不能跨档位混用（调用方负责配对）。
    """
    cmd: list[str] = []
    if media.decryption_key:
        cmd += ["-decryption_key", media.decryption_key]
    if media.referer:
        cmd += ["-headers", f"Referer: {media.referer}\r\n"]
    cmd += ["-i", media.url, "-c", "copy", "-movflags", "+faststart", dest]
    return cmd


def signing_required() -> str:
    """返回签名层接入提示——发真实请求前必须满足。"""
    return (
        "红果私有接口需要 x-gorgon/x-argus/x-ladon/x-medusa 等签名与设备注册，"
        "本模块不含签名算法；请接入外部签名服务后再发请求，"
        "禁止用无签名请求直接调用（必失败且影响设备指纹）。"
    )


class LeaderboardCursor:
    """榜单分页游标：贯彻手册 4.4 的终止与去重规则。"""

    def __init__(self, *, stable_board: bool = True, max_pages: int = 10):
        self.stable_board = stable_board
        self.max_pages = max_pages
        self.offset = 0
        self.session_id: str | None = None
        self.seen: set[str] = set()
        self.pages = 0

    def next_params(self, board: str, channel: str) -> dict[str, Any]:
        return build_leaderboard_params(
            board, channel, self.offset, self.session_id, self.seen)

    def consume(self, items: list[dict[str, Any]], *, next_offset: int,
                has_more: bool, session_id: str | None = None) -> list[dict[str, Any]]:
        """收下本页：按 Id 去重；更新游标；返回本页新增项。

        ``has_more=False`` 或 ``next_offset<=当前`` 时应停止继续请求。
        """
        fresh: list[dict[str, Any]] = []
        for it in items:
            ident = str(it.get("Id") or it.get("id") or "")
            if ident and ident in self.seen:
                continue
            if ident:
                self.seen.add(ident)
            fresh.append(it)
        self.pages += 1
        if session_id:
            self.session_id = session_id
        self.offset = next_offset if next_offset > self.offset else self.offset
        return fresh

    @property
    def exhausted(self) -> bool:
        return self.pages >= self.max_pages


# ---------------------------------------------------------------- 异常体系（手册第 8 章）

class PhoenixReadApiException(Exception):
    """分层异常基类，携带 operation / request_uri / status_code 等上下文。

    注意：``request_uri`` 可能含签名参数，异常对象禁止入库或写日志，
    只能在调用方按合规要求脱敏后短期展示（手册第 10 节）。
    """

    def __init__(self, message: str = "", *, operation: str = "",
                 request_uri: str = "", status_code: int = 0,
                 provider_code: str = "", is_transient: bool = False):
        super().__init__(message or self.__class__.__name__)
        self.message = message
        self.operation = operation
        self.request_uri = request_uri
        self.status_code = status_code
        self.provider_code = provider_code
        # 只有 IsTransient=True 才适合安全重试（手册明确）
        self.is_transient = is_transient


class PhoenixReadApiTransportException(PhoenixReadApiException):
    """网络传输 / 连接问题：可安全重试。"""

    def __init__(self, message: str = "", **kwargs: Any):
        kwargs.setdefault("is_transient", True)
        super().__init__(message, **kwargs)


class PhoenixReadApiHttpException(PhoenixReadApiException):
    """HTTP 4xx / 5xx：默认不重试；5xx 可显式传 is_transient=True。"""

    def __init__(self, message: str = "", **kwargs: Any):
        kwargs.setdefault("is_transient", False)
        super().__init__(message, **kwargs)


class PhoenixReadApiResponseException(PhoenixReadApiException):
    """响应结构不符合业务协议。"""


class PhoenixReadApiDeserializationException(PhoenixReadApiException):
    """JSON 反序列化失败。"""


class PhoenixReadApiProviderException(PhoenixReadApiException):
    """服务端明确业务错误（provider_code 为服务端错误码）。"""


class PhoenixReadApiAuthenticationException(PhoenixReadApiProviderException):
    """登录态失效 / 设备认证被拒：应重新登录或重置设备，不要盲目重试。"""

    def __init__(self, message: str = "", **kwargs: Any):
        kwargs.setdefault("is_transient", False)
        super().__init__(message, **kwargs)


class PhoenixReadApiMediaException(PhoenixReadApiException):
    """媒体直链或解密密钥解析失败。"""


class PhoenixReadApiFallbackException(PhoenixReadApiException):
    """App 与 Web 双通道均失败。"""


# 便于调用方使用的短别名（与 C# 类名一一对应，保留完整名以兼容手册）
TransportException = PhoenixReadApiTransportException
HttpException = PhoenixReadApiHttpException
ResponseException = PhoenixReadApiResponseException
DeserializationException = PhoenixReadApiDeserializationException
ProviderException = PhoenixReadApiProviderException
AuthenticationException = PhoenixReadApiAuthenticationException
MediaException = PhoenixReadApiMediaException
FallbackException = PhoenixReadApiFallbackException

# ---------------------------------------------------------------- 能力清单（手册第 6 章）

# 这些公开能力均由 C# SDK 提供；**需外部签名服务，本模块不实现请求**。
# 本模块只提供参数拼装/解析等纯算法，不得据此伪造未签名请求。
CAPABILITY_METHODS: frozenset[str] = frozenset({
    "GetCatalogAsync", "GetTagCategoriesAsync", "GetTagDramaPageAsync",
    "EnumerateTagDramasAsync", "SearchAsync", "GetSuggestionsAsync",
    "GetSearchCuesAsync", "GetHotSearchAsync", "GetDramaDetailsAsync",
    "GetBatchDramaDetailsAsync", "GetBatchDramaDetailsWithEpisodesAsync",
    "GetDramaSeasonsAsync", "GetShortDramaLeaderboardAsync",
    "EnumerateShortDramaLeaderboardAsync", "EnumerateNewDramaRecommendationsAsync",
    "EnumerateRecommendationsAsync", "EnumerateActorLeaderboardAsync",
    "ResolveMediaAsync", "GetDanmakuAsync", "GetDramaReviewsAsync",
    "GetEpisodeCommentsAsync", "GetDramaReviewRepliesAsync",
    "SendEpisodeCommentAsync", "SetReviewLikeAsync", "GetEmoticonsAsync",
    "CollectEmoticonAsync", "UploadPictureAsync", "GetHistoryAsync",
    "ReportProgressAsync", "SetFavoriteAsync", "RemoveFavoriteAsync",
    "GetFavoritesAsync", "CreateQrCodeLoginAsync", "CheckQrCodeStatusAsync",
    "SendVerificationCodeAsync", "LoginAsync", "ValidateSessionAsync",
    "Logout", "ResetDeviceAsync", "GetUserProfileAsync", "GetUserWorksAsync",
    "GetUserFollowersAsync", "GetUserFollowingsAsync", "SetFollowUpdateAsync",
    "GetFollowUpdatesAsync", "CheckNoticesAsync", "GetNoticesAsync",
    "GetDislikePanelAsync", "SubmitDislikeAsync",
})
CAPABILITIES_REQUIRE_EXTERNAL_SIGNING = True


def capability_note() -> str:
    """能力清单使用边界提示：需外部签名服务，本模块不实现请求。"""
    return (
        "CAPABILITY_METHODS 列出 C# SDK 的公开能力；这些能力均需外部签名服务"
        "（x-gorgon/x-argus/x-ladon/x-medusa 与设备注册），本模块不实现真实请求。"
    )
