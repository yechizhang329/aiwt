"""STAGE_INFO canonical definitions and DB write helpers.

A6 invariant (Tier 2 v0.7 §13): observability ≠ reasoning.
All functions here are pure code — zero LLM calls.
"""

from datetime import datetime, timezone
from typing import Optional

# Canonical stage keys (spec v1.2.1, 8-stage)
STAGE_KEYS = [
    "stage_0_sufficiency_gate",
    "stage_A_initial_reader",
    "stage_B_kb_retrieve",
    "stage_C_senior_clinician",
    "stage_D_critic",
    "stage_E_hrw",
    "stage_F_format",
    "stage_G_doctor_review",
]

# Human-readable labels (used in frontend progress UX)
STAGE_LABELS = {
    "stage_0_sufficiency_gate": "信息充分度检查",
    "stage_A_initial_reader": "视觉初判",
    "stage_B_kb_retrieve": "知识库检索",
    "stage_C_senior_clinician": "临床综合判断",
    "stage_D_critic": "独立审核",
    "stage_E_hrw": "硬规则校验",
    "stage_F_format": "格式化输出",
    "stage_G_doctor_review": "医生审核",
}

# Median latency estimates in seconds (for ETA progress bar)
STAGE_ETA_SEC = {
    "stage_0_sufficiency_gate": 2,
    "stage_A_initial_reader": 25,
    "stage_B_kb_retrieve": 30,
    "stage_C_senior_clinician": 45,
    "stage_D_critic": 35,
    "stage_E_hrw": 5,
    "stage_F_format": 5,
    "stage_G_doctor_review": 0,  # human gate — no ETA
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_running(stage_key: str) -> dict:
    """Return a stage_info value dict with status=running."""
    return {"status": "running", "started_at": _now_iso()}


def make_completed(stage_key: str, started_at: str, extra: Optional[dict] = None) -> dict:
    """Return a stage_info value dict with status=completed and latency_ms computed."""
    now = datetime.now(timezone.utc)
    try:
        start = datetime.fromisoformat(started_at)
        if start.tzinfo is None:
            from datetime import timezone as _tz
            start = start.replace(tzinfo=_tz.utc)
        latency_ms = int((now - start).total_seconds() * 1000)
    except Exception:
        latency_ms = None
    result = {"status": "completed", "started_at": started_at,
              "completed_at": _now_iso(), "latency_ms": latency_ms}
    if extra:
        result.update(extra)
    return result


def make_failed(stage_key: str, started_at: str, reason: str) -> dict:
    """Return a stage_info value dict with status=failed."""
    return {
        "status": "failed",
        "started_at": started_at,
        "completed_at": _now_iso(),
        "reason": reason,
    }
