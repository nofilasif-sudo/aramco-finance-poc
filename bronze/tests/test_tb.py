"""Tests for the trial-balance ingestion (bronze_tb_raw).

Plain unittest, no pytest needed:  python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from bronze_ingest.excel import IngestError
from bronze_ingest.tb import COLUMNS, affiliate_code, extract

CFG = {
    "header_sentinel": "account",
    "affiliate_pattern": r"\((\d{4})\)",
    "drop_section_rows": True,
    "csv_encoding": "utf-8",
    "nil_abs_tolerance": 0.01,
    "nil_rel_tolerance": 1e-9,
}


def fixture(unbalanced: bool = False) -> Path:
    """A workbook shaped like the real pack, but deliberately different:
    header on row 4 instead of row 6, so the test fails if header discovery
    ever regresses to a fixed row."""
    wb = Workbook()
    ws = wb.active
    ws.title = "TB - Fixture (2010)"
    ws.append(["Fixture Trial Balance"])
    ws.append(["signed convention note"])
    ws.append([])
    ws.append(["Account", "Account name", "Type", "Category",
               "2024 Q1", "2024 Q2"])
    ws.append(["NON-CURRENT ASSETS"])                              # section
    ws.append(["1100", "Land", "BS", "Non-current assets", 500.0, 510.5])
    ws.append(["4000", "Revenue", "PL", "Revenue",
               -500.0 + (1.0 if unbalanced else 0.0), -510.5])
    ws.append([None, "  Subtotal - all", None, None, 0.0, 0.0])    # subtotal
    ws.append([])                                                  # blank ends body
    ws.append([None, "TRIAL BALANCE CHECK", None, None, 0, 0])     # footer
    ws.row_dimensions[9].height = 15
    path = Path(tempfile.gettempdir()) / f"tb_fixture_{unbalanced}.xlsx"
    wb.save(path)
    return path


class TestAffiliateCode(unittest.TestCase):
    def test_code_comes_from_the_tab_name(self):
        self.assertEqual(affiliate_code("TB - SABIC (2010)", r"\((\d{4})\)"), "2010")

    def test_unattributable_tab_raises(self):
        with self.assertRaises(IngestError):
            affiliate_code("TB - Mystery", r"\((\d{4})\)")


class TestExtract(unittest.TestCase):
    def test_header_found_not_assumed_and_aliases_resolve(self):
        rows, meta = extract(fixture(), CFG, [])
        self.assertEqual([r["account"] for r in rows if r["account"]],
                         ["1100", "1100", "4000", "4000"])

    def test_melted_one_row_per_period(self):
        rows, meta = extract(fixture(), CFG, [])
        m = meta["TB - Fixture (2010)"]
        self.assertEqual(m["periods"], ["2024 Q1", "2024 Q2"])
        # section dropped, 2 accounts + 1 subtotal = 3 rows melted x 2 periods
        self.assertEqual(m["rows_landed"], 6)
        self.assertEqual(len(rows), 6)

    def test_section_divider_dropped_subtotal_lands_by_default(self):
        rows, _ = extract(fixture(), CFG, [])
        self.assertNotIn("NON-CURRENT ASSETS",
                         [r["account_name"] for r in rows])
        self.assertIn("  Subtotal - all", [r["account_name"] for r in rows])

    def test_drop_subtotal_rows_is_an_independent_switch(self):
        rows, meta = extract(fixture(), {**CFG, "drop_subtotal_rows": True}, [])
        self.assertNotIn("  Subtotal - all", [r["account_name"] for r in rows])
        # 2 accounts x 2 periods only, subtotal gone
        self.assertEqual(len(rows), 4)
        self.assertEqual(meta["TB - Fixture (2010)"]["rows_landed"], 4)

    def test_source_file_populated(self):
        path = fixture()
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_unbalanced_trial_balance_refuses_to_land(self):
        with self.assertRaises(IngestError) as ctx:
            extract(fixture(unbalanced=True), CFG, [])
        self.assertIn("prove to nil", str(ctx.exception))


class TestSink(unittest.TestCase):
    def test_columns_include_source_file(self):
        self.assertEqual(COLUMNS[-1], "source_file")


if __name__ == "__main__":
    unittest.main()
