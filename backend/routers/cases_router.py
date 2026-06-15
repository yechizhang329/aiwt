"""
Case lifecycle endpoints.
Sprint 1: mock responses.
Sprint 2: SlimOrchestrator integration via asyncio background task.
Sprint 5.1: per-case audit endpoint wired to audit_log.query().
"""

import asyncio
import sys
from pathlib import Path
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException

import auth
import config
import db
from models import (
    CaseSubmitResponse,
    CaseStatusResponse,
    FinalOutput,
    Scene1CaseSubmit,
    Scene3CaseSubmit,
)

router = APIRouter(prefix="/v1/case", tags=["cases"])

# Tracks in-flight asyncio Tasks keyed by case_id (for cancellation + GC)
_running_tasks: dict[str, asyncio.Task] = {}

# Item 5: Queue max 3 concurrent dispatches
_MAX_CONCURRENT = 3
_case_queue: list[tuple] = []  # (case_id, trace_id, scene, case_payload)


def _drain_queue():
    """Promote queued cases to processing if slots are available. Call after any task finishes."""
    active = sum(1 for t in _running_tasks.values() if not t.done())
    while _case_queue and active < _MAX_CONCURRENT:
        case_id, trace_id, scene, case_payload = _case_queue.pop(0)
        db.update_case_status(case_id, "processing", phase="A")
        task = asyncio.create_task(_run_orchestration(case_id, trace_id, scene, case_payload))
        _running_tasks[case_id] = task
        active += 1


def _check_sufficiency(case_payload: dict, scene: str) -> dict:
    """Change 20: Pre-Phase-A sufficiency gate. Returns ok=False with message if incomplete."""
    missing = []
    tmd_urgent = False

    if scene.startswith("1"):
        age = case_payload.get("patient_age")
        gender = case_payload.get("patient_gender")
        complaint = case_payload.get("chief_complaint") or ""
        attachment_ids = case_payload.get("attachment_ids") or []

        if not age or not isinstance(age, int) or age < 1:
            missing.append("患者年龄")
        if not gender:
            missing.append("性别")
        if not isinstance(complaint, str) or len(complaint.strip()) < 10:
            missing.append("文字主诉（至少 10 字）")
        # Change 27: photos required only when complaint is short (< 30 chars);
        # a sufficiently detailed complaint provides enough morphology signal on its own.
        if not attachment_ids and isinstance(complaint, str) and len(complaint.strip()) < 30:
            missing.append("至少 1 张照片或 X 光（主诉较简短时需影像辅助形态评估）")

    elif scene.startswith("3"):
        # Change 34: primary field is doctor_specific_question; fallback to legacy fields
        doctor_q = case_payload.get("doctor_specific_question") or ""
        complaint_d = case_payload.get("chief_complaint_doctor") or ""
        complaint_p = case_payload.get("chief_complaint_patient") or ""
        imaging = case_payload.get("imaging_provided") or []

        # age/sex are optional for Scene 3 (Phase 5 DavidC ratify)
        # Change 38b: non-empty only, no min-length
        has_question = (
            bool(doctor_q.strip())
            or len(complaint_d.strip()) >= 1
            or len(complaint_p.strip()) >= 1
        )
        if not has_question:
            missing.append("提问内容")
        if not imaging:
            missing.append("至少 1 张影像资料")

        # TMD 急诊红旗: active urgent symptoms + no CT/MRI
        cf = case_payload.get("clinical_findings") or {}
        if isinstance(cf, dict):
            tmd = cf.get("tmd_signals") or {}
            if isinstance(tmd, dict):
                symptoms = tmd.get("active_symptoms") or []
                urgency_kw = ["积液", "张口受限", "急性"]
                has_urgent = any(
                    any(kw in s for kw in urgency_kw) for s in symptoms
                ) if symptoms else False
                if has_urgent:
                    imaging = case_payload.get("imaging_provided") or []
                    ct_types = {"cbct_axial", "cbct_coronal", "mri", "cbct"}
                    has_ct = any(
                        str(item.get("type", "")).lower() in ct_types
                        for item in imaging
                        if isinstance(item, dict)
                    )
                    if not has_ct:
                        tmd_urgent = True

    if missing:
        field_str = "、".join(missing)
        message = (
            f"您好，您的咨询缺少：{field_str}。"
            f"请补充后再次提交。"
            f"建议优先补：{missing[0]}。"
            f"这是为给您准确诊断。"
        )
        return {"ok": False, "missing": missing, "message": message, "tmd_urgent": tmd_urgent}

    if tmd_urgent:
        return {
            "ok": False,
            "missing": [],
            "message": (
                "⚠️ 急诊提醒：检测到颞下颌关节急性症状（积液 / 张口受限），"
                "但未上传关节影像（CBCT / MRI）。"
                "建议先完成关节影像检查后再提交，或直接联系诊所急诊处理。"
            ),
            "tmd_urgent": True,
        }

    return {"ok": True, "missing": [], "message": "", "tmd_urgent": False}


def _build_status_response(case: dict) -> CaseStatusResponse:
    final_output = None
    if case.get("final_output"):
        fo = case["final_output"]
        final_output = FinalOutput(
            format=fo.get("format", "markdown"),
            sections=fo.get("sections", {}),
            rendered_markdown=fo.get("rendered_markdown", ""),
            word_count=fo.get("word_count", 0),
            layer_2=fo.get("layer_2"),
            image_anchors=fo.get("image_anchors") or [],
            confidence=fo.get("confidence"),
            学派_attribution_used=fo.get("学派_attribution_used"),
            v4_review_prompt=fo.get("v4_review_prompt"),
            v4_phase2=fo.get("v4_phase2"),
            v4_source_explanation=fo.get("v4_source_explanation"),
            v4_shengang_subtype=fo.get("v4_shengang_subtype"),
            v4_treatment_advisory=fo.get("v4_treatment_advisory"),
            v4_diagnosis_first=fo.get("v4_diagnosis_first"),
            v4_diagnosis_first_training=fo.get("v4_diagnosis_first_training"),
            v4_reasoning_output_projection=fo.get("v4_reasoning_output_projection"),
            v4_reasoning_training=fo.get("v4_reasoning_training"),
            v4_final_report_compliance=fo.get("v4_final_report_compliance"),
        )
    submission = dict(case.get("case_payload") or {})
    # Enrich attachment_ids with filename/mime metadata for UI display (Option B)
    attachment_ids = submission.get("attachment_ids") or []
    if attachment_ids:
        meta_list = []
        for i, aid in enumerate(attachment_ids):
            a = db.get_attachment(aid)
            meta_list.append({
                "id": aid,
                "filename": a["filename"] if a else f"附件 #{i + 1}",
                "mime_type": a["mime_type"] if a else "unknown",
            })
        submission["attachment_meta"] = meta_list
    # Scene 3: enrich imaging_provided items with filenames too
    imaging = submission.get("imaging_provided") or []
    if imaging:
        meta_by_id = {m["id"]: m for m in submission.get("attachment_meta", [])}
        for item in imaging:
            aid = item.get("attachment_id", "")
            if aid and aid not in meta_by_id:
                a = db.get_attachment(aid)
                if a:
                    meta_by_id[aid] = {"id": aid, "filename": a["filename"], "mime_type": a["mime_type"]}
        submission["_imaging_meta_by_id"] = meta_by_id

    meta = case.get("metadata", {})
    return CaseStatusResponse(
        case_id=case["case_id"],
        trace_id=case["trace_id"],
        status=case["status"],
        phase=case.get("phase"),
        scene=case["scene"],
        final_output=final_output,
        metadata=meta,
        submission=submission,
        submitted_at=case.get("submitted_at"),
        completed_at=case.get("completed_at"),
        stage_info=meta.get("stage_info", {}),
        error_msg=case.get("error_msg"),
    )


async def _run_orchestration(case_id: str, trace_id: str, scene: str, case_payload: dict):
    """Background task: run v2 orchestrator and write result back to DB."""
    try:
        from orchestrator.v2_orchestrator import run_v2
        final_output = await run_v2(case_id, trace_id, scene, case_payload, db)

        final_status = "done"

        db.update_case_status(
            case_id, final_status,
            final_output=final_output,
            metadata={
                "voice_mode_applied": final_output.get("voice_mode_applied"),
                "hrw_voice_violations": final_output.get("hrw_voice_violations", []),
                "v4_phase1": final_output.get("v4_phase1"),
                "v4_phase2": final_output.get("v4_phase2"),
                "v4_shengang_subtype": final_output.get("v4_shengang_subtype"),
                "v4_treatment_advisory": final_output.get("v4_treatment_advisory"),
                "v4_diagnosis_first": final_output.get("v4_diagnosis_first"),
                "v4_diagnosis_first_training": final_output.get("v4_diagnosis_first_training"),
                "v4_reasoning_output_projection": final_output.get("v4_reasoning_output_projection"),
                "v4_reasoning_workspace": final_output.get("v4_reasoning_workspace"),
                "v4_reasoning_doctor_trace": final_output.get("v4_reasoning_doctor_trace"),
                "v4_reasoning_patient_summary": final_output.get("v4_reasoning_patient_summary"),
                "v4_reasoning_training": final_output.get("v4_reasoning_training"),
                "v4_final_report_compliance": final_output.get("v4_final_report_compliance"),
            },
        )
    except asyncio.CancelledError:
        db.update_case_status(case_id, "aborted", error_msg="Aborted by user")
        raise
    except Exception as exc:
        db.update_case_status(case_id, "failed", error_msg=str(exc))
    finally:
        _running_tasks.pop(case_id, None)
        _drain_queue()


@router.get("", response_model=list[CaseStatusResponse])
async def list_cases(
    scene: Optional[str] = None,
    limit: int = 50,
    user: dict = Depends(auth.get_current_user),
):
    submitted_by = None if user["role"] == "admin" else user["username"]
    cases = db.list_cases(submitted_by=submitted_by, scene=scene, limit=limit)
    return [_build_status_response(c) for c in cases]


@router.post("", response_model=CaseSubmitResponse, status_code=202)
async def submit_case(
    body: Union[Scene1CaseSubmit, Scene3CaseSubmit],
    user: dict = Depends(auth.get_current_user),
):
    scene = body.scene
    case_payload = body.model_dump()

    # Change 20: pre-Phase-A sufficiency gate (saves 5-agent cost for incomplete submissions)
    suf = _check_sufficiency(case_payload, scene)
    if not suf["ok"]:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "needs_more_info",
                "message": suf["message"],
                "missing_fields": suf["missing"],
                "tmd_urgent": suf["tmd_urgent"],
            },
        )

    case_id, trace_id = db.create_case(
        submitted_by=user["username"],
        scene=scene,
        case_payload=case_payload,
    )

    active = sum(1 for t in _running_tasks.values() if not t.done())
    if active >= _MAX_CONCURRENT:
        db.update_case_status(case_id, "queued")
        _case_queue.append((case_id, trace_id, scene, case_payload))
        return CaseSubmitResponse(case_id=case_id, trace_id=trace_id, status="queued")

    db.update_case_status(case_id, "processing", phase="A")
    task = asyncio.create_task(
        _run_orchestration(case_id, trace_id, scene, case_payload)
    )
    _running_tasks[case_id] = task
    return CaseSubmitResponse(case_id=case_id, trace_id=trace_id, status="processing")


@router.get("/{case_id}", response_model=CaseStatusResponse)
async def get_case(
    case_id: str,
    user: dict = Depends(auth.get_current_user),
):
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # RBAC: non-admin can only see their own cases
    if user["role"] != "admin" and case["submitted_by"] != user["username"]:
        raise HTTPException(status_code=403, detail="Access denied")

    return _build_status_response(case)


@router.get("/{case_id}/audit")
async def get_case_audit(
    case_id: str,
    user: dict = Depends(auth.get_current_user),
):
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if user["role"] != "admin" and case["submitted_by"] != user["username"]:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        _scripts = str(config.ORCHESTRATOR_SCRIPTS)
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        import audit_log
        entries = audit_log.query(case_id=case_id)
    except Exception:
        entries = []

    return {
        "case_id": case_id,
        "trace_id": case["trace_id"],
        "entries": entries,
        "total": len(entries),
    }


@router.delete("/{case_id}", status_code=200)
async def delete_case(
    case_id: str,
    user: dict = Depends(auth.get_current_user),
):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    attachment_records = db.delete_case(case_id)
    if attachment_records is None:
        raise HTTPException(status_code=404, detail="Case not found")

    files_deleted = 0
    for record in attachment_records:
        try:
            Path(record["storage_path"]).unlink(missing_ok=True)
            files_deleted += 1
        except Exception:
            pass

    # Audit log
    try:
        _scripts = str(config.ORCHESTRATOR_SCRIPTS)
        if _scripts not in sys.path:
            sys.path.insert(0, _scripts)
        import audit_log, uuid as _uuid
        audit_log.write_entry({
            "audit_id": str(_uuid.uuid4()),
            "trace_id": str(_uuid.uuid4()),
            "case_id": case_id,
            "phase": "admin",
            "from": user["username"],
            "to": "db",
            "msg_type": "case_deleted",
            "msg_id": str(_uuid.uuid4()),
            "metadata": {
                "attachments_deleted": [r["attachment_id"] for r in attachment_records],
                "files_deleted": files_deleted,
            },
        })
    except Exception:
        pass

    return {
        "case_id": case_id,
        "deleted": True,
        "attachments_deleted": len(attachment_records),
        "files_deleted": files_deleted,
    }


@router.post("/{case_id}/retry", response_model=CaseSubmitResponse, status_code=202)
async def retry_case(
    case_id: str,
    user: dict = Depends(auth.get_current_user),
):
    if user["role"] not in ("admin", "patient_team"):
        raise HTTPException(status_code=403, detail="Not authorized")
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case["status"] not in ("failed", "error", "escalated"):
        raise HTTPException(status_code=409, detail=f"Cannot retry case in status: {case['status']}")
    if case_id in _running_tasks:
        raise HTTPException(status_code=409, detail="Case already running")

    db.reset_for_retry(case_id)

    active = sum(1 for t in _running_tasks.values() if not t.done())
    if active >= _MAX_CONCURRENT:
        db.update_case_status(case_id, "queued")
        _case_queue.append((case_id, case["trace_id"], case["scene"], case["case_payload"]))
        return CaseSubmitResponse(case_id=case_id, trace_id=case["trace_id"], status="queued")

    task = asyncio.create_task(
        _run_orchestration(case_id, case["trace_id"], case["scene"], case["case_payload"])
    )
    _running_tasks[case_id] = task
    return CaseSubmitResponse(case_id=case_id, trace_id=case["trace_id"], status="processing")


@router.post("/{case_id}/abort", status_code=200)
async def abort_case(
    case_id: str,
    user: dict = Depends(auth.get_current_user),
):
    if user["role"] not in ("admin", "patient_team"):
        raise HTTPException(status_code=403, detail="Not authorized")
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case["status"] not in ("pending", "processing", "queued"):
        raise HTTPException(status_code=409, detail=f"Cannot abort case in status: {case['status']}")

    task = _running_tasks.get(case_id)
    if task:
        task.cancel()
        # CancelledError handler in _run_orchestration writes "aborted" to DB
    else:
        # Remove from queue if still waiting
        _case_queue[:] = [item for item in _case_queue if item[0] != case_id]
        db.update_case_status(case_id, "aborted", error_msg="Aborted by user")

    return {"case_id": case_id, "status": "aborted"}


@router.post("/{case_id}/escalate", status_code=200)
async def escalate_case(
    case_id: str,
    reason: str = "",
    user: dict = Depends(auth.get_current_user),
):
    case = db.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    db.update_case_status(
        case_id, "escalated",
        metadata={"escalation_reason": reason, "escalated_by": user["username"]},
    )
    return {"case_id": case_id, "status": "escalated", "reason": reason}
