"""bronze_ifrs_rubric_raw — IFRS disclosure requirements (Agent 4 reference data).

*** RE-SOURCED from ifrs_requirements_updated.csv (was: the xlsx rubric tab) ***

Verified before switching: the CSV carries the same 15 requirements already
loaded from the workbook — identical (standard, req) keys and byte-identical
requirement text, 0 differences. So this is not new data, it is the same data
with three extra columns:

    standard_code   short stable code ('IFRS 15') where only the long name
                    existed before. The FK target for bronze_ifrs_standard_raw.
    evidence_type   narrative | table_structure | both
    check_guidance  what actually counts as meeting the requirement

The last two are what turn this from a list of requirement TEXT into something
an agent can act on mechanically: it can branch on whether to inspect prose or
table shape, and has an explicit rule for 'met'.

The xlsx path was deleted rather than kept alongside. Two tables holding the
same fifteen requirements, one strictly richer, is the same trap already
present twice in this warehouse (dim_affiliate vs dim_entity,
bronze_trial_balance_raw vs bronze_tb_raw) — a text-to-SQL agent cannot tell
which copy is authoritative and will eventually pick the wrong one.

*** WHY ALIASES MAPS TWO COLUMN NAMES ***
The CSV says `standard_name` and `req_id`; this table has always called them
`standard` and `req`, and is live in BigQuery under those names. Mapping at
the boundary keeps the warehouse schema stable while the source's naming
varies — the same reason `category` already accepts two spellings. BigQuery
can add columns but cannot rename them, so preserving the existing names is
also what lets this re-source happen additively, with no table drop.

Columns D/E/F of the original workbook ('Compliant version', 'Gap version',
'Gap detail') remain deliberately excluded — they are the expected answers for
two demo scenarios, not reference data. They are simply not in ALIASES.

Expected against the current pack: 15 rows (3 standards x 5 requirements).
"""

from __future__ import annotations

from .flatcsv import read_rows

COLUMNS = ["standard", "req", "requirement", "standard_code",
           "evidence_type", "check_guidance", "source_file"]

ALIASES = {
    "standard":       ["standard_name", "standard"],
    "req":            ["req_id", "req"],
    "requirement":    ["requirement"],
    "standard_code":  ["standard_code"],
    "evidence_type":  ["evidence_type"],
    "check_guidance": ["check_guidance"],
}
REQUIRED = {"standard", "req", "requirement", "standard_code"}

# The rubric is machine-actionable only if this stays a closed set — an
# unexpected value means the agent has no rule for how to check it.
EVIDENCE_TYPES = {"narrative", "table_structure", "both"}


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read the requirements CSV and return (rows, metadata)."""
    from .excel import IngestError

    source_file = getattr(path, "name", str(path))
    raw, _ = read_rows(path, ALIASES, REQUIRED,
                       cfg.get("csv_read_encoding", "utf-8-sig"))

    rows = []
    for rec in raw:
        rows.append({**{c: "" for c in COLUMNS}, **rec,
                     "source_file": source_file})

    keys = [(r["standard_code"], r["req"]) for r in rows]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise IngestError(
            f"{source_file}: duplicate (standard_code, req) {dupes} — "
            f"refusing to land, the key would not be a key.")

    bad = sorted({r["evidence_type"] for r in rows
                  if r["evidence_type"] and r["evidence_type"] not in EVIDENCE_TYPES})
    if bad:
        raise IngestError(
            f"{source_file}: unexpected evidence_type value(s) {bad} — "
            f"expected one of {sorted(EVIDENCE_TYPES)}. A value the agent has "
            f"no rule for is worse than a missing one.")

    per_standard: dict[str, int] = {}
    for r in rows:
        per_standard[r["standard_code"]] = per_standard.get(r["standard_code"], 0) + 1

    report.append(f"{source_file}: {len(rows)} requirements landed across "
                  f"{len(per_standard)} standards "
                  f"({', '.join(f'{k}={v}' for k, v in sorted(per_standard.items()))})")
    meta = {source_file: {"rows_landed": len(rows),
                          "requirements_per_standard": per_standard}}
    return rows, meta
