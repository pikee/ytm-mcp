from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class YTMError(Exception):
    message: str
    error_type: str = "ytm_error"
    status_code: int | None = None
    retry_after: float | None = None
    details: tuple[dict[str, Any], ...] = ()

    def __str__(self) -> str:
        status = f" (HTTP {self.status_code})" if self.status_code is not None else ""
        retry = f"; retry after {self.retry_after:g}s" if self.retry_after is not None else ""
        return f"YTM {self.error_type}{status}: {self.message}{retry}"


class YTMUnauthorizedError(YTMError):
    pass


class YTMForbiddenError(YTMError):
    pass


class YTMNotFoundError(YTMError):
    pass


class YTMRateLimitError(YTMError):
    pass


class YTMServerError(YTMError):
    pass


class YTMTimeoutError(YTMError):
    pass


class YTMNetworkError(YTMError):
    pass


class ContainerAccessError(YTMError):
    pass

