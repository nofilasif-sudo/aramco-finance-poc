"""bronze_coa_mapping_sabic_raw / bronze_coa_mapping_rabigh_raw — Agent 3's
affiliate-account-to-Group-node mapping, with confidence scores.

Source: PoC_CoA_Mapping.xlsx, one tab per affiliate. One module serves both
tables; which tab is read is a config choice, because any difference between
the two tables has to come from the WORKBOOK, never from two extractors that
drifted apart.

TWO TABLES, NOT ONE STACKED TABLE. This is the one place this module departs
from coa.py, which stacks all three chart tabs into bronze_coa_raw and tells
them apart by an added chart_scope column. The mapping tabs are kept separate
because each is a self-contained SAP BPC configuration for one affiliate,
carrying its own triage footer (`Auto-mapped: N  Analyst review: N  Unmapped:
N  Total: N`) that is a control total for THAT affiliate only. affiliate_code
is still added at ingest, so the two can be UNION ALL'd whenever a consumer
wants them stacked — but a per-affiliate count never has to be recovered with
a WHERE clause first.

WHY affiliate_code IS ADDED AT INGEST. Exactly the reasoning behind
coa.chart_scope: the affiliate this mapping belongs to appears only in the tab
title and the title block, never in a column, and it disappears the moment the
rows leave the sheet. Without it a row is unjoinable, because the two
affiliates share 4-digit account codes that mean different things (1120 is
plant & machinery for 2010 but refinery plant for 2380).

*** THIS EXTRACTOR DOES NOT ADJUDICATE THE MAPPING ***
The low-confidence and unmapped rows are the DELIVERABLE, not a defect —
SABIC's 'Net result from discontinued operations' scores 0.35 because the
Group by-nature chart has no discontinued-operations caption, and surfacing
that is the point of the demo. So nothing here refuses to land on a low score,
a blank group_node, or a status the sheet disagrees with. Checks are
STRUCTURAL only (did we read the tab correctly) plus the sheet's own triage
control total, which validates OUR PARSE rather than the data. Same posture as
fs_statements.py, arrived at for the same reason.

Expected against the current pack: 66 rows SABIC, 44 rows Petro Rabigh.
"""

from __future__ import annotations

import re

from .excel import (IngestError, Table, read_tables, resolve_columns,
                    to_raw_str)
from .sink import to_csv_text as _to_csv_text, write_csv as _write_csv

# The bronze schema. The CSV header is generated from this list, so the file
# cannot drift from the spec without this line changing.
# affiliate_code and source_file are both ADDED AT INGEST — neither is a
# column in the sheet. See the module docstring.
COLUMNS = ["affiliate_code", "affiliate_account", "affiliate_account_name",
           "group_node", "group_node_name", "confidence", "status",
           "rationale", "source_file"]

ALIASES = {
    "affiliate_account":      ["affiliate a/c", "affiliate account",
                               "affiliate a/c code"],
    "affiliate_account_name": ["affiliate account name"],
    "group_node":             ["group node"],
    "group_node_name":        ["group node name"],
    "confidence":             ["confidence", "confidence score"],
    "status":                 ["status"],
    # 'Rationale (flagged rows)' — the parenthetical says WHEN it is populated,
    # not what the column is, so it is aliased away rather than carried into
    # the column name.
    "rationale":              ["rationale (flagged rows)", "rationale"],
}

# rationale is legitimately EMPTY on the ~90% of rows that auto-map, but the
# COLUMN must still be present — absent, every flagged row would silently lose
# the analyst's reasoning, which is the most valuable text in the workbook.
REQUIRED = set(ALIASES)

# The triage bands the workbook's own 'Read me' tab declares. Used to check
# that a status string matches the confidence it is paired with — a mismatch
# means the sheet was hand-edited after the agent scored it.
AUTO_MAPPED = "Auto-mapped"
ANALYST_REVIEW = "Analyst review"
UNMAPPED = "Unmapped - analyst intervention"
STATUSES = {AUTO_MAPPED, ANALYST_REVIEW, UNMAPPED}


def affiliate_code(title: str, title_text: str, pattern: str) -> str:
    """Derive affiliate_code from the tab's title block.

    Derived, not enumerated: a third affiliate next quarter needs no code
    change. The code is read from the rows ABOVE the header ('Affiliate 2010
    -> Aramco Group nodes'), not from the tab name, because the tab name
    ('Mapping - SABIC') carries the trade name while the title block carries
    the 4-digit code that bronze_coa_raw.chart_scope and bronze_tb_raw
    actually join on.

    A mapping tab we cannot attribute RAISES, for the same reason coa.py
    raises: landing 66 mappings with a blank affiliate_code is far worse than
    a failed run, because the collision between the two affiliate code sets
    makes an unattributed row unjoinable.
    """
    m = re.search(pattern, title_text)
    if m:
        return m.group(1)
    raise IngestError(
        f"tab '{title}' is a mapping tab but its title block carries no "
        f"affiliate code matching {pattern!r}. Text seen: {title_text[:200]!r}")


def check_control_total(tab: Table, counts: dict, report: list[str]) -> dict:
    """Reconcile against the tab's own triage footer.

    The footer reads 'Auto-mapped: 58    Analyst review: 7    Unmapped: 1
    Total: 66'. Like coa.check_control_total this validates OUR PARSE, not the
    data — but it is a stronger check than a bare row count, because it also
    proves the `status` COLUMN was resolved to the right index. Mapping status
    into the wrong column would still give 66 rows.
    """
    counted_total = sum(counts.values())
    footer = tab.footer_text()

    declared = {}
    for label, key in ((AUTO_MAPPED, AUTO_MAPPED),
                       ("Analyst review", ANALYST_REVIEW),
                       ("Unmapped", UNMAPPED),
                       ("Total", "_total")):
        m = re.search(rf"{re.escape(label.lower())}:\s*(\d+)", footer.lower())
        if m:
            declared[key] = int(m.group(1))

    if "_total" not in declared:
        report.append(f"[warn] {tab.title}: no triage footer — control total "
                      f"not checked")
        return {"declared": None, "counted": counted_total, "status": "ABSENT"}

    if declared["_total"] != counted_total:
        raise IngestError(
            f"{tab.title}: control total mismatch (sheet says "
            f"{declared['_total']} mappings, parsed {counted_total}) — "
            f"refusing to land.")

    for status in STATUSES:
        if status in declared and declared[status] != counts.get(status, 0):
            raise IngestError(
                f"{tab.title}: '{status}' count mismatch (sheet says "
                f"{declared[status]}, parsed {counts.get(status, 0)}) — the "
                f"status column may have been read from the wrong position. "
                f"Refusing to land.")

    report.append(
        f"control total {tab.title}: {declared['_total']} mappings "
        f"({', '.join(f'{s}={declared[s]}' for s in sorted(STATUSES) if s in declared)})"
        f" -> OK")
    return {"declared": declared, "counted": counted_total, "status": "OK"}


def _check_structure(rows: list[dict], title: str, cfg: dict) -> None:
    """Structural checks only. NOTHING here may judge whether a mapping is
    CORRECT or whether a confidence score is too low — see the module
    docstring."""
    expected = cfg.get("expected_rows")
    if expected is not None and len(rows) != expected:
        raise IngestError(
            f"{title}: expected {expected} mappings, got {len(rows)} — the "
            f"pack changed shape and needs eyes on it before it lands.")

    bad = sorted({r["status"] for r in rows} - STATUSES)
    if bad:
        raise IngestError(
            f"{title}: unknown status value(s) {bad} — expected "
            f"{sorted(STATUSES)}. A status we do not know is a row nobody "
            f"downstream can triage.")

    for row in rows:
        # Every column except rationale is NOT NULL in the DDL. Catching a
        # blank here names the account; letting it reach BigQuery gets you a
        # load-job error with a byte offset.
        blank = [c for c in COLUMNS if c != "rationale" and row[c].strip() == ""]
        if blank:
            raise IngestError(
                f"{title}: empty value(s) in NOT NULL column(s) {blank} at "
                f"affiliate_account={row['affiliate_account']!r}")

        # confidence stays TEXT in bronze like every other value, but prove it
        # parses HERE rather than discovering it downstream. The value is not
        # replaced by the parsed result.
        try:
            score = float(row["confidence"])
        except ValueError:
            raise IngestError(
                f"{title}: confidence {row['confidence']!r} is not a number "
                f"(affiliate_account={row['affiliate_account']!r})")
        if not 0.0 <= score <= 1.0:
            raise IngestError(
                f"{title}: confidence {score} is outside 0..1 "
                f"(affiliate_account={row['affiliate_account']!r})")

        # The Read me tab defines the bands; a row whose status contradicts its
        # own score means the sheet was edited after scoring, and every
        # downstream triage count would then be wrong in a way no row count
        # would catch. This checks the sheet against ITSELF — it never
        # second-guesses the mapping.
        expected_status = (AUTO_MAPPED if score >= 0.80 else
                           ANALYST_REVIEW if score >= 0.50 else UNMAPPED)
        if row["status"] != expected_status:
            raise IngestError(
                f"{title}: confidence {score} should be '{expected_status}' "
                f"per the triage bands, but the sheet says "
                f"{row['status']!r} (affiliate_account="
                f"{row['affiliate_account']!r})")

    # The affiliate account code is this table's key within one affiliate.
    codes = [r["affiliate_account"] for r in rows]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    if dupes:
        raise IngestError(
            f"{title}: duplicate affiliate_account(s) {dupes[:5]} — an "
            f"account cannot map to two Group nodes in one BPC configuration, "
            f"so this is a double-read of the tab, not a real fan-out.")


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read ONE affiliate's mapping tab and return (rows, metadata).

    `tab_marker` in the config selects the tab, so the same module produces
    both tables. Tab selection is still structural first — read_tables only
    yields tabs that have a header row, so the 'Read me' tab is skipped
    without being named.
    """
    # ADDED AT INGEST, same reasoning as affiliate_code: the workbook filename
    # is not a column in the sheet and it disappears once the rows are landed.
    source_file = getattr(path, "name", str(path))
    marker = cfg["tab_marker"].lower()

    matched: list[str] = []
    rows: list[dict] = []
    meta: dict = {}

    for title, tab in read_tables(path, cfg["header_sentinel"]):
        if marker not in title.lower():
            continue
        matched.append(title)

        code = affiliate_code(title, tab.title_text(), cfg["affiliate_pattern"])
        pos = resolve_columns(tab.headers, ALIASES, REQUIRED, title)

        # This tab's rows, checked and counted on their own before being
        # appended — never the accumulated list, so the checks and the report
        # stay per-tab even if tab_marker were ever loosened to match more.
        tab_rows: list[dict] = []
        counts: dict[str, int] = {}
        for row in tab.data:
            rec = dict.fromkeys(COLUMNS, "")
            rec["affiliate_code"] = code
            rec["source_file"] = source_file
            for col, i in pos.items():
                rec[col] = to_raw_str(row[i] if i < len(row) else None)
            tab_rows.append(rec)
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1

        _check_structure(tab_rows, title, cfg)
        ctl = check_control_total(tab, counts, report)

        flagged = sum(1 for r in tab_rows if r["rationale"].strip())
        rows.extend(tab_rows)
        report.append(
            f"{title}: {len(tab.data)} source rows, {len(tab_rows)} mappings "
            f"landed -> affiliate_code {code} ({flagged} carry a rationale)")
        meta[title] = {"affiliate_code": code, "source_rows": len(tab.data),
                       "rows_landed": len(tab_rows), "status_counts": counts,
                       "with_rationale": flagged, "control_total": ctl,
                       "columns_found": sorted(pos)}

    if len(matched) != 1:
        raise IngestError(
            f"{path}: tab_marker {cfg['tab_marker']!r} matched "
            f"{len(matched)} mapping tabs ({matched or 'none'}) — it must "
            f"select exactly one, or the two tables would hold the same rows.")
    return rows, meta


def to_csv_text(rows: list[dict]) -> str:
    """Render the mapping table as CSV text. See sink.to_csv_text."""
    return _to_csv_text(COLUMNS, rows)


def write_csv(rows: list[dict], path, encoding: str, report: list[str]) -> dict:
    """Write the mapping CSV to a local path. See sink.write_csv."""
    return _write_csv(rows, COLUMNS, path, encoding, report)
