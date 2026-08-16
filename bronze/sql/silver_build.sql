-- ===========================================================================
-- SILVER BUILD — upserts against silver (1).dbml, v2 (12 Aug)
--
-- Project : aramco-finance-poc-c2a4
-- Location: me-central2
--
-- UPSERT SEMANTICS, not rebuild. Every statement below is a MERGE keyed on
-- the PK(s) from silver (1).dbml: unchanged rows are left alone, existing
-- rows are updated in place, new rows are inserted. Nothing is dropped or
-- truncated. This replaces the old CREATE OR REPLACE approach in
-- silver_coa.sql (superseded by this file) now that silver is being fed
-- incrementally rather than rebuilt from scratch each run.
--
-- dim_entity, dim_period, dim_account, dim_group_account, fact_trial_balance
-- and map_account_to_group ALREADY EXIST in BigQuery — no CREATE TABLE for
-- those, MERGE only (fact_trial_balance additionally gets one
-- ADD COLUMN IF NOT EXISTS for source_file, which the table predates).
-- fact_group_trial_balance, dim_ifrs_requirement and dim_required_document
-- are genuinely new, so they get CREATE TABLE IF NOT EXISTS ahead of their
-- MERGE.
--
-- fact_trial_balance is REWIRED here from the old bronze_trial_balance_raw
-- onto bronze_tb_raw (this pipeline's own table, now account-rows-only —
-- see tb.py). Verified before rewiring: the two sources' 990 account rows
-- are byte-identical, so this changes nothing about the figures — it adds
-- source_file lineage and a maintained pipeline behind the table. See that
-- section below for the composite-key join reasoning.
--
-- map_account_to_group is NOT touched by this file — it is Agent 3's
-- deliberately-empty output slot, and nothing here produces mapping rows.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- dim_entity — who submitted. Adds Aramco as a third row (is_group = true).
-- Not derived from any sheet — assigned at ingest, per the DBML note.
-- ---------------------------------------------------------------------------
MERGE `aramco-finance-poc-c2a4.silver.dim_entity` AS tgt
USING (
  SELECT * FROM UNNEST([
    STRUCT('2010'   AS entity_code, 'SABIC'        AS entity_name, '2010' AS ticker, 'consolidated_subsidiary'    AS consolidation_method, FALSE AS is_group),
    STRUCT('2380'   AS entity_code, 'Petro Rabigh'  AS entity_name, '2380' AS ticker, 'equity_accounted_associate' AS consolidation_method, FALSE AS is_group),
    STRUCT('ARAMCO' AS entity_code, 'Saudi Aramco'  AS entity_name, CAST(NULL AS STRING) AS ticker, 'parent'      AS consolidation_method, TRUE  AS is_group)
  ])
) AS src
ON tgt.entity_code = src.entity_code
WHEN MATCHED THEN UPDATE SET
  entity_name = src.entity_name,
  ticker = src.ticker,
  consolidation_method = src.consolidation_method,
  is_group = src.is_group
WHEN NOT MATCHED THEN INSERT (entity_code, entity_name, ticker, consolidation_method, is_group)
VALUES (src.entity_code, src.entity_name, src.ticker, src.consolidation_method, src.is_group);


-- ---------------------------------------------------------------------------
-- dim_period — when. Sourced from bronze_group_tb_raw's period_label set
-- (identical 9 quarters to bronze_tb_raw — verified in bronze.dbml) rather
-- than from bronze_tb_raw, so this MERGE has no dependency on the
-- deliberately-untouched fact_trial_balance build.
-- ---------------------------------------------------------------------------
MERGE `aramco-finance-poc-c2a4.silver.dim_period` AS tgt
USING (
  WITH parsed AS (
    SELECT
      period_label,
      CAST(REGEXP_EXTRACT(period_label, r'(\d{4})') AS INT64) AS year,
      CAST(REGEXP_EXTRACT(period_label, r'Q(\d)')   AS INT64) AS quarter
    FROM (SELECT DISTINCT period_label
          FROM `aramco-finance-poc-c2a4.bronze.bronze_group_tb_raw`)
  )
  SELECT
    CONCAT(CAST(year AS STRING), 'Q', CAST(quarter AS STRING)) AS period_key,
    period_label,
    year,
    quarter,
    ROW_NUMBER() OVER (ORDER BY year, quarter) AS sort_order,
    LAST_DAY(DATE(year, quarter * 3, 1))       AS period_end_date
  FROM parsed
) AS src
ON tgt.period_key = src.period_key
WHEN MATCHED THEN UPDATE SET
  period_label = src.period_label,
  year = src.year,
  quarter = src.quarter,
  sort_order = src.sort_order,
  period_end_date = src.period_end_date
WHEN NOT MATCHED THEN INSERT (period_key, period_label, year, quarter, sort_order, period_end_date)
VALUES (src.period_key, src.period_label, src.year, src.quarter, src.sort_order, src.period_end_date);


-- ---------------------------------------------------------------------------
-- dim_account — the affiliate charts. Keyed (entity_code, account_code) —
-- NOT optional: 42 codes appear in both charts and 15 mean different things.
-- Same derivation as the old silver_coa.sql, now as an upsert.
-- ---------------------------------------------------------------------------
MERGE `aramco-finance-poc-c2a4.silver.dim_account` AS tgt
USING (
  SELECT
    chart_scope                       AS entity_code,
    account                           AS account_code,
    TRIM(account_name)                AS account_name,
    CASE statement
      WHEN 'Balance sheet'    THEN 'BS'
      WHEN 'Income statement' THEN 'PL'
      ELSE ERROR(FORMAT("unexpected statement value: %s", statement))
    END                               AS statement_type,
    category,
    normal_balance,
    SUBSTR(account, 1, 1)             AS code_block
  FROM `aramco-finance-poc-c2a4.bronze.bronze_coa_raw`
  WHERE chart_scope IN ('2010', '2380')
) AS src
ON tgt.entity_code = src.entity_code AND tgt.account_code = src.account_code
WHEN MATCHED THEN UPDATE SET
  account_name = src.account_name,
  statement_type = src.statement_type,
  category = src.category,
  normal_balance = src.normal_balance,
  code_block = src.code_block
WHEN NOT MATCHED THEN INSERT (entity_code, account_code, account_name, statement_type, category, normal_balance, code_block)
VALUES (src.entity_code, src.account_code, src.account_name, src.statement_type, src.category, src.normal_balance, src.code_block);


-- ---------------------------------------------------------------------------
-- fact_trial_balance — the affiliate atom. Rewired from the old
-- bronze_trial_balance_raw onto bronze_tb_raw (this pipeline's own table).
-- Verified beforehand: bronze_tb_raw's 990 account rows are byte-identical
-- to what bronze_trial_balance_raw already had (EXCEPT DISTINCT both
-- directions, 0 rows differ) — this rewire changes nothing about the 990
-- existing figures, it only adds source_file lineage and a maintained
-- pipeline behind the table.
--
-- *** THE JOIN TO dim_account MUST USE BOTH entity_code AND account_code ***
-- account_code alone is not unique: 42 codes are shared between SABIC and
-- Petro Rabigh, 15 of which mean different things. Joining on account_code
-- alone silently misattributes balances and still returns a number that
-- looks plausible (join_checks.sql section C proves this: SABIC's 2024 Q1
-- revenue comes out exactly double under the wrong join).
--
-- ADD COLUMN IF NOT EXISTS, not a recreate: this table already existed
-- before source_file was added to the bronze contract.
-- ---------------------------------------------------------------------------
ALTER TABLE `aramco-finance-poc-c2a4.silver.fact_trial_balance`
  ADD COLUMN IF NOT EXISTS source_file STRING
  OPTIONS(description="Original workbook filename this row's bronze source row was read from. ADDED AT INGEST, propagated up from bronze_tb_raw.");

MERGE `aramco-finance-poc-c2a4.silver.fact_trial_balance` AS tgt
USING (
  SELECT
    d.entity_code                                                   AS entity_code,
    d.account_code                                                  AS account_code,
    p.period_key                                                    AS period_key,
    CAST(b.amount AS NUMERIC)                                       AS ledger_amount,
    CASE d.normal_balance
      WHEN 'Cr' THEN -CAST(b.amount AS NUMERIC)
      ELSE CAST(b.amount AS NUMERIC)
    END                                                              AS presentation_amount,
    'SAR'                                                            AS currency,
    'thousands'                                                      AS amount_unit,
    b.source_file                                                   AS source_file
  FROM `aramco-finance-poc-c2a4.bronze.bronze_tb_raw` b
  JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
       ON b.affiliate_code = d.entity_code AND b.account = d.account_code
  JOIN `aramco-finance-poc-c2a4.silver.dim_period`  p ON b.period_label = p.period_label
) AS src
ON tgt.entity_code = src.entity_code AND tgt.account_code = src.account_code AND tgt.period_key = src.period_key
WHEN MATCHED THEN UPDATE SET
  ledger_amount = src.ledger_amount,
  presentation_amount = src.presentation_amount,
  currency = src.currency,
  amount_unit = src.amount_unit,
  source_file = src.source_file
WHEN NOT MATCHED THEN INSERT (entity_code, account_code, period_key, ledger_amount, presentation_amount, currency, amount_unit, source_file)
VALUES (src.entity_code, src.account_code, src.period_key, src.ledger_amount, src.presentation_amount, src.currency, src.amount_unit, src.source_file);


-- ---------------------------------------------------------------------------
-- dim_group_account — the Aramco consolidation target chart. Keyed on
-- group_node. parent_group_node DERIVED via the last-two-digits->'00' rule
-- (verified 26/26; the 3-char-prefix rule only resolves 18/26 — do not
-- revert to it). Same derivation as the old silver_coa.sql, now an upsert.
-- ---------------------------------------------------------------------------
MERGE `aramco-finance-poc-c2a4.silver.dim_group_account` AS tgt
USING (
  SELECT
    account                    AS group_node,
    TRIM(account_name)         AS group_name,
    statement,
    category,
    normal_balance,
    CAST(level AS INT64)       AS level,
    CASE WHEN level = '2'
         THEN CONCAT(SUBSTR(account, 1, LENGTH(account) - 2), '00')
    END                        AS parent_group_node,
    source_reference
  FROM `aramco-finance-poc-c2a4.bronze.bronze_coa_raw`
  WHERE chart_scope = 'GROUP'
) AS src
ON tgt.group_node = src.group_node
WHEN MATCHED THEN UPDATE SET
  group_name = src.group_name,
  statement = src.statement,
  category = src.category,
  normal_balance = src.normal_balance,
  level = src.level,
  parent_group_node = src.parent_group_node,
  source_reference = src.source_reference
WHEN NOT MATCHED THEN INSERT (group_node, group_name, statement, category, normal_balance, level, parent_group_node, source_reference)
VALUES (src.group_node, src.group_name, src.statement, src.category, src.normal_balance, src.level, src.parent_group_node, src.source_reference);


-- ---------------------------------------------------------------------------
-- fact_group_trial_balance — the Aramco parent atom. NEW table, separate
-- from fact_trial_balance BY DECISION (not by omission — see the DBML note):
-- the two sides are not comparable until the affiliate->group mapping
-- bridge exists, and a shared table invites summing across both and
-- silently double counting while ignoring intercompany eliminations.
--
-- entity_code is a literal 'ARAMCO' today, kept as a real column (not
-- hardcoded into the query shape) so a second group-vocabulary submitter
-- needs no schema change. group_node joins straight to dim_group_account —
-- no mapping needed, this source is already in group vocabulary. Section
-- dividers are already dropped in bronze_group_tb_raw itself; the inner
-- join to dim_group_account additionally excludes the subtotal rows that
-- do land in bronze (blank account, matches nothing), so no separate
-- row-kind filter is needed here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.fact_group_trial_balance`
(
  entity_code          STRING  OPTIONS(description="FK to dim_entity. Always ARAMCO today. PK part 1."),
  group_node           STRING  OPTIONS(description="FK to dim_group_account. PK part 2. Single-column key — G-codes are globally unique, unlike affiliate codes."),
  period_key           STRING  OPTIONS(description="FK to dim_period. PK part 3."),
  ledger_amount        NUMERIC OPTIONS(description="Signed debit-positive / credit-negative. All 9 period columns sum to exactly 0 independently of the affiliate TBs."),
  presentation_amount  NUMERIC OPTIONS(description="ledger_amount sign-flipped for Cr-normal nodes, same convention as fact_trial_balance."),
  currency             STRING  OPTIONS(description="SAR for all rows today."),
  amount_unit          STRING  OPTIONS(description="thousands.")
)
OPTIONS(description="Saudi Aramco's own (parent-only, GROUP CORE OPERATIONS, no affiliates) quarterly trial balance, in G-node vocabulary. 450 rows = 50 balance-carrying nodes x 9 periods. Kept separate from fact_trial_balance until the affiliate-to-group mapping bridge exists — a UNION across both facts is a deliberate action, not an accident. THIS IS PARENT-ONLY, NOT CONSOLIDATED: group total = this + mapped affiliates - eliminations.");

MERGE `aramco-finance-poc-c2a4.silver.fact_group_trial_balance` AS tgt
USING (
  SELECT
    'ARAMCO'                                                        AS entity_code,
    b.account                                                       AS group_node,
    p.period_key                                                    AS period_key,
    CAST(b.amount AS NUMERIC)                                       AS ledger_amount,
    CASE g.normal_balance
      WHEN 'Cr' THEN -CAST(b.amount AS NUMERIC)
      ELSE CAST(b.amount AS NUMERIC)
    END                                                              AS presentation_amount,
    'SAR'                                                            AS currency,
    'thousands'                                                      AS amount_unit
  FROM `aramco-finance-poc-c2a4.bronze.bronze_group_tb_raw` b
  JOIN `aramco-finance-poc-c2a4.silver.dim_group_account`  g ON b.account = g.group_node
  JOIN `aramco-finance-poc-c2a4.silver.dim_period`          p ON b.period_label = p.period_label
) AS src
ON tgt.entity_code = src.entity_code AND tgt.group_node = src.group_node AND tgt.period_key = src.period_key
WHEN MATCHED THEN UPDATE SET
  ledger_amount = src.ledger_amount,
  presentation_amount = src.presentation_amount,
  currency = src.currency,
  amount_unit = src.amount_unit
WHEN NOT MATCHED THEN INSERT (entity_code, group_node, period_key, ledger_amount, presentation_amount, currency, amount_unit)
VALUES (src.entity_code, src.group_node, src.period_key, src.ledger_amount, src.presentation_amount, src.currency, src.amount_unit);


-- ---------------------------------------------------------------------------
-- dim_ifrs_standard — the standard-level parent of dim_ifrs_requirement.
-- NEW table, keyed on standard_code.
--
-- Kept separate rather than denormalised onto each requirement row because
-- disclosure_summary is a paragraph with only three distinct values, and
-- repeating it across five requirement rows per standard invites drift. It
-- also gives an agent somewhere to ask "which standards are in scope?"
-- without reading fifteen requirement rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_ifrs_standard`
(
  standard_code      STRING OPTIONS(description="PK. Short stable code: 'IFRS 15', 'IAS 24', 'IAS 16'. Chosen as the key over the full title because a standard can be retitled but its code will not change."),
  standard_title     STRING OPTIONS(description="Full official title, e.g. 'IFRS 15 Revenue from Contracts with Customers'."),
  disclosure_summary STRING OPTIONS(description="Paragraph summarising what the standard requires to be disclosed.")
)
OPTIONS(description="One row per IFRS/IAS standard in scope. 3 rows. Parent of dim_ifrs_requirement via standard_code. Agent 4 (Disclosure Drafting) reference data.");

MERGE `aramco-finance-poc-c2a4.silver.dim_ifrs_standard` AS tgt
USING (
  SELECT standard_code, standard_title, disclosure_summary
  FROM `aramco-finance-poc-c2a4.bronze.bronze_ifrs_standard_raw`
) AS src
ON tgt.standard_code = src.standard_code
WHEN MATCHED THEN UPDATE SET
  standard_title = src.standard_title,
  disclosure_summary = src.disclosure_summary
WHEN NOT MATCHED THEN INSERT (standard_code, standard_title, disclosure_summary)
VALUES (src.standard_code, src.standard_title, src.disclosure_summary);


-- ---------------------------------------------------------------------------
-- dim_ifrs_requirement — Agent 4 reference data.
--
-- CHANGED THIS PASS: gains standard_code (FK to dim_ifrs_standard),
-- evidence_type and check_guidance, and its key moves from
-- (standard, req) — where `standard` is the long name — to
-- (standard_code, req). The code is the stable identifier; the long title is
-- a descriptive attribute that could be revised.
--
-- *** THE BACKFILL BELOW IS NOT OPTIONAL — READ BEFORE REORDERING ***
-- The table already holds 15 rows whose standard_code is NULL, because the
-- column did not exist when they were written. If the MERGE keyed on
-- standard_code ran first, NULL would match nothing, every row would fall to
-- WHEN NOT MATCHED, and the table would end up with 30 rows: the 15 originals
-- orphaned alongside 15 new ones. Row-count checks would catch it, but only
-- after the damage.
--
-- The backfill is idempotent — once standard_code is populated the WHERE
-- clause matches nothing and it becomes a no-op — so it is safe to leave in
-- place permanently rather than run once by hand and forget it was needed.
-- ---------------------------------------------------------------------------
ALTER TABLE `aramco-finance-poc-c2a4.silver.dim_ifrs_requirement`
  ADD COLUMN IF NOT EXISTS standard_code STRING
    OPTIONS(description="PK part 1. FK to dim_ifrs_standard. Short stable code, e.g. 'IFRS 15'."),
  ADD COLUMN IF NOT EXISTS evidence_type STRING
    OPTIONS(description="narrative | table_structure | both. What kind of evidence satisfies this requirement — lets the agent decide whether to inspect prose or table shape."),
  ADD COLUMN IF NOT EXISTS check_guidance STRING
    OPTIONS(description="The explicit rule for what counts as meeting this requirement. Makes the rubric machine-actionable rather than a list of topics.");

-- One-time backfill, self-neutralising. Joins on the OLD key, which is still
-- populated and still unique, to give every existing row its standard_code.
UPDATE `aramco-finance-poc-c2a4.silver.dim_ifrs_requirement` t
SET standard_code = b.standard_code
FROM `aramco-finance-poc-c2a4.bronze.bronze_ifrs_rubric_raw` b
WHERE t.standard = b.standard AND t.req = b.req AND t.standard_code IS NULL;

MERGE `aramco-finance-poc-c2a4.silver.dim_ifrs_requirement` AS tgt
USING (
  SELECT standard_code, req, standard, requirement AS requirement_text,
         evidence_type, check_guidance
  FROM `aramco-finance-poc-c2a4.bronze.bronze_ifrs_rubric_raw`
) AS src
ON tgt.standard_code = src.standard_code AND tgt.req = src.req
WHEN MATCHED THEN UPDATE SET
  standard = src.standard,
  requirement_text = src.requirement_text,
  evidence_type = src.evidence_type,
  check_guidance = src.check_guidance
WHEN NOT MATCHED THEN INSERT (standard_code, req, standard, requirement_text, evidence_type, check_guidance)
VALUES (src.standard_code, src.req, src.standard, src.requirement_text, src.evidence_type, src.check_guidance);


-- ---------------------------------------------------------------------------
-- dim_entity_context — reporting-entity metadata. NEW table, keyed on
-- context_key.
--
-- Deliberately key-value (EAV) shaped. That is normally an anti-pattern in a
-- warehouse — you cannot type the columns, cannot constrain the values, and
-- every query has to pivot — and it is the right call here only because all
-- three of these hold:
--   1. the attributes are open-ended narrative, not a fixed schema
--   2. exactly one entity is described, so nothing is compared or aggregated
--   3. the consumer is an agent reading prose, not a tool summing a measure
-- If a second entity ever submits context, the key becomes
-- (entity_code, context_key) and the case for real columns gets stronger.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_entity_context`
(
  context_key   STRING OPTIONS(description="PK. Metadata key, e.g. 'reporting_entity', 'presentation_currency', 'reportable_segments'."),
  context_value STRING OPTIONS(description="Free-text value. Narrative reference an agent reads to ground a disclosure or interpret a figure — never aggregated, never cast.")
)
OPTIONS(description="Reporting-entity metadata as key-value pairs: entity, period, currency, units, segments, framework, sign convention. 13 rows. Describes the Aramco consolidated group. Deliberately connected to nothing else in silver.");

MERGE `aramco-finance-poc-c2a4.silver.dim_entity_context` AS tgt
USING (
  SELECT context_key, context_value
  FROM `aramco-finance-poc-c2a4.bronze.bronze_entity_context_raw`
) AS src
ON tgt.context_key = src.context_key
WHEN MATCHED THEN UPDATE SET context_value = src.context_value
WHEN NOT MATCHED THEN INSERT (context_key, context_value)
VALUES (src.context_key, src.context_value);


-- ---------------------------------------------------------------------------
-- dim_required_document — Agent 2 reference data. NEW table, keyed on item.
-- Also connected to nothing: the validation RESULT (manifest status per
-- entity per item) is Agent 2's own output, not modelled here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.dim_required_document`
(
  item             STRING OPTIONS(description="01..14. PK."),
  document         STRING OPTIONS(description="Required document name, e.g. 'Signed trial balance'."),
  required         STRING OPTIONS(description="Whether mandatory for a submission."),
  applies_to       STRING OPTIONS(description="Which affiliates this applies to."),
  expected_format  STRING OPTIONS(description="Expected file format, e.g. 'xlsx / SAP BPC export'."),
  description      STRING OPTIONS(description="What the document should contain.")
)
OPTIONS(description="Required-documents master for an affiliate submission pack — the checklist a submission is validated against. 14 rows. Deliberately connected to nothing else in silver; the validation result is Agent 2's own output.");

MERGE `aramco-finance-poc-c2a4.silver.dim_required_document` AS tgt
USING (
  SELECT item, document, required, applies_to, expected_format, description
  FROM `aramco-finance-poc-c2a4.bronze.bronze_checklist_raw`
) AS src
ON tgt.item = src.item
WHEN MATCHED THEN UPDATE SET
  document = src.document,
  required = src.required,
  applies_to = src.applies_to,
  expected_format = src.expected_format,
  description = src.description
WHEN NOT MATCHED THEN INSERT (item, document, required, applies_to, expected_format, description)
VALUES (src.item, src.document, src.required, src.applies_to, src.expected_format, src.description);
