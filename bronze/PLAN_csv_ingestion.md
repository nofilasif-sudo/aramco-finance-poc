# Plan — ingesting the four CSV sources

Status: **BUILT AND LOADED, 2026-08-13.** All checks green. Two corrections to
the pre-build analysis are marked below — kept rather than edited away, since
the reasoning that turned out wrong is worth seeing.

Decisions confirmed:
- IFRS requirements: **re-source `bronze_ifrs_rubric_raw` from the CSV** (option 1).
- `entity_context`: use **`entity_context (1).csv`** (13 keys, later export).
- `ifrs_standard_context`: **new table**.

ER diagrams already updated: `dbml/bronze.dbml`, `dbml/silver.dbml`.

---

## 1. Contracts — declared before any code

| Table | Grain | Key | Rows |
|---|---|---|---|
| `bronze_entity_context_raw` | one row per context key | `context_key` | 13 |
| `bronze_ifrs_standard_raw` | one row per standard | `standard_code` | 3 |
| `bronze_ifrs_rubric_raw` | one row per standard per requirement | `(standard_code, req)` | 15 *(unchanged — re-sourced, not extended)* |
| `silver.dim_ifrs_standard` | one row per standard | `standard_code` | 3 |
| `silver.dim_ifrs_requirement` | one row per standard per requirement | `(standard_code, req)` | 15 |
| `silver.dim_entity_context` | one row per context key | `context_key` | 13 |

**Invariants to assert:**
- every `standard_code` in the rubric exists in `bronze_ifrs_standard_raw` (3/3 today, verified)
- exactly 5 requirements per standard, `req` in `R1..R5`
- `evidence_type` ∈ {`narrative`, `table_structure`, `both`} — nothing else
- `context_key` unique (this is what forced the file choice)
- no blank `standard_code`, `req`, `requirement`, `context_key`, `context_value`

---

## 2. New code

### `src/bronze_ingest/flatcsv.py` — new, ~40 lines

A CSV reader parallel to `excel.py`, **not** a change to it. A flat CSV has no
title block, no header discovery, no melt, no footer — none of `excel.py`'s
machinery applies, and bending it to fit would damage the module that currently
serves three tables cleanly.

Provides: read rows, resolve columns through the existing `ALIASES` convention,
raise `IngestError` on a missing required column. Same fail-closed contract as
`resolve_columns()` today.

### Three extractor modules, following the established shape

`COLUMNS` / `ALIASES` / `REQUIRED` / `extract(path, cfg, report) -> (rows, meta)`,
each stamping `source_file`:

- `entity_context.py` → `bronze_entity_context_raw`
- `ifrs_standard.py` → `bronze_ifrs_standard_raw`
- `ifrs_rubric.py` → **rewritten** to read the CSV instead of the xlsx

### Configs

`configs/bronze_entity_context.json`, `configs/bronze_ifrs_standard.json`,
and `configs/bronze_ifrs_rubric.json` repointed at the CSV.

---

## 3. Changes to existing code

| File | Change |
|---|---|
| `src/bronze_ingest/ifrs_rubric.py` | Rewritten for CSV. `ALIASES` maps `standard_name`→`standard`, `req_id`→`req`; adds `standard_code`, `evidence_type`, `check_guidance` |
| `src/bronze_ingest/cloud.py` | 3 new/updated entries in `BRONZE_TABLES` with column descriptions |
| `src/bronze_ingest/__main__.py` | 2 new rows in the `TABLES` registry |
| `scripts/push_to_bq.py` | 2 new `EXPECTED_ROWS` entries; verify clauses for the new tables |
| `sql/silver_build.sql` | `CREATE IF NOT EXISTS` + `MERGE` for `dim_ifrs_standard` and `dim_entity_context`; rework the `dim_ifrs_requirement` MERGE |
| `sql/silver_checks.sql` | New checks per section 1's invariants |
| `scripts/build_silver.py` | 2 new names in `TABLES` |
| `data/` | Copy the 3 CSVs in, keeping filenames so `source_file` stays truthful |
| Tests | One test module per new extractor, plus updates to the rubric tests |

### The schema-evolution wrinkle — ~~predicted~~ **did not happen**

*Pre-build prediction, left in place as a record of a wrong call:* the three new
columns would append after `source_file`, stranding it mid-table, and that
cosmetic ugliness should simply be accepted rather than drop a live table.

**What actually happened.** Deployed schema came out in the intended order:

```
standard, req, requirement, standard_code, evidence_type, check_guidance, source_file
```

The prediction was wrong because bronze loads use `WRITE_TRUNCATE` with an
explicit schema, which **replaces** the table's schema outright rather than
appending to it. `ensure_table()`'s additive column-add is belt-and-braces for
bronze, not the operative mechanism. It *is* the operative mechanism in silver,
where MERGE never replaces a schema — which is exactly why the
`dim_ifrs_requirement` migration below needed real care.

### `dim_ifrs_requirement` — a key change on a live table

Keyed on `(standard, req)`; moved to `(standard_code, req)`, keeping the long
name as a descriptive attribute. The code is the stable identifier; a title can
be revised.

**The hazard this exposed, found while writing the SQL rather than by running
it.** The 15 existing rows had `standard_code = NULL`, because the column did
not exist when they were written. A MERGE keyed on `standard_code` would match
nothing, send all 15 rows to `WHEN NOT MATCHED`, and leave **30 rows** — the
originals orphaned beside 15 new ones. The row-count check would have caught it,
but only after the damage.

Fix: a one-time backfill joining on the *old* key, placed before the MERGE. It
is idempotent — once `standard_code` is populated its `WHERE` clause matches
nothing — so it is safe to leave in the script permanently rather than run by
hand and forget it was ever needed. Check 21 (`backfill took`) exists
specifically to prove it ran.

**Generalises to:** whenever a MERGE key changes on a populated table, the old
rows cannot match the new key until they are backfilled. Backfill first, switch
the key second, and assert the backfill landed.

---

## 4. Open question (not blocking)

`bronze_entity_context_raw` has no `entity_code` column, because the source file
has none — bronze mirrors files. But the content describes the Aramco
consolidated group specifically. If a second entity ever submits its own
context, the table needs that column and the key becomes
`(entity_code, context_key)`.

Leaving it out for now, and noting it here rather than pre-building for a case
that may not arrive.

---

## 5. Verification

1. `python -m unittest discover -s tests` — existing 37 plus new
2. `python -m bronze_ingest --dry-run` — all 8 tables reconcile to declared counts
3. `python scripts/push_to_bq.py --check` — connect and reconcile, no writes
4. Independent cell-by-cell validation against the source CSVs, extending
   `validate_all_bronze.py`
5. `python scripts/build_silver.py --check` — SQL validates
6. Only then the real load, on your go-ahead

---

## 6. Sequencing

Bronze first, all three tables, verified. Silver second. The silver MERGEs
reference bronze tables that must exist first — the same dependency that made
`build_silver.py --check` fail earlier when `bronze_group_tb_raw` was missing.
