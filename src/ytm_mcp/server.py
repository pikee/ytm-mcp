from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Any
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from .client import YTMClient
from .config import Settings, load_settings
from .oauth import OAUTH_SCOPE, SharedKeyOAuthProvider, register_oauth_routes
from .router import AccountRouter
from .tools import register_tools


def build_server(
    settings: Settings,
    client: YTMClient | None = None,
    oauth_provider: SharedKeyOAuthProvider | None = None,
) -> MCPServer:
    ytm_client = client or YTMClient(settings)
    router = AccountRouter(ytm_client, settings.accounts)
    resource_url = f"{settings.public_url}/mcp"
    provider = oauth_provider or SharedKeyOAuthProvider(
        issuer_url=settings.public_url,
        resource_url=resource_url,
        api_key=settings.mcp_api_key,
        client_id=settings.oauth_client_id,
        client_secret=settings.oauth_client_secret,
        redirect_uri=settings.oauth_redirect_uri,
        signing_key=settings.oauth_signing_key,
        access_token_ttl=settings.oauth_access_token_ttl,
        refresh_token_ttl=settings.oauth_refresh_token_ttl,
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await ytm_client.close()

    mcp = MCPServer(
        "Yandex Tag Manager (read-only)",
        instructions="Read-only access to configured Yandex Tag Manager containers. Never exposes credentials.",
        lifespan=lifespan,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=settings.public_url,  # type: ignore[arg-type]
            resource_server_url=resource_url,  # type: ignore[arg-type]
            required_scopes=[OAUTH_SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=False,
                valid_scopes=[OAUTH_SCOPE],
                default_scopes=[OAUTH_SCOPE],
            ),
            validate_token_resource=True,
        ),
    )
    register_tools(mcp, router, settings)
    register_oauth_routes(mcp, provider)
    return mcp


def create_app(settings: Settings | None = None) -> Any:
    resolved_settings = settings or load_settings()
    mcp = build_server(resolved_settings)
    public_host = urlparse(resolved_settings.public_url).hostname or "127.0.0.1"
    return mcp.streamable_http_app(json_response=True, stateless_http=True, host=public_host)
