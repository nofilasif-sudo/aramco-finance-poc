"""Tests for scripts/build_silver.py's pure helpers.

Only retarget() is unit-testable without live BigQuery access — everything
else in build_silver.py makes network calls. Run with:
    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_silver import retarget, TABLES  # noqa: E402


class TestRetarget(unittest.TestCase):
    def test_default_datasets_are_a_no_op(self):
        sql = "SELECT * FROM `aramco-finance-poc-c2a4.bronze.bronze_coa_raw`"
        self.assertEqual(retarget(sql, "bronze", "silver"), sql)

    def test_rewrites_bronze_qualifier(self):
        sql = "FROM `aramco-finance-poc-c2a4.bronze.bronze_coa_raw`"
        out = retarget(sql, "bronze_staging", "silver")
        self.assertIn("aramco-finance-poc-c2a4.bronze_staging.bronze_coa_raw", out)
        self.assertNotIn(".bronze.", out)

    def test_rewrites_silver_qualifier(self):
        sql = "MERGE `aramco-finance-poc-c2a4.silver.dim_entity` AS tgt"
        out = retarget(sql, "bronze", "silver_staging")
        self.assertIn("aramco-finance-poc-c2a4.silver_staging.dim_entity", out)
        self.assertNotIn(".silver.", out)

    def test_rewrites_both_independently(self):
        sql = ("SELECT b.x FROM `p.bronze.bronze_coa_raw` b "
               "JOIN `p.silver.dim_account` d ON TRUE")
        out = retarget(sql, "bronze_staging", "silver_staging")
        self.assertIn("p.bronze_staging.bronze_coa_raw", out)
        self.assertIn("p.silver_staging.dim_account", out)

    def test_does_not_touch_unrelated_text(self):
        # "bronze contract" has no ".bronze." substring (no leading dot), so
        # prose mentioning bronze/silver must survive untouched.
        sql = "-- All columns STRING per the bronze contract, in the silver layer"
        self.assertEqual(retarget(sql, "bronze_staging", "silver_staging"), sql)


class TestTablesList(unittest.TestCase):
    def test_fact_trial_balance_excluded(self):
        # fact_trial_balance is sourced from bronze_tb_raw, owned by another
        # developer's trial_balance/ pipeline — must never be (re)added here.
        self.assertNotIn("fact_trial_balance", TABLES)

    def test_dim_entity_and_dim_period_excluded(self):
        # Both are owned/populated by trial_balance/'s pipeline. This
        # package only reads them (fact_group_trial_balance's MERGE joins
        # to both) — re-adding them here would resume the silent
        # double-MERGE (different data from each pipeline) this list was
        # split out to stop. See silver_build.sql's header comment.
        self.assertNotIn("dim_entity", TABLES)
        self.assertNotIn("dim_period", TABLES)

    def test_fact_group_trial_balance_included(self):
        # The Group (Aramco parent-only) trial balance IS owned by this
        # package, and is easy to confuse with fact_trial_balance by name.
        self.assertIn("fact_group_trial_balance", TABLES)


if __name__ == "__main__":
    unittest.main()
