"""
Deterministic dashboard builder.

CsvProfile -> DashboardPlan. Phase 2 replaces the single hardcoded sheet with a
two-sheet plan, still chosen by pure rules (no LLM):

  Overview sheet
    - up to 4 KPIs (Sum of the first numeric measures)
    - a left-rail filter pane (top categorical dimensions)
    - 1 bar   (best categorical dimension x primary measure), full width
    - 1 line/area (first temporal, binned to month x primary measure)
    - 1 pie/donut (a readable categorical x primary measure) — replaces the
      old Details table on the Overview

  Details sheet
    - a left-rail filter pane
    - 1 table (all dimensions/temporals + measures) — the full detail, moved off
      the Overview so it stays visual

Pure Python, correct-by-construction: every expression references a field that
exists in the profile. This function is also the deterministic fallback for the
Phase 2b LLM path — when a proposed plan is rejected, this plan is used instead.
"""
from __future__ import annotations

from .models import (
    ColumnProfile,
    CsvProfile,
    DashboardPlan,
    MeasureSpec,
    SheetSpec,
    VisualSpec,
)

MAX_KPIS = 4
MAX_FILTER_FIELDS = 4
# A pie is only readable with a handful of slices; outside this band it is noise.
PIE_MIN_DISTINCT = 2
PIE_MAX_DISTINCT = 12
# Fields worth offering as filters: categorical, not near-unique keys.
FILTER_MAX_DISTINCT = 1000


def _bracket(field: str) -> str:
    """Field reference inside an expression: [Field] handles spaces safely."""
    return "[" + field.replace("]", "]]") + "]"


def _sum(field: str) -> str:
    return f"Sum({_bracket(field)})"


def _bin_month(field: str) -> str:
    """
    Computed dimension that bins a date/timestamp field to its month, as a Qlik
    dual so it still sorts chronologically. Turns ~thousands of raw dates into
    ~dozens of points, so a trend reads as a trend, not a spike field.
    """
    return f"=Date(MonthStart({_bracket(field)}), 'MMM-YYYY')"


def _good_categoricals(dims: list[ColumnProfile]) -> list[ColumnProfile]:
    """Dimensions with >= 2 distinct values, smallest first (fewest bars)."""
    return sorted((c for c in dims if c.distinct >= 2), key=lambda c: c.distinct)


def build_plan(profile: CsvProfile) -> DashboardPlan:
    measures = profile.measures()
    dims = profile.dimensions()
    temporals = profile.temporals()
    good = _good_categoricals(dims)

    # ---- shared measure the charts reuse -------------------------------------
    if measures:
        primary = MeasureSpec(label=measures[0].name, expr=_sum(measures[0].name))
    else:
        primary = MeasureSpec(label="Count", expr="Count(1)")

    # ---- KPIs ----------------------------------------------------------------
    kpis: list[VisualSpec] = []
    if measures:
        for i, m in enumerate(measures[:MAX_KPIS]):
            kpis.append(
                VisualSpec(key=f"kpi{i}", type="kpi",
                           label=f"Sum of {m.name}", expr=_sum(m.name))
            )
    else:
        kpis.append(VisualSpec(key="kpi0", type="kpi", label="Row count", expr="Count(1)"))

    # ---- filter fields (top categoricals, then a temporal) -------------------
    filter_fields = [c.name for c in good if c.distinct <= FILTER_MAX_DISTINCT][:MAX_FILTER_FIELDS]
    if temporals and len(filter_fields) < MAX_FILTER_FIELDS:
        filter_fields.append(temporals[0].name)

    def filter_pane(key: str) -> VisualSpec | None:
        if not filter_fields:
            return None
        return VisualSpec(key=key, type="filterpane", fields=list(filter_fields))

    # ---- bar: best categorical (fewest bars), full width ---------------------
    bar_dim = None
    if good:
        bar_dim = good[0].name
    elif dims:
        bar_dim = dims[0].name
    elif temporals:
        bar_dim = temporals[0].name
    bar = None
    if bar_dim is not None:
        bar = VisualSpec(key="bar", type="barchart", dim=bar_dim,
                         measure_label=primary.label, measure_expr=primary.expr)

    # ---- line/area: first temporal, binned to month --------------------------
    line = None
    if temporals:
        t = temporals[0].name
        line = VisualSpec(key="line", type="linechart",
                          dim_expr=_bin_month(t), dim_label=t,
                          measure_label=primary.label, measure_expr=primary.expr,
                          options={"area": True})
    elif len(dims) > 1:
        line = VisualSpec(key="line", type="linechart", dim=dims[1].name,
                          measure_label=primary.label, measure_expr=primary.expr,
                          options={"area": True})

    # ---- pie/donut: a readable categorical, ideally not the bar's dim --------
    pie = None
    pie_candidates = [c for c in good if PIE_MIN_DISTINCT <= c.distinct <= PIE_MAX_DISTINCT]
    pie_dim = next((c.name for c in pie_candidates if c.name != bar_dim), None)
    if pie_dim is None and pie_candidates:
        pie_dim = pie_candidates[0].name
    if pie_dim is not None:
        pie = VisualSpec(key="pie", type="piechart", dim=pie_dim,
                         measure_label=primary.label, measure_expr=primary.expr,
                         options={"donut": True})

    # ---- Overview sheet ------------------------------------------------------
    overview_visuals: list[VisualSpec] = [*kpis]
    fp = filter_pane("of")
    if fp:
        overview_visuals.append(fp)
    for v in (bar, line, pie):
        if v is not None:
            overview_visuals.append(v)
    overview = SheetSpec(title="Overview", rank=0, visuals=overview_visuals)

    sheets = [overview]

    # ---- Details sheet: filter pane + the full table -------------------------
    table_dims = [c.name for c in profile.columns if c.role in ("dimension", "temporal")]
    table_measures = [MeasureSpec(label=m.name, expr=_sum(m.name)) for m in measures]
    if table_dims or table_measures:
        details_visuals: list[VisualSpec] = []
        fp2 = filter_pane("df")
        if fp2:
            details_visuals.append(fp2)
        details_visuals.append(
            VisualSpec(key="table", type="table", dims=table_dims, measures=table_measures)
        )
        sheets.append(SheetSpec(title="Details", rank=1, visuals=details_visuals))

    return DashboardPlan(sheets=sheets)
