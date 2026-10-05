from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .config import Settings, YandexAccount
from .errors import (
    YTMError,
    YTMForbiddenError,
    YTMNetworkError,
    YTMNotFoundError,
    YTMRateLimitError,
    YTMServerError,
    YTMTimeoutError,
    YTMUnauthorizedError,
)


class YTMClient:
    """Small async client for the documented YTM Management API v1 GET endpoints."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.ytm_base_url + "/",
            timeout=httpx.Timeout(settings.request_timeout),
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "ytm-mcp/0.1"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def get(
        self, account: YandexAccount, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await self._http.get(
                    path.lstrip("/"),
                    params={key: value for key, value in (params or {}).items() if value is not None and value != ""},
                    headers={"Authorization": f"OAuth {account.token}"},
                )
            except httpx.TimeoutException as exc:
                if attempt < self._settings.max_retries:
                    await self._backoff(attempt)
                    continue
                raise YTMTimeoutError("request timed out", "timeout") from exc
            except httpx.NetworkError as exc:
                if attempt < self._settings.max_retries:
                    await self._backoff(attempt)
                    continue
                raise YTMNetworkError("network request failed", "network_error") from exc

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self._settings.max_retries:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
            if response.is_error:
                raise self._api_error(response, (account.token,))
            try:
                payload = response.json()
            except ValueError as exc:
                raise YTMServerError("API returned invalid JSON", "invalid_response", response.status_code) from exc
            if not isinstance(payload, dict):
                raise YTMServerError("API returned a non-object JSON response", "invalid_response", response.status_code)
            return payload
        raise AssertionError("retry loop exhausted")

    async def list_all(
        self,
        account: YandexAccount,
        path: str,
        item_key: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        offset = 0
        per_page = 200
        items: list[dict[str, Any]] = []
        while True:
            page_params = {**(params or {}), "offset": offset, "per_page": per_page}
            payload = await self.get(account, path, page_params)
            page = payload.get(item_key, [])
            if not isinstance(page, list):
                raise YTMServerError(f"API response field {item_key!r} is not a list", "invalid_response")
            items.extend(item for item in page if isinstance(item, dict))
            total = payload.get("total")
            offset += len(page)
            if not page:
                return items
            if isinstance(total, int) and offset >= total:
                return items
            if not isinstance(total, int) and len(page) < per_page:
                return items

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._settings.retry_base_delay * (2**attempt))

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(60.0, max(0.0, float(raw)))
            except ValueError:
                try:
                    delta = (parsedate_to_datetime(raw) - parsedate_to_datetime(response.headers["Date"])).total_seconds()
                    return min(60.0, max(0.0, delta))
                except (ValueError, KeyError, TypeError):
                    pass
        return min(60.0, self._settings.retry_base_delay * (2**attempt))

    @staticmethod
    def _api_error(response: httpx.Response, secrets: tuple[str, ...] = ()) -> YTMError:
        message = response.reason_phrase or "YTM API request failed"
        error_type = "api_error"
        details: tuple[dict[str, Any], ...] = ()
        try:
            payload = response.json()
            if isinstance(payload, dict):
                message = YTMClient._redact(str(payload.get("message") or message), secrets)
                raw_errors = payload.get("errors")
                if isinstance(raw_errors, list):
                    safe = tuple(
                        {
                            key: YTMClient._redact(str(item.get(key)), secrets)
                            for key in ("error_type", "message", "location")
                            if item.get(key) is not None
                        }
                        for item in raw_errors
                        if isinstance(item, dict)
                    )
                    details = safe
                    if safe and safe[0].get("error_type"):
                        error_type = str(safe[0]["error_type"])
        except ValueError:
            pass
        status = response.status_code
        cls = {
            401: YTMUnauthorizedError,
            403: YTMForbiddenError,
            404: YTMNotFoundError,
            429: YTMRateLimitError,
        }.get(status, YTMServerError if status >= 500 else YTMError)
        retry_after = YTMClient._header_retry_after(response) if status == 429 else None
        return cls(message, error_type, status, retry_after, details)

    @staticmethod
    def _redact(value: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value

    @staticmethod
    def _header_retry_after(response: httpx.Response) -> float | None:
        try:
            return float(response.headers["Retry-After"])
        except (KeyError, ValueError):
            return None
