# Silver — dim/fact build

Applies fixed DDL, then upserts the silver dimension/fact tables fed by the
bronze layer, then runs the data-quality check board. Kept separate from
`bronze/` because it's a different layer with its own lifecycle — bronze
extracts source workbooks/CSVs, silver builds the warehouse model purely
from bronze tables already sitting in BigQuery.

## Scope

Builds/maintains these 9 tables: `dim_entity`, `dim_period`, `dim_account`,
`dim_group_account`, `fact_group_trial_balance`, `dim_ifrs_standard`,
`dim_ifrs_requirement`, `dim_entity_context`, `dim_required_document`.

**`fact_trial_balance` is out of scope.** It's sourced from `bronze_tb_raw`
(affiliate trial balance), which is owned by another developer's
`trial_balance/` pipeline at the repo root — same ownership boundary as
excluding `bronze_tb_raw` itself from `bronze/`. This package doesn't
build it, apply DDL for it, or check it. `scripts/build_silver.py`'s
`TABLES` list and its own tests (`tests/test_build_silver.py`) both assert
this exclusion, so it can't silently creep back in.

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
│   ├── silver_checks.sql    the DQ board build_silver.py runs after upsert
│   ├── silver_coa.sql,      SUPERSEDED by silver_build.sql — kept for
│   │   silver_coa_checks.sql  history, not executed by any script
│   └── join_checks.sql      ad-hoc referential-integrity queries, paste
│                            into the BigQuery console and run manually
├── scripts/
│   ├── build_silver.py      apply DDL -> upsert -> verify (see below)
│   └── export_ddl.py        re-baseline sql/*.sql from a live table's
│                            schema (rare — see its docstring)
└── tests/
    └── test_build_silver.py unit tests for retarget() and the TABLES list
                              (pure Python, no BigQuery access needed)
```

**`build_silver.py`'s three steps**, same DDL-first shape as
`bronze/scripts/push_to_bq.py`:

1. **Apply DDL** — for every table in `TABLES`, read `sql/<table>.sql` and
   execute `CREATE TABLE IF NOT EXISTS` (idempotent). 6 of these tables
   predate this pipeline (`dim_entity`, `dim_period`, `dim_account`,
   `dim_group_account`) and their DDL was baselined from the live table via
   `export_ddl.py`; the rest were genuinely introduced by this pipeline.
2. **Upsert** — runs `sql/silver_build.sql`, one `MERGE` per table, keyed on
   each table's primary key. Nothing is dropped or truncated; unchanged
   rows are left alone.
3. **Verify** — runs `sql/silver_checks.sql`'s DQ board and prints a
   PASS/FAIL line per check; exits non-zero if anything failed.

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
python scripts/build_silver.py                 # real run: DDL -> upsert -> verify

# validate in separate datasets before ever touching the live bronze./silver. tables:
python scripts/build_silver.py --bronze-dataset bronze_staging --silver-dataset silver_staging
```

The `--bronze-dataset`/`--silver-dataset` run must point at a `bronze_staging`
dataset that `bronze/scripts/push_to_bq.py --dataset bronze_staging` has
already populated — silver only reads bronze, it never writes to it.

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

**`map_account_to_group` is deliberately untouched here.** It's Agent 3's
output slot for the affiliate→Group account mapping, expected to be empty
until that agent runs. Nothing in this pipeline writes to it.
