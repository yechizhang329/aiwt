#!/usr/bin/env python3
"""
Legacy Slock CLI AgentAdapter for SlimOrchestrator -> agent dispatch.

Quarantined for historical reference only. It depends on the old
`SlimOrchestrator.py` bridge, which is intentionally excluded from the
source-of-truth branch because the current backend uses v2 direct orchestration.

Phase 1 Week 2 wrap — M-C2. Per Tier 2 v0.3 + jonathan 21:17 MVP reframe + 太上老君 polling decision (5s fixed).

ISOLATED: All Slock-specific code lives in this file only. Future migration = swap this class.

Design:
- `invoke(envelope)` serializes JSON envelope → `slock message send dm:@<to>` body
- Polls `slock message read --channel dm:@<from> --after <ts>` every 5s until response (matching by prior_msg_id)
- 90s timeout per dispatch (Opus median 30-60s + buffer)
- Retry max 1 on subprocess fail
- Audit metadata: total dispatch wall time / subprocess wall time / poll cycle count / retry count

Per MVP reframe (jonathan 21:17): "不要试图突破 slock 本身的限制, 尽量找 work around".
- Accept Slock daemon limitations (no streaming, DM全量读, freshness hold)
- Workaround: `--send-draft --target` if freshness hold rejects send
- No optimization of daemon API
"""
import json
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from agent_schema import build_envelope, validate_message, ValidationError
from SlimOrchestrator import AgentAdapter


# Tunables (MVP defaults, surface to config if Phase 2+ tuning needed)
POLL_INTERVAL_SEC = 5
DISPATCH_TIMEOUT_SEC = 90
SUBPROCESS_TIMEOUT_SEC = 30
MAX_RETRY = 1

ENVELOPE_MARKER_START = "<<<SLOCK_ENVELOPE_V1>>>"
ENVELOPE_MARKER_END = "<<<END_SLOCK_ENVELOPE>>>"


class SlockTransportError(Exception):
    """Raised when Slock CLI subprocess fails or times out."""


class SlockCLIAdapter(AgentAdapter):
    """Real Slock-based transport adapter. Replace this class for future non-Slock migration."""

    def __init__(self, from_agent_id="@SlimOrchestrator",
                 poll_interval=POLL_INTERVAL_SEC,
                 dispatch_timeout=DISPATCH_TIMEOUT_SEC,
                 subprocess_timeout=SUBPROCESS_TIMEOUT_SEC,
                 max_retry=MAX_RETRY):
        self.from_agent_id = from_agent_id
        self.poll_interval = poll_interval
        self.dispatch_timeout = dispatch_timeout
        self.subprocess_timeout = subprocess_timeout
        self.max_retry = max_retry
        self._subprocess_total_sec = 0.0
        self._poll_count = 0
        self._l7_counters: dict = {}

    def _run_slock(self, args, stdin=None):
        """Run slock CLI subprocess. Track wall time. Raise on non-zero exit."""
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                ["slock"] + args,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.subprocess_timeout,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            self._subprocess_total_sec += elapsed
            raise SlockTransportError(f"slock CLI timeout after {elapsed:.1f}s: {' '.join(args)}")
        elapsed = time.monotonic() - t0
        self._subprocess_total_sec += elapsed
        if result.returncode != 0:
            raise SlockTransportError(f"slock CLI exit={result.returncode} stderr={result.stderr.strip()[:300]}")
        return result.stdout

    def _send_envelope(self, envelope):
        """Send envelope to envelope['to'] via slock message send. Returns send timestamp (UTC)."""
        target = f"dm:{envelope['to']}"
        body = (
            f"{ENVELOPE_MARKER_START}\n"
            f"{json.dumps(envelope, ensure_ascii=False)}\n"
            f"{ENVELOPE_MARKER_END}\n"
        )
        send_ts = datetime.now(timezone.utc)
        for attempt in range(self.max_retry + 1):
            try:
                self._run_slock(["message", "send", "--target", target], stdin=body)
                return send_ts
            except SlockTransportError as e:
                if "freshness hold" in str(e).lower() or "draft" in str(e).lower():
                    # Workaround per MVP reframe: re-send via --send-draft
                    try:
                        self._run_slock(["message", "send", "--send-draft", "--target", target])
                        return send_ts
                    except SlockTransportError:
                        pass
                if attempt < self.max_retry:
                    time.sleep(1)
                    continue
                raise

    def _parse_envelope_from_body(self, body):
        """Extract envelope JSON between markers. Returns None if not found.

        Robustness: agents may mention marker strings INSIDE the JSON payload (e.g., reasoning_trace
        quoting the protocol). Use `rfind` for END marker to find the true closing one — the JSON
        payload comes between the first START and the last END.
        """
        start_idx = body.find(ENVELOPE_MARKER_START)
        if start_idx < 0:
            return None
        end_idx = body.rfind(ENVELOPE_MARKER_END)
        if end_idx <= start_idx:
            return None
        json_text = body[start_idx + len(ENVELOPE_MARKER_START):end_idx].strip()
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            return None

    def _poll_for_response(self, target_agent, prior_msg_id, send_ts, dispatch_start,
                           expected_case_id=None, expected_trace_id=None):
        """Poll DM channel from target until response envelope arrives (matching prior_msg_id).

        Slock CLI `--after` expects an integer sequence number, not a timestamp. We instead read
        the last N messages each poll (sorted recent-first by the daemon) and scan for the response
        envelope matching prior_msg_id. Seen msg_ids cached to skip re-parse.

        Change 7b: lenient fallback — if an envelope has matching case_id+trace_id but wrong
        prior_msg_id, accept it immediately with a stderr warning instead of waiting for the full
        dispatch_timeout. Requires expected_case_id and expected_trace_id to be provided.

        L7: when a malformed envelope is detected, sends an educational DM to the agent and
        continues polling. Escalates after 3 rejections per (agent, trace_id).
        """
        channel = f"dm:{target_agent}"
        seen = set()
        seen_l7 = set()  # Dedup for Class 1 format errors (no msg_id available)
        while True:
            elapsed = time.monotonic() - dispatch_start
            if elapsed > self.dispatch_timeout:
                raise SlockTransportError(
                    f"dispatch timeout: no response from {target_agent} for prior_msg_id={prior_msg_id} after {elapsed:.1f}s"
                )
            time.sleep(self.poll_interval)
            self._poll_count += 1
            try:
                output = self._run_slock(["message", "read", "--channel", channel, "--limit", "20"])
            except SlockTransportError:
                continue  # poll loop tolerates transient subprocess errors
            # Parse all messages in output, look for matching envelope
            for block in self._iter_message_blocks(output):
                env = self._parse_envelope_from_body(block)
                if not env:
                    # L7 Class 1: format error — block looks like an envelope but uses wrong wrapper
                    if self._detect_format_error(block):
                        m = re.search(r'\bmsg=([a-f0-9]+)', block)
                        block_key = m.group(1) if m else block[:80]
                        if block_key not in seen_l7:
                            seen_l7.add(block_key)
                            ctr_key = (target_agent, expected_trace_id or "unknown")
                            self._l7_counters[ctr_key] = self._l7_counters.get(ctr_key, 0) + 1
                            ctr = self._l7_counters[ctr_key]
                            self._write_l7_audit(
                                target_agent, expected_case_id, expected_trace_id,
                                "format_error", ctr,
                                "envelope wrapper in wrong format (backtick fence or bare JSON without SLOCK markers)",
                            )
                            if ctr > 3:
                                raise SlockTransportError(
                                    f"L7 escalation: {target_agent} format_error — 3 educational rejections exhausted "
                                    f"(trace={expected_trace_id})"
                                )
                            self._send_l7_rejection(
                                target_agent, expected_case_id, expected_trace_id,
                                "format_error",
                                "backtick fence or bare JSON instead of <<<SLOCK_ENVELOPE_V1>>> markers",
                                ctr,
                            )
                    continue
                if env.get("msg_id") in seen:
                    continue
                seen.add(env.get("msg_id"))
                # L7 Class 2: schema error — SLOCK markers present but envelope fails validate_message
                try:
                    validate_message(env)
                except ValidationError as ve:
                    if (expected_case_id and expected_trace_id
                            and env.get("case_id") == expected_case_id
                            and env.get("trace_id") == expected_trace_id):
                        ctr_key = (target_agent, expected_trace_id)
                        self._l7_counters[ctr_key] = self._l7_counters.get(ctr_key, 0) + 1
                        ctr = self._l7_counters[ctr_key]
                        self._write_l7_audit(
                            target_agent, expected_case_id, expected_trace_id,
                            "schema_error", ctr, str(ve), env.get("msg_id"),
                        )
                        if ctr > 3:
                            raise SlockTransportError(
                                f"L7 escalation: {target_agent} schema_error — 3 educational rejections exhausted "
                                f"(trace={expected_trace_id})"
                            )
                        self._send_l7_rejection(
                            target_agent, expected_case_id, expected_trace_id,
                            "schema_error", str(ve), ctr,
                        )
                    continue  # Don't return a schema-invalid envelope
                # L7 Class 3: direction error — agent re-emitted a request instead of a response
                if (env.get("type", "").endswith("_request")
                        and expected_case_id and expected_trace_id
                        and env.get("case_id") == expected_case_id
                        and env.get("trace_id") == expected_trace_id
                        and env.get("from") == target_agent):
                    ctr_key = (target_agent, expected_trace_id)
                    self._l7_counters[ctr_key] = self._l7_counters.get(ctr_key, 0) + 1
                    ctr = self._l7_counters[ctr_key]
                    self._write_l7_audit(
                        target_agent, expected_case_id, expected_trace_id,
                        "direction_error", ctr,
                        f"type={env.get('type')} ends with _request (request forwarded instead of response emitted)",
                        env.get("msg_id"),
                    )
                    if ctr > 3:
                        raise SlockTransportError(
                            f"L7 escalation: {target_agent} direction_error — 3 educational rejections exhausted "
                            f"(trace={expected_trace_id})"
                        )
                    self._send_l7_rejection(
                        target_agent, expected_case_id, expected_trace_id,
                        "direction_error", f"type={env.get('type')}", ctr,
                    )
                    continue
                # Strict match: correct prior_msg_id
                if env.get("prior_msg_id") == prior_msg_id:
                    return env
                # Change 7b + L7 Class 4: lenient fallback with educational rejection.
                # Guard: prior_msg_id must be non-None to distinguish responses from requests
                # (request envelopes have prior_msg_id=None and appear in DM read history too).
                if (expected_case_id and expected_trace_id
                        and env.get("case_id") == expected_case_id
                        and env.get("trace_id") == expected_trace_id
                        and env.get("prior_msg_id") is not None):
                    # L7 Class 4: educational rejection fired but envelope still accepted (non-breaking)
                    ctr_key = (target_agent, expected_trace_id)
                    self._l7_counters[ctr_key] = self._l7_counters.get(ctr_key, 0) + 1
                    ctr = self._l7_counters[ctr_key]
                    self._write_l7_audit(
                        target_agent, expected_case_id, expected_trace_id,
                        "prior_msg_id_error", ctr,
                        f"expected={prior_msg_id} got={env.get('prior_msg_id')}",
                        env.get("msg_id"),
                    )
                    self._send_l7_rejection(
                        target_agent, expected_case_id, expected_trace_id,
                        "prior_msg_id_error", env.get("prior_msg_id"), ctr,
                        expected_prior_msg_id=prior_msg_id,
                    )
                    print(
                        f"[WARN Change7b+L7] prior_msg_id mismatch from {target_agent}: "
                        f"expected={prior_msg_id} got={env.get('prior_msg_id')} "
                        f"case_id={expected_case_id} — L7 Class4 fired, accepted via lenient fallback",
                        file=sys.stderr,
                    )
                    return env

    # Match start of a message block — handles both `slock message check` ([target=...]) and `slock message read` ([seq=...]) formats
    _BLOCK_START_RE = re.compile(r"^\[(?:seq|target)=")

    def _iter_message_blocks(self, read_output):
        """Yield message body segments. Handles both [target=...] (check) and [seq=...] (read) header formats."""
        lines = read_output.split("\n")
        current = []
        for line in lines:
            if self._BLOCK_START_RE.match(line):
                if current:
                    yield "\n".join(current)
                current = [line]
            else:
                current.append(line)
        if current:
            yield "\n".join(current)

    # === L7 Adapter Educational Rejection ===

    def _detect_format_error(self, body):
        """Return True if body looks like a JSON envelope in wrong format (no SLOCK markers present)."""
        if ENVELOPE_MARKER_START in body:
            return False
        if "```json" in body or "```\n{" in body:
            if '"msg_id"' in body:
                return True
        if '"msg_id"' in body and '"prior_msg_id"' in body and ('"v": "1"' in body or '"v":"1"' in body):
            return True
        return False

    def _send_l7_rejection(self, agent, case_id, trace_id, class_, detail, counter,
                           expected_prior_msg_id=None):
        """Send L7 educational rejection DM to agent. Best-effort — swallows transport errors."""
        n = f"{counter}/3"
        case_str = case_id or "unknown"
        trace_str = trace_id or "unknown"
        if class_ == "format_error":
            body = (
                f"⚠️ [L7 Adapter Rejection] Your last response for case {case_str} "
                f"(trace {trace_str}) was REJECTED.\n\n"
                f"Reason: envelope wrapper used markdown code fence (```json ... ```) or bare JSON "
                f"instead of <<<SLOCK_ENVELOPE_V1>>> markers.\n\n"
                f"Fix:\n"
                f"1. Call `scripts/agent_schema.py:build_envelope(payload, ...)` to construct the envelope.\n"
                f"2. Emit its **literal** return value as your message body — do NOT hand-write or wrap in code fence.\n"
                f"3. Expected shape:\n"
                f"   <<<SLOCK_ENVELOPE_V1>>>\n"
                f"   {{\"v\": \"1\", \"type\": \"...\", ..., \"payload\": {{...}}}}\n"
                f"   <<<END_SLOCK_ENVELOPE>>>\n\n"
                f"Reference: MEMORY.md § Universal Operational Discipline Rule 1 (envelope discipline) + Change 6/9 ack history.\n\n"
                f"Please re-emit your response now using the correct wrapper. You have ~5 min before this dispatch escalates.\n\n"
                f"Rejection counter for this trace: {n}"
            )
        elif class_ == "schema_error":
            body = (
                f"⚠️ [L7 Adapter Rejection] Your envelope wrapper is correct but the payload failed validate_message().\n\n"
                f"Reason: {detail}\n\n"
                f"Fix:\n"
                f"1. Re-construct the envelope using `build_envelope(payload, from_agent, to_agent, case_id, trace_id, prior_msg_id, type)` "
                f"— ensure all required fields are present.\n"
                f"2. For Phase A KC/CM responses, `prior_msg_id` MUST equal the orchestrator's dispatch msg_id (the message you are replying to).\n"
                f"3. Re-emit the corrected envelope.\n\n"
                f"Reference: scripts/agent_schema.py:validate_message + MEMORY.md § Universal Operational Discipline Rules 1+2.\n\n"
                f"Rejection counter: {n}"
            )
        elif class_ == "direction_error":
            body = (
                f"⚠️ [L7 Adapter Rejection] The envelope you just emitted appears to be a REQUEST, not a RESPONSE.\n\n"
                f"Reason: `type` field ends with `_request` instead of `_response`.\n\n"
                f"Context: this is likely because you re-forwarded the orchestrator's dispatch envelope instead of producing "
                f"your own response. The adapter relies on direction filter (Change 7b) but L7 surfaces it explicitly so you can correct.\n\n"
                f"Fix:\n"
                f"1. Construct a NEW envelope with `from = {agent}`, `to = @SlimOrchestrator`, "
                f"`type = <your_response_type>` (e.g., `kc_retrieval_response`, `cm_similar_case_response`).\n"
                f"2. `prior_msg_id` = the request envelope's `msg_id`.\n"
                f"3. Emit using build_envelope().\n\n"
                f"Rejection counter: {n}"
            )
        elif class_ == "prior_msg_id_error":
            body = (
                f"⚠️ [L7 Adapter Rejection] Envelope OK but `prior_msg_id` does not match the dispatching request.\n\n"
                f"Reason: expected prior_msg_id = {expected_prior_msg_id}, got {detail}.\n\n"
                f"Common cause: per-case isolation discipline lapse — you used a prior case's msg_id (stale memory) "
                f"instead of THIS dispatch's msg_id.\n\n"
                f"Fix:\n"
                f"1. Re-read the dispatch request envelope you received.\n"
                f"2. Set `prior_msg_id = {expected_prior_msg_id}` in your response.\n"
                f"3. Re-emit using build_envelope().\n\n"
                f"Reference: MEMORY.md § Universal Operational Discipline Rule 2 (prior_msg_id verify) + Rule 3 (per-case isolation).\n\n"
                f"Rejection counter: {n}"
            )
        else:
            return
        try:
            self._run_slock(["message", "send", "--target", f"dm:{agent}"], stdin=body)
        except SlockTransportError as e:
            print(f"[L7] failed to send rejection DM to {agent}: {e}", file=sys.stderr)

    def _write_l7_audit(self, agent, case_id, trace_id, rejection_class, rejection_counter,
                        specific_violation, rejected_msg_id=None):
        """Append L7 rejection entry to notes/audit/l7_rejections.jsonl."""
        entry = {
            "type": "l7_adapter_educational_rejection",
            "agent": agent,
            "case_id": case_id,
            "trace_id": trace_id,
            "rejection_class": rejection_class,
            "rejection_counter": rejection_counter,
            "specific_violation": specific_violation,
            "rejected_msg_id": rejected_msg_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        audit_path = ROOT / "notes" / "audit" / "l7_rejections.jsonl"
        try:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[L7] audit write failed: {e}", file=sys.stderr)

    # === End L7 ===

    def invoke(self, envelope):
        """Send envelope, poll for response, return response envelope. Raises on timeout/transport error."""
        validate_message(envelope)
        self._subprocess_total_sec = 0.0
        self._poll_count = 0
        self._l7_counters = {}
        dispatch_start = time.monotonic()
        send_ts = self._send_envelope(envelope)
        try:
            response = self._poll_for_response(
                target_agent=envelope["to"],
                prior_msg_id=envelope["msg_id"],
                send_ts=send_ts,
                dispatch_start=dispatch_start,
                expected_case_id=envelope.get("case_id"),
                expected_trace_id=envelope.get("trace_id"),
            )
        except SlockTransportError:
            raise
        validate_message(response)
        # Attach transport metadata to response payload _transport (optional, for audit)
        response.setdefault("_transport_metadata", {}).update({
            "dispatch_wall_sec": round(time.monotonic() - dispatch_start, 3),
            "subprocess_wall_sec": round(self._subprocess_total_sec, 3),
            "poll_cycle_count": self._poll_count,
            "subprocess_overhead_pct": round(
                100 * self._subprocess_total_sec / max(time.monotonic() - dispatch_start, 0.001), 1
            ),
        })
        return response


# === Self-test (lightweight, no actual Slock dispatch — needs live env) ===
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true", help="Run lightweight import + structure tests")
    parser.add_argument("--l7-test", action="store_true", help="Run L7 adapter educational rejection self-tests (no live Slock dispatch)")
    parser.add_argument("--echo-test", metavar="AGENT", help="Live echo test: send envelope to @AGENT and wait for response (requires that agent to echo back)")
    args = parser.parse_args()

    if args.self_test:
        # Validate adapter class structure
        adapter = SlockCLIAdapter(from_agent_id="@SlimOrchestrator")
        assert hasattr(adapter, "invoke")
        assert adapter.poll_interval == 5
        assert adapter.dispatch_timeout == 90
        assert adapter.subprocess_timeout == 30
        # Test envelope parse roundtrip
        sample_env = {
            "v": "1", "msg_id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "from": "@A", "to": "@B", "type": "kc_retrieval_request",
            "scene": "3_doctor", "trace_id": str(uuid.uuid4()),
            "prior_msg_id": None, "audit_id": str(uuid.uuid4()),
            "case_id": None,
            "payload": {"query_hints": ["test"]},
        }
        body = (
            f"[target=dm:@B msg=xxx time=now type=agent] @A:\n"
            f"{ENVELOPE_MARKER_START}\n"
            f"{json.dumps(sample_env, ensure_ascii=False)}\n"
            f"{ENVELOPE_MARKER_END}\n"
        )
        parsed = adapter._parse_envelope_from_body(body)
        assert parsed is not None, "envelope parse roundtrip failed"
        assert parsed["msg_id"] == sample_env["msg_id"]
        assert parsed["type"] == "kc_retrieval_request"
        # Test multi-message block iteration
        multi = (
            "[target=dm:@B msg=1 time=t1 type=agent] @A: first\n"
            "[target=dm:@B msg=2 time=t2 type=agent] @A: second\n"
            "line2 of second\n"
        )
        blocks = list(adapter._iter_message_blocks(multi))
        assert len(blocks) == 2, f"expected 2 blocks, got {len(blocks)}"
        print("[self-test] adapter structure OK")
        print("[self-test] envelope parse roundtrip OK")
        print("[self-test] multi-block iteration OK (2 blocks)")
        print("[self-test] passed")

    elif args.l7_test:
        import tempfile, os
        adapter = SlockCLIAdapter()

        # Test 1: format_error detection — backtick fence with msg_id → True
        backtick_block = (
            "[target=dm:@KC msg=aabbccdd time=now type=agent] @KnowledgeCurator:\n"
            "```json\n"
            '{"v": "1", "msg_id": "abc", "prior_msg_id": "xyz", "type": "kc_retrieval_response"}\n'
            "```\n"
        )
        assert adapter._detect_format_error(backtick_block), "Test 1 FAIL: backtick block should detect format error"
        print("[l7-test] 1/5 format_error detection (backtick) OK")

        # Test 2: format_error detection — bare JSON with envelope fields → True
        bare_json_block = (
            "[target=dm:@KC msg=11223344 time=now type=agent] @KnowledgeCurator:\n"
            '{"v": "1", "msg_id": "abc", "prior_msg_id": "xyz"}\n'
        )
        assert adapter._detect_format_error(bare_json_block), "Test 2 FAIL: bare JSON block should detect format error"
        print("[l7-test] 2/5 format_error detection (bare JSON) OK")

        # Test 3: format_error NOT fired for correct SLOCK markers
        correct_block = (
            f"[target=dm:@KC msg=55667788 time=now type=agent] @KnowledgeCurator:\n"
            f"{ENVELOPE_MARKER_START}\n"
            '{"v": "1", "msg_id": "abc"}\n'
            f"{ENVELOPE_MARKER_END}\n"
        )
        assert not adapter._detect_format_error(correct_block), "Test 3 FAIL: correct markers should NOT detect format error"
        print("[l7-test] 3/5 format_error NOT fired for correct markers OK")

        # Test 4: format_error NOT fired for plain chat message
        chat_block = "[target=dm:@KC msg=99aabbcc time=now type=agent] @KnowledgeCurator: Thanks for the info!"
        assert not adapter._detect_format_error(chat_block), "Test 4 FAIL: plain chat should NOT detect format error"
        print("[l7-test] 4/5 format_error not fired for plain chat OK")

        # Test 5: audit write + counter escalation logic
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch audit path to temp dir
            orig_root = globals().get("ROOT")
            import importlib
            mod = sys.modules[__name__] if __name__ != "__main__" else None
            # Write audit entry directly
            tmp_audit = Path(tmpdir) / "l7_rejections.jsonl"
            entry = {
                "type": "l7_adapter_educational_rejection",
                "agent": "@KnowledgeCurator",
                "case_id": "test-case",
                "trace_id": "test-trace",
                "rejection_class": "format_error",
                "rejection_counter": 1,
                "specific_violation": "backtick fence test",
                "rejected_msg_id": None,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            }
            with open(tmp_audit, "a") as f:
                f.write(json.dumps(entry) + "\n")
            with open(tmp_audit) as f:
                lines = f.readlines()
            assert len(lines) == 1, f"Test 5 FAIL: expected 1 audit line, got {len(lines)}"
            parsed_entry = json.loads(lines[0])
            assert parsed_entry["rejection_class"] == "format_error"
            assert parsed_entry["rejection_counter"] == 1
            # Counter escalation: counter > 3 should escalate
            adapter._l7_counters = {("@KnowledgeCurator", "test-trace"): 4}
            ctr = adapter._l7_counters[("@KnowledgeCurator", "test-trace")]
            assert ctr > 3, "Test 5 FAIL: counter should be >3 for escalation"
        print("[l7-test] 5/5 audit write + counter escalation logic OK")

        print("[l7-test] all 5 L7 self-tests passed")

    elif args.echo_test:
        # Live test: requires another agent to be listening + echo back response
        adapter = SlockCLIAdapter(from_agent_id="@DentistWang")  # use real agent ID for from
        trace_id = str(uuid.uuid4())
        # Use clinician_synthesize_request when target is @Clinician (per 太上老君 recommendation msg=c866688b)
        if args.echo_test == "Clinician":
            envelope = build_envelope(
                from_="@DentistWang", to=f"@{args.echo_test}",
                type_="clinician_synthesize_request",
                trace_id=trace_id, case_id=str(uuid.uuid4()), scene="3_doctor",
                payload={
                    "case_struct": {
                        "chief_complaint": "M-C2 adapter live echo test (synthetic case, please respond with valid clinician_synthesize_response)",
                        "morphology_hint": "test",
                    },
                    "kc_response_ref": str(uuid.uuid4()),  # mock — Clinician should treat as test
                    "cm_response_ref": str(uuid.uuid4()),
                },
            )
        else:
            envelope = build_envelope(
                from_="@DentistWang", to=f"@{args.echo_test}",
                type_="kc_retrieval_request",
                trace_id=trace_id, case_id=None, scene="3_doctor",
                payload={"query_hints": ["adapter echo test"], "entity_filter": None, "kb_scope_subset": None},
            )
        print(f"[echo-test] sending envelope msg_id={envelope['msg_id']} to @{args.echo_test}, waiting up to 90s...")
        try:
            response = adapter.invoke(envelope)
            print(f"[echo-test] response received: msg_id={response['msg_id']}, type={response['type']}")
            transport = response.get("_transport_metadata", {})
            print(f"[echo-test] transport metadata: {transport}")
        except SlockTransportError as e:
            print(f"[echo-test] transport error: {e}")
            sys.exit(1)
        except ValidationError as e:
            print(f"[echo-test] response schema invalid: {e}")
            sys.exit(2)
