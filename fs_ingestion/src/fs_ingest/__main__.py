"""CLI entry point.

Named __main__.py so the package is runnable with `python -m fs_ingest`.
It stays thin on purpose: everything below it is importable, so an
orchestrator can call `statements.extract()` directly without going through
argparse.

    python -m fs_ingest                     # both documents -> outputs/*.csv
    python -m fs_ingest --only fs_seeded
    python -m fs_ingest --dry-run           # run the checks, write nothing

Each document is read independently: a structural failure in one is reported
and stops that document's write, but does not prevent the other from
landing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import statements
from .errors import IngestError
from .schema import COLUMNS
from .sink import write_csv

# The package root, from src/fs_ingest/__main__.py -> up three.
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs"

# (table name, config filename). Both documents use the SAME extractor and
# the same schema — only the config's input_path differs.
TABLES = [
    ("fs_clean", "fs_clean.json"),
    ("fs_seeded", "fs_seeded.json"),
]


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    # Relative paths resolve against the package root, not the shell's cwd,
    # so the pipeline behaves the same from any directory.
    for key in ("input_path", "output_dir"):
        p = Path(cfg[key])
        cfg[key] = p if p.is_absolute() else (ROOT / p).resolve()
    return cfg


def run_one(cfg: dict, dry_run: bool) -> dict:
    """Ingest one document. Returns the run summary. Raises IngestError."""
    report: list[str] = []
    rows, meta = statements.extract(cfg["input_path"], cfg, report)

    if dry_run:
        report.append(f"dry run — would write {len(rows)} rows")
    else:
        out = cfg["output_dir"] / cfg["output_file"]
        meta["_output"] = write_csv(rows, COLUMNS, out, cfg["csv_encoding"],
                                    report)

    for line in report:
        print("   ", line)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="fs-ingest",
        description="Group FS .docx -> bronze CSV (fs_clean, fs_seeded)")
    ap.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    ap.add_argument("--dry-run", action="store_true",
                    help="run the checks, write nothing")
    ap.add_argument("--only", help="comma-separated table names, "
                    "e.g. fs_seeded")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    failed: list[str] = []

    for name, config_file in TABLES:
        if only and name not in only:
            continue
        print(f"\n[{name}] config: {config_file}")
        try:
            cfg = load_config(args.config_dir / config_file)
            print(f"    {cfg['input_path']}")
            run_one(cfg, args.dry_run)
        except IngestError as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            print("    No output written for this document.", file=sys.stderr)
            failed.append(name)
        except FileNotFoundError as exc:
            print(f"    FAILED: {exc}", file=sys.stderr)
            failed.append(name)

    print()
    if failed:
        print(f"FAILED: {len(failed)}/{len(TABLES)} document(s) did not land: "
              f"{', '.join(failed)}", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
