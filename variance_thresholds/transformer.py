from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from helpers import normalize_text, to_string

logger = logging.getLogger(__name__)


class VarianceThresholdsTransformer:
    """Bronze-layer ingest for PoC_Variance_Thresholds.xlsx.

    Reads 4 logical tables out of the workbook (per-entity thresholds, unioned
    across whichever sheets match the configured entity keywords; handling
    rules; ratio checks; silent-account rules) and writes them either to local
    CSV (output_mode="csv", for offline validation) or BigQuery bronze tables
    (output_mode="bigquery"). All 4 tables are built before anything is
    written — see build_tables() for why.
    """

    def __init__(self, config: dict[str, Any], storage_client, bq_client):
        self.config = config
        self.storage_client = storage_client
        self.bq_client = bq_client

    def detect_table_header(
        self, df: pd.DataFrame, expected_columns: set[str], start_row: int = 0, max_scan: int = 40
    ) -> int:
        """Scan for the row whose normalized values are a superset of
        expected_columns, rather than assuming a fixed row number — a header
        that shifts (e.g. someone inserts a row above the table) is still
        found correctly. Raises if not found within max_scan rows: a
        wrong/missing header must stop the pipeline, not silently misparse
        every row beneath it.
        """
        max_row = min(len(df), start_row + max_scan)
        for idx in range(start_row, max_row):
            values = {normalize_text(v) for v in df.iloc[idx].tolist()}
            if expected_columns.issubset(values):
                return idx
        raise ValueError(
            f"Header row matching {sorted(expected_columns)} not found within rows {start_row}-{max_row}"
        )

    def read_table(self, df: pd.DataFrame, header_row: int, column_map: dict[str, str]) -> list[dict[str, Any]]:
        """Read rows below header_row until the first fully blank row — the
        table's natural end, not a hardcoded row count. This is what makes
        the pipeline tolerate the source file growing or shrinking between
        runs without any code change.
        """
        header = df.iloc[header_row]
        col_field_by_index = {
            idx: column_map[normalize_text(raw)]
            for idx, raw in enumerate(header.tolist())
            if normalize_text(raw) in column_map
        }

        rows: list[dict[str, Any]] = []
        for idx in range(header_row + 1, len(df)):
            row = df.iloc[idx]
            if row.isna().all() or all(to_string(v).strip() == "" for v in row.tolist()):
                break

            record = {field: to_string(row.iloc[col_idx]) for col_idx, field in col_field_by_index.items()}
            rows.append(record)

        return rows

    def select_threshold_sheets(self, sheet_names: list[str]) -> list[tuple[str, str]]:
        """Match threshold sheets by keyword substring (e.g. "sabic" in
        "Thresholds - SABIC") instead of an exact sheet-name list, so a sheet
        can be renamed/reordered/have suffixes added without breaking
        ingestion. A sheet whose name contains the threshold_sheet_marker
        ("threshold") but matches none of the configured entity keywords is
        logged and skipped rather than silently dropped — that's the signal
        a new entity was added to the file without updating config.
        """
        keywords: dict[str, str] = self.config["threshold_sheet_keywords"]
        marker = self.config["threshold_sheet_marker"]

        matches: list[tuple[str, str]] = []
        for name in sheet_names:
            normalized = normalize_text(name)
            entity_scope = next((scope for keyword, scope in keywords.items() if keyword in normalized), None)
            if entity_scope:
                matches.append((name, entity_scope))
            elif marker in normalized:
                logger.warning(
                    "Sheet '%s' contains '%s' but matched none of the configured entity keywords %s — skipping",
                    name, marker, sorted(keywords),
                )
        return matches

    def build_threshold_table(self, workbook: pd.ExcelFile) -> pd.DataFrame:
        column_map = self.config["threshold_column_map"]
        expected_columns = set(column_map.keys())
        allowed_entity_scopes = set(self.config["threshold_sheet_keywords"].values())

        threshold_sheets = self.select_threshold_sheets(workbook.sheet_names)

        all_rows: list[dict[str, Any]] = []
        for sheet_name, entity_scope in threshold_sheets:
            df = workbook.parse(sheet_name, header=None)
            header_row = self.detect_table_header(df, expected_columns)
            rows = self.read_table(df, header_row, column_map)
            if not rows:
                raise ValueError(f"'{sheet_name}': header found but 0 data rows extracted")
            for row in rows:
                row["entity_scope"] = entity_scope
            logger.info("Sheet '%s': %d threshold row(s) (entity_scope=%s)", sheet_name, len(rows), entity_scope)
            all_rows.extend(rows)

        columns = [
            "entity_scope", "account", "account_name", "category", "archetype", "seasonal",
            "primary_basis", "qoq_low", "qoq_med", "qoq_high", "yoy_low", "yoy_med", "yoy_high",
            "abs_floor", "large_dollar_override", "notes",
        ]
        df = pd.DataFrame(all_rows, columns=columns)

        # Every declared entity keyword must have produced at least one row,
        # and no row may carry a scope outside the declared set — catches a
        # keyword/sheet mismatch (e.g. a sheet renamed so it no longer
        # matches any keyword) that would otherwise silently drop an entity.
        actual_scopes = set(df["entity_scope"].unique())
        unexpected = actual_scopes - allowed_entity_scopes
        if unexpected:
            raise ValueError(f"Unexpected entity_scope value(s): {unexpected}")
        missing = allowed_entity_scopes - actual_scopes
        if missing:
            raise ValueError(f"No sheet matched configured entity_scope(s): {missing}")

        # These columns are threshold bands/monetary values, not identifiers —
        # cast to numeric so downstream consumers can compare/aggregate them
        # directly instead of parsing strings. errors="raise": a genuinely
        # non-numeric value here means the source data is wrong, not that the
        # column should silently become NULL.
        for col in self.config.get("threshold_numeric_columns", []):
            df[col] = pd.to_numeric(df[col].replace("", pd.NA), errors="raise").astype("float64")

        return df

    def build_simple_table(self, workbook: pd.ExcelFile, sheet_name: str, column_map: dict[str, str]) -> pd.DataFrame:
        expected_columns = set(column_map.keys())
        df = workbook.parse(sheet_name, header=None)
        header_row = self.detect_table_header(df, expected_columns)
        rows = self.read_table(df, header_row, column_map)
        if not rows:
            raise ValueError(f"'{sheet_name}': header found but 0 data rows extracted")
        logger.info("Sheet '%s': %d row(s)", sheet_name, len(rows))

        columns = list(dict.fromkeys(column_map.values()))
        return pd.DataFrame(rows, columns=columns)

    def build_tables(self, workbook: pd.ExcelFile) -> dict[str, pd.DataFrame]:
        """Build both DataFrames before returning. If either fails (e.g. a
        header can't be found), the exception propagates immediately and
        neither is written anywhere — these tables are consumed together by
        the same downstream agent, so a partial refresh (1 fresh + 1 stale)
        would be a worse failure mode than no refresh at all this run.
        """
        return {
            "threshold": self.build_threshold_table(workbook),
            "silent_account_rule": self.build_simple_table(
                workbook, self.config["silent_account_rule_sheet"], self.config["silent_account_rule_column_map"]
            ),
        }

    def run(self) -> dict[str, int]:
        from helpers import find_latest_gcs_blob, read_excel_from_gcs

        logger.info("=== Pipeline run started ===")

        input_uri = self.config.get("input_path") or find_latest_gcs_blob(
            self.storage_client, self.config["input_prefix"], self.config.get("input_file_pattern", "*")
        )
        logger.info("Input file: %s", input_uri)

        workbook = read_excel_from_gcs(self.storage_client, input_uri)
        tables = self.build_tables(workbook)

        output_mode = self.config.get("output_mode", "bigquery")
        results = self._write_csv(tables) if output_mode == "csv" else self._load_bigquery(tables)

        logger.info("=== Pipeline run complete: %s ===", results)
        return results

    def _write_csv(self, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
        output_dir = Path(self.config.get("output_dir", "outputs"))
        output_dir.mkdir(parents=True, exist_ok=True)

        results = {}
        for table_key, df in tables.items():
            path = output_dir / f"{table_key}.csv"
            df.to_csv(path, index=False)
            logger.info("Wrote %d rows to %s", len(df), path)
            results[table_key] = len(df)
        return results

    def _load_bigquery(self, tables: dict[str, pd.DataFrame]) -> dict[str, int]:
        from helpers import load_dataframe

        write_disposition = self.config.get("write_disposition", "WRITE_TRUNCATE")
        bq_targets = self.config["bq_targets"]

        return {
            table_key: load_dataframe(self.bq_client, df, bq_targets[table_key], write_disposition)
            for table_key, df in tables.items()
        }
