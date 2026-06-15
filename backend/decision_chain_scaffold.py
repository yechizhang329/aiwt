"""Compiled decision-chain scaffold served as advisory reasoning context (task #140).

File-backed, no service/orchestrator/gbrain.db dependency. Returns the compiled
protrusion decision chain (DentistWang #145 batch-1 paradigm + the #147 sandbox
encoding) as ADVISORY DATA for the SeniorClinician — a reasoning scaffold, never an
auto-verdict. The hot path injects it into Stage C only behind the default-OFF flag
config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED (zero live-user effect until unified test
+ human review).

Targets E4 (no-structure free reasoning) / E5 (discriminator used at wrong level) /
E6 (single-detail verdict) per the v4 accuracy spec §3/§5. It is structure, not an
executable walker — the offline deterministic walker lives in
backend/sandbox/protrusion_decision_reasoner.py (#147).
"""

from __future__ import annotations

from typing import Any

# Source provenance for the scaffold (advisory; cites the compiled paradigm). The node_1/2/3
# framework axes are walter_reviewed (§0-4); the current full subtype structure is DW's
# 6-type tree v2 (supersedes the 4-type paradigm). NOTE (§5/§8): this serves DW clinical
# content, so default-OFF until the 突/凹 trees are walter_reviewed — not live before then.
_SOURCE = (
    "DW #145 kb_recompile_protrusion_6type_tree_v2 (current; supersedes 4-type "
    "kb_recompile_protrusion_decision_paradigm) + #147 sandbox encoding"
)

# Feature permission tiers (spec §3.2): veto / decision-axis / support-only. A
# "support-only" feature has no structural path straight to a conclusion.
FEATURE_PERMISSION_TIERS = {
    "lower_lip_vs_upper_lip": "veto",
    "crossbite": "veto",
    "molar_relation": "decision_axis",       # 齿槽 vs 颌位 decisive axis
    "maxillary_skeletal_protrusion": "decision_axis",
    "u1_axial": "decision_axis",              # only within mandibular-retrusion family
    "overjet": "support",
    "lip_inclination": "support",             # shared; never a node splitter
    "deep_spee": "support",
}

# Per-node served discriminator + the features FORBIDDEN at that node (spec §3.1/§3.3:
# serve only the node's own discriminator + forbidden marks — no whole-pack ammo so the
# model cannot 钻牛角尖 / use a shared feature at the wrong level).
_NODE_DISCRIMINATORS = {
    "node_1": {"discriminator": "maxillary_skeletal_protrusion", "forbidden": []},
    "node_2": {"discriminator": "molar_relation",
               "forbidden": ["lip_inclination", "u1_axial", "overjet"]},
    "node_3": {"discriminator": "u1_axial", "forbidden": ["lip_inclination"]},
}

# Reverse confusable-pair index (spec §3.3 / E9): a misleading SURFACE appearance maps
# to the FALSIFICATION entry (the confusable-pair veto discriminators) — NOT to a
# confirmation knowledge pack for that appearance.
_REVERSE_CONFUSABLE_INDEX = {
    "surface_convex_bimax_protrusion": {
        "do_not_serve": "ordinary-protrusion confirmation pack",
        "serve_falsification": "true-convex vs false-protrusive-concave confusable pair "
                               "(read lower_lip_vs_upper_lip -> crossbite -> molar before gestalt)",
        "card_hint": "false_protrusive / maxillary_source_concave cards",
    },
}


def decision_chain_node(node_id: str) -> dict[str, Any]:
    """Serve ONLY one decision node's discriminator + forbidden marks (§3 node-keyed)."""
    node = _NODE_DISCRIMINATORS.get(node_id)
    if node is None:
        raise KeyError(f"unknown decision node {node_id!r}")
    disc = node["discriminator"]
    return {
        "node": node_id,
        "discriminator": disc,
        "discriminator_tier": FEATURE_PERMISSION_TIERS.get(disc),
        "forbidden_features": list(node["forbidden"]),
        "usage_boundary": "node-keyed advisory; do not pull other nodes' ammo",
    }


def reverse_confusable_index(surface_appearance: str) -> dict[str, Any] | None:
    """Map a misleading surface appearance to its falsification entry (§3.3 / E9), or None."""
    return _REVERSE_CONFUSABLE_INDEX.get(surface_appearance)


# Gates whose surface presentation is "looks convex" → the reverse index should serve the
# false-protrusive/concave FALSIFICATION entry (E9), not an ordinary-protrusion pack.
_SURFACE_CONVEX_GATES = {
    "maxillary_origin_masked_concave_review_required",
    "frank_concave_classIII_review_required",
    "true_convex_closed",
    "unresolved_not_closeable_needs_review",
}


def node_keyed_scaffold(gate_result: str | None = None) -> dict[str, Any]:
    """§3 node-keyed serving (spec §3.1/§3.2/§3.3): a focused slice, not the whole pack.

    Instead of the flat full tree (decision_chain_scaffold inc-2), serve: the universal
    reading frame (meta-principle / reading order / binary-first veto / no-verdict-on-
    missing-anchor / pending subtype) + EACH decision node carrying only its OWN
    discriminator + forbidden marks (no cross-node ammo → anti-钻牛角尖 E4/E5) + the
    permission tiers (E10/§3.2) + the reverse falsification entry for a surface-convex
    presentation (E9). This is the per-node-structured serving step toward the full
    interactive per-node loop. Advisory; default-OFF in the hot path.
    """
    full = protrusion_decision_chain_scaffold()
    rev = reverse_confusable_index("surface_convex_bimax_protrusion") \
        if gate_result in _SURFACE_CONVEX_GATES else None
    return {
        "usage_boundary": "node-keyed advisory slice; per-node discriminators only, not whole-pack ammo",
        "source": full["source"],
        "gate_result": gate_result,
        "meta_principle": full["meta_principle"],
        "reading_order": full["reading_order"],
        "binary_first_veto": full["binary_first_veto"],
        "nodes": {nid: decision_chain_node(nid) for nid in ("node_1", "node_2", "node_3")},
        "feature_permission_tiers": FEATURE_PERMISSION_TIERS,
        "reverse_confusable": rev,
        "masking_rules": full["masking_rules"],
        "no_verdict_on_missing_anchor": full["no_verdict_on_missing_anchor"],
        "subtype": full["subtype"],
    }


def protrusion_decision_chain_scaffold() -> dict[str, Any]:
    """Advisory protrusion decision-chain structure for the SeniorClinician."""
    return {
        "usage_boundary": "advisory_reasoning_scaffold_not_auto_verdict",
        "source": _SOURCE,
        "meta_principle": (
            "multi-indicator convergence, not a necessary-condition checklist; no single "
            "detail decides (E6). Report which indicators agree and which do not."
        ),
        # Fixed reading order — build hypothesis on lateral ceph first, then confirm.
        "reading_order": [
            "lateral_ceph (build hypothesis)",
            "intraoral (confirm; canine/molar relation)",
            "panoramic",
            "facial",
            "cephalometric (lock/weight)",
        ],
        # Binary-first veto reads — discrete answers BEFORE gestalt. Veto = brake + redirect
        # (forbid premature surface-convex clean-close, route to concave/source review); it is
        # NOT a single-anchor concave verdict — landing still needs convergence.
        "binary_first_veto": {
            "order": ["lower_lip_vs_upper_lip", "crossbite", "molar_relation"],
            "concave_pointing": {
                "lower_lip_vs_upper_lip": "lower lip ahead of upper lip (Walter ML29F gold)",
                "crossbite": "any anterior or posterior crossbite",
                "molar_relation": "mesial",
            },
            "rule": "any concave-pointing -> brake surface-convex clean-close, route to concave/source review (not a verdict)",
        },
        # Protrusion tree with the ONE discriminator each node may use.
        "decision_tree": {
            "node_1": {"question": "maxillary skeletal protrusion? (SNA/ANB)",
                       "absent": "dentoalveolar/jaw-position family -> node_2",
                       "present": "combined family -> node_3"},
            "node_2": {"question": "molar relation (ONLY valid splitter here)",
                       "neutral": "dentoalveolar (上颌正常 + 上前牙唇倾 + 尖磨牙中性)",
                       "distal": "jaw-position (= dentoalveolar + 下颌后退/尖磨牙远中)"},
            "node_3": {"question": "U1 axial (within mandibular-retrusion family)",
                       "upright": "combined-1 (骨性前突 + U1 直立 + 下颌后退)",
                       "retroclined": "combined-2 (骨性前突 + U1 内倾 + 下颌后退)"},
        },
        # Shared-feature-forbidden (E5): a shared feature must NOT split a node it cannot.
        "shared_feature_forbidden": [
            {"feature": "lip/U1 inclination (唇倾/U1-SN)",
             "must_not_split": "dentoalveolar vs jaw-position (shared by both)",
             "only_valid_splitter": "molar relation"},
        ],
        # Masking rules (E2/E11): read through compensation traps.
        "masking_rules": [
            "panoramic 下颌5-7 mesial drift + crowding -> apparent neutral molar is really distal",
            "ANB/overjet pulled near-normal by dental compensation must NOT exclude skeletal/concave; read SNA, SNB separately to cranial base",
        ],
        # Missing decision-axis anchor -> no verdict; name the gap, do not guess (E3).
        "no_verdict_on_missing_anchor": (
            "if a decision-axis anchor is unreadable, skip that step and withhold the verdict; "
            "name the specific missing evidence (e.g. 'lateral ceph SNA/SNB tracing')"
        ),
        "subtype": "沈刚精确亚型 = pending_walter (do not fabricate)",
    }
