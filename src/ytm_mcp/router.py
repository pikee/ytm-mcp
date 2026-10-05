from __future__ import annotations

import asyncio
from typing import Any

from .client import YTMClient
from .config import YandexAccount
from .errors import ContainerAccessError, YTMForbiddenError, YTMNotFoundError, YTMUnauthorizedError


class AccountRouter:
    """Resolves and caches container_id -> account without exposing tokens."""

    def __init__(self, client: YTMClient, accounts: tuple[YandexAccount, ...]) -> None:
        self.client = client
        self.accounts = accounts
        self._container_accounts: dict[int, YandexAccount] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def account_for(self, container_id: int) -> YandexAccount:
        if container_id <= 0:
            raise ValueError("container_id must be a positive integer")
        cached = self._container_accounts.get(container_id)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(container_id, asyncio.Lock())
        async with lock:
            cached = self._container_accounts.get(container_id)
            if cached is not None:
                return cached
            for account in self.accounts:
                try:
                    await self.client.get(account, f"container/{container_id}/tags", {"offset": 0, "per_page": 1})
                except (YTMUnauthorizedError, YTMForbiddenError, YTMNotFoundError):
                    continue
                self._container_accounts[container_id] = account
                return account
        raise ContainerAccessError(
            f"container {container_id} is not accessible with any configured Yandex account",
            "container_access_denied",
            403,
        )

    async def get(self, container_id: int, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        account = await self.account_for(container_id)
        try:
            return await self.client.get(account, path, params)
        except (YTMUnauthorizedError, YTMForbiddenError):
            self._container_accounts.pop(container_id, None)
            replacement = await self.account_for(container_id)
            return await self.client.get(replacement, path, params)

    async def list_all(
        self, container_id: int, path: str, item_key: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        account = await self.account_for(container_id)
        try:
            return await self.client.list_all(account, path, item_key, params)
        except (YTMUnauthorizedError, YTMForbiddenError):
            self._container_accounts.pop(container_id, None)
            replacement = await self.account_for(container_id)
            return await self.client.list_all(replacement, path, item_key, params)

    async def get_global(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for account in self.accounts:
            try:
                return await self.client.get(account, path, params)
            except (YTMUnauthorizedError, YTMForbiddenError) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Yandex accounts configured")

    def cached_account_name(self, container_id: int) -> str | None:
        account = self._container_accounts.get(container_id)
        return account.name if account else None

