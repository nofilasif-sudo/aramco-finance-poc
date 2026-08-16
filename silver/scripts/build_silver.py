"""Apply fixed DDL, upsert the silver tables fed by this pass, then run the
DQ board.

    set GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\key.json
    python scripts/build_silver.py --check   # validate SQL only, write nothing
    python scripts/build_silver.py           # apply DDL, upsert, then assert

    # write to different (e.g. staging/validation) datasets instead of the
    # live bronze/silver ones, to compare against the existing tables
    # before ever touching them:
    python scripts/build_silver.py --bronze-dataset bronze_staging --silver-dataset silver_staging

Exits non-zero if any DQ check fails, so this is safe to put in CI later.

Table CREATION is DDL-first, same as bronze/scripts/push_to_bq.py: each
table in TABLES has a fixed CREATE TABLE IF NOT EXISTS in sql/<table>.sql,
applied here in dependency order (dim_entity/dim_period before
fact_trial_balance, which has FOREIGN KEY constraints on them) before the
MERGE statements in sql/silver_build.sql run. Edit the .sql file to change
a table's schema — this script only applies it, never builds it in Python.

Covers dim_entity, dim_period, dim_account, dim_group_account,
fact_trial_balance, fact_group_trial_balance, dim_ifrs_standard,
dim_ifrs_requirement, dim_entity_context, dim_required_document — see
sql/silver_build.sql for the MERGE statements.

Requires bronze_tb_raw and its siblings (bronze_group_tb_raw,
bronze_ifrs_rubric_raw, bronze_checklist_raw) to already be loaded — run
bronze/scripts/push_to_bq.py first (same --bronze-dataset value, so this
reads from wherever that pass wrote to).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "aramco-finance-poc-c2a4"
LOCATION = "me-central2"

BUILD = ROOT / "sql" / "silver_build.sql"
CHECKS = ROOT / "sql" / "silver_checks.sql"
SQL_DIR = ROOT / "sql"

# DDL applied in this order — dim_entity/dim_period before fact_trial_balance
# (its FOREIGN KEY constraints reference them), everything else has no
# cross-table dependency at CREATE time. Also the order build_silver prints
# row counts in after a real build.
TABLES = ["dim_entity", "dim_period", "dim_account", "dim_group_account",
          "fact_trial_balance", "fact_group_trial_balance",
          "dim_ifrs_standard", "dim_ifrs_requirement", "dim_entity_context",
          "dim_required_document"]


def retarget(sql_text: str, bronze_dataset: str, silver_dataset: str) -> str:
    """Repoint every `<project>.bronze.<table>` / `.silver.<table>` reference
    at the given datasets. Every occurrence in these files is inside a
    backtick-quoted fully-qualified table name (verified — no false
    positives in comments/descriptions), so a literal substring replace is
    safe and avoids needing a real SQL parser."""
    return (sql_text
            .replace(".bronze.", f".{bronze_dataset}.")
            .replace(".silver.", f".{silver_dataset}."))


def apply_ddl(client, silver_dataset: str, bronze_dataset: str) -> None:
    ds = bigquery.Dataset(f"{PROJECT}.{silver_dataset}")
    ds.location = LOCATION
    client.create_dataset(ds, exists_ok=True)

    for t in TABLES:
        ddl_sql = (SQL_DIR / f"{t}.sql").read_text(encoding="utf-8")
        ddl_sql = retarget(ddl_sql, bronze_dataset, silver_dataset)
        client.query(ddl_sql, location=LOCATION).result()
        print(f"    ensured {silver_dataset}.{t}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="dry-run the build SQL; create nothing")
    ap.add_argument("--bronze-dataset", default="bronze",
                    help="dataset silver_build.sql reads bronze.* from")
    ap.add_argument("--silver-dataset", default="silver",
                    help="dataset the silver tables are created/upserted in")
    args = ap.parse_args()

    client = bigquery.Client(project=PROJECT, location=LOCATION)
    build_sql = retarget(BUILD.read_text(encoding="utf-8"),
                         args.bronze_dataset, args.silver_dataset)
    checks_sql = retarget(CHECKS.read_text(encoding="utf-8"),
                          args.bronze_dataset, args.silver_dataset)

    if args.check:
        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = client.query(build_sql, job_config=cfg)
        print(f"[dry run] {BUILD.name}: VALID — "
              f"{job.total_bytes_processed/1e6:.3f} MB would be scanned")
        print("Nothing created.")
        return 0

    print(f"[1/3] DDL      {len(TABLES)} table(s) -> {args.silver_dataset}")
    apply_ddl(client, args.silver_dataset, args.bronze_dataset)

    print(f"[2/3] upsert   {BUILD.name}")
    client.query(build_sql).result()
    for t in TABLES:
        tbl = client.get_table(f"{PROJECT}.{args.silver_dataset}.{t}")
        print(f"    {args.silver_dataset}.{t:<24} {tbl.num_rows:>4} rows, "
              f"{len(tbl.schema)} cols")

    print(f"[3/3] verify   {CHECKS.name}")
    rows = list(client.query(checks_sql).result())
    failed = 0
    for r in rows:
        if r["status"] != "PASS":
            failed += 1
        print(f"    [{r['status']}] {r['check_name']:<52} {r['detail']}")

    print(f"\n{len(rows) - failed}/{len(rows)} checks passed."
          + ("" if not failed else f"  {failed} FAILED."))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
