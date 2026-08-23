-- dim_variance_threshold: silver-layer copy of bronze_variance_threshold_raw,
-- kept wide (same shape as bronze) rather than unpivoted into a long
-- band table — mirrors trial_balance's silver SQL doing light
-- casting/key-building rather than structural pivots, and bronze already
-- carries the QoQ/YoY columns as FLOAT64, so no further typing is needed here.
-- This MERGE exists to decouple consumers from bronze's write-disposition and
-- landing-layer churn, same rationale as dim_entity/dim_period.

MERGE `aramco-finance-poc-c2a4.silver.dim_variance_threshold` AS target
USING (
  SELECT
    entity_scope,
    account,
    account_name,
    category,
    archetype,
    seasonal,
    primary_basis,
    qoq_low,
    qoq_med,
    qoq_high,
    yoy_low,
    yoy_med,
    yoy_high,
    abs_floor,
    large_dollar_override,
    notes
  FROM `aramco-finance-poc-c2a4.bronze.bronze_variance_threshold_raw`
) AS source
ON  target.entity_scope = source.entity_scope
AND target.account = source.account
WHEN MATCHED THEN
  UPDATE SET
    account_name = source.account_name,
    category = source.category,
    archetype = source.archetype,
    seasonal = source.seasonal,
    primary_basis = source.primary_basis,
    qoq_low = source.qoq_low,
    qoq_med = source.qoq_med,
    qoq_high = source.qoq_high,
    yoy_low = source.yoy_low,
    yoy_med = source.yoy_med,
    yoy_high = source.yoy_high,
    abs_floor = source.abs_floor,
    large_dollar_override = source.large_dollar_override,
    notes = source.notes
WHEN NOT MATCHED THEN
  INSERT (
    entity_scope, account, account_name, category, archetype, seasonal,
    primary_basis, qoq_low, qoq_med, qoq_high, yoy_low, yoy_med, yoy_high,
    abs_floor, large_dollar_override, notes
  )
  VALUES (
    source.entity_scope, source.account, source.account_name, source.category,
    source.archetype, source.seasonal, source.primary_basis, source.qoq_low,
    source.qoq_med, source.qoq_high, source.yoy_low, source.yoy_med,
    source.yoy_high, source.abs_floor, source.large_dollar_override, source.notes
  );
