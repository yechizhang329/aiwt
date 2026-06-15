#!/usr/bin/env python3
"""
Audit log writer + rotator + reader for agent cowork.

Phase 1 Week 2 M-B1. Per Tier 2 § 8 + phase1_agent_json_schema_v1.md § 5.

- Append-only JSONL: notes/audit/{YYYY}/{MM}/{DD}.jsonl
- 3-day retention (per Tier 1 § 8) — files older than 3 days deleted on `rotate()`
- Single entry per audit_log_write call
- Sensitive case data NOT in log (refs only)
- Detail levels: standard (default) / verbose (on-demand)

Usage:
    from audit_log import write_entry, query, rotate

    write_entry({
        "audit_id": "...",
        "trace_id": "...",
        "case_id": "...",
        "scene": "3_doctor",
        "phase": "B",
        "from": "@SlimOrchestrator",
        "to": "@Clinician",
        "msg_type": "clinician_synthesize_request",
        "msg_id": "...",
        "input_ref": "...",
        "output_ref": "...",
        "confidence": 0.78,
        "metadata": {"model": "opus", "retry_count": 0}
    })
"""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "notes/audit/agent_cowork"
RETENTION_DAYS = 3


def _today_path():
    now = datetime.now(timezone.utc)
    p = AUDIT_DIR / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


REQUIRED_FIELDS = {"audit_id", "trace_id", "phase", "from", "msg_type", "msg_id"}


def write_entry(entry):
    """Append entry to today's audit log file. Validates minimum required fields."""
    missing = REQUIRED_FIELDS - set(entry.keys())
    if missing:
        raise ValueError(f"audit entry missing required fields: {missing}")
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    entry.setdefault("metadata", {})
    entry["metadata"].setdefault("detail_level", "standard")
    path = _today_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def rotate(retention_days=RETENTION_DAYS, dry_run=False):
    """Delete audit log files older than retention_days. Returns list of deleted paths."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = []
    if not AUDIT_DIR.exists():
        return deleted
    for path in AUDIT_DIR.rglob("*.jsonl"):
        try:
            parts = path.relative_to(AUDIT_DIR).parts
            if len(parts) != 3:
                continue
            yyyy, mm, dd_jsonl = parts
            dd = dd_jsonl.replace(".jsonl", "")
            file_date = datetime(int(yyyy), int(mm), int(dd), tzinfo=timezone.utc)
            if file_date < cutoff:
                if not dry_run:
                    path.unlink()
                deleted.append(str(path))
        except (ValueError, OSError):
            continue
    return deleted


def query(case_id=None, trace_id=None, msg_type=None, phase=None, agent=None, since=None, limit=None):
    """Query audit entries by filters. Returns list of matching entries (full content)."""
    results = []
    if not AUDIT_DIR.exists():
        return results
    files = sorted(AUDIT_DIR.rglob("*.jsonl"))
    for path in files:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if case_id and e.get("case_id") != case_id:
                    continue
                if trace_id and e.get("trace_id") != trace_id:
                    continue
                if msg_type and e.get("msg_type") != msg_type:
                    continue
                if phase and e.get("phase") != phase:
                    continue
                if agent and not (e.get("from") == agent or e.get("to") == agent):
                    continue
                if since:
                    try:
                        if datetime.fromisoformat(e["ts"]) < since:
                            continue
                    except (KeyError, ValueError):
                        continue
                results.append(e)
                if limit and len(results) >= limit:
                    return results
    return results


def detect_anomalies(since=None):
    """Compute basic anomaly metrics for 太上老君 monitoring."""
    entries = query(since=since)
    if not entries:
        return {}
    critic_disagree_count = sum(1 for e in entries if e.get("msg_type") == "critic_review_response"
                                and (e.get("metadata", {}).get("overall_disagreement_count") or 0) > 0)
    critic_total = sum(1 for e in entries if e.get("msg_type") == "critic_review_response")
    wrapper_violations = sum(1 for e in entries if e.get("msg_type") == "wrapper_check_response"
                             and e.get("violations"))
    wrapper_total = sum(1 for e in entries if e.get("msg_type") == "wrapper_check_response")
    retry_total = sum(e.get("metadata", {}).get("retry_count", 0) for e in entries)
    escalations = sum(1 for e in entries if e.get("msg_type") == "error_escalation")
    latencies = [e.get("latency_ms") for e in entries if isinstance(e.get("latency_ms"), int)]
    latencies.sort()

    def pct(p):
        if not latencies:
            return None
        idx = max(0, min(len(latencies) - 1, int(len(latencies) * p / 100)))
        return latencies[idx]

    return {
        "total_entries": len(entries),
        "critic_disagreement_rate": (critic_disagree_count / critic_total) if critic_total else None,
        "wrapper_violation_rate": (wrapper_violations / wrapper_total) if wrapper_total else None,
        "total_retries": retry_total,
        "total_escalations": escalations,
        "latency_p50_ms": pct(50),
        "latency_p95_ms": pct(95),
        "latency_p99_ms": pct(99),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("self-test")

    p_query = sub.add_parser("query")
    p_query.add_argument("--case_id")
    p_query.add_argument("--trace_id")
    p_query.add_argument("--msg_type")
    p_query.add_argument("--phase")
    p_query.add_argument("--agent")
    p_query.add_argument("--limit", type=int)

    p_rot = sub.add_parser("rotate")
    p_rot.add_argument("--dry-run", action="store_true")

    p_anom = sub.add_parser("anomaly")

    args = parser.parse_args()

    if args.cmd == "self-test":
        import uuid
        entry = {
            "audit_id": str(uuid.uuid4()),
            "trace_id": str(uuid.uuid4()),
            "case_id": "case-test-1",
            "scene": "3_doctor",
            "phase": "B",
            "from": "@SlimOrchestrator",
            "to": "@Clinician",
            "msg_type": "clinician_synthesize_request",
            "msg_id": str(uuid.uuid4()),
            "input_ref": None,
            "output_ref": None,
            "confidence": 0.7,
            "latency_ms": 4200,
            "metadata": {"model": "opus", "retry_count": 0},
        }
        path = write_entry(entry)
        print(f"[self-test] wrote entry to {path}")
        results = query(case_id="case-test-1", limit=10)
        print(f"[self-test] query returned {len(results)} entries")
        assert len(results) >= 1
        rotate(dry_run=True)
        print("[self-test] rotate dry-run OK")
        metrics = detect_anomalies()
        print(f"[self-test] anomaly metrics: {metrics}")
        print("[self-test] all checks passed")

    elif args.cmd == "query":
        rs = query(case_id=args.case_id, trace_id=args.trace_id, msg_type=args.msg_type,
                   phase=args.phase, agent=args.agent, limit=args.limit)
        print(json.dumps(rs, ensure_ascii=False, indent=2))

    elif args.cmd == "rotate":
        deleted = rotate(dry_run=args.dry_run)
        print(f"{'[dry-run] would delete' if args.dry_run else 'deleted'} {len(deleted)} files")
        for p in deleted:
            print(f"  {p}")

    elif args.cmd == "anomaly":
        print(json.dumps(detect_anomalies(), ensure_ascii=False, indent=2))
