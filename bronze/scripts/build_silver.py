"""Upsert the silver tables fed by this pass, then run the DQ board.

    set GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\key.json
    python scripts/build_silver.py --check   # validate SQL only, write nothing
    python scripts/build_silver.py           # upsert, then assert

Exits non-zero if any DQ check fails, so this is safe to put in CI later.

Covers dim_entity, dim_period, dim_account, dim_group_account,
fact_trial_balance, fact_group_trial_balance, dim_ifrs_requirement,
dim_required_document — see sql/silver_build.sql for the MERGE statements.

Requires bronze_tb_raw and its siblings (bronze_group_tb_raw,
bronze_ifrs_rubric_raw, bronze_checklist_raw) to already be loaded — run
scripts/push_to_bq.py first.
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

# Printed after a real build. row count is informational only — the DQ board
# is what actually gates success.
TABLES = ["dim_entity", "dim_period", "dim_account", "dim_group_account",
          "fact_trial_balance", "fact_group_trial_balance",
          "dim_ifrs_standard", "dim_ifrs_requirement", "dim_entity_context",
          "dim_required_document"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="dry-run the build SQL; create nothing")
    args = ap.parse_args()

    client = bigquery.Client(project=PROJECT, location=LOCATION)

    if args.check:
        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        job = client.query(BUILD.read_text(encoding="utf-8"), job_config=cfg)
        print(f"[dry run] {BUILD.name}: VALID — "
              f"{job.total_bytes_processed/1e6:.3f} MB would be scanned")
        print("Nothing created.")
        return 0

    print(f"[1/2] upsert  {BUILD.name}")
    client.query(BUILD.read_text(encoding="utf-8")).result()
    for t in TABLES:
        tbl = client.get_table(f"{PROJECT}.silver.{t}")
        print(f"    silver.{t:<24} {tbl.num_rows:>4} rows, "
              f"{len(tbl.schema)} cols")

    print(f"[2/2] verify  {CHECKS.name}")
    rows = list(client.query(CHECKS.read_text(encoding="utf-8")).result())
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
