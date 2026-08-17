-- ===========================================================================
-- bronze.bronze_ifrs_standard_raw
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.bronze_ifrs_standard_raw`
(
  standard_code STRING OPTIONS(description="Short stable code: 'IFRS 15', 'IAS 24', 'IAS 16'. Primary key, and the value bronze_ifrs_rubric_raw.standard_code joins to."),
  standard_title STRING OPTIONS(description="Full official title, e.g. 'IFRS 15 Revenue from Contracts with Customers'."),
  disclosure_summary STRING OPTIONS(description="Paragraph summarising what the standard requires to be disclosed. Held once per standard rather than repeated across its five requirement rows."),
  source_file STRING OPTIONS(description="Original workbook filename this row was read from (e.g. 'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in the sheet. Lets a row be traced back to the exact file it came from when a workbook is re-shared or re-versioned.")
)
OPTIONS(
  description="One row per IFRS/IAS standard in scope — the standard-level parent of bronze_ifrs_rubric_raw. 3 rows: IFRS 15, IAS 24, IAS 16. Separate from the rubric because disclosure_summary is a paragraph with only three distinct values, and repeating it across five requirement rows per standard invites drift."
);
