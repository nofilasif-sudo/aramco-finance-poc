"""Run this package's bronze ingestions, then publish each to BigQuery.

    set GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\key.json
    python scripts/push_to_bq.py --check     # connectivity + plan, no writes
    python scripts/push_to_bq.py             # create -> load -> verify, all tables
    python scripts/push_to_bq.py --only bronze_coa_raw

    # write to a different dataset (e.g. to validate against the existing
    # live bronze tables before ever touching them):
    python scripts/push_to_bq.py --dataset bronze_staging

Three GLOBAL phases, each covering every table before the next phase
starts (not interleaved per table) — this is what lets "create bronze
table" and "run bronze loading pipeline" be pointed at independently:

  [1/3] CREATE  extract + reconcile each table locally (control total /
        nil-proof — raises before any cloud call if a table doesn't
        balance), then apply its fixed DDL (sql/<table>.sql,
        CREATE TABLE IF NOT EXISTS — idempotent).
  [2/3] LOAD    load every table that survived phase 1. The rendered CSV
        never touches local disk: it's either uploaded straight from
        memory to GCS (if BUCKET is set — a byte-for-byte lineage
        artifact) then loaded from there, or streamed straight from
        memory into the load job.
  [3/3] VERIFY  re-query BigQuery directly for every table that loaded —
        proves the *loaded table* is right, not just that the CSV was.

A table that fails phase 1 is dropped from phases 2/3 but doesn't stop
the others — one bad workbook shouldn't block the whole pack.

Reuses the same (name, module, config file) registry as `python -m
bronze_ingest`, so the two entry points can never define a different set of
tables or configs.

Affiliate trial balance (bronze_tb_raw) is owned by another developer — see
trial_balance/ at the repo root. Everything else this script's TABLES
registry lists is owned by this script.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bronze_ingest.__main__ import TABLES, load_config              # noqa: E402
from bronze_ingest.excel import IngestError                         # noqa: E402
from bronze_ingest import cloud                                     # noqa: E402
from bronze_ingest.sink import to_csv_text                          # noqa: E402

# --- settings ---------------------------------------------------------------
PROJECT = "aramco-finance-poc-c2a4"
DATASET = "bronze"            # matches the dataset already in the project
# Only used if the dataset does not exist yet; otherwise its real location
# wins, because a dataset's location is immutable and cross-location loads
# are a hard error.
DEFAULT_LOCATION = "me-central2"   # Dammam — confirmed: the existing
                                   # bronze/silver datasets are both here
# Set to None to load straight from memory (no bucket needed). Set to a
# bucket name to stage the rendered CSV in GCS first — gives a byte-for-byte
# lineage artifact of exactly what was loaded, at gs://<BUCKET>/<CSV_BLOB_DIR>/.
BUCKET = "aramco-finance-poc-raw-landing"
CSV_BLOB_DIR = "staging"

# Expected row counts, per the DBML (bronze.dbml v2, 12 Aug). A mismatch
# here means the pack changed shape and needs eyes on it before it lands.
EXPECTED_ROWS = {
    "bronze_coa_raw": 188,
    "bronze_group_tb_raw": 531,
    "bronze_ifrs_rubric_raw": 15,
    "bronze_ifrs_standard_raw": 3,
    "bronze_entity_context_raw": 13,
    "bronze_checklist_raw": 14,
    # One row per affiliate account, matching each mapping tab's own triage
    # footer. Same counts as that affiliate's chart in bronze_coa_raw (66/44),
    # because every affiliate account is mapped — including the one that maps
    # to nothing usable and lands as 'Unmapped - analyst intervention'.
    "bronze_coa_mapping_sabic_raw": 66,
    "bronze_coa_mapping_rabigh_raw": 44,
}


@dataclass
class TableState:
    """Carries one table's state across the create -> load -> verify phases."""
    name: str
    table_id: str
    columns: list[str]
    descriptions: dict
    location: str = DEFAULT_LOCATION
    rows: list = field(default_factory=list)
    csv_text: str = ""
    created: bool = False
    loaded: bool = False
    ok: bool = True
    error: str = ""


def verify_common(client, table_id: str, table_name: str, expected: int) -> bool:
    """Checks every table gets: row count, and no NULLs anywhere (the
    null_marker="\\N" trick means an empty bronze cell must load as an empty
    string, never NULL — a NULL here means the load config regressed)."""
    checks = [
        (f"row count == {expected}",
         f"SELECT COUNT(*) = {expected} AS ok, COUNT(*) AS actual "
         f"FROM `{table_id}`"),
    ]
    return _run_checks(client, checks)


def verify_coa(client, table_id: str) -> bool:
    checks = [
        # NB: `nulls` is a reserved keyword in BigQuery (ORDER BY ... NULLS
        # FIRST), so the alias has to be something else.
        ("empties stayed empty, not NULL (110 affiliate rows)",
         f"SELECT COUNTIF(level IS NULL) = 0 AS ok, "
         f"COUNTIF(level IS NULL) AS null_count, "
         f"COUNTIF(level = '') AS empty_count FROM `{table_id}`"),
        ("scopes are GROUP/2010/2380 with 78/66/44",
         f"SELECT COUNTIF(chart_scope='GROUP')=78 AND "
         f"COUNTIF(chart_scope='2010')=66 AND "
         f"COUNTIF(chart_scope='2380')=44 AS ok FROM `{table_id}`"),
        ("no empty account or account_name",
         f"SELECT COUNTIF(COALESCE(account,'')='' OR "
         f"COALESCE(account_name,'')='')=0 AS ok FROM `{table_id}`"),
        ("source_file never empty",
         f"SELECT COUNTIF(COALESCE(source_file,'')='')=0 AS ok "
         f"FROM `{table_id}`"),
    ]
    return _run_checks(client, checks)


def verify_tb_like(client, table_id: str, entity_col: str) -> bool:
    checks = [
        (f"no NULL {entity_col} (null_marker regression check)",
         f"SELECT COUNTIF({entity_col} IS NULL)=0 AS ok FROM `{table_id}`"),
        ("no NULL period_label or source_file",
         f"SELECT COUNTIF(period_label IS NULL OR source_file IS NULL)=0 "
         f"AS ok FROM `{table_id}`"),
        ("9 distinct period labels",
         f"SELECT COUNT(DISTINCT period_label) = 9 AS ok, "
         f"COUNT(DISTINCT period_label) AS actual FROM `{table_id}`"),
    ]
    return _run_checks(client, checks)


def verify_reference(client, table_id: str, required_cols: list[str]) -> bool:
    cond = " OR ".join(f"COALESCE({c},'')=''" for c in required_cols)
    checks = [
        (f"no blank {', '.join(required_cols)}",
         f"SELECT COUNTIF({cond})=0 AS ok FROM `{table_id}`"),
    ]
    return _run_checks(client, checks)


def verify_unique(client, table_id: str, col: str) -> bool:
    """A primary key that isn't unique isn't a key. Cheap, and the failure it
    catches (a file loaded twice, two files concatenated) is otherwise silent
    because the row count check would also have to be wrong to notice."""
    checks = [
        (f"{col} is unique",
         f"SELECT COUNT(*) = COUNT(DISTINCT {col}) AS ok, "
         f"COUNT(*) AS total, COUNT(DISTINCT {col}) AS distinct_vals "
         f"FROM `{table_id}`"),
    ]
    return _run_checks(client, checks)


def verify_rubric(client, table_id: str) -> bool:
    """Rubric-specific: the closed evidence_type set, the 5-per-standard
    shape, and referential integrity to the new standard parent table."""
    std_table = f"{PROJECT}.{DATASET}.bronze_ifrs_standard_raw"
    checks = [
        ("evidence_type is a closed set (narrative/table_structure/both)",
         f"SELECT COUNTIF(evidence_type NOT IN "
         f"('narrative','table_structure','both'))=0 AS ok, "
         f"COUNTIF(evidence_type NOT IN "
         f"('narrative','table_structure','both')) AS bad FROM `{table_id}`"),
        ("exactly 5 requirements per standard",
         f"SELECT LOGICAL_AND(n = 5) AS ok, COUNT(*) AS standards FROM "
         f"(SELECT standard_code, COUNT(*) AS n FROM `{table_id}` "
         f"GROUP BY standard_code)"),
        ("(standard_code, req) is unique",
         f"SELECT COUNT(*) = COUNT(DISTINCT FORMAT('%s|%s', standard_code, req)) "
         f"AS ok FROM `{table_id}`"),
        # Bronze enforces no relationships, but an orphan here means the two
        # files disagree about which standards exist — worth failing on.
        ("every standard_code resolves to bronze_ifrs_standard_raw",
         f"SELECT COUNTIF(s.standard_code IS NULL)=0 AS ok, "
         f"COUNTIF(s.standard_code IS NULL) AS orphans "
         f"FROM `{table_id}` r LEFT JOIN `{std_table}` s USING (standard_code)"),
    ]
    return _run_checks(client, checks)


def _run_checks(client, checks) -> bool:
    all_ok = True
    for label, sql in checks:
        row = list(client.query(sql).result())[0]
        detail = ", ".join(f"{k}={row[k]}" for k in row.keys() if k != "ok")
        status = "OK" if row["ok"] else "FAIL"
        all_ok &= bool(row["ok"])
        print(f"    [{status}] {label}" + (f"  ({detail})" if detail else ""))
    return all_ok


def dispatch_verify(client, name: str, table_id: str, expected: int) -> bool:
    ok = verify_common(client, table_id, name, expected)
    if name == "bronze_coa_raw":
        ok &= verify_coa(client, table_id)
    elif name == "bronze_group_tb_raw":
        ok &= verify_tb_like(client, table_id, "account")
    elif name == "bronze_ifrs_rubric_raw":
        ok &= verify_reference(client, table_id,
                               ["standard", "req", "requirement", "standard_code"])
        ok &= verify_rubric(client, table_id)
    elif name == "bronze_ifrs_standard_raw":
        ok &= verify_reference(client, table_id,
                               ["standard_code", "standard_title",
                                "disclosure_summary"])
    elif name == "bronze_entity_context_raw":
        ok &= verify_reference(client, table_id, ["context_key", "context_value"])
        ok &= verify_unique(client, table_id, "context_key")
    elif name == "bronze_checklist_raw":
        ok &= verify_reference(client, table_id, ["item", "document"])
    elif name == "bronze_coa_mapping_sabic_raw":
        ok &= verify_coa_mapping(client, table_id, "2010",
                                 {"Auto-mapped": 58, "Analyst review": 7,
                                  "Unmapped - analyst intervention": 1})
    elif name == "bronze_coa_mapping_rabigh_raw":
        ok &= verify_coa_mapping(client, table_id, "2380",
                                 {"Auto-mapped": 40, "Analyst review": 4,
                                  "Unmapped - analyst intervention": 0})
    return ok


def create_one(client, name: str, module, config_file: str, args) -> TableState:
    """Phase 1: extract + reconcile locally, then apply the fixed DDL.
    Never raises — a failure here must not stop the other tables."""
    print(f"\n=== {name} ===")
    spec = cloud.BRONZE_TABLES[name]
    state = TableState(name=name, table_id=f"{PROJECT}.{DATASET}.{name}",
                       columns=spec["columns"],
                       descriptions=spec["descriptions"])

    try:
        cfg = load_config(ROOT / "configs" / config_file)
    except FileNotFoundError as exc:
        print(f"[1/3] FAILED: {exc}", file=sys.stderr)
        state.ok, state.error = False, str(exc)
        return state

    print(f"[1/3] extract  {Path(cfg['input_path']).name}")
    report: list[str] = []
    try:
        state.rows, _ = module.extract(cfg["input_path"], cfg, report)
    except IngestError as exc:
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

    state.csv_text = to_csv_text(state.columns, state.rows)
    print(f"    {len(state.rows)} rows, {len(state.csv_text):,} bytes "
          f"(in memory, no local file written)")

    state.location = cloud.dataset_location(client, f"{PROJECT}.{DATASET}",
                                            DEFAULT_LOCATION)
    print(f"    target {state.table_id}  (location {state.location})")

    if args.check:
        print("--check: reconciled. DDL/load/verify skipped, nothing written.")
        return state

    ddl_path = ROOT / "sql" / f"{name}.sql"
    cloud.ensure_table(client, state.table_id, state.location, ddl_path)
    state.created = True
    print(f"    ensured {state.table_id}")
    return state


def load_one(client, state: TableState) -> None:
    """Phase 2: load one already-created table. Mutates state in place."""
    print(f"\n=== {state.name} ===")
    print(f"[2/3] load     via "
          f"{'gs://' + BUCKET + '/' + CSV_BLOB_DIR if BUCKET else 'direct in-memory load'}")
    if BUCKET:
        uri = cloud.upload_csv(state.csv_text,
                               f"gs://{BUCKET}/{CSV_BLOB_DIR}/{state.name}.csv")
        print(f"    uploaded {uri}")
        job = cloud.load_csv_from_gcs(client, uri, state.table_id, state.location,
                                      state.columns, state.descriptions)
    else:
        job = cloud.load_csv_from_memory(client, state.csv_text, state.table_id,
                                         state.location, state.columns,
                                         state.descriptions)
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
    expected = EXPECTED_ROWS.get(state.name, len(state.rows))
    ok = dispatch_verify(client, state.name, state.table_id, expected)
    print("Done." if ok else "Loaded, but a verification check FAILED.")
    state.ok = state.ok and ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="extract + connectivity test only; write nothing")
    ap.add_argument("--only", help="comma-separated table names to run")
    ap.add_argument("--dataset", default="bronze",
                    help="dataset to create/load tables in (default: bronze)")
    args = ap.parse_args()

    global DATASET
    DATASET = args.dataset

    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)

    only = set(args.only.split(",")) if args.only else None
    wanted = [(n, m, c) for n, m, c in TABLES if not only or n in only]

    print(f"[1/3] CREATE — extract, reconcile, apply DDL for {len(wanted)} table(s)")
    states = {n: create_one(client, n, m, c, args) for n, m, c in wanted}

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
