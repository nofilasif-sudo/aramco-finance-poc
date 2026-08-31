"""GCS and BigQuery adapters.

Kept separate from `excel.py` / the extractor modules so those stay pure —
they take bytes and return rows, and have no idea whether they run on a
laptop, in a notebook, or in a Cloud Run job. That separation is what lets
the same code serve Path 2 without a rewrite.

The cloud libraries are imported INSIDE the functions on purpose: the package
must stay importable and testable on a machine with no GCP SDK.

Generalized from a single CoA-only schema to a registry (BRONZE_TABLES) so
the same bq_schema()/ensure_table()/load helpers serve every bronze table
this package owns instead of being re-hardcoded per table.

Table CREATION is DDL-first: ensure_table() executes the fixed CREATE
TABLE IF NOT EXISTS statement in sql/<table>.sql — those files are the
schema's source of truth, not this module. BRONZE_TABLES' columns/
descriptions here still drive the LOAD job's schema (must stay in the same
column order as the matching .sql file) and the local CSV header order —
keep both in sync by hand when a column changes.

Affiliate trial balance (bronze_tb_raw) is owned by another developer and
is not defined here. Everything else — coa, group_tb, ifrs standard/rubric,
entity context, checklist — is owned by this package.
"""

from __future__ import annotations

import io
from pathlib import Path

# ---------------------------------------------------------------------------
# source_file — the row-level lineage column every bronze table carries.
# Defined once here so its type and description can't drift between tables.
# ---------------------------------------------------------------------------
SOURCE_FILE_DESCRIPTION = (
    "Original workbook filename this row was read from (e.g. "
    "'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in "
    "the sheet. Lets a row be traced back to the exact file it came from "
    "when a workbook is re-shared or re-versioned."
)


def with_source_file(columns: list[str], descriptions: dict) -> tuple[list[str], dict]:
    """Append the shared source_file column/description to a table spec."""
    cols = [*columns, "source_file"]
    descs = {**descriptions, "source_file": SOURCE_FILE_DESCRIPTION}
    return cols, descs


# ---------------------------------------------------------------------------
# Column descriptions live here, not only in sql/*.sql, because this is what
# actually runs — and because Agent 6 is text-to-SQL over this warehouse, so
# the description is what tells the agent which column to pick. Treat them
# as product, not documentation.
# ---------------------------------------------------------------------------
_COA_COLUMNS, _COA_DESCRIPTIONS = with_source_file(
    ["chart_scope", "account", "account_name", "statement", "category",
     "normal_balance", "level", "source_reference"],
    {
        "chart_scope":
            "GROUP, 2010 or 2380. ADDED AT INGEST from the worksheet tab name — "
            "it is not a column in the sheet. Essential to every join: the two "
            "affiliate charts share 42 four-digit codes, 15 of which mean "
            "different things (1120 = plant & machinery for 2010 but refinery "
            "plant for 2380). Never join on account alone.",
        "account":
            "Account code as text. Affiliates use 4-digit ledger codes; the Group "
            "chart uses G-prefixed 5-digit nodes (G11000). Deliberately "
            "non-colliding namespaces. Never empty.",
        "account_name":
            "Account description as written, leading spaces preserved — "
            "indentation marks level-2 sub-accounts on the Group chart and is "
            "data, not formatting.",
        "statement":
            "Balance sheet | Income statement. Note the trial balances use BS/PL "
            "for the same concept; bronze does not harmonise them.",
        "category":
            "FS caption group, e.g. 'Non-current assets', 'Cost of sales'. "
            "13 distinct values across the affiliate charts.",
        "normal_balance":
            "Dr or Cr — the side this account normally sits on. Supports "
            "wrong-side anomaly detection; not used for sign correction.",
        "level":
            "Group chart only; empty for affiliates. 1 = face caption (52 nodes), "
            "2 = analytical sub-account (26 nodes). Text, so '1' not 1.0.",
        "source_reference":
            "Group chart only; empty for affiliates. Where the node traces to in "
            "Aramco's Q1-2026 condensed interim report, e.g. 'Balance sheet; "
            "Note 5'. Supports disclosure traceability.",
    },
)

_GROUP_TB_COLUMNS, _GROUP_TB_DESCRIPTIONS = with_source_file(
    ["account", "group_node", "type", "category", "period_label", "amount"],
    {
        "account":
            "The G-prefixed 5-digit Group node code, e.g. 'G11000'. Section "
            "header rows land here too.",
        "group_node":
            "The Group node NAME, e.g. 'Property, plant & equipment (net)'. "
            "EXTRA COLUMN vs bronze_tb_raw — this sheet carries both the code "
            "(in `account`) and the name (here); silver decides what to do "
            "with them.",
        "type":
            "LONG FORM here — 'Balance sheet' / 'Income statement' — NOT the "
            "affiliate TB's BS/PL abbreviations for the same concept. Bronze "
            "does not harmonise vocabularies across sources.",
        "category":
            "FS caption group.",
        "period_label":
            "Raw header string, e.g. '2024 Q1'. Same 9 quarters as "
            "bronze_tb_raw.",
        "amount":
            "Signed balance for this node and period, kept as text. Aramco "
            "GROUP CORE OPERATIONS ONLY — no affiliates in this figure.",
    },
)

_IFRS_STANDARD_COLUMNS, _IFRS_STANDARD_DESCRIPTIONS = with_source_file(
    ["standard_code", "standard_title", "disclosure_summary"],
    {
        "standard_code":
            "Short stable code: 'IFRS 15', 'IAS 24', 'IAS 16'. Primary key, "
            "and the value bronze_ifrs_rubric_raw.standard_code joins to.",
        "standard_title":
            "Full official title, e.g. 'IFRS 15 Revenue from Contracts with "
            "Customers'.",
        "disclosure_summary":
            "Paragraph summarising what the standard requires to be "
            "disclosed. Held once per standard rather than repeated across "
            "its five requirement rows.",
    },
)

_IFRS_RUBRIC_COLUMNS, _IFRS_RUBRIC_DESCRIPTIONS = with_source_file(
    ["standard", "req", "requirement", "standard_code", "evidence_type",
     "check_guidance"],
    {
        "standard":
            "The IFRS/IAS standard's full name, e.g. 'IFRS 15 Revenue', "
            "'IAS 24 Related party', 'IAS 16 PP&E'. A descriptive attribute — "
            "join on standard_code, not on this.",
        "req":
            "Requirement code within the standard, R1..R5.",
        "requirement":
            "The disclosure requirement text. Agent 4 (Disclosure Drafting) "
            "reference data — this is what a note is scored AGAINST, not "
            "financial data itself.",
        "standard_code":
            "Short stable code for the standard: 'IFRS 15', 'IAS 24', "
            "'IAS 16'. Joins to bronze_ifrs_standard_raw.standard_code. "
            "Preferred over the full name as a key because a standard can be "
            "retitled but its code will not change.",
        "evidence_type":
            "What kind of evidence satisfies this requirement: 'narrative' "
            "(prose in the note), 'table_structure' (the shape of a table), "
            "or 'both'. Lets the disclosure agent decide whether to inspect "
            "wording or table columns.",
        "check_guidance":
            "The explicit rule for what counts as meeting this requirement, "
            "e.g. 'Revenue table must break down revenue into product "
            "categories, not only a total.' This is what makes the rubric "
            "machine-actionable rather than just a list of topics.",
    },
)

_ENTITY_CONTEXT_COLUMNS, _ENTITY_CONTEXT_DESCRIPTIONS = with_source_file(
    ["context_key", "context_value"],
    {
        "context_key":
            "Metadata key, e.g. 'reporting_entity', 'presentation_currency', "
            "'reportable_segments', 'negative_convention'. Primary key.",
        "context_value":
            "Free-text value. Narrative reference an agent reads to ground a "
            "disclosure or interpret a figure — never aggregated, never cast.",
    },
)

_CHECKLIST_COLUMNS, _CHECKLIST_DESCRIPTIONS = with_source_file(
    ["item", "document", "required", "applies_to", "expected_format",
     "description"],
    {
        "item":
            "Checklist item number, 01..14.",
        "document":
            "Required document name, e.g. 'Signed trial balance', "
            "'Intercompany reconciliation'.",
        "required":
            "Whether this document is mandatory for a submission ('Yes').",
        "applies_to":
            "Which affiliates this requirement applies to, e.g. "
            "'All affiliates'.",
        "expected_format":
            "Expected file format, e.g. 'xlsx / SAP BPC export', "
            "'pdf (signed)'.",
        "description":
            "What the document should contain.",
    },
)

# ---------------------------------------------------------------------------
# fs_clean / fs_seeded — the Group financial statements pair.
#
# THREE DELIBERATE DIVERGENCES from every other table in this registry, all
# ruled by the Group FS Ingestion Notes rather than by repo convention:
#
#   1. NO source_file COLUMN — note the absence of with_source_file() below,
#      which every other spec here uses. The table name and doc_version both
#      already identify the document.
#   2. TYPED, not all-STRING — see _FS_TYPES. The only non-STRING columns in
#      bronze.
#   3. THE NAMES ARE fs_clean / fs_seeded, not bronze_fs_*_raw.
#
# Source is Lynn's flat extract, already one row per line item per column, so
# there is nothing to melt or derive — see fs_statements.py.
# ---------------------------------------------------------------------------
_FS_COLUMNS = ["doc_version", "statement", "section", "line_order",
               "line_item", "note_ref", "line_role", "column_label",
               "amount", "amount_unit"]

# STRING unless named here. note_ref stays STRING on purpose: it is an
# identifier compared only for equality, never summed, and a future '9a'
# would not need a schema change.
_FS_TYPES = {"line_order": "INT64", "amount": "NUMERIC"}

_FS_DESCRIPTIONS = {
    "doc_version":
        "Which source document this row came from — 'clean' in fs_clean, "
        "'seeded' in fs_seeded. CONSTANT within each table, so it selects "
        "nothing as a filter; it exists so the two can be UNION ALL'd into "
        "one result set and still be told apart.",
    "statement":
        "Which statement or note the row belongs to, as a SNAKE_CASE KEY, "
        "not a printed heading: 'income_statement', 'balance_sheet', "
        "'note_05_ppe', 'note_07_tax', 'note_09_borrowings', "
        "'note_10_revenue'. Exactly 6 values. The grouping key of the whole "
        "model — cluster on it. Notes are values in this column rather than "
        "separate tables, so a note's internal arithmetic is checked by the "
        "same logic as a face statement, and adding a cash flow statement "
        "later is an insert rather than a schema change.",
    "section":
        "The grouping band within the statement, e.g. 'Non-current assets', "
        "'Current liabilities', 'Equity and liabilities'. 13 distinct "
        "values, NEVER NULL: income statement rows all read 'Income "
        "statement' and each note's rows read that note's title. Rows that "
        "total ACROSS bands carry the band they conclude ('Assets' for Total "
        "assets), so summing every 'item' inside one section is well defined.",
    "line_order":
        "Presentation order within a statement, from 1, contiguous with no "
        "gaps. It is the line's position on the PAGE, not the row's position "
        "in this table, so it REPEATS once per column — Inventories appears "
        "twice at line_order 9, once per period. The natural key is "
        "(statement, line_order, column_label); BigQuery enforces no key, so "
        "ingest checks it.",
    "line_item":
        "The line item description, verbatim, e.g. 'Cash and cash "
        "equivalents'. Never empty. NOT unique and NOT a safe join key: "
        "'Other assets and receivables', 'Post-employment benefits', "
        "'Investments in securities' and 'Borrowings' each appear under both "
        "a non-current and a current section. The note reference is NOT part "
        "of this text — it lives in note_ref.",
    "note_ref":
        "The note this line cross-references, as the bare number ('5', '7', "
        "'9', '10'). NULL on the 126 of 142 rows that print no reference — "
        "one of the few genuine NULLs in bronze. STRING, not INT64: it is "
        "compared for equality, never summed. A WRONG value here is a real "
        "finding that carries no arithmetic signal, so no footing check will "
        "ever surface it — see fs_seeded's table description.",
    "line_role":
        "What the line does arithmetically: 'item' (a component), 'subtotal' "
        "(sums the items above it within a section) or 'total' (concludes "
        "the statement or sums across sections). LOAD-BEARING: totals sit in "
        "the same table as their components, so an unguarded SUM(amount) "
        "double-counts — filter to line_role = 'item' to sum. The line_item "
        "text will not save you: most totals here do not contain the word "
        "'Total' (Operating income, Net income, Cost - closing, Net book "
        "value, External revenue).",
    "column_label":
        "The column header, verbatim. 8 distinct values. DO NOT treat the "
        "second column as comparable across statements: the income statement "
        "compares 'Q1 2026' to 'Q1 2025' (a prior-year quarter), the balance "
        "sheet compares '31 Mar 2026' to '31 Dec 2025' (a prior year end). "
        "Match on the literal string, never on column position. Two "
        "exceptions: Note 9 uses 'Non-current'/'Current'/'Total', a "
        "breakdown axis rather than a period, which is why this is not "
        "called period_label; and Notes 5, 7 and 10 are single-column and "
        "print the literal 'SAR million' here.",
    "amount":
        "The figure, as NUMERIC — never FLOAT64. Footing checks run at zero "
        "tolerance and binary floating point manufactures breaks that are "
        "not in the document. SIGNED AS PRESENTED: figures printed in "
        "parentheses in the source document are stored NEGATIVE (29 of 142 "
        "rows), so costs, treasury shares and accumulated depreciation are "
        "negative and every subtotal sums with a plain SUM and no per-section "
        "sign rules. Never NULL.",
    "amount_unit":
        "The unit of amount. CONSTANT 'SAR million' for every row, so it is "
        "useless as a filter or GROUP BY key; it is carried so the figures "
        "are never read as riyals. Unrelated to column_label also reading "
        "'SAR million' on Notes 5, 7 and 10, where that is the note's single "
        "column HEADER.",
}


def _fs_table_description(which: str, extra: str) -> str:
    return (
        f"Saudi Aramco Group condensed consolidated financial statements for "
        f"Q1 2026, {which}, loaded verbatim from Lynn's flat extract. 142 "
        f"rows = income statement 19 lines x 2 periods + balance sheet 38 x "
        f"2 + Notes 5/7/10 7+5+7 x 1 column + Note 9 3 x 3 columns. {extra} "
        f"TYPED, not all-STRING: amount NUMERIC, line_order INT64 — the only "
        f"such columns in bronze, so that footing checks run at zero "
        f"tolerance. Sum with a line_role = 'item' filter; totals and "
        f"subtotals share the table with their components. No source_file "
        f"column: the table name and doc_version identify the document. "
        f"fs_clean and fs_seeded share this schema and have NO relationship "
        f"— no join keys, no shared surrogate ids, and nothing presuming the "
        f"two documents correspond row-for-row; the agent reads both and "
        f"performs its own comparison. Ingest checks structure only and "
        f"NEVER refuses to land on an arithmetic break — the breaks are the "
        f"point. SYNTHETIC data."
    )


# ---------------------------------------------------------------------------
# The CoA mapping pair — Agent 3's affiliate-account-to-Group-node mapping,
# one table per affiliate (see coa_mapping.py for why they are not stacked).
#
# Built by a factory rather than written twice: the two tabs are the same BPC
# configuration for different affiliates, so all but three descriptions are
# word-for-word identical and two hand-maintained copies would drift.
# ---------------------------------------------------------------------------
_MAPPING_BASE_COLUMNS = ["affiliate_code", "affiliate_account",
                         "affiliate_account_name", "group_node",
                         "group_node_name", "confidence", "status",
                         "rationale"]


def _mapping_spec(code: str, name: str, rows: int, flagged: int,
                  account_example: str, status_note: str,
                  rationale_example: str) -> tuple[list[str], dict]:
    """One affiliate's mapping columns + descriptions."""
    return with_source_file(
        _MAPPING_BASE_COLUMNS,
        {
            "affiliate_code":
                f"The affiliate this mapping configures: constant '{code}' "
                f"({name}) in this table. ADDED AT INGEST from the "
                f"worksheet's title block — it is not a column in the sheet. "
                f"Constant here, so it selects nothing as a filter; it exists "
                f"so the two mapping tables can be UNION ALL'd and still be "
                f"told apart, and so a row stays joinable to "
                f"bronze_coa_raw.chart_scope and bronze_tb_raw once it leaves "
                f"the sheet.",
            "affiliate_account":
                f"The affiliate's 4-digit ledger account code, e.g. '1100'. "
                f"Unique within this table — one account maps to exactly one "
                f"Group node in a BPC configuration. Joins to "
                f"bronze_coa_raw.account WITH chart_scope = '{code}'; never "
                f"join on account alone, because 2010 and 2380 share 4-digit "
                f"codes that mean different things (1120 = plant & machinery "
                f"for 2010 but refinery plant for 2380).",
            "affiliate_account_name":
                f"The affiliate's own account description, verbatim, e.g. "
                f"'{account_example}'. The by-FUNCTION vocabulary the "
                f"affiliate books in — this is the text the mapping had to "
                f"reconcile against the Group's by-NATURE captions.",
            "group_node":
                "The G-prefixed 5-digit Aramco Group node this account maps "
                "to, e.g. 'G11000'. Joins to bronze_coa_raw.account with "
                "chart_scope = 'GROUP', and to bronze_group_tb_raw.account. "
                "Populated even on flagged rows — there it is the agent's "
                "best candidate, NOT a confirmed mapping; read `status` "
                "before trusting it.",
            "group_node_name":
                "The Group node NAME, e.g. 'Property, plant & equipment "
                "(net)'. Carried alongside the code for readability; the code "
                "is the join key.",
            "confidence":
                "The agent's confidence in this mapping, 0.00-1.00, as TEXT "
                "per the bronze all-STRING contract — CAST to NUMERIC to "
                "compare or aggregate. Drives the triage bands: >= 0.80 "
                "auto-maps, 0.50-0.79 routes to analyst review, < 0.50 is "
                "unmapped. Ingest verifies every row's `status` agrees with "
                "its score, so the two can be used interchangeably.",
            "status": status_note,
            "rationale":
                f"Why this row was flagged, written by the agent — e.g. "
                f"\"{rationale_example}\" EMPTY STRING (never NULL) on rows "
                f"that mapped cleanly; populated on {flagged} of {rows}. The "
                f"most valuable text in the table: it names the specific "
                f"by-function-to-by-nature judgement an analyst has to make.",
        },
    )


_MAPPING_SABIC_COLUMNS, _MAPPING_SABIC_DESCRIPTIONS = _mapping_spec(
    "2010", "SABIC", rows=66, flagged=10,
    account_example="Cost of sales - catalysts & chemicals",
    status_note=(
        "Triage outcome, a closed set of exactly three values: 'Auto-mapped' "
        "(58 rows), 'Analyst review' (7 rows), 'Unmapped - analyst "
        "intervention' (1 row). Derived from confidence per the workbook's "
        "stated bands. This is the column an agent filters on to find the "
        "work queue."),
    rationale_example=(
        "Catalysts & chemicals: 'Producing & manufacturing' vs 'Purchases' - "
        "classic by-function/by-nature ambiguity."))

_MAPPING_RABIGH_COLUMNS, _MAPPING_RABIGH_DESCRIPTIONS = _mapping_spec(
    "2380", "Petro Rabigh", rows=44, flagged=6,
    account_example="Cost of sales - crude & feedstock",
    status_note=(
        "Triage outcome: 'Auto-mapped' (40 rows) and 'Analyst review' (4 "
        "rows). NOTE this table has NO 'Unmapped - analyst intervention' "
        "rows — that value is legal in the column and appears in "
        "bronze_coa_mapping_sabic_raw; do not infer the domain from this "
        "table alone."),
    rationale_example=(
        "Shareholder subordinated loan: 'Borrowings' vs related-party "
        "financing - analyst to confirm."))


# Table name -> (columns, descriptions, table description). One entry per
# bronze table this package owns — the local CLI, push_to_bq.py and this
# module's own bq_schema()/ensure_table() all key off this registry instead
# of each hardcoding a schema.
#
# An entry may carry an optional "types" key; without it every column is
# STRING, which is the bronze contract and what all six original tables use.
BRONZE_TABLES = {
    "fs_clean": {
        "columns": _FS_COLUMNS,
        "descriptions": _FS_DESCRIPTIONS,
        "types": _FS_TYPES,
        "table_description": _fs_table_description(
            "the CLEAN version",
            "Every subtotal foots and every cross-statement tie-out agrees; "
            "this is the control against which fs_seeded is read."),
    },
    "fs_seeded": {
        "columns": _FS_COLUMNS,
        "descriptions": _FS_DESCRIPTIONS,
        "types": _FS_TYPES,
        "table_description": _fs_table_description(
            "the SEEDED-ERROR version",
            "Structurally identical to fs_clean — the defects change three "
            "values, never the shape. THE THREE PLANTED DEFECTS: (1) Total "
            "current assets at 31 Mar 2026 prints 683,180 where its eight "
            "components foot to 682,760, a 420 break that also breaks the "
            "balance sheet, since Total assets still prints 2,661,959; (2) "
            "Revenue from contracts with customers in Note 10 prints 423,221 "
            "where its three components foot to 423,218, a 3 break; (3) "
            "non-current Borrowings cites note 5 where the borrowings note "
            "is 9 — a text-only defect with no arithmetic signal, detectable "
            "only by validating the reference against the note. Defects 1 "
            "and 2 are in amount, defect 3 is in note_ref."),
    },
    "bronze_coa_raw": {
        "columns": _COA_COLUMNS,
        "descriptions": _COA_DESCRIPTIONS,
        "table_description": (
            "Charts of accounts for both affiliates and the Group, stacked. 188 "
            "rows = 78 GROUP + 66 SABIC + 44 Petro Rabigh, matching each tab's "
            "own 'Total accounts' footer exactly. All columns STRING per the "
            "bronze contract. Section-divider rows are dropped at ingest (their "
            "captions are fully duplicated by the category column) — a "
            "deviation negotiated specifically for this table, not a "
            "pipeline-wide rule. No relationships enforced at this layer: the "
            "affiliate-to-Group mapping does not exist in this pack — it is "
            "Agent 3's output, produced with a confidence score, not an input. "
            "SYNTHETIC data calibrated to public results; not Aramco actuals."
        ),
    },
    "bronze_coa_mapping_sabic_raw": {
        "columns": _MAPPING_SABIC_COLUMNS,
        "descriptions": _MAPPING_SABIC_DESCRIPTIONS,
        "table_description": (
            "SABIC (affiliate 2010) chart-of-accounts mapping to Aramco Group "
            "nodes, as configured in SAP BPC and scored by Agent 3. 66 rows, "
            "one per affiliate account, matching the tab's own triage footer "
            "(58 auto-mapped + 7 analyst review + 1 unmapped) exactly. All "
            "columns STRING per the bronze contract — CAST confidence to "
            "compare it. THE FLAGGED ROWS ARE THE DELIVERABLE, NOT A DEFECT: "
            "ingest never refuses to land on a low score or an unmapped row, "
            "because surfacing exactly these is the point of the demo. The "
            "flagship case is account 8100 'Net result from discontinued "
            "operations' at confidence 0.35 — the Group by-nature chart has "
            "NO discontinued-operations caption, so the line cannot be mapped "
            "and must be decomposed by an analyst. Kept as its own table "
            "rather than stacked with Petro Rabigh because each tab is a "
            "self-contained BPC configuration with its own control total; "
            "UNION ALL on affiliate_code to combine them. This is the "
            "affiliate-to-Group mapping that bronze_coa_raw's description "
            "notes was absent from the original pack. No relationships "
            "enforced at this layer. SYNTHETIC data calibrated to public "
            "results; not Aramco actuals."
        ),
    },
    "bronze_coa_mapping_rabigh_raw": {
        "columns": _MAPPING_RABIGH_COLUMNS,
        "descriptions": _MAPPING_RABIGH_DESCRIPTIONS,
        "table_description": (
            "Petro Rabigh (affiliate 2380) chart-of-accounts mapping to "
            "Aramco Group nodes, as configured in SAP BPC and scored by Agent "
            "3. 44 rows, one per affiliate account, matching the tab's own "
            "triage footer (40 auto-mapped + 4 analyst review + 0 unmapped) "
            "exactly. All columns STRING per the bronze contract — CAST "
            "confidence to compare it. THE FLAGGED ROWS ARE THE DELIVERABLE, "
            "NOT A DEFECT: ingest never refuses to land on a low score, "
            "because surfacing exactly these is the point of the demo. The "
            "four flagged rows are the genuine by-function-to-by-nature "
            "judgements — catalysts & chemicals, other operating "
            "income/expenses, and a shareholder subordinated loan that could "
            "sit in 'Borrowings' or in related-party financing. Petro Rabigh "
            "is a REFINERY: its chart is 22 accounts shorter than SABIC's and "
            "carries no discontinued operations, no derivatives and no "
            "separate income-tax line, so an account absent here is a real "
            "difference between the affiliates rather than a dropped row. "
            "Kept as its own table rather than stacked with SABIC because "
            "each tab is a self-contained BPC configuration with its own "
            "control total; UNION ALL on affiliate_code to combine them. No "
            "relationships enforced at this layer. SYNTHETIC data calibrated "
            "to public results; not Aramco actuals."
        ),
    },
    "bronze_group_tb_raw": {
        "columns": _GROUP_TB_COLUMNS,
        "descriptions": _GROUP_TB_DESCRIPTIONS,
        "table_description": (
            "Saudi Aramco's own (parent-only, GROUP CORE OPERATIONS, no "
            "affiliates) quarterly trial balance in G-node vocabulary, "
            "unpivoted the same way as bronze_tb_raw. 531 rows = 59 account/"
            "subtotal rows x 9 periods (68 body rows minus 9 dropped section "
            "dividers, verified redundant with category). Refuses to land "
            "unless every period column proves to nil. All columns STRING. "
            "SYNTHETIC data."
        ),
    },
    "bronze_ifrs_standard_raw": {
        "columns": _IFRS_STANDARD_COLUMNS,
        "descriptions": _IFRS_STANDARD_DESCRIPTIONS,
        "table_description": (
            "One row per IFRS/IAS standard in scope — the standard-level "
            "parent of bronze_ifrs_rubric_raw. 3 rows: IFRS 15, IAS 24, "
            "IAS 16. Separate from the rubric because disclosure_summary is "
            "a paragraph with only three distinct values, and repeating it "
            "across five requirement rows per standard invites drift."
        ),
    },
    "bronze_ifrs_rubric_raw": {
        "columns": _IFRS_RUBRIC_COLUMNS,
        "descriptions": _IFRS_RUBRIC_DESCRIPTIONS,
        "table_description": (
            "IFRS disclosure requirements rubric — Agent 4 (Disclosure "
            "Drafting) reference data. 15 rows = 3 standards x 5 "
            "requirements. Sourced from ifrs_requirements_updated.csv; the "
            "'Compliant version'/'Gap version'/'Gap detail' columns of the "
            "original workbook are the demo answer key and remain "
            "deliberately excluded. evidence_type and check_guidance make "
            "the rubric machine-actionable. standard_code joins to "
            "bronze_ifrs_standard_raw."
        ),
    },
    "bronze_entity_context_raw": {
        "columns": _ENTITY_CONTEXT_COLUMNS,
        "descriptions": _ENTITY_CONTEXT_DESCRIPTIONS,
        "table_description": (
            "Reporting-entity metadata as key-value pairs: entity, period, "
            "currency, units, segments, reporting framework, sign "
            "convention. 13 rows. Deliberately key-value (EAV) shaped "
            "because the attributes are open-ended narrative, there is "
            "exactly one entity described, and the consumer is an agent "
            "reading prose rather than a tool aggregating a measure. Source: "
            "entity_context (1).csv — a shorter 10-key file of the same name "
            "exists and is NOT the authoritative one."
        ),
    },
    "bronze_checklist_raw": {
        "columns": _CHECKLIST_COLUMNS,
        "descriptions": _CHECKLIST_DESCRIPTIONS,
        "table_description": (
            "Required-documents checklist for an affiliate submission pack — "
            "Agent 2 reference data. 14 rows. CHECKLIST TAB ONLY: the "
            "Manifest-*/IC-confirmations tabs in the same workbook (submission "
            "status, intercompany anomaly A6) are Agent 2's own ingestion and "
            "are not part of this table."
        ),
    },
}


def bq_schema(columns: list[str], descriptions: dict, types: dict | None = None):
    """Columns as BigQuery fields, with descriptions.

    One definition feeds both CREATE TABLE and the load job, so a table's
    schema and the loader's schema cannot disagree.

    STRING unless `types` names the column otherwise. The default is the
    bronze contract — every value lands as text — and every table this
    package owned before fs_clean/fs_seeded passes no `types` at all, so
    their schemas are byte-identical to what they were.

    The exception exists for the FS pair only, per the Group FS Ingestion
    Notes: `amount` is NUMERIC and `line_order` is INT64 so that footing
    checks downstream run at zero tolerance, which binary floating point
    cannot do. See BRONZE_TABLES['fs_clean'].
    """
    from google.cloud import bigquery
    types = types or {}
    return [bigquery.SchemaField(c, types.get(c, "STRING"),
                                 description=descriptions[c])
            for c in columns]


# ---------------------------------------------------------------------------
# GCS
# ---------------------------------------------------------------------------
def read_workbook(gcs_uri: str) -> io.BytesIO:
    """Download an xlsx from GCS into memory.

    openpyxl cannot open a gs:// path — it needs a filename or a file-like
    object. Memory is simpler than staging on disk for a file this size, and
    leaves nothing behind when a notebook runtime is recycled.
    """
    from google.cloud import storage

    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    if not blob.exists():
        raise FileNotFoundError(f"{gcs_uri} does not exist")
    return io.BytesIO(blob.download_as_bytes())


def upload_csv(text: str, gcs_uri: str) -> str:
    """Write the CSV to GCS. This object is the lineage artifact, not a temp
    file — it is what proves, byte for byte, what was loaded."""
    from google.cloud import storage

    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    client = storage.Client()
    client.bucket(bucket_name).blob(blob_name).upload_from_string(
        text, content_type="text/csv")
    return gcs_uri


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------
def dataset_location(client, dataset_id: str, default: str) -> str:
    """Return the dataset's location, or `default` if it does not exist yet.

    Discovered rather than configured, because a dataset's location is
    IMMUTABLE and BigQuery refuses to load from a bucket in another location.
    Hardcoding a guess here turns a config typo into a confusing load error.
    """
    from google.cloud.exceptions import NotFound
    try:
        return client.get_dataset(dataset_id).location
    except NotFound:
        return default


def ensure_table(client, table_id: str, location: str, ddl_path) -> None:
    """Create the dataset and table from a FIXED DDL file. Never drops data.

    The .sql file at ddl_path (e.g. sql/bronze_coa_raw.sql) is the schema's
    source of truth — it is executed verbatim via CREATE TABLE IF NOT
    EXISTS, so re-running this is always safe and only the load replaces
    rows. Python no longer builds or evolves the schema: to add, rename, or
    retype a column, edit the .sql file directly. CREATE TABLE IF NOT
    EXISTS is a no-op against an existing table, so a real schema change on
    an already-created table needs an explicit ALTER TABLE statement added
    to the same file (see silver/sql/silver_build.sql for the pattern) —
    this function will not silently evolve a live table's schema for you.

    table_id's dataset does not have to be "bronze" — every .sql file
    hardcodes `.bronze.` as its dataset qualifier, so if table_id targets
    a different dataset (e.g. bronze_staging, for validating a run before
    it touches the live tables), that qualifier is rewritten to match
    before the DDL runs.
    """
    from google.cloud import bigquery

    project, dataset, table = table_id.split(".")
    ds = bigquery.Dataset(f"{project}.{dataset}")
    ds.location = location
    client.create_dataset(ds, exists_ok=True)

    ddl_sql = Path(ddl_path).read_text(encoding="utf-8")
    ddl_sql = ddl_sql.replace(".bronze.", f".{dataset}.")
    client.query(ddl_sql, location=location).result()


def _load_config(columns: list[str], descriptions: dict,
                 types: dict | None = None):
    """Load settings shared by the GCS and local-file paths.

    Three settings carry all the risk:

    autodetect=False — autodetection would type `account` as INT64, making
        G11000 and 1100 incompatible, and silently break the all-STRING
        contract. The schema IS the contract; never let BigQuery guess it.

    null_marker="\\N" — BigQuery's CSV loader turns an EMPTY UNQUOTED FIELD
        into NULL by default. Our contract says an empty cell lands as an
        empty string; a marker that never occurs means empties stay empty
        strings and bronze holds no NULLs at all. VERIFY on the first load —
        do not assume it.

    WRITE_TRUNCATE — reloading the same pack with append gives double the
        rows and every check still passes proportionally. Wrong but
        internally consistent is the failure mode to fear.
    """
    from google.cloud import bigquery
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        schema=bq_schema(columns, descriptions, types),
        skip_leading_rows=1,
        autodetect=False,
        null_marker="\\N",
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )


def load_csv_from_gcs(client, gcs_uri: str, table_id: str, location: str,
                      columns: list[str], descriptions: dict,
                      types: dict | None = None):
    job = client.load_table_from_uri(
        gcs_uri, table_id,
        job_config=_load_config(columns, descriptions, types),
        location=location)
    job.result()          # blocks; raises with row-level detail on failure
    return job


def load_csv_from_memory(client, csv_text: str, table_id: str, location: str,
                         columns: list[str], descriptions: dict,
                         types: dict | None = None):
    """Load straight from an in-memory CSV string — no local file, no bucket.

    csv_text never touches disk: it is encoded straight into a BytesIO
    buffer and streamed to the load job. Useful when GCS is not
    provisioned yet; prefer the GCS path (load_csv_from_gcs) once a bucket
    exists, since that also gives you a lineage artifact.
    """
    buf = io.BytesIO(csv_text.encode("utf-8"))
    job = client.load_table_from_file(
        buf, table_id, job_config=_load_config(columns, descriptions, types),
        location=location)
    job.result()
    return job
