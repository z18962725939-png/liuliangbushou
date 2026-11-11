# -*- coding: utf-8 -*-
"""GEO 引擎模型名 / 参数兼容性矩阵探针（真机）。

逐个试「模型名 × temperature」，找出真正能稳定作答的组合，避免又把下线的
模型名写进默认值。Key 走环境变量，不落盘。
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

QUERY = "进口重卡品牌怎么选？请给几个具体品牌名。回答控制在 200 字内。"
SYSTEM = ("你是资深行业顾问。请直接给出具体品牌或产品名单，不要只给抽象方法论。"
          "只依据你已有的知识回答，回答控制在 400 字以内，用中文。")


def call(base_url: str, key: str, model: str, temperature: float,
         timeout: float = 60.0) -> tuple[bool, str, float]:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {"model": model, "stream": False, "temperature": temperature,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": QUERY}]}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "Accept": "application/json"})
    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            txt = r.read().decode("utf-8", errors="replace")
        obj = json.loads(txt)
        content = (((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return True, content, time.time() - t0
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:220]
        except Exception:                                     # noqa: BLE001
            pass
        return False, f"HTTP {e.code} {detail}", time.time() - t0
    except Exception as e:                                    # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", time.time() - t0


def sweep(title: str, base_url: str, key: str, models: list[str], temps: list[float]):
    print(f"\n{'=' * 70}\n{title}  {base_url}\n{'=' * 70}")
    print(f"{'模型':<22}{'temp':<7}{'结果':<7}{'耗时':<8}摘要")
    print("-" * 70)
    best = None
    for m in models:
        for t in temps:
            ok, txt, el = call(base_url, key, m, t)
            flag = "✓" if ok else "✗"
            head = (txt[:46] if ok else txt[:60]).replace("\n", " ")
            print(f"{m:<22}{t:<7}{flag:<7}{el:<8.1f}{head}")
            if ok and best is None:
                best = (m, t, el, txt)
    print("-" * 70)
    if best:
        print(f"推荐默认：model={best[0]}  temperature={best[1]}  耗时={best[2]:.1f}s")
        print("完整回答：")
        for line in best[3][:400].splitlines():
            print("  | " + line)
    else:
        print("✗ 没有任何组合可用")
    return best


def main():
    ds = os.environ.get("GEO_DS_KEY", "").strip()
    kimi = os.environ.get("GEO_KIMI_KEY", "").strip()

    if ds:
        sweep("DeepSeek", "https://api.deepseek.com/v1", ds,
              ["deepseek-chat", "deepseek-flash", "deepseek-v4-pro"], [0.6, 1.0])
    if kimi:
        sweep("Kimi", "https://api.moonshot.cn/v1", kimi,
              ["kimi-k2.6", "kimi-k2.7-code", "kimi-k3"], [0.6, 1.0])


if __name__ == "__main__":
    main()
