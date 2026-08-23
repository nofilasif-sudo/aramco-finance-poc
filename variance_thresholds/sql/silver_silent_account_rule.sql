-- dim_silent_account_rule: silver-layer copy of bronze_variance_silent_account_rule_raw.
-- accounts/affiliate_codes/group_nodes kept as raw STRING, not split into
-- arrays — affiliate_codes is consistently comma-separated, but group_nodes
-- and accounts sometimes carry free-text annotations (e.g. "G56000 (charge)",
-- "(within G21000 / G25300)") that aren't safe to auto-split without risking
-- silently losing that context. Revisit once consumption pattern is clearer.

MERGE `aramco-finance-poc-c2a4.silver.dim_silent_account_rule` AS target
USING (
  SELECT
    rule_code,
    accounts,
    affiliate_codes,
    group_nodes,
    expected_cadence,
    expected_movement,
    flag_when,
    urgency,
    likely_cause
  FROM `aramco-finance-poc-c2a4.bronze.bronze_variance_silent_account_rule_raw` order by rule_code
) AS source
ON target.rule_code = source.rule_code
WHEN MATCHED THEN
  UPDATE SET
    accounts = source.accounts,
    affiliate_codes = source.affiliate_codes,
    group_nodes = source.group_nodes,
    expected_cadence = source.expected_cadence,
    expected_movement = source.expected_movement,
    flag_when = source.flag_when,
    urgency = source.urgency,
    likely_cause = source.likely_cause
WHEN NOT MATCHED THEN
  INSERT (
    rule_code, accounts, affiliate_codes, group_nodes, expected_cadence,
    expected_movement, flag_when, urgency, likely_cause
  )
  VALUES (
    source.rule_code, source.accounts, source.affiliate_codes, source.group_nodes,
    source.expected_cadence, source.expected_movement, source.flag_when,
    source.urgency, source.likely_cause
  );
