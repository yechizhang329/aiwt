"""File-backed reasoning guard registry and safe projections."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GUARD_DIR = REPO_ROOT / "gbrain_cards" / "reasoning_guards"
DEFAULT_REGISTRY_PATH = DEFAULT_GUARD_DIR / "registry.yaml"

PROJECTION_DEFAULT = "default"
PROJECTION_AUDIT = "audit"
PROJECTION_FULL = "full"
PROJECTION_MODES = {PROJECTION_DEFAULT, PROJECTION_AUDIT, PROJECTION_FULL}

DEFAULT_FORBIDDEN_TOP_LEVEL = {"source_provenance", "raw_source_text", "source_paths"}


class ReasoningGuardError(ValueError):
    """Raised when the guard registry or projection shape is invalid."""


def load_reasoning_guard_registry(registry_path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, Any]:
    data = _read_yaml(registry_path)
    guards = data.get("guards")
    if not isinstance(guards, list) or not guards:
        raise ReasoningGuardError(f"{registry_path}: expected non-empty guards list")
    for entry in guards:
        _validate_registry_entry(entry, registry_path)
    return data


def get_reasoning_guard(
    guard_id: str,
    requested_projection: str = PROJECTION_DEFAULT,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> dict[str, Any]:
    for entry in load_reasoning_guard_registry(registry_path)["guards"]:
        if entry["guard_id"] == guard_id:
            return project_reasoning_guard(_load_guard(entry, registry_path), requested_projection)
    raise ReasoningGuardError(f"guard_id not found: {guard_id}")


def retrieve_reasoning_guards(
    tags: Optional[Iterable[str]] = None,
    guard_id: Optional[str] = None,
    requested_projection: str = PROJECTION_DEFAULT,
    max_guards: int = 3,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> list[dict[str, Any]]:
    if max_guards < 1:
        raise ReasoningGuardError("max_guards must be >= 1")
    query_tags = {str(tag) for tag in tags or []}
    matches: list[tuple[int, dict[str, Any]]] = []
    for entry in load_reasoning_guard_registry(registry_path)["guards"]:
        if guard_id and entry["guard_id"] != guard_id:
            continue
        entry_tags = {str(tag) for tag in entry.get("retrieval_tags") or []}
        score = len(query_tags & entry_tags)
        if query_tags and score == 0:
            continue
        if not query_tags and not guard_id:
            continue
        matches.append((score or 1, entry))
    matches.sort(key=lambda item: (-item[0], item[1]["guard_id"]))
    return [
        project_reasoning_guard(_load_guard(entry, registry_path), requested_projection)
        for _, entry in matches[:max_guards]
    ]


def project_reasoning_guard(guard: dict[str, Any], requested_projection: str = PROJECTION_DEFAULT) -> dict[str, Any]:
    if requested_projection not in PROJECTION_MODES:
        raise ReasoningGuardError(
            f"unknown projection {requested_projection!r}; expected one of {sorted(PROJECTION_MODES)}"
        )
    _validate_guard(guard)
    if requested_projection == PROJECTION_FULL:
        return deepcopy(guard)

    projection = {
        "guard_id": guard["guard_id"],
        "guard_type": guard["guard_type"],
        "title": guard["title"],
        "status": guard["status"],
        "unit_type": guard["unit_type"],
        "runtime_roles": deepcopy(guard["runtime_roles"]),
        "source_status": guard["source_status"],
        "source_sensitivity": guard["source_sensitivity"],
        "projection_policy": deepcopy(guard["projection_policy"]),
        "allowed_runtime_entry": deepcopy(guard["allowed_runtime_entry"]),
        "forbidden_runtime_entry": deepcopy(guard["forbidden_runtime_entry"]),
        "may_enter_final_conclusion": guard["may_enter_final_conclusion"],
        "may_support_finalization": guard["may_support_finalization"],
        "measurement_confidence_hierarchy": deepcopy(guard["measurement_confidence_hierarchy"]),
        "strict_quant_metrics": deepcopy(guard["strict_quant_metrics"]),
        "required_current_case_metadata": deepcopy(guard["required_current_case_metadata"]),
        "hard_fail_flags": deepcopy(guard["hard_fail_flags"]),
        "context_filter": deepcopy(guard["context_filter"]),
    }
    if requested_projection == PROJECTION_AUDIT:
        projection["fixture_plan"] = deepcopy(guard.get("fixture_plan", []))
        projection["source_provenance"] = deepcopy(guard.get("source_provenance", {}))
    _validate_default_projection(projection)
    return projection


def _validate_registry_entry(entry: Any, registry_path: Path) -> None:
    if not isinstance(entry, dict):
        raise ReasoningGuardError(f"{registry_path}: registry entries must be objects")
    for key in ("guard_id", "guard_type", "status", "path", "retrieval_tags"):
        if key not in entry:
            raise ReasoningGuardError(f"{registry_path}: registry entry missing {key!r}")
    if entry["guard_type"] != "finalization_boundary_guard":
        raise ReasoningGuardError(f"{entry['guard_id']}: unsupported guard_type")
    if not isinstance(entry["retrieval_tags"], list) or not entry["retrieval_tags"]:
        raise ReasoningGuardError(f"{entry['guard_id']}: retrieval_tags must be non-empty")


def _load_guard(entry: dict[str, Any], registry_path: Path) -> dict[str, Any]:
    path = Path(entry["path"])
    if not path.is_absolute():
        path = registry_path.parent / path
    guard = _read_yaml(path)
    if guard.get("guard_id") != entry["guard_id"]:
        raise ReasoningGuardError(
            f"{path}: guard_id {guard.get('guard_id')!r} does not match registry {entry['guard_id']!r}"
        )
    guard_tags = set((guard.get("context_filter") or {}).get("retrieval_tags") or [])
    missing_tags = sorted(set(entry.get("retrieval_tags") or []) - guard_tags)
    if missing_tags:
        raise ReasoningGuardError(f"{entry['guard_id']}: registry tags missing from guard: {missing_tags}")
    _validate_guard(guard)
    return guard


def _validate_guard(guard: dict[str, Any]) -> None:
    for key in (
        "guard_type",
        "schema_version",
        "guard_id",
        "title",
        "status",
        "unit_type",
        "runtime_roles",
        "source_status",
        "source_sensitivity",
        "projection_policy",
        "allowed_runtime_entry",
        "forbidden_runtime_entry",
        "may_enter_final_conclusion",
        "may_support_finalization",
        "measurement_confidence_hierarchy",
        "strict_quant_metrics",
        "required_current_case_metadata",
        "hard_fail_flags",
        "context_filter",
    ):
        if key not in guard:
            raise ReasoningGuardError(f"{guard.get('guard_id', '<unknown>')}: missing {key!r}")
    if guard["guard_type"] != "finalization_boundary_guard":
        raise ReasoningGuardError(f"{guard['guard_id']}: unsupported guard_type")
    if "finalization_boundary_guard" not in guard["runtime_roles"]:
        raise ReasoningGuardError(f"{guard['guard_id']}: missing finalization_boundary_guard role")
    if guard["may_enter_final_conclusion"] is not False:
        raise ReasoningGuardError(f"{guard['guard_id']}: guard itself may not enter final conclusion")


def _validate_default_projection(projection: dict[str, Any]) -> None:
    if projection.get("source_provenance") and projection.get("guard_id"):
        return
    leaked = sorted(DEFAULT_FORBIDDEN_TOP_LEVEL & set(projection))
    if leaked:
        raise ReasoningGuardError(f"default projection leaked provenance fields: {leaked}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ReasoningGuardError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ReasoningGuardError(f"{path}: expected YAML object")
    return data
