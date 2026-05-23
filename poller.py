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
from typing import Optional

import config
import db
import slock_client

_thread: Optional[threading.Thread] = None
_logger = logging.getLogger("poller")


def _poll_once():
    timed_out = db.timeout_stale_jobs(config.JOB_TIMEOUT_MINUTES)
    if timed_out:
        _logger.info("Timed out %d stale jobs", timed_out)

    active_jobs = [j for j in db.list_jobs()
                   if j["status"] in ("processing", "sufficiency")]
    if not active_jobs:
        return

    # Read the DM channel once for all active jobs
    raw = slock_client.read_dm_channel()
    if not raw:
        return

    for job in active_jobs:
        try:
            parsed = slock_client.parse_dm_for_job(raw, job["job_id"])
            if parsed is None:
                continue
            if parsed["type"] == "done":
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
            elif parsed["type"] == "sufficiency" and job["status"] != "sufficiency":
                db.update_job_sufficiency(job["job_id"], {
                    "cowork_id":         parsed.get("cowork_id", ""),
                    "sufficient_fields": parsed.get("sufficient_fields", []),
                    "missing_fields":    parsed.get("missing_fields", []),
                    "priority_order":    parsed.get("priority_order", []),
                })
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
