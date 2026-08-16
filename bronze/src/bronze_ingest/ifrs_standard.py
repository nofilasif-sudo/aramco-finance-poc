"""bronze_ifrs_standard_raw — one row per IFRS/IAS standard.

The standard-level parent of bronze_ifrs_rubric_raw: full title plus a
paragraph summarising what the standard requires to be disclosed.

Held as its own table rather than as repeated columns on the rubric because
disclosure_summary is a long paragraph with only three distinct values, and
repeating it across five requirement rows per standard is redundancy that can
drift. It also gives an agent a place to ask 'which standards are in scope?'
without reading fifteen requirement rows.

Verified against the pack: the three standard_code values here match the three
in the rubric exactly — no orphans in either direction.

Expected against the current pack: 3 rows.
"""

from __future__ import annotations

from .flatcsv import read_rows

COLUMNS = ["standard_code", "standard_title", "disclosure_summary",
           "source_file"]

ALIASES = {
    "standard_code":      ["standard_code"],
    "standard_title":     ["standard_title"],
    "disclosure_summary": ["disclosure_summary"],
}
REQUIRED = set(ALIASES)


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read the standard-context CSV and return (rows, metadata)."""
    source_file = getattr(path, "name", str(path))
    raw, _ = read_rows(path, ALIASES, REQUIRED,
                       cfg.get("csv_read_encoding", "utf-8-sig"))

    rows = [{**rec, "source_file": source_file} for rec in raw]

    codes = [r["standard_code"] for r in rows]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    if dupes:
        from .excel import IngestError
        raise IngestError(
            f"{source_file}: duplicate standard_code(s) {dupes} — refusing to "
            f"land, the key would not be a key.")

    report.append(f"{source_file}: {len(rows)} standards landed "
                  f"({', '.join(codes)})")
    meta = {source_file: {"rows_landed": len(rows), "standard_codes": codes}}
    return rows, meta
