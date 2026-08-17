-- ===========================================================================
-- bronze.bronze_ifrs_rubric_raw
--
-- FIXED DDL — hand-maintained, executed by scripts/push_to_bq.py's
-- ensure_table() before every load (CREATE TABLE IF NOT EXISTS is
-- idempotent). This file is now the schema's source of truth: to add,
-- rename, or retype a column, edit this file — the code no longer
-- derives or evolves the schema in Python.
--
-- Project  : aramco-finance-poc-c2a4
-- Dataset  : bronze          (location me-central2 - Dammam, IMMUTABLE)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.bronze_ifrs_rubric_raw`
(
  standard STRING OPTIONS(description="The IFRS/IAS standard's full name, e.g. 'IFRS 15 Revenue', 'IAS 24 Related party', 'IAS 16 PP&E'. A descriptive attribute — join on standard_code, not on this."),
  req STRING OPTIONS(description="Requirement code within the standard, R1..R5."),
  requirement STRING OPTIONS(description="The disclosure requirement text. Agent 4 (Disclosure Drafting) reference data — this is what a note is scored AGAINST, not financial data itself."),
  standard_code STRING OPTIONS(description="Short stable code for the standard: 'IFRS 15', 'IAS 24', 'IAS 16'. Joins to bronze_ifrs_standard_raw.standard_code. Preferred over the full name as a key because a standard can be retitled but its code will not change."),
  evidence_type STRING OPTIONS(description="What kind of evidence satisfies this requirement: 'narrative' (prose in the note), 'table_structure' (the shape of a table), or 'both'. Lets the disclosure agent decide whether to inspect wording or table columns."),
  check_guidance STRING OPTIONS(description="The explicit rule for what counts as meeting this requirement, e.g. 'Revenue table must break down revenue into product categories, not only a total.' This is what makes the rubric machine-actionable rather than just a list of topics."),
  source_file STRING OPTIONS(description="Original workbook filename this row was read from (e.g. 'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in the sheet. Lets a row be traced back to the exact file it came from when a workbook is re-shared or re-versioned.")
)
OPTIONS(
  description="IFRS disclosure requirements rubric — Agent 4 (Disclosure Drafting) reference data. 15 rows = 3 standards x 5 requirements. Sourced from ifrs_requirements_updated.csv; the 'Compliant version'/'Gap version'/'Gap detail' columns of the original workbook are the demo answer key and remain deliberately excluded. evidence_type and check_guidance make the rubric machine-actionable. standard_code joins to bronze_ifrs_standard_raw."
);
