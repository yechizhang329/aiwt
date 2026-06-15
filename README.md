# AIWT Core Source

Canonical source-of-truth staging repo for the Wang orthodontic V4 application.

## Contents

- `backend/` — FastAPI case API, auth, storage, audit, queue/watchdog, v2 orchestrator, schemas, prompts, Track1/GBrain admin APIs, runtime CI guards.
- `webapp/` — Streamlit user and doctor UI, case history, result rendering, admin/dashboard pages, API client.
- `runtime_scripts/` — small runtime dependencies still imported by the backend, including `HardRuleWrapper.py`, `audit_log.py`, and Slock adapter compatibility code.
- `tools/` — selected ceph/HRNet advisory tools and configs. Model weights and generated overlays are intentionally excluded from git.
- `CODE_INVENTORY.md` — keep/gated/deprecated/archive inventory and cleanup plan.

## Runtime Notes

This repo intentionally excludes:

- user uploads and patient data;
- local SQLite databases;
- auth secrets and user config;
- logs and runtime sessions;
- model weights and large generated artifacts.

Set deployment-specific paths via environment variables:

- `BACKEND_DB_PATH`
- `UPLOAD_DIR`
- `USERS_YAML_PATH`
- `DENTIST_WORKSPACE`
- `ORCHESTRATOR_SCRIPTS`
- `BACKEND_API_URL`

The canonical default for `ORCHESTRATOR_SCRIPTS` is `runtime_scripts/` inside this repo. `DENTIST_WORKSPACE` may still point to the clinical knowledge workspace where governed KB notes live.

## Safety Rule

Do not delete or rewrite clinical prompts, output schemas, HRW, or CI guard files without DW/K/PM review. Deprecation should start by moving old paths behind explicit legacy/archive boundaries, then verifying imports and startup checks.

