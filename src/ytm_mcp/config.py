from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


class ConfigError(ValueError):
    """Raised when required server configuration is invalid."""


@dataclass(frozen=True, slots=True)
class YandexAccount:
    name: str
    token: str
    primary: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    mcp_api_key: str
    accounts: tuple[YandexAccount, ...]
    container_ids: tuple[int, ...]
    public_url: str = "http://localhost:8000"
    oauth_client_id: str = "chatgpt-ytm"
    oauth_client_secret: str = ""
    oauth_redirect_uri: str = ""
    oauth_signing_key: str = ""
    oauth_access_token_ttl: int = 3600
    oauth_refresh_token_ttl: int = 30 * 24 * 3600
    ytm_base_url: str = "https://api.ytm.yandex.net/ytm/management/v1"
    request_timeout: float = 15.0
    max_retries: int = 3
    retry_base_delay: float = 0.5


def _accounts(raw: str) -> tuple[YandexAccount, ...]:
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError("YTM_ACCOUNTS_JSON must be valid JSON") from exc
    if not isinstance(value, list) or not value:
        raise ConfigError("YTM_ACCOUNTS_JSON must be a non-empty JSON array")

    accounts: list[YandexAccount] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"YTM_ACCOUNTS_JSON item {index} must be an object")
        name = item.get("name")
        token = item.get("token")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"YTM account {index} has no valid name")
        if not isinstance(token, str) or not token.strip():
            raise ConfigError(f"YTM account {name!r} has no valid token")
        accounts.append(YandexAccount(name=name.strip(), token=token.strip(), primary=item.get("primary") is True))

    if len({account.name for account in accounts}) != len(accounts):
        raise ConfigError("YTM account names must be unique")
    primary_count = sum(account.primary for account in accounts)
    if primary_count > 1:
        raise ConfigError("Only one YTM account may be primary")
    if primary_count == 0:
        accounts[0] = YandexAccount(accounts[0].name, accounts[0].token, True)
    accounts.sort(key=lambda account: not account.primary)
    return tuple(accounts)


def _container_ids(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    try:
        values = json.loads(raw) if raw.lstrip().startswith("[") else raw.split(",")
        result = tuple(dict.fromkeys(int(value) for value in values))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigError("YTM_CONTAINER_IDS must be comma-separated integers or a JSON array") from exc
    if any(value <= 0 for value in result):
        raise ConfigError("YTM_CONTAINER_IDS values must be positive integers")
    return result


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    api_key = env.get("MCP_API_KEY", "")
    if len(api_key) < 24:
        raise ConfigError("MCP_API_KEY is required and must be at least 24 characters")
    accounts_raw = env.get("YTM_ACCOUNTS_JSON", "")
    if not accounts_raw:
        raise ConfigError("YTM_ACCOUNTS_JSON is required")

    public_url = env.get("MCP_PUBLIC_URL", "").rstrip("/")
    if not public_url:
        domain = env.get("MCP_DOMAIN", "").strip()
        public_url = f"https://{domain}" if domain else "http://localhost:8000"
    if not public_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ConfigError("MCP_PUBLIC_URL must use HTTPS (HTTP is allowed only for localhost)")

    oauth_client_secret = env.get("MCP_OAUTH_CLIENT_SECRET", "")
    if len(oauth_client_secret) < 24:
        raise ConfigError("MCP_OAUTH_CLIENT_SECRET is required and must be at least 24 characters")
    oauth_signing_key = env.get("MCP_OAUTH_SIGNING_KEY", "")
    if len(oauth_signing_key) < 32:
        raise ConfigError("MCP_OAUTH_SIGNING_KEY is required and must be at least 32 characters")
    oauth_redirect_uri = env.get("MCP_OAUTH_REDIRECT_URI", "").strip()
    if not oauth_redirect_uri.startswith("https://chatgpt.com/"):
        raise ConfigError("MCP_OAUTH_REDIRECT_URI must be the HTTPS callback URL shown by ChatGPT")

    return Settings(
        mcp_api_key=api_key,
        accounts=_accounts(accounts_raw),
        container_ids=_container_ids(env.get("YTM_CONTAINER_IDS", "")),
        public_url=public_url,
        oauth_client_id=env.get("MCP_OAUTH_CLIENT_ID", "chatgpt-ytm").strip() or "chatgpt-ytm",
        oauth_client_secret=oauth_client_secret,
        oauth_redirect_uri=oauth_redirect_uri,
        oauth_signing_key=oauth_signing_key,
        oauth_access_token_ttl=max(300, int(env.get("MCP_OAUTH_ACCESS_TOKEN_TTL", "3600"))),
        oauth_refresh_token_ttl=max(3600, int(env.get("MCP_OAUTH_REFRESH_TOKEN_TTL", "2592000"))),
        ytm_base_url=env.get("YTM_BASE_URL", "https://api.ytm.yandex.net/ytm/management/v1").rstrip("/"),
        request_timeout=float(env.get("YTM_TIMEOUT_SECONDS", "15")),
        max_retries=max(0, int(env.get("YTM_MAX_RETRIES", "3"))),
        retry_base_delay=max(0.0, float(env.get("YTM_RETRY_BASE_DELAY", "0.5"))),
    )
