"""bronze_coa_raw — the charts of accounts, stacked into one CSV.

Contract (DBML 2026-08-05, amended 2026-08-06):
  - Eight columns, all text. No casting, no cleaning.
  - All three chart tabs stacked; no relationships enforced.
  - chart_scope is ADDED at ingest from the tab name — it is the only place
    that information exists, and it disappears once the tabs are stacked.
  - Section-divider rows are DROPPED (see drop_row).

Expected against the current pack: 78 + 66 + 44 = 188 rows.
"""

from __future__ import annotations

import re

from .excel import (IngestError, Table, read_tables, resolve_columns,
                    to_raw_str)
from .sink import to_csv_text as _to_csv_text, write_csv as _write_csv

# The bronze schema. The CSV header is generated from this list, so the file
# cannot drift from the spec without this line changing.
# source_file is ADDED AT INGEST (path.name) for row-level lineage — not a
# column in the sheet, same pattern as chart_scope.
COLUMNS = ["chart_scope", "account", "account_name", "statement",
           "category", "normal_balance", "level", "source_reference",
           "source_file"]

ALIASES = {
    "account":          ["account"],
    "account_name":     ["account name"],
    "statement":        ["statement"],
    "category":         ["category (fs caption group)", "category"],
    "normal_balance":   ["normal balance"],
    "level":            ["level"],
    "source_reference": ["source / reference", "source/reference",
                         "source reference"],
}

# 'level' and 'source_reference' are Group-only, so they are absent here and
# land empty for the affiliates — exactly what the DBML says.
REQUIRED = {"account", "account_name", "statement", "category", "normal_balance"}


def chart_scope(title: str, affiliate_pattern: str, group_marker: str) -> str:
    """Derive chart_scope from the tab name.

    Derived, not enumerated: a fourth chart next quarter needs no code change.
    A data tab we cannot attribute RAISES — landing 40 accounts with a blank
    chart_scope is far worse than a failed run, because the collision between
    the two affiliate code sets makes an unattributed row unjoinable.
    """
    m = re.search(affiliate_pattern, title)
    if m:
        return m.group(1)
    if group_marker in title.lower():
        return "GROUP"
    raise IngestError(
        f"tab '{title}' looks like a chart tab but carries no affiliate code "
        f"'(NNNN)' and is not the Group chart")


def drop_row(row: tuple, enabled: bool) -> bool:
    """The single filtering decision this pipeline makes.

    Section dividers ("NON-CURRENT ASSETS") are dropped. Two findings justify
    it, both verified against the pack:
      (a) They carry no information — all 16 distinct captions already appear
          verbatim in the `category` column of the accounts beneath them.
      (b) They are unusable downstream — a divider means something only by row
          position, and BigQuery tables are unordered sets with no ordinal to
          reconstruct it from. They would land inert.

    This deviates from the "no filtering" line in the agreed DBML. It is a
    contract change, flagged to Hussein, not a silent one.
    """
    return enabled and Table.classify(row) == "section"


def check_control_total(tab: Table, report: list[str]) -> dict:
    """Reconcile against the tab's own "Total accounts: N" footer.

    This validates OUR PARSE, not the data. If the header row were found in
    the wrong place, or the body ended early, this count moves. It is a free
    integrity test on exactly the logic most likely to break silently.
    """
    counted = tab.counts()["account"]
    m = re.search(r"total accounts:\s*(\d+)", tab.footer_text().lower())
    if not m:
        report.append(f"[warn] {tab.title}: no 'Total accounts' footer — "
                      f"control total not checked")
        return {"declared": None, "counted": counted, "status": "ABSENT"}
    declared = int(m.group(1))
    if declared != counted:
        raise IngestError(
            f"{tab.title}: control total mismatch (sheet says {declared}, "
            f"parsed {counted}) — refusing to land.")
    report.append(f"control total {tab.title}: {declared} -> OK")
    return {"declared": declared, "counted": counted, "status": "OK"}


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read every chart tab and return (rows, per-tab metadata)."""
    rows: list[dict] = []
    meta: dict = {}
    # ADDED AT INGEST, same reasoning as chart_scope: the workbook filename
    # is not a column in the sheet, and it disappears the moment the tab is
    # stacked into bronze — so it has to be captured here or not at all.
    source_file = getattr(path, "name", str(path))

    for title, tab in read_tables(path, cfg["header_sentinel"]):
        scope = chart_scope(title, cfg["affiliate_pattern"], cfg["group_marker"])
        pos = resolve_columns(tab.headers, ALIASES, REQUIRED, title)

        landed = 0
        for row in tab.data:
            if drop_row(row, cfg["drop_section_rows"]):
                continue
            rec = dict.fromkeys(COLUMNS, "")
            rec["chart_scope"] = scope
            rec["source_file"] = source_file
            for col, i in pos.items():
                rec[col] = to_raw_str(row[i] if i < len(row) else None)
            rows.append(rec)
            landed += 1

        counts = tab.counts()
        ctl = check_control_total(tab, report)
        dropped = len(tab.data) - landed
        report.append(
            f"{title}: {len(tab.data)} source rows, {dropped} dividers "
            f"dropped, {landed} landed -> chart_scope {scope}")
        meta[title] = {"chart_scope": scope, "source_rows": len(tab.data),
                       "sections_dropped": dropped, "rows_landed": landed,
                       "row_kinds": counts, "control_total": ctl,
                       "columns_found": sorted(pos)}

    if not rows:
        raise IngestError(f"{path}: no chart tabs found")
    return rows, meta


def to_csv_text(rows: list[dict]) -> str:
    """Render bronze_coa_raw as CSV text. See sink.to_csv_text."""
    return _to_csv_text(COLUMNS, rows)


def write_csv(rows: list[dict], path, encoding: str, report: list[str]) -> dict:
    """Write bronze_coa_raw.csv to a local path. See sink.write_csv."""
    return _write_csv(rows, COLUMNS, path, encoding, report)
