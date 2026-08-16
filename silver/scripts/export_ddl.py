"""Re-baseline sql/<table>.sql from the live table's current DDL.

    python scripts/export_ddl.py              # re-baseline every silver table
    python scripts/export_ddl.py --only dim_account

sql/<table>.sql is normally FIXED and hand-maintained — build_silver.py
applies it, it does not build/derive it (see that script's docstring).
This script exists for the rare case a table's live schema legitimately
drifted from the checked-in file (e.g. someone ran a manual ALTER TABLE)
and you want to pull the new baseline back into the repo for review,
rather than to keep the files in permanent sync automatically.

TABLES here matches build_silver.py's list: fact_trial_balance, dim_entity
and dim_period are all excluded, owned by another developer's
trial_balance/ pipeline (dim_entity/dim_period are read-only lookups for
this package, not tables it creates or exports DDL for).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROJECT = "aramco-finance-poc-c2a4"
DATASET = "silver"
SQL_DIR = ROOT / "sql"

TABLES = ["dim_account", "dim_group_account", "fact_group_trial_balance",
          "dim_ifrs_standard", "dim_ifrs_requirement", "dim_entity_context",
          "dim_required_document"]


def export_one(client, name: str) -> bool:
    rows = list(client.query(
        f"SELECT ddl FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.TABLES` "
        f"WHERE table_name = '{name}'"
    ).result())
    if not rows:
        print(f"    SKIP {name}: table does not exist yet in BigQuery "
              f"(run build_silver.py first)")
        return False

    ddl = rows[0]["ddl"].rstrip()
    header = (
        f"-- ===========================================================================\n"
        f"-- {DATASET}.{name}\n"
        f"--\n"
        f"-- EXPORTED FROM THE LIVE TABLE on {date.today().isoformat()} via\n"
        f"--   SELECT ddl FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.TABLES`\n"
        f"--   WHERE table_name = '{name}'\n"
        f"--\n"
        f"-- This is what BigQuery actually has, not a reconstruction. Built/maintained\n"
        f"-- by scripts/build_silver.py via sql/silver_build.sql — treat that file as\n"
        f"-- the source of truth for HOW this table is populated, and this file as the\n"
        f"-- reproducible snapshot of its current schema.\n"
        f"--\n"
        f"-- Project  : {PROJECT}\n"
        f"-- Dataset  : {DATASET}\n"
        f"-- ===========================================================================\n\n"
    )
    out = SQL_DIR / f"{name}.sql"
    out.write_text(header + ddl + "\n", encoding="utf-8", newline="\n")
    print(f"    wrote {out.relative_to(ROOT)}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated table names to export")
    args = ap.parse_args()

    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)

    only = set(args.only.split(",")) if args.only else None
    names = [n for n in TABLES if not only or n in only]

    SQL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Exporting DDL for {len(names)} table(s) -> {SQL_DIR}")
    exported = [export_one(client, n) for n in names]
    ok = sum(exported)
    print(f"\n{ok}/{len(names)} exported.")
    return 0 if ok == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
