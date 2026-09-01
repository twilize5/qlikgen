"""
Thin Qlik Cloud REST client. Phase 0 needs:
- GET  /api/v1/spaces         — for the config screen dropdown
- POST /api/v1/apps           — create an empty app in a space
- GET  /api/v1/users/me       — health check the token
"""
from __future__ import annotations

import httpx

from . import auth


async def _client() -> httpx.AsyncClient:
    token = await auth.get_access_token()
    tenant = (await _tenant()).rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{tenant}/api/v1",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30,
    )


async def _tenant() -> str:
    from .. import secrets

    url = secrets.get("tenant_url")
    if not url:
        raise RuntimeError("tenant_url not configured")
    return url


async def whoami() -> dict:
    async with await _client() as c:
        r = await c.get("/users/me")
        r.raise_for_status()
        return r.json()


async def list_spaces(limit: int = 50) -> list[dict]:
    async with await _client() as c:
        r = await c.get("/spaces", params={"limit": limit})
        r.raise_for_status()
        return r.json().get("data", [])


async def create_app(name: str, space_id: str | None, description: str = "") -> dict:
    """
    Create an empty app.

    Qlik Cloud request body shape:
      { "attributes": { "name": "...", "description": "...", "spaceId": "..." } }

    If space_id is falsy the app lands in the M2M user's personal space.
    """
    attributes: dict = {"name": name, "description": description}
    if space_id:
        attributes["spaceId"] = space_id
    async with await _client() as c:
        r = await c.post("/apps", json={"attributes": attributes})
        if r.status_code >= 300:
            raise RuntimeError(f"create_app failed ({r.status_code}): {r.text}")
        return r.json()
