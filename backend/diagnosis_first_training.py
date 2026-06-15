"""Runtime training payload for diagnosis-first positive KB use.

The clinical contract comes from DentistWang task #64. This module turns that
contract into a small auditable payload the orchestrator can attach to runtime
agent dispatches without changing clinical rules or doing case-specific card
retrieval.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from positive_diagnosis_cards import get_positive_diagnosis_card


TRAINING_VERSION = "v4_diagnosis_first_training.2"
FSP19_CARD_ID = "fsp19_false_protrusive_compensated_concave"

_STAGE_TARGETS: dict[str, list[str]] = {
    "stage_A_initial_reader": ["evidence_reader"],
    "stage_A_v4_ir_packet": ["evidence_reader"],
    "stage_B_kc": ["candidate_builder"],
    "stage_B_cm": ["differential_diagnoser"],
    "stage_C_senior_clinician": [
        "source_subtype_integrator",
        "treatment_mapper",
        "human_report",
    ],
    "stage_D_critic": ["safety_boundary"],
}

_RESPONSIBILITIES: dict[str, dict[str, Any]] = {
    "evidence_reader": {
        "must": [
            "observe clinical anchors only",
            "separate surface cues, compensation cues, Class III/concave cues, source cues, occlusal cues",
            "mark unreadable anchors as unreadable rather than closing diagnosis",
        ],
        "must_not": [
            "close final diagnosis from positive overjet, lip protrusion, crowding, or surface convexity",
            "treat positive overjet as ruling out compensated concave",
        ],
    },
    "candidate_builder": {
        "must": [
            "generate ordinary convex/bimaxillary protrusion and compensated Class III/concave candidates when cues conflict",
            "surface supporting and opposing evidence for both candidate families",
        ],
        "must_not": [
            "return only one candidate when surface and concave anchors conflict",
            "treat concave as warning-only instead of a positive candidate",
        ],
    },
    "differential_diagnoser": {
        "must": [
            "explain why the tempting surface-protrusive path is plausible",
            "explain why compensated concave fits better when positive anchors support it",
        ],
        "must_not": [
            "use 'cannot rule out' as the entire reasoning",
            "use warning count as the diagnostic comparison",
        ],
    },
    "source_subtype_integrator": {
        "must": [
            "derive source attribution as structured meaning",
            "derive ShenGang subtype as structured meaning",
            "keep formal measurement/Coben/occlusion boundary explicit",
        ],
        "must_not": [
            "emit source/subtype only as decorative prose",
            "downgrade Walter-confirmed FSP19 subtype to pure unresolved when only measurement magnitude is pending",
        ],
    },
    "treatment_mapper": {
        "must": [
            "map treatment from diagnosis/source/subtype",
            "for FSP19 include MSE or skeletal expansion, maxillary protraction, and orthodontic-orthognathic boundary",
        ],
        "must_not": [
            "make ordinary protrusion extraction/retraction the mainline for the FSP19 positive path",
            "hide MSE/protraction as a minor warning footnote",
        ],
    },
    "safety_boundary": {
        "must": [
            "check contradictions after the diagnosis chain is formed",
            "force non-pass for warning-only, wrong-treatment, or surface-cue closure outputs",
        ],
        "must_not": [
            "replace the diagnosis chain with a warning list",
            "make warning presence the success condition",
        ],
    },
    "human_report": {
        "must": [
            "lead with current diagnosis direction",
            "then give supporting evidence, misleading surface cues, source/subtype, uncertainty/next evidence, treatment implication",
        ],
        "must_not": [
            "lead with gate jargon",
            "output only needs-review language",
            "emit a treatment branch that contradicts diagnosis",
        ],
    },
}

_FSP19_EXPECTED = {
    "main_diagnosis": "compensated concave / skeletal Class III direction",
    "source_attribution": "upper_lower_source",
    "shengang_subtype": "lower_triangular_shallow",
    "treatment_branch_required": [
        "MSE_or_skeletal_expansion",
        "maxillary_protraction",
        "orthodontic_orthognathic_boundary",
    ],
    "uncertainty_boundary": [
        "formal ceph",
        "Coben",
        "occlusion",
        "CR-CO",
    ],
}

_NEGATIVE_EXAMPLES = {
    "warning_only_failure": {
        "failing_behavior": "review warning without positive diagnosis",
        "must_fail_because": "warning/review output alone is not diagnostic success",
    },
    "wrong_treatment_failure": {
        "failing_behavior": "concave risk mentioned but ordinary protrusion extraction/retraction is the mainline",
        "must_fail_because": "treatment contradicts adult concave branch",
    },
    "surface_cue_closure_failure": {
        "failing_behavior": "positive overjet + lip protrusion + crowding closes ordinary convex/bimax protrusion",
        "must_fail_because": "these cues can be compensation and cannot explain Class III/concave anchors",
    },
}

_BATCH_A_CARD_USE_INDEX = [
    {
        "card_id": FSP19_CARD_ID,
        "card_level": "L6_eval_covered_L7_slice",
        "specific_trigger_family": "compensated false-protrusive concave / upper-lower-source / triangular-shallow",
        "generic_non_trigger_tags": ["surface protrusion", "positive overjet", "crowding"],
        "boundary": "not generic crowding or positive-overjet closure",
    },
    {
        "card_id": "ml29f_surface_convex_upper_source_concave_review",
        "card_level": "L6_eval_covered",
        "specific_trigger_family": "surface-convex appearance with crossbite/mesial molar/maxillary-support/lower-lip anchors",
        "generic_non_trigger_tags": ["surface_convexity", "protrusion", "convexity"],
        "boundary": "review card; not exact subtype gold",
    },
    {
        "card_id": "tcfp16_treatment_realism_false_protrusive_concave",
        "card_level": "L6_eval_covered",
        "specific_trigger_family": "treatment-realism false-protrusive concave constraints",
        "generic_non_trigger_tags": ["false_protrusive", "concave", "compensated_class_III", "surface_convexity"],
        "boundary": "not fixed treatment protocol and not automatic FSP19 blocker",
    },
    {
        "card_id": "concave_asymmetry_dental_differential_correction",
        "card_level": "L6_eval_covered",
        "specific_trigger_family": "concave-asymmetry with dental differential correction anchors",
        "generic_non_trigger_tags": ["asymmetry", "Class_III", "dental_Class_III", "dental_midline", "concave"],
        "boundary": "not surgery-only default and not final skeletal class closure",
    },
    {
        "card_id": "skeletal_protrusive_triangular_vertical_sagittal_coupling",
        "card_level": "L6_eval_covered",
        "specific_trigger_family": "skeletal protrusive triangular + vertical-sagittal coupling anchors",
        "generic_non_trigger_tags": ["protrusion", "Class_II", "triangular", "vertical", "TMJ"],
        "boundary": "triangular modifier must not become concave subtype",
    },
    {
        "card_id": "retreatment_extraction_history_source_attribution",
        "card_level": "L6_eval_covered",
        "specific_trigger_family": "history-informed source/space/axis/compensation/treatment-realism anchors",
        "generic_non_trigger_tags": ["retreatment", "extraction_history", "missing_tooth", "prior_treatment"],
        "boundary": "history alone is not activation; no implant-first or repeat-extraction default",
    },
]

_BATCH_A_WORKFLOW_PROTOCOL = {
    "schema_version": "batch_a_card_use_protocol.1",
    "clinical_contract_task": "#101",
    "adaptability_matrix_task": "#100",
    "implementation_task": "#102",
    "activation_status_enum": [
        "active_supported",
        "candidate_needs_more_evidence",
        "negative_control_not_applicable",
        "no_card_match",
    ],
    "workflow_steps": [
        "case anchors",
        "candidate KB retrieval",
        "minimum positive anchor check",
        "differential contrast",
        "source/subtype integration",
        "treatment implication",
        "uncertainty/evidence gaps",
        "final answer or structured escalation",
    ],
    "normalized_output_required_fields": [
        "evidence_anchors",
        "card_retrieval_record",
        "candidate_diagnoses",
        "differential_contrast",
        "source_subtype_integration",
        "treatment_implication",
        "uncertainty_boundary",
        "safety_boundary_checks",
        "clinical_escalation_payload_if_any",
    ],
    "retrieval_rules": [
        "use explicit card/context/card_id or specific anchor tags only",
        "generic tag match is not card activation",
        "card retrieval is not card activation",
        "minimum positive anchor set must be checked before active use",
        "use default projection for runtime context",
        "non-default projection requires explicit reason",
    ],
    "new_case_flow": {
        "exact_match": "use default card projection after minimum anchor check",
        "partial_match": "candidate cards plus differential and missing anchors",
        "cross_card_match": "differential matrix; preserve each card boundary",
        "no_match": "candidate diagnosis plus evidence gap, review-required, and backlog/escalation payload",
    },
    "escalation_payload_fields": [
        "case_id",
        "observed_anchor_summary",
        "retrieved_cards_and_status",
        "differential_problem",
        "attempted_diagnosis_chain",
        "missing_evidence",
        "exact_question_for_DW",
        "old_kb_or_wiki_refs_if_any",
    ],
    "evaluator_habits": [
        "positive_pass",
        "warning_only_fail",
        "wrong_source_fail",
        "wrong_treatment_fail",
        "generic_non_trigger_control",
        "projection_hygiene",
        "sibling_regression",
        "no_card_new_case_smoke",
        "cross_card_conflict_fixture",
        "candidate_backlog_fixture",
    ],
}


def build_diagnosis_first_training_payload(stage_key: str) -> dict[str, Any]:
    """Return the auditable runtime training payload for one dispatch stage."""
    targets = _STAGE_TARGETS.get(stage_key, [])
    card = get_positive_diagnosis_card(FSP19_CARD_ID, requested_projection="default")
    return {
        "schema_version": TRAINING_VERSION,
        "clinical_contract_task": "#64",
        "implementation_task": "#65",
        "stage_key": stage_key,
        "stage_targets": targets,
        "shared_invariants": {
            "diagnosis_first": "derive the best positive diagnosis before applying safety boundary",
            "cards_are_reasoning_aids": "positive diagnosis cards support reasoning; they are not final answers by name",
            "explicit_activation_required": "a card is active only after its minimum positive anchor set is checked",
            "generic_tags_are_not_activation": "generic words cannot activate specific cards by themselves",
            "no_card_match_still_reacts": "if no card fits, continue diagnosis-first reasoning and mark evidence gap/escalation",
            "warning_not_success": "warning/review output alone is not diagnostic success",
            "surface_cues_not_closure": (
                "positive overjet, lip/incisor protrusion, crowding, or ConvexIR score cannot close ordinary convex"
            ),
            "uncertainty_is_specific": "state missing evidence and what it would change",
            "gold_not_diluted": "Walter-confirmed FSP19 gold is not pending-only just because measurements are still needed",
            "treatment_from_diagnosis": "treatment follows diagnosis/source/subtype",
        },
        "target_responsibilities": {target: deepcopy(_RESPONSIBILITIES[target]) for target in targets},
        "fsp19_positive_example": deepcopy(_FSP19_EXPECTED),
        "negative_examples_must_fail": deepcopy(_NEGATIVE_EXAMPLES),
        "positive_diagnosis_card_context": {
            "used": True,
            "card_id": card["card_id"],
            "projection": "default",
            "projection_reason": None,
            "usage_boundary": "training_example_and_anchor_check_only_not_case_closure",
            "minimum_positive_anchor_set_checked": True,
            "default_projection": card,
        },
        "batch_a_card_use_index": deepcopy(_BATCH_A_CARD_USE_INDEX),
        "batch_a_workflow_protocol": deepcopy(_BATCH_A_WORKFLOW_PROTOCOL),
        "normalized_outputs_required": [
            "main_diagnosis",
            "source_attribution",
            "shengang_subtype",
            "treatment_branch",
            "uncertainty_boundary",
            "safety_boundary",
            "card_retrieval_record",
            "clinical_escalation_payload_if_any",
        ],
        "auditability": {
            "stage_targets_produce_meaning": targets,
            "card_context_used": True,
            "non_default_projection_requires_reason": True,
        },
    }


def build_diagnosis_first_training_receipt() -> dict[str, Any]:
    """Small persistence receipt for final output/API metadata."""
    return {
        "schema_version": TRAINING_VERSION,
        "clinical_contract_task": "#64",
        "implementation_task": "#65",
        "positive_diagnosis_card": {
            "used": True,
            "card_id": FSP19_CARD_ID,
            "projection": "default",
            "minimum_positive_anchor_set_checked": True,
        },
        "stage_map": deepcopy(_STAGE_TARGETS),
        "negative_examples_must_fail": sorted(_NEGATIVE_EXAMPLES),
        "batch_a_workflow_protocol": {
            "clinical_contract_task": "#101",
            "adaptability_matrix_task": "#100",
            "implementation_task": "#102",
            "card_count": len(_BATCH_A_CARD_USE_INDEX),
            "activation_status_enum": deepcopy(_BATCH_A_WORKFLOW_PROTOCOL["activation_status_enum"]),
            "normalized_output_required_fields": deepcopy(_BATCH_A_WORKFLOW_PROTOCOL["normalized_output_required_fields"]),
            "evaluator_habits": deepcopy(_BATCH_A_WORKFLOW_PROTOCOL["evaluator_habits"]),
        },
    }
