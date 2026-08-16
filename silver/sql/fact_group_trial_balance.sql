-- ===========================================================================
-- silver.fact_group_trial_balance
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.fact_group_trial_balance`
(
  entity_code STRING OPTIONS(description="FK to dim_entity. Always ARAMCO today. PK part 1."),
  group_node STRING OPTIONS(description="FK to dim_group_account. PK part 2. Single-column key — G-codes are globally unique, unlike affiliate codes."),
  period_key STRING OPTIONS(description="FK to dim_period. PK part 3."),
  ledger_amount NUMERIC OPTIONS(description="Signed debit-positive / credit-negative. All 9 period columns sum to exactly 0 independently of the affiliate TBs."),
  presentation_amount NUMERIC OPTIONS(description="ledger_amount sign-flipped for Cr-normal nodes, same convention as fact_trial_balance."),
  currency STRING OPTIONS(description="SAR for all rows today."),
  amount_unit STRING OPTIONS(description="thousands.")
)
OPTIONS(
  description="Saudi Aramco's own (parent-only, GROUP CORE OPERATIONS, no affiliates) quarterly trial balance, in G-node vocabulary. 450 rows = 50 balance-carrying nodes x 9 periods. Kept separate from fact_trial_balance until the affiliate-to-group mapping bridge exists — a UNION across both facts is a deliberate action, not an accident. THIS IS PARENT-ONLY, NOT CONSOLIDATED: group total = this + mapped affiliates - eliminations."
);
