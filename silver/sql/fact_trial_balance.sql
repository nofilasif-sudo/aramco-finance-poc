-- ===========================================================================
-- silver.fact_trial_balance
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.fact_trial_balance`
(
  entity_code STRING NOT NULL OPTIONS(description="PK part 1. FK to dim_entity."),
  account_code STRING NOT NULL OPTIONS(description="PK part 2. Composite FK to dim_account with entity_code (dim_account not yet built)."),
  period_key STRING NOT NULL OPTIONS(description="PK part 3. FK to dim_period."),
  ledger_amount NUMERIC OPTIONS(description="Source value, signed debit-positive / credit-negative. This is the column that proves to nil — all entity-period columns sum to exactly 0."),
  presentation_amount NUMERIC OPTIONS(description="ledger_amount sign-flipped for Cr-normal accounts, so revenue reads positive. Never use this for the balance check — it will not sum to zero."),
  currency STRING OPTIONS(description="SAR for all rows today. A real column, not a suffix in the measure name."),
  amount_unit STRING OPTIONS(description="thousands. A real column, so the agent can narrate correctly (26,150,000 in thousands = SAR 26.15 billion)."),
  source_file STRING OPTIONS(description="Original workbook filename this row's bronze source row was read from. ADDED AT INGEST, propagated up from bronze_tb_raw."),
  PRIMARY KEY (entity_code, account_code, period_key) NOT ENFORCED,
  FOREIGN KEY (entity_code) REFERENCES `aramco-finance-poc-c2a4.silver.dim_entity`(entity_code) NOT ENFORCED,
  FOREIGN KEY (period_key) REFERENCES `aramco-finance-poc-c2a4.silver.dim_period`(period_key) NOT ENFORCED
)
OPTIONS(
  description="Grain: one row per entity x account x period. 990 rows today (66+44 accounts x 9 quarters). NO PRE-AGGREGATION, NO DERIVED MEASURES — no QoQ, no YoY, no % of revenue. Roll-ups belong in views if needed."
);
