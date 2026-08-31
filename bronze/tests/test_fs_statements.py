"""fs_statements — the FS pair's structural contract.

The point of most of these is the INVERSION: this extractor must NOT refuse
to land on an arithmetic break, unlike every other module in the package. A
test that let a footing check creep in here would look like an improvement
and would break the deliverable, so the seeded defects are asserted to LAND.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bronze_ingest import fs_statements
from bronze_ingest.excel import IngestError

HEADER = ("doc_version,statement,section,line_order,line_item,note_ref,"
          "line_role,column_label,amount,amount_unit")

# A minimal but legal two-statement file: line_order runs 1..n per statement.
ROWS = [
    "clean,income_statement,Income statement,1,Revenue,10,item,Q1 2026,433101,SAR million",
    "clean,income_statement,Income statement,2,Purchases,,item,Q1 2026,-111692,SAR million",
    "clean,balance_sheet,Current assets,1,Inventories,,item,31 Mar 2026,93814,SAR million",
    "clean,balance_sheet,Current assets,2,Total current assets,,subtotal,31 Mar 2026,93814,SAR million",
]
STATEMENTS = ["note_05_ppe", "note_07_tax", "note_09_borrowings",
              "note_10_revenue"]
# One filler row per remaining statement, so the statement-set check passes.
FILLER = [f"clean,{s},Note,1,Something,,item,SAR million,1,SAR million"
          for s in STATEMENTS]


def write(lines, tmpdir, name="fs.csv") -> Path:
    path = Path(tmpdir) / name
    path.write_text("\n".join([HEADER, *lines]) + "\n", encoding="utf-8")
    return path


class FsStatementsTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.cfg = {"doc_version": "clean"}

    def tearDown(self):
        self._tmp.cleanup()

    def extract(self, lines, **cfg):
        path = write(lines, self.tmp)
        return fs_statements.extract(path, {**self.cfg, **cfg}, [])

    # -- the inversion ----------------------------------------------------
    def test_lands_a_subtotal_that_does_not_foot(self):
        """THE CENTRAL CONTRACT. 683180 against components footing to 682760
        is fs_seeded's planted defect and it MUST land."""
        broken = [
            "clean,balance_sheet,Current assets,1,Inventories,,item,31 Mar 2026,682760,SAR million",
            "clean,balance_sheet,Current assets,2,Total current assets,,subtotal,31 Mar 2026,683180,SAR million",
        ]
        rows, _ = self.extract([*ROWS[:2], *broken, *FILLER])
        amounts = [r["amount"] for r in rows]
        self.assertIn("683180", amounts)
        self.assertIn("682760", amounts)

    def test_lands_a_wrong_note_reference(self):
        """The text-only defect: note 5 cited where the note is 9. It carries
        no arithmetic signal, so only preserving it verbatim makes it findable."""
        wrong = ("clean,balance_sheet,Non-current liabilities,3,Borrowings,5,"
                 "item,31 Mar 2026,323341,SAR million")
        rows, _ = self.extract([*ROWS, wrong, *FILLER])
        borrowings = [r for r in rows if r["line_item"] == "Borrowings"]
        self.assertEqual(borrowings[0]["note_ref"], "5")

    # -- values are mirrored, not interpreted -------------------------------
    def test_negative_amounts_are_preserved_as_written(self):
        rows, _ = self.extract([*ROWS, *FILLER])
        purchases = next(r for r in rows if r["line_item"] == "Purchases")
        self.assertEqual(purchases["amount"], "-111692")

    def test_absent_note_ref_becomes_the_null_marker(self):
        """Empty -> \\N so it lands as a real NULL. Bronze holds no other
        NULLs; if this regressed to '' the column would group and sort with
        real references."""
        rows, _ = self.extract([*ROWS, *FILLER])
        self.assertEqual(rows[0]["note_ref"], "10")          # present, kept
        self.assertEqual(rows[1]["note_ref"], fs_statements.NULL)

    def test_no_source_file_column(self):
        """Per the Ingestion Notes — unlike every other table here."""
        rows, _ = self.extract([*ROWS, *FILLER])
        self.assertNotIn("source_file", rows[0])
        self.assertEqual(list(rows[0]), fs_statements.COLUMNS)

    # -- structural failures DO stop the load -------------------------------
    def test_wrong_document_is_refused(self):
        """fs_clean's config pointed at the seeded file. Both are 142 rows of
        the same shape, so nothing else would notice."""
        seeded = [r.replace("clean,", "seeded,", 1) for r in ROWS]
        with self.assertRaises(IngestError) as ctx:
            self.extract([*seeded, *FILLER])
        self.assertIn("doc_version", str(ctx.exception))

    def test_missing_statement_is_refused(self):
        with self.assertRaises(IngestError) as ctx:
            self.extract(ROWS)                    # no note statements
        self.assertIn("statement set mismatch", str(ctx.exception))

    def test_unknown_line_role_is_refused(self):
        bad = ROWS[0].replace(",item,", ",header,")
        with self.assertRaises(IngestError) as ctx:
            self.extract([bad, *ROWS[1:], *FILLER])
        self.assertIn("line_role", str(ctx.exception))

    def test_duplicate_natural_key_is_refused(self):
        with self.assertRaises(IngestError) as ctx:
            self.extract([*ROWS, ROWS[0], *FILLER])
        self.assertIn("natural key", str(ctx.exception))

    def test_line_order_gap_is_refused(self):
        gapped = ROWS[1].replace(",2,Purchases", ",7,Purchases")
        with self.assertRaises(IngestError) as ctx:
            self.extract([ROWS[0], gapped, *ROWS[2:], *FILLER])
        self.assertIn("line_order", str(ctx.exception))

    def test_unparseable_amount_is_refused(self):
        """A reading failure, not an arithmetic one — amount is NUMERIC in
        the DDL, so this would otherwise be a load-job error with a byte
        offset instead of a named row."""
        bad = ROWS[0].replace(",433101,", ",4o3101,")
        with self.assertRaises(IngestError) as ctx:
            self.extract([bad, *ROWS[1:], *FILLER])
        self.assertIn("NUMERIC", str(ctx.exception))

    def test_blank_not_null_column_is_refused(self):
        bad = ROWS[0].replace(",Revenue,", ",,")
        with self.assertRaises(IngestError) as ctx:
            self.extract([bad, *ROWS[1:], *FILLER])
        self.assertIn("line_item", str(ctx.exception))

    def test_missing_column_is_refused(self):
        """flatcsv's contract: a silently absent column would land a whole
        column of empty strings that looks like real 'no value' data."""
        path = Path(self.tmp) / "short.csv"
        path.write_text("doc_version,statement\nclean,income_statement\n",
                        encoding="utf-8")
        with self.assertRaises(IngestError):
            fs_statements.extract(path, self.cfg, [])

    def test_expected_rows_mismatch_is_refused(self):
        with self.assertRaises(IngestError) as ctx:
            self.extract([*ROWS, *FILLER], expected_rows=999)
        self.assertIn("999", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
