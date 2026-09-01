"""
Qlik Cloud OAuth 2.0 — authorization code flow with PKCE (public/Native client).

Why this shape:
- User creates a "Native" OAuth client in the tenant (no client secret required).
- User clicks "Sign in with Qlik" in the local UI.
- We open the tenant's /oauth/authorize URL in their default browser.
- They log in with their normal Qlik account.
- Qlik redirects to http://localhost:8787/api/oauth/callback?code=...&state=...
- We exchange the code (plus PKCE verifier) for access_token + refresh_token.
- refresh_token goes into the OS keyring; access_token is cached in-memory.
- When access_token expires we refresh silently in the background.
"""
from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .. import secrets
from ..config import OAUTH_REDIRECT_URI, OAUTH_SCOPES


@dataclass
class _PkceState:
    """One in-flight OAuth exchange: verifier + expected state."""
    verifier: str
    created_at: float = field(default_factory=time.time)


@dataclass
class _TokenCache:
    access_token: str
    expires_at: float


# state (string) -> PKCE verifier for that pending login attempt.
# Cleared on callback. Keyed by `state` to defend against CSRF.
_pending: dict[str, _PkceState] = {}

_cache: Optional[_TokenCache] = None


def _tenant_url() -> str:
    url = secrets.get("tenant_url")
    if not url:
        raise RuntimeError("tenant_url not configured")
    return url.rstrip("/")


def _client_id() -> str:
    cid = secrets.get("qlik_client_id")
    if not cid:
        raise RuntimeError("qlik_client_id not configured")
    return cid


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_authorize_url() -> str:
    """Generate a PKCE verifier + challenge, remember them, return the
    URL the user should be sent to in the browser."""
    verifier = _b64url(_secrets.token_bytes(64))  # 86 chars, well within 43-128
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(_secrets.token_bytes(24))

    _pending[state] = _PkceState(verifier=verifier)
    _gc_pending()

    params = {
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{_tenant_url()}/oauth/authorize?{urllib.parse.urlencode(params)}"


def _gc_pending() -> None:
    """Drop pending entries older than 10 minutes."""
    cutoff = time.time() - 600
    for k, v in list(_pending.items()):
        if v.created_at < cutoff:
            _pending.pop(k, None)


async def exchange_code(code: str, state: str) -> None:
    """Callback handler: swap authorization code for tokens."""
    pkce = _pending.pop(state, None)
    if not pkce:
        raise RuntimeError("Unknown or expired OAuth state")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_tenant_url()}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "client_id": _client_id(),
                "code_verifier": pkce.verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    body = resp.json()
    _store_tokens(body)


def _store_tokens(body: dict) -> None:
    """Save access token in memory, refresh token in keyring."""
    global _cache
    access = body["access_token"]
    expires_in = int(body.get("expires_in", 3600))
    _cache = _TokenCache(access_token=access, expires_at=time.time() + expires_in)

    refresh = body.get("refresh_token")
    if refresh:
        secrets.set_field("qlik_refresh_token", refresh)


async def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing via refresh_token if needed."""
    global _cache
    now = time.time()
    if _cache and not force_refresh and _cache.expires_at - 30 > now:
        return _cache.access_token

    refresh = secrets.get("qlik_refresh_token")
    if not refresh:
        raise RuntimeError("Not signed in — click 'Sign in with Qlik' first")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_tenant_url()}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": _client_id(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        # If the refresh token was rejected, clear it so the UI shows "sign in".
        if resp.status_code in (400, 401):
            secrets.delete("qlik_refresh_token")
            _cache = None
        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")

    body = resp.json()
    _store_tokens(body)
    assert _cache is not None
    return _cache.access_token


def is_signed_in() -> bool:
    return bool(secrets.get("qlik_refresh_token")) or _cache is not None


def sign_out() -> None:
    global _cache
    _cache = None
    secrets.delete("qlik_refresh_token")
