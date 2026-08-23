from __future__ import annotations

import fnmatch
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
from google.cloud import bigquery, storage

logger = logging.getLogger(__name__)


def configure_logging(level: str = "INFO") -> None:
    """Call once, at startup. Every module's logger inherits this config."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def normalize_text(value: Any) -> str:
    return str(value).strip().lower() if value is not None and str(value).strip() != "" else ""


def to_string(value: Any) -> str:
    return "" if pd.isna(value) else str(value)


def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    parsed = urlparse(gcs_uri)
    return parsed.netloc, parsed.path.lstrip("/")


def find_latest_gcs_blob(storage_client: storage.Client, prefix_uri: str, pattern: str = "*") -> str:
    """Return the gs:// URI of the most recently updated file under a GCS
    prefix matching a wildcard pattern (e.g. '*.xlsx'). Avoids hardcoding a
    filename — new files are picked up automatically."""
    bucket_name, prefix_path = _parse_gcs_uri(prefix_uri)
    blobs = [
        b for b in storage_client.list_blobs(bucket_name, prefix=prefix_path)
        if fnmatch.fnmatch(Path(b.name).name, pattern)
    ]

    if not blobs:
        raise FileNotFoundError(f"No files matching '{pattern}' found under {prefix_uri}")

    blobs.sort(key=lambda b: b.updated, reverse=True)
    latest = blobs[0]
    if len(blobs) > 1:
        logger.info(
            "Found %d matches under %s (pattern='%s'); using latest: %s (updated %s)",
            len(blobs), prefix_uri, pattern, latest.name, latest.updated,
        )
    return f"gs://{bucket_name}/{latest.name}"


def read_excel_from_gcs(storage_client: storage.Client, gcs_uri: str) -> pd.ExcelFile:
    """Read an Excel workbook straight from GCS into memory.

    No local disk write needed: xlsx is a zip archive and needs a seekable
    file, which BytesIO provides directly.
    """
    bucket_name, blob_path = _parse_gcs_uri(gcs_uri)
    blob = storage_client.bucket(bucket_name).blob(blob_path)

    if not blob.exists():
        raise FileNotFoundError(f"{gcs_uri} not found")

    logger.info("Reading %s into memory ...", gcs_uri)
    start = time.perf_counter()
    buffer = BytesIO(blob.download_as_bytes())
    workbook = pd.ExcelFile(buffer)
    logger.info(
        "Loaded %s (%.1f KB) in %.2fs", gcs_uri, len(buffer.getvalue()) / 1024, time.perf_counter() - start
    )
    return workbook


def get_table_schema(bq_client: bigquery.Client, table_id: str) -> list[Any]:
    """Fetch the live schema from BQ so loads always match what the table
    actually looks like right now, instead of a hardcoded assumption."""
    schema = bq_client.get_table(table_id).schema
    logger.info("Fetched schema for %s: %s", table_id, [f.name for f in schema])
    return schema


def validate_columns(df: pd.DataFrame, schema: list[Any], table_id: str) -> None:
    """Fail with a clear message before the load, not a cryptic one during it."""
    schema_cols = {f.name for f in schema}
    df_cols = set(df.columns)

    missing = schema_cols - df_cols
    extra = df_cols - schema_cols

    if missing:
        raise ValueError(f"{table_id}: dataframe is missing columns {sorted(missing)}")
    if extra:
        raise ValueError(f"{table_id}: dataframe has unexpected columns {sorted(extra)}")


def load_dataframe(
    bq_client: bigquery.Client,
    df: pd.DataFrame,
    table_id: str,
    write_disposition: str = "WRITE_TRUNCATE",
) -> int:
    schema = get_table_schema(bq_client, table_id)
    validate_columns(df, schema, table_id)

    logger.info("Loading %d rows into %s (write_disposition=%s) ...", len(df), table_id, write_disposition)
    start = time.perf_counter()

    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition=write_disposition)
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()

    logger.info("Loaded %d rows into %s in %.2fs", job.output_rows, table_id, time.perf_counter() - start)
    return job.output_rows


def run_sql_file(bq_client: bigquery.Client, sql_path: str | Path) -> int:
    """Execute a .sql file's contents as a single BigQuery job. Used for the
    silver MERGE scripts."""
    sql_text = Path(sql_path).read_text(encoding="utf-8")

    logger.info("Running %s ...", sql_path)
    start = time.perf_counter()
    job = bq_client.query(sql_text)
    job.result()
    elapsed = time.perf_counter() - start

    affected = job.num_dml_affected_rows if job.num_dml_affected_rows is not None else 0
    logger.info("Finished %s in %.2fs (%d rows affected)", sql_path, elapsed, affected)
    return affected
