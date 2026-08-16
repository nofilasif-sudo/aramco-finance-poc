"""GCS and BigQuery adapters.

Kept separate from `excel.py` / the extractor modules so those stay pure —
they take bytes and return rows, and have no idea whether they run on a
laptop, in a notebook, or in a Cloud Run job. That separation is what lets
the same code serve Path 2 without a rewrite.

The cloud libraries are imported INSIDE the functions on purpose: the package
must stay importable and testable on a machine with no GCP SDK.

Generalized from a single CoA-only schema to a registry (BRONZE_TABLES) so
the same bq_schema()/ensure_table()/load helpers serve every bronze table
this package owns instead of being re-hardcoded per table.

Trial balance and the CSV-sourced tables (ifrs standard/rubric, entity
context) are ingested elsewhere and are not defined here.
"""

from __future__ import annotations

import io

# ---------------------------------------------------------------------------
# source_file — the row-level lineage column every bronze table carries.
# Defined once here so its type and description can't drift between tables.
# ---------------------------------------------------------------------------
SOURCE_FILE_DESCRIPTION = (
    "Original workbook filename this row was read from (e.g. "
    "'PoC_Charts_of_Accounts (1).xlsx'). ADDED AT INGEST — not a column in "
    "the sheet. Lets a row be traced back to the exact file it came from "
    "when a workbook is re-shared or re-versioned."
)


def with_source_file(columns: list[str], descriptions: dict) -> tuple[list[str], dict]:
    """Append the shared source_file column/description to a table spec."""
    cols = [*columns, "source_file"]
    descs = {**descriptions, "source_file": SOURCE_FILE_DESCRIPTION}
    return cols, descs


# ---------------------------------------------------------------------------
# Column descriptions live here, not only in sql/*.sql, because this is what
# actually runs — and because Agent 6 is text-to-SQL over this warehouse, so
# the description is what tells the agent which column to pick. Treat them
# as product, not documentation.
# ---------------------------------------------------------------------------
_COA_COLUMNS, _COA_DESCRIPTIONS = with_source_file(
    ["chart_scope", "account", "account_name", "statement", "category",
     "normal_balance", "level", "source_reference"],
    {
        "chart_scope":
            "GROUP, 2010 or 2380. ADDED AT INGEST from the worksheet tab name — "
            "it is not a column in the sheet. Essential to every join: the two "
            "affiliate charts share 42 four-digit codes, 15 of which mean "
            "different things (1120 = plant & machinery for 2010 but refinery "
            "plant for 2380). Never join on account alone.",
        "account":
            "Account code as text. Affiliates use 4-digit ledger codes; the Group "
            "chart uses G-prefixed 5-digit nodes (G11000). Deliberately "
            "non-colliding namespaces. Never empty.",
        "account_name":
            "Account description as written, leading spaces preserved — "
            "indentation marks level-2 sub-accounts on the Group chart and is "
            "data, not formatting.",
        "statement":
            "Balance sheet | Income statement. Note the trial balances use BS/PL "
            "for the same concept; bronze does not harmonise them.",
        "category":
            "FS caption group, e.g. 'Non-current assets', 'Cost of sales'. "
            "13 distinct values across the affiliate charts.",
        "normal_balance":
            "Dr or Cr — the side this account normally sits on. Supports "
            "wrong-side anomaly detection; not used for sign correction.",
        "level":
            "Group chart only; empty for affiliates. 1 = face caption (52 nodes), "
            "2 = analytical sub-account (26 nodes). Text, so '1' not 1.0.",
        "source_reference":
            "Group chart only; empty for affiliates. Where the node traces to in "
            "Aramco's Q1-2026 condensed interim report, e.g. 'Balance sheet; "
            "Note 5'. Supports disclosure traceability.",
    },
)

_CHECKLIST_COLUMNS, _CHECKLIST_DESCRIPTIONS = with_source_file(
    ["item", "document", "required", "applies_to", "expected_format",
     "description"],
    {
        "item":
            "Checklist item number, 01..14.",
        "document":
            "Required document name, e.g. 'Signed trial balance', "
            "'Intercompany reconciliation'.",
        "required":
            "Whether this document is mandatory for a submission ('Yes').",
        "applies_to":
            "Which affiliates this requirement applies to, e.g. "
            "'All affiliates'.",
        "expected_format":
            "Expected file format, e.g. 'xlsx / SAP BPC export', "
            "'pdf (signed)'.",
        "description":
            "What the document should contain.",
    },
)

# Table name -> (columns, descriptions, table description). One entry per
# bronze table this package owns — the local CLI, push_to_bq.py and this
# module's own bq_schema()/ensure_table() all key off this registry instead
# of each hardcoding a schema.
BRONZE_TABLES = {
    "bronze_coa_raw": {
        "columns": _COA_COLUMNS,
        "descriptions": _COA_DESCRIPTIONS,
        "table_description": (
            "Charts of accounts for both affiliates and the Group, stacked. 188 "
            "rows = 78 GROUP + 66 SABIC + 44 Petro Rabigh, matching each tab's "
            "own 'Total accounts' footer exactly. All columns STRING per the "
            "bronze contract. Section-divider rows are dropped at ingest (their "
            "captions are fully duplicated by the category column) — a "
            "deviation negotiated specifically for this table, not a "
            "pipeline-wide rule. No relationships enforced at this layer: the "
            "affiliate-to-Group mapping does not exist in this pack — it is "
            "Agent 3's output, produced with a confidence score, not an input. "
            "SYNTHETIC data calibrated to public results; not Aramco actuals."
        ),
    },
    "bronze_checklist_raw": {
        "columns": _CHECKLIST_COLUMNS,
        "descriptions": _CHECKLIST_DESCRIPTIONS,
        "table_description": (
            "Required-documents checklist for an affiliate submission pack — "
            "Agent 2 reference data. 14 rows. CHECKLIST TAB ONLY: the "
            "Manifest-*/IC-confirmations tabs in the same workbook (submission "
            "status, intercompany anomaly A6) are Agent 2's own ingestion and "
            "are not part of this table."
        ),
    },
}


def bq_schema(columns: list[str], descriptions: dict):
    """Columns as BigQuery STRING fields, with descriptions.

    One definition feeds both CREATE TABLE and the load job, so a table's
    schema and the loader's schema cannot disagree.
    """
    from google.cloud import bigquery
    return [bigquery.SchemaField(c, "STRING", description=descriptions[c])
            for c in columns]


# ---------------------------------------------------------------------------
# GCS
# ---------------------------------------------------------------------------
def read_workbook(gcs_uri: str) -> io.BytesIO:
    """Download an xlsx from GCS into memory.

    openpyxl cannot open a gs:// path — it needs a filename or a file-like
    object. Memory is simpler than staging on disk for a file this size, and
    leaves nothing behind when a notebook runtime is recycled.
    """
    from google.cloud import storage

    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    client = storage.Client()
    blob = client.bucket(bucket_name).blob(blob_name)
    if not blob.exists():
        raise FileNotFoundError(f"{gcs_uri} does not exist")
    return io.BytesIO(blob.download_as_bytes())


def upload_csv(text: str, gcs_uri: str) -> str:
    """Write the CSV to GCS. This object is the lineage artifact, not a temp
    file — it is what proves, byte for byte, what was loaded."""
    from google.cloud import storage

    bucket_name, _, blob_name = gcs_uri.removeprefix("gs://").partition("/")
    client = storage.Client()
    client.bucket(bucket_name).blob(blob_name).upload_from_string(
        text, content_type="text/csv")
    return gcs_uri


# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------
def dataset_location(client, dataset_id: str, default: str) -> str:
    """Return the dataset's location, or `default` if it does not exist yet.

    Discovered rather than configured, because a dataset's location is
    IMMUTABLE and BigQuery refuses to load from a bucket in another location.
    Hardcoding a guess here turns a config typo into a confusing load error.
    """
    from google.cloud.exceptions import NotFound
    try:
        return client.get_dataset(dataset_id).location
    except NotFound:
        return default


def ensure_table(client, table_id: str, location: str, columns: list[str],
                 descriptions: dict, table_description: str) -> None:
    """Create the dataset and table if absent. Never drops data or columns.

    CREATE-IF-NOT-EXISTS semantics separate schema management from data
    management: re-running this is always safe, and only the load replaces
    rows.

    If the table already exists, its schema is evolved ADDITIVELY ONLY: any
    column present in `columns` but missing from the deployed table (e.g.
    source_file, added to every bronze table after bronze_coa_raw had
    already been created and loaded once) is appended. Existing columns are
    never renamed, retyped, or dropped — a real contract change still has to
    be a deliberate migration, not something this function does for you.
    Without this, a load job whose schema has grown since the table was
    created fails outright with a schema-mismatch error instead of loading.
    """
    from google.cloud import bigquery
    from google.cloud.exceptions import NotFound

    project, dataset, table = table_id.split(".")
    ds = bigquery.Dataset(f"{project}.{dataset}")
    ds.location = location
    client.create_dataset(ds, exists_ok=True)

    desired_schema = bq_schema(columns, descriptions)
    try:
        existing = client.get_table(table_id)
    except NotFound:
        tbl = bigquery.Table(table_id, schema=desired_schema)
        tbl.description = table_description
        client.create_table(tbl)
        return

    existing_names = {f.name for f in existing.schema}
    missing = [f for f in desired_schema if f.name not in existing_names]
    if missing:
        existing.schema = [*existing.schema, *missing]
        client.update_table(existing, ["schema"])


def _load_config(columns: list[str], descriptions: dict):
    """Load settings shared by the GCS and local-file paths.

    Three settings carry all the risk:

    autodetect=False — autodetection would type `account` as INT64, making
        G11000 and 1100 incompatible, and silently break the all-STRING
        contract. The schema IS the contract; never let BigQuery guess it.

    null_marker="\\N" — BigQuery's CSV loader turns an EMPTY UNQUOTED FIELD
        into NULL by default. Our contract says an empty cell lands as an
        empty string; a marker that never occurs means empties stay empty
        strings and bronze holds no NULLs at all. VERIFY on the first load —
        do not assume it.

    WRITE_TRUNCATE — reloading the same pack with append gives double the
        rows and every check still passes proportionally. Wrong but
        internally consistent is the failure mode to fear.
    """
    from google.cloud import bigquery
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        schema=bq_schema(columns, descriptions),
        skip_leading_rows=1,
        autodetect=False,
        null_marker="\\N",
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )


def load_csv_from_gcs(client, gcs_uri: str, table_id: str, location: str,
                      columns: list[str], descriptions: dict):
    job = client.load_table_from_uri(
        gcs_uri, table_id, job_config=_load_config(columns, descriptions),
        location=location)
    job.result()          # blocks; raises with row-level detail on failure
    return job


def load_csv_from_file(client, path, table_id: str, location: str,
                       columns: list[str], descriptions: dict):
    """Load straight from a local file — no bucket required.

    Useful when GCS is not provisioned yet. It skips the lineage artifact,
    so prefer the GCS path once a bucket exists.
    """
    with open(path, "rb") as f:
        job = client.load_table_from_file(
            f, table_id, job_config=_load_config(columns, descriptions),
            location=location)
    job.result()
    return job
