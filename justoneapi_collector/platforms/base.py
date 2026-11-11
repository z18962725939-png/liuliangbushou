"""
Base platform resource class.

Each platform subclass inherits generic _call / _call_async methods
and gets type-safe endpoint methods auto-generated from the registry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models import ApiResponse

if TYPE_CHECKING:
    from ..client import JustOneAPICollector


class BasePlatform:
    """
    Base class for all platform resources.

    Subclasses set the class attributes ``slug``, ``name``, and ``category``.
    Endpoint methods are auto-generated at import time from the registry.
    """

    slug: str = ""
    name: str = ""
    category: str = ""

    def __init__(self, client: "JustOneAPICollector"):
        self._client = client

    def _call(self, endpoint: str, **params: Any) -> ApiResponse:
        """Synchronously call an endpoint on this platform."""
        return self._client.request(self.slug, endpoint, params)

    async def _call_async(self, endpoint: str, **params: Any) -> ApiResponse:
        """Asynchronously call an endpoint on this platform."""
        return await self._client.request_async(self.slug, endpoint, params)

    def list_endpoints(self) -> list[dict[str, str]]:
        """Return metadata for all endpoints on this platform."""
        from . import get_platform_meta
        from .registry import path_to_method_name
        meta = get_platform_meta(self.slug)
        if meta is None:
            return []
        return [
            {
                "name": path_to_method_name(ep[0]),
                "path": ep[0],
                "method": ep[1],
                "description": ep[2],
            }
            for ep in meta["endpoints"]
        ]

    def __repr__(self) -> str:
        return f"<Platform {self.name} ({self.slug})>"
