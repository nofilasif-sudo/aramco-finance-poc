"""fs_clean / fs_seeded — the Group financial statements, as printed.

Contract (Group FS Ingestion Notes, 17 Aug):
  - Eight columns. Every one is either verbatim text from the page or a fact
    about how the page was laid out. Nothing is reconciled here.
  - TYPED, not all-STRING. `value` is NUMERIC, `is_bold` is BOOL, `row_ord`
    is INT64. This is a DELIBERATE divergence from the bronze contract every
    table in bronze_ingest follows ("every value lands as text") — ruled in
    favour of the Ingestion Notes, which argue that footing checks run at
    zero tolerance and binary floating point would manufacture breaks that
    are not in the document. Decimal is used end-to-end below so the argument
    holds in Python too, and not only in the DDL.
  - TWO TABLES, fs_clean and fs_seeded, identical schema, no relationship.
    No join keys and no shared surrogate ids: the agent reads both and does
    its own comparison, rather than the pipeline pre-deciding that the two
    documents correspond row-for-row.
  - No source_file column. Every bronze_ingest table carries one; here the
    table name already identifies the document.

!! THIS TABLE MUST NOT REFUSE TO LAND ON AN ARITHMETIC BREAK !!

Every extractor in bronze_ingest raises IngestError when the numbers
disagree — group_tb refuses to land a trial balance that does not prove to
nil. THIS ONE INVERTS THAT. fs_seeded carries three deliberately planted
defects and they are the deliverable, not a fault:

    Total current assets             683,180  (components foot to 682,760)
    Revenue from contracts w/ cust.  423,221  (components foot to 423,218)
    Borrowings (Note 5)              in non-current liabilities; s/b Note 9

An extractor that footed the statements and refused would be unable to
ingest the document the PoC exists to examine. So the checks below are
STRUCTURAL ONLY — did we read the page correctly — and every arithmetic
question is left to silver, where a break becomes a finding.

The third defect is worth dwelling on: it carries NO arithmetic signal at
all. It is detectable only because `label` keeps the note reference verbatim
instead of stripping the parenthetical into a tidy note_ref column. That is
the strongest argument for the verbatim rule in the whole schema.

The one check that does inspect values earns its place: because the
Ingestion Notes declined a `value_text` companion column, a cell that failed
to parse would land as NULL and be indistinguishable from a legitimately
blank one. That fidelity is not persisted anywhere, so it has to be checked
at read time — see check_values_parsed(). A dropped figure is a READING
failure, not an arithmetic one.

section / subsection are DERIVED, not observed, and are the only two columns
here that are. They come from the config's `statements` block rather than
from the page, because the documents do not print them consistently: the
balance sheet prints "Equity and liabilities" as a header row but never
prints "Assets" or "Equity" at all, and the income statement prints no group
headers whatsoever — its groups are inferable only backwards, from a
trailing subtotal. Rather than bury that in a heuristic, the ranges are
declared by label in the config where a reviewer can hold them against the
printed page, and every anchor is verified to resolve exactly once.

Expected against the current pack: 152 rows per document.
  income statement   19 lines x 2 periods = 38
  balance sheet      43 lines x 2 periods = 86
  Note 5 / 7 / 10     7 + 5 + 7 x 1 col   = 19
  Note 9              3 lines x 3 columns =  9
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .errors import IngestError
from .schema import COLUMNS, NULL          # noqa: F401  (COLUMNS re-exported)
from .word import read_tables

# Characters a financial statement uses to mean "no figure" rather than zero.
DASHES = {"-", "‐", "‑", "‒", "–", "—", "−"}

# Thousands separators, and the several spaces Word likes to insert near them.
_STRIP = str.maketrans({",": "", " ": "", " ": "", " ": ""})

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
# Anything containing a digit should have parsed. Used only to tell a real
# parse failure ("1,2 34") from a legitimately non-numeric cell ("SAR
# million"), which the config allowlists per statement.
_HAS_DIGIT_RE = re.compile(r"\d")


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------
def parse_value(text: str) -> Decimal | None:
    """The printed figure as a Decimal, or None where there is no figure.

    Signed as presented: a figure in parentheses is stored negative, so
    costs, treasury shares and accumulated depreciation are all negative and
    every subtotal in both documents sums with a plain SUM and no
    per-section sign rules. Verified against the pack — the eight operating
    cost lines sum to exactly -244,700, and Note 5's cost less accumulated
    depreciation gives the 1,615,178 net book value on the balance sheet.

    Decimal, never float: the same argument the Ingestion Notes make for
    NUMERIC over FLOAT64. It has to hold here too, or the type in the DDL is
    decoration.
    """
    text = text.strip()
    if not text or text in DASHES:
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    text = text.translate(_STRIP).replace("−", "-")
    if not _NUMERIC_RE.match(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:            # pragma: no cover - regex precedes it
        return None
    return -value if negative else value


def is_unparsed_number(text: str, allowed: set[str]) -> bool:
    """True when a cell looks like it held a figure but did not parse."""
    text = text.strip()
    if not text or text in DASHES or text in allowed:
        return False
    return bool(_HAS_DIGIT_RE.search(text)) and parse_value(text) is None


# ---------------------------------------------------------------------------
# Hierarchy (derived — see the module docstring)
# ---------------------------------------------------------------------------
def resolve_range(labels: list[str], spec: dict, statement: str,
                  kind: str) -> range:
    """Turn a {name, from, to} label range into row indices.

    Anchors match the label exactly as printed. A missing or repeated anchor
    raises: it means the config and the document have drifted apart, and
    silently mis-assigning a section would corrupt every group subtotal
    check downstream.
    """
    bounds = []
    for edge in ("from", "to"):
        hits = [i for i, lab in enumerate(labels) if lab == spec[edge]]
        if not hits:
            raise IngestError(
                f"{statement}: {kind} '{spec['name']}' anchors on "
                f"{edge}='{spec[edge]}', which is not a label in this "
                f"statement.")
        if len(hits) > 1:
            raise IngestError(
                f"{statement}: {kind} '{spec['name']}' anchors on "
                f"{edge}='{spec[edge]}', which appears {len(hits)} times — "
                f"the range would be ambiguous.")
        bounds.append(hits[0])

    start, stop = bounds
    if stop < start:
        raise IngestError(
            f"{statement}: {kind} '{spec['name']}' ends at '{spec['to']}' "
            f"(line {stop + 1}) before it starts at '{spec['from']}' "
            f"(line {start + 1}).")
    return range(start, stop + 1)


def build_hierarchy(labels: list[str], spec: dict,
                    statement: str) -> list[tuple[str | None, str | None]]:
    """Per-line (section, subsection); None where the line sits outside any."""
    section: list[str | None] = [None] * len(labels)
    subsection: list[str | None] = [None] * len(labels)

    for kind, target in (("section", section), ("subsection", subsection)):
        for item in spec.get(kind + "s", []):
            for i in resolve_range(labels, item, statement, kind):
                target[i] = item["name"]

    # A total that sums ACROSS the groups above it belongs to none of them —
    # Total assets spans both subsections of the assets half. Applied last so
    # it overrides whatever range covered the line.
    for label in spec.get("top_level", []):
        if label not in labels:
            raise IngestError(
                f"{statement}: top_level lists '{label}', which is not a "
                f"label in this statement.")
        for i, lab in enumerate(labels):
            if lab == label:
                section[i] = subsection[i] = None

    return list(zip(section, subsection))


# ---------------------------------------------------------------------------
# Structural checks — reading failures only, never arithmetic
# ---------------------------------------------------------------------------
def check_statements(found: list[str], cfg: dict, report: list[str]) -> None:
    """The document holds exactly the statements the config declares."""
    expected = list(cfg["statements"])
    if found != expected:
        missing = [s for s in expected if s not in found]
        extra = [s for s in found if s not in expected]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        if not detail:
            detail.append(f"order differs: got {found}, expected {expected}")
        raise IngestError("statement set does not match the config — "
                          + "; ".join(detail))
    report.append(f"statements: all {len(expected)} present, in order -> OK")


def check_required(rows: list[dict], report: list[str]) -> None:
    """The four NOT NULL columns are populated on every row."""
    bad = [f"row_ord {r['row_ord']} of '{r['statement']}'"
           for r in rows
           if not r["statement"] or not r["label"]
           or r["is_bold"] not in ("true", "false")
           or not str(r["row_ord"]).isdigit()]
    if bad:
        raise IngestError("NOT NULL column empty on " + "; ".join(bad[:5]))
    report.append(f"required columns: statement, label, is_bold, row_ord "
                  f"populated on all {len(rows)} rows -> OK")


def check_values_parsed(rows: list[dict], failures: list[str],
                        report: list[str]) -> None:
    """No cell that looked like a figure was silently dropped.

    Stands in for the `value_text` column the Ingestion Notes declined. With
    the verbatim text not persisted, an unparsed figure lands as NULL and
    looks exactly like a blank group-header cell, so this is the only place
    the distinction still exists.
    """
    if failures:
        raise IngestError(
            f"{len(failures)} cell(s) look numeric but did not parse — "
            f"refusing to land, because without value_text a dropped figure "
            f"is indistinguishable from a blank cell: "
            + "; ".join(failures[:5])
            + (f" (+{len(failures) - 5} more)" if len(failures) > 5 else ""))
    parsed = sum(1 for r in rows if r["value"] != NULL)
    report.append(f"values: {parsed}/{len(rows)} cells hold a figure, "
                  f"0 parse failures -> OK")


def check_natural_key(rows: list[dict], report: list[str]) -> None:
    """(statement, row_ord, column_label) identifies a row.

    row_ord is the line's position on the PAGE, not in the table, so it
    repeats once per column — Inventories appears twice at row_ord 11.
    BigQuery enforces no key, so if this is not checked here it is not
    checked anywhere.
    """
    seen, dupes = set(), []
    for row in rows:
        key = (row["statement"], row["row_ord"], row["column_label"])
        if key in seen:
            dupes.append(" / ".join(map(str, key)))
        seen.add(key)
    if dupes:
        raise IngestError(
            "natural key (statement, row_ord, column_label) is not unique — "
            + "; ".join(dupes[:5]))
    report.append(f"natural key: {len(seen)} rows, all distinct -> OK")


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------
def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read one FS document into bronze rows. Returns (rows, metadata)."""
    tables = read_tables(path)
    check_statements([t.title for t in tables], cfg, report)

    rows: list[dict] = []
    meta: dict = {}
    parse_failures: list[str] = []

    for table in tables:
        spec = cfg["statements"][table.title]
        allowed = set(spec.get("non_numeric_cells", []))

        # The printed header, minus its first cell — that one sits above the
        # labels and is a stub ("Revenue by product" on Note 10) rather than
        # a column heading.
        printed = list(table.header[1:])
        # Notes carry a single unlabelled value column and inherit the period
        # of the face statement they support, which the page does not print
        # (it prints "SAR million"). Declared per statement, not guessed.
        labels_out = spec.get("column_labels", printed)
        if len(labels_out) != len(printed):
            raise IngestError(
                f"{table.title}: config gives {len(labels_out)} column "
                f"label(s) {labels_out} but the table prints "
                f"{len(printed)} {printed}.")
        if any(not lab for lab in labels_out):
            raise IngestError(f"{table.title}: blank column label in "
                              f"{labels_out}.")

        hierarchy = build_hierarchy([r.cells[0] for r in table.rows],
                                    spec, table.title)

        bold_count = 0
        for ordinal, (line, (section, subsection)) in enumerate(
                zip(table.rows, hierarchy), start=1):
            bold_count += line.bold
            for index, column_label in enumerate(labels_out):
                cell = (line.cells[index + 1]
                        if index + 1 < len(line.cells) else "")
                if is_unparsed_number(cell, allowed):
                    parse_failures.append(
                        f"'{table.title}' line {ordinal} ({line.cells[0]}) "
                        f"column '{column_label}': {cell!r}")
                value = parse_value(cell)
                rows.append({
                    "statement": table.title,
                    "section": section if section else NULL,
                    "subsection": subsection if subsection else NULL,
                    "label": line.cells[0],
                    "column_label": column_label,
                    "value": NULL if value is None else str(value),
                    "is_bold": "true" if line.bold else "false",
                    "row_ord": ordinal,
                })

        landed = len(table.rows) * len(labels_out)
        report.append(f"{table.title}: {len(table.rows)} lines x "
                      f"{len(labels_out)} column(s) = {landed} rows, "
                      f"{bold_count} bold")
        meta[table.title] = {"lines": len(table.rows),
                             "columns": labels_out,
                             "column_count": len(labels_out),
                             "rows_landed": landed,
                             "bold_lines": bold_count}

    check_required(rows, report)
    check_values_parsed(rows, parse_failures, report)
    check_natural_key(rows, report)
    # Deliberately NOT checked: footings, cross-statement tie-outs, note
    # references. See the module docstring — those are findings, not faults.
    return rows, meta
