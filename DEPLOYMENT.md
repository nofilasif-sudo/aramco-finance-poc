# Cloud Run Deployment Guide — Aramco Finance PoC

This covers deploying a dataset pipeline (e.g. `trial_balance/`) as a Cloud Run Job. Each dataset folder in this repo follows the same pattern — this doc uses `trial_balance` as the working example.

## Dockerfile template

Every dataset folder needs its own `Dockerfile`. Copy this template into your dataset's folder and adjust the two marked lines — the rest should stay as-is unless you have a real reason to change it.

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Adjust this line: list every .py and .json file your pipeline actually
# imports or reads. If you add a new file to your pipeline later, add it
# here too — a missing file here is the single most common deployment
# failure we've hit (silent until the container actually runs).
COPY main.py transformer.py helpers.py default_config.json ./

# If your pipeline has a sql/ folder (or any other subfolder it reads at
# runtime), copy it explicitly like this — COPY main.py ... does NOT
# recurse into subfolders automatically.
COPY sql/ ./sql/

# Adjust this line only if your entry point file is named something other
# than main.py.
CMD ["python", "main.py"]
```

**Before running any build**, cross-check this list against two things:
1. `ls` in your dataset folder — does every file the Dockerfile copies actually exist there?
2. Your code's actual imports (`from helpers import ...`, `open(ROOT / "default_config.json")`, etc.) — does the Dockerfile copy everything your code touches, and nothing extra?

Mismatches between these two are the root cause of almost every deployment issue we've hit so far — worth the 30 seconds to check before building, rather than debugging a cryptic error after.

## GCP resources reference

| Resource | Value |
|---|---|
| Project ID | `aramco-finance-poc-c2a4` |
| Region | `me-central2` (Dammam) |
| Artifact Registry repo | `etl-images` |
| Service account | `de-ingestion-sa@aramco-finance-poc-c2a4.iam.gserviceaccount.com` |

The service account has: `roles/storage.objectViewer`, `roles/bigquery.dataEditor`, `roles/bigquery.jobUser`, `roles/cloudbuild.builds.builder`, `roles/artifactregistry.writer`, `roles/logging.logWriter`. If you're deploying a new dataset's pipeline and hit a permissions error, it's likely this service account needs an additional role — don't just switch to your own account or a broader-permissioned one, add the specific missing role instead.

## Prerequisites

- Access to the `aramco-finance-poc-c2a4` GCP project (ask the project owner)
- `gcloud` CLI authenticated: `gcloud auth login` (Cloud Shell has this by default)
- Project set: `gcloud config set project aramco-finance-poc-c2a4`

## 1. Build and push the image

Run from **inside the dataset's folder** (e.g. `trial_balance/`), not the repo root — the Dockerfile's `COPY` paths are relative to wherever you run this from:

```bash
cd trial_balance
gcloud builds submit --region=me-central2 \
  --tag me-central2-docker.pkg.dev/aramco-finance-poc-c2a4/etl-images/<image-name> \
  --service-account=projects/aramco-finance-poc-c2a4/serviceAccounts/de-ingestion-sa@aramco-finance-poc-c2a4.iam.gserviceaccount.com \
  --gcs-log-dir=gs://aramco-finance-poc-c2a4_cloudbuild/logs
```

Replace `<image-name>` with something matching the dataset (e.g. `bronze-silver-etl` for trial balance). Watch for `SUCCESS` and a `Digest: sha256:...` line at the end.

## 2. Create the Cloud Run Job (one-time, per dataset)

```bash
gcloud run jobs create <job-name> \
  --image=me-central2-docker.pkg.dev/aramco-finance-poc-c2a4/etl-images/<image-name> \
  --region=me-central2 \
  --project=aramco-finance-poc-c2a4 \
  --service-account=de-ingestion-sa@aramco-finance-poc-c2a4.iam.gserviceaccount.com
```

This defines the job but does not run it — deliberate, so you can test manually before anything's automated.

If you need to update an existing job after rebuilding the image:

```bash
gcloud run jobs update <job-name> \
  --image=me-central2-docker.pkg.dev/aramco-finance-poc-c2a4/etl-images/<image-name> \
  --region=me-central2
```

## 3. Run it manually and check the logs

```bash
gcloud run jobs execute <job-name> --region=me-central2
```

Follow the logs link it gives you, or check Cloud Logging directly. **Do not attach a schedule until this succeeds cleanly at least once** — a scheduled job that fails silently at 6am is worse than one that fails loudly in front of you now.

## 4. Scheduling (not yet set up)

Cloud Scheduler → Cloud Run Job on a cron trigger is the intended next step once a dataset's manual run is verified clean. Not documented here yet since it hasn't been done — will be added once we actually set it up for trial balance.

## Troubleshooting — real issues we've hit

**`ERROR: The required property [project] is not currently set`**
Cloud Shell lost its project context (happens after session restarts). Fix: `gcloud config set project aramco-finance-poc-c2a4`, verify with `gcloud config get-value project`.

**`Invalid value for [source]: Dockerfile required when specifying --tag`**
Either the Dockerfile is missing from the folder you're building from, or it's named `dockerfile` (lowercase) instead of `Dockerfile`. Check with `ls -la` — filenames are case-sensitive on Linux.

**`COPY failed: file not found in build context`**
Something the Dockerfile expects to copy isn't actually in the folder. Check with `ls -la` before rebuilding — don't guess, verify.

**`Provided service account (...) is disabled`**
The default Cloud Build service account was disabled on this project (likely deliberate policy). Don't re-enable it — use `de-ingestion-sa` explicitly via `--service-account` on the build command instead.

**`if 'build.service_account' is specified, the build must either... specify 'build.logs_bucket'`**
When using a custom service account for the build, you must also specify `--gcs-log-dir`. Use `gs://aramco-finance-poc-c2a4_cloudbuild/logs`.

**Pipeline runs locally but fails in the container with a `FileNotFoundError`**
Almost always means a file the code expects (e.g. something under `sql/`) didn't actually get copied into the build context, or the Dockerfile's `COPY` list is out of sync with what the code actually imports/reads. Run `ls -la` in the exact folder you're building from and compare against what `main.py`/the Dockerfile reference.

## Notes for new datasets

If you're setting up deployment for a dataset other than trial balance, this same doc applies — just:
1. Follow the same folder structure (`<your-dataset>/main.py`, `helpers.py`, etc., self-contained)
2. Pick a distinct `<image-name>` and `<job-name>` so it doesn't collide with trial balance's
3. Confirm `de-ingestion-sa` has the roles your pipeline needs — check the reference table above before assuming a permissions error means you need a different account
