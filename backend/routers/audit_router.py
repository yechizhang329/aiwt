"""
Audit query endpoints — 太上老君 interface.
Sprint 3: wired to audit_log.query() + detect_anomalies().
RBAC: admin + audit_agent roles only.
"""
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

import auth
import config
import db
from models import AnomalyMetrics

# audit_log.py lives in DentistWang's scripts directory
_scripts = str(config.ORCHESTRATOR_SCRIPTS)
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import audit_log  # noqa: E402

router = APIRouter(prefix="/v1/audit", tags=["audit"])


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid since format: {since!r}. Use ISO 8601.")


@router.get("/query")
async def audit_query(
    case_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    phase: Optional[str] = None,
    msg_type: Optional[str] = None,
    agent: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
    user: dict = Depends(auth.require_admin_or_audit_agent),
):
    since_dt = _parse_since(since)
    entries = audit_log.query(
        case_id=case_id,
        trace_id=trace_id,
        msg_type=msg_type,
        phase=phase,
        agent=agent,
        since=since_dt,
        limit=limit,
    )
    return {
        "entries": entries,
        "total": len(entries),
        "filters": {
            "case_id": case_id, "trace_id": trace_id, "phase": phase,
            "msg_type": msg_type, "agent": agent, "since": since,
        },
    }


@router.get("/kpi")
async def audit_kpi(
    range: str = "today",
    user: dict = Depends(auth.require_admin_or_audit_agent),
):
    """Change 28: aggregate KPI metrics from cases DB. range=today|7d."""
    now = datetime.utcnow()
    if range == "today":
        since_dt = now - timedelta(hours=24)
    elif range == "7d":
        since_dt = now - timedelta(days=7)
    else:
        raise HTTPException(status_code=422, detail="range must be 'today' or '7d'")
    since_iso = since_dt.isoformat()

    cases_in_range = db.list_cases(since=since_iso, limit=1000)

    by_status: dict = {}
    by_scene: dict = {}
    latencies: list = []
    mode_counts = {"A": 0, "B": 0, "unknown": 0}
    escalation_reasons: dict = {}
    retries_total = 0
    latency_per_phase_all: dict = {}
    daily_counts: dict = {}
    case_rows = []

    for c in cases_in_range:
        status = c.get("status", "unknown")
        scene = c.get("scene", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_scene[scene] = by_scene.get(scene, 0) + 1

        meta = c.get("metadata") or {}
        lat = meta.get("total_latency_ms")
        if lat:
            latencies.append(lat)

        phase_results = meta.get("phase_results") or {}
        mode = (phase_results.get("E") or {}).get("voice_mode_applied") or "unknown"
        if mode in ("A", "B"):
            mode_counts[mode] += 1
        elif status in ("done", "escalated"):
            mode_counts["unknown"] += 1

        for ph in ("A", "B", "C", "D", "E", "F"):
            ph_lat = (phase_results.get(ph) or {}).get("latency_ms")
            if ph_lat:
                latency_per_phase_all.setdefault(ph, []).append(ph_lat)

        retries_total += meta.get("retry_count", 0)

        if status == "escalated":
            reason = meta.get("escalation_reason") or "unknown"
            escalation_reasons[reason] = escalation_reasons.get(reason, 0) + 1

        day = (c.get("submitted_at") or "")[:10]
        if day:
            daily_counts[day] = daily_counts.get(day, 0) + 1

        case_rows.append({
            "case_id": c["case_id"],
            "scene": scene,
            "status": status,
            "submitted_at": c.get("submitted_at", ""),
            "completed_at": c.get("completed_at"),
            "latency_ms": lat,
            "retry_count": meta.get("retry_count", 0),
            "voice_mode": mode,
            "escalation_reason": meta.get("escalation_reason"),
        })

    def _percentile(lst, p):
        if not lst:
            return None
        s = sorted(lst)
        return s[min(int(len(s) * p / 100), len(s) - 1)]

    latency_stats = {}
    if latencies:
        latency_stats = {
            "p50": _percentile(latencies, 50),
            "p90": _percentile(latencies, 90),
            "p99": _percentile(latencies, 99),
            "avg": int(sum(latencies) / len(latencies)),
            "min": min(latencies),
            "max": max(latencies),
        }

    latency_per_phase = {
        ph: {
            "p50": _percentile(v, 50),
            "p90": _percentile(v, 90),
            "avg": int(sum(v) / len(v)) if v else None,
        }
        for ph, v in latency_per_phase_all.items()
    }

    return {
        "range": range,
        "since": since_iso,
        "total_cases": len(cases_in_range),
        "by_status": by_status,
        "by_scene": by_scene,
        "latency_ms": latency_stats,
        "latency_per_phase_ms": latency_per_phase,
        "voice_mode_distribution": mode_counts,
        "escalation_reasons": escalation_reasons,
        "total_retries": retries_total,
        "daily_counts": dict(sorted(daily_counts.items())),
        "cases": case_rows,
    }


@router.get("/anomalies", response_model=AnomalyMetrics)
async def audit_anomalies(
    since: Optional[str] = None,
    user: dict = Depends(auth.require_admin_or_audit_agent),
):
    since_dt = _parse_since(since)
    raw = audit_log.detect_anomalies(since=since_dt)
    return AnomalyMetrics(
        total_entries=raw.get("total_entries", 0),
        critic_disagreement_rate=raw.get("critic_disagreement_rate") or 0.0,
        wrapper_violation_rate=raw.get("wrapper_violation_rate") or 0.0,
        total_retries=raw.get("total_retries", 0),
        total_escalations=raw.get("total_escalations", 0),
        latency_p50_ms=raw.get("latency_p50_ms"),
        latency_p95_ms=raw.get("latency_p95_ms"),
        latency_p99_ms=raw.get("latency_p99_ms"),
    )
