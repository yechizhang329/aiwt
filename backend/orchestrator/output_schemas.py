"""JSON Schema definitions for v2 agent output validation (Task 3 Mode B).

Source: DentistWang structured_output_schemas/v1.0.md (2026-05-30)
v1.0.1 (2026-05-30): false-positive fixes + DW-approved deviations:
  - Stage A: TENTATIVE pattern scoped to axis 2/5 only (if/then)
  - Stage A: sufficiency_gaps.scope default="both" for tolerance
  - Stage B CM: removed required on status + top_5_cases[].case_id
  - Stage C: device_routing_canonical required fields removed
  - Stage D: verification_scope changed to string array (enum removed)
  - Stage D: critical_concerns required reduced; recommended_action → string
  - Stage D: cross_modal_check/kb_ghost_check/image_evidence_level_independent_re_verify/
             pipeline_violation_catch/cross_case_drift_log required sub-fields removed
  - Stage D: sc_claim_match expanded to 5-value enum (DW approved)
v1.1 (2026-05-31): P1+P2 schema reconcile (DW v1.3.4 sysprompt push + agent self-audit):
  - Stage B CM: add retrieval_mode enum field (pre_retrieved|corpus_fallback)
  - Stage C: add communication_boundary (content not action), risk_patterns_confirmed
  - Stage C: _DEVICE_ROUTING_ENTRY add actual SC emit fields (device_code/canonical_name/applicability)
  - Stage D: critical_concerns add type/title/description/scope (Critic actual emit fields)
  - Stage D: _CONCERN_TYPE_ENUM extend with 4 positive_* governance signal types
  - Stage D: cross_modal_check/voice_mode_consistency_check/cross_case_drift_log add actual Critic emit fields
  - Stage D: add overall_assessment field
v1.2 (2026-06-05): 批A schema scaffold — CL1/CL3/CL4 re-key structured fields
  (DW 続138/140/142 + KBadvisor 25ca1edf/5207634f + 太上老君 3adbfa38):
  - Stage C: direction_falsification.four_check → verdict enum (SUPPORTS_CONCAVE/REFUTES_CONCAVE/UNRESOLVED)
    per-anchor, DW-spec names: SNA_对颅底/上颌弓宽_腭穹/上唇_颏AP/鼻旁区 (absent→UNRESOLVED gate default)
  - Stage C: direction_falsification.concave_source enum {上颌源/下颌源/双源/未pin/null}
    (null=concave-not-affirmed; 未pin=affirmed-open → CL1 (ii) fire)
  - Stage C: skeletal_anchor_used → bool sub-fields sna_对颅底_read + snb_对颅底_read
    (absent→FALSE gate default; ceph_measured_directly derived from measurement_source by gate)
  - Stage C: severity_determination enum {LOCKED_FIRM/ESCALATED_FOR_ANCHOR/NOT_AT_ISSUE}
    (gate cross-check: rec_type=surgical OR severity_class∈{中度,重度_手术阈值} forces LOCKED_FIRM)
Flag-only — never blocks pipeline execution.
v1.3 (2026-06-07): V4 Phase I gate-stability scaffold.
  - Stage A may carry orchestrator/runtime-derived sagittal_consensus_packet.
  - Stage C may echo v4_gate_consistency_status / anchor_dispute.
  Optional + tolerant: Phase I is an orchestrator guard, not an agent rewrite.
v1.4 (2026-06-08): V4 Phase II source-attribution packet scaffold.
  - Additive/read-only source_attribution_packet carrier for SC/Critic/frontend.
  - Must not overwrite Phase I gate_result or suppress review-required prompts.
v1.5 (2026-06-08): Walter-rule non-measure sagittal anchor scaffold.
  - Adds split anterior/posterior crossbite, overjet depth, side-specific incisor
    compensation, molar sagittal tendency, paranasal/lip AP/chin/mandibular soft
    tissue/maxillary arch form anchors.
  - Additive only; deterministic gate behavior is changed in later P0 tasks.
v1.6 (2026-06-15): ConcaveIR extraction-blocker structured signals (DW 1edd7feb).
  - 3 clinical bools (5 fields: crossbite split anterior/posterior, reverse-overjet,
    molar mesial split left/right).
  - upper_incisor_retroclination removed (DW 1f101b97: retroclination is 混II marker,
    not concave — would false-fire on 15/40 混II cases).
  - Decoupled from ANB measurement; read by A4 extraction-blocker signal branch.
  - danger_flag removed as trigger per DW ruling (a): pure original clinical signs only.
"""

_CONFIDENCE_3WAY = {"enum": ["high", "medium", "low"]}
_VERIFY_STRENGTH = {"enum": ["STRONG", "MEDIUM", "WEAK", "NOT_FOUND", "N/A"]}

_V4_GATE_RESULT_ENUM = [
    "true_convex_closed",
    "maxillary_origin_masked_concave_review_required",
    "frank_concave_classIII_review_required",
    "unresolved_not_closeable_needs_review",
]

_V4_ANCHOR_ENTRY = {
    "type": "object",
    "required": ["value", "clarity", "confound", "evidence_ref", "role_note"],
    "properties": {
        "value": {"type": ["string", "null"]},
        "clarity": {"enum": ["clear", "suspicious", "unreadable", None]},
        "confound": {
            "enum": [
                "none", "missing_view", "photo_angle", "head_posture",
                "dental_crowding", "dental_compensation",
                "prior_extraction_retraction", "ceph_without_tracing",
                "soft_tissue_thickness", "mixed", None,
            ],
        },
        "evidence_ref": {"type": ["string", "null"]},
        "role_note": {"type": ["string", "null"]},
    },
    "additionalProperties": True,
}

_V4_SAGITTAL_SIDE_PACKET = {
    "type": "object",
    "properties": {
        "packet_role": {"type": "string"},
        "score": {"type": ["number", "integer", "null"]},
        "anchors": {
            "type": "object",
            "properties": {
                "overjet_sign": _V4_ANCHOR_ENTRY,
                "anterior_crossbite": _V4_ANCHOR_ENTRY,
                "posterior_crossbite": _V4_ANCHOR_ENTRY,
                "overjet_depth": _V4_ANCHOR_ENTRY,
                "skeletal_chin_ap": _V4_ANCHOR_ENTRY,
                "maxillary_support": _V4_ANCHOR_ENTRY,
                "paranasal_support": _V4_ANCHOR_ENTRY,
                "lip_ap_relation": _V4_ANCHOR_ENTRY,
                "chin_prominence": _V4_ANCHOR_ENTRY,
                "mandibular_soft_tissue_volume": _V4_ANCHOR_ENTRY,
                "arch_palate": _V4_ANCHOR_ENTRY,
                "maxillary_arch_form": _V4_ANCHOR_ENTRY,
                "incisor_compensation": _V4_ANCHOR_ENTRY,
                "upper_incisor_compensation": _V4_ANCHOR_ENTRY,
                "lower_incisor_compensation": _V4_ANCHOR_ENTRY,
                "molar_canine_relation": _V4_ANCHOR_ENTRY,
                "molar_relation_sagittal": _V4_ANCHOR_ENTRY,
                "prior_extraction_retraction": _V4_ANCHOR_ENTRY,
                "surface_protrusion_signal": _V4_ANCHOR_ENTRY,
                "closure_blockers": _V4_ANCHOR_ENTRY,
                "closure_status": _V4_ANCHOR_ENTRY,
            },
            "additionalProperties": True,
        },
        "supporting_evidence": {"type": "array"},
        "counter_evidence": {"type": "array"},
        "unresolved_anchors": {"type": "array", "items": {"type": "string"}},
        "extraction_blocker_signals": {
            "type": "object",
            "properties": {
                "has_anterior_crossbite": {"type": "boolean"},
                "has_posterior_crossbite": {"type": "boolean"},
                "has_reverse_overjet": {"type": "boolean"},
                "has_molar_mesial_left": {"type": "boolean"},
                "has_molar_mesial_right": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": True,
}

_V4_SAGITTAL_CONSENSUS_PACKET = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "gate_result": {"enum": _V4_GATE_RESULT_ENUM},
        "rule_trace": {"type": "array"},
        "routing_intent": {"type": "object"},
        "raw_packets": {
            "type": "object",
            "properties": {
                "convex": _V4_SAGITTAL_SIDE_PACKET,
                "concave": _V4_SAGITTAL_SIDE_PACKET,
                "non_sagittal": {"type": "object"},
            },
            "additionalProperties": True,
        },
        "observability": {"type": "object"},
    },
    "additionalProperties": True,
}

_V4_SOURCE_CANDIDATE_ENUM = [
    "upper_lower_source_candidate",
    "maxillary_primary_candidate",
    "mandibular_primary_candidate",
    "bimaxillary_candidate",
    "dental_compensation_dominant",
    "unresolved",
]

_V4_SOURCE_ATTRIBUTION_LEVEL_ENUM = ["review_candidate", "likely", "unresolved"]

_V4_SOURCE_ANCHOR = {
    "type": "object",
    "properties": {
        "anchor": {"type": "string"},
        "field": {"type": "string"},
        "value": {"type": "string"},
        "side": {"type": "string"},
        "role": {"enum": ["decisive", "supporting", "conflicting"]},
        "note": {"type": "string"},
    },
    "additionalProperties": True,
}

_V4_SOURCE_ATTRIBUTION_PACKET = {
    "type": "object",
    "required": [
        "schema_version",
        "derived_by",
        "source_candidate",
        "attribution_level",
        "confidence",
        "decisive_anchors",
        "supporting_anchors",
        "conflicting_anchors",
        "missing_required_anchors",
        "cannot_close_reason",
        "rule_trace",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "derived_by": {"enum": ["deterministic_orchestrator"]},
        "source_candidate": {"enum": _V4_SOURCE_CANDIDATE_ENUM},
        "attribution_level": {"enum": _V4_SOURCE_ATTRIBUTION_LEVEL_ENUM},
        "confidence": _CONFIDENCE_3WAY,
        "decisive_anchors": {"type": "array", "items": _V4_SOURCE_ANCHOR},
        "supporting_anchors": {"type": "array", "items": _V4_SOURCE_ANCHOR},
        "conflicting_anchors": {"type": "array", "items": _V4_SOURCE_ANCHOR},
        "missing_required_anchors": {"type": "array", "items": {"type": "object"}},
        "cannot_close_reason": {"type": "string"},
        "closure_state": {"type": ["string", "null"]},
        "rule_trace": {"type": "array"},
        "phase1_gate_result_read_only": {"enum": _V4_GATE_RESULT_ENUM},
    },
    "additionalProperties": True,
}

_V4_DIAGNOSIS_FIRST_PACKET = {
    "type": "object",
    "required": [
        "schema_version",
        "derived_by",
        "positive_chain_status",
        "minimum_positive_anchor_set",
        "missing_required_anchors",
        "rule_trace",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "derived_by": {"enum": ["deterministic_orchestrator"]},
        "fixture_id": {"type": ["string", "null"]},
        "card_context": {"type": "object"},
        "positive_chain_status": {"enum": ["active", "partial_missing_evidence", "not_applicable"]},
        "main_diagnosis_candidate": {"type": ["string", "null"]},
        "source_attribution_candidate": {"type": ["string", "null"]},
        "subtype_candidate": {"type": ["string", "null"]},
        "treatment_branch_candidate": {"type": ["string", "null"]},
        "closure_state": {"type": ["string", "null"]},
        "uncertainty_boundary": {"type": ["string", "null"]},
        "required_missing_evidence": {"type": "array", "items": {"type": "string"}},
        "minimum_positive_anchor_set": {"type": "object"},
        "missing_required_anchors": {"type": "array", "items": {"type": "string"}},
        "source_attribution_packet_read_only": {"type": "object"},
        "shengang_subtype_read_only": {"type": "object"},
        "rule_trace": {"type": "array"},
    },
    "additionalProperties": True,
}

_CONCERN_TYPE_ENUM = [
    "kb_anchor_fabrication", "kb_ghost_appliance", "cross_modal_mismatch",
    "sub_class_visual_lock_violation", "device_routing_canonical_violation",
    "voice_mode_anti_pattern", "deprecated_term_usage", "framework_drift",
    "R10_AND3_misread", "R11_layer3_overreach", "R15_forbidden_term_leak",
    "R16_citation_missing", "S18_patient_facing_leak", "S17_strict_violation",
    "image_anchor_binding_fabrication", "age_inferred_low_confidence_unflagged",
    "axis_1_visual_reverse_misdiagnose_risk",
    "axis_1_visual_only_revise_without_ceph_quant",
    "axis_1_alt_hypothesis_missed_by_clinician",
    "midline_3_subclass_differentiation_missing",
    "concave_family_history_followup_missing",
    # v1.1: positive governance signal types (Critic v1.3.4 self-audit msg=217435db)
    "positive_governance_signal",
    "positive_p_code_governance_production_validation",
    "positive_v1_3_2_CoVe_first_production_application",
    "positive_cycle_close_validation",
]

# ── Shared sub-schemas ─────────────────────────────────────────────────────────

_AXIS_LOCK_ENTRY = {
    "type": "object",
    "required": ["axis", "locked", "label", "anchor_source"],
    "properties": {
        "axis": {"type": "integer", "minimum": 1, "maximum": 6},
        "locked": {"type": "boolean"},
        "label": {"type": "string"},
        "anchor_source": {"type": "string"},
    },
}

_DEVICE_ROUTING_ENTRY = {
    "type": "object",
    "properties": {
        # SC v1.3.4 actual emit fields (reconciled; 5 production cases all empty array)
        "device_code": {"type": "string"},
        "canonical_name": {"type": "string"},
        "applicability": {"type": "string"},
        # Legacy schema-canonical names (v1.0; kept for tolerance)
        "device_class": {"type": "string"},
        "sub_class_anchor": {"type": "string"},
        "age_fit_check": {"type": "boolean"},
        "canonical_map_ref": {"type": "string"},
    },
}

_IMAGE_ANCHOR_ENTRY = {
    "type": "object",
    "required": ["claim_id", "section", "claim_excerpt", "image_ref", "region_tag", "axis_ref", "binding_type"],
    "properties": {
        "claim_id": {"type": "string", "pattern": "^C[0-9]+$"},
        "section": {
            "enum": [
                "s1_情况判读", "s1_临床推理",
                "s2_面诊重点", "s2_治疗路径",
                "s3_配合事项", "s3_3_school_compare",
                "s4_行动建议", "s4_要点提醒",
            ],
        },
        "claim_excerpt": {"type": "string"},
        "image_ref": {"type": ["string", "null"]},
        "region_tag": {"type": ["string", "null"]},
        "axis_ref": {"enum": ["axis_1", "axis_2", "axis_3", "axis_4", "axis_5", "axis_6", "none"]},
        "binding_type": {"enum": ["image_evidence", "text_claim_only"]},
    },
}

_VERIFICATION_CHAIN_ITEM = {
    "type": "object",
    "required": ["question", "independent_answer", "sc_claim_match"],
    "properties": {
        "question": {"type": "string"},
        "independent_answer": {"type": "string"},
        "sc_claim_match": {
            "enum": [
                "consistent",
                "consistent_with_minor_anchor_framing_nuance",
                "inconsistent_minor",
                "inconsistent_major",
                "unverifiable",
            ],
        },
    },
}

_VERIFICATION_QUESTIONS_FAILED_ITEM = {
    "type": "object",
    "required": ["question", "independent_answer", "sc_claim_answer"],
    "properties": {
        "question": {"type": "string"},
        "independent_answer": {"type": "string"},
        "sc_claim_answer": {"type": "string"},
    },
}

# ── Stage A — InitialReader ────────────────────────────────────────────────────

STAGE_A_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "msg_type", "case_id", "scene", "axes", "image_refs_per_axis",
        "targeted_query", "risk_patterns_hinted", "sufficiency_verdict",
        "sufficiency_gaps", "image_evidence_level", "reasoning_trace",
        "age_inferred_from_text",
    ],
    "properties": {
        "msg_type": {"const": "initial_reader_response"},
        "case_id": {"type": "string"},
        "scene": {"enum": ["1", "3"]},
        "axes": {
            "type": "array",
            "minItems": 6,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["axis", "name"],
                # R12: axis 2+5 candidate_list labels must contain TENTATIVE marker
                "if": {
                    "properties": {"axis": {"enum": [2, 5]}},
                    "required": ["axis"],
                },
                "then": {
                    "properties": {
                        "candidate_list": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "pattern": "TENTATIVE 待面诊精测",
                                    },
                                },
                            },
                        },
                    },
                },
                "properties": {
                    "axis": {"type": "integer", "minimum": 1, "maximum": 6},
                    "name": {
                        "enum": [
                            "面型主类", "sub-class candidates", "牙列",
                            "关节", "中线", "黄金期窗口",
                        ],
                    },
                    "lockable": {"type": "boolean"},
                    "value": {"type": ["string", "null"]},
                    "confidence": {"enum": ["high", "medium", "low", None]},
                    "candidate_list": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["label", "confidence"],
                            "properties": {
                                "label": {"type": "string"},
                                "confidence": _CONFIDENCE_3WAY,
                            },
                        },
                    },
                    "direction": {
                        "enum": [
                            "上颌左偏", "上颌右偏", "下颌左偏",
                            "下颌右偏", "正常", None,
                        ],
                    },
                    "subclass_candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["label", "confidence"],
                            "properties": {
                                "label": {"type": "string"},
                                "confidence": _CONFIDENCE_3WAY,
                            },
                        },
                    },
                    "and3_imaging_present": {"type": "boolean"},
                    "band": {"enum": ["黄金期内", "边缘", "已关", None]},
                },
            },
        },
        "image_refs_per_axis": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["axis", "image_ref", "region_tag"],
                "properties": {
                    "axis": {"type": "integer", "minimum": 1, "maximum": 6},
                    "image_ref": {"type": ["string", "null"]},
                    "region_tag": {"type": ["string", "null"]},
                },
            },
        },
        "targeted_query": {
            "type": "object",
            "required": ["kc_query", "cm_query", "family_history_followup"],
            "properties": {
                "kc_query": {"type": "string"},
                "cm_query": {"type": "string"},
                "family_history_followup": {"type": "boolean"},
            },
        },
        "risk_patterns_hinted": {"type": "array", "items": {"type": "string"}},
        "sufficiency_verdict": {"enum": ["PASS", "NEED_MORE"]},
        "sufficiency_gaps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "severity", "reason", "scope"],
                "properties": {
                    "field": {"type": "string"},
                    "severity": {"enum": ["blocker", "degradable_soft"]},
                    "reason": {"type": "string"},
                    "scope": {"enum": ["scene_1", "scene_3", "both"], "default": "both"},
                },
            },
        },
        "image_evidence_level": _CONFIDENCE_3WAY,
        "voice_mode_hint": {"enum": ["A", "B"]},
        "voice_mode_hint_trigger_refs": {
            "type": "array",
            "items": {
                "enum": [
                    "sufficiency_need_more_degraded_scene1",
                    "image_evidence_level_low",
                    "axis2_subclass_geq3_medium",
                    "axis4_AND3_HIGH",
                    "risk_patterns_p12_or_p19_hinted",
                    "axis6_window_closed_hard_constraint",
                ],
            },
        },
        "unanchored_finding_freetext": {"type": ["string", "null"]},
        "sagittal_consensus_packet": _V4_SAGITTAL_CONSENSUS_PACKET,
        "source_attribution_packet": _V4_SOURCE_ATTRIBUTION_PACKET,
        "diagnosis_first_packet": _V4_DIAGNOSIS_FIRST_PACKET,
        "reasoning_trace": {"type": "string"},
        "age_inferred_from_text": {"type": "boolean"},
        "inferred_age": {"type": ["integer", "null"]},
        "inferred_sex": {"enum": ["M", "F", None]},
        "inference_text_anchor": {"type": ["string", "null"]},
    },
    "additionalProperties": True,
}

# ── Stage B_KC — KnowledgeCurator ─────────────────────────────────────────────

STAGE_B_KC_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string"},
        "e_blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "content": {"type": "string"},
                    "confidence": _CONFIDENCE_3WAY,
                },
            },
        },
        "opposing_evidence": {"type": "array"},
        "kb_gaps": {"type": "array"},
        "kc_status": {"type": "string"},
    },
    "additionalProperties": True,
}

# ── Stage B_CM — CaseMemory ────────────────────────────────────────────────────

STAGE_B_CM_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "top_5_cases": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "confidence": {"type": ["number", "string"]},
                },
            },
        },
        "overall_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "and3_rating": {"type": "string"},
        # v1.1: retrieval mode signal (CM v1.3.4 — candidate_cases[] presence indicator)
        "retrieval_mode": {"enum": ["pre_retrieved", "corpus_fallback"]},
    },
    "additionalProperties": True,
}

# ── Stage C — SeniorClinician ──────────────────────────────────────────────────
# Validates common fields; scene-specific sections validated separately below.

_SC_SECTIONS_SCENE1 = {
    "type": "object",
    "required": ["s1_情况判读", "s2_面诊重点", "s3_配合事项", "s4_行动建议"],
    "properties": {
        "s1_情况判读": {"type": "string"},
        "s2_面诊重点": {"type": "string"},
        "s3_配合事项": {"type": "string"},
        "s4_行动建议": {"type": "string"},
    },
    "additionalProperties": False,
}

_SC_SECTIONS_SCENE3 = {
    "type": "object",
    "required": ["s1_临床推理", "s2_治疗路径", "s3_3_school_compare", "s4_要点提醒"],
    "properties": {
        "s1_临床推理": {"type": "string"},
        "s2_治疗路径": {"type": "string"},
        "s3_3_school_compare": {"type": "string"},
        "s4_要点提醒": {"type": "string"},
    },
    "additionalProperties": False,
}

STAGE_C_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["msg_type", "case_id", "scene", "voice_mode_applied", "sections", "confidence"],
    "properties": {
        "msg_type": {"const": "senior_clinician_response"},
        "case_id": {"type": "string"},
        "scene": {"enum": ["1", "3"]},
        "voice_mode_applied": {
            "enum": ["A_standard", "B_difficult_diagnosis_warning", "doctor_to_doctor"],
        },
        "voice_mode_escalation_triggers": {
            "type": "array",
            "items": {
                "enum": [
                    "stage_c_conf_lt_065", "stage_c_hrw_medium_flag",
                    "stage_c_axis2_geq3_medium_post_kb", "stage_c_device_routing_uncertain",
                    "stage_c_age_or_sex_missing_critical_clinical_decision",
                    "stage_c_axis_1_凹凸_reverse_ambiguity",
                ],
            },
        },
        # sections validated scene-specifically via _validate_sc_sections()
        "sections": {"type": "object", "minProperties": 4},
        "layer_2": {"type": ["object", "null"]},
        "rendered_markdown": {"type": "string"},
        "reasoning_trace": {"type": "string"},
        "axis_lock_status": {"type": "array", "items": _AXIS_LOCK_ENTRY},
        "device_routing_canonical": {"type": "array", "items": _DEVICE_ROUTING_ENTRY},
        "image_anchors": {"type": "array", "items": _IMAGE_ANCHOR_ENTRY},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "uncertainty_flags": {"type": "array", "items": {"type": "string"}},
        "学派_attribution_used": {
            "type": "array",
            "items": {"enum": ["沈刚学派", "王特派", "北大", "九院"]},
        },
        "char_count": {"type": "integer", "minimum": 0},
        # v1.1: SC v1.3.4 actual emit fields (reconciled from self-audit msg=359e2c71)
        "communication_boundary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "content": {"type": "string"},  # actual emit; schema had "action"
                },
            },
        },
        "risk_patterns_confirmed": {"type": "array", "items": {"type": "string"}},
        # P2-#3a 批A joint schema scaffold (DW fdf1cfe0 + 太上老君 e1c2da22):
        # SC emits these once sysprompt emit-tokens are deployed (DW authors sysprompt, WebAppDev owns schema).
        # CL3-broadened durable trigger reads these fields (guard discharges on skeletal_anchor_used).
        "recommendation_class": {
            "enum": ["拔牙", "正颌_手术", "可逆矫治", "观察监测", "未给"],
        },
        "skeletal_severity_class": {
            "enum": ["正常_轻", "中度", "重度_手术阈值", "未评估"],
        },
        # 批A v1.2: severity_determination replaces text-based CL3 trigger detection.
        # SC emits this field; gate cross-checks rec_type+severity_class tokens as override.
        # LOCKED_FIRM = firm severity/surgical lock; ESCALATED_FOR_ANCHOR = honest defer for measurement;
        # NOT_AT_ISSUE = coarse-direction case, severity not load-bearing.
        "severity_determination": {
            "enum": ["LOCKED_FIRM", "ESCALATED_FOR_ANCHOR", "NOT_AT_ISSUE"],
        },
        # skeletal_anchor_used: 批A v1.2 — sub-fields renamed to bool (absent→FALSE gate default).
        # Gate DERIVES skeletal_anchor_used = skeletal_anchor_measurement_valid AND sna AND snb.
        # skeletal_anchor_measurement_valid derived from direction_falsification.measurement_source.
        "skeletal_anchor_used": {
            "type": "object",
            "properties": {
                "sna_对颅底_read": {"type": "boolean"},
                "snb_对颅底_read": {"type": "boolean"},
                # Legacy keys kept for schema tolerance (additionalProperties=True):
                "sna_cranial_base": {"enum": ["present", "absent"]},
                "snb_cranial_base": {"enum": ["present", "absent"]},
            },
        },
        # v3 §13 direction_falsification — required-conditional when high-risk direction conclusion present.
        # null = no high-risk conclusion triggered. Missing/empty ruled_out_basis = fail-safe unfalsified.
        # 批A v1.2: four_check now verdict-enum per anchor; concave_source and sna/snb bool fields added.
        "direction_falsification": {
            "type": ["object", "null"],
            "properties": {
                "trigger_conclusion": {"type": "string"},
                # 批A v1.2: four_check per-anchor verdict enum (DW spec anchor names).
                # Gate maps absent → UNRESOLVED (fail-closed). Old free-text keys kept for schema tolerance.
                "four_check": {
                    "type": "object",
                    "properties": {
                        # DW-spec anchor names (批A v1.2):
                        "SNA_对颅底": {"enum": ["SUPPORTS_CONCAVE", "REFUTES_CONCAVE", "UNRESOLVED"]},
                        "上颌弓宽_腭穹": {"enum": ["SUPPORTS_CONCAVE", "REFUTES_CONCAVE", "UNRESOLVED"]},
                        "上唇_颏AP": {"enum": ["SUPPORTS_CONCAVE", "REFUTES_CONCAVE", "UNRESOLVED"]},
                        "鼻旁区": {"enum": ["SUPPORTS_CONCAVE", "REFUTES_CONCAVE", "UNRESOLVED"]},
                        # Legacy free-text keys (pre-批A, kept for schema tolerance):
                        "上颌弓宽": {"type": "string"},
                        "SNA_SNB_对颅底": {"type": "string"},
                    },
                },
                # 批A v1.2: concave_source enum.
                # null/absent = concave-not-affirmed (convex/non-concave case, no CL1 (ii) fire).
                # 未pin = concave-affirmed but source-attribution open → CL1 (ii) fire.
                "concave_source": {
                    "enum": ["上颌源", "下颌源", "双源", "未pin", None],
                },
                "concave_ruled_out": {"type": "boolean"},
                "skeletal_severity_ruled_light": {"type": "boolean"},
                "ruled_out_basis": {"type": "string"},
                "measurement_source": {
                    "enum": ["原片直接测(R18)", "报告引值", "视觉qualitative", "缺片"],
                },
            },
        },
        "v4_gate_consistency_status": {
            "type": "object",
            "properties": {
                "gate_result": {"enum": _V4_GATE_RESULT_ENUM},
                "status": {
                    "enum": [
                        "consistent",
                        "violation_clean_true_convex_against_review_gate",
                        "not_evaluated",
                    ],
                },
                "rule_trace": {"type": "array"},
            },
            "additionalProperties": True,
        },
        "anchor_dispute": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
        "source_attribution_packet": _V4_SOURCE_ATTRIBUTION_PACKET,
        "diagnosis_first_packet": _V4_DIAGNOSIS_FIRST_PACKET,
    },
    "additionalProperties": True,
}

# ── Stage D — Critic ───────────────────────────────────────────────────────────

STAGE_D_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "msg_type", "case_id", "scene", "claim_review", "critical_concerns",
        "cross_modal_check", "kb_ghost_check", "overall_disagreement_count",
        "confidence_in_clinician_output",
    ],
    "properties": {
        "msg_type": {"const": "critic_review_response"},
        "case_id": {"type": "string"},
        "scene": {"enum": ["1", "3"]},
        "claim_review": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim_id", "agree"],
                "properties": {
                    "claim_id": {"type": "string", "pattern": "^C[0-9]+$"},
                    "claim_text": {"type": "string"},
                    "section": {"type": "string"},
                    "verification_scope": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "agree": {"type": "boolean"},
                    "reason": {"type": "string"},
                    "alt_hypothesis": {"type": ["string", "null"]},
                    "kb_anchor_verified": _VERIFY_STRENGTH,
                    "anchor_refs": {"type": "array", "items": {"type": "string"}},
                    "axis_lock_discipline_verified": _VERIFY_STRENGTH,
                    "device_routing_canonical_verified": _VERIFY_STRENGTH,
                    "visual_finding_verified": _VERIFY_STRENGTH,
                    "image_anchor_binding_verified": _VERIFY_STRENGTH,
                    "image_anchor_refs": {"type": "array", "items": {"type": "string"}},
                    # Phase 5 v1.3.2 CoVe Step 2d
                    "verification_chain": {
                        "type": "array",
                        "items": _VERIFICATION_CHAIN_ITEM,
                    },
                },
            },
        },
        "critical_concerns": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["concern_id", "severity"],
                "properties": {
                    "concern_id": {"type": "string", "pattern": "^CC[0-9]+$"},
                    "severity": {"enum": ["HIGH", "MEDIUM", "LOW"]},
                    "concern_type": {"enum": _CONCERN_TYPE_ENUM},
                    "recommended_action": {"type": "string"},
                    "reason": {"type": "string"},
                    "anchor_refs": {"type": "array", "items": {"type": "string"}},
                    "section": {"type": "string"},
                    # v1.1: Critic v1.3.4 actual emit fields (reconciled msg=217435db)
                    "type": {"type": "string"},  # Critic historical field; v1.3.4 aligns to concern_type
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "scope": {"type": "string"},
                    # Phase 5 v1.3.2 CoVe — inconsistent_major triggers
                    "verification_questions_failed": {
                        "type": "array",
                        "items": _VERIFICATION_QUESTIONS_FAILED_ITEM,
                    },
                },
            },
        },
        "cross_modal_check": {
            "type": "object",
            "properties": {
                # Canonical schema fields
                "image_vs_complaint_consistency": {
                    "enum": ["consistent", "inconsistent", "partial"],
                },
                "image_vs_stage_a_axes_consistency": {
                    "enum": ["consistent", "inconsistent", "partial"],
                },
                "image_vs_stage_c_diagnosis_consistency": {
                    "enum": ["consistent", "inconsistent", "partial"],
                },
                "specific_concerns": {"type": "array", "items": {"type": "string"}},
                # v1.1: Critic v1.3.4 actual emit fields (reconciled msg=217435db)
                "image_refs_assessed": {"type": "array"},
                "findings": {"type": ["string", "array"]},
                "verification_result": {"type": "string"},
            },
        },
        "kb_ghost_check": {
            "type": "object",
            "properties": {
                "appliance_mentions_in_stage_c": {"type": "array", "items": {"type": "string"}},
                "all_verified_in_kb": {"type": "boolean"},
                "unverified_appliances": {"type": "array", "items": {"type": "string"}},
                "deprecated_term_detected": {"type": "array", "items": {"type": "string"}},
            },
        },
        "voice_mode_consistency_check": {
            "type": "object",
            "properties": {
                # Canonical schema fields
                "stage_a_hint_present": {"type": "boolean"},
                "stage_a_hint_value": {"enum": ["A", "B", None]},
                "stage_a_trigger_refs_valid": {"type": "boolean"},
                "stage_a_trigger_refs_fabricated": {"type": "array", "items": {"type": "string"}},
                "stage_a_missed_b_trigger": {"type": "array", "items": {"type": "string"}},
                "stage_c_voice_mode_applied": {
                    "enum": ["A_standard", "B_difficult_diagnosis_warning", "doctor_to_doctor"],
                },
                "stage_c_one_way_violation": {"type": "boolean"},
                "stage_c_escalation_triggers_valid": {"type": "boolean"},
                "stage_c_escalation_triggers_fabricated": {"type": "array", "items": {"type": "string"}},
                # v1.1: allows Critic v1.3.4 sub-fields (canonical via self-check 17)
                "voice_mode_hint": {"enum": ["A", "B", None]},
                "voice_mode_applied": {"type": "string"},
                "consistency": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "sufficiency_gaps_render_check": {
            "type": "object",
            "properties": {
                "scene_1_degraded_proceed_detected": {"type": "boolean"},
                "stage_a_gaps_for_section_3_rendering": {"type": "array", "items": {"type": "string"}},
                "stage_c_section_3_covers_all_gaps": {"type": "boolean"},
                "stage_c_section_3_missed_gaps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "image_evidence_level_independent_re_verify": {
            "type": "object",
            "properties": {
                "stage_a_value": _CONFIDENCE_3WAY,
                "critic_independent_value": _CONFIDENCE_3WAY,
                "agree": {"type": "boolean"},
                "reason_if_disagree": {"type": "string"},
            },
        },
        "pipeline_violation_catch": {
            "type": "object",
            "properties": {
                "blocker_reached_stage_c": {"type": "boolean"},
                "scene_3_degraded_proceed_detected": {"type": "boolean"},
            },
        },
        "cross_case_drift_log": {
            "type": "object",
            "properties": {
                # Canonical schema fields
                "drift_observed": {"type": "boolean"},
                "drift_class": {
                    "enum": [
                        "framework_5_flat_recurrence", "convenience_term_累積",
                        "kc_age_signal_misread", "device_code_hallucination_累積", None,
                    ],
                },
                "drift_description": {"type": "string"},
                "suggested_governance_action": {"type": ["string", "null"]},
                # v1.1: Critic v1.3.4 actual emit fields (reconciled msg=217435db)
                "this_case_governance_signals": {"type": "array"},
                "prior_drift_classes_status": {"type": "array"},
            },
        },
        "overall_disagreement_count": {"type": "integer", "minimum": 0},
        "confidence_in_clinician_output": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "kb_re_anchor_findings": {"type": "string"},  # optional; Critic v1.3.4 uses overall_assessment instead
        "reasoning_trace": {"type": "string"},        # optional; Critic v1.3.4 uses overall_assessment instead
        # v1.1: Critic v1.3.4 actual emit field (replaces kb_re_anchor_findings + reasoning_trace)
        "overall_assessment": {"type": "string"},
    },
    "additionalProperties": True,
}

# ── Registry ───────────────────────────────────────────────────────────────────

STAGE_SCHEMAS = {
    "stage_A_initial_reader": STAGE_A_SCHEMA,
    "stage_B_kc": STAGE_B_KC_SCHEMA,
    "stage_B_cm": STAGE_B_CM_SCHEMA,
    "stage_C_senior_clinician": STAGE_C_SCHEMA,
    "stage_D_critic": STAGE_D_SCHEMA,
}

# Scene-specific SC sections schema (used in _validate_stage_output for Stage C)
SC_SECTIONS_BY_SCENE = {
    "1": _SC_SECTIONS_SCENE1,
    "3": _SC_SECTIONS_SCENE3,
}
