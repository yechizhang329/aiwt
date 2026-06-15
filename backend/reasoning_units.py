"""File-backed reasoning-unit registry and safe projections."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIT_DIR = REPO_ROOT / "gbrain_cards" / "reasoning_units"
DEFAULT_REGISTRY_PATH = DEFAULT_UNIT_DIR / "registry.yaml"

PROJECTION_DEFAULT = "default"
PROJECTION_AUDIT = "audit"
PROJECTION_FULL = "full"
PROJECTION_MODES = {PROJECTION_DEFAULT, PROJECTION_AUDIT, PROJECTION_FULL}

DEFAULT_FORBIDDEN_TOP_LEVEL = {"source_provenance", "source_files", "raw_source_text", "fixture_ids"}


class ReasoningUnitError(ValueError):
    """Raised when the reasoning-unit registry or projection shape is invalid."""


def load_reasoning_unit_registry(registry_path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    data = _read_yaml(registry_path)
    units = data.get("units")
    if not isinstance(units, list) or not units:
        raise ReasoningUnitError(f"{registry_path}: expected non-empty units list")
    for entry in units:
        _validate_registry_entry(entry, registry_path)
    return data


def get_reasoning_unit(
    unit_id: str,
    requested_projection: str = PROJECTION_DEFAULT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    for entry in load_reasoning_unit_registry(registry_path)["units"]:
        if entry["unit_id"] == unit_id:
            return project_reasoning_unit(_load_unit(entry, registry_path), requested_projection)
    raise ReasoningUnitError(f"unit_id not found: {unit_id}")


def retrieve_reasoning_units(
    tags: Optional[Iterable[str]] = None,
    unit_id: Optional[str] = None,
    requested_projection: str = PROJECTION_DEFAULT,
    max_units: int = 3,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> list[dict[str, Any]]:
    if max_units < 1:
        raise ReasoningUnitError("max_units must be >= 1")
    query_tags = {str(tag) for tag in tags or []}
    matches: list[tuple[int, dict[str, Any]]] = []
    for entry in load_reasoning_unit_registry(registry_path)["units"]:
        if unit_id and entry["unit_id"] != unit_id:
            continue
        entry_tags = {str(tag) for tag in entry.get("retrieval_tags") or []}
        score = len(query_tags & entry_tags)
        if query_tags and score == 0:
            continue
        if not query_tags and not unit_id:
            continue
        matches.append((score or 1, entry))
    matches.sort(key=lambda item: (-item[0], item[1]["unit_id"]))
    return [
        project_reasoning_unit(_load_unit(entry, registry_path), requested_projection)
        for _, entry in matches[:max_units]
    ]


def project_reasoning_unit(unit: dict[str, Any], requested_projection: str = PROJECTION_DEFAULT) -> dict[str, Any]:
    if requested_projection not in PROJECTION_MODES:
        raise ReasoningUnitError(
            f"unknown projection {requested_projection!r}; expected one of {sorted(PROJECTION_MODES)}"
        )
    _validate_unit(unit)
    if requested_projection == PROJECTION_FULL:
        return deepcopy(unit)
    projection = {
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "title": unit["title"],
        "status": unit["status"],
        "runtime_roles": deepcopy(unit["runtime_roles"]),
        "source_status": unit["source_status"],
        "source_sensitivity": unit["source_sensitivity"],
        "projection_policy": deepcopy(unit["projection_policy"]),
        "allowed_runtime_entry": deepcopy(unit["allowed_runtime_entry"]),
        "forbidden_runtime_entry": deepcopy(unit["forbidden_runtime_entry"]),
        "may_enter_final_conclusion": unit["may_enter_final_conclusion"],
        "may_support_finalization": unit["may_support_finalization"],
        "classification": deepcopy(unit["classification"]),
        "required_evidence_domains": deepcopy(unit["required_evidence_domains"]),
        "measurement_gate_dependency": deepcopy(unit["measurement_gate_dependency"]),
        "treatment_boundary": deepcopy(unit["treatment_boundary"]),
        "context_filter": deepcopy(unit["context_filter"]),
    }
    if requested_projection == PROJECTION_AUDIT:
        projection["hard_fail_flags"] = deepcopy(unit["hard_fail_flags"])
        projection["source_provenance"] = deepcopy(unit.get("source_provenance", {}))
    _validate_default_projection(projection)
    return projection


def _validate_registry_entry(entry: Any, registry_path: Path) -> None:
    if not isinstance(entry, dict):
        raise ReasoningUnitError(f"{registry_path}: registry entries must be objects")
    for key in ("unit_id", "unit_type", "status", "path", "retrieval_tags"):
        if key not in entry:
            raise ReasoningUnitError(f"{registry_path}: registry entry missing {key!r}")
    if entry["unit_type"] != "reasoning_role_row":
        raise ReasoningUnitError(f"{entry['unit_id']}: unsupported unit_type")
    if not isinstance(entry["retrieval_tags"], list) or not entry["retrieval_tags"]:
        raise ReasoningUnitError(f"{entry['unit_id']}: retrieval_tags must be non-empty")


def _load_unit(entry: dict[str, Any], registry_path: Path) -> dict[str, Any]:
    path = Path(entry["path"])
    if not path.is_absolute():
        path = registry_path.parent / path
    unit = _read_yaml(path)
    if unit.get("unit_id") != entry["unit_id"]:
        raise ReasoningUnitError(
            f"{path}: unit_id {unit.get('unit_id')!r} does not match registry {entry['unit_id']!r}"
        )
    unit_tags = set((unit.get("context_filter") or {}).get("retrieval_tags") or [])
    missing_tags = sorted(set(entry.get("retrieval_tags") or []) - unit_tags)
    if missing_tags:
        raise ReasoningUnitError(f"{entry['unit_id']}: registry tags missing from unit: {missing_tags}")
    _validate_unit(unit)
    return unit


def _validate_unit(unit: dict[str, Any]) -> None:
    for key in (
        "unit_type",
        "schema_version",
        "unit_id",
        "title",
        "status",
        "runtime_roles",
        "source_status",
        "source_sensitivity",
        "projection_policy",
        "allowed_runtime_entry",
        "forbidden_runtime_entry",
        "may_enter_final_conclusion",
        "may_support_finalization",
        "classification",
        "required_evidence_domains",
        "measurement_gate_dependency",
        "treatment_boundary",
        "hard_fail_flags",
        "context_filter",
    ):
        if key not in unit:
            raise ReasoningUnitError(f"{unit.get('unit_id', '<unknown>')}: missing {key!r}")
    if unit["unit_type"] != "reasoning_role_row":
        raise ReasoningUnitError(f"{unit['unit_id']}: unsupported unit_type")
    if unit["may_enter_final_conclusion"] is not False:
        raise ReasoningUnitError(f"{unit['unit_id']}: reasoning unit itself may not enter final conclusion")


def _validate_default_projection(projection: dict[str, Any]) -> None:
    if projection.get("source_provenance") and projection.get("unit_id"):
        return
    leaked = sorted(DEFAULT_FORBIDDEN_TOP_LEVEL & set(projection))
    if leaked:
        raise ReasoningUnitError(f"default projection leaked provenance fields: {leaked}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReasoningUnitError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ReasoningUnitError(f"{path}: expected YAML object")
    return data
