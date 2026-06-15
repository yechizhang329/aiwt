import sqlite3
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

import config

DB_PATH = config.DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    submitted_by     TEXT NOT NULL,
    submitted_at     TEXT NOT NULL,
    patient_age      INTEGER NOT NULL,
    patient_gender   TEXT NOT NULL,
    chief_complaint  TEXT NOT NULL,
    attachment_paths TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'pending',
    dm_msg_id        TEXT,
    cowork_id        TEXT,
    clinic_msg_id    TEXT,
    confidence       TEXT,
    sufficiency      TEXT,
    emergency_level  TEXT,
    sufficiency_msg  TEXT,
    layer1_output    TEXT,
    layer2_output    TEXT,
    completed_at     TEXT,
    error_msg        TEXT
);
"""

# status: pending | processing | sufficiency | done | failed


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _conn() as con:
        con.executescript(_SCHEMA)
        # Add new columns to existing DBs (idempotent)
        for col, typedef in [
            ("dm_msg_id",       "TEXT"),
            ("confidence",      "TEXT"),
            ("sufficiency",     "TEXT"),
            ("emergency_level", "TEXT"),
            ("sufficiency_msg", "TEXT"),
            ("received_at",     "TEXT"),
            ("estimated_path",  "TEXT"),
        ]:
            try:
                con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists


def create_job(submitted_by: str, age: int, gender: str,
               chief_complaint: str, attachment_paths: list) -> str:
    job_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            """INSERT INTO jobs
               (job_id, submitted_by, submitted_at, patient_age, patient_gender,
                chief_complaint, attachment_paths)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, submitted_by, now, age, gender,
             chief_complaint, json.dumps(attachment_paths)),
        )
    return job_id


def get_job(job_id: str):
    with _conn() as con:
        row = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(submitted_by=None):
    with _conn() as con:
        if submitted_by:
            rows = con.execute(
                "SELECT * FROM jobs WHERE submitted_by=? ORDER BY submitted_at DESC",
                (submitted_by,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM jobs ORDER BY submitted_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def set_job_received(job_id: str, estimated_path: str):
    """Mark job as acked by DentistWang with estimated processing path."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET received_at=?, estimated_path=? WHERE job_id=?",
            (now, estimated_path, job_id),
        )


def set_dm_msg_id(job_id: str, dm_msg_id: str):
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET dm_msg_id=?, status='processing' WHERE job_id=?",
            (dm_msg_id, job_id),
        )


def set_processing(job_id: str, dm_msg_id: str = None):
    """Mark job as processing; resets submitted_at so timeout is fresh."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE jobs SET status='processing', dm_msg_id=?, error_msg=NULL,
               submitted_at=? WHERE job_id=?""",
            (dm_msg_id, now, job_id),
        )


def update_job_done(job_id: str, cowork_id: str, clinic_msg_id: str,
                    confidence: str, sufficiency: str, emergency_level: str,
                    layer1: str, layer2: str):
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE jobs SET
               status='done', cowork_id=?, clinic_msg_id=?,
               confidence=?, sufficiency=?, emergency_level=?,
               layer1_output=?, layer2_output=?, completed_at=?
               WHERE job_id=?""",
            (cowork_id, clinic_msg_id, confidence, sufficiency, emergency_level,
             layer1, layer2, now, job_id),
        )


def update_job_sufficiency(job_id: str, sufficiency_data: dict):
    """Store structured sufficiency check data as JSON."""
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET status='sufficiency', sufficiency_msg=? WHERE job_id=?",
            (json.dumps(sufficiency_data), job_id),
        )


def update_job_failed(job_id: str, error_msg: str):
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET status='failed', error_msg=? WHERE job_id=?",
            (error_msg, job_id),
        )


def delete_job(job_id: str):
    with _conn() as con:
        con.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))


def requeue_job(job_id: str):
    """After supplement submitted, move back to processing."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE jobs SET status='processing', sufficiency_msg=NULL, submitted_at=? WHERE job_id=?",
            (now, job_id),
        )


def timeout_stale_jobs(timeout_minutes: int) -> int:
    """
    Mark processing/sufficiency jobs as 'timeout' if submitted_at is older
    than timeout_minutes. Returns count of jobs timed out.
    """
    cutoff = datetime.utcnow().isoformat()
    with _conn() as con:
        # SQLite datetime arithmetic via strftime
        result = con.execute(
            """UPDATE jobs SET status='timeout',
               error_msg='AI 超时未响应（超过 {} 分钟），请重新提交或联系管理员。'
               WHERE status = 'processing'
               AND datetime(submitted_at) < datetime('now', '-{} minutes')
               RETURNING job_id""".format(timeout_minutes, timeout_minutes)
        )
        return len(result.fetchall())
