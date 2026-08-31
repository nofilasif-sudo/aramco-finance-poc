-- ===========================================================================
-- bronze.fs_clean
--
-- GENERATED from bronze_ingest.cloud.BRONZE_TABLES['fs_clean'] — the same
-- registry that builds the LOAD JOB's schema, so the DDL and the loader
-- cannot disagree. Do not hand-edit; edit cloud.py.
--
-- Executed by scripts/push_to_bq.py's ensure_table() before every load;
-- CREATE TABLE IF NOT EXISTS, so it is idempotent.
--
-- Source: Lynn's flat extract PoC_Group_FS_clean.csv, read by
-- src/bronze_ingest/fs_statements.py.
--
-- TYPED, unlike every other table in this package: amount is NUMERIC and
-- line_order is INT64 — a deliberate divergence from the all-STRING bronze
-- contract, ruled in favour of the Group FS Ingestion Notes, because footing
-- checks downstream run at zero tolerance and binary floating point would
-- manufacture breaks that are not in the document.
--
-- No source_file column, also per the Ingestion Notes — the table name and
-- doc_version already identify the document.
--
-- CLUSTER BY statement, not PARTITION BY: BigQuery partitions on DATE /
-- TIMESTAMP / INT64 only, so a STRING grouping key clusters.
--
-- Project  : aramco-finance-poc-c2a4
-- Dataset  : bronze          (location me-central2 - Dammam, IMMUTABLE)
-- ===========================================================================

CREATE TABLE IF NOT EXISTS `aramco-finance-poc-c2a4.bronze.fs_clean`
(
  doc_version STRING NOT NULL OPTIONS(description="Which source document this row came from — 'clean' in fs_clean, 'seeded' in fs_seeded. CONSTANT within each table, so it selects nothing as a filter; it exists so the two can be UNION ALL'd into one result set and still be told apart."),
  statement STRING NOT NULL OPTIONS(description="Which statement or note the row belongs to, as a SNAKE_CASE KEY, not a printed heading: 'income_statement', 'balance_sheet', 'note_05_ppe', 'note_07_tax', 'note_09_borrowings', 'note_10_revenue'. Exactly 6 values. The grouping key of the whole model — cluster on it. Notes are values in this column rather than separate tables, so a note's internal arithmetic is checked by the same logic as a face statement, and adding a cash flow statement later is an insert rather than a schema change."),
  section STRING NOT NULL OPTIONS(description="The grouping band within the statement, e.g. 'Non-current assets', 'Current liabilities', 'Equity and liabilities'. 13 distinct values, NEVER NULL: income statement rows all read 'Income statement' and each note's rows read that note's title. Rows that total ACROSS bands carry the band they conclude ('Assets' for Total assets), so summing every 'item' inside one section is well defined."),
  line_order INT64 NOT NULL OPTIONS(description="Presentation order within a statement, from 1, contiguous with no gaps. It is the line's position on the PAGE, not the row's position in this table, so it REPEATS once per column — Inventories appears twice at line_order 9, once per period. The natural key is (statement, line_order, column_label); BigQuery enforces no key, so ingest checks it."),
  line_item STRING NOT NULL OPTIONS(description="The line item description, verbatim, e.g. 'Cash and cash equivalents'. Never empty. NOT unique and NOT a safe join key: 'Other assets and receivables', 'Post-employment benefits', 'Investments in securities' and 'Borrowings' each appear under both a non-current and a current section. The note reference is NOT part of this text — it lives in note_ref."),
  note_ref STRING OPTIONS(description="The note this line cross-references, as the bare number ('5', '7', '9', '10'). NULL on the 126 of 142 rows that print no reference — one of the few genuine NULLs in bronze. STRING, not INT64: it is compared for equality, never summed. A WRONG value here is a real finding that carries no arithmetic signal, so no footing check will ever surface it — see fs_seeded's table description."),
  line_role STRING NOT NULL OPTIONS(description="What the line does arithmetically: 'item' (a component), 'subtotal' (sums the items above it within a section) or 'total' (concludes the statement or sums across sections). LOAD-BEARING: totals sit in the same table as their components, so an unguarded SUM(amount) double-counts — filter to line_role = 'item' to sum. The line_item text will not save you: most totals here do not contain the word 'Total' (Operating income, Net income, Cost - closing, Net book value, External revenue)."),
  column_label STRING NOT NULL OPTIONS(description="The column header, verbatim. 8 distinct values. DO NOT treat the second column as comparable across statements: the income statement compares 'Q1 2026' to 'Q1 2025' (a prior-year quarter), the balance sheet compares '31 Mar 2026' to '31 Dec 2025' (a prior year end). Match on the literal string, never on column position. Two exceptions: Note 9 uses 'Non-current'/'Current'/'Total', a breakdown axis rather than a period, which is why this is not called period_label; and Notes 5, 7 and 10 are single-column and print the literal 'SAR million' here."),
  amount NUMERIC NOT NULL OPTIONS(description="The figure, as NUMERIC — never FLOAT64. Footing checks run at zero tolerance and binary floating point manufactures breaks that are not in the document. SIGNED AS PRESENTED: figures printed in parentheses in the source document are stored NEGATIVE (29 of 142 rows), so costs, treasury shares and accumulated depreciation are negative and every subtotal sums with a plain SUM and no per-section sign rules. Never NULL."),
  amount_unit STRING NOT NULL OPTIONS(description="The unit of amount. CONSTANT 'SAR million' for every row, so it is useless as a filter or GROUP BY key; it is carried so the figures are never read as riyals. Unrelated to column_label also reading 'SAR million' on Notes 5, 7 and 10, where that is the note's single column HEADER.")
)
CLUSTER BY statement
OPTIONS(
  description="Saudi Aramco Group condensed consolidated financial statements for Q1 2026, the CLEAN version, loaded verbatim from Lynn's flat extract. 142 rows = income statement 19 lines x 2 periods + balance sheet 38 x 2 + Notes 5/7/10 7+5+7 x 1 column + Note 9 3 x 3 columns. Every subtotal foots and every cross-statement tie-out agrees; this is the control against which fs_seeded is read. TYPED, not all-STRING: amount NUMERIC, line_order INT64 — the only such columns in bronze, so that footing checks run at zero tolerance. Sum with a line_role = 'item' filter; totals and subtotals share the table with their components. No source_file column: the table name and doc_version identify the document. fs_clean and fs_seeded share this schema and have NO relationship — no join keys, no shared surrogate ids, and nothing presuming the two documents correspond row-for-row; the agent reads both and performs its own comparison. Ingest checks structure only and NEVER refuses to land on an arithmetic break — the breaks are the point. SYNTHETIC data."
);
