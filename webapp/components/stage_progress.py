"""STAGE_INFO progress rendering component (A6 invariant: zero LLM calls).

Renders current stage label, progress bar, ETA estimate, and completed stage history.
"""

from datetime import datetime, timezone
from typing import Optional

import streamlit as st

# Must mirror backend/orchestrator/stage_info.py
STAGE_KEYS = [
    "stage_0_sufficiency_gate",
    "stage_A_initial_reader",
    "stage_B_kb_retrieve",
    "stage_C_senior_clinician",
    "stage_D_critic",
    "stage_E_hrw",
    "stage_F_format",
]

STAGE_LABELS = {
    "stage_0_sufficiency_gate": "信息充分度检查",
    "stage_A_initial_reader": "视觉初判",
    "stage_B_kb_retrieve": "知识库检索",
    "stage_C_senior_clinician": "临床综合判断",
    "stage_D_critic": "独立审核",
    "stage_E_hrw": "硬规则校验",
    "stage_F_format": "格式化输出",
}

# Median latency estimates in seconds (for ETA)
STAGE_ETA_SEC = {
    "stage_0_sufficiency_gate": 2,
    "stage_A_initial_reader": 25,
    "stage_B_kb_retrieve": 30,
    "stage_C_senior_clinician": 45,
    "stage_D_critic": 35,
    "stage_E_hrw": 5,
    "stage_F_format": 5,
}


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def render_stage_progress(stage_info: dict, simple: bool = False):
    """Render STAGE_INFO progress inline (for Case History processing section).

    simple=True: sidebar mode — only progress bar + percentage, no labels or history.
    """
    if not stage_info:
        st.caption("处理中…")
        return

    now = datetime.now(timezone.utc)

    completed = [k for k in STAGE_KEYS if stage_info.get(k, {}).get("status") == "completed"]
    running_key = next((k for k in STAGE_KEYS if stage_info.get(k, {}).get("status") == "running"), None)

    total_stages = len(STAGE_KEYS)
    done_count = len(completed)
    progress_frac = min(done_count / total_stages, 1.0)

    if simple:
        st.progress(progress_frac, text=f"{int(progress_frac * 100)}%")
        return

    if running_key:
        label = STAGE_LABELS.get(running_key, running_key)
        eta = _compute_eta(stage_info, running_key, now)
        eta_str = f"约 {eta}s" if eta and eta > 0 else ""
        progress_text = f"⏳ {label}" + (f"（ETA {eta_str}）" if eta_str else "")
    elif completed:
        last = completed[-1]
        progress_text = f"✅ {STAGE_LABELS.get(last, last)}"
    else:
        progress_text = "等待开始…"

    st.progress(progress_frac, text=progress_text)

    # Completed stage history
    if completed:
        history_parts = []
        for k in completed:
            info = stage_info[k]
            latency = info.get("latency_ms")
            lat_str = f" {latency // 1000}s" if latency and latency > 1000 else ""
            history_parts.append(f"✅ {STAGE_LABELS.get(k, k)}{lat_str}")
        st.caption(" → ".join(history_parts))


def render_active_sidebar(cases: list):
    """Render active case progress in sidebar. Call inside `with st.sidebar:` block."""
    active = [c for c in cases if c.get("status") in ("pending", "processing")]
    queued = [c for c in cases if c.get("status") == "queued"]
    if not active and not queued:
        return

    st.divider()
    st.caption("📡 诊断进度")
    for c in active[:3]:
        cid = c.get("case_id", "")
        st.caption(f"案例 #{cid[:8]}")
        render_stage_progress(c.get("stage_info") or {}, simple=True)
    if queued:
        st.caption(f"🕐 排队中: {len(queued)} 案")


def _compute_eta(stage_info: dict, running_key: str, now: datetime) -> Optional[int]:
    """Estimate remaining seconds for the current running stage."""
    info = stage_info.get(running_key, {})
    started = _parse_iso(info.get("started_at"))
    if not started:
        return STAGE_ETA_SEC.get(running_key)
    elapsed = int((now - started).total_seconds())
    median = STAGE_ETA_SEC.get(running_key, 30)
    remaining = median - elapsed
    return max(remaining, 1) if remaining > 0 else None
