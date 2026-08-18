"""Render the extracted rows back to the printed page and diff.

    python scripts/roundtrip_check.py

EXHAUSTIVE, not a sample. Both documents together are 304 rows, which is
small enough to check every single label and every single value cell against
the source — so this proves extraction is correct rather than merely
plausible. A production pipeline could not afford this; a one-time load of
two known documents can, and should.

The check is deliberately independent of the extractor's own structural
checks: it re-reads the .docx, re-renders each bronze row back into the
string the page shows (commas restored, negatives re-parenthesised) and
compares. A bug in parse_value would have to be exactly mirrored by a bug in
the renderer below to escape.

Exit code 0 when both documents round-trip cleanly, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_ingest import statements                             # noqa: E402
from fs_ingest.__main__ import TABLES, load_config           # noqa: E402
from fs_ingest.schema import NULL                            # noqa: E402
from fs_ingest.word import read_tables                       # noqa: E402


def as_printed(value: str) -> str:
    """A stored value back into the string the page shows."""
    if value == NULL:
        return ""
    number = Decimal(value)
    shown = f"{abs(number):,}"
    return f"({shown})" if number < 0 else shown


def check(name: str, config_file: str) -> tuple[int, int]:
    cfg = load_config(ROOT / "configs" / config_file)
    rows, _ = statements.extract(cfg["input_path"], cfg, [])
    tables = read_tables(cfg["input_path"])

    indexed: dict = defaultdict(dict)
    for row in rows:
        indexed[row["statement"]].setdefault(row["row_ord"], {})
        indexed[row["statement"]][row["row_ord"]][row["column_label"]] = row

    mismatches: list[str] = []
    cells = 0

    for table in tables:
        spec = cfg["statements"][table.title]
        columns = spec.get("column_labels", list(table.header[1:]))
        for ordinal, line in enumerate(table.rows, start=1):
            stored = indexed[table.title][ordinal]

            if line.cells[0] != stored[columns[0]]["label"]:
                mismatches.append(
                    f"{table.title} line {ordinal} LABEL: "
                    f"page={line.cells[0]!r} "
                    f"bronze={stored[columns[0]]['label']!r}")

            for index, column in enumerate(columns):
                page = (line.cells[index + 1].strip()
                        if index + 1 < len(line.cells) else "")
                back = as_printed(stored[column]["value"])
                cells += 1
                if page != back:
                    mismatches.append(
                        f"{table.title} line {ordinal} "
                        f"({line.cells[0]}) [{column}]: "
                        f"page={page!r} bronze={back!r}")

    status = "OK" if not mismatches else "FAIL"
    print(f"[{status}] {name}: {len(rows)} rows, {cells} value cells, "
          f"{len(mismatches)} mismatch(es)")
    for line in mismatches:
        print(f"        {line}")
    return len(mismatches), cells


def main() -> int:
    total_bad = total_cells = 0
    for name, config_file in TABLES:
        bad, cells = check(name, config_file)
        total_bad += bad
        total_cells += cells

    print(f"\n{total_cells} value cells round-tripped, "
          f"{total_bad} mismatch(es)")
    if total_bad:
        print("Extraction does NOT reproduce the source documents.",
              file=sys.stderr)
        return 1
    print("Both documents reproduce exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
