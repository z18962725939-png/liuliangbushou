"""
Response and data models.

ApiResponse wraps every API result. CollectResult aggregates batch
collection outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ApiResponse(Generic[T]):
    """
    Unified response object returned by every API call.

    Attributes:
        success: True only when business code == 0.
        code:    Business code from the server (0 = success).
        message: Server-provided message string.
        data:    The business payload (may be None on failure).
        raw_json: The complete unmodified JSON response.
    """

    success: bool
    code: Any
    message: str
    data: T | None
    raw_json: dict[str, Any]

    def __bool__(self) -> bool:
        return self.success


@dataclass
class CollectResult:
    """Aggregated result from a batch collection run."""

    platform: str
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    response: ApiResponse | None = None
    error: str | None = None
    duration_ms: float = 0.0
    cached: bool = False
    retries: int = 0

    @property
    def success(self) -> bool:
        return self.response is not None and self.response.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "endpoint": self.endpoint,
            "params": self.params,
            "success": self.success,
            "code": self.response.code if self.response else None,
            "message": self.response.message if self.response else self.error,
            "data": self.response.data if self.response else None,
            "duration_ms": round(self.duration_ms, 2),
            "cached": self.cached,
            "retries": self.retries,
        }


@dataclass
class CollectReport:
    """Summary report for a collection session."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    cached: int = 0
    total_duration_ms: float = 0.0
    results: list[CollectResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return (self.succeeded / self.total * 100) if self.total else 0.0

    def add(self, result: CollectResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.success:
            self.succeeded += 1
        else:
            self.failed += 1
        if result.cached:
            self.cached += 1
        self.total_duration_ms += result.duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "cached": self.cached,
            "success_rate": round(self.success_rate, 2),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "results": [r.to_dict() for r in self.results],
        }
