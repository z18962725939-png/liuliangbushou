# -*- coding: utf-8 -*-
"""
授权 / 商业化模块：机器码、数字签名授权码、Token 内嵌、试用期、功能门禁。

═══════════════════════════════════════════════════════════════════════════
一、为什么是「离线数字签名」而不是「在线验证」
═══════════════════════════════════════════════════════════════════════════
在线验证要服务器 + 域名 + 备案，一年至少几百块，而且客户网络一断就用不了。
离线授权的关键是要防止客户自己伪造授权码 —— 这靠【非对称签名】解决：

    · 你手里有【私钥】(tools/license_private.key)，只用来签发授权码；
    · 程序里只内置【公钥】，只能验证、不能签发；
    · 客户把 exe 反编译了也只能拿到公钥，伪造不出任何授权码。

这就是为什么本模块要用 Ed25519（RFC 8032）而不是简单的 HMAC：
HMAC 是对称的，验证用的密钥同时也能签发，密钥一旦被从 exe 里挖出来，
整条授权体系当场沦陷。Ed25519 没这个问题。

实现用的是纯标准库（整数运算 + hashlib），不引入 cryptography / pynacl，
免得给 exe 增重几十 MB、还要处理编译依赖。正确性用 RFC 8032 官方向量验证。

═══════════════════════════════════════════════════════════════════════════
二、Token 怎么给客户用（你的 Token 只有一份，不能明文塞进 exe）
═══════════════════════════════════════════════════════════════════════════
    · 签发授权码时，把你的 JustOneAPI Token 一起加密进授权码里（可选字段）；
    · 加密密钥由「内置种子 + 该授权码的 nonce」派生，每个客户的密文都不同；
    · 客户装上软件看不到明文 Token，程序内部解密后直接用，客户无感；
    · 想给某个客户停服？下次给他发的码里不带 Token 字段即可（或者靠到期时间
      自然过期，你按周期换发新码）。

安全边界（必须说清楚，不吹牛）：
    内置种子能防住「随手翻文件/记事本搜索」级别的窥探，防不住专门针对你
    做逆向的人 —— 但那种人就算拿到 Token，也仍然伪造不出授权码，
    你能通过 JustOneAPI 后台的用量异常发现并停用该 Token。

═══════════════════════════════════════════════════════════════════════════
三、试用期与防回拨
═══════════════════════════════════════════════════════════════════════════
    · 首次运行记 trial_start，默认 7 天全功能试用；
    · 状态文件同时写两处（%APPDATA% 与 %LOCALAPPDATA%），删一处也还有备份，
      取两处里【最早】的 trial_start，防止「删配置刷试用」；
    · 系统时间往回拨超过 1 天即记一次篡改，累计 3 次按过期处理。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

from . import branding

# ======================================================================
#  可配置常量
# ======================================================================

TRIAL_DAYS = 7                    # 试用天数（0 = 不提供试用）
STATE_FILENAME = "license.json"
LICENSE_PREFIX = "LLBS1"          # 授权码前缀（同时充当格式版本号）

# 授权码里的「不限机器」标记
ANY_MACHINE = "ANY"

# ── 每日调用限额（写进授权码，受签名保护，客户改不了）────────────────────
# 背景：19.9 元/月是按「包 API」卖的，接口成本由作者承担，不限额会被重度
# 用户打穿。限额只统计 JustOneAPI 调用（抖音直连的喜欢/收藏抓取、视频下载
# 不消耗额度），按 UTC+8 自然日 0 点重置。
#
# 取值约定：
#   q > 0  → 每天最多这么多次调用
#   q = -1 → 不限（作者自用 / 特殊客户）
#   码里没有 q 字段 → 按 DEFAULT_DAILY_QUOTA 兜底（兼容早期发出的老码）
UNLIMITED = -1
DEFAULT_DAILY_QUOTA = 30

# 试用期也限额：7 天全功能但不能白嫖接口额度，按体验版给
TRIAL_DAILY_QUOTA = 10

# ======================================================================
#  免费版（v1.14.0 起）—— 试用期结束不再把人锁死，退回两个功能永久可用
# ======================================================================
#
#  ★ 为什么敢永久免费：这两条链路本身就不花钱，不是"烧钱换用户"。
#    · 视频下载  走 _free_html_resolve() —— 带登录 Cookie 抓页面自己解析，
#      取到的播放地址与付费详情接口同源（video.play_addr），一样无水印。
#    · 直播录制  走 douyin_live_stream —— 抖音自有 webcast web 接口，
#      全程不碰 JustOneAPI，没有边际成本。
#
#  ★ v1.20.0 起这两项【不限次数】。
#    以前给免费档卡「每天 5 次」，出发点是想留一个转化钩子。但实测下来
#    这个限制只干成了一件事：把本来愿意留下来当免费入口的人赶去用别的
#    下载工具 —— 而下载这条路我们一分钱成本都没有，卡它毫无收益。
#    现在改成不限量：免费档 = 「抖音视频下载 + 直播录制，装完就能一直用」，
#    采集、截流、仿写、GEO 这些**每条都有接口成本**的功能照样收费。
#
#  ★ 因此免费版有一条硬约束：免费通道没命中时**不许回退到付费接口**
#    （见 video_downloader.get_video_detail 里的免费模式短路）。
#    否则免费用户每点一次就在烧接口额度，"免费版"就变成了倒贴。
FREE_FEATURES: tuple[str, ...] = ("download", "live")

# 免费档的**接口调用**兜底上限（与上面的「下载不限量」是两件事，别混）：
# 免费功能本就不走付费接口，这个值只在「某条链路意外落到付费接口上」时
# 兜底，防止被无限调用。它**不**限制免费下载的次数。
FREE_DAILY_QUOTA = 5

# 分级套餐：注册机里直接选，日限额会写进授权码
PLANS: dict[str, dict[str, object]] = {
    "trial10": {"label": "体验版", "quota": 10, "note": "首购 19.9 元 / 38 天（含赠送 7 天）"},
    "std30": {"label": "标准版", "quota": 30, "note": "续费 29.9 元/月"},
    "pro100": {"label": "专业版", "quota": 100, "note": "59.9 元/月，重度用户 / 工作室"},
    "unlimited": {"label": "不限量", "quota": UNLIMITED, "note": "作者自用或特殊客户"},
}

# 功能清单：key -> 中文名。免费试用期 = 全部功能。
FEATURES: dict[str, str] = {
    "collect": "数据采集",
    "keyword": "关键词抓取",
    "batch": "批量采集",
    "download": "视频下载",
    "live": "直播监控录制",
    "transcribe": "音视频转文字",
    "export": "数据导出",
    "analyze": "竞品 / 选题 / 复盘",
    "comment": "评论区截流",
    "report": "数据报告",
    "geo": "AI 可见度监测（GEO）",
    "script": "短视频文案仿写",
    "drama": "短剧成片",
    "drama_parse": "短剧解析",
    "wechat_chat": "微信聊天整理",
}
ALL_FEATURES = tuple(FEATURES.keys())

# ★ 补登记功能位时的向后兼容策略（2026-10-12 立，踩过坑）。
#
#   背景：授权码的 `f` 字段记录「签发时勾了哪些功能」。新增一个功能位后，
#   老授权码里天然不含这一位 —— 若直接判定为「不可用」，等于把已经卖给客户的
#   功能收回，客户会来投诉「昨天还能用今天就提示没授权」。
#
#   auto = 老码（未声明该位）视为「已包含」。用于随套餐一起售出的常规功能。
#   deny = 老码不含该位就真的不可用。用于新增的「独立付费模块」（如 GEO 单卖）。
FEATURE_BACKFILL: dict[str, str] = {
    "keyword": "auto",     # v1.3.0 起随采集套餐售出，补登记时不能收回
    "comment": "auto",     # v1.7.0 起随采集套餐售出，同上
    "geo": "deny",         # 独立定价模块：老码不含即不可用，需单独开通
    # 文案仿写走客户**自己的** AI 接口 Key（与 GEO 同一份配置），不消耗我方
    # JustOneAPI 额度、没有边际成本，所以随套餐赠送、老码自动获得。
    # 若日后改为单独定价（例如打包卖提示词库 / 代写服务），把这里改成
    # "deny"，并在 script_page 里补上 licensing.check("script") 门禁即可。
    "script": "auto",
    # 短剧成片：媒体处理全在本机跑，不吃 JustOneAPI 额度；作者这边的边际成本
    # 只有一次 450MB 的 COS 组件包流量（约两毛钱），所以先随付费套餐赠送。
    # 想把它做成单独定价的模块（像 GEO 那样单卖）就把这里改成 "deny" ——
    # 但那会让已售出的老授权码用不了本页，必须给老客户补发新码。
    "drama": "auto",
    "drama_parse": "auto",
    # 微信聊天整理：全部在本机处理（解析 ZIP + 写 Markdown），不联网、
    # 不吃 JustOneAPI 额度、零边际成本，随付费套餐赠送，老码自动获得。
    "wechat_chat": "auto",
}

# 首批功能位（v1.0 起就随任意套餐售出）。
# 它们存在于所有已发出的授权码里 —— 无论码是「全选签发（无 f 字段）」还是
# 「逐项勾选（有 f 字段）」，都一定声明过，因此【不需要】补位策略。
# 后来新增的位才必须在上面的 FEATURE_BACKFILL 里说明兼容方式。
# （tools/_validate_feature_sync.py 会强制这条：非基础位漏定策略直接报错）
BASE_FEATURES: tuple[str, ...] = (
    "collect", "batch", "download", "live", "transcribe", "export", "analyze", "report",
)

# ★ 产品公钥（Ed25519，32 字节 hex）。由 tools/license_keygen.py 生成后填在这里。
#   私钥只存在你自己机器上的 tools/license_private.key，绝不进安装包。
LICENSE_PUBLIC_KEY_HEX = (
    "37d6836cac560e5cbf01f0f1eb3097f54ceff0e3c4fbdfd2b4bf7980ae2f7991"
)

# ★ Token 加密种子（32 字节 hex）。换了它，已经发出去的码里带的 Token 就解不开了，
#   所以【决定商用后不要再改】。
TOKEN_SEED_HEX = (
    "d72123f8790704a1fdff8ba0e25f31bf65e8dee7ee24e5d7fe285bf355136000"
)


# ======================================================================
#  一、Ed25519（RFC 8032）纯标准库实现
# ======================================================================

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493      # 群的阶
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)

# 基点 B
_BY = (4 * pow(5, _P - 2, _P)) % _P
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_B = (_BX, _BY, 1, (_BX * _BY) % _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _recover_x(y: int, sign: int):
    """由 y 恢复 x（Ed25519 的点压缩解码）。非法点返回 None。"""
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _ed_add(p, q):
    """扩展坐标下的点加。"""
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    d = 2 * z1 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _ed_double(p):
    x1, y1, z1, _t1 = p
    a = x1 * x1 % _P
    b = y1 * y1 % _P
    c = 2 * z1 * z1 % _P
    d = (_P - a) % _P
    e = ((x1 + y1) * (x1 + y1) - a - b) % _P
    g = (d + b) % _P
    f = (g - c) % _P
    h = (d - b) % _P
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _scalarmult(p, e: int):
    """标量乘（递归倍点，253 层，纯 Python 约 5–15 ms）。"""
    if e <= 0:
        return (0, 1, 1, 0)          # 单位元
    q = _scalarmult(p, e >> 1)
    q = _ed_double(q)
    if e & 1:
        q = _ed_add(q, p)
    return q


def _encode_point(p) -> bytes:
    x, y, z, _t = p
    zi = pow(z, _P - 2, _P)
    x = x * zi % _P
    y = y * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(enc: bytes):
    if len(enc) != 32:
        return None
    y = int.from_bytes(enc, "little") & ((1 << 255) - 1)
    x = _recover_x(y, enc[31] >> 7)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _clamp(scalar: int) -> int:
    scalar &= (1 << 254) - 8
    return scalar | (1 << 254)


def ed25519_public_key(seed: bytes) -> bytes:
    """由 32 字节种子（私钥）推导公钥。"""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    a = _clamp(int.from_bytes(_sha512(seed)[:32], "little"))
    return _encode_point(_scalarmult(_B, a))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """签名，返回 64 字节。只在发卡工具里用，客户端不需要。"""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(seed)
    a = _clamp(int.from_bytes(h[:32], "little"))
    prefix = h[32:]
    pub = _encode_point(_scalarmult(_B, a))
    r = int.from_bytes(_sha512(prefix + message), "little") % _L
    r_enc = _encode_point(_scalarmult(_B, r))
    k = int.from_bytes(_sha512(r_enc + pub + message), "little") % _L
    s = (r + k * a) % _L
    return r_enc + s.to_bytes(32, "little")


def ed25519_verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """验签。任何异常一律视为验签失败（绝不能因为解码出错就放行）。"""
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        a_enc = public_key
        r_enc = signature[:32]
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        a = _decode_point(a_enc)
        r = _decode_point(r_enc)
        if a is None or r is None:
            return False
        k = int.from_bytes(_sha512(r_enc + a_enc + message), "little") % _L
        left = _scalarmult(_B, s)
        right = _ed_add(r, _scalarmult(a, k))
        # 投影坐标下比较：X1*Z2 == X2*Z1 且 Y1*Z2 == Y2*Z1
        return (left[0] * right[2] - right[0] * left[2]) % _P == 0 and \
               (left[1] * right[2] - right[1] * left[2]) % _P == 0
    except Exception:                                      # noqa: BLE001
        return False


# ======================================================================
#  二、机器码
# ======================================================================

_MACHINE_CACHE: dict[str, str] = {}


def _machine_guid() -> str:
    """Windows 安装唯一标识（重装系统才变）。纯标准库可读，无需子进程。"""
    try:
        import winreg                                   # noqa: PLC0415
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        )
        try:
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(val or "")
        finally:
            winreg.CloseKey(key)
    except Exception:                                      # noqa: BLE001
        return ""


def _volume_serial(drive: str | None = None) -> str:
    """系统盘卷序列号（ctypes 调 kernel32，纯标准库。格式化磁盘才会变）。"""
    try:
        import ctypes                                   # noqa: PLC0415
        if not drive:
            drive = os.environ.get("SystemDrive", "C:") + "\\"
        serial = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive), None, 0, ctypes.byref(serial),
            None, None, None, 0,
        )
        return f"{serial.value:08X}" if ok else ""
    except Exception:                                      # noqa: BLE001
        return ""


def _cpu_fingerprint() -> str:
    return "|".join(filter(None, (
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
        os.environ.get("PROCESSOR_ARCHITECTURE", ""),
        os.environ.get("NUMBER_OF_PROCESSORS", ""),
    )))


def machine_fingerprint() -> str:
    """完整指纹（含卷序列号，用于严格比对，不对外展示）。"""
    raw = "|".join((
        "LLBS-v1",
        _machine_guid(),
        _volume_serial(),
        _cpu_fingerprint(),
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def machine_id() -> str:
    """给客户看的机器码：XXXX-XXXX-XXXX-XXXX（16 位十六进制）。"""
    if "id" not in _MACHINE_CACHE:
        h = machine_fingerprint().upper()
        _MACHINE_CACHE["id"] = "-".join(h[i:i + 4] for i in range(0, 16, 4))
    return _MACHINE_CACHE["id"]


def machine_matches(bound: str) -> bool:
    """授权码里的机器绑定是否匹配本机。

    bound 允许三种写法：
      · ""/"ANY"        → 不限机器（多机版 / 工作室版）
      · 完整指纹（64 hex）→ 严格比对
      · 短机器码（16 hex，可带 -）→ 比对短码
    """
    b = (bound or "").strip().upper()
    if not b or b == ANY_MACHINE:
        return True
    b_norm = b.replace("-", "")
    if len(b_norm) >= 32:                       # 完整指纹
        return hmac.compare_digest(machine_fingerprint().upper(), b_norm)
    return hmac.compare_digest(machine_id().replace("-", ""), b_norm)


# ======================================================================
#  三、Token 加密（对称，密钥由 种子 + nonce 派生）
# ======================================================================

def _seed_bytes(hex_str: str, name: str) -> bytes:
    try:
        raw = binascii.unhexlify(hex_str.strip())
    except Exception:                                      # noqa: BLE001
        raise ValueError(f"{name} 不是合法的 hex 字符串")
    if len(raw) != 32:
        raise ValueError(f"{name} 必须是 32 字节（64 位 hex）")
    return raw


def _keystream(key: bytes, length: int) -> bytes:
    """SHA256 计数器模式生成密钥流（标准库里没有 AES，这是够用的替代）。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


def encrypt_token(token: str, nonce: str, seed_hex: str | None = None) -> str:
    """把 Token 加密成可放进授权码的 URL-safe base64 字符串。"""
    token = (token or "").strip()
    if not token:
        return ""
    seed = _seed_bytes(seed_hex or TOKEN_SEED_HEX, "TOKEN_SEED")
    key = hashlib.sha256(seed + b"|tok|" + (nonce or "").encode("utf-8")).digest()
    data = token.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(key, len(data))))
    mac = hmac.new(key, ct, hashlib.sha256).digest()[:8]
    return _b64e(ct + mac)


def decrypt_token(blob: str, nonce: str, seed_hex: str | None = None) -> str:
    """解出 Token；密文被改过一个字节就返回 ""（HMAC 校验）。"""
    if not blob:
        return ""
    try:
        seed = _seed_bytes(seed_hex or TOKEN_SEED_HEX, "TOKEN_SEED")
        raw = _b64d(blob)
        if len(raw) < 9:
            return ""
        ct, mac = raw[:-8], raw[-8:]
        key = hashlib.sha256(seed + b"|tok|" + (nonce or "").encode("utf-8")).digest()
        if not hmac.compare_digest(hmac.new(key, ct, hashlib.sha256).digest()[:8], mac):
            return ""
        pt = bytes(a ^ b for a, b in zip(ct, _keystream(key, len(ct))))
        return pt.decode("utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        return ""


# ======================================================================
#  四、base64 工具（URL-safe，无填充 —— 便于粘贴，不会在换行处被吃掉）
# ======================================================================

def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    s = re.sub(r"\s+", "", text or "")
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


# ======================================================================
#  五、授权码：签发 / 解析 / 校验
# ======================================================================

def build_payload(license_token: str = "", machine: str = ANY_MACHINE,
                  days: int = 0, customer: str = "", features: str = "",
                  issued_at: float | None = None, nonce: str = "",
                  quota: int = 0) -> str:
    """拼出授权码里的载荷字符串（紧凑 k=v，| 分隔）。

    days=0 表示永久授权；features 为空表示全部功能。
    quota: 每日 API 调用上限（>0 限额 / -1 不限 / 0 = 不写该字段，由客户端兜底）。

    days 允许为负：表示"签发即已过期"，用来给客户演示/测试到期后的表现。
    """
    nonce = nonce or secrets.token_hex(6)
    exp = 0
    if days:
        # days=0 → 永久（exp=0）；正数 → 未来某天；负数 → 过去的某天（已过期）
        issued = issued_at or time.time()
        exp = int(issued) + int(days) * 86400
    parts = [
        "v=1",
        f"n={nonce}",
        f"m={machine or ANY_MACHINE}",
        f"e={exp}",
    ]
    if customer:
        parts.append("c=" + re.sub(r"[|\r\n]", " ", customer)[:40])
    if features:
        parts.append("f=" + re.sub(r"[^a-z0-9,]", "", features.lower()))
    else:
        parts.append("f=all")
    if quota:
        # 只接受整数；非法值当"不限"处理，避免签出无法解析的码
        try:
            parts.append("q=" + str(int(quota)))
        except (TypeError, ValueError):
            parts.append("q=" + str(UNLIMITED))
    if license_token:
        parts.append("t=" + encrypt_token(license_token, nonce))
    parts.append(f"i={int(issued_at or time.time())}")
    return "|".join(parts)


def parse_payload(payload: str) -> dict:
    """载荷字符串 → dict（不做验签，验签由 verify_license 负责）。"""
    out: dict[str, str] = {}
    for seg in (payload or "").split("|"):
        if "=" in seg:
            k, _, v = seg.partition("=")
            out[k.strip()] = v.strip()
    return out


def format_license(payload: str, signature: bytes) -> str:
    """载荷 + 签名 → 给客户的授权码字符串。"""
    return f"{LICENSE_PREFIX}-{_b64e(payload.encode('utf-8'))}.{_b64e(signature)}"


def issue_license(private_seed_hex: str, license_token: str = "",
                  machine: str = ANY_MACHINE, days: int = 0,
                  customer: str = "", features: str = "",
                  quota: int = 0) -> str:
    """签发一个授权码（发卡工具调用，需要私钥）。

    quota: 每日 API 调用上限（见 PLANS；-1 = 不限）。
    """
    seed = _seed_bytes(private_seed_hex, "私钥")
    payload = build_payload(license_token=license_token, machine=machine,
                            days=days, customer=customer, features=features,
                            quota=quota)
    sig = ed25519_sign(seed, payload.encode("utf-8"))
    return format_license(payload, sig)


def extract_code(text: str) -> str:
    """从粘贴内容里把授权码抠出来（容忍前后空白、换行、多余说明文字）。"""
    s = (text or "").strip()
    if not s:
        return ""
    m = re.search(re.escape(LICENSE_PREFIX) + r"-[A-Za-z0-9_\-\.]+", s)
    return m.group(0) if m else s.split()[0] if s.split() else ""


def _plan_label(quota: int) -> str:
    """按日限额反查套餐名（仅用于界面展示）。"""
    for p in PLANS.values():
        if int(p["quota"]) == int(quota):
            return str(p["label"])
    if int(quota) == UNLIMITED:
        return "不限量"
    return f"自定义 {quota} 次/天"


def daily_quota() -> int:
    """当前生效的每日 API 调用上限（-1 = 不限，0 = 不可用）。

    已授权 → 授权码里写的额度；试用期 → TRIAL_DAILY_QUOTA；
    免费版 → FREE_DAILY_QUOTA。

    ★ 注意这个值管的是【接口调用】，不是【免费下载次数】。v1.20.0 起免费档的
      视频下载 / 直播录制已经不限量（见 quota.free_limit），走的是零成本链路，
      压根不经过这里。FREE_DAILY_QUOTA 只是意外落到付费接口时的兜底。
    """
    st = status()
    return int(st.get("daily_quota") or 0)


def verify_license(code: str, public_key_hex: str | None = None) -> dict:
    """校验授权码，返回结论 dict（永不抛异常）。

    返回：{valid, reason, payload{...}, machine_ok, expired, days_left,
           customer, expires_at, features, embedded_token}
    """
    result = {
        "valid": False, "reason": "", "payload": {}, "machine_ok": False,
        "expired": False, "days_left": None, "customer": "",
        "expires_at": 0,
        # 未校验通过时也按同一口径给出功能范围（排除独立付费模块），
        # 避免任何「没授权却显示全功能」的误导显示。
        "features": [f for f in ALL_FEATURES if FEATURE_BACKFILL.get(f) != "deny"],
        "embedded_token": "",
        "daily_quota": DEFAULT_DAILY_QUOTA, "plan": "",
    }
    code = extract_code(code)
    if not code:
        result["reason"] = "授权码为空"
        return result
    if not code.startswith(LICENSE_PREFIX + "-"):
        result["reason"] = f"授权码格式不对（应以 {LICENSE_PREFIX}- 开头）"
        return result
    body = code[len(LICENSE_PREFIX) + 1:]
    if "." not in body:
        result["reason"] = "授权码不完整（缺少签名段），可能复制时被截断了"
        return result
    payload_b64, _, sig_b64 = body.rpartition(".")
    try:
        payload = _b64d(payload_b64).decode("utf-8")
        sig = _b64d(sig_b64)
    except Exception as e:                                 # noqa: BLE001
        result["reason"] = f"授权码解码失败：{e}"
        return result

    pk_hex = (public_key_hex or LICENSE_PUBLIC_KEY_HEX).strip()
    try:
        pk = binascii.unhexlify(pk_hex)
    except Exception:                                      # noqa: BLE001
        pk = b""
    if len(pk) != 32 or pk == bytes(32):
        result["reason"] = (
            "程序内未配置有效公钥（这是发行方需要执行的构建步骤，"
            "请联系软件作者）"
        )
        return result
    if not ed25519_verify(pk, sig, payload.encode("utf-8")):
        result["reason"] = "签名校验失败：这个授权码不是本软件签发的（或已被修改）"
        return result

    data = parse_payload(payload)
    result["payload"] = data
    result["customer"] = data.get("c", "")

    # 功能范围
    #   · 码里带 f 字段 → 以它为准（只认本版本认识的功能位）
    #   · 码里没有 f 字段 → 签发时勾的是「全选」，但**不能**直接等于「当前全部功能」：
    #     否则后来新增的独立付费模块（如 GEO）会被老码白送。
    #     故按 FEATURE_BACKFILL 剔除 deny 位，其余视为已包含。
    #     （codegen 里「全勾 = 不写 f 字段」是在 v1.8.x 之前就有的行为，动不得）
    feat = (data.get("f") or "").lower()
    if feat and feat != "all":
        result["features"] = [f for f in feat.split(",") if f in FEATURES] or list(ALL_FEATURES)
    else:
        result["features"] = [f for f in ALL_FEATURES
                              if FEATURE_BACKFILL.get(f) != "deny"]

    # 每日调用限额（q>0 限额 / q=-1 不限 / 缺字段用默认值兜底）
    try:
        result["daily_quota"] = int(data.get("q", DEFAULT_DAILY_QUOTA) or DEFAULT_DAILY_QUOTA)
    except (TypeError, ValueError):
        result["daily_quota"] = DEFAULT_DAILY_QUOTA
    result["plan"] = _plan_label(result["daily_quota"])

    # 机器绑定
    bound = data.get("m", ANY_MACHINE)
    result["machine_ok"] = machine_matches(bound)
    if not result["machine_ok"]:
        result["reason"] = (
            f"该授权码绑定的是另一台电脑（授权机器：{bound}，本机：{machine_id()}）。"
            "一码一机，如需换机请把本机机器码发给作者重新出码。"
        )
        return result

    # 有效期
    try:
        exp = int(data.get("e", "0") or 0)
    except ValueError:
        exp = 0
    result["expires_at"] = exp
    if exp:
        left = (exp - time.time()) / 86400.0
        result["days_left"] = left
        if left <= 0:
            result["expired"] = True
            result["reason"] = (
                f"授权已于 {time.strftime('%Y-%m-%d', time.localtime(exp))} 到期，"
                "请续费后获取新的授权码"
            )
            return result

    # 内嵌 Token
    if data.get("t"):
        tok = decrypt_token(data["t"], data.get("n", ""))
        result["embedded_token"] = tok
        if not tok:
            result["reason"] = (
                "授权码里的接口凭证解密失败（可能软件版本与本码不匹配），"
                "请向作者索取与当前版本匹配的授权码"
            )
            return result

    result["valid"] = True
    return result


# ======================================================================
#  六、本地状态（试用期 / 已激活的授权码 / 防回拨）
# ======================================================================

def _state_paths() -> list[Path]:
    """状态文件的两个落点：删掉一个，另一个还在（防"删配置刷试用"）。"""
    paths = [branding.app_data_dir() / STATE_FILENAME]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        p = Path(local) / branding.DATA_DIR_NAME / STATE_FILENAME
        if p not in paths:
            paths.append(p)
    return paths


def _read_state_file(path: Path) -> dict:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:                                      # noqa: BLE001
        pass
    return {}


def load_state() -> dict:
    """读状态：多处取"最不利于刷试用"的那一份（trial_start 取最早）。"""
    merged: dict = {}
    for p in _state_paths():
        d = _read_state_file(p)
        if not d:
            continue
        if not merged:
            merged = dict(d)
            continue
        # trial_start 取最早；last_seen 取最晚；tamper 取最大
        for key, pick in (("trial_start", min), ("last_seen", max), ("tamper", max)):
            a, b = merged.get(key), d.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                merged[key] = pick(a, b)
            elif b is not None and a is None:
                merged[key] = b
    return merged


def save_state(state: dict) -> None:
    for p in _state_paths():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        except Exception:                                  # noqa: BLE001
            pass


def _now() -> float:
    return time.time()


def touch(force: bool = False) -> dict:
    """程序启动时调一次：初始化试用起点、检测时钟回拨、记录 last_seen。"""
    state = load_state()
    now = _now()
    if not state.get("trial_start"):
        state["trial_start"] = now
        state["trial_days"] = TRIAL_DAYS
    last = state.get("last_seen") or 0
    if last and now < last - 86400:
        # 时钟被往回拨超过 1 天：按篡改处理
        state["tamper"] = int(state.get("tamper") or 0) + 1
    state["last_seen"] = max(now, last)
    state["last_version"] = branding.APP_VERSION
    save_state(state)
    return state


def trial_days_left(state: dict | None = None) -> float:
    st = state if state is not None else load_state()
    start = st.get("trial_start") or 0
    days = int(st.get("trial_days") or TRIAL_DAYS)
    if not start:
        return float(days)
    left = (float(start) + days * 86400 - _now()) / 86400.0
    return max(0.0, left)


# ======================================================================
#  七、当前授权状态（GUI 只需要这一个入口）
# ======================================================================

_ACTIVE_CACHE: dict = {}


def status(reload: bool = False) -> dict:
    """返回当前授权状态，GUI 直接用它渲染。

    {
      state: 'licensed' | 'trial' | 'free' | 'expired' | 'none',
             （free = 试用结束/未激活/授权过期后的免费版，仅含 download 与 live）
      label: 一句话状态文案,
      licensed: bool, days_left: float|None,
      customer, expires_at, machine_id, features: [...], embedded_token,
      tamper: int, trial_start: float,
    }
    """
    if _ACTIVE_CACHE and not reload:
        return _ACTIVE_CACHE

    st = load_state()
    code = (st.get("license") or "").strip()
    out = {
        "state": "none", "label": "", "licensed": False, "days_left": None,
        "customer": "", "expires_at": 0, "machine_id": machine_id(),
        "features": [], "embedded_token": "", "tamper": int(st.get("tamper") or 0),
        "trial_start": float(st.get("trial_start") or 0), "message": "",
        "daily_quota": 0, "plan": "",
    }

    if code:
        info = verify_license(code)
        if info["valid"]:
            out.update({
                "state": "licensed", "licensed": True,
                "customer": info["customer"], "expires_at": info["expires_at"],
                "features": info["features"],
                "embedded_token": info["embedded_token"],
                "days_left": info["days_left"],
                "daily_quota": info["daily_quota"], "plan": info["plan"],
            })
            who = f"（{info['customer']}）" if info["customer"] else ""
            if info["expires_at"]:
                out["label"] = (
                    f"已授权{who}，有效期至 "
                    f"{time.strftime('%Y-%m-%d', time.localtime(info['expires_at']))}"
                    f"，剩余 {info['days_left']:.1f} 天"
                )
            else:
                out["label"] = f"已授权{who}（永久）"
            _ACTIVE_CACHE.clear()
            _ACTIVE_CACHE.update(out)
            return out
        out["message"] = info["reason"]
        out["state"] = "expired" if info["expired"] else "none"
        if info["expired"]:
            out["label"] = info["reason"]
            _ACTIVE_CACHE.clear()
            _ACTIVE_CACHE.update(out)
            return out

    # 没有可用授权码 → 看试用
    left = trial_days_left(st)
    if out["tamper"] >= 3:
        out.update({"state": "expired", "label": "检测到多次修改系统时间，试用已被终止"})
    elif left <= 0:
        # ★ 免费版（v1.14.0）：试用结束不再锁死软件，退回「视频下载 + 直播录制」
        #   两个功能永久可用；v1.20.0 起这两项**不限次数**。它们走的是零成本
        #   链路，放开既不烧接口额度，也不会被白嫖打穿成本 ——
        #   而把人锁死只会让客户直接卸载，留一个能用的入口才有转化机会。
        out.update({
            "state": "free",
            "label": "免费版：视频下载、直播录制不限量可用，其余功能需激活授权",
            "features": list(FREE_FEATURES),
            "daily_quota": FREE_DAILY_QUOTA, "plan": "免费版",
        })
    else:
        out.update({
            "state": "trial",
            "label": f"试用中，剩余 {left:.1f} 天（每日 {TRIAL_DAILY_QUOTA} 次调用额度）",
            "days_left": left, "features": list(ALL_FEATURES),
            "daily_quota": TRIAL_DAILY_QUOTA, "plan": "试用",
        })
    _ACTIVE_CACHE.clear()
    _ACTIVE_CACHE.update(out)
    return out


def current() -> dict:
    """status() 的简写（GUI 里用得多）。"""
    return status()


def refresh() -> dict:
    return status(reload=True)


def activate(code: str) -> tuple[bool, str]:
    """激活：验签通过才写入本地状态。返回 (成功, 说明)。"""
    info = verify_license(code)
    if not info["valid"]:
        return False, info["reason"]
    st = load_state()
    st["license"] = extract_code(code)
    st["activated_at"] = _now()
    save_state(st)
    status(reload=True)
    who = f"（{info['customer']}）" if info["customer"] else ""
    tail = ("（永久）" if not info["expires_at"]
            else f"，有效期至 {time.strftime('%Y-%m-%d', time.localtime(info['expires_at']))}")
    return True, f"激活成功{who}{tail}"


def deactivate() -> None:
    st = load_state()
    st.pop("license", None)
    save_state(st)
    status(reload=True)


def imported_license_code() -> str:
    return (load_state().get("license") or "").strip()


# ======================================================================
#  八、功能门禁
# ======================================================================

def check(feature: str) -> tuple[bool, str]:
    """该功能是否可用。返回 (可用, 不可用原因)。

    试用期内 = 全部功能可用；已授权 = 按授权码里的功能位；否则不可用。

    ★ 向后兼容（2026-10-12）：功能位是后来才补登记的（如「关键词抓取」「评论区截流」），
      老授权码里没有这一位。若直接判定不可用，等于把已经卖出去的功能收回，
      客户会投诉「昨天还能用今天就说没授权」。故 FEATURE_BACKFILL 标了 auto 的位，
      老码一律视为已包含；标 deny 的（GEO 独立付费模块）则真的拦住。
    """
    st = status()
    if st["licensed"]:
        feats = st.get("features") or []
        if feature in feats or not feats:
            return True, ""
        if FEATURE_BACKFILL.get(feature) == "auto":
            return True, ""
        return False, f"当前授权不包含「{FEATURES.get(feature, feature)}」，请升级授权"
    if st["state"] == "trial":
        return True, ""

    # ★ 免费版（v1.14.0）：非授权态下「视频下载」与「直播录制」永久可用，
    #   v1.20.0 起并且【不限次数】。这两条链路是零成本的，放开既能当引流
    #   入口，也不会烧接口额度。
    #   tamper >= 3（多次改系统时间）的不给 —— 那是绕过试用期的作弊行为。
    if int(st.get("tamper") or 0) < 3 and feature in FREE_FEATURES:
        return True, ""

    if st.get("state") == "expired":
        return False, (
            f"{st.get('label') or '授权已到期'}。\n\n"
            "在此期间你仍可免费使用「视频下载」与「直播录制」（不限次数）；"
            "其余功能续费激活后恢复。")
    return False, (
        "这是付费功能。当前为免费版，可一直用的是「视频下载」和「直播录制」两项"
        "（不限次数），采集、截流、仿写等需要激活授权码。\n\n"
        + (st.get("label") or ""))


def is_trial() -> bool:
    return status()["state"] == "trial"


def is_free_tier() -> bool:
    """当前是否处于免费档（非授权、非试用）。

    ★ 免费档有一条硬约束：解析视频时**不许回退到付费接口**（见
      video_downloader.get_video_detail）。免费通道没命中就是没命中，
      直接告诉用户原因 —— 否则免费用户每点一次都在烧接口额度。

    ★ 2026-10-23：**免费版（独立发行版）一律算免费档**，哪怕客户把
      完整版的授权码粘进来也一样。原因有两条：
        ① 免费版的存在意义就是「完全不碰接口」——客户之所以要它，正是
           因为接口欠费时付费功能全废。让它再去调接口等于自相矛盾；
        ② 免费版的界面里根本没有付费功能的入口，留着这条通路只会让
           客户在日志里看到一堆看不懂的接口报错。
      想让免费版客户用付费功能，正确做法是让他下载完整版。
    """
    if getattr(branding, "APP_IS_FREE", False):
        return True
    st = status()
    return not st.get("licensed") and st.get("state") != "trial"


def embedded_token() -> str:
    """授权码里内嵌的接口 Token（没有则空串）。"""
    st = status()
    return st.get("embedded_token") or "" if st.get("licensed") else ""


def purchasable() -> bool:
    return bool(branding.APP_HOMEPAGE or branding.APP_CONTACT)


def purchase_hint() -> str:
    """给用户看的购买/续费指引（把 branding 里的联系方式填上就会显示）。"""
    bits = ["购买 / 续费授权请联系作者："]
    if branding.APP_CONTACT:
        bits.append(f"　联系方式：{branding.APP_CONTACT}")
    if branding.APP_HOMEPAGE:
        bits.append(f"　购买链接：{branding.APP_HOMEPAGE}")
    if not branding.APP_CONTACT and not branding.APP_HOMEPAGE:
        bits.append("　（作者尚未填写联系方式，请在 branding.py 里补上 "
                    "APP_CONTACT / APP_HOMEPAGE）")
    bits.append(f"\n请把本机机器码发给我：{machine_id()}")
    return "\n".join(bits)


def export_license_file(code: str, path: Path, note: str = "") -> Path:
    """把授权码写成一个 .lic 文件（给客户发文件比发长字符串更省事）。"""
    info = verify_license(code)
    data = {
        "product": branding.APP_NAME,
        "code": extract_code(code),
        "customer": info.get("customer") or "",
        "note": note,
        "issued_by": branding.APP_PUBLISHER,
        "issued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_license_file(path: Path) -> str:
    """从 .lic 文件里读出授权码（同时支持纯文本文件）。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        return ""
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("code"):
            return str(data["code"])
    except Exception:                                      # noqa: BLE001
        pass
    return extract_code(text)


# ======================================================================
#  九、自检（`python -m justoneapi_collector.licensing`）
# ======================================================================

_RFC8032_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821"
     "590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e4"
     "3e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b53"
     "8d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def self_test() -> tuple[int, int]:
    """跑 RFC 8032 官方向量 + 一圈授权码。返回 (通过, 总数)。"""
    ok = total = 0
    for seed_hex, pub_hex, msg_hex, sig_hex in _RFC8032_VECTORS:
        total += 1
        seed = binascii.unhexlify(seed_hex)
        msg = binascii.unhexlify(msg_hex)
        pub = ed25519_public_key(seed)
        if pub.hex() == pub_hex:
            ok += 1
        total += 1
        sig = ed25519_sign(seed, msg)
        if sig.hex() == sig_hex:
            ok += 1
        total += 1
        if ed25519_verify(pub, sig, msg):
            ok += 1
        total += 1
        if not ed25519_verify(pub, sig, msg + b"x"):
            ok += 1
    return ok, total


if __name__ == "__main__":                                  # pragma: no cover
    passed, all_n = self_test()
    print(f"Ed25519 RFC 8032 向量：{passed}/{all_n} "
          f"{'全部通过' if passed == all_n else '有失败！'}")
    print("本机机器码：", machine_id())
    print("当前状态：", status()["label"])
