"""
Config persistence.

Split:
- Secrets (refresh token, Anthropic key) → OS keyring
- Non-secrets (tenant URL, client_id, space) → SQLite
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Optional

import keyring

from .config import DB_PATH, KEYRING_KEYS, KEYRING_SERVICE

PLAIN_FIELDS = {
    "tenant_url", "qlik_client_id", "target_space_id", "anthropic_workspace_id"
}
SECRET_FIELDS = set(KEYRING_KEYS.keys())
ALL_FIELDS = PLAIN_FIELDS | SECRET_FIELDS


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT
            )
            """
        )


def get(field: str) -> Optional[str]:
    if field not in ALL_FIELDS:
        raise ValueError(f"Unknown field: {field}")
    if field in SECRET_FIELDS:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_KEYS[field])
    with _db() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (field,)).fetchone()
        return row["value"] if row else None


def set_field(field: str, value: str) -> None:
    if field not in ALL_FIELDS:
        raise ValueError(f"Unknown field: {field}")
    if field in SECRET_FIELDS:
        keyring.set_password(KEYRING_SERVICE, KEYRING_KEYS[field], value)
    else:
        with _db() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (field, value),
            )


def delete(field: str) -> None:
    if field not in ALL_FIELDS:
        raise ValueError(f"Unknown field: {field}")
    if field in SECRET_FIELDS:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_KEYS[field])
        except keyring.errors.PasswordDeleteError:
            pass
    else:
        with _db() as conn:
            conn.execute("DELETE FROM kv WHERE key = ?", (field,))


def get_all(mask_secrets: bool = True) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for f in ALL_FIELDS:
        val = get(f)
        if f in SECRET_FIELDS and val and mask_secrets:
            out[f] = "***"
        else:
            out[f] = val
    return out


def record_run(kind: str, status: str, detail: str = "") -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO runs (kind, status, detail) VALUES (?, ?, ?)",
            (kind, status, detail),
        )
