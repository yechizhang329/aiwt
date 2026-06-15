"""Track 2 governance brain — agent shared memory (GBrainDB).

Structural isolation invariants (CI-verifiable):
- Separate gbrain.db file, zero FK to cases.db
- node_type CHECK enum: only governance/coordination types
- No patient_id, case_id, image, measurement, or clinical columns

Bitemporal design (nodes + edges): valid_from/valid_to on both tables.
Retrieval defaults to active-only (valid_to IS NULL).
Superseded decisions are invalidated, not deleted — stale recovery prevention.
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

_NODE_TYPES = (
    "governance_decision",
    "handoff",
    "session_context",
    "agent_claim",
    "authorization",
)

_EDGE_TYPES = (
    "decided",
    "supersedes",
    "blocks",
    "authorized-by",
    "delegates-to",
    "references",
)

# Tables only — no indexes. Indexes are created after migration so they reference existing columns.
_TABLE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,
    node_type   TEXT NOT NULL CHECK(node_type IN {_NODE_TYPES!r}),
    label       TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{{}}',
    created_at  TEXT NOT NULL,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id          TEXT PRIMARY KEY,
    src_id      TEXT NOT NULL,
    dst_id      TEXT NOT NULL,
    edge_type   TEXT NOT NULL CHECK(edge_type IN {_EDGE_TYPES!r}),
    payload     TEXT NOT NULL DEFAULT '{{}}',
    valid_from  TEXT NOT NULL,
    valid_to    TEXT
);
"""

# Indexes created after migration so valid_from/valid_to exist on both old and new DBs.
_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_nodes_type  ON graph_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_valid ON graph_nodes(valid_to);
CREATE INDEX IF NOT EXISTS idx_edges_src   ON graph_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst   ON graph_edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_type  ON graph_edges(edge_type);
"""

# Migration sentinel: default valid_from for rows that pre-date the bitemporal schema.
_MIGRATION_EPOCH = "1970-01-01T00:00:00"

# R-2 payload guard: recursive key allowlist (DW 7dba3f6c final form).
# Allowlist > denylist rationale (same fail-safe principle as F-2 IN-4 whitelist):
#   - denylist requires enumerating every clinical key (treadmill); allowlist auto-rejects unknowns
#   - nested {"meta": {"case_id": ...}} bypasses denylist (meta ∉ deny) but allowlist rejects at
#     top level (meta ∉ allow), so denylist+recursive still has a bypass denylist alone cannot close
#   - values are NOT scanned: clinical prose in values is legitimate governance description
#     (KBadvisor governance_decision values mention "SNA", "ANB" as firewall docs); value-scan = misfire
# Residual (by design, non-automatable): value-level clinical prose → schema columns + IN-4 writer
# discipline + review control. R-2 = "recursive allowlist on key affordance" only.
# KBadvisor a6ba619e: 18 keys grounded from full-depth live content audit; 太上老君 3b002aa7: ratified.
# DW 7dba3f6c: allowlist form + wording locked as R-2 terminal state.
_R2_ALLOWED_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "act", "action", "agent", "blocking", "confidence", "convergence",
    "date", "evidence_layers", "lane", "next_owner", "poc", "priority",
    "red_line", "scope", "status", "track", "weave_status", "what",
})


def _r2_payload_check(payload: dict) -> list[str]:
    """R-2 content gate: return violations for any key not in governance allowlist.

    Recursive: walks every dict layer and list so clinical keys cannot bypass the gate
    by nesting inside an allowed container key (e.g. {"convergence": {"case_id": ...}}).
    Values are not scanned — clinical terms in value text are legitimate governance prose.
    """
    violations: list[str] = []

    def _walk(obj: object, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k not in _R2_ALLOWED_PAYLOAD_KEYS:
                    violations.append(
                        f"R-2 violation: payload key {k!r} at {path!r} is not in governance "
                        "allowlist — only pre-approved governance keys are permitted "
                        "(extend _R2_ALLOWED_PAYLOAD_KEYS via DW/太上老君 review)"
                    )
                _walk(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    _walk(payload, "payload")
    return violations


class GBrainDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self._conn() as con:
            # 1. Create tables (existing tables are no-ops via IF NOT EXISTS).
            con.executescript(_TABLE_SCHEMA)
            # 2. Migrate nodes to bitemporal schema if columns are missing.
            #    Must run before index creation — idx_nodes_valid references valid_to.
            existing_cols = {row[1] for row in con.execute("PRAGMA table_info(graph_nodes)")}
            if "valid_from" not in existing_cols:
                con.execute(
                    f"ALTER TABLE graph_nodes ADD COLUMN valid_from TEXT NOT NULL DEFAULT '{_MIGRATION_EPOCH}'"
                )
            if "valid_to" not in existing_cols:
                con.execute("ALTER TABLE graph_nodes ADD COLUMN valid_to TEXT")
            # 3. Create indexes (valid_from/valid_to now guaranteed to exist on graph_nodes).
            con.executescript(_INDEX_SCHEMA)

    def add_node(self, node_type: str, label: str, payload: Optional[dict] = None) -> str:
        p = payload or {}
        violations = _r2_payload_check(p)
        if violations:
            raise ValueError("; ".join(violations))
        node_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                """INSERT INTO graph_nodes (id, node_type, label, payload, created_at, valid_from)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (node_id, node_type, label, json.dumps(p), now, now),
            )
        return node_id

    def invalidate_node(self, node_id: str):
        """Bi-temporal invalidation: set valid_to instead of deleting.

        Use for superseded governance decisions. A superseding node should be created
        first (with a 'supersedes' edge pointing to this node), then this node invalidated.
        """
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                "UPDATE graph_nodes SET valid_to=? WHERE id=? AND valid_to IS NULL",
                (now, node_id),
            )

    def add_edge(self, src_id: str, dst_id: str, edge_type: str,
                 payload: Optional[dict] = None) -> str:
        p = payload or {}
        violations = _r2_payload_check(p)
        if violations:
            raise ValueError("; ".join(violations))
        edge_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                """INSERT INTO graph_edges (id, src_id, dst_id, edge_type, payload, valid_from)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (edge_id, src_id, dst_id, edge_type, json.dumps(p), now),
            )
        return edge_id

    def invalidate_edge(self, edge_id: str):
        """Bi-temporal invalidation: set valid_to instead of deleting."""
        now = datetime.utcnow().isoformat()
        with self._conn() as con:
            con.execute(
                "UPDATE graph_edges SET valid_to=? WHERE id=? AND valid_to IS NULL",
                (now, edge_id),
            )

    def get_nodes(self, node_type: Optional[str] = None,
                  active_only: bool = True) -> list[dict]:
        clauses, params = [], []
        if node_type:
            clauses.append("node_type=?")
            params.append(node_type)
        if active_only:
            clauses.append("valid_to IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM graph_nodes {where} ORDER BY valid_from DESC", params
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_edges(self, src_id: Optional[str] = None, dst_id: Optional[str] = None,
                  edge_type: Optional[str] = None, active_only: bool = True) -> list[dict]:
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
        if active_only:
            clauses.append("valid_to IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._conn() as con:
            rows = con.execute(
                f"SELECT * FROM graph_edges {where} ORDER BY valid_from DESC", params
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "payload" in d:
        d["payload"] = json.loads(d["payload"] or "{}")
    return d
