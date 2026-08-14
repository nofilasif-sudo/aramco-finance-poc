-- fact_trial_balance: loads everything derivable from bronze right now.
-- presentation_amount is intentionally left NULL — computing it correctly
-- needs the full distinct (type, category) list to know which accounts are
-- Cr-normal, and guessing at sign logic on a financial table is worse than
-- leaving it blank until confirmed.

MERGE `aramco-finance-poc-c2a4.silver.fact_trial_balance` AS target
USING (
  SELECT
    affiliate_code AS entity_code,
    account AS account_code,
    CONCAT(
      SPLIT(period_label, ' ')[OFFSET(0)],
      'Q',
      SUBSTR(SPLIT(period_label, ' ')[OFFSET(1)], 2)
    ) AS period_key,
    CAST(amount AS NUMERIC) AS ledger_amount,
    CAST(NULL AS NUMERIC) AS presentation_amount,  -- pending: needs Cr-normal mapping
    'SAR' AS currency,
    'thousands' AS amount_unit
  FROM `aramco-finance-poc-c2a4.bronze.bronze_trial_balance_raw`
  WHERE amount IS NOT NULL AND amount != ''
) AS source
ON  target.entity_code = source.entity_code
AND target.account_code = source.account_code
AND target.period_key = source.period_key
WHEN MATCHED THEN
  UPDATE SET
    ledger_amount = source.ledger_amount,
    currency = source.currency,
    amount_unit = source.amount_unit
    -- presentation_amount intentionally untouched on MATCHED so a future
    -- backfill isn't silently overwritten with NULL again
WHEN NOT MATCHED THEN
  INSERT (entity_code, account_code, period_key, ledger_amount, presentation_amount, currency, amount_unit)
  VALUES (source.entity_code, source.account_code, source.period_key, source.ledger_amount, source.presentation_amount, source.currency, source.amount_unit);