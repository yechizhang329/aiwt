"""FastAPI router for Track 2 governance brain (GBrainDB).

F-2 invariant: hot-path LLM agents (OUT-6) must NEVER access gbrain (read or write).
Reason: diagnostic independence requires stateless agents; shared memory collapses
independent judgments into a single illusion. KC is doubly forbidden: corrected
canonical labels must not resurrect through shared memory (F-1 risk).

IN-4 (allowed): 太上老君 / DentistWang / KBadvisor / WebAppDev
OUT-6 (denied): InitialReader / KC / CaseMemory / SeniorClinician / Critic / HardRuleWrapper

Gate policy (symmetric — all endpoints):
- Caller=None (no header) DENIED on all endpoints. Explicit IN-4 identity required.
- IN-4 callers: allowed
- All others (OUT-6, unknown, future agents): denied by default (fail-safe whitelist)
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

import config
from gbrain import GBrainDB

router = APIRouter(prefix="/gbrain", tags=["gbrain"])

# F-2 whitelist — default-deny / fail-safe. Unknown/future agents denied automatically.
# Roster-invariant: anything that touches the diagnostic pipeline defaults to OUT.
_F2_IN4_ALLOW: frozenset[str] = frozenset({
    "太上老君",
    "DentistWang",
    "KBadvisor",
    "WebAppDev",
})

# KC belt-and-suspenders: explicitly named for the F-1 error message.
# KC would be denied by the whitelist anyway; explicit entry documents the failure mode.
_F2_KC_ALIASES: frozenset[str] = frozenset({"KC", "KnowledgeCurator"})


def _decode_caller(caller: Optional[str]) -> Optional[str]:
    """Recover UTF-8 caller names that arrived as latin-1 mojibake.

    HTTP/1.1 headers are latin-1 per spec; Starlette decodes them that way.
    Non-ASCII callers (e.g. 太上老君) sending UTF-8 bytes arrive as mojibake — re-decode.
    Pure ASCII callers pass through unchanged (encode/decode is a no-op for ASCII).
    """
    if caller is None:
        return None
    try:
        return caller.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return caller


def _f2_check(caller: Optional[str]) -> None:
    """F-2 gate (symmetric, all endpoints): caller=None DENIED; IN-4 allowed; all others denied.

    Requires explicit IN-4 identity on every access (read and write). Default-deny is fail-safe:
    unknown/future agents are denied automatically without needing to be enumerated.
    KC gets a specific F-1 error; all other denied callers get the generic message.

    Encoding: non-ASCII callers (e.g. 太上老君) sending UTF-8 bytes arrive as latin-1 mojibake;
    _decode_caller() recovers the original UTF-8 string before whitelist lookup.
    """
    caller = _decode_caller(caller)
    if caller is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "F-2 violation",
                "caller": None,
                "reason": "All gbrain endpoints require explicit IN-4 caller identity "
                          "(X-Gbrain-Caller header). Anonymous access is denied.",
            },
        )
    if caller in _F2_IN4_ALLOW:
        return
    if caller in _F2_KC_ALIASES:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "F-2 violation",
                "caller": caller,
                "reason": "KC is hard-denied from gbrain access (F-1 risk: corrected canonical "
                          "labels must not resurrect via shared governance memory into clinical KB)",
            },
        )
    raise HTTPException(
        status_code=403,
        detail={
            "error": "F-2 violation",
            "caller": caller,
            "reason": "Default-deny: caller not in IN-4 whitelist "
                      "{太上老君/DentistWang/KBadvisor/WebAppDev}. "
                      "Gbrain access is governance-only; diagnostic agents and unknown callers "
                      "are denied by default.",
        },
    )


_db: Optional[GBrainDB] = None


def get_db() -> GBrainDB:
    global _db
    if _db is None:
        _db = GBrainDB(config.GBRAIN_DB_PATH)
    return _db


class NodeCreate(BaseModel):
    node_type: str
    label: str
    payload: dict = {}


class EdgeCreate(BaseModel):
    src_id: str
    dst_id: str
    edge_type: str
    payload: dict = {}


@router.post("/nodes")
def create_node(
    body: NodeCreate,
    x_gbrain_caller: Optional[str] = Header(default=None, alias="X-Gbrain-Caller"),
):
    _f2_check(x_gbrain_caller)
    try:
        node_id = get_db().add_node(body.node_type, body.label, body.payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": node_id}


@router.get("/nodes")
def list_nodes(
    node_type: Optional[str] = None,
    active_only: bool = True,
    x_gbrain_caller: Optional[str] = Header(default=None, alias="X-Gbrain-Caller"),
):
    _f2_check(x_gbrain_caller)
    return get_db().get_nodes(node_type=node_type, active_only=active_only)


@router.delete("/nodes/{node_id}")
def invalidate_node(
    node_id: str,
    x_gbrain_caller: Optional[str] = Header(default=None, alias="X-Gbrain-Caller"),
):
    _f2_check(x_gbrain_caller)
    get_db().invalidate_node(node_id)
    return {"invalidated": node_id}


@router.post("/edges")
def create_edge(
    body: EdgeCreate,
    x_gbrain_caller: Optional[str] = Header(default=None, alias="X-Gbrain-Caller"),
):
    _f2_check(x_gbrain_caller)
    try:
        edge_id = get_db().add_edge(body.src_id, body.dst_id, body.edge_type, body.payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": edge_id}


@router.get("/edges")
def list_edges(
    src_id: Optional[str] = None,
    dst_id: Optional[str] = None,
    edge_type: Optional[str] = None,
    active_only: bool = True,
    x_gbrain_caller: Optional[str] = Header(default=None, alias="X-Gbrain-Caller"),
):
    _f2_check(x_gbrain_caller)
    return get_db().get_edges(src_id=src_id, dst_id=dst_id,
                              edge_type=edge_type, active_only=active_only)


@router.delete("/edges/{edge_id}")
def invalidate_edge(
    edge_id: str,
    x_gbrain_caller: Optional[str] = Header(default=None, alias="X-Gbrain-Caller"),
):
    _f2_check(x_gbrain_caller)
    get_db().invalidate_edge(edge_id)
    return {"invalidated": edge_id}
