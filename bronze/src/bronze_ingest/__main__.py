"""CLI entry point.

Named __main__.py so the package is runnable with `python -m bronze_ingest`.
It stays thin on purpose: everything below it is importable, so Path 2 or an
orchestrator can call `<module>.extract()` directly without going through
argparse.

Runs both bronze tables in one invocation. Each table reconciles
independently: a bad control total or nil-proof in one table stops that
table's write and is reported, but does not prevent the other from
landing.

Trial balance (bronze_tb_raw, bronze_group_tb_raw) and the CSV-sourced
tables (bronze_ifrs_standard_raw, bronze_ifrs_rubric_raw,
bronze_entity_context_raw) are owned/ingested elsewhere and are not part of
this package — see trial_balance/ at the repo root for trial balance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import checklist, coa
from .excel import IngestError
from .sink import write_csv

# The repo root, from src/bronze_ingest/__main__.py -> up three.
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs"

# (table name, extractor module, config filename). One entry per bronze
# table this package owns.
TABLES = [
    ("bronze_coa_raw",           coa,             "bronze_coa.json"),
    ("bronze_checklist_raw",     checklist,       "bronze_checklist.json"),
]


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    # Relative paths resolve against the repo root, not the shell's cwd, so
    # the pipeline behaves the same from any directory.
    for key in ("input_path", "output_dir"):
        p = Path(cfg[key])
        cfg[key] = p if p.is_absolute() else (ROOT / p).resolve()
    return cfg


def run_one(name: str, module, cfg: dict, dry_run: bool) -> dict:
    """Ingest one bronze table. Returns the run summary. Raises IngestError."""
    report: list[str] = []
    rows, meta = module.extract(cfg["input_path"], cfg, report)

    if dry_run:
        report.append(f"dry run — would write {len(rows)} rows")
    else:
        out = cfg["output_dir"] / cfg["output_file"]
        meta["_output"] = write_csv(rows, module.COLUMNS, out,
                                    cfg["csv_encoding"], report)

    for line in report:
        print("   ", line)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="bronze-ingest",
        description="Excel data pack -> bronze CSVs (coa, checklist)")
    ap.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    ap.add_argument("--dry-run", action="store_true",
                    help="run the checks, write nothing")
    ap.add_argument("--only", help="comma-separated table names to run, "
                    "e.g. bronze_coa_raw,bronze_tb_raw")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    failed: list[str] = []

    for name, module, config_file in TABLES:
        if only and name not in only:
            continue
        print(f"\n[{name}] config: {config_file}")
        try:
            cfg = load_config(args.config_dir / config_file)
            print(f"    {cfg['input_path']}")
            run_one(name, module, cfg, args.dry_run)
        except IngestError as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            print("    No output written for this table.", file=sys.stderr)
            failed.append(name)
        except FileNotFoundError as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            failed.append(name)

    print()
    if failed:
        print(f"FAILED: {len(failed)}/{len(TABLES)} table(s) did not land: "
              f"{', '.join(failed)}", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
