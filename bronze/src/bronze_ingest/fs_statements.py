"""fs_clean / fs_seeded — the Group condensed financial statements pair.

Source: Lynn's flat extracts, PoC_Group_FS_{clean,seeded}.csv. One module
serves both tables; which document is read is a config choice, because any
difference between the two tables has to come from the DOCUMENTS, never from
two extractors that drifted.

*** THIS EXTRACTOR INVERTS THE PACKAGE'S ARITHMETIC RULE ***
Every other extractor here refuses to land when the numbers disagree —
group_tb raises unless every period proves to nil. This one must NOT.
`fs_seeded` carries three deliberately planted defects and they are the
deliverable:

    Total current assets                    683,180 vs 682,760 footed
    Revenue from contracts with customers   423,221 vs 423,218 footed
    Borrowings cites note 5                 the borrowings note is 9

An extractor that footed the statements and refused would be unable to ingest
the document the PoC exists to examine. So the checks below are STRUCTURAL
only — did we read the file correctly — and every arithmetic question is left
to silver, where a break becomes a finding rather than a failure.

WHY THIS IS SO THIN. The other modules in this package carry real work:
excel.py discovers a table's boundaries in a sheet a human laid out, group_tb
melts wide period columns and proves them to nil. Lynn's file is already at
the target grain — one row per line item per column, typed, signed, ordered —
so there is nothing to reshape. Reading it through flatcsv.read_rows gives it
the same alias resolution and the same "a missing required column raises"
contract as every other CSV-sourced table, and nothing more is warranted.

Two things this module does NOT do, both deliberate:

  - it does not strip, pad or cast any value. Bronze mirrors the file; the
    types are applied by the load job from BRONZE_TABLES['fs_clean']['types'].
  - it does not add source_file. Per the Group FS Ingestion Notes, this pair
    carries no lineage column — doc_version and the table name already say
    which document a row came from.

Expected against the current pack: 142 rows per document.
"""

from __future__ import annotations

from .excel import IngestError
from .flatcsv import read_rows

COLUMNS = ["doc_version", "statement", "section", "line_order", "line_item",
           "note_ref", "line_role", "column_label", "amount", "amount_unit"]

# Single spellings, unlike the CoA/TB aliases: this file is machine-generated
# upstream rather than hand-authored, so there is no second spelling in the
# wild to accommodate. The alias table is kept anyway because it is what
# read_rows uses to enforce "a missing required column raises rather than
# landing a silent column of empty strings".
ALIASES = {c: [c] for c in COLUMNS}

# Every column is required. note_ref is legitimately EMPTY on most rows, but
# the COLUMN must still be present — absent, every row would silently lose
# its note reference and the seeded note-reference defect would vanish.
REQUIRED = set(ALIASES)

STATEMENTS = {"income_statement", "balance_sheet", "note_05_ppe",
              "note_07_tax", "note_09_borrowings", "note_10_revenue"}
LINE_ROLES = {"item", "subtotal", "total"}

# What the CSV writer emits for a genuine NULL, matching the load job's
# null_marker in cloud.py. Bronze otherwise holds no NULLs at all — an empty
# cell lands as an empty string — but note_ref is a nullable column in a
# typed table, and "no note reference" is a fact worth being able to express
# as NULL rather than as an empty string that sorts and groups with real ones.
NULL = "\\N"


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read one FS extract and return (rows, metadata)."""
    source_file = getattr(path, "name", str(path))
    raw, _headers = read_rows(path, ALIASES, REQUIRED,
                              cfg.get("csv_read_encoding", "utf-8-sig"))

    rows = [{c: rec[c] for c in COLUMNS} for rec in raw]

    _check_structure(rows, source_file, cfg)

    # Absent note_ref -> the NULL marker, so it lands as a real NULL rather
    # than an empty string. Done AFTER the checks so they see the file's own
    # empty value, not our marker.
    for row in rows:
        if row["note_ref"] == "":
            row["note_ref"] = NULL

    negatives = sum(1 for r in rows if r["amount"].startswith("-"))
    with_note = sum(1 for r in rows if r["note_ref"] != NULL)
    report.append(f"{source_file}: {len(rows)} rows landed "
                  f"({negatives} negative amounts, {with_note} note refs)")

    meta = {source_file: {
        "rows_landed": len(rows),
        "statements": sorted({r["statement"] for r in rows}),
        "negative_amounts": negatives,
    }}
    return rows, meta


def _check_structure(rows: list[dict], source_file: str, cfg: dict) -> None:
    """Structural checks only. NOTHING here may look at whether a subtotal
    foots — see the module docstring."""
    if not rows:
        raise IngestError(f"{source_file}: no data rows")

    expected = cfg.get("expected_rows")
    if expected is not None and len(rows) != expected:
        raise IngestError(
            f"{source_file}: expected {expected} rows, got {len(rows)} — the "
            f"pack changed shape and needs eyes on it before it lands.")

    doc_version = cfg.get("doc_version")
    if doc_version is not None:
        wrong = {r["doc_version"] for r in rows} - {doc_version}
        if wrong:
            # Guards the one mistake that would be invisible afterwards:
            # pointing fs_clean's config at the seeded file. Both are 142
            # rows of the same shape, so nothing else would notice.
            raise IngestError(
                f"{source_file}: expected doc_version '{doc_version}', also "
                f"saw {sorted(wrong)} — is input_path pointing at the wrong "
                f"document?")

    seen = {r["statement"] for r in rows}
    if seen != STATEMENTS:
        raise IngestError(
            f"{source_file}: statement set mismatch. "
            f"missing={sorted(STATEMENTS - seen)} "
            f"unexpected={sorted(seen - STATEMENTS)}")

    bad_roles = sorted({r["line_role"] for r in rows} - LINE_ROLES)
    if bad_roles:
        raise IngestError(
            f"{source_file}: unknown line_role(s) {bad_roles} — expected "
            f"{sorted(LINE_ROLES)}. A role we do not know is a row we cannot "
            f"tell apart from its own components.")

    # Every column except note_ref is NOT NULL in the DDL. Catching a blank
    # here names the column and the row; letting it reach BigQuery gets you a
    # load-job error with a byte offset.
    for row in rows:
        blank = [c for c in COLUMNS if c != "note_ref" and row[c].strip() == ""]
        if blank:
            raise IngestError(
                f"{source_file}: empty value(s) in NOT NULL column(s) {blank} "
                f"at statement={row['statement']!r} "
                f"line_order={row['line_order']!r}")

    # Typed columns: prove they parse HERE rather than discovering it as a
    # load-job failure. No value is replaced by the parsed result — bronze
    # still mirrors the file's text; this only proves BigQuery will accept it.
    from decimal import Decimal, InvalidOperation
    for row in rows:
        if not row["line_order"].lstrip("-").isdigit():
            raise IngestError(
                f"{source_file}: line_order {row['line_order']!r} is not an "
                f"integer (statement={row['statement']!r})")
        try:
            Decimal(row["amount"])
        except InvalidOperation:
            raise IngestError(
                f"{source_file}: amount {row['amount']!r} will not parse as "
                f"NUMERIC (statement={row['statement']!r} "
                f"line_order={row['line_order']!r})")

    keys = [(r["statement"], r["line_order"], r["column_label"]) for r in rows]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise IngestError(
            f"{source_file}: duplicate natural key(s) "
            f"(statement, line_order, column_label): {dupes[:5]} — the key "
            f"would not be a key.")

    # line_order is the line's position on the page, so within one statement
    # the distinct values must run 1..n. A gap means rows were dropped on the
    # way out of the source document.
    per_statement: dict[str, set[int]] = {}
    for row in rows:
        per_statement.setdefault(row["statement"], set()).add(
            int(row["line_order"]))
    for statement, orders in sorted(per_statement.items()):
        if min(orders) != 1 or max(orders) != len(orders):
            raise IngestError(
                f"{source_file}: {statement} line_order does not run 1..n "
                f"with no gaps — min={min(orders)} max={max(orders)} "
                f"distinct={len(orders)}")
