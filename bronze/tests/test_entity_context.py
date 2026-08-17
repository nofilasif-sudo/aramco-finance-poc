"""Tests for the entity-context ingestion (bronze_entity_context_raw).

Plain unittest, no pytest needed:  python -m unittest discover -s tests
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bronze_ingest.entity_context import COLUMNS, extract
from bronze_ingest.excel import IngestError

CFG = {"csv_read_encoding": "utf-8-sig", "csv_encoding": "utf-8"}


def fixture(name: str, body: str, bom: bool = False) -> Path:
    path = Path(tempfile.gettempdir()) / name
    path.write_text(body, encoding="utf-8-sig" if bom else "utf-8", newline="")
    return path


GOOD = ("context_key,context_value\n"
        "reporting_entity,Saudi Aramco consolidated group\n"
        "presentation_currency,\"Saudi Riyal (SAR), primary; USD referenced\"\n"
        "reportable_segments,Upstream; Downstream; Corporate\n")


class TestExtract(unittest.TestCase):
    def test_key_value_pairs_land(self):
        rows, _ = extract(fixture("ec_good.csv", GOOD), CFG, [])
        got = {r["context_key"]: r["context_value"] for r in rows}
        self.assertEqual(got["reporting_entity"],
                         "Saudi Aramco consolidated group")

    def test_commas_inside_quoted_values_survive(self):
        # presentation_currency contains a comma; naive splitting would
        # truncate it at "Saudi Riyal (SAR)".
        rows, _ = extract(fixture("ec_good.csv", GOOD), CFG, [])
        got = {r["context_key"]: r["context_value"] for r in rows}
        self.assertEqual(got["presentation_currency"],
                         "Saudi Riyal (SAR), primary; USD referenced")

    def test_utf8_bom_does_not_break_the_first_column(self):
        # Excel exports a BOM. Read as plain utf-8 it becomes part of the
        # first header name and 'context_key' silently fails to resolve.
        rows, _ = extract(fixture("ec_bom.csv", GOOD, bom=True), CFG, [])
        self.assertEqual(len(rows), 3)

    def test_source_file_populated(self):
        path = fixture("ec_good.csv", GOOD)
        rows, _ = extract(path, CFG, [])
        self.assertTrue(all(r["source_file"] == path.name for r in rows))

    def test_duplicate_context_key_refuses_to_land(self):
        dupe = GOOD + "reporting_entity,Something Else\n"
        with self.assertRaises(IngestError) as ctx:
            extract(fixture("ec_dupe.csv", dupe), CFG, [])
        self.assertIn("duplicate context_key", str(ctx.exception))

    def test_missing_required_column_raises(self):
        with self.assertRaises(IngestError):
            extract(fixture("ec_bad.csv", "key_name,value\na,b\n"), CFG, [])

    def test_empty_file_raises(self):
        with self.assertRaises(IngestError):
            extract(fixture("ec_empty.csv", ""), CFG, [])

    def test_header_only_file_raises(self):
        with self.assertRaises(IngestError):
            extract(fixture("ec_hdr.csv", "context_key,context_value\n"), CFG, [])


class TestSchema(unittest.TestCase):
    def test_columns_include_source_file(self):
        self.assertEqual(COLUMNS[-1], "source_file")


if __name__ == "__main__":
    unittest.main()
