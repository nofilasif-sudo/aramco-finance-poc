-- ===========================================================================
-- SILVER DQ — run after silver_build.sql
--
-- Covers exactly what silver_build.sql touches: dim_account,
-- dim_group_account, fact_group_trial_balance, dim_ifrs_standard,
-- dim_ifrs_requirement, dim_entity_context, dim_required_document.
--
-- fact_trial_balance, dim_entity and dim_period are OUT OF SCOPE — all
-- three are owned/populated by another developer's trial_balance/
-- pipeline (dim_entity/dim_period are shared: both pipelines used to
-- MERGE into them with different data, which is why they were pulled out
-- of this file). de/dp below are READ-ONLY lookups for referential-
-- integrity checks against fact_group_trial_balance, not tables this
-- file asserts a shape for.
--
-- One query, one pass/fail board. Every row must read PASS.
-- ===========================================================================

WITH
de   AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_entity`),
dp   AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_period`),
da   AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_account`),
dga  AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_group_account`),
fgtb AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.fact_group_trial_balance`),
dir  AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_ifrs_requirement`),
dis  AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_ifrs_standard`),
dec  AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_entity_context`),
drd  AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_required_document`),

checks AS (

  -- 1. dim_account / dim_group_account.
  SELECT 1 AS seq, 'dim_account has 110 rows (66 + 44)' AS check_name,
         COUNT(*) = 110 AS ok, FORMAT('actual %d', COUNT(*)) AS detail FROM da
  UNION ALL
  SELECT 2, 'dim_account (entity_code, account_code) unique',
         COUNT(*) = COUNT(DISTINCT FORMAT('%s|%s', entity_code, account_code)),
         FORMAT('%d rows, %d distinct', COUNT(*),
                COUNT(DISTINCT FORMAT('%s|%s', entity_code, account_code))) FROM da
  UNION ALL
  SELECT 3, 'dim_group_account has 78 rows, group_node unique',
         COUNT(*) = 78 AND COUNT(*) = COUNT(DISTINCT group_node),
         FORMAT('actual %d, distinct %d', COUNT(*), COUNT(DISTINCT group_node)) FROM dga

  -- 2. fact_group_trial_balance — grain, referential integrity (including
  --    against dim_entity/dim_period, which this pipeline reads but does
  --    not own), and the strongest check available: it proves to nil
  --    independently of the affiliate side.
  UNION ALL
  SELECT 4, 'fact_group_trial_balance has 450 rows (50 nodes x 9 periods)',
         COUNT(*) = 450, FORMAT('actual %d', COUNT(*)) FROM fgtb
  UNION ALL
  SELECT 5, 'fact_group_trial_balance grain is unique',
         COUNT(*) = COUNT(DISTINCT FORMAT('%s|%s|%s', entity_code, group_node, period_key)),
         FORMAT('%d rows, %d distinct keys', COUNT(*),
                COUNT(DISTINCT FORMAT('%s|%s|%s', entity_code, group_node, period_key))) FROM fgtb
  UNION ALL
  SELECT 6, 'every fact_group_trial_balance row joins to dim_group_account',
         COUNTIF(g.group_node IS NULL) = 0,
         FORMAT('orphan rows %d', COUNTIF(g.group_node IS NULL))
  FROM fgtb f LEFT JOIN dga g USING (group_node)
  UNION ALL
  SELECT 7, 'every fact_group_trial_balance row joins to dim_entity',
         COUNTIF(e.entity_code IS NULL) = 0,
         FORMAT('orphan rows %d', COUNTIF(e.entity_code IS NULL))
  FROM fgtb f LEFT JOIN de e USING (entity_code)
  UNION ALL
  SELECT 8, 'every fact_group_trial_balance row joins to dim_period',
         COUNTIF(p.period_key IS NULL) = 0,
         FORMAT('orphan rows %d', COUNTIF(p.period_key IS NULL))
  FROM fgtb f LEFT JOIN dp p USING (period_key)
  UNION ALL
  SELECT 9, 'fact_group_trial_balance proves to nil per period (Aramco parent-only)',
         COUNTIF(residual != 0) = 0,
         FORMAT('non-zero periods %d', COUNTIF(residual != 0))
  FROM (SELECT period_key, SUM(ledger_amount) AS residual FROM fgtb GROUP BY period_key)

  -- 3. Agent reference data.
  UNION ALL
  SELECT 10, 'dim_ifrs_requirement has 15 rows (3 standards x 5 reqs)',
         COUNT(*) = 15, FORMAT('actual %d', COUNT(*)) FROM dir
  UNION ALL
  SELECT 11, 'dim_ifrs_requirement (standard_code, req) unique',
         COUNT(*) = COUNT(DISTINCT FORMAT('%s|%s', standard_code, req)),
         FORMAT('%d rows, %d distinct', COUNT(*),
                COUNT(DISTINCT FORMAT('%s|%s', standard_code, req))) FROM dir

  -- The backfill guard. If standard_code were left NULL the MERGE would
  -- insert duplicates on the next run instead of updating in place, so this
  -- is the check that the migration actually took.
  UNION ALL
  SELECT 12, 'dim_ifrs_requirement standard_code fully populated (backfill took)',
         COUNTIF(standard_code IS NULL OR standard_code = '') = 0,
         FORMAT('null/blank %d', COUNTIF(standard_code IS NULL OR standard_code = '')) FROM dir
  UNION ALL
  SELECT 13, 'dim_ifrs_requirement evidence_type is a closed set',
         COUNTIF(evidence_type NOT IN ('narrative','table_structure','both')) = 0,
         FORMAT('bad %d', COUNTIF(evidence_type NOT IN ('narrative','table_structure','both'))) FROM dir
  UNION ALL
  SELECT 14, 'dim_ifrs_requirement check_guidance populated',
         COUNTIF(COALESCE(check_guidance,'') = '') = 0,
         FORMAT('blank %d', COUNTIF(COALESCE(check_guidance,'') = '')) FROM dir
  UNION ALL
  SELECT 15, 'exactly 5 requirements per standard',
         LOGICAL_AND(n = 5), FORMAT('%d standards', COUNT(*))
  FROM (SELECT standard_code, COUNT(*) AS n FROM dir GROUP BY standard_code)

  UNION ALL
  SELECT 16, 'dim_ifrs_standard has 3 rows, standard_code unique',
         COUNT(*) = 3 AND COUNT(*) = COUNT(DISTINCT standard_code),
         FORMAT('actual %d, distinct %d', COUNT(*), COUNT(DISTINCT standard_code)) FROM dis

  -- Referential integrity for the new parent/child pair.
  UNION ALL
  SELECT 17, 'every dim_ifrs_requirement resolves to dim_ifrs_standard',
         COUNTIF(s.standard_code IS NULL) = 0,
         FORMAT('orphan requirements %d', COUNTIF(s.standard_code IS NULL))
  FROM dir r LEFT JOIN dis s USING (standard_code)
  UNION ALL
  SELECT 18, 'no dim_ifrs_standard row is unused',
         COUNTIF(r.standard_code IS NULL) = 0,
         FORMAT('unreferenced standards %d', COUNTIF(r.standard_code IS NULL))
  FROM dis s LEFT JOIN (SELECT DISTINCT standard_code FROM dir) r USING (standard_code)

  UNION ALL
  SELECT 19, 'dim_entity_context has 13 rows, context_key unique',
         COUNT(*) = 13 AND COUNT(*) = COUNT(DISTINCT context_key),
         FORMAT('actual %d, distinct %d', COUNT(*), COUNT(DISTINCT context_key)) FROM dec
  UNION ALL
  SELECT 20, 'dim_entity_context has no blank values',
         COUNTIF(COALESCE(context_value,'') = '') = 0,
         FORMAT('blank %d', COUNTIF(COALESCE(context_value,'') = '')) FROM dec

  UNION ALL
  SELECT 21, 'dim_required_document has 14 rows, item unique',
         COUNT(*) = 14 AND COUNT(*) = COUNT(DISTINCT item),
         FORMAT('actual %d, distinct %d', COUNT(*), COUNT(DISTINCT item)) FROM drd
)

SELECT seq, check_name, IF(ok, 'PASS', 'FAIL') AS status, detail
FROM checks
ORDER BY seq;
