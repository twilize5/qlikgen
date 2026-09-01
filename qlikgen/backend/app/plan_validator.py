"""
Validate a proposed DashboardPlan against the CSV profile.

This is the guard between the Phase 2b LLM and the deterministic QIX builder: the
model only ever *proposes* a plan (types + field mapping), and nothing it emits
reaches the Engine until it passes here. Any breach raises ValueError; the caller
(plan_gen.plan_for_profile) turns that into a fallback to the deterministic plan.

Every rule is a "would this build something wrong or blank?" check:
  - unknown chart type            -> the builder has no renderer for it
  - a field that isn't in the model -> the hypercube errors / renders blank
  - a pie with too many slices    -> noise, not a chart
  - an empty plan                 -> nothing to show
"""
from __future__ import annotations

import re

from . import geo
from .models import CsvProfile, DashboardPlan, VisualSpec

ALLOWED_TYPES = {
    "kpi", "barchart", "linechart", "piechart", "table", "filterpane", "map"
}
# A pie stays readable up to a couple dozen slices; past that it is meaningless.
PIE_MAX_DISTINCT = 25

_FIELD_TOKEN = re.compile(r"\[(.*?)\]")


def _referenced_fields(expr: str) -> set[str]:
    """Field names referenced as [Field] inside an expression (unescaping ]])."""
    if not expr:
        return set()
    return {m.replace("]]", "]").strip() for m in _FIELD_TOKEN.findall(expr)}


class PlanRejected(ValueError):
    """A proposed plan failed validation and must not be built."""


def validate_plan(plan: DashboardPlan, profile: CsvProfile) -> None:
    field_names = {c.name for c in profile.columns}
    distinct_by = {c.name: c.distinct for c in profile.columns}

    def check_fields(expr: str | None, where: str) -> None:
        for f in _referenced_fields(expr or ""):
            if f not in field_names:
                raise PlanRejected(f"{where}: references unknown field [{f}]")

    if not plan.sheets:
        raise PlanRejected("plan has no sheets")
    total_visuals = sum(len(s.visuals) for s in plan.sheets)
    if total_visuals == 0:
        raise PlanRejected("plan has no visuals")

    for si, sheet in enumerate(plan.sheets):
        if not sheet.title:
            raise PlanRejected(f"sheet[{si}] has no title")
        keys: set[str] = set()
        for v in sheet.visuals:
            where = f"sheet '{sheet.title}' / {v.key or v.type}"
            if v.type not in ALLOWED_TYPES:
                raise PlanRejected(f"{where}: unsupported visual type '{v.type}'")
            if v.key in keys:
                raise PlanRejected(f"{where}: duplicate key '{v.key}'")
            keys.add(v.key)
            _validate_visual(v, where, field_names, distinct_by, check_fields)


def _validate_dim(v: VisualSpec, where: str, field_names: set[str], check_fields) -> None:
    """A category/pie chart needs exactly one usable dimension: a real field, or a
    computed expression that only references real fields."""
    if v.dim_expr:
        if not v.dim_expr.lstrip().startswith("="):
            raise PlanRejected(f"{where}: dim_expr must be a Qlik expression starting with '='")
        check_fields(v.dim_expr, f"{where} dim_expr")
    elif v.dim:
        if v.dim not in field_names:
            raise PlanRejected(f"{where}: dimension [{v.dim}] is not a field")
    else:
        raise PlanRejected(f"{where}: chart needs a dim or dim_expr")


def _validate_measure(v: VisualSpec, where: str, check_fields) -> None:
    if not v.measure_expr:
        raise PlanRejected(f"{where}: chart needs a measure_expr")
    check_fields(v.measure_expr, f"{where} measure")


def _validate_visual(v, where, field_names, distinct_by, check_fields) -> None:
    if v.type == "kpi":
        if not v.expr:
            raise PlanRejected(f"{where}: kpi needs an expr")
        check_fields(v.expr, f"{where} kpi")

    elif v.type in ("barchart", "linechart"):
        _validate_dim(v, where, field_names, check_fields)
        _validate_measure(v, where, check_fields)

    elif v.type == "piechart":
        _validate_dim(v, where, field_names, check_fields)
        _validate_measure(v, where, check_fields)
        # Guard slice count only when the dimension is a plain field we profiled.
        if v.dim and not v.dim_expr:
            d = distinct_by.get(v.dim, 0)
            if d > PIE_MAX_DISTINCT:
                raise PlanRejected(
                    f"{where}: pie dimension [{v.dim}] has {d} values (> {PIE_MAX_DISTINCT})"
                )

    elif v.type == "table":
        if not v.dims and not v.measures:
            raise PlanRejected(f"{where}: table needs at least one column")
        for d in v.dims:
            if d not in field_names:
                raise PlanRejected(f"{where}: table dimension [{d}] is not a field")
        for m in v.measures:
            if not m.expr:
                raise PlanRejected(f"{where}: table measure '{m.label}' has no expr")
            check_fields(m.expr, f"{where} table measure")

    elif v.type == "map":
        # A choropleth needs a named-area geo dimension and a measure.
        if not v.dim:
            raise PlanRejected(f"{where}: map needs a geographic dim field")
        if v.dim not in field_names:
            raise PlanRejected(f"{where}: map dimension [{v.dim}] is not a field")
        if not geo.is_area_field(v.dim):
            raise PlanRejected(
                f"{where}: map dimension [{v.dim}] is not a geographic area "
                f"(needs country/state/county/city/postal)"
            )
        _validate_measure(v, where, check_fields)

    elif v.type == "filterpane":
        if not v.fields:
            raise PlanRejected(f"{where}: filter pane has no fields")
        for f in v.fields:
            if f not in field_names:
                raise PlanRejected(f"{where}: filter field [{f}] is not a field")
