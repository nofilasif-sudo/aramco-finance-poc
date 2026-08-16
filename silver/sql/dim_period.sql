-- ===========================================================================
-- silver.dim_period
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_period`
(
  period_key STRING NOT NULL OPTIONS(description="Sortable surrogate, e.g. \"2024Q1\". Generated, not from source."),
  period_label STRING OPTIONS(description="The raw header string from the sheet: \"2024 Q1\". Keep it as-is for human/agent reference."),
  year INT64 OPTIONS(description="2024, 2025, 2026"),
  quarter INT64 OPTIONS(description="1..4"),
  sort_order INT64 OPTIONS(description="Dense integer 1..9. Makes \"last 5 quarters\" a BETWEEN, and prior-period a sort_order - 1."),
  period_end_date DATE OPTIONS(description="2024-03-31, 2024-06-30, ... Real calendar quarters (Gregorian). Confirm this holds for other affiliates."),
  PRIMARY KEY (period_key) NOT ENFORCED
)
OPTIONS(
  description="9 rows today: 2024 Q1 -> 2026 Q1. Time is data, not schema — new quarters are new rows, never new columns."
);
