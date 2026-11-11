# -*- coding: utf-8 -*-
"""授权码体检 —— 检查一张已发出的码到底能不能让客户用起来。

为什么需要它（2026-10-12 客户投诉）：
    客户激活成功却一直提示「未连接采集服务」，根因是**码里没带采集凭证**。
    这种码在软件里看起来一切正常（授权状态显示"已授权"），只有真的去采集
    才会暴露。发出去的码有几十张，总不能一张张让客户去试。

用法：
    # 单张
    python tools/check_license.py "eyJ...整串授权码"

    # 从文件读（每行一张码，自动跳过空行/注释）
    python tools/check_license.py --file 已发出的码.txt

    # 手边没有码？至少确认自己的 Token 在不在（决定出码有没有凭证）
    python tools/check_license.py --my-token
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from justoneapi_collector import branding, licensing      # noqa: E402


def my_token() -> int:
    """打印本机可用于出码的 Token（就是你签名时该填进去的那个）。"""
    tok = ""
    code = licensing.imported_license_code()
    if code:
        tok = (licensing.verify_license(code).get("embedded_token") or "").strip()
    src = "本机已激活的授权码"
    if not tok:
        try:
            import json
            cfg = branding.app_data_dir() / "config.json"
            data = json.loads(cfg.read_text(encoding="utf-8"))
            tok = str(data.get("token") or data.get("api_token") or "").strip()
            src = "本机 config.json"
        except Exception:                                    # noqa: BLE001
            tok = ""
            src = "未找到"
    if tok:
        print(f"✔ 可用于出码的 Token：{tok[:10]}…（共 {len(tok)} 字符，来自{src}）")
        print("  出码时点「用我本机的 Token」把它填进去，客户激活后即可采集。")
        return 0
    print("✘ 本机没有找到可用的 Token。")
    print("  此时签发的码【不含采集凭证】，客户激活后会一直提示「未连接采集服务」。")
    print("  请先把你的 JustOneAPI Token 填进 config.json 的 token 字段。")
    return 1


def check_one(code: str, name: str = "") -> bool:
    """检查一张码，返回「客户能否直接采集」。"""
    code = (code or "").strip()
    label = name or (code[:18] + "…" if len(code) > 18 else code)
    if not code:
        print(f"  ✘ {label} —— 空码")
        return False

    info = licensing.verify_license(code)
    print(f"\n── {label} ──")
    if not info.get("valid"):
        print(f"  ✘ 验签不通过：{info.get('reason') or '未知原因'}")
        print("     （可能被抄错、被改动，或不是本密钥对签发的）")
        return False

    cust = info.get("customer") or "（未填写）"
    exp = info.get("expires_at") or 0
    days = info.get("days_left")
    import time
    exp_txt = "永久" if not exp else (
        f"{time.strftime('%Y-%m-%d', time.localtime(exp))}"
        + (f"（剩 {days:.1f} 天）" if days is not None else ""))
    q = info.get("daily_quota")
    q_txt = "不限" if q == licensing.UNLIMITED else f"{q} 次/天"
    feats = info.get("features") or []
    print(f"  ✔ 验签通过")
    print(f"    客户      : {cust}")
    print(f"    有效期    : {exp_txt}")
    print(f"    每日额度  : {q_txt}")
    print(f"    功能位    : {'、'.join(feats) if feats else '（空 = 全部）'}")

    tok = (info.get("embedded_token") or "").strip()
    machine = info.get("machine_id") or licensing.ANY_MACHINE
    if tok:
        print(f"    采集凭证  : ✔ 有（{tok[:8]}…，{len(tok)} 字符）")
    else:
        print(f"    采集凭证  : ✘ 【没有】—— 这就是客户报「未连接采集服务」的原因")

    if info.get("machine_ok") is False:
        print(f"    机器绑定  : ✘ 该码绑定 {machine}，当前机器不匹配"
              f"（客户会激活失败，提示机器码不符）")
    else:
        print(f"    机器绑定  : ✔ {machine}")

    usable = bool(tok) and info.get("machine_ok") is not False
    print(f"  ==> 客户激活后能否直接采集：{'能' if usable else '不能'}")
    if not usable and not tok:
        print(f"      处理：用「用我本机的 Token」重新签一张发给 {cust}")
    return usable


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "--my-token":
        return my_token()

    if argv[0] == "--file":
        if len(argv) < 2:
            print("用法：python tools/check_license.py --file 已发出的码.txt")
            return 1
        p = Path(argv[1])
        if not p.is_file():
            print(f"找不到文件：{p}")
            return 1
        lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()]
        codes = [l for l in lines if l and not l.startswith("#")]
        print(f"共读到 {len(codes)} 张码")
        bad = sum(0 if check_one(c) else 1 for c in codes)
        print(f"\n{'=' * 60}")
        print(f"合计 {len(codes)} 张：可用 {len(codes) - bad} 张，"
              f"不可用 {bad} 张")
        if bad:
            print("不可用的那些请重签后再发给客户（点「用我本机的 Token」）。")
        return 0 if bad == 0 else 1

    ok = all(check_one(c) for c in argv)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
