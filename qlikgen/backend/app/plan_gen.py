"""
Phase 2b: LLM-proposed dashboard plan ("propose, don't emit").

One Claude call turns a CsvProfile into a DashboardPlan (chart types + field
mapping only — never QIX properties, never layout). The proposal is parsed into
the same Pydantic model the deterministic builder uses, then validated against
the profile. If anything fails — no API key, API error, bad JSON, schema
mismatch, or a semantic breach — we fall back to the deterministic build_plan.

The model chooses *what* to show; the deterministic builder still emits every
property and positions every visual, so a bad proposal can never build a broken
or blank report.
"""
from __future__ import annotations

import json
import logging

from . import geo, secrets
from .models import CsvProfile, DashboardPlan
from .plan_validator import validate_plan
from .template_builder import build_plan

log = logging.getLogger("qlikgen")

# The documented default. A dashboard plan is a small, correctness-sensitive
# structured output; opus handles the field/measure reasoning well.
MODEL = "claude-opus-5"

SYSTEM = """
You design a Qlik Sense dashboard PLAN from a CSV column profile. You choose
WHICH charts to show and WHICH fields they use — nothing else. You never write
Qlik object properties and you never position anything; a deterministic builder
does that from your plan.

Allowed visual types ONLY: kpi, barchart, linechart, piechart, table,
filterpane, map.

Return ONLY valid JSON (no markdown, no prose) with this exact shape:

{
  "sheets": [
    {
      "title": "Overview",
      "rank": 0,
      "visuals": [
        {"key": "kpi0", "type": "kpi", "label": "Sum of Sales", "expr": "Sum([Sales])"},
        {"key": "bar", "type": "barchart", "dim": "Segment",
         "measure_label": "Sales", "measure_expr": "Sum([Sales])"},
        {"key": "line", "type": "linechart", "dim_expr": "=Date(MonthStart([Order Date]), 'MMM-YYYY')",
         "dim_label": "Order Date", "measure_label": "Sales", "measure_expr": "Sum([Sales])",
         "options": {"area": true}},
        {"key": "pie", "type": "piechart", "dim": "Region",
         "measure_label": "Sales", "measure_expr": "Sum([Sales])", "options": {"donut": true}},
        {"key": "map", "type": "map", "dim": "State",
         "measure_label": "Sales", "measure_expr": "Sum([Sales])"},
        {"key": "filt", "type": "filterpane", "fields": ["Segment", "Region", "Category"]}
      ]
    }
  ]
}

Rules:
- Allowed types ONLY: kpi, barchart, linechart, piechart, table, filterpane, map.
- map: a choropleth of a measure over a GEOGRAPHIC AREA field. Use it ONLY when
  a column is marked "geo": "area" in the profile (e.g. Country, State, County,
  City, Postal Code). Set "dim" to that field and give it a measure. Never put a
  non-geo field on a map. If the user asks for a map but no area field exists,
  skip the map rather than forcing one.
- Every field name and every [Field] inside an expression MUST be a column that
  exists in the profile, spelled exactly (case-sensitive). Do not invent fields.
- Measures are aggregations: Sum([num]), Avg([num]), Count(1), etc. Reference a
  measure column, never a dimension, inside the aggregation.
- KPIs: 3-4 of the most important numeric measures, as Sum(...).
- barchart: a categorical dimension with a small number of values (a few to a
  few dozen) x a measure. Avoid id/key columns.
- linechart: a DATE/TIMESTAMP column, binned to month with a computed dimension
  "=Date(MonthStart([the date field]), 'MMM-YYYY')" (set dim_label to the field
  name), x a measure, options {"area": true}. Only if a temporal column exists.
- piechart: a categorical dimension with 2-12 values x a measure. options
  {"donut": true} for a donut. Pick a different dimension than the bar if you can.
- filterpane: 2-4 useful categorical fields (a temporal field is fine too).
- Put the visual, summary charts on an "Overview" sheet. Put a wide "table"
  (all dimensions + all measures) on a second "Details" sheet with rank 1.
- Give every visual a short unique "key" within its sheet.
- Prefer a compact, readable dashboard over cramming in every possible chart.

Coarse layout hints you MAY set (a deterministic engine still positions and
sizes everything — you do NOT set pixels, x/y, or width/height):
- Chart order within a sheet is the order you list them.
- "emphasize": true in a visual's options makes it the large, full-width feature
  of its sheet (use for at most one chart per sheet).
- "filters_position": "left" | "top" on a sheet places the filter pane.

If a USER BRIEF is provided, follow it: honor the visuals and chart types they
ask for, the number and naming of sheets they want, and their layout emphasis
(e.g. "make the trend big", "filters on top", "one sheet only", "add a table of
top products"). Stay within the allowed types and the fields that exist; if the
brief asks for something impossible with this data, get as close as you can.
"""


def _profile_for_prompt(profile: CsvProfile) -> str:
    cols = []
    for c in profile.columns:
        col = {"name": c.name, "role": c.role, "type": c.qlik_type, "distinct": c.distinct}
        g = geo.geo_kind(c.name)
        if g:
            col["geo"] = g  # "area" (mappable) or "point"
        cols.append(col)
    return json.dumps(
        {"table": profile.table_name, "row_count": profile.row_count, "columns": cols},
        indent=2,
    )


def _extract_text(resp) -> str:
    """Concatenate the text blocks of a Messages response (skips thinking blocks)."""
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def _strip_fences(raw: str) -> str:
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def propose_plan(profile: CsvProfile, brief: str = "") -> DashboardPlan:
    """Call Claude for a plan. Raises on missing key, API error, or bad output.
    `brief` is optional free-text from the user steering visuals/sheets/layout."""
    import anthropic  # local import so the app runs without the SDK if LLM unused

    api_key = secrets.get("anthropic_api_key")
    if not api_key:
        raise RuntimeError("no anthropic_api_key configured")

    brief_block = (
        f"USER BRIEF (follow this):\n{brief.strip()}\n\n" if brief and brief.strip() else ""
    )

    # Identity-linked keys require the workspace id as a header on every request.
    workspace_id = secrets.get("anthropic_workspace_id")
    default_headers = (
        {"anthropic-workspace-id": workspace_id} if workspace_id else None
    )

    client = anthropic.Anthropic(api_key=api_key, default_headers=default_headers)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{
            "role": "user",
            "content": f"CSV column profile:\n{_profile_for_prompt(profile)}\n\n"
                       f"{brief_block}"
                       f"Return the dashboard plan JSON.",
        }],
    )
    raw = _strip_fences(_extract_text(resp))
    data = json.loads(raw)          # may raise JSONDecodeError
    return DashboardPlan(**data)    # may raise pydantic ValidationError


def _reason(e: Exception) -> str:
    """A short, user-facing explanation of why the LLM plan wasn't used."""
    msg = str(e).strip() or e.__class__.__name__
    low = msg.lower()
    if "no anthropic_api_key" in msg:
        return "No Anthropic API key configured — add it in Config."
    # Identity-linked key that needs a workspace id.
    if "anthropic-workspace-id" in low or "identity-linked" in low:
        return ("Your Anthropic key is identity-linked. Easiest fix: create a "
                "standard API key at console.anthropic.com (Settings > API keys) "
                "and paste it in Config. Or paste your workspace ID (Settings > "
                "Workspaces > Default) into the 'Anthropic workspace ID' field.")
    # Unsupported visual type: pydantic Literal rejection, or the validator's own.
    if "literal_error" in low or "unsupported visual type" in low or (
        "input should be" in low and "kpi" in low
    ):
        return ("The plan asked for an unsupported visual type. Supported: KPI, "
                "bar, line, pie/donut, table, filter pane, map.")
    # A map on a non-geographic column.
    if "not a geographic area" in low:
        return ("A map was requested but the chosen column isn't geographic. Maps "
                "need a country/state/county/city/postal field.")
    # Model access / auth problems from the API.
    if "not_found" in low or "model" in low and "does not exist" in low:
        return f"Anthropic API rejected the model — {msg[:200]}"
    return msg[:300]


def plan_for_profile(profile: CsvProfile, brief: str = "") -> tuple[DashboardPlan, str, str]:
    """
    The single entry point: try the LLM (with the optional user brief), validate
    it, fall back to deterministic. Returns (plan, source, note) — source is
    "llm" or "deterministic"; note is "" on success or a short reason when the
    LLM plan was not used. The brief only steers the LLM path; the deterministic
    fallback ignores it (it has no free-text understanding).
    """
    try:
        plan = propose_plan(profile, brief)
        validate_plan(plan, profile)
        log.info("using LLM-proposed plan (%d sheets)", len(plan.sheets))
        return plan, "llm", ""
    except Exception as e:
        note = _reason(e)
        log.warning("LLM plan not used (%s); falling back to deterministic", e)
        return build_plan(profile), "deterministic", note
