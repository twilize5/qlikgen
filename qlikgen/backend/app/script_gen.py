"""
Deterministic Qlik LOAD-script generation.

No LLM: the script is a pure function of the CsvProfile plus the resolved data
connection name and the uploaded file name. Because field names come straight
from the same profile the hypercubes use, script fields and chart references
stay in lockstep.
"""
from __future__ import annotations

from .models import CsvProfile

# Qlik logical type -> the interpretation wrapper we apply in the LOAD, if any.
# Numbers usually auto-interpret from CSV; dates are the fragile case, so we
# leave the raw field and let embedded-labels + Qlik's type sniffing handle it.
# (Explicit Date#/Timestamp# interpretation is deferred to a later phase where we
#  can profile the actual date format.)


def _q(field: str) -> str:
    """Quote a Qlik field/identifier, escaping embedded quotes."""
    return '"' + field.replace('"', '""') + '"'


def build_load_script(profile: CsvProfile, connection_name: str, filename: str) -> str:
    """
    Build a single-table LOAD script.

        LIB CONNECT TO 'DataFiles';

        [Sales]:
        LOAD
            "Region",
            "Date",
            "Revenue"
        FROM [lib://DataFiles/sales.csv]
        (txt, utf8, embedded labels, delimiter is ',', msq);

    `connection_name` is the qName of the space's DataFiles connection (usually
    "DataFiles"); `filename` is the name the CSV was stored under in that space.
    """
    field_lines = ",\n".join(f"    {_q(c.name)}" for c in profile.columns)
    lib_path = f"lib://{connection_name}/{filename}"

    return (
        f"LIB CONNECT TO '{connection_name}';\n\n"
        f"[{profile.table_name}]:\n"
        f"LOAD\n"
        f"{field_lines}\n"
        f"FROM [{lib_path}]\n"
        f"(txt, utf8, embedded labels, delimiter is ',', msq);\n"
    )
