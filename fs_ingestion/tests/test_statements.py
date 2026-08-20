"""Tests for the FS extractor (statements.py).

Plain unittest, no pytest needed:  python -m unittest discover -s tests

Two layers. The unit tests below run anywhere. TestRealDocuments runs only
when data/ is populated — the source pack is gitignored — and is the one
that would catch a real regression, so it should not be allowed to skip
quietly on a machine that is supposed to have the files.
"""

from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fs_ingest import statements                              # noqa: E402
from fs_ingest.errors import IngestError                      # noqa: E402
from fs_ingest.schema import COLUMNS, NULL                    # noqa: E402

LABELS = ["Non-current assets", "Property, plant and equipment",
          "Total non-current assets", "Current assets", "Inventories",
          "Total current assets", "Total assets"]


class TestParseValue(unittest.TestCase):
    def test_plain_and_thousands(self):
        self.assertEqual(statements.parse_value("433,101"), Decimal("433101"))
        self.assertEqual(statements.parse_value("264"), Decimal("264"))

    def test_parentheses_are_negative(self):
        """Signed as presented — this is what lets every subtotal sum with a
        plain SUM and no per-section sign rules."""
        self.assertEqual(statements.parse_value("(41,087)"),
                         Decimal("-41087"))

    def test_no_figure_is_none(self):
        for text in ("", "   ", "—", "-", "–"):
            self.assertIsNone(statements.parse_value(text), repr(text))

    def test_non_numeric_is_none(self):
        """Note 10 prints 'SAR million' over its value column."""
        self.assertIsNone(statements.parse_value("SAR million"))

    def test_unicode_minus_and_nbsp(self):
        self.assertEqual(statements.parse_value("−1 234"),
                         Decimal("-1234"))

    def test_returns_decimal_not_float(self):
        """The DDL says NUMERIC; if Python used float here the zero-tolerance
        footing argument would be decoration."""
        self.assertIsInstance(statements.parse_value("1.05"), Decimal)

    def test_unparsed_number_is_detected(self):
        self.assertTrue(statements.is_unparsed_number("1,2 3x", set()))
        self.assertFalse(statements.is_unparsed_number("SAR million", set()))
        self.assertFalse(statements.is_unparsed_number("93,814", set()))
        self.assertFalse(statements.is_unparsed_number("", set()))


class TestHierarchy(unittest.TestCase):
    def test_ranges_and_top_level(self):
        spec = {
            "sections": [{"name": "Assets", "from": "Non-current assets",
                          "to": "Total current assets"}],
            "subsections": [{"name": "Current assets",
                             "from": "Current assets",
                             "to": "Total current assets"}],
            "top_level": ["Total assets"],
        }
        pairs = statements.build_hierarchy(LABELS, spec, "BS")
        self.assertEqual(pairs[0], ("Assets", None))
        self.assertEqual(pairs[3], ("Assets", "Current assets"))
        self.assertEqual(pairs[5], ("Assets", "Current assets"))
        # Total assets sums across both subsections, so it belongs to neither.
        self.assertEqual(pairs[6], (None, None))

    def test_missing_anchor_raises(self):
        spec = {"sections": [{"name": "X", "from": "Nope",
                              "to": "Total assets"}]}
        with self.assertRaises(IngestError):
            statements.build_hierarchy(LABELS, spec, "BS")

    def test_duplicate_anchor_raises(self):
        """'Other assets and receivables' really does appear twice on the
        balance sheet, so an ambiguous anchor is a live risk."""
        labels = ["Dup", "Middle", "Dup"]
        spec = {"sections": [{"name": "X", "from": "Dup", "to": "Middle"}]}
        with self.assertRaises(IngestError):
            statements.build_hierarchy(labels, spec, "BS")

    def test_reversed_range_raises(self):
        spec = {"sections": [{"name": "X", "from": "Total assets",
                              "to": "Non-current assets"}]}
        with self.assertRaises(IngestError):
            statements.build_hierarchy(LABELS, spec, "BS")

    def test_unknown_top_level_raises(self):
        with self.assertRaises(IngestError):
            statements.build_hierarchy(LABELS, {"top_level": ["Nope"]}, "BS")


class TestChecks(unittest.TestCase):
    def test_statement_set_mismatch_raises(self):
        cfg = {"statements": {"A": {}, "B": {}}}
        with self.assertRaises(IngestError):
            statements.check_statements(["A"], cfg, [])
        with self.assertRaises(IngestError):
            statements.check_statements(["A", "B", "C"], cfg, [])
        with self.assertRaises(IngestError):
            statements.check_statements(["B", "A"], cfg, [])

    def test_duplicate_natural_key_raises(self):
        row = {"statement": "BS", "row_ord": 1, "column_label": "Q1 2026"}
        with self.assertRaises(IngestError):
            statements.check_natural_key([row, dict(row)], [])

    def test_parse_failure_raises(self):
        """Without a value_text column, a dropped figure is invisible
        downstream — so it has to stop the load here."""
        with self.assertRaises(IngestError):
            statements.check_values_parsed([], ["'BS' line 3 ...: '1,2 3x'"],
                                           [])

    def test_missing_required_column_raises(self):
        rows = [{"statement": "BS", "label": "", "is_bold": "false",
                 "row_ord": 1}]
        with self.assertRaises(IngestError):
            statements.check_required(rows, [])


def _config(name: str) -> dict:
    cfg = json.loads((ROOT / "configs" / f"{name}.json").read_text("utf-8"))
    cfg["input_path"] = ROOT / cfg["input_path"]
    return cfg


@unittest.skipUnless((ROOT / "data" / "PoC_Group_FS_clean.docx").exists(),
                     "source pack not present (data/ is gitignored)")
class TestRealDocuments(unittest.TestCase):
    """End-to-end against the actual pack."""

    def setUp(self):
        self.clean, _ = statements.extract(
            _config("fs_clean")["input_path"], _config("fs_clean"), [])
        self.seeded, _ = statements.extract(
            _config("fs_seeded")["input_path"], _config("fs_seeded"), [])

    def find(self, rows, statement, label, column):
        return next(r for r in rows if r["statement"].startswith(statement)
                    and r["label"] == label and r["column_label"] == column)

    def test_row_counts(self):
        self.assertEqual(len(self.clean), 152)
        self.assertEqual(len(self.seeded), 152)

    def test_columns_and_null_marker(self):
        self.assertEqual(list(self.clean[0]), COLUMNS)
        # Only the five bold balance-sheet group headers have no figure,
        # once per period.
        blanks = [r for r in self.clean if r["value"] == NULL]
        self.assertEqual(len(blanks), 10)
        self.assertTrue(all(r["is_bold"] == "true" for r in blanks))

    def test_notes_are_flat(self):
        for row in self.clean:
            if row["statement"].startswith("Note "):
                self.assertEqual(row["section"], NULL)
                self.assertEqual(row["subsection"], NULL)

    def test_derived_hierarchy_matches_the_ingestion_notes(self):
        row = self.find(self.clean, "Consolidated balance sheet",
                        "Inventories", "31 Mar 2026")
        self.assertEqual((row["section"], row["subsection"]),
                         ("Assets", "Current assets"))
        # A group header carries its own subsection, so filtering on the
        # subsection returns the group intact, header included.
        header = self.find(self.clean, "Consolidated balance sheet",
                           "Current assets", "31 Mar 2026")
        self.assertEqual(header["subsection"], "Current assets")
        self.assertEqual(header["value"], NULL)
        self.assertEqual(header["is_bold"], "true")
        # Total assets sums across both subsections, so it leaves both.
        total = self.find(self.clean, "Consolidated balance sheet",
                          "Total assets", "31 Mar 2026")
        self.assertEqual((total["section"], total["subsection"]),
                         (NULL, NULL))

    def test_notes_inherit_the_period_they_support(self):
        """The page prints 'SAR million'; the config supplies the period."""
        row = self.find(self.clean, "Note 10", "External revenue", "Q1 2026")
        self.assertEqual(row["value"], "433101")

    def test_note_9_columns_are_a_breakdown_not_a_period(self):
        for column in ("Non-current", "Current", "Total"):
            self.find(self.clean, "Note 9", "Total borrowings", column)

    def test_signs_are_as_presented(self):
        row = self.find(self.clean, "Consolidated statement of income",
                        "Purchases", "Q1 2026")
        self.assertEqual(Decimal(row["value"]), Decimal("-111692"))

    def test_row_ord_repeats_once_per_period(self):
        both = [r for r in self.clean
                if r["statement"] == "Consolidated balance sheet"
                and r["label"] == "Inventories"]
        self.assertEqual({r["row_ord"] for r in both}, {11})
        self.assertEqual({r["column_label"] for r in both},
                         {"31 Mar 2026", "31 Dec 2025"})

    # --- the seeded defects must survive ingestion intact ------------------
    def test_seeded_subtotal_breaks_are_preserved(self):
        """Ingest must NOT repair or refuse these — they are the deliverable."""
        components = sum(
            Decimal(r["value"]) for r in self.seeded
            if r["statement"] == "Consolidated balance sheet"
            and r["column_label"] == "31 Mar 2026" and 11 <= r["row_ord"] <= 18)
        printed = Decimal(self.find(self.seeded, "Consolidated balance sheet",
                                    "Total current assets",
                                    "31 Mar 2026")["value"])
        self.assertEqual(printed, Decimal("683180"))
        self.assertEqual(components, Decimal("682760"))
        self.assertEqual(printed - components, Decimal("420"))

    def test_seeded_note_10_break_is_preserved(self):
        components = sum(Decimal(r["value"]) for r in self.seeded
                         if r["statement"].startswith("Note 10")
                         and 1 <= r["row_ord"] <= 3)
        printed = Decimal(self.find(self.seeded, "Note 10",
                                    "Revenue from contracts with customers",
                                    "Q1 2026")["value"])
        self.assertEqual(printed - components, Decimal("3"))

    def test_seeded_note_reference_survives_verbatim(self):
        """The defect with NO arithmetic signal. It is findable only because
        label keeps the parenthetical exactly as printed."""
        row = self.find(self.seeded, "Consolidated balance sheet",
                        "Borrowings (Note 5)", "31 Mar 2026")
        self.assertEqual(row["subsection"], "Non-current liabilities")
        # The clean document says Note 9 in the same position.
        self.find(self.clean, "Consolidated balance sheet",
                  "Borrowings (Note 9)", "31 Mar 2026")

    def test_clean_document_actually_foots(self):
        """Not an ingest check — a fixture check. If the clean document stops
        footing, the control is no longer a control."""
        components = sum(
            Decimal(r["value"]) for r in self.clean
            if r["statement"] == "Consolidated balance sheet"
            and r["column_label"] == "31 Mar 2026" and 11 <= r["row_ord"] <= 18)
        printed = Decimal(self.find(self.clean, "Consolidated balance sheet",
                                    "Total current assets",
                                    "31 Mar 2026")["value"])
        self.assertEqual(printed, components)


if __name__ == "__main__":
    unittest.main()
