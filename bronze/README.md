# Bronze Ingestion — Chart of Accounts

Aramco FC&RD PoC. Reads `PoC_Charts_of_Accounts.xlsx` and emits
`bronze_coa_raw.csv` per the agreed DBML: eight columns, all text, three chart
tabs stacked.

Scope is **CoA only**. Trial-balance ingestion is out of scope for this
deliverable.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on *nix
pip install -e .

# put the workbook where the config expects it
mkdir data && copy PoC_Charts_of_Accounts.xlsx data\

python -m unittest discover -s tests    # 36 tests, no pytest needed
python -m bronze_ingest                 # writes all 5 bronze CSVs to outputs/
python -m bronze_ingest --only bronze_coa_raw   # just one table
```

`pip install -e .` also installs a `bronze-ingest` command. On Windows Store
Python its Scripts directory is not on PATH by default, so `python -m
bronze_ingest` is the reliable form.

Useful flags:

```bash
python -m bronze_ingest --dry-run                  # run checks, write nothing
python -m bronze_ingest --input other.xlsx         # override the config
python -m bronze_ingest --config configs/alt.json
```

## Expected output

| Tab | Source rows | Dividers dropped | Landed |
|---|---:|---:|---:|
| CoA - Group (Aramco) | 89 | 11 | **78** |
| CoA - SABIC (2010) | 79 | 13 | **66** |
| CoA - Petro Rabigh (2380) | 55 | 11 | **44** |
| `bronze_coa_raw.csv` | | | **188** |

Landed rows equal each tab's own "Total accounts: N" footer exactly, so any
drift shows up as a direct mismatch and the run fails.

## Layout

```
bronze_ingestion/
├── pyproject.toml           declares the package; enables `pip install -e .`
├── requirements.txt         runtime deps (openpyxl only)
├── configs/                 one JSON per bronze table — paths and parsing knobs, no logic
├── src/
│   └── bronze_ingest/
│       ├── __init__.py      makes this an importable package
│       ├── __main__.py      thin CLI; `python -m bronze_ingest` runs all 5 tables
│       ├── excel.py         generic worksheet parsing — knows nothing of any bronze table
│       ├── sink.py          shared CSV writer — knows nothing of any bronze table either
│       ├── coa.py           bronze_coa_raw   — 3 chart tabs stacked
│       ├── tb.py            bronze_tb_raw    — affiliate trial balances, unpivoted
│       ├── group_tb.py      bronze_group_tb_raw — Aramco (parent-only) trial balance, unpivoted
│       ├── ifrs_rubric.py   bronze_ifrs_rubric_raw — IFRS disclosure requirements
│       ├── checklist.py     bronze_checklist_raw — required submission documents
│       └── cloud.py         GCS + BigQuery adapters, one registry entry per table
└── tests/                   one test file per extractor, stdlib unittest
```

`excel.py` and `sink.py` are separate from the five table modules because
they are genuinely domain-agnostic — the same header-discovery, melt, and
CSV-writing logic serves all five tables and would serve a sixth source
unchanged. Each table module owns its own schema, aliases, and
fail-closed checks; nothing about a specific workbook lives in `excel.py`.

**Every bronze row carries `source_file`** — the workbook filename it was
read from, added at ingest the same way `chart_scope`/`affiliate_code` are:
it is not a column in any sheet, and it disappears the moment tabs are
stacked, so it has to be captured at read time or not at all. It lets a row
be traced back to the exact file it came from when a workbook is re-shared
or re-versioned (e.g. the `(1)` copies verified value-identical on 12 Aug).

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

**The run fails rather than landing unverified data.** Each tab's control total
is reconciled before anything is written. A pipeline that lands unverified data
is worse than one that fails, because the failure is then discovered downstream
by someone who trusts the number.

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
