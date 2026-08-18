"""The one exception type this package raises.

Its own module rather than living inside a reader, so that `word.py` and
`statements.py` can both raise it without either importing the other, and so
that a caller can catch every failure this package produces with one import.

Deliberately mirrors bronze_ingest.excel.IngestError in name and meaning: if
these two packages are merged later, the two classes collapse into one and
nothing that catches it has to change.
"""

from __future__ import annotations


class IngestError(Exception):
    """A source document did not look the way the contract says it should.

    Raised for READING failures only — a missing statement, an anchor that
    does not resolve, a figure that would not parse. NEVER raised because the
    numbers disagree: see statements.py for why that distinction is the whole
    point of this package.
    """
