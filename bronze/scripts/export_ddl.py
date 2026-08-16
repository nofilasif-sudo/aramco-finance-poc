"""Export each bronze table's live DDL to sql/<table>.sql.

    python scripts/export_ddl.py              # export every table this package owns
    python scripts/export_ddl.py --only bronze_coa_raw

Mirrors the existing sql/bronze_coa_raw.sql: the DDL is EXPORTED FROM THE
LIVE TABLE via INFORMATION_SCHEMA, not hand-authored. The Python in
bronze_ingest.cloud.bq_schema() is the source of truth for the schema;
these .sql files are the reproducible, reviewable artifact. Re-run this
after any schema change (a new column, a new table) to keep them in sync.

Only exports tables this package owns (cloud.BRONZE_TABLES) — bronze_tb_raw
is the other developer's table and is not touched here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bronze_ingest import cloud                                     # noqa: E402

PROJECT = "aramco-finance-poc-c2a4"
DATASET = "bronze"
SQL_DIR = ROOT / "sql"


def export_one(client, name: str) -> bool:
    rows = list(client.query(
        f"SELECT ddl FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.TABLES` "
        f"WHERE table_name = '{name}'"
    ).result())
    if not rows:
        print(f"    SKIP {name}: table does not exist yet in BigQuery "
              f"(run push_to_bq.py first)")
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
        f"-- This is what BigQuery actually has, not a reconstruction. The table was\n"
        f"-- created by scripts/push_to_bq.py (which builds the schema from\n"
        f"-- bronze_ingest.cloud.bq_schema()), so treat that Python as the source of\n"
        f"-- truth and this file as the reproducible artifact.\n"
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
    names = [n for n in cloud.BRONZE_TABLES if not only or n in only]

    SQL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Exporting DDL for {len(names)} table(s) -> {SQL_DIR}")
    exported = [export_one(client, n) for n in names]
    ok = sum(exported)
    print(f"\n{ok}/{len(names)} exported.")
    return 0 if ok == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
