"""bronze_entity_context_raw — reporting-entity metadata.

Key-value reference data describing the reporting entity: who it is, the
period, the currency, the segments, the reporting framework.

*** THIS IS DELIBERATELY KEY-VALUE (EAV) SHAPED ***
Entity-Attribute-Value is normally an anti-pattern in an analytics warehouse:
you cannot type the columns, cannot constrain the values, and every query has
to pivot. It is the right shape HERE for three specific reasons, and it stops
being right if any of them change:

  1. The attributes are open-ended narrative, not a fixed schema. New context
     keys arrive without warning ('industry', 'main_operations' and
     'secondary_currency' appeared between two exports of this same file).
  2. There is exactly one entity described, so there is no cross-entity
     comparison to make and nothing to aggregate.
  3. The consumer is an agent reading prose, not a BI tool summing a measure.

If a second entity ever submits its own context, the key becomes
(entity_code, context_key) and the case for pivoting into real columns gets
much stronger. See PLAN_csv_ingestion.md §4.

Source: entity_context (1).csv — the 13-key export. A second 10-key file of
the same name exists; the two are NOT identical (the shorter one lacks three
keys and carries a terser presentation_currency), so only one may land — the
grain is one row per context_key, and loading both would duplicate every key.

Expected against the current pack: 13 rows.
"""

from __future__ import annotations

from .flatcsv import read_rows

COLUMNS = ["context_key", "context_value", "source_file"]

ALIASES = {
    "context_key":   ["context_key", "key"],
    "context_value": ["context_value", "value"],
}
REQUIRED = set(ALIASES)


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Read the context CSV and return (rows, metadata)."""
    source_file = getattr(path, "name", str(path))
    raw, headers = read_rows(path, ALIASES, REQUIRED,
                             cfg.get("csv_read_encoding", "utf-8-sig"))

    rows = [{**rec, "source_file": source_file} for rec in raw]

    keys = [r["context_key"] for r in rows]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        # context_key is the primary key. A duplicate means two files were
        # concatenated or one was loaded twice — either way the table would
        # answer 'what is the presentation currency?' with two rows.
        from .excel import IngestError
        raise IngestError(
            f"{source_file}: duplicate context_key(s) {dupes} — refusing to "
            f"land, the key would not be a key.")

    report.append(f"{source_file}: {len(rows)} context keys landed")
    meta = {source_file: {"rows_landed": len(rows), "context_keys": sorted(keys)}}
    return rows, meta
