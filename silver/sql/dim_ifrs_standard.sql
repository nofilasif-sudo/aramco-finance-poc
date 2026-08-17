-- ===========================================================================
-- silver.dim_ifrs_standard
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_ifrs_standard`
(
  standard_code STRING OPTIONS(description="PK. Short stable code: 'IFRS 15', 'IAS 24', 'IAS 16'. Chosen as the key over the full title because a standard can be retitled but its code will not change."),
  standard_title STRING OPTIONS(description="Full official title, e.g. 'IFRS 15 Revenue from Contracts with Customers'."),
  disclosure_summary STRING OPTIONS(description="Paragraph summarising what the standard requires to be disclosed.")
)
OPTIONS(
  description="One row per IFRS/IAS standard in scope. 3 rows. Parent of dim_ifrs_requirement via standard_code. Agent 4 (Disclosure Drafting) reference data."
);
