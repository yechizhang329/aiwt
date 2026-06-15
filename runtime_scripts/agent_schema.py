#!/usr/bin/env python3
"""
Agent-to-Agent message envelope + payload validation library.

Phase 1 Week 2 M-B1. Per Tier 2 § 3 + phase1_agent_json_schema_v1.md + _schemas.json.

Validates envelope structure + msg_type-specific payload before send.
Reject malformed messages early (per Tier 1 § 4.4 准确度 > 效率).

Usage:
    from agent_schema import validate_message, build_envelope, ValidationError

    msg = build_envelope(
        from_="@SlimOrchestrator", to="@KC",
        type_="kc_retrieval_request", trace_id=trace_id, case_id=case_id,
        scene="3_doctor",
        payload={"query_hints": ["真凹假突", "17F"]}
    )
    validate_message(msg)  # raises ValidationError on invalid
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_PATH = ROOT / "notes/orthodontics/clinical_kb/_schemas.json"

_SCHEMAS_CACHE = None


class ValidationError(Exception):
    def __init__(self, msg, path=None):
        super().__init__(msg)
        self.path = path


def _load_schemas():
    global _SCHEMAS_CACHE
    if _SCHEMAS_CACHE is None:
        with open(SCHEMAS_PATH) as f:
            _SCHEMAS_CACHE = json.load(f)
    return _SCHEMAS_CACHE


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_AGENT_RE = re.compile(r"^@[A-Za-z0-9_一-鿿]+$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def _check_field(obj, key, spec, path):
    p = f"{path}.{key}" if path else key
    if key not in obj:
        raise ValidationError(f"missing required field: {p}", p)
    val = obj[key]
    types = spec.get("type")
    if isinstance(types, str):
        types = [types]
    if types:
        ok = False
        for t in types:
            if t == "null" and val is None:
                ok = True
            elif t == "string" and isinstance(val, str):
                ok = True
            elif t == "integer" and isinstance(val, int) and not isinstance(val, bool):
                ok = True
            elif t == "number" and isinstance(val, (int, float)) and not isinstance(val, bool):
                ok = True
            elif t == "boolean" and isinstance(val, bool):
                ok = True
            elif t == "object" and isinstance(val, dict):
                ok = True
            elif t == "array" and isinstance(val, list):
                ok = True
        if not ok:
            raise ValidationError(f"type mismatch at {p}: expected {types}, got {type(val).__name__}", p)
    if "enum" in spec and val not in spec["enum"]:
        raise ValidationError(f"enum violation at {p}: {val!r} not in {spec['enum']}", p)
    if "const" in spec and val != spec["const"]:
        raise ValidationError(f"const violation at {p}: expected {spec['const']!r}, got {val!r}", p)
    if spec.get("format") == "uuid" and val is not None and not _UUID_RE.match(val):
        raise ValidationError(f"uuid format invalid at {p}: {val!r}", p)
    if spec.get("format") == "date-time" and val is not None and not _TS_RE.match(val):
        raise ValidationError(f"date-time format invalid at {p}: {val!r}", p)
    if "pattern" in spec and val is not None and not re.match(spec["pattern"], str(val)):
        raise ValidationError(f"pattern violation at {p}: {val!r}", p)
    if "minimum" in spec and val is not None and val < spec["minimum"]:
        raise ValidationError(f"minimum violation at {p}: {val} < {spec['minimum']}", p)
    if "maximum" in spec and val is not None and val > spec["maximum"]:
        raise ValidationError(f"maximum violation at {p}: {val} > {spec['maximum']}", p)


def _validate_against_schema(obj, schema, path=""):
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        if not isinstance(obj, dict):
            raise ValidationError(f"expected object at {path}, got {type(obj).__name__}", path)
        for req in schema.get("required", []):
            sub_spec = schema.get("properties", {}).get(req, {})
            _check_field(obj, req, sub_spec, path)
        for key, sub_spec in schema.get("properties", {}).items():
            if key in obj:
                sub_path = f"{path}.{key}" if path else key
                _check_field(obj, key, sub_spec, path)
                if obj[key] is not None:
                    if sub_spec.get("type") == "object":
                        _validate_against_schema(obj[key], sub_spec, sub_path)
                    elif sub_spec.get("type") == "array" and "items" in sub_spec:
                        if not isinstance(obj[key], list):
                            raise ValidationError(f"expected array at {sub_path}", sub_path)
                        for i, item in enumerate(obj[key]):
                            _validate_against_schema(item, sub_spec["items"], f"{sub_path}[{i}]")


def validate_message(msg):
    """Validate full envelope + msg_type-specific payload. Raises ValidationError on failure."""
    schemas = _load_schemas()
    _validate_against_schema(msg, schemas["envelope"])
    msg_type = msg["type"]
    payload_schema = schemas["payloads"].get(msg_type)
    if payload_schema is None:
        raise ValidationError(f"unknown msg_type: {msg_type}")
    _validate_against_schema(msg.get("payload", {}), payload_schema, path="payload")
    return True


def build_envelope(from_, to, type_, trace_id, payload, case_id=None, scene=None,
                   prior_msg_id=None, audit_id=None, msg_id=None, ts=None):
    """Build envelope dict. Auto-generates msg_id + audit_id + ts if not provided."""
    return {
        "v": "1",
        "msg_id": msg_id or str(uuid.uuid4()),
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "from": from_,
        "to": to,
        "case_id": case_id,
        "type": type_,
        "scene": scene,
        "trace_id": trace_id,
        "prior_msg_id": prior_msg_id,
        "payload": payload,
        "audit_id": audit_id or str(uuid.uuid4()),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
        try:
            validate_message(data)
            print(f"[ok] {sys.argv[1]} valid")
        except ValidationError as e:
            print(f"[fail] {e}")
            sys.exit(1)
    else:
        # Self-test
        trace_id = str(uuid.uuid4())
        case_id = str(uuid.uuid4())
        msg = build_envelope(
            from_="@SlimOrchestrator", to="@KC",
            type_="kc_retrieval_request", trace_id=trace_id, case_id=case_id,
            scene="3_doctor",
            payload={"query_hints": ["真凹假突", "17F"], "entity_filter": None, "kb_scope_subset": None},
        )
        validate_message(msg)
        print("[self-test] kc_retrieval_request envelope OK")

        # Negative test: missing required field
        bad_msg = dict(msg); del bad_msg["trace_id"]
        try:
            validate_message(bad_msg)
            print("[self-test FAIL] should have rejected missing trace_id")
            sys.exit(1)
        except ValidationError as e:
            print(f"[self-test] correctly rejected missing trace_id: {e}")

        # Negative test: invalid enum
        bad_msg2 = dict(msg); bad_msg2["scene"] = "2_unknown"
        try:
            validate_message(bad_msg2)
            print("[self-test FAIL] should have rejected invalid scene")
            sys.exit(1)
        except ValidationError as e:
            print(f"[self-test] correctly rejected invalid scene: {e}")

        print("[self-test] all checks passed")
