-- ===========================================================================
-- silver.dim_group_account
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_group_account`
(
  group_node STRING OPTIONS(description="PK. G-prefixed 5-digit code, e.g. 'G52000'. Separate namespace from the affiliate 4-digit codes by design — a group node can never be confused with an affiliate account in a query or an agent response."),
  group_name STRING OPTIONS(description="e.g. 'Producing & manufacturing', 'Purchases'. Trimmed: the source indents level-2 names."),
  statement STRING OPTIONS(description="Balance sheet | Income statement. Left in the CoA vocabulary — unlike dim_account this table never joins to the trial balances."),
  category STRING OPTIONS(description="FS caption group."),
  normal_balance STRING OPTIONS(description="Dr or Cr."),
  level INT64 OPTIONS(description="1 = face caption (52 nodes), 2 = analytical sub-account (26 nodes)."),
  parent_group_node STRING OPTIONS(description="Self-FK to group_node. DERIVED, not in the source: the child code with its last two digits reset to '00'. NULL for level-1 nodes. Without this the hierarchy is decorative and nothing rolls up."),
  source_reference STRING OPTIONS(description="Where the node traces to in Aramco's Q1-2026 condensed interim report, e.g. 'Balance sheet; Note 5'. Supports disclosure traceability.")
)
OPTIONS(
  description="Aramco Group consolidation target chart. 78 rows = 52 level-1 + 26 level-2. Target vocabulary only — never receives balances. Aramco presents operating costs BY NATURE while the affiliates present BY FUNCTION, which is why the affiliate-to-group bridge is a judgement (Agent 3's scored output) and not a join. Rebuilt from bronze.bronze_coa_raw."
);
