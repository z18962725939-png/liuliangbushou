"""
Exception hierarchy for Just One API Collector.

All exceptions derive from CollectorError so callers can catch any
library-raised error with a single except clause.
"""


class CollectorError(Exception):
    """Base exception for all collector errors."""


class ConfigError(CollectorError):
    """Raised when configuration is missing or invalid."""


class TransportError(CollectorError):
    """Raised when an HTTP transport-level failure occurs."""


class ProtocolError(CollectorError):
    """Raised when the server response cannot be parsed as JSON."""


class BusinessError(CollectorError):
    """Raised when the API returns a non-zero business code."""

    def __init__(self, code, message, data=None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


class RateLimitError(BusinessError):
    """Business code 302 — rate limit exceeded."""


class QuotaExceededError(BusinessError):
    """Business code 303 — daily quota exceeded."""


class AuthError(BusinessError):
    """Business code 100/600 — token invalid or insufficient permission."""


class InsufficientBalanceError(BusinessError):
    """Business code 601 — account balance insufficient."""


class TokenLimitError(BusinessError):
    """Business code 602 — token quota exceeded."""


class CollectError(BusinessError):
    """Business code 301 — collection failed, retry recommended."""


class ParamError(BusinessError):
    """Business code 400 — parameter error."""


class ServerError(BusinessError):
    """Business code 500 — internal server error."""


# Maps business codes to exception classes for classification.
CODE_EXCEPTION_MAP = {
    100: AuthError,
    301: CollectError,
    302: RateLimitError,
    303: QuotaExceededError,
    400: ParamError,
    500: ServerError,
    600: AuthError,
    601: InsufficientBalanceError,
    602: TokenLimitError,
}


def classify_business_error(code, message, data=None) -> BusinessError:
    """Classify a business error code into the most specific exception."""
    exc_cls = CODE_EXCEPTION_MAP.get(code, BusinessError)
    return exc_cls(code, message, data)
