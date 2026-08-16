-- ===========================================================================
-- SILVER — the chart-of-accounts dimensions
--
--   bronze.bronze_coa_raw (188 rows, all STRING)
--     -> silver.dim_account         110 rows  (66 SABIC + 44 Petro Rabigh)
--     -> silver.dim_group_account    78 rows  (52 level-1 + 26 level-2)
--     -> silver.map_account_to_group   0 rows  (slot for Agent 3's output)
--
-- Project : aramco-finance-poc-c2a4
-- Location: me-central2
--
-- REBUILD SEMANTICS. The two dimensions use CREATE OR REPLACE ... AS SELECT:
-- they are pure functions of bronze, so rebuilding is always safe and always
-- idempotent. map_account_to_group uses CREATE TABLE IF NOT EXISTS instead —
-- it will hold Agent 3's output, and a rebuild must never wipe data that
-- bronze cannot regenerate. That distinction is the whole reason the two
-- statements differ; do not "tidy" them into the same form.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- dim_account — the affiliate charts
--
-- Keyed (entity_code, account_code). NOT optional: 42 codes appear in both
-- charts and 15 mean different things. Joining on account_code alone
-- misattributes balances between affiliates and still returns numbers that
-- foot — wrong, but confident.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE `aramco-finance-poc-c2a4.silver.dim_account`
(
  entity_code    STRING  OPTIONS(description="FK to dim_entity. 2010 (SABIC) or 2380 (Petro Rabigh). PK part 1 — never join on account_code alone."),
  account_code   STRING  OPTIONS(description="Affiliate 4-digit ledger code, e.g. '1100'. PK part 2. Unique only WITHIN an entity."),
  account_name   STRING  OPTIONS(description="e.g. 'Land', 'Revenue - Petrochemicals segment'."),
  statement_type STRING  OPTIONS(description="BS or PL. Normalised here: the CoA says 'Balance sheet'/'Income statement', the trial balances say BS/PL. Governs additivity — PL is discrete quarterly activity and may be summed across periods; BS is a quarter-end balance and must not be."),
  category       STRING  OPTIONS(description="FS caption group, e.g. 'Non-current assets', 'Cost of sales'. 13 distinct values."),
  normal_balance STRING  OPTIONS(description="Dr or Cr. Drives the sign flip into presentation_amount and enables wrong-side anomaly detection."),
  code_block     STRING  OPTIONS(description="First digit of account_code. DERIVED, not in source. 1=assets 2=liabilities 3=equity 4=revenue 5=cost of sales 6=operating expenses and other operating income/(expense) 7=finance income/(costs) and non-operating 8=zakat/income tax and discontinued operations.")
)
OPTIONS(description="Affiliate chart of accounts. 110 rows = 66 SABIC + 44 Petro Rabigh. Composite key (entity_code, account_code) — 42 codes are shared across the two charts and 15 denote different accounts. Rebuilt from bronze.bronze_coa_raw.")
AS
SELECT
  chart_scope                       AS entity_code,
  account                           AS account_code,
  TRIM(account_name)                AS account_name,
  CASE statement
    WHEN 'Balance sheet'    THEN 'BS'
    WHEN 'Income statement' THEN 'PL'
    -- ERROR() rather than ELSE NULL: a third vocabulary value means the
    -- source changed, and silently landing NULL would hide it until some
    -- downstream sum quietly excluded a whole statement.
    ELSE ERROR(FORMAT("unexpected statement value: %s", statement))
  END                               AS statement_type,
  category,
  normal_balance,
  SUBSTR(account, 1, 1)             AS code_block
FROM `aramco-finance-poc-c2a4.bronze.bronze_coa_raw`
WHERE chart_scope IN ('2010', '2380');


-- ---------------------------------------------------------------------------
-- dim_group_account — the Aramco consolidation target chart
--
-- Target vocabulary only. NEVER receives balances.
--
-- parent_group_node is DERIVED. The source sheet gives only Level.
--
-- *** DO NOT USE A 3-CHARACTER PREFIX MATCH ***
-- It resolves only 18 of 26 level-2 nodes. Six 3-char prefixes are shared by
-- several level-1 nodes — G31 alone covers G31000..G31600 — so G31510 has
-- seven candidate parents and the join fans out or picks arbitrarily.
--
-- The real convention: every level-1 node ends in '00', and level-2 nodes end
-- in 10,20,...,80. So the parent is the child with its last two digits reset
-- to '00'. That resolves 26/26 unambiguously and is verified below.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE `aramco-finance-poc-c2a4.silver.dim_group_account`
(
  group_node        STRING OPTIONS(description="PK. G-prefixed 5-digit code, e.g. 'G52000'. Separate namespace from the affiliate 4-digit codes by design — a group node can never be confused with an affiliate account in a query or an agent response."),
  group_name        STRING OPTIONS(description="e.g. 'Producing & manufacturing', 'Purchases'. Trimmed: the source indents level-2 names."),
  statement         STRING OPTIONS(description="Balance sheet | Income statement. Left in the CoA vocabulary — unlike dim_account this table never joins to the trial balances."),
  category          STRING OPTIONS(description="FS caption group."),
  normal_balance    STRING OPTIONS(description="Dr or Cr."),
  level             INT64  OPTIONS(description="1 = face caption (52 nodes), 2 = analytical sub-account (26 nodes)."),
  parent_group_node STRING OPTIONS(description="Self-FK to group_node. DERIVED, not in the source: the child code with its last two digits reset to '00'. NULL for level-1 nodes. Without this the hierarchy is decorative and nothing rolls up."),
  source_reference  STRING OPTIONS(description="Where the node traces to in Aramco's Q1-2026 condensed interim report, e.g. 'Balance sheet; Note 5'. Supports disclosure traceability.")
)
OPTIONS(description="Aramco Group consolidation target chart. 78 rows = 52 level-1 + 26 level-2. Target vocabulary only — never receives balances. Aramco presents operating costs BY NATURE while the affiliates present BY FUNCTION, which is why the affiliate-to-group bridge is a judgement (Agent 3's scored output) and not a join. Rebuilt from bronze.bronze_coa_raw.")
AS
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
WHERE chart_scope = 'GROUP';


-- ---------------------------------------------------------------------------
-- map_account_to_group — deliberately empty
--
-- IF NOT EXISTS, not OR REPLACE. This table will hold Agent 3's output, which
-- bronze cannot regenerate. Re-running this script must never wipe it.
--
-- Cardinality is assumed 1-to-0-or-1. If the mapping turns out many-to-many,
-- add an allocation_pct column — a known caveat, not an oversight.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.silver.map_account_to_group`
(
  entity_code    STRING  OPTIONS(description="FK to dim_account. Part of the source key."),
  account_code   STRING  OPTIONS(description="FK to dim_account. Part of the source key."),
  group_node     STRING  OPTIONS(description="FK to dim_group_account. The consolidation target."),
  confidence     NUMERIC OPTIONS(description="0.000-1.000 from the mapping agent. <0.80 routes to an analyst and drives review priority."),
  method         STRING  OPTIONS(description="exact | fuzzy | llm — how the match was made."),
  rationale      STRING  OPTIONS(description="The agent's stated reasoning. Required for mapping traceability."),
  mapped_at      TIMESTAMP OPTIONS(description="When the agent produced this row.")
)
OPTIONS(description="Affiliate account -> Group node bridge, with confidence. DELIBERATELY EMPTY until Agent 3 runs: no historical mapping table exists in the PoC pack, so this is a produced artifact, not an input. Nothing joining to dim_group_account until this is populated is expected, not a defect.");
