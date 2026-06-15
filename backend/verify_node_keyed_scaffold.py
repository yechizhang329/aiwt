"""Verify the §3 node-keyed scaffold primitives (task #140 / spec §3).

Offline. Run: python3 backend/verify_node_keyed_scaffold.py

Proves the node-keyed serving primitives that target E4 (no-structure free reasoning),
E5 (discriminator used at wrong level), E9 (reverse retrieval = confirmation ammo):
- decision_chain_node serves ONLY that node's discriminator + its forbidden features
  (no whole-tree / no other-node ammo);
- the 齿槽-vs-颌位 node forbids the shared lip/U1 inclination, allowing only molar;
- feature permission tiers (veto / decision_axis / support) are correct;
- reverse_confusable_index maps a misleading surface to the FALSIFICATION entry,
  never to a confirmation knowledge pack.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from decision_chain_scaffold import (  # noqa: E402
    FEATURE_PERMISSION_TIERS,
    decision_chain_node,
    node_keyed_scaffold,
    reverse_confusable_index,
)


def _check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


def main() -> None:
    print("§3 node-keyed scaffold primitives")

    n2 = decision_chain_node("node_2")
    _check("node_2 discriminator = molar_relation (decision_axis)",
           n2["discriminator"] == "molar_relation" and n2["discriminator_tier"] == "decision_axis")
    _check("node_2 forbids shared lip/U1 inclination (E5)",
           "lip_inclination" in n2["forbidden_features"] and "u1_axial" in n2["forbidden_features"])
    _check("node serving carries no whole-tree / no other-node ammo (E4)",
           set(n2) == {"node", "discriminator", "discriminator_tier", "forbidden_features", "usage_boundary"})
    _check("node_3 discriminator = u1_axial", decision_chain_node("node_3")["discriminator"] == "u1_axial")

    _check("veto tier: lower_lip + crossbite",
           FEATURE_PERMISSION_TIERS["lower_lip_vs_upper_lip"] == "veto"
           and FEATURE_PERMISSION_TIERS["crossbite"] == "veto")
    _check("support-only: lip_inclination/overjet have no structural path",
           FEATURE_PERMISSION_TIERS["lip_inclination"] == "support"
           and FEATURE_PERMISSION_TIERS["overjet"] == "support")

    r = reverse_confusable_index("surface_convex_bimax_protrusion")
    _check("reverse index serves falsification entry, not a confirmation pack (E9)",
           r and "confusable pair" in r["serve_falsification"] and "confirmation" in r["do_not_serve"])
    _check("falsification reads binary discriminators before gestalt",
           "lower_lip" in r["serve_falsification"] and "before gestalt" in r["serve_falsification"])
    _check("unknown surface appearance -> None", reverse_confusable_index("nope") is None)

    try:
        decision_chain_node("node_X")
        _check("unknown node raises", False)
    except KeyError:
        _check("unknown node raises KeyError", True)

    # node_keyed_scaffold — the served §3 slice (what Stage C gets)
    sc = node_keyed_scaffold("maxillary_origin_masked_concave_review_required")
    _check("served slice: per-node, not whole-pack (usage_boundary)",
           "not whole-pack" in sc["usage_boundary"])
    _check("served slice: has all 3 nodes, each with own discriminator+forbidden",
           set(sc["nodes"]) == {"node_1", "node_2", "node_3"}
           and all("forbidden_features" in n for n in sc["nodes"].values()))
    _check("served slice: NO flat whole-tree dict (decision_tree absent)", "decision_tree" not in sc)
    _check("served slice: permission tiers included (E10/§3.2)", "feature_permission_tiers" in sc)
    _check("served slice: surface-convex gate -> reverse falsification entry (E9)",
           sc["reverse_confusable"] is not None and "confusable pair" in sc["reverse_confusable"]["serve_falsification"])
    _check("served slice: binary-first veto + reading order + pending subtype carried",
           "binary_first_veto" in sc and "reading_order" in sc and "pending_walter" in sc["subtype"])
    sc_none = node_keyed_scaffold(None)
    _check("served slice: non-surface-convex/unknown gate -> no reverse entry",
           sc_none["reverse_confusable"] is None)

    print("§3 node-keyed scaffold checks: OK")


if __name__ == "__main__":
    main()
