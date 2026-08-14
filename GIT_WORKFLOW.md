# Git Workflow — Aramco Finance PoC

This document covers how we collaborate on this repo. Read it before your first commit.

## 1. One-time setup

Clone the repo:

```bash
git clone https://github.com/nofilasif-sudo/aramco-finance-poc.git
cd aramco-finance-poc
```

Set your git identity (use your real name and the email tied to your GitHub account):

```bash
git config --global user.email "you@example.com"
git config --global user.name "Your Name"
```

Install dependencies:

```bash
pip install -r requirements.txt
```

You'll need access to the `aramco-finance-poc-c2a4` GCP project (ask the project owner to grant you the appropriate IAM roles) to actually run the pipeline against real GCS/BigQuery resources.

## 2. `main` is protected — never push to it directly

You cannot `git push origin main` directly, even as an admin. All changes go through a branch and a Pull Request. This is enforced by GitHub, not just a convention — a direct push will be rejected.

## 3. Branch naming

Every branch starts with `feature/`, followed by your dataset area and a short description:

```
feature/<dataset>-<short-description>
```

Examples:
- `feature/trial-balance-presentation-amount`
- `feature/inventory-data-ingestion`
- `feature/ap-ar-schema-fix`

Since different people own different datasets in this same repo, naming the dataset in the branch makes it obvious at a glance whose work is whose — without needing separate `fix/`, `chore/` prefixes. A one-line description after the dataset name is enough; keep it short.

## 4. The actual workflow

```bash
# 1. Start from an up-to-date main
git checkout main
git pull

# 2. Create your branch (name it feature/<dataset>-<description>)
git checkout -b feature/trial-balance-your-change-name

# 3. Make your changes, commit as you go
git add .
git commit -m "Clear description of what changed and why"

# 4. Push your branch
git push origin feature/trial-balance-your-change-name
```

Then on GitHub: open a **Pull Request** targeting `main`. Write a short description of what changed and why — this is the record of the decision, not just the diff.

**Before opening a PR on pipeline code (`main.py`, `transformer.py`, `helpers.py`, or anything in `sql/`)**, confirm locally:
- `python main.py` runs clean, all 5 stages complete
- The zero-sum validation (stage 5) passes — if it doesn't, something in the data or logic broke, and it should be fixed before the PR, not after

**Get it reviewed.** At least one other person reviews the diff before merge. SQL changes in `sql/` especially — these directly affect financial data in BigQuery, read them carefully, not just skim.

**Merge and clean up:**
- Merge via GitHub's UI (not a manual command)
- **Delete the branch after merging** — GitHub offers a button right on the merged PR page. Always do this. The merge commit already preserves everything in `main`'s history, so nothing is lost — an undeleted branch just becomes clutter that makes it hard to tell active work from finished work, especially with multiple people each working on their own dataset.
- Update your local `main`:
```bash
git checkout main
git pull
```

## 5. Working across multiple datasets in one repo

This repo isn't single-purpose — different people own different datasets (e.g. trial balance, and others as they're added). A few things that keep this manageable:

- **Scope your PR to your dataset.** If your change is about trial balance, don't also touch files for someone else's dataset in the same PR, even if it's tempting to bundle a quick fix in — it makes review harder and blurs ownership.
- **Coordinate before touching shared files.** Things like `helpers.py`, the Dockerfile, or `default_config.json` may be used across multiple datasets' pipelines. If your change affects one of these, flag it in the PR description so a reviewer knows to check impact beyond just your own dataset.
- **Name things after your dataset**, not just generically — branch names (`feature/trial-balance-...`), and where relevant, table/file names too. This is what makes it possible for someone to glance at the repo and know who owns what.

## 6. Never commit secrets

No `.env` files, no service account key JSON, no credentials of any kind — ever, in any commit, even one you plan to delete later (git history keeps it). `.gitignore` already excludes the common cases (`.env`, `.venv/`, `__pycache__/`), but double check `git status` before every commit, especially the first one on a new branch.

If you ever add a new GCP integration or config file, check whether it might contain something sensitive before it gets staged.

## 7. If you hit a merge conflict

With a small team this should be rare, but if `main` has moved since you branched:

```bash
git checkout main
git pull
git checkout feature/trial-balance-your-change-name
git merge main
# resolve any conflicts shown, then:
git add .
git commit -m "Merge main into feature branch"
git push origin feature/trial-balance-your-change-name
```

## Questions

Ask before guessing, especially on anything touching GCP IAM, the service account, or the `sql/` MERGE logic — a wrong assumption there affects real client data, not just code correctness.
