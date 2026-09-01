"""
Qlik Cloud data-files REST client.

Phase 1 needs to:
  1. Resolve the DataFiles connection for the target space (or personal space).
  2. Upload the CSV into that connection so a LOAD script can read it via lib://.

The DataFiles connection and the app MUST live in the same space, otherwise the
reload fails with an access error — so the caller passes the same space_id it
used to create the app.

We build a dedicated httpx client here rather than reuse rest._client(), because
that one pins Content-Type: application/json which would break the multipart
upload.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from . import auth
from .. import secrets

# The built-in per-space file connection is a "DataFiles" datasource.
_DATAFILES_DATASOURCE = "DataFiles"


def _tenant() -> str:
    url = secrets.get("tenant_url")
    if not url:
        raise RuntimeError("tenant_url not configured")
    return url.rstrip("/")


async def _headers() -> dict[str, str]:
    token = await auth.get_access_token()
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def resolve_connection(space_id: str | None) -> tuple[str, str]:
    """
    Return (connection_id, connection_name) for the DataFiles connection in the
    given space. `space_id` None means the user's personal space (connection has
    no space attached).
    """
    base = f"{_tenant()}/api/v1"
    async with httpx.AsyncClient(timeout=30, headers=await _headers()) as c:
        # The data-connections list is small; filter client-side to stay robust
        # across minor API filter-syntax differences.
        params = {"limit": 100}
        if space_id:
            params["spaceId"] = space_id
        r = await c.get(f"{base}/data-connections", params=params)
        if r.status_code >= 300:
            raise RuntimeError(f"list data-connections failed ({r.status_code}): {r.text}")
        conns = r.json().get("data", [])

    def _is_datafiles(conn: dict) -> bool:
        ds = (conn.get("datasourceID") or "").lower()
        name = (conn.get("qName") or conn.get("name") or "").lower()
        return ds == _DATAFILES_DATASOURCE.lower() or name == _DATAFILES_DATASOURCE.lower()

    def _space_of(conn: dict) -> str | None:
        return conn.get("space") or conn.get("spaceId")

    candidates = [c for c in conns if _is_datafiles(c)]
    if space_id:
        candidates = [c for c in candidates if _space_of(c) == space_id] or candidates
    else:
        personal = [c for c in candidates if not _space_of(c)]
        candidates = personal or candidates

    if not candidates:
        raise RuntimeError(
            "No DataFiles connection found for the target space. "
            "Ensure the space exists and you have access to its data files."
        )

    conn = candidates[0]
    conn_id = conn.get("id")
    conn_name = conn.get("qName") or conn.get("name") or _DATAFILES_DATASOURCE
    if not conn_id:
        raise RuntimeError(f"DataFiles connection has no id: {conn}")
    return conn_id, conn_name


async def upload_csv(path: str | Path, filename: str, connection_id: str) -> dict:
    """
    Upload a CSV to the data-files store under `filename`, into `connection_id`.
    Returns the created data-file resource. If a file with the same name already
    exists in that connection we overwrite it (delete-then-upload) so repeated
    generates on the same app are idempotent.
    """
    base = f"{_tenant()}/api/v1"
    data_bytes = Path(path).read_bytes()

    async with httpx.AsyncClient(timeout=120, headers=await _headers()) as c:
        await _delete_existing(c, base, filename, connection_id)

        meta = json.dumps({"name": filename, "connectionId": connection_id})
        # Multipart with two parts: File (the bytes) and Json (the metadata).
        files = {
            "File": (filename, data_bytes, "text/csv"),
            "Json": (None, meta, "application/json"),
        }
        r = await c.post(f"{base}/data-files", files=files)
        if r.status_code >= 300:
            raise RuntimeError(f"upload data-file failed ({r.status_code}): {r.text}")
        return r.json()


async def _delete_existing(
    c: httpx.AsyncClient, base: str, filename: str, connection_id: str
) -> None:
    """Best-effort delete of a same-named file in the connection (for overwrite)."""
    try:
        r = await c.get(
            f"{base}/data-files",
            params={"connectionId": connection_id, "name": filename},
        )
        if r.status_code >= 300:
            return
        for item in r.json().get("data", []):
            if item.get("name") == filename and item.get("id"):
                await c.delete(f"{base}/data-files/{item['id']}")
    except httpx.HTTPError:
        # Overwrite is a nicety; a failure here just means the upload may 409,
        # which we surface normally.
        return
