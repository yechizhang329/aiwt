#!/usr/bin/env python3
"""
HardRuleWrapper — Deterministic hard rule check for clinical output.

Phase 1 Week 2 M-B2. Per Tier 2 § 6 + 太上老君 Q1 decision (deterministic = HardRuleWrapper, semantic = VoiceWrapper).

13 rules organized as 5 shared + 5 Scene 1 + 3 Scene 3 (per 太上老君 a5ade3cd).

Backward compat: `diagnostic_post.py` will import `check_output()` from this module.

Usage (as module):
    from HardRuleWrapper import check_output
    result = check_output(content, scene="1_patient", voice_mode="A")
    # → {"pass": bool, "violations": [...], "rules_applied": [...], "audit_log_ref": uuid}

Usage (CLI):
    cat output.md | python3 scripts/HardRuleWrapper.py --scene 1_patient --voice-mode A
"""
import json
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "notes/orthodontics/clinical_kb/_hard_rules.json"
ONTOLOGY_PATH = ROOT / "notes/orthodontics/clinical_kb/_entity_ontology.json"

_RULES_CACHE = None
_ONTOLOGY_CACHE = None


def _load_rules():
    global _RULES_CACHE
    if _RULES_CACHE is None:
        with open(RULES_PATH) as f:
            _RULES_CACHE = json.load(f)
    return _RULES_CACHE


def _load_ontology():
    global _ONTOLOGY_CACHE
    if _ONTOLOGY_CACHE is None:
        with open(ONTOLOGY_PATH) as f:
            _ONTOLOGY_CACHE = json.load(f)
    return _ONTOLOGY_CACHE


# === Rule check implementations ===

def _check_regex_forbidden(content, rule):
    """Block content matching forbidden patterns, unless inside exemption context."""
    violations = []
    exemptions = rule.get("exemptions_contexts", [])
    for pattern in rule["patterns"]:
        for m in re.finditer(pattern, content):
            line_start = content.rfind("\n", 0, m.start()) + 1
            line_end = content.find("\n", m.end())
            line = content[line_start:line_end if line_end > 0 else len(content)]
            if any(exempt in line for exempt in exemptions):
                continue
            violations.append({
                "match": m.group(0),
                "position": m.start(),
                "context_line": line.strip()[:200],
            })
    return violations


def _check_regex_pattern_check(content, rule):
    """Like regex_forbidden but allow listed exact exemptions (used for convenience-term hallucination)."""
    violations = []
    exemptions = set(rule.get("exemptions", []))
    for pattern in rule["convenience_patterns"]:
        for m in re.finditer(pattern, content):
            if m.group(0) in exemptions:
                continue
            line_start = content.rfind("\n", 0, m.start()) + 1
            line_end = content.find("\n", m.end())
            line = content[line_start:line_end if line_end > 0 else len(content)]
            violations.append({
                "match": m.group(0),
                "position": m.start(),
                "context_line": line.strip()[:200],
            })
    return violations


_MD_HEADER_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_MD_BOLD_LABEL_LINE = re.compile(r"^\*\*[^*\n]+\*\*\s*$", re.MULTILINE)


def _strip_markdown_chrome(content):
    """Remove markdown formatting overhead (ATX headers + **bold label** section headers).
    Length check measures clinical content per VoiceWrapper's sections-only count;
    rendered_markdown headers ("### 您的初步咨询答复" / "**您的情况**" etc.) are presentation, not content.
    """
    content = _MD_HEADER_LINE.sub("", content)
    content = _MD_BOLD_LABEL_LINE.sub("", content)
    return content


def _check_length_range(content, rule, voice_mode=None):
    violations = []
    cleaned = _strip_markdown_chrome(content)
    char_count = len(re.sub(r'[^一-鿿　-〿！-｠]', '', cleaned))
    modes = rule.get("modes", {})
    # Mode resolution
    if voice_mode and voice_mode in modes:
        m = modes[voice_mode]
    elif "default" in modes:
        m = modes["default"]
    elif modes:
        m = list(modes.values())[0]
    else:
        m = {"min": rule.get("min", 0), "max": rule.get("max", 99999)}
    if char_count < m["min"]:
        violations.append({"reason": "below_min", "actual": char_count, "expected_min": m["min"]})
    if char_count > m["max"]:
        violations.append({"reason": "above_max", "actual": char_count, "expected_max": m["max"]})
    return violations


def _check_lock_with_anchor(content, rule, scene):
    """If sub-class name surfaces, require quant anchor OR tentative marker nearby.
    Per tier3_change_15 (2026-05-28), sub-class surface-form variants (canonical + synonyms from
    `subclass_synonyms` rule field) are treated as same lock — Clinician/KC may use 颌位型 (KB
    qa-0264) vs 颌位性 (Walter v2) interchangeably. Anchor check applies to each occurrence of
    canonical OR synonym variant, but proximity window also matches if anchor sits on a sibling
    variant (cross-variant anchoring within ±200 chars window).
    """
    violations = []
    if scene == "3_doctor":
        anchor_phrases = rule.get("anchor_required_phrases_scene3", [])
    else:
        anchor_phrases = rule.get("anchor_required_phrases_scene1", [])
    tentative_phrases = rule.get("tentative_marker_phrases", [])
    all_acceptable = list(anchor_phrases) + list(tentative_phrases)
    subclass_synonyms = rule.get("subclass_synonyms", {})
    # Sort surface forms longest-first so e.g. "颌位型" matches before "颌位" fallback.
    # Skip metadata keys (e.g., "_comment") whose value is a string, not a list of variants.
    synonym_lists = [
        vs for k, vs in subclass_synonyms.items()
        if not k.startswith("_") and isinstance(vs, list)
    ]
    surface_forms = sorted(
        set(rule["subclass_names"]) | {v for vs in synonym_lists for v in vs},
        key=len,
        reverse=True,
    )
    # Phase 5 v1.2: Scene 3 framework discussion context exemption (per cases 1d9e40b9/a73f8817/
    # 5b94e637/ae84780c HRW false-positive lessons): doctor-to-doctor Scene 3 sections include
    # sub-class names in framework/taxonomy/qa-lineage discussion contexts (e.g., "qa-0264 颌位型
    # lineage" / "axis 2 候选 关节代偿性-突吸偏 leading" / "三分法") which are NOT patient-facing
    # lock attempts. Distinguish via framework_discussion_context_phrases within ±window.
    framework_phrases = rule.get("framework_discussion_context_phrases_scene3", [])
    framework_window = rule.get("framework_discussion_context_window_chars", 50)
    framework_exempt_scene3 = rule.get("framework_discussion_exempt_scene3", False)
    seen_positions = set()
    for sub in surface_forms:
        for m in re.finditer(sub, content):
            # Skip overlapping matches already handled by a longer-form match
            if any(start <= m.start() < end for start, end in seen_positions):
                continue
            seen_positions.add((m.start(), m.end()))
            window_start = max(0, m.start() - 200)
            window_end = min(len(content), m.end() + 200)
            window = content[window_start:window_end]
            if any(p in window for p in all_acceptable):
                continue
            # Phase 5 v1.2: Scene 3 framework discussion context exemption
            if scene == "3_doctor" and framework_exempt_scene3 and framework_phrases:
                fw_window_start = max(0, m.start() - framework_window)
                fw_window_end = min(len(content), m.end() + framework_window)
                fw_window = content[fw_window_start:fw_window_end]
                if any(p in fw_window for p in framework_phrases):
                    continue  # framework discussion context → not a patient-facing lock attempt → skip
            violations.append({
                "subclass": sub,
                "position": m.start(),
                "issue": "sub-class name surfaced without quantitative anchor OR tentative marker within ±200 chars",
            })
    return violations


def _check_conditional_lock(content, rule):
    """If device name surfaces, require prerequisite + ≥1 trigger phrase.

    Phase 5 v1.2 (per cases 1d9e40b9/a73f8817 false-positive lessons):
    Context-aware negation detection — if device mention is within negation context
    (e.g., "S17 = N/A" / "S17 不触发"), skip violation. Each device occurrence
    individually evaluated for negation context within ±window_chars.
    """
    violations = []
    device = rule.get("device_name", "")
    prereq = rule.get("prerequisite_phrases", [])
    triggers = rule.get("trigger_any_phrases", [])
    negation_phrases = rule.get("negation_context_phrases", [])
    window = rule.get("negation_context_window_chars", 30)

    if not re.search(rf"\b{device}\b", content):
        return violations  # device not used → OK

    # Phase 5 v1.2: check if EVERY device occurrence is within negation context
    # → entire mention is non-affirmative → skip violation
    device_pattern = rf"\b{device}\b"
    matches = list(re.finditer(device_pattern, content))
    if matches and negation_phrases:
        all_negated = True
        for m in matches:
            start = max(0, m.start() - window)
            end = min(len(content), m.end() + window)
            context_slice = content[start:end]
            if not any(neg in context_slice for neg in negation_phrases):
                all_negated = False
                break
        if all_negated:
            return violations  # all mentions in negation context → no affirmative S17 use → OK

    has_prereq = any(p in content for p in prereq)
    has_trigger = any(t in content for t in triggers)
    if not has_prereq:
        violations.append({"device": device, "issue": "missing prerequisite phrase", "expected_any": prereq})
    if not has_trigger:
        violations.append({"device": device, "issue": "missing trigger condition phrase", "expected_any": triggers})
    return violations


def _check_conditional_phrase_required(content, rule):
    """If any trigger_phrases present in content, require at least 1 of required_phrases_any
    OR required_phrases_count_geq.count from required_phrases_count_geq.from_set.

    Phase 5 v1.2.3 (case 0b4994b8 + Walter msg=fedbbb0a calibration lessons):
    - concave_family_history_required_scene3: 凹面 sub-class mention → 家族遗传 follow-up phrase required
    - midline_misalign_3_subclass_required_scene3: 偏颌 mention → 3 子类 鉴别 phrases required

    Phase 5 v1.3.1 (case e317081d 17F TMD false-positive lesson):
    Framework discussion context exemption — if every trigger phrase occurrence is within
    ±framework_discussion_context_window_chars of framework_discussion_context_phrases_scene3
    (e.g., "Anti-凹面", "鉴别", "TMD", "axis 4", "排除", "不触发"), skip violation
    (NOT actual sub-class diagnosis context; framework discussion / scope exclusion).
    Mirror s17 negation + subclass_lock framework discussion exemption pattern.
    """
    violations = []
    triggers = rule.get("trigger_phrases", [])

    # Find all trigger occurrences (each must be evaluated individually for context)
    trigger_matches = []
    for t in triggers:
        for m in re.finditer(re.escape(t), content):
            trigger_matches.append((t, m.start(), m.end()))
    if not trigger_matches:
        return violations  # not applicable — no trigger fired

    # Phase 5 v1.3.1: framework discussion context exemption
    framework_phrases = rule.get("framework_discussion_context_phrases_scene3", [])
    framework_window = rule.get("framework_discussion_context_window_chars", 50)
    framework_exempt = rule.get("framework_discussion_exempt_scene3", False)

    if framework_exempt and framework_phrases:
        # Check if EVERY trigger occurrence is within framework discussion context
        # → entire mention is framework/exclusion, NOT actual diagnosis → skip violation
        all_in_framework_context = True
        for (t, start, end) in trigger_matches:
            ctx_start = max(0, start - framework_window)
            ctx_end = min(len(content), end + framework_window)
            ctx_slice = content[ctx_start:ctx_end]
            if not any(fp in ctx_slice for fp in framework_phrases):
                all_in_framework_context = False
                break
        if all_in_framework_context:
            return violations  # all triggers in framework discussion context → OK

    # Required: at least 1 from required_phrases_any
    required_any = rule.get("required_phrases_any", [])
    if required_any:
        if not any(p in content for p in required_any):
            violations.append({
                "issue": "trigger phrase present but required follow-up phrase missing",
                "trigger_found": [t for (t, _, _) in trigger_matches[:3]],
                "required_any": required_any,
            })
            return violations

    # Required: at least count_geq.count from count_geq.from_set
    count_geq = rule.get("required_phrases_count_geq")
    if count_geq:
        min_count = count_geq.get("count", 1)
        from_set = count_geq.get("from_set", [])
        found_count = sum(1 for p in from_set if p in content)
        if found_count < min_count:
            violations.append({
                "issue": f"trigger phrase present but only {found_count} of required {min_count} sub-class differentiation phrases found",
                "trigger_found": [t for (t, _, _) in trigger_matches[:3]],
                "required_min_count": min_count,
                "from_set": from_set,
                "found_count": found_count,
            })
    return violations


def _check_conditional_routing_required(content, rule):
    """If trigger_subclass_phrases present in non-TENTATIVE context, require routing phrases + check forbidden device.

    Phase 5 v1.2.3 (case 0b4994b8 axis 1 reverse lesson):
    凹面 sub-class lock → MUST include face mask + 扩弓 family routing, MUST NOT lock SGTB family.
    Skip if TENTATIVE / ambiguity exemption context present near the trigger phrase.
    """
    violations = []
    triggers = rule.get("trigger_subclass_phrases", [])
    required_any = rule.get("required_phrases_any", [])
    forbidden = rule.get("forbidden_device_when_locked", [])
    exemptions = rule.get("tentative_exemption_phrases", [])
    window = 120  # chars around trigger phrase to check for TENTATIVE exemption

    # Find any trigger phrase NOT in TENTATIVE exemption context
    affirmative_triggers = []
    for trig in triggers:
        for m in re.finditer(re.escape(trig), content):
            start = max(0, m.start() - window)
            end = min(len(content), m.end() + window)
            context_slice = content[start:end]
            if not any(ex in context_slice for ex in exemptions):
                affirmative_triggers.append(trig)
                break  # one affirmative occurrence sufficient

    if not affirmative_triggers:
        return violations  # all triggers in TENTATIVE/ambiguity context → OK

    # Check required routing phrase present
    has_required = any(p in content for p in required_any)
    if not has_required:
        violations.append({
            "issue": "凹面 sub-class affirmatively locked but face mask / 扩弓 routing missing",
            "trigger_found": affirmative_triggers[:3],
            "required_any": required_any,
        })

    # Check forbidden device not locked (allow if TENTATIVE-context near the device)
    for dev in forbidden:
        for m in re.finditer(re.escape(dev), content):
            start = max(0, m.start() - window)
            end = min(len(content), m.end() + window)
            context_slice = content[start:end]
            if not any(ex in context_slice for ex in exemptions):
                violations.append({
                    "issue": f"forbidden device {dev} affirmatively locked alongside 凹面 sub-class (reverse misdiagnosis routing risk)",
                    "trigger_found": affirmative_triggers[:3],
                    "forbidden_device": dev,
                })
                break  # one violation per forbidden device sufficient
    return violations


def _check_school_anchor(content, rule):
    """If trigger device/concept surfaces, require school lineage attribution."""
    violations = []
    triggers = rule.get("trigger_if_present_phrases", [])
    schools = rule.get("school_phrases", [])
    has_trigger = any(t in content for t in triggers)
    if not has_trigger:
        return violations
    has_school = any(s in content for s in schools)
    if not has_school:
        violations.append({"issue": "trigger device/concept used without 学派 attribution", "trigger_found": [t for t in triggers if t in content][:3]})
    return violations


def _check_citation(content, rule):
    violations = []
    min_cit = rule.get("min_citations", 1)
    found = 0
    for pat in rule["citation_patterns"]:
        if re.search(pat, content):
            found += 1
    if found < min_cit:
        violations.append({"issue": "insufficient citations", "found": found, "min_required": min_cit})
    return violations


def _check_ontology_consistency(content, rule):
    """Ensure sub-class / device names align ontology canonical (not synonym variants in deprecated forms)."""
    violations = []
    ont = _load_ontology()
    entities = ont.get("entities", {})
    # Build synonym → canonical map (only for deprecated)
    for name, e in entities.items():
        if not isinstance(e, dict):
            continue
        if e.get("deprecated_label"):
            # Find usage of the canonical name OUTSIDE deprecation context
            # (we don't block — just flag if surfaced bare)
            if name in content:
                # Check ±100 chars context for "deprecated" markers
                for m in re.finditer(re.escape(name), content):
                    win_start = max(0, m.start() - 80)
                    win_end = min(len(content), m.end() + 80)
                    win = content[win_start:win_end]
                    if not any(marker in win for marker in ["deprecated", "DEPRECATED", "❌", "已弃用"]):
                        violations.append({
                            "deprecated_label": name,
                            "position": m.start(),
                            "issue": "deprecated_label canonical used without deprecation marker in context",
                        })
    return violations


# === Critic HIGH severity block check (Change 2-3, Tier 3 amendment 2026-05-28) ===

def check_critic_high_severity(critic_payload):
    """Phase F enhancement: check Critic critical_concerns for HIGH + recommended_action: block.

    Tier 2 § 4.3 carve-out (post jonathan 03:35 ack): general Critic disagreement remains
    surface-not-block, BUT critical_concerns with severity=HIGH + recommended_action=block
    triggers BLOCK (escalate to DentistWang governance or Clinician retry).

    Returns wrapper_check_response-shaped dict for SlimOrchestrator integration.
    """
    if not isinstance(critic_payload, dict):
        return {"status": "ok", "pass": True, "violations": [], "rules_applied": ["critic_high_severity_block"]}

    critical_concerns = critic_payload.get("critical_concerns", []) or []
    block_concerns = []
    for c in critical_concerns:
        if not isinstance(c, dict):
            # Legacy string format — cannot evaluate structured severity, skip (transition period)
            continue
        if c.get("severity") == "HIGH" and c.get("recommended_action") == "block":
            block_concerns.append(c)

    if block_concerns:
        return {
            "status": "ok",
            "pass": False,
            "violations": [{
                "rule_id": "critic_high_severity_block",
                "message": f"Critic flagged {len(block_concerns)} HIGH severity concern(s) with recommended_action=block",
                "severity": "error",
                "detail": {"block_concerns": block_concerns},
            }],
            "rules_applied": ["critic_high_severity_block"],
            "audit_log_ref": str(uuid.uuid4()),
        }
    return {
        "status": "ok",
        "pass": True,
        "violations": [],
        "rules_applied": ["critic_high_severity_block"],
        "audit_log_ref": str(uuid.uuid4()),
    }


# === Top-level check_output ===

RULE_CHECKERS = {
    "regex_forbidden": _check_regex_forbidden,
    "regex_pattern_check": _check_regex_pattern_check,
    "regex_forbidden_with_replacement": _check_regex_forbidden,
    "length_range": _check_length_range,
    "lock_with_anchor": _check_lock_with_anchor,
    "conditional_lock": _check_conditional_lock,
    "conditional_phrase_required": _check_conditional_phrase_required,
    "conditional_routing_required": _check_conditional_routing_required,
    "school_lineage_check": _check_school_anchor,
    "citation_check": _check_citation,
    "ontology_lookup": _check_ontology_consistency,
}


def check_output(content, scene, voice_mode=None, mode="voice_output"):
    """Apply hard rules for given scene + content phase.

    mode (Phase 2 architecture fix 2026-05-28 per Critic T-A1 #10 catch):
      - "clinician_content": Phase D check on Clinician raw section1+2+3 — apply only `shared_5` rules.
        Voice-output-specific rules (forbidden_coben/devices/length/S18/30岁/scene3_citation/length/s17)
        do NOT apply here because Clinician legitimately reasons about these in section 2 alt-path.
      - "voice_output" (default): Phase F check on VoiceWrapper rendered markdown — apply all scene rules.

    Returns wrapper_check_response payload (per _schemas.json).
    """
    rules = _load_rules()
    audit_id = str(uuid.uuid4())
    rules_applied = []
    all_violations = []

    # Determine applicable rule set based on mode
    if mode == "clinician_content":
        # Only shared rules at this phase (clinical reasoning may legitimately surface voice-forbidden content)
        applicable_rule_ids = set(rules.get("rule_groups", {}).get("shared_5", []))
    else:
        # voice_output (Phase F): all rules apply
        applicable_rule_ids = set(rules["rules"].keys())

    for rule_id, rule in rules["rules"].items():
        if rule_id not in applicable_rule_ids:
            continue
        if scene not in rule.get("applies_to_scenes", []):
            continue
        rules_applied.append(rule_id)
        rule_type = rule.get("type")
        checker = RULE_CHECKERS.get(rule_type)
        if not checker:
            all_violations.append({
                "rule_id": rule_id,
                "message": f"unknown rule type: {rule_type}",
                "severity": "warning",
            })
            continue
        try:
            if rule_type == "length_range":
                rule_violations = checker(content, rule, voice_mode=voice_mode)
            elif rule_type == "lock_with_anchor":
                rule_violations = checker(content, rule, scene)
            else:
                rule_violations = checker(content, rule)
        except Exception as e:
            all_violations.append({
                "rule_id": rule_id,
                "message": f"checker error: {e}",
                "severity": "warning",
            })
            continue
        for v in rule_violations:
            all_violations.append({
                "rule_id": rule_id,
                "message": rule["description"],
                "severity": "error",
                "clinical_severity": rule.get("clinical_severity", "LOW"),
                "detail": v,
            })

    return {
        "status": "ok",
        "pass": len(all_violations) == 0,
        "violations": all_violations,
        "rules_applied": rules_applied,
        "audit_log_ref": audit_id,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=["1_patient", "3_doctor"])
    parser.add_argument("--voice-mode", choices=["A_standard", "B_difficult_diagnosis_warning"])
    parser.add_argument("--file")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test and not args.scene:
        parser.error("--scene required unless --self-test")

    if args.self_test:
        # Test scene 1 — should fail on Coben + S18 + device codes
        bad_s1 = "Coben 三指标提示 S18 + S8-SGTB 联合方案. 30 岁 cutoff 适用. " * 50
        r1 = check_output(bad_s1, "1_patient", voice_mode="A_standard")
        assert not r1["pass"], "Scene 1 bad content should fail"
        violated_rules = {v["rule_id"] for v in r1["violations"]}
        print(f"[self-test] Scene 1 bad: pass={r1['pass']}, {len(r1['violations'])} violations on rules: {sorted(violated_rules)}")
        assert "forbidden_coben_layer1" in violated_rules
        assert "s18_not_recommended_patient_facing" in violated_rules
        assert "forbidden_device_codes_layer1" in violated_rules

        # Test scene 3 — should fail on missing citation if too short
        bad_s3_no_cite = "患者 17F 朱禹涵。上颌源型, 治疗 S8-SGHB。" * 30
        r3 = check_output(bad_s3_no_cite, "3_doctor")
        violated_s3 = {v["rule_id"] for v in r3["violations"]}
        print(f"[self-test] Scene 3 no citation: pass={r3['pass']}, violations on: {sorted(violated_s3)}")
        assert "scene3_source_citation_required" in violated_s3

        # Test scene 1 clean — should pass
        good_s1 = ("沈刚学派 4 大分类是凹面诊断框架核心。建议使用扩弓装置 + 前牵装置。"
                   "通过影像量化分析 + 头侧位精读 + 面诊精测确认 sub-class。"
                   "成年阶段关节改建机会相对较少，但仍可保守治疗。"
                   "面诊时需要做的事情包括影像精读、咬合检查和颌位评估。"
                   "建议本月内来沪面诊确认。预期改善但骨性完全正常较难达到。") * 3
        r_good = check_output(good_s1, "1_patient", voice_mode="A_standard")
        print(f"[self-test] Scene 1 clean: pass={r_good['pass']}, {len(r_good['violations'])} violations")
        if not r_good["pass"]:
            for v in r_good["violations"]:
                detail = v.get("detail", v.get("message", ""))
                print(f"    - {v['rule_id']}: {detail}")
        assert r_good["pass"], "Scene 1 clean content should pass"

        print(f"[self-test] applied {len(r1['rules_applied'])} rules for Scene 1, {len(r3['rules_applied'])} for Scene 3")
        print("[self-test] passed")
        sys.exit(0)

    content = open(args.file).read() if args.file else sys.stdin.read()
    result = check_output(content, args.scene, voice_mode=args.voice_mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["pass"] else 2)
