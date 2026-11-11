# -*- coding: utf-8 -*-
"""
建一个「只能管发布这一个桶」的腾讯云子账号，并生成专用密钥，自动写回 cos_config.json。

为什么要有这个脚本
──────────────────
发版脚本要用到腾讯云密钥。如果用主账号密钥，一旦泄露 = 整个腾讯云账号
（可能还有服务器、数据库）被人接管。子账号只给这一个桶的读写权限，
泄露的损失也就是「别人能往你桶里传文件」，可控得多。

它做的事
────────
    1. 创建子用户（CAM CreateUser）
    2. 建一条自定义策略：只允许操作 config 里那一个桶（CreatePolicy）
    3. 把策略绑到子用户（AttachUserPolicy）
    4. 给子用户生成密钥对（CreateAccessKey）—— SecretKey 只在创建时返回一次
    5. 写回 tools/cos_config.json，并备份旧配置到 cos_config.json.bak
    6. 用新密钥验证：能不能 head_bucket、能不能上传并匿名读到

用法
────
    python tools/cam_setup_subuser.py              # 正常跑
    python tools/cam_setup_subuser.py --dry-run    # 只打印将要做什么，不真的建

前置：tools/cos_config.json 里必须是**主账号**密钥（子账号没权限建用户）。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CONFIG_FILE = TOOLS / "cos_config.json"

CAM_HOST = "cam.tencentcloudapi.com"
CAM_SERVICE = "cam"
CAM_VERSION = "2019-01-16"


# ──────────────────────────────────────────────────────────────────────
#  腾讯云 API TC3-HMAC-SHA256 签名（不依赖 SDK，装任何包都能跑）
# ──────────────────────────────────────────────────────────────────────
def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def call_api(action: str, params: dict[str, Any], secret_id: str, secret_key: str,
             region: str = "") -> dict[str, Any]:
    import urllib.error
    import urllib.request

    payload = json.dumps(params, ensure_ascii=False)
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))

    canonical_headers = f"content-type:application/json\nhost:{CAM_HOST}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        "POST", "/", "", canonical_headers, signed_headers, hashed_payload,
    ])

    algo = "TC3-HMAC-SHA256"
    credential_scope = f"{date}/{CAM_SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        algo, str(timestamp), credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, CAM_SERVICE)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algo} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Host": CAM_HOST,
        "X-TC-Action": action,
        "X-TC-Version": CAM_VERSION,
        "X-TC-Timestamp": str(timestamp),
    }
    if region:
        headers["X-TC-Region"] = region

    req = urllib.request.Request(f"https://{CAM_HOST}/", data=payload.encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8", "ignore"))
        err = body.get("Response", {}).get("Error", {})
        raise RuntimeError(f"{err.get('Code')} {err.get('Message')}") from exc

    resp_body = body.get("Response", {})
    if "Error" in resp_body:
        err = resp_body["Error"]
        raise RuntimeError(f"{err.get('Code')} {err.get('Message')}")
    return resp_body


# ──────────────────────────────────────────────────────────────────────
#  策略文档：只允许操作这一个桶
# ──────────────────────────────────────────────────────────────────────
def policy_document(bucket: str, region: str, appid: str) -> str:
    actions = [
        "cos:GetService",                 # 列举桶列表（发版脚本不用，但排查时有用）
        "cos:HeadBucket",
        "cos:GetBucketACL",
        "cos:PutObject",
        "cos:GetObject",
        "cos:HeadObject",
        "cos:DeleteObject",
        "cos:InitiateMultipartUpload",
        "cos:UploadPart",
        "cos:CompleteMultipartUpload",
        "cos:AbortMultipartUpload",
        "cos:ListMultipartUploads",
        "cos:ListParts",
    ]
    # resource 里的 uid/<appid> 必须和桶的 AppID 一致，否则授权不生效
    return json.dumps({
        "version": "2.0",
        "statement": [{
            "effect": "allow",
            "action": actions,
            "resource": [
                f"qcs::cos:{region}:uid/{appid}:{bucket}/*",
                f"qcs::cos:{region}:uid/{appid}:{bucket}/",
            ],
        }],
    }, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────
#  主流程
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="创建 COS 专用子账号并替换发版密钥")
    ap.add_argument("--dry-run", action="store_true", help="只打印将做什么，不真的创建")
    ap.add_argument("--user", default="trafficcatcher-publisher", help="子用户名")
    ap.add_argument("--keep-old", action="store_true", help="保留旧配置不备份覆盖（默认会备份）")
    args = ap.parse_args()

    if not CONFIG_FILE.exists():
        print("[错误] 没有 tools/cos_config.json，先跑 python tools/setup_cos.py")
        return 1

    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    root_id, root_key = cfg["secret_id"], cfg["secret_key"]
    bucket, region = cfg["bucket"], cfg.get("region", "ap-guangzhou")
    appid = bucket.rsplit("-", 1)[-1]

    print("=" * 60)
    print(" 创建 COS 专用子账号")
    print("=" * 60)
    print(f"  桶       {bucket}（{region}）")
    print(f"  AppID    {appid}")
    print(f"  子用户名 {args.user}")
    print(f"  权限     仅该桶的读写，不含其他任何云资源")
    print()

    if args.dry_run:
        print("[dry-run] 将执行：")
        print(f"  1. CreateUser      {args.user}")
        print( "  2. CreatePolicy    仅允许操作 " + bucket)
        print( "  3. AttachUserPolicy 绑到子用户")
        print( "  4. CreateAccessKey 生成密钥对")
        print( "  5. 写回 cos_config.json（旧配置备份为 .bak）")
        print("\n确认无误后去掉 --dry-run 再跑。")
        return 0

    # ── 1) 建子用户（已存在就复用）──
    print("① 创建子用户…")
    try:
        # CAM 的建用户动作叫 AddUser（不是 CreateUser，用错会 InvalidAction）
        u = call_api("AddUser", {
            "Name": args.user,
            "Remark": "流量捕手发版专用，仅可操作 liuliangbushou 桶",
            "ConsoleLogin": 0,          # uint64，0=不允许登录控制台（只要密钥，不要控制台权限）
        }, root_id, root_key)
        uin = u.get("Uin")
        print(f"   [OK] 已创建，Uin={uin}")
    except RuntimeError as exc:
        # 重跑时用户名已占用，直接复用，不要因此中断
        # 注意：ListUsers 不认 Rp / Limit / Offset 这类分页参数（会 UnknownParameter），
        # 这里直接按名字精确查 GetUser 最稳。
        uin = None
        try:
            uin = call_api("GetUser", {"Name": args.user}, root_id, root_key).get("Uin")
        except RuntimeError:
            pass
        if uin:
            print(f"   [OK] 子用户已存在（{exc}），复用 Uin={uin}")
        else:
            print(f"   [失败] {exc}")
            print("   若提示权限不足，说明 cos_config.json 里放的已经是子账号密钥，")
            print("   子账号建不了用户——请临时换成主账号密钥后再跑一次。")
            return 2

    # ── 2) 建策略（已存在就复用）──
    print("② 创建最小权限策略…")
    policy_name = f"TrafficCatcher-Only-{appid}"
    doc = policy_document(bucket, region, appid)
    policy_id = None
    try:
        p = call_api("CreatePolicy", {"PolicyName": policy_name, "PolicyDocument": doc},
                     root_id, root_key)
        policy_id = p.get("PolicyId")
        print(f"   [OK] 已创建 PolicyId={policy_id}")
    except RuntimeError as exc:
        # CAM 对「已存在」的错误码不统一：PolicyNameInUse / SubUserNameInUse / AlreadyExists
        msg = str(exc).lower()
        if any(k in msg for k in ("alreadyexists", "inuse", "in use", "已存在")):
            # 只传 Scope：ListPolicies 同样不认 Rp/Page 分页参数
            lst = call_api("ListPolicies", {"Scope": "Local"}, root_id, root_key)
            for item in lst.get("List", []) or []:
                if item.get("PolicyName") == policy_name:
                    policy_id = item.get("PolicyId")
                    break
            print(f"   [OK] 策略已存在，复用 PolicyId={policy_id}")
        else:
            print(f"   [失败] {exc}")
            return 3
    if not policy_id:
        print("   [失败] 拿不到 PolicyId")
        return 3

    # ── 3) 绑定 ──
    print("③ 绑定策略到子用户…")
    try:
        call_api("AttachUserPolicy", {"PolicyId": policy_id, "AttachUin": uin},
                 root_id, root_key)
        print("   [OK] 已绑定")
    except RuntimeError as exc:
        print(f"   [提示] {exc}（若已绑定则无妨）")

    # ── 4) 生成密钥 ──
    print("④ 生成密钥对…")
    ak = call_api("CreateAccessKey", {"TargetUin": uin}, root_id, root_key)
    # CAM 返回的是 AccessKeyId / SecretAccessKey（和 COS 那套 SecretId/SecretKey 命名不同）
    ak_obj = ak.get("AccessKey") or ak
    new_id = ak_obj.get("SecretId") or ak_obj.get("AccessKeyId") or ""
    new_key = ak_obj.get("SecretKey") or ak_obj.get("SecretAccessKey") or ""
    if not new_id or not new_key:
        print(f"   [失败] 返回里没拿到密钥对：{json.dumps(ak, ensure_ascii=False)[:300]}")
        return 5
    print(f"   [OK] SecretId={new_id[:8]}…（SecretKey 已直接写入配置，不再显示）")

    # ── 5) 写回配置 ──
    if not args.keep_old:
        bak = CONFIG_FILE.with_suffix(".json.bak")
        bak.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"   旧配置已备份 → {bak.name}")

    cfg["secret_id"] = new_id
    cfg["secret_key"] = new_key
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"   已写入 {CONFIG_FILE.name}")

    # ── 6) 用新密钥验证 ──
    print("\n⑤ 用新密钥验证：")
    try:
        from qcloud_cos import CosConfig, CosS3Client  # type: ignore
    except ImportError:
        print("   [跳过] 没装 cos-python-sdk-v5")
        return 0

    try:
        c = CosS3Client(CosConfig(Region=region, SecretId=new_id, SecretKey=new_key, Scheme="https"))
        c.head_bucket(Bucket=bucket)
        print("   [OK] head_bucket 通过（能访问桶）")
        probe_key = f"{cfg.get('prefix','trafficcatcher').strip('/')}/_perm_check.txt"
        c.put_object(Bucket=bucket, Key=probe_key, Body=b"ok")
        print("   [OK] 上传通过（能写）")
        import urllib.request
        from urllib.parse import quote
        pub = f"https://{bucket}.cos.{region}.myqcloud.com/{quote(probe_key)}"
        got = urllib.request.urlopen(pub, timeout=20).read().decode()
        print(f"   [OK] 匿名可读（{got}）—— 客户能正常下载")
        c.delete_object(Bucket=bucket, Key=probe_key)
        print("   [OK] 探针文件已清理")
    except Exception as exc:  # noqa: BLE001
        print(f"   [失败] {exc}")
        print("   如果报权限不足，去控制台检查该子用户的策略是否绑定成功。")
        return 4

    print("\n完成。现在发版走的是子账号密钥，泄露也只是这个桶。")
    print("建议：去控制台把之前那把主账号密钥禁用掉（它已经用不上了）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
