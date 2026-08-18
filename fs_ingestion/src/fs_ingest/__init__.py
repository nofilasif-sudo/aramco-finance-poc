"""Group financial statements (.docx) -> bronze fs_clean / fs_seeded.

Self-contained on purpose: this package shares no code with bronze_ingest so
the two can be reviewed and merged independently. Where a convention is
duplicated rather than imported (the CSV writer, the IngestError name, the
three-phase push script), it is duplicated deliberately and marked as such,
so that merging later is a deletion rather than a rewrite.
"""

from .errors import IngestError
from .schema import COLUMNS, NULL, TABLES, TYPES

__all__ = ["IngestError", "COLUMNS", "NULL", "TABLES", "TYPES"]
