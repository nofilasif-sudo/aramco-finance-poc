"""Tests for the Group trial-balance ingestion (bronze_group_tb_raw).

Plain unittest, no pytest needed:  python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from bronze_ingest.excel import IngestError
from bronze_ingest.group_tb import COLUMNS, extract

CFG = {
    "header_sentinel": "account",
    "drop_section_rows": True,
    "csv_encoding": "utf-8",
    "nil_abs_tolerance": 0.01,
    "nil_rel_tolerance": 1e-9,
}


def fixture(unbalanced: bool = False) -> Path:
    """Header on row 5 (not row 6) and 'Group node' as the extra id column,
    so the test fails if header discovery or the extra-column shape regresses."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Group TB - Fixture"
    ws.append(["Fixture Group Trial Balance"])
    ws.append(["core operations only"])
    ws.append([])
    ws.append(["Account", "Group node", "Type", "Category",
               "2024 Q1", "2024 Q2"])
    ws.append(["NON-CURRENT ASSETS"])
    ws.append(["G11000", "Property, plant & equipment", "Balance sheet",
               "Non-current assets", 1000.0, 1010.0])
    ws.append(["G40000", "Revenue", "Income statement", "Revenue",
               -1000.0 + (1.0 if unbalanced else 0.0), -1010.0])
    ws.append([])
    ws.append([None, "TRIAL BALANCE CHECK", None, None, 0, 0])
    ws.row_dimensions[8].height = 15
    path = Path(tempfile.gettempdir()) / f"group_tb_fixture_{unbalanced}.xlsx"
    wb.save(path)
    return path


class TestExtract(unittest.TestCase):
    def test_header_found_and_group_node_column_lands(self):
        rows, _ = extract(fixture(), CFG, [])
        self.assertEqual(
            {r["group_node"] for r in rows},
            {"Property, plant & equipment", "Revenue"})

    def test_no_affiliate_code_column(self):
        self.assertNotIn("affiliate_code", COLUMNS)

    def test_type_stays_long_form_unnormalised(self):
        rows, _ = extract(fixture(), CFG, [])
        types = {r["type"] for r in rows}
        self.assertEqual(types, {"Balance sheet", "Income statement"})

    def test_melted_one_row_per_period(self):
        rows, meta = extract(fixture(), CFG, [])
        m = meta["Group TB - Fixture"]
        self.assertEqual(m["periods"], ["2024 Q1", "2024 Q2"])
        self.assertEqual(len(rows), 4)   # 2 accounts x 2 periods (section dropped)

    def test_source_file_populated(self):
        path = fixture()
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_unbalanced_refuses_to_land(self):
        with self.assertRaises(IngestError) as ctx:
            extract(fixture(unbalanced=True), CFG, [])
        self.assertIn("prove to nil", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
