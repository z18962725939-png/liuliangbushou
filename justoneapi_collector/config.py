"""
Configuration management.

Reads from environment variables or a .env file. All settings are
centralized in a single dataclass for easy inspection and override.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

DEFAULT_BASE_URL = "https://api.justoneapi.com"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0
DEFAULT_RETRY_MAX_DELAY = 60.0
DEFAULT_CACHE_TTL = 300
DEFAULT_MAX_CONCURRENCY = 5


@dataclass
class Config:
    """Runtime configuration for the collector."""

    token: str = ""
    base_url: str = DEFAULT_BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY
    cache_enabled: bool = True
    cache_ttl: int = DEFAULT_CACHE_TTL
    cache_dir: str = ".justoneapi_cache"
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    log_level: str = "INFO"
    log_file: str | None = None
    raise_on_business_error: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Config":
        """Load configuration from environment variables and optional .env."""
        if load_dotenv and env_file:
            load_dotenv(env_file)
        elif load_dotenv:
            for candidate in (Path.cwd() / ".env", Path.home() / ".justoneapi.env"):
                if candidate.exists():
                    load_dotenv(candidate)
                    break

        token = os.getenv("JUSTONEAPI_TOKEN", "")
        if not token:
            raise ValueError(
                "JUSTONEAPI_TOKEN is required. "
                "Set it in .env, as an environment variable, or pass token= "
                "to the client constructor."
            )

        return cls(
            token=token,
            base_url=os.getenv("JUSTONEAPI_BASE_URL", DEFAULT_BASE_URL),
            timeout=int(os.getenv("JUSTONEAPI_TIMEOUT", DEFAULT_TIMEOUT)),
            max_retries=int(os.getenv("JUSTONEAPI_MAX_RETRIES", DEFAULT_MAX_RETRIES)),
            retry_backoff=float(os.getenv("JUSTONEAPI_RETRY_BACKOFF", DEFAULT_RETRY_BACKOFF)),
            retry_max_delay=float(os.getenv("JUSTONEAPI_RETRY_MAX_DELAY", DEFAULT_RETRY_MAX_DELAY)),
            cache_enabled=os.getenv("JUSTONEAPI_CACHE_ENABLED", "true").lower() == "true",
            cache_ttl=int(os.getenv("JUSTONEAPI_CACHE_TTL", DEFAULT_CACHE_TTL)),
            cache_dir=os.getenv("JUSTONEAPI_CACHE_DIR", ".justoneapi_cache"),
            max_concurrency=int(os.getenv("JUSTONEAPI_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY)),
            log_level=os.getenv("JUSTONEAPI_LOG_LEVEL", "INFO"),
            log_file=os.getenv("JUSTONEAPI_LOG_FILE"),
            raise_on_business_error=os.getenv("JUSTONEAPI_RAISE_ON_ERROR", "false").lower() == "true",
        )

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create a config from a dict, filling defaults for missing keys."""
        known = {f.name for f in cls.__dataclass_fields__.values() if f.init}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)
