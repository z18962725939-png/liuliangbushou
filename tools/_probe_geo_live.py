# -*- coding: utf-8 -*-
"""GEO 引擎真机探针：验证 Key 可用性、模型名是否存在、返回结构是否符合预期。

用法（Key 走环境变量，绝不写进仓库）：
    set GEO_DS_KEY=sk-xxxx
    set GEO_KIMI_KEY=sk-yyyy
    python tools/_probe_geo_live.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from justoneapi_collector import geo_monitor as gm           # noqa: E402


def _get(url: str, api_key: str, timeout: float = 20.0) -> tuple[bool, str]:
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return True, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: " + (e.read().decode("utf-8", "replace")[:300])
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def list_models(base_url: str, key: str) -> list[str]:
    ok, txt = _get(base_url.rstrip("/") + "/models", key)
    if not ok:
        print(f"    ✗ 拉取模型列表失败：{txt[:160]}")
        return []
    try:
        data = json.loads(txt)
        return sorted(str(m.get("id")) for m in (data.get("data") or []))
    except Exception as e:                                    # noqa: BLE001
        print(f"    ✗ 模型列表解析失败：{e}")
        return []


def probe(engine_key: str, api_key: str):
    spec = gm.ENGINE_SPECS[engine_key]
    print(f"\n{'=' * 66}\n【{spec.label}】{spec.base_url}  默认模型={spec.model}\n{'=' * 66}")

    if not api_key:
        print("    ⚠ 未提供 Key，跳过")
        return None

    models = list_models(spec.base_url, api_key)
    if models:
        print(f"    可用模型（{len(models)} 个）：")
        print("      " + ", ".join(models[:25]) + ("…" if len(models) > 25 else ""))
    has = spec.model in models if models else None
    if has is True:
        print(f"    ✓ 默认模型 {spec.model} 在列表中")
    elif has is False:
        print(f"    ✗ 默认模型 {spec.model} **不在**列表中，必须改名！")

    print("    —— 真实提问 ——")
    t0 = time.time()
    r = gm.ask_engine(spec, "进口重卡品牌怎么选？请给几个具体品牌名。",
                      api_keys={engine_key: api_key}, timeout=60.0)
    print(f"    ok={r.ok}  耗时={r.elapsed:.1f}s  错误={r.error or '无'}")
    if r.ok:
        print(f"    回答长度={len(r.text)}  引用信源={r.sources or '无'}")
        print("    回答前 300 字：")
        for line in r.text[:300].splitlines():
            print("      | " + line)
        # 命中检测自检
        vs = gm.brand_variants("斯堪尼亚", ["Scania"])
        print(f"    brand_variants('斯堪尼亚') = {vs}")
        print(f"    first_position = {gm.first_position(r.text, vs):.3f}（-1=未提及）")
        print(f"    detect_sentiment = {gm.detect_sentiment(r.text, vs)}")
    return r


def main():
    ds = os.environ.get("GEO_DS_KEY", "").strip()
    kimi = os.environ.get("GEO_KIMI_KEY", "").strip()
    print(f"DeepSeek Key: {'已提供 ' + ds[:8] + '…' if ds else '未提供'}")
    print(f"Kimi     Key: {'已提供 ' + kimi[:8] + '…' if kimi else '未提供'}")

    r1 = probe("deepseek", ds)
    r2 = probe("kimi", kimi)

    print(f"\n{'=' * 66}\n结论\n{'=' * 66}")
    for name, r in (("DeepSeek", r1), ("Kimi", r2)):
        if r is None:
            print(f"  {name}: 未测")
        elif r.ok:
            print(f"  {name}: ✓ 可用")
        else:
            print(f"  {name}: ✗ {r.error}")


if __name__ == "__main__":
    main()
