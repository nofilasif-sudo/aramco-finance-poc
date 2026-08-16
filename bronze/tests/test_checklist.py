"""Tests for the submission-checklist ingestion (bronze_checklist_raw).

Plain unittest, no pytest needed:  python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from bronze_ingest.excel import IngestError
from bronze_ingest.checklist import COLUMNS, extract

CFG = {"header_sentinel": "item", "csv_encoding": "utf-8"}


def fixture(missing_column: bool = False) -> Path:
    """Header on row 3 (not row 4). Also carries a 'Manifest'-shaped tab
    whose first header cell is 'Entity', never 'Item' — proves the
    Checklist-only selection is structural, not a name check."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist"
    ws.append(["Checklist"])
    ws.append([])
    headers = ["Item", "Document", "Required", "Applies to",
               "Expected format", "Description"]
    if missing_column:
        headers.remove("Required")
    ws.append(headers)
    ws.append(["01", "Signed trial balance", "Yes", "All affiliates",
               "xlsx / SAP BPC export", "Full signed TB"])
    ws.append(["02", "Income statement", "Yes", "All affiliates",
               "xlsx / pdf", "Period income statement"])
    ws.append([])

    manifest = wb.create_sheet("Manifest - Fixture")
    manifest.append(["Entity", "Item", "Document", "Status"])
    manifest.append(["SABIC", "01", "Signed trial balance", "Present"])

    path = Path(tempfile.gettempdir()) / f"checklist_fixture_{missing_column}.xlsx"
    wb.save(path)
    return path


class TestExtract(unittest.TestCase):
    def test_header_found_not_assumed(self):
        rows, _ = extract(fixture(), CFG, [])
        self.assertEqual([r["item"] for r in rows], ["01", "02"])

    def test_manifest_tab_skipped_structurally(self):
        rows, meta = extract(fixture(), CFG, [])
        self.assertNotIn("Manifest - Fixture", meta)
        self.assertEqual(len(rows), 2)

    def test_source_file_populated(self):
        path = fixture()
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_missing_required_column_raises(self):
        with self.assertRaises(IngestError):
            extract(fixture(missing_column=True), CFG, [])

    def test_columns_include_source_file(self):
        self.assertEqual(COLUMNS[-1], "source_file")


if __name__ == "__main__":
    unittest.main()
