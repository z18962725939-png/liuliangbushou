# -*- coding: utf-8 -*-
"""诊断「流量捕手.cn」在腾讯云上的接入状态：CDN 加速域名 / HTTPS 证书 / 域名实名。

用主账号密钥（cos_config.json.bak）只读查询，不改任何配置。
用法： python tools/_diag_domain.py
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "tools" / "cos_config.json.bak"

from tencentcloud.common import credential  # noqa: E402
from tencentcloud.common.profile.client_profile import ClientProfile  # noqa: E402
from tencentcloud.common.profile.http_profile import HttpProfile  # noqa: E402

cfg = json.loads(CFG.read_text(encoding="utf-8"))
cred = credential.Credential(cfg["secret_id"], cfg["secret_key"])

SERVICES = {
    "cdn": ("v20180606", "cdn.tencentcloudapi.com", "CdnClient"),
    "ssl": ("v20191205", "ssl.tencentcloudapi.com", "SslClient"),
    "domain": ("v20180808", "domain.tencentcloudapi.com", "DomainClient"),
    "teo": ("v20220901", "teo.tencentcloudapi.com", "TeoClient"),
}


def make(service: str):
    ver, ep, cls = SERVICES[service]
    mm = importlib.import_module(f"tencentcloud.{service}.{ver}.models")
    mc = importlib.import_module(f"tencentcloud.{service}.{ver}.{service}_client")
    hp = HttpProfile()
    hp.endpoint = ep
    cp = ClientProfile()
    cp.httpProfile = hp
    return getattr(mc, cls)(cred, "", cp), mm


def call(service: str, action: str, **kw):
    cli, mm = make(service)
    req = getattr(mm, action + "Request")()
    for k, v in kw.items():
        setattr(req, k, v)
    return json.loads(getattr(cli, action)(req).to_json_string())


def show(title: str):
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


show("1) CDN 加速域名")
try:
    d = call("cdn", "DescribeDomainsConfig", Offset=0, Limit=100)
    print(f"   共 {d.get('TotalNumber', 0)} 个")
    for it in d.get("Domains", []) or []:
        https = it.get("Https") or {}
        ci = https.get("CertInfo") or {}
        origin = (it.get("Origin") or {}).get("Origins") or []
        osrc = ", ".join(f"{o.get('Origins')}({o.get('ServerName', '')})" for o in origin)
        print(f"\n   ▸ {it.get('Domain')}   状态={it.get('Status')}")
        print(f"     CNAME      : {it.get('Cname')}")
        print(f"     回源       : {osrc}")
        print(f"     HTTPS      : switch={https.get('Switch')} 证书ID={ci.get('CertId')} 证书名={ci.get('CertName')}")
        print(f"     强制跳转   : {(it.get('ForceRedirect') or {}).get('Switch')}")
        print(f"     创建时间   : {it.get('CreateTime')}")
except Exception as e:  # noqa: BLE001
    print(f"   [查询失败] {type(e).__name__}: {e}")

show("2) SSL 证书列表")
try:
    d = call("ssl", "DescribeCertificates", Offset=0, Limit=100)
    print(f"   共 {d.get('TotalCount', 0)} 张")
    for c in d.get("Certificates", []) or []:
        print(f"   ▸ {c.get('Domain')}  状态={c.get('StatusName')}  ID={c.get('CertificateId')}  到期={c.get('CertEndTime')}")
except Exception as e:  # noqa: BLE001
    print(f"   [查询失败] {type(e).__name__}: {e}")

show("3) 域名注册与实名")
try:
    d = call("domain", "DescribeDomainNameList", Offset=0, Limit=100)
    print(f"   共 {d.get('TotalCount', 0)} 个")
    for it in d.get("DomainSet", []) or []:
        print(f"   ▸ {it.get('DomainName')}  实名={it.get('IsVerified')}  状态={it.get('DomainStatus')}  到期={it.get('ExpirationDate')}")
except Exception as e:  # noqa: BLE001
    print(f"   [查询失败] {type(e).__name__}: {e}")

show("4) EdgeOne 站点")
try:
    d = call("teo", "DescribeZones", Offset=0, Limit=100)
    print(f"   共 {d.get('TotalCount', 0)} 个")
    for z in d.get("Zones", []) or []:
        print(f"   ▸ {z.get('ZoneName')}  状态={z.get('Status')}  ID={z.get('ZoneId')}")
except Exception as e:  # noqa: BLE001
    print(f"   [查询失败] {type(e).__name__}: {e}")
