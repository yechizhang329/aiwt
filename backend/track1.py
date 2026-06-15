"""Track 1 clinical KB — L1 convergence graph schema (DDL + GBrain1DB class).

Schema invariants (DW-spec, KBadvisor T1-1/T1-4 signed):
- node_type: subtype / indicator / cluster (convergence-signature, NOT decision-tree)
- edge_type: supports / masked-by / corrected-from / instance-of
- weight_modifier: REAL NULL — three-state: no-edge ≠ pending_calibration ≠ calibrated (NULL≠0)
- calibration_status: 'pending_calibration' | 'calibrated' | 'disabled'
- condition_on_dst: BOOL DEFAULT TRUE — per-case conditional (masked-by activates only when compensation node present in current case, NOT static suppression)
- Bi-temporal: valid_from / valid_to (invalidate-not-discard)
- Zero FK to cases.db / gbrain.db — Track 1 is read-only from hot path
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

_NODE_TYPES = ("subtype", "indicator", "cluster", "masked_axis")

_EDGE_TYPES = ("supports", "masked-by", "corrected-from", "instance-of", "gated-by")

_CALIBRATION_STATUSES = ("pending_calibration", "calibrated", "disabled")

_PAGE_TYPES = ("subtype-def", "decision-rule", "case", "reasoning-trace", "correction-event", "imaging-asset")

_DATA_CLASSES = ("teaching", "patient_ref")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    id                  TEXT PRIMARY KEY,
    node_type           TEXT NOT NULL CHECK(node_type IN ('subtype', 'indicator', 'cluster', 'masked_axis')),
    label               TEXT NOT NULL,
    face_type           TEXT,
    payload             TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    valid_from          TEXT,
    valid_to            TEXT
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id                  TEXT PRIMARY KEY,
    src_id              TEXT NOT NULL,
    dst_id              TEXT NOT NULL,
    edge_type           TEXT NOT NULL CHECK(edge_type IN ('supports', 'masked-by', 'corrected-from', 'instance-of', 'gated-by')),
    weight_modifier     REAL,
    calibration_status  TEXT NOT NULL DEFAULT 'pending_calibration'
                        CHECK(calibration_status IN ('pending_calibration', 'calibrated', 'disabled')),
    condition_on_dst    INTEGER NOT NULL DEFAULT 1,
    provenance          TEXT,
    payload             TEXT NOT NULL DEFAULT '{}',
    valid_from          TEXT NOT NULL,
    valid_to            TEXT,
    clinician_signed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_t1_edges_src   ON graph_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_t1_edges_dst   ON graph_edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_t1_edges_type  ON graph_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_t1_nodes_face  ON graph_nodes(face_type);

-- L2 wiki pages (T1-3 hybrid retrieval layer)
-- Hard split: data_class ∈ {teaching, patient_ref}
-- patient_ref: case_ref only — no PII, no raw-image bytes
-- l1_anchor: mandatory FK to graph_nodes.id (no orphan pages)
CREATE TABLE IF NOT EXISTS pages (
    id          TEXT PRIMARY KEY,
    page_type   TEXT NOT NULL CHECK(page_type IN ('subtype-def', 'decision-rule', 'case', 'reasoning-trace', 'correction-event', 'imaging-asset')),
    l1_anchor   TEXT NOT NULL,
    data_class  TEXT NOT NULL CHECK(data_class IN ('teaching', 'patient_ref')),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    provenance          TEXT,
    calibration_status  TEXT,
    valid_from          TEXT NOT NULL,
    valid_to            TEXT
);

CREATE INDEX IF NOT EXISTS idx_pages_anchor ON pages(l1_anchor);
CREATE INDEX IF NOT EXISTS idx_pages_type   ON pages(page_type);

-- FTS5 index for BM25 full-text search over page title + body
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    title, body, content=pages, content_rowid=rowid
);
"""

_PAGES_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body);
    INSERT INTO pages_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
"""


class GBrain1DB:
    """Track 1 clinical KB graph. Read-only from hot path; write only via KB curation workflow."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        with self._conn() as con:
            con.executescript(_SCHEMA)
            con.executescript(_PAGES_FTS_TRIGGERS)
            # IX-2.7: add clinician_signed_at if upgrading from earlier schema (no-op on new DBs)
            try:
                con.execute("ALTER TABLE graph_edges ADD COLUMN clinician_signed_at TEXT")
            except Exception:
                pass  # column already exists
            # pages.calibration_status: surface tier for C-c filtering (tagged 太上老君 c7ae5b5c)
            try:
                con.execute("ALTER TABLE pages ADD COLUMN calibration_status TEXT")
            except Exception:
                pass  # column already exists

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def add_node(self, node_type: str, label: str, face_type: Optional[str] = None,
                 payload: Optional[dict] = None) -> str:
        node_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                "INSERT INTO graph_nodes (id, node_type, label, face_type, payload, created_at, valid_from)"
                " VALUES (?,?,?,?,?,?,?)",
                (node_id, node_type, label, face_type, json.dumps(payload or {}), now, now),
            )
        return node_id

    def get_edge_by_id(self, edge_id: str) -> Optional[dict]:
        """Return active edge by canonical ID, or None if not found / invalidated."""
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM graph_edges WHERE id=? AND valid_to IS NULL", (edge_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def add_edge(self, src_id: str, dst_id: str, edge_type: str,
                 calibration_status: str = "pending_calibration",
                 weight_modifier: Optional[float] = None,
                 condition_on_dst: bool = True,
                 provenance: Optional[str] = None,
                 payload: Optional[dict] = None) -> str:
        edge_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                """INSERT INTO graph_edges
                   (id, src_id, dst_id, edge_type, weight_modifier, calibration_status,
                    condition_on_dst, provenance, payload, valid_from)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (edge_id, src_id, dst_id, edge_type,
                 weight_modifier,  # NULL = pending calibration; never coalesce to 0
                 calibration_status,
                 1 if condition_on_dst else 0,
                 provenance,
                 json.dumps(payload or {}),
                 now),
            )
        return edge_id

    def invalidate_edge(self, edge_id: str):
        """Bi-temporal invalidation: set valid_to. Do not delete."""
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                "UPDATE graph_edges SET valid_to=? WHERE id=? AND valid_to IS NULL",
                (now, edge_id),
            )

    def clinician_sign_edge(self, edge_id: str, clinician_msg_id: str) -> None:
        """IX-2.7: set clinician_signed_at on a calibrated active edge.

        This is the ONLY write path for clinician_signed_at (W7 frozen).
        Not reachable from seed scripts or bulk calibration_status updates.
        Only callable from explicit Walter-review workflow with a verifiable msg_id.

        Raises ValueError if edge is not calibrated + active.
        """
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            row = con.execute(
                "SELECT id, calibration_status FROM graph_edges"
                " WHERE id=? AND valid_to IS NULL",
                (edge_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"clinician_sign_edge: edge {edge_id!r} not found or invalidated")
            if row["calibration_status"] != "calibrated":
                raise ValueError(
                    f"clinician_sign_edge: edge {edge_id!r} is {row['calibration_status']!r},"
                    " must be 'calibrated' before clinician sign-off"
                )
            con.execute(
                "UPDATE graph_edges SET clinician_signed_at=?"
                " WHERE id=? AND valid_to IS NULL",
                (f"{now}|msg={clinician_msg_id}", edge_id),
            )

    def get_nodes(self, node_type: Optional[str] = None, face_type: Optional[str] = None) -> list[dict]:
        clauses, params = [], []
        if node_type:
            clauses.append("node_type=?")
            params.append(node_type)
        if face_type:
            clauses.append("face_type=?")
            params.append(face_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM graph_nodes {where} ORDER BY created_at", params
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_edges(self, src_id: Optional[str] = None, dst_id: Optional[str] = None,
                  edge_type: Optional[str] = None,
                  calibration_status: Optional[str] = None,
                  active_only: bool = True) -> list[dict]:
        clauses, params = [], []
        if src_id:
            clauses.append("src_id=?")
            params.append(src_id)
        if dst_id:
            clauses.append("dst_id=?")
            params.append(dst_id)
        if edge_type:
            clauses.append("edge_type=?")
            params.append(edge_type)
        if calibration_status:
            clauses.append("calibration_status=?")
            params.append(calibration_status)
        if active_only:
            clauses.append("valid_to IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM graph_edges {where} ORDER BY valid_from", params
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_masked_by_edges(self, dst_id: str, active_only: bool = True) -> list[dict]:
        """Return all masked-by edges whose dst_id matches (i.e. edges that mask a given indicator/conclusion)."""
        return self.get_edges(dst_id=dst_id, edge_type="masked-by", active_only=active_only)

    # --- L3 hybrid retrieval ---

    def add_page(self, page_type: str, l1_anchor: str, data_class: str,
                 title: str, body: str = "",
                 provenance: Optional[str] = None,
                 calibration_status: Optional[str] = None) -> str:
        page_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                """INSERT INTO pages (id, page_type, l1_anchor, data_class, title, body,
                                      provenance, calibration_status, valid_from)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (page_id, page_type, l1_anchor, data_class, title, body,
                 provenance, calibration_status, now),
            )
        return page_id

    def search_bm25(self, query: str, limit: int = 5) -> list[dict]:
        """BM25 full-text search over page title + body via FTS5."""
        with self._conn() as con:
            rows = con.execute(
                """SELECT p.*, rank FROM pages p
                   JOIN pages_fts f ON p.rowid = f.rowid
                   WHERE pages_fts MATCH ? AND p.valid_to IS NULL
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_pages_by_l1_anchor(self, l1_anchor: str, active_only: bool = True) -> list[dict]:
        clauses = ["l1_anchor=?"]
        params: list = [l1_anchor]
        if active_only:
            clauses.append("valid_to IS NULL")
        where = "WHERE " + " AND ".join(clauses)
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM pages {where} ORDER BY valid_from", params
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def hybrid_search(self, query: str, l1_anchor: Optional[str] = None,
                      bm25_limit: int = 10, top_k: int = 5) -> list[dict]:
        """RRF fusion: BM25 + graph-anchor retrieval. Vector search = TODO stub.

        Scoped to L2 namespace + subtype coords. NOT in synthesis path.
        """
        bm25_results = self.search_bm25(query, limit=bm25_limit)
        graph_results = self.get_pages_by_l1_anchor(l1_anchor) if l1_anchor else []

        # Build RRF scores (k=60 per standard RRF paper)
        k = 60
        scores: dict[str, float] = {}
        page_map: dict[str, dict] = {}

        for rank, page in enumerate(bm25_results):
            pid = page["id"]
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
            page_map[pid] = page

        for rank, page in enumerate(graph_results):
            pid = page["id"]
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
            page_map.setdefault(pid, page)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [page_map[pid] for pid, _ in ranked[:top_k]]


def falsification_check(db: "GBrain1DB", edge_ids: list) -> list:
    """T1-4 hot-path edge lookup for IX state machine.

    Returns per-edge status (IX-2.7 4-state enum):
      "present"           — calibrated + clinician_signed_at IS NOT NULL
      "calibrated_absent" — calibrated + clinician_signed_at IS NULL (door-3 gate NOT YET BUILT;
                            caller must treat as "unknown" until door-3 Walter-provenance lock lands)
      "unknown"           — edge found but pending_calibration
      "not_found"         — edge missing from KB, invalidated (valid_to set), or disabled

    Activation-direction semantics (DW + KBadvisor T1-1 §2):
      supports  → fires on src activation (indicator_value node)
      masked-by → fires on dst activation (condition node)
    Both directions are returned via edge_type + src_id/dst_id so the caller
    can determine which endpoint to check against case indicators.

    CRITICAL: weight_modifier is returned as-is (NULL for all 档1 rows).
    NULL ≠ 0: never coalesce (§5.2 catch — NULL = pending_calibration,
    not zero-weight; coalescing re-enables unconditional ANB trust, the original error).
    """
    results = []
    for eid in edge_ids:
        row = db.get_edge_by_id(eid)
        if row is None:
            results.append({"edge_id": eid, "status": "not_found", "evidence": None})
        elif row["calibration_status"] == "pending_calibration":
            results.append({"edge_id": eid, "status": "unknown", "evidence": None})
        elif row["calibration_status"] == "disabled":
            results.append({"edge_id": eid, "status": "not_found", "evidence": None})
        elif row["calibration_status"] == "calibrated" and row.get("clinician_signed_at"):
            results.append({
                "edge_id": eid,
                "status": "present",
                "edge_type": row["edge_type"],
                "src_id": row["src_id"],
                "dst_id": row["dst_id"],
                "weight_modifier": row["weight_modifier"],  # NULL for all 档1 rows
                "forced_action": row.get("payload", {}).get("forced_action"),
            })
        else:
            # calibrated but clinician_signed_at IS NULL — scaffold only; door-3 not built
            results.append({
                "edge_id": eid,
                "status": "calibrated_absent",
                "edge_type": row["edge_type"],
                "src_id": row["src_id"],
                "dst_id": row["dst_id"],
                "weight_modifier": row["weight_modifier"],
                "forced_action": row.get("payload", {}).get("forced_action"),
            })
    return results


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "payload" in d:
        d["payload"] = json.loads(d["payload"] or "{}")
    if "condition_on_dst" in d:
        d["condition_on_dst"] = bool(d["condition_on_dst"])
    return d
