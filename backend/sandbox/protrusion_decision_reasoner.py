"""Compiled protrusion decision-chain reasoner for the offline sandbox (task #147).

OFFLINE ONLY. Plugs into the #143 harness `reasoner` seam (signature
(case, card_context) -> candidate dict). Encodes DentistWang's #145 batch-1
protrusion chain — three compiled artifacts at DW notes/:
  - kb_recompile_protrusion_decision_paradigm.md     (tree + reading order + shared-feature flags + meta-principle)
  - kb_recompile_confusable_pair_trueconvex_vs_falseprotrusive_concave.md  (binary-first veto discriminators)
  - kb_recompile_reading_anchor_extraction_checklist.md   (per-modality required anchors)

This is engineering-path validation of the decision MACHINERY, not an accuracy
claim (teach != test). The reasoner is deterministic and rule-based; it walks a
fixed tree, cites the anchor at every step, and — per DW — refuses a verdict when
a decision-axis anchor is missing (skipped step = no verdict, name the gap).

Reading order (paradigm §1):
    lateral_ceph (hypothesis) -> intraoral (confirm, canine/molar) -> panoramic -> facial -> cephalometric
Binary-first veto (confusable-pair §2; before gestalt). The veto is a BRAKE +
REDIRECT, not a verdict (DW): any of lower_lip_vs_upper_lip -> crossbite ->
molar_relation pointing concave FORBIDS a surface-convex clean-close and routes
to concave/source review. Landing on concave still needs §0 multi-indicator
convergence downstream — the veto never closes a concave diagnosis by itself.
Protrusion tree (paradigm §2):
    maxillary skeletal protrusion?
      no  -> dentoalveolar family: split by MOLAR (neutral=dentoalveolar / distal=jaw-position)
      yes -> combined family: split by U1 axial (upright=combined-1 / retroclined=combined-2)
Shared-feature-forbidden (paradigm §3): lip/U1 inclination MUST NOT split
dentoalveolar vs jaw-position — only molar relation may. Using a shared feature
at the wrong node is a level error (a top diagnostic-failure mode).
"""

from __future__ import annotations

from typing import Any, Optional

# Modality -> required anchors (reading-anchor checklist). 否决级/决策轴/支持.
READING_ORDER = ["lateral_ceph", "intraoral", "panoramic", "facial", "cephalometric"]

# Binary-first veto discriminators, in read order, with the value that points to
# false-protrusive compensated concave (confusable-pair §2).
VETO_DISCRIMINATORS = [
    ("lower_lip_vs_upper_lip", "ahead", "lateral_ceph"),   # 否决级 — Walter ML29F gold 957de2bc
    ("crossbite", ("anterior", "posterior"), "intraoral"),  # 任何反合 -> 强偏凹
    ("molar_relation", "mesial", "intraoral"),              # 近中 -> 凹方向
]

# Node -> the ONLY discriminator allowed to split it, plus features forbidden there.
DENTOALVEOLAR_VS_JAWPOSITION_DISCRIMINATOR = "molar_relation"
SHARED_FEATURE_FORBIDDEN_AT_DA_VS_JP = {"lip_inclination", "u1_axial", "lower_incisor_axial"}

PENDING_SUBTYPE = "沈刚精确亚型 = pending_walter"  # paradigm §5 / cards — never fabricate


def _cite(trace: list[dict], modality: str, anchor: str, value: Any, note: str) -> None:
    trace.append({"modality": modality, "anchor": anchor, "value": value, "note": note})


def _read_through_molar_masking(case: dict[str, Any], trace: list[dict]) -> Optional[str]:
    """Paradigm §4: panoramic 下颌5-7 mesial drift + crowding => apparent neutral molar is really distal."""
    molar = case.get("molar_relation")
    if case.get("panoramic_mesial_drift") and case.get("crowding") == "present" and molar == "neutral":
        _cite(trace, "panoramic", "mandibular_5_7_mesial_drift+crowding", True,
              "read-through molar masking: apparent neutral re-read as distal (paradigm §4)")
        return "distal"
    return molar


def _source_subrouting(case: dict[str, Any], trace: list[dict]) -> str:
    """After a concave veto, route source (confusable-pair §5). Direction-level only; subtype pending."""
    if case.get("paranasal_support") == "deficient" and case.get("crossbite") == "posterior":
        _cite(trace, "lateral_ceph/intraoral", "paranasal_support+posterior_crossbite", "deficient+present",
              "maxillary-source concave (ML29F territory)")
        return "maxillary_origin"
    _cite(trace, "lateral_ceph", "source_anchors_insufficient_for_maxillary_only", None,
          "upper+lower source / mandibular shallow-concave (FSP19 territory)")
    return "combined_or_mandibular_source"


def walk(case: dict[str, Any]) -> dict[str, Any]:
    """Walk the compiled protrusion decision tree; return candidate + trace + convergence."""
    trace: list[dict] = []
    convergence = {"toward_concave": [], "toward_convex": []}

    # ---- Step 1: binary-first veto reads (decisive, before gestalt) ----------
    veto_hits = []
    for anchor, concave_value, modality in VETO_DISCRIMINATORS:
        value = case.get(anchor)
        if value is None:
            continue  # missing veto anchor: not read, cannot fire (recorded as not-read)
        hit = value == concave_value if isinstance(concave_value, str) else value in concave_value
        _cite(trace, modality, anchor, value, "veto read" + (" -> concave" if hit else ""))
        if hit:
            veto_hits.append(anchor)
            convergence["toward_concave"].append(f"{anchor}={value}")
        elif anchor == "lower_lip_vs_upper_lip" and value == "not_ahead":
            convergence["toward_convex"].append(f"{anchor}={value}")

    if veto_hits:
        # Veto = BRAKE + REDIRECT, not arrival (DW clarification): it forbids a
        # premature surface-convex clean-close and routes to concave/source review.
        # It does NOT itself close a concave verdict — landing on concave still
        # requires §0 multi-indicator convergence downstream. Encoding it as
        # "one anchor -> concave verdict" would be single-detail adjudication.
        review_hint = _source_subrouting(case, trace)
        n = len(convergence["toward_concave"])
        return {
            "verdict_reached": False,
            "routing": "concave_source_review_required",
            "main_diagnosis_direction": (
                "surface-convex clean-close VETOED -> concave/source review required "
                "(NOT a closed concave verdict)"
            ),
            "skeletal_source": None,  # not decided here; only a review-direction hint below
            "review_hint_source": review_hint,
            "subtype_within_evidence": PENDING_SUBTYPE,
            "treatment_direction": None,  # deferred until concave is confirmed by convergence
            "uncertainty": {
                "item": "whether direction is truly concave + its source",
                "reason": (
                    f"veto braked premature convex closure on {n} concave-pointing anchor(s); "
                    "concave landing needs multi-indicator convergence (paradigm §0), not a single veto anchor"
                ),
                "candidates": [
                    "false_protrusive_compensated_concave (pending convergence)",
                    "ordinary protrusion (if convergence insufficient)",
                ],
            },
            "veto_fired_on": veto_hits,
            "tree_walk": trace,
            "convergence": convergence,
        }

    # ---- Step 2: surface convex confirmed -> protrusion tree -----------------
    # Node 1: maxillary skeletal protrusion? (decision axis: SNA/ANB)
    max_prot = case.get("maxillary_skeletal_protrusion")
    if max_prot is None:
        return _no_verdict(trace, convergence, "maxillary_skeletal_protrusion (SNA/SNB tracing)",
                           "lateral_ceph", "node-1 decision axis unreadable — cephalometric tracing required")
    _cite(trace, "lateral_ceph", "maxillary_skeletal_protrusion", max_prot, "tree node-1")

    if max_prot == "absent":
        # Dentoalveolar / jaw-position family. Split ONLY by molar (shared-feature-forbidden).
        molar = _read_through_molar_masking(case, trace)
        if molar is None:
            return _no_verdict(trace, convergence, "molar_relation (canine/molar class)",
                               "intraoral", "node-2 decision axis (molar) unreadable")
        _cite(trace, "intraoral", DENTOALVEOLAR_VS_JAWPOSITION_DISCRIMINATOR, molar,
              "tree node-2 (ONLY valid splitter; lip/U1 inclination forbidden here — paradigm §3)")
        if molar == "neutral":
            dx, treat = "dentoalveolar protrusion (上颌正常 + 上前牙唇倾 + 尖磨牙中性)", "alignment/retraction per force-direction extraction timing (paradigm §4)"
        else:  # distal
            dx, treat = "jaw-position protrusion (= dentoalveolar + 下颌位置后退/尖磨牙远中)", "consider mandibular advancement context; extraction timing per force direction"
        skeletal = "no_skeletal_maxillary_excess"
    else:  # present -> combined family
        u1 = case.get("u1_axial")
        if u1 is None:
            return _no_verdict(trace, convergence, "u1_axial (U1-SN)",
                               "lateral_ceph", "node-3 decision axis (U1 axial) unreadable")
        _cite(trace, "lateral_ceph", "u1_axial", u1, "tree node-3 (within mandibular-retrusion family)")
        if u1 == "upright":
            dx, treat = "combined-1 (上颌骨性前突 + 上前牙直立 + 下颌后退)", "skeletal anchorage retraction; surgical context if severe"
        else:  # retroclined
            dx, treat = "combined-2 (上颌骨性前突 + 上前牙内倾 + 下颌后退)", "decompensation then retraction; latent overjet on U1 uprighting (paradigm §4)"
        skeletal = "maxillary_skeletal_excess + mandibular_retrusion"

    # Multi-indicator convergence (meta-principle §0): record same-direction support; no single-detail lock.
    for feat in ("overjet", "u1_axial", "molar_relation"):
        v = case.get(feat)
        if v:
            convergence["toward_convex"].append(f"{feat}={v}")

    return {
        "verdict_reached": True,
        "main_diagnosis_direction": dx,
        "skeletal_source": skeletal,
        "subtype_within_evidence": PENDING_SUBTYPE,
        "treatment_direction": treat,
        "uncertainty": {
            "item": "subtype precision / progression staging",
            "reason": "subtype thresholds + jaw-retrusion confirmation pending (paradigm §5: CBCT for 下颌后退)",
            "candidates": [dx],
        },
        "veto_fired_on": [],
        "tree_walk": trace,
        "convergence": convergence,
    }


def _no_verdict(trace: list[dict], convergence: dict, named_anchor: str, modality: str, reason: str) -> dict:
    """Skipped decision-axis step => NO verdict; name the missing anchor, do not guess (DW)."""
    _cite(trace, modality, named_anchor, None, f"SKIPPED — {reason}; no verdict")
    return {
        "verdict_reached": False,
        "main_diagnosis_direction": "undetermined — decision-axis anchor missing",
        "skeletal_source": None,
        "subtype_within_evidence": f"pending: needs {named_anchor}",
        "treatment_direction": None,
        "uncertainty": {
            "item": "diagnosis direction/subtype",
            "reason": reason,
            "candidates": [],
        },
        "missing_decision_anchor": named_anchor,
        "tree_walk": trace,
        "convergence": convergence,
    }


def reasoner(case: dict[str, Any], card_context: list[dict[str, Any]]) -> dict[str, Any]:
    """Adapter for the #143 harness `reasoner` seam. Maps case anchors -> walk()."""
    anchors = case.get("anchors") or case.get("candidate") or case
    result = walk(anchors)
    result["card_context_consulted"] = [c.get("card_id") for c in card_context]
    return result
