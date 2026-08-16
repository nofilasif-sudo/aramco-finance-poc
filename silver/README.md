# Silver — dim/fact build

Upserts the silver dimension/fact tables fed by the bronze layer, then runs
the data-quality check board. Moved out of `bronze/` because it's a
different layer with its own lifecycle — bronze extracts source workbooks,
silver builds the warehouse model from bronze tables already in BigQuery.

Requires the bronze tables it reads (`bronze_coa_raw`, `bronze_tb_raw`,
`bronze_group_tb_raw`, `bronze_ifrs_rubric_raw`, `bronze_checklist_raw`,
etc.) to already be loaded — run the relevant bronze ingestion(s) first
(`bronze/scripts/push_to_bq.py` for coa/group_tb/checklist;
`trial_balance/` for affiliate trial balance).

```bash
set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\key.json
pip install google-cloud-bigquery

python scripts/build_silver.py --check   # validate SQL only, write nothing
python scripts/build_silver.py           # upsert, then assert
```

## Layout

```
silver/
├── scripts/
│   └── build_silver.py     upserts silver.*, then runs the DQ board
└── sql/
    ├── silver_build.sql       MERGE statements for every silver dim/fact table
    ├── silver_checks.sql      DQ board queried by build_silver.py
    ├── silver_coa.sql         CoA-specific silver build (dim_account, dim_group_account)
    ├── silver_coa_checks.sql  CoA-specific checks
    └── join_checks.sql        ad-hoc referential-integrity queries, run manually
```
