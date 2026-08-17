-- ===========================================================================
-- bronze.bronze_entity_context_raw
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.bronze_entity_context_raw`
(
  context_key STRING OPTIONS(description="Metadata key, e.g. 'reporting_entity', 'presentation_currency', 'reportable_segments', 'negative_convention'. Primary key."),
  context_value STRING OPTIONS(description="Free-text value. Narrative reference an agent reads to ground a disclosure or interpret a figure — never aggregated, never cast."),
  source_file STRING OPTIONS(description="Original workbook filename this row was read from (e.g. 'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in the sheet. Lets a row be traced back to the exact file it came from when a workbook is re-shared or re-versioned.")
)
OPTIONS(
  description="Reporting-entity metadata as key-value pairs: entity, period, currency, units, segments, reporting framework, sign convention. 13 rows. Deliberately key-value (EAV) shaped because the attributes are open-ended narrative, there is exactly one entity described, and the consumer is an agent reading prose rather than a tool aggregating a measure. Source: entity_context (1).csv — a shorter 10-key file of the same name exists and is NOT the authoritative one."
);
