"""
justoneapi_collector — A commercial-grade data collection toolkit
wrapping the Just One API platform, covering all 40 platforms and ~320
endpoints with async/sync support, retry, caching, rate limiting,
batch collection, and multi-format export.

Quick start:
    from justoneapi_collector import JustOneAPICollector

    client = JustOneAPICollector(token="your_token")

    # Call any endpoint across 40 platforms
    resp = client.taobao_and_tmall.product_details_v1(item_id="123")
    print(resp.data)

    # Async
    import asyncio
    async def main():
        async with JustOneAPICollector(token="...") as c:
            r = await client.douyin_tiktok_china.hot_search_v1_async()
            print(r.data)
    asyncio.run(main())
"""

from __future__ import annotations

from .client import JustOneAPICollector
from .collector import BatchCollector, CollectTask
from .config import Config
from .exceptions import (
    AuthError,
    BusinessError,
    CollectorError,
    CollectError,
    ConfigError,
    InsufficientBalanceError,
    ParamError,
    ProtocolError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
    TokenLimitError,
    TransportError,
)
from .exporter import export, export_csv, export_excel, export_json
from .logger import get_logger, setup_logging
from .models import ApiResponse, CollectReport, CollectResult
from .platforms import count_endpoints, list_categories, list_platforms
from .video_downloader import BloggerInfo, VideoDownloader, VideoInfo
from .live_monitor import LiveMonitor, LiveStatus
from .douyin_live_stream import DouyinLiveStreamInfo, DouyinLiveStreamResolver

__version__ = "1.0.0"

__all__ = [
    # Client
    "JustOneAPICollector",
    "Config",
    # Collector
    "BatchCollector",
    "CollectTask",
    # Models
    "ApiResponse",
    "CollectReport",
    "CollectResult",
    # Export
    "export",
    "export_json",
    "export_csv",
    "export_excel",
    # Exceptions
    "CollectorError",
    "ConfigError",
    "TransportError",
    "ProtocolError",
    "BusinessError",
    "AuthError",
    "CollectError",
    "RateLimitError",
    "QuotaExceededError",
    "ParamError",
    "ServerError",
    "InsufficientBalanceError",
    "TokenLimitError",
    # Platform introspection
    "list_platforms",
    "list_categories",
    "count_endpoints",
    # Video downloader
    "VideoDownloader",
    "BloggerInfo",
    "VideoInfo",
    # Live monitor
    "LiveMonitor",
    "LiveStatus",
    # Douyin live stream resolver
    "DouyinLiveStreamResolver",
    "DouyinLiveStreamInfo",
    # Logging
    "get_logger",
    "setup_logging",
    # Version
    "__version__",
]
