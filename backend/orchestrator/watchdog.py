"""Per-stage timeout watchdog for v2 orchestrator.

Checks for cases stuck in a specific stage beyond STAGE_TIMEOUTS and escalates.
Runs as a background task alongside the main watchdog in main.py.
"""

import asyncio
from datetime import datetime, timezone, timedelta

from orchestrator.stage_info import STAGE_KEYS
from orchestrator.v2_orchestrator import STAGE_TIMEOUTS


async def run_stage_watchdog(db_mod, interval_sec: int = 60):
    """Periodically scan for cases with a stage stuck beyond its timeout."""
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await asyncio.to_thread(_check_stuck_stages, db_mod)
        except Exception:
            pass


def _check_stuck_stages(db_mod):
    """Escalate cases where any running stage exceeds its timeout."""
    now = datetime.now(timezone.utc)
    processing = db_mod.list_cases(status="processing", limit=100)
    for case in processing:
        meta = case.get("metadata") or {}
        stage_info = meta.get("stage_info") or {}
        for key, info in stage_info.items():
            if info.get("status") != "running":
                continue
            timeout_sec = STAGE_TIMEOUTS.get(key, 300)
            if timeout_sec == 0:
                continue
            try:
                started = datetime.fromisoformat(info["started_at"])
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if (now - started).total_seconds() > timeout_sec:
                db_mod.update_case_status(
                    case["case_id"],
                    "escalated",
                    error_msg=f"Stage {key} exceeded timeout ({timeout_sec}s)",
                    metadata={**meta, "escalation_reason": f"stage_timeout:{key}"},
                )
                break  # escalate once per case
