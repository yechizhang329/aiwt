# Code Inventory

Date: 2026-06-15

Scope: first source-of-truth consolidation of the V4 Wang orthodontic app code. This file classifies code assets; it does not certify clinical readiness.

## Keep: Main Runtime

- `backend/main.py`, `backend/routers/*`, `backend/db.py`, `backend/auth.py`, `backend/models.py`, `backend/config.py`
  - FastAPI backend, auth, case lifecycle, attachments, audit, queue/retry/watchdog.
- `backend/orchestrator/v2_orchestrator.py`
  - Current v2/Config-4 orchestration core.
- `backend/orchestrator/output_schemas.py`, `stage_info.py`, `multimodal_dispatch.py`, `read_tool_advisory.py`
  - Stage contracts, UI progress state, image dispatch, and read-tool advisory wrapper.
- `backend/prompts/stage_A_initial_reader.md`, `stage_B_kc.md`, `stage_B_cm.md`, `stage_C_senior_clinician.md`, `stage_D_critic.md`, `diagnosis_first_positive_kb_training.md`
  - Clinical system prompts and training contract surfaces. Changes require clinical governance.
- `webapp/app.py`, `webapp/pages/1_New_Case.py`, `2_Case_History.py`, `3_Scene3_Doctor.py`, `webapp/api_client.py`, `webapp/case_display.py`, `webapp/components/stage_progress.py`
  - Current user/doctor submission and result display path.
- `backend/ci_checks.py`, `backend/reasoning_guards.py`, `backend/positive_diagnosis_card_checks.py`
  - Structural safety checks and governance guards.
- `runtime_scripts/HardRuleWrapper.py`, `runtime_scripts/audit_log.py`
  - Still imported by v2 backend runtime.

## Keep: Admin / Governance Runtime

- `backend/track1.py`, `backend/gbrain.py`
- `backend/routers/track1_router.py`, `backend/routers/gbrain_router.py`
- `webapp/pages/4_Admin_Dashboard.py`, `5_Track1_KB.py`, `6_GBrain.py`

These remain governance/admin surfaces, not hot-path clinical diagnosis sources unless separately approved and wired.

## Gated / Quarantined

- Stage D Critic path
  - `backend/config.py` defaults `STAGE_D_CRITIC_ENABLED=0`.
  - Keep as rollback-compatible code until PM decides full removal.
- IX / ensemble / positive diagnosis advisory / read-tool advisory
  - Defaults are OFF by environment flag.
  - Keep for controlled harness/shadow use; no live effect without explicit enablement.
- `tools/*`
  - Offline/advisory ceph and HRNet tools only. Model weights are excluded from git.
- `backend/sandbox/*`
  - Research and regression harness code. Not product runtime.

## Deprecated Candidates

- `backend/legacy/orchestrator_bridge.py`
  - Legacy bridge to old `SlimOrchestrator`; current `cases_router.py` imports `orchestrator.v2_orchestrator.run_v2`.
  - Quarantined for historical reference only. It is not importable without the old `SlimOrchestrator.py` dependency, which is intentionally excluded from this source-of-truth branch.
- `runtime_scripts/legacy/adapters/slock_cli_adapter.py`
  - Legacy Slock transport adapter for the old `SlimOrchestrator` bridge.
  - Quarantined with the same missing legacy dependency; not part of current runtime or deployment.
- `webapp/slock_client.py`, `webapp/poller.py`, legacy jobs rendering in `webapp/pages/2_Case_History.py`
  - Legacy Slock-CLI jobs path. Keep read-only until migration is complete.
- Old one-off scripts from DentistWang workspace
  - Examples: `SlimOrchestrator.py`, `diagnostic_post.py`, `consult*.py`, KG/download/scrape utilities.
  - Not copied here except runtime imports needed by backend.

## Archive / Do Not Git

- Patient uploads, local DBs, logs, runtime sessions.
- `artifacts/zhengya_corpus/*`, eval packages, extracted images, generated PPT/media outputs.
- HRNet model weights (`*.pth`) and generated overlays.
- `config/users.yaml`, `secure/`, `.env`, Slock credentials, agent profile state.

## Immediate Cleanup Sequence

1. Establish this repo as the canonical code source with `backend/`, `webapp/`, `runtime_scripts/`, selected `tools/`, and this inventory.
2. Verify WebAppDev deployment path and uncommitted webapp changes before merging to `main`.
3. Add import/startup checks for the staged repo.
4. Delete quarantined legacy bridge files after one more runtime import verification cycle.
5. Move data/eval artifacts into a data registry or object storage with manifests only in git.
