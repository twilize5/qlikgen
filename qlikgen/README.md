# QlikGen — Phase 0 (interactive OAuth)

Local desktop web app that generates Qlik Sense apps on Qlik Cloud from CSV/SQL.

**Phase 0 scope:** you create a **Native** OAuth client in your Qlik tenant → click "Sign in with Qlik" in QlikGen → log in with your normal Qlik account in the browser → QlikGen stores a refresh token in your OS keyring → creates an empty app via REST → opens it via the Engine API (QIX) to prove the WebSocket handshake works.

## Stack

- Backend: Python + FastAPI on `127.0.0.1:8787`
- QIX sidecar: Node 20 + Express + enigma.js on `127.0.0.1:8788`
- UI: Vite + React + TypeScript + Tailwind on `127.0.0.1:5173`
- Secrets: OS keyring; non-secrets in SQLite in `~/.qlikgen/`

## Prerequisites

- Python 3.11+
- Node 20 LTS

Verify:
```
py -3 --version    # Windows
python3 --version  # macOS/Linux
node --version
```

## Step 1 — Create a Native OAuth client in Qlik

1. In your tenant admin: `https://<tenant>/console` → Integrations → **OAuth**
2. **Create new** → Client type: **Native**
3. Fill in:
   - **Name:** `QlikGen`
   - **Description:** local dashboard generator
   - **Redirect URIs:** `http://localhost:8787/api/oauth/callback` (exactly this — no trailing slash)
   - **Scopes:** tick `admin_classic`, `user_default`, and `offline_access` (refresh tokens)
   - **Allowed origins:** leave blank (not needed for Native)
4. Save. Copy the **Client ID** — Native clients don't have a secret.

## Step 2 — Run QlikGen

**Windows:**
```
scripts\setup.cmd
start.cmd
```

**macOS/Linux:**
```
./scripts/setup.sh
./start.sh
```

Browser opens to `http://localhost:5173`.

## Step 3 — Configure & sign in

1. Paste **tenant URL** (e.g. `https://qbc90wfggcojjmn.in.qlikcloud.com`)
2. Paste **OAuth client ID** from step 1
3. Paste **Anthropic API key**
4. Click **Save config**
5. Click **Sign in with Qlik** — a browser tab opens to Qlik's login page. Log in with your normal account. On success the tab shows a green "Signed in" page — close it and return to QlikGen.
6. **Check Qlik auth** → should show your name/email
7. **Load spaces** → pick target space (or leave blank for personal)
8. **Create test app** → creates empty app, opens via QIX, shows link

## Phase 1 — CSV pipeline

Once you're signed in, the **CSV → dashboard** panel appears. Flow:

1. **Profile CSV** — pick a `.csv`. The backend profiles it locally with duckdb
   (no Qlik, no LLM) and returns column types/roles, a `LOAD` script preview, and
   the proposed template. Nothing has touched Qlik yet.
2. **Generate dashboard** — creates a new app in the selected space, uploads the
   CSV into that space's **DataFiles** connection, pushes the `LOAD` script via
   QIX `SetScript`, runs `DoReload`, and (if the reload produced data) builds a
   **multi-sheet plan**: an **Overview** sheet (4 KPIs + full-width bar + a
   month-binned area/line + a pie/donut + a left-rail filter pane) and a
   **Details** sheet (filter pane + the full table). You get an "Open app in
   Qlik" link.

Everything in Phase 1 is deterministic — the script and template are pure
functions of the profile, so the LOAD fields and the chart field references can't
drift apart. (The Anthropic key is stored but not used yet; LLM assistance is a
later phase, in a "propose, don't emit" shape.)

### Endpoints

- `POST /api/csv/profile` (multipart `file`) → `{ upload_id, profile, script_preview, plan, plan_source }`
- `POST /api/csv/generate` `{ upload_id, app_name, space_id? }` → app id, reload result, `sheet_ids`

### Sidecar endpoints (`qix-node`)

- `POST /qix/set-script` — `SetScript` + `DoReload` + `DoSave`
- `POST /qix/build-app` — build every sheet in the plan (KPI/bar/line/pie/table +
  filter-pane listboxes), lay each out on a 24×12 grid, `DoSave`

### Phase 2 — richer, planned dashboards

The generated app is now a `DashboardPlan` (`backend/app/models.py`): an ordered
list of sheets, each a list of generic `VisualSpec`s (type + field mapping only).
`template_builder.build_plan` chooses it deterministically; `build_objects.js`
turns each visual into Engine object properties and tiles them to fill the sheet.

The plan shape is deliberately the same struct the **Phase 2b** LLM emits — so
the model only ever *proposes* types and fields, and the deterministic builder
still emits every property.

**Phase 2b (propose, don't emit).** `plan_gen.propose_plan` makes one Claude call
(`claude-opus-5`, via the `anthropic` SDK, using the Anthropic key from Phase 0
step 3) that turns the CSV profile into a `DashboardPlan`. `plan_validator`
checks it against the profile — every field and every `[Field]` in an expression
must exist, chart/data pairings must be sane (e.g. a pie's dimension can't have
5,000 values), and the plan can't be empty. On **any** failure — no key, API
error, bad JSON, or a rejected plan — `plan_gen.plan_for_profile` falls back to
the deterministic `build_plan`, so the pipeline never breaks for LLM reasons. The
chosen plan is cached per upload (`~/.qlikgen/uploads/<id>.plan.json`) so the
preview and the built app always match; `plan_source` (`llm` / `deterministic`)
is surfaced in the API and the UI.

**User brief.** An optional free-text `brief` steers the LLM planner — which
visuals and chart types, how many sheets, and *coarse* layout hints. Layout stays
deterministic (the engine owns pixels), but honors hints the plan can carry:
chart order, one `options.emphasize` chart per sheet (full-width feature), and a
sheet's `filters_position` (`left` rail / `top` strip). The brief only affects the
LLM path; the deterministic fallback ignores it. The cache keys on the brief, so
editing it and hitting **Regenerate plan** re-rolls. Styling/theming is not yet
in scope — colors stay Qlik defaults.

- `POST /api/csv/profile` now also takes a `brief` form field.
- `POST /api/csv/{upload_id}/replan` `{ brief }` → re-rolls the plan.

Supported visual types: `kpi barchart linechart piechart table filterpane map`.
When the LLM plan can't be used, `plan_note` explains why (e.g. an unsupported
type, no API key), and the UI shows it — the fallback is never silent.

**Maps.** A `map` is a named-area choropleth (a measure shaded over a geographic
field). `geo.py` flags mappable columns by name (country/state/county/city/postal),
that hint is sent to the planner, and `plan_validator` refuses a map on any
non-geo field — so a map only builds on real geography, and only when the brief
asks for one (the deterministic fallback never emits a map). The map object is
layer-based; `mapProps` builds an `AreaLayer` choropleth and reads map-level
settings from `chart_defaults.json` → `map`. Those settings are a best-effort
starting point — if a generated map renders blank, capture a hand-built map's
props via `POST /qix/dump-objects` and paste the `map` object's properties into
`chart_defaults.json`, exactly how bar/line/pie were captured.

New/changed visuals are captured appearance defaults in `chart_defaults.json`
(`piechart`, `filterpane`, `listbox`, `table`); the area chart is a
`lineType`/`stackedArea` toggle on `linechart`; date trends are binned to month
via a computed dimension (`=Date(MonthStart([field]),'MMM-YYYY')`).

### Notes / gotchas

- **Space match matters.** The CSV is uploaded into the *same* space the app is
  created in; a `DoReload` reading `lib://DataFiles/<file>` fails if the file and
  app live in different spaces. Pick your target space before generating.
- **Reload before charts.** If `DoReload` fails, the sheet is not built (charts on
  an empty model render blank); the UI shows the engine error so you can fix the
  script/data and regenerate.
- Uploaded CSVs + cached profiles live in `~/.qlikgen/uploads/`.

## Troubleshooting

- **"redirect_uri_mismatch"** on login — the Native client's Redirect URI must be *exactly* `http://localhost:8787/api/oauth/callback`. Check for trailing slash or `127.0.0.1` vs `localhost`.
- **Sign-in tab hangs** — the backend must be running. Check the FastAPI window for errors.
- **Refresh fails after time** — refresh tokens can be revoked. Just click Sign in again.
- **QIX handshake fails** — Qlik may have bumped the Engine schema; in `qix-node/index.js` bump `12.2000.0` and `npm i` again in that folder.
