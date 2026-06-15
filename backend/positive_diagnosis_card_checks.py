"""Projection-contract checks for the positive-diagnosis card registry.

Standalone, file-backed, no service/orchestrator/gbrain.db dependency. Run with
`python3 backend/positive_diagnosis_card_checks.py` to confirm the 3-tier
projection contract holds against the real registry cards:

- every registry card loads and validates;
- default projection preserves forbidden_inference / minimum_positive_anchor_set
  / calibration_boundaries and leaks no on-demand field;
- provenance projection carries only source/privacy fields, no reasoning;
- audit projection is default + provenance + acceptance/safety.
"""

from __future__ import annotations

from positive_diagnosis_cards import (
    DEFAULT_FORBIDDEN_TOP_LEVEL,
    DEFAULT_REQUIRED_PATHS,
    PositiveDiagnosisCardError,
    _get_path,
    get_positive_diagnosis_card,
    load_registry,
)

_FSP19 = "fsp19_false_protrusive_compensated_concave"


def assert_all_cards_validate() -> list[str]:
    violations: list[str] = []
    for entry in load_registry()["cards"]:
        try:
            get_positive_diagnosis_card(entry["card_id"], "default")
        except PositiveDiagnosisCardError as exc:
            violations.append(f"{entry['card_id']}: {exc}")
    return violations


def assert_default_preserves_required(card_id: str = _FSP19) -> list[str]:
    projection = get_positive_diagnosis_card(card_id, "default")
    violations = [
        f"{card_id}: default projection missing required field {path}"
        for path in DEFAULT_REQUIRED_PATHS
        if _get_path(projection, path) in (None, [], {})
    ]
    leaked = sorted(DEFAULT_FORBIDDEN_TOP_LEVEL & set(projection))
    if leaked:
        violations.append(f"{card_id}: default projection leaked on-demand fields {leaked}")
    return violations


def assert_provenance_scope(card_id: str = _FSP19) -> list[str]:
    projection = get_positive_diagnosis_card(card_id, "provenance")
    expected = {"card_id", "title", "status", "source_provenance", "privacy_boundary"}
    extra = sorted(set(projection) - expected)
    return [f"{card_id}: provenance projection carries non-provenance fields {extra}"] if extra else []


def assert_audit_scope(card_id: str = _FSP19) -> list[str]:
    projection = get_positive_diagnosis_card(card_id, "audit")
    required = (
        "source_provenance",
        "privacy_boundary",
        "acceptance_cases",
        "safety_boundary",
        "forbidden_inference",
        "positive_reasoning_chain",
    )
    return [
        f"{card_id}: audit projection missing {field}"
        for field in required
        if field not in projection
    ]


def run_all_checks() -> None:
    for label, violations in (
        ("registry card validation", assert_all_cards_validate()),
        ("default projection contract", assert_default_preserves_required()),
        ("provenance projection scope", assert_provenance_scope()),
        ("audit projection scope", assert_audit_scope()),
    ):
        if violations:
            raise RuntimeError(f"{label} failed:\n" + "\n".join(violations))


if __name__ == "__main__":
    run_all_checks()
    print("positive-diagnosis card projection checks: OK")
