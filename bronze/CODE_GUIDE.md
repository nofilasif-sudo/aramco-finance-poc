# Code guide

Two parts. **Part 1** walks the code. **Part 2** pulls out the engineering
principles it is built on, each anchored to a specific line here — because a
principle you can point at is one you can actually use.

Roughly 800 lines across 8 Python files. Read `excel.py` first; everything
else is glue around it.

---

# Part 1 — How the code works

## 1.1 The one rule that explains the structure

```
        excel.py            knows spreadsheets.   imports: stdlib + openpyxl
           ▲
        coa.py              knows the contract.   imports: excel
           ▲
        cloud.py            knows GCS/BigQuery.   imports: coa
           ▲
   __main__.py, scripts/    know how you invoke it
```

**Dependencies point one way and never back.** `excel.py` has no idea charts of
accounts exist. `coa.py` has no idea BigQuery exists.

Two things fall out of that, and both are load-bearing:

- The 12 tests run on a laptop with **no GCP SDK installed**, because nothing
  in the tested path imports one.
- The same parser reads the trial balances tomorrow without being edited,
  because it was never told what a chart of accounts is.

## 1.2 One run, traced

`python scripts/push_to_bq.py`

| # | Call | Where |
|---|---|---|
| 1 | `main()` | `scripts/push_to_bq.py:87` |
| 2 | `extract(xlsx, CFG, report)` | `coa.py:99` |
| 3 | `read_tables()` yields `(title, Table)` per tab | `excel.py:169` |
| 4 | `Table.__init__` splits header / body / footer | `excel.py:112` |
| 5 | `chart_scope()` reads `(2010)` from the tab name | `coa.py:42` |
| 6 | `resolve_columns()` maps sheet headers → bronze columns | `excel.py:71` |
| 7 | per row: `drop_row()`, then `to_raw_str()` per cell | `coa.py:60`, `excel.py:26` |
| 8 | `check_control_total()` — **raises** on mismatch | `coa.py:77` |
| 9 | `to_csv_text()` | `coa.py:135` |
| 10 | `ensure_table()` → `load_csv_from_file()` | `cloud.py:128`, `cloud.py:187` |
| 11 | `verify()` — four SQL assertions | `scripts/push_to_bq.py:53` |

Note the ordering. **Everything reconciles before anything touches the cloud.**
Step 8 raises before step 9 has produced a byte.

## 1.3 `excel.py` — the only genuinely hard file (152 lines)

### `to_raw_str(value)` — line 26

Renders a cell as text. The entire function exists because of one fact:
**xlsx has no integer type.** Excel stores `1` as `1.0`. Without this, every
`level` lands as `"1.0"` and every amount as `"41750000.0"`.

```python
if isinstance(value, bool):          # BEFORE int — bool subclasses int
if isinstance(value, float):
    return str(int(value)) if value.is_integer() else repr(value)
```

`repr()` rather than `str()` for non-integers because `repr` round-trips
exactly. Dates become ISO because `3/31/24` is locale-ambiguous. Everything
else passes through untouched — **including leading spaces**, which encode the
Group chart's level-2 hierarchy and are therefore data, not formatting.

### `norm_header(value)` — line 57

Lowercase, collapse whitespace, trim. **For matching only, never for output.**
Excel headers routinely carry a trailing space or an alt-enter line break that
is invisible on screen; matching the raw string makes the pipeline fail for a
reason nobody can see by looking.

### `resolve_columns(headers, aliases, required, where)` — line 71

Maps sheet headers to output columns through an alias table. Returns **only
the columns actually present**, and raises if a required one is missing.

That "only present" behaviour is what makes `level` and `source_reference`
land empty for the affiliate charts instead of erroring — those tabs genuinely
do not have those columns.

### `Table.__init__` — line 112

A three-state pass over the rows:

```python
in_body = True
for row in rows[self.header_idx + 1:]:
    if in_body:
        if is_blank_row(row): in_body = False    # first blank ends the table
        else:                 self.data.append(row)
    elif not is_blank_row(row):
        self.footer.append(row)                  # "Total accounts: 78"
```

Both boundaries are **discovered**. The header is found by sentinel
(`_find_header`, line 131); the body ends at the first blank row. Verified
against the pack: no blank row occurs *inside* a table, so this cannot
truncate early.

### `Table.classify(row)` — line 142

```
code in col A and name in col B  → account
text in col A only               → section    "NON-CURRENT ASSETS"
text in col B only               → subtotal   "  Subtotal - Revenue"
```

**The most important function in the codebase.** Not because it is clever —
it is ten lines — but because it is used by *both* `drop_row` and the control
total. If the definition of "account" were ever wrong, the rows landed and the
reconciled count would move **together**, so the mismatch surfaces loudly
instead of silently dropping rows. See §2.3.

### `read_tables(path, sentinel)` — line 169

```python
try:
    yield ws.title, Table(ws.title, rows, sentinel)
except IngestError:
    continue
```

**The `except` clause is the tab filter.** "Read me" and "Anchors & sources"
are skipped because they have no header row — not because they appear on a
blacklist. Add a fourth prose tab tomorrow and nothing changes.

It is a generator, so tabs stream rather than all loading at once, and
`wb.close()` sits in a `finally`.

## 1.4 `coa.py` — the contract (129 lines)

| Thing | Line | Purpose |
|---|---|---|
| `COLUMNS` | 23 | The DBML schema. The CSV header and the BigQuery schema are both generated from it |
| `ALIASES` | 26 | `category` accepts two spellings: the CoA tabs say `Category (FS caption group)`, the TB tabs say `Category` |
| `REQUIRED` | 39 | `level` / `source_reference` deliberately absent — Group-only |
| `chart_scope()` | 42 | Regex `(NNNN)` from the tab name, else `GROUP`, else **raise** |
| `drop_row()` | 60 | Two lines. The only filtering the pipeline performs |
| `check_control_total()` | 77 | Parse the footer, compare, **raise** on mismatch |
| `extract()` | 99 | The orchestrator |
| `to_csv_text()` / `write_csv()` | 135 / 154 | Split so the same bytes reach a file, a GCS blob, or a test |

`chart_scope` **raising** rather than defaulting matters more than it looks:
42 account codes are shared between the two affiliate charts, so a row with a
blank scope is permanently unjoinable. A failed run beats 44 orphan rows.

The row-building loop repays close reading:

```python
rec = dict.fromkeys(COLUMNS, "")     # all 8 keys, all empty, correct order
rec["chart_scope"] = scope            # the column added at ingest
for col, i in pos.items():            # only the columns THIS tab has
    rec[col] = to_raw_str(row[i] if i < len(row) else None)
```

`dict.fromkeys` guarantees every record carries all eight keys in schema
order. `if i < len(row)` guards short rows — openpyxl can hand back a row
narrower than its header.

## 1.5 `cloud.py` — boring on purpose (162 lines)

**Every cloud import sits inside a function.** That is what keeps the package
importable, and the tests runnable, on a machine with no GCP SDK.

| Thing | Line | Note |
|---|---|---|
| `DESCRIPTIONS` | 22 | Column descriptions. **Product, not documentation** — Agent 6 is text-to-SQL, and the description is what tells it which column to pick |
| `bq_schema()` | 68 | One definition feeds both `CREATE TABLE` and the load job, so they cannot disagree |
| `dataset_location()` | 114 | **Discovered, not configured.** Dataset location is immutable and cross-location loads are a hard error |
| `ensure_table()` | 128 | `exists_ok=True` on both calls — can never drop or alter |
| `_load_config()` | 148 | The four settings carrying all the risk |

`_load_config` is the highest-consequence handful of lines in the repo:

```python
autodetect=False                    # else `account` becomes INT64 and G11000 breaks
null_marker="\\N"                   # else empty fields load as NULL, not ""
skip_leading_rows=1
write_disposition=WRITE_TRUNCATE    # else a re-run gives 376 rows
```

## 1.6 Entry points

**`__main__.py`** (64) — local CLI, CSV only, never touches the cloud. Named
`__main__.py` so `python -m bronze_ingest` works. Resolves config paths against
the repo root rather than your shell's working directory.

**`scripts/push_to_bq.py`** (135) — the script that ran. Settings at the top;
`verify()` runs four SQL assertions and returns a bool so the process exit code
means something.

**`scripts/build_silver.py`** (47) — reads two `.sql` files and executes them.
No SQL embedded in Python strings: the SQL lives in `sql/` where it can be
reviewed, diffed, and run by hand.

## 1.7 `tests/test_coa.py` — 12 tests, 103 lines

The `fixture()` at line 29 is **deliberately unlike the real data**: header on
row 4 instead of row 6, and the `Category (FS caption group)` spelling. So the
suite fails if header discovery ever regresses to a fixed row, or if alias
resolution is removed.

Each test protects a decision, not a shape:

- header found not assumed · aliases resolve
- divider dropped · **indentation preserved**
- Group-only columns land empty for affiliates
- **a bad control total refuses to land**
- keeping dividers is a config switch, not a rewrite
- `4.175e7` → `41750000`
- CSV header matches the DBML exactly
- no `\r\n` on any platform

---

# Part 2 — The engineering fundamentals

Twelve ideas. Each one: what it is, why it is true from first principles,
where it appears in this code, and what breaks without it.

## 2.1 Layering — dependencies point one way

**The idea.** Arrange modules so that imports form a directed acyclic graph,
with the most general code at the bottom and the most situational at the top.

**Why.** A module can only be reused in contexts that satisfy all of its
imports. `excel.py` imports openpyxl and the standard library, so it runs
anywhere Python runs. If it imported `google.cloud.bigquery`, it would run
only where GCP credentials exist — including in your test suite. **Every
dependency you add narrows where the code can live.**

**Here.** `excel.py → coa.py → cloud.py → entry points`. Verified: `from
bronze_ingest import cloud` succeeds with no GCP SDK installed, because
`cloud.py` imports the cloud libraries *inside* its functions.

**Without it.** Your teammate's `transformer.py` imports pandas at module
level and `main.py` patches `sys.path` — so the parsing logic can only run
from the repo root with pandas installed. The logic is fine; its reach is not.

## 2.2 Functional core, imperative shell

**The idea.** Push side effects — file I/O, network, clocks, randomness — to
the outside edge. Keep the middle as functions that take data and return data.

**Why.** A pure function is testable with no setup: call it, compare the
return value. An impure one needs a filesystem, a network, or a mock. The
ratio of pure to impure code is roughly the ratio of your codebase you can
test cheaply.

**Here.** `extract()` takes a path *or a `BytesIO`* and returns
`(rows, meta)` — it never writes. `to_csv_text()` returns a string;
`write_csv()` is the thin impure wrapper that puts that string on disk. That
split is exactly why the same function serves a laptop, a notebook, and a
Cloud Run job.

**The tell.** When a function both computes and writes, you cannot test the
computation without tolerating the write.

## 2.3 Single source of truth

**The idea.** Any fact should be expressed in exactly one place. Everything
else derives from it.

**Why.** Two copies of a fact are two things that can disagree, and nothing
will tell you when they do. The failure is always silent, because both copies
looked right when written.

**Here, three times over:**

- `COLUMNS` (`coa.py:23`) generates the CSV header *and* the BigQuery schema
  (`cloud.py:68`). The file and the table cannot drift apart.
- `Table.classify()` is used by both `drop_row()` and `check_control_total()`.
  A wrong definition of "account" moves both numbers together, so the control
  total catches it.
- `DESCRIPTIONS` lives in Python, and `sql/bronze_coa_raw.sql` is *exported*
  from the live table rather than hand-maintained.

**Where this repo still has two copies:** the column descriptions exist in
`cloud.py` and in the exported `.sql`. The runbook names which one wins.
Acknowledged duplication with a stated owner beats duplication nobody noticed.

## 2.4 Fail closed, and fail loudly

**The idea.** When something is wrong, stop. Do not substitute a default and
continue.

**Why.** A pipeline that fails is discovered by you, now, with a stack trace.
A pipeline that defaults is discovered by someone else, later, via a wrong
number in a meeting. The second costs more by orders of magnitude — not
because the bug is worse, but because the *distance* between cause and
symptom is.

**Here.** `chart_scope()` raises on an unattributable tab.
`check_control_total()` raises on mismatch. `resolve_columns()` raises on a
missing required column. The silver SQL uses `ERROR()` rather than
`ELSE NULL` on the statement mapping. And when any of them raises,
**no CSV is written at all** — not a partial one.

The stated version: *bronze stays empty rather than wrong.*

**The counter-case.** `read_tables()` swallows `IngestError` deliberately —
because there, a missing header genuinely means "not a data tab". Swallowing
an exception is fine when the exception *is* the signal. It is a bug when it
is merely inconvenient.

## 2.5 Derive rather than configure

**The idea.** If the input can tell you something, do not put it in a config
file.

**Why.** Configuration is a second copy of a fact (§2.3) that has to be kept
in sync by a human. Every hardcoded row number, tab name, or column count is a
silent bomb: when the source shifts, the code keeps running and produces
plausible, wrong output.

**Here.** Header row: found by sentinel. Table end: found at the first blank
row. Tabs: selected structurally. `chart_scope`: parsed from the tab name.
BigQuery location: read from the existing dataset (`cloud.py:114`).

Grep the source for `2024`, `Q1`, or a row number — there are none.

**What legitimately stays in config:** the *patterns*, not the values.
`affiliate_pattern` is a regex, not a list of affiliates. Adding a fourth
chart next quarter requires no code change and no config change.

**Contrast.** Your teammate's config has `"period_start_column": 4`. That is a
magic integer standing in for "wherever the id columns end". Insert one column
upstream and it melts the wrong thing, silently.

## 2.6 Separate policy from mechanism

**The idea.** *What* the system does should be adjustable without touching
*how* it does it.

**Why.** Decisions get revisited; mechanisms rarely do. If the two are
tangled, revisiting a decision means re-testing the mechanism.

**Here.** Dropping section dividers is a policy — it deviates from a signed
contract and may be reversed. So it is one config flag (`drop_section_rows`)
consumed by one two-line function (`coa.py:60`), and there is a test proving
the `false` path still works. Reversing the decision is a config edit, not a
code change.

**The smell to avoid.** A policy expressed as a filter buried inside a loop.
Your teammate's pipeline drops subtotals as a *side effect* of
`account_text.isdigit()` — a numeric test doing policy work. Nothing in the
code says subtotals were excluded, and nobody reading it would know.

## 2.7 Idempotency

**The idea.** Running the operation twice leaves the same state as running it
once.

**Why.** Reruns are inevitable — a crash, a retry, a nervous engineer pressing
the button again. If a rerun changes the answer, every incident becomes two
problems.

**Here.** `WRITE_TRUNCATE` on the load. `CREATE OR REPLACE TABLE` on the two
silver dimensions. The local CSV write is a clean overwrite.

**And the deliberate exception.** `map_account_to_group` uses
`CREATE TABLE IF NOT EXISTS`, because it will hold Agent 3's output — data
bronze cannot regenerate. Rebuilding must never wipe it. The asymmetry between
those two statements is the whole point; do not let anyone tidy them into the
same form.

**The failure worth fearing.** Append instead of truncate gives 376 rows and
*every check still passes proportionally*. Control totals scale, the trial
balance still foots. Wrong, but internally consistent. Those are the bugs that
survive review.

## 2.8 Determinism and reproducibility

**The idea.** Same input, same bytes out. Every time, on every machine.

**Why.** It is what makes a hash comparison meaningful, which is what makes
"did anything actually change?" answerable without reading the data.

**Here.** `lineterminator="\n"` (`coa.py:148`) — Python's `csv` defaults to
`\r\n`, which would make your laptop and CI produce different hashes from
identical data. Row order is deterministic: tab order, then sheet row order.
Verified: two consecutive runs produced byte-identical files.

**The general rule.** Anything that varies between runs — timestamps, UUIDs,
dict iteration order in older Pythons, `os.listdir` ordering — has to be
either eliminated or quarantined. The manifest keeps `run_utc` in exactly one
field, so everything else in it is diffable.

## 2.9 Self-proving pipelines

**The idea.** A pipeline should carry checks that would fail if it were wrong,
and refuse to publish when they do.

**Why.** Tests prove the code does what you wrote. They cannot prove the data
is right, because the data was not there when you wrote them. Only a check
that runs against the *actual* input can do that.

**The strongest checks are ones you did not invent.** They come from the
domain:

- A trial balance sums to zero. That is double-entry bookkeeping, not a
  convention — nobody can argue with it.
- Each chart tab carries its own `Total accounts: N`. The source is asserting
  a fact about itself.
- Every fact row must find its dimension row. Referential integrity.

**Here.** Three control totals, four post-load assertions, thirteen silver
assertions. And because dividers are dropped and the CoA tabs have no
subtotals, rows landed now *equals* the footer exactly — the strongest form,
where the check is an equality rather than a tolerance.

**Recompute, do not read.** The TB pipeline sums the account rows itself
instead of reading the sheet's own `TRIAL BALANCE CHECK` row. That row is a
formula: it can reference the wrong range or carry a stale cached value.
Recomputing from the cells you are actually landing tests *the output*. The
sheet's own row is then a genuinely independent second opinion.

## 2.10 Contracts, and what counts as breaking one

**The idea.** An agreed schema is an interface. Changing it unilaterally is
the same class of act as changing a function signature other people call.

**Why.** Downstream code encodes assumptions you cannot see. The cost of a
contract change is not the edit; it is everyone else's rework, and it lands on
them without warning.

**Here.** The DBML said bronze does no filtering. Dropping section dividers
breaks that. The response was not to skip it or to do it quietly, but to:

1. gather evidence (all 16 captions duplicated by `category`; dividers are
   positional and BigQuery is unordered)
2. make it reversible (one flag, one test)
3. document it in the code, the README, and the runbook
4. **flag it for sign-off and mark it unresolved**

That is what a defensible deviation looks like. The alternative — a filter
added in a loop with no note — is how a data model quietly stops matching its
documentation.

## 2.11 Test behaviour, not shape

**The idea.** An assertion should fail when a decision regresses, not merely
when the code crashes.

**Why.** `assert not df.empty` passes with wrong values, missing rows, wrong
keys, and a broken transformation. It only catches total failure — which you
would have noticed anyway.

**Here.** The fixture is *deliberately unlike production data*: header on row
4, not row 6. That single choice means the suite fails if header discovery
regresses to a fixed row. Similarly, `test_control_total_mismatch_refuses_to_land`
exists because **a check that has never been observed to fail is not a check.**

**The rule of thumb.** For each test, ask: what change to the source would make
this fail? If the honest answer is "deleting the function", the test is
measuring existence, not behaviour.

## 2.12 Packaging and imports — why `sys.path` is a smell

**The idea.** A Python package is a directory with `__init__.py`, declared in
`pyproject.toml`, installed with `pip install -e .`. Then `import x` resolves
from anywhere.

**Why the `src/` layout.** If the package sits at the repo root, `import
bronze_ingest` silently finds the local folder whether or not it is installed.
Put it under `src/` and Python cannot find it by accident — it resolves only
if genuinely installed. **Broken packaging then fails on your laptop instead
of on Hussein's.**

**Here.**

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

plus an `__init__.py`. That is the whole mechanism.

**The smell.** `sys.path.insert(...)` at the top of a file means the package is
not installed and imports are being forced. Your teammate's project has it in
three files, load-bearing in all three. This repo has it once, in
`scripts/push_to_bq.py:18` — redundant, since the package *is* installed, and
kept only as a fallback for someone who cloned without installing. Worth
knowing it is there.

---

# Part 3 — Where Azure instincts mislead here

| Your instinct | Reality in this stack |
|---|---|
| Partition everything, it is cheap | A BigQuery partition carries real metadata cost. At 188 rows: do not. Clustering does nothing under roughly 1 GB |
| Region is a performance choice | A BigQuery dataset's location is **immutable**, and BigQuery **refuses to join across locations**. It is an architecture decision, made once |
| Cross-region reads just cost egress | A load from a GCS bucket in a different location is a **hard error**, not a slow path |
| Let the loader infer types | `autodetect=True` types `account` as `INT64`, making `G11000` and `1100` incompatible, and silently breaks the all-STRING contract |
| Empty string and NULL are much the same | BigQuery's CSV loader turns empty unquoted fields into NULL. `level` is empty on all 110 affiliate rows — the distinction is load-bearing |
| Float is fine for money | Use `NUMERIC`. Exact decimal means "sums to zero" means *exactly* zero, which is the claim you make to the client |
| A notebook is the deliverable | It is not orchestratable. Keep cells thin so the logic moves to a job without a rewrite |

---

# Reading order

1. `src/bronze_ingest/excel.py` — 152 lines, and everything else is glue
2. `src/bronze_ingest/coa.py` — the contract made executable
3. `tests/test_coa.py` — reads as a specification of the decisions
4. `scripts/push_to_bq.py` — how it reaches BigQuery
5. `RUNBOOK.md` — exact commands and expected output
