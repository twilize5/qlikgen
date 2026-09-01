// Vite proxies /api to the FastAPI backend on :8787.

export type Config = {
  tenant_url: string | null;
  qlik_client_id: string | null;
  target_space_id: string | null;
  qlik_refresh_token: string | null;  // "***" when set, null when unset
  anthropic_api_key: string | null;
  anthropic_workspace_id: string | null;
};

export type Space = { id: string; name: string; type: string };

// ---- Phase 1: CSV pipeline ----

export type ColumnProfile = {
  name: string;
  qlik_type: string;
  role: "dimension" | "measure" | "temporal";
  distinct: number;
  null_pct: number;
  min: string | null;
  max: string | null;
  samples: string[];
};

export type CsvProfile = {
  table_name: string;
  filename: string;
  row_count: number;
  columns: ColumnProfile[];
};

export type VisualType =
  | "kpi"
  | "barchart"
  | "linechart"
  | "piechart"
  | "table"
  | "filterpane"
  | "map";

export type VisualSpec = {
  key: string;
  type: VisualType;
  dim?: string | null;
  dim_expr?: string | null;
  dim_label?: string | null;
  measure_label?: string | null;
  measure_expr?: string | null;
  dims?: string[];
  measures?: { label: string; expr: string }[];
  label?: string | null;
  expr?: string | null;
  fields?: string[];
  options?: Record<string, unknown>;
};

export type SheetSpec = {
  title: string;
  rank: number;
  filters_position?: "left" | "top";
  visuals: VisualSpec[];
};

export type DashboardPlan = {
  sheets: SheetSpec[];
};

export type ProfileResponse = {
  upload_id: string;
  profile: CsvProfile;
  script_preview: string;
  plan: DashboardPlan;
  plan_source: string;
  plan_note: string;
};

export type GenerateResponse = {
  app_id: string;
  app_name: string;
  space_id: string | null;
  file_ref: string;
  reload: { ok: boolean; reloaded: boolean; detail: string };
  sheet_id: string | null;
  sheet_ids: string[];
  plan_source: string;
  object_ids: Record<string, string>;
  open_url: string | null;
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.text();
    throw new Error(body || `${r.status} ${r.statusText}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  getConfig: () => fetch("/api/config").then(j<Config>),

  saveConfig: (patch: Partial<Config>) =>
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<{ ok: true }>),

  authStatus: () => fetch("/api/auth/status").then(j<{ signed_in: boolean }>),

  signOut: () =>
    fetch("/api/auth/signout", { method: "POST" }).then(j<{ ok: true }>),

  whoami: () => fetch("/api/qlik/whoami").then(j<any>),

  spaces: () => fetch("/api/qlik/spaces").then(j<Space[]>),

  createTestApp: (payload: {
    name: string;
    space_id?: string | null;
    open_after_create?: boolean;
  }) =>
    fetch("/api/test/create-app", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<any>),

  profileCsv: (file: File, brief = "") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("brief", brief);
    return fetch("/api/csv/profile", { method: "POST", body: fd }).then(
      j<ProfileResponse>
    );
  },

  replan: (uploadId: string, brief = "") =>
    fetch(`/api/csv/${uploadId}/replan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief }),
    }).then(
      j<{
        upload_id: string;
        plan: DashboardPlan;
        plan_source: string;
        plan_note: string;
      }>
    ),

  generateFromCsv: (payload: {
    upload_id: string;
    app_name: string;
    space_id?: string | null;
  }) =>
    fetch("/api/csv/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(j<GenerateResponse>),
};

// Kick off OAuth by full-page navigating to the backend endpoint (which 302s to Qlik).
export const oauthStartUrl = () => "http://localhost:8787/api/oauth/start";
