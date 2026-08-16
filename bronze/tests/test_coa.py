"""Tests for the CoA ingestion.

Plain unittest, no pytest needed:  python -m unittest discover -s tests

These assert BEHAVIOUR, not shape. `assert not df.empty` passes when every
value is wrong; each test below fails if a specific decision regresses.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from bronze_ingest.coa import COLUMNS, chart_scope, extract, write_csv
from bronze_ingest.excel import IngestError, Table, to_raw_str

CFG = {
    "header_sentinel": "account",
    "affiliate_pattern": r"\((\d{4})\)",
    "group_marker": "group",
    "drop_section_rows": True,
    "csv_encoding": "utf-8",
}


def fixture(total_accounts: int = 2) -> Path:
    """A workbook shaped like the real pack, but deliberately different.

    The header sits on row 4 rather than row 6, and the category column uses
    the CoA spelling - so the test fails if header discovery regresses to a
    fixed row, or if alias resolution is dropped.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "CoA - Fixture (2010)"
    ws.append(["Fixture Chart of Accounts"])
    ws.append(["some note"])
    ws.append([])
    ws.append(["Account", "Account name", "Statement",
               "Category (FS caption group)", "Normal balance"])
    ws.append(["NON-CURRENT ASSETS"])                      # section divider
    ws.append(["1100", "Land", "Balance sheet", "Non-current assets", "Dr"])
    ws.append(["1110", "  Buildings", "Balance sheet", "Non-current assets", "Dr"])
    ws.append([])                                          # blank ends the body
    ws.append([None, f"Total accounts: {total_accounts}"])  # footer
    ws.row_dimensions[8].height = 15   # force the blank row into the saved XML
    path = Path(tempfile.gettempdir()) / f"coa_fixture_{total_accounts}.xlsx"
    wb.save(path)
    return path


class TestScope(unittest.TestCase):
    def test_affiliate_code_comes_from_the_tab_name(self):
        self.assertEqual(chart_scope("CoA - SABIC (2010)", r"\((\d{4})\)", "group"), "2010")
        self.assertEqual(chart_scope("CoA - Petro Rabigh (2380)", r"\((\d{4})\)", "group"), "2380")

    def test_group_tab_resolves_to_GROUP(self):
        self.assertEqual(chart_scope("CoA - Group (Aramco)", r"\((\d{4})\)", "group"), "GROUP")

    def test_unattributable_tab_raises(self):
        # Landing rows with a blank chart_scope would make them unjoinable,
        # because the two affiliate charts share 42 account codes.
        with self.assertRaises(IngestError):
            chart_scope("CoA - Mystery", r"\((\d{4})\)", "group")


class TestExtract(unittest.TestCase):
    def test_header_found_not_assumed_and_aliases_resolve(self):
        rows, meta = extract(fixture(), CFG, [])
        self.assertEqual([r["account"] for r in rows], ["1100", "1110"])
        self.assertEqual(rows[0]["category"], "Non-current assets")

    def test_section_divider_dropped_indentation_kept(self):
        rows, meta = extract(fixture(), CFG, [])
        m = meta["CoA - Fixture (2010)"]
        self.assertEqual(m["sections_dropped"], 1)
        self.assertEqual(m["rows_landed"], 2)
        self.assertNotIn("NON-CURRENT ASSETS", [r["account"] for r in rows])
        # indentation encodes level-2 hierarchy - it must survive
        self.assertEqual(rows[1]["account_name"], "  Buildings")

    def test_group_only_columns_land_empty_for_affiliates(self):
        rows, _ = extract(fixture(), CFG, [])
        self.assertEqual(rows[0]["level"], "")
        self.assertEqual(rows[0]["source_reference"], "")

    def test_source_file_populated_from_workbook_name(self):
        path = fixture()
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_control_total_mismatch_refuses_to_land(self):
        # A check that has never been seen to fail is not a check.
        with self.assertRaises(IngestError):
            extract(fixture(total_accounts=99), CFG, [])

    def test_keeping_section_rows_is_a_switch_not_a_rewrite(self):
        rows, _ = extract(fixture(), {**CFG, "drop_section_rows": False}, [])
        self.assertEqual(len(rows), 3)


class TestCellFormatting(unittest.TestCase):
    def test_xlsx_float_artifact_removed(self):
        # xlsx has no integer type; level 1 is stored as 1.0
        self.assertEqual(to_raw_str(1.0), "1")
        self.assertEqual(to_raw_str(1.5), "1.5")
        self.assertEqual(to_raw_str(4.175e7), "41750000")  # never 4.175E7
        self.assertEqual(to_raw_str(None), "")


class TestClassify(unittest.TestCase):
    def test_row_kinds(self):
        self.assertEqual(Table.classify(("1100", "Land")), "account")
        self.assertEqual(Table.classify(("NON-CURRENT ASSETS", None)), "section")
        self.assertEqual(Table.classify((None, "  Subtotal - Revenue")), "subtotal")


class TestSink(unittest.TestCase):
    def test_header_matches_the_dbml_exactly(self):
        rows, _ = extract(fixture(), CFG, [])
        out = Path(tempfile.gettempdir()) / "bronze_coa_test.csv"
        write_csv(rows, out, "utf-8", [])
        first = out.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first, ",".join(COLUMNS))

    def test_line_endings_are_lf_on_every_platform(self):
        rows, _ = extract(fixture(), CFG, [])
        out = Path(tempfile.gettempdir()) / "bronze_coa_test.csv"
        write_csv(rows, out, "utf-8", [])
        self.assertNotIn(b"\r\n", out.read_bytes())


if __name__ == "__main__":
    unittest.main()
