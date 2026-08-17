-- ===========================================================================
-- bronze.bronze_checklist_raw
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.bronze_checklist_raw`
(
  item STRING OPTIONS(description="Checklist item number, 01..14."),
  document STRING OPTIONS(description="Required document name, e.g. 'Signed trial balance', 'Intercompany reconciliation'."),
  required STRING OPTIONS(description="Whether this document is mandatory for a submission ('Yes')."),
  applies_to STRING OPTIONS(description="Which affiliates this requirement applies to, e.g. 'All affiliates'."),
  expected_format STRING OPTIONS(description="Expected file format, e.g. 'xlsx / SAP BPC export', 'pdf (signed)'."),
  description STRING OPTIONS(description="What the document should contain."),
  source_file STRING OPTIONS(description="Original workbook filename this row was read from (e.g. 'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in the sheet. Lets a row be traced back to the exact file it came from when a workbook is re-shared or re-versioned.")
)
OPTIONS(
  description="Required-documents checklist for an affiliate submission pack — Agent 2 reference data. 14 rows. CHECKLIST TAB ONLY: the Manifest-*/IC-confirmations tabs in the same workbook (submission status, intercompany anomaly A6) are Agent 2's own ingestion and are not part of this table."
);
