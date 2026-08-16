"""Apply fixed DDL, upsert the silver tables fed by this pass, then run the
DQ board.

    set GOOGLE_APPLICATION_CREDENTIALS=C:\\path\\to\\key.json
    python scripts/build_silver.py --check   # validate SQL only, write nothing
    python scripts/build_silver.py           # apply DDL, upsert, then assert

    # write to different (e.g. staging/validation) datasets instead of the
    # live bronze/silver ones, to compare against the existing tables
    # before ever touching them:
    python scripts/build_silver.py --bronze-dataset bronze_staging --silver-dataset silver_staging

Four phases: [1/4] apply DDL, [2/4] upsert (MERGE), [3/4] run the SQL DQ
board (sql/silver_checks.sql — shape + business-rule checks like "proves
to nil"), [4/4] table-wise DQ — nulls / referential integrity / source-to-
target, printed one block per table this package owns (table_wise_dq()).
Exits non-zero if anything in phase 3 or 4 fails, so this is safe to put
in CI later.

Table CREATION is DDL-first, same as bronze/scripts/push_to_bq.py: each
table in TABLES has a fixed CREATE TABLE IF NOT EXISTS in sql/<table>.sql,
applied here in dependency order before the MERGE statements in
sql/silver_build.sql run. Edit the .sql file to change a table's schema —
this script only applies it, never builds it in Python.

Covers dim_account, dim_group_account, fact_group_trial_balance,
dim_ifrs_standard, dim_ifrs_requirement, dim_entity_context,
dim_required_document — see sql/silver_build.sql for the MERGE statements.

fact_trial_balance, dim_entity and dim_period are OUT OF SCOPE: all three
are owned/populated by another developer's trial_balance/ pipeline. Not
built, DDL'd, or checked here. dim_entity and dim_period used to also be
built by this file, until it turned out both pipelines were MERGing into
them independently with different data — see sql/silver_build.sql's header
comment for the full story. fact_group_trial_balance still JOINs to both
as read-only lookups, so a real run of THIS script now depends on
trial_balance/ having populated them first.

Requires bronze_group_tb_raw, bronze_ifrs_rubric_raw, bronze_checklist_raw
(and the other tables this pass reads) to already be loaded — run
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

# DDL applied in this order. No cross-table dependency at CREATE time among
# these (fact_trial_balance was the only one with FOREIGN KEY constraints,
# and it is out of scope — see module docstring). Also the order
# build_silver prints row counts in after a real build.
#
# dim_entity and dim_period are DELIBERATELY ABSENT — do not add them back.
# Both are owned by trial_balance/'s pipeline; this file only reads them
# (see silver_build.sql's fact_group_trial_balance MERGE). Re-adding them
# here would resume the silent double-MERGE this list was split out to
# stop. tests/test_build_silver.py asserts this exclusion.
TABLES = ["dim_account", "dim_group_account", "fact_group_trial_balance",
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


# ---------------------------------------------------------------------------
# Table-wise DQ: nulls / RI / source-to-target, one block per table this
# package owns. Complements sql/silver_checks.sql (which also covers shape
# and business-rule checks like "proves to nil") with a report organized
# the way a reviewer actually reasons about a table, not as one flat list.
# ---------------------------------------------------------------------------
def _count(client, sql: str) -> int:
    return list(client.query(sql).result())[0][0]


def check_nulls(client, table_id: str, cols: list[str]) -> bool:
    ok = True
    for c in cols:
        n = _count(client, f"SELECT COUNTIF({c} IS NULL) FROM `{table_id}`")
        status = "OK" if n == 0 else "FAIL"
        ok &= (n == 0)
        print(f"    [{status}] NULLS      {c:<20} {n} null row(s)")
    return ok


def check_ri(client, table_id: str, fk_col: str, ref_table_id: str,
            ref_col: str, where: str = "") -> bool:
    clause = f"WHERE {where}" if where else ""
    n = _count(client,
        f"SELECT COUNT(*) FROM `{table_id}` t {clause} "
        f"LEFT JOIN `{ref_table_id}` r ON t.{fk_col} = r.{ref_col} "
        f"WHERE r.{ref_col} IS NULL")
    status = "OK" if n == 0 else "FAIL"
    ref_name = ref_table_id.rsplit(".", 1)[-1]
    print(f"    [{status}] RI         {fk_col} -> {ref_name}.{ref_col}: "
          f"{n} orphan row(s)")
    return n == 0


def check_source_to_target(client, source_sql: str, target_sql: str,
                           expected_ratio: str = "1:1") -> bool:
    s, t = _count(client, source_sql), _count(client, target_sql)
    ok = (s == t) if expected_ratio == "1:1" else True
    status = "OK" if ok else ("FAIL" if expected_ratio == "1:1" else "INFO")
    print(f"    [{status}] SRC->TGT   source={s} target={t} "
          f"(expected {expected_ratio})")
    return ok


def table_wise_dq(client, bronze_dataset: str, silver_dataset: str) -> bool:
    b = f"{PROJECT}.{bronze_dataset}"
    s = f"{PROJECT}.{silver_dataset}"
    all_ok = True

    print("\n--- dim_account ---")
    all_ok &= check_nulls(client, f"{s}.dim_account",
        ["entity_code", "account_code", "account_name", "statement_type",
         "category", "normal_balance", "code_block"])
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_coa_raw` WHERE chart_scope IN ('2010','2380')",
        f"SELECT COUNT(*) FROM `{s}.dim_account`")

    print("\n--- dim_group_account ---")
    all_ok &= check_nulls(client, f"{s}.dim_group_account",
        ["group_node", "group_name", "statement", "category", "normal_balance", "level"])
    all_ok &= check_ri(client, f"{s}.dim_group_account", "parent_group_node",
                       f"{s}.dim_group_account", "group_node", where="level = 2")
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_coa_raw` WHERE chart_scope = 'GROUP'",
        f"SELECT COUNT(*) FROM `{s}.dim_group_account`")

    print("\n--- fact_group_trial_balance ---")
    all_ok &= check_nulls(client, f"{s}.fact_group_trial_balance",
        ["entity_code", "group_node", "period_key", "ledger_amount",
         "currency", "amount_unit"])
    all_ok &= check_ri(client, f"{s}.fact_group_trial_balance", "group_node",
                       f"{s}.dim_group_account", "group_node")
    all_ok &= check_ri(client, f"{s}.fact_group_trial_balance", "entity_code",
                       f"{s}.dim_entity", "entity_code")
    all_ok &= check_ri(client, f"{s}.fact_group_trial_balance", "period_key",
                       f"{s}.dim_period", "period_key")
    # NOT 1:1 by design: bronze_group_tb_raw carries subtotal rows that the
    # inner join to dim_group_account in silver_build.sql's MERGE excludes
    # (531 = 59 account/subtotal rows x 9 periods -> 450 = 50 balance-
    # carrying nodes x 9 periods). Informational, not pass/fail.
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_group_tb_raw`",
        f"SELECT COUNT(*) FROM `{s}.fact_group_trial_balance`",
        expected_ratio="531:450 (subtotal rows filtered out)")

    print("\n--- dim_ifrs_standard ---")
    all_ok &= check_nulls(client, f"{s}.dim_ifrs_standard",
        ["standard_code", "standard_title", "disclosure_summary"])
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_ifrs_standard_raw`",
        f"SELECT COUNT(*) FROM `{s}.dim_ifrs_standard`")

    print("\n--- dim_ifrs_requirement ---")
    all_ok &= check_nulls(client, f"{s}.dim_ifrs_requirement",
        ["standard_code", "req", "standard", "requirement_text",
         "evidence_type", "check_guidance"])
    all_ok &= check_ri(client, f"{s}.dim_ifrs_requirement", "standard_code",
                       f"{s}.dim_ifrs_standard", "standard_code")
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_ifrs_rubric_raw`",
        f"SELECT COUNT(*) FROM `{s}.dim_ifrs_requirement`")

    print("\n--- dim_entity_context ---")
    all_ok &= check_nulls(client, f"{s}.dim_entity_context",
        ["context_key", "context_value"])
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_entity_context_raw`",
        f"SELECT COUNT(*) FROM `{s}.dim_entity_context`")

    print("\n--- dim_required_document ---")
    all_ok &= check_nulls(client, f"{s}.dim_required_document",
        ["item", "document", "required", "applies_to", "expected_format",
         "description"])
    all_ok &= check_source_to_target(
        client,
        f"SELECT COUNT(*) FROM `{b}.bronze_checklist_raw`",
        f"SELECT COUNT(*) FROM `{s}.dim_required_document`")

    return all_ok


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

    print(f"[1/4] DDL      {len(TABLES)} table(s) -> {args.silver_dataset}")
    apply_ddl(client, args.silver_dataset, args.bronze_dataset)

    print(f"[2/4] upsert   {BUILD.name}")
    client.query(build_sql).result()
    for t in TABLES:
        tbl = client.get_table(f"{PROJECT}.{args.silver_dataset}.{t}")
        print(f"    {args.silver_dataset}.{t:<24} {tbl.num_rows:>4} rows, "
              f"{len(tbl.schema)} cols")

    print(f"[3/4] verify   {CHECKS.name}")
    rows = list(client.query(checks_sql).result())
    failed = 0
    for r in rows:
        if r["status"] != "PASS":
            failed += 1
        print(f"    [{r['status']}] {r['check_name']:<52} {r['detail']}")

    print(f"\n{len(rows) - failed}/{len(rows)} checks passed."
          + ("" if not failed else f"  {failed} FAILED."))

    print(f"\n[4/4] table-wise DQ (nulls / RI / source-to-target)")
    table_wise_ok = table_wise_dq(client, args.bronze_dataset, args.silver_dataset)
    print(f"\ntable-wise DQ: {'all OK' if table_wise_ok else 'FAILURES ABOVE'}")

    return 0 if (not failed and table_wise_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
