"""SQLite case store for FastAPI backend."""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id      TEXT PRIMARY KEY,
    trace_id     TEXT NOT NULL,
    scene        TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    phase        TEXT,
    case_payload TEXT NOT NULL DEFAULT '{}',
    final_output TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}',
    error_msg    TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS attachments (
    attachment_id TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    mime_type     TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    uploaded_by   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    storage_path  TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with _conn() as con:
        con.executescript(_SCHEMA)


def create_case(submitted_by: str, scene: str, case_payload: dict) -> tuple[str, str]:
    case_id = str(uuid.uuid4())[:8]
    trace_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            """INSERT INTO cases (case_id, trace_id, scene, submitted_by, submitted_at, case_payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, trace_id, scene, submitted_by, now, json.dumps(case_payload)),
        )
    return case_id, trace_id


def get_case(case_id: str) -> Optional[dict]:
    with _conn() as con:
        row = con.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["case_payload"] = json.loads(d.get("case_payload") or "{}")
    d["final_output"] = json.loads(d.get("final_output") or "null")
    d["metadata"] = json.loads(d.get("metadata") or "{}")
    return d


def create_attachment(attachment_id: str, filename: str, mime_type: str,
                      size_bytes: int, uploaded_by: str, storage_path: str):
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            """INSERT INTO attachments
               (attachment_id, filename, mime_type, size_bytes, uploaded_by, created_at, storage_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (attachment_id, filename, mime_type, size_bytes, uploaded_by, now, storage_path),
        )


def get_attachment(attachment_id: str) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM attachments WHERE attachment_id=?", (attachment_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_attachment(attachment_id: str) -> bool:
    with _conn() as con:
        cur = con.execute("DELETE FROM attachments WHERE attachment_id=?", (attachment_id,))
    return cur.rowcount > 0


def list_cases(submitted_by: Optional[str] = None, scene: Optional[str] = None,
               status: Optional[str] = None, since: Optional[str] = None,
               limit: int = 50) -> list[dict]:
    clauses, params = [], []
    if submitted_by:
        clauses.append("submitted_by=?")
        params.append(submitted_by)
    if scene:
        clauses.append("scene=?")
        params.append(scene)
    if status:
        clauses.append("status=?")
        params.append(status)
    if since:
        clauses.append("submitted_at >= ?")
        params.append(since)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM cases {where} ORDER BY submitted_at DESC LIMIT ?", params
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["case_payload"] = json.loads(d.get("case_payload") or "{}")
        d["final_output"] = json.loads(d.get("final_output") or "null")
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        result.append(d)
    return result


def delete_case(case_id: str) -> Optional[list]:
    """Delete a case and all its attachments from DB. Returns list of attachment dicts (for physical file deletion) or None if case not found."""
    case = get_case(case_id)
    if not case:
        return None

    # Collect all attachment_ids referenced by this case
    payload = case.get("case_payload") or {}
    attachment_ids: list[str] = []

    # Scene 1: flat list
    for aid in (payload.get("attachment_ids") or []):
        if aid and aid not in attachment_ids:
            attachment_ids.append(aid)

    # Scene 3: imaging_provided[].attachment_id
    for item in (payload.get("imaging_provided") or []):
        if isinstance(item, dict):
            aid = item.get("attachment_id")
            if aid and aid not in attachment_ids:
                attachment_ids.append(aid)

    # Fetch attachment records before deleting
    attachment_records: list[dict] = []
    with _conn() as con:
        for aid in attachment_ids:
            row = con.execute(
                "SELECT * FROM attachments WHERE attachment_id=?", (aid,)
            ).fetchone()
            if row:
                attachment_records.append(dict(row))

    # Single transaction: delete attachments then the case
    with _conn() as con:
        for aid in attachment_ids:
            con.execute("DELETE FROM attachments WHERE attachment_id=?", (aid,))
        con.execute("DELETE FROM cases WHERE case_id=?", (case_id,))

    return attachment_records


def update_case_stage_info(case_id: str, key: str, value: dict):
    """Write one STAGE_INFO key into cases.metadata.stage_info (read-modify-write)."""
    with _conn() as con:
        row = con.execute("SELECT metadata FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            return
        meta = json.loads(row["metadata"] or "{}")
        if "stage_info" not in meta:
            meta["stage_info"] = {}
        meta["stage_info"][key] = value
        con.execute("UPDATE cases SET metadata=? WHERE case_id=?", (json.dumps(meta), case_id))


def update_case_status(case_id: str, status: str, phase: Optional[str] = None,
                       final_output: Optional[dict] = None,
                       metadata: Optional[dict] = None,
                       error_msg: Optional[str] = None):
    now = datetime.utcnow().isoformat() if status in ("done", "escalated", "failed", "awaiting_doctor_review", "aborted") else None
    with _conn() as con:
        if metadata is not None:
            # Merge: read current metadata, apply new keys on top (preserves stage_info from orchestration)
            row = con.execute("SELECT metadata FROM cases WHERE case_id=?", (case_id,)).fetchone()
            existing = json.loads((row["metadata"] if row else None) or "{}") if row else {}
            existing.update(metadata)
            merged_meta = json.dumps(existing)
        else:
            merged_meta = None
        con.execute(
            """UPDATE cases SET status=?, phase=?,
               final_output=COALESCE(?, final_output),
               metadata=COALESCE(?, metadata),
               error_msg=COALESCE(?, error_msg),
               completed_at=COALESCE(?, completed_at)
               WHERE case_id=?""",
            (
                status, phase,
                json.dumps(final_output) if final_output is not None else None,
                merged_meta,
                error_msg,
                now,
                case_id,
            ),
        )
        if status == "done":
            con.execute("UPDATE cases SET error_msg=NULL WHERE case_id=?", (case_id,))


def reset_for_retry(case_id: str):
    """Reset a failed/escalated case back to processing state for orchestration re-run."""
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE cases SET status='processing', phase='A', metadata=?, error_msg=NULL, completed_at=NULL, submitted_at=? WHERE case_id=?",
            (json.dumps({"stage_info": {}}), now, case_id),
        )
