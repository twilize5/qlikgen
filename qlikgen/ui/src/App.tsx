import { useEffect, useState } from "react";
import { api, oauthStartUrl, type Config, type Space } from "./lib/api";
import CsvPanel from "./CsvPanel";

type Status =
  | { kind: "idle" }
  | { kind: "loading"; msg: string }
  | { kind: "ok"; msg: string; data?: any }
  | { kind: "error"; msg: string };

const emptyConfig: Config = {
  tenant_url: "",
  qlik_client_id: "",
  target_space_id: "",
  qlik_refresh_token: "",
  anthropic_api_key: "",
  anthropic_workspace_id: "",
};

export default function App() {
  const [cfg, setCfg] = useState<Config>(emptyConfig);
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [appName, setAppName] = useState<string>("QlikGen smoke test");
  const [signedIn, setSignedIn] = useState<boolean>(false);

  const refreshState = () => {
    api
      .getConfig()
      .then((c) =>
        setCfg({
          tenant_url: c.tenant_url ?? "",
          qlik_client_id: c.qlik_client_id ?? "",
          target_space_id: c.target_space_id ?? "",
          qlik_refresh_token: c.qlik_refresh_token ?? "",
          anthropic_api_key: c.anthropic_api_key ?? "",
          anthropic_workspace_id: c.anthropic_workspace_id ?? "",
        })
      )
      .catch(() => {});
    api.authStatus().then((s) => setSignedIn(s.signed_in)).catch(() => {});
  };

  useEffect(() => {
    refreshState();
    // Re-check sign-in state when the tab regains focus (after OAuth login).
    const onFocus = () => api.authStatus().then((s) => setSignedIn(s.signed_in));
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  const update = (k: keyof Config, v: string) =>
    setCfg((prev) => ({ ...prev, [k]: v }));

  const save = async () => {
    setStatus({ kind: "loading", msg: "Saving config..." });
    try {
      const patch: Partial<Config> = {};
      (Object.keys(cfg) as (keyof Config)[]).forEach((k) => {
        const v = cfg[k];
        if (v !== null && v !== "" && v !== "***") patch[k] = v;
      });
      await api.saveConfig(patch);
      setStatus({ kind: "ok", msg: "Saved." });
    } catch (e: any) {
      setStatus({ kind: "error", msg: e.message ?? String(e) });
    }
  };

  const signIn = () => {
    if (!cfg.tenant_url || !cfg.qlik_client_id) {
      setStatus({
        kind: "error",
        msg: "Fill in tenant URL and client ID, then Save, before signing in.",
      });
      return;
    }
    // Full-page navigate so the OAuth redirect chain works cleanly.
    window.location.href = oauthStartUrl();
  };

  const signOut = async () => {
    await api.signOut();
    setSignedIn(false);
    setStatus({ kind: "ok", msg: "Signed out." });
  };

  const checkAuth = async () => {
    setStatus({ kind: "loading", msg: "Checking Qlik auth..." });
    try {
      const me = await api.whoami();
      setStatus({
        kind: "ok",
        msg: `Auth OK — ${me.email ?? me.name ?? "user"}`,
      });
    } catch (e: any) {
      setStatus({ kind: "error", msg: e.message ?? String(e) });
    }
  };

  const loadSpaces = async () => {
    setStatus({ kind: "loading", msg: "Loading spaces..." });
    try {
      const s = await api.spaces();
      setSpaces(s);
      setStatus({ kind: "ok", msg: `Loaded ${s.length} spaces.` });
    } catch (e: any) {
      setStatus({ kind: "error", msg: e.message ?? String(e) });
    }
  };

  const runSmokeTest = async () => {
    setStatus({ kind: "loading", msg: "Creating app + opening via QIX..." });
    try {
      const result = await api.createTestApp({
        name: appName,
        space_id: cfg.target_space_id || null,
        open_after_create: true,
      });
      setStatus({
        kind: "ok",
        msg: `Created app ${result.app_id}. QIX handshake OK.`,
        data: result,
      });
    } catch (e: any) {
      setStatus({ kind: "error", msg: e.message ?? String(e) });
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-8 space-y-8">
      <header>
        <h1 className="text-2xl font-semibold">QlikGen</h1>
        <p className="text-sm text-slate-600">
          Phase 0 — config &amp; smoke test.
        </p>
      </header>

      <section className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <h2 className="text-lg font-medium">Config</h2>

        <Field
          label="Qlik tenant URL"
          placeholder="https://qbc90wfggcojjmn.in.qlikcloud.com"
          value={cfg.tenant_url ?? ""}
          onChange={(v) => update("tenant_url", v)}
        />

        <Field
          label="Qlik OAuth client ID (from Native OAuth client)"
          value={cfg.qlik_client_id ?? ""}
          onChange={(v) => update("qlik_client_id", v)}
        />

        <Field
          label="Anthropic API key"
          type="password"
          placeholder={cfg.anthropic_api_key === "***" ? "(stored)" : ""}
          value={
            cfg.anthropic_api_key === "***" ? "" : cfg.anthropic_api_key ?? ""
          }
          onChange={(v) => update("anthropic_api_key", v)}
        />

        <Field
          label="Anthropic workspace ID (only for identity-linked keys)"
          placeholder="Leave blank unless you get an 'anthropic-workspace-id is required' error"
          value={cfg.anthropic_workspace_id ?? ""}
          onChange={(v) => update("anthropic_workspace_id", v)}
        />

        <Field
          label="Target space ID (optional)"
          placeholder="Leave blank for personal space"
          value={cfg.target_space_id ?? ""}
          onChange={(v) => update("target_space_id", v)}
        />

        <div className="flex flex-wrap gap-2 pt-2">
          <Btn onClick={save}>Save config</Btn>
        </div>
      </section>

      <section className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <h2 className="text-lg font-medium">Sign in</h2>
        <div
          className={`rounded border px-3 py-2 text-sm ${
            signedIn
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-slate-200 bg-slate-50 text-slate-700"
          }`}
        >
          {signedIn ? "Signed in to Qlik." : "Not signed in."}
        </div>
        <div className="flex flex-wrap gap-2">
          {!signedIn ? (
            <Btn onClick={signIn}>Sign in with Qlik</Btn>
          ) : (
            <>
              <Btn onClick={checkAuth} variant="ghost">
                Check Qlik auth
              </Btn>
              <Btn onClick={loadSpaces} variant="ghost">
                Load spaces
              </Btn>
              <Btn onClick={signOut} variant="ghost">
                Sign out
              </Btn>
            </>
          )}
        </div>

        {spaces.length > 0 && (
          <div className="pt-3">
            <label className="mb-1 block text-sm font-medium">
              Spaces in tenant
            </label>
            <select
              className="w-full rounded border px-3 py-2 text-sm"
              value={cfg.target_space_id ?? ""}
              onChange={(e) => update("target_space_id", e.target.value)}
            >
              <option value="">— personal space —</option>
              {spaces.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.type})
                </option>
              ))}
            </select>
          </div>
        )}
      </section>

      <section className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <h2 className="text-lg font-medium">Smoke test</h2>
        <p className="text-sm text-slate-600">
          Creates an empty app in the selected space, then opens it via the
          Engine API to verify the full REST + WebSocket chain.
        </p>
        <Field label="App name" value={appName} onChange={setAppName} />
        <Btn onClick={runSmokeTest}>Create test app</Btn>
      </section>

      {signedIn && (
        <CsvPanel
          spaceId={cfg.target_space_id ?? ""}
          tenantUrl={cfg.tenant_url ?? ""}
        />
      )}

      <StatusBar status={status} tenantUrl={cfg.tenant_url ?? ""} />
    </div>
  );
}

function Field(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium">{props.label}</label>
      <input
        type={props.type ?? "text"}
        className="w-full rounded border px-3 py-2 text-sm"
        value={props.value}
        placeholder={props.placeholder}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </div>
  );
}

function Btn(props: {
  onClick: () => void;
  children: React.ReactNode;
  variant?: "solid" | "ghost";
}) {
  const cls =
    props.variant === "ghost"
      ? "border border-slate-300 bg-white hover:bg-slate-100"
      : "bg-slate-900 text-white hover:bg-slate-700";
  return (
    <button
      onClick={props.onClick}
      className={`rounded px-4 py-2 text-sm font-medium ${cls}`}
    >
      {props.children}
    </button>
  );
}

function StatusBar({
  status,
  tenantUrl,
}: {
  status: Status;
  tenantUrl: string;
}) {
  if (status.kind === "idle") return null;
  const color =
    status.kind === "error"
      ? "bg-red-50 border-red-200 text-red-800"
      : status.kind === "ok"
      ? "bg-emerald-50 border-emerald-200 text-emerald-800"
      : "bg-slate-100 border-slate-200 text-slate-700";
  const appId = status.kind === "ok" ? status.data?.app_id : null;
  return (
    <div className={`rounded border p-4 text-sm ${color}`}>
      <div>{status.msg}</div>
      {appId && tenantUrl && (
        <div className="mt-2">
          <a
            className="underline"
            href={`${tenantUrl.replace(/\/$/, "")}/sense/app/${appId}`}
            target="_blank"
            rel="noreferrer"
          >
            Open app in Qlik →
          </a>
        </div>
      )}
    </div>
  );
}
