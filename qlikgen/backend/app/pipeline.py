"""
Phase 1 CSV pipeline orchestration.

Mirrors how main.py sequenced REST + QIX for the Phase 0 smoke test:

  profile (duckdb)              [local, at upload time]
    -> create app              (rest.create_app)
    -> upload CSV to space     (data_files.upload_csv)
    -> push script + reload    (qix_client.set_script_and_reload)
    -> build 1-sheet template  (qix_client.build_sheet)

The profile computed at upload time is cached to disk so /generate doesn't have
to re-read the CSV, and so the script/template are built from the exact same
CsvProfile the user previewed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path

from . import plan_gen, qix_client, secrets
from .config import UPLOADS_DIR
from .models import (
    CsvProfile,
    DashboardPlan,
    GenerateResponse,
    ReloadResult,
)
from .profiling import profile_csv
from .qlik import data_files, rest
from .script_gen import build_load_script

log = logging.getLogger("qlikgen")


# ---------- upload store ----------


def _csv_path(upload_id: str) -> Path:
    return UPLOADS_DIR / f"{upload_id}.csv"


def _meta_path(upload_id: str) -> Path:
    return UPLOADS_DIR / f"{upload_id}.json"


def _plan_path(upload_id: str) -> Path:
    return UPLOADS_DIR / f"{upload_id}.plan.json"


def _safe_filename(name: str) -> str:
    """Keep a readable, Qlik-friendly file name for the data-files store."""
    base = Path(name).name or "upload.csv"
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", base).strip("._-")
    if not cleaned:
        cleaned = "upload.csv"
    if not cleaned.lower().endswith(".csv"):
        cleaned += ".csv"
    return cleaned


def _to_utf8(raw: bytes) -> bytes:
    """
    Normalise CSV bytes to UTF-8 (no BOM).

    Excel/Qlik exports are frequently Windows-1252 (e.g. the Superstore sample),
    which breaks both duckdb's strict UTF-8 reader and a Qlik LOAD declared as
    `utf8`. We transcode once at ingest so duckdb, the stored data file, and the
    reload all see consistent UTF-8.
    """
    # Already valid UTF-8 (with or without BOM)? utf-8-sig strips a BOM if present.
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc).encode("utf-8")
        except UnicodeDecodeError:
            continue
    # Common single-byte fallbacks; cp1252 is a near-superset that rarely fails.
    for enc in ("cp1252", "latin-1"):
        try:
            return raw.decode(enc).encode("utf-8")
        except UnicodeDecodeError:
            continue
    # Last resort: replace undecodable bytes rather than fail the upload.
    return raw.decode("utf-8", errors="replace").encode("utf-8")


def save_upload(raw: bytes, original_name: str) -> tuple[str, CsvProfile]:
    """Persist the CSV, profile it, cache the profile, return (upload_id, profile)."""
    upload_id = uuid.uuid4().hex
    filename = _safe_filename(original_name)

    path = _csv_path(upload_id)
    path.write_bytes(_to_utf8(raw))

    profile = profile_csv(path, filename)
    _meta_path(upload_id).write_text(profile.model_dump_json(indent=2))
    return upload_id, profile


def load_profile(upload_id: str) -> CsvProfile:
    meta = _meta_path(upload_id)
    if not meta.exists():
        raise ValueError(f"Unknown or expired upload_id: {upload_id}")
    return CsvProfile(**json.loads(meta.read_text()))


# ---------- preview + generate ----------


def preview_script(profile: CsvProfile) -> str:
    """Script preview shown before anything hits Qlik (connection unknown yet)."""
    return build_load_script(profile, connection_name="DataFiles", filename=profile.filename)


async def resolve_plan(
    upload_id: str, profile: CsvProfile, brief: str | None = None
) -> tuple[DashboardPlan, str, str]:
    """
    The dashboard plan for an upload, chosen once and cached so the preview and
    the build use the same plan. Tries the LLM (Phase 2b, steered by the optional
    user `brief`) and falls back to the deterministic builder; the LLM call runs
    in a worker thread so it doesn't block the event loop. Returns
    (plan, source, note) — note explains a fallback when source is deterministic.

    `brief=None` means "use whatever is cached" (the build step passes None so it
    reuses the exact plan the user previewed). A string (including "") recomputes
    when it differs from the cached brief, so editing the brief re-plans.
    """
    pp = _plan_path(upload_id)
    if pp.exists():
        cached = json.loads(pp.read_text())
        if brief is None or cached.get("brief", "") == brief:
            return (
                DashboardPlan(**cached["plan"]),
                cached.get("plan_source", "deterministic"),
                cached.get("plan_note", ""),
            )

    plan, source, note = await asyncio.to_thread(
        plan_gen.plan_for_profile, profile, brief or ""
    )
    pp.write_text(json.dumps(
        {"plan": plan.model_dump(), "plan_source": source,
         "plan_note": note, "brief": brief or ""}, indent=2
    ))
    return plan, source, note


async def regenerate_plan(upload_id: str, brief: str = "") -> tuple[DashboardPlan, str, str]:
    """Drop the cached plan and choose a fresh one (re-rolls the LLM proposal),
    applying the optional user brief."""
    _plan_path(upload_id).unlink(missing_ok=True)
    profile = load_profile(upload_id)
    return await resolve_plan(upload_id, profile, brief)


async def run_generate(upload_id: str, app_name: str, space_id: str | None) -> GenerateResponse:
    profile = load_profile(upload_id)
    csv_path = _csv_path(upload_id)
    if not csv_path.exists():
        raise ValueError(f"Uploaded file missing for upload_id: {upload_id}")

    # 1. Create the app in the target space.
    created = await rest.create_app(
        name=app_name,
        space_id=space_id,
        description="Created by QlikGen (Phase 1 CSV pipeline)",
    )
    app_id = created.get("attributes", {}).get("id") or created.get("id")
    if not app_id:
        raise RuntimeError(f"create_app returned no id: {created}")

    # 2. Resolve the DataFiles connection for that same space and upload the CSV.
    conn_id, conn_name = await data_files.resolve_connection(space_id)
    await data_files.upload_csv(csv_path, profile.filename, conn_id)
    file_ref = f"lib://{conn_name}/{profile.filename}"

    # 3. Generate the LOAD script (against the resolved connection) and push it.
    script = build_load_script(profile, conn_name, profile.filename)
    reload_raw = await qix_client.set_script_and_reload(app_id, script)
    reload = ReloadResult(
        ok=bool(reload_raw.get("ok")),
        reloaded=bool(reload_raw.get("reloaded")),
        detail=str(reload_raw.get("detail") or ""),
    )

    result = GenerateResponse(
        app_id=app_id,
        app_name=app_name,
        space_id=space_id,
        file_ref=file_ref,
        reload=reload,
        open_url=_open_url(app_id),
    )

    # 4. Only build the sheets if the reload produced data — charts on an empty
    #    model would render blank. Surface the reload error otherwise. The plan
    #    is the same one previewed at profile time (LLM or deterministic).
    if reload.reloaded:
        plan, plan_source, _note = await resolve_plan(upload_id, profile)
        build_raw = await qix_client.build_app(app_id, plan.model_dump())
        result.sheet_id = build_raw.get("sheet_id")
        result.sheet_ids = build_raw.get("sheet_ids", [])
        result.object_ids = build_raw.get("object_ids", {})
        result.diagnostics = build_raw.get("diagnostics", [])
        result.plan_source = plan_source
        log.info("build-app (%s) diagnostics: %s", plan_source, result.diagnostics)

    secrets.record_run(
        "csv-generate",
        "ok" if reload.reloaded else "reload-failed",
        f"{app_id} {reload.detail}".strip(),
    )
    return result


def _open_url(app_id: str) -> str | None:
    tenant = secrets.get("tenant_url")
    if not tenant:
        return None
    return f"{tenant.rstrip('/')}/sense/app/{app_id}"
