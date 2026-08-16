# Contribution accounting — who did what

An honest split of human vs AI contribution on this project. Written to be
accurate rather than flattering in either direction. Every claim below is
traceable to a specific moment in the working sessions.

**Codebase size for reference:** 3,352 lines (2,335 Python across 22 files,
1,017 SQL across 6 files), plus configs, DBML and docs.

---

## Summary

| | Human | AI |
|---|---|---|
| **Role** | Domain owner, architect, reviewer | Implementer, verifier |
| **Decisions that determined correctness** | Most | Few |
| **Lines authored** | ~0 | ~3,350 |
| **Errors caught that would have caused real damage** | 3 | 4 |
| **Errors introduced** | 0 direct (process gaps instead) | 6 |

The short version: **the human decided what was correct; the AI decided how to
express it.** Both mattered, and neither substitutes for the other — but they
are different kinds of work and should not be conflated on a CV or in a review.

---

## Human contributions

### 1. Prevented destructive writes to live tables — highest-impact catch

> *"these exist in big query already no need to recreate: dim_account,
> dim_affiliate, dim_entity, dim_group_account, dim_period,
> fact_trial_balance, map_account_to_group"*

The AI was about to generate `CREATE OR REPLACE TABLE` against seven populated
silver tables. None of them were mentioned in the DBML specs it was working
from. This knowledge existed only in the human's head, and the intervention
changed the entire silver strategy from rebuild to MERGE-upsert.

**Consequence if missed:** destroyed 990 fact rows and four populated
dimensions. Recoverable, but only by rebuilding from bronze — and
`map_account_to_group` would not have been recoverable at all.

### 2. Enforced the architectural boundary

> *"ONLY USE WHAT IS IN BRONZE_INGESTION"*

The AI had started reading a loose `ingest_bronze.py` from the Downloads
folder to reuse its trial-balance logic. This instruction forced that logic to
be **rebuilt** against the package's own primitives rather than copy-pasted
from an unversioned external script.

**Consequence:** `tb.py` and `group_tb.py` share `excel.py`'s discovery and
melt helpers instead of carrying duplicate copies of parsing logic that could
drift. This is the reason the codebase has one implementation of row
classification rather than three.

### 3. Rejected the divider rows on domain reasoning

> *"seeing dividers does not make any sense as category shows what is the
> divider"*

Correct, and stated before any evidence existed. Verification afterwards
confirmed it: all 33 divider captions across three sheets appear verbatim in
the `category` column. This is domain judgement applied to a modelling
decision — the kind of call the AI can verify but was not going to originate,
because it had already implemented the opposite.

### 4. Made the grain call on subtotals

> *"also drop subtotal rows from bronze_tb_raw first"*

Set the grain of `bronze_tb_raw` to one row per account per period. This is
what made `fact_trial_balance`'s rewire clean, and it eliminated a latent
double-counting bug (subtotal rows contain the accounts above them, so
`SUM(amount)` over a mixed-grain table double counts while the nil-proof still
passes).

### 5. Instituted the "explain why" rule

> *"Always explain why change is needed?"*

Applied to every subsequent change. This is a process contribution rather than
a technical one, and it measurably improved the reasoning quality of everything
after it — including surfacing the intent behind decisions that would otherwise
have been silent.

### 6. Code review that caught a real defect

> *"everything is xlsx wht import csv?"*

Caught `import csv` being added to `excel.py`, a module whose entire purpose is
spreadsheet reading. Result: CSV writing was split into a separate `sink.py`
instead of polluting the parsing module — which later made `flatcsv.py` a
natural sibling rather than a branch inside `excel.py`.

### 7. Drove verification standards

A series of demands that shaped the quality bar:

- *"I need help visualizing test cases and actually validating them"* → led to
  mutation testing of three gating checks
- *"validate group trial balance csv with original xlsx"* → led to independent
  cell-by-cell validation, later extended to all tables
- *"is new trial balance file and coa file unchanged or were there
  differences"* → caught the AI trusting a DBML claim it had not verified
- *"does our code create tables everytime or only merge executes"* → surfaced
  the truncate-vs-merge distinction and what merge cannot catch
- *"anything weird in existing ER, does it conflict with what is in BigQuery"*
  → produced eight documented findings including two live stale tables

### 8. Process control

Repeatedly stopped implementation to demand a plan first
(*"only create a plan for now"*, *"review with me then only start"*), and
correctly reversed an earlier decision to defer `fact_trial_balance` once its
dependencies were clear.

### 9. Source-of-truth decisions

Chose `entity_context (1).csv` over the alternative; chose to re-source the
IFRS rubric from CSV rather than maintain two tables. Both were decisions with
stated tradeoffs, made after being shown the options.

---

## AI contributions

### Authored essentially all code

- 7 bronze extractors, `excel.py` extensions, `flatcsv.py`, `sink.py`
- `cloud.py` generalised from a CoA-only script into a table registry
- `push_to_bq.py` generalised from one table to a verified loop over seven
- `silver_build.sql` (MERGE upserts) and `silver_checks.sql` (30 DQ checks)
- 56 unit tests
- Both DBML diagrams, the runbook-level docs, and the verification scripts

### Found four defects that would have caused real failures

1. **Schema mismatch on `bronze_coa_raw`.** The live table had 8 columns; the
   pipeline now emitted 9. `ensure_table()` never touched existing schemas, so
   the next real load would have failed outright. Fixed by making schema
   evolution additive.
2. **The MERGE key change would have doubled `dim_ifrs_requirement` to 30
   rows.** Existing rows had `standard_code = NULL`, so a MERGE on the new key
   would match nothing and insert 15 duplicates beside 15 orphans. Caught while
   writing the SQL, not by running it. Fixed with an idempotent backfill.
3. **`csv_encoding` collision.** Reusing that config key for reading would have
   set `utf-8-sig` on *output* and injected a BOM into every bronze CSV — the
   exact thing the original config deliberately avoided.
4. **Load-order dependency.** The rubric's referential check queries
   `bronze_ifrs_standard_raw`, which loaded after it — a cold run would have
   failed. Reordered so parent precedes child.

### Built the verification apparatus

Independent validators that re-derive expected output from source *without
importing the pipeline*, comparing amounts numerically so they cannot inherit
the pipeline's own formatting logic. All 7 bronze tables verified cell-by-cell
this way. Also the mutation-testing procedure that proved three gating checks
actually fire.

### Noticed the row-count discrepancy

The first dry run produced 1,206 rows where the DBML specified 1,422. The AI
flagged the mismatch rather than adjusting the expected number — though the
underlying cause was its own error (see below).

---

## Errors, both sides

### AI errors

1. **Applied the CoA divider-drop policy to the trial-balance tables without
   checking** whether it was warranted there. Caused the 1,422 → 1,206 → 990
   churn and three BigQuery reloads.
2. **Wrote a plan with a wrong premise** (1,422 rows for `bronze_tb_raw`) that
   was falsified within the hour.
3. **Predicted a column-ordering problem that did not occur** and advised
   accepting a tradeoff that did not exist — `WRITE_TRUNCATE` replaces schemas
   outright.
4. **Two bugs in its own validation scripts** — failed to trim Excel padding,
   then stripped whitespace it should have preserved. Both produced false
   alarms that were caught; a bug in the other direction would have falsely
   reported "clean".
5. **Trusted the DBML's claim** that the `(1)` workbook copies were identical,
   rather than verifying — only checked when asked.
6. **Nearly added an inappropriate import** to `excel.py`.

### Human process gaps

1. **No version control.** Still true. Nothing in this project has ever been
   committed. 3,352 lines exist in exactly one mutable copy.
2. **Grain decided after implementation rather than before.** The divider and
   subtotal calls were both correct and both made post-deployment; made
   up-front they would have cost one sentence instead of three reloads and
   ~15 documentation edits.
3. **Approved a plan without catching its wrong premises.** The 1,422 figure
   was in the approved plan and went unchallenged until execution.
4. **Scope oscillation** on `fact_trial_balance` — deferred to a teammate, then
   reclaimed.

---

## Honest characterisation

**What the human genuinely owns:** every decision that determines whether the
warehouse is *correct* rather than merely *working*. Composite keys, grain,
what gets filtered, which source is authoritative, what stays out of scope, and
the standard of proof demanded before anything was trusted. Three interventions
prevented real damage. The architectural boundary call (§2) measurably improved
the code's structure.

**What the AI genuinely owns:** all implementation, all tests, all
verification tooling, and four defect catches — two of which (the schema
mismatch and the MERGE-doubling) were found by reasoning about the code rather
than by running it.

**The dependency worth naming:** the human cannot currently verify most of what
was built by reading it. Quality control came from domain knowledge, demanded
verification, and the unusual richness of this domain's built-in checks — trial
balances prove to nil, sheets carry their own control totals, published figures
exist to tie back to. That combination worked here. On a dataset without those
properties, the same working method would have far less to catch errors with,
and the absence of version control would matter considerably more.

**The fair summary for external audiences:** this is architecture, domain
modelling and technical review work, executed with AI implementation. That is a
real and increasingly common engineering role. It is not the same as having
authored the code, and describing it as either "I built a data pipeline" or
"the AI built it" would both be misleading.
