-- ===========================================================================
-- silver.dim_entity_context
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_entity_context`
(
  context_key STRING OPTIONS(description="PK. Metadata key, e.g. 'reporting_entity', 'presentation_currency', 'reportable_segments'."),
  context_value STRING OPTIONS(description="Free-text value. Narrative reference an agent reads to ground a disclosure or interpret a figure — never aggregated, never cast.")
)
OPTIONS(
  description="Reporting-entity metadata as key-value pairs: entity, period, currency, units, segments, framework, sign convention. 13 rows. Describes the Aramco consolidated group. Deliberately connected to nothing else in silver."
);
