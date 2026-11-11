# -*- coding: utf-8 -*-
"""查看 EdgeOne 站点完整信息，并尝试触发域名归属验证（只读为主）。"""
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


def call(action: str, **kw):
    ver, ep = "v20220901", "teo.tencentcloudapi.com"
    mm = importlib.import_module(f"tencentcloud.teo.{ver}.models")
    mc = importlib.import_module(f"tencentcloud.teo.{ver}.teo_client")
    hp = HttpProfile()
    hp.endpoint = ep
    cp = ClientProfile()
    cp.httpProfile = hp
    cli = getattr(mc, "TeoClient")(cred, "", cp)
    req = getattr(mm, action + "Request")()
    for k, v in kw.items():
        setattr(req, k, v)
    return json.loads(getattr(cli, action)(req).to_json_string())


print("=" * 60)
print("站点完整信息")
print("=" * 60)
try:
    d = call("DescribeZones", Offset=0, Limit=100)
    print(json.dumps(d, ensure_ascii=False, indent=2)[:2500])
except Exception as e:  # noqa: BLE001
    print(f"[失败] {type(e).__name__}: {e}")

print()
print("=" * 60)
print("尝试 IdentifyZone（查域名归属验证所需记录）")
print("=" * 60)
for dom in ("xn--kpuzw91vr71b.cn",):
    try:
        d = call("IdentifyZone", ZoneName=dom)
        print(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        print(f"  {dom}: [失败] {type(e).__name__}: {str(e)[:300]}")
