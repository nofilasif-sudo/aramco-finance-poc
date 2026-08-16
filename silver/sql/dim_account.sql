-- ===========================================================================
-- silver.dim_account
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_account`
(
  entity_code STRING OPTIONS(description="FK to dim_entity. 2010 (SABIC) or 2380 (Petro Rabigh). PK part 1 — never join on account_code alone."),
  account_code STRING OPTIONS(description="Affiliate 4-digit ledger code, e.g. '1100'. PK part 2. Unique only WITHIN an entity."),
  account_name STRING OPTIONS(description="e.g. 'Land', 'Revenue - Petrochemicals segment'."),
  statement_type STRING OPTIONS(description="BS or PL. Normalised here: the CoA says 'Balance sheet'/'Income statement', the trial balances say BS/PL. Governs additivity — PL is discrete quarterly activity and may be summed across periods; BS is a quarter-end balance and must not be."),
  category STRING OPTIONS(description="FS caption group, e.g. 'Non-current assets', 'Cost of sales'. 13 distinct values."),
  normal_balance STRING OPTIONS(description="Dr or Cr. Drives the sign flip into presentation_amount and enables wrong-side anomaly detection."),
  code_block STRING OPTIONS(description="First digit of account_code. DERIVED, not in source. 1=assets 2=liabilities 3=equity 4=revenue 5=cost of sales 6=operating expenses and other operating income/(expense) 7=finance income/(costs) and non-operating 8=zakat/income tax and discontinued operations.")
)
OPTIONS(
  description="Affiliate chart of accounts. 110 rows = 66 SABIC + 44 Petro Rabigh. Composite key (entity_code, account_code) — 42 codes are shared across the two charts and 15 denote different accounts. Rebuilt from bronze.bronze_coa_raw."
);
