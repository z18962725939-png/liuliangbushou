# -*- coding: utf-8 -*-
"""授权 / 商业化模块单测。

这是整套商业化体系里最不能出错的一块：签名错了 → 客户激活不了；
机器绑定判错了 → 客户换机后要退款；试用计时错了 → 白送或误锁。
所以这里把每条分支都钉死：

  1. Ed25519（RFC 8032 官方向量，签名/验签/公钥推导/篡改检测）
  2. 机器码与绑定匹配（一机一码 / 不限机器 / 完整指纹）
  3. 授权码签发与校验矩阵（有效期、功能范围、内嵌 Token、伪造、篡改）
  4. 本地状态机（试用 / 已授权 / 过期 / 时钟回拨）
  5. 功能门禁
  6. 旧品牌数据目录迁移

全程用临时 APPDATA，碰不到用户真实配置。
"""
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="llbs_lic_")
os.environ["APPDATA"] = _TMP
os.environ["LOCALAPPDATA"] = _TMP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justoneapi_collector import branding as B   # noqa: E402
from justoneapi_collector import licensing as L  # noqa: E402

PASS, FAIL = [], []
_TMP_DIRS = [_TMP]


def chk(name, cond, extra=""):
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name}  ←  {extra}")


# ======================================================================
# 0. 旧品牌数据目录迁移（先用独立目录跑，避免污染后面用例的状态）
# ======================================================================
_MIG = tempfile.mkdtemp(prefix="llbs_mig_")
_TMP_DIRS.append(_MIG)
os.environ["APPDATA"] = _MIG
_legacy = Path(_MIG) / "JustOneAPICollector"
(_legacy / "whisper" / "models").mkdir(parents=True)
(_legacy / "config.json").write_text('{"token":"old-token"}', encoding="utf-8")
(_legacy / "whisper" / "models" / "ggml-tiny.bin").write_bytes(b"x" * 128)

msg = B.migrate_legacy_data()
_new = Path(_MIG) / B.DATA_DIR_NAME
chk("迁移：返回了说明文字", bool(msg), msg)
chk("迁移：config.json 到位",
    (_new / "config.json").is_file() and
    "old-token" in (_new / "config.json").read_text(encoding="utf-8"))
chk("迁移：转写模型目录跟着过来", (_new / "whisper" / "models" / "ggml-tiny.bin").is_file())
chk("迁移：写了标记文件，不会重复搬", (_new / ".migrated_from_legacy").is_file())
chk("迁移：没有旧目录时静默返回空", B.migrate_legacy_data() == "")
os.environ["APPDATA"] = _TMP

# ======================================================================
# 1. Ed25519 / RFC 8032
# ======================================================================
_p, _n = L.self_test()
chk(f"RFC 8032 官方向量 {_p}/{_n} 全通过", _p == _n, f"{_p}/{_n}")

_seed = bytes(range(32))
_pub = L.ed25519_public_key(_seed)
_sig = L.ed25519_sign(_seed, b"hello")
chk("签名字节数正确", len(_sig) == 64, str(len(_sig)))
chk("公钥字节数正确", len(_pub) == 32, str(len(_pub)))
chk("正常验签通过", L.ed25519_verify(_pub, _sig, b"hello"))
chk("改了消息 → 验签失败", not L.ed25519_verify(_pub, _sig, b"hello!"))
chk("改了一个签名字节 → 验签失败",
    not L.ed25519_verify(_pub, _sig[:10] + bytes([_sig[10] ^ 0x01]) + _sig[11:], b"hello"))
chk("换公钥 → 验签失败", not L.ed25519_verify(L.ed25519_public_key(b"z" * 32), _sig, b"hello"))
chk("长度不对不崩且返回 False",
    L.ed25519_verify(b"short", _sig, b"hello") is False
    and L.ed25519_verify(_pub, b"short", b"hello") is False)
chk("垃圾签名不抛异常", L.ed25519_verify(_pub, b"\xff" * 64, b"hello") is False)
try:
    L.ed25519_sign(b"short", b"x")
    _bad_seed = False
except ValueError:
    _bad_seed = True
chk("错误的种子长度会抛 ValueError", _bad_seed)

# ======================================================================
# 2. 机器码
# ======================================================================
_mid = L.machine_id()
chk("机器码是 XXXX-XXXX-XXXX-XXXX 格式",
    len(_mid) == 19 and _mid.count("-") == 3 and all(
        c in "0123456789ABCDEF-" for c in _mid), _mid)
chk("机器码稳定（两次调用一致）", L.machine_id() == _mid)
chk("完整指纹 64 位 hex",
    len(L.machine_fingerprint()) == 64 and all(
        c in "0123456789abcdef" for c in L.machine_fingerprint()))
chk("ANY 匹配任意机器", L.machine_matches("ANY") and L.machine_matches(""))
chk("空绑定视为不限机器", L.machine_matches(""))
chk("本机短码匹配", L.machine_matches(_mid))
chk("本机短码（无横线）也匹配", L.machine_matches(_mid.replace("-", "")))
chk("本机完整指纹匹配", L.machine_matches(L.machine_fingerprint()))
chk("别的机器码不匹配", not L.machine_matches("0000-0000-0000-0000"))
chk("小写机器码也能匹配", L.machine_matches(_mid.lower()))

# ======================================================================
# 3. Token 加解密
# ======================================================================
_tok = "sk-abcdef1234567890-中文也要能过"
chk("Token 加解密往返一致", L.decrypt_token(L.encrypt_token(_tok, "n1"), "n1") == _tok)
chk("空 Token 加密返回空", L.encrypt_token("", "n1") == "")
chk("空串解密返回空", L.decrypt_token("", "n1") == "")
chk("nonce 不对 → 解不出来", L.decrypt_token(L.encrypt_token(_tok, "n1"), "n2") == "")
_enc = L.encrypt_token(_tok, "n1")
chk("密文被改一个字符 → 解不出来",
    L.decrypt_token(_enc[:-2] + ("zz" if not _enc.endswith("zz") else "yy"), "n1") == "")
chk("密文里搜不到明文", _tok not in _enc)
chk("每次加密结果都不同（nonce 隔离）",
    L.encrypt_token(_tok, "a") != L.encrypt_token(_tok, "b"))

# ======================================================================
# 4. 授权码：签发 → 校验矩阵
# ======================================================================
_SK = secrets.token_hex(32)          # 测试用私钥（不是产品私钥）
_PK = L.ed25519_public_key(bytes.fromhex(_SK)).hex()

def issue(**kw):
    return L.issue_license(_SK, **kw)

def verify(code):
    return L.verify_license(code, public_key_hex=_PK)

chk("产品公钥已正确植入（不是全 0 占位）",
    L.LICENSE_PUBLIC_KEY_HEX != "0" * 64 and len(L.LICENSE_PUBLIC_KEY_HEX) == 64)
chk("Token 种子已正确植入", L.TOKEN_SEED_HEX != "0" * 64)
chk("未配置公钥时明确报错",
    "公钥" in L.verify_license(issue(machine="ANY"), public_key_hex="0" * 64)["reason"])

# 4.1 正常路径
c365 = issue(machine=L.machine_id(), days=365, customer="张三",
             license_token="MY-TOKEN-1")
i = verify(c365)
chk("本机绑定 + 365 天：有效", i["valid"], i["reason"])
chk("客户名解析正确", i["customer"] == "张三", i["customer"])
chk("剩余天数是 365 左右", 364 < (i["days_left"] or 0) <= 365, str(i["days_left"]))
chk("内嵌 Token 解出来了", i["embedded_token"] == "MY-TOKEN-1", i["embedded_token"])
# ★ 2026-10-12 改：默认码（不写 f 字段 = 签发时全选）不再等于「当前全部功能位」。
#   否则后来新增的独立付费模块（GEO）会被老码白送 —— 收钱的东西不能送。
#   默认码 = 全部常规位；独立付费模块必须显式勾选签发。
_REGULAR = [f for f in L.ALL_FEATURES if L.FEATURE_BACKFILL.get(f) != "deny"]
check_default_feats = set(i["features"]) == set(_REGULAR)
chk("默认给全部常规功能", check_default_feats, str(i["features"]))
chk("默认码不含独立付费模块", "geo" not in i["features"], str(i["features"]))

# 4.2 机器绑定
i2 = verify(issue(machine="FFFF-FFFF-FFFF-FFFF", days=30))
chk("换机：拒绝", not i2["valid"])
chk("换机：原因是机器不匹配", "另一台电脑" in i2["reason"], i2["reason"])
i3 = verify(issue(machine="ANY", days=30))
chk("不限机器：通过", i3["valid"], i3["reason"])
chk("不限机器：days_left 有值", (i3["days_left"] or 0) > 29)
i4 = verify(issue(machine=L.machine_fingerprint(), days=10))
chk("完整指纹绑定：通过", i4["valid"], i4["reason"])

# 4.3 有效期
i5 = verify(issue(machine="ANY", days=0))
chk("永久授权：有效", i5["valid"])
chk("永久授权：expires_at=0", i5["expires_at"] == 0)
chk("永久授权：days_left 为 None", i5["days_left"] is None)
i6 = verify(issue(machine="ANY", days=-1))
chk("已过期授权：无效", not i6["valid"])
chk("已过期授权：标记为 expired", i6["expired"])
chk("已过期授权：提示续费", "到期" in i6["reason"], i6["reason"])

# 4.4 功能范围
i7 = verify(issue(machine="ANY", days=30, features="collect,export"))
chk("部分功能授权：只给勾选的两个",
    sorted(i7["features"]) == ["collect", "export"], str(i7["features"]))
i8 = verify(issue(machine="ANY", days=30, features="不存在的功能"))
chk("非法功能名被忽略并回退到全部常规功能",
    sorted(i8["features"]) == sorted(_REGULAR), str(i8["features"]))
i8b = verify(issue(machine="ANY", days=30, features="geo"))
chk("显式勾选独立付费模块：只给该模块",
    i8b["features"] == ["geo"], str(i8b["features"]))

# 4.5 伪造与篡改
_fake = L.issue_license(secrets.token_hex(32), machine="ANY", days=0)
chk("用别人的私钥签 → 拒绝", not verify(_fake)["valid"])
chk("伪造的拒绝原因提到签名", "签名" in verify(_fake)["reason"], verify(_fake)["reason"])
chk("改一个字符 → 拒绝", not verify(c365[:-2] + "xy")["valid"])
chk("空授权码 → 拒绝", not verify("")["valid"])
chk("乱码 → 拒绝", not verify("这不是授权码")["valid"])
chk("前缀不对 → 拒绝", not verify("ABC-xxxx.yyyy")["valid"])
chk("缺签名段 → 明确提示截断",
    "截断" in verify(L.LICENSE_PREFIX + "-abcdef")["reason"])
chk("校验永不抛异常（各种垃圾输入）",
    all(isinstance(verify(x), dict) for x in
        ["", "  ", "\n\n", "LLBS1-", "LLBS1-a.b", "LLBS1-!!!.???", "x" * 500]))
chk("extract_code 能从一堆说明文字里抠出授权码",
    L.extract_code(f"你好，这是你的授权码：\n{c365}\n请查收") == c365)
chk("extract_code 容忍首尾空白", L.extract_code(f"  {c365}  \n") == c365)
chk("授权码长度适合复制粘贴", 100 < len(c365) < 1200, str(len(c365)))

# 4.6 payload 解析
_pl = L.parse_payload(L.build_payload(customer="李四", machine="ANY", days=7))
chk("payload 含客户名", _pl.get("c") == "李四", str(_pl))
chk("payload 含机器绑定", _pl.get("m") == "ANY")
chk("payload 含功能位", _pl.get("f") == "all")
chk("payload 含 nonce（每次不同）",
    L.parse_payload(L.build_payload(machine="ANY"))["n"] !=
    L.parse_payload(L.build_payload(machine="ANY"))["n"])
chk("客户名里的分隔符被清洗（不会破坏载荷）",
    "|" not in L.parse_payload(L.build_payload(customer="a|b|c", machine="ANY"))["c"])

# ======================================================================
# 5. .lic 文件读写
# ======================================================================
# 注意：产品私钥在 tools/ 下、不进测试环境，所以这里把程序内置公钥
# 临时换成测试公钥，用来验证 activate() / export_license_file() 这些
# 走「默认公钥」的完整路径也能正常工作。测试结束前会还原。
_REAL_PK = L.LICENSE_PUBLIC_KEY_HEX
chk("用非产品私钥签的码，在真实产品公钥下会被拒绝（这是防伪造的关键）",
    not L.verify_license(c365)["valid"], L.verify_license(c365)["reason"])
L.LICENSE_PUBLIC_KEY_HEX = _PK

_lic_file = Path(_TMP) / "客户_授权.lic"
L.export_license_file(c365, _lic_file, note="测试")
chk(".lic 文件已生成", _lic_file.is_file())
chk(".lic 读回的码与原件一致", L.read_license_file(_lic_file) == c365)
chk(".lic 里带客户名", "张三" in _lic_file.read_text(encoding="utf-8"))
_txt = Path(_TMP) / "plain.txt"
_txt.write_text(f"随便写点说明\n{c365}\n", encoding="utf-8")
chk("纯文本文件也能读出授权码", L.read_license_file(_txt) == c365)
chk("不存在的文件返回空", L.read_license_file(Path(_TMP) / "nope.lic") == "")

# ======================================================================
# 6. 本地状态机：试用 / 激活 / 过期 / 防回拨
# ======================================================================
L.deactivate()
st = L.status(reload=True)
chk("全新环境：处于试用状态", st["state"] == "trial", str(st))
chk("全新环境：试用天数为 " + str(L.TRIAL_DAYS),
    2 < (st["days_left"] or 0) <= L.TRIAL_DAYS, str(st["days_left"]))
chk("试用期文案含「试用」", "试用" in st["label"], st["label"])
chk("试用期内功能全开", all(L.check(f)[0] for f in L.ALL_FEATURES))

L.touch()
st2 = L.status(reload=True)
chk("touch 之后仍是试用", st2["state"] == "trial")

# 6.1 激活
ok, m = L.activate(c365)
st3 = L.status(reload=True)
chk("激活成功", ok, m)
chk("激活后状态为 licensed", st3["state"] == "licensed", str(st3))
chk("激活后能读到内嵌 Token", st3["embedded_token"] == "MY-TOKEN-1")
chk("激活后 embedded_token() 可取用", L.embedded_token() == "MY-TOKEN-1")
chk("激活后常规功能全开", all(L.check(f)[0] for f in _REGULAR))
chk("这张默认码用不了独立付费模块 GEO（需单独开通）",
    L.check("geo")[0] is False, L.check("geo")[1])
chk("激活后不再是试用", not L.is_trial())
chk("已激活文案含有效期", "有效期至" in st3["label"], st3["label"])

ok2, m2 = L.activate("完全无效的码")
chk("激活无效码失败", not ok2)
chk("失败原因是签名/格式", "签名" in m2 or "格式" in m2 or "截断" in m2, m2)
chk("失败不会破坏已有授权", L.status(reload=True)["state"] == "licensed")

# 6.2 部分功能的门禁
ok3, _ = L.activate(issue(machine="ANY", days=30, features="collect"))
st4 = L.status(reload=True)
chk("切换到部分功能授权成功", ok3)
chk("授权内的功能可用", L.check("collect")[0])
chk("授权外的功能被挡", not L.check("transcribe")[0])
chk("被挡时给出中文原因", "不包含" in L.check("transcribe")[1], L.check("transcribe")[1])

# 6.3 解除授权
L.deactivate()
chk("解除授权后回到试用", L.status(reload=True)["state"] == "trial")

# 6.4 试用到期
state = L.load_state()
state["trial_start"] = time.time() - (L.TRIAL_DAYS + 1) * 86400
state["tamper"] = 0
L.save_state(state)
L.deactivate()
st5 = L.status(reload=True)
# ★ v1.14.0 起：试用结束不再把软件锁死，而是降为「免费版」——
#   视频下载与直播录制两条零成本链路永久可用，其余功能需要激活。
#   把人锁死只会让他卸载，留一个能用的入口才有转化机会。
chk("试用超期 → 免费版状态", st5["state"] == "free", str(st5))
chk("免费版：视频下载仍可用", L.check("download")[0])
chk("免费版：直播录制仍可用", L.check("live")[0])
chk("免费版：付费功能被挡",
    not any(L.check(f)[0] for f in L.ALL_FEATURES if f not in L.FREE_FEATURES))
chk("免费版提示写清了可用范围",
    "视频下载" in st5["label"] and "直播录制" in st5["label"], st5["label"])
chk("过期时 purchase_hint 提示机器码", L.machine_id() in L.purchase_hint())

# 6.5 时钟回拨防护
state = L.load_state()
state["trial_start"] = time.time()
state["last_seen"] = time.time() + 10 * 86400      # 假装上次运行在 10 天后
state["tamper"] = 0
L.save_state(state)
L.touch()
chk("时钟回拨一次 → 记一次篡改", L.load_state().get("tamper") == 1,
    str(L.load_state().get("tamper")))
state = L.load_state()
state["tamper"] = 3
L.save_state(state)
st6 = L.status(reload=True)
chk("篡改累计 3 次 → 直接过期", st6["state"] == "expired", str(st6))
chk("篡改过期文案提到系统时间", "系统时间" in st6["label"], st6["label"])
chk("★ 改时间被抓到时连免费功能也不给（否则试用形同虚设）",
    not L.check("download")[0])

# 6.6 付费客户授权到期 —— 同样是降级，不是锁死
#    老客户付过钱，到期后不能连"存个视频"都不让，那是最伤口碑的做法。
state = L.load_state()
state["license"] = issue(machine="ANY", days=-1)
state["tamper"] = 0
L.save_state(state)
st7 = L.status(reload=True)
chk("已付费但授权过期 → expired 状态", st7["state"] == "expired", str(st7))
chk("★ 授权过期的老客户仍能用下载与录制", L.check("download")[0] and L.check("live")[0])
chk("★ 授权过期的老客户：付费功能被挡", not L.check("collect")[0])
L.deactivate()

# ======================================================================
# 7. 基础品牌与版本工具
# ======================================================================
chk("软件名已更名", B.APP_NAME == "流量捕手", B.APP_NAME)
chk("exe 名跟随软件名", B.EXE_NAME == B.APP_NAME)
chk("版本号是 x.y.z 形式", len(B.version_tuple(B.APP_VERSION)) >= 3)
chk("版本比较：1.0.1 > 1.0.0", B.is_newer("1.0.1", "1.0.0"))
chk("版本比较：v1.2 > 1.1.9", B.is_newer("v1.2", "1.1.9"))
chk("版本比较：相同版本不算新", not B.is_newer("1.0.0", "1.0.0"))
chk("版本比较：旧版本不算新", not B.is_newer("0.9.9", "1.0.0"))
chk("版本比较：乱七八糟的后缀不会崩",
    B.version_tuple("1.2.3-beta+build5") == (1, 2, 3),
    str(B.version_tuple("1.2.3-beta+build5")))
chk("版本比较：空/乱串回退到 0.0.0",
    B.version_tuple("") == (0, 0, 0) and B.version_tuple("abc") == (0, 0, 0),
    str(B.version_tuple("abc")))
chk("数据目录名是纯 ASCII（避免中文路径踩坑）",
    B.DATA_DIR_NAME.isascii(), B.DATA_DIR_NAME)
chk("素材目录跟随品牌", B.DESKTOP_WORK_DIR.startswith(B.APP_NAME))
chk("功能清单是中文可读的", all(isinstance(v, str) for v in L.FEATURES.values()))


L.LICENSE_PUBLIC_KEY_HEX = _REAL_PK          # 还原，避免影响其它测试套件
chk("公钥已还原为产品公钥", L.LICENSE_PUBLIC_KEY_HEX == _REAL_PK)


for d in _TMP_DIRS:
    shutil.rmtree(d, ignore_errors=True)

print()
print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  " + f)
    sys.exit(1)
