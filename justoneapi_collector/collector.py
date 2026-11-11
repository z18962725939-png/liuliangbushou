"""
Batch collection orchestration.

CollectTask defines a single collection unit (platform + endpoint + params).
BatchCollector runs multiple tasks concurrently with rate limiting and
optional auto-pagination, then aggregates results into a CollectReport.

Auto-pagination: if the endpoint supports page/cursor params, the collector
can automatically iterate until exhausted (no more data or max_pages reached).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .client import JustOneAPICollector
from .logger import get_logger
from .models import ApiResponse, CollectReport, CollectResult
from .rate_limiter import gather_with_concurrency

_log = get_logger("collector")

# Parameter names that control pagination across platforms
PAGE_PARAMS = {"page", "page_no", "pageNo", "offset", "cursor", "lastCursor", "searchId"}
CURSOR_PARAMS = {"cursor", "lastCursor", "continuation_token", "searchId", "offset"}


@dataclass
class CollectTask:
    """A single collection task."""

    platform: str
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    auto_paginate: bool = False
    max_pages: int = 10
    page_param: str = "page"

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "endpoint": self.endpoint,
            "params": self.params,
            "label": self.label,
            "auto_paginate": self.auto_paginate,
            "max_pages": self.max_pages,
            "page_param": self.page_param,
        }


class BatchCollector:
    """
    Run batch collection tasks with concurrency, rate limiting, and
    optional auto-pagination.
    """

    def __init__(
        self,
        client: JustOneAPICollector,
        max_concurrency: int | None = None,
        rate: float = 0.0,
    ):
        self._client = client
        self._max_concurrency = max_concurrency or client.config.max_concurrency
        self._rate = rate

    def collect_sync(self, tasks: list[CollectTask]) -> CollectReport:
        """Run tasks synchronously (sequentially)."""
        report = CollectReport()
        for task in tasks:
            result = self._run_task_sync(task)
            report.add(result)
        _log.info(
            "batch done: %d tasks, %d succeeded, %d failed (%.0fms)",
            report.total, report.succeeded, report.failed, report.total_duration_ms,
        )
        return report

    async def collect_async(self, tasks: list[CollectTask]) -> CollectReport:
        """Run tasks concurrently with rate limiting."""
        coros = [self._run_task_async(task) for task in tasks]
        results_raw = await gather_with_concurrency(
            coros,
            max_concurrency=self._max_concurrency,
            rate=self._rate,
        )
        report = CollectReport()
        for result in results_raw:
            if isinstance(result, CollectResult):
                report.add(result)
            elif isinstance(result, Exception):
                _log.error("task exception: %s", result)
                report.add(CollectResult(
                    platform="unknown", endpoint="unknown",
                    error=str(result),
                ))
        _log.info(
            "async batch done: %d tasks, %d succeeded, %d failed (%.0fms)",
            report.total, report.succeeded, report.failed, report.total_duration_ms,
        )
        return report

    # -- Task execution ---------------------------------------------------

    def _run_task_sync(self, task: CollectTask) -> CollectResult:
        start = time.monotonic()
        if task.auto_paginate:
            return self._paginate_sync(task, start)
        try:
            resp = self._client.request(task.platform, task.endpoint, task.params)
            elapsed = (time.monotonic() - start) * 1000
            return CollectResult(
                platform=task.platform,
                endpoint=task.endpoint,
                params=task.params,
                response=resp,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return CollectResult(
                platform=task.platform,
                endpoint=task.endpoint,
                params=task.params,
                error=str(exc),
                duration_ms=elapsed,
            )

    async def _run_task_async(self, task: CollectTask) -> CollectResult:
        start = time.monotonic()
        if task.auto_paginate:
            return await self._paginate_async(task, start)
        try:
            resp = await self._client.request_async(task.platform, task.endpoint, task.params)
            elapsed = (time.monotonic() - start) * 1000
            return CollectResult(
                platform=task.platform,
                endpoint=task.endpoint,
                params=task.params,
                response=resp,
                duration_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return CollectResult(
                platform=task.platform,
                endpoint=task.endpoint,
                params=task.params,
                error=str(exc),
                duration_ms=elapsed,
            )

    # -- Auto-pagination --------------------------------------------------

    def _paginate_sync(self, task: CollectTask, start: float) -> CollectResult:
        """Auto-paginate through all pages (sync)."""
        all_data: list = []
        current_params = dict(task.params)
        page_param = task.page_param
        retries = 0
        last_response: ApiResponse | None = None

        for page_num in range(1, task.max_pages + 1):
            if page_param in ("page", "page_no", "pageNo", "offset"):
                current_params[page_param] = page_num
            elif page_param in CURSOR_PARAMS and page_num > 1:
                if not last_response or not last_response.data:
                    break
                cursor_val = _extract_cursor(last_response.data, page_param)
                if cursor_val is None:
                    break
                current_params[page_param] = cursor_val

            resp = self._client.request(task.platform, task.endpoint, current_params)
            last_response = resp
            retries += 1

            if not resp.success:
                break

            page_data = _extract_list(resp.data)
            if page_data is None or len(page_data) == 0:
                break

            all_data.extend(page_data)
            _log.debug(
                "%s/%s page %d: %d items (total %d)",
                task.platform, task.endpoint, page_num, len(page_data), len(all_data),
            )

        elapsed = (time.monotonic() - start) * 1000
        merged = ApiResponse(
            success=all_data != [],
            code=last_response.code if last_response else -1,
            message=last_response.message if last_response else "",
            data={"items": all_data, "total_pages": retries},
            raw_json={"merged": True},
        )
        return CollectResult(
            platform=task.platform,
            endpoint=task.endpoint,
            params=task.params,
            response=merged,
            duration_ms=elapsed,
            retries=retries,
        )

    async def _paginate_async(self, task: CollectTask, start: float) -> CollectResult:
        """Auto-paginate through all pages (async)."""
        all_data: list = []
        current_params = dict(task.params)
        page_param = task.page_param
        retries = 0
        last_response: ApiResponse | None = None

        for page_num in range(1, task.max_pages + 1):
            if page_param in ("page", "page_no", "pageNo", "offset"):
                current_params[page_param] = page_num
            elif page_param in CURSOR_PARAMS and page_num > 1:
                if not last_response or not last_response.data:
                    break
                cursor_val = _extract_cursor(last_response.data, page_param)
                if cursor_val is None:
                    break
                current_params[page_param] = cursor_val

            resp = await self._client.request_async(task.platform, task.endpoint, current_params)
            last_response = resp
            retries += 1

            if not resp.success:
                break

            page_data = _extract_list(resp.data)
            if page_data is None or len(page_data) == 0:
                break

            all_data.extend(page_data)
            _log.debug(
                "%s/%s page %d (async): %d items (total %d)",
                task.platform, task.endpoint, page_num, len(page_data), len(all_data),
            )

        elapsed = (time.monotonic() - start) * 1000
        merged = ApiResponse(
            success=all_data != [],
            code=last_response.code if last_response else -1,
            message=last_response.message if last_response else "",
            data={"items": all_data, "total_pages": retries},
            raw_json={"merged": True},
        )
        return CollectResult(
            platform=task.platform,
            endpoint=task.endpoint,
            params=task.params,
            response=merged,
            duration_ms=elapsed,
            retries=retries,
        )


def _extract_list(data: Any) -> list | None:
    """Try to find a list in the response data (varies by platform)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "list", "data", "results", "cards", "feeds", "aweme_list", "notes"):
            if key in data and isinstance(data[key], list):
                return data[key]
        for v in data.values():
            if isinstance(v, list):
                return v
    return None


def _extract_cursor(data: Any, cursor_param: str) -> str | None:
    """Try to find a cursor/next-page token in the response data."""
    if isinstance(data, dict):
        for key in (cursor_param, "cursor", "nextCursor", "lastCursor",
                     "continuation_token", "searchId", "next_page_token",
                     "offset", "page_token"):
            if key in data and data[key]:
                return str(data[key])
        # Nested
        for v in data.values():
            if isinstance(v, dict):
                for key in (cursor_param, "cursor", "nextCursor", "lastCursor"):
                    if key in v and v[key]:
                        return str(v[key])
    return None
