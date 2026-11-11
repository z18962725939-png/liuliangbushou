"""
Platform package — auto-generates platform classes and endpoint methods
from the registry at import time.

Usage:
    from justoneapi_collector import JustOneAPICollector

    client = JustOneAPICollector(token="...")
    # Synchronous
    resp = client.taobao_and_tmall.product_details_v1(item_id="123")
    # Asynchronous
    resp = await client.taobao_and_tmall.product_details_v1_async(item_id="123")
    # List available endpoints
    client.taobao_and_tmall.list_endpoints()
"""

from __future__ import annotations

from typing import Any

from ..models import ApiResponse
from .base import BasePlatform
from .registry import (
    PLATFORMS,
    count_endpoints,
    get_endpoint_meta,
    get_platform_meta,
    list_categories,
    list_platforms,
    path_to_method_name,
)

__all__ = [
    "PLATFORM_CLASSES",
    "get_platform_class",
    "list_platforms",
    "list_categories",
    "get_platform_meta",
    "get_endpoint_meta",
    "count_endpoints",
]


def _make_sync_method(path_slug: str, description: str):
    """Create a synchronous endpoint method."""

    def method(self, **kwargs: Any) -> ApiResponse:
        return self._call(path_slug, **kwargs)

    method.__name__ = path_to_method_name(path_slug)
    method.__qualname__ = f"<platform>.{method.__name__}"
    method.__doc__ = description
    return method


def _make_async_method(path_slug: str, description: str):
    """Create an asynchronous endpoint method (suffixed with _async)."""

    async def method(self, **kwargs: Any) -> ApiResponse:
        return await self._call_async(path_slug, **kwargs)

    method.__name__ = path_to_method_name(path_slug) + "_async"
    method.__qualname__ = f"<platform>.{method.__name__}"
    method.__doc__ = description
    return method


def _build_platform_class(meta: dict) -> type[BasePlatform]:
    """Dynamically create a platform class from registry metadata."""
    slug = meta["slug"]
    name = meta["name"]
    category = meta["category"]

    attrs: dict[str, Any] = {
        "slug": slug,
        "name": name,
        "category": category,
    }

    seen: set[str] = set()
    for path_slug, http_method, description in meta["endpoints"]:
        method_name = path_to_method_name(path_slug)
        if method_name in seen:
            continue
        seen.add(method_name)
        attrs[method_name] = _make_sync_method(path_slug, description)
        attrs[method_name + "_async"] = _make_async_method(path_slug, description)

    cls = type(
        slug.replace("-", "_").replace(" ", "_").title().replace("_", ""),
        (BasePlatform,),
        attrs,
    )
    cls.__module__ = __name__
    cls.__qualname__ = slug
    return cls


# Build all platform classes at import time
PLATFORM_CLASSES: dict[str, type[BasePlatform]] = {}
for _meta in PLATFORMS:
    _cls = _build_platform_class(_meta)
    PLATFORM_CLASSES[_meta["slug"]] = _cls


def get_platform_class(slug: str) -> type[BasePlatform] | None:
    """Return the dynamically generated class for a platform slug."""
    return PLATFORM_CLASSES.get(slug)
