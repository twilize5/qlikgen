/**
 * Engine generic-object property builders for the Phase 2 dashboard.
 *
 * Turns a Python-side SheetSpec into qHyperCubeDef-backed object properties.
 * The plan contract (see backend/app/models.py):
 *
 *   sheet = { title, rank, visuals: [ VisualSpec ] }
 *   VisualSpec = {
 *     key, type,                              // kpi|barchart|linechart|piechart|table|filterpane
 *     dim?, dim_expr?, dim_label?,            // category charts + pie
 *     measure_label?, measure_expr?,
 *     dims?, measures?:[{label,expr}],        // table
 *     label?, expr?,                          // kpi
 *     fields?:[name],                         // filterpane
 *     options?:{ area?, donut? }
 *   }
 *
 * Dimensions use qFieldDefs (a raw field name, or an "=..." expression for a
 * computed/binned dimension); measures use a master-less inline qDef expression
 * like "Sum([Revenue])".
 */
import { createRequire } from "module";
const CHART_DEFAULTS = createRequire(import.meta.url)("./chart_defaults.json");

// ── low-level defs ──────────────────────────────────────────────────────────

function dimDefFromSpec(spec) {
  // A computed dimension (binned date) rides in qFieldDefs as an "=" expression.
  const def = spec.dim_expr || spec.dim;
  const label = spec.dim_label || spec.dim || def;
  return {
    qLibraryId: "",
    qNullSuppression: false,
    qDef: { qGrouping: "N", qFieldDefs: [def], qFieldLabels: [label] },
  };
}

function dimDefField(fieldName) {
  return {
    qLibraryId: "",
    qNullSuppression: false,
    qDef: { qGrouping: "N", qFieldDefs: [fieldName], qFieldLabels: [fieldName] },
  };
}

function measureDef(label, expr) {
  return {
    qLibraryId: "",
    qSortBy: { qSortByNumeric: -1, qSortByLoadOrder: 1 },
    qDef: { qLabel: label, qDef: expr, qGrouping: "N" },
  };
}

function fetch(height, width) {
  return [{ qTop: 0, qLeft: 0, qHeight: height, qWidth: width }];
}

function interColumnSortOrder(nDims, nMeasures) {
  return Array.from({ length: nDims + nMeasures }, (_, i) => i);
}

// ── per-type property builders ──────────────────────────────────────────────

function kpiProps(v) {
  return {
    qInfo: { qType: "kpi" },
    visualization: "kpi",
    showTitles: true,
    title: v.label,
    qHyperCubeDef: {
      qDimensions: [],
      qMeasures: [measureDef(v.label, v.expr)],
      qInitialDataFetch: fetch(1, 1),
    },
  };
}

// Bar and line share the 1-dim / 1-measure hypercube shape.
function catChartProps(type, v) {
  const title = v.title || `${v.measure_label} by ${v.dim_label || v.dim}`;
  const defaults = { ...(CHART_DEFAULTS[type] || {}) };

  if (type === "linechart") {
    // Area is a fill toggle on the line renderer, not a separate chart type.
    defaults.lineType = "line";
    defaults.stackedArea = !!(v.options && v.options.area);
  }

  return {
    qInfo: { qType: type },
    visualization: type,
    showTitles: true,
    title,
    ...defaults,
    qHyperCubeDef: {
      qDimensions: [dimDefFromSpec(v)],
      qMeasures: [measureDef(v.measure_label, v.measure_expr)],
      qInterColumnSortOrder: interColumnSortOrder(1, 1),
      qSuppressZero: false,
      qSuppressMissing: false,
      qMode: "S",
      qInitialDataFetch: fetch(1000, 2),
    },
  };
}

function pieProps(v) {
  const title = v.title || `${v.measure_label} by ${v.dim_label || v.dim}`;
  const donut = !!(v.options && v.options.donut);
  return {
    qInfo: { qType: "piechart" },
    visualization: "piechart",
    showTitles: true,
    title,
    ...(CHART_DEFAULTS.piechart || {}),
    // Override the captured donut flag with our choice, and drop the captured
    // library-item colour refs (they point at objects that don't exist here).
    donut: { showAsDonut: donut },
    color: { auto: true, mode: "byDimension" },
    qHyperCubeDef: {
      qDimensions: [dimDefFromSpec(v)],
      qMeasures: [measureDef(v.measure_label, v.measure_expr)],
      qInterColumnSortOrder: interColumnSortOrder(1, 1),
      qSuppressZero: false,
      qSuppressMissing: false,
      qMode: "S",
      qInitialDataFetch: fetch(100, 2),
    },
  };
}

// A choropleth (area) map: one measure shaded over a named-area geo dimension.
// Qlik Cloud maps are layer-based — the hypercube lives on the layer, not the
// object. Map-level settings come from CHART_DEFAULTS.map (capture from a real
// hand-built map to lock the exact client props, same as the other charts).
function mapProps(v) {
  const title = v.title || `${v.measure_label} by ${v.dim_label || v.dim}`;
  return {
    qInfo: { qType: "map" },
    visualization: "map",
    showTitles: true,
    title,
    ...(CHART_DEFAULTS.map || {}),
    gaLayers: [
      {
        type: "AreaLayer",
        cId: `${v.key}_area`,
        qHyperCubeDef: {
          qDimensions: [dimDefFromSpec(v)],
          qMeasures: [measureDef(v.measure_label, v.measure_expr)],
          qInterColumnSortOrder: interColumnSortOrder(1, 1),
          qSuppressZero: false,
          qSuppressMissing: false,
          qMode: "S",
          qInitialDataFetch: fetch(5000, 2),
        },
        // Let Qlik auto-resolve the dimension to areas by name.
        locationType: "auto",
        locationOrCoordinates: "location",
        color: { auto: true, mode: "byMeasure" },
      },
    ],
  };
}

function tableProps(v) {
  const dims = v.dims || [];
  const measures = v.measures || [];
  return {
    qInfo: { qType: "table" },
    visualization: "table",
    showTitles: true,
    title: v.title || "Details",
    ...(CHART_DEFAULTS.table || {}),
    qHyperCubeDef: {
      qDimensions: dims.map(dimDefField),
      qMeasures: measures.map((m) => measureDef(m.label, m.expr)),
      qInterColumnSortOrder: interColumnSortOrder(dims.length, measures.length),
      qSuppressZero: false,
      qSuppressMissing: false,
      qMode: "S",
      qColumnOrder: interColumnSortOrder(dims.length, measures.length),
      qInitialDataFetch: fetch(100, (dims.length + measures.length) || 1),
    },
  };
}

// A listbox child of a filter pane: one selectable field via a list object.
function listboxProps(field) {
  return {
    qInfo: { qType: "listbox" },
    visualization: "listbox",
    showTitles: true,
    title: field,
    ...(CHART_DEFAULTS.listbox || {}),
    qListObjectDef: {
      qDef: { qFieldDefs: [field], qFieldLabels: [field] },
      qShowAlternatives: true,
      qInitialDataFetch: [{ qTop: 0, qLeft: 0, qHeight: 100, qWidth: 1 }],
    },
  };
}

function filterPaneProps() {
  return {
    qInfo: { qType: "filterpane" },
    visualization: "filterpane",
    showTitles: false,
    title: "Filters",
    ...(CHART_DEFAULTS.filterpane || {}),
    qChildListDef: { qData: { title: "/title" } },
  };
}

/**
 * Ordered child objects to create for a sheet, each as { key, props, children? }.
 * `children` (filter panes) are created under the child's own handle, not the
 * sheet's — the sidecar handles that second level.
 */
function childrenForSheet(sheet) {
  const out = [];
  for (const v of sheet.visuals || []) {
    if (v.type === "kpi") {
      out.push({ key: v.key, props: kpiProps(v) });
    } else if (v.type === "barchart") {
      out.push({ key: v.key, props: catChartProps("barchart", v) });
    } else if (v.type === "linechart") {
      out.push({ key: v.key, props: catChartProps("linechart", v) });
    } else if (v.type === "piechart") {
      out.push({ key: v.key, props: pieProps(v) });
    } else if (v.type === "table") {
      out.push({ key: v.key, props: tableProps(v) });
    } else if (v.type === "map") {
      out.push({ key: v.key, props: mapProps(v) });
    } else if (v.type === "filterpane") {
      out.push({
        key: v.key,
        props: filterPaneProps(),
        children: (v.fields || []).map((f) => ({ props: listboxProps(f) })),
      });
    }
  }
  return out;
}

// ── layout ──────────────────────────────────────────────────────────────────

const GRID_COLS = 24;
const GRID_ROWS = 12;
const KPI_ROWS = 2;   // height of the KPI band
const RAIL_COLS = 5;  // width of the left filter rail

// A sheet cell carries BOTH the legacy grid (col/row/colspan/rowspan) and the
// responsive `bounds` (percentage). Qlik Cloud sizes an object's render canvas
// from `bounds`; without it, charts get a 0-sized plot area and render blank.
function cell(id, col, row, colspan, rowspan) {
  return {
    name: id,
    col,
    row,
    colspan,
    rowspan,
    bounds: {
      x: (col / GRID_COLS) * 100,
      y: (row / GRID_ROWS) * 100,
      width: (colspan / GRID_COLS) * 100,
      height: (rowspan / GRID_ROWS) * 100,
    },
  };
}

/**
 * Tile n rectangles into the region {x,y,w,h} so it is filled with no gaps:
 *   1 → full · 2 → stacked halves · 3 → full-width banner + two below ·
 *   n → 2-column grid. Integer-exact so cells abut on the 24x12 grid.
 */
function tile(n, x, y, w, h) {
  if (n <= 0) return [];
  if (n === 1) return [{ x, y, w, h }];
  if (n === 2) {
    const top = Math.round(h / 2);
    return [
      { x, y, w, h: top },
      { x, y: y + top, w, h: h - top },
    ];
  }
  if (n === 3) {
    const top = Math.round(h / 2);
    const left = Math.round(w / 2);
    return [
      { x, y, w, h: top },
      { x, y: y + top, w: left, h: h - top },
      { x: x + left, y: y + top, w: w - left, h: h - top },
    ];
  }
  // 2-column grid for 4+.
  const rows = Math.ceil(n / 2);
  const left = Math.round(w / 2);
  const rects = [];
  let yc = y;
  for (let r = 0; r < rows; r++) {
    const rowH = Math.floor((h - (yc - y)) / (rows - r)); // distribute remainder
    const idx = r * 2;
    rects.push({ x, y: yc, w: idx + 1 === n ? w : left, h: rowH });
    if (idx + 1 < n) rects.push({ x: x + left, y: yc, w: w - left, h: rowH });
    yc += rowH;
  }
  return rects.slice(0, n);
}

const FILTER_TOP_ROWS = 2; // height of a top filter strip

/**
 * Compute sheet cells for the placed children. `placed` is [{ key, id }];
 * types/options come from the sheet spec. KPIs band the top; the filter pane
 * takes the left rail (default) or a top strip (`filters_position: "top"`); the
 * remaining charts tile the rest. Coarse hints honored: chart order follows the
 * spec, and a chart flagged `options.emphasize` gets a full-width banner.
 */
function sheetCells(sheet, placed) {
  const byKey = Object.fromEntries(placed.map((p) => [p.key, p.id]));
  const typeOf = Object.fromEntries((sheet.visuals || []).map((v) => [v.key, v.type]));
  const optsOf = Object.fromEntries((sheet.visuals || []).map((v) => [v.key, v.options || {}]));
  const keys = placed.map((p) => p.key);

  const kpiKeys = keys.filter((k) => typeOf[k] === "kpi");
  const filterKeys = keys.filter((k) => typeOf[k] === "filterpane");
  const chartKeys = keys.filter(
    (k) => typeOf[k] && typeOf[k] !== "kpi" && typeOf[k] !== "filterpane"
  );

  const cells = [];

  // KPIs across the top, evenly, last one absorbs the remainder.
  const nk = Math.min(kpiKeys.length, 4);
  if (nk > 0) {
    const base = Math.floor(GRID_COLS / nk);
    let cx = 0;
    kpiKeys.slice(0, nk).forEach((k, i) => {
      const cw = i === nk - 1 ? GRID_COLS - cx : base;
      cells.push(cell(byKey[k], cx, 0, cw, KPI_ROWS));
      cx += cw;
    });
  }

  let contentTop = nk > 0 ? KPI_ROWS : 0;
  let contentX = 0;
  const filtersTop = (sheet.filters_position || "left") === "top";

  // Filter pane: either a top strip (pushes content down) or a left rail.
  if (filterKeys.length > 0) {
    if (filtersTop) {
      cells.push(cell(byKey[filterKeys[0]], 0, contentTop, GRID_COLS, FILTER_TOP_ROWS));
      contentTop += FILTER_TOP_ROWS;
    } else {
      cells.push(cell(byKey[filterKeys[0]], 0, contentTop, RAIL_COLS, GRID_ROWS - contentTop));
      contentX = RAIL_COLS;
    }
  }
  const contentW = GRID_COLS - contentX;
  const contentH = GRID_ROWS - contentTop;

  // Chart placement. An emphasized chart gets a full-width banner on top; the
  // rest tile below it. Otherwise the whole set tiles the content region.
  const emphKey = chartKeys.find((k) => optsOf[k] && optsOf[k].emphasize);
  if (emphKey && chartKeys.length > 1) {
    const bannerH = Math.max(1, Math.round(contentH * 0.5));
    cells.push(cell(byKey[emphKey], contentX, contentTop, contentW, bannerH));
    const rest = chartKeys.filter((k) => k !== emphKey);
    const rects = tile(rest.length, contentX, contentTop + bannerH, contentW, contentH - bannerH);
    rest.forEach((k, i) => {
      const r = rects[i];
      if (r) cells.push(cell(byKey[k], r.x, r.y, r.w, r.h));
    });
  } else {
    const rects = tile(chartKeys.length, contentX, contentTop, contentW, contentH);
    chartKeys.forEach((k, i) => {
      const r = rects[i];
      if (r) cells.push(cell(byKey[k], r.x, r.y, r.w, r.h));
    });
  }

  return cells;
}

function sheetProps(title, rank) {
  return {
    qInfo: { qType: "sheet" },
    qMetaDef: { title: title || "Overview", description: "" },
    rank: rank || 0,
    thumbnail: { qStaticContentUrlDef: { qUrl: "" } },
    columns: GRID_COLS,
    rows: GRID_ROWS,
    cells: [],
    qChildListDef: {
      qData: { title: "/title", visualization: "/visualization" },
    },
  };
}

export { childrenForSheet, sheetCells, sheetProps };
