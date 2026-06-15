"""Verify task #140 positive-diagnosis advisory wiring: DEFAULT OFF + additive-only.

Offline. Run: python3 backend/verify_positive_diagnosis_advisory_wiring.py

Proves (spec §5/§8 frozen_boundary — zero live-user effect until unified test + human review):
- config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED defaults OFF (False);
- the Stage C helper returns gate-matched DEFAULT-projection cards for the masked gate,
  and None for true_convex / unresolved / missing gate (clean negative control);
- served advisory cards leak no forbidden top-level field (source/biometric stripped);
- OFF = no-op: the payload key is set in exactly ONE place, guarded by the flag.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND.parent))  # repo root, for the `artifacts` package
sys.path.insert(0, str(BACKEND))

import config  # noqa: E402
import positive_diagnosis_cards as p  # noqa: E402
from orchestrator.v2_orchestrator import _positive_diagnosis_card_context  # noqa: E402


def _check(label: str, cond: bool) -> None:
    if not cond:
        raise AssertionError(f"FAILED: {label}")
    print(f"  ok: {label}")


def _ir(gate):
    return {"sagittal_consensus_packet": {"gate_result": gate}}


def main() -> None:
    print("task #140 positive-diagnosis advisory wiring — verification")

    # 1. default OFF
    _check("flag POSITIVE_DIAGNOSIS_ADVISORY_ENABLED defaults OFF",
           config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED is False)

    # 2. helper content on a matching gate
    ctx = _positive_diagnosis_card_context(_ir("maxillary_origin_masked_concave_review_required"))
    _check("masked gate -> advisory context with cards", bool(ctx) and len(ctx["cards"]) >= 1)
    _check("masked gate -> ML29F card present",
           any(c["card_id"] == "ml29f_surface_convex_maxillary_source_concave" for c in ctx["cards"]))
    _check("usage_boundary marks advisory, not auto-verdict",
           "not_auto_verdict" in ctx["usage_boundary"])
    _check("DEFAULT projection only — no forbidden top-level leaked",
           not [k for c in ctx["cards"] for k in p.DEFAULT_FORBIDDEN_TOP_LEVEL if k in c])

    # 3. negative control — non-matching gates inject nothing
    for g in ("true_convex_closed", "unresolved_not_closeable_needs_review", None):
        _check(f"gate {g!r} -> None (negative control)",
               _positive_diagnosis_card_context(_ir(g)) is None)
    _check("empty IR packet -> None", _positive_diagnosis_card_context({}) is None)

    # 4. OFF = no-op (structural): the payload key is set in exactly one place, flag-guarded
    src = (BACKEND / "orchestrator" / "v2_orchestrator.py").read_text(encoding="utf-8")
    sites = [m.start() for m in re.finditer(r'payload\["positive_diagnosis_card_context"\]\s*=', src)]
    _check("exactly one assignment site for the advisory key", len(sites) == 1)
    guard = src.rfind("if config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED:", 0, sites[0])
    _check("assignment is guarded by the default-OFF flag (no-op when OFF)",
           guard != -1 and (sites[0] - guard) < 400)

    # 5. increment-2: decision-chain scaffold — advisory structure + flag-guarded
    from decision_chain_scaffold import protrusion_decision_chain_scaffold  # noqa: E402
    sc = protrusion_decision_chain_scaffold()
    _check("scaffold marks advisory, not auto-verdict", "not_auto_verdict" in sc["usage_boundary"])
    _check("scaffold has reading_order + binary_first_veto + decision_tree + shared_feature_forbidden",
           all(k in sc for k in ("reading_order", "binary_first_veto", "decision_tree", "shared_feature_forbidden")))
    _check("scaffold veto is brake-not-verdict", "not a verdict" in sc["binary_first_veto"]["rule"])
    _check("scaffold withholds verdict on missing anchor", "withhold" in sc["no_verdict_on_missing_anchor"])
    _check("scaffold keeps 沈刚 subtype pending_walter", "pending_walter" in sc["subtype"])
    sc_sites = [m.start() for m in re.finditer(r'payload\["decision_chain_scaffold"\]\s*=', src)]
    _check("exactly one scaffold assignment site", len(sc_sites) == 1)
    sc_guard = src.rfind("if config.POSITIVE_DIAGNOSIS_ADVISORY_ENABLED:", 0, sc_sites[0])
    _check("scaffold injection guarded by default-OFF flag (no-op when OFF)",
           sc_guard != -1 and (sc_sites[0] - sc_guard) < 800)

    print("task #140 advisory wiring checks: OK")


if __name__ == "__main__":
    main()
