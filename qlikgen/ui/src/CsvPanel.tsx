import { useState } from "react";
import {
  api,
  type GenerateResponse,
  type ProfileResponse,
} from "./lib/api";

type Props = {
  spaceId: string;
  tenantUrl: string;
};

type Phase =
  | { kind: "idle" }
  | { kind: "profiling" }
  | { kind: "profiled"; data: ProfileResponse }
  | { kind: "generating"; data: ProfileResponse }
  | { kind: "done"; data: ProfileResponse; result: GenerateResponse }
  | { kind: "error"; msg: string; data?: ProfileResponse };

const roleColor: Record<string, string> = {
  measure: "bg-blue-50 text-blue-700 border-blue-200",
  dimension: "bg-slate-50 text-slate-700 border-slate-200",
  temporal: "bg-amber-50 text-amber-700 border-amber-200",
};

export default function CsvPanel({ spaceId, tenantUrl }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [appName, setAppName] = useState("QlikGen CSV report");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [replanning, setReplanning] = useState(false);
  const [brief, setBrief] = useState("");

  const dataOf = (p: Phase): ProfileResponse | undefined =>
    "data" in p ? p.data : undefined;

  const profile = async () => {
    if (!file) return;
    setPhase({ kind: "profiling" });
    try {
      const data = await api.profileCsv(file, brief);
      const stem = file.name.replace(/\.[^.]+$/, "");
      if (stem) setAppName(`${stem} report`);
      setPhase({ kind: "profiled", data });
    } catch (e: any) {
      setPhase({ kind: "error", msg: e.message ?? String(e) });
    }
  };

  const generate = async () => {
    const data = dataOf(phase);
    if (!data) return;
    setPhase({ kind: "generating", data });
    try {
      const result = await api.generateFromCsv({
        upload_id: data.upload_id,
        app_name: appName,
        space_id: spaceId || null,
      });
      setPhase({ kind: "done", data, result });
    } catch (e: any) {
      setPhase({ kind: "error", msg: e.message ?? String(e), data });
    }
  };

  const replan = async () => {
    if (phase.kind !== "profiled") return;
    setReplanning(true);
    try {
      const res = await api.replan(phase.data.upload_id, brief);
      setPhase({
        kind: "profiled",
        data: {
          ...phase.data,
          plan: res.plan,
          plan_source: res.plan_source,
          plan_note: res.plan_note,
        },
      });
    } catch (e: any) {
      setPhase({ kind: "error", msg: e.message ?? String(e), data: phase.data });
    } finally {
      setReplanning(false);
    }
  };

  const data = dataOf(phase);
  const busy = phase.kind === "profiling" || phase.kind === "generating";

  return (
    <section className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
      <div>
        <h2 className="text-lg font-medium">CSV → dashboard</h2>
        <p className="text-sm text-slate-600">
          Upload a CSV. QlikGen profiles it, builds a LOAD script and a
          multi-sheet plan (Overview: KPIs + bar + area + pie + filters ·
          Details: full table), then creates the app in Qlik.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setPhase({ kind: "idle" });
          }}
          className="text-sm"
        />
        <button
          onClick={profile}
          disabled={!file || busy}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
        >
          {phase.kind === "profiling" ? "Profiling…" : "Profile CSV"}
        </button>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">
          What do you want? <span className="font-normal text-slate-400">(optional)</span>
        </label>
        <textarea
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          rows={3}
          placeholder="e.g. Two sheets. Overview with sales trend emphasized as an area chart, a donut of sales by category, and KPIs. Filters on top. A second sheet with a table of top products."
          className="w-full rounded border px-3 py-2 text-sm"
        />
        <p className="mt-1 text-xs text-slate-500">
          Steers the LLM planner — which charts, how many sheets, and layout
          emphasis. Leave blank for an automatic plan. Change it and use
          “Regenerate plan” to re-roll.
        </p>
      </div>

      {data && (
        <>
          <div className="text-sm text-slate-700">
            <span className="font-medium">{data.profile.table_name}</span> ·{" "}
            {data.profile.row_count.toLocaleString()} rows ·{" "}
            {data.profile.columns.length} columns
          </div>

          <div className="overflow-x-auto rounded border">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-3 py-2 font-medium">Column</th>
                  <th className="px-3 py-2 font-medium">Type</th>
                  <th className="px-3 py-2 font-medium">Role</th>
                  <th className="px-3 py-2 font-medium">Distinct</th>
                  <th className="px-3 py-2 font-medium">Samples</th>
                </tr>
              </thead>
              <tbody>
                {data.profile.columns.map((c) => (
                  <tr key={c.name} className="border-t">
                    <td className="px-3 py-1.5 font-mono text-xs">{c.name}</td>
                    <td className="px-3 py-1.5 text-slate-600">{c.qlik_type}</td>
                    <td className="px-3 py-1.5">
                      <span
                        className={`rounded border px-2 py-0.5 text-xs ${
                          roleColor[c.role] ?? ""
                        }`}
                      >
                        {c.role}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-slate-600">{c.distinct}</td>
                    <td className="px-3 py-1.5 text-slate-500">
                      {c.samples.join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">
              LOAD script preview
            </label>
            <pre className="max-h-56 overflow-auto rounded border bg-slate-900 p-3 text-xs text-slate-100">
              {data.script_preview}
            </pre>
          </div>

          <PlanSummary
            plan={data.plan}
            source={data.plan_source}
            note={data.plan_note}
            onReplan={phase.kind === "profiled" ? replan : undefined}
            replanning={replanning}
          />

          <div className="flex flex-wrap items-end gap-3 pt-1">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium">App name</label>
              <input
                value={appName}
                onChange={(e) => setAppName(e.target.value)}
                className="w-full rounded border px-3 py-2 text-sm"
              />
            </div>
            <button
              onClick={generate}
              disabled={busy}
              className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
            >
              {phase.kind === "generating"
                ? "Generating…"
                : "Generate dashboard"}
            </button>
          </div>
        </>
      )}

      {phase.kind === "error" && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {phase.msg}
        </div>
      )}

      {phase.kind === "done" && (
        <ResultBox result={phase.result} tenantUrl={tenantUrl} />
      )}
    </section>
  );
}

function PlanSummary({
  plan,
  source,
  note,
  onReplan,
  replanning,
}: {
  plan: ProfileResponse["plan"];
  source: string;
  note?: string;
  onReplan?: () => void;
  replanning?: boolean;
}) {
  const describe = (v: ProfileResponse["plan"]["sheets"][number]["visuals"][number]): string => {
    const by = v.dim_label || v.dim;
    switch (v.type) {
      case "kpi":
        return v.label || "KPI";
      case "barchart":
        return `bar: ${v.measure_label} by ${by}`;
      case "linechart":
        return `${v.options?.area ? "area" : "line"}: ${v.measure_label} by ${by}`;
      case "piechart":
        return `${v.options?.donut ? "donut" : "pie"}: ${v.measure_label} by ${by}`;
      case "table":
        return `table: ${(v.dims?.length ?? 0) + (v.measures?.length ?? 0)} cols`;
      case "map":
        return `map: ${v.measure_label} by ${v.dim}`;
      case "filterpane":
        return `filters: ${(v.fields ?? []).join(", ")}`;
      default:
        return v.type;
    }
  };

  return (
    <div className="space-y-2 text-sm text-slate-600">
      <div className="flex items-center gap-2">
        <span className="font-medium">Plan</span>
        <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-xs text-slate-500">
          {source === "llm" ? "LLM-proposed" : "deterministic"}
        </span>
        {onReplan && (
          <button
            onClick={onReplan}
            disabled={replanning}
            className="ml-auto rounded border border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"
            title="Clear the cached plan and propose a fresh one"
          >
            {replanning ? "Regenerating…" : "Regenerate plan"}
          </button>
        )}
      </div>
      {source !== "llm" && note && (
        <div className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span className="font-medium">Your brief wasn’t applied</span> — used
          the automatic plan instead. {note}
        </div>
      )}
      {plan.sheets.map((s) => (
        <div key={s.title} className="rounded border bg-slate-50/60 px-3 py-2">
          <div className="font-medium text-slate-700">{s.title}</div>
          <div className="text-xs text-slate-500">
            {s.visuals.map(describe).join(" · ")}
          </div>
        </div>
      ))}
    </div>
  );
}

function ResultBox({
  result,
  tenantUrl,
}: {
  result: GenerateResponse;
  tenantUrl: string;
}) {
  const ok = result.reload.reloaded;
  return (
    <div
      className={`rounded border p-4 text-sm ${
        ok
          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
          : "border-amber-200 bg-amber-50 text-amber-800"
      }`}
    >
      <div>
        App <span className="font-mono">{result.app_id}</span> created.{" "}
        {ok
          ? `Reload OK — ${result.sheet_ids?.length || 1} sheet${
              (result.sheet_ids?.length || 1) > 1 ? "s" : ""
            } built.`
          : `Reload did not complete: ${result.reload.detail || "unknown error"}. Sheets not built.`}
      </div>
      <div className="mt-1 text-xs opacity-80">Data file: {result.file_ref}</div>
      {(result.open_url || tenantUrl) && (
        <div className="mt-2">
          <a
            className="underline"
            href={
              result.open_url ||
              `${tenantUrl.replace(/\/$/, "")}/sense/app/${result.app_id}`
            }
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
