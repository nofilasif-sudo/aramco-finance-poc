-- ===========================================================================
-- SILVER DQ — run after silver_coa.sql
--
-- One query, one pass/fail board. Every row must read PASS.
-- Each check is written so the failure names the thing that broke, not just
-- that something did.
-- ===========================================================================

WITH
da  AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_account`),
dga AS (SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_group_account`),

checks AS (

  -- 1. Row counts tie back to the source control totals (66 + 44).
  SELECT 1 AS seq, 'dim_account row count = 110' AS check_name,
         COUNT(*) = 110 AS ok, FORMAT('actual %d', COUNT(*)) AS detail FROM da
  UNION ALL
  SELECT 2, 'dim_account split 66 / 44',
         COUNTIF(entity_code='2010') = 66 AND COUNTIF(entity_code='2380') = 44,
         FORMAT('2010=%d 2380=%d', COUNTIF(entity_code='2010'),
                COUNTIF(entity_code='2380')) FROM da

  -- 2. The composite key is actually a key. If this fails, every aggregate
  --    downstream silently double counts.
  UNION ALL
  SELECT 3, 'dim_account (entity_code, account_code) unique',
         COUNT(*) = COUNT(DISTINCT FORMAT('%s|%s', entity_code, account_code)),
         FORMAT('%d rows, %d distinct keys', COUNT(*),
                COUNT(DISTINCT FORMAT('%s|%s', entity_code, account_code)))
  FROM da

  -- 3. Normalisation produced no holes.
  UNION ALL
  SELECT 4, 'dim_account statement_type is BS or PL only',
         COUNTIF(statement_type NOT IN ('BS','PL') OR statement_type IS NULL) = 0,
         FORMAT('bad %d', COUNTIF(statement_type NOT IN ('BS','PL')
                                  OR statement_type IS NULL)) FROM da
  UNION ALL
  SELECT 5, 'dim_account normal_balance is Dr or Cr only',
         COUNTIF(normal_balance NOT IN ('Dr','Cr')) = 0,
         FORMAT('bad %d', COUNTIF(normal_balance NOT IN ('Dr','Cr'))) FROM da
  UNION ALL
  SELECT 6, 'dim_account code_block is a single digit 1-8',
         COUNTIF(code_block NOT BETWEEN '1' AND '8') = 0,
         FORMAT('bad %d', COUNTIF(code_block NOT BETWEEN '1' AND '8')) FROM da

  -- 4. Group chart shape.
  UNION ALL
  SELECT 7, 'dim_group_account row count = 78',
         COUNT(*) = 78, FORMAT('actual %d', COUNT(*)) FROM dga
  UNION ALL
  SELECT 8, 'dim_group_account 52 level-1 + 26 level-2',
         COUNTIF(level=1) = 52 AND COUNTIF(level=2) = 26,
         FORMAT('L1=%d L2=%d', COUNTIF(level=1), COUNTIF(level=2)) FROM dga
  UNION ALL
  SELECT 9, 'dim_group_account group_node unique',
         COUNT(*) = COUNT(DISTINCT group_node),
         FORMAT('%d rows, %d distinct', COUNT(*), COUNT(DISTINCT group_node))
  FROM dga

  -- 5. THE DERIVATION. Every level-2 node must resolve to a real level-1
  --    parent, and no level-1 node may have one. A 3-char prefix rule fails
  --    this check on 8 rows — that is exactly what it is here to catch.
  UNION ALL
  SELECT 10, 'every level-2 node has a parent that exists and is level-1',
         COUNTIF(c.level = 2 AND p.group_node IS NULL) = 0,
         FORMAT('unresolved %d of %d',
                COUNTIF(c.level = 2 AND p.group_node IS NULL),
                COUNTIF(c.level = 2))
  FROM dga c LEFT JOIN dga p
    ON c.parent_group_node = p.group_node AND p.level = 1
  UNION ALL
  SELECT 11, 'level-1 nodes have no parent',
         COUNTIF(level = 1 AND parent_group_node IS NOT NULL) = 0,
         FORMAT('bad %d', COUNTIF(level = 1 AND parent_group_node IS NOT NULL))
  FROM dga

  -- 6. The bridge is expected to be empty right now. Stated as a check so the
  --    day it stops being empty is visible rather than assumed.
  UNION ALL
  SELECT 12, 'map_account_to_group is still empty (Agent 3 pending)',
         COUNT(*) = 0, FORMAT('rows %d', COUNT(*))
  FROM `aramco-finance-poc-c2a4.silver.map_account_to_group`
)

SELECT seq, check_name, IF(ok, 'PASS', 'FAIL') AS status, detail
FROM checks
ORDER BY seq;


-- ---------------------------------------------------------------------------
-- Evidence query — the 15 colliding codes. Keep this handy: it is the
-- one-screen justification for the composite key if the client asks.
-- ---------------------------------------------------------------------------
-- SELECT s.account_code, s.account_name AS sabic, p.account_name AS petro_rabigh
-- FROM `aramco-finance-poc-c2a4.silver.dim_account` s
-- JOIN `aramco-finance-poc-c2a4.silver.dim_account` p USING (account_code)
-- WHERE s.entity_code = '2010' AND p.entity_code = '2380'
--   AND s.account_name <> p.account_name
-- ORDER BY s.account_code;
