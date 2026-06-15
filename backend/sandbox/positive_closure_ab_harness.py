"""Offline sandbox A/B harness for diagnose-first positive closure.

OFFLINE ONLY. This module is NOT imported by the webapp or the hot-path
orchestrator, performs no live deploy, opens no service, and does not touch
gbrain.db. It is the ready-to-run A/B harness for 太上 task #143 (live-webapp
wiring stays HALTed under #140).

It runs a per-case diagnosis closure in two modes:
  - "baseline":     no positive-diagnosis card context
  - "card_context": matching cards (DEFAULT projection) injected as advisory context

Diagnose-first positive closure (jonathan 开发第一; warnings are a floor, never the
headline):
  - evidence-sufficient          -> state main diagnosis + reasoning basis
  - evidence-insufficient        -> name actionable missing evidence (not a vague warning)
  - high-risk/surgery/TMD-active  -> review_required, still giving direction + differential

Card retrieval fires ONLY on matching case patterns (the v4 sagittal-gate
`gate_result`). Non-matching cases (true_convex_closed / unresolved) get NO card
context — a clean negative control. Cards enter as DEFAULT projection only
(forbidden_inference / minimum_positive_anchor_set / calibration_boundaries
preserved; zero source/biometric data).

The card A/B effect lives at the SeniorClinician reasoning seam (`reasoner`): the
default stub is deterministic and offline so the harness mechanics run today. A
live SC reasoner plugs into the same seam when Walter-reviewed, system-unseen
cases arrive (a separate human decision). The stub does NOT fabricate an accuracy
delta between modes — wiring-in is not accuracy improvement.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from positive_diagnosis_cards import (  # noqa: E402
    GATE_RESULT_RETRIEVAL_TAGS as GATE_RESULT_TO_RETRIEVAL_TAGS,
    retrieve_positive_diagnosis_cards,
)

# GATE_RESULT_TO_RETRIEVAL_TAGS is imported from the canonical backend module (single
# source of truth, also used by the hot-path #140 wiring). Only the masked/frank-concave
# review gates retrieve cards; true_convex_closed and unresolved retrieve nothing
# (clean negative control).

CLOSURE_POSITIVE = "positive_diagnosis"
CLOSURE_MISSING = "missing_evidence"
CLOSURE_REVIEW = "review_required"

MODE_BASELINE = "baseline"
MODE_CARD_CONTEXT = "card_context"


def retrieve_card_context(gate_result: str) -> list[dict[str, Any]]:
    """DEFAULT-projection cards matching the gate signal; [] for non-matching gates."""
    tags = GATE_RESULT_TO_RETRIEVAL_TAGS.get(gate_result, [])
    if not tags:
        return []
    return retrieve_positive_diagnosis_cards(tags=tags, requested_projection="default")


def _is_high_risk(case: dict[str, Any]) -> Optional[str]:
    """Return a high-risk reason (surgery / TMD-active / other) or None."""
    risk = case.get("risk") or {}
    if risk.get("surgery_candidate"):
        return "surgery_candidate"
    if risk.get("tmd_active"):
        return "tmd_active_progression"
    others = risk.get("other_high_risk") or []
    return others[0] if others else None


def decide_closure(case: dict[str, Any]) -> str:
    """Diagnose-first closure state. High-risk overrides; warnings never headline."""
    if _is_high_risk(case):
        return CLOSURE_REVIEW
    evidence = case.get("evidence") or {}
    if evidence.get("min_anchor_set_met") and not (evidence.get("missing_evidence") or []):
        return CLOSURE_POSITIVE
    return CLOSURE_MISSING


def _normalize_missing(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Structure each missing-evidence item so actionable vs generic is visible.

    An item is actionable iff it names a concrete target to obtain (named_target),
    e.g. "lateral cephalogram SNA/SNB quantification". A bare string with no named
    target (e.g. "建议进一步检查") is surfaced as actionable=False so DW #144 Layer-1
    can fail generic gaps without reading prose.
    """
    items: list[dict[str, Any]] = []
    for raw in evidence.get("missing_evidence") or []:
        if isinstance(raw, dict):
            named = raw.get("named_target") or ""
            items.append({
                "named_target": named,
                "why_needed": raw.get("why_needed", ""),
                "actionable": bool(named),
            })
        else:
            items.append({"named_target": "", "why_needed": str(raw), "actionable": False})
    return items


def _layer1_checklist(report: dict[str, Any]) -> dict[str, Any]:
    """Derived machine-checkable yes/no view of DW #144 Layer-1 dimensions.

    Lets a non-orthodontist (KBadvisor/PM) gate structure without clinical judgment.
    Whether each direction is *correct* is Layer 2 (fact-match vs Walter, unseen
    cases) — out of scope here.
    """
    missing = report["actionable_missing_evidence"]
    return {
        "has_main_diagnosis_direction": bool(report["main_diagnosis_direction"]),
        "has_skeletal_source": bool(report["skeletal_source"]),
        "has_subtype_or_named_measurement_gap": bool(report["subtype_within_evidence"]),
        "has_treatment_direction": bool(report["treatment_direction"]),
        "uncertainty_has_reason_and_candidate": bool(
            report["uncertainty"] and report["uncertainty"].get("reason")
            and report["uncertainty"].get("candidates")
        ),
        "missing_evidence_actionable_not_generic": (
            all(m["actionable"] for m in missing) if missing else None
        ),
        "card_injection_visible": "card_injected" in report,
    }


def _stub_reasoner(case: dict[str, Any], card_context: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic offline stand-in for the SeniorClinician reasoning stage.

    Returns the case's candidate Layer-1 fields verbatim and records whether card
    context was consulted. It does NOT alter the diagnosis based on cards — a real
    SC LLM does that, and only when run on unseen cases. Keeping the stub neutral
    prevents a fabricated baseline-vs-card accuracy delta.
    """
    candidate = dict(case.get("candidate") or {})
    candidate["card_context_consulted"] = [c["card_id"] for c in card_context]
    return candidate


def run_case(
    case: dict[str, Any],
    mode: str,
    reasoner: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] = _stub_reasoner,
) -> dict[str, Any]:
    """Run one case in one mode; return a positive diagnostic report.

    Report fields map to DentistWang's #144 Layer-1 structure gate:
    main_diagnosis_direction / skeletal_source / subtype_within_evidence /
    treatment_direction / uncertainty.
    """
    if mode not in (MODE_BASELINE, MODE_CARD_CONTEXT):
        raise ValueError(f"unknown mode {mode!r}")

    gate_result = case.get("gate_result", "unresolved_not_closeable_needs_review")
    card_context = retrieve_card_context(gate_result) if mode == MODE_CARD_CONTEXT else []

    reasoned = reasoner(case, card_context)
    closure = decide_closure(case)
    evidence = case.get("evidence") or {}
    high_risk_reason = _is_high_risk(case)

    report: dict[str, Any] = {
        "case_id": case.get("case_id"),
        "mode": mode,
        "gate_result": gate_result,
        "closure_state": closure,
        # DW #144 Layer-1 dimensions — each a separate locatable field, not prose.
        "main_diagnosis_direction": None,
        "skeletal_source": None,
        "subtype_within_evidence": None,
        "treatment_direction": None,
        "reasoning_basis": [],
        "uncertainty": None,  # {item, reason, candidates:[...]} when applicable
        "actionable_missing_evidence": [],  # [{named_target, why_needed, actionable}]
        "review_required_reason": None,
        "warnings_floor": list(case.get("safety_floor") or []),
        # Negative-control visibility: did this case get card context?
        "card_injected": bool(card_context),
        "card_context_card_ids": [c["card_id"] for c in card_context],
    }

    if closure == CLOSURE_POSITIVE:
        report["main_diagnosis_direction"] = reasoned.get("main_diagnosis_direction")
        report["skeletal_source"] = reasoned.get("skeletal_source")
        report["subtype_within_evidence"] = reasoned.get("subtype_within_evidence")
        report["treatment_direction"] = reasoned.get("treatment_direction")
        report["reasoning_basis"] = list(evidence.get("support_anchors") or [])
        report["uncertainty"] = reasoned.get("uncertainty")
    elif closure == CLOSURE_MISSING:
        # Diagnose-first: name what to obtain, do not stop at a warning. The subtype
        # field carries the named measurement gap so its dimension still resolves.
        report["main_diagnosis_direction"] = reasoned.get("main_diagnosis_direction")
        report["subtype_within_evidence"] = reasoned.get("subtype_within_evidence")
        report["actionable_missing_evidence"] = _normalize_missing(evidence)
        report["uncertainty"] = reasoned.get("uncertainty")
    else:  # CLOSURE_REVIEW
        # Still give direction + differential, then refer to human (counts as a
        # qualified output under DW #144).
        report["main_diagnosis_direction"] = reasoned.get("main_diagnosis_direction")
        report["skeletal_source"] = reasoned.get("skeletal_source")
        report["subtype_within_evidence"] = reasoned.get("subtype_within_evidence")
        report["treatment_direction"] = reasoned.get("treatment_direction")
        report["reasoning_basis"] = list(evidence.get("support_anchors") or [])
        report["review_required_reason"] = high_risk_reason
        report["uncertainty"] = reasoned.get("uncertainty")

    report["layer1_checklist"] = _layer1_checklist(report)
    return report


def ab_run(
    case: dict[str, Any],
    reasoner: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] = _stub_reasoner,
) -> dict[str, Any]:
    """Run both modes for one case and surface the card-context delta."""
    baseline = run_case(case, MODE_BASELINE, reasoner)
    card_context = run_case(case, MODE_CARD_CONTEXT, reasoner)
    return {
        "case_id": case.get("case_id"),
        "gate_result": case.get("gate_result"),
        "baseline": baseline,
        "card_context": card_context,
        "card_ids_fired": card_context["card_context_card_ids"],
        "closure_changed": baseline["closure_state"] != card_context["closure_state"],
    }
