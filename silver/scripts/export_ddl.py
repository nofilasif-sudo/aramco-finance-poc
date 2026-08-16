"""Export each silver table's live DDL to sql/<table>.sql.

    python scripts/export_ddl.py              # export every silver table
    python scripts/export_ddl.py --only dim_entity

Mirrors bronze/scripts/export_ddl.py: the DDL is EXPORTED FROM THE LIVE
TABLE via INFORMATION_SCHEMA, not hand-authored. sql/silver_build.sql
(the MERGE statements, plus CREATE TABLE IF NOT EXISTS for the tables it
introduces) is the source of truth for how each table is built; these
per-table .sql files are the reproducible, reviewable snapshot of what
BigQuery actually has. Re-run after any schema change to keep them in sync.

TABLES here matches the list build_silver.py prints after a real build —
some of these tables predate this pipeline (dim_entity, dim_period,
dim_account, dim_group_account, fact_trial_balance) and are only
maintained, not created, by silver_build.sql; the rest are genuinely new.
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

TABLES = ["dim_entity", "dim_period", "dim_account", "dim_group_account",
          "fact_trial_balance", "fact_group_trial_balance",
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
