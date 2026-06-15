"""Smoke checks for the compiled protrusion decision-chain reasoner (task #147).

Offline, synthetic. Run: python3 backend/sandbox/protrusion_decision_reasoner_checks.py

Verifies the decision MACHINERY (not accuracy):
- the walker follows the tree and reaches each leaf on the right discriminator;
- the binary-first veto fires before gestalt and routes to source review (no clean-close);
- every walk step cites its anchor;
- a missing decision-axis anchor => skipped step => NO verdict (gap named, no guess);
- shared-feature-forbidden: dentoalveolar-vs-jaw-position is split by molar only;
- read-through molar masking re-reads apparent-neutral as distal;
- pending subtype (沈刚 = pending_walter) is preserved, never fabricated.
Plus end-to-end through the #143 harness seam.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from protrusion_decision_reasoner import (  # noqa: E402
    PENDING_SUBTYPE,
    SHARED_FEATURE_FORBIDDEN_AT_DA_VS_JP,
    reasoner,
    walk,
)


def _check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


def _anchor_cited(trace: list[dict], anchor: str) -> bool:
    return any(step["anchor"] == anchor for step in trace)


def main() -> None:
    print("protrusion decision-chain reasoner — smoke checks")

    # 1. dentoalveolar: surface convex, molar neutral -> dentoalveolar ----------
    r = walk({
        "lower_lip_vs_upper_lip": "not_ahead", "crossbite": "none", "molar_relation": "neutral",
        "maxillary_skeletal_protrusion": "absent", "u1_axial": "proclined", "overjet": "deep",
    })
    _check("dentoalveolar: verdict reached", r["verdict_reached"] is True)
    _check("dentoalveolar: direction is dentoalveolar", "dentoalveolar" in r["main_diagnosis_direction"])
    _check("dentoalveolar: split cited MOLAR (not lip/U1)", _anchor_cited(r["tree_walk"], "molar_relation"))
    _check("dentoalveolar: every step cites an anchor", all(s["anchor"] for s in r["tree_walk"]))

    # 2. jaw-position: same surface but molar distal --------------------------
    r2 = walk({
        "lower_lip_vs_upper_lip": "not_ahead", "crossbite": "none", "molar_relation": "distal",
        "maxillary_skeletal_protrusion": "absent", "u1_axial": "proclined",
    })
    _check("jaw-position: distal molar -> jaw-position", "jaw-position" in r2["main_diagnosis_direction"])

    # 3. combined-2: maxillary skeletal protrusion + U1 retroclined ------------
    r3 = walk({
        "lower_lip_vs_upper_lip": "not_ahead", "crossbite": "none", "molar_relation": "distal",
        "maxillary_skeletal_protrusion": "present", "u1_axial": "retroclined",
    })
    _check("combined-2: present + retroclined -> combined-2", "combined-2" in r3["main_diagnosis_direction"])
    _check("combined-2: split cited U1 axial", _anchor_cited(r3["tree_walk"], "u1_axial"))

    # 4. veto: lower lip ahead + crossbite + mesial -> source review ----------
    r4 = walk({
        "lower_lip_vs_upper_lip": "ahead", "crossbite": "posterior", "molar_relation": "mesial",
        "maxillary_skeletal_protrusion": "absent", "paranasal_support": "deficient",
    })
    _check("veto: fired (no clean-close)", bool(r4["veto_fired_on"]))
    _check("veto: BRAKE not arrival — no closed verdict", r4["verdict_reached"] is False)
    _check("veto: routed to concave/source REVIEW (not a concave verdict)",
           r4["routing"] == "concave_source_review_required" and r4["skeletal_source"] is None)
    _check("veto: maxillary-source is a review HINT only (ML29F territory)",
           r4["review_hint_source"] == "maxillary_origin")
    _check("veto: treatment deferred until concave confirmed by convergence",
           r4["treatment_direction"] is None)
    _check("veto: convergence tally exposed for downstream §0 weighting",
           len(r4["convergence"]["toward_concave"]) >= 1)
    _check("veto: lower-lip read came BEFORE tree (first trace step)",
           r4["tree_walk"][0]["anchor"] == "lower_lip_vs_upper_lip")

    # 5. missing decision-axis anchor -> NO verdict ---------------------------
    r5 = walk({
        "lower_lip_vs_upper_lip": "not_ahead", "crossbite": "none", "molar_relation": None,
        "maxillary_skeletal_protrusion": "absent",
    })
    _check("missing-anchor: NO verdict", r5["verdict_reached"] is False)
    _check("missing-anchor: names the missing molar anchor", "molar_relation" in r5["missing_decision_anchor"])
    _check("missing-anchor: did not guess a direction", "undetermined" in r5["main_diagnosis_direction"])
    _check("missing-anchor: skipped step recorded in trace",
           any("SKIPPED" in s["note"] for s in r5["tree_walk"]))

    # 6. shared-feature-forbidden: molar is the ONLY splitter -----------------
    _check("shared-feature-forbidden: lip/U1 inclination forbidden at DA-vs-JP node",
           {"lip_inclination", "u1_axial"}.issubset(SHARED_FEATURE_FORBIDDEN_AT_DA_VS_JP))

    # 7. read-through molar masking: apparent neutral -> distal ----------------
    r7 = walk({
        "lower_lip_vs_upper_lip": "not_ahead", "crossbite": "none", "molar_relation": "neutral",
        "panoramic_mesial_drift": True, "crowding": "present",
        "maxillary_skeletal_protrusion": "absent",
    })
    _check("masking: mesial-drift+crowding re-reads neutral as distal -> jaw-position",
           "jaw-position" in r7["main_diagnosis_direction"])
    _check("masking: trace cites the read-through anchor",
           _anchor_cited(r7["tree_walk"], "mandibular_5_7_mesial_drift+crowding"))

    # 8. pending subtype preserved everywhere (never fabricated) --------------
    for label, rr in (("dentoalveolar", r), ("combined-2", r3), ("veto", r4)):
        _check(f"pending: {label} keeps 沈刚 subtype pending_walter",
               rr["subtype_within_evidence"] == PENDING_SUBTYPE)

    # 9. end-to-end through #143 harness seam ---------------------------------
    from positive_closure_ab_harness import run_case  # noqa: E402
    case = {
        "case_id": "synthetic_e2e_dentoalveolar",
        "gate_result": "true_convex_closed",  # non-matching -> no card injected (negative control intact)
        "evidence": {"min_anchor_set_met": True, "support_anchors": ["molar_relation=neutral"], "missing_evidence": []},
        "risk": {},
        "candidate": {
            "lower_lip_vs_upper_lip": "not_ahead", "crossbite": "none", "molar_relation": "neutral",
            "maxillary_skeletal_protrusion": "absent", "u1_axial": "proclined",
        },
    }
    report = run_case(case, "card_context", reasoner=reasoner)
    _check("e2e: harness produced a positive_diagnosis report via real reasoner",
           report["closure_state"] == "positive_diagnosis" and "dentoalveolar" in report["main_diagnosis_direction"])
    _check("e2e: negative-control intact (true_convex -> no card injected)", report["card_injected"] is False)

    print("protrusion decision-chain reasoner checks: OK")


if __name__ == "__main__":
    main()
