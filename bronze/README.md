# Bronze Ingestion

Aramco FC&RD PoC. Reads source Excel workbooks and CSVs, reconciles them
against their own control totals, and lands them as bronze tables — either
as local CSVs (`python -m bronze_ingest`) or straight into BigQuery
(`scripts/push_to_bq.py`).

## Scope

This package owns 6 of the 7 bronze tables in the project:

| Table | Source | Format |
|---|---|---|
| `bronze_coa_raw` | `PoC_Charts_of_Accounts (1).xlsx` | xlsx |
| `bronze_group_tb_raw` | `PoC_Group_Trial_Balance_Aramco.xlsx` | xlsx |
| `bronze_checklist_raw` | `PoC_Submission_Validator.xlsx` | xlsx |
| `bronze_ifrs_standard_raw` | `ifrs_standard_context.csv` | csv |
| `bronze_ifrs_rubric_raw` | `ifrs_requirements_updated.csv` | csv |
| `bronze_entity_context_raw` | `entity_context (1).csv` | csv |

**`bronze_tb_raw`** (affiliate trial balance) is **not** part of this
package — it's owned by another developer's `trial_balance/` pipeline at
the repo root. Don't add it here; don't build silver logic here that
depends on it (see `../silver/README.md`).

## Architecture

```
bronze/
├── pyproject.toml, requirements.txt   package + runtime deps (openpyxl only)
├── configs/                           one JSON per table — paths + parsing knobs, no logic
├── sql/                                FIXED DDL, one CREATE TABLE IF NOT EXISTS per table
│   └── bronze_<table>.sql              hand-maintained — edit here to change a schema
├── src/bronze_ingest/
│   ├── __main__.py                    local CLI: python -m bronze_ingest (CSV only, no cloud)
│   ├── excel.py                       generic xlsx parsing — header discovery, table
│   │                                   boundaries, row classification. Knows nothing of
│   │                                   any specific table.
│   ├── flatcsv.py                     generic CSV-row reader, same role as excel.py for
│   │                                   the 3 CSV-sourced tables
│   ├── coa.py, group_tb.py,           one extractor module per table — owns its own
│   │   checklist.py, ifrs_standard.py,  schema, column aliases, and fail-closed
│   │   ifrs_rubric.py,                  reconciliation checks (control totals /
│   │   entity_context.py                nil-proofs). extract(path, cfg, report) -> rows
│   ├── sink.py                        shared CSV renderer, used by both the local CLI
│   │                                   and push_to_bq.py
│   └── cloud.py                       GCS + BigQuery adapters: ensure_table() (applies
│                                       the fixed DDL), load_csv_from_memory/_gcs(),
│                                       upload_csv(). BRONZE_TABLES registry maps table
│                                       name -> (columns, descriptions) for the load job.
├── scripts/
│   ├── push_to_bq.py                  THE cloud entry point: extract -> reconcile ->
│   │                                   apply DDL -> load -> verify, per table
│   └── export_ddl.py                  re-baseline sql/*.sql from a live table's schema
│                                       (rare — DDL is normally hand-edited, not exported)
└── tests/                             one test file per extractor, stdlib unittest
```

**Data flow for one table**, `push_to_bq.py`'s `push_one()`:

1. **Extract** — `<table>.extract(path, cfg, report)` parses the xlsx/CSV and
   returns rows in memory, having already verified its own control total or
   nil-proof (raises `IngestError` before any cloud call if it doesn't
   balance).
2. **Render** — `sink.to_csv_text()` turns the rows into CSV text, kept in
   memory. **Nothing is written to local disk** — no `outputs/` folder from
   this script.
3. **Apply DDL** — `cloud.ensure_table()` reads the table's fixed
   `sql/<table>.sql` and executes `CREATE TABLE IF NOT EXISTS` — idempotent,
   safe to re-run. This is the schema's only source of truth; nothing here
   builds or evolves a schema in Python.
4. **Load** — either uploaded to GCS first (`gs://aramco-finance-poc-raw-landing/staging/<table>.csv`,
   a byte-for-byte lineage artifact) then loaded from there, or streamed
   straight from memory into the BigQuery load job — controlled by the
   `BUCKET` setting at the top of `push_to_bq.py`.
5. **Verify** — table-specific post-load checks re-query BigQuery directly
   (row counts, no-NULL, uniqueness, referential integrity) — proves the
   *loaded table* is right, not just that the CSV was right.

`excel.py`/`flatcsv.py` and `sink.py` are deliberately domain-agnostic: the
same parsing/rendering logic serves every table and would serve a new one
unchanged. Each table module owns everything specific to it.

**Every bronze row carries `source_file`** — the workbook/CSV filename it
was read from, added at ingest (not a column in any source file). Lets a
row be traced back to exactly what produced it.

## How to run

### Local only (no cloud, no credentials needed)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; source .venv/bin/activate on *nix
pip install -e .

mkdir data   # put the source files where each configs/*.json expects them

python -m unittest discover -s tests            # run the test suite
python -m bronze_ingest                         # writes every table's CSV to outputs/
python -m bronze_ingest --only bronze_coa_raw    # just one table
python -m bronze_ingest --dry-run               # run checks, write nothing
```

### Cloud (BigQuery)

```bash
pip install google-cloud-bigquery google-cloud-storage
gcloud auth application-default login            # or set GOOGLE_APPLICATION_CREDENTIALS

python scripts/push_to_bq.py --check              # extract + reconcile + connectivity, writes nothing
python scripts/push_to_bq.py                      # real run: extract -> DDL -> load -> verify
python scripts/push_to_bq.py --only bronze_coa_raw # just one table

# validate in a separate dataset before ever touching the live bronze.* tables:
python scripts/push_to_bq.py --dataset bronze_staging
```

Source files live in `gs://aramco-finance-poc-raw-landing/other_files/` —
pull the ones you need into `data/` with `gsutil cp` before running.

## Expected output (bronze_coa_raw)

| Tab | Source rows | Dividers dropped | Landed |
|---|---:|---:|---:|
| CoA - Group (Aramco) | 89 | 11 | **78** |
| CoA - SABIC (2010) | 79 | 13 | **66** |
| CoA - Petro Rabigh (2380) | 55 | 11 | **44** |
| `bronze_coa_raw` | | | **188** |

Landed rows equal each tab's own "Total accounts: N" footer exactly, so any
drift shows up as a direct mismatch and the run fails. Expected row counts
for every table live in `scripts/push_to_bq.py`'s `EXPECTED_ROWS`.

## Design decisions worth knowing

**Everything lands as text.** Casting is interpretation: `"1,234"` → `1234`
assumes a locale, `"(500)"` → `-500` assumes accounting notation. Get it wrong
here and the original is gone. Silver can always re-derive from bronze; bronze
cannot re-derive from silver.

**Nothing the file can tell us is hardcoded.** The header row is *found* (first
cell = "Account"), the table end is *found* (first blank row), tabs are
selected *structurally* (does it have a header?), and `chart_scope` is *parsed*
from `(NNNN)` in the tab name. Adding a fourth chart next quarter needs no code
change. Grep for a row number or a tab name — there are none.

**`chart_scope` is added at ingest and is not optional.** The two affiliate
charts share 42 four-digit codes, 15 of which mean different things — `1120` is
plant & machinery for SABIC but refinery plant for Petro Rabigh. An account
code alone is not a key. A tab we cannot attribute raises rather than landing
rows with a blank scope.

**Section dividers are dropped.** ⚠️ This deviates from the "no filtering" line
in the DBML dated 2026-08-05 — it was agreed on 2026-08-06 and needs to be
confirmed with Hussein. Justification, verified against the pack:

1. They carry no information — all 16 distinct captions already appear verbatim
   in the `category` column of the accounts beneath them.
2. They would be unusable anyway — a divider means something only by row
   position, and BigQuery tables are unordered sets with no ordinal to
   reconstruct it from.

Set `"drop_section_rows": false` to restore strict DBML behaviour; there is a
test covering that path.

**The run fails rather than landing unverified data.** Each table's control
total or nil-proof is reconciled before anything touches the cloud. A
pipeline that lands unverified data is worse than one that fails, because
the failure is then discovered downstream by someone who trusts the number.

**Schema changes go through `sql/*.sql`, not Python.** `cloud.ensure_table()`
executes the fixed DDL file verbatim; it no longer builds or auto-evolves a
schema. To add, rename, or retype a column: edit the `.sql` file. `CREATE
TABLE IF NOT EXISTS` is a no-op against an already-existing table, so a
real change on a live table also needs an explicit `ALTER TABLE`.

**No pandas.** There is no melt, no arithmetic, and every value lands as text —
pandas would add ~50 MB and a numpy ABI constraint to save nothing.

## Handover notes for silver

- `level` and `source_reference` are populated only for `chart_scope = GROUP`;
  the affiliate tabs do not have those columns.
- Leading spaces in `account_name` are deliberate — they encode level-2
  hierarchy on the Group chart. Do not trim.
- `statement` uses "Balance sheet" / "Income statement", while the trial
  balances use `BS` / `PL`. Bronze does not harmonise them.
- The affiliate→Group mapping does **not** exist in this pack. It is Agent 3's
  output, produced with a confidence score, not an input.
- Data is **SYNTHETIC**, calibrated to public results. Nothing derived from it
  may be presented as Aramco actuals.
