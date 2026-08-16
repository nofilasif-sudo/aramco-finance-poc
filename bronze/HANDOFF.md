# Context handoff — Aramco FC&RD PoC

Paste this whole file as your first message to a new assistant. Everything below
was verified against the live warehouse on 2026-08-13/14, not recalled.

---

## 0. How I want you to work with me

These are agreements from prior sessions. Follow them without being reminded.

1. **Explain why for every change**, not just what. What breaks without it, or
   what it makes possible. Never present a diff as self-explanatory.
2. **Never state a fact you have not executed a command to verify.** Docs, DBML
   files, comments and my own assumptions are all *unverified claims* — the
   warehouse is the truth. When they disagree, say so.
3. **Plan → I review → then build.** For anything non-trivial, write the plan
   first and wait. Do not start coding off an approved-sounding sentence.
4. **Teach me the concepts as we go.** Name the idea, don't just apply it. I am
   deliberately building vocabulary so I recognise problems instead of
   re-deriving them.
5. **Stay inside the project.** Do not pull in loose scripts from elsewhere on
   disk. If logic is needed, rebuild it against this package's primitives.
6. **Report failures honestly**, with the output. Never adjust an expected
   number to match reality without saying that is what you are doing.

I do not read most of the generated code. My trust comes from verified output,
so build accordingly — see §5.

---

## 1. What the project is

Financial-consolidation PoC for Saudi Aramco's FC&RD function. Reads
human-authored Excel workbooks and CSVs into a BigQuery medallion warehouse
(bronze → silver) that AI agents query.

- **Project:** `aramco-finance-poc-c2a4`, location `me-central2` (immutable)
- **Repo:** `C:\Users\Lenovo\Downloads\bronze_ingestion`
- **Credentials:** set `GOOGLE_APPLICATION_CREDENTIALS` to the service-account
  JSON in `Downloads` before any script
- **Data is SYNTHETIC**, calibrated to public results. Nothing derived from it
  may be presented as Aramco actuals.

Downstream consumers are agents: Agent 2 (submission validation), Agent 3
(affiliate→group account mapping), Agent 4 (disclosure drafting), Agent 6
(text-to-SQL over the warehouse). **Agent 6 matters for design**: column
descriptions are product, not documentation, and any ambiguity in the schema
becomes a wrong answer.

---

## 2. Current state — verified

### Bronze (8 tables)

| Table | Rows | Source |
|---|---:|---|
| `bronze_coa_raw` | 188 | `PoC_Charts_of_Accounts (1).xlsx` |
| `bronze_tb_raw` | 990 | `PoC_Affiliate_Trial_Balances (1).xlsx` |
| `bronze_group_tb_raw` | 531 | `PoC_Group_Trial_Balance_Aramco.xlsx` |
| `bronze_ifrs_rubric_raw` | 15 | `ifrs_requirements_updated.csv` |
| `bronze_ifrs_standard_raw` | 3 | `ifrs_standard_context.csv` |
| `bronze_entity_context_raw` | 13 | `entity_context (1).csv` |
| `bronze_checklist_raw` | 14 | `PoC_Submission_Validator.xlsx` |
| `bronze_trial_balance_raw` | 990 | **STALE — orphaned, see §6** |

### Silver (11 tables)

| Table | Rows |
|---|---:|
| `dim_entity` | 3 |
| `dim_period` | 9 |
| `dim_account` | 110 |
| `dim_group_account` | 78 |
| `fact_trial_balance` | 990 |
| `fact_group_trial_balance` | 450 |
| `dim_ifrs_standard` | 3 |
| `dim_ifrs_requirement` | 15 |
| `dim_entity_context` | 13 |
| `dim_required_document` | 14 |
| `dim_affiliate` | 2 — **STALE, see §6** |
| `map_account_to_group` | 0 — deliberately empty, Agent 3's output slot |

### Commands

```
python -m unittest discover -s tests     # 56 tests
python -m bronze_ingest --dry-run        # all 7 tables, checks only
python -m bronze_ingest --only <table>
python scripts/push_to_bq.py --check     # reconcile + connect, no writes
python scripts/push_to_bq.py             # extract → load → verify
python scripts/build_silver.py --check   # validate SQL only
python scripts/build_silver.py           # MERGE upserts + 30 DQ checks
```

---

## 3. Architecture

Strict one-way dependency chain. Do not add imports that reverse it.

```
excel.py  /  flatcsv.py     stdlib + openpyxl — know nothing about any table
      ↑
coa · tb · group_tb · ifrs_rubric · ifrs_standard · entity_context · checklist
      ↑                            each owns its own schema, aliases, checks
cloud.py     GCP libs imported INSIDE functions (keeps tests SDK-free)
      ↑
__main__.py  /  scripts/     entry points
```

- `excel.py` — header discovery by sentinel, table boundaries, row
  classification, melt helpers. For human-authored spreadsheets.
- `flatcsv.py` — flat CSVs. Deliberately **separate**, not a branch inside
  `excel.py`: a CSV has no title block, no discovered boundaries, no footer.
- `sink.py` — shared CSV writer. `lineterminator="\n"` pinned so Windows and
  Linux produce identical bytes.
- Each extractor exposes `COLUMNS`, `ALIASES`, `REQUIRED`,
  `extract(path, cfg, report) -> (rows, meta)`.
- `cloud.BRONZE_TABLES` — one registry entry per table (columns, descriptions,
  table description). `__main__.TABLES` — the (name, module, config) list that
  `push_to_bq.py` also reads, so the two entry points cannot disagree.

---

## 4. Decisions already made — do not re-open without saying why

- **Composite key `(entity_code, account_code)`** on `dim_account` and
  `fact_trial_balance`. 42 account codes are shared between SABIC (2010) and
  Petro Rabigh (2380); 15 mean different things. Joining on `account_code`
  alone doubles SABIC revenue and returns a plausible number.
  `fact_group_trial_balance` keys on `group_node` alone — G-codes are globally
  unique, so the same reasoning gives a different answer.
- **Every bronze row carries `source_file`** — the workbook/CSV filename,
  added at ingest like `chart_scope`. Propagated up into `fact_trial_balance`.
- **Section dividers and subtotals are dropped** from all trial-balance tables.
  Verified: all 33 divider captions already appear verbatim in `category`.
  Subtotals dropped so the grain is one row per account per period — keeping
  them mixed two grains and made `SUM(amount)` double count.
- **Bronze loads with `WRITE_TRUNCATE`** (idempotent, converges on source).
  **Silver uses MERGE** keyed on the ER's PKs (never rebuilds, never deletes).
- **`autodetect=False`, `null_marker="\N"`** on every load. Autodetect types
  `account` as INT64 and breaks `G11000`; the null marker keeps empty cells as
  empty strings rather than NULL.
- **Fail-closed everywhere.** Control totals, nil-proofs, required columns,
  closed enum sets all raise, and nothing is written when they do — not even
  partially. Validation completes before any cloud call.
- **`ifrs_rubric` re-sourced from CSV**, xlsx path deleted rather than kept
  alongside — two tables holding the same 15 requirements is the trap already
  present twice here.
- **`dim_ifrs_standard` is snowflaked** off `dim_ifrs_requirement` rather than
  denormalised, because `disclosure_summary` is a paragraph with 3 distinct
  values and repeating it 5× per standard invites drift.
- **`dim_entity_context` is key-value (EAV)** — normally an anti-pattern,
  correct here because the attributes are open-ended narrative, there is one
  entity, and the consumer reads prose rather than aggregating.

---

## 5. How correctness is established here

I do not review most code. These are what actually earn trust, strongest first:

1. **Differential testing.** Before rewiring `fact_trial_balance` onto the new
   bronze table, `EXCEPT DISTINCT` both directions proved the 990 rows were
   byte-identical. That is proof, not evidence.
2. **Domain invariants.** Trial balances sum to nil per entity per period.
   Each CoA tab carries its own `Total accounts: N` footer. These come from
   double-entry bookkeeping and from the files themselves — you cannot
   accidentally write them to agree with your own bug.
3. **External anchors.** SABIC FY2024 revenue = 117,736,492 from audited
   statements.
4. **Independent cell-by-cell validation.** Scripts that re-derive expected
   output from source *without importing the pipeline*, then diff against
   BigQuery. All 7 tables currently verified clean this way.
5. **Mutation testing.** Break a gating check on purpose, confirm exactly one
   test fails, revert. Done for three checks; extend to any new one.

**Expected row counts are load-bearing.** `EXPECTED_ROWS` in `push_to_bq.py`
is the backstop for several silent-failure modes in §6. Never loosen a count to
make a run pass.

---

## 6. Known gaps — real, unresolved

1. **No version control.** The repo is not a git repo. Highest-priority fix and
   the cheapest. Nothing here has ever been committed.
2. **Stale artifacts, live in BigQuery:** `bronze_trial_balance_raw` (990 rows,
   orphaned since the rewire) and `dim_affiliate` (2 rows, duplicate of
   `dim_entity`). Also `sql/silver_coa.sql` and `sql/silver_coa_checks.sql`,
   superseded by `silver_build.sql` / `silver_checks.sql`. These are traps for
   Agent 6 — two tables, same row counts, no marker of which is authoritative.
3. **`Table.classify()` infers row kind positionally** (code in col A + name in
   col B = account). A real account shipping without a name would be silently
   dropped. The CoA control total catches this; **the TB tables have no
   equivalent guard** — dropping a balanced Dr/Cr pair leaves the nil-proof at
   zero.
4. **`read_tables()` swallows `IngestError`** — that is how non-data tabs are
   skipped. A renamed header on a *real* data tab is therefore invisible; only
   the expected row count catches it.
5. **`find_period_columns()` treats anything right of the id columns as a
   period.** A stray "Total" column would be melted in as a fake period and
   would pass the nil-proof. The `9 distinct period labels` check catches it.
6. **MERGE never deletes.** A record removed upstream lives in silver forever
   and row-count checks stay green. `WHEN NOT MATCHED BY SOURCE THEN DELETE`
   would fix it but requires trusting the source is complete.
7. **`dim_ifrs_requirement` / `dim_account` are Type 1 SCD by accident.** A
   renamed account retroactively re-labels historical balances. Fine for a PoC;
   the point is it was never chosen.
8. **`period_end_date` assumes Gregorian calendar quarters.** Unconfirmed for
   other affiliates.
9. **`dim_account` / `dim_group_account` PK columns are NULLABLE** in BigQuery,
   unlike the other tables' REQUIRED keys.
10. **`bronze_entity_context_raw` has no `entity_code`.** Fine while one entity
    is described; the key becomes `(entity_code, context_key)` if that changes.

---

## 7. Concepts I am learning — reinforce these, do not re-explain from scratch

Covered already, with worked examples from this codebase:

| Concept | Where it bit |
|---|---|
| **Declaring the grain** | Row count churned 1,422→1,206→990 because grain was never declared. Mixed grain is the disease; row count is the symptom |
| **Natural / surrogate / composite keys** | `(entity_code, account_code)`; also "prefer the stable code over the descriptive name" when `dim_ifrs_requirement` moved to `standard_code` |
| **Conformed dimensions** | `dim_entity` and `dim_period` shared across both fact tables; `dim_affiliate` is the anti-pattern |
| **SCD Type 1 vs 2** | Currently Type 1 by accident. Type 2 would force surrogate keys |
| **Fail-fast / fail-closed** | Why most `try/except` in a pipeline is harmful; the two legitimate catches here |
| **Idempotency** | Truncate-reload vs merge-no-delete, and what each cannot catch |
| **Functional core, imperative shell** | `extract()` returns data; `write_csv()` does I/O |
| **Dependency direction** | Tests run with no GCP SDK because cloud imports sit inside functions |
| **Anti-corruption layer** | `ALIASES` maps `standard_name`→`standard` so source naming churn doesn't reach the warehouse |
| **EAV / key-value tables** | Usually an anti-pattern; correct for `entity_context`, and knowing *why* is the skill |
| **Snowflake vs denormalise** | Kimball says denormalise dimensions; that rule optimises for BI aggregation, not agent-read reference data |
| **Schema evolution** | Additive column-add works; rename and reorder do not. `WRITE_TRUNCATE` + explicit schema *replaces* a schema; MERGE never does |
| **Backfill before a key change** | Changing a MERGE key on a populated table doubles it unless old rows are backfilled first — they cannot match the new key while NULL |
| **Mutation testing** | A check never observed failing is not a check |
| **Differential testing** | The strongest tool when replacing something that already works |

Reading I have been pointed at: Kimball *The Data Warehouse Toolkit* ch. 1–2
(grain, keys, conformed dimensions), Gary Bernhardt's "Boundaries" talk,
Kleppmann *DDIA* batch chapter, `hypothesis` docs.

---

## 8. Working discipline to keep applying

Before writing code for any table, state and get confirmation on:

- **Grain** — what is one row, in a business sentence
- **Key** — which columns, and unique within what scope
- **Expected row count** — with the arithmetic
- **Invariants** — preferably ones the domain gives free

Then: build one table end-to-end and verified before replicating the pattern
(depth before breadth — building all extractors horizontally meant every lesson
learned on table three had to be retrofitted to one and two).

After writing code, surface the **decision surface** — the <5% of lines
encoding filtering rules, classification rules, join keys, casts and load
settings — in domain language, plus the input that would make each wrong.

There is also `.claude/skills/pipeline-discipline/SKILL.md` in the repo
encoding this as an auto-loading skill for Claude Code.
