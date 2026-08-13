-- dim_entity is reference/master data enriched with business knowledge
-- (entity_name, ticker, consolidation_method, is_group) that does not exist
-- in the source Excel file. Maintain this by hand — add a row when a new
-- entity starts submitting (e.g. Aramco's parent-level submission).

MERGE `aramco-finance-poc-c2a4.silver.dim_entity` AS target
USING (
  SELECT * FROM UNNEST([
    STRUCT('2010' AS entity_code, 'Saudi Basic Industries Corporation' AS entity_name, '2010' AS ticker, 'consolidated_subsidiary' AS consolidation_method, FALSE AS is_group),
    STRUCT('2380' AS entity_code, 'Petro Rabigh' AS entity_name, '2380' AS ticker, 'equity_accounted_associate' AS consolidation_method, FALSE AS is_group)
  ])
) AS source
ON target.entity_code = source.entity_code
WHEN MATCHED THEN
  UPDATE SET
    entity_name = source.entity_name,
    ticker = source.ticker,
    consolidation_method = source.consolidation_method,
    is_group = source.is_group
WHEN NOT MATCHED THEN
  INSERT (entity_code, entity_name, ticker, consolidation_method, is_group)
  VALUES (source.entity_code, source.entity_name, source.ticker, source.consolidation_method, source.is_group);