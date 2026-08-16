-- ===========================================================================
-- silver.dim_entity
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_entity`
(
  entity_code STRING NOT NULL OPTIONS(description="Tadawul ticker used as the natural key. 2010 = SABIC, 2380 = Petro Rabigh."),
  entity_name STRING OPTIONS(description="e.g. \"Saudi Basic Industries Corporation\""),
  ticker STRING OPTIONS(description="e.g. \"2010\""),
  consolidation_method STRING OPTIONS(description="consolidated_subsidiary | equity_accounted_associate. SABIC is line-by-line consolidated; Petro Rabigh is equity-accounted."),
  is_group BOOL OPTIONS(description="FALSE for affiliates. Reserved TRUE for an Aramco parent-level submission, which does not exist yet but will."),
  PRIMARY KEY (entity_code) NOT ENFORCED
)
OPTIONS(
  description="2 rows today: 2010 (SABIC) and 2380 (Petro Rabigh). Source: the two TB tab names — not a column in the sheets, assigned at ingest."
);
