"""Bronze ingestion for the Aramco FC&RD PoC.

This file is what makes `src/bronze_ingest/` an importable package rather than
a folder that happens to contain .py files. Without it (plus pyproject.toml),
every entry point has to patch sys.path to find its own modules.
"""

__version__ = "0.1.0"
