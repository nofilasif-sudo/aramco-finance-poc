"""GCS and BigQuery adapters for the FS tables.

Kept separate from `word.py` / `statements.py` so those stay pure — they take
a path and return rows, and have no idea whether they run on a laptop or in
a Cloud Run job.

The cloud libraries are imported INSIDE the functions on purpose: the package
must stay importable and testable on a machine with no GCP SDK, which is what
lets the whole test suite run offline.

Table CREATION is DDL-first: ensure_table() executes the fixed CREATE TABLE
IF NOT EXISTS in sql/<table>.sql. Those files are generated from schema.py by
scripts/generate_ddl.py, so the DDL and the load job's schema cannot drift —
regenerate rather than hand-editing them.
"""

from __future__ import annotations

import io
from pathlib import Path

from .schema import DESCRIPTIONS, NULL, TYPES


def bq_schema(columns: list[str]):
    """Columns as BigQuery fields, with descriptions.

    One definition feeds both the DDL generator and the load job, so a
    table's schema and the loader's schema cannot disagree.
    """
    from google.cloud import bigquery
    return [bigquery.SchemaField(c, TYPES.get(c, "STRING"),
                                 description=DESCRIPTIONS[c])
            for c in columns]


def dataset_location(client, dataset_id: str, default: str) -> str:
    """The dataset's real location, or `default` if it does not exist yet.

    A dataset's location is immutable and cross-location loads are a hard
    error, so the existing dataset always wins over the configured default.
    """
    from google.cloud.exceptions import NotFound
    try:
        return client.get_dataset(dataset_id).location
    except NotFound:
        return default


def ensure_table(client, table_id: str, location: str, ddl_path) -> None:
    """Apply the fixed DDL. CREATE TABLE IF NOT EXISTS, so idempotent."""
    dataset = table_id.split(".")[1]
    ddl_sql = Path(ddl_path).read_text(encoding="utf-8")
    # Lets --dataset point the same DDL at a staging dataset for a dry run
    # against the live tables.
    ddl_sql = ddl_sql.replace(".bronze.", f".{dataset}.")
    client.query(ddl_sql, location=location).result()


def upload_csv(text: str, gcs_uri: str) -> str:
    """Stage the rendered CSV in GCS — a byte-for-byte lineage artifact."""
    from google.cloud import storage
    bucket_name, _, blob_name = gcs_uri[len("gs://"):].partition("/")
    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    blob.upload_from_string(text.encode("utf-8"), content_type="text/csv")
    return gcs_uri


def _load_config(columns: list[str]):
    """Load settings shared by the GCS and in-memory paths.

    Three settings carry all the risk:

    autodetect=False — autodetection would guess types from the data and
        could just as easily land `value` as FLOAT64, which is the single
        thing the Ingestion Notes rule out. The schema IS the contract;
        never let BigQuery infer it.

    null_marker — BigQuery's CSV loader turns an EMPTY UNQUOTED FIELD into
        NULL by default. This pair NEEDS its NULLs (a bold group header has
        no figure, and an empty string is not a legal NUMERIC), and it gets
        them from an explicit marker rather than from empty fields, so that
        an accidentally blank cell is still distinguishable from a
        deliberate NULL. VERIFY on the first load — do not assume it.

    WRITE_TRUNCATE — reloading the same document with append gives double
        the rows and every check still passes proportionally. Wrong but
        internally consistent is the failure mode to fear.
    """
    from google.cloud import bigquery
    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        schema=bq_schema(columns),
        skip_leading_rows=1,
        autodetect=False,
        null_marker=NULL,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )


def load_csv_from_gcs(client, gcs_uri: str, table_id: str, location: str,
                      columns: list[str]):
    job = client.load_table_from_uri(
        gcs_uri, table_id, job_config=_load_config(columns), location=location)
    job.result()          # blocks; raises with row-level detail on failure
    return job


def load_csv_from_memory(client, csv_text: str, table_id: str, location: str,
                         columns: list[str]):
    """Load straight from an in-memory CSV string — no local file, no bucket."""
    buf = io.BytesIO(csv_text.encode("utf-8"))
    job = client.load_table_from_file(
        buf, table_id, job_config=_load_config(columns), location=location)
    job.result()
    return job
