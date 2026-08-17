-- ===========================================================================
-- bronze.bronze_group_tb_raw
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.bronze_group_tb_raw`
(
  account STRING OPTIONS(description="The G-prefixed 5-digit Group node code, e.g. 'G11000'. Section header rows land here too."),
  group_node STRING OPTIONS(description="The Group node NAME, e.g. 'Property, plant & equipment (net)'. EXTRA COLUMN vs bronze_tb_raw — this sheet carries both the code (in `account`) and the name (here); silver decides what to do with them."),
  type STRING OPTIONS(description="LONG FORM here — 'Balance sheet' / 'Income statement' — NOT the affiliate TB's BS/PL abbreviations for the same concept. Bronze does not harmonise vocabularies across sources."),
  category STRING OPTIONS(description="FS caption group."),
  period_label STRING OPTIONS(description="Raw header string, e.g. '2024 Q1'. Same 9 quarters as bronze_tb_raw."),
  amount STRING OPTIONS(description="Signed balance for this node and period, kept as text. Aramco GROUP CORE OPERATIONS ONLY — no affiliates in this figure."),
  source_file STRING OPTIONS(description="Original workbook filename this row was read from (e.g. 'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in the sheet. Lets a row be traced back to the exact file it came from when a workbook is re-shared or re-versioned.")
)
OPTIONS(
  description="Saudi Aramco's own (parent-only, GROUP CORE OPERATIONS, no affiliates) quarterly trial balance in G-node vocabulary, unpivoted the same way as bronze_tb_raw. 531 rows = 59 account/subtotal rows x 9 periods (68 body rows minus 9 dropped section dividers, verified redundant with category). Refuses to land unless every period column proves to nil. All columns STRING. SYNTHETIC data."
);
