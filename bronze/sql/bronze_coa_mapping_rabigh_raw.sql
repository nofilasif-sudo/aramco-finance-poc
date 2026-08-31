-- ===========================================================================
-- bronze.bronze_coa_mapping_rabigh_raw
--
-- FIXED DDL — hand-maintained, executed by scripts/push_to_bq.py's
-- ensure_table() before every load (CREATE TABLE IF NOT EXISTS is
-- idempotent). This file is now the schema's source of truth: to add,
-- rename, or retype a column, edit this file — the code no longer
-- derives or evolves the schema in Python.
--
-- Schema is IDENTICAL to bronze_coa_mapping_sabic_raw by design — the two
-- tabs are the same BPC configuration for different affiliates, so the pair
-- can be UNION ALL'd on affiliate_code. Keep the two files in step.
--
-- Project  : aramco-finance-poc-c2a4
-- Dataset  : bronze          (location me-central2 - Dammam, IMMUTABLE)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.bronze_coa_mapping_rabigh_raw`
(
  affiliate_code STRING OPTIONS(description="The affiliate this mapping configures: constant '2380' (Petro Rabigh) in this table. ADDED AT INGEST from the worksheet's title block — it is not a column in the sheet. Constant here, so it selects nothing as a filter; it exists so this table and bronze_coa_mapping_sabic_raw can be UNION ALL'd and still be told apart, and so a row stays joinable to bronze_coa_raw.chart_scope and bronze_tb_raw once it leaves the sheet."),
  affiliate_account STRING OPTIONS(description="The affiliate's 4-digit ledger account code, e.g. '1100'. Unique within this table — one account maps to exactly one Group node in a BPC configuration. Joins to bronze_coa_raw.account WITH chart_scope = '2380'; never join on account alone, because 2010 and 2380 share 4-digit codes that mean different things (1120 = plant & machinery for 2010 but refinery plant for 2380)."),
  affiliate_account_name STRING OPTIONS(description="The affiliate's own account description, verbatim, e.g. 'Cost of sales - crude & feedstock'. The by-FUNCTION vocabulary the affiliate books in — this is the text the mapping had to reconcile against the Group's by-NATURE captions. Refinery-specific wording here differs from SABIC's petrochemical wording for several shared account codes."),
  group_node STRING OPTIONS(description="The G-prefixed 5-digit Aramco Group node this account maps to, e.g. 'G11000'. Joins to bronze_coa_raw.account with chart_scope = 'GROUP', and to bronze_group_tb_raw.account. Read `status` before trusting it — on a flagged row it is the agent's best candidate, not a confirmed mapping."),
  group_node_name STRING OPTIONS(description="The Group node NAME, e.g. 'Property, plant & equipment (net)'. Carried alongside the code for readability; the code is the join key."),
  confidence STRING OPTIONS(description="The agent's confidence in this mapping, 0.00-1.00, as TEXT per the bronze all-STRING contract — CAST to NUMERIC to compare or aggregate. Drives the triage bands: >= 0.80 auto-maps, 0.50-0.79 routes to analyst review, < 0.50 is unmapped. Ingest verifies every row's `status` agrees with its score, so the two can be used interchangeably."),
  status STRING OPTIONS(description="Triage outcome, a closed set: 'Auto-mapped' (40 rows) and 'Analyst review' (4 rows). NOTE this table has NO 'Unmapped - analyst intervention' rows — that value is legal in the column and appears in bronze_coa_mapping_sabic_raw; do not infer the domain from this table alone. This is the column an agent filters on to find the work queue."),
  rationale STRING OPTIONS(description="Why this row was flagged, written by the agent — e.g. \"Shareholder subordinated loan: 'Borrowings' vs related-party financing - analyst to confirm.\" EMPTY STRING (never NULL) on rows that mapped cleanly; populated on 6 of 44. The most valuable text in the table: it names the specific judgement an analyst has to make."),
  source_file STRING OPTIONS(description="Original workbook filename this row was read from (e.g. 'PoC_CoA_Mapping.xlsx'). ADDED AT INGEST — not a column in the sheet. Lets a row be traced back to the exact file it came from when a workbook is re-shared or re-versioned.")
)
OPTIONS(
  description="Petro Rabigh (affiliate 2380) chart-of-accounts mapping to Aramco Group nodes, as configured in SAP BPC and scored by Agent 3. 44 rows, one per affiliate account, matching the tab's own triage footer (40 auto-mapped + 4 analyst review + 0 unmapped) exactly. All columns STRING per the bronze contract — CAST confidence to compare it. THE FLAGGED ROWS ARE THE DELIVERABLE, NOT A DEFECT: ingest never refuses to land on a low score, because surfacing exactly these is the point of the demo. The four flagged rows are the genuine by-function-to-by-nature judgements — catalysts & chemicals, other operating income/expenses, and a shareholder subordinated loan that could sit in 'Borrowings' or in related-party financing. Petro Rabigh is a REFINERY: its chart is 22 accounts shorter than SABIC's and carries no discontinued operations, no derivatives and no separate income-tax line, so an account absent here is a real difference between the affiliates rather than a dropped row. Kept as its own table rather than stacked with SABIC because each tab is a self-contained BPC configuration with its own control total; UNION ALL on affiliate_code to combine them. No relationships enforced at this layer. SYNTHETIC data calibrated to public results; not Aramco actuals."
);
