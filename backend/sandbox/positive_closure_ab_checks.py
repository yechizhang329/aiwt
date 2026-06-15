"""Offline demo + assertion checks for the positive-closure A/B harness.

Standalone, offline, file-backed (reuses the real card registry). Run with
`python3 backend/sandbox/positive_closure_ab_checks.py`. Proves the harness
mechanics on SYNTHETIC fixtures only — no accuracy/判对率 number is produced or
implied (the card-authoring cards cannot grade themselves; real A/B needs
Walter-reviewed, system-unseen cases — a separate human decision).

Covers, per 太上 #143 and DW #144:
- all three closure branches: positive_diagnosis / missing_evidence / review_required
- card fires only on matching gate_result; true_convex_closed = clean negative control
- each Layer-1 dimension is a separate locatable field (layer1_checklist machine-checkable)
- actionable vs generic missing-evidence is distinguishable
- "this case had no card injected" is visible (card_injected flag)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from positive_closure_ab_harness import (  # noqa: E402
    CLOSURE_MISSING,
    CLOSURE_POSITIVE,
    CLOSURE_REVIEW,
    MODE_BASELINE,
    MODE_CARD_CONTEXT,
    ab_run,
    run_case,
)

# --- synthetic fixtures (NOT real patient data; no images, no biometrics) -------

CASE_MATCHING_POSITIVE = {
    "case_id": "synthetic_maxillary_masked_concave",
    "scene": "3",
    "gate_result": "maxillary_origin_masked_concave_review_required",
    "evidence": {
        "min_anchor_set_met": True,
        "support_anchors": [
            "posterior_crossbite=present_unilateral",
            "lip_ap_relation=lower_lip_ahead",
            "maxillary_support=deficient",
        ],
        "missing_evidence": [],
    },
    "risk": {},
    "candidate": {
        "main_diagnosis_direction": "false-protrusive surface, maxillary-source compensated concave",
        "skeletal_source": "maxillary_origin",
        "subtype_within_evidence": "transverse-deficiency-dominant (沈刚精确亚型 pending_walter)",
        "treatment_direction": "MSE + maxillary protraction branch, not ordinary retraction",
        "uncertainty": {
            "item": "precise 沈刚 subtype",
            "reason": "maxillary-vs-combined source ratio not yet quantified",
            "candidates": ["maxillary-source-dominant", "combined upper+lower source"],
        },
    },
}

CASE_INSUFFICIENT = {
    "case_id": "synthetic_insufficient_named_gap",
    "scene": "3",
    "gate_result": "maxillary_origin_masked_concave_review_required",
    "evidence": {
        "min_anchor_set_met": False,
        "support_anchors": ["lip_ap_relation=lower_lip_ahead"],
        "missing_evidence": [
            {
                "named_target": "lateral cephalogram SNA/SNB + Wits quantification",
                "why_needed": "separate maxillary-source from combined-source concave",
            }
        ],
    },
    "risk": {},
    "candidate": {
        "main_diagnosis_direction": "masked concave suspected, source not yet resolvable",
        "subtype_within_evidence": "pending: needs lateral ceph quantification",
        "uncertainty": {
            "item": "skeletal source",
            "reason": "no cephalometric quantification on file",
            "candidates": ["maxillary_origin", "combined_source"],
        },
    },
}

CASE_HIGH_RISK = {
    "case_id": "synthetic_tmd_active_review",
    "scene": "3",
    "gate_result": "frank_concave_classIII_review_required",
    "evidence": {
        "min_anchor_set_met": True,
        "support_anchors": ["anterior_crossbite=present", "molar_relation_sagittal=mesial"],
        "missing_evidence": [],
    },
    "risk": {"tmd_active": True},
    "candidate": {
        "main_diagnosis_direction": "skeletal Class III concave direction",
        "skeletal_source": "mandibular_excess_candidate",
        "subtype_within_evidence": "Class III concave, surgical-borderline",
        "treatment_direction": "stabilize TMD first; ortho-surgical vs camouflage as differential",
        "uncertainty": {
            "item": "definitive treatment branch",
            "reason": "active TMD progression must stabilize before a GS orthodontic decision",
            "candidates": ["ortho-surgical", "camouflage"],
        },
    },
}

CASE_NEGATIVE_CONTROL = {
    "case_id": "synthetic_true_convex_negative_control",
    "scene": "1",
    "gate_result": "true_convex_closed",
    "evidence": {
        "min_anchor_set_met": True,
        "support_anchors": ["overjet_depth=large_positive", "molar_relation_sagittal=distal"],
        "missing_evidence": [],
    },
    "risk": {},
    "candidate": {
        "main_diagnosis_direction": "true bimaxillary/convex protrusion",
        "skeletal_source": "dentoalveolar_protrusion",
        "subtype_within_evidence": "ordinary convex",
        "treatment_direction": "standard retraction",
        "uncertainty": {
            "item": "none material",
            "reason": "clean true-convex closure",
            "candidates": ["true_convex"],
        },
    },
}

CASE_GENERIC_GAP = {  # demonstrates a GENERIC missing-evidence gap failing Layer-1
    "case_id": "synthetic_generic_gap_should_fail_layer1",
    "scene": "3",
    "gate_result": "maxillary_origin_masked_concave_review_required",
    "evidence": {
        "min_anchor_set_met": False,
        "support_anchors": [],
        "missing_evidence": ["建议进一步检查"],  # bare string, no named target
    },
    "risk": {},
    "candidate": {"main_diagnosis_direction": "unclear"},
}


def _check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


def main() -> None:
    print("positive-closure A/B harness — offline synthetic checks")

    # 1. matching positive case -------------------------------------------------
    r = ab_run(CASE_MATCHING_POSITIVE)
    base, cc = r["baseline"], r["card_context"]
    _check("matching: closure=positive_diagnosis", cc["closure_state"] == CLOSURE_POSITIVE)
    _check("matching: card fires in card_context mode", cc["card_injected"] is True)
    _check("matching: ML29F among fired cards",
           "ml29f_surface_convex_maxillary_source_concave" in cc["card_context_card_ids"])
    _check("matching: baseline gets NO card", base["card_injected"] is False)
    _check("matching: closure identical across modes (no fabricated delta)",
           r["closure_changed"] is False)
    cl = cc["layer1_checklist"]
    _check("matching: Layer-1 main dx + source + subtype + treatment all present",
           cl["has_main_diagnosis_direction"] and cl["has_skeletal_source"]
           and cl["has_subtype_or_named_measurement_gap"] and cl["has_treatment_direction"])
    _check("matching: uncertainty has reason + candidate",
           cl["uncertainty_has_reason_and_candidate"] is True)

    # 2. insufficient evidence -> named actionable gap --------------------------
    r2 = run_case(CASE_INSUFFICIENT, MODE_CARD_CONTEXT)
    _check("insufficient: closure=missing_evidence", r2["closure_state"] == CLOSURE_MISSING)
    _check("insufficient: missing-evidence is actionable (named target)",
           r2["actionable_missing_evidence"][0]["actionable"] is True)
    _check("insufficient: subtype field carries the named measurement gap",
           bool(r2["subtype_within_evidence"]))
    _check("insufficient: Layer-1 marks gap actionable_not_generic=True",
           r2["layer1_checklist"]["missing_evidence_actionable_not_generic"] is True)

    # 3. high-risk -> review_required, still directional ------------------------
    r3 = run_case(CASE_HIGH_RISK, MODE_CARD_CONTEXT)
    _check("high-risk: closure=review_required", r3["closure_state"] == CLOSURE_REVIEW)
    _check("high-risk: reason=tmd_active_progression",
           r3["review_required_reason"] == "tmd_active_progression")
    _check("high-risk: still gives direction + treatment differential",
           bool(r3["main_diagnosis_direction"]) and bool(r3["treatment_direction"]))
    _check("high-risk: uncertainty gives reason + candidate differential",
           r3["layer1_checklist"]["uncertainty_has_reason_and_candidate"] is True)

    # 4. negative control -> zero cards in BOTH modes ---------------------------
    r4 = ab_run(CASE_NEGATIVE_CONTROL)
    _check("negative-control: card_context mode injects 0 cards",
           r4["card_context"]["card_injected"] is False)
    _check("negative-control: baseline mode injects 0 cards",
           r4["baseline"]["card_injected"] is False)
    _check("negative-control: card_ids_fired empty", r4["card_ids_fired"] == [])

    # 5. generic gap -> Layer-1 fails actionable distinction --------------------
    r5 = run_case(CASE_GENERIC_GAP, MODE_CARD_CONTEXT)
    _check("generic-gap: closure=missing_evidence", r5["closure_state"] == CLOSURE_MISSING)
    _check("generic-gap: marked NOT actionable (generic)",
           r5["actionable_missing_evidence"][0]["actionable"] is False)
    _check("generic-gap: Layer-1 actionable_not_generic=False",
           r5["layer1_checklist"]["missing_evidence_actionable_not_generic"] is False)

    print("positive-closure A/B harness checks: OK")


if __name__ == "__main__":
    main()
