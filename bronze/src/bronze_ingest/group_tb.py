"""bronze_group_tb_raw — the Aramco Group (parent-only) trial balance.

Contract (bronze.dbml v2, 12 Aug — NEW table):
  - Six columns, all text, unpivoted the same way as bronze_tb_raw.
  - No affiliate_code / chart_scope: this workbook carries a single entity
    (Aramco core operations), so there is nothing to derive from the tab
    name. entity assignment happens in silver, not here.
  - group_node is an EXTRA column vs the affiliate TB: this sheet's column A
    is already the G-code and column B is the node NAME — both land as-is;
    silver decides what to do with them.
  - `type` stays LONG FORM ("Balance sheet" / "Income statement") — the
    affiliate TB uses BS/PL for the same concept. Bronze does not harmonise
    vocabularies across sources; that is silver's job.
  - Section dividers are DROPPED, same policy as bronze_tb_raw — verified
    against this sheet too: all 9 divider captions already appear verbatim
    in `category`.
  - Refuses to land unless every period column proves to nil, same mechanism
    as bronze_tb_raw.

Expected against the current pack: 59 account/subtotal rows x 9 periods =
531 (68 body rows minus 9 dropped dividers).
"""

from __future__ import annotations

from .excel import (IngestError, Table, find_period_columns, read_tables,
                    resolve_columns, sum_account_rows, check_row_by_label,
                    to_raw_str)

COLUMNS = ["account", "group_node", "type", "category", "period_label",
           "amount", "source_file"]

ID_ALIASES = {
    "account":    ["account"],
    "group_node": ["group node"],
    "type":       ["type"],
    "category":   ["category"],
}
REQUIRED = set(ID_ALIASES)


def drop_row(row: tuple, enabled: bool) -> bool:
    """Same section-divider policy as coa.drop_row / tb.drop_row."""
    return enabled and Table.classify(row) == "section"


def check_proves_to_nil(tab: Table, periods: list[tuple[int, str]],
                        cfg: dict, report: list[str]) -> dict:
    """Sum the account rows per period column and require zero. See
    tb.check_proves_to_nil for the reasoning — identical mechanism."""
    results, failures = {}, []
    for idx, label in periods:
        total, magnitude = sum_account_rows(tab, idx)
        tol = max(cfg["nil_abs_tolerance"], cfg["nil_rel_tolerance"] * magnitude)
        ok = abs(total) <= tol
        results[label] = {"sum": total, "tolerance": tol,
                          "status": "OK" if ok else "FAIL"}
        if not ok:
            failures.append(f"{label} sums to {total:,.4f} (tol {tol:,.4f})")
    if failures:
        raise IngestError(
            f"{tab.title}: trial balance does not prove to nil — "
            + "; ".join(failures) + " — refusing to land.")
    report.append(f"proves to nil {tab.title}: all {len(periods)} period "
                  f"columns sum to 0 across account rows -> OK")
    return results


def check_sheet_check_row(tab: Table, periods: list[tuple[int, str]],
                          report: list[str]) -> dict:
    row = check_row_by_label(tab, "check")
    if row is None:
        report.append(f"[warn] {tab.title}: no CHECK row in footer")
        return {"status": "ABSENT"}
    bad = []
    for idx, label in periods:
        v = row[idx] if idx < len(row) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0:
            bad.append(f"{label}={v}")
    if bad:
        report.append(f"[warn] {tab.title}: sheet CHECK row is non-zero "
                      f"({', '.join(bad)}) — workbook is internally "
                      f"inconsistent")
        return {"status": "NONZERO", "detail": bad}
    report.append(f"sheet CHECK row {tab.title}: all zero -> OK")
    return {"status": "OK"}


def extract(path, cfg: dict, report: list[str]) -> tuple[list[dict], dict]:
    """Melt the Group TB tab and return (rows, per-tab metadata)."""
    rows: list[dict] = []
    meta: dict = {}
    source_file = getattr(path, "name", str(path))

    for title, tab in read_tables(path, cfg["header_sentinel"]):
        pos = resolve_columns(tab.headers, ID_ALIASES, REQUIRED, title)

        last_id = max(pos.values())
        periods = find_period_columns(tab.raw_headers, last_id)
        if not periods:
            raise IngestError(f"{title}: no period columns found right of "
                              f"'{tab.headers[last_id]}'")
        labels = [p[1] for p in periods]
        dupes = {l for l in labels if labels.count(l) > 1}
        if dupes:
            raise IngestError(f"{title}: duplicate period labels "
                              f"{sorted(dupes)} — melt would be ambiguous")

        melted = 0
        for row in tab.data:
            if drop_row(row, cfg["drop_section_rows"]):
                continue
            melted += 1
            base = {b: to_raw_str(row[i] if i < len(row) else None)
                    for b, i in pos.items()}
            for i, label in periods:
                rows.append({**base, "period_label": label,
                            "amount": to_raw_str(row[i] if i < len(row) else None),
                            "source_file": source_file})

        counts = tab.counts()
        nil = check_proves_to_nil(tab, periods, cfg, report)
        chk = check_sheet_check_row(tab, periods, report)
        dropped = len(tab.data) - melted
        report.append(
            f"{title}: {len(tab.data)} source rows, {dropped} dividers "
            f"dropped, {melted} x {len(periods)} periods = "
            f"{melted * len(periods)} rows")
        meta[title] = {"source_rows": len(tab.data), "sections_dropped": dropped,
                       "rows_melted": melted, "periods": labels,
                       "period_count": len(labels),
                       "rows_landed": melted * len(periods), "row_kinds": counts,
                       "proves_to_nil": nil, "sheet_check_row": chk,
                       "columns_found": sorted(pos)}

    if not rows:
        raise IngestError(f"{path}: no Group TB tabs found")
    return rows, meta
