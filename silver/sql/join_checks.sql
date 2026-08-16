-- ===========================================================================
-- JOIN CHECKS — silver layer
--
-- Paste any query into the BigQuery console and run it on its own.
-- Every one states its expected result. Anything else is a finding.
--
-- Measured against the warehouse on 2026-08-10:
--   fact_trial_balance 990 · dim_account 110 · dim_entity 2
--   dim_period 9 · dim_group_account 78 · map_account_to_group 0
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- A. REFERENTIAL INTEGRITY — does every fact row find its dimension?
--    An orphan fact row is a balance with no meaning attached to it.
-- ---------------------------------------------------------------------------

-- A1. fact -> dim_entity          EXPECT: 0 rows
SELECT DISTINCT f.entity_code
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_entity` e USING (entity_code)
WHERE e.entity_code IS NULL;


-- A2. fact -> dim_account         EXPECT: 0 rows
--     Joined on BOTH key parts. See section C for why.
SELECT DISTINCT f.entity_code, f.account_code
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
       USING (entity_code, account_code)
WHERE d.account_code IS NULL;


-- A3. fact -> dim_period          EXPECT: 0 rows
SELECT DISTINCT f.period_key
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_period` p USING (period_key)
WHERE p.period_key IS NULL;


-- A4. dim_account -> dim_entity   EXPECT: 0 rows
--     The dimension must also respect the entity list.
SELECT DISTINCT d.entity_code
FROM `aramco-finance-poc-c2a4.silver.dim_account` d
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_entity` e USING (entity_code)
WHERE e.entity_code IS NULL;


-- ---------------------------------------------------------------------------
-- B. FAN-OUT — does joining change the row count?
--    A join to a dimension must never add rows. If it does, the dimension
--    key is not unique and every downstream SUM double counts.
-- ---------------------------------------------------------------------------

-- B1. Row count is preserved through all three joins
--     EXPECT: 990, 990, 990, 990 — all four identical
SELECT
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance`)
    AS before_any_join,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
     JOIN `aramco-finance-poc-c2a4.silver.dim_entity` e USING (entity_code))
    AS after_entity,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
     JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
          USING (entity_code, account_code))
    AS after_account,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
     JOIN `aramco-finance-poc-c2a4.silver.dim_entity` e USING (entity_code)
     JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
          USING (entity_code, account_code)
     JOIN `aramco-finance-poc-c2a4.silver.dim_period` p USING (period_key))
    AS after_all_three;


-- B2. Dimension keys are actually unique     EXPECT: 0 rows from each
SELECT 'dim_account' AS tbl, entity_code, account_code, COUNT(*) AS n
FROM `aramco-finance-poc-c2a4.silver.dim_account`
GROUP BY 1, 2, 3 HAVING n > 1;

SELECT 'dim_entity' AS tbl, entity_code, COUNT(*) AS n
FROM `aramco-finance-poc-c2a4.silver.dim_entity`
GROUP BY 1, 2 HAVING n > 1;

SELECT 'dim_period' AS tbl, period_key, COUNT(*) AS n
FROM `aramco-finance-poc-c2a4.silver.dim_period`
GROUP BY 1, 2 HAVING n > 1;


-- ---------------------------------------------------------------------------
-- C. THE COMPOSITE KEY — why entity_code is not optional
--
--    This is the most important query here. It measures what happens if
--    someone joins on account_code alone.
-- ---------------------------------------------------------------------------

-- C1. Correct join vs wrong join
--     EXPECT: correct = 990, wrong = 1746, phantom = 756
SELECT
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
     JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
          USING (entity_code, account_code))          AS correct_join,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
     JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
       ON f.account_code = d.account_code)            AS wrong_join,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
     JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
       ON f.account_code = d.account_code)
  - (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance`)
                                                       AS phantom_rows;


-- C2. What the wrong join does to a real number
--     SABIC's 2024 Q1 revenue, joined correctly and incorrectly.
--     EXPECT: correct = -30,376,015 · wrong = -60,752,030
--
--     Exactly double, because codes 4000 and 4010 exist in Petro Rabigh's
--     chart too, so every SABIC revenue row matches two dimension rows.
--     Note that -60,752,030 is not obviously wrong on sight — it is a
--     plausible revenue figure for a company this size. That is the whole
--     problem: the wrong join returns numbers that still look like numbers.
SELECT 'correct (entity + code)' AS method,
       SUM(f.ledger_amount) AS revenue_2024q1
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
     USING (entity_code, account_code)
WHERE f.entity_code = '2010' AND f.period_key = '2024Q1'
  AND d.category = 'Revenue'
UNION ALL
SELECT 'wrong (code only)',
       SUM(f.ledger_amount)
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
     ON f.account_code = d.account_code
WHERE f.entity_code = '2010' AND f.period_key = '2024Q1'
  AND d.category = 'Revenue';


-- C3. The 15 codes that make it dangerous     EXPECT: 15 rows
SELECT s.account_code,
       s.account_name AS sabic,
       p.account_name AS petro_rabigh
FROM `aramco-finance-poc-c2a4.silver.dim_account` s
JOIN `aramco-finance-poc-c2a4.silver.dim_account` p USING (account_code)
WHERE s.entity_code = '2010'
  AND p.entity_code = '2380'
  AND s.account_name <> p.account_name
ORDER BY s.account_code;


-- ---------------------------------------------------------------------------
-- D. GRAIN AND COMPLETENESS
-- ---------------------------------------------------------------------------

-- D1. The fact grain is unique     EXPECT: 990 and 990, identical
--     NB: `rows` is a reserved keyword in BigQuery (as is `nulls`), so the
--     alias has to be something else.
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT FORMAT('%s|%s|%s', entity_code, account_code, period_key))
         AS distinct_grain
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance`;


-- D2. Every account appears in every period     EXPECT: 0 rows
--     110 accounts x 9 periods = 990. A gap means a missing submission.
SELECT d.entity_code, d.account_code, COUNT(f.period_key) AS periods_present
FROM `aramco-finance-poc-c2a4.silver.dim_account` d
LEFT JOIN `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
       USING (entity_code, account_code)
GROUP BY 1, 2
HAVING periods_present <> 9;


-- D3. No unused dimension rows     EXPECT: 0, 0, 0
--     A dimension row nothing references is usually a sign the key is wrong.
SELECT
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.dim_account` d
     LEFT JOIN `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
            USING (entity_code, account_code)
    WHERE f.account_code IS NULL)                    AS accounts_never_used,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.dim_period` p
     LEFT JOIN `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
            USING (period_key)
    WHERE f.period_key IS NULL)                      AS periods_never_used,
  (SELECT COUNT(*) FROM `aramco-finance-poc-c2a4.silver.dim_entity` e
     LEFT JOIN `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
            USING (entity_code)
    WHERE f.entity_code IS NULL)                     AS entities_never_used;


-- D4. Group nodes never receive balances     EXPECT: 0
--     dim_group_account is target vocabulary. A G-code in the fact means
--     the two namespaces have been mixed.
SELECT COUNTIF(STARTS_WITH(account_code, 'G')) AS group_codes_in_fact
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance`;


-- ---------------------------------------------------------------------------
-- E. THE HIERARCHY SELF-JOIN — dim_group_account
-- ---------------------------------------------------------------------------

-- E1. Every level-2 node resolves to a real level-1 parent
--     EXPECT: 0 rows.  A 3-character prefix rule fails this on 8 rows.
SELECT c.group_node, c.parent_group_node, c.group_name
FROM `aramco-finance-poc-c2a4.silver.dim_group_account` c
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_group_account` p
       ON c.parent_group_node = p.group_node AND p.level = 1
WHERE c.level = 2 AND p.group_node IS NULL;


-- E2. Level-1 nodes have no parent, and nothing is its own parent
--     EXPECT: 0 rows
SELECT group_node, level, parent_group_node
FROM `aramco-finance-poc-c2a4.silver.dim_group_account`
WHERE (level = 1 AND parent_group_node IS NOT NULL)
   OR group_node = parent_group_node;


-- E3. The hierarchy, rolled up     EXPECT: 5 parents, 26 children
--     G11000 (8), G12000 (5), G21000 (5), G31500 (5), G40000 (3)
SELECT p.group_node   AS parent,
       p.group_name   AS parent_name,
       COUNT(*)       AS children
FROM `aramco-finance-poc-c2a4.silver.dim_group_account` c
JOIN `aramco-finance-poc-c2a4.silver.dim_group_account` p
     ON c.parent_group_node = p.group_node
GROUP BY 1, 2
ORDER BY 1;


-- ---------------------------------------------------------------------------
-- F. BUSINESS RULES, TESTED THROUGH THE JOIN
-- ---------------------------------------------------------------------------

-- F1. Every trial balance still proves to nil     EXPECT: 0 rows
--     Double-entry law. The strongest check available, because nobody can
--     argue with it. NUMERIC is exact, so "= 0" means exactly zero.
SELECT f.entity_code, f.period_key, SUM(f.ledger_amount) AS residual
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
     USING (entity_code, account_code)
GROUP BY 1, 2
HAVING residual <> 0;


-- F2. Balance sheet and P&L split     EXPECT: BS 675 rows, PL 315 rows
--     Guards the additivity rule: PL may be summed across periods,
--     BS may not.
SELECT d.statement_type,
       COUNT(*)                        AS fact_rows,
       COUNT(DISTINCT f.account_code)  AS distinct_codes
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
     USING (entity_code, account_code)
GROUP BY 1
ORDER BY 1;


-- F3. The balance sheet's imbalance IS the period result     EXPECT: 0 rows
--
--     Do NOT expect the BS subset to sum to zero on its own — it does not,
--     and should not. These are unclosed trial balances: the period's profit
--     has not yet been posted to equity, so the balance sheet is out by
--     exactly that amount. SABIC 2024 Q1: BS = +1,207,737, PL = -1,207,737.
--
--     So the real identity is BS residual + PL residual = 0. Checking that
--     is stronger than F1, because F1 would still pass if a BS account were
--     misclassified as PL — the total would net out either way. This one
--     catches the misclassification.
SELECT f.entity_code,
       p.period_label,
       SUM(IF(d.statement_type = 'BS', f.ledger_amount, 0)) AS bs_residual,
       SUM(IF(d.statement_type = 'PL', f.ledger_amount, 0)) AS pl_residual
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
     USING (entity_code, account_code)
JOIN `aramco-finance-poc-c2a4.silver.dim_period` p USING (period_key)
GROUP BY 1, 2, p.sort_order
HAVING bs_residual + pl_residual <> 0
ORDER BY 1, p.sort_order;


-- F4. A readable report — the four-table join working end to end
--     EXPECT: SABIC revenue by quarter, 9 rows, negative (credit balances)
SELECT p.period_label,
       e.entity_name,
       d.category,
       SUM(f.ledger_amount) AS amount_sar_000
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_entity`  e USING (entity_code)
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d USING (entity_code, account_code)
JOIN `aramco-finance-poc-c2a4.silver.dim_period`  p USING (period_key)
WHERE e.entity_code = '2010' AND d.category = 'Revenue'
GROUP BY 1, 2, 3, p.sort_order
ORDER BY p.sort_order;


-- F5. THE STRONGEST CHECK — tie to a published figure
--     EXPECT: 117,736,492 and difference = 0
--
--     SABIC's four 2024 quarters must sum to the FY2024 revenue in the
--     workbook's 'Anchors & sources' tab, which is taken from the audited
--     statements. This is worth more than every structural check above put
--     together: those prove the pipeline is self-consistent, this proves it
--     agrees with the outside world.
--
--     ABS() because the signed convention stores revenue as a credit.
SELECT SUM(ABS(f.ledger_amount))                  AS fy2024_revenue,
       117736492                                  AS published_anchor,
       SUM(ABS(f.ledger_amount)) - 117736492      AS difference
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance` f
JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
     USING (entity_code, account_code)
JOIN `aramco-finance-poc-c2a4.silver.dim_period` p USING (period_key)
WHERE f.entity_code = '2010'
  AND d.category    = 'Revenue'
  AND p.year        = 2024;


-- ---------------------------------------------------------------------------
-- G. THE BRIDGE — run these once Agent 3 populates map_account_to_group
--    All four return 0 rows today because the table is empty. That is
--    expected, not a pass.
-- ---------------------------------------------------------------------------

-- G1. Both sides of the bridge resolve     EXPECT: 0 rows
SELECT m.entity_code, m.account_code, m.group_node,
       IF(d.account_code IS NULL, 'no such account', 'no such group node') AS problem
FROM `aramco-finance-poc-c2a4.silver.map_account_to_group` m
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
       USING (entity_code, account_code)
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_group_account` g
       ON m.group_node = g.group_node
WHERE d.account_code IS NULL OR g.group_node IS NULL;


-- G2. No account mapped twice     EXPECT: 0 rows
--     Cardinality is assumed 1-to-0-or-1. If this ever returns rows, the
--     mapping is many-to-many and needs an allocation percentage.
SELECT entity_code, account_code, COUNT(*) AS mappings
FROM `aramco-finance-poc-c2a4.silver.map_account_to_group`
GROUP BY 1, 2 HAVING mappings > 1;


-- G3. Confidence is in range     EXPECT: 0 rows
SELECT * FROM `aramco-finance-poc-c2a4.silver.map_account_to_group`
WHERE confidence IS NULL OR confidence < 0 OR confidence > 1;


-- G4. Mapping coverage — which accounts are still unmapped?
--     EXPECT today: all 110. This is the completeness measure for Agent 3.
SELECT d.entity_code, COUNT(*) AS unmapped_accounts
FROM `aramco-finance-poc-c2a4.silver.dim_account` d
LEFT JOIN `aramco-finance-poc-c2a4.silver.map_account_to_group` m
       USING (entity_code, account_code)
WHERE m.account_code IS NULL
GROUP BY 1 ORDER BY 1;


-- ---------------------------------------------------------------------------
-- H. KNOWN ISSUES — these are expected to FAIL right now.
--    Kept here so the day they start passing is visible.
-- ---------------------------------------------------------------------------

-- H1. presentation_amount is unpopulated     CURRENTLY: 990 of 990 NULL
--     It needs normal_balance to flip signs, which dim_account now supplies.
SELECT COUNT(*) AS total_rows,
       COUNTIF(presentation_amount IS NULL) AS still_null
FROM `aramco-finance-poc-c2a4.silver.fact_trial_balance`;


-- H2. dim_affiliate duplicates dim_entity     CURRENTLY: same 2 keys
--     Two tables for one concept. A text-to-SQL agent has to guess which
--     to join, and guessing is what the semantic layer exists to prevent.
SELECT COALESCE(a.affiliate_code, e.entity_code) AS code,
       a.affiliate_name,
       e.entity_name
FROM `aramco-finance-poc-c2a4.silver.dim_affiliate` a
FULL JOIN `aramco-finance-poc-c2a4.silver.dim_entity` e
       ON a.affiliate_code = e.entity_code
ORDER BY 1;


-- H3. dim_entity has no Group entity     CURRENTLY: 0 rows
--     The design called for a GRP row alongside 2010 and 2380.
SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_entity` WHERE is_group;
