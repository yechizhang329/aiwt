"""
FastAPI backend — WebAppDev M-C3, Sprint 1.

Architecture: Streamlit frontends (Scene 1 + Scene 3) → FastAPI → SlimOrchestrator → 5-agent stack.
Sprint 1: skeleton with auth + mock endpoints.
Sprint 2: SlimOrchestrator integration via asyncio task queue.
"""

import asyncio
import sys
import uuid as _uuid
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import ci_checks
import config
import db
from routers.auth_router import router as auth_router
from routers.cases_router import router as cases_router
from routers.attachments_router import router as attachments_router
from routers.audit_router import router as audit_router
from routers.meta_router import router as meta_router
from routers.gbrain_router import router as gbrain_router
from routers.track1_router import router as track1_router

app = FastAPI(
    title="WangSpecial Backend API",
    description="FastAPI backend bridging Streamlit frontends to 5-agent cowork stack.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://localhost:8502", "http://localhost:8503"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(cases_router)
app.include_router(attachments_router)
app.include_router(audit_router)
app.include_router(meta_router)
app.include_router(gbrain_router)
app.include_router(track1_router)


@app.on_event("startup")
async def startup():
    ci_checks.run_all_checks()
    db.init_db()
    await _recover_processing_cases()
    asyncio.create_task(_hard_timeout_watchdog())


def _apply_hard_timeouts():
    """Change 29: escalate processing cases that exceeded ORCHESTRATOR_HARD_TIMEOUT_SEC."""
    import json as _json
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=config.ORCHESTRATOR_HARD_TIMEOUT_SEC)
    stuck = []
    for c in db.list_cases(status="processing", limit=100):
        # Use last_dispatched_at (set on startup recovery) if available, else submitted_at.
        # This prevents watchdog from incorrectly escalating cases re-dispatched after a
        # backend restart whose original submitted_at predates the restart.
        meta = c.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        ref_time_str = (meta.get("last_dispatched_at") if isinstance(meta, dict) else None) or c.get("submitted_at")
        if ref_time_str and datetime.fromisoformat(ref_time_str) < cutoff:
            stuck.append(c)
    for case in stuck:
        case_id = case["case_id"]
        scene = case.get("scene", "")
        existing_meta = case.get("metadata") or {}
        merged_meta = {**existing_meta, "timeout_reason": "system_timeout", "timeout_at": now.isoformat()}

        db.update_case_status(
            case_id, "failed",
            metadata=merged_meta,
            error_msg="系统处理超时（建议重试）",
        )

        try:
            _scripts_path = str(config.ORCHESTRATOR_SCRIPTS)
            if _scripts_path not in sys.path:
                sys.path.insert(0, _scripts_path)
            import audit_log
            audit_log.write_entry({
                "audit_id": str(_uuid.uuid4()),
                "trace_id": case.get("trace_id", str(_uuid.uuid4())),
                "case_id": case_id,
                "phase": "watchdog",
                "from": "system",
                "to": "db",
                "msg_type": "system_timeout_escalation",
                "msg_id": str(_uuid.uuid4()),
                "metadata": {
                    "timeout_sec": config.ORCHESTRATOR_HARD_TIMEOUT_SEC,
                    "submitted_at": case.get("submitted_at"),
                    "timeout_at": now.isoformat(),
                },
            })
        except Exception:
            pass


async def _hard_timeout_watchdog():
    """Change 29: scan every 5 min for processing cases past hard SLA."""
    while True:
        await asyncio.sleep(300)
        try:
            await asyncio.to_thread(_apply_hard_timeouts)
        except Exception:
            pass


async def _recover_processing_cases():
    """Change 18: re-dispatch cases stuck in 'processing' from a prior restart.
    Only recovers cases older than 2 min to avoid race-condition double-dispatch
    of cases that just started when the backend restarted mid-flight.
    Also re-queues any cases stuck in 'queued' state (Item 5).
    """
    import asyncio
    from routers.cases_router import _run_orchestration, _running_tasks, _case_queue, _drain_queue

    stale_cutoff = datetime.utcnow() - timedelta(minutes=2)
    stuck = db.list_cases(status="processing", limit=50)
    for case in stuck:
        case_id = case["case_id"]
        if case_id in _running_tasks:
            continue
        try:
            submitted = datetime.fromisoformat(case.get("submitted_at", ""))
            if submitted > stale_cutoff:
                continue  # Too fresh — skip to avoid double-dispatch
        except Exception:
            pass
        # Stamp last_dispatched_at so watchdog uses restart time, not original submitted_at
        import json as _json
        try:
            meta = case.get("metadata") or {}
            if isinstance(meta, str):
                meta = _json.loads(meta)
            meta["last_dispatched_at"] = datetime.utcnow().isoformat()
            with db._conn() as _con:
                _con.execute("UPDATE cases SET metadata=? WHERE case_id=?",
                             (_json.dumps(meta), case_id))
        except Exception:
            pass
        task = asyncio.create_task(
            _run_orchestration(
                case_id, case["trace_id"], case["scene"], case["case_payload"]
            )
        )
        _running_tasks[case_id] = task

    # Re-queue cases stuck in "queued" state from prior restart (Item 5)
    queued_cases = db.list_cases(status="queued", limit=50)
    existing_queue_ids = {item[0] for item in _case_queue}
    for case in queued_cases:
        case_id = case["case_id"]
        if case_id not in existing_queue_ids and case_id not in _running_tasks:
            _case_queue.append((case_id, case["trace_id"], case["scene"], case["case_payload"]))
    if _case_queue:
        _drain_queue()
