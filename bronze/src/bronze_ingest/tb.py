"""bronze_tb_raw — the two affiliate trial balances, unpivoted.

Contract (DBML 12 Aug, v2 — "UNCHANGED" from the original agreement):
  - Seven columns, all text. No casting.
  - The nine quarter columns are UNPIVOTED (melted): one input row becomes
    one output row per period. This is the one shape change bronze makes,
    and it is lossless/reversible, which is why it is allowed.
  - affiliate_code is ADDED at ingest from the tab name, same reasoning as
    coa.chart_scope.
  - Section-divider rows are DROPPED, same policy and same justification as
    coa.drop_row: verified against both TB tabs (13 dividers in SABIC, 11 in
    Petro Rabigh) that every divider caption already appears verbatim in the
    `category` column of the rows beneath it — nothing is lost. This
    deviates from the DBML's "no filtering" framing for bronze_tb_raw the
    same way it already does for bronze_coa_raw; the same drop_row()
    switch exists so it is one config flag, not a rewrite, if that decision
    is ever revisited.
  - Subtotal rows are ALSO DROPPED (configs/bronze_tb.json:
    drop_subtotal_rows=true), so this table lands account-rows-only,
    matching the grain fact_trial_balance is built on. Unlike the divider
    drop, this is not a "nothing is lost" claim — a subtotal is an
    independently-computed figure, not a redundant label — so it is a
    separate, independent switch (default false) rather than folded into
    drop_section_rows.
  - Refuses to land unless every period column proves to nil.

Expected against the current pack: (66 + 44) accounts x 9 periods = 990 —
same grain and same 990 figures as the account rows always carried, now
with source_file lineage added.
"""

from __future__ import annotations

from .excel import (IngestError, Table, find_period_columns, read_tables,
                    resolve_columns, sum_account_rows, check_row_by_label,
                    to_raw_str)

COLUMNS = ["affiliate_code", "account", "account_name", "type", "category",
           "period_label", "amount", "source_file"]

ID_ALIASES = {
    "account":      ["account"],
    "account_name": ["account name"],
    "type":         ["type"],
    "category":     ["category", "category (fs caption group)"],
}
REQUIRED = set(ID_ALIASES)


def affiliate_code(title: str, affiliate_pattern: str) -> str:
    """Derive affiliate_code from the tab name. Raises if it cannot be found —
    a TB tab with no affiliate code is unattributable, same reasoning as
    coa.chart_scope."""
    import re
    m = re.search(affiliate_pattern, title)
    if m:
        return m.group(1)
    raise IngestError(
        f"tab '{title}' looks like a TB tab but carries no affiliate code "
        f"'(NNNN)'")


def drop_row(row: tuple, drop_sections: bool, drop_subtotals: bool) -> bool:
    """Two independent filtering switches, kept as two flags rather than one
    so each decision stays reversible on its own.

    drop_sections: same rule and justification as coa.drop_row — divider
        captions duplicate the category column.

    drop_subtotals: OFF unless a config turns it on. Unlike dividers,
        subtotal rows carry an independently-computed figure that is not
        trivially reconstructible from the account rows alone without
        re-deriving the sheet's own grouping — dropping them is a bigger
        claim than dropping a redundant label, so it defaults to false and
        has to be requested explicitly (configs/bronze_tb.json sets it true
        so bronze_tb_raw lands account-rows-only, matching the grain
        fact_trial_balance is built on).
    """
    kind = Table.classify(row)
    return (drop_sections and kind == "section") or (drop_subtotals and kind == "subtotal")


def check_proves_to_nil(tab: Table, periods: list[tuple[int, str]],
                        cfg: dict, report: list[str]) -> dict:
    """Sum the account rows per period column and require zero.

    Computed from the same cells being landed rather than trusting the
    sheet's own CHECK row, which is a formula and can be wrong or stale.
    """
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
    """Read the sheet's own 'TRIAL BALANCE CHECK' row as an advisory second
    opinion. Our own sum (check_proves_to_nil) is what governs the refusal."""
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
    """Melt every TB tab and return (rows, per-tab metadata)."""
    rows: list[dict] = []
    meta: dict = {}
    source_file = getattr(path, "name", str(path))
    period_sets: dict[str, list[str]] = {}

    for title, tab in read_tables(path, cfg["header_sentinel"]):
        affiliate = affiliate_code(title, cfg["affiliate_pattern"])
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
        period_sets[title] = labels

        drop_subtotals = cfg.get("drop_subtotal_rows", False)
        melted = 0
        for row in tab.data:
            if drop_row(row, cfg["drop_section_rows"], drop_subtotals):
                continue
            melted += 1
            base = {b: to_raw_str(row[i] if i < len(row) else None)
                    for b, i in pos.items()}
            for i, label in periods:
                rows.append({"affiliate_code": affiliate, **base,
                            "period_label": label,
                            "amount": to_raw_str(row[i] if i < len(row) else None),
                            "source_file": source_file})

        counts = tab.counts()
        nil = check_proves_to_nil(tab, periods, cfg, report)
        chk = check_sheet_check_row(tab, periods, report)
        dropped = len(tab.data) - melted
        report.append(
            f"{title}: {len(tab.data)} source rows, {dropped} dropped "
            f"(sections{' + subtotals' if drop_subtotals else ''}), "
            f"{melted} x {len(periods)} periods = "
            f"{melted * len(periods)} rows -> affiliate {affiliate}")
        meta[title] = {"affiliate_code": affiliate, "source_rows": len(tab.data),
                       "rows_dropped": dropped, "rows_melted": melted,
                       "periods": labels, "period_count": len(labels),
                       "rows_landed": melted * len(periods), "row_kinds": counts,
                       "proves_to_nil": nil, "sheet_check_row": chk,
                       "columns_found": sorted(pos)}

    distinct = {tuple(v) for v in period_sets.values()}
    if len(distinct) > 1:
        report.append(f"[warn] TB tabs do not share the same period set: "
                      f"{period_sets}")

    if not rows:
        raise IngestError(f"{path}: no TB tabs found")
    return rows, meta
