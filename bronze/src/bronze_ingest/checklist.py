"""bronze_checklist_raw — required submission documents (Agent 2 reference data).

Contract (bronze.dbml v2, 12 Aug — NEW table):
  - Six columns, all text, plus source_file.
  - CHECKLIST TAB ONLY. The workbook also has "Manifest - SABIC",
    "Manifest - Petro Rabigh" and "IC confirmations" tabs — those are
    explicitly Agent 2's ingestion (submission status / intercompany
    anomaly A6), not this one, and they are skipped STRUCTURALLY rather
    than by name: their first header cell is "Entity", never "Item", so
    read_tables' header-sentinel search never matches them. Same mechanism
    that already skips "Read me" without a blacklist.

Expected against the current pack: 14 rows.
"""

from __future__ import annotations

from .excel import IngestError, read_tables, resolve_columns, to_raw_str

COLUMNS = ["item", "document", "required", "applies_to", "expected_format",
           "description", "source_file"]

ALIASES = {
    "item":            ["item"],
    "document":        ["document"],
    "required":        ["required"],
    "applies_to":      ["applies to"],
    "expected_format": ["expected format"],
    "description":     ["description"],
}
REQUIRED = set(ALIASES)


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read the Checklist tab and return (rows, per-tab metadata)."""
    rows: list[dict] = []
    meta: dict = {}
    source_file = getattr(path, "name", str(path))

    for title, tab in read_tables(path, cfg["header_sentinel"]):
        pos = resolve_columns(tab.headers, ALIASES, REQUIRED, title)

        landed = 0
        for row in tab.data:
            rec = dict.fromkeys(COLUMNS, "")
            rec["source_file"] = source_file
            for col, i in pos.items():
                rec[col] = to_raw_str(row[i] if i < len(row) else None)
            rows.append(rec)
            landed += 1

        report.append(f"{title}: {landed} rows landed")
        meta[title] = {"rows_landed": landed, "columns_found": sorted(pos)}

    if not rows:
        raise IngestError(f"{path}: no checklist tab found")
    return rows, meta
