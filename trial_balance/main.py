from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from google.cloud import bigquery, storage

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helpers import configure_logging, run_sql_file, validate_zero_sum
from transformer import ExcelTransformer

logger = logging.getLogger("main")


def main() -> None:
    with open(ROOT / "default_config.json", "r", encoding="utf-8") as handle:
        config = json.load(handle)

    configure_logging(config.get("log_level", "INFO"))
    logger.info("Config loaded")

    project = config["gcp_project"]
    storage_client = storage.Client(project=project)
    bq_client = bigquery.Client(project=project)

    bq_targets = config["bq_targets"]
    pipeline_start = time.perf_counter()

    try:
        logger.info("=== STAGE 1/5: bronze_ingest ===")
        transformer = ExcelTransformer(config, storage_client, bq_client)
        bronze_results = transformer.run()
        logger.info("Stage 1 done: %s", bronze_results)

        logger.info("=== STAGE 2/5: dim_entity_merge ===")
        run_sql_file(bq_client, ROOT / "sql" / "silver_entity.sql")

        logger.info("=== STAGE 3/5: dim_period_merge ===")
        run_sql_file(bq_client, ROOT / "sql" / "silver_period.sql")

        logger.info("=== STAGE 4/5: fact_trial_balance_merge ===")
        run_sql_file(bq_client, ROOT / "sql" / "silver_fact_tb.sql")

        logger.info("=== STAGE 5/5: validate_zero_sum ===")
        validate_zero_sum(bq_client, bq_targets["fact_trial_balance"])

        elapsed = time.perf_counter() - pipeline_start
        logger.info("=== Pipeline finished successfully in %.2fs ===", elapsed)
    except Exception:
        logger.exception("Pipeline failed")
        raise SystemExit(1)

if __name__ == "__main__":
    main()