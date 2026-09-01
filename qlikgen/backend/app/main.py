"""FastAPI entrypoint. Phase 0 endpoints — interactive OAuth version."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from . import pipeline, qix_client, secrets
from .models import (
    ConfigIn,
    CreateAppIn,
    GenerateIn,
    GenerateResponse,
    ProfileResponse,
)
from .qlik import auth, rest

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("qlikgen")

app = FastAPI(title="QlikGen", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    secrets.init_db()


# ---------- Config ----------


@app.get("/api/config")
def get_config() -> dict:
    return secrets.get_all(mask_secrets=True)


@app.post("/api/config")
def save_config(cfg: ConfigIn) -> dict:
    for field, value in cfg.model_dump().items():
        if value is None or value == "***":
            continue
        secrets.set_field(field, value)
    return {"ok": True}


# ---------- OAuth ----------

@app.get("/api/oauth/start")
def oauth_start() -> RedirectResponse:
    """Kick off the OAuth flow: 302 to Qlik's authorize URL."""
    try:
        url = auth.build_authorize_url()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=url, status_code=302)


@app.get("/api/oauth/callback")
async def oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
) -> HTMLResponse:
    """Qlik redirects here after login. Exchange code for tokens, then show
    a small HTML page that tells the user they can close the tab."""
    if error:
        return HTMLResponse(_callback_page(f"Login failed: {error} — {error_description or ''}", ok=False))
    if not code or not state:
        return HTMLResponse(_callback_page("Missing code or state in callback.", ok=False))

    try:
        await auth.exchange_code(code, state)
    except Exception as e:
        log.exception("token exchange failed")
        return HTMLResponse(_callback_page(f"Token exchange failed: {e}", ok=False))

    return HTMLResponse(_callback_page("Signed in. You can close this tab and return to QlikGen.", ok=True))


def _callback_page(msg: str, ok: bool) -> str:
    color = "#059669" if ok else "#dc2626"
    return f"""<!doctype html>
<html><head><title>QlikGen — sign in</title>
<style>
body{{font-family:-apple-system,Segoe UI,sans-serif;padding:3rem;max-width:40rem;margin:auto;color:#0f172a}}
.card{{border:1px solid #e2e8f0;border-radius:8px;padding:2rem;background:#fff}}
h1{{color:{color};margin-top:0}}
p{{color:#475569}}
</style></head>
<body><div class="card">
<h1>{'Success' if ok else 'Error'}</h1>
<p>{msg}</p>
<p><small>You can close this browser tab.</small></p>
</div></body></html>"""


@app.get("/api/auth/status")
def auth_status() -> dict:
    return {"signed_in": auth.is_signed_in()}


@app.post("/api/auth/signout")
def auth_signout() -> dict:
    auth.sign_out()
    return {"ok": True}


# ---------- Qlik health / spaces ----------

@app.get("/api/qlik/whoami")
async def qlik_whoami() -> dict:
    try:
        return await rest.whoami()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/qlik/spaces")
async def qlik_spaces() -> list[dict]:
    try:
        return await rest.list_spaces()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- Phase 0 smoke test ----------


@app.post("/api/test/create-app")
async def test_create_app(payload: CreateAppIn) -> dict:
    try:
        created = await rest.create_app(
            name=payload.name,
            space_id=payload.space_id,
            description="Created by QlikGen (Phase 0 smoke test)",
        )
        app_id = created.get("attributes", {}).get("id") or created.get("id")
        result: dict = {"app": created, "app_id": app_id}
        if payload.open_after_create and app_id:
            result["qix"] = await qix_client.open_app(app_id)
        secrets.record_run("create-app", "ok", app_id or "")
        return result
    except Exception as e:
        secrets.record_run("create-app", "error", str(e))
        log.exception("create-app failed")
        raise HTTPException(status_code=400, detail=str(e))


# ---------- Phase 1 CSV pipeline ----------


@app.post("/api/csv/profile", response_model=ProfileResponse)
async def csv_profile(
    file: UploadFile = File(...),
    brief: str = Form(""),
) -> ProfileResponse:
    """Profile an uploaded CSV locally (duckdb) and return the profile, a LOAD
    script preview, and the proposed dashboard plan. `brief` is optional free-text
    steering the LLM planner. Nothing touches Qlik yet."""
    try:
        raw = await file.read()
        if not raw:
            raise ValueError("Uploaded file is empty")
        upload_id, profile = pipeline.save_upload(raw, file.filename or "upload.csv")
        plan, plan_source, plan_note = await pipeline.resolve_plan(upload_id, profile, brief)
        return ProfileResponse(
            upload_id=upload_id,
            profile=profile,
            script_preview=pipeline.preview_script(profile),
            plan=plan,
            plan_source=plan_source,
            plan_note=plan_note,
        )
    except Exception as e:
        log.exception("csv profile failed")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/csv/{upload_id}/replan")
async def csv_replan(upload_id: str, brief: str = Body("", embed=True)):
    """Re-roll the dashboard plan for an already-profiled upload: clears the
    cached plan and proposes a fresh one (LLM, falling back to deterministic),
    applying the optional user `brief`."""
    try:
        plan, plan_source, plan_note = await pipeline.regenerate_plan(upload_id, brief)
        return {"upload_id": upload_id, "plan": plan,
                "plan_source": plan_source, "plan_note": plan_note}
    except Exception as e:
        log.exception("csv replan failed")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/debug/objects/{app_id}")
async def debug_objects(app_id: str) -> dict:
    """Diagnostic: dump all object properties in an app (for comparing a
    hand-built chart against a generated one). Writes the full JSON to disk so
    large dumps don't have to be copy-pasted."""
    import json as _json
    from .config import APP_DIR

    try:
        dump = await qix_client.dump_objects(app_id)
        out_path = APP_DIR / f"dump_{app_id}.json"
        out_path.write_text(_json.dumps(dump, indent=2, ensure_ascii=False))
        return {"saved_to": str(out_path), "count": dump.get("count")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/csv/generate", response_model=GenerateResponse)
async def csv_generate(payload: GenerateIn) -> GenerateResponse:
    """Run the full pipeline for a previously-profiled upload: create app,
    upload the CSV to the space, push the script, reload, and build the sheet."""
    try:
        return await pipeline.run_generate(
            upload_id=payload.upload_id,
            app_name=payload.app_name,
            space_id=payload.space_id,
        )
    except Exception as e:
        secrets.record_run("csv-generate", "error", str(e))
        log.exception("csv generate failed")
        raise HTTPException(status_code=400, detail=str(e))
