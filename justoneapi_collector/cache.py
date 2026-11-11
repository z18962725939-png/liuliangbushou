# -*- coding: utf-8 -*-
"""API 响应缓存（磁盘 JSON）。

为什么单独成文件：Client 每次请求前先看缓存，命中就直接返回（并在 raw_json 里
打 `_cached: True`），省一次接口调用。所有平台共用同一份实现。

    >>> cache = ResponseCache(cache_dir=".justoneapi_cache", ttl=3600, enabled=True)
    >>> cache.set("douyin", "aweme_detail", {"aweme_id": "123"}, {"title": "hi"})
    >>> cache.get("douyin", "aweme_detail", {"aweme_id": "123"})
    {'title': 'hi'}

★ 2026-10-29 重建说明：本文件是工程创始文件之一，源码丢失后按 client.py 的调用
  契约重新实现。对外接口就三处 —— 构造参数(cache_dir/ttl/enabled)、.enabled、
  .get()/.set()，与调用方完全一致。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

__all__ = ["ResponseCache"]


class ResponseCache:
    """简单的 TTL 磁盘缓存。禁用了就什么都不做（上层照样调，不用到处判空）。"""

    def __init__(self, cache_dir: str = ".justoneapi_cache",
                 ttl: int = 3600, enabled: bool = True) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl = int(ttl) if ttl else 0
        self.enabled = bool(enabled)

    # -- 内部 ------------------------------------------------------------

    def _make_key(self, platform: str, endpoint: str, params: dict | None) -> str:
        """同一个平台+端点+参数 => 同一个 key。参数顺序不影响结果。"""
        raw = json.dumps(
            {"p": platform, "e": endpoint, "q": params or {}},
            sort_keys=True, ensure_ascii=False, default=str,
        )
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _file(self, key: str) -> Path:
        # 取前 2 位做子目录，避免单目录几万文件时 Windows 资源管理器卡死
        return self.cache_dir / key[:2] / (key + ".json")

    # -- 对外 ------------------------------------------------------------

    def get(self, platform: str, endpoint: str, params: dict | None) -> Any | None:
        """命中且未过期返回数据；未命中/过期/禁用一律返回 None（调用方直接判空）。"""
        if not self.enabled:
            return None
        path = self._file(self._make_key(platform, endpoint, params))
        try:
            if not path.is_file():
                return None
            if self.ttl > 0 and (time.time() - path.stat().st_mtime) > self.ttl:
                try:
                    path.unlink()
                except OSError:
                    pass
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("data")
        except Exception:
            # 缓存坏了不该拖垮主流程：读不出来就当没缓存
            return None

    def set(self, platform: str, endpoint: str, params: dict | None, data: Any) -> None:
        """写缓存。任何写入异常都吞掉 —— 缓存失败不能影响请求本身。"""
        if not self.enabled:
            return
        path = self._file(self._make_key(platform, endpoint, params))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"ts": int(time.time()), "data": data},
                           ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass

    # -- 维护 ------------------------------------------------------------

    def clear(self) -> int:
        """清空整个缓存目录，返回删除的文件数。"""
        removed = 0
        if not self.cache_dir.is_dir():
            return 0
        for p in self.cache_dir.rglob("*.json"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def purge_expired(self) -> int:
        """删掉已过期的条目，返回删除数。ttl<=0 表示永不过期。"""
        if not self.enabled or self.ttl <= 0 or not self.cache_dir.is_dir():
            return 0
        removed = 0
        now = time.time()
        for p in self.cache_dir.rglob("*.json"):
            try:
                if (now - p.stat().st_mtime) > self.ttl:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    def size(self) -> int:
        """缓存占用字节数。"""
        if not self.cache_dir.is_dir():
            return 0
        return sum(p.stat().st_size for p in self.cache_dir.rglob("*.json")
                   if p.is_file())

    def __repr__(self) -> str:  # pragma: no cover
        return (f"ResponseCache(dir={str(self.cache_dir)!r}, ttl={self.ttl}, "
                f"enabled={self.enabled})")
