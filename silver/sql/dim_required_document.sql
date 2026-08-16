-- ===========================================================================
-- silver.dim_required_document
--
-- FIXED DDL — hand-maintained, executed by scripts/build_silver.py before
-- the MERGE statements in sql/silver_build.sql (CREATE TABLE IF NOT EXISTS
-- is idempotent). This file is now the schema's source of truth: to add,
-- rename, or retype a column, edit this file. Baseline captured from the
-- live table on 2026-08-17 via INFORMATION_SCHEMA.TABLES.
--
-- Project  : aramco-finance-poc-c2a4
-- Dataset  : silver
-- ===========================================================================

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_required_document`
(
  item STRING OPTIONS(description="01..14. PK."),
  document STRING OPTIONS(description="Required document name, e.g. 'Signed trial balance'."),
  required STRING OPTIONS(description="Whether mandatory for a submission."),
  applies_to STRING OPTIONS(description="Which affiliates this applies to."),
  expected_format STRING OPTIONS(description="Expected file format, e.g. 'xlsx / SAP BPC export'."),
  description STRING OPTIONS(description="What the document should contain.")
)
OPTIONS(
  description="Required-documents master for an affiliate submission pack — the checklist a submission is validated against. 14 rows. Deliberately connected to nothing else in silver; the validation result is Agent 2's own output."
);
