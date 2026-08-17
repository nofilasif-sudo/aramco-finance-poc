# Silver — dim/fact build

Applies fixed DDL, then upserts the silver dimension/fact tables fed by the
bronze layer, then runs two layers of data-quality checks. Kept separate
from `bronze/` because it's a different layer with its own lifecycle —
bronze extracts source workbooks/CSVs, silver builds the warehouse model
purely from bronze tables already sitting in BigQuery.

## Scope

Builds/maintains these 7 tables: `dim_account`, `dim_group_account`,
`fact_group_trial_balance`, `dim_ifrs_standard`, `dim_ifrs_requirement`,
`dim_entity_context`, `dim_required_document`.

**`fact_trial_balance`, `dim_entity`, and `dim_period` are out of scope.**
All three are owned by another developer's `trial_balance/` pipeline at
the repo root:

- `fact_trial_balance` is sourced from `bronze_tb_raw` (affiliate trial
  balance) — same ownership boundary as excluding `bronze_tb_raw` itself
  from `bronze/`. This package doesn't build it, DDL it, or check it.
- `dim_entity` and `dim_period` **used to** also be built here, until it
  turned out both pipelines were `MERGE`ing into them independently with
  different data (`trial_balance/` writes only the 2010/2380 rows to
  `dim_entity` and derives `dim_period` from `bronze_trial_balance_raw`;
  this package used to also add an `ARAMCO` row and derive `dim_period`
  from `bronze_group_tb_raw`). Pulled out to stop two pipelines silently
  fighting over the same tables — see `sql/silver_build.sql`'s header
  comment for the full story.

`fact_group_trial_balance` still **reads** `dim_entity` and `dim_period`
as foreign-key lookups (it doesn't own them, but it joins to them), so **a
real run of this package now depends on `trial_balance/` having populated
both first.** `scripts/build_silver.py`'s `TABLES` list and its tests
(`tests/test_build_silver.py`) both assert `fact_trial_balance`/
`dim_entity`/`dim_period` stay excluded, so the boundary can't silently
regress.

There's also a live, currently-unowned `bronze_tb_raw` table (990 rows) —
a leftover from an earlier, now-deleted module of this package. Neither
`trial_balance/` (which builds a *different* table, `bronze_trial_balance_raw`)
nor this package builds it. Left alone deliberately; not restored.

Requires the bronze tables it reads (`bronze_coa_raw`, `bronze_group_tb_raw`,
`bronze_ifrs_standard_raw`, `bronze_ifrs_rubric_raw`,
`bronze_entity_context_raw`, `bronze_checklist_raw`) to already be
loaded — run `bronze/scripts/push_to_bq.py` first.

## Architecture

```
silver/
├── sql/
│   ├── <table>.sql          FIXED DDL, one CREATE TABLE IF NOT EXISTS per
│   │                        table this package owns — hand-maintained,
│   │                        edit here to change a schema
│   ├── silver_build.sql     MERGE statements that upsert every table from
│   │                        its bronze source(s)
│   ├── silver_checks.sql    the SQL DQ board build_silver.py runs after upsert
│   ├── silver_coa.sql,      SUPERSEDED by silver_build.sql — kept for
│   │   silver_coa_checks.sql  history, not executed by any script
│   └── join_checks.sql      ad-hoc referential-integrity queries, paste
│                            into the BigQuery console and run manually
├── scripts/
│   ├── build_silver.py      apply DDL -> upsert -> SQL DQ board -> table-wise
│   │                        DQ (see below)
│   └── export_ddl.py        re-baseline sql/*.sql from a live table's
│                            schema (rare — see its docstring)
└── tests/
    └── test_build_silver.py unit tests for retarget() and the TABLES list
                              (pure Python, no BigQuery access needed)
```

**`build_silver.py`'s four GLOBAL phases** — every table finishes phase N
before phase N+1 starts for any table:

1. **`[1/4] DDL`** — for every table in `TABLES`, read `sql/<table>.sql`
   and execute `CREATE TABLE IF NOT EXISTS` (idempotent). 4 of these
   (`dim_account`, `dim_group_account` predate this pipeline; DDL was
   baselined from the live table via `export_ddl.py`) already existed;
   the rest were genuinely introduced by this pipeline.
2. **`[2/4] upsert`** — runs `sql/silver_build.sql`, one `MERGE` per
   table, keyed on each table's primary key. Nothing is dropped or
   truncated; unchanged rows are left alone.
3. **`[3/4] verify`** — runs `sql/silver_checks.sql`'s SQL DQ board:
   shape checks (row counts, uniqueness) and business-rule checks (e.g.
   "proves to nil"), one flat PASS/FAIL list.
4. **`[4/4] table-wise DQ`** — `table_wise_dq()` in `build_silver.py`
   itself: for every table this package owns, prints a labeled block
   covering **NULLS** (required columns never null), **RI** (referential
   integrity — every foreign key resolves, including against `dim_entity`/
   `dim_period` which this package reads but doesn't own), and
   **SRC->TGT** (row count reconciliation against the bronze source table
   it was built from — `1:1` where every source row should land, or a
   documented ratio like `fact_group_trial_balance`'s `531:450`, where
   `silver_build.sql`'s join deliberately filters out subtotal rows).

Exits non-zero if anything in phase 3 or 4 fails.

**`retarget(sql_text, bronze_dataset, silver_dataset)`** rewrites every
`` `<project>.bronze.<table>` ``/`` `<project>.silver.<table>` `` reference
in a SQL string to point at different datasets. Applied to the DDL files,
`silver_build.sql`, and `silver_checks.sql` before they run — this is what
makes the `--bronze-dataset`/`--silver-dataset` flags below work. Safe as a
literal substring replace because every occurrence in these files sits
inside a backtick-quoted table reference, never in a comment or
description (covered by `tests/test_build_silver.py`).

## How to run

```bash
pip install google-cloud-bigquery
gcloud auth application-default login   # or set GOOGLE_APPLICATION_CREDENTIALS

python -m unittest discover -s tests           # pure-Python tests, no cloud needed
python scripts/build_silver.py --check         # dry-run silver_build.sql, write nothing
python scripts/build_silver.py                 # real run: DDL -> upsert -> verify -> table-wise DQ

# validate in separate datasets before ever touching the live bronze./silver. tables:
python scripts/build_silver.py --bronze-dataset bronze_staging --silver-dataset silver_staging
```

The `--bronze-dataset`/`--silver-dataset` run must point at a `bronze_staging`
dataset that `bronze/scripts/push_to_bq.py --dataset bronze_staging` has
already populated — silver only reads bronze, it never writes to it. It
also needs `dim_entity`/`dim_period` to already exist in whatever
`--silver-dataset` you point at (`fact_group_trial_balance` joins to
them) — copy them from the live `silver` dataset into your staging
dataset first, since this package doesn't create them.

## Design decisions worth knowing

**Upsert semantics, not rebuild.** Every statement in `silver_build.sql` is
a `MERGE` keyed on each table's primary key(s): unchanged rows are left
alone, existing rows are updated in place, new rows are inserted. Nothing
is ever dropped or truncated by this pipeline.

**Schema changes go through `sql/*.sql`, not Python.** Same convention as
`bronze/`: `build_silver.py` applies the fixed DDL file verbatim, it does
not build or evolve a schema in code. `CREATE TABLE IF NOT EXISTS` is a
no-op against a table that already exists, so a real schema change on a
live table needs an explicit `ALTER TABLE` (see the backfill pattern for
`dim_ifrs_requirement` in `silver_build.sql` for the shape this takes).

**Two pipelines can share a warehouse without sharing tables.** The
`dim_entity`/`dim_period` situation is the concrete lesson: when two
independently-run pipelines both `MERGE` into the same table with
different source data, upserts don't conflict loudly — they silently
interleave, and whichever ran last "wins" for any row both touched. The
fix here wasn't a smarter merge key, it was ownership: exactly one
pipeline builds a given table, everyone else reads it.

**`map_account_to_group` is deliberately untouched here.** It's Agent 3's
output slot for the affiliate→Group account mapping, expected to be empty
until that agent runs. Nothing in this pipeline writes to it.
