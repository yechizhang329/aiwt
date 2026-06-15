"""FastAPI router — Track 1 clinical KB (LLM-Wiki, L2 pages layer).

KC write chain: POST /track1/pages writes subtype-def / decision-rule pages.
Retrieval: GET /track1/search (BM25+graph hybrid), GET /track1/pages, GET /track1/nodes.

R-1 isolation: this module imports only track1.GBrain1DB, never gbrain.GBrainDB.
R-2 body-content gate: rejects embedded base64 image data in body/title.
data_class=patient_ref pages must contain only case_ref content (no PII, no raw images).
"""

import re
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

import config
from track1 import GBrain1DB

router = APIRouter(prefix="/track1", tags=["track1"])

_db: Optional[GBrain1DB] = None

_VALID_PAGE_TYPES = {"subtype-def", "decision-rule", "case", "reasoning-trace", "correction-event", "imaging-asset"}
_VALID_DATA_CLASSES = {"teaching", "patient_ref"}

# R-2: detect embedded raw-image blobs in page body/title
# Matches data-URI image prefix (data:image/ or data:application/) and
# long unbroken base64 sequences (≥500 chars) that indicate embedded binary.
_BASE64_DATA_URI = re.compile(r"data:(image|application)/[^;]+;base64,", re.IGNORECASE)
_LONG_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{500,}={0,2}")


def _r2_body_check(title: str, body: str, data_class: str) -> list[str]:
    """R-2 gate: return list of violations; empty = pass.

    Blocks embedded raw-image base64 in body/title for all data_class values.
    For patient_ref: also enforces structural reminder (not automatable without name list).
    """
    violations = []
    for field, text in (("title", title), ("body", body)):
        if _BASE64_DATA_URI.search(text):
            violations.append(f"R-2 violation: {field} contains embedded data-URI image (raw bytes forbidden)")
        if _LONG_BASE64_RUN.search(text):
            violations.append(f"R-2 violation: {field} contains long base64 run ≥500 chars (embedded binary forbidden)")
    if data_class == "patient_ref" and len(body) > 0:
        # Cannot automate PII grep without name list; enforce contractual reminder at write time.
        # Caller must assert: body contains only case_ref slugs (asymm_NN / concave_NN etc), no patient names.
        # This check is structural (not semantically sufficient) — de-id is KC's write-time responsibility.
        pass
    return violations


def get_db() -> GBrain1DB:
    global _db
    if _db is None:
        _db = GBrain1DB(config.TRACK1_DB_PATH)
    return _db


class PageCreate(BaseModel):
    page_type: str
    l1_anchor: str
    data_class: str
    title: str
    body: str = ""
    provenance: Optional[str] = None
    calibration_status: Optional[str] = None

    @field_validator("page_type")
    @classmethod
    def validate_page_type(cls, v):
        if v not in _VALID_PAGE_TYPES:
            raise ValueError(f"page_type must be one of {_VALID_PAGE_TYPES}")
        return v

    @field_validator("data_class")
    @classmethod
    def validate_data_class(cls, v):
        if v not in _VALID_DATA_CLASSES:
            raise ValueError(f"data_class must be one of {_VALID_DATA_CLASSES}")
        return v


@router.get("/nodes")
def list_nodes(node_type: Optional[str] = None, face_type: Optional[str] = None):
    """List L1 graph nodes. KC uses this to pick l1_anchor for page creation."""
    return get_db().get_nodes(node_type=node_type, face_type=face_type)


@router.get("/edges")
def list_edges(src_id: Optional[str] = None, dst_id: Optional[str] = None,
               edge_type: Optional[str] = None, calibration_status: Optional[str] = None,
               active_only: bool = True):
    """List L1 graph edges."""
    return get_db().get_edges(
        src_id=src_id, dst_id=dst_id, edge_type=edge_type,
        calibration_status=calibration_status, active_only=active_only,
    )


@router.post("/pages")
def create_page(body: PageCreate):
    """KC write chain: create a new L2 wiki page anchored to an L1 node."""
    db = get_db()
    # R-2: body-content gate (base64 image rejection)
    r2_violations = _r2_body_check(body.title, body.body, body.data_class)
    if r2_violations:
        raise HTTPException(status_code=422, detail={"r2_violations": r2_violations})
    # Validate l1_anchor exists
    nodes = db.get_nodes()
    valid_ids = {n["id"] for n in nodes}
    if body.l1_anchor not in valid_ids:
        raise HTTPException(status_code=400, detail=f"l1_anchor {body.l1_anchor!r} not found in graph_nodes")
    try:
        page_id = db.add_page(
            page_type=body.page_type,
            l1_anchor=body.l1_anchor,
            data_class=body.data_class,
            title=body.title,
            body=body.body,
            provenance=body.provenance,
            calibration_status=body.calibration_status,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": page_id}


@router.get("/pages")
def list_pages(l1_anchor: Optional[str] = None, page_type: Optional[str] = None,
               data_class: Optional[str] = None, active_only: bool = True):
    """List L2 wiki pages, optionally filtered."""
    db = get_db()
    if l1_anchor:
        pages = db.get_pages_by_l1_anchor(l1_anchor, active_only=active_only)
    else:
        import sqlite3
        clauses, params = [], []
        if page_type:
            clauses.append("page_type=?")
            params.append(page_type)
        if data_class:
            clauses.append("data_class=?")
            params.append(data_class)
        if active_only:
            clauses.append("valid_to IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with sqlite3.connect(config.TRACK1_DB_PATH) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"SELECT * FROM pages {where} ORDER BY valid_from", params).fetchall()
        import json
        pages = [{**dict(r)} for r in rows]
    return pages


@router.get("/search")
def search_pages(q: str, l1_anchor: Optional[str] = None, top_k: int = 5):
    """BM25 + graph-anchor hybrid search over L2 wiki pages."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="q must be non-empty")
    return get_db().hybrid_search(q, l1_anchor=l1_anchor, top_k=top_k)
