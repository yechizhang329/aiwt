"""
Background polling thread — checks pending/processing jobs every POLL_INTERVAL_SECONDS.
Reads the main DM channel with DentistWang once per cycle and matches responses by job_id.
Also enforces JOB_TIMEOUT_MINUTES for stale jobs.

Architecture note (2026-05-23): Slock DM threads are not replyable by the receiver.
DentistWang sends responses as top-level DM messages to @WebAppDev.
Matching is done by job_id field in the message body, not by thread ID.
"""

import threading
import time
import logging
from datetime import datetime
from typing import Optional

import config
import db
import slock_client

_thread: Optional[threading.Thread] = None
_logger = logging.getLogger("poller")

# job_id → set of escalation levels already sent (1=5min, 2=15min)
_escalated: dict = {}


def _escalate_if_needed(job: dict):
    """Ping DentistWang in channel if a processing job has been waiting too long."""
    if job["status"] != "processing":
        return
    # Once acked, DentistWang has confirmed receipt — stop escalating
    if job.get("received_at"):
        _escalated.pop(job["job_id"], None)
        return
    try:
        elapsed_min = (datetime.utcnow() -
                       datetime.fromisoformat(job["submitted_at"])
                       ).total_seconds() / 60
    except Exception:
        return

    job_id = job["job_id"]
    sent = _escalated.setdefault(job_id, set())

    def _ping(level: int, label: str):
        if level in sent:
            return
        sent.add(level)
        notify = config.NOTIFY_CHANNEL
        if not notify:
            return
        msg = (f"{config.DENTIST_HANDLE} 案例 #{job_id} 已等待 {label}，"
               f"尚未收到回复，请查收 DM。")
        slock_client._run(["message", "send", "--target", notify], stdin_text=msg)
        _logger.info("Escalation level %d sent for job %s", level, job_id)

    if elapsed_min >= 15:
        _ping(2, "15 分钟")
    elif elapsed_min >= 5:
        _ping(1, "5 分钟")


def _poll_once():
    timed_out = db.timeout_stale_jobs(config.JOB_TIMEOUT_MINUTES)
    if timed_out:
        _logger.info("Timed out %d stale jobs", timed_out)

    active_jobs = [j for j in db.list_jobs()
                   if j["status"] in ("processing", "sufficiency")]
    if not active_jobs:
        return

    # Escalation pings for long-waiting processing jobs
    for job in active_jobs:
        _escalate_if_needed(job)

    # Read the DM channel once for all active jobs
    raw = slock_client.read_dm_channel()
    if not raw:
        return

    for job in active_jobs:
        try:
            parsed = slock_client.parse_dm_for_job(raw, job["job_id"])
            if parsed is None:
                continue
            if parsed["type"] == "received" and not job.get("received_at"):
                db.set_job_received(job["job_id"], parsed.get("estimated_path", "full_pipeline"))
                # Cancel escalation pings — DentistWang has acked
                _escalated.pop(job["job_id"], None)
            elif parsed["type"] == "done":
                db.update_job_done(
                    job_id=job["job_id"],
                    cowork_id=parsed["cowork_id"],
                    clinic_msg_id=parsed["clinic_msg_id"],
                    confidence=parsed["confidence"],
                    sufficiency=parsed["sufficiency"],
                    emergency_level=parsed["emergency_level"],
                    layer1=parsed["layer1"],
                    layer2=parsed["layer2"],
                )
                _escalated.pop(job["job_id"], None)
            elif parsed["type"] == "sufficiency" and job["status"] != "sufficiency":
                db.update_job_sufficiency(job["job_id"], {
                    "cowork_id":         parsed.get("cowork_id", ""),
                    "sufficient_fields": parsed.get("sufficient_fields", []),
                    "missing_fields":    parsed.get("missing_fields", []),
                    "priority_order":    parsed.get("priority_order", []),
                })
                _escalated.pop(job["job_id"], None)
        except Exception as e:
            _logger.warning("Poll error for job %s: %s", job["job_id"], e)


def _loop():
    while True:
        try:
            _poll_once()
        except Exception as e:
            _logger.error("Poller loop error: %s", e)
        time.sleep(config.POLL_INTERVAL_SECONDS)


def start():
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="slock-poller", daemon=True)
    _thread.start()
