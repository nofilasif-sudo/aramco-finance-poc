"""CLI entry point.

Named __main__.py so the package is runnable with `python -m bronze_ingest`.
It stays thin on purpose: everything below it is importable, so Path 2 or an
orchestrator can call `<module>.extract()` directly without going through
argparse.

Runs this package's bronze tables in one invocation. Each table reconciles
independently: a bad control total or nil-proof in one table stops that
table's write and is reported, but does not prevent the others from
landing.

Affiliate trial balance (bronze_tb_raw) is owned by another developer —
see trial_balance/ at the repo root. Everything else is owned by this
package.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (checklist, coa, coa_mapping, entity_context, fs_statements,
               group_tb, ifrs_rubric, ifrs_standard)
from .excel import IngestError
from .sink import write_csv

# The repo root, from src/bronze_ingest/__main__.py -> up three.
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs"

# (table name, extractor module, config filename). One entry per bronze
# table this package owns.
TABLES = [
    ("bronze_coa_raw",           coa,             "bronze_coa.json"),
    ("bronze_group_tb_raw",      group_tb,        "bronze_group_tb.json"),
    # ifrs_standard BEFORE ifrs_rubric: the rubric's post-load verification
    # checks that every standard_code resolves to the standard table, so the
    # parent has to exist first on a cold run.
    ("bronze_ifrs_standard_raw", ifrs_standard,   "bronze_ifrs_standard.json"),
    ("bronze_ifrs_rubric_raw",   ifrs_rubric,     "bronze_ifrs_rubric.json"),
    ("bronze_entity_context_raw", entity_context, "bronze_entity_context.json"),
    ("bronze_checklist_raw",     checklist,       "bronze_checklist.json"),
    # The FS pair. Named fs_clean/fs_seeded rather than bronze_fs_*_raw, and
    # typed rather than all-STRING, per the Group FS Ingestion Notes — see
    # fs_statements.py. One module, two configs: any difference between the
    # two tables must come from the documents, never from two extractors.
    ("fs_clean",                 fs_statements,   "fs_clean.json"),
    ("fs_seeded",                fs_statements,   "fs_seeded.json"),
    # The CoA mapping pair — Agent 3's affiliate-account-to-Group-node
    # mapping. One module, two configs, same reasoning as the FS pair: one
    # table per affiliate, and any difference between them must come from the
    # workbook rather than from two extractors. See coa_mapping.py.
    ("bronze_coa_mapping_sabic_raw",  coa_mapping,
     "bronze_coa_mapping_sabic.json"),
    ("bronze_coa_mapping_rabigh_raw", coa_mapping,
     "bronze_coa_mapping_rabigh.json"),
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
        description="Excel/CSV data -> bronze CSVs (all tables this package owns)")
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
