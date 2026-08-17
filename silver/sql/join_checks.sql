-- ===========================================================================
-- JOIN CHECKS — silver layer
--
-- Paste any query into the BigQuery console and run it on its own.
-- Every one states its expected result. Anything else is a finding.
--
-- fact_trial_balance / dim_account / dim_entity / dim_period joins are OUT
-- OF SCOPE here — fact_trial_balance is sourced from bronze_tb_raw, owned
-- by another developer's trial_balance/ pipeline, same boundary as
-- silver_build.sql and silver_checks.sql. What's left below covers only
-- dim_group_account (this pipeline's own table) and the still-empty
-- map_account_to_group bridge.
--
-- Measured against the warehouse on 2026-08-10:
--   dim_group_account 78 · map_account_to_group 0
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- A. THE HIERARCHY SELF-JOIN — dim_group_account
-- ---------------------------------------------------------------------------

-- A1. Every level-2 node resolves to a real level-1 parent
--     EXPECT: 0 rows.  A 3-character prefix rule fails this on 8 rows.
SELECT c.group_node, c.parent_group_node, c.group_name
FROM `aramco-finance-poc-c2a4.silver.dim_group_account` c
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_group_account` p
       ON c.parent_group_node = p.group_node AND p.level = 1
WHERE c.level = 2 AND p.group_node IS NULL;


-- A2. Level-1 nodes have no parent, and nothing is its own parent
--     EXPECT: 0 rows
SELECT group_node, level, parent_group_node
FROM `aramco-finance-poc-c2a4.silver.dim_group_account`
WHERE (level = 1 AND parent_group_node IS NOT NULL)
   OR group_node = parent_group_node;


-- A3. The hierarchy, rolled up     EXPECT: 5 parents, 26 children
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
-- B. THE BRIDGE — run these once Agent 3 populates map_account_to_group
--    All four return 0 rows today because the table is empty. That is
--    expected, not a pass.
-- ---------------------------------------------------------------------------

-- B1. Both sides of the bridge resolve     EXPECT: 0 rows
SELECT m.entity_code, m.account_code, m.group_node,
       IF(d.account_code IS NULL, 'no such account', 'no such group node') AS problem
FROM `aramco-finance-poc-c2a4.silver.map_account_to_group` m
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_account` d
       USING (entity_code, account_code)
LEFT JOIN `aramco-finance-poc-c2a4.silver.dim_group_account` g
       ON m.group_node = g.group_node
WHERE d.account_code IS NULL OR g.group_node IS NULL;


-- B2. No account mapped twice     EXPECT: 0 rows
--     Cardinality is assumed 1-to-0-or-1. If this ever returns rows, the
--     mapping is many-to-many and needs an allocation percentage.
SELECT entity_code, account_code, COUNT(*) AS mappings
FROM `aramco-finance-poc-c2a4.silver.map_account_to_group`
GROUP BY 1, 2 HAVING mappings > 1;


-- B3. Confidence is in range     EXPECT: 0 rows
SELECT * FROM `aramco-finance-poc-c2a4.silver.map_account_to_group`
WHERE confidence IS NULL OR confidence < 0 OR confidence > 1;


-- B4. Mapping coverage — which accounts are still unmapped?
--     EXPECT today: all 110. This is the completeness measure for Agent 3.
SELECT d.entity_code, COUNT(*) AS unmapped_accounts
FROM `aramco-finance-poc-c2a4.silver.dim_account` d
LEFT JOIN `aramco-finance-poc-c2a4.silver.map_account_to_group` m
       USING (entity_code, account_code)
WHERE m.account_code IS NULL
GROUP BY 1 ORDER BY 1;


-- ---------------------------------------------------------------------------
-- C. KNOWN ISSUES — these are expected to FAIL right now.
--    Kept here so the day they start passing is visible.
-- ---------------------------------------------------------------------------

-- C1. dim_affiliate duplicates dim_entity     CURRENTLY: same 2 keys
--     Two tables for one concept. A text-to-SQL agent has to guess which
--     to join, and guessing is what the semantic layer exists to prevent.
SELECT COALESCE(a.affiliate_code, e.entity_code) AS code,
       a.affiliate_name,
       e.entity_name
FROM `aramco-finance-poc-c2a4.silver.dim_affiliate` a
FULL JOIN `aramco-finance-poc-c2a4.silver.dim_entity` e
       ON a.affiliate_code = e.entity_code
ORDER BY 1;


-- C2. dim_entity has no Group entity     CURRENTLY: 0 rows
--     The design called for a GRP row alongside 2010 and 2380.
SELECT * FROM `aramco-finance-poc-c2a4.silver.dim_entity` WHERE is_group;
