"""Tests for the IFRS standard-context ingestion (bronze_ifrs_standard_raw).

Plain unittest, no pytest needed:  python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bronze_ingest.excel import IngestError
from bronze_ingest.ifrs_standard import COLUMNS, extract

CFG = {"csv_read_encoding": "utf-8-sig", "csv_encoding": "utf-8"}

GOOD = ('standard_code,standard_title,disclosure_summary\n'
        'IFRS 15,IFRS 15 Revenue from Contracts with Customers,'
        '"Requires disclosure of revenue policies, disaggregation, and more."\n'
        'IAS 24,IAS 24 Related Party Disclosures,'
        '"Requires disclosure of relationships and controlling party."\n')


def fixture(name: str, body: str) -> Path:
    path = Path(tempfile.gettempdir()) / name
    path.write_text(body, encoding="utf-8", newline="")
    return path


class TestExtract(unittest.TestCase):
    def test_standards_land(self):
        rows, _ = extract(fixture("std_good.csv", GOOD), CFG, [])
        self.assertEqual([r["standard_code"] for r in rows],
                         ["IFRS 15", "IAS 24"])

    def test_long_summary_with_commas_survives(self):
        rows, _ = extract(fixture("std_good.csv", GOOD), CFG, [])
        self.assertTrue(rows[0]["disclosure_summary"].endswith("and more."))

    def test_source_file_populated(self):
        path = fixture("std_good.csv", GOOD)
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_duplicate_standard_code_refuses_to_land(self):
        dupe = GOOD + "IFRS 15,Something else,Another summary\n"
        with self.assertRaises(IngestError) as ctx:
            extract(fixture("std_dupe.csv", dupe), CFG, [])
        self.assertIn("duplicate standard_code", str(ctx.exception))

    def test_missing_required_column_raises(self):
        bad = "standard_code,standard_title\nIFRS 15,Revenue\n"
        with self.assertRaises(IngestError):
            extract(fixture("std_bad.csv", bad), CFG, [])


class TestSchema(unittest.TestCase):
    def test_columns_include_source_file(self):
        self.assertEqual(COLUMNS[-1], "source_file")


if __name__ == "__main__":
    unittest.main()
