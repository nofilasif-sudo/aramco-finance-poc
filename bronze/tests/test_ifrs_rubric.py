"""Tests for the IFRS rubric ingestion (bronze_ifrs_rubric_raw).

Re-sourced from ifrs_requirements_updated.csv — these replace the previous
xlsx-based tests, which no longer describe how the table is built.

Plain unittest, no pytest needed:  python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bronze_ingest.excel import IngestError
from bronze_ingest.ifrs_rubric import COLUMNS, extract

CFG = {"csv_read_encoding": "utf-8-sig", "csv_encoding": "utf-8"}

HEADER = ("standard_code,standard_name,req_id,requirement,"
          "evidence_type,check_guidance\n")

GOOD = HEADER + (
    'IFRS 15,IFRS 15 Revenue,R1,Revenue recognition policy,narrative,'
    '"Policy must state when control transfers."\n'
    'IFRS 15,IFRS 15 Revenue,R2,Disaggregation of revenue,table_structure,'
    '"Table must break revenue into categories, not only a total."\n'
    'IAS 24,IAS 24 Related party,R3,Outstanding balances incl. terms,both,'
    '"Requires a balances table AND narrative terms."\n')


def fixture(name: str, body: str) -> Path:
    path = Path(tempfile.gettempdir()) / name
    path.write_text(body, encoding="utf-8", newline="")
    return path


class TestExtract(unittest.TestCase):
    def test_source_column_names_are_mapped_to_bronze_names(self):
        # The CSV says standard_name / req_id; the table has always called
        # them standard / req and is live in BigQuery under those names.
        rows, _ = extract(fixture("rub_good.csv", GOOD), CFG, [])
        self.assertEqual(rows[0]["standard"], "IFRS 15 Revenue")
        self.assertEqual(rows[0]["req"], "R1")

    def test_new_columns_land(self):
        rows, _ = extract(fixture("rub_good.csv", GOOD), CFG, [])
        self.assertEqual(rows[0]["standard_code"], "IFRS 15")
        self.assertEqual(rows[1]["evidence_type"], "table_structure")
        self.assertTrue(rows[1]["check_guidance"].startswith("Table must break"))

    def test_answer_key_columns_never_land(self):
        # 'Compliant version' / 'Gap version' / 'Gap detail' are demo answers,
        # not reference data. They are absent from ALIASES, so even if a
        # source file carries them they must not appear.
        with_extra = (
            "standard_code,standard_name,req_id,requirement,evidence_type,"
            "check_guidance,Compliant version,Gap version\n"
            "IFRS 15,IFRS 15 Revenue,R1,Policy,narrative,Guidance,Met,Missing\n")
        rows, _ = extract(fixture("rub_extra.csv", with_extra), CFG, [])
        self.assertEqual(set(rows[0]), set(COLUMNS))
        self.assertNotIn("Compliant version", rows[0])

    def test_source_file_populated(self):
        path = fixture("rub_good.csv", GOOD)
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_unexpected_evidence_type_refuses_to_land(self):
        # A value the agent has no branch for is worse than a missing one.
        bad = HEADER + ('IFRS 15,IFRS 15 Revenue,R1,Policy,diagram,Guidance\n')
        with self.assertRaises(IngestError) as ctx:
            extract(fixture("rub_evtype.csv", bad), CFG, [])
        self.assertIn("evidence_type", str(ctx.exception))

    def test_duplicate_key_refuses_to_land(self):
        dupe = GOOD + ('IFRS 15,IFRS 15 Revenue,R1,Duplicate row,narrative,G\n')
        with self.assertRaises(IngestError) as ctx:
            extract(fixture("rub_dupe.csv", dupe), CFG, [])
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_missing_standard_code_raises(self):
        bad = ("standard_name,req_id,requirement\n"
               "IFRS 15 Revenue,R1,Policy\n")
        with self.assertRaises(IngestError):
            extract(fixture("rub_nocode.csv", bad), CFG, [])


class TestSchema(unittest.TestCase):
    def test_columns_include_source_file(self):
        self.assertEqual(COLUMNS[-1], "source_file")


if __name__ == "__main__":
    unittest.main()
