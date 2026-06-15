"""File-backed positive diagnosis card registry and projection retrieval.

Phase 1 intentionally stays outside gbrain.db and the hot-path orchestrator.
The module loads curated YAML cards and returns explicit projections so default
working context stays diagnosis-first without source-thread/history noise.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CARD_DIR = REPO_ROOT / "gbrain_cards" / "positive_diagnosis"
DEFAULT_REGISTRY_PATH = DEFAULT_CARD_DIR / "registry.yaml"

PROJECTION_DEFAULT = "default"
PROJECTION_PROVENANCE = "provenance"
PROJECTION_AUDIT = "audit"
PROJECTION_FULL = "full"
PROJECTION_MODES = {
    PROJECTION_DEFAULT,
    PROJECTION_PROVENANCE,
    PROJECTION_AUDIT,
    PROJECTION_FULL,
}

DEFAULT_REQUIRED_PATHS = (
    "forbidden_inference",
    "source_attribution_logic.minimum_positive_anchor_set",
    "subtype_mapping.calibration_boundaries",
)

DEFAULT_FORBIDDEN_TOP_LEVEL = {
    "source_provenance",
    "privacy_boundary",
    "acceptance_cases",
    "safety_boundary",
}

# Canonical map: v4 sagittal-gate result (v2_orchestrator._v4_routing_intent) -> the
# case retrieval tags that fire matching positive-diagnosis cards. Only the masked /
# frank concave review gates retrieve cards; true_convex_closed and unresolved
# retrieve nothing — ordinary convex cases get NO card (clean negative control).
# Single source of truth: the offline sandbox harness imports this, not vice versa.
GATE_RESULT_RETRIEVAL_TAGS: dict[str, list[str]] = {
    "maxillary_origin_masked_concave_review_required": [
        "false_protrusive",
        "maxillary_source_concave",
        "transverse_deficiency",
        "compensated_class_III",
        "posterior_crossbite",
        "lower_lip_ahead_of_upper_lip",
    ],
    "frank_concave_classIII_review_required": [
        "false_protrusive",
        "concave",
        "compensated_class_III",
    ],
    "true_convex_closed": [],
    "unresolved_not_closeable_needs_review": [],
}


class PositiveDiagnosisCardError(ValueError):
    """Raised when the file-backed card registry or card shape is invalid."""


def load_registry(registry_path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    data = _read_yaml(registry_path)
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        raise PositiveDiagnosisCardError(f"{registry_path}: expected non-empty cards list")
    for entry in cards:
        _validate_registry_entry(entry, registry_path)
    return data


def get_positive_diagnosis_card(
    card_id: str,
    requested_projection: str = PROJECTION_DEFAULT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    for entry in load_registry(registry_path)["cards"]:
        if entry["card_id"] == card_id:
            card = _load_card(entry, registry_path)
            return project_positive_diagnosis_card(card, requested_projection)
    raise PositiveDiagnosisCardError(f"card_id not found: {card_id}")


def retrieve_positive_diagnosis_cards(
    tags: Optional[Iterable[str]] = None,
    card_id: Optional[str] = None,
    task_type: Optional[str] = None,
    requested_projection: str = PROJECTION_DEFAULT,
    max_cards: int = 3,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> list[dict[str, Any]]:
    """Retrieve matching cards by card_id or tag overlap.

    task_type is accepted for the planned query shape. Phase 1 does not filter
    by it because the reviewed registry does not define task-specific cards yet.
    """
    del task_type
    if max_cards < 1:
        raise PositiveDiagnosisCardError("max_cards must be >= 1")

    registry = load_registry(registry_path)
    query_tags = {str(tag) for tag in (tags or [])}
    matches: list[tuple[int, dict[str, Any]]] = []

    for entry in registry["cards"]:
        if card_id and entry["card_id"] != card_id:
            continue
        entry_tags = {str(tag) for tag in entry.get("retrieval_tags", [])}
        score = len(query_tags & entry_tags)
        if query_tags and score == 0:
            continue
        if not query_tags:
            score = 1
        matches.append((score, entry))

    matches.sort(key=lambda item: (-item[0], item[1]["card_id"]))
    return [
        project_positive_diagnosis_card(_load_card(entry, registry_path), requested_projection)
        for _, entry in matches[:max_cards]
    ]


def retrieve_cards_for_gate(
    gate_result: Optional[str],
    requested_projection: str = PROJECTION_DEFAULT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> list[dict[str, Any]]:
    """Cards matching a v4 sagittal-gate result; [] for non-matching gates.

    Fires only on the masked/frank concave review gates; true_convex_closed and
    unresolved return [] (ordinary convex cases get no advisory card).
    """
    tags = GATE_RESULT_RETRIEVAL_TAGS.get(gate_result or "", [])
    if not tags:
        return []
    return retrieve_positive_diagnosis_cards(
        tags=tags,
        requested_projection=requested_projection,
        registry_path=registry_path,
    )


def project_positive_diagnosis_card(
    card: dict[str, Any],
    requested_projection: str = PROJECTION_DEFAULT,
) -> dict[str, Any]:
    if requested_projection not in PROJECTION_MODES:
        raise PositiveDiagnosisCardError(
            f"unknown projection {requested_projection!r}; expected one of {sorted(PROJECTION_MODES)}"
        )
    _validate_card(card)

    if requested_projection == PROJECTION_FULL:
        return deepcopy(card)

    default_projection = _default_projection(card)
    if requested_projection == PROJECTION_DEFAULT:
        return default_projection

    provenance_projection = {
        "card_id": card["card_id"],
        "title": card["title"],
        "status": card["status"],
        "source_provenance": deepcopy(card.get("source_provenance", {})),
        "privacy_boundary": deepcopy(card.get("privacy_boundary", {})),
    }
    if requested_projection == PROJECTION_PROVENANCE:
        return provenance_projection

    audit_projection = deepcopy(default_projection)
    audit_projection.update(provenance_projection)
    audit_projection["acceptance_cases"] = deepcopy(card.get("acceptance_cases", {}))
    audit_projection["safety_boundary"] = deepcopy(card.get("safety_boundary", {}))
    audit_projection["context_filter"] = deepcopy(card.get("context_filter", {}))
    return audit_projection


def _default_projection(card: dict[str, Any]) -> dict[str, Any]:
    context_filter = card.get("context_filter") or {}
    projection = {
        "card_id": card["card_id"],
        "title": card["title"],
        "status": card["status"],
        "diagnostic_concept": deepcopy(card["diagnostic_concept"]),
        "positive_reasoning_chain": deepcopy(card["positive_reasoning_chain"]),
        "misleading_surface_cues": deepcopy(card["misleading_surface_cues"]),
        "forbidden_inference": deepcopy(card["forbidden_inference"]),
        "differential_contrast": deepcopy(card["differential_contrast"]),
        "source_attribution_logic": {
            "minimum_positive_anchor_set": deepcopy(
                card["source_attribution_logic"]["minimum_positive_anchor_set"]
            ),
        },
        "subtype_mapping": {
            "target_path": deepcopy(card["subtype_mapping"]["target_path"]),
            "calibration_boundaries": deepcopy(card["subtype_mapping"]["calibration_boundaries"]),
        },
        "treatment_implication": deepcopy(card["treatment_implication"]),
        "uncertainty_boundary": deepcopy(card["uncertainty_boundary"]),
        "context_filter": {
            "applies_when": deepcopy(context_filter.get("applies_when", [])),
            "does_not_apply_when": deepcopy(context_filter.get("does_not_apply_when", [])),
        },
    }
    _validate_default_projection(projection)
    return projection


def _validate_registry_entry(entry: Any, registry_path: Path) -> None:
    if not isinstance(entry, dict):
        raise PositiveDiagnosisCardError(f"{registry_path}: registry entries must be objects")
    for key in ("card_id", "card_type", "status", "path", "retrieval_tags"):
        if key not in entry:
            raise PositiveDiagnosisCardError(f"{registry_path}: registry entry missing {key!r}")
    if entry["card_type"] != "positive_diagnosis_card":
        raise PositiveDiagnosisCardError(f"{entry['card_id']}: unsupported card_type")
    if not isinstance(entry["retrieval_tags"], list) or not entry["retrieval_tags"]:
        raise PositiveDiagnosisCardError(f"{entry['card_id']}: retrieval_tags must be non-empty")


def _load_card(entry: dict[str, Any], registry_path: Path) -> dict[str, Any]:
    path = Path(entry["path"])
    if not path.is_absolute():
        path = registry_path.parent / path
    card = _read_yaml(path)
    if card.get("card_id") != entry["card_id"]:
        raise PositiveDiagnosisCardError(
            f"{path}: card_id {card.get('card_id')!r} does not match registry {entry['card_id']!r}"
        )
    card_tags = set((card.get("context_filter") or {}).get("retrieval_tags") or [])
    registry_tags = set(entry.get("retrieval_tags") or [])
    missing_tags = sorted(registry_tags - card_tags)
    if missing_tags:
        raise PositiveDiagnosisCardError(
            f"{entry['card_id']}: registry tags missing from card context_filter: {missing_tags}"
        )
    _validate_card(card)
    return card


def _validate_card(card: dict[str, Any]) -> None:
    for key in (
        "card_type",
        "schema_version",
        "card_id",
        "title",
        "status",
        "diagnostic_concept",
        "positive_reasoning_chain",
        "misleading_surface_cues",
        "forbidden_inference",
        "differential_contrast",
        "source_attribution_logic",
        "subtype_mapping",
        "treatment_implication",
        "uncertainty_boundary",
        "context_filter",
    ):
        if key not in card:
            raise PositiveDiagnosisCardError(f"{card.get('card_id', '<unknown>')}: missing {key!r}")
    if card["card_type"] != "positive_diagnosis_card":
        raise PositiveDiagnosisCardError(f"{card['card_id']}: unsupported card_type")
    for path in DEFAULT_REQUIRED_PATHS:
        if _get_path(card, path) in (None, [], {}):
            raise PositiveDiagnosisCardError(f"{card['card_id']}: missing required default field {path}")


def _validate_default_projection(projection: dict[str, Any]) -> None:
    leaked = sorted(DEFAULT_FORBIDDEN_TOP_LEVEL & set(projection))
    if leaked:
        raise PositiveDiagnosisCardError(f"default projection leaked on-demand fields: {leaked}")
    for path in DEFAULT_REQUIRED_PATHS:
        if _get_path(projection, path) in (None, [], {}):
            raise PositiveDiagnosisCardError(f"default projection missing required field {path}")


def _get_path(obj: dict[str, Any], dotted_path: str) -> Any:
    current: Any = obj
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PositiveDiagnosisCardError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise PositiveDiagnosisCardError(f"{path}: expected YAML object")
    return data
