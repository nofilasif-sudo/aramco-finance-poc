-- ===========================================================================
-- silver.dim_ifrs_requirement
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

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_ifrs_requirement`
(
  standard STRING OPTIONS(description="'IFRS 15 Revenue', 'IAS 24 Related party', 'IAS 16 PP&E'. PK part 1."),
  req STRING OPTIONS(description="R1..R5. PK part 2."),
  requirement_text STRING OPTIONS(description="The disclosure requirement text."),
  standard_code STRING OPTIONS(description="PK part 1. FK to dim_ifrs_standard. Short stable code, e.g. 'IFRS 15'."),
  evidence_type STRING OPTIONS(description="narrative | table_structure | both. What kind of evidence satisfies this requirement — lets the agent decide whether to inspect prose or table shape."),
  check_guidance STRING OPTIONS(description="The explicit rule for what counts as meeting this requirement. Makes the rubric machine-actionable rather than a list of topics.")
)
OPTIONS(
  description="IFRS disclosure requirements Agent 4 (Disclosure Drafting) scores notes against. 15 rows = 3 standards x 5 requirements. Deliberately connected to nothing else in silver."
);
