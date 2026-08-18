"""Ingest both FS documents, then publish each to BigQuery.

    set GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\key.json
    python scripts/push_to_bq.py --check     # connectivity + plan, no writes
    python scripts/push_to_bq.py             # create -> load -> verify, both
    python scripts/push_to_bq.py --only fs_seeded

    # write to a different dataset (e.g. to validate before ever touching
    # the live bronze dataset):
    python scripts/push_to_bq.py --dataset bronze_staging

Three GLOBAL phases, each covering both tables before the next begins, so
"create the tables" and "run the load" can be pointed at independently:

  [1/3] CREATE  extract + run the STRUCTURAL checks locally (raises before
        any cloud call if a document did not read correctly), then apply
        the fixed DDL (sql/<table>.sql, CREATE TABLE IF NOT EXISTS).
  [2/3] LOAD    load every table that survived phase 1. The rendered CSV
        never touches local disk: it is either uploaded straight from
        memory to GCS (if BUCKET is set — a byte-for-byte lineage
        artifact) then loaded from there, or streamed into the load job.
  [3/3] VERIFY  re-query BigQuery for every table that loaded — proves the
        LOADED TABLE is right, not just that the CSV was.

A table that fails phase 1 is dropped from phases 2/3 but does not stop the
other — one unreadable document should not block the other.

Reuses the same registry as `python -m fs_ingest`, so the two entry points
cannot define a different set of tables or configs.

Structurally a twin of bronze_ingest/scripts/push_to_bq.py, deliberately, so
the two can be merged later without rethinking the phase model.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_ingest import cloud, statements                      # noqa: E402
from fs_ingest.__main__ import TABLES, load_config           # noqa: E402
from fs_ingest.errors import IngestError                     # noqa: E402
from fs_ingest.schema import COLUMNS                         # noqa: E402
from fs_ingest.sink import to_csv_text                       # noqa: E402

# --- settings ---------------------------------------------------------------
PROJECT = "aramco-finance-poc-c2a4"
DATASET = "bronze"            # matches the dataset already in the project
# Only used if the dataset does not exist yet; otherwise its real location
# wins, because a dataset's location is immutable and cross-location loads
# are a hard error.
DEFAULT_LOCATION = "me-central2"   # Dammam — the existing bronze/silver
                                   # datasets are both here
# Set to None to load straight from memory (no bucket needed).
BUCKET = "aramco-finance-poc-raw-landing"
CSV_BLOB_DIR = "staging"

# 19x2 + 43x2 + 7 + 5 + 3x3 + 7. Identical for both documents: the seeded
# defects change two figures and one note reference, never the shape. A
# mismatch means the pack changed and needs eyes on it before it lands.
EXPECTED_ROWS = {"fs_clean": 152, "fs_seeded": 152}

# The only rows with no figure are the balance sheet's five bold group
# headers, once per period.
EXPECTED_NULL_VALUES = 10


@dataclass
class TableState:
    """Carries one table's state across the create -> load -> verify phases."""
    name: str
    table_id: str
    location: str = DEFAULT_LOCATION
    rows: list = field(default_factory=list)
    csv_text: str = ""
    created: bool = False
    loaded: bool = False
    ok: bool = True
    error: str = ""


def _run_checks(client, checks) -> bool:
    all_ok = True
    for label, sql in checks:
        row = list(client.query(sql).result())[0]
        detail = ", ".join(f"{k}={row[k]}" for k in row.keys() if k != "ok")
        status = "OK" if row["ok"] else "FAIL"
        all_ok &= bool(row["ok"])
        print(f"    [{status}] {label}" + (f"  ({detail})" if detail else ""))
    return all_ok


def verify(client, table_id: str, expected: int) -> bool:
    """Post-load verification.

    STRUCTURAL ONLY. Do NOT add footing or tie-out checks here: fs_seeded
    carries three planted defects on purpose, and a check that failed on them
    would mark the load FAILED and block the very table the PoC exists to
    examine. Arithmetic belongs in silver, where a break is a finding.

    Worth proving beyond the usual: that the TYPES survived. This pair is the
    only non-STRING table in bronze, so a regression to the all-STRING
    default would land silently and surface much later as a wrong answer.
    """
    dataset, table = table_id.split(".")[1], table_id.split(".")[2]
    checks = [
        (f"row count == {expected}",
         f"SELECT COUNT(*) = {expected} AS ok, COUNT(*) AS actual "
         f"FROM `{table_id}`"),

        ("value/is_bold/row_ord kept their types (NUMERIC/BOOL/INT64)",
         f"SELECT LOGICAL_AND(matches) AS ok, "
         f"STRING_AGG(FORMAT('%s=%s', column_name, data_type)) AS types "
         f"FROM (SELECT column_name, data_type, data_type = CASE column_name "
         f"WHEN 'value' THEN 'NUMERIC' WHEN 'is_bold' THEN 'BOOL' "
         f"ELSE 'INT64' END AS matches "
         f"FROM `{PROJECT}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
         f"WHERE table_name = '{table}' "
         f"AND column_name IN ('value','is_bold','row_ord'))"),

        ("6 statements",
         f"SELECT COUNT(DISTINCT statement) = 6 AS ok, "
         f"COUNT(DISTINCT statement) AS actual FROM `{table_id}`"),

        ("no NULL in the NOT NULL columns",
         f"SELECT COUNTIF(statement IS NULL OR label IS NULL "
         f"OR is_bold IS NULL OR row_ord IS NULL) = 0 AS ok "
         f"FROM `{table_id}`"),

        ("no blank statement or label",
         f"SELECT COUNTIF(TRIM(statement) = '' OR TRIM(label) = '') = 0 "
         f"AS ok FROM `{table_id}`"),

        ("natural key (statement, row_ord, column_label) is unique",
         f"SELECT COUNT(*) = COUNT(DISTINCT FORMAT('%s|%d|%s', statement, "
         f"row_ord, COALESCE(column_label, ''))) AS ok, COUNT(*) AS total "
         f"FROM `{table_id}`"),

        # The null marker really did become NULL rather than the literal two
        # characters. bronze_ingest's tables assert the opposite (no NULLs at
        # all); this pair needs its NULLs, so the check runs the other way.
        ("null marker resolved — no literal backslash-N landed",
         f"SELECT COUNTIF(statement = r'\\N' OR label = r'\\N' "
         f"OR section = r'\\N' OR subsection = r'\\N' "
         f"OR column_label = r'\\N') = 0 AS ok FROM `{table_id}`"),

        (f"value IS NULL only on bold group headers "
         f"({EXPECTED_NULL_VALUES} rows)",
         f"SELECT COUNTIF(value IS NULL) = {EXPECTED_NULL_VALUES} "
         f"AND COUNTIF(value IS NULL AND NOT is_bold) = 0 AS ok, "
         f"COUNTIF(value IS NULL) AS null_values FROM `{table_id}`"),

        ("row_ord runs 1..n with no gaps in every statement",
         f"SELECT LOGICAL_AND(lo = 1 AND hi = n) AS ok FROM "
         f"(SELECT MIN(row_ord) AS lo, MAX(row_ord) AS hi, "
         f"COUNT(DISTINCT row_ord) AS n FROM `{table_id}` "
         f"GROUP BY statement)"),

        # Signed as presented: if parentheses stopped becoming negatives,
        # every cost would flip positive and every downstream subtotal check
        # would break in a way that looks like a document defect.
        ("parenthesised figures landed negative",
         f"SELECT COUNTIF(value < 0) > 0 AS ok, COUNTIF(value < 0) "
         f"AS negatives FROM `{table_id}`"),

        ("notes are flat — no section or subsection",
         f"SELECT COUNTIF(STARTS_WITH(statement, 'Note ') AND "
         f"(section IS NOT NULL OR subsection IS NOT NULL)) = 0 AS ok "
         f"FROM `{table_id}`"),
    ]
    return _run_checks(client, checks)


def create_one(client, name: str, config_file: str, args) -> TableState:
    """Phase 1: extract + check locally, then apply the fixed DDL.
    Never raises — a failure here must not stop the other table."""
    print(f"\n=== {name} ===")
    state = TableState(name=name, table_id=f"{PROJECT}.{DATASET}.{name}")

    try:
        cfg = load_config(ROOT / "configs" / config_file)
    except FileNotFoundError as exc:
        print(f"[1/3] FAILED: {exc}", file=sys.stderr)
        state.ok, state.error = False, str(exc)
        return state

    print(f"[1/3] extract  {Path(cfg['input_path']).name}")
    report: list[str] = []
    try:
        state.rows, _ = statements.extract(cfg["input_path"], cfg, report)
    except (IngestError, FileNotFoundError) as exc:
        for line in report:
            print("   ", line)
        print(f"\nFAILED: {exc}", file=sys.stderr)
        state.ok, state.error = False, str(exc)
        return state
    for line in report:
        print("   ", line)

    expected = EXPECTED_ROWS.get(name)
    if expected is not None and len(state.rows) != expected:
        msg = f"expected {expected} rows, got {len(state.rows)}"
        print(f"\nFAILED: {msg}", file=sys.stderr)
        state.ok, state.error = False, msg
        return state

    state.csv_text = to_csv_text(COLUMNS, state.rows)
    print(f"    {len(state.rows)} rows, {len(state.csv_text):,} bytes "
          f"(in memory, no local file written)")

    state.location = cloud.dataset_location(client, f"{PROJECT}.{DATASET}",
                                            DEFAULT_LOCATION)
    print(f"    target {state.table_id}  (location {state.location})")

    if args.check:
        print("--check: read and checked. DDL/load/verify skipped, "
              "nothing written.")
        return state

    cloud.ensure_table(client, state.table_id, state.location,
                       ROOT / "sql" / f"{name}.sql")
    state.created = True
    print(f"    ensured {state.table_id}")
    return state


def load_one(client, state: TableState) -> None:
    """Phase 2: load one already-created table. Mutates state in place."""
    print(f"\n=== {state.name} ===")
    target = (f"gs://{BUCKET}/{CSV_BLOB_DIR}" if BUCKET
              else "direct in-memory load")
    print(f"[2/3] load     via {target}")
    if BUCKET:
        uri = cloud.upload_csv(
            state.csv_text, f"gs://{BUCKET}/{CSV_BLOB_DIR}/{state.name}.csv")
        print(f"    uploaded {uri}")
        job = cloud.load_csv_from_gcs(client, uri, state.table_id,
                                      state.location, COLUMNS)
    else:
        job = cloud.load_csv_from_memory(client, state.csv_text,
                                         state.table_id, state.location,
                                         COLUMNS)
    print(f"    loaded {job.output_rows} rows")
    if job.output_rows != len(state.rows):
        msg = f"loaded {job.output_rows}, extracted {len(state.rows)}"
        print(f"\nFAILED: {msg}", file=sys.stderr)
        state.ok, state.error = False, msg
        return
    state.loaded = True


def verify_one(client, state: TableState) -> None:
    """Phase 3: verify one loaded table. Mutates state in place."""
    print(f"\n=== {state.name} ===")
    print("[3/3] verify")
    ok = verify(client, state.table_id,
                EXPECTED_ROWS.get(state.name, len(state.rows)))
    print("Done." if ok else "Loaded, but a verification check FAILED.")
    state.ok = state.ok and ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="extract + connectivity test only; write nothing")
    ap.add_argument("--only", help="comma-separated table names")
    ap.add_argument("--dataset", default="bronze",
                    help="dataset to create/load tables in (default: bronze)")
    args = ap.parse_args()

    global DATASET
    DATASET = args.dataset

    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)

    only = set(args.only.split(",")) if args.only else None
    wanted = [(n, c) for n, c in TABLES if not only or n in only]

    print(f"[1/3] CREATE — extract, check, apply DDL for {len(wanted)} table(s)")
    states = {n: create_one(client, n, c, args) for n, c in wanted}

    if args.check:
        print("\n=== summary ===")
        for name, state in states.items():
            print(f"    [{'OK' if state.ok else 'FAIL'}] {name}")
        return 0 if all(s.ok for s in states.values()) else 1

    to_load = {n: s for n, s in states.items() if s.ok}
    print(f"\n[2/3] LOAD — {len(to_load)} table(s) that passed CREATE")
    for state in to_load.values():
        load_one(client, state)

    to_verify = {n: s for n, s in to_load.items() if s.loaded}
    print(f"\n[3/3] VERIFY — {len(to_verify)} table(s) that loaded")
    for state in to_verify.values():
        verify_one(client, state)

    print("\n=== summary ===")
    for name, state in states.items():
        print(f"    [{'OK' if state.ok else 'FAIL'}] {name}"
              + (f"  ({state.error})" if state.error else ""))
    return 0 if all(s.ok for s in states.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
