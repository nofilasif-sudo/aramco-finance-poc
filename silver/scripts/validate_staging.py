"""Compare a staging run against the live tables, per table — bronze and
silver together, one report.

    python scripts/validate_staging.py
    python scripts/validate_staging.py --bronze-staging bronze_staging --silver-staging silver_staging
    python scripts/validate_staging.py --deep     # also row-level diff each table (slower)

Defaults assume the staging convention used throughout this project:
bronze_staging/silver_staging written by
    bronze/scripts/push_to_bq.py --dataset bronze_staging
    silver/scripts/build_silver.py --bronze-dataset bronze_staging --silver-dataset silver_staging

Row counts only by default — cheap, and enough to catch the overwhelming
majority of "this approach changed something" cases. --deep additionally
runs an EXCEPT DISTINCT in both directions per table (staging rows not in
live, live rows not in staging) to catch same-count-different-content
drift; skipped by default because it scans full tables and can be slow/
costly on large ones.

Table lists are read from each package's own registry (bronze_ingest.
cloud.BRONZE_TABLES and build_silver.TABLES) rather than hardcoded here,
so this can never drift from what those pipelines actually build.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # silver/
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "bronze" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from bronze_ingest import cloud as bronze_cloud      # noqa: E402
from build_silver import TABLES as SILVER_TABLES     # noqa: E402

PROJECT = "aramco-finance-poc-c2a4"


def _count(client, table_id: str) -> int | None:
    try:
        return list(client.query(f"SELECT COUNT(*) FROM `{table_id}`").result())[0][0]
    except Exception as exc:
        print(f"    ERROR counting {table_id}: {exc}", file=sys.stderr)
        return None


def _deep_diff(client, staging_id: str, live_id: str) -> tuple[int, int] | None:
    """Rows only in staging, rows only in live — SELECT * EXCEPT DISTINCT
    both directions. Requires identical column sets."""
    try:
        only_staging = list(client.query(
            f"SELECT COUNT(*) FROM ((SELECT * FROM `{staging_id}`) "
            f"EXCEPT DISTINCT (SELECT * FROM `{live_id}`))").result())[0][0]
        only_live = list(client.query(
            f"SELECT COUNT(*) FROM ((SELECT * FROM `{live_id}`) "
            f"EXCEPT DISTINCT (SELECT * FROM `{staging_id}`))").result())[0][0]
        return only_staging, only_live
    except Exception as exc:
        print(f"    ERROR diffing {staging_id} vs {live_id}: {exc}", file=sys.stderr)
        return None


def compare_one(client, name: str, staging_dataset: str, live_dataset: str,
                deep: bool) -> bool:
    staging_id = f"{PROJECT}.{staging_dataset}.{name}"
    live_id = f"{PROJECT}.{live_dataset}.{name}"

    s_count = _count(client, staging_id)
    l_count = _count(client, live_id)
    if s_count is None or l_count is None:
        return False

    ok = s_count == l_count
    status = "OK" if ok else "DIFF"
    print(f"    [{status}] {name:<28} staging={s_count:<6} live={l_count}")

    if deep and ok:
        diff = _deep_diff(client, staging_id, live_id)
        if diff is not None:
            only_staging, only_live = diff
            if only_staging or only_live:
                ok = False
                print(f"           row-level diff: {only_staging} row(s) only in "
                      f"staging, {only_live} only in live")
            else:
                print(f"           row-level: identical")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bronze-staging", default="bronze_staging")
    ap.add_argument("--silver-staging", default="silver_staging")
    ap.add_argument("--bronze-live", default="bronze")
    ap.add_argument("--silver-live", default="silver")
    ap.add_argument("--deep", action="store_true",
                    help="also run an EXCEPT DISTINCT row-level diff per table")
    args = ap.parse_args()

    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)

    results: dict[str, bool] = {}

    print(f"=== bronze: {args.bronze_staging} vs {args.bronze_live} ===")
    for name in bronze_cloud.BRONZE_TABLES:
        results[f"bronze.{name}"] = compare_one(
            client, name, args.bronze_staging, args.bronze_live, args.deep)

    print(f"\n=== silver: {args.silver_staging} vs {args.silver_live} ===")
    for name in SILVER_TABLES:
        results[f"silver.{name}"] = compare_one(
            client, name, args.silver_staging, args.silver_live, args.deep)

    failed = [k for k, ok in results.items() if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} tables match."
          + ("" if not failed else f"\nDIFFER: {', '.join(failed)}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
