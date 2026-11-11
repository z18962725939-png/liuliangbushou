# -*- coding: utf-8 -*-
"""修正 EdgeOne 域名归属验证记录，让站点从 pending 转 active（证书才能签发）。

背景（2026-10-11）：
    流量捕手.cn 的 CNAME 已指向 EdgeOne Makers，但站点一直 Status=pending、
    CnameStatus=pending，浏览器报 ERR_CERT_COMMON_NAME_INVALID（拿到的是 CDN 默认证书）。
    根因是 DNS 里 edgeonereclaim 这条 TXT 的**值与平台要求的不一致**。

用法：
    python tools/fix_domain_verify.py            # 只看差异（不改动）
    python tools/fix_domain_verify.py --apply    # 写入正确值并触发验证
"""
from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "tools" / "cos_config.json.bak").read_text(encoding="utf-8"))

from tencentcloud.common import credential  # noqa: E402
from tencentcloud.common.profile.client_profile import ClientProfile  # noqa: E402
from tencentcloud.common.profile.http_profile import HttpProfile  # noqa: E402

cred = credential.Credential(cfg["secret_id"], cfg["secret_key"])

DOMAIN_PUNY = "xn--kpuzw91vr71b.cn"
DOMAIN_CN = "流量捕手.cn"


def _client(svc: str, ver: str, ep: str, cls: str):
    hp = HttpProfile()
    hp.endpoint = ep
    cp = ClientProfile()
    cp.httpProfile = hp
    mc = importlib.import_module(f"tencentcloud.{svc}.{ver}.{svc}_client")
    return getattr(mc, cls)(cred, "", cp)


def call(svc, ver, ep, cls, action, **kw):
    mm = importlib.import_module(f"tencentcloud.{svc}.{ver}.models")
    cli = _client(svc, ver, ep, cls)
    req = getattr(mm, action + "Request")()
    for k, v in kw.items():
        setattr(req, k, v)
    return json.loads(getattr(cli, action)(req).to_json_string())


TEO = ("teo", "v20220901", "teo.tencentcloudapi.com", "TeoClient")
DNS = ("dnspod", "v20210323", "dnspod.tencentcloudapi.com", "DnspodClient")

print("=" * 64)
print("1) 平台要求的归属验证记录")
print("=" * 64)
want = call(*TEO, "IdentifyZone", ZoneName=DOMAIN_PUNY)
asc = want.get("Ascription") or {}
sub = asc.get("Subdomain")
rtype = asc.get("RecordType")
value_want = asc.get("RecordValue")
print(f"   主机记录 : {sub}")
print(f"   类型     : {rtype}")
print(f"   期望值   : {value_want}")

print()
print("=" * 64)
print("2) 当前 DNS 里的实际情况")
print("=" * 64)
recs = call(*DNS, "DescribeRecordList", Domain=DOMAIN_PUNY, Limit=100)
target = None
for r in recs.get("RecordList", []) or []:
    print(f"   {r.get('Name'):<18} {r.get('Type'):<6} {r.get('Value')}")
    if r.get("Name") == sub and r.get("Type") == rtype:
        target = r
if not target:
    print(f"   [找不到 {sub} 的 {rtype} 记录]")
    sys.exit(1)

value_now = (target.get("Value") or "").strip()
print()
print(f"   当前值   : {value_now!r}")
print(f"   是否一致 : {'一致' if value_now == value_want else '★ 不一致 → 需要修正'}")

if value_now == value_want:
    print("\n   记录已正确，无需改动。")
    args = argparse.Namespace(apply=False)
else:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入正确值")
    args = ap.parse_args()

if not args.apply:
    print("\n[预览模式] 未做任何修改。加 --apply 才会写入。")
    sys.exit(0)

print()
print("=" * 64)
print("3) 写入正确值")
print("=" * 64)
call(*DNS, "ModifyRecord",
     Domain=DOMAIN_PUNY, RecordId=target.get("RecordId"),
     SubDomain=sub, RecordType=rtype, RecordLine=target.get("Line") or "默认",
     Value=value_want, TTL=600)
print("   [OK] 已更新 TXT 记录")

print()
print("=" * 64)
print("4) 触发域名归属验证")
print("=" * 64)
try:
    v = call(*TEO, "VerifyOwnership", Domain=DOMAIN_PUNY)
    print(f"   结果: {json.dumps(v, ensure_ascii=False)}")
except Exception as e:  # noqa: BLE001
    print(f"   [验证调用失败] {type(e).__name__}: {str(e)[:240]}")

print()
print("=" * 64)
print("5) 站点状态")
print("=" * 64)
z = call(*TEO, "DescribeZones", Offset=0, Limit=100)["Zones"][0]
print(f"   Status={z.get('Status')}  CnameStatus={z.get('CnameStatus')}  ActiveStatus={z.get('ActiveStatus')}")
