"""
Main client for the Just One API collector.

Provides both synchronous and asynchronous HTTP access to all 40 platforms
covered by Just One API, with built-in retry, caching, rate limiting, and
structured error handling.

Usage (sync):
    from justoneapi_collector import JustOneAPICollector

    client = JustOneAPICollector(token="your_token")
    resp = client.taobao_and_tmall.product_details_v1(item_id="123456")
    if resp.success:
        print(resp.data)
    client.close()

Usage (async):
    async with JustOneAPICollector(token="your_token") as client:
        resp = await client.douyin_tiktok_china.hot_search_v1_async()
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .cache import ResponseCache
from .config import Config
from .exceptions import (
    BusinessError,
    ProtocolError,
    TransportError,
    classify_business_error,
)
from .logger import get_logger, setup_logging
from .models import ApiResponse
from .platforms import PLATFORM_CLASSES, BasePlatform
from .rate_limiter import ConcurrencyLimiter
from .retry import retry_async, retry_sync


def _slug_to_attr(slug: str) -> str:
    """Convert a platform slug to a valid Python attribute name."""
    name = slug.replace("-", "_")
    if name[0].isdigit():
        name = "p_" + name
    return name


class JustOneAPICollector:
    """
    Unified client for all Just One API platforms.

    Args:
        token:       API access token. Required if config is not provided.
        config:      A Config instance. If omitted, loads from environment.
        **overrides: Override any Config field at construction time.
    """

    def __init__(
        self,
        token: str | None = None,
        config: Config | None = None,
        **overrides: Any,
    ):
        if config is not None:
            cfg = config
            if overrides:
                data = {**config.__dict__, **overrides}
                cfg = Config.from_dict(data)
        elif token is not None:
            data = {"token": token, **overrides}
            cfg = Config.from_dict(data)
        else:
            cfg = Config.from_env()

        if not cfg.token:
            raise ValueError("API token is required. Set JUSTONEAPI_TOKEN or pass token=...")

        self._config = cfg
        setup_logging(cfg.log_level, cfg.log_file)
        self._log = get_logger("client")

        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

        # 最近一次请求是否因 Token 无效/未激活而失败。
        # 上层（GUI）据此给出"请到设置页更新 Token"的明确提示，而不是笼统的"解析失败"。
        self.token_invalid: bool = False
        self.token_error: str = ""

        self._cache = ResponseCache(
            cache_dir=cfg.cache_dir,
            ttl=cfg.cache_ttl,
            enabled=cfg.cache_enabled,
        )
        self._concurrency = ConcurrencyLimiter(cfg.max_concurrency)

        # Instantiate all platform resources
        self._platforms: dict[str, BasePlatform] = {}
        for slug, cls in PLATFORM_CLASSES.items():
            attr = _slug_to_attr(slug)
            instance = cls(self)
            setattr(self, attr, instance)
            self._platforms[slug] = instance

    # -- Properties --------------------------------------------------------

    @property
    def config(self) -> Config:
        return self._config

    @property
    def cache(self) -> ResponseCache:
        return self._cache

    # -- Platform access --------------------------------------------------

    def get_platform(self, slug: str) -> BasePlatform | None:
        """Access a platform resource by its slug (e.g. 'taobao-and-tmall')."""
        return self._platforms.get(slug)

    def list_platforms(self) -> list[dict[str, str]]:
        """Return metadata for all available platforms."""
        from .platforms import list_platforms as _lp
        return [
            {"slug": p["slug"], "name": p["name"], "category": p["category"]}
            for p in _lp()
        ]

    def list_endpoints(self, platform_slug: str) -> list[dict[str, str]]:
        """Return all endpoint metadata for a given platform."""
        from .platforms.registry import get_platform_meta
        meta = get_platform_meta(platform_slug)
        if not meta:
            return []
        return [
            {"path": ep[0], "method": ep[1], "description": ep[2]}
            for ep in meta["endpoints"]
        ]

    # -- Core request (sync) ----------------------------------------------

    def request(
        self,
        platform: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> ApiResponse:
        """
        Make a synchronous API request.

        Args:
            platform: Platform slug (e.g. 'taobao-and-tmall').
            endpoint: Endpoint path slug (e.g. 'product-details-v1').
            params:   Query/POST parameters (token is added automatically).
        """
        params = params or {}

        # Check cache
        if self._cache.enabled:
            cached = self._cache.get(platform, endpoint, params)
            if cached is not None:
                return ApiResponse(
                    success=True, code=0, message="", data=cached,
                    raw_json={"code": 0, "message": "", "data": cached, "_cached": True},
                )

        path = self._get_api_path(platform, endpoint)
        method = self._get_method(platform, endpoint)

        def _do_request() -> ApiResponse:
            return self._raw_request_sync(path, method, params)

        start = time.monotonic()
        try:
            response = retry_sync(
                _do_request,
                max_retries=self._config.max_retries,
                base_delay=self._config.retry_backoff,
                max_delay=self._config.retry_max_delay,
            )
        except BusinessError:
            if self._config.raise_on_business_error:
                raise
            response = ApiResponse(
                success=False, code=-1, message="max retries exceeded",
                data=None, raw_json={},
            )
        except Exception as exc:
            self._log.error("request failed: %s/%s — %s", platform, endpoint, exc)
            if self._config.raise_on_business_error:
                raise
            response = ApiResponse(
                success=False, code=-1, message=str(exc),
                data=None, raw_json={},
            )

        elapsed = (time.monotonic() - start) * 1000
        self._log.debug(
            "%s/%s completed in %.0fms (code=%s)",
            platform, endpoint, elapsed, response.code,
        )

        # Cache successful responses
        if response.success and response.data is not None:
            self._cache.set(platform, endpoint, params, response.data)

        return response

    # -- Core request (async) ---------------------------------------------

    async def request_async(
        self,
        platform: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> ApiResponse:
        """Asynchronous version of request()."""
        params = params or {}

        if self._cache.enabled:
            cached = self._cache.get(platform, endpoint, params)
            if cached is not None:
                return ApiResponse(
                    success=True, code=0, message="", data=cached,
                    raw_json={"code": 0, "message": "", "data": cached, "_cached": True},
                )

        path = self._get_api_path(platform, endpoint)
        method = self._get_method(platform, endpoint)

        async def _do_request() -> ApiResponse:
            return await self._raw_request_async(path, method, params)

        start = time.monotonic()
        try:
            response = await retry_async(
                _do_request,
                max_retries=self._config.max_retries,
                base_delay=self._config.retry_backoff,
                max_delay=self._config.retry_max_delay,
            )
        except BusinessError:
            if self._config.raise_on_business_error:
                raise
            response = ApiResponse(
                success=False, code=-1, message="max retries exceeded",
                data=None, raw_json={},
            )
        except Exception as exc:
            self._log.error("async request failed: %s/%s — %s", platform, endpoint, exc)
            if self._config.raise_on_business_error:
                raise
            response = ApiResponse(
                success=False, code=-1, message=str(exc),
                data=None, raw_json={},
            )

        elapsed = (time.monotonic() - start) * 1000
        self._log.debug(
            "%s/%s async completed in %.0fms (code=%s)",
            platform, endpoint, elapsed, response.code,
        )

        if response.success and response.data is not None:
            self._cache.set(platform, endpoint, params, response.data)

        return response

    # -- Raw HTTP ---------------------------------------------------------

    def _get_api_path(self, platform: str, endpoint: str) -> str:
        """Look up the actual API path for a platform/endpoint pair."""
        from .platforms.api_paths_generated import API_PATHS
        platform_map = API_PATHS.get(platform, {})
        path = platform_map.get(endpoint)
        if path:
            return path
        # Fallback: construct from slugs (may not work for all endpoints)
        self._log.warning("API path not found in mapping, using fallback: %s/%s", platform, endpoint)
        return f"/{platform}/{endpoint}"

    def _get_method(self, platform: str, endpoint: str) -> str:
        from .platforms.registry import get_endpoint_meta
        meta = get_endpoint_meta(platform, endpoint)
        if meta:
            return meta["method"]
        return "GET"

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = httpx.Client(
                base_url=self._config.base_url,
                timeout=self._config.timeout,
                follow_redirects=True,
            )
        return self._sync_client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self._config.base_url,
                timeout=self._config.timeout,
                follow_redirects=True,
            )
        return self._async_client

    def _build_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Merge token with user params, dropping None values."""
        merged = {"token": self._config.token}
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, bool):
                merged[k] = str(v).lower()
            else:
                merged[k] = v
        return merged

    def _raw_request_sync(
        self, path: str, method: str, params: dict[str, Any]
    ) -> ApiResponse:
        client = self._get_sync_client()
        req_params = self._build_params(params)
        try:
            if method.upper() == "POST":
                resp = client.post(path, data=req_params)
            else:
                resp = client.get(path, params=req_params)
        except httpx.HTTPError as exc:
            raise TransportError(f"GET/POST {path} failed: {exc}") from exc

        return self._parse_response(path, resp)

    async def _raw_request_async(
        self, path: str, method: str, params: dict[str, Any]
    ) -> ApiResponse:
        client = self._get_async_client()
        req_params = self._build_params(params)
        try:
            if method.upper() == "POST":
                resp = await client.post(path, data=req_params)
            else:
                resp = await client.get(path, params=req_params)
        except httpx.HTTPError as exc:
            raise TransportError(f"async GET/POST {path} failed: {exc}") from exc

        return self._parse_response(path, resp)

    def _parse_response(self, path: str, resp: httpx.Response) -> ApiResponse:
        """Parse HTTP response into ApiResponse, handling errors."""
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProtocolError(
                f"Server returned non-JSON for {path} (HTTP {resp.status_code})"
            ) from exc

        if not isinstance(payload, dict):
            raise ProtocolError(f"Server returned non-object JSON for {path}")

        code = payload.get("code")
        message = str(payload.get("message") or "")
        data = payload.get("data")
        success = code == 0

        api_resp = ApiResponse(
            success=success, code=code, message=message,
            data=data, raw_json=payload,
        )

        # 识别 Token 失效（code=100 / 消息含 TOKEN）。JustOneAPI 在 token 过期、
        # 未激活或试用到期时统一返回 {"code":100,"message":"TOKEN INVALID/UNACTIVATE"}。
        if not success and (
            code == 100 or "TOKEN" in message.upper() or "未激活" in message
        ):
            self.token_invalid = True
            self.token_error = message or f"code={code}"

        if not success and self._config.raise_on_business_error:
            raise classify_business_error(code, message, data)

        return api_resp

    # -- Lifecycle --------------------------------------------------------

    def close(self) -> None:
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    def __enter__(self) -> "JustOneAPICollector":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    async def __aenter__(self) -> "JustOneAPICollector":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def __repr__(self) -> str:
        return (
            f"<JustOneAPICollector "
            f"base_url={self._config.base_url!r} "
            f"platforms={len(self._platforms)}>"
        )
