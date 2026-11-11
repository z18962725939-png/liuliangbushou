"""接口请求重试（同步 / 异步两版）。

★ 为什么单独一个模块：client 的每次请求都要「网络抖动自动重来、业务错误立刻
  放弃」，两条路都写一遍必然走偏。集中在这里，改重试策略只改一处。

判定原则（很关键）：
  · **只有传输层错误才重试** —— TransportError（超时、连接断开、DNS 失败），
    重来一次大概率能成。
  · **业务错误一律不重试** —— BusinessError 及其子类（余额不足、参数错误、
    限流、鉴权失败）。这些重试一百次也是同一个结果，还会白白吃掉用户额度
    （每次调用都是真金白银），必须立刻抛给上层处理。

延迟策略：指数退避 + 随机抖动（避免多个请求同时重试把接口打挂），
上限由 max_delay 兜住，不会因为 max_retries 很大就睡几分钟。

注：本文件是 2026-10-31 工程重建时按 client.py 的调用签名重写，
接口与原来保持一致（retry_sync / retry_async，参数名一致）。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, TypeVar

from .exceptions import TransportError

T = TypeVar("T")


def _sleep_backoff(attempt: int, base_delay: float, max_delay: float) -> None:
    """第 attempt 次重试前等一会儿（指数退避 + 抖动，不超过 max_delay）。"""
    delay = min(max_delay, base_delay * (2 ** max(0, attempt - 1)))
    if delay <= 0:
        return
    # 抖动 ±25%，打散并发重试
    time.sleep(delay * (0.75 + random.random() * 0.5))


def retry_sync(fn: Callable[[], T], max_retries: int = 3,
               base_delay: float = 0.5, max_delay: float = 8.0) -> T:
    """同步执行 fn，传输层失败时最多重试 max_retries 次。

    业务错误（BusinessError 及其子类）不重试，直接抛出。
    重试次数用尽后抛出最后一次的异常。
    """
    tries = max(0, int(max_retries or 0)) + 1      # max_retries=0 表示只跑一次
    last: BaseException | None = None
    for i in range(1, tries + 1):
        try:
            return fn()
        except TransportError as exc:              # 只有网络类才值得重来
            last = exc
            if i >= tries:
                break
            _sleep_backoff(i, float(base_delay or 0), float(max_delay or 0))
        except BaseException:                      # noqa: BLE001 - 业务错误/其它一律透传
            raise
    assert last is not None
    raise last


async def retry_async(fn: Callable[[], Any], max_retries: int = 3,
                      base_delay: float = 0.5, max_delay: float = 8.0) -> Any:
    """异步版 retry_sync：fn 是可等待对象工厂（调用后返回 coroutine）。"""
    tries = max(0, int(max_retries or 0)) + 1
    last: BaseException | None = None
    for i in range(1, tries + 1):
        try:
            return await fn()
        except TransportError as exc:
            last = exc
            if i >= tries:
                break
            delay = min(float(max_delay or 0),
                        float(base_delay or 0) * (2 ** max(0, i - 1)))
            if delay > 0:
                await asyncio.sleep(delay * (0.75 + random.random() * 0.5))
        except BaseException:                      # noqa: BLE001
            raise
    assert last is not None
    raise last
