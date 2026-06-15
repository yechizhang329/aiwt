"""v2 Orchestrator — Path B: Slock DM dispatch to agent instances.

Stage sequence (spec v1.2.1):
  0: sufficiency gate (code, pre-submit)
  A: Initial Reader (multimodal, via @InitialReader Slock DM)
  B: KC + CM parallel (via @KnowledgeCurator + @CaseMemory Slock DMs)
  C: SeniorClinician (multimodal, via @SeniorClinician Slock DM)
  D: Critic (multimodal, via @Critic Slock DM)
  E: HardRuleWrapper (code — clinician_content check)
  F: Format (code — voice_output HRW check + finalize)
  G: Doctor review (human gate, Scene 1 only)

Protocol: plain JSON payload via slock DM, poll dm:@Agent for response matching request_id.
No SLOCK_ENVELOPE_V1. Each agent uses its own byoc credentials.
image_refs passed as {storage_path, mime_type, attachment_id} — agents read + base64 encode.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import config
from diagnosis_first_training import (
    build_diagnosis_first_training_payload,
    build_diagnosis_first_training_receipt,
)
from reasoning_workspace_runtime import (
    build_reasoning_output_projection,
    build_reasoning_loop_training_payload,
    build_reasoning_loop_training_receipt,
    build_runtime_reasoning_workspace,
)
from track1 import GBrain1DB, falsification_check as _t1_falsification_check

_t1_db: Optional["GBrain1DB"] = None

def _get_t1_db() -> "GBrain1DB":
    global _t1_db
    if _t1_db is None:
        _t1_db = GBrain1DB(config.TRACK1_DB_PATH)
    return _t1_db

_SCRIPTS_DIR = str(config.ORCHESTRATOR_SCRIPTS)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# P-code canonical text lookup (P2: expand risk_patterns_hinted for KC/SC/Critic dispatch)
_KB_PCODE_FILE = (config.DENTIST_WORKSPACE / "notes" / "orthodontics" / "clinical_kb"
                  / "risk_patterns_by_diagnosis.md")
_PCODE_LOOKUP: dict = {}  # lazy-load cache; reset on backend restart

from orchestrator.stage_info import make_running, make_completed, make_failed
from orchestrator.multimodal_dispatch import load_image_refs_for_case
from orchestrator.output_schemas import STAGE_SCHEMAS, SC_SECTIONS_BY_SCENE

# Slock CLI binary — agent-specific wrapper injected by daemon
_SLOCK_BIN = str(Path(__file__).parent.parent.parent / ".slock" / "slock")

# Agent handles for Slock DM dispatch
_AGENT_IR = "InitialReader"
_AGENT_KC = "KnowledgeCurator"
_AGENT_CM = "CaseMemory"
_AGENT_SC = "SeniorClinician"
_AGENT_CRITIC = "Critic"
_AGENT_CONVEX_IR = "ConvexIR"
_AGENT_CONCAVE_IR = "ConcaveIR"
_AGENT_NONSAGITTAL_IR = "NonSagittalIR"
_V4_PACKET_AGENTS = frozenset({_AGENT_CONVEX_IR, _AGENT_CONCAVE_IR, _AGENT_NONSAGITTAL_IR})

# Durable Trigger-B fix: orchestrator deterministically syncs SC sysprompt at dispatch time
# (replaces unreliable LLM-executed directive that failed 2/2 — DW msg=2f2de10f)
_SC_SYSPROMPT_SRC = _BACKEND_DIR / "prompts" / "stage_C_senior_clinician.md"
_SC_SYSPROMPT_DST = Path.home() / ".slock" / "agents" / "9a43b2c8-f74e-4bdd-bb60-42e565002041" / "notes" / "sysprompt.md"

# Per-stage timeouts (seconds) — longer than Phase A to account for Slock round-trip
STAGE_TIMEOUTS = {
    "stage_0_sufficiency_gate": 10,
    "stage_A_initial_reader": 300,
    "stage_A_v4_ir_packets": 600,
    "stage_B_kb_retrieve": 600,    # reduced from 900s (DW governance 2026-05-31: KB retrieve ≤ 10 min)
    "stage_C_senior_clinician": 1200,
    "stage_D_critic": 900,    # raised from 600s: complex Walter B3 TMD CoVe >600s (PM msg=626da08d)
    "stage_E_hrw": 30,
    "stage_F_format": 30,
    "stage_G_doctor_review": 0,
}

# InitialReader can queue behind other live Slock work and still produce a valid
# fresh response. Future runs may salvage that late response, but already-terminal
# rows remain immutable because recovery happens only inside the active run.
INITIAL_READER_LATE_GRACE_SEC = 240

# Case-level total latency budgets (seconds) — hard cap via asyncio.wait_for in run_v2
# Scene 1: 1200s (data-driven回调 from 1500s; DW clinical safety approve msg=7476f552; DM-poll discipline fix
#   makes 1200s sufficient: 19e93c14=563s, 30886dee=765s; ~435-640s safety margin)
# Scene 3: 2400s (raised from 1800s: v3 §13 SC 512-752s + KC-timeout(600s) worst path = 1640s before Critic; PM+DW+WebAppDev align msg=fdeffa9a/f6bf5c89)
CASE_BUDGETS = {
    "1_patient": 1200,   # 20 min hard cap (data-driven回调 from 1500s; DW approve msg=7476f552)
    "3_doctor": 2400,    # 40 min hard cap (raised from 1800s: v3 SC 752s max + KC-timeout path; PM+DW+WebAppDev align msg=fdeffa9a/f6bf5c89)
}

MAX_RETRIES = 1  # Stage C retry for voice mode upgrade

# O-5 asymmetric dispatch: SC gets full CM context; Critic gets metadata only (no prior-answer fields).
# Classified by DW (msg=1a13801c): top_5_cases/confidence/and3_assessment/axis2_coverage_note
# encode CM's prior leanings → quarantine from Critic to preserve 假设盲.
# wang_te_decision_patterns already stripped; included here for completeness.
_CRITIC_CM_QUARANTINE = frozenset({
    "wang_te_decision_patterns",
    "top_5_cases",
    "confidence",
    "and3_assessment",
    "axis2_coverage_note",
})

# HRW v1.3.4 HIGH carve-out: these rules block pipeline + trigger SC re-correction (Scene 1 only)
# v1.4.0 临床安全 lane: added 3 clinical routing safety rules (PM+DW approved, 273ffe08 TRUE signal verified)
_HRW_HIGH_BLOCK_RULES = {
    "forbidden_coben_layer1",
    "forbidden_device_codes_layer1",
    "forbidden_kb_internal_codes_layer1_and_3",
    "concave_subclass_face_mask_routing_scene3",       # SGTB + 真凹 mismatch → SC re-correction
    "concave_family_history_required_scene3",           # 凹面 family history mandatory
    "midline_misalign_3_subclass_required_scene3",      # 偏颌 3 sub-class mandatory
}


# CL1 concave backstop edges — must NOT discharge via missing/invalidated row (DW e47679d0).
# IX-2.7: "not_found" (edge missing/disabled) ≠ clinical ruling-out → remapped to "unknown" → BLOCK.
# "calibrated_absent" (no clinician sign-off) also remapped to "unknown" until door-3.
# door-3 GO condition: both == "present" (calibrated + signed = OUTCOME 上颌源凹 ruled-out confirmed).
# Must match ci_checks._CL1_BACKSTOP_EDGES.
_CL1_BACKSTOP_EDGE_IDS: frozenset[str] = frozenset({"e_mask_003", "e_mask_006"})

# ── IX-2.2~2.6 + IX-3 interaction state machine ─────────────────────────────
# Activated when config.IX_ENABLED=True. Default OFF.
# Loop: HYPOTHESIZE→DISPATCH→GATHER→CONVERGENCE_CHECK up to config.IX_MAX_ROUNDS.
# T1-4 live: 34 supports + 6 masked-by calibrated (DW sign-off msg=cb7c3976).
# NULL-weight edges return "unknown" → loop continues → max rounds → escalate (W1).
# Degrades gracefully: if SC returns no ix_falsification_request (pre-IX agents), exits immediately.

# IX-2.2 cross-track comparator: fixed incoherent pairing table (DW clause 护栏①, 05ef4149).
# Only (突面+凹增偏) and (凹面+突吸偏) are flagged. Non-matching labels always pass through.
_INCOHERENT_CROSS_TRACK_PAIRS = [
    ("突面", "凹增偏"),  # protrusive face + unilateral condyle hyperplasia = incoherent
    ("凹面", "突吸偏"),  # concave face + bilateral condyle absorption = incoherent
]

# W4 Critic exclusion: fields that MUST NOT appear in critic_verify payload.
# cross_track_flag added per 太上老君 6a799c4a (engine-internal routing signal, 护栏③).
_CRITIC_W4_EXCLUDED_FIELDS = frozenset({
    "hypothesis_frame", "dispatch_trace", "leading_hypothesis_label", "cross_track_flag",
    "ix_falsification_request", "ix_current_hypothesis", "ix_dispatch_trace",
    "ix_evidence", "ix_mode", "ix_round",
})


class IXNonConvergenceError(RuntimeError):
    """IX loop exhausted rounds without convergence — caller should escalate to awaiting_doctor_review."""


class CrossTrackEscalateError(RuntimeError):
    """IX-2.2 cross-track label incoherence detected — escalate to awaiting_doctor_review (护栏②: flag-and-escalate, not hard-reject)."""


def _cross_track_comparator(face_class: str, hypothesis_label: str) -> tuple:
    """IX-2.2 orchestrator label-consistency comparator (DW 护栏①②③, signed 05ef4149).

    Compares sagittal face_class (axis_1) × 偏颌 subtype from SC hypothesis.
    Only flags known incoherent pairs per fixed table — non-matching labels always pass.
    Zero T1-4 edges consulted. Returns (coherent: bool, flag_reason: str | None).
    """
    if not face_class or not hypothesis_label:
        return True, None
    for sagittal, midline_type in _INCOHERENT_CROSS_TRACK_PAIRS:
        if sagittal in face_class and midline_type in hypothesis_label:
            return False, f"cross_track:{sagittal}×{midline_type}"
    return True, None


def _assert_w4_critic_clean(payload: dict):
    """W4 weld: raise if any hypothesis-frame or routing field is present in Critic payload.

    Fail-closed — Critic must be hypothesis-blind (IX-4 W4, 太上老君 3992b555).
    Called at _run_stage_D dispatch time; must pass every time.
    """
    found = _CRITIC_W4_EXCLUDED_FIELDS & set(payload.keys())
    if found:
        raise RuntimeError(f"W4 invariant violation: Critic payload contains forbidden fields {sorted(found)}")


def _ix_convergence_check(evidence_state: dict, required_edges: list,
                           competing_hypotheses: Optional[list] = None) -> bool:
    """IX-2.5 4-conjunct convergence predicate. W5: never force — all 4 must be True.

    ① Required masked-by set: every edge ∈ {present, absent}, no unknown. LIVE (W1-tied).
    ② Kill-combo absent. STUB → False (escalate-forcing, Batch A Walter-gated:
       needs calibrated 杀死组合 thresholds — 凸+SNA后缩/凸+弓窄/拔牙+凹证伪未absent/突面+凹增偏).
    ③ Safety-threshold axis directional quantification. STUB → False (escalate-forcing,
       Batch A Walter-gated: SNA/SNB GT + 华人 norm not yet set).
    ④ No competing hypothesis with unhandled present support. LIVE (structural).

    Stubs ②③ are escalate-forcing by design (not default-pass): pre-批A engine must stay
    escalate-heavy — silently converging while skipping kill-combo/directional-quant is the
    safety hole W1 corollary forbids (太上老君 a4a02b77).
    """
    if not required_edges:
        return True  # IX-3 easy-case short circuit: no masked-by edges → structural determination

    # Predicate ①: all required edges resolved (W1-tied: unknown/not_found → False → continue loop)
    # IX-2.7: "absent" is a dead state — predicate passes only when all edges == "present".
    # door-3 pre-req: outcome-rekey this predicate (presence-string "present" ≠ OUTCOME ruling-out).
    if not all(evidence_state.get(eid) == "present" for eid in required_edges):
        return False

    # Predicate ②: kill-combo absent (Batch A stub — always escalate until Walter calibration)
    # Needs: calibrated edge weights + 杀死组合 clinical thresholds (SNA后缩/弓窄 numerics).
    _pred2_kill_combo_clean = False  # escalate-forcing stub
    if not _pred2_kill_combo_clean:
        return False

    # Predicate ③: safety-threshold axis directional quantification (Batch A stub)
    # Needs: SNA/SNB ground truth thresholds + 华人 norm calibration (Batch A Walter-gated).
    _pred3_directional_confirmed = False  # escalate-forcing stub
    if not _pred3_directional_confirmed:
        return False

    # Predicate ④: no competing hypothesis with unhandled present support
    if competing_hypotheses:
        for ch in competing_hypotheses:
            ch_support_ids = ch.get("support_edge_ids") or []
            if any(evidence_state.get(eid) == "present" for eid in ch_support_ids):
                return False

    return True




async def _run_stage_C_ix(case_id: str, case_payload: dict, ir_output: dict, kc_output: dict,
                           cm_output: dict, image_refs: list, scene: str,
                           forced_voice_mode: Optional[str] = None,
                           hrw_correction: Optional[list] = None) -> dict:
    """IX-2.2~2.5: HYPOTHESIZE→DISPATCH→GATHER→CONVERGENCE loop.

    IX-2.2 includes orchestrator cross-track comparator (post-GESTALT, pre-DISPATCH).
    T1-4 live: 34 supports + 6 masked-by = 40 edges (DW sign-off msg=cb7c3976).
    NULL-weight edges return "unknown" → loop continues → max rounds → escalate (W1).
    Pre-IX agents (no ix_falsification_request in response) exit loop immediately (easy-case path).
    Max rounds hit without convergence → raises IXNonConvergenceError.

    IX-2.6 CRITIC blind: strips ix_* + cross_track_flag from SC output before returning.
    Critic never sees hypothesis_frame, dispatch_trace, leading_hypothesis_label, cross_track_flag (W4).
    """
    max_rounds = config.IX_MAX_ROUNDS
    evidence_state: dict[str, str] = {}   # edge_id → status
    dispatch_trace: list = []
    sc_output: Optional[dict] = None
    cross_track_checked = False  # run comparator exactly once after first hypothesis

    for round_n in range(max_rounds):
        ir_for_dispatch = dict(ir_output)
        if forced_voice_mode == "B_difficult_diagnosis_warning":
            ir_for_dispatch["voice_mode_hint"] = "B"
        cm_for_dispatch = {k: v for k, v in cm_output.items() if k != "wang_te_decision_patterns"}

        payload: dict = {
            "case_id": case_id,
            "scene": "1" if scene.startswith("1") else "3",
            "case_struct": _build_case_struct(case_payload, scene),
            "stage_a_output": ir_for_dispatch,
            "sagittal_consensus_packet": ir_for_dispatch.get("sagittal_consensus_packet"),
            "source_attribution_packet": ir_for_dispatch.get("source_attribution_packet"),
            "diagnosis_first_packet": ir_for_dispatch.get("diagnosis_first_packet"),
            "stage_b_kc": kc_output,
            "stage_b_cm": cm_for_dispatch,
            "image_refs": image_refs,
            # IX protocol fields (agents ignore if not IX-aware)
            "ix_mode": "hypothesize" if round_n == 0 else "update",
            "ix_round": round_n,
        }
        if hrw_correction:
            payload["hrw_correction_required"] = True
            payload["hrw_violations"] = hrw_correction
        if evidence_state:
            payload["ix_evidence"] = evidence_state
        if dispatch_trace:
            payload["ix_dispatch_trace"] = dispatch_trace

        if round_n == 0:
            shutil.copy2(_SC_SYSPROMPT_SRC, _SC_SYSPROMPT_DST)
        sc_output = await _slock_dispatch(_AGENT_SC, payload, STAGE_TIMEOUTS["stage_C_senior_clinician"])

        # IX-2.2 HYPOTHESIZE: check if SC requests falsification (§6.2 step 3)
        falsification_request = sc_output.get("ix_falsification_request") or []
        hypothesis_label = sc_output.get("ix_current_hypothesis", "unknown")

        # IX-2.2 cross-track comparator (orchestrator-level, post-GESTALT pre-DISPATCH, run once).
        # Compares axis_1 face_class × SC's leading hypothesis 偏颌 subtype.
        # 护栏②: flag-and-escalate, NOT hard-reject. 护栏③: flag stays engine-internal (not passed to Critic).
        if not cross_track_checked and round_n == 0:
            cross_track_checked = True
            axes_raw = ir_output.get("axes") or []
            axis1 = next((a for a in axes_raw if isinstance(a, dict) and a.get("axis") == 1), None)
            face_class = ""
            if axis1:
                face_class = (axis1.get("face_class") or axis1.get("value") or "")
            coherent, cross_flag_reason = _cross_track_comparator(face_class, hypothesis_label)
            if not coherent:
                _log.warning("case_id=%s IX-2.2 cross-track incoherence: %s — escalating",
                             case_id, cross_flag_reason)
                # 护栏③: cross_track_flag is engine-internal routing signal — strip from SC before raising
                for _f in list(_CRITIC_W4_EXCLUDED_FIELDS):
                    sc_output.pop(_f, None)
                raise CrossTrackEscalateError(
                    f"IX-2.2 cross-track incoherence: {cross_flag_reason} "
                    f"— escalated for IX-2.1 re-anchor (护栏②: not hard-reject)"
                )
            _log.info("case_id=%s IX-2.2 cross-track coherent face_class=%s hyp=%s",
                      case_id, face_class, hypothesis_label)

        if not falsification_request:
            # Easy-case short circuit (IX-3 §6.3 §6.4): no masked-by edges in KB for this hypothesis,
            # OR pre-IX agent (no ix response fields) — exit loop, use SC output as-is
            _log.info("case_id=%s IX round=%d: no falsification_request — easy-case exit", case_id, round_n)
            break

        # W3 weld: dispatch set = full required_masked_by_set (confidence MUST NOT prune edges).
        # All edges requested by SC are dispatched; no filtering by confidence or any other criterion.
        required_edges = [r.get("edge_id") for r in falsification_request if isinstance(r, dict) and r.get("edge_id")]
        malformed = len(falsification_request) - len(required_edges)
        if malformed > 0:
            # Malformed requests (no edge_id field) are a protocol error, not a W3 pruning violation.
            # Log as warning — dispatch proceeds on valid subset (edge_id-less entries cannot be dispatched).
            _log.warning("case_id=%s IX round=%d W3-protocol: %d/%d falsification_request entries missing edge_id",
                         case_id, round_n, malformed, len(falsification_request))

        # IX-2.3 DISPATCH: query Track 1 KB for each requested edge (T1-4 live)
        evidence_results = _t1_falsification_check(_get_t1_db(), required_edges)
        _log.info("case_id=%s IX round=%d: queried %d edges hypothesis=%s statuses=%s",
                  case_id, round_n, len(required_edges), hypothesis_label,
                  {e["edge_id"]: e["status"] for e in evidence_results})

        # IX-2.4 GATHER+UPDATE: accumulate evidence
        for ev in evidence_results:
            _ev_status = ev["status"]
            # CL1 fail-closed: a missing/invalidated backstop edge returns "not_found" from
            # falsification_check, which would clear the CL1 拔牙 block.
            # Remap "not_found" → "unknown" for backstop edges so the CL1 primary gate stays
            # BLOCKED when the edge is not confirmably active. (DW e47679d0 fix option #1)
            if _ev_status == "not_found" and ev["edge_id"] in _CL1_BACKSTOP_EDGE_IDS:
                _ev_status = "unknown"
            # IX-2.7: calibrated_absent = calibrated but no clinician sign-off yet.
            # Door-3 Walter-provenance clear gate NOT YET BUILT — treat as "unknown"
            # (fail-closed) until door-3 lands. At that point this remap is removed.
            if _ev_status == "calibrated_absent":
                _ev_status = "unknown"
            evidence_state[ev["edge_id"]] = _ev_status
            # W2 polarity assertion: present-edge path must have known activation direction.
            # supports → src-activation; masked-by → dst-activation. Unknown edge_type = fail-closed.
            if _ev_status == "present":
                _w2_et = ev.get("edge_type")
                if _w2_et not in ("supports", "masked-by"):
                    raise RuntimeError(
                        f"W2 polarity violation: case_id={case_id} edge={ev['edge_id']} "
                        f"has unexpected edge_type={_w2_et!r}; "
                        "supports→src-activation / masked-by→dst-activation only"
                    )
                _log.info("W2 polarity: case_id=%s edge=%s type=%s activation=%s",
                          case_id, ev["edge_id"], _w2_et, "src" if _w2_et == "supports" else "dst")
        dispatch_trace.append({
            "round": round_n,
            "hypothesis": hypothesis_label,
            "edges_queried": required_edges,
        })

        # IX-2.4 named retreat-to-2.1: if all queried edges are Walter-pending (unknown),
        # convergence is structurally impossible this cycle — exit early with named retreat signal
        # rather than burning remaining rounds (ergonomic improvement; P2 DW ruling 9e325979).
        _ix24_all_unknown = required_edges and all(
            evidence_state.get(eid) == "unknown" for eid in required_edges
        )
        if _ix24_all_unknown:
            _log.info(
                "case_id=%s IX round=%d: IX-2.4 retreat-to-2.1 — all %d edges Walter-pending "
                "(unknown); convergence impossible → escalate",
                case_id, round_n, len(required_edges)
            )
            raise IXNonConvergenceError(
                f"IX-2.4 retreat-to-2.1: all {len(required_edges)} queried edges are "
                f"Walter-pending (unknown) — calibration required before convergence "
                f"(case_id={case_id} hypothesis={hypothesis_label})"
            )

        # IX-2.5 CONVERGENCE CHECK (4-conjunct predicate — see _ix_convergence_check docstring)
        competing_hypotheses = sc_output.get("ix_competing_hypotheses") or []
        if _ix_convergence_check(evidence_state, required_edges, competing_hypotheses):
            _log.info("case_id=%s IX round=%d: CONVERGED", case_id, round_n)
            break

        if round_n == max_rounds - 1:
            # W5 / IX-3 weld-lock 3: non-convergence → honest escalate, never force-commit
            raise IXNonConvergenceError(
                f"IX state machine: max rounds ({max_rounds}) reached without convergence "
                f"— escalated for human review (hypothesis={hypothesis_label})"
            )

    # IX-2.6 CRITIC blind: strip IX protocol + routing fields before SC output leaves this function.
    # W4: Critic must not see hypothesis_frame, dispatch_trace, leading_hypothesis_label, cross_track_flag.
    # 护栏③: cross_track_flag is engine-internal routing signal — must not reach Critic.
    if sc_output:
        for _ix_field in _CRITIC_W4_EXCLUDED_FIELDS:
            sc_output.pop(_ix_field, None)
        # Thread evidence_state to Stage E (IX-2.7 HRW floor) via internal field.
        # _ix_evidence_state is extracted + removed in _run_v2_stages before Critic dispatch.
        sc_output["_ix_evidence_state"] = evidence_state

    return sc_output


# ── P-code canonical text lookup (P2: expand risk_patterns_hinted) ───────────

import re as _re

# Fix F3: Slock DM message timestamp extraction for stale-response filtering
_SLOCK_TIME_RE = _re.compile(r"time=(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})")

def _load_pcode_canonical(filepath: Path) -> dict:
    """Parse risk_patterns_by_diagnosis.md → {code: canonical_text} for P01-P62.

    Two formats co-exist:
    - P01-P37: `### P14 — canonical text` heading
    - P38-P62: `## P38` heading + `**diagnosis_pattern:** canonical text` body
    """
    pmap: dict = {}
    try:
        lines = filepath.read_text(encoding="utf-8").splitlines()
    except OSError:
        return pmap
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _re.match(r'^### (P\d+) — (.+)$', line)
        if m:
            pmap[m.group(1)] = m.group(2)
        else:
            m2 = _re.match(r'^## (P\d+)$', line)
            if m2:
                code = m2.group(1)
                for j in range(i + 1, min(i + 10, len(lines))):
                    m3 = _re.match(r'^\*\*diagnosis_pattern:\*\*\s*(.+)$', lines[j].strip())
                    if m3:
                        pmap[code] = m3.group(1)
                        break
        i += 1
    return pmap


def _get_pcode_lookup() -> dict:
    global _PCODE_LOOKUP
    if not _PCODE_LOOKUP:
        _PCODE_LOOKUP = _load_pcode_canonical(_KB_PCODE_FILE)
        _log.info("_get_pcode_lookup: loaded %d P-codes from KB", len(_PCODE_LOOKUP))
    return _PCODE_LOOKUP


def _expand_risk_patterns(ir_output: dict) -> dict:
    """Expand risk_patterns_hinted from ["P14"] to [{"code": "P14", "canonical_text": "..."}].

    Eliminates KC/SC ~30-60s per-case KB verify overhead by injecting canonical text at
    dispatch time. No-ops gracefully if KB file unavailable or hints already expanded.
    """
    hints = ir_output.get("risk_patterns_hinted")
    if not hints or not isinstance(hints, list):
        return ir_output
    if hints and isinstance(hints[0], dict):
        return ir_output  # already expanded
    pmap = _get_pcode_lookup()
    if not pmap:
        return ir_output  # KB unavailable — pass through unchanged
    expanded = []
    for h in hints:
        if isinstance(h, str):
            canonical = pmap.get(h)
            if canonical:
                expanded.append({"code": h, "canonical_text": canonical})
            else:
                _log.warning("_expand_risk_patterns: unknown P-code %s", h)
                expanded.append({"code": h, "canonical_text": ""})
        else:
            expanded.append(h)
    return {**ir_output, "risk_patterns_hinted": expanded}


# ── V4 Phase I sagittal gate stability ───────────────────────────────────────

_V4_ANCHOR_FIELDS = (
    "overjet_sign",
    "anterior_crossbite",
    "posterior_crossbite",
    "overjet_depth",
    "skeletal_chin_ap",
    "maxillary_support",
    "paranasal_support",
    "lip_ap_relation",
    "chin_prominence",
    "mandibular_soft_tissue_volume",
    "arch_palate",
    "maxillary_arch_form",
    "incisor_compensation",
    "upper_incisor_compensation",
    "lower_incisor_compensation",
    "molar_canine_relation",
    "molar_relation_sagittal",
    "prior_extraction_retraction",
    "surface_protrusion_signal",
    "closure_blockers",
    "closure_status",
)

_V4_REVIEW_GATES = {
    "maxillary_origin_masked_concave_review_required",
    "frank_concave_classIII_review_required",
    "unresolved_not_closeable_needs_review",
}

_V4_ANCHOR_VALUE_ENUMS = {
    "overjet_sign": {"reverse", "edge_to_edge", "positive", "unreadable"},
    "anterior_crossbite": {"present", "absent", "suspicious", "unreadable"},
    "posterior_crossbite": {
        "present_unilateral", "present_bilateral", "absent", "suspicious", "unreadable",
    },
    "overjet_depth": {
        "large_positive", "normal_positive", "shallow_positive",
        "edge_to_edge", "reverse", "unreadable",
    },
    "skeletal_chin_ap": {
        "anterior_prognathic", "neutral_not_retrusive",
        "retrusive_classII_like", "unreadable",
    },
    "maxillary_support": {"deficient", "adequate", "excessive", "unreadable"},
    "paranasal_support": {"deficient", "adequate", "suspicious", "unreadable"},
    "lip_ap_relation": {
        "lower_lip_ahead", "both_lips_protrusive",
        "upper_lip_ahead_or_classII_like", "neutral", "unreadable",
    },
    "chin_prominence": {
        "prominent_or_forward", "not_prominent", "retrusive", "suspicious", "unreadable",
    },
    "mandibular_soft_tissue_volume": {
        "large", "small", "normal", "suspicious", "unreadable",
    },
    "arch_palate": {
        "strict_narrow_high", "generic_crowding_only", "normal_or_broad", "unreadable",
    },
    "maxillary_arch_form": {
        "narrow_or_high_palate", "normal", "crowded_only", "suspicious", "unreadable",
    },
    "incisor_compensation": {
        "upper_proclination", "lower_retroclination", "bimax_proclination",
        "compensated_positive_overjet", "none_clear", "unreadable",
    },
    "upper_incisor_compensation": {
        "proclined", "not_proclined", "suspicious", "unreadable",
    },
    "lower_incisor_compensation": {
        "retroclined_or_upright_compensation", "not_compensated", "suspicious", "unreadable",
    },
    "molar_canine_relation": {"class_I", "class_II", "class_III", "confounded", "unreadable"},
    "molar_relation_sagittal": {"mesial", "neutral", "distal", "mixed", "unreadable"},
    "prior_extraction_retraction": {"present", "absent", "source_gap_unknown", "unreadable"},
    "surface_protrusion_signal": {"strong", "mild", "absent", "unreadable"},
    "closure_blockers": {
        "none_clear", "present", "missing_required_anchor",
        "conflicting_anchors", "unreadable",
    },
    "closure_status": {"closeable", "not_closeable", "review_required", "unreadable"},
}

_V4_CLARITY_ENUM = {"clear", "suspicious", "unreadable"}
_V4_CONFOUND_ENUM = {
    "none", "missing_view", "photo_angle", "head_posture",
    "dental_crowding", "dental_compensation", "prior_extraction_retraction",
    "ceph_without_tracing", "soft_tissue_thickness", "mixed",
}


def _v4_anchor(value: str = "unreadable", *, clarity: str = "unreadable",
               confound: str = "missing_view", evidence_ref: str = "orchestrator_fallback",
               role_note: str = "") -> dict:
    return {
        "value": value,
        "clarity": clarity,
        "confound": confound,
        "evidence_ref": evidence_ref,
        "role_note": role_note,
    }


def _v4_normalize_anchor(field: str, raw: object) -> tuple[dict, list[str]]:
    issues = []
    if not isinstance(raw, dict):
        return _v4_anchor(role_note="missing_anchor"), [f"{field}:missing_anchor"]

    value = raw.get("value")
    clarity = raw.get("clarity")
    confound = raw.get("confound")
    evidence_ref = raw.get("evidence_ref")
    role_note = raw.get("role_note")

    if value not in _V4_ANCHOR_VALUE_ENUMS[field]:
        issues.append(f"{field}:invalid_value:{value!r}")
        value = "unreadable"
    if clarity not in _V4_CLARITY_ENUM:
        issues.append(f"{field}:invalid_clarity:{clarity!r}")
        clarity = "unreadable"
    if confound not in _V4_CONFOUND_ENUM:
        issues.append(f"{field}:invalid_confound:{confound!r}")
        confound = "mixed"
    if not evidence_ref:
        issues.append(f"{field}:missing_evidence_ref")
        evidence_ref = "missing"
    if not role_note:
        issues.append(f"{field}:missing_role_note")
        role_note = "missing"
    return _v4_anchor(
        str(value), clarity=str(clarity), confound=str(confound),
        evidence_ref=str(evidence_ref), role_note=str(role_note),
    ), issues


def _v4_empty_side_packet(role: str) -> dict:
    return {
        "packet_role": role,
        "score": None,
        "anchors": {field: _v4_anchor(role_note="absent_or_unreadable") for field in _V4_ANCHOR_FIELDS},
        "supporting_evidence": [],
        "counter_evidence": [],
        "unresolved_anchors": list(_V4_ANCHOR_FIELDS),
    }


def _v4_normalize_side_packet(raw: object, role: str) -> tuple[dict, list[str]]:
    issues = []
    if not isinstance(raw, dict):
        return _v4_empty_side_packet(role), [f"{role}:missing_packet"]
    for wrapper_key in ("side_packet", "packet"):
        if isinstance(raw.get(wrapper_key), dict):
            raw = raw[wrapper_key]
            break

    anchors_raw = raw.get("anchors")
    if not isinstance(anchors_raw, dict):
        anchors_raw = raw
        issues.append(f"{role}:anchors_wrapper_missing")

    anchors = {}
    unresolved = []
    for field in _V4_ANCHOR_FIELDS:
        anchor, anchor_issues = _v4_normalize_anchor(field, anchors_raw.get(field))
        anchors[field] = anchor
        issues.extend(f"{role}:{issue}" for issue in anchor_issues)
        if anchor["value"] == "unreadable" or anchor["clarity"] == "unreadable":
            unresolved.append(field)

    result = {
        "packet_role": str(raw.get("packet_role") or role),
        "score": raw.get("score"),
        "anchors": anchors,
        "supporting_evidence": raw.get("supporting_evidence") if isinstance(raw.get("supporting_evidence"), list) else [],
        "counter_evidence": raw.get("counter_evidence") if isinstance(raw.get("counter_evidence"), list) else [],
        "unresolved_anchors": unresolved,
    }
    ebs = raw.get("extraction_blocker_signals")
    if isinstance(ebs, dict):
        result["extraction_blocker_signals"] = ebs
    return result, issues


def _v4_normalize_non_sagittal_packet(raw: object) -> tuple[dict, list[str]]:
    if not isinstance(raw, dict):
        return {"packet_role": "non_sagittal", "interference_flags": []}, ["non_sagittal:missing_packet"]
    if isinstance(raw.get("packet"), dict):
        raw = raw["packet"]
    forbidden = []
    clean = {}
    for key, value in raw.items():
        lower = str(key).lower()
        if any(token in lower for token in ("sagittal_vote", "final_direction", "gate_result", "face_class")):
            forbidden.append(str(key))
            continue
        clean[key] = value
    clean.setdefault("packet_role", "non_sagittal")
    issues = [f"non_sagittal:forbidden_direction_field_stripped:{k}" for k in forbidden]
    return clean, issues


def _v4_runtime_side_packets_from_stage_a(ir_output: dict) -> tuple[dict, list[str]]:
    """Normalize Phase-I IR packets attached to Stage-A output.

    Gate derivation reads only the structured anchors. If packets are absent or malformed,
    the normalized packet contains unreadable anchors and the gate fails closed.
    """
    packet_issues = []
    existing = ir_output.get("sagittal_consensus_packet") or {}
    raw_existing = existing.get("raw_packets") if isinstance(existing, dict) else None
    raw = raw_existing if isinstance(raw_existing, dict) else None

    if raw is None:
        for key in ("v4_ir_packets", "ir_packets", "raw_packets"):
            candidate = ir_output.get(key)
            if isinstance(candidate, dict) and ("convex" in candidate or "concave" in candidate):
                raw = candidate
                break

    if raw is None:
        raw = {}
        packet_issues.append("raw_packets:missing")

    convex, convex_issues = _v4_normalize_side_packet(raw.get("convex"), "convex_side_packet")
    concave, concave_issues = _v4_normalize_side_packet(raw.get("concave"), "concave_side_packet")
    non_sagittal, non_issues = _v4_normalize_non_sagittal_packet(raw.get("non_sagittal"))
    packet_issues.extend(convex_issues + concave_issues + non_issues)
    return {"convex": convex, "concave": concave, "non_sagittal": non_sagittal}, packet_issues


def _v4_anchor_values(raw_packets: dict, field: str) -> list[dict]:
    values = []
    for side in ("convex", "concave"):
        packet = raw_packets.get(side) or {}
        anchor = (packet.get("anchors") or {}).get(field)
        if isinstance(anchor, dict):
            values.append(anchor)
    return values


def _v4_any_value(raw_packets: dict, field: str, allowed: set[str],
                  *, clear_only: bool = False) -> bool:
    for anchor in _v4_anchor_values(raw_packets, field):
        if anchor.get("value") in allowed:
            if clear_only and anchor.get("clarity") != "clear":
                continue
            return True
    return False


def _v4_first_value(raw_packets: dict, field: str) -> str:
    for anchor in _v4_anchor_values(raw_packets, field):
        value = anchor.get("value")
        if value and value != "unreadable":
            return value
    return "unreadable"


def _v4_normalize_compensated_positive_overjet(raw_packets: dict) -> list[dict]:
    """Promote a narrow machine-readable compensation pattern to the masked-risk enum."""
    events = []
    concave = raw_packets.get("concave") or {}
    anchors = concave.get("anchors") or {}
    overjet = anchors.get("overjet_sign") or {}
    arch = anchors.get("arch_palate") or {}
    incisor = anchors.get("incisor_compensation") or {}

    source_value = incisor.get("value")
    hit = (
        overjet.get("value") == "positive"
        and overjet.get("confound") == "dental_compensation"
        and arch.get("value") == "strict_narrow_high"
        and source_value in {"bimax_proclination", "upper_proclination"}
    )
    if hit:
        incisor["value"] = "compensated_positive_overjet"
        events.append({
            "packet": "concave",
            "field": "incisor_compensation",
            "source_value": source_value,
            "normalized_value": "compensated_positive_overjet",
            "inputs": {
                "overjet_sign": overjet.get("value"),
                "overjet_confound": overjet.get("confound"),
                "arch_palate": arch.get("value"),
                "incisor_compensation": source_value,
            },
        })
    return events


def _v4_walter_masked_concave_risk_components(raw_packets: dict) -> list[str]:
    """Return non-measure Walter anchors that support masked-concave review.

    These are supporting risk anchors only. The caller still requires a surface
    convex context plus a multi-anchor cluster before blocking clean closure.
    """
    components = []
    risk_map = (
        ("anterior_crossbite", {"present"}),
        ("posterior_crossbite", {"present_unilateral", "present_bilateral"}),
        ("overjet_depth", {"shallow_positive", "edge_to_edge", "reverse"}),
        ("paranasal_support", {"deficient"}),
        ("lip_ap_relation", {"lower_lip_ahead"}),
        ("chin_prominence", {"prominent_or_forward"}),
        ("mandibular_soft_tissue_volume", {"large"}),
        ("maxillary_arch_form", {"narrow_or_high_palate"}),
        ("upper_incisor_compensation", {"proclined"}),
        ("lower_incisor_compensation", {"retroclined_or_upright_compensation"}),
        ("molar_relation_sagittal", {"mesial"}),
    )
    for field, risky_values in risk_map:
        for anchor in _v4_anchor_values(raw_packets, field):
            value = anchor.get("value")
            if value in risky_values:
                component = f"{field}={value}"
                if component not in components:
                    components.append(component)
                break
    return components


def _v4_routing_intent(gate_result: str) -> dict:
    mapping = {
        "true_convex_closed": {
            "cm_retrieval_intent": ["true_convex", "bimaxillary_protrusion"],
            "kc_retrieval_intent": ["true_convex", "bimaxillary_protrusion"],
            "surface_family_parallel": False,
            "masked_concave_review_required": False,
            "frank_concave_review_required": False,
        },
        "maxillary_origin_masked_concave_review_required": {
            "cm_retrieval_intent": ["maxillary_origin_masked_concave_review", "surface_convex_or_bimaxillary_family"],
            "kc_retrieval_intent": ["maxillary_origin_masked_concave_review", "surface_convex_or_bimaxillary_family"],
            "surface_family_parallel": True,
            "masked_concave_review_required": True,
            "frank_concave_review_required": False,
        },
        "frank_concave_classIII_review_required": {
            "cm_retrieval_intent": ["frank_concave", "class_III"],
            "kc_retrieval_intent": ["frank_concave", "class_III"],
            "surface_family_parallel": False,
            "masked_concave_review_required": False,
            "frank_concave_review_required": True,
        },
        "unresolved_not_closeable_needs_review": {
            "cm_retrieval_intent": ["review_missing_or_conflicting_evidence"],
            "kc_retrieval_intent": ["review_missing_or_conflicting_evidence"],
            "surface_family_parallel": False,
            "masked_concave_review_required": False,
            "frank_concave_review_required": False,
        },
    }
    return mapping.get(gate_result, mapping["unresolved_not_closeable_needs_review"])


def _derive_v4_sagittal_gate(ir_output: dict) -> dict:
    raw_packets, packet_issues = _v4_runtime_side_packets_from_stage_a(ir_output)
    trace = []

    def rule(rule_id: str, hit: bool, inputs: dict, outcome: str) -> bool:
        trace.append({"rule_id": rule_id, "hit": bool(hit), "inputs": inputs, "outcome": outcome})
        return hit

    if packet_issues:
        gate_result = "unresolved_not_closeable_needs_review"
        rule(
            "packet_schema_valid",
            True,
            {"packet_issues": packet_issues},
            "fail_closed_unresolved",
        )
        return {
            "schema_version": "v4_phase1.0",
            "gate_result": gate_result,
            "closure_status": "not_closeable",
            "derived_by": "deterministic_orchestrator",
            "rule_trace": trace,
            "blocking_reasons": packet_issues,
            "routing_intent": _v4_routing_intent(gate_result),
            "raw_packets": raw_packets,
            "observability": {"closure_blockers": packet_issues},
        }

    normalization_events = _v4_normalize_compensated_positive_overjet(raw_packets)
    if normalization_events:
        trace.append({
            "rule_id": "packet_contract_compensated_positive_overjet_normalization",
            "hit": True,
            "inputs": {"normalization_events": normalization_events},
            "outcome": "incisor_compensation_enum_normalized",
        })

    overjet_reverse = _v4_any_value(raw_packets, "overjet_sign", {"reverse"})
    overjet_edge = _v4_any_value(raw_packets, "overjet_sign", {"edge_to_edge"})
    chin_ant = _v4_any_value(raw_packets, "skeletal_chin_ap", {"anterior_prognathic"})
    molar_iii_clear = _v4_any_value(raw_packets, "molar_canine_relation", {"class_III"}, clear_only=True)
    molar_iii_any = _v4_any_value(raw_packets, "molar_canine_relation", {"class_III"})

    frank_hit = rule(
        "frank_concave_classIII_hard_anchor",
        overjet_reverse or chin_ant or molar_iii_clear or (overjet_edge and (molar_iii_any or chin_ant)),
        {
            "overjet": _v4_first_value(raw_packets, "overjet_sign"),
            "chin": _v4_first_value(raw_packets, "skeletal_chin_ap"),
            "molar": _v4_first_value(raw_packets, "molar_canine_relation"),
        },
        "frank_review" if (overjet_reverse or chin_ant or molar_iii_clear or (overjet_edge and (molar_iii_any or chin_ant))) else "not_hit",
    )
    if frank_hit:
        gate_result = "frank_concave_classIII_review_required"
        return {
            "schema_version": "v4_phase1.0",
            "gate_result": gate_result,
            "closure_status": "review_required",
            "derived_by": "deterministic_orchestrator",
            "rule_trace": trace,
            "blocking_reasons": ["frank_concave_classIII_hard_anchor"],
            "routing_intent": _v4_routing_intent(gate_result),
            "raw_packets": raw_packets,
            "observability": {"closure_blockers": ["frank_concave_classIII_hard_anchor"]},
        }

    surface_context = (
        _v4_any_value(raw_packets, "overjet_sign", {"positive"})
        or _v4_any_value(raw_packets, "surface_protrusion_signal", {"mild", "strong"})
    )
    walter_risk_components = _v4_walter_masked_concave_risk_components(raw_packets)
    source_risk_strong_context = (
        _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"})
        or _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"})
        or _v4_any_value(raw_packets, "prior_extraction_retraction", {"present"})
    )
    risk_components = []
    if _v4_any_value(raw_packets, "maxillary_support", {"deficient"}):
        risk_components.append("maxillary_support=deficient")
    elif (_v4_any_value(raw_packets, "maxillary_support", {"unreadable"})
          and source_risk_strong_context):
        risk_components.append("maxillary_support=unreadable_with_source_risk_context")
    if _v4_any_value(raw_packets, "skeletal_chin_ap", {"neutral_not_retrusive"}):
        risk_components.append("skeletal_chin_ap=neutral_not_retrusive")
    if _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"}):
        risk_components.append("arch_palate=strict_narrow_high")
    if _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"}):
        risk_components.append("incisor_compensation=compensated_positive_overjet")
    if _v4_any_value(raw_packets, "prior_extraction_retraction", {"present"}):
        risk_components.append("prior_extraction_retraction=present")
    risk_components.extend(walter_risk_components)

    source_gap_only = (
        _v4_any_value(raw_packets, "prior_extraction_retraction", {"source_gap_unknown"})
        and not risk_components
    )
    generic_crowding_only = (
        _v4_any_value(raw_packets, "arch_palate", {"generic_crowding_only"})
        and not [r for r in risk_components if not r.startswith("arch_palate=")]
    )
    masked_hit = rule(
        "maxillary_origin_masked_concave_review",
        surface_context and len(risk_components) >= 2 and not source_gap_only and not generic_crowding_only,
        {
            "surface_context": surface_context,
            "risk_components": risk_components,
            "walter_risk_components": walter_risk_components,
            "source_gap_only": source_gap_only,
            "generic_crowding_only": generic_crowding_only,
        },
        "masked_concave_review" if surface_context and len(risk_components) >= 2 and not source_gap_only and not generic_crowding_only else "not_hit",
    )
    if masked_hit:
        gate_result = "maxillary_origin_masked_concave_review_required"
        return {
            "schema_version": "v4_phase1.0",
            "gate_result": gate_result,
            "closure_status": "review_required",
            "derived_by": "deterministic_orchestrator",
            "rule_trace": trace,
            "blocking_reasons": risk_components,
            "routing_intent": _v4_routing_intent(gate_result),
            "raw_packets": raw_packets,
            "observability": {"closure_blockers": risk_components},
        }

    true_convex_inputs = {
        "surface_context": surface_context,
        "overjet": _v4_first_value(raw_packets, "overjet_sign"),
        "chin": _v4_first_value(raw_packets, "skeletal_chin_ap"),
        "maxillary_support": _v4_first_value(raw_packets, "maxillary_support"),
        "incisor_compensation": _v4_first_value(raw_packets, "incisor_compensation"),
        "prior_retraction": _v4_first_value(raw_packets, "prior_extraction_retraction"),
        "surface_protrusion_ignored_as_closure_qualification": True,
        "score_ignored": True,
    }
    true_convex_hit = rule(
        "true_convex_closure_all_clear",
        (
            surface_context
            and not overjet_edge
            and not _v4_any_value(raw_packets, "maxillary_support", {"deficient"})
            and not _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"})
            and not _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"})
            and not _v4_any_value(raw_packets, "prior_extraction_retraction", {"present"})
            and (
                _v4_any_value(raw_packets, "skeletal_chin_ap", {"retrusive_classII_like"})
                or _v4_any_value(raw_packets, "incisor_compensation", {"bimax_proclination", "upper_proclination"})
                or _v4_any_value(raw_packets, "maxillary_support", {"adequate"})
            )
        ),
        true_convex_inputs,
        "true_convex_closed" if (
            surface_context
            and not overjet_edge
            and not _v4_any_value(raw_packets, "maxillary_support", {"deficient"})
            and not _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"})
            and not _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"})
            and not _v4_any_value(raw_packets, "prior_extraction_retraction", {"present"})
            and (
                _v4_any_value(raw_packets, "skeletal_chin_ap", {"retrusive_classII_like"})
                or _v4_any_value(raw_packets, "incisor_compensation", {"bimax_proclination", "upper_proclination"})
                or _v4_any_value(raw_packets, "maxillary_support", {"adequate"})
            )
        ) else "not_hit",
    )
    gate_result = "true_convex_closed" if true_convex_hit else "unresolved_not_closeable_needs_review"
    blockers = [] if true_convex_hit else ["missing_or_conflicting_required_closure_anchors"]
    return {
        "schema_version": "v4_phase1.0",
        "gate_result": gate_result,
        "closure_status": "closeable" if true_convex_hit else "not_closeable",
        "derived_by": "deterministic_orchestrator",
        "rule_trace": trace,
        "blocking_reasons": blockers,
        "routing_intent": _v4_routing_intent(gate_result),
        "raw_packets": raw_packets,
        "observability": {"closure_blockers": blockers},
    }


def _attach_v4_sagittal_gate(ir_output: dict) -> dict:
    packet = _derive_v4_sagittal_gate(ir_output)
    return {**ir_output, "sagittal_consensus_packet": packet}


def _sc_text_clean_true_convex_closure(clinician_output: dict) -> bool:
    import re as _re
    sections = clinician_output.get("sections") or {}
    full_text = "\n\n".join(str(v) for v in sections.values() if v)
    clean_convex = bool(_re.search(
        r"明确.{0,12}(凸面|突面|双颌前突)|"
        r"(凸面|突面|双颌前突).{0,12}(明确|锁定|确诊|成立)|"
        r"true[-_\s]?convex|clean\s+true\s+convex",
        full_text,
        _re.IGNORECASE,
    ))
    guarded = bool(_re.search(
        r"暂判|待.*(头影|描记|精测|复核)|不能.*锁|未.*排除|review|required|复核|需查",
        full_text,
        _re.IGNORECASE,
    ))
    return clean_convex and not guarded


def _apply_v4_gate_consistency(clinician_output: dict, gate_packet: Optional[dict]) -> dict:
    if not isinstance(gate_packet, dict):
        return {**clinician_output, "v4_gate_consistency_status": {"status": "not_evaluated"}}
    gate_result = gate_packet.get("gate_result")
    disputes = clinician_output.get("anchor_dispute") or []
    silent_clean_closure = (
        gate_result in _V4_REVIEW_GATES
        and _sc_text_clean_true_convex_closure(clinician_output)
        and not disputes
    )
    status = (
        "violation_clean_true_convex_against_review_gate"
        if silent_clean_closure else "consistent"
    )
    return {
        **clinician_output,
        "v4_gate_consistency_status": {
            "gate_result": gate_result,
            "status": status,
            "rule_trace": gate_packet.get("rule_trace") or [],
            "routing_intent": gate_packet.get("routing_intent") or {},
        },
    }


def _v4_report_text(final_output: dict) -> str:
    sections = final_output.get("sections") or {}
    section_text = "\n\n".join(str(v) for v in sections.values() if v)
    rendered = final_output.get("rendered_markdown") or ""
    return f"{section_text}\n\n{rendered}"


def _v4_advisory_options_visible(text: str) -> bool:
    return (
        ("MSE" in text or "骨性扩弓" in text)
        and ("前牵" in text or "上颌前牵" in text)
        and ("正颌" in text or "联合评估" in text)
    )


def _v4_final_report_contradictions(final_output: dict, gate_packet: dict,
                                    treatment_advisory: Optional[dict]) -> list[str]:
    import re as _re

    text = _v4_report_text(final_output)
    gate_result = (gate_packet or {}).get("gate_result")
    advisory_active = bool((treatment_advisory or {}).get("visible"))
    contradictions: list[str] = []
    if gate_result == "frank_concave_classIII_review_required":
        if _re.search(r"(普通)?(突面|凸面)倾向|年轻成人突面|按普通(突面|凸面).{0,8}(处理|诊断|主线|路径)", text):
            contradictions.append("ordinary_protrusive_or_convex_main_label")
        if _re.search(r"不宜.{0,8}(归入|进入).{0,8}(凹面|III|Class\s*III|上颌前牵|前牵)", text, _re.I):
            contradictions.append("excludes_concave_or_protraction_path")
        if _re.search(r"拔牙.{0,12}(强候选|主线|优先|第一|直接)|内收.{0,12}(强候选|主线|优先|第一|直接)", text):
            contradictions.append("retraction_or_extraction_first_main_path")
    if advisory_active:
        if _re.search(r"不宜.{0,12}(上颌前牵|前牵|MSE)|排除.{0,12}(上颌前牵|前牵|MSE)", text):
            contradictions.append("excludes_active_advisory_option")
        if not _v4_advisory_options_visible(text):
            contradictions.append("missing_mse_protraction_orthognathic_options")
    return sorted(set(contradictions))


def _v4_compliant_review_sections(gate_packet: dict, treatment_advisory: Optional[dict],
                                  contradictions: list[str]) -> dict:
    advisory = treatment_advisory or {}
    advisory_text = advisory.get("message") or (
        "成人凹面 / III 类复核病例需保留 MSE 骨性扩弓 + 上颌前牵作为可讨论的掩饰路径选项；"
        "成人效果有限，需结合 CBCT、骨缝和牙周条件评估，并保留正畸-正颌联合评估边界。"
    )
    return {
        "s1_临床推理": (
            "当前较支持的诊断讨论：本例应先按凹面 / III 类方向讨论，来源更偏上下源性凹面方向，"
            "下颌三角浅凹需要正式测量后确认；不能先当作单纯嘴突或普通突度病例来定方案。\n\n"
            "支持理由：现有资料中出现了 III 类 / 下颌来源相关线索，且磨牙关系、上颌弓形、"
            "前牙代偿和正覆盖深度共同提示矢状来源不能按普通突度解释。嘴突外观或正覆盖是容易误导的表面线索，"
            "更合适的解释是可能存在牙性代偿或假突表现，而不是直接排除凹面来源。"
        ),
        "s2_治疗路径": (
            "来源和亚型：目前可以把上下源性凹面方向作为诊断讨论主线；下颌三角浅凹、上颌横向/前后向不足参与程度、"
            "以及牙性代偿比例还没有完全确定，需要正式测量补齐后再分层。\n\n"
            "治疗分支影响：治疗应从这个诊断框架分流。若正式测量确认成人凹面、上颌横向不足或上颌前后向不足参与，"
            f"{advisory_text}"
            "若后续测量反证凹面来源，才回到突度/拥挤主导的内收或拔牙边界讨论。"
        ),
        "s4_要点提醒": (
            "仍缺的关键证据：正式头影测量、Coben 上下颌比例、CR-CO / 功能移位记录、标准左右侧咬合关系、"
            "口扫空间分析、CBCT 牙周骨板与根轴条件，以及既往拔牙或内收史。这些资料决定是上下源性凹面、"
            "下颌三角浅凹伴代偿，还是其他来源组合。\n\n"
            "下一步需要的资料：请补正式头影数值和 Coben 分析，记录 CR-CO 与小开口前导，"
            "补清晰侧方咬合照或口扫，确认上颌弓宽度 / 腭穹形态和后牙横向关系，"
            "并补 CBCT 或牙周资料评估前牙移动边界。资料补齐前，方案讨论应围绕上述诊断方向展开，"
            "同时保留正式测量后的修正空间。"
        ),
    }


def _apply_v4_final_report_compliance(final_output: dict, gate_packet: dict,
                                      treatment_advisory: Optional[dict],
                                      clinician_output: dict) -> dict:
    contradictions = _v4_final_report_contradictions(final_output, gate_packet, treatment_advisory)
    anchor_dispute = clinician_output.get("anchor_dispute") or []
    if not contradictions:
        return {
            **final_output,
            "v4_final_report_compliance": {
                "status": "consistent",
                "gate_result": (gate_packet or {}).get("gate_result"),
                "adult_concave_advisory_active": bool((treatment_advisory or {}).get("visible")),
                "contradictions": [],
                "anchor_dispute_present": bool(anchor_dispute),
            },
        }

    status = "conflict_reopen_required" if anchor_dispute else "guarded_replaced_silent_contradiction"
    compliance = {
        "status": status,
        "visible": True,
        "gate_result": (gate_packet or {}).get("gate_result"),
        "adult_concave_advisory_active": bool((treatment_advisory or {}).get("visible")),
        "contradictions": contradictions,
        "anchor_dispute_present": bool(anchor_dispute),
        "anchor_dispute": anchor_dispute,
        "message": (
            "SC disagrees with V4 gate/advisory -> reopen/review required"
            if anchor_dispute
            else "Final prose contradicted V4 gate/advisory without structured anchor_dispute; user-facing body replaced by V4 review guard."
        ),
    }
    if anchor_dispute:
        warning = (
            "## 诊断复核提示\n\n"
            "综合报告与上游影像证据存在方向不一致，需要医生先复核争议证据，再使用下方草稿。\n\n"
        )
        return {
            **final_output,
            "rendered_markdown": warning + (final_output.get("rendered_markdown") or ""),
            "sections": {
                "v4_conflict_warning": compliance["message"],
                **(final_output.get("sections") or {}),
            },
            "v4_final_report_compliance": compliance,
        }

    guarded_sections = _v4_compliant_review_sections(gate_packet, treatment_advisory, contradictions)
    guarded_markdown = "\n\n".join(
        f"## {title.replace('s1_', '').replace('s2_', '').replace('s4_', '')}\n\n{text}"
        for title, text in guarded_sections.items()
    )
    return {
        **final_output,
        "sections": guarded_sections,
        "rendered_markdown": guarded_markdown,
        "image_anchors": [],
        "v4_final_report_compliance": compliance,
        "v4_original_senior_clinician_report": {
            "sections": final_output.get("sections") or {},
            "rendered_markdown": final_output.get("rendered_markdown") or "",
        },
    }


def _run_v4_gate_consistency_floor(clinician_output: dict) -> list[dict]:
    status = clinician_output.get("v4_gate_consistency_status") or {}
    if status.get("status") != "violation_clean_true_convex_against_review_gate":
        return []
    return [{
        "rule_id": "v4_phase1_gate_consistency",
        "clinical_severity": "HIGH",
        "block": False,
        "message": (
            "V4 Phase I: review-required/not-closeable sagittal gate cannot silently become "
            "clean true-convex closure; SC must emit anchor_dispute + recompute_required=true"
        ),
    }]


def _v4_review_prompt(v4_phase1: dict) -> Optional[dict]:
    gate = (v4_phase1.get("v4_gate_packet") or {}) if isinstance(v4_phase1, dict) else {}
    sc = (v4_phase1.get("v4_sc_gate_consistency") or {}) if isinstance(v4_phase1, dict) else {}
    gate_result = gate.get("gate_result")
    anchor_dispute = bool(sc.get("anchor_dispute_present"))
    recompute_required = bool(sc.get("recompute_required"))
    review_required = (
        bool(gate_result and gate_result != "true_convex_closed")
        or sc.get("consistent") is False
        or anchor_dispute
        or recompute_required
    )
    if not review_required:
        return None
    return {
        "visible": True,
        "kind": "v4_phase1_review_required",
        "gate_result": gate_result,
        "sc_consistent": sc.get("consistent"),
        "anchor_dispute_present": anchor_dispute,
        "recompute_required": recompute_required,
        "message": (
            "诊断仍需复核：本案例存在尚未关闭的分型疑义或证据不足路径。"
            "请医生重点核查报告与影像依据。"
        ),
    }


def _history_text(case_payload: dict) -> str:
    parts = []
    for key in (
        "prior_treatment_history",
        "doctor_specific_question",
        "chief_complaint_doctor",
        "chief_complaint_patient",
        "context_notes",
        "notes",
    ):
        value = case_payload.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def _derive_v4_retreatment_history_packet(case_payload: dict, v4_gate_packet: dict) -> dict:
    """P0 #28 deterministic retreatment/extraction review gate.

    History can block high-confidence source closure, but missing history alone is not
    diagnostic and must not push first-treatment cases to human review.
    """
    import re as _re

    text = _history_text(case_payload)
    lowered = text.lower()
    raw_packets = v4_gate_packet.get("raw_packets") or {}

    no_pattern = r"(无|未|没有|否认|no|not)\s*(既往|二次|再次|复治|矫治|正畸|拔牙|extraction|retreatment)"
    has_negation = bool(_re.search(no_pattern, lowered, _re.IGNORECASE))
    extraction_hit = bool(_re.search(r"拔牙|拔除|减数|前磨牙|premolar|extraction|extract", lowered, _re.IGNORECASE))
    retreatment_hit = bool(_re.search(r"二次|再次|复治|重治|再治疗|second|retreatment|re-treatment", lowered, _re.IGNORECASE))
    retraction_hit = bool(_re.search(r"内收|关闭间隙|space\s*closure|retraction", lowered, _re.IGNORECASE))
    suspected_hit = bool(_re.search(r"疑似|可能|不详|记不清|未知|suspect|possible|unknown", lowered, _re.IGNORECASE))

    packet_prior_present = _v4_any_value(raw_packets, "prior_extraction_retraction", {"present"})
    packet_prior_unknown = _v4_any_value(raw_packets, "prior_extraction_retraction", {"source_gap_unknown", "unreadable"})

    if has_negation and not (extraction_hit and retreatment_hit):
        prior_treatment = "no"
        prior_extraction = "no"
        prior_retraction = "no"
        retreatment = "no"
        confidence = "clear"
    else:
        prior_treatment = "yes" if (retreatment_hit or extraction_hit or packet_prior_present) else "unknown"
        prior_extraction = "yes" if (extraction_hit or packet_prior_present) else "unknown"
        prior_retraction = "yes" if (retraction_hit or packet_prior_present) else "unknown"
        retreatment = "yes" if retreatment_hit else "unknown"
        confidence = "clear" if (retreatment_hit and (extraction_hit or packet_prior_present)) else (
            "suspected" if (suspected_hit or packet_prior_unknown or extraction_hit or retreatment_hit or packet_prior_present) else "missing"
        )

    confirmed_review = retreatment == "yes" and prior_extraction == "yes" and confidence == "clear"
    suspected_review = (confidence == "suspected" and (prior_extraction == "yes" or prior_retraction == "yes" or retreatment == "yes"))
    review_required = bool(confirmed_review or suspected_review)

    reasons = []
    if confirmed_review:
        reasons.append("second_treatment_and_prior_extraction")
    if suspected_review:
        reasons.append("suspected_extraction_retraction_history")
    if not reasons and confidence == "missing":
        reasons.append("history_missing_no_gate")

    return {
        "schema_version": "v4_reassessment_history.0",
        "derived_by": "deterministic_orchestrator",
        "prior_orthodontic_treatment": prior_treatment,
        "prior_extraction": prior_extraction,
        "prior_retraction_or_space_closure": prior_retraction,
        "second_treatment_or_retreatment": retreatment,
        "history_confidence": confidence,
        "review_required": review_required,
        "source_attribution_cap": "review_candidate" if review_required else None,
        "human_review_message": (
            "既往拔牙或二次矫治会明显改变面型来源判断，需要人工复核/补病史。"
            if review_required else None
        ),
        "reasons": reasons,
    }


def _apply_v4_retreatment_history_gate(v4_gate_packet: dict, history_packet: dict) -> dict:
    if not history_packet.get("review_required"):
        return v4_gate_packet
    gate = {**(v4_gate_packet or {})}
    previous = gate.get("gate_result")
    gate["gate_result"] = "unresolved_not_closeable_needs_review"
    gate["closure_status"] = "review_required"
    blockers = list(gate.get("blocking_reasons") or [])
    for reason in history_packet.get("reasons") or []:
        blocker = f"retreatment_history={reason}"
        if blocker not in blockers:
            blockers.append(blocker)
    gate["blocking_reasons"] = blockers
    gate["routing_intent"] = _v4_routing_intent(gate["gate_result"])
    trace = list(gate.get("rule_trace") or [])
    trace.append({
        "rule_id": "retreatment_prior_extraction_review_gate",
        "hit": True,
        "inputs": {
            "previous_gate_result": previous,
            "prior_extraction": history_packet.get("prior_extraction"),
            "second_treatment_or_retreatment": history_packet.get("second_treatment_or_retreatment"),
            "history_confidence": history_packet.get("history_confidence"),
            "reasons": history_packet.get("reasons") or [],
        },
        "outcome": "review_required_human_assisted",
    })
    gate["rule_trace"] = trace
    observability = dict(gate.get("observability") or {})
    observability["retreatment_history_gate"] = history_packet
    observability["closure_blockers"] = blockers
    gate["observability"] = observability
    return gate


def _v4_source_anchor(field: str, value: str, *, side: str = "phase1",
                      role: str = "supporting", note: str = "") -> dict:
    return {
        "anchor": f"{field}={value}",
        "field": field,
        "value": value,
        "side": side,
        "role": role,
        "note": note,
    }


def _derive_v4_source_attribution(v4_gate_packet: dict) -> dict:
    """Additive Phase-II source attribution from Phase-I structured packets.

    This packet is deliberately read-only with respect to Phase I: it explains likely
    source candidates but never rewrites the gate result or closes a review case.
    """
    raw_packets = v4_gate_packet.get("raw_packets") or {}
    gate_result = v4_gate_packet.get("gate_result")
    trace = []

    def values(field: str) -> list[dict]:
        return _v4_anchor_values(raw_packets, field)

    decisive = []
    supporting = []
    conflicting = []
    missing = []

    def add_support(field: str, value: str, *, side: str = "phase1", note: str = ""):
        supporting.append(_v4_source_anchor(field, value, side=side, role="supporting", note=note))

    def add_decisive(field: str, value: str, *, side: str = "phase1", note: str = ""):
        decisive.append(_v4_source_anchor(field, value, side=side, role="decisive", note=note))

    def add_conflict(field: str, value: str, *, side: str = "phase1", note: str = ""):
        conflicting.append(_v4_source_anchor(field, value, side=side, role="conflicting", note=note))

    def add_missing(field: str, *, reason: str):
        missing.append({"field": field, "reason": reason})

    max_support_def = _v4_any_value(raw_packets, "maxillary_support", {"deficient"})
    max_support_adequate = _v4_any_value(raw_packets, "maxillary_support", {"adequate"})
    max_support_unreadable = _v4_any_value(raw_packets, "maxillary_support", {"unreadable"})
    arch_strict = _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"})
    arch_generic = _v4_any_value(raw_packets, "arch_palate", {"generic_crowding_only"})
    compensated_oj = _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"})
    bimax_or_upper = _v4_any_value(raw_packets, "incisor_compensation", {"bimax_proclination", "upper_proclination"})
    surface = _v4_any_value(raw_packets, "surface_protrusion_signal", {"mild", "strong"})
    positive_oj = _v4_any_value(raw_packets, "overjet_sign", {"positive"})
    reverse_or_edge = _v4_any_value(raw_packets, "overjet_sign", {"reverse", "edge_to_edge"})
    chin_ant = _v4_any_value(raw_packets, "skeletal_chin_ap", {"anterior_prognathic"})
    chin_neutral = _v4_any_value(raw_packets, "skeletal_chin_ap", {"neutral_not_retrusive"})
    chin_class2 = _v4_any_value(raw_packets, "skeletal_chin_ap", {"retrusive_classII_like"})
    molar_iii = _v4_any_value(raw_packets, "molar_canine_relation", {"class_III"})
    molar_confounded = _v4_any_value(raw_packets, "molar_canine_relation", {"confounded", "unreadable"})
    prior_unknown = _v4_any_value(raw_packets, "prior_extraction_retraction", {"source_gap_unknown", "unreadable"})

    if max_support_def:
        add_decisive("maxillary_support", "deficient", note="structured maxillary deficiency anchor")
    if chin_ant:
        add_decisive("skeletal_chin_ap", "anterior_prognathic", note="mandibular/Class III source anchor")
    if molar_iii:
        add_decisive("molar_canine_relation", "class_III", note="Class III occlusal source anchor")
    if compensated_oj:
        add_decisive(
            "incisor_compensation", "compensated_positive_overjet",
            note="compensation explains why positive overjet may hide source",
        )

    if arch_strict:
        add_support("arch_palate", "strict_narrow_high", note="supporting maxillary-source suspicion only")
    if arch_generic:
        add_conflict("arch_palate", "generic_crowding_only", note="ordinary crowding is not maxillary-source evidence")
    if surface:
        add_support("surface_protrusion_signal", _v4_first_value(raw_packets, "surface_protrusion_signal"))
    if positive_oj:
        add_support("overjet_sign", "positive")
    if bimax_or_upper:
        add_support("incisor_compensation", _v4_first_value(raw_packets, "incisor_compensation"))
    if chin_neutral:
        add_support("skeletal_chin_ap", "neutral_not_retrusive")
    if chin_class2:
        add_support("skeletal_chin_ap", "retrusive_classII_like")
    if reverse_or_edge:
        add_support("overjet_sign", _v4_first_value(raw_packets, "overjet_sign"))
    if max_support_adequate:
        add_conflict("maxillary_support", "adequate", note="argues against primary maxillary deficiency")

    if max_support_unreadable:
        add_missing("maxillary_support", reason="maxillary support not decisive/readable")
    if molar_confounded:
        add_missing("molar_canine_relation", reason="molar/canine source relation confounded or unreadable")
    if prior_unknown:
        add_missing("prior_extraction_retraction", reason="prior extraction/retraction history is source gap")

    candidate = "unresolved"
    level = "unresolved"
    confidence = "low"
    cannot_close_reason = "missing_or_conflicting_source_anchors"

    maxillary_candidate = (
        gate_result == "maxillary_origin_masked_concave_review_required"
        and (max_support_def or (arch_strict and compensated_oj) or (arch_strict and surface and positive_oj))
    )
    upper_lower_fsp19_candidate = (
        gate_result == "maxillary_origin_masked_concave_review_required"
        and max_support_def
        and compensated_oj
        and positive_oj
        and (chin_neutral or chin_ant or molar_iii)
    )
    class_iii_candidate = gate_result == "frank_concave_classIII_review_required" and (
        chin_ant or molar_iii or reverse_or_edge
    )
    true_convex_candidate = gate_result == "true_convex_closed" and surface

    if upper_lower_fsp19_candidate:
        candidate = "upper_lower_source_candidate"
        level = "review_candidate"
        confidence = "medium" if (max_support_def and compensated_oj and chin_neutral) else "low"
        cannot_close_reason = "formal_measurement_and_occlusion_required"
    elif maxillary_candidate:
        candidate = "maxillary_primary_candidate"
        level = "likely" if max_support_def and (compensated_oj or chin_neutral) else "review_candidate"
        confidence = "medium" if decisive else "low"
        cannot_close_reason = (
            "review_required_supporting_anchors_only"
            if level == "review_candidate" else "phase1_review_gate_still_visible"
        )
    elif class_iii_candidate:
        candidate = "mandibular_primary_candidate"
        level = "likely" if (chin_ant and (molar_iii or reverse_or_edge)) else "review_candidate"
        confidence = "medium" if decisive else "low"
        cannot_close_reason = (
            "class_III_source_review_required"
            if level == "review_candidate" else "phase1_review_gate_still_visible"
        )
    elif true_convex_candidate:
        if bimax_or_upper:
            candidate = "bimaxillary_candidate"
            level = "likely" if max_support_adequate and not arch_strict and not compensated_oj else "review_candidate"
            confidence = "medium"
            cannot_close_reason = "phase1_clean_gate_explanation_only"
        else:
            candidate = "dental_compensation_dominant"
            level = "review_candidate"
            confidence = "low"
            cannot_close_reason = "insufficient_decisive_source_anchors"

    if gate_result == "unresolved_not_closeable_needs_review" and decisive:
        level = "review_candidate"
        confidence = "low"
        cannot_close_reason = "phase1_unresolved_gate_preserved"

    high_allowed = bool(decisive and len(decisive) >= 2 and not missing and not conflicting)
    if confidence == "high" and not high_allowed:
        confidence = "medium"

    trace.append({
        "rule_id": "phase2_source_attribution_read_only",
        "hit": candidate != "unresolved",
        "inputs": {
            "phase1_gate_result": gate_result,
            "decisive_count": len(decisive),
            "supporting_count": len(supporting),
            "conflicting_count": len(conflicting),
            "missing_count": len(missing),
            "upper_lower_fsp19_candidate": upper_lower_fsp19_candidate,
        },
        "outcome": candidate,
    })

    return {
        "schema_version": "v4_phase2.0",
        "derived_by": "deterministic_orchestrator",
        "source_candidate": candidate,
        "attribution_level": level,
        "confidence": confidence,
        "decisive_anchors": decisive,
        "supporting_anchors": supporting,
        "conflicting_anchors": conflicting,
        "missing_required_anchors": missing,
        "cannot_close_reason": cannot_close_reason,
        "closure_state": "review_required" if candidate == "upper_lower_source_candidate" else None,
        "rule_trace": trace,
        "phase1_gate_result_read_only": gate_result,
    }


def _cap_v4_source_for_retreatment(v4_source_packet: dict, history_packet: dict) -> dict:
    if not history_packet.get("review_required"):
        return v4_source_packet
    packet = {**v4_source_packet}
    if packet.get("attribution_level") == "likely":
        packet["attribution_level"] = "review_candidate"
    if packet.get("confidence") == "high":
        packet["confidence"] = "medium"
    missing = list(packet.get("missing_required_anchors") or [])
    missing.append({
        "field": "prior_treatment_history",
        "reason": "retreatment/extraction history blocks automatic source closure",
    })
    packet["missing_required_anchors"] = missing
    packet["cannot_close_reason"] = "retreatment_extraction_history_requires_human_review"
    trace = list(packet.get("rule_trace") or [])
    trace.append({
        "rule_id": "phase2_retreatment_history_cap",
        "hit": True,
        "inputs": history_packet,
        "outcome": "review_candidate_max",
    })
    packet["rule_trace"] = trace
    return packet


def _derive_v4_shengang_subtype(v4_gate_packet: dict, v4_source_packet: dict) -> dict:
    raw_packets = v4_gate_packet.get("raw_packets") or {}
    gate_result = v4_gate_packet.get("gate_result")
    concave_scope = gate_result in {
        "frank_concave_classIII_review_required",
        "maxillary_origin_masked_concave_review_required",
    }
    evidence_for = []
    evidence_against = []
    missing = []

    def add_for(anchor: str):
        if anchor not in evidence_for:
            evidence_for.append(anchor)

    def add_against(anchor: str):
        if anchor not in evidence_against:
            evidence_against.append(anchor)

    def add_missing(anchor: str):
        if anchor not in missing:
            missing.append(anchor)

    max_def = _v4_any_value(raw_packets, "maxillary_support", {"deficient"})
    para_def = _v4_any_value(raw_packets, "paranasal_support", {"deficient"})
    arch_narrow = _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"}) or _v4_any_value(
        raw_packets, "maxillary_arch_form", {"narrow_or_high_palate"}
    )
    chin_forward = _v4_any_value(raw_packets, "skeletal_chin_ap", {"anterior_prognathic"}) or _v4_any_value(
        raw_packets, "chin_prominence", {"prominent_or_forward"}
    )
    mandibular_large = _v4_any_value(raw_packets, "mandibular_soft_tissue_volume", {"large"})
    reverse_or_edge = _v4_any_value(raw_packets, "overjet_sign", {"reverse", "edge_to_edge"}) or _v4_any_value(
        raw_packets, "overjet_depth", {"reverse", "edge_to_edge"}
    )
    compensated_positive = _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"}) or _v4_any_value(
        raw_packets, "overjet_sign", {"positive"}
    )
    positive_overjet = _v4_any_value(raw_packets, "overjet_sign", {"positive"})
    chin_neutral = _v4_any_value(raw_packets, "skeletal_chin_ap", {"neutral_not_retrusive"})
    fsp19_upper_lower_shallow = (
        concave_scope
        and max_def
        and compensated_positive
        and positive_overjet
        and chin_neutral
    )

    if max_def:
        add_for("maxillary_support=deficient")
    if para_def:
        add_for("paranasal_support=deficient")
    if arch_narrow:
        add_for("maxillary_arch_or_palate=narrow_high")
    if chin_forward:
        add_for("mandibular_ap_or_chin=forward")
    if mandibular_large:
        add_for("mandibular_soft_tissue_volume=large")
    if compensated_positive:
        add_for("positive_overjet_or_compensation=false_protrusive_risk")
    if _v4_any_value(raw_packets, "maxillary_support", {"adequate"}):
        add_against("maxillary_support=adequate")
    if _v4_any_value(raw_packets, "chin_prominence", {"not_prominent", "retrusive"}):
        add_against(f"chin_prominence={_v4_first_value(raw_packets, 'chin_prominence')}")

    family = "unresolved"
    subtype = "unresolved"
    if concave_scope:
        if fsp19_upper_lower_shallow:
            family = "skeletal_upper_lower_source"
        elif max_def and (chin_forward or mandibular_large):
            family = "skeletal_upper_lower_source"
        elif max_def or (para_def and arch_narrow):
            family = "skeletal_maxillary_source"
        elif reverse_or_edge and not (max_def or chin_forward):
            family = "alveolar"
        elif _v4_any_value(raw_packets, "overjet_sign", {"edge_to_edge"}):
            family = "positional"

        if family == "skeletal_upper_lower_source":
            subtype = "triangular_deep_concave" if (reverse_or_edge and chin_forward and mandibular_large) else "triangular_shallow_concave"
        elif compensated_positive and family in {"skeletal_maxillary_source", "unresolved"}:
            subtype = "true_concave_false_protrusive_risk"

    if not concave_scope:
        add_missing("concave_or_review_concave_gate")
    if concave_scope and family == "unresolved":
        add_missing("decisive_source_subtype_anchors")
    if concave_scope and subtype == "unresolved":
        add_missing("upper_lower_subtype_anchors")
    if family == "skeletal_upper_lower_source" and subtype == "triangular_shallow_concave":
        for item in ("formal ceph", "Coben", "occlusion model", "CR-CO functional record"):
            add_missing(item)

    return {
        "schema_version": "v4_shengang_subtype.0",
        "derived_by": "deterministic_orchestrator",
        "scope_applies": concave_scope,
        "shen_concave_family": family,
        "upper_lower_subtype": subtype,
        "subtype_closure_status": "review_required" if subtype == "triangular_shallow_concave" else None,
        "calibration_boundary": (
            "formal ceph/Coben/occlusion/CR-CO required"
            if subtype == "triangular_shallow_concave" else None
        ),
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "missing_evidence": missing,
        "phase1_gate_result_read_only": gate_result,
        "source_candidate_read_only": v4_source_packet.get("source_candidate"),
        "rule_trace": [{
            "rule_id": "shengang_concave_subtype_review_scope_only",
            "hit": concave_scope and family != "unresolved",
            "inputs": {
                "gate_result": gate_result,
                "maxillary_deficiency": max_def,
                "paranasal_deficiency": para_def,
                "arch_narrow": arch_narrow,
                "chin_forward": chin_forward,
                "chin_neutral": chin_neutral,
                "mandibular_large": mandibular_large,
                "compensated_positive": compensated_positive,
                "fsp19_upper_lower_shallow": fsp19_upper_lower_shallow,
            },
            "outcome": family,
        }],
    }


def _derive_v4_treatment_advisory(case_payload: dict, v4_gate_packet: dict,
                                  shengang_packet: dict) -> Optional[dict]:
    raw_packets = v4_gate_packet.get("raw_packets") or {}
    age = case_payload.get("patient_age") or case_payload.get("age")
    try:
        age_num = int(age) if age is not None else None
    except Exception:
        age_num = None
    adult_or_near = age_num is None or age_num >= 16
    concave_scope = bool(shengang_packet.get("scope_applies"))
    maxillary_or_transverse = (
        shengang_packet.get("shen_concave_family") in {"skeletal_maxillary_source", "skeletal_upper_lower_source"}
        or _v4_any_value(raw_packets, "maxillary_support", {"deficient"})
        or _v4_any_value(raw_packets, "paranasal_support", {"deficient"})
        or _v4_any_value(raw_packets, "posterior_crossbite", {"present_unilateral", "present_bilateral"})
        or _v4_any_value(raw_packets, "arch_palate", {"strict_narrow_high"})
        or _v4_any_value(raw_packets, "maxillary_arch_form", {"narrow_or_high_palate"})
    )
    trigger = adult_or_near and concave_scope and maxillary_or_transverse
    if not trigger:
        return None
    return {
        "visible": True,
        "kind": "adult_concave_treatment_boundary",
        "age": age_num,
        "message": (
            "成年或近成年凹面病例如存在上颌横向/前后向不足，非手术掩饰路径需把 "
            "MSE 骨性扩弓 + 上颌前牵作为可讨论选项；其作用为成人有限的骨性/牙槽性"
            "支持，需逐案评估。若骨性差异或面型诉求超过掩饰范围，应保留正畸-正颌联合评估边界。"
        ),
        "must_include_options": ["MSE_skeletal_expansion", "maxillary_protraction", "orthognathic_boundary"],
        "forbidden_framing": [
            "adult_concave_as_ordinary_protrusion",
            "upper_anterior_retraction_before_excluding_maxillary_source",
            "child_level_growth_effect_claim",
        ],
        "rule_trace": [{
            "rule_id": "adult_concave_maxillary_deficiency_template_guard",
            "hit": True,
            "inputs": {
                "adult_or_near": adult_or_near,
                "concave_scope": concave_scope,
                "maxillary_or_transverse": maxillary_or_transverse,
            },
            "outcome": "surface_mse_protraction_option_and_surgery_boundary",
        }],
    }


def _derive_v4_diagnosis_first_packet(v4_gate_packet: dict, v4_source_packet: dict,
                                      shengang_packet: dict,
                                      treatment_advisory: Optional[dict]) -> dict:
    """Deterministic diagnosis-first FSP19 vertical-slice carrier.

    This is a runtime carrier for an already-reviewed card context, not a gold
    lookup. It can only become active from structured hot-path anchors.
    """
    gate_result = (v4_gate_packet or {}).get("gate_result")
    raw_packets = (v4_gate_packet or {}).get("raw_packets") or {}
    source_candidate = (v4_source_packet or {}).get("source_candidate")
    source_level = (v4_source_packet or {}).get("attribution_level")
    subtype = (shengang_packet or {}).get("upper_lower_subtype")
    family = (shengang_packet or {}).get("shen_concave_family")

    anchors = {
        "surface_protrusion_or_positive_overjet": bool(
            _v4_any_value(raw_packets, "surface_protrusion_signal", {"mild", "strong"})
            or _v4_any_value(raw_packets, "overjet_sign", {"positive"})
        ),
        "dental_compensation_explanation": bool(
            _v4_any_value(raw_packets, "incisor_compensation", {"compensated_positive_overjet"})
        ),
        "class_III_or_concave_anchors": gate_result in {
            "maxillary_origin_masked_concave_review_required",
            "frank_concave_classIII_review_required",
        },
        "upper_source_participation": bool(
            family in {"skeletal_maxillary_source", "skeletal_upper_lower_source"}
            or source_candidate == "maxillary_primary_candidate"
            or source_candidate == "upper_lower_source_candidate"
            or _v4_any_value(raw_packets, "maxillary_support", {"deficient"})
            or _v4_any_value(raw_packets, "paranasal_support", {"deficient"})
        ),
        "mandibular_source_participation": bool(
            family == "skeletal_upper_lower_source"
            or source_candidate == "mandibular_primary_candidate"
            or source_candidate == "upper_lower_source_candidate"
            or _v4_any_value(raw_packets, "skeletal_chin_ap", {"anterior_prognathic", "neutral_not_retrusive"})
            or _v4_any_value(raw_packets, "molar_canine_relation", {"class_III"})
        ),
        "treatment_branch_change_anchor": bool(
            treatment_advisory
            and set((treatment_advisory or {}).get("must_include_options") or []) >= {
                "MSE_skeletal_expansion",
                "maxillary_protraction",
                "orthognathic_boundary",
            }
        ),
    }
    required = [
        "surface_protrusion_or_positive_overjet",
        "dental_compensation_explanation",
        "class_III_or_concave_anchors",
        "upper_source_participation",
        "mandibular_source_participation",
        "treatment_branch_change_anchor",
    ]
    missing = [key for key in required if not anchors.get(key)]
    active = not missing and subtype in {"triangular_shallow_concave", "true_concave_false_protrusive_risk"}
    if active:
        status = "active"
    elif anchors["class_III_or_concave_anchors"] and (anchors["upper_source_participation"] or anchors["mandibular_source_participation"]):
        status = "partial_missing_evidence"
    else:
        status = "not_applicable"

    return {
        "schema_version": "v4_diagnosis_first.0",
        "derived_by": "deterministic_orchestrator",
        "fixture_id": "fsp19_false_protrusive_compensated_concave" if status != "not_applicable" else None,
        "card_context": {
            "used": status != "not_applicable",
            "card_id": "fsp19_false_protrusive_compensated_concave" if status != "not_applicable" else None,
            "projection": "default" if status != "not_applicable" else None,
            "projection_reason": None,
            "minimum_positive_anchor_set_checked": status != "not_applicable",
        },
        "positive_chain_status": status,
        "main_diagnosis_candidate": "凹面 / III 类方向" if status != "not_applicable" else None,
        "source_attribution_candidate": "上下源性凹面方向" if active else None,
        "subtype_candidate": "下颌三角浅凹待正式测量确认" if active else None,
        "treatment_branch_candidate": (
            "MSE 骨性扩弓 + 上颌前牵，并保留正畸-正颌联合评估边界"
            if active else None
        ),
        "closure_state": "correct_non_closure_review_required" if status != "not_applicable" else None,
        "uncertainty_boundary": (
            "仍需正式头影、Coben、咬合模型、CR-CO 功能记录后才能关闭；"
            "当前正向诊断讨论不能被 pending-only 表述抹掉。"
            if status != "not_applicable" else None
        ),
        "required_missing_evidence": (
            ["正式头影", "Coben", "咬合模型", "CR-CO 功能记录"]
            if status != "not_applicable" else []
        ),
        "minimum_positive_anchor_set": anchors,
        "missing_required_anchors": missing,
        "source_attribution_packet_read_only": {
            "source_candidate": source_candidate,
            "attribution_level": source_level,
        },
        "shengang_subtype_read_only": {
            "shen_concave_family": family,
            "upper_lower_subtype": subtype,
        },
        "rule_trace": [{
            "rule_id": "fsp19_diagnosis_first_vertical_slice",
            "hit": active,
            "inputs": {
                "gate_result": gate_result,
                "source_candidate": source_candidate,
                "source_attribution_level": source_level,
                "shengang_family": family,
                "upper_lower_subtype": subtype,
                "treatment_advisory_visible": bool(treatment_advisory and treatment_advisory.get("visible")),
            },
            "outcome": status,
        }],
    }


def _v4_source_explanation(v4_phase2: dict) -> Optional[dict]:
    packet = (v4_phase2 or {}).get("source_attribution_packet") or {}
    if not packet:
        return None
    labels = {
        "upper_lower_source_candidate": "上下源性凹面候选",
        "maxillary_primary_candidate": "上颌来源候选",
        "mandibular_primary_candidate": "下颌 / III 类来源候选",
        "bimaxillary_candidate": "双颌 / 牙槽前突来源候选",
        "dental_compensation_dominant": "牙性代偿主导候选",
        "unresolved": "来源仍未关闭",
    }
    return {
        "visible": True,
        "kind": "v4_phase2_source_explanation",
        "source_candidate": packet.get("source_candidate"),
        "source_label": labels.get(packet.get("source_candidate"), "来源仍需复核"),
        "attribution_level": packet.get("attribution_level"),
        "confidence": packet.get("confidence"),
        "cannot_close_reason": packet.get("cannot_close_reason"),
        "decisive_anchor_count": len(packet.get("decisive_anchors") or []),
        "supporting_anchor_count": len(packet.get("supporting_anchors") or []),
        "message": "来源归因仅供复核解释使用，不改变 Phase I 分型 gate 或复核提示。",
    }


# ── Schema validation (Task 3 Mode B — flag-only, never block) ────────────────

import logging
_log = logging.getLogger(__name__)
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [orchestrator] %(levelname)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)

def _validate_stage_output(stage_key: str, data: dict) -> list[dict]:
    """Validate agent JSON response against its stage schema. Returns list of violation dicts.

    Flag-only — caller logs violations in stage_info but does NOT raise.
    For Stage C, also validates scene-specific sections keys.
    """
    schema = STAGE_SCHEMAS.get(stage_key)
    if schema is None:
        return []
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        violations = [
            {
                "path": "/".join(str(p) for p in e.path) or "(root)",
                "message": e.message,
                "validator": e.validator,
            }
            for e in errors
        ]
        # Stage C: additionally validate scene-specific sections keys
        if stage_key == "stage_C_senior_clinician" and isinstance(data, dict):
            scene = data.get("scene")
            sections_schema = SC_SECTIONS_BY_SCENE.get(str(scene))
            if sections_schema and isinstance(data.get("sections"), dict):
                sec_validator = jsonschema.Draft7Validator(sections_schema)
                for e in sec_validator.iter_errors(data["sections"]):
                    violations.append({
                        "path": "sections/" + "/".join(str(p) for p in e.path),
                        "message": e.message,
                        "validator": e.validator,
                    })
        return violations
    except Exception as exc:
        _log.warning("Schema validation internal error for %s: %s", stage_key, exc)
        return []


# ── Slock dispatch / poll ─────────────────────────────────────────────────────

async def _slock_dispatch(handle: str, payload: dict, timeout_sec: int,
                          *, late_grace_sec: int = 0) -> dict:
    """Send payload to agent via Slock DM. Poll for plain JSON response matching request_id."""
    request_id = str(_uuid.uuid4())
    effective_expiry_sec = timeout_sec + max(0, late_grace_sec)
    _expiry_utc = (datetime.utcnow() + timedelta(seconds=effective_expiry_sec)).strftime("%Y-%m-%dT%H:%M:%SZ")
    full_payload = {**payload, "v2_dispatch": True, "request_id": request_id,
                    "dispatch_expiry_utc": _expiry_utc}  # Fix F4: agents skip if past expiry
    payload_str = json.dumps(full_payload, ensure_ascii=False)

    send_proc = await asyncio.to_thread(
        subprocess.run,
        [_SLOCK_BIN, "message", "send", "--target", f"dm:@{handle}"],
        input=payload_str,
        capture_output=True, text=True, timeout=60,
    )
    if send_proc.returncode != 0:
        raise RuntimeError(f"slock dispatch to @{handle} failed: {send_proc.stderr.strip()}")

    # Slock freshness-hold: message saved as draft when DM has unread messages.
    # Re-send the draft unchanged so the payload is actually delivered.
    if "draft" in send_proc.stdout.lower():
        draft_proc = await asyncio.to_thread(
            subprocess.run,
            [_SLOCK_BIN, "message", "send", "--send-draft", "--target", f"dm:@{handle}"],
            capture_output=True, text=True, timeout=60,
        )
        if draft_proc.returncode != 0:
            raise RuntimeError(f"slock draft-send to @{handle} failed: {draft_proc.stderr.strip()}")

    _log.info("dispatch sent to @%s request_id=%s timeout=%ds", handle, request_id, timeout_sec)
    # Slock history renders local wall-clock timestamps; use local time for freshness
    # comparisons while keeping dispatch_expiry_utc in UTC for cross-agent expiry.
    dispatch_sent_time = datetime.now()  # Fix F3: anchor for stale DM response filter
    deadline = time.monotonic() + timeout_sec
    poll_interval = 8
    poll_count = 0

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        poll_count += 1
        try:
            read_proc = await asyncio.to_thread(
                subprocess.run,
                [_SLOCK_BIN, "message", "read", "--channel", f"dm:@{handle}", "--limit", "20"],
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            _log.warning("poll #%d to @%s CLI timeout (request_id=%s)", poll_count, handle, request_id)
            continue  # CLI hung; retry on next poll cycle
        if read_proc.returncode == 0:
            response = _extract_json_response(read_proc.stdout, handle, request_id,
                                               fallback_case_id=payload.get("case_id", ""),
                                               dispatch_sent_time=dispatch_sent_time)
            if response is not None:
                elapsed = poll_count * poll_interval
                _log.info("@%s responded request_id=%s after ~%ds (%d polls)", handle, request_id, elapsed, poll_count)
                return response
            if handle in _V4_PACKET_AGENTS:
                response = _extract_v4_packet_response(
                    read_proc.stdout, handle, request_id, payload, dispatch_sent_time
                )
                if response is not None:
                    elapsed = poll_count * poll_interval
                    _log.info(
                        "@%s V4 packet fallback responded request_id=%s after ~%ds (%d polls)",
                        handle, request_id, elapsed, poll_count,
                    )
                    return response
                response = _extract_v4_packet_unavailable_response(
                    read_proc.stdout, handle, request_id, payload, dispatch_sent_time
                )
                if response is not None:
                    elapsed = poll_count * poll_interval
                    _log.warning(
                        "@%s unavailable response request_id=%s after ~%ds (%d polls)",
                        handle, request_id, elapsed, poll_count,
                    )
                    return response

    if late_grace_sec > 0:
        _log.warning(
            "@%s primary timeout reached request_id=%s; entering late-response grace=%ds",
            handle, request_id, late_grace_sec,
        )
        late_deadline = time.monotonic() + late_grace_sec
        late_polls = 0
        while time.monotonic() < late_deadline:
            await asyncio.sleep(poll_interval)
            late_polls += 1
            try:
                read_proc = await asyncio.to_thread(
                    subprocess.run,
                    [_SLOCK_BIN, "message", "read", "--channel", f"dm:@{handle}", "--limit", "20"],
                    capture_output=True, text=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                _log.warning(
                    "late poll #%d to @%s CLI timeout (request_id=%s)",
                    late_polls, handle, request_id,
                )
                continue
            if read_proc.returncode != 0:
                continue
            response = _extract_json_response(
                read_proc.stdout, handle, request_id,
                fallback_case_id=payload.get("case_id", ""),
                dispatch_sent_time=dispatch_sent_time,
            )
            if response is not None:
                elapsed = poll_count * poll_interval + late_polls * poll_interval
                _log.warning(
                    "@%s late-response salvaged request_id=%s after ~%ds "
                    "(primary_polls=%d late_polls=%d)",
                    handle, request_id, elapsed, poll_count, late_polls,
                )
                if isinstance(response, dict):
                    response["_dispatch_meta"] = {
                        "request_id": request_id,
                        "response_mode": "late_response_salvage",
                        "timeout_sec": timeout_sec,
                        "late_grace_sec": late_grace_sec,
                        "primary_polls": poll_count,
                        "late_polls": late_polls,
                        "elapsed_estimate_sec": elapsed,
                    }
                return response

    _log.error("@%s timeout request_id=%s timeout_sec=%d polls=%d", handle, request_id, timeout_sec, poll_count)
    if late_grace_sec > 0:
        raise TimeoutError(
            f"@{handle} did not respond within {timeout_sec}s + {late_grace_sec}s "
            f"late-response grace (request_id={request_id})"
        )
    raise TimeoutError(f"@{handle} did not respond within {timeout_sec}s (request_id={request_id})")


def _slock_msg_is_fresh(context_before_json: str, dispatch_sent_time: Optional[datetime]) -> bool:
    """Return True if the Slock message preceding a JSON block was sent after dispatch_sent_time - 10s."""
    if dispatch_sent_time is None:
        return True
    matches = list(_SLOCK_TIME_RE.finditer(context_before_json))
    if not matches:
        return True  # no header found — be permissive
    time_str = matches[-1].group(1).replace(" ", "T")
    try:
        msg_time = datetime.fromisoformat(time_str)
        # Both are naive UTC datetimes; allow 10s tolerance for clock drift at dispatch boundary
        return (dispatch_sent_time - msg_time).total_seconds() <= 10
    except Exception:
        return True  # parse failure — be permissive


def _response_timestamp_is_stale(resp_ts, dispatch_sent_time: Optional[datetime]) -> bool:
    """Return True when an agent response_timestamp predates dispatch by >10s.

    Slock history headers are local wall-clock, but many agent JSON
    response_timestamp fields are UTC (`...Z`). Normalize only the inner agent
    timestamp check here; keep `_slock_msg_is_fresh` local for Slock headers.
    """
    if not resp_ts or dispatch_sent_time is None:
        return False
    raw = str(resp_ts).strip().replace(" ", "T")
    try:
        if raw.endswith("Z"):
            response_time = datetime.fromisoformat(raw[:-1] + "+00:00")
            offset = datetime.now().astimezone().utcoffset() or timedelta(0)
            dispatch_utc = (dispatch_sent_time - offset).replace(tzinfo=timezone.utc)
            return (dispatch_utc - response_time).total_seconds() > 10
        response_time = datetime.fromisoformat(raw)
        if response_time.tzinfo is not None:
            dispatch_local = dispatch_sent_time.replace(
                tzinfo=datetime.now().astimezone().tzinfo
            )
            return (dispatch_local.astimezone(response_time.tzinfo) - response_time).total_seconds() > 10
        return (dispatch_sent_time - response_time).total_seconds() > 10
    except Exception:
        return False


def _extract_v4_packet_unavailable_response(slock_output: str, handle: str, request_id: str,
                                            payload: dict,
                                            dispatch_sent_time: Optional[datetime]) -> Optional[dict]:
    """Detect V4 packet-agent refusal messages and convert them to fail-closed input.

    Packet-reader agents may be configured as sandbox-only and decline hot-path
    payloads without emitting the request_id JSON envelope. Treat that as an
    explicit unavailable packet so Stage A can proceed to deterministic
    not-closeable/review-required gating instead of waiting for the full timeout.
    """
    case_id = str(payload.get("case_id") or "")
    if not case_id or f"@{handle}" not in slock_output:
        return None

    # TECH-DEBT: these terms match on the FULL Slock line including the agent’s
    # profile description header, not just message content. Agents whose description
    # contains a refusal-shaped phrase (e.g. "no hot-path") will false-match on
    # EVERY message. Currently safe because _V4_PACKET_AGENTS gates this function
    # to sandbox IRs only. Fix: split header from content before matching.
    refusal_terms = (
        "can’t process",
        "cannot process",
        "can’t process",
        "sandbox-only",
        "no hot-path",
        "hot path",
        "hot-path",
        "current lane is sandbox",
        "outside my sandbox",
        "outside my sandbox-only",
        "outside my sandbox only",
    )
    last_webapp_dispatch_for_case = False
    for line in slock_output.splitlines():
        lower = line.lower()
        if "@webappdev" in lower and case_id in line and "v4_phase1_dispatch" in line:
            last_webapp_dispatch_for_case = _slock_msg_is_fresh(line, dispatch_sent_time)
            continue

        if f"@{handle}".lower() not in lower:
            continue
        if not any(term in lower for term in refusal_terms):
            continue
        if not last_webapp_dispatch_for_case and case_id not in line:
            continue
        if not _slock_msg_is_fresh(line, dispatch_sent_time):
            _log.warning("@%s V4 unavailable fallback: STALE msg skipped case_id=%s",
                         handle, case_id)
            continue
        return {
            "case_id": case_id,
            "request_id": request_id,
            "packet_role": payload.get("packet_role"),
            "packet_unavailable": True,
            "unavailable_reason": "packet_agent_declined_hot_path_payload",
            "packet_schema_version": payload.get("packet_schema_version", "v4_phase1.0"),
        }
    return None


def _extract_v4_packet_response(slock_output: str, handle: str, request_id: str,
                                payload: dict,
                                dispatch_sent_time: Optional[datetime]) -> Optional[dict]:
    """Accept V4 packet JSON by case_id + packet role, even if request_id is omitted."""
    case_id = str(payload.get("case_id") or "")
    expected_role = str(payload.get("packet_role") or "")
    if not case_id or handle not in _V4_PACKET_AGENTS:
        return None

    decoder = json.JSONDecoder()
    for search_phr in (f'"case_id":"{case_id}"', f'"case_id": "{case_id}"'):
        pos = 0
        while True:
            idx = slock_output.find(search_phr, pos)
            if idx == -1:
                break
            obj_start = slock_output.rfind("{", 0, idx)
            if obj_start == -1:
                pos = idx + 1
                continue
            ctx = slock_output[max(0, obj_start - 4096):obj_start]
            try:
                data, _ = decoder.raw_decode(slock_output, obj_start)
            except json.JSONDecodeError:
                pos = idx + 1
                continue
            if not isinstance(data, dict) or f"@{handle}" not in ctx:
                pos = idx + 1
                continue
            role = str(
                data.get("packet_role")
                or (data.get("side_packet") or {}).get("packet_role")
                or (data.get("packet") or {}).get("packet_role")
                or ""
            )
            has_packet_shape = any(
                isinstance(data.get(key), dict)
                for key in ("anchors", "side_packet", "packet")
            )
            if data.get("case_id") != case_id or not has_packet_shape:
                pos = idx + 1
                continue
            if expected_role and role and role not in {expected_role, f"{expected_role}_side_packet"}:
                pos = idx + 1
                continue
            if not _slock_msg_is_fresh(ctx, dispatch_sent_time):
                _log.warning("@%s V4 packet fallback: STALE msg skipped case_id=%s",
                             handle, case_id)
                pos = idx + 1
                continue
            if not data.get("request_id"):
                data["request_id"] = request_id
            _log.warning(
                "@%s V4 packet fallback match (request_id missing or nested) case_id=%s role=%s",
                handle, case_id, role or expected_role,
            )
            return data
    return None


def _extract_json_response(slock_output: str, handle: str, request_id: str,
                            fallback_case_id: str = "",
                            dispatch_sent_time: Optional[datetime] = None) -> Optional[dict]:
    """Find agent's JSON reply in slock message read output. Match by request_id.

    Searches for the UUID directly (not '"request_id": "uuid"') to handle both
    spaced and compact JSON formatting from different agents.

    fallback_case_id: when set and handle is CaseMemory, attempt secondary match
    by case_id + top_5_cases if no request_id match found (CM sometimes omits request_id).
    """
    search_str = request_id
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        idx = slock_output.find(search_str, pos)
        if idx == -1:
            break
        obj_start = slock_output.rfind("{", 0, idx)
        if obj_start == -1:
            break
        try:
            data, _ = decoder.raw_decode(slock_output, obj_start)
            if (isinstance(data, dict)
                    and data.get("request_id") == request_id
                    and f"@{handle}" in slock_output[max(0, obj_start - 4096):obj_start]):
                # V-3b Fix 4: response_timestamp freshness on primary match.
                # If agent emits response_timestamp > 10s before dispatch, it is a stale
                # cached/replayed response — skip and continue searching.
                # Absent timestamp: accepted (agent may not emit it; Fix F1 already warns).
                resp_ts = data.get("response_timestamp")
                if _response_timestamp_is_stale(resp_ts, dispatch_sent_time):
                    _log.warning(
                        "@%s Fix4: STALE response_timestamp primary match skipped"
                        " ts=%s dispatch=%s",
                        handle, resp_ts, dispatch_sent_time.isoformat(),
                    )
                    pos = idx + 1
                    continue
                return data
        except json.JSONDecodeError:
            # LLM may emit unescaped ASCII quotes inside string values.
            # Iteratively escape the offending character and retry.
            if f"@{handle}" in slock_output[max(0, obj_start - 4096):obj_start]:
                snippet = slock_output[obj_start:]
                repair_decoder = json.JSONDecoder()
                for _ in range(50):
                    try:
                        data, _ = repair_decoder.raw_decode(snippet, 0)
                        if (isinstance(data, dict)
                                and data.get("request_id") == request_id):
                            # V-3b Fix 4: freshness check on repair match (same invariant).
                            resp_ts = data.get("response_timestamp")
                            if _response_timestamp_is_stale(resp_ts, dispatch_sent_time):
                                _log.warning(
                                    "@%s Fix4: STALE response_timestamp repair match"
                                    " skipped ts=%s",
                                    handle, resp_ts,
                                )
                                break  # exit repair loop → outer loop continues
                            return data
                        break
                    except json.JSONDecodeError as e2:
                        # LLM may emit paired Chinese-quoted phrases with ASCII "..."
                        # Opening quote: error pos is right after the quote.
                        escape_at = e2.pos - 1
                        if escape_at >= 0 and snippet[escape_at] == '"':
                            snippet = snippet[:escape_at] + '\\"' + snippet[escape_at + 1:]
                        else:
                            # Closing quote of a pair caused premature string end.
                            # Search backwards from error pos for nearest unescaped quote.
                            found = False
                            for look in range(e2.pos - 1, max(0, e2.pos - 200), -1):
                                if snippet[look] == '"' and (look == 0 or snippet[look - 1] != '\\'):
                                    snippet = snippet[:look] + '\\"' + snippet[look + 1:]
                                    found = True
                                    break
                            if not found:
                                break
        pos = idx + 1
    # Fallback: CM sometimes omits request_id — match by case_id + top_5_cases presence
    if fallback_case_id and handle == _AGENT_CM:
        for search_phr in (f'"case_id":"{fallback_case_id}"', f'"case_id": "{fallback_case_id}"'):
            pos2 = 0
            while True:
                idx = slock_output.find(search_phr, pos2)
                if idx == -1:
                    break
                obj_start = slock_output.rfind("{", 0, idx)
                if obj_start == -1:
                    pos2 = idx + 1
                    continue
                try:
                    data, _ = decoder.raw_decode(slock_output, obj_start)
                    ctx = slock_output[max(0, obj_start - 4096):obj_start]
                    if (isinstance(data, dict)
                            and data.get("case_id") == fallback_case_id
                            and "top_5_cases" in data
                            and f"@{handle}" in ctx):
                        if not _slock_msg_is_fresh(ctx, dispatch_sent_time):
                            _log.warning("@%s case_id fallback: STALE msg skipped case_id=%s",
                                         handle, fallback_case_id)
                            pos2 = idx + 1
                            continue
                        # Fix F1: agent-reported response_timestamp inner freshness check
                        resp_ts = data.get("response_timestamp")
                        if _response_timestamp_is_stale(resp_ts, dispatch_sent_time):
                            _log.warning("@%s case_id fallback: STALE response_timestamp skipped case_id=%s ts=%s",
                                         handle, fallback_case_id, resp_ts)
                            pos2 = idx + 1
                            continue
                        _log.warning("@%s case_id fallback match (request_id missing from response) case_id=%s",
                                     handle, fallback_case_id)
                        return data
                except json.JSONDecodeError:
                    pass
                pos2 = idx + 1
    # Fallback: KC may echo nested request_id — match by case_id + e_blocks presence
    if fallback_case_id and handle == _AGENT_KC:
        for search_phr in (f'"case_id":"{fallback_case_id}"', f'"case_id": "{fallback_case_id}"'):
            pos2 = 0
            while True:
                idx = slock_output.find(search_phr, pos2)
                if idx == -1:
                    break
                obj_start = slock_output.rfind("{", 0, idx)
                if obj_start == -1:
                    pos2 = idx + 1
                    continue
                try:
                    data, _ = decoder.raw_decode(slock_output, obj_start)
                    ctx = slock_output[max(0, obj_start - 4096):obj_start]
                    if (isinstance(data, dict)
                            and data.get("case_id") == fallback_case_id
                            and "e_blocks" in data
                            and f"@{handle}" in ctx):
                        if not _slock_msg_is_fresh(ctx, dispatch_sent_time):
                            _log.warning("@%s case_id fallback: STALE msg skipped case_id=%s",
                                         handle, fallback_case_id)
                            pos2 = idx + 1
                            continue
                        # Fix F1: agent-reported response_timestamp inner freshness check
                        resp_ts = data.get("response_timestamp")
                        if _response_timestamp_is_stale(resp_ts, dispatch_sent_time):
                            _log.warning("@%s case_id fallback: STALE response_timestamp skipped case_id=%s ts=%s",
                                         handle, fallback_case_id, resp_ts)
                            pos2 = idx + 1
                            continue
                        _log.warning("@%s case_id fallback match (request_id echo mismatch) case_id=%s",
                                     handle, fallback_case_id)
                        return data
                except json.JSONDecodeError:
                    pass
                pos2 = idx + 1
    # Fallback: Critic may echo nested request_id — match by case_id + claim_review presence
    if fallback_case_id and handle == _AGENT_CRITIC:
        for search_phr in (f'"case_id":"{fallback_case_id}"', f'"case_id": "{fallback_case_id}"'):
            pos2 = 0
            while True:
                idx = slock_output.find(search_phr, pos2)
                if idx == -1:
                    break
                obj_start = slock_output.rfind("{", 0, idx)
                if obj_start == -1:
                    pos2 = idx + 1
                    continue
                try:
                    data, _ = decoder.raw_decode(slock_output, obj_start)
                    ctx = slock_output[max(0, obj_start - 4096):obj_start]
                    if (isinstance(data, dict)
                            and data.get("case_id") == fallback_case_id
                            and "claim_review" in data
                            and f"@{handle}" in ctx):
                        if not _slock_msg_is_fresh(ctx, dispatch_sent_time):
                            _log.warning("@%s case_id fallback: STALE msg skipped case_id=%s",
                                         handle, fallback_case_id)
                            pos2 = idx + 1
                            continue
                        # Fix F1: agent-reported response_timestamp inner freshness check
                        resp_ts = data.get("response_timestamp")
                        if _response_timestamp_is_stale(resp_ts, dispatch_sent_time):
                            _log.warning("@%s case_id fallback: STALE response_timestamp skipped case_id=%s ts=%s",
                                         handle, fallback_case_id, resp_ts)
                            pos2 = idx + 1
                            continue
                        _log.warning("@%s case_id fallback match (request_id echo mismatch) case_id=%s",
                                     handle, fallback_case_id)
                        return data
                except json.JSONDecodeError:
                    pass
                pos2 = idx + 1
    return None


# ── Main orchestration entry point ────────────────────────────────────────────

async def _run_v2_stages(case_id: str, trace_id: str, scene: str, case_payload: dict,
                          image_refs: list, db_mod, audit_mod=None) -> dict:
    """Inner stage sequence — called via asyncio.wait_for() in run_v2 for case-level budget."""

    # Stage 0 — pre-submit gate (already checked in cases_router, mark complete)
    await _write_stage(db_mod, case_id, "stage_0_sufficiency_gate",
                       make_completed("stage_0_sufficiency_gate", _now(),
                                      {"result": "pass", "note": "pre-submit gate passed"}))

    # Read-tool advisory (shadow-mode: stage_info only, no downstream effect)
    _read_tool_result = None
    if config.READ_TOOL_ADVISORY_ENABLED and image_refs:
        _rt_start = _now()
        try:
            from orchestrator.read_tool_advisory import run_advisory
            for ref in image_refs:
                _rt = await asyncio.to_thread(run_advisory, ref["storage_path"])
                if _rt.get("status") in ("sane", "sanity_fail"):
                    _read_tool_result = _rt
                    break
            await _write_stage(db_mod, case_id, "stage_read_tool_advisory",
                               make_completed("stage_read_tool_advisory", _rt_start,
                                              {"result": _read_tool_result or {"status": "no_ceph_found"}}))
        except Exception as e:
            await _write_stage(db_mod, case_id, "stage_read_tool_advisory",
                               make_failed("stage_read_tool_advisory", _rt_start, str(e)))

    # Stage A — Initial Reader (single or ensemble per config.ENSEMBLE_IR_READS)
    stage_A_start = _now()
    await _write_stage(db_mod, case_id, "stage_A_initial_reader", make_running("stage_A_initial_reader"))
    try:
        _ensemble_n = config.ENSEMBLE_IR_READS
        if _ensemble_n >= 2:
            ir_output = await _run_stage_A_ensemble(case_id, case_payload, image_refs, scene, _ensemble_n)
        else:
            ir_output = await _run_stage_A(case_id, case_payload, image_refs, scene)
        _a_violations = _validate_stage_output("stage_A_initial_reader", ir_output)
        _a_extra = {"schema_violations": _a_violations} if _a_violations else {}
        if ir_output.get("ensemble_meta"):
            _a_extra["ensemble_meta"] = ir_output["ensemble_meta"]
        if ir_output.get("_dispatch_meta"):
            _a_extra["dispatch_meta"] = ir_output["_dispatch_meta"]
        await _write_stage(db_mod, case_id, "stage_A_initial_reader",
                           make_completed("stage_A_initial_reader", stage_A_start, _a_extra))
    except Exception as e:
        await _write_stage(db_mod, case_id, "stage_A_initial_reader",
                           make_failed("stage_A_initial_reader", stage_A_start, str(e)))
        raise
    if ir_output.get("ensemble_disagreement"):
        db_mod.update_case_status(
            case_id, "awaiting_doctor_review",
            metadata={"ensemble_disagreement": ir_output.get("ensemble_meta", {})},
            error_msg="IR ensemble disagreement on axis_1 face_class — escalated for human review",
        )
        return {}
    ir_output = _expand_risk_patterns(ir_output)  # P2: inject KB canonical text for P-codes
    v4_raw_packets = await _run_stage_A_v4_ir_packets(case_id, case_payload, ir_output, image_refs, scene)
    ir_output = _attach_v4_sagittal_gate({**ir_output, "v4_ir_packets": v4_raw_packets})
    v4_gate_packet = ir_output.get("sagittal_consensus_packet") or {}
    v4_retreatment_packet = _derive_v4_retreatment_history_packet(case_payload, v4_gate_packet)
    v4_gate_packet = _apply_v4_retreatment_history_gate(v4_gate_packet, v4_retreatment_packet)
    ir_output = {**ir_output, "sagittal_consensus_packet": v4_gate_packet}
    v4_routing_packet = v4_gate_packet.get("routing_intent") or {}
    v4_phase1 = {
        "v4_ir_packets": {
            "convex_side_packet": (v4_gate_packet.get("raw_packets") or {}).get("convex"),
            "concave_side_packet": (v4_gate_packet.get("raw_packets") or {}).get("concave"),
            "non_sagittal_packet": (v4_gate_packet.get("raw_packets") or {}).get("non_sagittal"),
            "packet_schema_version": v4_raw_packets.get("packet_schema_version", "v4_phase1.0"),
            "packet_source": v4_raw_packets.get("packet_source", {}),
        },
        "v4_gate_packet": {
            "gate_result": v4_gate_packet.get("gate_result"),
            "closure_status": v4_gate_packet.get("closure_status"),
            "rule_trace": v4_gate_packet.get("rule_trace") or [],
            "blocking_reasons": v4_gate_packet.get("blocking_reasons") or [],
            "derived_by": v4_gate_packet.get("derived_by"),
            "schema_version": v4_gate_packet.get("schema_version"),
        },
        "v4_routing_packet": v4_routing_packet,
        "v4_sc_gate_consistency": {"consistent": None, "status": "pending_stage_c"},
        "v4_retreatment_history_packet": v4_retreatment_packet,
    }
    v4_source_packet = _derive_v4_source_attribution(v4_gate_packet)
    v4_source_packet = _cap_v4_source_for_retreatment(v4_source_packet, v4_retreatment_packet)
    v4_shengang_packet = _derive_v4_shengang_subtype(v4_gate_packet, v4_source_packet)
    v4_treatment_advisory = _derive_v4_treatment_advisory(case_payload, v4_gate_packet, v4_shengang_packet)
    v4_diagnosis_first_packet = _derive_v4_diagnosis_first_packet(
        v4_gate_packet, v4_source_packet, v4_shengang_packet, v4_treatment_advisory
    )
    v4_reasoning_workspace = build_runtime_reasoning_workspace(
        case_id=case_id,
        scene=scene,
        case_payload=case_payload,
        v4_gate_packet=v4_gate_packet,
        v4_source_packet=v4_source_packet,
        v4_shengang_packet=v4_shengang_packet,
        v4_treatment_advisory=v4_treatment_advisory,
        v4_diagnosis_first_packet=v4_diagnosis_first_packet,
    )
    v4_reasoning_output_projection = build_reasoning_output_projection(v4_reasoning_workspace)
    v4_reasoning_training = build_reasoning_loop_training_receipt()
    v4_phase2 = {
        "source_attribution_packet": v4_source_packet,
        "shengang_concave_subtype_packet": v4_shengang_packet,
        "retreatment_history_packet": v4_retreatment_packet,
        "diagnosis_first_packet": v4_diagnosis_first_packet,
        "reasoning_workspace": {
            "schema_version": v4_reasoning_workspace.get("schema_version"),
            "closure_axes": v4_reasoning_workspace.get("closure_axes"),
            "runtime_receipt": v4_reasoning_workspace.get("runtime_receipt"),
        },
    }
    v4_diagnosis_first_training = build_diagnosis_first_training_receipt()
    if v4_treatment_advisory:
        v4_phase2["treatment_template_advisory"] = v4_treatment_advisory
    ir_output = {
        **ir_output,
        "source_attribution_packet": v4_source_packet,
        "diagnosis_first_packet": v4_diagnosis_first_packet,
        "reasoning_workspace": v4_reasoning_workspace,
    }
    await _write_stage(db_mod, case_id, "stage_A_initial_reader",
                       make_completed(
                           "stage_A_initial_reader",
                           stage_A_start,
                           _merge_stage_extras(_a_extra, {
                               "v4_gate_result": v4_gate_packet.get("gate_result"),
                               "v4_blocking_reasons": v4_gate_packet.get("blocking_reasons") or [],
                               "v4_source_candidate": v4_source_packet.get("source_candidate"),
                               "v4_source_attribution_level": v4_source_packet.get("attribution_level"),
                               "v4_retreatment_review_required": v4_retreatment_packet.get("review_required"),
                               "v4_shengang_family": v4_shengang_packet.get("shen_concave_family"),
                               "v4_diagnosis_first_status": v4_diagnosis_first_packet.get("positive_chain_status"),
                               "v4_diagnosis_first_training_version": v4_diagnosis_first_training.get("schema_version"),
                               "v4_reasoning_workspace_schema": v4_reasoning_workspace.get("schema_version"),
                               "v4_reasoning_close_diagnosis": (
                                   v4_reasoning_workspace.get("closure_axes") or {}
                               ).get("close_diagnosis"),
                           }),
                       ))

    # Stage B — KC + CM parallel
    stage_B_start = _now()
    await _write_stage(db_mod, case_id, "stage_B_kb_retrieve", make_running("stage_B_kb_retrieve"))
    try:
        kc_output, cm_output = await _run_stage_B(case_id, case_payload, ir_output, scene)
        _kc_violations = _validate_stage_output("stage_B_kc", kc_output)
        _cm_violations = _validate_stage_output("stage_B_cm", cm_output)
        _kc_status_val = kc_output.get("status")
        _kc_e_blocks_count = len(kc_output.get("e_blocks") or [])
        _kc_opposing_count = len(kc_output.get("opposing_evidence") or [])
        _kc_kb_gaps_count = len(kc_output.get("kb_gaps") or [])
        _b_extra = {
            "kc_status": _kc_status_val,
            "kc_e_blocks_count": _kc_e_blocks_count,
            "cm_top_cases_count": len(cm_output.get("top_5_cases") or []),
        }
        if _kc_violations:
            _b_extra["kc_schema_violations"] = _kc_violations
            # KC observability (批A): kc_status=null with 'status'-required violation = response-schema
            # degradation. Retrieval may still be functional but kc_status untracked → class-2 exclusion
            # risk on re-score runs. Log explicitly so it is not silent (KBadvisor bbbfbaf5).
            if _kc_status_val is None and any(
                v.get("message", "").startswith("'status' is a required property")
                for v in _kc_violations if isinstance(v, dict)
            ):
                _log.warning(
                    "KC_STATUS_NULL_DEGRADATION case_id=%s: kc_status=null (schema 'status' required "
                    "violation). kc_e_blocks=%d opposing=%d kb_gaps=%d cm_top=%d. "
                    "Forward trigger: if kc_status=null on a 子集B re-score case → class-2 exclusion "
                    "candidate (分支B iff kc_e_blocks>0, else 分支A — KBadvisor 67b76438).",
                    case_id, _kc_e_blocks_count, _kc_opposing_count, _kc_kb_gaps_count,
                    len(cm_output.get("top_5_cases") or []),
                )
        if _cm_violations:
            _b_extra["cm_schema_violations"] = _cm_violations
        await _write_stage(db_mod, case_id, "stage_B_kb_retrieve",
                           make_completed("stage_B_kb_retrieve", stage_B_start, _b_extra))
    except Exception as e:
        await _write_stage(db_mod, case_id, "stage_B_kb_retrieve",
                           make_failed("stage_B_kb_retrieve", stage_B_start, str(e)))
        raise

    # Stage C — SeniorClinician (IX loop or single dispatch)
    stage_C_start = _now()
    await _write_stage(db_mod, case_id, "stage_C_senior_clinician", make_running("stage_C_senior_clinician"))
    clinician_output = None
    _stage_C_fn = _run_stage_C_ix if config.IX_ENABLED else _run_stage_C
    for attempt in range(MAX_RETRIES + 1):
        try:
            clinician_output = await _stage_C_fn(
                case_id, case_payload, ir_output, kc_output, cm_output, image_refs, scene,
                forced_voice_mode=None,
            )
            clinician_output = _apply_v4_gate_consistency(clinician_output, v4_gate_packet)
            break
        except CrossTrackEscalateError as e:
            # IX-2.2 cross-track incoherence: flag-and-escalate (护栏②: not hard-reject)
            # Signals IX-2.1 re-anchor needed; human doctor review is the re-anchor gate.
            await _write_stage(db_mod, case_id, "stage_C_senior_clinician",
                               make_failed("stage_C_senior_clinician", stage_C_start, str(e)))
            db_mod.update_case_status(
                case_id, "awaiting_doctor_review",
                metadata={"ix_cross_track_escalate": True},
                error_msg=str(e),
            )
            return {}
        except IXNonConvergenceError as e:
            # IX-3 weld-lock 3: non-convergence → honest escalate (not a retry-able failure)
            await _write_stage(db_mod, case_id, "stage_C_senior_clinician",
                               make_failed("stage_C_senior_clinician", stage_C_start, str(e)))
            db_mod.update_case_status(
                case_id, "awaiting_doctor_review",
                metadata={"ix_non_convergence": True},
                error_msg=str(e),
            )
            return {}
        except Exception as e:
            if attempt == MAX_RETRIES:
                await _write_stage(db_mod, case_id, "stage_C_senior_clinician",
                                   make_failed("stage_C_senior_clinician", stage_C_start, str(e)))
                raise
    _c_violations = _validate_stage_output("stage_C_senior_clinician", clinician_output)
    _c_extra = {
        "voice_mode_applied": clinician_output.get("voice_mode_applied"),
        "ix_enabled": config.IX_ENABLED,
        "v4_gate_consistency_status": (clinician_output.get("v4_gate_consistency_status") or {}).get("status"),
    }
    v4_sc_status = clinician_output.get("v4_gate_consistency_status") or {}
    v4_phase1["v4_sc_gate_consistency"] = {
        "sc_received_gate_result": v4_sc_status.get("gate_result"),
        "sc_final_closure_claim": (
            "clean_true_convex"
            if _sc_text_clean_true_convex_closure(clinician_output) else "not_clean_true_convex"
        ),
        "consistent": v4_sc_status.get("status") == "consistent",
        "anchor_dispute_present": bool(clinician_output.get("anchor_dispute")),
        "recomputed_gate_result": None,
        "violation_reason": (
            "clean_true_convex_against_review_gate"
            if v4_sc_status.get("status") == "violation_clean_true_convex_against_review_gate"
            else None
        ),
    }
    if _c_violations:
        _c_extra["schema_violations"] = _c_violations
    await _write_stage(db_mod, case_id, "stage_C_senior_clinician",
                       make_completed("stage_C_senior_clinician", stage_C_start, _c_extra))

    # Extract IX evidence_state for HRW floor (threaded via internal field from _run_stage_C_ix).
    # Must be removed before Critic dispatch (W4: Critic must not see internal IX state).
    _ix_evidence_state: dict = clinician_output.pop("_ix_evidence_state", {})

    # Stage D — Critic (independent)
    critic_output = await _run_stage_D_with_flag(
        db_mod, case_id, case_payload, ir_output, kc_output, cm_output,
        clinician_output, image_refs, scene,
    )

    # Voice mode re-evaluation: retry Stage C if Critic forces Mode B
    if scene == "1_patient":
        final_mode = _infer_voice_mode(clinician_output, scene, critic_output)
        if (final_mode == "B_difficult_diagnosis_warning"
                and clinician_output.get("voice_mode_applied") != "B_difficult_diagnosis_warning"):
            try:
                clinician_output = await _run_stage_C(
                    case_id, case_payload, ir_output, kc_output, cm_output, image_refs, scene,
                    forced_voice_mode="B_difficult_diagnosis_warning",
                )
                clinician_output = _apply_v4_gate_consistency(clinician_output, v4_gate_packet)
                await _write_stage(db_mod, case_id, "stage_C_senior_clinician",
                                   make_completed("stage_C_senior_clinician", stage_C_start, {
                                       "voice_mode_applied": clinician_output.get("voice_mode_applied"),
                                       "mode_b_retry": True,
                                   }))
            except Exception:
                pass  # Use original clinician_output if retry fails

    # Stage E — HardRuleWrapper (clinician content, Change 21a: flag only, never block)
    stage_E_start = _now()
    await _write_stage(db_mod, case_id, "stage_E_hrw", make_running("stage_E_hrw"))
    try:
        hrw_result = await _run_stage_E(clinician_output, critic_output, scene,
                                        evidence_state=_ix_evidence_state)
        await _write_stage(db_mod, case_id, "stage_E_hrw",
                           make_completed("stage_E_hrw", stage_E_start, {
                               "hrw_clinical_flag": hrw_result.get("hrw_clinical_flag"),
                           }))
    except Exception as e:
        await _write_stage(db_mod, case_id, "stage_E_hrw",
                           make_failed("stage_E_hrw", stage_E_start, str(e)))
        raise

    # Stage F — Format (voice_output HRW check + build final_output)
    stage_F_start = _now()
    await _write_stage(db_mod, case_id, "stage_F_format", make_running("stage_F_format"))
    try:
        final_output = await _run_stage_F(clinician_output, hrw_result, scene)
        final_output["v4_phase1"] = v4_phase1
        final_output["v4_phase2"] = v4_phase2
        final_output["v4_review_prompt"] = _v4_review_prompt(v4_phase1)
        final_output["v4_source_explanation"] = _v4_source_explanation(v4_phase2)
        final_output["v4_shengang_subtype"] = v4_shengang_packet
        final_output["v4_treatment_advisory"] = v4_treatment_advisory
        final_output["v4_diagnosis_first"] = v4_diagnosis_first_packet
        final_output["v4_diagnosis_first_training"] = v4_diagnosis_first_training
        final_output["v4_reasoning_workspace"] = v4_reasoning_workspace
        final_output["v4_reasoning_doctor_trace"] = v4_reasoning_workspace.get("doctor_trace")
        final_output["v4_reasoning_patient_summary"] = v4_reasoning_workspace.get("patient_summary")
        final_output["v4_reasoning_output_projection"] = v4_reasoning_output_projection
        final_output["v4_reasoning_training"] = v4_reasoning_training
        final_output = _apply_v4_final_report_compliance(
            final_output, v4_gate_packet, v4_treatment_advisory, clinician_output
        )
        await _write_stage(db_mod, case_id, "stage_F_format",
                           make_completed("stage_F_format", stage_F_start))
    except Exception as e:
        await _write_stage(db_mod, case_id, "stage_F_format",
                           make_failed("stage_F_format", stage_F_start, str(e)))
        raise

    # HRW v1.3.4 HIGH carve-out: forbidden_coben_layer1 / forbidden_device_codes_layer1 → BLOCK
    hrw_high_violations = [
        v for v in (final_output.get("hrw_voice_violations") or [])
        if v.get("rule_id") in _HRW_HIGH_BLOCK_RULES and v.get("clinical_severity") == "HIGH"
    ]
    if hrw_high_violations:
        _log.warning("case_id=%s HRW v1.3.4 HIGH BLOCK: rules=%s — re-dispatching SC for correction",
                     case_id, sorted({v["rule_id"] for v in hrw_high_violations}))
        try:
            clinician_output = await _run_stage_C(
                case_id, case_payload, ir_output, kc_output, cm_output, image_refs, scene,
                forced_voice_mode=clinician_output.get("voice_mode_applied"),
                hrw_correction=hrw_high_violations,
            )
            clinician_output = _apply_v4_gate_consistency(clinician_output, v4_gate_packet)
            hrw_result = await _run_stage_E(clinician_output, critic_output, scene,
                                            evidence_state=_ix_evidence_state)
            final_output = await _run_stage_F(clinician_output, hrw_result, scene)
            final_output["v4_phase1"] = v4_phase1
            final_output["v4_phase2"] = v4_phase2
            final_output["v4_review_prompt"] = _v4_review_prompt(v4_phase1)
            final_output["v4_source_explanation"] = _v4_source_explanation(v4_phase2)
            final_output["v4_shengang_subtype"] = v4_shengang_packet
            final_output["v4_treatment_advisory"] = v4_treatment_advisory
            final_output["v4_diagnosis_first"] = v4_diagnosis_first_packet
            final_output["v4_diagnosis_first_training"] = v4_diagnosis_first_training
            final_output["v4_reasoning_workspace"] = v4_reasoning_workspace
            final_output["v4_reasoning_doctor_trace"] = v4_reasoning_workspace.get("doctor_trace")
            final_output["v4_reasoning_patient_summary"] = v4_reasoning_workspace.get("patient_summary")
            final_output["v4_reasoning_output_projection"] = v4_reasoning_output_projection
            final_output["v4_reasoning_training"] = v4_reasoning_training
            final_output = _apply_v4_final_report_compliance(
                final_output, v4_gate_packet, v4_treatment_advisory, clinician_output
            )
            await _write_stage(db_mod, case_id, "stage_F_format",
                               make_completed("stage_F_format", stage_F_start,
                                              {"hrw_correction_applied": True}))
        except Exception as e:
            err_msg = f"HRW HIGH BLOCK: SC re-correction dispatch error: {e}"
            await _write_stage(db_mod, case_id, "stage_F_format",
                               make_failed("stage_F_format", stage_F_start, err_msg))
            raise RuntimeError(err_msg) from e
        still_high = [
            v for v in (final_output.get("hrw_voice_violations") or [])
            if v.get("rule_id") in _HRW_HIGH_BLOCK_RULES and v.get("clinical_severity") == "HIGH"
        ]
        if still_high:
            err_msg = f"HRW HIGH BLOCK: SC correction failed — {sorted({v['rule_id'] for v in still_high})}"
            await _write_stage(db_mod, case_id, "stage_F_format",
                               make_failed("stage_F_format", stage_F_start, err_msg))
            raise RuntimeError(err_msg)

    # Stage G — Doctor review gate (Scene 1 only)
    if scene == "1_patient":
        await _write_stage(db_mod, case_id, "stage_G_doctor_review",
                           {"status": "awaiting", "started_at": _now()})

    return final_output


async def run_v2(case_id: str, trace_id: str, scene: str, case_payload: dict,
                 db_mod, audit_mod=None):
    """v2 orchestration entry point — enforces case-level latency budget via asyncio.wait_for."""
    image_refs = await asyncio.to_thread(load_image_refs_for_case, case_payload)
    budget = CASE_BUDGETS.get(scene, 1500)
    try:
        return await asyncio.wait_for(
            _run_v2_stages(case_id, trace_id, scene, case_payload, image_refs, db_mod, audit_mod),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        _log.error("case_id=%s case-level budget exceeded budget=%ds scene=%s", case_id, budget, scene)
        raise RuntimeError(f"case-level latency budget exceeded ({budget}s) — resubmit case")


# ── Stage implementations ──────────────────────────────────────────────────────

async def _run_stage_A(case_id: str, case_payload: dict, image_refs: list, scene: str) -> dict:
    """Dispatch to @InitialReader: 6-axis visual anchor + sufficiency gate."""
    payload = {
        "case_id": case_id,
        "scene": "1" if scene.startswith("1") else "3",
        "case_struct": _build_case_struct(case_payload, scene),
        "image_refs": image_refs,
        "diagnosis_first_positive_kb_training": build_diagnosis_first_training_payload("stage_A_initial_reader"),
        "reasoning_loop_training": build_reasoning_loop_training_payload("stage_A_initial_reader"),
    }
    return await _slock_dispatch(
        _AGENT_IR, payload, STAGE_TIMEOUTS["stage_A_initial_reader"],
        late_grace_sec=INITIAL_READER_LATE_GRACE_SEC,
    )


async def _run_stage_A_ensemble(case_id: str, case_payload: dict, image_refs: list,
                                 scene: str, n: int) -> dict:
    """IX-2.1: Run IR n times in parallel. If all agree on axis_1 face_class → return first result.
    If any disagree → return result with ensemble_disagreement=True (triggers escalation upstream).
    Contract: each read is a full independent gestalt, NO majority-vote aggregation.
    """
    results = await asyncio.gather(
        *[_run_stage_A(case_id, case_payload, image_refs, scene) for _ in range(n)],
        return_exceptions=True,
    )
    valid = [r for r in results if isinstance(r, dict) and "axes" in r]
    if not valid:
        raise RuntimeError(f"Ensemble: all {n} IR reads failed")
    face_classes, midline_classes = set(), set()
    for r in valid:
        try:
            face_classes.add(r["axes"]["axis_1"]["face_class"])
        except (KeyError, TypeError):
            pass
        try:
            midline_classes.add(r["axes"]["axis_5"].get("midline") or r["axes"]["axis_5"].get("classification"))
        except (KeyError, TypeError):
            pass
    primary = valid[0]
    axis1_agree = len(face_classes) <= 1
    axis5_agree = len(midline_classes) <= 1
    primary["ensemble_meta"] = {
        "n_reads": n,
        "n_valid": len(valid),
        "axis1_face_classes": sorted(face_classes),
        "axis5_midline_classes": sorted(midline_classes),
        "agreement": axis1_agree and axis5_agree,
    }
    if not axis1_agree or not axis5_agree:
        primary["ensemble_disagreement"] = True
    return primary


def _v4_packet_schema_contract(role: str) -> dict:
    """Source-only packet contract for V4 IR readers; no Stage A/answer-bearing context."""
    return {
        "schema_version": "v4_phase1.0",
        "packet_role": role,
        "response_shape": {
            "case_id": "same case_id",
            "request_id": "echo if available",
            "packet_role": role,
            "side_packet": {
                "packet_role": f"{role}_side_packet" if role in {"convex", "concave"} else role,
                "score": "optional numeric 1-10 or null",
                "anchors": {
                    field: {
                        "value": sorted(_V4_ANCHOR_VALUE_ENUMS[field]),
                        "clarity": sorted(_V4_CLARITY_ENUM),
                        "confound": sorted(_V4_CONFOUND_ENUM),
                        "evidence_ref": "image ref or source-text ref string",
                        "role_note": "short role-specific note",
                    }
                    for field in _V4_ANCHOR_FIELDS
                } if role in {"convex", "concave"} else "omit for non_sagittal",
                "supporting_evidence": "list[str]",
                "counter_evidence": "list[str]",
                "unresolved_anchors": "list[str]",
            },
            "packet": {
                "packet_role": "non_sagittal",
                "interference_flags": "list[dict|str]; must not include final sagittal direction",
            } if role == "non_sagittal" else "omit",
        },
        "hard_constraints": [
            "Use only source case_struct and image_refs from this payload.",
            "Do not use Stage A, KC, CM, SC, Critic, final report, history, or sealed gold.",
            "Do not emit final diagnosis or clinical treatment plan.",
            "If an anchor is not readable from source material, mark it unreadable with evidence_ref.",
            "Keep anterior_crossbite and posterior_crossbite separate; do not merge them into one crossbite anchor.",
            "Use overjet_depth for shallow/deep positive overjet depth; overjet_sign only records sign.",
            "Use molar_relation_sagittal for mesial/distal/neutral/mixed tendency; molar_canine_relation keeps class I/II/III relation.",
            "Use maxillary_arch_form for high palate/narrow arch/crowded-only pattern; do not collapse posterior crossbite into generic crowding.",
            "Use paranasal_support, lip_ap_relation, chin_prominence, and mandibular_soft_tissue_volume for non-measure Walter-rule sagittal anchors when visible.",
            "Use upper_incisor_compensation and lower_incisor_compensation for side-specific incisor compensation; keep legacy incisor_compensation as the aggregate compatibility field.",
            "For positive overjet that is unreliable because of dental compensation under strict narrow/high maxillary-source suspicion, emit incisor_compensation=compensated_positive_overjet instead of generic bimax/upper proclination.",
        ],
    }


async def _run_stage_A_v4_ir_packets(case_id: str, case_payload: dict, ir_output: dict,
                                      image_refs: list, scene: str) -> dict:
    """Dispatch Phase-I sagittal packet readers and return a raw packet bundle.

    Packet failures are non-fatal: deterministic gate normalization will convert missing
    or malformed packet data to unreadable anchors and fail closed.
    """
    payload = {
        "case_id": case_id,
        "scene": "1" if scene.startswith("1") else "3",
        "case_struct": _build_case_struct(case_payload, scene),
        "image_refs": image_refs,
        "source_only": True,
        "v4_phase1_dispatch": True,
        "packet_schema_version": "v4_phase1.0",
        "diagnosis_first_positive_kb_training": build_diagnosis_first_training_payload("stage_A_v4_ir_packet"),
        "reasoning_loop_training": build_reasoning_loop_training_payload("stage_A_v4_ir_packet"),
    }
    results = await asyncio.gather(
        _slock_dispatch(
            _AGENT_CONVEX_IR,
            {**payload, "packet_role": "convex", "packet_schema_contract": _v4_packet_schema_contract("convex")},
            STAGE_TIMEOUTS["stage_A_v4_ir_packets"],
        ),
        _slock_dispatch(
            _AGENT_CONCAVE_IR,
            {**payload, "packet_role": "concave", "packet_schema_contract": _v4_packet_schema_contract("concave")},
            STAGE_TIMEOUTS["stage_A_v4_ir_packets"],
        ),
        _slock_dispatch(
            _AGENT_NONSAGITTAL_IR,
            {
                **payload,
                "packet_role": "non_sagittal",
                "packet_schema_contract": _v4_packet_schema_contract("non_sagittal"),
            },
            STAGE_TIMEOUTS["stage_A_v4_ir_packets"],
        ),
        return_exceptions=True,
    )

    def ok(result):
        return result if isinstance(result, dict) else None

    return {
        "convex": ok(results[0]),
        "concave": ok(results[1]),
        "non_sagittal": ok(results[2]),
        "packet_schema_version": "v4_phase1.0",
        "packet_source": {
            "convex": "agent:ConvexIR" if isinstance(results[0], dict) else f"missing:{type(results[0]).__name__}",
            "concave": "agent:ConcaveIR" if isinstance(results[1], dict) else f"missing:{type(results[1]).__name__}",
            "non_sagittal": "agent:NonSagittalIR" if isinstance(results[2], dict) else f"missing:{type(results[2]).__name__}",
        },
    }


async def _run_stage_B(case_id: str, case_payload: dict, ir_output: dict, scene: str) -> tuple:
    """Dispatch @KnowledgeCurator + @CaseMemory in parallel. Returns (kc_output, cm_output)."""
    base = {
        "case_id": case_id,
        "scene": "1" if scene.startswith("1") else "3",
        "case_struct": _build_case_struct(case_payload, scene),
        "stage_a_output": ir_output,
        "sagittal_consensus_packet": ir_output.get("sagittal_consensus_packet"),
        "source_attribution_packet": ir_output.get("source_attribution_packet"),
        "diagnosis_first_packet": ir_output.get("diagnosis_first_packet"),
    }
    cm_payload = {
        **base,
        "diagnosis_first_positive_kb_training": build_diagnosis_first_training_payload("stage_B_cm"),
        "reasoning_loop_training": build_reasoning_loop_training_payload("stage_B_cm"),
    }
    # Backtest anti-leakage (task #148): exclude specified case_ids from CM retrieval so a
    # case under test cannot retrieve itself / its origin as a reference. DEFAULT-NO-OP —
    # absent or empty => key not added => CM payload byte-identical to current. Set only by
    # the offline backtest harness via case_payload["_backtest_exclude_case_ids"].
    _bt_exclude = case_payload.get("_backtest_exclude_case_ids")
    if _bt_exclude:
        cm_payload["exclude_case_ids"] = list(_bt_exclude)
    results = await asyncio.gather(
        _slock_dispatch(
            _AGENT_KC,
            {
                **base,
                "diagnosis_first_positive_kb_training": build_diagnosis_first_training_payload("stage_B_kc"),
                "reasoning_loop_training": build_reasoning_loop_training_payload("stage_B_kc"),
            },
            STAGE_TIMEOUTS["stage_B_kb_retrieve"],
        ),
        _slock_dispatch(_AGENT_CM, cm_payload, STAGE_TIMEOUTS["stage_B_kb_retrieve"]),
        return_exceptions=True,
    )
    kc_out, cm_out = results[0], results[1]
    # Normalize CM key: some CM versions emit top_cases instead of top_5_cases
    if isinstance(cm_out, dict) and "top_cases" in cm_out and "top_5_cases" not in cm_out:
        cm_out = {**cm_out, "top_5_cases": cm_out["top_cases"]}
    # CM failure is always fatal
    if isinstance(cm_out, Exception):
        raise RuntimeError(f"Stage B CM error: {cm_out}")
    if cm_out.get("status") == "error" or "top_5_cases" not in cm_out:
        raise RuntimeError(f"Stage B CM error: status={cm_out.get('status')}, msg={cm_out.get('error_detail')}")
    # KC timeout → soft-proceed with empty e_blocks; SC sysprompt detects kc_status=timeout
    if isinstance(kc_out, Exception):
        kc_out = {"e_blocks": [], "opposing_evidence": [], "kb_gaps": [], "kc_status": "timeout", "status": "timeout"}
    elif kc_out.get("status") == "error" or "e_blocks" not in kc_out:
        raise RuntimeError(f"Stage B KC error: status={kc_out.get('status')}, msg={kc_out.get('error_detail')}")
    return kc_out, cm_out


def _positive_diagnosis_card_context(ir_for_dispatch: dict) -> Optional[dict]:
    """task #140 advisory card context (gate-matched DEFAULT projection), or None.

    Pure + side-effect-free; the caller gates this on
    config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED (default OFF). Cards are advisory
    reasoning context, never an auto-verdict; non-matching gates yield None.
    """
    from positive_diagnosis_cards import retrieve_cards_for_gate
    gate = (ir_for_dispatch.get("sagittal_consensus_packet") or {}).get("gate_result")
    cards = retrieve_cards_for_gate(gate)
    if not cards:
        return None
    return {
        "cards": cards,
        "gate_result": gate,
        "usage_boundary": "advisory_default_projection_reasoning_context_not_auto_verdict",
    }


async def _run_stage_C(case_id: str, case_payload: dict, ir_output: dict, kc_output: dict,
                        cm_output: dict, image_refs: list, scene: str,
                        forced_voice_mode: Optional[str] = None,
                        hrw_correction: Optional[list] = None) -> dict:
    """Dispatch to @SeniorClinician: multimodal synthesis + voice formatting."""
    ir_for_dispatch = dict(ir_output)
    if forced_voice_mode == "B_difficult_diagnosis_warning":
        ir_for_dispatch["voice_mode_hint"] = "B"

    cm_for_dispatch = {k: v for k, v in cm_output.items() if k != "wang_te_decision_patterns"}
    payload = {
        "case_id": case_id,
        "scene": "1" if scene.startswith("1") else "3",
        "case_struct": _build_case_struct(case_payload, scene),
        "stage_a_output": ir_for_dispatch,
        "sagittal_consensus_packet": ir_for_dispatch.get("sagittal_consensus_packet"),
        "source_attribution_packet": ir_for_dispatch.get("source_attribution_packet"),
        "diagnosis_first_packet": ir_for_dispatch.get("diagnosis_first_packet"),
        "stage_b_kc": kc_output,
        "stage_b_cm": cm_for_dispatch,
        "image_refs": image_refs,
        "diagnosis_first_positive_kb_training": build_diagnosis_first_training_payload(
            "stage_C_senior_clinician"
        ),
        "reasoning_loop_training": build_reasoning_loop_training_payload("stage_C_senior_clinician"),
    }
    # task #140 (spec §5/§8) — positive-diagnosis card advisory context. DEFAULT OFF
    # (config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED): when OFF the payload is byte-identical
    # to current (zero live-user effect until unified test + human review). When ON, inject
    # gate-matched DEFAULT-projection cards as advisory reasoning context — NOT an auto-verdict.
    if config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED:
        _pd_ctx = _positive_diagnosis_card_context(ir_for_dispatch)
        if _pd_ctx:
            payload["positive_diagnosis_card_context"] = _pd_ctx
        # task #140 inc-2 + §3 node-keyed serving: serve a node-keyed slice (each node's
        # own discriminator + forbidden marks, permission tiers, reverse falsification
        # entry) gated on the case's sagittal gate_result — not the whole-pack ammo
        # (anti-钻牛角尖 E4/E5/E9). Advisory, not auto-verdict. Same default-OFF flag.
        from decision_chain_scaffold import node_keyed_scaffold
        _dc_gate = (ir_for_dispatch.get("sagittal_consensus_packet") or {}).get("gate_result")
        payload["decision_chain_scaffold"] = node_keyed_scaffold(_dc_gate)
    if hrw_correction:
        payload["hrw_correction_required"] = True
        payload["hrw_violations"] = hrw_correction
    shutil.copy2(_SC_SYSPROMPT_SRC, _SC_SYSPROMPT_DST)
    return await _slock_dispatch(_AGENT_SC, payload, STAGE_TIMEOUTS["stage_C_senior_clinician"])


def _strip_axis_diagnostic_gloss(ax: dict) -> dict:
    """O-5 axis content de-glossing: remove '= [verdict]' phrases from axis text fields.

    Applies to axes 3-6 per DW ruling (msg=a7ba94ad):
    - axis_3 value: remove 'observation = conclusion' clauses (split by ';', strip after '=')
    - axis_4 candidate_list labels: strip 'AND-3 [verdict] / ' prefix (resolved AND-3 is W-1 🔒Walter)
    - axis_5, axis_6: confirmed gloss-free (structured fields, no = pattern) — pass through
    """
    import copy, re as _re
    ax = copy.deepcopy(ax)
    axnum = ax.get("axis")

    if axnum == 3:
        val = ax.get("value", "")
        if isinstance(val, str):
            # Split by ';', for each segment strip everything from ' = ' onward (the conclusion half)
            segments = val.split(";")
            clean_segs = []
            for seg in segments:
                stripped = seg.split(" = ")[0].strip()
                if stripped:
                    clean_segs.append(stripped)
            ax["value"] = "; ".join(clean_segs)

    elif axnum == 4:
        clist = ax.get("candidate_list")
        if isinstance(clist, list):
            new_clist = []
            for item in clist:
                item = dict(item)
                label = item.get("label", "")
                # Strip 'AND-3 [VERDICT] / ' prefix (resolved assessment, W-1 🔒Walter)
                cleaned = _re.sub(r'^AND-3\s+\S+\s*/\s*', '', label, flags=_re.IGNORECASE)
                item["label"] = cleaned.strip()
                new_clist.append(item)
            ax["candidate_list"] = new_clist

    return ax


def _build_ir_for_critic(ir_output: dict) -> dict:
    """O-5 Critic IR payload: whitelist-only (deny-by-default) + axis de-glossing.

    PERMITTED axes: axis 3, 4, 5, 6 only (by numeric axis field in list schema).
    PERMITTED top-level: risk_patterns_hinted, image_analysis, image_evidence_level,
                          sufficiency_verdict.
    All other fields (targeted_query, reasoning_trace, etc.) denied implicitly.
    Axis content: '= [verdict]' gloss phrases stripped per DW msg=a7ba94ad.

    hypothesis_under_test: neutral face_type label derived from axis 1 value
    before stripping — gives Critic the proposition to falsify without IR's confidence
    endorsement. (SC's stage_c_output provides the second hypothesis channel.)

    DW-spec: #wt-v3-exec msg=936a3e96 — whitelist, deny-by-default.
    axes schema: list[dict] with "axis" key (int 1-6) per STAGE_A_SCHEMA.
    """
    axes_raw = ir_output.get("axes") or []
    _AXIS_PERMIT_NUMS = {3, 4, 5, 6}
    safe_axes = [
        _strip_axis_diagnostic_gloss(a)
        for a in axes_raw
        if isinstance(a, dict) and a.get("axis") in _AXIS_PERMIT_NUMS
    ]

    _TOP_WHITELIST = {
        "risk_patterns_hinted", "image_analysis", "image_evidence_level", "sufficiency_verdict"
    }
    result = {k: v for k, v in ir_output.items() if k in _TOP_WHITELIST}
    result["axes"] = safe_axes

    try:
        axis1 = next(a for a in axes_raw if isinstance(a, dict) and a.get("axis") == 1)
        face_class = axis1.get("value")
        if face_class:
            result["hypothesis_under_test"] = {"face_type": face_class}
    except (StopIteration, TypeError):
        pass

    return result


async def _run_stage_D(case_id: str, case_payload: dict, ir_output: dict, kc_output: dict,
                        cm_output: dict, clinician_output: dict, image_refs: list, scene: str) -> dict:
    """Dispatch to @Critic: independent re-anchor + disagreement + drift detection.

    W4 weld: payload must not contain hypothesis_frame, dispatch_trace, leading_hypothesis_label,
    or cross_track_flag. _assert_w4_critic_clean() enforces this fail-closed before dispatch.
    cross_track_flag stripped from clinician_output upstream in _run_stage_C_ix (护栏③).
    """
    cm_for_dispatch = {k: v for k, v in cm_output.items() if k not in _CRITIC_CM_QUARANTINE}
    # O-5 DW-sign: strip KC note — it's a per-case diagnostic conclusion (face class + subtype
    # + trap classification), not KB reference. Measurements (Wits/U1-SN/overjet) reach Critic
    # via image_refs (ceph films) + axis_3 dental findings. (DW ruling msg=39546ad9)
    kc_for_critic = {k: v for k, v in kc_output.items() if k != "note"}
    ir_for_critic = _build_ir_for_critic(ir_output)
    payload = {
        "case_id": case_id,
        "scene": "1" if scene.startswith("1") else "3",
        "case_struct": _build_case_struct(case_payload, scene),
        "stage_a_output": ir_for_critic,
        "sagittal_consensus_packet": ir_output.get("sagittal_consensus_packet"),
        "source_attribution_packet": ir_output.get("source_attribution_packet"),
        "diagnosis_first_packet": ir_output.get("diagnosis_first_packet"),
        "stage_b_kc": kc_for_critic,
        "stage_b_cm": cm_for_dispatch,
        "stage_c_output": clinician_output,
        "image_refs": image_refs,
        "diagnosis_first_positive_kb_training": build_diagnosis_first_training_payload("stage_D_critic"),
        "reasoning_loop_training": build_reasoning_loop_training_payload("stage_D_critic"),
    }
    _assert_w4_critic_clean(payload)  # W4: fail-closed before dispatch
    return await _slock_dispatch(_AGENT_CRITIC, payload, STAGE_TIMEOUTS["stage_D_critic"])


def _neutral_critic_output(reason: str) -> dict:
    return {
        "status": "skipped",
        "critic_enabled": False,
        "skip_reason": reason,
        "overall_disagreement_count": 0,
        "critical_concerns": [],
        "cross_modal_check": {},
        "voice_mode_consistency_check": {},
        "cross_case_drift_log": [],
        "overall_assessment": "neutral_critic_disabled",
    }


async def _run_stage_D_with_flag(db_mod, case_id: str, case_payload: dict, ir_output: dict,
                                  kc_output: dict, cm_output: dict, clinician_output: dict,
                                  image_refs: list, scene: str) -> dict:
    if not config.STAGE_D_CRITIC_ENABLED:
        reason = "STAGE_D_CRITIC_ENABLED=0"
        critic_output = _neutral_critic_output(reason)
        await _write_stage(db_mod, case_id, "stage_D_critic",
                           make_completed("stage_D_critic", _now(), {
                               "critic_enabled": False,
                               "dispatch_skipped": True,
                               "skip_reason": reason,
                               "overall_disagreement_count": 0,
                           }))
        _log.info("case_id=%s Stage D Critic dispatch skipped by %s", case_id, reason)
        return critic_output

    stage_D_start = _now()
    await _write_stage(db_mod, case_id, "stage_D_critic", make_running("stage_D_critic"))
    try:
        critic_output = await _run_stage_D(
            case_id, case_payload, ir_output, kc_output, cm_output, clinician_output, image_refs, scene
        )
        _d_violations = _validate_stage_output("stage_D_critic", critic_output)
        _d_extra = {
            "critic_enabled": True,
            "overall_disagreement_count": critic_output.get("overall_disagreement_count"),
        }
        if _d_violations:
            _d_extra["schema_violations"] = _d_violations
        await _write_stage(db_mod, case_id, "stage_D_critic",
                           make_completed("stage_D_critic", stage_D_start, _d_extra))
        return critic_output
    except Exception as e:
        await _write_stage(db_mod, case_id, "stage_D_critic",
                           make_failed("stage_D_critic", stage_D_start, str(e)))
        raise


def _run_hrw_cl2_always_on(clinician_output: dict) -> list[dict]:
    """HRW-CL2 always-on check: 凸面断言 + SNA后缩信号 → flag (decoupled from IX_ENABLED).

    CL2 is the single keep-1 dead-rule (76db): prevents confident convex+SNA-retrusive
    misclassification that would lead to wrong extraction/surgery direction.
    CL1/CL3/CL4/CL5 remain IX_ENABLED-gated (IX-loop-coupled, not MVP-ready).
    Decouple wire authorized: jonathan db2bd365, DW f60482e6/59ddd2ea, KBadvisor 0198bdc2.
    """
    import re as _re
    sections = clinician_output.get("sections") or {}
    full_text = "\n\n".join(str(v) for v in sections.values() if v)
    face_class_protrusive = bool(_re.search(r'凸面|突面|protrusive.?face|凸型', full_text, _re.IGNORECASE))
    if face_class_protrusive:
        sna_retrusive = bool(_re.search(
            r'SNA.*后缩|后缩.*SNA|SNA.*retrusive|retrusive.*SNA|SNA偏小|SNA.*小|上颌后缩',
            full_text, _re.IGNORECASE
        ))
        if sna_retrusive:
            return [{
                "rule_id": "ix_hrw_cl2_convex_sna_retrusive",
                "clinical_severity": "HIGH",
                "block": False,  # Change 21a: flag-only, never block
                "message": "HRW-CL2: 凸面断言 + SNA后缩信号 → flag (3× 误判模式; always-on keep-1)",
            }]
    return []


def _run_ix27_hrw_clinical_floor(clinician_output: dict, evidence_state: dict) -> list[dict]:
    """IX-2.7 HRW deterministic floor — 4 clinical invariant checks (DW ba41facc).

    Zero-LLM, program-level, defense-in-depth. Fires on QUALITATIVE from-image anchors,
    NOT gated on Batch A numeric cutoffs (DW 54483f22: floor must be live during build).
    Returns list of floor violation dicts; empty = floor passed.
    CL2 removed from this function — now always-on via _run_hrw_cl2_always_on (wire-a, jonathan db2bd365).

    批A v1.2 re-keyed gates (DW 続138/140/142, KBadvisor 25ca1edf/5207634f, 太上老君 3adbfa38):
    HRW-CL1: re-keyed from NULL-edge heuristic to four_check-derived concave-falsification-incomplete signal.
      Gate DERIVES concave_ruled_out from four_check verdict enums (absent→UNRESOLVED, fail-closed).
      FIRE on (i) direction_locked AND NOT derived concave_ruled_out, OR (ii) concave_source=="未pin".
    HRW-CL3: re-keyed from text-token fail-closed to severity_determination enum + derived skeletal_anchor.
      Gate DERIVES skeletal_anchor_used = skeletal_anchor_measurement_valid AND sna_读 AND snb_读.
      Cross-check: rec_type=surgical OR severity_class∈{中度,重度} forces engaged even if SC under-declares.
    HRW-CL4: face classification firm + no cross-modal anchor in anchor_source → BLOCK.
      侧位片|头颅侧位 added to structured-anchor regex (ceph-present schema-FP fix).
    HRW-CL5: 突吸偏/凹增偏 leaf asserted + 4-conjunct fire-set incomplete → downgrade+escalate.
    """
    import re as _re
    violations = []

    sections = clinician_output.get("sections") or {}
    full_text = "\n\n".join(str(v) for v in sections.values() if v)

    # HRW-CL1 (批A re-key): concave-falsification-INCOMPLETE signal, decoupled from NULL-edge heuristic.
    # Gate DERIVES concave_ruled_out from direction_falsification.four_check verdict enums.
    # Anchor absent → UNRESOLVED (fail-closed). Gate does NOT trust SC-set concave_ruled_out bool.
    # DW four-anchor spec (DW 続138): SNA_对颅底 / 上颌弓宽_腭穹 / 上唇_颏AP / 鼻旁区.
    # FIRE conditions (DW 続138, KBadvisor 25ca1edf):
    #   (i) direction locked (extraction/convex) AND NOT derived concave_ruled_out
    #   (ii) concave_source == "未pin" (concave affirmed, source-attribution open)
    # Conditions (i) and (iii)[any-UNRESOLVED+direction-locked] collapse: (i) subsumes (iii) because
    # NOT-all-REFUTES_CONCAVE covers both UNRESOLVED and SUPPORTS_CONCAVE cases.
    _df = clinician_output.get("direction_falsification") or {}
    _four_check = _df.get("four_check") or {}
    _VERDICT_VALID = {"SUPPORTS_CONCAVE", "REFUTES_CONCAVE", "UNRESOLVED"}
    _cl1_anchors = ["SNA_对颅底", "上颌弓宽_腭穹", "上唇_颏AP", "鼻旁区"]
    _cl1_verdicts = {
        a: (_four_check.get(a) if _four_check.get(a) in _VERDICT_VALID else "UNRESOLVED")
        for a in _cl1_anchors
    }
    _cl1_concave_ruled_out = all(v == "REFUTES_CONCAVE" for v in _cl1_verdicts.values())
    _cl1_concave_source = _df.get("concave_source")  # None=not-affirmed; "未pin"=affirmed-open
    _cl1_direction_locked = bool(_re.search(
        r'拔牙|内收|extraction|retraction|减数|凸面|前突', full_text, _re.IGNORECASE
    ))

    _cl1_fire_reasons = []
    if _cl1_direction_locked and not _cl1_concave_ruled_out:
        _non_refuted = [a for a, v in _cl1_verdicts.items() if v != "REFUTES_CONCAVE"]
        _cl1_fire_reasons.append(
            f"(i/iii) direction_locked + 凹面未证伪 (non-REFUTES={_non_refuted})"
        )
    if _cl1_concave_source == "未pin":
        _cl1_fire_reasons.append("(ii) concave_source=未pin (source-attribution open)")

    if _cl1_fire_reasons:
        violations.append({
            "rule_id": "ix_hrw_cl1_concave_source_not_excluded",
            "clinical_severity": "HIGH",
            "block": True,
            "message": (
                f"HRW-CL1: 凹面证伪-INCOMPLETE → BLOCK "
                f"({'; '.join(_cl1_fire_reasons)})"
            ),
        })

    # HRW-CL2 removed from this function: now always-on via _run_hrw_cl2_always_on (wire-a).

    # HRW-CL3 (批A re-key): severity_determination enum + derived skeletal_anchor_used.
    # DW 続140/142: gate DERIVES skeletal_anchor_used = skeletal_anchor_measurement_valid AND sna AND snb.
    # skeletal_anchor_measurement_valid = direction_falsification.measurement_source ∈ {R18, 报告引值}.
    # Cross-check (KBadvisor 2f49a27f): force engaged=True when rec_type=surgical OR
    #   skeletal_severity_class∈{中度, 重度_手术阈值}, even if SC under-declares NOT_AT_ISSUE.
    # Fail-closed default (absent severity_determination + cross-check signal → engaged).
    _cl3_severity_determination = clinician_output.get("severity_determination")
    _cl3_skeletal_severity_class = clinician_output.get("skeletal_severity_class", "未评估")
    _cl3_rec_type_cross_check = bool(_re.search(
        r'正颌外科|正颌手术|正畸正颌联合|正畸-正颌联合|颌面外科|口腔颌面外科|外科手术|手术治疗|手术干预'
        r'|需要手术|需手术|截骨术|截骨|颏成形|牵张成骨|外科会诊|下颌升支矢状劈开|勒福'
        r'|Le Fort|LeFort|BSSO|orthognathic|surgery referral|surgical correction|osteotomy|genioplasty'
        r'|正颌',
        full_text, _re.IGNORECASE
    ))
    _cl3_severity_class_cross_check = _cl3_skeletal_severity_class in {"中度", "重度_手术阈值"}
    _cl3_cross_check_engaged = _cl3_rec_type_cross_check or _cl3_severity_class_cross_check

    # severity_determination engaged (SC-stated or cross-check override):
    _cl3_severity_engaged = _cl3_severity_determination in {"LOCKED_FIRM", "ESCALATED_FOR_ANCHOR"}
    _cl3_engaged = _cl3_severity_engaged or _cl3_cross_check_engaged

    if _cl3_engaged:
        # Derive skeletal_anchor_used: gate does NOT trust SC-set bool (derive-not-trust discipline).
        # skeletal_anchor_measurement_valid = measurement_source provenance (R18 or 报告引值).
        _cl3_measurement_source = _df.get("measurement_source")  # reuse _df from CL1
        _cl3_anchor_meas_valid = _cl3_measurement_source in {"原片直接测(R18)", "报告引值"}
        _cl3_sna_read = bool((clinician_output.get("skeletal_anchor_used") or {}).get("sna_对颅底_read"))
        _cl3_snb_read = bool((clinician_output.get("skeletal_anchor_used") or {}).get("snb_对颅底_read"))
        _cl3_skeletal_anchor_used = _cl3_anchor_meas_valid and _cl3_sna_read and _cl3_snb_read

        if not _cl3_skeletal_anchor_used:
            _cl3_engaged_src = (
                _cl3_severity_determination if _cl3_severity_determination else
                ("cross-check/rec_type" if _cl3_rec_type_cross_check else "cross-check/severity_class")
            )
            violations.append({
                "rule_id": "ix_hrw_cl3_surgical_no_cranial_base_anchor",
                "clinical_severity": "HIGH",
                "block": True,
                "message": (
                    f"HRW-CL3: severity engaged ({_cl3_engaged_src}) + skeletal_anchor_used=FALSE "
                    f"(meas_valid={_cl3_anchor_meas_valid} sna={_cl3_sna_read} snb={_cl3_snb_read}) "
                    f"→ BLOCK (批A re-key: measurement_source={_cl3_measurement_source!r})"
                ),
            })

    # HRW-CL4 (P2-#3 structural; DW e1c2da22 / 太上老君 e1c2da22):
    # Discharge: structured anchor_source field ONLY — text "SNA" mention MUST NOT discharge (fail-OPEN danger).
    # Belt-suspenders text path = trigger-tightening ONLY (more BLOCK), never discharge.
    _cl4_axis_lock = clinician_output.get("axis_lock_status") or []
    _cl4_axis1 = next((e for e in _cl4_axis_lock if isinstance(e, dict) and e.get("axis") == 1), None)
    _cl4_anchor_source = (_cl4_axis1.get("anchor_source") or "") if _cl4_axis1 else ""
    _cl4_has_structured_anchor = bool(_re.search(
        r'SNA|SNB|ANB|ceph|头影|CBCT|弓宽|arch.?width|倾度|inclination|U1|overjet|覆盖'
        r'|侧位片|头颅侧位',
        _cl4_anchor_source, _re.IGNORECASE
    ))
    # Primary: structural firm face-class signal
    _cl4_structural_trigger = _cl4_axis1 is not None and bool(_cl4_axis1.get("locked"))
    # Belt-suspenders text: catches profile-only firm-sagittal the structural path missed (more conservative)
    _cl4_text_trigger = bool(_re.search(
        r'(仅|只|solely).{0,20}侧貌|(侧貌).{0,20}(定性|诊断|分类|classification)',
        full_text, _re.IGNORECASE
    ))
    if (_cl4_structural_trigger or _cl4_text_trigger) and not _cl4_has_structured_anchor:
        _cl4_src = "structural" if _cl4_structural_trigger else "text-belt-suspenders"
        violations.append({
            "rule_id": "ix_hrw_cl4_profile_only",
            "clinical_severity": "HIGH",
            "block": True,
            "message": (
                f"HRW-CL4: 矢状 face_class firm 无 cross-modal anchor → BLOCK "
                f"(trigger={_cl4_src} label={(_cl4_axis1 or {{}}).get('label')!r} "
                f"anchor_source={_cl4_anchor_source!r}; discharge=structured-only)"
            ),
        })

    # HRW-CL5: 突吸偏/凹增偏 leaf asserted + any of 4-conjunct fire-arms missing → downgrade+escalate
    # DW tightened (8d2df486): fire-set = arm-a升支(主分水岭) ∧ arm-c中线 ∧ AP轴 ∧ laterality患侧标.
    # arm-b (髁突 cortical-continuity) = subtype DISCRIMINATOR, NOT a fire-arm; arm-b alone ≠ leaf-close.
    # DW: downgrade (not block). Forbid firm leaf unless full 4-conjunct fire-set confirmed.
    leaf_asserted = bool(_re.search(
        r'突吸偏|凹增偏|bilateral.?condyle.?absorb|unilateral.?condyle.?hyper',
        full_text, _re.IGNORECASE
    ))
    if leaf_asserted:
        # arm-a: 升支对称 confirmed (ramus symmetry — 主分水岭)
        arma_ok = bool(_re.search(r'升支.*对称|对称.*升支|ramus.*(sym|asym)|升支.*不对称|不对称.*升支', full_text, _re.IGNORECASE))
        # arm-c: 中线 confirmed (midline direction)
        armc_ok = bool(_re.search(r'中线.*确认|确认.*中线|中线.*偏移|midline.*(confirm|deviat|measur)', full_text, _re.IGNORECASE))
        # AP轴: AP axis confirmed
        ap_ok = bool(_re.search(r'AP轴|AP.?axis|矢状.*偏|偏.*矢状|sagittal.*asym', full_text, _re.IGNORECASE))
        # laterality: patient-side labeled
        lat_ok = bool(_re.search(r'患侧|健侧|left.?side|right.?side|左侧|右侧|laterality', full_text, _re.IGNORECASE))

        missing_arms = []
        if not arma_ok:
            missing_arms.append("arm-a升支对称")
        if not armc_ok:
            missing_arms.append("arm-c中线")
        if not ap_ok:
            missing_arms.append("AP轴")
        if not lat_ok:
            missing_arms.append("laterality患侧标")

        if missing_arms:
            violations.append({
                "rule_id": "ix_hrw_cl5_leaf_fire_set_incomplete",
                "clinical_severity": "MEDIUM",
                "block": False,  # downgrade, not block (DW: 禁 firm leaf, not 禁 mention)
                "message": f"HRW-CL5: 突吸偏/凹增偏 leaf 断言 + 火力集缺 {missing_arms} → downgrade+escalate",
            })

    return violations


def _run_direction_falsification_gate(clinician_output: dict) -> list[dict]:
    """v3 §13 gate: high-risk direction conclusion present but direction_falsification missing/empty.

    Thin backstop (L1-L4 residual catcher). Flag path only — NEVER block (M-1 invariant).
    High-risk triggers: convex lock / extraction / surgery / 'skeletal light'/'no-surgery' / unilateral lock.
    """
    import re as _re
    sections = clinician_output.get("sections") or {}
    full_text = "\n\n".join(str(v) for v in sections.values() if v)

    # v3 DW高危措辞全集 (2026-06-04): 每类不可逆动作 lexeme 独立 arm，不要求方向词共现。
    # Categories: ①拔牙 ②凸/前突 ③正颌手术 ④反向不可逆(不手术/骨性轻) ⑤偏斜sub-class ⑥凹面锁
    high_risk_pattern = _re.compile(
        r'拔牙|减数|拔除|拔4|拔牙内收|拔牙矫治|拔牙代偿|上颌减数|下颌减数|拔前磨牙|'
        r'凸面|突面|前突|双颌前突|牙槽性前突|唇倾前突|上前牙内收|内收上前牙|'
        r'正颌|转外科|手术评估|下颌前徙|上颌前移|双颌手术|Le\s*Fort|BSSO|颏成形|'
        r'牙性可代偿|牙性代偿|可代偿|掩饰性治疗|掩饰治疗|camouflage|'
        r'非手术|不需手术|不必手术|不手术|骨性轻|骨性较轻|轻度骨性|牙性为主|单纯正畸可解|正畸掩饰|'
        r'偏颌|偏斜|颜面不对称|中线偏|单侧锁定|颌位锁定|突吸偏|凹增偏|'
        r'凹面|上颌源凹面|反合骨性',
        _re.IGNORECASE,
    )
    if not high_risk_pattern.search(full_text):
        return []

    df = clinician_output.get("direction_falsification")
    if df is None:
        missing = True
        empty_basis = False
    else:
        missing = False
        ruled_out = (df.get("ruled_out_basis") or "").strip()
        empty_basis = not ruled_out or ruled_out in ("", "AI不确定", "AI 不确定")

    if missing or empty_basis:
        reason = "direction_falsification 字段缺失" if missing else "ruled_out_basis 为空或泛泛"
        return [{
            "rule_id": "v3_direction_falsification_gate",
            "clinical_severity": "MEDIUM",
            "block": False,  # NEVER block — flag + confidence suppression only
            "message": f"v3 §13 gate: 高危方向结论命中但 {reason} → 未证伪 (fail-safe); 压低 confidence, 抑制方向建议",
        }]
    return []


async def _run_stage_E(clinician_output: dict, critic_output: dict, scene: str,
                        evidence_state: Optional[dict] = None) -> dict:
    """HardRuleWrapper: clinician content check (Change 21a: flag only, never block).

    Also runs IX-2.7 HRW clinical floor (_run_ix27_hrw_clinical_floor) when IX is enabled.
    IX-2.7 floor is deterministic/zero-LLM and live pre-Batch A (qualitative anchor checks).
    """
    from HardRuleWrapper import check_output, check_critic_high_severity

    sections = clinician_output.get("sections") or {}
    content = "\n\n".join(str(v) for v in sections.values() if v)
    voice_mode = clinician_output.get("voice_mode_applied", "A_standard")

    hrw_check = await asyncio.to_thread(check_output, content, scene, voice_mode, "clinician_content")
    critic_check = check_critic_high_severity(critic_output)

    # CL2 always-on (wire-a: decoupled from IX_ENABLED, jonathan db2bd365).
    # CL1/CL3/CL4/CL5 remain IX_ENABLED-gated (IX-loop-coupled, not MVP-ready).
    cl2_violations = _run_hrw_cl2_always_on(clinician_output)

    # v3 §13 direction_falsification backstop (always-on, thin residual catcher after L1-L4).
    df_gate_violations = _run_direction_falsification_gate(clinician_output)

    # V4 Phase I gate-consistency floor: flag-only, deterministic from persisted gate packet.
    v4_gate_violations = _run_v4_gate_consistency_floor(clinician_output)

    # IX-2.7 HRW clinical floor (CL1/CL3/CL4/CL5; CL2 removed — runs always-on above)
    ix_floor_violations = []
    if config.IX_ENABLED:
        ix_floor_violations = _run_ix27_hrw_clinical_floor(
            clinician_output, evidence_state or {}
        )

    all_violations = (
        (hrw_check.get("violations") or [])
        + (critic_check.get("violations") or [])
        + cl2_violations
        + df_gate_violations
        + v4_gate_violations
        + ix_floor_violations
    )
    hrw_clinical_flag = None
    if all_violations:
        severity_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "error": 3}
        max_sev = max(
            (v.get("clinical_severity", v.get("severity", "LOW")) for v in all_violations),
            key=lambda s: severity_rank.get(s, 0),
            default="LOW",
        )
        hrw_clinical_flag = {"violations": all_violations, "clinical_severity_max": max_sev}

    return {"hrw_clinical_flag": hrw_clinical_flag}


async def _run_stage_F(clinician_output: dict, hrw_result: dict, scene: str) -> dict:
    """Format: voice_output HRW check on rendered_markdown + build final DB payload."""
    from HardRuleWrapper import check_output

    rendered_markdown = clinician_output.get("rendered_markdown", "")
    voice_mode = clinician_output.get("voice_mode_applied", "A_standard")

    # HRW length gate uses sections_text (full clinical narrative), not rendered_markdown (condensed summary).
    hrw_voice_violations = []
    sections = clinician_output.get("sections") or {}
    sections_text = "\n\n".join(str(v) for v in sections.values() if v)
    if sections_text:
        hrw_voice = await asyncio.to_thread(check_output, sections_text, scene, voice_mode, "voice_output")
        hrw_voice_violations = hrw_voice.get("violations") or []

    return {
        "sections": clinician_output.get("sections", {}),
        "rendered_markdown": rendered_markdown,
        "word_count": clinician_output.get("char_count", 0),
        "layer_2": clinician_output.get("layer_2"),
        "image_anchors": clinician_output.get("image_anchors") or [],
        "voice_mode_applied": voice_mode,
        "hrw_voice_violations": hrw_voice_violations,
        "confidence": clinician_output.get("confidence"),
        "学派_attribution_used": clinician_output.get("学派_attribution_used"),
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _infer_voice_mode(clinician_payload: dict, scene: str,
                      critic_payload: Optional[dict] = None) -> str:
    """Ported from SlimOrchestrator._infer_voice_mode (Change 5 / Tier 3 amendment)."""
    if scene != "1_patient":
        return "doctor_to_doctor"

    conf = clinician_payload.get("confidence", 0.7)
    uncertainty_count = len(clinician_payload.get("uncertainty_flags") or [])

    critic_disagree_count = 0
    critic_high_concerns = 0
    if isinstance(critic_payload, dict):
        critic_disagree_count = critic_payload.get("overall_disagreement_count") or 0
        for c in (critic_payload.get("critical_concerns") or []):
            if isinstance(c, dict) and c.get("severity") == "HIGH":
                critic_high_concerns += 1

    if (conf < 0.65 or critic_disagree_count > 1
            or critic_high_concerns > 0 or uncertainty_count >= 3):
        return "B_difficult_diagnosis_warning"
    return "A_standard"


def _build_case_struct(case_payload: dict, scene: str) -> dict:
    """Build case_struct for Stage A/B/C/D dispatch."""
    struct = {
        "age": case_payload.get("patient_age") or case_payload.get("age"),
        "sex": (case_payload.get("patient_gender")
                or case_payload.get("patient_sex")
                or case_payload.get("sex")),
    }
    if scene.startswith("1"):
        struct["chief_complaint"] = case_payload.get("chief_complaint")
    else:
        struct["doctor_question"] = (
            case_payload.get("doctor_specific_question")
            or case_payload.get("chief_complaint_doctor")
        )
    return struct


async def _write_stage(db_mod, case_id: str, key: str, value: dict):
    await asyncio.to_thread(db_mod.update_case_stage_info, case_id, key, value)


def _merge_stage_extras(*extras: Optional[dict]) -> dict:
    merged = {}
    for extra in extras:
        if isinstance(extra, dict):
            merged.update(extra)
    return merged


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
