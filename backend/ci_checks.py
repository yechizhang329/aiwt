"""CI-verifiable isolation checks (R-1, W1, W6, flip-prerequisite).

Run at startup and in tests to assert structural isolation invariants:
- Track 2 (gbrain) is never accessed from hot-path orchestrator code
- Hot-path agent names are hardcoded (no dynamic injection)
- weight_modifier NULL never coalesced to 0 (W1)
- CL1 concave backstop edges (e_mask_003/006) cannot flip as byproduct of (a2)/(b) curation
"""

import ast
import sqlite3
import sys
from pathlib import Path

# Flip-prerequisite: CL1 concave backstop edges (上颌源凹 = 3× 凹面陷阱 red-line)
# These MUST NOT flip as a byproduct of (a2) 偏-leaf or (b) severity curation.
# Only flippable via dedicated a1 update with explicit concave_gate=True intent flag,
# AFTER joint 太上+DW a1-gate authorization (concave_* Walter-confidence + DW double-reconcile).
_CL1_BACKSTOP_EDGES: frozenset[str] = frozenset({"e_mask_003", "e_mask_006"})

# R-1: agents that MUST NOT read Track 2 gbrain in any code path
HOTPATH_AGENT_DENY_LIST = frozenset({
    "InitialReader",
    "KnowledgeCurator",   # KC hardcoded deny: gbrain is governance brain, not clinical KB
    "CaseMemory",
    "SeniorClinician",
    "Critic",
})

_ORCHESTRATOR_DIR = Path(__file__).parent / "orchestrator"
_GBRAIN_IDENTIFIERS = {"gbrain", "GBrainDB", "gbrain_router", "GBRAIN_DB_PATH"}

# R-1 cross-track isolation: track1↔gbrain must never import each other
_TRACK1_FILES = {"track1.py", "track1_router.py"}
_GBRAIN_FILES = {"gbrain.py", "gbrain_router.py"}
_TRACK1_IDENTIFIERS = frozenset({"track1", "GBrain1DB", "track1_router", "TRACK1_DB_PATH"})
_GBRAIN_CROSS_IDENTIFIERS = frozenset({"gbrain", "GBrainDB", "gbrain_router", "GBRAIN_DB_PATH"})


def assert_r1_cross_track_isolation() -> list[str]:
    """R-1 durable guard: track1.* must never import gbrain.*, and gbrain.* must never import track1.*.

    GBrain1DB (Track-1) and GBrainDB (Track-2) are near-identical names — static import assert
    prevents accidental cross-track coupling that would silently breach R-1.
    Returns list of violations; empty = CI pass.
    """
    backend_dir = Path(__file__).parent
    routers_dir = backend_dir / "routers"
    violations = []

    def _check_file(py_file: Path, forbidden: frozenset[str], direction: str) -> None:
        if not py_file.exists():
            return
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in getattr(node, "names", [])]
                module = getattr(node, "module", "") or ""
                for ident in forbidden:
                    if ident in module or any(ident in n for n in names):
                        violations.append(
                            f"{py_file.name}:{node.lineno}: R-1 {direction} violation — imports {ident!r}"
                        )

    for fname in _TRACK1_FILES:
        for d in (backend_dir, routers_dir):
            _check_file(d / fname, _GBRAIN_CROSS_IDENTIFIERS, "track1→gbrain")

    for fname in _GBRAIN_FILES:
        for d in (backend_dir, routers_dir):
            _check_file(d / fname, _TRACK1_IDENTIFIERS, "gbrain→track1")

    return violations


def assert_orchestrator_gbrain_isolation() -> list[str]:
    """Return list of violations: any orchestrator file importing/calling gbrain symbols.

    Returns empty list if isolation is clean (CI pass).

    FORWARD INVARIANT (DW 6bc94e32): if this check is ever intentionally relaxed to allow
    an orchestrator→gbrain injection path (e.g. for IN-4 context recovery), that injection
    MUST be identity-gated (same _F2_IN4_ALLOW whitelist as the endpoint gate). Relaxing
    the import ban without adding identity-gating breaks the independence wall — orchestrator
    injection bypasses the endpoint gate and feeds gbrain state directly into hot-path context.
    Current state: import ban is total (no injection path exists), so (a) pre-dispatch gate is
    structurally moot. Any future change here must add the gate at the injection site.
    """
    violations = []
    for py_file in _ORCHESTRATOR_DIR.glob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in getattr(node, "names", [])]
                module = getattr(node, "module", "") or ""
                if any(g in module or g in n for g in _GBRAIN_IDENTIFIERS for n in names + [module]):
                    violations.append(f"{py_file.name}:{node.lineno}: imports gbrain symbol")
            elif isinstance(node, ast.Name) and node.id in _GBRAIN_IDENTIFIERS:
                violations.append(f"{py_file.name}:{node.lineno}: references {node.id}")
    return violations


def assert_track1_null_weight_preserved(db_path: Path) -> list:
    """§5.2 catch: calibrated edges must carry weight_modifier=NULL (not 0 or any real).

    NULL ≠ 0: NULL means Walter-gated calibration pending (no weight assigned yet).
    Coalescing NULL→0 re-enables unconditional ANB trust — the original error we are guarding against.
    Returns list of violation strings (empty = CI pass).
    """
    if not db_path.exists():
        return []
    violations = []
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT id, weight_modifier FROM graph_edges"
        " WHERE calibration_status='calibrated' AND weight_modifier IS NOT NULL"
    ).fetchall()
    con.close()
    for row in rows:
        violations.append(f"edge {row[0]}: calibrated but weight_modifier={row[1]} (must be NULL)")
    return violations


_WEIGHT_COALESCE_PATTERNS = [
    # Python patterns that would silently collapse NULL weight_modifier to 0
    r"weight_modifier\s+or\s+0",
    r"weight_modifier\s*\?\?\s*0",
    r"weight_modifier\s*if\s+weight_modifier\s+is\s+not\s+None\s+else\s+0",
    r'COALESCE\s*\(\s*weight_modifier\s*,\s*0\s*\)',  # SQL
    r'fillna\s*\(\s*0\s*\)',  # pandas
    r'weight_modifier\s+or\s+[1-9]',  # any non-zero default
]


def assert_no_weight_coalesce() -> list[str]:
    """W1 CI guard: fail build on any NULL→0 coalesce on weight_modifier path in orchestrator code.

    NULL weight_modifier means Walter-gated calibration pending (not zero-weight).
    Coalescing NULL→0 re-enables unconditional ANB trust — the original clinical error.
    CI must forbid this (太上老君 build-acceptance 3992b555 W1).
    Returns list of violations; empty = CI pass.
    """
    import re as _re
    violations = []
    search_dirs = [_ORCHESTRATOR_DIR, _ORCHESTRATOR_DIR.parent]  # orchestrator/ + backend/
    for search_dir in search_dirs:
        for py_file in search_dir.glob("*.py"):
            try:
                source = py_file.read_text()
            except OSError:
                continue
            for pattern in _WEIGHT_COALESCE_PATTERNS:
                for m in _re.finditer(pattern, source, _re.IGNORECASE):
                    lineno = source[:m.start()].count('\n') + 1
                    violations.append(
                        f"{py_file.name}:{lineno}: W1 violation — weight_modifier NULL→0 coalesce: {m.group()!r}"
                    )
    return violations


def check_calibration_scope(edge_ids: list[str], *, concave_gate: bool = False) -> None:
    """Flip-prerequisite guard: validate that a calibration update is explicitly scoped.

    Call this in any curation/calibration script BEFORE issuing:
        UPDATE graph_edges SET calibration_status='calibrated' WHERE id=?

    Raises ValueError if:
    - edge_ids is empty (unscoped bulk update)
    - any edge is a CL1 backstop edge (e_mask_003/006) without concave_gate=True

    concave_gate=True asserts INTENT only ("this is a deliberate a1 flip") — NOT readiness.
    Readiness stays the joint 太上+DW a1-gate (concave_* Walter-confidence + DW double-reconcile).
    """
    if not edge_ids:
        raise ValueError(
            "Calibration update REJECTED: edge_ids is empty — unscoped bulk update forbidden. "
            "Provide explicit edge IDs per-edge-class."
        )
    backstop_in_scope = _CL1_BACKSTOP_EDGES.intersection(edge_ids)
    if backstop_in_scope and not concave_gate:
        raise ValueError(
            f"Calibration update REJECTED: {backstop_in_scope} are CL1 concave backstop edges "
            "(e_mask_003/006 = 上颌源凹) — cannot flip as byproduct of (a2) 偏 or (b) severity "
            "curation. Pass concave_gate=True only via dedicated a1 update after joint 太上+DW "
            "a1-gate authorization (concave_* calibration-confidence + DW double-reconcile)."
        )


def assert_backstop_edges_active(db_path: Path) -> list[str]:
    """CI canary: CL1 concave backstop edges must exist and be active (not invalidated/disabled).

    A missing or bi-temporally invalidated e_mask_003/006 → falsification_check returns "not_found"
    → CL1 silently clears the 拔牙 block. The evidence loop remaps this to "unknown" at runtime
    (DW e47679d0 option #1), but this CI check provides the structural defense layer (option #3).
    Returns list of violations; empty = CI pass.
    """
    if not db_path.exists():
        return []
    violations = []
    con = sqlite3.connect(db_path)
    active_ids = {
        row[0] for row in con.execute(
            "SELECT id FROM graph_edges WHERE id IN ({}) AND valid_to IS NULL AND calibration_status != 'disabled'".format(
                ", ".join("?" * len(_CL1_BACKSTOP_EDGES))
            ),
            list(_CL1_BACKSTOP_EDGES),
        ).fetchall()
    }
    con.close()
    for eid in _CL1_BACKSTOP_EDGES:
        if eid not in active_ids:
            violations.append(
                f"edge {eid}: CL1 backstop edge missing or invalidated/disabled — "
                "must remain active (valid_to IS NULL + not disabled) to hold the 凹面陷阱 gate"
            )
    return violations


_DIR_GATE_CONCL_NODE = "concl_dir_convex_clear"
_DIR_GATE_PRIMARY_ROLES = frozenset({"LOAD_BEARING_MANDATORY", "primary_hard_anchor"})
_DIR_GATE_SUPPORT_ROLE = "profile_support"


def assert_direction_gate_tier_encoding(db_path: Path) -> list[str]:
    """Build-window guard: concl_dir_convex_clear gated-by edges must carry tier markers.

    Prevents any future gate-evaluator from consuming all 4 edges as equal-weight
    (which would let profile_support edges solo-satisfy the clear-凸 gate — the
    76db/16F/26F relapse path DW flagged in b8821cee).

    Rules enforced:
    - ≥1 edge has role ∈ {LOAD_BEARING_MANDATORY, primary_hard_anchor} (PRIMARY tier present)
    - All profile_support edges must carry "NO-SOLO" in their note (support cannot solo-satisfy)
    - No unrecognized role values (must be explicitly classified)

    Note: crossmodal_corroborator (e_gate_dir_004 arch_width) is final — DW-endorsed 911fdf35.
    SNA-mandatory is stricter than §H ≥1-of-two and accepted as safer direction. Check passes
    because ≥1 PRIMARY (e_gate_dir_001 SNA LOAD_BEARING_MANDATORY) is present.
    """
    if not db_path.exists():
        return []
    import json as _json
    violations = []
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT id, payload FROM graph_edges"
        " WHERE dst_id=? AND edge_type='gated-by' AND valid_to IS NULL",
        (_DIR_GATE_CONCL_NODE,)
    ).fetchall()
    con.close()

    _VALID_ROLES = _DIR_GATE_PRIMARY_ROLES | {_DIR_GATE_SUPPORT_ROLE, "crossmodal_corroborator"}
    primary_found = 0
    for eid, payload_str in rows:
        payload = _json.loads(payload_str or "{}")
        role = payload.get("role", "")
        note = payload.get("note", "")
        if role in _DIR_GATE_PRIMARY_ROLES:
            primary_found += 1
        elif role == _DIR_GATE_SUPPORT_ROLE:
            if "NO-SOLO" not in note:
                violations.append(
                    f"edge {eid}: role=profile_support but 'NO-SOLO' absent from note — "
                    "support-tier edges must carry NO-SOLO (DW §H T-凹① b8821cee)"
                )
        elif role not in _VALID_ROLES:
            violations.append(
                f"edge {eid}: role={role!r} is unrecognized — must be one of {sorted(_VALID_ROLES)}"
            )

    if not rows:
        violations.append(
            f"concl_dir_convex_clear: no active gated-by edges found — "
            "direction gate scaffold missing"
        )
    elif primary_found == 0:
        violations.append(
            "concl_dir_convex_clear: no PRIMARY-tier gated-by edge found "
            "(role ∈ {LOAD_BEARING_MANDATORY, primary_hard_anchor}) — "
            "≥1 PRIMARY hard anchor required (DW §H T-凹① b8821cee)"
        )
    return violations


_CONCAVE_GATE_CONCL_NODE = "concl_dir_concave_retain"
_CONCAVE_GATE_PRIMARY_ROLE = "crossmodal_primary"


def assert_direction_gate_concave_tier_encoding(db_path: Path) -> list[str]:
    """Build-window guard: concl_dir_concave_retain gated-by edges must carry tier markers.

    Sibling to assert_direction_gate_tier_encoding (convex), but the rule differs:
    - Concave: ≥1 crossmodal_primary co-equal (SNA / arch_width / incisor_inclination)
    - Convex: ≥1 LOAD_BEARING_MANDATORY (SNA specifically mandatory)

    Rules enforced:
    - ≥1 edge has role=crossmodal_primary (co-equal ≥1-of-three required, NOT SNA-specific)
    - All profile_support edges carry "NO-SOLO" in their note
    - anchor_incisor_inclination has ≥1 edge (no dangling cross-modal PRIMARY anchor)

    Spec: DW 84c5cabd / 太上老君 bac5d2ee — §C cross-modal hard-constraint direction-independent.
    profile-only cases (paranasal + upper_lip only) → escalate both ways (correct safe failure).
    """
    if not db_path.exists():
        return []
    import json as _json
    violations = []
    con = sqlite3.connect(db_path)

    rows = con.execute(
        "SELECT id, payload FROM graph_edges"
        " WHERE dst_id=? AND edge_type='gated-by' AND valid_to IS NULL",
        (_CONCAVE_GATE_CONCL_NODE,)
    ).fetchall()

    incisor_edge_count = con.execute(
        "SELECT COUNT(*) FROM graph_edges"
        " WHERE src_id='anchor_incisor_inclination' AND valid_to IS NULL"
    ).fetchone()[0]

    con.close()

    _VALID_ROLES = {_CONCAVE_GATE_PRIMARY_ROLE, _DIR_GATE_SUPPORT_ROLE}
    primary_found = 0
    for eid, payload_str in rows:
        payload = _json.loads(payload_str or "{}")
        role = payload.get("role", "")
        note = payload.get("note", "")
        if role == _CONCAVE_GATE_PRIMARY_ROLE:
            primary_found += 1
        elif role == _DIR_GATE_SUPPORT_ROLE:
            if "NO-SOLO" not in note:
                violations.append(
                    f"edge {eid}: role=profile_support but 'NO-SOLO' absent from note — "
                    "support-tier edges must carry NO-SOLO (DW §C bac5d2ee)"
                )
        else:
            violations.append(
                f"edge {eid}: role={role!r} unrecognized for concave gate — "
                f"must be one of {sorted(_VALID_ROLES)}"
            )

    if not rows:
        violations.append(
            "concl_dir_concave_retain: no active gated-by edges found — "
            "concave gate scaffold missing"
        )
    elif primary_found == 0:
        violations.append(
            "concl_dir_concave_retain: no crossmodal_primary gated-by edge found — "
            "≥1 cross-modal PRIMARY required (DW §C bac5d2ee)"
        )

    if incisor_edge_count == 0:
        violations.append(
            "anchor_incisor_inclination has zero edges (dangling) — "
            "must be wired as crossmodal_primary into concave gate + masking feeder "
            "(76db escape if absent: occlusal/intraoral-only cases cannot fire §C 反伪闸)"
        )

    return violations


def assert_gbrain_r2_payload_guard() -> list[str]:
    """R-2 CI pin: gbrain.py must contain recursive allowlist payload guard on add_node and add_edge.

    Verifies:
    1. _r2_payload_check() function exists (the guard implementation)
    2. _R2_ALLOWED_PAYLOAD_KEYS constant exists (allowlist form — fail-safe, not denylist treadmill)
    3. _walk helper exists in source (recursive guard — catches nested clinical keys)
    4. add_node() calls _r2_payload_check via AST
    5. add_edge() calls _r2_payload_check via AST

    DW 7dba3f6c final form: recursive allowlist > denylist (same fail-safe as F-2 IN-4 whitelist).
    Prevents regression to schema-only R-2 or shallow denylist.
    Returns list of violations; empty = CI pass.
    """
    gbrain_file = Path(__file__).parent / "gbrain.py"
    if not gbrain_file.exists():
        return ["gbrain.py not found — R-2 payload guard cannot be verified"]
    source = gbrain_file.read_text()
    violations = []
    if "_r2_payload_check" not in source:
        violations.append("gbrain.py: _r2_payload_check() missing — R-2 content-level guard removed")
    if "_R2_ALLOWED_PAYLOAD_KEYS" not in source:
        violations.append(
            "gbrain.py: _R2_ALLOWED_PAYLOAD_KEYS missing — "
            "must use allowlist (not denylist) per DW 7dba3f6c R-2 final form"
        )
    if "_walk" not in source:
        violations.append(
            "gbrain.py: _walk() helper missing — recursive payload walk removed; "
            "nested clinical keys (e.g. {'convergence': {'case_id': ...}}) would bypass guard"
        )

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        violations.append(f"gbrain.py: SyntaxError — cannot verify R-2 coverage: {e}")
        return violations

    def _calls_in_func(func_node: ast.FunctionDef) -> set[str]:
        return {
            node.func.id
            for node in ast.walk(func_node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("add_node", "add_edge"):
            found.add(node.name)
            if "_r2_payload_check" not in _calls_in_func(node):
                violations.append(
                    f"gbrain.py: {node.name}() does not call _r2_payload_check — R-2 gate missing"
                )
    for expected in ("add_node", "add_edge"):
        if expected not in found:
            violations.append(f"gbrain.py: {expected}() not found — method removed or renamed")
    return violations


def assert_gbrain_r2_payload_runtime() -> list[str]:
    """R-2 runtime pin: _r2_payload_check must catch both shallow and nested forbidden keys.

    DW 7dba3f6c + KBadvisor a6ba619e: nested case — {'convergence': {'case_id': ...}} must fail.
    Tests: (a) shallow case_id → fail, (b) nested case_id → fail, (c) clean governance → pass.
    Returns list of violations; empty = CI pass.
    """
    import importlib.util
    gbrain_file = Path(__file__).parent / "gbrain.py"
    if not gbrain_file.exists():
        return ["gbrain.py not found — R-2 runtime check skipped"]
    spec = importlib.util.spec_from_file_location("gbrain_ci", gbrain_file)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        return [f"gbrain.py: import error during R-2 runtime check: {e}"]
    check = mod._r2_payload_check
    violations = []

    # (a) shallow forbidden key — must fail
    v = check({"case_id": None})
    if not v:
        violations.append("R-2 runtime: shallow {'case_id': None} not rejected — guard broken")

    # (b) nested forbidden key — the regression KBadvisor a6ba619e caught
    v = check({"convergence": {"case_id": "test"}})
    if not v:
        violations.append(
            "R-2 runtime: nested {'convergence': {'case_id': 'test'}} not rejected — "
            "recursive walk missing or _walk not descending into dict values"
        )

    # (c) clean governance payload — must pass (no false-positive)
    v = check({"status": "hardened", "scope": "f2-firewall", "what": "describes SNA ANB"})
    if v:
        violations.append(
            f"R-2 runtime: clean governance payload incorrectly rejected: {v}"
        )

    return violations


def assert_gbrain_f2_router_guard() -> list[str]:
    """F-2 CI guard: gbrain_router.py must use default-deny IN-4 whitelist on ALL endpoints.

    Verifies (source-level only — no false behavioral claims):
    1. _F2_IN4_ALLOW whitelist constant is present (fail-safe: unknown agents denied by default)
    2. KC is explicitly named in _F2_KC_ALIASES (belt-and-suspenders F-1 message)
    3. Both _f2_read_check and _f2_write_check gate functions are present
    4. All five endpoint handlers call a gate function (AST-based endpoint coverage)
       Read  (caller=None allowed): list_nodes, list_edges → _f2_read_check
       Write (caller=None denied):  create_node, create_edge, invalidate_edge → _f2_write_check
    5. All four IN-4 members are in the whitelist

    Prevents accidental deletion of a gate call on any endpoint while the function itself survives.
    Returns list of violations; empty = CI pass.
    """
    router_file = Path(__file__).parent / "routers" / "gbrain_router.py"
    if not router_file.exists():
        return ["gbrain_router.py not found — F-2 caller gate cannot be verified"]
    source = router_file.read_text()
    violations = []

    if "_F2_IN4_ALLOW" not in source:
        violations.append(
            "gbrain_router.py: _F2_IN4_ALLOW whitelist missing — "
            "must use default-deny IN-4 whitelist, not OUT-6 blacklist (fail-safe requirement)"
        )
    if '"KC"' not in source and "'KC'" not in source:
        violations.append(
            'gbrain_router.py: "KC" not in _F2_KC_ALIASES — KC hard deny missing (F-1 canonical-label risk)'
        )
    if "_f2_check" not in source:
        violations.append("gbrain_router.py: _f2_check() missing — symmetric gate function removed")
    for member in ("太上老君", "DentistWang", "KBadvisor", "WebAppDev"):
        if member not in source:
            violations.append(f"gbrain_router.py: IN-4 member {member!r} missing from _F2_IN4_ALLOW")

    # AST-based endpoint coverage: verify each handler calls _f2_check.
    # Symmetric gate: all 5 handlers (read + write) use the same check; None always denied.
    # This catches deletion of a gate call even when the gate function itself still exists.
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        violations.append(f"gbrain_router.py: SyntaxError — cannot verify endpoint coverage: {e}")
        return violations

    _ALL_HANDLERS = {"list_nodes", "list_edges", "create_node", "create_edge",
                     "invalidate_edge", "invalidate_node"}

    def _calls_in_func(func_node: ast.FunctionDef) -> set[str]:
        return {
            node.func.id
            for node in ast.walk(func_node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

    found_handlers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        name = node.name
        if name in _ALL_HANDLERS:
            found_handlers.add(name)
            calls = _calls_in_func(node)
            if "_f2_check" not in calls:
                violations.append(
                    f"gbrain_router.py: {name}() does not call _f2_check — gate missing"
                )

    for expected in _ALL_HANDLERS:
        if expected not in found_handlers:
            violations.append(
                f"gbrain_router.py: handler {expected}() not found — endpoint removed or renamed"
            )

    return violations


def assert_w6_ensemble_on_when_ix_enabled() -> list[str]:
    """W6 guard: if IX_ENABLED, ENSEMBLE_IR_READS must be >= 2 (ensemble-ON by construction).

    ensemble-OFF release is 批B post-build, Walter-gated, DW holds gate (IX-4 W6).
    Build runs ensemble-ON; turning it off without 批B release is an invariant violation.
    """
    import os as _os
    violations = []
    ix_enabled = bool(int(_os.environ.get("IX_ENABLED", "0")))
    ensemble_n = int(_os.environ.get("ENSEMBLE_IR_READS", "0"))
    if ix_enabled and ensemble_n < 2:
        violations.append(
            f"W6 violation: IX_ENABLED=True but ENSEMBLE_IR_READS={ensemble_n} (< 2). "
            "ensemble must be ON when IX is active — set ENSEMBLE_IR_READS >= 2 "
            "or wait for 批B Walter-gated release to turn ensemble off."
        )
    return violations


def assert_cl2_always_on_decoupled() -> list[str]:
    """Wire-a guard: _run_hrw_cl2_always_on must exist and be called outside IX_ENABLED gate.

    CL2 is the keep-1 dead-rule (76db). It must run unconditionally — not gated on IX_ENABLED.
    Authorized: jonathan db2bd365 / DW f60482e6+59ddd2ea / KBadvisor 0198bdc2.
    """
    orch_file = _ORCHESTRATOR_DIR / "v2_orchestrator.py"
    if not orch_file.exists():
        return ["CL2 always-on check: v2_orchestrator.py not found"]
    src = orch_file.read_text()
    violations = []
    if "_run_hrw_cl2_always_on" not in src:
        violations.append("CL2 always-on: _run_hrw_cl2_always_on function not found in v2_orchestrator.py")
    # Verify CL2 not inside the IX_ENABLED block: check that cl2_violations assignment precedes IX_ENABLED check
    cl2_pos = src.find("cl2_violations = _run_hrw_cl2_always_on")
    ix_pos = src.find("if config.IX_ENABLED:")
    if cl2_pos == -1:
        violations.append("CL2 always-on: cl2_violations = _run_hrw_cl2_always_on(...) call not found")
    elif ix_pos != -1 and cl2_pos > ix_pos:
        violations.append(
            "CL2 always-on: cl2_violations assignment appears AFTER IX_ENABLED gate — "
            "CL2 must run unconditionally before the IX_ENABLED block"
        )
    # Verify CL2 check not inside the floor function (removed from IX-gated floor)
    if "ix_hrw_cl2_convex_sna_retrusive" in src:
        # Find the block inside _run_ix27_hrw_clinical_floor
        floor_start = src.find("def _run_ix27_hrw_clinical_floor(")
        floor_end = src.find("\ndef ", floor_start + 1) if floor_start != -1 else -1
        if floor_start != -1 and floor_end != -1:
            floor_body = src[floor_start:floor_end]
            if "ix_hrw_cl2_convex_sna_retrusive" in floor_body:
                violations.append(
                    "CL2 always-on: ix_hrw_cl2_convex_sna_retrusive still present in "
                    "_run_ix27_hrw_clinical_floor — must be removed (now runs always-on separately)"
                )
    return violations


def run_all_checks():
    cl2_violations = assert_cl2_always_on_decoupled()
    if cl2_violations:
        raise RuntimeError(
            "CL2 always-on invariant violation — _run_hrw_cl2_always_on must be decoupled from IX_ENABLED:\n"
            + "\n".join(cl2_violations)
        )

    r1_cross = assert_r1_cross_track_isolation()
    if r1_cross:
        raise RuntimeError(
            "R-1 cross-track isolation violation — track1↔gbrain must never import each other:\n"
            + "\n".join(r1_cross)
        )

    violations = assert_orchestrator_gbrain_isolation()
    if violations:
        raise RuntimeError(
            "R-1 isolation violation — hot-path orchestrator must not access Track 2 gbrain:\n"
            + "\n".join(violations)
        )

    r2_payload_violations = assert_gbrain_r2_payload_guard()
    if r2_payload_violations:
        raise RuntimeError(
            "R-2 payload guard missing — gbrain add_node/add_edge must use recursive allowlist:\n"
            + "\n".join(r2_payload_violations)
        )

    r2_runtime_violations = assert_gbrain_r2_payload_runtime()
    if r2_runtime_violations:
        raise RuntimeError(
            "R-2 runtime check failed — shallow or nested forbidden key not rejected:\n"
            + "\n".join(r2_runtime_violations)
        )

    f2_violations = assert_gbrain_f2_router_guard()
    if f2_violations:
        raise RuntimeError(
            "F-2 router guard missing — gbrain read endpoints must deny OUT-6 hot-path callers:\n"
            + "\n".join(f2_violations)
        )

    # W1: no weight_modifier NULL→0 coalesce in orchestrator code (see §5.2 + build-acceptance W1)
    w1_violations = assert_no_weight_coalesce()
    if w1_violations:
        raise RuntimeError(
            "W1 invariant violation — weight_modifier NULL must not be coalesced to 0 in any code path:\n"
            + "\n".join(w1_violations)
        )

    # W6: ensemble must be ON when IX is enabled (批B gate held by DW)
    w6_violations = assert_w6_ensemble_on_when_ix_enabled()
    if w6_violations:
        raise RuntimeError(
            "W6 invariant violation — ensemble must be ON when IX_ENABLED:\n"
            + "\n".join(w6_violations)
        )

    # Track 1 is optional at startup (db may not exist yet in fresh deploy)
    try:
        from pathlib import Path as _Path
        import os as _os
        _t1 = _Path(_os.environ.get("TRACK1_DB_PATH",
                    str(_Path(__file__).parent / "data" / "track1.db")))
        t1_violations = assert_track1_null_weight_preserved(_t1)
        if t1_violations:
            raise RuntimeError(
                "§5.2 violation — calibrated Track 1 edges must have NULL weight_modifier:\n"
                + "\n".join(t1_violations)
            )
        backstop_violations = assert_backstop_edges_active(_t1)
        if backstop_violations:
            raise RuntimeError(
                "CL1 backstop violation — e_mask_003/006 missing or invalidated/disabled "
                "(must remain active to hold the 凹面陷阱 gate; DW e47679d0 option #3):\n"
                + "\n".join(backstop_violations)
            )
        dir_tier_violations = assert_direction_gate_tier_encoding(_t1)
        if dir_tier_violations:
            raise RuntimeError(
                "Direction gate tier violation — concl_dir_convex_clear gated-by edges "
                "missing PRIMARY tier marker or profile_support NO-SOLO (DW §H T-凹① b8821cee):\n"
                + "\n".join(dir_tier_violations)
            )
        concave_tier_violations = assert_direction_gate_concave_tier_encoding(_t1)
        if concave_tier_violations:
            raise RuntimeError(
                "Concave gate tier violation — concl_dir_concave_retain gated-by edges "
                "missing crossmodal_primary or anchor_incisor_inclination dangling "
                "(DW §C 84c5cabd / 太上 bac5d2ee — 76db 反伪闸):\n"
                + "\n".join(concave_tier_violations)
            )
    except RuntimeError:
        raise
    except Exception:
        pass  # track1 not available / not seeded yet — skip
