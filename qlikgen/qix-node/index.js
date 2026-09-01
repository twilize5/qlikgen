/**
 * QIX sidecar. Wraps enigma.js so the Python backend can talk to the
 * Qlik Cloud Engine API over WebSocket.
 *
 * Endpoints:
 *   POST /qix/open-app     — Phase 0 smoke test (open, read layout, close).
 *   POST /qix/set-script   — Phase 1: SetScript + DoReload + DoSave.
 *   POST /qix/build-app    — Phase 2: create objects across N sheets, DoSave.
 *
 * Every request carries { tenant, token, appId }; the OAuth bearer token is
 * injected as a WebSocket request header (the browser client can't do this).
 */
import express from "express";
import enigma from "enigma.js";
import WebSocket from "ws";
import { createRequire } from "module";
import { childrenForSheet, sheetCells, sheetProps } from "./build_objects.js";

const require = createRequire(import.meta.url);
// Schema shipped by enigma.js/api-spec. 12.2000.0 is Qlik Cloud's current
// Engine schema at time of writing — bump if your tenant is newer.
const schema = require("enigma.js/schemas/12.2015.0.json");

const app = express();
app.use(express.json({ limit: "8mb" }));

app.get("/health", (_req, res) => res.json({ ok: true }));

/**
 * Open an app for a given { tenant, token, appId } and return the enigma
 * session + global + doc handles. Caller is responsible for session.close().
 */
async function openDoc({ tenant, token, appId }) {
  const host = tenant.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const url = `wss://${host}/app/${appId}`;

  const session = enigma.create({
    schema,
    url,
    createSocket: (u) =>
      new WebSocket(u, { headers: { Authorization: `Bearer ${token}` } }),
  });

  const global = await session.open();
  const doc = await global.openDoc(appId);
  return { session, global, doc };
}

function requireFields(req, res, names) {
  const body = req.body || {};
  for (const n of names) {
    if (!body[n]) {
      res.status(400).json({ error: `${n} is required` });
      return null;
    }
  }
  return body;
}

/**
 * Phase 0: open an app, read a lightweight layout, close the session.
 * If this returns successfully, auth + WebSocket + Engine API all work.
 */
app.post("/qix/open-app", async (req, res) => {
  const body = requireFields(req, res, ["tenant", "token", "appId"]);
  if (!body) return;

  let session;
  try {
    const opened = await openDoc(body);
    session = opened.session;
    const doc = opened.doc;

    const appLayout = await doc.getAppLayout();
    const sheetList = await doc.getObjects({
      qTypes: ["sheet"],
      qIncludeSessionObjects: false,
    });
    const script = await doc.getScript();

    res.json({
      docId: appLayout.qFileName || body.appId,
      title: appLayout.qTitle || null,
      sheets: sheetList.map((s) => ({ id: s.qInfo.qId, type: s.qInfo.qType })),
      script_len: (script || "").length,
    });
  } catch (err) {
    console.error("[qix] open-app failed:", err);
    res.status(500).json({ error: errMsg(err) });
  } finally {
    await closeQuiet(session);
  }
});

/**
 * Phase 1: replace the load script, reload, and save.
 * Returns { ok, reloaded }. A reload that throws is reported with ok:false and
 * the engine error message so the caller can surface it.
 */
app.post("/qix/set-script", async (req, res) => {
  const body = requireFields(req, res, ["tenant", "token", "appId", "script"]);
  if (!body) return;

  let session;
  try {
    const opened = await openDoc(body);
    session = opened.session;
    const doc = opened.doc;

    await doc.setScript(body.script);

    let reloaded = false;
    let detail = "";
    try {
      // qMode=0 (default), qPartial=false, qDebug=false
      reloaded = await doc.doReload(0, false, false);
    } catch (reloadErr) {
      detail = errMsg(reloadErr);
    }

    await doc.doSave();
    res.json({ ok: reloaded, reloaded, detail });
  } catch (err) {
    console.error("[qix] set-script failed:", err);
    res.status(500).json({ error: errMsg(err) });
  } finally {
    await closeQuiet(session);
  }
});

/**
 * Phase 2: build a multi-sheet dashboard plan.
 * For each sheet in the plan, create its visualizations (and any filter-pane
 * listbox children), lay them out on the grid, and save. Returns the ordered
 * sheet ids + a per-key object-id map + read-back diagnostics.
 */
async function buildOneSheet(doc, sheetSpec) {
  // 1. Create the sheet (empty cells for now), carrying its rank so sheet order
  //    in the app matches the plan order.
  const sheet = await doc.createObject(sheetProps(sheetSpec.title, sheetSpec.rank));
  const sheetId = sheet.id;

  // 2. Create each visualization as a child of the sheet.
  const placed = [];
  const diagnostics = [];
  for (const child of childrenForSheet(sheetSpec)) {
    const handle = await sheet.createChild(child.props);
    placed.push({ key: child.key, id: handle.id });

    // Filter panes carry listbox children created under the pane itself.
    if (child.children && child.children.length) {
      for (const gc of child.children) {
        try {
          await handle.createChild(gc.props);
        } catch (e) {
          diagnostics.push({ key: child.key, childError: errMsg(e) });
        }
      }
    }

    // Diagnostic: read back the hypercube so we can tell whether a blank chart
    // is empty data (definition problem) or a client render problem. Objects
    // without a hypercube (filterpane) are skipped past the data pull.
    const type = child.props.qInfo.qType;
    const d = { key: child.key, id: handle.id, type };
    try {
      const lay = await handle.getLayout();
      const hc = lay.qHyperCube || {};
      d.size = hc.qSize || null; // { qcx, qcy }
      d.error = hc.qError || null;
      d.dimError = (hc.qDimensionInfo || []).map((x) => x.qError || null);
      d.measError = (hc.qMeasureInfo || []).map((x) => x.qError || null);
      if (type !== "kpi" && type !== "filterpane" && hc.qSize) {
        const pages = await handle.getHyperCubeData("/qHyperCubeDef", [
          { qTop: 0, qLeft: 0, qHeight: 2, qWidth: hc.qSize.qcx || 5 },
        ]);
        const matrix = (pages && pages[0] && pages[0].qMatrix) || [];
        d.sampleRows = matrix.length;
        d.firstRow = (matrix[0] || []).map((c) => c.qText);
      }
    } catch (e) {
      d.layoutError = errMsg(e);
    }
    diagnostics.push(d);
  }

  // 3. Lay the children out on the sheet grid.
  const props = await sheet.getProperties();
  props.cells = sheetCells(sheetSpec, placed);
  await sheet.setProperties(props);

  const objectIds = {};
  for (const p of placed) objectIds[p.key] = p.id;
  return { sheet_id: sheetId, object_ids: objectIds, diagnostics };
}

app.post("/qix/build-app", async (req, res) => {
  const body = requireFields(req, res, ["tenant", "token", "appId", "plan"]);
  if (!body) return;

  let session;
  try {
    const opened = await openDoc(body);
    session = opened.session;
    const doc = opened.doc;

    const sheets = (body.plan && body.plan.sheets) || [];
    const results = [];
    for (const sheetSpec of sheets) {
      results.push(await buildOneSheet(doc, sheetSpec));
    }

    await doc.doSave();

    res.json({
      sheet_ids: results.map((r) => r.sheet_id),
      sheets: results,
      // Back-compat: first sheet's ids at the top level.
      sheet_id: results[0] ? results[0].sheet_id : null,
      object_ids: results[0] ? results[0].object_ids : {},
      diagnostics: results.flatMap((r) => r.diagnostics),
    });
  } catch (err) {
    console.error("[qix] build-app failed:", err);
    res.status(500).json({ error: errMsg(err) });
  } finally {
    await closeQuiet(session);
  }
});

/**
 * Diagnostic: dump the properties of every non-sheet object in the app.
 * Used to diff a hand-created (working) chart against our generated (blank) one.
 */
app.post("/qix/dump-objects", async (req, res) => {
  const body = requireFields(req, res, ["tenant", "token", "appId"]);
  if (!body) return;

  let session;
  try {
    const opened = await openDoc(body);
    session = opened.session;
    const doc = opened.doc;

    const infos = await doc.getAllInfos();
    const out = [];
    for (const info of infos) {
      try {
        const obj = await doc.getObject(info.qId);
        const props = await obj.getProperties();
        out.push({ id: info.qId, type: info.qType, props });
      } catch (e) {
        out.push({ id: info.qId, type: info.qType, error: errMsg(e) });
      }
    }
    res.json({ count: out.length, objects: out });
  } catch (err) {
    console.error("[qix] dump-objects failed:", err);
    res.status(500).json({ error: errMsg(err) });
  } finally {
    await closeQuiet(session);
  }
});

function errMsg(err) {
  if (err && err.message) return String(err.message);
  // enigma engine errors carry a structured payload.
  if (err && err.parameter) return JSON.stringify(err.parameter);
  return String(err);
}

async function closeQuiet(session) {
  if (!session) return;
  try {
    await session.close();
  } catch (_) {
    /* ignore */
  }
}

const PORT = 8788;
app.listen(PORT, "127.0.0.1", () => {
  console.log(`[qix] sidecar listening on http://127.0.0.1:${PORT}`);
});
