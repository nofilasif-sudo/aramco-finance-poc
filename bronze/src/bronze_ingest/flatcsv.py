"""Reading a table out of a flat CSV.

Deliberately separate from `excel.py` rather than an extension of it. A CSV
exported by a tool has none of the problems that module exists to solve: no
title block above the header, no discovered table boundaries, no footer
carrying a control total, no wide period columns to melt. Its header is row 1
and its body is everything after.

Bending `excel.py` to also handle this would add branches to a module that
currently serves three tables cleanly, to save about thirty lines here. The
two file shapes are genuinely different problems.

What IS shared is the contract: columns resolve through an alias table, a
missing required column raises rather than landing a silent column of empty
strings, and nothing is interpreted — every value lands as the text it was.
"""

from __future__ import annotations

import csv

from .excel import IngestError, norm_header


def read_rows(path, aliases: dict[str, list[str]], required: set[str],
              encoding: str = "utf-8-sig") -> tuple[list[dict], list[str]]:
    """Read a flat CSV and return (rows keyed by bronze column name, headers).

    encoding defaults to utf-8-sig because a CSV exported from Excel routinely
    carries a UTF-8 BOM. Read as plain utf-8 that BOM becomes part of the first
    header name, so 'context_key' silently fails to match and the whole file
    looks like it is missing its first column.

    Values are returned exactly as read — str() of whatever the csv module
    produced, with no casting, trimming, or interpretation. Bronze mirrors the
    file.
    """
    with open(path, newline="", encoding=encoding) as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            raise IngestError(f"{path}: file is empty — no header row")
        headers = [norm_header(h) for h in raw_headers]
        pos = _resolve(headers, aliases, required, str(path))

        rows: list[dict] = []
        for record in reader:
            if all(c.strip() == "" for c in record):
                continue          # trailing blank line, not a record
            rows.append({col: (record[i] if i < len(record) else "")
                         for col, i in pos.items()})

    if not rows:
        raise IngestError(f"{path}: header present but no data rows")
    return rows, headers


def _resolve(headers: list[str], aliases: dict[str, list[str]],
             required: set[str], where: str) -> dict[str, int]:
    """Map bronze column -> index in THIS file, via the alias table.

    Same behaviour and same reasoning as excel.resolve_columns: returns only
    the columns actually present, raises if a required one is absent. It is a
    separate function only because that one takes an openpyxl-shaped header
    list; the contract it enforces is identical.
    """
    pos: dict[str, int] = {}
    for out_col, spellings in aliases.items():
        for spelling in spellings:
            if spelling in headers:
                pos[out_col] = headers.index(spelling)
                break
    missing = required - set(pos)
    if missing:
        raise IngestError(
            f"{where}: required column(s) {sorted(missing)} not found. "
            f"Headers seen: {[h for h in headers if h]}")
    return pos
