-- ===========================================================================
-- bronze.bronze_coa_raw
--
-- EXPORTED FROM THE LIVE TABLE on 2026-08-06 via
--   SELECT ddl FROM `<project>.bronze.INFORMATION_SCHEMA.TABLES`
--   WHERE table_name = 'bronze_coa_raw'
--
-- This is what BigQuery actually has, not a reconstruction. The table was
-- created by scripts/push_to_bq.py (which builds the schema from
-- bronze_ingest.cloud.bq_schema()), so treat that Python as the source of
-- truth and this file as the reproducible artifact.
--
-- Project  : aramco-finance-poc-c2a4
-- Dataset  : bronze          (location me-central2 - Dammam, IMMUTABLE)
-- Rows     : 188 = 78 GROUP + 66 SABIC (2010) + 44 Petro Rabigh (2380)
-- ===========================================================================

CREATE TABLE `aramco-finance-poc-c2a4.bronze.bronze_coa_raw`
(
  chart_scope STRING OPTIONS(description="GROUP, 2010 or 2380. ADDED AT INGEST from the worksheet tab name — it is not a column in the sheet. Essential to every join: the two affiliate charts share 42 four-digit codes, 15 of which mean different things (1120 = plant & machinery for 2010 but refinery plant for 2380). Never join on account alone."),
  account STRING OPTIONS(description="Account code as text. Affiliates use 4-digit ledger codes; the Group chart uses G-prefixed 5-digit nodes (G11000). Deliberately non-colliding namespaces. Never empty."),
  account_name STRING OPTIONS(description="Account description as written, leading spaces preserved — indentation marks level-2 sub-accounts on the Group chart and is data, not formatting."),
  statement STRING OPTIONS(description="Balance sheet | Income statement. Note the trial balances use BS/PL for the same concept; bronze does not harmonise them."),
  category STRING OPTIONS(description="FS caption group, e.g. 'Non-current assets', 'Cost of sales'. 13 distinct values across the affiliate charts."),
  normal_balance STRING OPTIONS(description="Dr or Cr — the side this account normally sits on. Supports wrong-side anomaly detection; not used for sign correction."),
  level STRING OPTIONS(description="Group chart only; empty for affiliates. 1 = face caption (52 nodes), 2 = analytical sub-account (26 nodes). Text, so '1' not 1.0."),
  source_reference STRING OPTIONS(description="Group chart only; empty for affiliates. Where the node traces to in Aramco's Q1-2026 condensed interim report, e.g. 'Balance sheet; Note 5'. Supports disclosure traceability.")
)
OPTIONS(
  description="Charts of accounts for both affiliates and the Group, stacked. 188 rows = 78 GROUP + 66 SABIC + 44 Petro Rabigh, matching each tab's own 'Total accounts' footer exactly. All columns STRING per the bronze contract. Section-divider rows are dropped at ingest (their captions are fully duplicated by the category column). No relationships enforced at this layer: the affiliate-to-Group mapping does not exist in this pack — it is Agent 3's output, produced with a confidence score, not an input. Source: PoC_Charts_of_Accounts.xlsx. SYNTHETIC data calibrated to public results; not Aramco actuals."
);
