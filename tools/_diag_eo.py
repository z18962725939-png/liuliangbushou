# -*- coding: utf-8 -*-
"""查 EdgeOne 加速域名与 DNS 解析记录（只读）。"""
from __future__ import annotations

import importlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "tools" / "cos_config.json.bak").read_text(encoding="utf-8"))

from tencentcloud.common import credential  # noqa: E402
from tencentcloud.common.profile.client_profile import ClientProfile  # noqa: E402
from tencentcloud.common.profile.http_profile import HttpProfile  # noqa: E402

cred = credential.Credential(cfg["secret_id"], cfg["secret_key"])


def call(service, ver, endpoint, cls, action, **kw):
    mm = importlib.import_module(f"tencentcloud.{service}.{ver}.models")
    mc = importlib.import_module(f"tencentcloud.{service}.{ver}.{service}_client")
    hp = HttpProfile()
    hp.endpoint = endpoint
    cp = ClientProfile()
    cp.httpProfile = hp
    cli = getattr(mc, cls)(cred, "", cp)
    req = getattr(mm, action + "Request")()
    for k, v in kw.items():
        setattr(req, k, v)
    return json.loads(getattr(cli, action)(req).to_json_string())


print("=" * 60)
print("EdgeOne 站点列表")
print("=" * 60)
try:
    d = call("teo", "v20220901", "teo.tencentcloudapi.com", "TeoClient", "DescribeZones", Offset=0, Limit=100)
    for z in d.get("Zones", []) or []:
        print(f"  ▸ {z.get('ZoneName')}  status={z.get('Status')}  id={z.get('ZoneId')}  type={z.get('Type')}")
        print(f"    {json.dumps(z, ensure_ascii=False)[:400]}")
except Exception as e:  # noqa: BLE001
    print(f"  [失败] {type(e).__name__}: {e}")

print()
print("=" * 60)
print("EdgeOne 加速域名")
print("=" * 60)
for zid in ("zone-3w140zooqzo5",):
    try:
        d = call("teo", "v20220901", "teo.tencentcloudapi.com", "TeoClient",
                 "DescribeAccelerationDomains", ZoneId=zid, Offset=0, Limit=100)
        print(f"  zone={zid} 共 {d.get('TotalCount', 0)} 个")
        for it in d.get("AccelerationDomains", []) or []:
            print(f"  ▸ {it.get('DomainName')}  状态={it.get('DomainStatus')}  CNAME={it.get('Cname')}")
            print(f"    证书: {json.dumps(it.get('Certificate') or {}, ensure_ascii=False)}")
            print(f"    {json.dumps(it, ensure_ascii=False)[:600]}")
    except Exception as e:  # noqa: BLE001
        print(f"  [失败] {type(e).__name__}: {e}")

print()
print("=" * 60)
print("DNSPod 解析记录")
print("=" * 60)
for dom in ("xn--kpuzw91vr71b.cn", "流量捕手.cn"):
    try:
        d = call("dnspod", "v20210323", "dnspod.tencentcloudapi.com", "DnspodClient",
                 "DescribeRecordList", Domain=dom, Limit=100)
        print(f"  {dom}: 共 {d.get('RecordCountInfo', {}).get('TotalCount', 0)} 条")
        for r in d.get("RecordList", []) or []:
            print(f"   ▸ {r.get('Name')}.  {r.get('Type')}  →  {r.get('Value')}   (line={r.get('Line')})")
    except Exception as e:  # noqa: BLE001
        print(f"  {dom}: [失败] {type(e).__name__}: {str(e)[:160]}")
