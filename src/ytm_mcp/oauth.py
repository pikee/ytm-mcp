from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response


OAUTH_SCOPE = "ytm:read"


@dataclass(slots=True)
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    expires_at: float
    failed_attempts: int = 0


class SharedKeyOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Small OAuth 2.1 provider for a trusted team using one shared login key.

    Authorization requests and one-time codes live in process memory. Access and
    refresh tokens are signed, self-contained values, so deployments do not need
    a database and tokens survive application restarts as long as the signing key
    remains unchanged.
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        api_key: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        signing_key: str,
        access_token_ttl: int = 3600,
        refresh_token_ttl: int = 30 * 24 * 3600,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url
        self._api_key = api_key
        self._signing_key = signing_key.encode("utf-8")
        self._access_token_ttl = access_token_ttl
        self._refresh_token_ttl = refresh_token_ttl
        self._pending: dict[str, PendingAuthorization] = {}
        self._codes: dict[str, AuthorizationCode] = {}
        self._client = OAuthClientInformationFull(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=[redirect_uri],
            token_endpoint_auth_method="client_secret_post",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=OAUTH_SCOPE,
            client_name="ChatGPT YTM MCP",
            application_type="web",
        )

    @property
    def login_url(self) -> str:
        return f"{self.issuer_url}/oauth/login"

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        if self._secure_equal(client_id, self._client.client_id):
            return self._client
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise NotImplementedError("Dynamic client registration is disabled")

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        resource = params.resource or self.resource_url
        if resource != self.resource_url:
            raise AuthorizeError("invalid_target", "Unknown resource")

        scopes = params.scopes or [OAUTH_SCOPE]
        if any(scope != OAUTH_SCOPE for scope in scopes):
            raise AuthorizeError("invalid_scope", "Only ytm:read is supported")

        self._cleanup()
        request_id = secrets.token_urlsafe(32)
        params.resource = resource
        params.scopes = scopes
        self._pending[request_id] = PendingAuthorization(
            client_id=client.client_id,
            params=params,
            expires_at=time.time() + 300,
        )
        return f"{self.login_url}?{urlencode({'request_id': request_id})}"

    def complete_authorization(self, request_id: str, api_key: str) -> str | None:
        self._cleanup()
        pending = self._pending.get(request_id)
        if pending is None:
            return None
        if not self._secure_equal(api_key, self._api_key):
            pending.failed_attempts += 1
            if pending.failed_attempts >= 5:
                del self._pending[request_id]
            return None

        del self._pending[request_id]
        code = AuthorizationCode(
            code=secrets.token_urlsafe(32),
            scopes=pending.params.scopes or [OAUTH_SCOPE],
            expires_at=time.time() + 300,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=pending.params.redirect_uri,
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
            subject="shared-team",
        )
        self._codes[code.code] = code
        return construct_redirect_uri(
            str(pending.params.redirect_uri),
            code=code.code,
            state=pending.params.state,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self._cleanup()
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        stored = self._codes.pop(authorization_code.code, None)
        if stored is None or stored.client_id != client.client_id:
            raise TokenError("invalid_grant", "Authorization code is invalid or already used")
        return self._issue_tokens(
            client_id=stored.client_id,
            scopes=stored.scopes,
            resource=stored.resource or self.resource_url,
            subject=stored.subject or "shared-team",
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        payload = self._verify_signed_token(refresh_token, "refresh")
        if payload is None or payload.get("client_id") != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=payload["client_id"],
            scopes=payload["scopes"],
            expires_at=payload["exp"],
            resource=payload["resource"],
            subject=payload["sub"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if refresh_token.client_id != client.client_id:
            raise TokenError("invalid_grant", "Refresh token does not belong to this client")
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=scopes,
            resource=refresh_token.resource or self.resource_url,
            subject=refresh_token.subject or "shared-team",
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Preserve direct Bearer access for MCP clients that support custom headers.
        if self._secure_equal(token, self._api_key):
            return AccessToken(
                token=token,
                client_id="static-bearer",
                scopes=[OAUTH_SCOPE],
                resource=self.resource_url,
                subject="shared-team",
                claims={"iss": self.issuer_url},
            )

        payload = self._verify_signed_token(token, "access")
        if payload is None:
            return None
        return AccessToken(
            token=token,
            client_id=payload["client_id"],
            scopes=payload["scopes"],
            expires_at=payload["exp"],
            resource=payload["resource"],
            subject=payload["sub"],
            claims={"iss": self.issuer_url},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Revocation is not advertised. Tokens are short-lived or bounded by the
        # refresh-token TTL; rotating MCP_OAUTH_SIGNING_KEY invalidates all of them.
        return None

    def _issue_tokens(self, *, client_id: str, scopes: list[str], resource: str, subject: str) -> OAuthToken:
        now = int(time.time())
        common: dict[str, Any] = {
            "client_id": client_id,
            "scopes": scopes,
            "resource": resource,
            "sub": subject,
            "iat": now,
        }
        access_token = self._sign_token({**common, "typ": "access", "exp": now + self._access_token_ttl})
        refresh_token = self._sign_token({**common, "typ": "refresh", "exp": now + self._refresh_token_ttl})
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self._access_token_ttl,
            refresh_token=refresh_token,
            scope=" ".join(scopes),
        )

    def _sign_token(self, payload: dict[str, Any]) -> str:
        payload = {**payload, "jti": secrets.token_urlsafe(16)}
        body = self._b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = self._b64encode(hmac.digest(self._signing_key, body.encode("ascii"), hashlib.sha256))
        return f"ytm1.{body}.{signature}"

    def _verify_signed_token(self, token: str, expected_type: str) -> dict[str, Any] | None:
        if len(token) > 4096:
            return None
        try:
            prefix, body, signature = token.split(".", 2)
            if prefix != "ytm1":
                return None
            expected = self._b64encode(hmac.digest(self._signing_key, body.encode("ascii"), hashlib.sha256))
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(self._b64decode(body))
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict):
            return None
        required = {"typ", "client_id", "scopes", "resource", "sub", "iat", "exp", "jti"}
        if not required.issubset(payload):
            return None
        if payload["typ"] != expected_type or payload["resource"] != self.resource_url:
            return None
        if not isinstance(payload["scopes"], list) or OAUTH_SCOPE not in payload["scopes"]:
            return None
        if not isinstance(payload["exp"], int) or payload["exp"] <= int(time.time()):
            return None
        return payload

    def _cleanup(self) -> None:
        now = time.time()
        self._pending = {key: value for key, value in self._pending.items() if value.expires_at > now}
        self._codes = {key: value for key, value in self._codes.items() if value.expires_at > now}

    @staticmethod
    def _secure_equal(left: str, right: str) -> bool:
        return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def register_oauth_routes(mcp: Any, provider: SharedKeyOAuthProvider) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/oauth/login", methods=["GET", "POST"])
    async def login(request: Request) -> Response:
        action_url = f"{request.url.scheme}://{request.url.netloc}/oauth/login"
        if request.method == "GET":
            request_id = request.query_params.get("request_id", "")
            return _login_page(request_id, action_url=action_url)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 8192:
                    return _login_page("", "Запрос слишком большой", status_code=413, action_url=action_url)
            except ValueError:
                return _login_page("", "Некорректный запрос", status_code=400, action_url=action_url)

        form = await request.form()
        request_id = str(form.get("request_id", ""))
        api_key = str(form.get("api_key", ""))
        redirect_url = provider.complete_authorization(request_id, api_key)
        if redirect_url is None:
            return _login_page(request_id, "Неверный ключ или запрос истёк", status_code=403, action_url=action_url)
        return RedirectResponse(redirect_url, status_code=302, headers={"Cache-Control": "no-store"})


def _login_page(request_id: str, error: str | None = None, *, status_code: int = 200, action_url: str = "/oauth/login") -> HTMLResponse:
    safe_request_id = html.escape(request_id, quote=True)
    safe_action_url = html.escape(action_url, quote=True)
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    body = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Доступ к YTM MCP</title>
  <style>
    body {{ font: 16px system-ui, sans-serif; max-width: 32rem; margin: 10vh auto; padding: 1.5rem; color: #18181b; }}
    form {{ display: grid; gap: 1rem; }}
    input, button {{ font: inherit; padding: .75rem; }}
    button {{ cursor: pointer; }}
    .error {{ color: #b91c1c; }}
    .note {{ color: #52525b; font-size: .9rem; }}
  </style>
</head>
<body>
  <h1>Подключение YTM MCP</h1>
  <p>Введите общий MCP API key, чтобы разрешить ChatGPT доступ только для чтения.</p>
  {error_html}
  <form method="post" action="{safe_action_url}" autocomplete="off">
    <input type="hidden" name="request_id" value="{safe_request_id}">
    <label>API key<br><input type="password" name="api_key" required autofocus></label>
    <button type="submit">Разрешить доступ</button>
  </form>
  <p class="note">OAuth-токены Яндекса никогда не передаются в ChatGPT.</p>
</body>
</html>"""
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
