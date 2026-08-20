"""The bronze contract for fs_clean / fs_seeded: columns, types, prose.

One definition drives the CSV header order, the BigQuery load job's schema
and the generated DDL, so those three cannot disagree with each other.

Column descriptions are product, not documentation. Agent 6 is text-to-SQL
over this warehouse, so the description is what tells the agent which column
to pick and which trap to avoid — hence the length, and hence the warnings
written in the imperative.
"""

from __future__ import annotations

# What the CSV writes for a genuine NULL. The load job sets null_marker to
# this, so an empty field stays an empty STRING while this token becomes a
# real NULL.
#
# WHY THIS PAIR NEEDS IT AT ALL: every bronze_ingest table is all-STRING and
# deliberately holds no NULLs — an empty cell lands as an empty string, and
# its verification asserts exactly that. Here `value` is a nullable NUMERIC
# and an empty string is not a legal NUMERIC, so a balance-sheet group header
# (bold caption, no figure) would fail the load outright. Emitting the marker
# is what lets the same convention serve a typed table.
NULL = "\\N"

COLUMNS = ["statement", "section", "subsection", "label", "column_label",
           "value", "is_bold", "row_ord"]

# STRING unless named here. The Ingestion Notes' call — see statements.py.
TYPES = {"value": "NUMERIC", "is_bold": "BOOL", "row_ord": "INT64"}

# The four the DDL declares NOT NULL.
REQUIRED = {"statement", "label", "is_bold", "row_ord"}

DESCRIPTIONS = {
    "statement":
        "The heading of the table this row was taken from, verbatim, e.g. "
        "'Consolidated balance sheet' or 'Note 9 — Borrowings'. The grouping "
        "key of the whole model — cluster on it. NOTE THE EM DASH (U+2014, "
        "not a hyphen) in the note headings: equality filters must match the "
        "character exactly. Notes are values in this column rather than "
        "separate tables, so a note's internal arithmetic is checked by the "
        "same logic as a face statement, and adding a cash flow statement "
        "later is an insert rather than a schema change.",
    "section":
        "Top-level grouping, e.g. 'Assets', 'Equity and liabilities', "
        "'Operating costs'. NULL where the line sits outside any group — "
        "both for totals that sum across groups (Total assets) and for "
        "ordinary lines in no group (Finance costs). DERIVED, NOT PRINTED: "
        "the balance sheet never prints 'Assets' and the income statement "
        "prints no group headers at all, so the ranges are declared by label "
        "in configs/fs_*.json. This and subsection are the only interpreted "
        "columns in the table.",
    "subsection":
        "Second level, balance sheet only: 'Non-current assets', 'Current "
        "assets', 'Equity', 'Non-current liabilities', 'Current "
        "liabilities'. Always NULL on the income statement and the notes — "
        "neither goes three deep. A group header row carries its own "
        "subsection, so filtering on one returns the group intact, header "
        "included; tell the header from a total by value IS NULL. DERIVED — "
        "see section.",
    "label":
        "The line item description, verbatim, INCLUDING any note reference "
        "printed in the text ('Borrowings (Note 9)'). The parenthetical is "
        "deliberately not stripped or split into its own column: a wrong "
        "reference is a finding, it carries no arithmetic signal, and "
        "keeping the text is the only thing that makes it detectable at all. "
        "Never empty. NOT unique — 'Other assets and receivables' appears "
        "under both non-current and current assets.",
    "column_label":
        "The column header, verbatim, e.g. 'Q1 2026', '31 Mar 2026'. DO NOT "
        "treat the second column as comparable across statements: the income "
        "statement compares to Q1 2025 (a prior-year quarter), the balance "
        "sheet to 31 Dec 2025 (a prior year end). Those are different dates "
        "— match on the literal string, never on column position. Note 9 is "
        "the exception: 'Non-current' / 'Current' / 'Total' are a breakdown "
        "axis rather than a period, which is why this column is not called "
        "period_label. Notes 5, 7 and 10 print 'SAR million' and are given "
        "the period of the face statement they support, so this column is "
        "NOT verbatim for those three.",
    "value":
        "The figure, as NUMERIC — never FLOAT64. Footing checks run at zero "
        "tolerance and binary floating point manufactures breaks that are "
        "not in the document. SIGNED AS PRESENTED: figures printed in "
        "parentheses are stored negative, so costs, treasury shares and "
        "accumulated depreciation are negative and every subtotal sums with "
        "a plain SUM and no per-section sign rules. NULL where the page "
        "prints no figure — the bold group headers on the balance sheet "
        "occupy a row and have empty cells. Every figure in both documents "
        "is SAR million; the unit is not a column because it never varies.",
    "is_bold":
        "Whether the row is set in bold on the page. A formatting fact, not "
        "an interpretation, which is why it belongs in bronze rather than an "
        "is_total column. It marks two things: group headers (bold, value "
        "NULL) and totals/subtotals (bold, value present). It earns its "
        "place because totals sit in the same table as their components, so "
        "an unguarded SUM(value) double-counts — and the label will not save "
        "you: most totals in these documents do not contain the word 'Total' "
        "(Operating income, Net income, Cost — closing, Net book value, "
        "External revenue).",
    "row_ord":
        "Presentation order within a statement, from 1. Lets the statement "
        "be rendered back exactly as printed, and gives a stable way to "
        "point at a specific line. It is the line's position on the PAGE, "
        "not the row's position in this table, so it REPEATS once per column "
        "— Inventories appears twice at row_ord 11, once per period. The "
        "natural key is (statement, row_ord, column_label); BigQuery "
        "enforces no key, so ingest checks it.",
}


def _table_description(which: str, extra: str) -> str:
    return (
        f"Saudi Aramco Group condensed consolidated financial statements for "
        f"Q1 2026, {which}, exactly as printed. 152 rows = income statement "
        f"19 lines x 2 periods + balance sheet 43 x 2 + Notes 5/7/10 7+5+7 x "
        f"1 column + Note 9 3 x 3 columns. {extra} TYPED, not all-STRING: "
        f"value NUMERIC, is_bold BOOL, row_ord INT64 — the only such tables "
        f"in bronze, so that footing checks run at zero tolerance. No "
        f"source_file column: the table name identifies the document. "
        f"fs_clean and fs_seeded share this schema and have NO relationship "
        f"— no join keys, no shared surrogate ids, and nothing presuming the "
        f"two documents correspond row-for-row; the agent reads both and "
        f"performs its own comparison. Ingest checks structure only and "
        f"NEVER refuses to land on an arithmetic break — the breaks are the "
        f"point. SYNTHETIC data."
    )


TABLES = {
    "fs_clean": _table_description(
        "the CLEAN version",
        "Every subtotal foots and every cross-statement tie-out agrees; this "
        "is the control against which fs_seeded is read."),
    "fs_seeded": _table_description(
        "the SEEDED-ERROR version",
        "Carries three deliberately planted defects: Total current assets "
        "prints 683,180 against components footing to 682,760; Revenue from "
        "contracts with customers prints 423,221 against 423,218; and "
        "non-current Borrowings cites (Note 5) where the borrowings note is "
        "Note 9 — a text-only defect with no arithmetic signal, detectable "
        "only because label keeps the reference verbatim."),
}
