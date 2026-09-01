"""HTTP wrapper around the local Node enigma.js sidecar."""
from __future__ import annotations

import httpx

from .config import QIX_SIDECAR_URL
from .qlik import auth
from . import secrets


async def _base_payload(app_id: str) -> dict:
    """Common { tenant, token, appId } every sidecar endpoint expects."""
    token = await auth.get_access_token()
    tenant = secrets.get("tenant_url")
    if not tenant:
        raise RuntimeError("tenant_url not configured")
    return {"tenant": tenant.rstrip("/"), "token": token, "appId": app_id}


async def _post(path: str, payload: dict, timeout: float = 180.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{QIX_SIDECAR_URL}{path}", json=payload)
        if r.status_code >= 300:
            # Surface the sidecar's error body rather than a bare status.
            raise RuntimeError(f"QIX {path} failed ({r.status_code}): {r.text}")
        return r.json()


async def open_app(app_id: str) -> dict:
    """Ask the sidecar to open the app via Engine API, read its layout, close.
    Proves the WebSocket + token combo works end-to-end."""
    return await _post("/qix/open-app", await _base_payload(app_id), timeout=60)


async def set_script_and_reload(app_id: str, script: str) -> dict:
    """Push a new load script, reload, and save. Returns { ok, reloaded, detail }."""
    payload = await _base_payload(app_id)
    payload["script"] = script
    return await _post("/qix/set-script", payload)


async def build_app(app_id: str, plan: dict) -> dict:
    """Build every sheet in the dashboard plan.
    Returns { sheet_ids, sheets, sheet_id, object_ids, diagnostics }."""
    payload = await _base_payload(app_id)
    payload["plan"] = plan
    return await _post("/qix/build-app", payload)


async def dump_objects(app_id: str) -> dict:
    """Diagnostic: full properties of every non-sheet object in the app."""
    return await _post("/qix/dump-objects", await _base_payload(app_id), timeout=60)
