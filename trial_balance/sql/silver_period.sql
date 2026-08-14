-- dim_period is fully derivable from bronze.period_label (e.g. "2024 Q1"),
-- unlike dim_entity. New quarters get picked up automatically as bronze
-- grows — no manual seeding needed here.

MERGE `aramco-finance-poc-c2a4.silver.dim_period` AS target
USING (
  SELECT
    CONCAT(year_str, 'Q', quarter_str) AS period_key,
    period_label,
    CAST(year_str AS INT64) AS year,
    CAST(quarter_str AS INT64) AS quarter,
    ROW_NUMBER() OVER (ORDER BY CAST(year_str AS INT64), CAST(quarter_str AS INT64)) AS sort_order,
    LAST_DAY(DATE(CAST(year_str AS INT64), CAST(quarter_str AS INT64) * 3, 1)) AS period_end_date
  FROM (
    SELECT
      DISTINCT period_label,
      SPLIT(period_label, ' ')[OFFSET(0)] AS year_str,
      SUBSTR(SPLIT(period_label, ' ')[OFFSET(1)], 2) AS quarter_str
    FROM `aramco-finance-poc-c2a4.bronze.bronze_trial_balance_raw`
    WHERE period_label IS NOT NULL AND period_label != ''
  )
) AS source
ON target.period_key = source.period_key
WHEN MATCHED THEN
  UPDATE SET
    period_label = source.period_label,
    year = source.year,
    quarter = source.quarter,
    sort_order = source.sort_order,
    period_end_date = source.period_end_date
WHEN NOT MATCHED THEN
  INSERT (period_key, period_label, year, quarter, sort_order, period_end_date)
  VALUES (source.period_key, source.period_label, source.year, source.quarter, source.sort_order, source.period_end_date);