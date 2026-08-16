"""Writing bronze rows out as CSV.

Kept separate from `excel.py` (which only reads spreadsheets) and separate
from each domain module, so the five bronze extractors share one CSV writer
instead of five near-identical copies.
"""

from __future__ import annotations

import csv
import io


def to_csv_text(columns: list[str], rows: list[dict]) -> str:
    """Render rows as CSV text.

    Returns a string rather than writing a file so the same bytes can go to
    a local path, a GCS blob, or a test assertion.

    lineterminator="\\n" is explicit: csv defaults to \\r\\n, which would make
    the same data produce different bytes on Windows and Linux and break any
    hash comparison between a laptop run and a cloud run.
    """
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=columns, quoting=csv.QUOTE_MINIMAL,
                       lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_csv(rows: list[dict], columns: list[str], path, encoding: str,
              report: list[str]) -> dict:
    """Write a bronze CSV to a local path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_csv_text(columns, rows), encoding=encoding, newline="")
    report.append(f"wrote {len(rows)} rows -> {path}")
    return {"path": str(path), "rows": len(rows), "bytes": path.stat().st_size}
