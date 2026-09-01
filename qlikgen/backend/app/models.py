"""
Shared Pydantic models.

Phase 1 introduces the CSV pipeline, so the request/response shapes that used
to live inline in main.py move here alongside the new profile/template models.
Everything that crosses the Python<->Node (QIX sidecar) boundary is also modelled
here so the JSON contract is in one place.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ---------- Config / OAuth (migrated from main.py) ----------


class ConfigIn(BaseModel):
    tenant_url: Optional[str] = None
    qlik_client_id: Optional[str] = None
    target_space_id: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    # Required only for identity-linked Anthropic keys — sent as the
    # anthropic-workspace-id header on Claude requests.
    anthropic_workspace_id: Optional[str] = None


class CreateAppIn(BaseModel):
    name: str
    space_id: Optional[str] = None
    open_after_create: bool = True


# ---------- CSV profiling ----------

# Qlik-facing logical types we reduce every source column to.
QlikType = Literal["string", "integer", "double", "date", "timestamp"]
# How the template builder is allowed to use a column.
ColumnRole = Literal["dimension", "measure", "temporal"]


class ColumnProfile(BaseModel):
    name: str
    qlik_type: QlikType
    role: ColumnRole
    distinct: int = 0          # approx distinct count
    null_pct: float = 0.0      # 0..100
    min: Optional[str] = None  # stringified for JSON safety
    max: Optional[str] = None
    samples: list[str] = Field(default_factory=list)


class CsvProfile(BaseModel):
    table_name: str
    filename: str              # original upload filename (sanitised)
    row_count: int
    columns: list[ColumnProfile]

    # Convenience views used by script_gen / template_builder.
    def measures(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.role == "measure"]

    def dimensions(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.role == "dimension"]

    def temporals(self) -> list[ColumnProfile]:
        return [c for c in self.columns if c.role == "temporal"]


# ---------- Template spec (Python -> QIX sidecar) ----------


class MeasureSpec(BaseModel):
    label: str
    expr: str  # e.g. 'Sum([Revenue])'


class KpiSpec(BaseModel):
    label: str
    expr: str


class ChartSpec(BaseModel):
    dim: str
    measure_label: str
    measure_expr: str


class TableSpec(BaseModel):
    dims: list[str] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)


class TemplateSpec(BaseModel):
    """The hardcoded 1-sheet layout. Any slot may be None for a degenerate CSV.

    Retained from Phase 1. Phase 2 builds a multi-sheet DashboardPlan instead
    (below); this stays for reference and any legacy single-sheet callers.
    """
    title: str = "Overview"
    kpis: list[KpiSpec] = Field(default_factory=list)
    bar: Optional[ChartSpec] = None
    line: Optional[ChartSpec] = None
    table: Optional[TableSpec] = None


# ---------- Phase 2: multi-sheet dashboard plan (Python -> QIX sidecar) ----------

# The visual types the QIX builder (build_objects.js) knows how to construct.
VisualType = Literal[
    "kpi", "barchart", "linechart", "piechart", "table", "filterpane", "map"
]


class VisualSpec(BaseModel):
    """
    One visual on a sheet. A flat, generic shape so the same contract carries a
    KPI, a category chart, a pie, a table, or a filter pane. Only the fields
    relevant to `type` are populated; the QIX builder reads what it needs.

    This is also the exact shape the Phase 2b LLM "plan" produces (types + field
    mapping only) — the deterministic builder and the LLM emit the same struct,
    so there is one contract to validate and one builder to trust.
    """
    key: str                              # unique slot id within the sheet
    type: VisualType

    # category charts (bar / line) and pie
    dim: Optional[str] = None             # raw field name for the dimension
    dim_expr: Optional[str] = None        # computed dimension (e.g. binned date); overrides dim
    dim_label: Optional[str] = None       # display label when dim_expr is used
    measure_label: Optional[str] = None
    measure_expr: Optional[str] = None

    # table
    dims: list[str] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)

    # kpi
    label: Optional[str] = None
    expr: Optional[str] = None

    # filter pane
    fields: list[str] = Field(default_factory=list)

    # per-type toggles: {"area": true} for line, {"donut": true} for pie, etc.
    options: dict[str, Any] = Field(default_factory=dict)


class SheetSpec(BaseModel):
    title: str
    rank: int = 0
    # Coarse layout hint the deterministic engine honors (it still owns pixels):
    # where the filter pane sits. Chart emphasis rides in a visual's options
    # ({"emphasize": true}) and chart order is the order of `visuals`.
    filters_position: Literal["left", "top"] = "left"
    visuals: list[VisualSpec] = Field(default_factory=list)


class DashboardPlan(BaseModel):
    """The full generated app: an ordered list of sheets. Empty sheets are dropped
    by the builder; a degenerate CSV still yields at least one Overview sheet."""
    sheets: list[SheetSpec] = Field(default_factory=list)


# ---------- Pipeline request/response ----------


class GenerateIn(BaseModel):
    upload_id: str
    app_name: str
    space_id: Optional[str] = None


class ProfileResponse(BaseModel):
    upload_id: str
    profile: CsvProfile
    script_preview: str
    plan: DashboardPlan
    # How the plan was chosen: "deterministic" (rules) or "llm" (proposed +
    # validated). Phase 2a is always deterministic; 2b sets "llm" on success.
    plan_source: str = "deterministic"
    # When the LLM plan was NOT used, a short reason (empty otherwise).
    plan_note: str = ""


class ReloadResult(BaseModel):
    ok: bool
    reloaded: bool = False
    detail: str = ""


class GenerateResponse(BaseModel):
    app_id: str
    app_name: str
    space_id: Optional[str] = None
    file_ref: str                 # lib:// path used in the script
    reload: ReloadResult
    sheet_id: Optional[str] = None          # first sheet built (back-compat)
    sheet_ids: list[str] = Field(default_factory=list)
    plan_source: str = "deterministic"
    object_ids: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[Any] = Field(default_factory=list)
    open_url: Optional[str] = None
