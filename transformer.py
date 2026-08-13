from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from helpers import find_latest_gcs_blob, load_dataframe, normalize_text, read_excel_from_gcs, to_string

logger = logging.getLogger(__name__)


class ExcelTransformer:
    def __init__(self, config: dict[str, Any], storage_client, bq_client):
        self.config = config
        self.storage_client = storage_client
        self.bq_client = bq_client

    def normalize_sheet_name(self, name: str) -> str:
        return normalize_text(name)

    def select_relevant_sheets(self, sheet_names: list[str]) -> list[str]:
        keywords = [k.strip().lower() for k in self.config.get("sheet_keywords", []) if k.strip()]
        return [name for name in sheet_names if any(k in self.normalize_sheet_name(name) for k in keywords)]

    def detect_header_row(self, df: pd.DataFrame, start_row: int = 0) -> int:
        max_scan = min(len(df), start_row + 20)
        for index in range(start_row, max_scan):
            values = [normalize_text(v) for v in df.iloc[index].fillna("").tolist()]
            if any(v in {"account", "account name", "type", "category"} for v in values):
                return index
        return start_row

    def build_period_column_map(self, header: pd.Series) -> dict[str, int]:
        """Map period_label -> column index, computed once per sheet.

        Previously this scan ran fresh for every data row (O(rows x periods x
        header width)) instead of once per sheet — the biggest perf fix here.
        """
        start_col = int(self.config.get("period_start_column", 4))
        skip = {"account", "account name", "type", "category"}
        mapping: dict[str, int] = {}
        for idx, raw in enumerate(header.tolist()):
            if idx < start_col:
                continue
            value = to_string(raw).strip()
            if not value or value.lower() in skip or value.lower().startswith("account"):
                continue
            mapping[value] = idx
        return mapping

    def resolve_affiliate_code(self, sheet_name: str) -> str | None:
        normalized_sheet = self.normalize_sheet_name(sheet_name)
        for name, code in self.config.get("affiliate_mapping", {}).items():
            if self.normalize_sheet_name(name) in normalized_sheet:
                return code
        return None

    def transform_sheet(self, df: pd.DataFrame, sheet_name: str) -> list[dict[str, Any]]:
        start_row = int(self.config.get("start_row", 0))
        header_row = self.detect_header_row(df, start_row=start_row)
        header = df.iloc[header_row].fillna("")

        # Computed once per sheet, not once per row (that was the bug).
        period_column_map = self.build_period_column_map(header)
        affiliate_code = self.resolve_affiliate_code(sheet_name)

        account_col = int(self.config.get("account_column", 0))
        name_col = int(self.config.get("account_name_column", 1))
        type_col = int(self.config.get("type_column", 2))
        category_col = int(self.config.get("category_column", 3))

        rows: list[dict[str, Any]] = []
        for idx in range(header_row + 1, len(df)):
            row = df.iloc[idx]
            account_value = row.iloc[account_col] if account_col < len(row) else None
            if pd.isna(account_value) or not str(account_value).strip():
                continue

            account_text = str(account_value)
            if not account_text.isdigit():
                continue  # section header row, e.g. "NON-CURRENT ASSETS" — not a data row

            account_name = to_string(row.iloc[name_col]) if name_col < len(row) else ""
            type_value = to_string(row.iloc[type_col]) if type_col < len(row) else ""
            category_value = to_string(row.iloc[category_col]) if category_col < len(row) else ""

            for period_label, col_index in period_column_map.items():
                rows.append(
                    {
                        "affiliate_code": affiliate_code,
                        "account": account_text,
                        "account_name": account_name,
                        "type": type_value,
                        "category": category_value,
                        "period_label": period_label,
                        "amount": to_string(row.iloc[col_index]),
                    }
                )

        return rows

    def build_trial_balance(self, workbook: pd.ExcelFile) -> pd.DataFrame:
        relevant_sheets = self.select_relevant_sheets(workbook.sheet_names)
        logger.info(
            "%d of %d sheet(s) matched sheet_keywords: %s",
            len(relevant_sheets), len(workbook.sheet_names), relevant_sheets,
        )

        all_rows: list[dict[str, Any]] = []
        for sheet_name in relevant_sheets:
            df = workbook.parse(sheet_name, header=None)
            sheet_rows = self.transform_sheet(df, sheet_name)
            logger.info("Sheet '%s': %d row(s)", sheet_name, len(sheet_rows))
            all_rows.extend(sheet_rows)

        return pd.DataFrame(all_rows)

    def build_dim_affiliate(self) -> pd.DataFrame:
        mapping = self.config.get("affiliate_mapping", {})
        return pd.DataFrame([{"affiliate_code": code, "affiliate_name": name} for name, code in mapping.items()])

    def run(self) -> dict[str, int]:
        logger.info("=== Pipeline run started ===")

        input_uri = self.config.get("input_path") or find_latest_gcs_blob(
            self.storage_client, self.config["input_prefix"], self.config.get("input_file_pattern", "*")
        )
        logger.info("Input file: %s", input_uri)

        workbook = read_excel_from_gcs(self.storage_client, input_uri)
        trial_balance_df = self.build_trial_balance(workbook)
        dim_affiliate_df = self.build_dim_affiliate()

        write_disposition = self.config.get("write_disposition", "WRITE_TRUNCATE")
        bq_targets = self.config["bq_targets"]

        results = {
            "trial_balance": load_dataframe(
                self.bq_client, trial_balance_df, bq_targets["trial_balance"], write_disposition
            ),
            "dim_affiliate": load_dataframe(
                self.bq_client, dim_affiliate_df, bq_targets["dim_affiliate"], write_disposition
            ),
        }

        logger.info("=== Pipeline run complete: %s ===", results)
        return results