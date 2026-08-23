from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from google.cloud import storage

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers import configure_logging, run_sql_file
from transformer import VarianceThresholdsTransformer

logger = logging.getLogger("main")


def main() -> None:
    with open(ROOT / "default_config.json", "r", encoding="utf-8") as handle:
        config = json.load(handle)

    configure_logging(config.get("log_level", "INFO"))
    logger.info("Config loaded")

    project = config["gcp_project"]
    storage_client = storage.Client(project=project)

    bq_client = None
    if config.get("output_mode", "bigquery") == "bigquery":
        from google.cloud import bigquery

        bq_client = bigquery.Client(project=project)

    pipeline_start = time.perf_counter()

    try:
        logger.info("=== STAGE 1/2: bronze_ingest ===")
        transformer = VarianceThresholdsTransformer(config, storage_client, bq_client)
        results = transformer.run()
        logger.info("Stage 1 done: %s", results)

        if config.get("output_mode", "bigquery") == "bigquery":
            logger.info("=== STAGE 2/2: silver_merge ===")
            run_sql_file(bq_client, ROOT / "sql" / "silver_variance_threshold.sql")
            run_sql_file(bq_client, ROOT / "sql" / "silver_silent_account_rule.sql")
        else:
            logger.info("=== STAGE 2/2: silver_merge skipped (output_mode != 'bigquery') ===")

        elapsed = time.perf_counter() - pipeline_start
        logger.info("=== Pipeline finished successfully in %.2fs ===", elapsed)
    except Exception:
        logger.exception("Pipeline failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
