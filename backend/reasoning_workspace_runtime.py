"""Runtime reasoning workspace carrier for #128.

The carrier is deterministic and additive. It turns existing V4 packets and
case payload into the #125 reasoning-loop state objects without changing intake
flow or relying on an LLM to obey a new schema on the first slice.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from artifacts.workflow_state_closure_only_contract import (
    NO_CLINICAL_CLOSURE_NOTE as WORKFLOW_STATE_NO_CLINICAL_CLOSURE_NOTE,
    canonical_or_review_required,
    review_required_clinical_axes,
)
from backend.reasoning_guards import get_reasoning_guard
from backend.reasoning_units import get_reasoning_unit


SCHEMA_VERSION = "clinical_reasoning_workspace_runtime.1"
TRAINING_VERSION = "reasoning_loop_runtime_training.1"
OUTPUT_PROJECTION_SCHEMA_VERSION = "reasoning_loop_output_projection.1"
PATIENT_PROJECTION_ALLOWED_KEYS = {
    "current_judgment",
    "status",
    "core_basis",
    "what_is_missing",
    "next_step",
    "uncertainty_explanation",
}
PROJECTION_FORBIDDEN_TERMS = (
    "raw_source",
    "source_provenance",
    "source path",
    "source_path",
    "fixture",
    "message_id",
    "thread_id",
    "Walter",
    "learning-log",
    "accepted_kb_spine_role_map",
    "_test_packets",
    "runtime_receipt",
)
PATIENT_FORBIDDEN_TERMS = PROJECTION_FORBIDDEN_TERMS + (
    "kb_unit",
    "unit_id",
    "card_id",
    "hypothesis_id",
    "doctor_trace",
    "kb_context_used",
    "finalization_boundary",
    "support_refute_trace",
    "hypothesis_transition_log",
    "weak_anchor_false_closure",
    "review_required_bypass",
    "source_leak",
    "必须",
    "强制",
    "SGTB",
    "MSE",
    "面具",
    "拔牙",
    "手术",
    "phase sequence",
)

REVIEW_REQUIRED_GATE_RESULTS = {
    "maxillary_origin_masked_concave_review_required",
    "frank_concave_classIII_review_required",
    "unresolved_not_closeable_needs_review",
}
KB_CONTEXT_ROLES = {
    "experience_fragment",
    "counterexample",
    "evidence_profile",
    "differential_prompt",
    "closure_criteria",
    "treatment_boundary",
    "individualization_variable",
    "support_refute_trace",
    "negative_control",
    "not_applicable",
    "source_review_backlog",
    "finalization_boundary_guard",
}
ACCEPTED_KB_SPINE_ROLE_MAP = {
    "FSP19": {
        "runtime_roles": ["evidence_profile", "closure_criteria", "counterexample", "treatment_boundary"],
        "influence_path": "may influence finalization only through current-case minimum anchors, source/subtype packets, and finalization_boundary whitelist",
    },
    "ML29F": {
        "runtime_roles": ["counterexample", "differential_prompt", "negative_control", "source_review_backlog"],
        "influence_path": "keeps surface-convex cases in differential/review unless current-case hard anchors support a source hypothesis",
    },
    "TCFP16": {
        "runtime_roles": ["treatment_boundary", "individualization_variable", "closure_criteria"],
        "influence_path": "may shape treatment branch only after current-case feasibility/risk variables are represented in treatment_personalization_state",
    },
    "CADDC": {
        "runtime_roles": ["differential_prompt", "counterexample", "evidence_profile"],
        "influence_path": "supports concave-vs-dental differential only through current-case dental/skeletal anchors and unresolved-conflict checks",
    },
    "SPT-VSC": {
        "runtime_roles": ["evidence_profile", "differential_prompt", "closure_criteria"],
        "influence_path": "supports vertical-sagittal coupling only through current-case sagittal/vertical packet evidence",
    },
    "RETX-EH": {
        "runtime_roles": ["individualization_variable", "source_review_backlog", "differential_prompt"],
        "influence_path": "prior treatment/extraction history affects source review and individualization only when observed in case input",
    },
    "TMD": {
        "runtime_roles": ["treatment_boundary", "individualization_variable", "negative_control", "source_review_backlog"],
        "influence_path": "active/stable TMJ/TMD signals force review_required before GS/orthodontic/surgical branch closure",
    },
    "protrusive_4type_sgtb_classification": {
        "runtime_roles": ["evidence_profile", "closure_criteria", "differential_prompt", "counterexample", "treatment_boundary"],
        "influence_path": "organizes protrusive SGTB candidate/support-refute reasoning only; treatment remains review_required and metric-dependent claims pass through measurement_closure_gate",
    },
    "transverse_expansion_growth_stage_device_boundary": {
        "runtime_roles": ["individualization_variable", "treatment_boundary", "closure_criteria", "negative_control", "evidence_profile"],
        "influence_path": "organizes transverse growth/suture/device-family eligibility support only; treatment remains review_required and metric-dependent claims pass through measurement_closure_gate",
    },
    "asymmetry_full_classification_functional_joint_bone": {
        "runtime_roles": ["differential_prompt", "evidence_profile", "negative_control", "closure_criteria", "treatment_boundary", "individualization_variable"],
        "influence_path": "organizes asymmetry contributor-family source differential only; treatment remains review_required and metric-dependent claims pass through measurement_closure_gate",
    },
    "jaw_position_reconstruction_occlusion_tmj_balance": {
        "runtime_roles": ["differential_prompt", "evidence_profile", "support_refute_trace", "closure_criteria", "treatment_boundary", "individualization_variable", "negative_control"],
        "influence_path": "organizes jaw-position / occlusion-TMJ balance support-refute reasoning only; treatment remains review_required and metric-dependent claims pass through measurement_closure_gate",
    },
    "concave_asymmetry_dental_functional_differential_runtime_deepening": {
        "runtime_roles": ["evidence_profile", "differential_prompt", "support_refute_trace", "closure_criteria", "counterexample", "treatment_boundary", "individualization_variable"],
        "influence_path": "deepens concave/asymmetry dental-functional differential support only; diagnosis/source/subtype/treatment remain review_required and metric-dependent claims pass through measurement_closure_gate",
    },
    "true_concave_false_protrusive_treatment_realism_generalization": {
        "runtime_roles": ["evidence_profile", "differential_prompt", "support_refute_trace", "closure_criteria", "counterexample", "treatment_boundary", "individualization_variable"],
        "influence_path": "generalizes true-concave/false-protrusive treatment-realism support only; blocks FSP19 overgeneralization and cannot close extraction/device/treatment branch",
    },
    "vertical_sagittal_coupling_mechanics_boundary": {
        "runtime_roles": ["treatment_boundary", "treatment_personalization", "support_refute", "evidence_gap", "counterexample", "individualization_variable", "doctor_trace"],
        "influence_path": "organizes vertical-sagittal mechanics fit/refute support only; treatment remains review_required and metric-dependent claims pass through measurement_closure_gate",
    },
    "retreatment_extraction_history_source_space_axis_reasoning": {
        "runtime_roles": ["evidence_profile", "differential_prompt", "support_refute", "treatment_personalization", "counterexample", "evidence_gap", "doctor_trace"],
        "influence_path": "prior treatment/extraction/missing-tooth history informs source-space-axis support only when current expression and relevance links exist; history is not current source fact",
    },
    "tmd_active_stable_dynamic_revise_persist_reasoning": {
        "runtime_roles": ["evidence_profile", "evidence_gap", "support_refute", "hypothesis_transition_log", "finalization_boundary", "treatment_boundary", "follow_up_owner", "doctor_trace"],
        "influence_path": "TMD active/stable evidence profile and revise/persist transition support only; active/progressive/uncertain keeps jaw-position and treatment finalization review_required",
    },
    "space_budget_second_molar_eruption_extraction_timing_reasoning": {
        "runtime_roles": ["treatment_boundary", "treatment_personalization", "evidence_gap", "support_refute", "counterexample", "doctor_trace"],
        "influence_path": "organizes 7s eruption/space-budget/extraction-timing evidence gaps and support only; extraction, anchorage, retraction amount, phase sequence, and final plan remain review_required",
    },
    "protrusive_jaw_position_mixed_subtype_differential_runtime_deepening": {
        "runtime_roles": ["differential_prompt", "evidence_profile", "support_refute", "counterexample", "source_subtype_boundary", "evidence_gap", "doctor_trace"],
        "influence_path": "deepens protrusive jaw-position mixed subtype differential support only; source/subtype/SGTB/S8/device/extraction/final plan remain review_required",
    },
    "airway_mouth_breathing_growth_etiology_timing_boundary": {
        "runtime_roles": ["etiology_candidate", "evidence_profile", "evidence_gap", "support_refute", "individualization_variable", "treatment_boundary", "follow_up_owner", "doctor_trace"],
        "influence_path": "airway/mouth-breathing timeline informs orthodontic growth/timing evidence gaps and follow-up owner only; medical diagnosis/treatment remains clinician/ENT/sleep owner",
    },
    "functional_advancement_extraction_staging_force_direction_boundary": {
        "runtime_roles": ["treatment_boundary", "treatment_personalization", "support_refute", "counterexample", "evidence_gap", "individualization_variable", "doctor_trace"],
        "influence_path": "organizes functional-advancement/extraction-staging force-direction support only; extraction, appliance, phase sequence, and final plan remain review_required",
    },
    "scene3_modality_reliability_evidence_seeking_boundary": {
        "runtime_roles": ["evidence_seeking", "evidence_profile", "support_refute", "negative_control", "projection_boundary", "doctor_trace"],
        "influence_path": "classifies Scene 3 modality confidence and confirmatory-modality needs only; low-confidence imaging cannot close pathology/TMD/asymmetry/source/treatment",
    },
    "ml29f_surface_convex_upper_source_concave_review": {
        "runtime_roles": ["candidate_recall", "differential_prompt", "evidence_profile", "support_refute", "evidence_gap", "counterexample", "source_subtype_boundary", "doctor_trace"],
        "influence_path": "surface-convex/lip-protrusive appearance plus clustered hard upper-source or concave-review anchors -> support-only review trigger; ordinary protrusion clean closure, exact source/subtype, and treatment branch remain review_required",
    },
    "visible_protrusion_youth_concave_ruleout_growth_window": {
        "runtime_roles": ["candidate_recall", "differential_prompt", "evidence_seeking", "evidence_gap", "support_refute", "source_subtype_boundary", "treatment_boundary", "doctor_trace"],
        "influence_path": "youth visible protrusion plus concave-ruleout/growth-window anchors -> evidence-seeking and differential support only; diagnosis, source/subtype, growth prediction, treatment timing, device, and final plan remain review_required",
    },
    "asymmetry_four_factor_plan_progression_expectation_boundary": {
        "runtime_roles": ["candidate_recall", "option_comparison", "expectation_boundary", "evidence_gap", "support_refute", "treatment_boundary", "follow_up_owner", "doctor_trace"],
        "influence_path": "asymmetry four-factor anchors organize option comparison, expectation boundary, records-needed, and follow-up owner only; source/subtype, surgery/extraction, jaw-position, and final plan remain review_required",
    },
    "lockbite_transverse_multimodal_convergence_evidence_seeking": {
        "runtime_roles": ["candidate_recall", "evidence_seeking", "modality_reliability", "evidence_gap", "support_refute", "treatment_boundary", "projection_boundary", "doctor_trace"],
        "influence_path": "lockbite/transverse multimodal convergence organizes evidence seeking and modality reliability only; transverse diagnosis, source/mm/device/treatment timing, unilateral mechanics, surgery, and final plan remain review_required",
    },
    "skeletal_protrusive_triangular_space_tmj_treatment_realism": {
        "runtime_roles": ["candidate_recall", "option_comparison", "treatment_realism", "contingency", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "clustered protrusive/Class II, triangular, vertical-space/restorative-space, and TMJ contingency anchors organize treatment-realism support only; diagnosis/source/subtype/extraction/implant/mesialization/intrusion/uprighting/staging/device/surgery/TMJ branch/final plan remain review_required",
    },
    "extraction_nonextraction_space_soft_tissue_tradeoff": {
        "runtime_roles": ["candidate_recall", "option_comparison", "space_budget", "soft_tissue_tradeoff", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes extraction/non-extraction space and soft-tissue tradeoff support only; extraction/IPR/distalization/expansion/retraction/final plan remain review_required and #20 is required for metric space/profile claims",
    },
    "adult_retx_periodontal_root_tmj_biologic_risk_boundary": {
        "runtime_roles": ["candidate_recall", "biologic_risk_boundary", "periodontal_root_review", "tmj_contingency", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes adult retreatment biologic-risk boundaries only; age/root/periodontal/TMJ labels cannot close no-treatment, safe-treatment, movement load, stabilization, or final plan",
    },
    "orthognathic_camouflage_severity_expectation_boundary": {
        "runtime_roles": ["candidate_recall", "option_comparison", "expectation_boundary", "severity_boundary", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes orthognathic-vs-camouflage severity and expectation boundary support only; surgery/camouflage/no-surgery/profile success/final plan remain review_required",
    },
    "deepbite_spee_tmj_compensation_bite_opening_boundary": {
        "runtime_roles": ["candidate_recall", "bite_opening_boundary", "tmj_compensation_review", "growth_stage_boundary", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes deepbite/Spee/TMJ compensation and bite-opening support only; TMJ pathology, S8/jaw-position reconstruction, intrusion/extrusion/device/phase/final plan remain review_required",
    },
    "openbite_etiology_vertical_control_treatment_boundary": {
        "runtime_roles": ["candidate_recall", "etiology_boundary", "vertical_control_boundary", "function_airway_review", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes openbite etiology/source-family and vertical-control support only; posterior intrusion, habit/airway/condylar source, extrusion/intrusion/device/final plan remain review_required",
    },
    "case_maturity_framework_selection_boundary": {
        "runtime_roles": ["candidate_recall", "case_stage_boundary", "downstream_unit_gating", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes case maturity and framework-selection support only; diagnosis, mechanics, plan approval, retention success, and new treatment plan remain review_required",
    },
    "active_treatment_mechanics_stall_boundary": {
        "runtime_roles": ["candidate_recall", "active_mechanics_boundary", "appliance_stage_review", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes active-treatment stall evidence and records-needed review only; force, aligner change, elastics/attachments, intrusion/extrusion, extraction/IPR, and final mechanics remain review_required",
    },
    "digital_setup_animation_mechanics_audit_boundary": {
        "runtime_roles": ["candidate_recall", "setup_animation_audit", "anchorage_step_size_review", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes digital setup animation audit support only; setup approval, step-size safety, anchorage, appliance choice, extraction position, and final plan remain review_required",
    },
    "retention_relapse_stability_monitoring_boundary": {
        "runtime_roles": ["candidate_recall", "retention_monitoring", "relapse_signal_review", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes retention/relapse monitoring support only; retainer schedule, relapse diagnosis, causality, stability success, disease closure, and new treatment plan remain review_required",
    },
    "tooth_size_bolton_fusion_midline_finishing_boundary": {
        "runtime_roles": ["candidate_recall", "finishing_detail_review", "bolton_midline_boundary", "evidence_gap", "support_refute", "treatment_boundary", "doctor_trace"],
        "influence_path": "organizes tooth-size/Bolton/abnormal-tooth/midline finishing detail support only; source, diagnosis, IPR/restorative, extraction, limited-ortho candidacy, final occlusion, and completion remain review_required",
    },
}
HIGH_RISK_REVIEW_TERMS = (
    "TMD",
    "TMJ",
    "关节",
    "疼痛",
    "张口受限",
    "active_stage",
    "active stage",
    "手术",
    "正颌",
    "吸收",
)
MEASUREMENT_GUARD_ID = "ceph_quant_source_closure_discipline"
SGTB_UNIT_ID = "protrusive_4type_sgtb_classification"
TRANSVERSE_UNIT_ID = "transverse_expansion_growth_stage_device_boundary"
ASYMMETRY_UNIT_ID = "asymmetry_full_classification_functional_joint_bone"
JAW_POSITION_UNIT_ID = "jaw_position_reconstruction_occlusion_tmj_balance"
CONCAVE_ASYMMETRY_UNIT_ID = "concave_asymmetry_dental_functional_differential_runtime_deepening"
TRUE_CONCAVE_UNIT_ID = "true_concave_false_protrusive_treatment_realism_generalization"
VERTICAL_SAGITTAL_UNIT_ID = "vertical_sagittal_coupling_mechanics_boundary"
RETX_SOURCE_SPACE_UNIT_ID = "retreatment_extraction_history_source_space_axis_reasoning"
TMD_REVISE_PERSIST_UNIT_ID = "tmd_active_stable_dynamic_revise_persist_reasoning"
SPACE_BUDGET_UNIT_ID = "space_budget_second_molar_eruption_extraction_timing_reasoning"
PROTRUSIVE_JAW_POSITION_UNIT_ID = "protrusive_jaw_position_mixed_subtype_differential_runtime_deepening"
AIRWAY_TIMING_UNIT_ID = "airway_mouth_breathing_growth_etiology_timing_boundary"
FUNCTIONAL_ADVANCEMENT_UNIT_ID = "functional_advancement_extraction_staging_force_direction_boundary"
SCENE3_MODALITY_UNIT_ID = "scene3_modality_reliability_evidence_seeking_boundary"
ML29F_SURFACE_CONVEX_UNIT_ID = "ml29f_surface_convex_upper_source_concave_review"
VISIBLE_PROTRUSION_GROWTH_UNIT_ID = "visible_protrusion_youth_concave_ruleout_growth_window"
ASYMMETRY_FOUR_FACTOR_UNIT_ID = "asymmetry_four_factor_plan_progression_expectation_boundary"
LOCKBITE_MULTIMODAL_UNIT_ID = "lockbite_transverse_multimodal_convergence_evidence_seeking"
SKELETAL_PROTRUSIVE_REALISM_UNIT_ID = "skeletal_protrusive_triangular_space_tmj_treatment_realism"
EXTRACTION_SPACE_TRADEOFF_UNIT_ID = "extraction_nonextraction_space_soft_tissue_tradeoff"
ADULT_RETX_BIOLOGIC_RISK_UNIT_ID = "adult_retx_periodontal_root_tmj_biologic_risk_boundary"
ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID = "orthognathic_camouflage_severity_expectation_boundary"
DEEPBITE_BITE_OPENING_UNIT_ID = "deepbite_spee_tmj_compensation_bite_opening_boundary"
OPENBITE_VERTICAL_CONTROL_UNIT_ID = "openbite_etiology_vertical_control_treatment_boundary"
CASE_MATURITY_UNIT_ID = "case_maturity_framework_selection_boundary"
ACTIVE_MECHANICS_STALL_UNIT_ID = "active_treatment_mechanics_stall_boundary"
DIGITAL_SETUP_AUDIT_UNIT_ID = "digital_setup_animation_mechanics_audit_boundary"
RETENTION_RELAPSE_UNIT_ID = "retention_relapse_stability_monitoring_boundary"
TOOTH_SIZE_FINISHING_UNIT_ID = "tooth_size_bolton_fusion_midline_finishing_boundary"
SECOND_BATCH_UNIT_IDS = (
    CONCAVE_ASYMMETRY_UNIT_ID,
    TRUE_CONCAVE_UNIT_ID,
    VERTICAL_SAGITTAL_UNIT_ID,
    RETX_SOURCE_SPACE_UNIT_ID,
    TMD_REVISE_PERSIST_UNIT_ID,
)
THIRD_BATCH_UNIT_IDS = (
    SPACE_BUDGET_UNIT_ID,
    PROTRUSIVE_JAW_POSITION_UNIT_ID,
    AIRWAY_TIMING_UNIT_ID,
    FUNCTIONAL_ADVANCEMENT_UNIT_ID,
    SCENE3_MODALITY_UNIT_ID,
)
FOURTH_BATCH_UNIT_IDS = (
    ML29F_SURFACE_CONVEX_UNIT_ID,
    VISIBLE_PROTRUSION_GROWTH_UNIT_ID,
    LOCKBITE_MULTIMODAL_UNIT_ID,
    ASYMMETRY_FOUR_FACTOR_UNIT_ID,
    SKELETAL_PROTRUSIVE_REALISM_UNIT_ID,
)
FIFTH_BATCH_UNIT_IDS = (
    EXTRACTION_SPACE_TRADEOFF_UNIT_ID,
    ADULT_RETX_BIOLOGIC_RISK_UNIT_ID,
    ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID,
    DEEPBITE_BITE_OPENING_UNIT_ID,
    OPENBITE_VERTICAL_CONTROL_UNIT_ID,
)
SIXTH_BATCH_UNIT_IDS = (
    CASE_MATURITY_UNIT_ID,
    ACTIVE_MECHANICS_STALL_UNIT_ID,
    DIGITAL_SETUP_AUDIT_UNIT_ID,
    RETENTION_RELAPSE_UNIT_ID,
    TOOTH_SIZE_FINISHING_UNIT_ID,
)
HIGH_CONFIDENCE_SOURCE_TYPES = {
    "pdf_text_layer_with_context",
    "treating_doctor_quantitative_confirmation",
    "ceph_software_report",
    "ct_cbct_formal_quantitative_report",
}
LOW_CONFIDENCE_SOURCE_TYPES = {
    "visual_estimate",
    "visual_estimate_mm_or_degree",
    "photo_guess",
    "ppt_photo_scanned_image_guess",
    "scanned_image_guess",
    "copied_number_without_source_context_confidence",
    "dental_relation_label_alone",
    "dental_relation",
}
VISUAL_SOURCE_TYPES = {
    "visual_estimate",
    "visual_estimate_mm_or_degree",
    "photo_guess",
    "ppt_photo_scanned_image_guess",
    "scanned_image_guess",
}
DENTAL_RELATION_SOURCE_TYPES = {"dental_relation", "dental_relation_label_alone", "molar_class_label"}
LOW_CONFIDENCE_VALUES = {"low", "unknown", "unverified", "missing", "none", ""}
STRICT_QUANT_METRICS = {
    "SNA",
    "SNB",
    "ANB",
    "Wits",
    "Coben_Ptm_A",
    "Coben_Ba_A",
    "Coben_Go_Pog",
    "Coben_Ba_Pog",
    "FMA",
    "FMIA",
    "SN_MP",
    "SN-GoGn",
    "SN-MP",
    "face_height_ratio",
    "lower_face_height_ratio",
    "U1_NA",
    "U1_L1",
    "transverse_width_mm_difference",
    "maxillary_transverse_width_deficit",
    "arch_width_difference",
    "exact_CVMI_stage",
    "midpalatal_suture_maturity",
    "buccal_bone_width",
    "Me_deviation",
    "occlusal_cant_angle",
    "ramus_height_difference",
    "condyle_height_difference",
    "Go_Me_difference",
    "CR_CO_discrepancy",
    "overbite",
    "overjet",
    "Spee_curve_depth",
    "occlusal_plane_angle",
    "condyle_fossa_joint_space",
    "tmj_joint_space",
    "airway_numeric_branch_support",
    "vertical_sagittal_numeric_branch_support",
    "incisor_display",
    "gummy_smile_mm",
    "posterior_intrusion_mm",
    "posterior_extrusion_mm",
    "mandibular_rotation_degrees",
    "space_mm",
    "axis_angle",
    "root_metric",
    "periodontal_metric",
    "crowding_amount",
    "expansion_gain",
    "eruption_status_confidence",
    "U1_SN",
    "incisor_axis",
    "arch_width",
    "mandibular_rotation",
    "growth_stage_timing",
    "impaction_inclination_severity",
    "molar_relation_metric",
    "canine_relation_metric",
    "PA_canting_measurement",
    "modality_quantitative_claim",
}
REQUIRED_MEASUREMENT_METADATA = {
    "metric_name",
    "value",
    "unit",
    "source_type",
    "confidence",
    "extraction_context",
    "current_case_link",
}
CRITICAL_TREATMENT_TERMS = ("surgery", "orthognathic", "正颌", "MSE", "前牵", "expansion", "protraction")


def build_reasoning_loop_training_payload(stage_key: str) -> dict[str, Any]:
    """Small runtime payload telling agents the reasoning-loop contract."""
    return {
        "schema_version": TRAINING_VERSION,
        "stage_key": stage_key,
        "source_tasks": ["#119", "#120", "#124", "#125", "#128"],
        "workflow": [
            "observe_case",
            "broad_candidate_recall",
            "preliminary_hypotheses",
            "kb_context_retrieval",
            "support_refute_evidence_seeking",
            "revise_persist_or_split",
            "individualize",
            "personalize_treatment_options",
            "finalize_or_review_required",
        ],
        "required_runtime_objects": _required_objects(),
        "closure_axes": ["close_diagnosis", "close_source_or_subtype", "close_treatment_branch"],
        "hard_fail_controls": [
            "candidate/preliminary/supported-but-unclosed state as final diagnosis",
            "card/KB context promoted to observed case fact",
            "free-text/prose-only closure",
            "weak/generic anchors closing diagnosis/source/subtype/treatment",
            "diagnosis/card label-only treatment",
            "revise/persist without structured reason",
            "final conclusion consuming non-whitelisted state",
            "review_required bypass for high-risk/surgery/TMD-active/conflicting/unclear-source cases",
            "private/source/provenance leakage",
        ],
    }


def build_reasoning_loop_training_receipt() -> dict[str, Any]:
    return {
        "schema_version": TRAINING_VERSION,
        "implementation_task": "#128",
        "runtime_workspace_schema": SCHEMA_VERSION,
        "required_runtime_objects": _required_objects(),
        "doctor_patient_projection_split": True,
        "finalization_boundary": "closed_or_review_required_only",
    }


def build_reasoning_output_projection(workspace: dict[str, Any]) -> dict[str, Any]:
    """Return the default-safe output projection for doctor/patient consumers."""
    return {
        "schema_version": OUTPUT_PROJECTION_SCHEMA_VERSION,
        "projection_receipt": {
            "derived_by": "deterministic_orchestrator",
            "source_workspace_schema": workspace.get("schema_version"),
            "internal_runtime_state_retained_for": ["storage", "debug", "audit", "compatibility"],
            "api_default_consumer_field": "v4_reasoning_output_projection.patient_reasoning_projection",
            "raw_workspace_is_default_projection": False,
            "full_doctor_trace_is_default_projection": False,
            "no_llm_cost": True,
        },
        "internal_runtime_state": {
            "schema_version": workspace.get("schema_version"),
            "storage_debug_audit_only": True,
            "default_api_projection": False,
            "raw_workspace_field": "v4_reasoning_workspace",
            "full_doctor_trace_field": "v4_reasoning_doctor_trace",
        },
        "doctor_reasoning_projection": _doctor_reasoning_projection(workspace),
        "patient_reasoning_projection": _patient_reasoning_projection(workspace),
    }


def build_runtime_reasoning_workspace(
    *,
    case_id: str,
    scene: str,
    case_payload: dict[str, Any],
    v4_gate_packet: dict[str, Any],
    v4_source_packet: dict[str, Any],
    v4_shengang_packet: dict[str, Any],
    v4_treatment_advisory: dict[str, Any] | None,
    v4_diagnosis_first_packet: dict[str, Any],
) -> dict[str, Any]:
    """Return runtime workspace plus doctor/patient projections."""
    gate_result = v4_gate_packet.get("gate_result")
    high_risk = _high_risk_review_required(case_payload, v4_gate_packet, v4_treatment_advisory)
    unclear_source = _source_unclear(v4_source_packet)
    conflicts = bool(v4_source_packet.get("conflicting_anchors") or v4_gate_packet.get("blocking_reasons"))
    diagnosis_active = v4_diagnosis_first_packet.get("positive_chain_status") == "active"
    true_convex_gate = gate_result == "true_convex_closed" and not high_risk
    true_convex_closed = true_convex_gate
    review_required = high_risk or unclear_source or (conflicts and not true_convex_gate) or gate_result in REVIEW_REQUIRED_GATE_RESULTS

    close_diagnosis = "closed" if (true_convex_closed and not review_required) else "review_required"
    close_source = "review_required"
    if true_convex_closed and not review_required:
        close_source = "closed"
    close_treatment = "review_required"
    measurement_gate = _measurement_closure_gate(
        case_payload,
        close_diagnosis=close_diagnosis,
        close_source=close_source,
        close_treatment=close_treatment,
    )
    sgtb_unit = _sgtb_reasoning_unit(case_payload, measurement_gate)
    transverse_unit = _transverse_expansion_unit(case_payload, measurement_gate)
    asymmetry_unit = _asymmetry_reasoning_unit(case_payload, measurement_gate)
    jaw_position_unit = _jaw_position_reasoning_unit(case_payload, measurement_gate)
    second_batch_units = _second_batch_reasoning_units(case_payload, measurement_gate)
    third_batch_units = _third_batch_reasoning_units(case_payload, measurement_gate)
    fourth_batch_units = _fourth_batch_reasoning_units(case_payload, measurement_gate)
    fifth_batch_units = _fifth_batch_reasoning_units(case_payload, measurement_gate)
    sixth_batch_units = _sixth_batch_reasoning_units(case_payload, measurement_gate)
    if measurement_gate["status"] == "fail":
        if measurement_gate["effect_on_closure"]["close_diagnosis"] == "block":
            close_diagnosis = "review_required"
        if measurement_gate["effect_on_closure"]["close_source_or_subtype"] == "block":
            close_source = "review_required"
        if measurement_gate["effect_on_closure"]["close_treatment_branch"] == "block":
            close_treatment = "review_required"

    hypothesis_id = "runtime_hypothesis_1"
    hypothesis_label = _hypothesis_label(
        gate_result, v4_source_packet, v4_shengang_packet, v4_diagnosis_first_packet
    )
    support = _supporting_evidence(v4_source_packet, v4_shengang_packet, v4_diagnosis_first_packet)
    refute = _refuting_evidence(v4_gate_packet, v4_source_packet, review_required=review_required)
    missing = _missing_evidence(v4_source_packet, v4_shengang_packet, v4_diagnosis_first_packet)
    support.extend(sgtb_unit.get("supporting_anchors") or [])
    refute.extend(sgtb_unit.get("refuting_anchors") or [])
    missing.extend(sgtb_unit.get("missing_evidence") or [])
    support.extend(transverse_unit.get("supporting_anchors") or [])
    refute.extend(transverse_unit.get("refuting_anchors") or [])
    missing.extend(transverse_unit.get("missing_evidence") or [])
    support.extend(asymmetry_unit.get("supporting_anchors") or [])
    refute.extend(asymmetry_unit.get("refuting_anchors") or [])
    missing.extend(asymmetry_unit.get("missing_evidence") or [])
    support.extend(jaw_position_unit.get("supporting_anchors") or [])
    refute.extend(jaw_position_unit.get("refuting_anchors") or [])
    missing.extend(jaw_position_unit.get("missing_evidence") or [])
    for unit in second_batch_units.values():
        support.extend(unit.get("supporting_anchors") or [])
        refute.extend(unit.get("refuting_anchors") or [])
        missing.extend(unit.get("missing_evidence") or [])
    for unit in third_batch_units.values():
        support.extend(unit.get("supporting_anchors") or [])
        refute.extend(unit.get("refuting_anchors") or [])
        missing.extend(unit.get("missing_evidence") or [])
    for unit in fourth_batch_units.values():
        support.extend(unit.get("supporting_anchors") or [])
        refute.extend(unit.get("refuting_anchors") or [])
        missing.extend(unit.get("missing_evidence") or [])
    for unit in fifth_batch_units.values():
        support.extend(unit.get("supporting_anchors") or [])
        refute.extend(unit.get("refuting_anchors") or [])
        missing.extend(unit.get("missing_evidence") or [])
    for unit in sixth_batch_units.values():
        support.extend(unit.get("supporting_anchors") or [])
        refute.extend(unit.get("refuting_anchors") or [])
        missing.extend(unit.get("missing_evidence") or [])
    why_card_not_applicable = None
    persist_reason = None
    card_id = (v4_diagnosis_first_packet.get("card_context") or {}).get("card_id")
    if card_id and not diagnosis_active:
        why_card_not_applicable = "related card is context only because minimum anchors or treatment/source anchors are incomplete"
    if diagnosis_active:
        persist_reason = "minimum positive anchor set and treatment-boundary anchors are structurally present"
    elif close_diagnosis == "closed":
        persist_reason = "current-case runtime packets meet clean closure criteria without high-risk/conflict review triggers"

    workspace = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "scene": scene,
        "case_observation_state": _case_observation_state(case_payload, v4_gate_packet),
        "candidate_state": _candidate_state(v4_gate_packet, v4_source_packet, v4_diagnosis_first_packet),
        "hypothesis_state": [{
            "hypothesis_id": hypothesis_id,
            "hypothesis_label": hypothesis_label,
            "status": close_diagnosis,
            "basis_observations": _basis_observations(v4_gate_packet, v4_source_packet, v4_shengang_packet, v4_diagnosis_first_packet),
            "confidence": "high" if close_diagnosis == "closed" else "moderate",
            "major_uncertainties": missing,
            "what_would_change_the_judgment": _what_would_change(missing),
        }],
        "kb_context_used": _kb_context_used(
            v4_diagnosis_first_packet,
            why_card_not_applicable=why_card_not_applicable,
            persist_reason=persist_reason,
            measurement_gate=measurement_gate,
            sgtb_unit=sgtb_unit,
            transverse_unit=transverse_unit,
            asymmetry_unit=asymmetry_unit,
            jaw_position_unit=jaw_position_unit,
            second_batch_units=second_batch_units,
            third_batch_units=third_batch_units,
            fourth_batch_units=fourth_batch_units,
            fifth_batch_units=fifth_batch_units,
            sixth_batch_units=sixth_batch_units,
        ),
        "evidence_seeking_state": [{
            "hypothesis_id": hypothesis_id,
            "evidence_needed_to_support": missing[:4] or ["records confirming stable closure"],
            "evidence_needed_to_refute": refute[:4] or ["opposite-source or high-risk anchors"],
            "minimum_evidence_profile": "sufficient_for_diagnosis_closure" if close_diagnosis == "closed" else "sufficient_for_candidate",
            "next_data_request": missing[:4] or ["routine diagnostic records"],
        }],
        "support_refute_trace": [{
            "hypothesis_id": hypothesis_id,
            "supporting_evidence": support,
            "refuting_evidence": refute,
            "unresolved_conflicts": missing if close_diagnosis != "closed" else [],
            "negative_controls_checked": _negative_controls(v4_gate_packet, v4_diagnosis_first_packet),
        }],
        "hypothesis_transition_log": [{
            "hypothesis_id": hypothesis_id,
            "from_status": "preliminary",
            "to_status": close_diagnosis,
            "action": "persist" if close_diagnosis == "closed" or diagnosis_active else "manual_review",
            "allowed_transition": close_diagnosis in {"closed", "review_required"},
            "transition_reason_field_present": True,
            "persist_reason": persist_reason,
            "change_reason": None if persist_reason else "review_required gate or missing/conflicting/high-risk anchors block closure",
        }],
        "individualization_state": _individualization_state(case_payload, high_risk=high_risk),
        "treatment_personalization_state": _treatment_personalization_state(
            v4_treatment_advisory,
            hypothesis_label=hypothesis_label,
            closure_status=close_treatment,
            high_risk=high_risk,
            sgtb_unit=sgtb_unit,
            transverse_unit=transverse_unit,
            asymmetry_unit=asymmetry_unit,
            jaw_position_unit=jaw_position_unit,
            second_batch_units=second_batch_units,
            third_batch_units=third_batch_units,
            fourth_batch_units=fourth_batch_units,
            fifth_batch_units=fifth_batch_units,
            sixth_batch_units=sixth_batch_units,
        ),
        "finalization_boundary": _finalization_boundary(
            hypothesis_id, close_diagnosis, close_source, close_treatment, measurement_gate, sgtb_unit, transverse_unit, asymmetry_unit, jaw_position_unit, second_batch_units, third_batch_units, fourth_batch_units, fifth_batch_units, sixth_batch_units
        ),
        "final_conclusion": _final_conclusion(
            hypothesis_id, hypothesis_label, close_diagnosis, close_source, close_treatment,
            support, missing, high_risk,
        ),
        "closure_axes": {
            "close_diagnosis": close_diagnosis,
            "close_source_or_subtype": close_source,
            "close_treatment_branch": close_treatment,
        },
        "runtime_receipt": {
            "schema_version": "reasoning_loop_runtime_receipt.1",
            "derived_by": "deterministic_orchestrator",
            "state_objects_separated": True,
            "finalization_boundary_applied": True,
            "doctor_patient_projection_split": True,
            "high_risk_review_required": high_risk,
            "review_required": review_required,
            "measurement_closure_gate_status": measurement_gate["status"],
            "sgtb_reasoning_status": sgtb_unit["status"],
            "transverse_expansion_status": transverse_unit["status"],
            "asymmetry_reasoning_status": asymmetry_unit["status"],
            "jaw_position_reasoning_status": jaw_position_unit["status"],
            "second_batch_reasoning_status": {
                unit_id: unit["status"] for unit_id, unit in second_batch_units.items()
            },
            "third_batch_reasoning_status": {
                unit_id: unit["status"] for unit_id, unit in third_batch_units.items()
            },
            "fourth_batch_reasoning_status": {
                unit_id: unit["status"] for unit_id, unit in fourth_batch_units.items()
            },
            "fifth_batch_reasoning_status": {
                unit_id: unit["status"] for unit_id, unit in fifth_batch_units.items()
            },
            "sixth_batch_reasoning_status": {
                unit_id: unit["status"] for unit_id, unit in sixth_batch_units.items()
            },
        },
        "accepted_kb_spine_role_map": _accepted_kb_spine_role_map(),
    }
    workflow_state_metadata = _workflow_state_metadata(
        case_payload,
        conflicts=conflicts,
        missing_evidence=missing,
        why_card_not_applicable=why_card_not_applicable,
    )
    doctor_trace = _doctor_trace(workspace, workflow_state_metadata=workflow_state_metadata)
    patient_summary = _patient_summary(workspace)
    workspace["doctor_trace"] = doctor_trace
    workspace["patient_summary"] = patient_summary
    return workspace


def _doctor_reasoning_projection(workspace: dict[str, Any]) -> dict[str, Any]:
    final = workspace.get("final_conclusion") or {}
    return {
        "schema_version": "doctor_reasoning_projection.1",
        "projection_level": "doctor_admin_reasoning_projection",
        "closure_axes": deepcopy(workspace.get("closure_axes") or {}),
        "current_best_judgment": final.get("current_best_judgment"),
        "closure_status": final.get("closure_status"),
        "knowns": _safe_text_list(final.get("knowns") or [], limit=6),
        "unknowns": _safe_text_list(final.get("unknowns") or [], limit=6),
        "what_would_change_the_plan": _safe_text_list(final.get("what_would_change_the_plan") or [], limit=4),
        "review_or_followup_owner": _safe_text(final.get("review_or_followup_owner")),
        "evidence_gaps": _doctor_evidence_gaps(workspace),
        "support_refute_summary": _doctor_support_refute_summary(workspace),
        "treatment_personalization_support": _doctor_treatment_support(workspace),
        "unit_role_boundaries": _doctor_unit_role_boundaries(workspace),
        "projection_boundary": {
            "source_safe": True,
            "support_only_not_final": True,
            "patient_default_projection": False,
            "provenance_excluded": True,
        },
    }


def _patient_reasoning_projection(workspace: dict[str, Any]) -> dict[str, Any]:
    final = workspace.get("final_conclusion") or {}
    review_required = final.get("closure_status") == "review_required"
    missing = _safe_text_list(final.get("unknowns") or [], limit=4)
    if not missing:
        missing = ["医生复核关键资料"]
    projection = {
        "current_judgment": "需要医生复核后确认" if review_required else "当前资料支持结构化判断",
        "status": "需要医生确认" if review_required else "结构化判断已形成",
        "core_basis": [
            "系统已整理当前资料中的主要线索",
            "结果仍以医生复核为准",
        ] if review_required else [
            "当前资料线索较一致",
            "仍建议由医生确认最终表达",
        ],
        "what_is_missing": missing,
        "next_step": "请医生结合病历、影像和测量结果复核",
        "uncertainty_explanation": (
            "部分证据仍需医生确认，因此只作为复核提示，不作为结论或方案。"
            if review_required else
            "该结果为结构化辅助输出，最终解释仍以医生判断为准。"
        ),
    }
    return {key: projection[key] for key in sorted(PATIENT_PROJECTION_ALLOWED_KEYS)}


def _doctor_evidence_gaps(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in workspace.get("evidence_seeking_state") or []:
        out.append({
            "evidence_needed_to_support": _safe_text_list(item.get("evidence_needed_to_support") or [], limit=4),
            "evidence_needed_to_refute": _safe_text_list(item.get("evidence_needed_to_refute") or [], limit=4),
            "next_data_request": _safe_text_list(item.get("next_data_request") or [], limit=4),
        })
    return out


def _doctor_support_refute_summary(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in workspace.get("support_refute_trace") or []:
        out.append({
            "supporting_evidence": _safe_text_list(item.get("supporting_evidence") or [], limit=8),
            "refuting_evidence": _safe_text_list(item.get("refuting_evidence") or [], limit=8),
            "unresolved_conflicts": _safe_text_list(item.get("unresolved_conflicts") or [], limit=8),
            "negative_controls_checked": _safe_text_list(item.get("negative_controls_checked") or [], limit=8),
        })
    return out


def _doctor_treatment_support(workspace: dict[str, Any]) -> dict[str, Any]:
    treatment = deepcopy(workspace.get("treatment_personalization_state") or {})
    for key in ("second_batch_reasoning_support", "third_batch_reasoning_support", "fourth_batch_reasoning_support"):
        if isinstance(treatment.get(key), dict):
            for support in treatment[key].values():
                if isinstance(support, dict):
                    support["may_enter_final_conclusion"] = False
                    support["support_only"] = True
    treatment["support_only_not_final_treatment_plan"] = True
    return _sanitize_for_projection(treatment)


def _doctor_unit_role_boundaries(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    boundaries = []
    role_map_by_unit = {
        item.get("kb_unit"): item
        for item in workspace.get("accepted_kb_spine_role_map") or []
        if item.get("kb_unit")
    }
    for context in workspace.get("kb_context_used") or []:
        unit_id = context.get("context_id")
        spec = role_map_by_unit.get(unit_id) or {}
        boundaries.append({
            "unit_id": unit_id,
            "title": _safe_text(str(unit_id or "reasoning_unit").replace("_", " ")),
            "role": context.get("role") or (spec.get("runtime_roles") or ["support"])[0],
            "runtime_roles": list(spec.get("runtime_roles") or []),
            "projection_level": context.get("projection_level") or spec.get("projection_level") or "ordinary",
            "source_sensitivity": context.get("source_sensitivity") or spec.get("source_sensitivity") or "deidentified_runtime_reference",
            "may_enter_final_conclusion": False,
            "current_case_influence_path": _safe_text(
                context.get("current_case_influence_path") or spec.get("current_case_influence_path")
            ),
            "treatment_branch_effect": "review_required_or_eligibility_support_only",
            "review_required_reason": _safe_text(
                context.get("why_card_not_applicable")
                or context.get("persist_reason")
                or "support-only reasoning boundary; clinician review remains required where closure is not independently whitelisted"
            ),
        })
    return boundaries


def _safe_text_list(values: list[Any], *, limit: int) -> list[str]:
    return [_safe_text(value) for value in values[:limit] if _safe_text(value)]


def _safe_text(value: Any) -> str:
    text = str(value or "")
    for term in PROJECTION_FORBIDDEN_TERMS:
        text = text.replace(term, "[redacted]")
    return text


def _sanitize_for_projection(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_for_projection(item) for key, item in value.items() if key not in {"raw_source_text", "source_provenance", "source_path"}}
    if isinstance(value, list):
        return [_sanitize_for_projection(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _required_objects() -> list[str]:
    return [
        "case_observation_state",
        "candidate_state",
        "hypothesis_state",
        "kb_context_used",
        "evidence_seeking_state",
        "support_refute_trace",
        "hypothesis_transition_log",
        "individualization_state",
        "treatment_personalization_state",
        "finalization_boundary",
        "final_conclusion",
    ]


def _accepted_kb_spine_role_map() -> list[dict[str, Any]]:
    role_map = [
        {
            "kb_unit": unit,
            "runtime_roles": list(spec["runtime_roles"]),
            "projection_level": "ordinary",
            "source_sensitivity": "deidentified_runtime_reference",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": spec["influence_path"],
            "backlog_boundary": "old or immature KB remains candidate/evidence-gap/source_review_backlog until accepted clinical contract and evaluator coverage exist",
        }
        for unit, spec in ACCEPTED_KB_SPINE_ROLE_MAP.items()
    ]
    role_map.append({
        "kb_unit": MEASUREMENT_GUARD_ID,
        "runtime_roles": ["finalization_boundary_guard"],
        "projection_level": "ordinary",
        "source_sensitivity": "deidentified_protocol_no_patient_default_projection",
        "may_enter_final_conclusion": False,
        "promoted_to_observed_fact": False,
        "current_case_influence_path": "current-case measurement metadata -> measurement_closure_gate -> finalization boundary",
        "backlog_boundary": "guard cannot create observed measurements and cannot close from protocol alone",
    })
    return role_map


def _high_risk_review_required(
    case_payload: dict[str, Any],
    gate_packet: dict[str, Any],
    treatment_advisory: dict[str, Any] | None,
) -> bool:
    text = repr(case_payload) + repr(gate_packet) + repr(treatment_advisory or {})
    return any(term in text for term in HIGH_RISK_REVIEW_TERMS)


def _source_unclear(source_packet: dict[str, Any]) -> bool:
    return source_packet.get("source_candidate") in {None, "unresolved"} or source_packet.get("attribution_level") in {None, "unresolved", "review_candidate"}


def _case_text(case_payload: dict[str, Any]) -> str:
    return " ".join(
        str(case_payload.get(key) or "")
        for key in (
            "chief_complaint",
            "chief_complaint_patient",
            "chief_complaint_doctor",
            "doctor_specific_question",
            "context_notes",
            "prior_treatment_history",
            "workflow_context",
        )
    ).lower()


def _hypothesis_label(
    gate_result: str | None,
    source_packet: dict[str, Any],
    subtype_packet: dict[str, Any],
    diagnosis_packet: dict[str, Any],
) -> str:
    if diagnosis_packet.get("positive_chain_status") == "active":
        return diagnosis_packet.get("main_diagnosis_candidate") or "active diagnosis-first hypothesis"
    if gate_result == "true_convex_closed":
        return "true convex/protrusive direction"
    source = source_packet.get("source_candidate") or "unresolved_source"
    subtype = subtype_packet.get("upper_lower_subtype") or "unresolved_subtype"
    return f"review-required sagittal/source hypothesis: {source} / {subtype}"


def _case_observation_state(case_payload: dict[str, Any], gate_packet: dict[str, Any]) -> dict[str, Any]:
    facts = []
    for key in (
        "chief_complaint",
        "chief_complaint_patient",
        "chief_complaint_doctor",
        "doctor_specific_question",
        "context_notes",
        "patient_age",
        "age",
        "prior_treatment_history",
    ):
        value = case_payload.get(key)
        if value:
            facts.append({"fact_id": key, "label": str(value), "source": "case_input"})
    if gate_packet.get("gate_result"):
        facts.append({"fact_id": "v4_gate_result", "label": gate_packet["gate_result"], "source": "runtime_packet"})
    for item in _measurement_evidence_items(case_payload):
        metric = item.get("metric_name")
        source_type = item.get("source_type")
        confidence = item.get("confidence")
        if metric:
            facts.append({
                "fact_id": f"measurement_metadata:{metric}",
                "label": f"{metric} source={source_type or 'missing'} confidence={confidence or 'missing'}",
                "source": "current_case_measurement_metadata",
            })
    sgtb_evidence = _sgtb_evidence(case_payload)
    if sgtb_evidence:
        facts.append({
            "fact_id": "sgtb_reasoning_metadata",
            "label": f"subtype_candidate={sgtb_evidence.get('subtype_candidate') or 'unresolved'}",
            "source": "current_case_reasoning_metadata",
        })
    transverse_evidence = _transverse_evidence(case_payload)
    if transverse_evidence:
        facts.append({
            "fact_id": "transverse_expansion_reasoning_metadata",
            "label": f"device_family_candidate={transverse_evidence.get('device_family_candidate') or 'unresolved'}",
            "source": "current_case_reasoning_metadata",
        })
    asymmetry_evidence = _asymmetry_evidence(case_payload)
    if asymmetry_evidence:
        facts.append({
            "fact_id": "asymmetry_functional_joint_bone_reasoning_metadata",
            "label": f"contributor_family_candidate={asymmetry_evidence.get('contributor_family_candidate') or 'unresolved'}",
            "source": "current_case_reasoning_metadata",
        })
    jaw_position_evidence = _jaw_position_evidence(case_payload)
    if jaw_position_evidence:
        facts.append({
            "fact_id": "jaw_position_occlusion_tmj_balance_reasoning_metadata",
            "label": f"balance_candidate={jaw_position_evidence.get('balance_candidate') or jaw_position_evidence.get('candidate') or 'unresolved'}",
            "source": "current_case_reasoning_metadata",
        })
    for unit_id, keys in (
        (CONCAVE_ASYMMETRY_UNIT_ID, ("concave_asymmetry_differential_evidence", CONCAVE_ASYMMETRY_UNIT_ID)),
        (TRUE_CONCAVE_UNIT_ID, ("true_concave_false_protrusive_evidence", TRUE_CONCAVE_UNIT_ID)),
        (VERTICAL_SAGITTAL_UNIT_ID, ("vertical_sagittal_evidence", VERTICAL_SAGITTAL_UNIT_ID)),
        (RETX_SOURCE_SPACE_UNIT_ID, ("retreatment_extraction_history_evidence", RETX_SOURCE_SPACE_UNIT_ID)),
        (TMD_REVISE_PERSIST_UNIT_ID, ("tmd_revise_persist_evidence", TMD_REVISE_PERSIST_UNIT_ID)),
        (SPACE_BUDGET_UNIT_ID, ("space_budget_evidence", SPACE_BUDGET_UNIT_ID)),
        (PROTRUSIVE_JAW_POSITION_UNIT_ID, ("protrusive_jaw_position_mixed_subtype_evidence", PROTRUSIVE_JAW_POSITION_UNIT_ID)),
        (AIRWAY_TIMING_UNIT_ID, ("airway_mouth_breathing_evidence", AIRWAY_TIMING_UNIT_ID)),
        (FUNCTIONAL_ADVANCEMENT_UNIT_ID, ("functional_advancement_staging_evidence", FUNCTIONAL_ADVANCEMENT_UNIT_ID)),
        (SCENE3_MODALITY_UNIT_ID, ("scene3_modality_evidence", SCENE3_MODALITY_UNIT_ID)),
        (ML29F_SURFACE_CONVEX_UNIT_ID, ("ml29f_surface_convex_upper_source_evidence", ML29F_SURFACE_CONVEX_UNIT_ID)),
        (VISIBLE_PROTRUSION_GROWTH_UNIT_ID, ("visible_protrusion_youth_growth_window_evidence", VISIBLE_PROTRUSION_GROWTH_UNIT_ID)),
        (LOCKBITE_MULTIMODAL_UNIT_ID, ("lockbite_transverse_multimodal_evidence", LOCKBITE_MULTIMODAL_UNIT_ID)),
        (ASYMMETRY_FOUR_FACTOR_UNIT_ID, ("asymmetry_four_factor_plan_progression_evidence", ASYMMETRY_FOUR_FACTOR_UNIT_ID)),
        (SKELETAL_PROTRUSIVE_REALISM_UNIT_ID, ("skeletal_protrusive_triangular_space_tmj_evidence", SKELETAL_PROTRUSIVE_REALISM_UNIT_ID)),
        (EXTRACTION_SPACE_TRADEOFF_UNIT_ID, ("extraction_nonextraction_space_soft_tissue_evidence", EXTRACTION_SPACE_TRADEOFF_UNIT_ID)),
        (ADULT_RETX_BIOLOGIC_RISK_UNIT_ID, ("adult_retx_periodontal_root_tmj_biologic_risk_evidence", ADULT_RETX_BIOLOGIC_RISK_UNIT_ID)),
        (ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID, ("orthognathic_camouflage_severity_expectation_evidence", ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID)),
        (DEEPBITE_BITE_OPENING_UNIT_ID, ("deepbite_spee_tmj_compensation_bite_opening_evidence", DEEPBITE_BITE_OPENING_UNIT_ID)),
        (OPENBITE_VERTICAL_CONTROL_UNIT_ID, ("openbite_etiology_vertical_control_evidence", OPENBITE_VERTICAL_CONTROL_UNIT_ID)),
        (CASE_MATURITY_UNIT_ID, ("case_maturity_framework_selection_evidence", CASE_MATURITY_UNIT_ID)),
        (ACTIVE_MECHANICS_STALL_UNIT_ID, ("active_treatment_mechanics_stall_evidence", ACTIVE_MECHANICS_STALL_UNIT_ID)),
        (DIGITAL_SETUP_AUDIT_UNIT_ID, ("digital_setup_animation_mechanics_audit_evidence", DIGITAL_SETUP_AUDIT_UNIT_ID)),
        (RETENTION_RELAPSE_UNIT_ID, ("retention_relapse_stability_monitoring_evidence", RETENTION_RELAPSE_UNIT_ID)),
        (TOOTH_SIZE_FINISHING_UNIT_ID, ("tooth_size_bolton_fusion_midline_finishing_evidence", TOOTH_SIZE_FINISHING_UNIT_ID)),
    ):
        evidence = _unit_evidence(case_payload, *keys)
        if evidence:
            facts.append({
                "fact_id": f"{unit_id}_reasoning_metadata",
                "label": f"support_candidate={evidence.get('candidate') or evidence.get('support_candidate') or evidence.get('profile') or 'unresolved'}",
                "source": "current_case_reasoning_metadata",
            })
    return {"schema_version": "case_observation_state.1", "observed_case_facts": facts}


def _candidate_state(gate_packet: dict[str, Any], source_packet: dict[str, Any], diagnosis_packet: dict[str, Any]) -> dict[str, Any]:
    card_id = (diagnosis_packet.get("card_context") or {}).get("card_id")
    active = diagnosis_packet.get("positive_chain_status") == "active"
    return {
        "schema_version": "runtime_candidate_state.1",
        "status": "candidate_only" if not active else "card_activated",
        "candidate_problem_families": [{
            "family_id": "runtime_sagittal_reasoning_family",
            "candidate_problem_family": _hypothesis_label(gate_packet.get("gate_result"), source_packet, {}, diagnosis_packet),
            "candidate_cards_or_backlog": [{
                "ref_id": card_id or "reasoning_workspace_backlog",
                "ref_type": "positive_diagnosis_card" if card_id else "backlog",
                "relation": "activated_with_minimum_anchor_check" if active else "possible_related_not_activated",
                "card_context_loaded": active,
            }],
            "closure_anchors_missing": diagnosis_packet.get("missing_required_anchors") or [],
            "negative_controls_to_check": _negative_controls(gate_packet, diagnosis_packet),
        }],
        "positive_card_context_used": active,
        "activation_boundary": "card can activate only after minimum positive anchor set is checked",
    }


def _kb_context_used(
    diagnosis_packet: dict[str, Any],
    *,
    why_card_not_applicable: str | None,
    persist_reason: str | None,
    measurement_gate: dict[str, Any],
    sgtb_unit: dict[str, Any],
    transverse_unit: dict[str, Any],
    asymmetry_unit: dict[str, Any],
    jaw_position_unit: dict[str, Any],
    second_batch_units: dict[str, dict[str, Any]],
    third_batch_units: dict[str, dict[str, Any]],
    fourth_batch_units: dict[str, dict[str, Any]],
    fifth_batch_units: dict[str, dict[str, Any]],
    sixth_batch_units: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    card_id = (diagnosis_packet.get("card_context") or {}).get("card_id")
    contexts = []
    if card_id:
        contexts.append({
            "context_id": card_id or "runtime_differential_context",
            "role": "closure_criteria",
            "projection_level": "ordinary",
            "source_sensitivity": "deidentified_runtime_reference",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "why_card_not_applicable": why_card_not_applicable,
            "persist_reason": persist_reason,
            "current_case_influence_path": "current-case evidence -> hypothesis/support-refute state -> finalization_boundary whitelist",
        })
    else:
        contexts.append({
            "context_id": "runtime_differential_context",
            "role": "differential_prompt",
            "projection_level": "ordinary",
            "source_sensitivity": "runtime_backlog",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "why_card_not_applicable": why_card_not_applicable or "no accepted card met minimum current-case anchors",
            "persist_reason": persist_reason,
            "current_case_influence_path": "candidate/evidence-gap only until current-case anchors support a closed or review_required hypothesis",
        })
    contexts.extend([
        {
            "context_id": "runtime_negative_controls",
            "role": "negative_control",
            "projection_level": "ordinary",
            "source_sensitivity": "runtime_rule",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": "blocks false closure when current-case evidence is weak, conflicting, or high-risk",
        },
        {
            "context_id": MEASUREMENT_GUARD_ID,
            "role": "finalization_boundary_guard",
            "projection_level": "ordinary",
            "source_sensitivity": "deidentified_protocol_no_patient_default_projection",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": "current-case measurement source/confidence metadata -> measurement_closure_gate -> closure-axis allow/block/review",
            "guard_status": measurement_gate.get("status"),
            "guard_effect": deepcopy(measurement_gate.get("effect_on_closure")),
        },
        {
            "context_id": "accepted_kb_spine_runtime_map",
            "role": "source_review_backlog",
            "projection_level": "ordinary",
            "source_sensitivity": "deidentified_runtime_reference",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": "spine items are role maps; each must pass through current-case evidence and finalization boundary before affecting conclusion",
            "accepted_kb_units": list(ACCEPTED_KB_SPINE_ROLE_MAP),
        },
    ])
    contexts.append({
        "context_id": SGTB_UNIT_ID,
        "role": "differential_prompt",
        "projection_level": "ordinary",
        "source_sensitivity": "deidentified_teaching_kb_default_projection",
        "may_enter_final_conclusion": False,
        "promoted_to_observed_fact": False,
        "current_case_influence_path": "current-case protrusive/source/jaw-position/TMJ/morphology anchors -> candidate/support-refute/evidence-gap only; treatment remains review_required",
        "unit_status": sgtb_unit.get("status"),
        "subtype_candidate": sgtb_unit.get("subtype_candidate"),
        "treatment_boundary": "review_required_or_eligibility_support_only",
    })
    if transverse_unit.get("status") != "not_applicable":
        contexts.append({
            "context_id": TRANSVERSE_UNIT_ID,
            "role": "treatment_boundary",
            "projection_level": "ordinary",
            "source_sensitivity": "mixed_teaching_public_walter_correction_kb_default_deidentified_summary",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": "current-case transverse/growth/suture/width/risk/personalization anchors -> eligibility_support/evidence_gap only; treatment remains review_required",
            "unit_status": transverse_unit.get("status"),
            "device_family_candidate": transverse_unit.get("device_family_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    if asymmetry_unit.get("status") != "not_applicable":
        contexts.append({
            "context_id": ASYMMETRY_UNIT_ID,
            "role": "differential_prompt",
            "projection_level": "ordinary",
            "source_sensitivity": "mixed_teaching_walter_correction_case_learning_kb_default_deidentified_summary",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": "current-case asymmetry/CR-CO/PA-CBCT/TMJ/dental/transverse anchors -> source-family eligibility/refutation support only; treatment remains review_required",
            "unit_status": asymmetry_unit.get("status"),
            "contributor_family_candidate": asymmetry_unit.get("contributor_family_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    if jaw_position_unit.get("status") != "not_applicable":
        contexts.append({
            "context_id": JAW_POSITION_UNIT_ID,
            "role": "support_refute_trace",
            "projection_level": "ordinary",
            "source_sensitivity": "mixed_teaching_product_system_walter_literature_kb_default_deidentified_summary",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": "current-case CR-CO/deprogramming/occlusion/TMJ/condyle-fossa anchors -> review-required eligibility/refutation support only; treatment remains review_required",
            "unit_status": jaw_position_unit.get("status"),
            "balance_candidate": jaw_position_unit.get("balance_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    for unit_id, unit in second_batch_units.items():
        if unit.get("status") == "not_applicable":
            continue
        contexts.append({
            "context_id": unit_id,
            "role": unit.get("context_role") or "evidence_profile",
            "projection_level": "ordinary",
            "source_sensitivity": unit.get("source_sensitivity") or "learning_log_derived_unresolved_raw_source_default_deidentified_summary",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": unit.get("current_case_influence_path") or "current-case anchors -> support/refute/evidence-gap/treatment-boundary support only; closure remains review_required",
            "unit_status": unit.get("status"),
            "support_candidate": unit.get("support_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    for unit_id, unit in third_batch_units.items():
        if unit.get("status") == "not_applicable":
            continue
        contexts.append({
            "context_id": unit_id,
            "role": unit.get("context_role") or "evidence_profile",
            "projection_level": "ordinary",
            "source_sensitivity": unit.get("source_sensitivity") or "deidentified_role_row_default_projection",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": unit.get("current_case_influence_path") or "current-case anchors -> support/refute/evidence-gap/projection-boundary support only; closure remains review_required",
            "unit_status": unit.get("status"),
            "support_candidate": unit.get("support_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    for unit_id, unit in fourth_batch_units.items():
        if unit.get("status") == "not_applicable":
            continue
        contexts.append({
            "context_id": unit_id,
            "role": unit.get("context_role") or "differential_prompt",
            "projection_level": "ordinary",
            "source_sensitivity": unit.get("source_sensitivity") or "deidentified_doctor_trace_only_default_projection",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": unit.get("current_case_influence_path") or "surface context plus hard anchors -> support-only review trigger; closure remains review_required",
            "unit_status": unit.get("status"),
            "support_candidate": unit.get("support_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    for unit_id, unit in fifth_batch_units.items():
        if unit.get("status") == "not_applicable":
            continue
        contexts.append({
            "context_id": unit_id,
            "role": unit.get("context_role") or "treatment_boundary",
            "projection_level": "ordinary",
            "source_sensitivity": unit.get("source_sensitivity") or "deidentified_role_row_default_projection",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": unit.get("current_case_influence_path") or "current-case anchors -> support/refute/evidence-gap/treatment-boundary support only; closure remains review_required",
            "unit_status": unit.get("status"),
            "support_candidate": unit.get("support_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    for unit_id, unit in sixth_batch_units.items():
        if unit.get("status") == "not_applicable":
            continue
        contexts.append({
            "context_id": unit_id,
            "role": unit.get("context_role") or "treatment_boundary",
            "projection_level": "ordinary",
            "source_sensitivity": unit.get("source_sensitivity") or "deidentified_role_row_default_projection",
            "may_enter_final_conclusion": False,
            "promoted_to_observed_fact": False,
            "current_case_influence_path": unit.get("current_case_influence_path") or "current-case anchors -> support/refute/evidence-gap/treatment-boundary support only; closure remains review_required",
            "unit_status": unit.get("status"),
            "support_candidate": unit.get("support_candidate"),
            "treatment_boundary": "review_required_or_eligibility_support_only",
        })
    return contexts


def _sgtb_reasoning_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _sgtb_evidence(case_payload)
    status = "not_applicable"
    subtype = evidence.get("subtype_candidate") or "unresolved"
    support: list[str] = []
    refute: list[str] = []
    missing: list[str] = []
    review_flags: list[str] = []
    domains = ("protrusive_axis", "source_separation", "jaw_position", "tmj_joint", "morphology_occlusion")
    present_domains = {domain for domain in domains if evidence.get(domain) is True}

    if _sgtb_candidate_signal(case_payload, evidence):
        status = "candidate_recall"
        missing = [domain for domain in domains if domain not in present_domains]
    if evidence.get("concave_direction") is True:
        status = "not_applicable"
        refute.append("concave/Class III direction routes away from SGTB")
        review_flags.append("concave_case_to_s8_sgtb")
    elif present_domains == set(domains):
        status = "finalization_support_candidate"
        support.append(f"sgtb_subtype_candidate={subtype}")

    if evidence.get("dentoalveolar_counterexample") is True or subtype == "dentoalveolar":
        refute.append("dentoalveolar-only source refutes SGTB activation")
        status = "candidate_recall"
    if evidence.get("skeletal_physiological_counterexample") is True or subtype == "skeletal_physiological":
        refute.append("skeletal physiological source without jaw-position driver refutes routine SGTB")
        status = "candidate_recall"
    if evidence.get("mixed_II_caution") is True or subtype == "mixed_II":
        support.append("mixed_II caution: direct bonding/aggressive upper-incisor uprighting forbidden")
    if evidence.get("pathologic_absorption_review") is True or subtype == "skeletal_pathological":
        support.append("pathologic absorption branch requires CT/CBCT/TMJ staging and active/stable status")
        status = "finalization_support_candidate" if present_domains == set(domains) else "candidate_recall"
    if evidence.get("metric_dependent") is True and measurement_gate.get("status") != "pass":
        review_flags.append("visual_or_low_conf_measurement_to_sgtb_closure")
        if "measurement gate pass for metric-dependent claims" not in missing:
            missing.append("measurement gate pass for metric-dependent claims")
        status = "candidate_recall"

    unit_projection = _sgtb_unit_projection()
    return {
        "schema_version": "sgtb_reasoning_unit.1",
        "unit_id": SGTB_UNIT_ID,
        "status": status,
        "subtype_candidate": subtype,
        "runtime_entry": "candidate_support_refute_evidence_gap_doctor_trace",
        "supporting_anchors": _dedupe(support),
        "refuting_anchors": _dedupe(refute),
        "missing_evidence": _dedupe(missing),
        "evidence_domains_present": sorted(present_domains),
        "review_flags": _dedupe(review_flags),
        "hard_fail_flags": [],
        "measurement_gate_status": measurement_gate.get("status"),
        "treatment_branch_effect": "review_required_or_eligibility_support_only",
        "may_enter_final_conclusion": False,
        "unit_projection": unit_projection,
    }


def _sgtb_unit_projection() -> dict[str, Any]:
    try:
        unit = get_reasoning_unit(SGTB_UNIT_ID)
    except Exception:
        return {"unit_id": SGTB_UNIT_ID, "unit_type": "reasoning_role_row", "may_enter_final_conclusion": False}
    return {
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "runtime_roles": unit["runtime_roles"],
        "source_sensitivity": unit["source_sensitivity"],
        "may_enter_final_conclusion": unit["may_enter_final_conclusion"],
        "may_support_finalization": unit["may_support_finalization"],
        "required_evidence_domains": unit["required_evidence_domains"],
        "treatment_boundary": unit["treatment_boundary"],
    }


def _sgtb_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    value = case_payload.get("sgtb_evidence") or case_payload.get("protrusive_4type_sgtb") or {}
    return deepcopy(value) if isinstance(value, dict) else {}


def _sgtb_candidate_signal(case_payload: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if evidence:
        return True
    text = repr(case_payload)
    return any(term in text for term in ("SGTB", "S8", "Class II", "convex", "protrusion", "嘴突", "突面"))


def _transverse_expansion_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _transverse_evidence(case_payload)
    status = "not_applicable"
    device_family = evidence.get("device_family_candidate") or evidence.get("candidate") or "unresolved"
    support: list[str] = []
    refute: list[str] = []
    missing: list[str] = []
    review_flags: list[str] = []
    domains = (
        "transverse_problem",
        "growth_and_maturity",
        "suture_maturity_when_material",
        "quantitative_width_evidence",
        "periodontal_root_buccal_bone_risk",
        "treatment_personalization_variables",
    )
    present_domains = {domain for domain in domains if evidence.get(domain) is True}

    if _transverse_candidate_signal(case_payload, evidence):
        status = "candidate_recall"
        missing = [domain for domain in domains if domain not in present_domains]

    if evidence.get("normal_transverse_width") is True:
        status = "candidate_recall"
        refute.append("normal or minimal transverse width refutes MSE/MARPE expansion closure")
    if evidence.get("mild_crowding_only") is True:
        status = "candidate_recall"
        refute.append("mild crowding alone cannot close expansion or extraction-avoidance branch")
        review_flags.append("crowding_alone_to_expansion_or_extraction_avoidance")
    if evidence.get("age_alone_device_choice") is True:
        review_flags.append("age_alone_to_expansion_device_closure")
    if evidence.get("fourteen_f_or_cvmi_label_alone") is True:
        review_flags.append("fourteen_f_or_cvmi_label_to_mse_marpe_closure")
    if evidence.get("generic_transverse_deficiency_to_mse") is True:
        review_flags.append("generic_transverse_deficiency_to_mse_marpe_closure")
    if evidence.get("fsp19_or_maxillary_source_label_only") is True:
        review_flags.append("fsp19_or_maxillary_source_label_to_mse_protraction_closure")
        if "current-case transverse width/suture/risk evidence" not in missing:
            missing.append("current-case transverse width/suture/risk evidence")
    if evidence.get("adult_without_suture_or_periodontal_risk") is True:
        review_flags.append("adult_transverse_to_mse_sarpe_without_suture_periodontal_risk")
    if evidence.get("dental_aligner_for_skeletal_deficit") is True:
        review_flags.append("dental_aligner_expansion_as_final_for_skeletal_width_deficit")
    if evidence.get("expansion_evidence_alone_to_final_plan") is True:
        review_flags.append("expansion_candidate_to_final_appliance_or_extraction_closure")
    if evidence.get("kb_or_walter_correction_as_case_fact") is True:
        review_flags.append("kb_or_walter_correction_as_case_fact")

    if evidence.get("metric_dependent") is True and measurement_gate.get("status") != "pass":
        if (
            measurement_gate.get("visual_quantification_detected") is True
            or measurement_gate.get("low_confidence_metrics_used_for_closure")
        ):
            review_flags.append("visual_width_estimate_to_device_or_source_closure")
        if "measurement gate pass for transverse metric-dependent claims" not in missing:
            missing.append("measurement gate pass for transverse metric-dependent claims")
        status = "candidate_recall"
    if (
        evidence.get("metric_dependent") is True
        and measurement_gate.get("high_confidence_metrics_used_for_closure")
        and not evidence.get("non_measurement_device_family_anchors")
    ):
        review_flags.append("high_conf_width_metric_alone_to_device_or_treatment_closure")

    if present_domains == set(domains) and not review_flags:
        status = "finalization_support_candidate"
        support.append(f"device_family_eligibility_candidate={device_family}")
    elif present_domains & {"transverse_problem", "growth_and_maturity", "quantitative_width_evidence"}:
        status = "candidate_recall"

    if device_family in {"dental_or_aligner_expansion_support", "dental_aligner_arch_form_support"}:
        support.append("dental/aligner expansion is arch-form support only when skeletal width deficit is absent or small")
    if device_family in {"adult_MSE_MARPE_boundary_support", "MSE_MARPE"}:
        support.append("adult MSE/MARPE branch remains review_required for final device or surgical boundary")
    if device_family in {"extreme_adult_SARPE_surgical_review_boundary", "SARPE_review"}:
        support.append("SARPE branch is surgical review boundary, not surgery-only closure")
    if device_family in {"adolescent_cast_expander_or_RPE_RME_support", "cast_RPE_RME"}:
        support.append("adolescent cast/RPE/RME support requires growth/suture/transverse evidence, not age alone")

    unit_projection = _transverse_unit_projection()
    return {
        "schema_version": "transverse_expansion_reasoning_unit.1",
        "unit_id": TRANSVERSE_UNIT_ID,
        "status": status,
        "device_family_candidate": device_family,
        "runtime_entry": "candidate_support_refute_evidence_gap_doctor_trace_treatment_personalization",
        "supporting_anchors": _dedupe(support),
        "refuting_anchors": _dedupe(refute),
        "missing_evidence": _dedupe(missing),
        "evidence_domains_present": sorted(present_domains),
        "review_flags": _dedupe(review_flags),
        "hard_fail_flags": [],
        "measurement_gate_status": measurement_gate.get("status"),
        "why_fit": evidence.get("why_fit") or [],
        "why_not": evidence.get("why_not") or [],
        "records_needed": evidence.get("records_needed") or missing[:4],
        "follow_up_owner": evidence.get("follow_up_owner") or "doctor review",
        "treatment_branch_effect": "review_required_or_eligibility_support_only",
        "may_enter_final_conclusion": False,
        "unit_projection": unit_projection,
    }


def _transverse_unit_projection() -> dict[str, Any]:
    try:
        unit = get_reasoning_unit(TRANSVERSE_UNIT_ID)
    except Exception:
        return {"unit_id": TRANSVERSE_UNIT_ID, "unit_type": "reasoning_role_row", "may_enter_final_conclusion": False}
    return {
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "runtime_roles": unit["runtime_roles"],
        "source_sensitivity": unit["source_sensitivity"],
        "may_enter_final_conclusion": unit["may_enter_final_conclusion"],
        "may_support_finalization": unit["may_support_finalization"],
        "required_evidence_domains": unit["required_evidence_domains"],
        "treatment_boundary": unit["treatment_boundary"],
    }


def _transverse_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    value = (
        case_payload.get("transverse_expansion_evidence")
        or case_payload.get("transverse_expansion")
        or case_payload.get("expansion_growth_device_boundary")
        or {}
    )
    return deepcopy(value) if isinstance(value, dict) else {}


def _transverse_candidate_signal(case_payload: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if evidence:
        return True
    text = repr(case_payload)
    return any(
        term in text
        for term in (
            "transverse",
            "crossbite",
            "posterior crossbite",
            "narrow maxilla",
            "MSE",
            "MARPE",
            "SARPE",
            "expansion",
            "扩弓",
            "横向",
            "后牙反𬌗",
        )
    )


def _asymmetry_reasoning_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _asymmetry_evidence(case_payload)
    status = "not_applicable"
    contributor = evidence.get("contributor_family_candidate") or evidence.get("candidate") or "unresolved"
    support: list[str] = []
    refute: list[str] = []
    missing: list[str] = []
    review_flags: list[str] = []
    domains = (
        "facial_midline_occlusal_cant",
        "crco_functional_shift",
        "pa_cbct_bone_source",
        "condyle_ramus_tmj",
        "dental_compensation_missing_teeth_space",
        "transverse_interaction",
        "treatment_personalization_variables",
    )
    present_domains = {domain for domain in domains if evidence.get(domain) is True}

    if _asymmetry_candidate_signal(case_payload, evidence):
        status = "candidate_recall"
        missing = [domain for domain in domains if domain not in present_domains]

    flag_fields = {
        "generic_asymmetry_only": "generic_asymmetry_to_source_or_treatment_closure",
        "dental_midline_only": "dental_midline_to_asymmetry_subtype_or_treatment_closure",
        "class_or_molar_label_only": "class_or_molar_label_to_asymmetry_source_closure",
        "face_photo_impression_only": "face_photo_impression_to_asymmetry_source_or_treatment_closure",
        "old_kb_or_walter_label_only": "old_kb_or_walter_label_to_case_fact_or_closure",
        "dental_compensation_overclosure": "dental_compensation_to_skeletal_joint_device_closure",
        "missing_teeth_or_extraction_history_overclosure": "missing_teeth_or_extraction_history_to_asymmetry_device_closure",
        "unilateral_space_overclosure": "unilateral_space_to_distal_mesial_or_extraction_closure",
        "transverse_crossbite_overclosure": "transverse_crossbite_to_asymmetry_device_or_surgery_closure",
        "missing_crco_functional_shift": "missing_crco_functional_shift_closure",
        "pa_only_bone_source": "pa_only_to_bone_source_closure",
        "condyle_tmd_label_only": "condyle_tmd_label_to_joint_source_sclass_or_surgery_closure",
        "sclass_device_splint_surgery_final_plan_closure": "asymmetry_unit_to_sclass_device_splint_surgery_or_final_plan_closure",
    }
    for field, flag in flag_fields.items():
        if evidence.get(field) is True:
            review_flags.append(flag)

    if evidence.get("old_kb_or_walter_label_only") is True:
        refute.append("old-KB/Walter label cannot become observed current-case fact")
    if evidence.get("dental_midline_only") is True:
        refute.append("dental midline alone cannot close asymmetry source family")
    if evidence.get("missing_crco_functional_shift") is True:
        refute.append("functional shift closure requires CR-CO/deprogramming/reposition evidence")
    if evidence.get("pa_only_bone_source") is True:
        refute.append("PA-only evidence without head-position/source-confidence context cannot close bone source")
    if evidence.get("condyle_tmd_label_only") is True:
        refute.append("condyle/TMD label alone cannot close joint source, S-class, surgery, or device branch")

    if evidence.get("metric_dependent") is True and measurement_gate.get("status") != "pass":
        if "measurement gate pass for asymmetry metric-dependent claims" not in missing:
            missing.append("measurement gate pass for asymmetry metric-dependent claims")
        status = "candidate_recall"
    if (
        evidence.get("metric_dependent") is True
        and measurement_gate.get("high_confidence_metrics_used_for_closure")
        and not evidence.get("non_measurement_source_family_anchors")
    ):
        review_flags.append("asymmetry_metric_alone_to_treatment_closure")

    if present_domains == set(domains) and not review_flags:
        status = "finalization_support_candidate"
        support.append(f"asymmetry_contributor_family_candidate={contributor}")
    elif present_domains & {"facial_midline_occlusal_cant", "crco_functional_shift", "pa_cbct_bone_source", "condyle_ramus_tmj"}:
        status = "candidate_recall"

    if contributor == "dental_dentoalveolar_compensation":
        support.append("dental/dentoalveolar contributor support must not close skeletal, joint, device, or surgery branch")
    if contributor == "functional_deviation_crco_shift":
        support.append("functional deviation support requires CR-CO/deprogramming/reposition evidence and remains review_required")
    if contributor == "skeletal_bone_developmental_asymmetry":
        support.append("skeletal-bone source-family support requires source-confident PA/CBCT/measurement evidence")
    if contributor == "condyle_ramus_joint_contribution":
        support.append("condyle-ramus-joint contribution support remains treatment-boundary review, not S-class/surgery closure")
    if contributor == "transverse_compensation_interaction":
        support.append("transverse compensation interaction is source-family support only and may depend on transverse/measurement gates")

    unit_projection = _asymmetry_unit_projection()
    return {
        "schema_version": "asymmetry_functional_joint_bone_reasoning_unit.1",
        "unit_id": ASYMMETRY_UNIT_ID,
        "status": status,
        "contributor_family_candidate": contributor,
        "runtime_entry": "candidate_support_refute_evidence_gap_doctor_trace_treatment_personalization",
        "supporting_anchors": _dedupe(support),
        "refuting_anchors": _dedupe(refute),
        "missing_evidence": _dedupe(missing),
        "evidence_domains_present": sorted(present_domains),
        "review_flags": _dedupe(review_flags),
        "hard_fail_flags": [],
        "measurement_gate_status": measurement_gate.get("status"),
        "why_fit": evidence.get("why_fit") or [],
        "why_not": evidence.get("why_not") or [],
        "records_needed": evidence.get("records_needed") or missing[:4],
        "follow_up_owner": evidence.get("follow_up_owner") or "doctor review",
        "treatment_branch_effect": "review_required_or_eligibility_support_only",
        "may_enter_final_conclusion": False,
        "unit_projection": unit_projection,
    }


def _asymmetry_unit_projection() -> dict[str, Any]:
    try:
        unit = get_reasoning_unit(ASYMMETRY_UNIT_ID)
    except Exception:
        return {"unit_id": ASYMMETRY_UNIT_ID, "unit_type": "reasoning_role_row", "may_enter_final_conclusion": False}
    return {
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "runtime_roles": unit["runtime_roles"],
        "source_sensitivity": unit["source_sensitivity"],
        "may_enter_final_conclusion": unit["may_enter_final_conclusion"],
        "may_support_finalization": unit["may_support_finalization"],
        "required_evidence_domains": unit["required_evidence_domains"],
        "treatment_boundary": unit["treatment_boundary"],
    }


def _asymmetry_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    value = (
        case_payload.get("asymmetry_evidence")
        or case_payload.get("asymmetry_functional_joint_bone")
        or case_payload.get("asymmetry_full_classification")
        or {}
    )
    return deepcopy(value) if isinstance(value, dict) else {}


def _asymmetry_candidate_signal(case_payload: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if evidence:
        return True
    text = repr(case_payload)
    return any(
        term in text
        for term in (
            "asymmetry",
            "midline",
            "occlusal cant",
            "CR-CO",
            "condyle",
            "ramus",
            "TMD",
            "TMJ",
            "偏颌",
            "偏斜",
            "中线",
            "咬合平面",
            "髁突",
            "升支",
        )
    )


def _jaw_position_reasoning_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _jaw_position_evidence(case_payload)
    status = "not_applicable"
    balance_candidate = evidence.get("balance_candidate") or evidence.get("candidate") or "unresolved"
    support: list[str] = []
    refute: list[str] = []
    missing: list[str] = []
    review_flags: list[str] = []
    domains = (
        "crco_deprogramming_reposition",
        "occlusion_interference_cusp_fossa",
        "tmj_active_stable_status",
        "condyle_fossa_position",
        "functional_vs_structural_jaw_position",
        "treatment_personalization_variables",
    )
    present_domains = {domain for domain in domains if evidence.get(domain) is True}

    if _jaw_position_candidate_signal(case_payload, evidence):
        status = "candidate_recall"
        missing = [domain for domain in domains if domain not in present_domains]

    flag_fields = {
        "class_label_only": "class_label_to_jaw_position_source_device_treatment_closure",
        "crossbite_or_locked_bite_only": "crossbite_or_locked_bite_to_jaw_position_reconstruction_closure",
        "occlusal_interference_final_mechanics": "occlusal_interference_to_final_mechanics_closure",
        "tmj_tmd_clicking_label_only": "tmj_tmd_clicking_label_to_device_splint_surgery_closure",
        "profile_impression_only": "profile_impression_to_jaw_position_source_or_treatment_closure",
        "old_kb_walter_or_device_label_only": "old_kb_walter_or_device_label_to_case_fact_or_closure",
        "single_bite_registration_only": "single_bite_registration_to_crco_jaw_position_or_device_closure",
        "missing_crco_deprogramming_reposition_evidence": "missing_crco_deprogramming_reposition_evidence_to_jaw_position_closure",
        "condyle_fossa_or_joint_space_metric_alone": "condyle_fossa_or_joint_space_metric_alone_to_treatment_device_surgery_closure",
        "active_progressive_tmd_bypass": "active_progressive_tmd_bypasses_stabilization_review_boundary",
        "stable_tmd_label_alone": "stable_tmd_label_alone_to_device_or_surgery_closure",
        "three_deep_spee_morphology_pattern_only": "three_deep_spee_morphology_pattern_to_gs_s8_device_closure",
        "jaw_position_theory_or_source_text_only": "jaw_position_theory_or_source_text_to_device_extraction_surgery_final_plan_closure",
        "device_surgery_final_plan_closure": "jaw_position_unit_to_gs_s8_s9_s10_s17_ars_splint_device_extraction_unilateral_orthognathic_surgery_final_plan_closure",
    }
    for field, flag in flag_fields.items():
        if evidence.get(field) is True:
            review_flags.append(flag)

    if evidence.get("old_kb_walter_or_device_label_only") is True:
        refute.append("old-KB/Walter/device label cannot become observed current-case fact")
    if evidence.get("class_label_only") is True:
        refute.append("Class II/III label alone cannot close jaw-position source, device, or treatment")
    if evidence.get("crossbite_or_locked_bite_only") is True:
        refute.append("crossbite/locked bite alone cannot close jaw-position reconstruction or final mechanics")
    if evidence.get("tmj_tmd_clicking_label_only") is True:
        refute.append("TMJ/TMD/clicking label alone cannot close device, splint, surgery, or GS branch")
    if evidence.get("single_bite_registration_only") is True:
        refute.append("single bite registration cannot close CR/CO reproducibility, jaw-position source, or device")
    if evidence.get("missing_crco_deprogramming_reposition_evidence") is True:
        refute.append("jaw-position closure requires CR/CO, deprogramming, or reposition reproducibility evidence")
    if evidence.get("active_progressive_tmd_bypass") is True:
        refute.append("active/progressive TMD requires stabilization/review boundary before orthodontic reconstruction")

    if evidence.get("metric_dependent") is True and measurement_gate.get("status") != "pass":
        if "measurement gate pass for jaw-position metric-dependent claims" not in missing:
            missing.append("measurement gate pass for jaw-position metric-dependent claims")
        status = "candidate_recall"
    if (
        evidence.get("metric_dependent") is True
        and measurement_gate.get("high_confidence_metrics_used_for_closure")
        and not evidence.get("non_measurement_balance_anchors")
    ):
        review_flags.append("condyle_fossa_or_joint_space_metric_alone_to_treatment_device_surgery_closure")

    if present_domains == set(domains) and not review_flags:
        status = "finalization_support_candidate"
        support.append(f"jaw_position_occlusion_tmj_balance_candidate={balance_candidate}")
    elif present_domains & {
        "crco_deprogramming_reposition",
        "occlusion_interference_cusp_fossa",
        "tmj_active_stable_status",
        "condyle_fossa_position",
    }:
        status = "candidate_recall"

    if evidence.get("crco_deprogramming_reposition") is True:
        support.append("CR/CO or deprogramming/reposition evidence supports review-required jaw-position balance reasoning")
    if evidence.get("occlusion_interference_cusp_fossa") is True:
        support.append("occlusal interference/cusp-fossa evidence may support/refute jaw-position balance but not final mechanics")
    if evidence.get("tmj_active_stable_status") is True:
        support.append("TMJ active/stable status shapes review boundary and cannot close device/surgery by label alone")
    if evidence.get("condyle_fossa_position") is True:
        support.append("condyle-fossa evidence remains support-only and metric-dependent claims require measurement gate")
    if evidence.get("functional_vs_structural_jaw_position") is True:
        support.append("functional vs structural jaw-position relation is differential support only")

    unit_projection = _jaw_position_unit_projection()
    return {
        "schema_version": "jaw_position_occlusion_tmj_balance_reasoning_unit.1",
        "unit_id": JAW_POSITION_UNIT_ID,
        "status": status,
        "balance_candidate": balance_candidate,
        "runtime_entry": "candidate_support_refute_evidence_gap_doctor_trace_treatment_personalization",
        "supporting_anchors": _dedupe(support),
        "refuting_anchors": _dedupe(refute),
        "missing_evidence": _dedupe(missing),
        "evidence_domains_present": sorted(present_domains),
        "review_flags": _dedupe(review_flags),
        "hard_fail_flags": [],
        "measurement_gate_status": measurement_gate.get("status"),
        "why_fit": evidence.get("why_fit") or [],
        "why_not": evidence.get("why_not") or [],
        "records_needed": evidence.get("records_needed") or missing[:4],
        "follow_up_owner": evidence.get("follow_up_owner") or "doctor review",
        "treatment_branch_effect": "review_required_or_eligibility_support_only",
        "may_enter_final_conclusion": False,
        "unit_projection": unit_projection,
    }


def _jaw_position_unit_projection() -> dict[str, Any]:
    try:
        unit = get_reasoning_unit(JAW_POSITION_UNIT_ID)
    except Exception:
        return {"unit_id": JAW_POSITION_UNIT_ID, "unit_type": "reasoning_role_row", "may_enter_final_conclusion": False}
    return {
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "runtime_roles": unit["runtime_roles"],
        "source_sensitivity": unit["source_sensitivity"],
        "may_enter_final_conclusion": unit["may_enter_final_conclusion"],
        "may_support_finalization": unit["may_support_finalization"],
        "required_evidence_domains": unit["required_evidence_domains"],
        "treatment_boundary": unit["treatment_boundary"],
    }


def _jaw_position_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    value = (
        case_payload.get("jaw_position_evidence")
        or case_payload.get("jaw_position_reconstruction_occlusion_tmj_balance")
        or case_payload.get("occlusion_tmj_balance")
        or {}
    )
    return deepcopy(value) if isinstance(value, dict) else {}


def _jaw_position_candidate_signal(case_payload: dict[str, Any], evidence: dict[str, Any]) -> bool:
    if evidence:
        return True
    text = repr(case_payload)
    return any(
        term in text
        for term in (
            "CR-CO",
            "centric relation",
            "deprogramming",
            "reposition",
            "bite registration",
            "occlusal interference",
            "cusp-fossa",
            "condyle-fossa",
            "joint space",
            "TMJ",
            "TMD",
            "clicking",
            "locked bite",
            "jaw position",
            "颌位",
            "咬合干扰",
            "关节",
            "髁突",
            "去程序化",
            "重定位",
            "咬合记录",
        )
    )


def _second_batch_reasoning_units(
    case_payload: dict[str, Any],
    measurement_gate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        CONCAVE_ASYMMETRY_UNIT_ID: _support_only_reasoning_unit(
            CONCAVE_ASYMMETRY_UNIT_ID,
            _concave_asymmetry_evidence(case_payload),
            measurement_gate,
            candidate_signal=_concave_asymmetry_candidate_signal(case_payload),
            schema_version="concave_asymmetry_dental_functional_differential_runtime_unit.1",
            candidate_key="differential_candidate",
            support_label="concave_asymmetry_dental_functional_differential_candidate",
            domains=(
                "concave_or_classIII_expression",
                "asymmetry_or_midline_expression",
                "dental_vs_skeletal_anchor",
                "crco_functional_shift",
                "transverse_or_crossbite_context",
                "tmj_condyle_context",
                "measurement_source_confidence",
            ),
            flag_map={
                "dental_classIII_alone": "caddc_dental_classIII_alone_to_skeletal_source_closure",
                "asymmetry_label_to_surgery": "caddc_asymmetry_label_to_surgery_or_final_plan",
                "midline_or_facial_asymmetry_alone": "caddc_midline_or_facial_asymmetry_alone_to_source_closure",
                "missing_crco_functional_closure": "caddc_missing_crco_to_functional_shift_closure",
                "transverse_crossbite_device_closure": "caddc_transverse_crossbite_alone_to_device_closure",
                "condyle_tmd_label_closure": "caddc_condyle_tmd_label_to_active_tmd_gs_surgery_closure",
                "conservative_differential_final_mechanics": "caddc_conservative_differential_to_final_mechanics_closure",
            },
            metric_flag="caddc_metric_without_measurement_gate_closure",
            high_conf_metric_alone_flag="caddc_high_conf_metric_alone_to_treatment_closure",
            support_messages={
                "dental_vs_skeletal_anchor": "dental vs skeletal anchors support differential only, not source closure",
                "crco_functional_shift": "CR-CO/functional-shift evidence may support functional contribution but cannot close without records",
                "transverse_or_crossbite_context": "transverse/crossbite context is a confounder support signal, not expansion/device closure",
                "tmj_condyle_context": "TMJ/condyle context remains support/refute and review boundary only",
            },
            source_sensitivity="learning_log_derived_unresolved_raw_source_default_deidentified_summary",
            context_role="differential_prompt",
            current_case_influence_path="current-case concave/asymmetry/dental/functional anchors -> differential support/refute/evidence-gap only; diagnosis/source/treatment remain review_required",
        ),
        TRUE_CONCAVE_UNIT_ID: _support_only_reasoning_unit(
            TRUE_CONCAVE_UNIT_ID,
            _true_concave_evidence(case_payload),
            measurement_gate,
            candidate_signal=_true_concave_candidate_signal(case_payload),
            schema_version="true_concave_false_protrusive_treatment_realism_unit.1",
            candidate_key="realism_candidate",
            support_label="true_concave_false_protrusive_treatment_realism_candidate",
            domains=(
                "concave_false_protrusive_anchor",
                "growth_stage_or_age_context",
                "vertical_context",
                "transverse_context",
                "chief_complaint_context",
                "periodontal_root_context",
                "tmj_context",
            ),
            flag_map={
                "automatic_mse_protraction": "tcfp_false_protrusive_concave_to_automatic_mse_protraction",
                "automatic_no_extraction": "tcfp_false_protrusive_concave_to_automatic_no_extraction",
                "single_case_universal_extraction_rule": "tcfp_single_16f_case_to_universal_extraction_rule",
                "fsp19_overgeneralization": "tcfp_fsp19_like_label_to_all_false_protrusive_path",
                "subtype_label_final_branch": "tcfp_subtype_label_to_final_treatment_branch",
                "missing_context_treatment_closure": "tcfp_missing_context_to_treatment_closure",
            },
            metric_flag="tcfp_metric_without_measurement_gate_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "concave_false_protrusive_anchor": "true-concave/false-protrusive anchors support treatment realism review only",
                "growth_stage_or_age_context": "growth or age context informs feasibility but cannot close treatment",
                "vertical_context": "vertical context changes why-fit/why-not without selecting mechanics",
                "transverse_context": "transverse context blocks one-case overgeneralization",
                "periodontal_root_context": "periodontal/root context is required before extraction/device reasoning",
                "tmj_context": "TMJ context keeps treatment branch review_required",
            },
            source_sensitivity="learning_log_derived_unresolved_raw_source_default_deidentified_summary",
            context_role="treatment_boundary",
            current_case_influence_path="current-case true-concave/false-protrusive treatment-realism anchors -> support/refute/treatment-boundary support only; extraction/device/final branch remains review_required",
        ),
        VERTICAL_SAGITTAL_UNIT_ID: _support_only_reasoning_unit(
            VERTICAL_SAGITTAL_UNIT_ID,
            _vertical_sagittal_evidence(case_payload),
            measurement_gate,
            candidate_signal=_vertical_sagittal_candidate_signal(case_payload),
            schema_version="vertical_sagittal_coupling_mechanics_boundary_unit.1",
            candidate_key="mechanics_boundary_candidate",
            support_label="vertical_sagittal_mechanics_boundary_candidate",
            domains=(
                "sagittal_or_source_context",
                "vertical_pattern_context",
                "occlusal_or_spee_context",
                "incisor_display_or_deepbite_context",
                "tmj_or_joint_context",
                "treatment_personalization_variables",
            ),
            flag_map={
                "high_angle_universal_vertical_control": "vsc_high_angle_keyword_to_universal_vertical_control_final_plan",
                "concave_classIII_universal_clockwise": "vsc_concave_classIII_to_universal_clockwise_rotation_camouflage",
                "gummysmile_deepbite_automatic_intrusion": "vsc_gummysmile_deepbite_only_to_intrusion_extraction_device_phase",
                "classII_logic_copied_to_classIII": "vsc_classII_vertical_logic_copied_to_classIII_concave",
                "triangular_modifier_misuse": "vsc_triangular_modifier_omitted_or_overclosed_to_concave_subtype",
                "vertical_mechanics_to_device_phase_final": "vsc_vertical_mechanics_alone_to_device_phase_final_plan",
                "tmj_keyword_closure": "vsc_tmj_keyword_to_active_tmd_stable_no_contingency_or_surgery_splint_tutorial",
            },
            metric_flag="vsc_low_conf_vertical_estimate_to_numeric_or_treatment_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "sagittal_or_source_context": "sagittal/source context is required before vertical mechanics support",
                "vertical_pattern_context": "vertical pattern supports why-fit/why-not only",
                "occlusal_or_spee_context": "occlusal/Spee evidence cannot choose intrusion/extrusion/device alone",
                "incisor_display_or_deepbite_context": "incisor display/deepbite context stays review_required until measured",
                "tmj_or_joint_context": "TMJ context adds review boundary, not splint/surgery/tutorial closure",
            },
            source_sensitivity="learning_log_derived_unresolved_raw_source_default_deidentified_summary",
            context_role="treatment_boundary",
            current_case_influence_path="current-case vertical/sagittal/occlusal/TMJ anchors -> mechanics fit/refute support only; intrusion/extrusion/device/phase/final plan remain review_required",
        ),
        RETX_SOURCE_SPACE_UNIT_ID: _retx_source_space_unit(case_payload, measurement_gate),
        TMD_REVISE_PERSIST_UNIT_ID: _tmd_revise_persist_unit(case_payload, measurement_gate),
    }


def _support_only_reasoning_unit(
    unit_id: str,
    evidence: dict[str, Any],
    measurement_gate: dict[str, Any],
    *,
    candidate_signal: bool,
    schema_version: str,
    candidate_key: str,
    support_label: str,
    domains: tuple[str, ...],
    flag_map: dict[str, str],
    metric_flag: str | None,
    high_conf_metric_alone_flag: str | None,
    support_messages: dict[str, str],
    source_sensitivity: str,
    context_role: str,
    current_case_influence_path: str,
) -> dict[str, Any]:
    status = "not_applicable"
    support_candidate = evidence.get(candidate_key) or evidence.get("candidate") or "unresolved"
    support: list[str] = []
    refute: list[str] = []
    missing: list[str] = []
    review_flags: list[str] = []
    present_domains = {domain for domain in domains if evidence.get(domain) is True}
    if candidate_signal:
        status = "candidate_recall"
        missing = [domain for domain in domains if domain not in present_domains]
    for field, flag in flag_map.items():
        if evidence.get(field) is True:
            review_flags.append(flag)
    for domain, message in support_messages.items():
        if evidence.get(domain) is True:
            support.append(message)
    if evidence.get("metric_dependent") is True and measurement_gate.get("status") != "pass":
        if metric_flag:
            review_flags.append(metric_flag)
        missing.append(f"measurement gate pass for {unit_id} metric-dependent claims")
        status = "candidate_recall"
    if (
        high_conf_metric_alone_flag
        and evidence.get("metric_dependent") is True
        and measurement_gate.get("high_confidence_metrics_used_for_closure")
        and not evidence.get("non_measurement_support_anchors")
    ):
        review_flags.append(high_conf_metric_alone_flag)
    if present_domains == set(domains) and not review_flags:
        status = "finalization_support_candidate"
        support.append(f"{support_label}={support_candidate}")
    elif present_domains:
        status = "candidate_recall"
    if review_flags:
        refute.append("review flags block closure and keep support-only boundary")
    unit_projection = _reasoning_unit_projection(unit_id)
    return {
        "schema_version": schema_version,
        "unit_id": unit_id,
        "status": status,
        "support_candidate": support_candidate,
        "runtime_entry": "candidate_support_refute_evidence_gap_doctor_trace_treatment_personalization",
        "supporting_anchors": _dedupe(support),
        "refuting_anchors": _dedupe(refute),
        "missing_evidence": _dedupe(missing),
        "evidence_domains_present": sorted(present_domains),
        "review_flags": _dedupe(review_flags),
        "hard_fail_flags": [],
        "measurement_gate_status": measurement_gate.get("status"),
        "why_fit": evidence.get("why_fit") or support[:3],
        "why_not": evidence.get("why_not") or ["unit is support-only and cannot close final diagnosis/source/subtype/treatment"],
        "records_needed": evidence.get("records_needed") or missing[:4],
        "follow_up_owner": evidence.get("follow_up_owner") or "doctor review",
        "review_required_reason": evidence.get("review_required_reason") or "support-only reasoning unit cannot close final branch",
        "treatment_branch_effect": "review_required_or_eligibility_support_only",
        "may_enter_final_conclusion": False,
        "source_sensitivity": source_sensitivity,
        "context_role": context_role,
        "current_case_influence_path": current_case_influence_path,
        "unit_projection": unit_projection,
    }


def _retx_source_space_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _retx_source_space_evidence(case_payload)
    current_expression_count = int(evidence.get("current_expression_count") or 0)
    if evidence.get("current_expression_input_1") is True:
        current_expression_count += 1
    if evidence.get("current_expression_input_2") is True:
        current_expression_count += 1
    activation_ready = (
        evidence.get("history_or_space_input") is True
        and current_expression_count >= 2
        and evidence.get("source_or_treatment_relevance_link") is True
    )
    unit = _support_only_reasoning_unit(
        RETX_SOURCE_SPACE_UNIT_ID,
        evidence,
        measurement_gate,
        candidate_signal=_retx_source_space_candidate_signal(case_payload),
        schema_version="retreatment_extraction_history_source_space_axis_reasoning_unit.1",
        candidate_key="history_space_axis_candidate",
        support_label="retreatment_extraction_history_source_space_axis_candidate",
        domains=("history_or_space_input", "current_expression_input_1", "current_expression_input_2", "source_or_treatment_relevance_link"),
        flag_map={
            "prior_ortho_only": "retx_prior_ortho_only_to_activation_source_subtype_treatment",
            "missing_tooth_only": "retx_missing_tooth_only_to_skeletal_source_or_extraction_implant_plan",
            "dental_class_plus_extraction_history": "retx_dental_class_plus_extraction_history_to_skeletal_source_without_records",
            "existing_space_only": "retx_existing_space_only_to_extraction_implant_final_space_mechanics",
            "old_history_without_records": "retx_old_history_without_records_to_current_source_fact_or_final_diagnosis",
            "implant_first_default": "retx_missing_molar_residual_root_to_implant_first_without_feasibility",
            "repeat_extraction_default": "retx_prior_extraction_plus_protrusion_crowding_to_repeat_extraction_default",
        },
        metric_flag=None,
        high_conf_metric_alone_flag=None,
        support_messages={
            "history_or_space_input": "history/space input may affect interpretation but is not current source fact",
            "source_or_treatment_relevance_link": "source/treatment relevance link supports feasibility review only",
        },
        source_sensitivity="learning_log_derived_unresolved_raw_source_default_deidentified_summary",
        context_role="evidence_profile",
        current_case_influence_path="history/space input + current expression + relevance link -> source-space-axis support only; extraction/implant/restorative/final mechanics remain review_required",
    )
    unit["activation_boundary"] = {
        "history_or_space_input_present": evidence.get("history_or_space_input") is True,
        "current_expression_count": current_expression_count,
        "source_or_treatment_relevance_link_present": evidence.get("source_or_treatment_relevance_link") is True,
        "activation_ready": activation_ready,
    }
    if unit["status"] != "not_applicable" and not activation_ready:
        unit["status"] = "candidate_recall"
        unit["missing_evidence"] = _dedupe(unit["missing_evidence"] + ["retreatment activation boundary: one history/space input, two current expression inputs, and one relevance link"])
    return unit


def _tmd_revise_persist_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _tmd_revise_persist_evidence(case_payload)
    unit = _support_only_reasoning_unit(
        TMD_REVISE_PERSIST_UNIT_ID,
        evidence,
        measurement_gate,
        candidate_signal=_tmd_revise_persist_candidate_signal(case_payload),
        schema_version="tmd_active_stable_dynamic_revise_persist_reasoning_unit.1",
        candidate_key="tmd_profile_candidate",
        support_label="tmd_activity_stability_profile_candidate",
        domains=("symptom_timeline", "mri_ct_or_specialist_record", "active_stable_profile", "evidence_source_confidence", "review_or_followup_owner"),
        flag_map={
            "generic_tmj_clicking_closure": "tmd_generic_tmj_clicking_to_active_timing_device_splint_surgery",
            "mri_disc_report_alone": "tmd_mri_disc_report_alone_to_gs_device_surgery_splint_tutorial",
            "active_progressive_final_plan": "tmd_active_progressive_to_final_orthodontic_plan_phase_jaw_position",
            "old_stable_clicking_device_closure": "tmd_old_stable_clicking_only_to_device_treatment_active_surgery_splint",
            "warning_only_tmd_paragraph": "tmd_warning_only_paragraph_counted_as_revise_persist_support",
            "disc_condyle_surgery_only": "tmd_disc_condyle_concern_to_surgery_only_without_specialist_boundary",
        },
        metric_flag=None,
        high_conf_metric_alone_flag=None,
        support_messages={
            "active_stable_profile": "active/stable profile supports revise/persist boundary only",
            "mri_ct_or_specialist_record": "MRI/CT/specialist record can inform evidence profile but cannot close device/surgery/treatment",
            "review_or_followup_owner": "follow-up owner is required before treatment timing closure",
        },
        source_sensitivity="learning_log_derived_unresolved_raw_source_default_deidentified_summary",
        context_role="evidence_profile",
        current_case_influence_path="current-case TMD active/stable evidence -> revise/persist/support-refute/treatment-boundary support only; diagnosis/device/splint/surgery/final plan remain review_required",
    )
    profile = evidence.get("profile") or evidence.get("tmd_profile") or unit.get("support_candidate")
    active_or_uncertain = profile in {"active", "progressive", "uncertain", "active_or_progressive", "uncertain_or_missing_records"} or evidence.get("active_progressive_or_uncertain") is True
    unit["activity_stability_profile"] = profile
    unit["jaw_position_finalization_override"] = "review_required" if active_or_uncertain else "cautious_support_only"
    unit["transition_log_fields"] = {
        "previous_tmd_profile": evidence.get("previous_tmd_profile") or "unknown",
        "new_evidence_type": evidence.get("new_evidence_type") or "missing",
        "evidence_source_confidence": evidence.get("evidence_source_confidence") or "missing",
        "revise_or_persist_decision": evidence.get("revise_or_persist_decision") or ("revise_to_review_required" if active_or_uncertain else "persist_review_required_support"),
        "reason": evidence.get("reason") or "TMD unit is support-only and cannot close final treatment",
        "what_would_change_the_plan": evidence.get("what_would_change_the_plan") or "updated MRI/CT/symptom timeline/specialist opinion",
        "review_or_followup_owner": evidence.get("review_or_followup_owner") or evidence.get("follow_up_owner") or "doctor or joint specialist review",
    }
    if active_or_uncertain and "active/progressive/uncertain TMD blocks jaw-position and treatment finalization" not in unit["refuting_anchors"]:
        unit["refuting_anchors"].append("active/progressive/uncertain TMD blocks jaw-position and treatment finalization")
    return unit


def _third_batch_reasoning_units(
    case_payload: dict[str, Any],
    measurement_gate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        SPACE_BUDGET_UNIT_ID: _support_only_reasoning_unit(
            SPACE_BUDGET_UNIT_ID,
            _space_budget_evidence(case_payload),
            measurement_gate,
            candidate_signal=_space_budget_candidate_signal(case_payload),
            schema_version="space_budget_second_molar_eruption_extraction_timing_reasoning_unit.1",
            candidate_key="space_budget_candidate",
            support_label="space_budget_second_molar_eruption_extraction_timing_candidate",
            domains=(
                "seven_eruption_occlusion_status",
                "crowding_space_severity",
                "anterior_retraction_need",
                "expansion_space_context",
                "anchorage_six_mesial_drift_risk",
            ),
            flag_map={
                "unerupted_second_molar_no_extraction": "unerupted_second_molar_to_no_extraction_rule",
                "crowding_early_extraction": "crowding_keyword_to_early_extraction_rule",
                "expansion_no_extraction": "expansion_keyword_to_no_extraction_rule",
                "space_allocation_final_phase": "space_allocation_support_to_final_phase_sequence",
            },
            metric_flag="space_budget_without_measurement_to_extraction_timing_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "seven_eruption_occlusion_status": "7s eruption/occlusion status can affect space-budget review only",
                "crowding_space_severity": "crowding/space severity supports records-needed reasoning, not extraction timing closure",
                "anterior_retraction_need": "anterior retraction need is treatment-personalization support only",
                "expansion_space_context": "expansion-space context cannot substitute for extraction/non-extraction closure",
                "anchorage_six_mesial_drift_risk": "anchorage/6 mesial-drift risk remains review_required",
            },
            source_sensitivity="deidentified_role_row_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case 7s/space/crowding/retraction/expansion/anchorage anchors -> why_fit/why_not/evidence_gap only; extraction timing and phase sequence remain review_required",
        ),
        PROTRUSIVE_JAW_POSITION_UNIT_ID: _support_only_reasoning_unit(
            PROTRUSIVE_JAW_POSITION_UNIT_ID,
            _protrusive_jaw_position_evidence(case_payload),
            measurement_gate,
            candidate_signal=_protrusive_jaw_position_candidate_signal(case_payload),
            schema_version="protrusive_jaw_position_mixed_subtype_differential_runtime_unit.1",
            candidate_key="mixed_subtype_candidate",
            support_label="protrusive_jaw_position_mixed_subtype_differential_candidate",
            domains=(
                "side_specific_molar_canine_relation",
                "mandibular_retrusion_jaw_position",
                "maxillary_skeletal_source",
                "u1sn_incisor_axis_source_confidence",
                "lower_crowding_six_mesial_drift_masking",
                "forward_jaw_profile_simulation_weak_support",
            ),
            flag_map={
                "u1sn_alone_false_subtype": "u1sn_alone_false_subtype",
                "old_kb_mixed_label_as_truth": "old_kb_mixed_label_as_truth",
                "near_neutral_molar_masking_missed": "near_neutral_molar_masking_missed",
                "profile_simulation_as_diagnosis": "profile_simulation_as_diagnosis",
                "subtype_to_treatment_closure": "subtype_to_treatment_closure",
                "patient_source_leak": "patient_source_leak",
            },
            metric_flag="u1sn_alone_false_subtype",
            high_conf_metric_alone_flag=None,
            support_messages={
                "side_specific_molar_canine_relation": "side-specific molar/canine relation supports differential only",
                "mandibular_retrusion_jaw_position": "jaw-position evidence may support/refute mixed subtype but cannot close source",
                "maxillary_skeletal_source": "maxillary source evidence remains review-required differential support",
                "u1sn_incisor_axis_source_confidence": "U1-SN/incisor axis requires #20 source confidence and cannot close subtype alone",
                "lower_crowding_six_mesial_drift_masking": "near-neutral molar relation requires masking assessment",
                "forward_jaw_profile_simulation_weak_support": "profile simulation response is weak support only",
            },
            source_sensitivity="deidentified_doctor_trace_only_default_projection",
            context_role="differential_prompt",
            current_case_influence_path="current-case side-specific relation/jaw-position/source/U1-SN/masking/profile-simulation anchors -> mixed-subtype differential support only; source/subtype/treatment remain review_required",
        ),
        AIRWAY_TIMING_UNIT_ID: _support_only_reasoning_unit(
            AIRWAY_TIMING_UNIT_ID,
            _airway_timing_evidence(case_payload),
            measurement_gate,
            candidate_signal=_airway_timing_candidate_signal(case_payload),
            schema_version="airway_mouth_breathing_growth_etiology_timing_boundary_unit.1",
            candidate_key="etiology_candidate",
            support_label="airway_mouth_breathing_growth_etiology_timing_candidate",
            domains=(
                "symptom_timeline",
                "medical_record_status_or_gap",
                "orthodontic_growth_arch_jaw_position",
                "symptom_history_diagnosis_separation",
            ),
            flag_map={
                "airway_keyword_medical_diagnosis": "airway_keyword_medical_diagnosis",
                "mouth_breathing_to_expansion": "mouth_breathing_to_expansion",
                "airway_to_functional_appliance": "airway_to_functional_appliance",
                "missing_medical_record_ignored": "missing_medical_record_ignored",
                "patient_medical_tutorial": "patient_medical_tutorial",
                "source_leak": "source_leak",
            },
            metric_flag=None,
            high_conf_metric_alone_flag=None,
            support_messages={
                "symptom_timeline": "symptom timeline supports etiology candidate and records-needed only",
                "medical_record_status_or_gap": "ENT/sleep/medical record status controls medical-owner evidence gap",
                "orthodontic_growth_arch_jaw_position": "orthodontic growth/arch/jaw-position evidence supports timing review only",
                "symptom_history_diagnosis_separation": "symptom history is separated from medical diagnosis",
            },
            source_sensitivity="deidentified_medical_owner_boundary_default_projection",
            context_role="evidence_profile",
            current_case_influence_path="current-case airway symptom/medical-record/growth evidence -> etiology candidate/evidence-gap/follow-up owner only; medical diagnosis and treatment remain clinician-owned",
        ),
        FUNCTIONAL_ADVANCEMENT_UNIT_ID: _support_only_reasoning_unit(
            FUNCTIONAL_ADVANCEMENT_UNIT_ID,
            _functional_advancement_evidence(case_payload),
            measurement_gate,
            candidate_signal=_functional_advancement_candidate_signal(case_payload),
            schema_version="functional_advancement_extraction_staging_force_direction_boundary_unit.1",
            candidate_key="force_direction_candidate",
            support_label="functional_advancement_extraction_staging_force_direction_candidate",
            domains=(
                "arch_specific_goal_movement_direction",
                "extraction_indication_context",
                "force_direction_relationship",
                "appliance_efficiency_evidence",
                "patient_preference_separate",
            ),
            flag_map={
                "sgtb_to_non_extraction": "sgtb_to_non_extraction",
                "sgtb_to_same_stage_extraction": "sgtb_to_same_stage_extraction",
                "lower_force_conflict_ignored": "lower_force_conflict_ignored",
                "patient_refusal_override": "patient_refusal_override",
                "aligner_uprighting_overclaim": "aligner_uprighting_overclaim",
                "final_phase_sequence_closure": "final_phase_sequence_closure",
                "source_leak": "source_leak",
            },
            metric_flag=None,
            high_conf_metric_alone_flag=None,
            support_messages={
                "arch_specific_goal_movement_direction": "arch-specific movement direction supports force-direction review only",
                "extraction_indication_context": "extraction indication context cannot close extraction/non-extraction",
                "force_direction_relationship": "force-direction relationship may be aligned/conflicting/uncertain records needed",
                "appliance_efficiency_evidence": "appliance efficiency is review-level concern only with supporting evidence",
                "patient_preference_separate": "patient preference/refusal is individualization, not biological feasibility conclusion",
            },
            source_sensitivity="deidentified_abstract_force_direction_principle_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case arch goal/extraction context/force direction/appliance evidence/preference -> support-only treatment personalization; extraction/appliance/phase/final plan remain review_required",
        ),
        SCENE3_MODALITY_UNIT_ID: _scene3_modality_unit(case_payload, measurement_gate),
    }


def _fourth_batch_reasoning_units(
    case_payload: dict[str, Any],
    measurement_gate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        ML29F_SURFACE_CONVEX_UNIT_ID: _support_only_reasoning_unit(
            ML29F_SURFACE_CONVEX_UNIT_ID,
            _ml29f_surface_convex_evidence(case_payload),
            measurement_gate,
            candidate_signal=_ml29f_surface_convex_candidate_signal(case_payload),
            schema_version="ml29f_surface_convex_upper_source_concave_review_unit.1",
            candidate_key="review_trigger_candidate",
            support_label="ml29f_surface_convex_upper_source_concave_review_candidate",
            domains=(
                "surface_context",
                "clustered_hard_upper_source_or_concave_anchors",
            ),
            flag_map={
                "surface_convex_to_ordinary_protrusion_false_closure": "surface_convex_to_ordinary_protrusion_false_closure",
                "support_only_to_source_subtype_closure": "support_only_to_source_subtype_closure",
                "support_only_to_treatment_branch_closure": "support_only_to_treatment_branch_closure",
                "patient_source_or_tutorial_leakage": "patient_source_or_tutorial_leakage",
                "single_anchor_to_source_closure": "single_anchor_to_source_closure",
                "surface_only_concave_ruleout": "surface_only_concave_ruleout",
                "walter_correction_as_current_case_fact": "walter_correction_as_current_case_fact",
            },
            metric_flag="support_only_to_source_subtype_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "surface_context": "surface convex/lip-protrusive appearance is a context cue, not ordinary-protrusion closure",
                "clustered_hard_upper_source_or_concave_anchors": "clustered hard anchors support upper-source/concave review only",
            },
            source_sensitivity="deidentified_doctor_trace_only_default_projection",
            context_role="differential_prompt",
            current_case_influence_path="current-case surface context plus clustered hard upper-source/concave anchors -> support-only review trigger; ordinary protrusion, exact source/subtype, and treatment branch remain review_required",
        ),
        VISIBLE_PROTRUSION_GROWTH_UNIT_ID: _support_only_reasoning_unit(
            VISIBLE_PROTRUSION_GROWTH_UNIT_ID,
            _visible_protrusion_growth_window_evidence(case_payload),
            measurement_gate,
            candidate_signal=_visible_protrusion_growth_window_candidate_signal(case_payload),
            schema_version="visible_protrusion_youth_concave_ruleout_growth_window_unit.1",
            candidate_key="review_trigger_candidate",
            support_label="visible_protrusion_youth_concave_ruleout_growth_window_candidate",
            domains=(
                "visible_protrusion_youth_context",
                "concave_ruleout_or_growth_window_anchors",
            ),
            flag_map={
                "visible_protrusion_to_convex_closure_without_concave_ruleout": "visible_protrusion_to_convex_closure_without_concave_ruleout",
                "age_to_facemask_mse_treatment_closure": "age_to_facemask_mse_treatment_closure",
                "single_anchor_to_shengang_subtype_closure": "single_anchor_to_shengang_subtype_closure",
                "family_history_to_growth_prediction_closure": "family_history_to_growth_prediction_closure",
                "cvmi_marker_to_growth_timing_closure": "cvmi_marker_to_growth_timing_closure",
                "positive_overjet_to_concave_ruleout": "positive_overjet_to_concave_ruleout",
                "functional_shift_to_positional_or_skeletal_closure": "functional_shift_to_positional_or_skeletal_closure",
                "support_only_to_treatment_branch_closure": "support_only_to_treatment_branch_closure",
                "measurement_gate_bypass_for_source_growth_claim": "measurement_gate_bypass_for_source_growth_claim",
                "source_history_as_current_case_fact": "source_history_as_current_case_fact",
                "patient_growth_device_tutorial_leakage": "patient_growth_device_tutorial_leakage",
            },
            metric_flag="measurement_gate_bypass_for_source_growth_claim",
            high_conf_metric_alone_flag=None,
            support_messages={
                "visible_protrusion_youth_context": "youth visible protrusion is a weak-entry context cue, not convex/protrusion closure",
                "concave_ruleout_or_growth_window_anchors": "concave-ruleout and growth-window anchors support evidence seeking only",
            },
            source_sensitivity="deidentified_growth_window_boundary_default_projection",
            context_role="evidence_seeking",
            current_case_influence_path="current-case youth visible protrusion plus concave-ruleout/growth-window anchors -> support-only evidence seeking; diagnosis/source/subtype/growth prediction/treatment timing/device/final plan remain review_required",
        ),
        LOCKBITE_MULTIMODAL_UNIT_ID: _support_only_reasoning_unit(
            LOCKBITE_MULTIMODAL_UNIT_ID,
            _lockbite_multimodal_evidence(case_payload),
            measurement_gate,
            candidate_signal=_lockbite_multimodal_candidate_signal(case_payload),
            schema_version="lockbite_transverse_multimodal_convergence_evidence_seeking_unit.1",
            candidate_key="evidence_seeking_candidate",
            support_label="lockbite_transverse_multimodal_convergence_evidence_seeking_candidate",
            domains=(
                "lockbite_transverse_context",
                "multimodal_convergence_anchors",
            ),
            flag_map={
                "multimodal_support_to_transverse_diagnosis_closure": "multimodal_support_to_transverse_diagnosis_closure",
                "single_modality_to_transverse_or_source_closure": "single_modality_to_transverse_or_source_closure",
                "image_annotation_to_mm_or_device_closure": "image_annotation_to_mm_or_device_closure",
                "device_family_eligibility_closure": "device_family_eligibility_closure",
                "source_or_skeletal_attribution_closure": "source_or_skeletal_attribution_closure",
                "timing_or_treatment_branch_closure": "timing_or_treatment_branch_closure",
                "unilateral_mechanics_or_surgery_closure": "unilateral_mechanics_or_surgery_closure",
                "support_only_to_treatment_branch_closure": "support_only_to_treatment_branch_closure",
                "measurement_gate_bypass_for_lockbite_quant_device_claim": "measurement_gate_bypass_for_lockbite_quant_device_claim",
                "patient_image_device_tutorial_or_command_leakage": "patient_image_device_tutorial_or_command_leakage",
                "source_history_as_current_case_fact": "source_history_as_current_case_fact",
                "buccal_photo_to_lockbite_mm_closure": "buccal_photo_to_lockbite_mm_closure",
                "occlusal_circle_to_transverse_diagnosis_closure": "occlusal_circle_to_transverse_diagnosis_closure",
                "photo_to_expansion_device_closure": "photo_to_expansion_device_closure",
                "lockbite_suspicion_to_mse_marpe_rpe_sarpe_closure": "lockbite_suspicion_to_mse_marpe_rpe_sarpe_closure",
                "pa_pano_to_skeletal_asymmetry_source_closure": "pa_pano_to_skeletal_asymmetry_source_closure",
                "basal_width_without_cbct_measurement_claim": "basal_width_without_cbct_measurement_claim",
                "single_modality_to_unilateral_mechanics_closure": "single_modality_to_unilateral_mechanics_closure",
                "transverse_suspicion_to_extraction_or_surgery_plan": "transverse_suspicion_to_extraction_or_surgery_plan",
                "patient_image_reading_or_device_tutorial_leak": "patient_image_reading_or_device_tutorial_leak",
            },
            metric_flag="measurement_gate_bypass_for_lockbite_quant_device_claim",
            high_conf_metric_alone_flag=None,
            support_messages={
                "lockbite_transverse_context": "lockbite/transverse context is an evidence-seeking cue, not transverse diagnosis closure",
                "multimodal_convergence_anchors": "multimodal convergence supports reliability review only; single modality remains insufficient for closure",
            },
            source_sensitivity="deidentified_multimodal_reliability_default_projection",
            context_role="evidence_seeking",
            current_case_influence_path="current-case lockbite/transverse context plus multimodal anchors -> support-only evidence seeking and modality reliability; transverse diagnosis/source/mm/device/treatment/final plan remain review_required",
        ),
        ASYMMETRY_FOUR_FACTOR_UNIT_ID: _support_only_reasoning_unit(
            ASYMMETRY_FOUR_FACTOR_UNIT_ID,
            _asymmetry_four_factor_evidence(case_payload),
            measurement_gate,
            candidate_signal=_asymmetry_four_factor_candidate_signal(case_payload),
            schema_version="asymmetry_four_factor_plan_progression_expectation_boundary_unit.1",
            candidate_key="option_comparison_candidate",
            support_label="asymmetry_four_factor_plan_progression_expectation_boundary_candidate",
            domains=(
                "asymmetry_four_factor_context",
                "plan_progression_expectation_anchors",
            ),
            flag_map={
                "option_support_to_active_recommendation": "option_support_to_active_recommendation",
                "four_factor_to_final_plan_closure": "four_factor_to_final_plan_closure",
                "patient_goal_override": "patient_goal_override",
                "risk_tolerance_ignored": "risk_tolerance_ignored",
                "surgery_tolerance_to_surgery_closure": "surgery_tolerance_to_surgery_closure",
                "extraction_tolerance_to_extraction_closure": "extraction_tolerance_to_extraction_closure",
                "source_subtype_or_jaw_position_closure": "source_subtype_or_jaw_position_closure",
                "support_only_to_treatment_branch_closure": "support_only_to_treatment_branch_closure",
                "measurement_gate_bypass_for_asymmetry_plan_claim": "measurement_gate_bypass_for_asymmetry_plan_claim",
                "patient_plan_tutorial_or_source_leakage": "patient_plan_tutorial_or_source_leakage",
                "asymmetry_label_to_surgery_default": "asymmetry_label_to_surgery_default",
                "jaw_position_to_bony_asymmetry_solved": "jaw_position_to_bony_asymmetry_solved",
                "non_extraction_to_expected_profile_asymmetry_success": "non_extraction_to_expected_profile_asymmetry_success",
                "extraction_to_final_patient_goal_match_without_preference_review": "extraction_to_final_patient_goal_match_without_preference_review",
                "orthognathic_alternative_as_active_recommendation": "orthognathic_alternative_as_active_recommendation",
                "soft_tissue_lag_omitted_when_expectation_central": "soft_tissue_lag_omitted_when_expectation_central",
                "four_factor_to_shengang_subtype_closure": "four_factor_to_shengang_subtype_closure",
                "single_plan_option_to_final_treatment_plan": "single_plan_option_to_final_treatment_plan",
                "measurement_gate_bypass_for_asymmetry_or_cant_claim": "measurement_gate_bypass_for_asymmetry_or_cant_claim",
                "patient_device_surgery_tutorial_leakage": "patient_device_surgery_tutorial_leakage",
                "source_history_as_current_case_fact": "source_history_as_current_case_fact",
            },
            metric_flag="measurement_gate_bypass_for_asymmetry_or_cant_claim",
            high_conf_metric_alone_flag=None,
            support_messages={
                "asymmetry_four_factor_context": "four-factor asymmetry context is an option-comparison cue, not source/subtype closure",
                "plan_progression_expectation_anchors": "plan progression and expectation anchors support records-needed and follow-up ownership only",
            },
            source_sensitivity="deidentified_option_expectation_boundary_default_projection",
            context_role="option_comparison",
            current_case_influence_path="current-case asymmetry four-factor context plus expectation anchors -> support-only option comparison; source/subtype/surgery/extraction/jaw-position/final plan remain review_required and patient goals/risk tolerance constrain the discussion",
        ),
        SKELETAL_PROTRUSIVE_REALISM_UNIT_ID: _support_only_reasoning_unit(
            SKELETAL_PROTRUSIVE_REALISM_UNIT_ID,
            _skeletal_protrusive_realism_evidence(case_payload),
            measurement_gate,
            candidate_signal=_skeletal_protrusive_realism_candidate_signal(case_payload),
            schema_version="skeletal_protrusive_triangular_space_tmj_treatment_realism_unit.1",
            candidate_key="treatment_realism_candidate",
            support_label="skeletal_protrusive_triangular_space_tmj_treatment_realism_candidate",
            domains=(
                "skeletal_protrusive_triangular_context",
                "space_tmj_treatment_realism_anchors",
            ),
            flag_map={
                "mouth_protrusion_to_ordinary_dental_protrusion_closure": "mouth_protrusion_to_ordinary_dental_protrusion_closure",
                "class_ii_label_to_extraction_retraction_plan_closure": "class_ii_label_to_extraction_retraction_plan_closure",
                "triangular_modifier_to_concave_subtype_closure": "triangular_modifier_to_concave_subtype_closure",
                "triangular_modifier_dropped_from_protrusive_case": "triangular_modifier_dropped_from_protrusive_case",
                "missing_molar_to_implant_first_closure": "missing_molar_to_implant_first_closure",
                "third_molar_to_mesialization_final_plan": "third_molar_to_mesialization_final_plan",
                "vertical_mechanics_to_final_intrusion_phase_plan": "vertical_mechanics_to_final_intrusion_phase_plan",
                "tmj_history_to_device_surgery_order": "tmj_history_to_device_surgery_order",
                "stable_joint_to_no_tmj_contingency": "stable_joint_to_no_tmj_contingency",
                "support_only_to_final_treatment_plan": "support_only_to_final_treatment_plan",
                "measurement_gate_bypass_for_skeletal_vertical_space_claim": "measurement_gate_bypass_for_skeletal_vertical_space_claim",
                "raw_source_or_learning_log_leakage": "raw_source_or_learning_log_leakage",
                "patient_implant_mesialization_device_tutorial_leakage": "patient_implant_mesialization_device_tutorial_leakage",
            },
            metric_flag="measurement_gate_bypass_for_skeletal_vertical_space_claim",
            high_conf_metric_alone_flag=None,
            support_messages={
                "skeletal_protrusive_triangular_context": "skeletal protrusive/Class II plus triangular modifier is an option-comparison cue, not diagnosis/source closure",
                "space_tmj_treatment_realism_anchors": "space/restorative/vertical/TMJ anchors support treatment-realism contingency only",
            },
            source_sensitivity="deidentified_treatment_realism_contingency_default_projection",
            context_role="option_comparison",
            current_case_influence_path="current-case protrusive/Class II/triangular plus space and TMJ contingency anchors -> support-only option comparison and treatment-realism contingency; diagnosis/source/subtype/extraction/implant/mesialization/intrusion/uprighting/staging/S8/S17/splint/surgery/TMJ branch/final plan remain review_required",
        ),
    }


def _fifth_batch_reasoning_units(
    case_payload: dict[str, Any],
    measurement_gate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        EXTRACTION_SPACE_TRADEOFF_UNIT_ID: _support_only_reasoning_unit(
            EXTRACTION_SPACE_TRADEOFF_UNIT_ID,
            _extraction_space_tradeoff_evidence(case_payload),
            measurement_gate,
            candidate_signal=_extraction_space_tradeoff_candidate_signal(case_payload),
            schema_version="extraction_nonextraction_space_soft_tissue_tradeoff_unit.1",
            candidate_key="tradeoff_candidate",
            support_label="extraction_nonextraction_space_soft_tissue_tradeoff_candidate",
            domains=(
                "space_tradeoff_context",
                "soft_tissue_profile_context",
                "space_method_context",
                "measurement_records_context",
            ),
            flag_map={
                "crowding_to_extraction_plan_closure": "crowding_to_extraction_plan_closure",
                "mouth_protrusion_to_four_premolar_extraction_closure": "mouth_protrusion_to_four_premolar_extraction_closure",
                "mild_crowding_to_nonextraction_closure": "mild_crowding_to_nonextraction_closure",
                "wisdom_teeth_to_distalization_space_closure": "wisdom_teeth_to_distalization_space_closure",
                "expansion_or_ipr_to_retraction_space_claim_without_measurement": "expansion_or_ipr_to_retraction_space_claim_without_measurement",
                "straight_profile_to_extraction_contraindication_closure": "straight_profile_to_extraction_contraindication_closure",
                "patient_extraction_ipr_distalization_tutorial_leakage": "patient_extraction_ipr_distalization_tutorial_leakage",
            },
            metric_flag="expansion_or_ipr_to_retraction_space_claim_without_measurement",
            high_conf_metric_alone_flag=None,
            support_messages={
                "space_tradeoff_context": "space tradeoff context supports option comparison only",
                "soft_tissue_profile_context": "soft-tissue profile context cannot close extraction or non-extraction",
                "space_method_context": "extraction/IPR/distalization/expansion method context remains review_required",
                "measurement_records_context": "#20-quality space/profile records are required for metric-dependent claims",
            },
            source_sensitivity="deidentified_space_soft_tissue_tradeoff_default_projection",
            context_role="option_comparison",
            current_case_influence_path="current-case space/profile/method/measurement anchors -> extraction vs non-extraction tradeoff support only; extraction/IPR/distalization/expansion/retraction/final plan remain review_required",
        ),
        ADULT_RETX_BIOLOGIC_RISK_UNIT_ID: _support_only_reasoning_unit(
            ADULT_RETX_BIOLOGIC_RISK_UNIT_ID,
            _adult_retx_biologic_risk_evidence(case_payload),
            measurement_gate,
            candidate_signal=_adult_retx_biologic_risk_candidate_signal(case_payload),
            schema_version="adult_retx_periodontal_root_tmj_biologic_risk_boundary_unit.1",
            candidate_key="biologic_risk_candidate",
            support_label="adult_retx_periodontal_root_tmj_biologic_risk_boundary_candidate",
            domains=(
                "adult_retx_context",
                "periodontal_root_records",
                "tmj_biologic_risk_context",
                "movement_load_followup_context",
            ),
            flag_map={
                "adult_age_to_orthodontic_contraindication_closure": "adult_age_to_orthodontic_contraindication_closure",
                "adult_aesthetic_complaint_to_treatment_plan_closure": "adult_aesthetic_complaint_to_treatment_plan_closure",
                "root_resorption_mention_to_no_treatment_or_safe_treatment_closure": "root_resorption_mention_to_no_treatment_or_safe_treatment_closure",
                "periodontal_or_bone_loss_to_movement_plan_without_periodontal_record": "periodontal_or_bone_loss_to_movement_plan_without_periodontal_record",
                "tmj_symptom_to_retx_plan_or_stabilization_closure": "tmj_symptom_to_retx_plan_or_stabilization_closure",
                "patient_biologic_risk_tutorial_or_fear_leakage": "patient_biologic_risk_tutorial_or_fear_leakage",
            },
            metric_flag="periodontal_or_bone_loss_to_movement_plan_without_periodontal_record",
            high_conf_metric_alone_flag=None,
            support_messages={
                "adult_retx_context": "adult/retreatment context is a biologic-risk review cue only",
                "periodontal_root_records": "periodontal/root records are required before movement-load claims",
                "tmj_biologic_risk_context": "TMJ biologic risk context constrains treatment timing only after review",
                "movement_load_followup_context": "movement-load and follow-up owner remain doctor-reviewed",
            },
            source_sensitivity="deidentified_biologic_risk_boundary_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case adult retreatment, periodontal/root, TMJ, and movement-load anchors -> biologic-risk boundary support only; no-treatment/safe-treatment/movement plan/stabilization/final plan remain review_required",
        ),
        ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID: _support_only_reasoning_unit(
            ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID,
            _orthognathic_camouflage_evidence(case_payload),
            measurement_gate,
            candidate_signal=_orthognathic_camouflage_candidate_signal(case_payload),
            schema_version="orthognathic_camouflage_severity_expectation_boundary_unit.1",
            candidate_key="expectation_boundary_candidate",
            support_label="orthognathic_camouflage_severity_expectation_boundary_candidate",
            domains=(
                "severity_context",
                "camouflage_surgery_option_context",
                "expectation_preference_context",
                "measurement_records_context",
            ),
            flag_map={
                "severe_skeletal_label_to_surgery_recommendation_closure": "severe_skeletal_label_to_surgery_recommendation_closure",
                "patient_refuses_surgery_to_camouflage_final_plan": "patient_refuses_surgery_to_camouflage_final_plan",
                "mild_skeletal_discrepancy_to_no_surgery_or_surgery_closure": "mild_skeletal_discrepancy_to_no_surgery_or_surgery_closure",
                "orthodontic_can_improve_to_expected_profile_success_closure": "orthodontic_can_improve_to_expected_profile_success_closure",
                "surgery_boundary_to_patient_surgical_tutorial_leakage": "surgery_boundary_to_patient_surgical_tutorial_leakage",
                "camouflage_option_to_final_treatment_plan_closure": "camouflage_option_to_final_treatment_plan_closure",
            },
            metric_flag="mild_skeletal_discrepancy_to_no_surgery_or_surgery_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "severity_context": "severity context supports boundary review only",
                "camouflage_surgery_option_context": "camouflage/surgery option context is option-comparison support, not recommendation closure",
                "expectation_preference_context": "expectation and preference context constrain the boundary but do not close a plan",
                "measurement_records_context": "#20-quality records are required for severity-dependent claims",
            },
            source_sensitivity="deidentified_orthognathic_camouflage_expectation_default_projection",
            context_role="option_comparison",
            current_case_influence_path="current-case severity, option, expectation/preference, and measurement anchors -> orthognathic/camouflage expectation-boundary support only; surgery/camouflage/no-surgery/profile-success/final-plan remain review_required",
        ),
        DEEPBITE_BITE_OPENING_UNIT_ID: _support_only_reasoning_unit(
            DEEPBITE_BITE_OPENING_UNIT_ID,
            _deepbite_bite_opening_evidence(case_payload),
            measurement_gate,
            candidate_signal=_deepbite_bite_opening_candidate_signal(case_payload),
            schema_version="deepbite_spee_tmj_compensation_bite_opening_boundary_unit.1",
            candidate_key="bite_opening_candidate",
            support_label="deepbite_spee_tmj_compensation_bite_opening_boundary_candidate",
            domains=(
                "deepbite_spee_context",
                "tmj_compensation_context",
                "bite_opening_method_context",
                "growth_stage_context",
            ),
            flag_map={
                "deepbite_label_to_tmj_pathology_closure": "deepbite_label_to_tmj_pathology_closure",
                "deepbite_label_to_s8_or_jaw_position_reconstruction_closure": "deepbite_label_to_s8_or_jaw_position_reconstruction_closure",
                "mixed_dentition_deepbite_to_immediate_treatment_closure": "mixed_dentition_deepbite_to_immediate_treatment_closure",
                "deep_curve_spee_to_intrusion_or_extrusion_plan_closure": "deep_curve_spee_to_intrusion_or_extrusion_plan_closure",
                "attrition_or_opening_path_to_final_phase_sequence": "attrition_or_opening_path_to_final_phase_sequence",
                "patient_deepbite_device_or_tmj_tutorial_leakage": "patient_deepbite_device_or_tmj_tutorial_leakage",
            },
            metric_flag="deep_curve_spee_to_intrusion_or_extrusion_plan_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "deepbite_spee_context": "deepbite/Spee context supports bite-opening boundary review only",
                "tmj_compensation_context": "TMJ/compensation context remains source/pathology review_required",
                "bite_opening_method_context": "bite-opening method context cannot close intrusion/extrusion/device/phase",
                "growth_stage_context": "growth-stage context constrains timing review but cannot close immediate treatment",
            },
            source_sensitivity="deidentified_deepbite_spee_tmj_boundary_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case deepbite/Spee, TMJ/compensation, method, and growth-stage anchors -> bite-opening boundary support only; TMJ pathology, S8/jaw-position reconstruction, intrusion/extrusion/device/phase/final plan remain review_required",
        ),
        OPENBITE_VERTICAL_CONTROL_UNIT_ID: _support_only_reasoning_unit(
            OPENBITE_VERTICAL_CONTROL_UNIT_ID,
            _openbite_vertical_control_evidence(case_payload),
            measurement_gate,
            candidate_signal=_openbite_vertical_control_candidate_signal(case_payload),
            schema_version="openbite_etiology_vertical_control_treatment_boundary_unit.1",
            candidate_key="vertical_control_candidate",
            support_label="openbite_etiology_vertical_control_treatment_boundary_candidate",
            domains=(
                "openbite_context",
                "etiology_source_family_context",
                "vertical_control_context",
                "function_airway_tmj_records_context",
            ),
            flag_map={
                "openbite_label_to_posterior_intrusion_plan_closure": "openbite_label_to_posterior_intrusion_plan_closure",
                "openbite_label_to_tongue_habit_or_airway_source_closure": "openbite_label_to_tongue_habit_or_airway_source_closure",
                "adult_openbite_to_condylar_absorption_closure": "adult_openbite_to_condylar_absorption_closure",
                "high_angle_to_intrusion_without_smile_or_vertical_record": "high_angle_to_intrusion_without_smile_or_vertical_record",
                "low_angle_to_anterior_extrusion_without_smile_or_function_review": "low_angle_to_anterior_extrusion_without_smile_or_function_review",
                "patient_openbite_exercise_or_device_tutorial_leakage": "patient_openbite_exercise_or_device_tutorial_leakage",
            },
            metric_flag="high_angle_to_intrusion_without_smile_or_vertical_record",
            high_conf_metric_alone_flag=None,
            support_messages={
                "openbite_context": "openbite context supports etiology/vertical-control review only",
                "etiology_source_family_context": "etiology/source-family context cannot close habit, airway, or condylar source",
                "vertical_control_context": "vertical-control context remains treatment-boundary support",
                "function_airway_tmj_records_context": "function/airway/TMJ records are required before source or treatment claims",
            },
            source_sensitivity="deidentified_openbite_etiology_vertical_control_default_projection",
            context_role="evidence_profile",
            current_case_influence_path="current-case openbite, etiology/source-family, vertical-control, and function/airway/TMJ records -> support-only boundary; posterior intrusion, habit/airway/condylar source, extrusion/intrusion/device/final plan remain review_required",
        ),
    }


def _sixth_batch_reasoning_units(
    case_payload: dict[str, Any],
    measurement_gate: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        CASE_MATURITY_UNIT_ID: _support_only_reasoning_unit(
            CASE_MATURITY_UNIT_ID,
            _case_maturity_evidence(case_payload),
            measurement_gate,
            candidate_signal=_case_maturity_candidate_signal(case_payload),
            schema_version="case_maturity_framework_selection_boundary_unit.1",
            candidate_key="case_maturity_candidate",
            support_label="case_maturity_framework_selection_boundary_candidate",
            domains=(
                "case_stage_signal_context",
                "current_care_status_context",
                "downstream_unit_gating_context",
                "review_owner_context",
            ),
            flag_map={
                "maturity_label_to_diagnosis_closure": "maturity_label_to_diagnosis_closure",
                "maturity_label_to_mechanics_plan_closure": "maturity_label_to_mechanics_plan_closure",
                "plan_animation_context_to_plan_approval": "plan_animation_context_to_plan_approval",
                "retention_context_to_stability_success": "retention_context_to_stability_success",
                "progress_review_to_new_treatment_plan": "progress_review_to_new_treatment_plan",
                "patient_case_stage_instruction_leakage": "patient_case_stage_instruction_leakage",
            },
            metric_flag=None,
            high_conf_metric_alone_flag=None,
            support_messages={
                "case_stage_signal_context": "case-stage signal supports framework selection only",
                "current_care_status_context": "current care status cannot close diagnosis or treatment",
                "downstream_unit_gating_context": "downstream-unit gating remains review_required",
                "review_owner_context": "review owner is required before progression or plan changes",
            },
            source_sensitivity="deidentified_case_maturity_framework_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case stage/status/gating/review-owner anchors -> case-maturity framework support only; diagnosis/mechanics/plan approval/retention success/new treatment plan remain review_required",
        ),
        ACTIVE_MECHANICS_STALL_UNIT_ID: _support_only_reasoning_unit(
            ACTIVE_MECHANICS_STALL_UNIT_ID,
            _active_mechanics_stall_evidence(case_payload),
            measurement_gate,
            candidate_signal=_active_mechanics_stall_candidate_signal(case_payload),
            schema_version="active_treatment_mechanics_stall_boundary_unit.1",
            candidate_key="mechanics_stall_candidate",
            support_label="active_treatment_mechanics_stall_boundary_candidate",
            domains=(
                "active_treatment_context",
                "appliance_stage_context",
                "stall_symptom_measurement_context",
                "tmj_or_biologic_risk_review_context",
                "review_owner_context",
            ),
            flag_map={
                "stuck_to_force_increase_closure": "stuck_to_force_increase_closure",
                "aligner_tracking_issue_to_aligner_change_instruction": "aligner_tracking_issue_to_aligner_change_instruction",
                "mechanics_stall_to_elastics_or_attachment_plan": "mechanics_stall_to_elastics_or_attachment_plan",
                "stall_to_intrusion_or_extrusion_plan": "stall_to_intrusion_or_extrusion_plan",
                "active_treatment_to_extraction_or_ipr_closure": "active_treatment_to_extraction_or_ipr_closure",
                "patient_active_mechanics_instruction_leakage": "patient_active_mechanics_instruction_leakage",
            },
            metric_flag="stall_to_intrusion_or_extrusion_plan",
            high_conf_metric_alone_flag=None,
            support_messages={
                "active_treatment_context": "active-treatment context is a stall-review cue only",
                "appliance_stage_context": "appliance stage supports records-needed review, not aligner/device change",
                "stall_symptom_measurement_context": "stall symptoms and measurements remain support-only until doctor review",
                "tmj_or_biologic_risk_review_context": "TMJ/biologic-risk context constrains mechanics review only",
                "review_owner_context": "review owner is required before mechanics changes",
            },
            source_sensitivity="deidentified_active_mechanics_stall_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case active-treatment/appliance/stall/TMJ-risk/review-owner anchors -> mechanics-stall support only; force/aligner/elastics/attachments/intrusion/extrusion/extraction/IPR/final mechanics remain review_required",
        ),
        DIGITAL_SETUP_AUDIT_UNIT_ID: _support_only_reasoning_unit(
            DIGITAL_SETUP_AUDIT_UNIT_ID,
            _digital_setup_audit_evidence(case_payload),
            measurement_gate,
            candidate_signal=_digital_setup_audit_candidate_signal(case_payload),
            schema_version="digital_setup_animation_mechanics_audit_boundary_unit.1",
            candidate_key="setup_audit_candidate",
            support_label="digital_setup_animation_mechanics_audit_boundary_candidate",
            domains=(
                "setup_animation_context",
                "objective_space_source_context",
                "anchorage_step_size_context",
                "appliance_combination_context",
                "doctor_audit_focus_context",
            ),
            flag_map={
                "setup_animation_to_plan_approval_closure": "setup_animation_to_plan_approval_closure",
                "animation_step_size_to_safety_closure": "animation_step_size_to_safety_closure",
                "space_source_to_extraction_position_closure": "space_source_to_extraction_position_closure",
                "anchorage_label_to_anchor_plan_closure": "anchorage_label_to_anchor_plan_closure",
                "appliance_combo_to_device_choice_closure": "appliance_combo_to_device_choice_closure",
                "patient_aligner_setup_tutorial_leakage": "patient_aligner_setup_tutorial_leakage",
            },
            metric_flag="animation_step_size_to_safety_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "setup_animation_context": "setup animation supports audit focus only",
                "objective_space_source_context": "objective space-source context cannot close extraction position",
                "anchorage_step_size_context": "anchorage and step-size context remain doctor audit inputs",
                "appliance_combination_context": "appliance combination context cannot close device choice",
                "doctor_audit_focus_context": "doctor audit focus is required before plan approval",
            },
            source_sensitivity="deidentified_setup_animation_audit_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case setup/space-source/anchorage/step-size/appliance/audit anchors -> setup audit support only; setup approval/safety/extraction position/anchorage/device/final plan remain review_required",
        ),
        RETENTION_RELAPSE_UNIT_ID: _support_only_reasoning_unit(
            RETENTION_RELAPSE_UNIT_ID,
            _retention_relapse_evidence(case_payload),
            measurement_gate,
            candidate_signal=_retention_relapse_candidate_signal(case_payload),
            schema_version="retention_relapse_stability_monitoring_boundary_unit.1",
            candidate_key="retention_monitoring_candidate",
            support_label="retention_relapse_stability_monitoring_boundary_candidate",
            domains=(
                "retention_context",
                "relapse_signal_context",
                "duration_recall_record_context",
                "wisdom_habit_expansion_context",
                "periodontal_tmj_followup_context",
            ),
            flag_map={
                "retention_status_to_retainer_schedule_instruction": "retention_status_to_retainer_schedule_instruction",
                "relapse_signal_to_relapse_diagnosis_closure": "relapse_signal_to_relapse_diagnosis_closure",
                "wisdom_teeth_to_relapse_causality_closure": "wisdom_teeth_to_relapse_causality_closure",
                "expansion_history_to_stability_claim_closure": "expansion_history_to_stability_claim_closure",
                "retention_context_to_new_treatment_plan": "retention_context_to_new_treatment_plan",
                "patient_retainer_or_relapse_tutorial_leakage": "patient_retainer_or_relapse_tutorial_leakage",
            },
            metric_flag="expansion_history_to_stability_claim_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "retention_context": "retention context supports monitoring review only",
                "relapse_signal_context": "relapse signal context cannot close relapse diagnosis",
                "duration_recall_record_context": "duration/recall records constrain monitoring but do not close schedule",
                "wisdom_habit_expansion_context": "wisdom/habit/expansion history cannot close causality or stability success",
                "periodontal_tmj_followup_context": "periodontal/TMJ follow-up context remains review_required",
            },
            source_sensitivity="deidentified_retention_relapse_monitoring_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case retention/relapse/duration/wisdom-habit-expansion/periodontal-TMJ anchors -> monitoring support only; schedule/diagnosis/causality/stability/disease/new-plan remain review_required",
        ),
        TOOTH_SIZE_FINISHING_UNIT_ID: _support_only_reasoning_unit(
            TOOTH_SIZE_FINISHING_UNIT_ID,
            _tooth_size_finishing_evidence(case_payload),
            measurement_gate,
            candidate_signal=_tooth_size_finishing_candidate_signal(case_payload),
            schema_version="tooth_size_bolton_fusion_midline_finishing_boundary_unit.1",
            candidate_key="finishing_detail_candidate",
            support_label="tooth_size_bolton_fusion_midline_finishing_boundary_candidate",
            domains=(
                "tooth_size_bolton_context",
                "abnormal_missing_fused_tooth_context",
                "midline_single_tooth_detail_context",
                "limited_ortho_ipr_restorative_context",
                "measurement_records_context",
            ),
            flag_map={
                "midline_deviation_to_asymmetry_source_closure": "midline_deviation_to_asymmetry_source_closure",
                "tooth_size_label_to_bolton_diagnosis_closure": "tooth_size_label_to_bolton_diagnosis_closure",
                "bolton_issue_to_ipr_or_restorative_plan": "bolton_issue_to_ipr_or_restorative_plan",
                "fused_or_missing_tooth_to_limited_ortho_candidacy": "fused_or_missing_tooth_to_limited_ortho_candidacy",
                "finishing_detail_to_final_occlusion_success": "finishing_detail_to_final_occlusion_success",
                "patient_ipr_restorative_tutorial_leakage": "patient_ipr_restorative_tutorial_leakage",
            },
            metric_flag="tooth_size_label_to_bolton_diagnosis_closure",
            high_conf_metric_alone_flag=None,
            support_messages={
                "tooth_size_bolton_context": "tooth-size/Bolton context supports finishing-detail review only",
                "abnormal_missing_fused_tooth_context": "abnormal/missing/fused tooth context cannot close limited-ortho candidacy",
                "midline_single_tooth_detail_context": "midline/single-tooth detail cannot close asymmetry source",
                "limited_ortho_ipr_restorative_context": "limited-ortho/IPR/restorative context remains option review only",
                "measurement_records_context": "#20-quality measurements are required for metric-dependent finishing claims",
            },
            source_sensitivity="deidentified_bolton_midline_finishing_default_projection",
            context_role="treatment_boundary",
            current_case_influence_path="current-case tooth-size/Bolton/abnormal-tooth/midline/limited-ortho/measurement anchors -> finishing-detail support only; source/diagnosis/IPR-restorative/extraction/candidacy/final occlusion/completion remain review_required",
        ),
    }


def _scene3_modality_unit(case_payload: dict[str, Any], measurement_gate: dict[str, Any]) -> dict[str, Any]:
    evidence = _scene3_modality_evidence(case_payload)
    unit = _support_only_reasoning_unit(
        SCENE3_MODALITY_UNIT_ID,
        evidence,
        measurement_gate,
        candidate_signal=_scene3_modality_candidate_signal(case_payload),
        schema_version="scene3_modality_reliability_evidence_seeking_boundary_unit.1",
        candidate_key="modality_candidate",
        support_label="scene3_modality_reliability_candidate",
        domains=(
            "modality_type_quality_confidence",
            "qualitative_vs_quantitative_claim",
            "validated_head_position_for_asymmetry",
            "confirmatory_modality_owner",
            "measurement_gate_for_numeric_claim",
        ),
        flag_map={
            "pano_shadow_pathology_closure": "pano_shadow_pathology_closure",
            "condyle_outline_tmd_closure": "condyle_outline_tmd_closure",
            "pa_screenshot_quant_closure": "pa_screenshot_quant_closure",
            "head_position_uncertain_canting_closure": "head_position_uncertain_canting_closure",
            "command_language": "command_language",
            "patient_tutorial_or_source_leak": "patient_tutorial_or_source_leak",
        },
        metric_flag="pa_screenshot_quant_closure",
        high_conf_metric_alone_flag=None,
        support_messages={
            "modality_type_quality_confidence": "modality type/quality/confidence controls qualitative vs confirmatory evidence path",
            "qualitative_vs_quantitative_claim": "qualitative claim can stay support-only while quantitative claims require #20",
            "validated_head_position_for_asymmetry": "asymmetry/canting claims require validated head position",
            "confirmatory_modality_owner": "confirmatory modality owner is required when uncertainty affects diagnosis/treatment",
            "measurement_gate_for_numeric_claim": "#20 measurement gate is required for numeric/metric claims",
        },
        source_sensitivity="deidentified_modality_reliability_boundary_default_projection",
        context_role="evidence_seeking",
        current_case_influence_path="current-case modality confidence/head-position/claim type -> evidence-seeking and projection boundary only; pathology/TMD/asymmetry/source/treatment closure remains review_required",
    )
    unit["modality_confidence"] = evidence.get("modality_confidence") or {
        "modality_type": evidence.get("modality_type") or "unknown",
        "quality_confidence": evidence.get("quality_confidence") or "missing",
        "validated_head_position": bool(evidence.get("validated_head_position_for_asymmetry")),
        "claim_type": evidence.get("claim_type") or "qualitative_or_unspecified",
    }
    unit["confirmatory_modality_needed"] = evidence.get("confirmatory_modality_needed") or unit.get("records_needed")
    unit["doctor_wording_boundary"] = {
        "allowed": ["建议医生结合", "建议补充", "建议确认", "需要医生复核"],
        "forbidden_low_confidence": ["必须", "强制", "direct patient command"],
    }
    return unit


def _reasoning_unit_projection(unit_id: str) -> dict[str, Any]:
    try:
        unit = get_reasoning_unit(unit_id)
    except Exception:
        return {"unit_id": unit_id, "unit_type": "reasoning_role_row", "may_enter_final_conclusion": False}
    return {
        "unit_id": unit["unit_id"],
        "unit_type": unit["unit_type"],
        "runtime_roles": unit["runtime_roles"],
        "source_sensitivity": unit["source_sensitivity"],
        "may_enter_final_conclusion": unit["may_enter_final_conclusion"],
        "may_support_finalization": unit["may_support_finalization"],
        "required_evidence_domains": unit["required_evidence_domains"],
        "treatment_boundary": unit["treatment_boundary"],
    }


def _unit_evidence(case_payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = case_payload.get(key)
        if isinstance(value, dict):
            return deepcopy(value)
    return {}


def _concave_asymmetry_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "concave_asymmetry_differential_evidence", CONCAVE_ASYMMETRY_UNIT_ID)


def _true_concave_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "true_concave_false_protrusive_evidence", TRUE_CONCAVE_UNIT_ID)


def _vertical_sagittal_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "vertical_sagittal_evidence", VERTICAL_SAGITTAL_UNIT_ID)


def _retx_source_space_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "retreatment_extraction_history_evidence", RETX_SOURCE_SPACE_UNIT_ID)


def _tmd_revise_persist_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "tmd_revise_persist_evidence", TMD_REVISE_PERSIST_UNIT_ID)


def _space_budget_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "space_budget_evidence", SPACE_BUDGET_UNIT_ID)


def _protrusive_jaw_position_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "protrusive_jaw_position_mixed_subtype_evidence", PROTRUSIVE_JAW_POSITION_UNIT_ID)


def _airway_timing_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "airway_mouth_breathing_evidence", AIRWAY_TIMING_UNIT_ID)


def _functional_advancement_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "functional_advancement_staging_evidence", FUNCTIONAL_ADVANCEMENT_UNIT_ID)


def _scene3_modality_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(case_payload, "scene3_modality_evidence", SCENE3_MODALITY_UNIT_ID)


def _ml29f_surface_convex_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "ml29f_surface_convex_upper_source_evidence",
        ML29F_SURFACE_CONVEX_UNIT_ID,
    )


def _visible_protrusion_growth_window_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "visible_protrusion_youth_growth_window_evidence",
        VISIBLE_PROTRUSION_GROWTH_UNIT_ID,
    )


def _lockbite_multimodal_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "lockbite_transverse_multimodal_evidence",
        LOCKBITE_MULTIMODAL_UNIT_ID,
    )


def _asymmetry_four_factor_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "asymmetry_four_factor_plan_progression_evidence",
        ASYMMETRY_FOUR_FACTOR_UNIT_ID,
    )


def _skeletal_protrusive_realism_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "skeletal_protrusive_triangular_space_tmj_evidence",
        SKELETAL_PROTRUSIVE_REALISM_UNIT_ID,
    )


def _extraction_space_tradeoff_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "extraction_nonextraction_space_soft_tissue_evidence",
        EXTRACTION_SPACE_TRADEOFF_UNIT_ID,
    )


def _adult_retx_biologic_risk_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "adult_retx_periodontal_root_tmj_biologic_risk_evidence",
        ADULT_RETX_BIOLOGIC_RISK_UNIT_ID,
    )


def _orthognathic_camouflage_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "orthognathic_camouflage_severity_expectation_evidence",
        ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID,
    )


def _deepbite_bite_opening_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "deepbite_spee_tmj_compensation_bite_opening_evidence",
        DEEPBITE_BITE_OPENING_UNIT_ID,
    )


def _openbite_vertical_control_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "openbite_etiology_vertical_control_evidence",
        OPENBITE_VERTICAL_CONTROL_UNIT_ID,
    )


def _case_maturity_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "case_maturity_framework_selection_evidence",
        CASE_MATURITY_UNIT_ID,
    )


def _active_mechanics_stall_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "active_treatment_mechanics_stall_evidence",
        ACTIVE_MECHANICS_STALL_UNIT_ID,
    )


def _digital_setup_audit_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "digital_setup_animation_mechanics_audit_evidence",
        DIGITAL_SETUP_AUDIT_UNIT_ID,
    )


def _retention_relapse_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "retention_relapse_stability_monitoring_evidence",
        RETENTION_RELAPSE_UNIT_ID,
    )


def _tooth_size_finishing_evidence(case_payload: dict[str, Any]) -> dict[str, Any]:
    return _unit_evidence(
        case_payload,
        "tooth_size_bolton_fusion_midline_finishing_evidence",
        TOOTH_SIZE_FINISHING_UNIT_ID,
    )


def _concave_asymmetry_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _concave_asymmetry_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("concave", "Class III", "asymmetry", "dental Class III", "凹面", "偏斜", "反𬌗"))


def _true_concave_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _true_concave_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("false protrusive", "true concave", "TCFP16", "false_protrusive", "假性嘴突"))


def _vertical_sagittal_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _vertical_sagittal_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("FMA", "SN-GoGn", "high angle", "deepbite", "gummy", "Spee", "vertical", "垂直", "露龈"))


def _retx_source_space_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _retx_source_space_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("retreatment", "prior orthodont", "extraction history", "missing tooth", "residual root", "space", "复诊", "拔牙史", "缺牙"))


def _tmd_revise_persist_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _tmd_revise_persist_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("TMD", "TMJ", "clicking", "MRI", "disc", "joint", "关节", "弹响", "盘移位"))


def _space_budget_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _space_budget_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("second molar", "7s", "eruption", "space budget", "crowding", "mesial drift", "第二磨牙", "间隙"))


def _protrusive_jaw_position_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _protrusive_jaw_position_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("U1-SN", "near-neutral molar", "forward jaw simulation", "mixed subtype", "jaw-position", "下颌后缩"))


def _airway_timing_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _airway_timing_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("mouth breathing", "airway", "snoring", "ENT", "adenoid", "tonsil", "口呼吸", "气道", "打鼾"))


def _functional_advancement_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _functional_advancement_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("functional advancement", "force direction", "extraction staging", "aligner", "patient refusal", "功能前导", "支抗"))


def _scene3_modality_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _scene3_modality_evidence(case_payload):
        return True
    text = repr(case_payload)
    return any(term in text for term in ("pano", "PA screenshot", "face photo", "intraoral photo", "CBCT screenshot", "head position", "全景片", "头位"))


def _ml29f_surface_convex_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _ml29f_surface_convex_evidence(case_payload):
        return True
    text = repr(case_payload)
    surface_hit = any(term in text for term in ("surface convex", "lip protrusive", "bimax protrusive", "incisor proclination", "突面", "唇突", "双突"))
    hard_anchor_hit = any(term in text for term in ("lower lip ahead", "crossbite", "mesial molar", "maxillary support deficiency", "paranasal deficiency", "上颌支撑不足", "反𬌗"))
    return surface_hit and hard_anchor_hit


def _visible_protrusion_growth_window_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _visible_protrusion_growth_window_evidence(case_payload):
        return True
    text = repr(case_payload)
    weak_entry_hit = any(term in text for term in ("visible protrusion", "mouth protrusion", "positive overjet", "upper incisor proclination", "嘴突", "前突", "覆盖"))
    growth_or_ruleout_hit = any(term in text for term in ("age 6", "age 7", "age 8", "age 9", "age 10", "age 11", "age 12", "age 13", "age 14", "CVMI", "bone age", "family Class III", "anterior crossbite", "paranasal", "functional shift", "生长", "家族史", "功能偏斜", "鼻旁"))
    return weak_entry_hit and growth_or_ruleout_hit


def _lockbite_multimodal_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _lockbite_multimodal_evidence(case_payload):
        return True
    text = repr(case_payload)
    lockbite_hit = any(term in text for term in ("locked bite", "lockbite", "posterior crossbite", "scissor bite", "transverse discrepancy", "锁𬌗", "反𬌗", "横向"))
    multimodal_hit = any(term in text for term in ("intraoral photo", "cast", "scan", "PA", "CBCT", "occlusal view", "multimodal", "modality", "口扫", "模型", "影像"))
    return lockbite_hit and multimodal_hit


def _asymmetry_four_factor_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _asymmetry_four_factor_evidence(case_payload):
        return True
    text = repr(case_payload)
    asymmetry_hit = any(term in text for term in ("asymmetry", "cant", "midline", "chin deviation", "facial deviation", "偏斜", "中线", "颏偏", "咬合平面"))
    planning_hit = any(term in text for term in ("option comparison", "expectation", "patient goal", "risk tolerance", "records needed", "follow-up owner", "surgery tolerance", "extraction tolerance", "方案", "预期", "风险", "复查"))
    return asymmetry_hit and planning_hit


def _skeletal_protrusive_realism_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _skeletal_protrusive_realism_evidence(case_payload):
        return True
    text = repr(case_payload)
    protrusive_hit = any(term in text for term in ("skeletal protrusive", "Class II", "protrusive", "mandibular retrusion", "凸面", "下颌后缩", "嘴突"))
    realism_hit = any(term in text for term in ("triangular", "vertical space", "missing space", "restorative space", "TMJ", "TMD", "splint", "treatment realism", "contingency", "三角", "间隙", "关节"))
    return protrusive_hit and realism_hit


def _extraction_space_tradeoff_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _extraction_space_tradeoff_evidence(case_payload):
        return True
    text = repr(case_payload)
    space_hit = any(term in text for term in ("crowding", "space", "extraction", "IPR", "distalization", "wisdom teeth", "拥挤", "间隙", "拔牙", "片切", "远移"))
    profile_hit = any(term in text for term in ("soft tissue", "profile", "lip protrusion", "mouth protrusion", "straight profile", "侧貌", "嘴突", "唇突"))
    return space_hit and profile_hit


def _adult_retx_biologic_risk_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _adult_retx_biologic_risk_evidence(case_payload):
        return True
    text = repr(case_payload)
    adult_retx_hit = any(term in text for term in ("adult", "retreatment", "prior orthodont", "二次矫正", "成人", "复治", "复诊"))
    biologic_hit = any(term in text for term in ("periodontal", "bone loss", "root resorption", "root length", "TMJ", "TMD", "牙周", "骨丧失", "牙根吸收", "关节"))
    return adult_retx_hit and biologic_hit


def _orthognathic_camouflage_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _orthognathic_camouflage_evidence(case_payload):
        return True
    text = repr(case_payload)
    severity_hit = any(term in text for term in ("severe skeletal", "orthognathic", "camouflage", "surgery", "skeletal discrepancy", "正颌", "掩饰", "骨性", "手术"))
    expectation_hit = any(term in text for term in ("expectation", "profile", "refuses surgery", "patient preference", "改善", "预期", "拒绝手术", "侧貌"))
    return severity_hit and expectation_hit


def _deepbite_bite_opening_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _deepbite_bite_opening_evidence(case_payload):
        return True
    text = repr(case_payload)
    deepbite_hit = any(term in text for term in ("deepbite", "deep bite", "curve of Spee", "Spee", "overbite", "深覆", "Spee曲线"))
    boundary_hit = any(term in text for term in ("TMJ", "TMD", "attrition", "bite opening", "intrusion", "extrusion", "mixed dentition", "关节", "磨耗", "打开咬合", "压低", "伸长", "替牙"))
    return deepbite_hit and boundary_hit


def _openbite_vertical_control_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _openbite_vertical_control_evidence(case_payload):
        return True
    text = repr(case_payload)
    openbite_hit = any(term in text for term in ("openbite", "open bite", "anterior open bite", "开𬌗", "开合"))
    etiology_hit = any(term in text for term in ("tongue habit", "airway", "condylar absorption", "posterior intrusion", "high angle", "low angle", "smile", "vertical control", "舌习惯", "气道", "髁突吸收", "后牙压低", "高角", "低角", "露笑"))
    return openbite_hit and etiology_hit


def _case_maturity_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _case_maturity_evidence(case_payload):
        return True
    text = repr(case_payload)
    stage_hit = any(term in text for term in ("case stage", "maturity", "progress review", "active treatment", "retention", "阶段", "复查", "保持"))
    gating_hit = any(term in text for term in ("framework", "downstream unit", "plan animation", "review owner", "gating", "框架", "分流", "审核"))
    return stage_hit and gating_hit


def _active_mechanics_stall_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _active_mechanics_stall_evidence(case_payload):
        return True
    text = repr(case_payload)
    active_hit = any(term in text for term in ("active treatment", "aligner tracking", "appliance stage", "stuck", "stall", "治疗中", "牙套", "卡住", "停滞"))
    mechanics_hit = any(term in text for term in ("force", "elastics", "attachment", "intrusion", "extrusion", "IPR", "biologic risk", "牵引", "附件", "压低", "伸长", "片切"))
    return active_hit and mechanics_hit


def _digital_setup_audit_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _digital_setup_audit_evidence(case_payload):
        return True
    text = repr(case_payload)
    setup_hit = any(term in text for term in ("digital setup", "setup animation", "ClinCheck", "animation", "aligner setup", "方案动画", "模拟"))
    audit_hit = any(term in text for term in ("step size", "anchorage", "appliance combination", "space source", "doctor audit", "支抗", "步距", "间隙来源", "审核"))
    return setup_hit and audit_hit


def _retention_relapse_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _retention_relapse_evidence(case_payload):
        return True
    text = repr(case_payload)
    retention_hit = any(term in text for term in ("retention", "retainer", "relapse", "stability", "保持", "保持器", "复发", "稳定"))
    monitoring_hit = any(term in text for term in ("recall", "wisdom tooth", "habit", "expansion history", "periodontal", "TMJ", "复诊", "智齿", "习惯", "扩弓", "牙周", "关节"))
    return retention_hit and monitoring_hit


def _tooth_size_finishing_candidate_signal(case_payload: dict[str, Any]) -> bool:
    if _tooth_size_finishing_evidence(case_payload):
        return True
    text = repr(case_payload)
    finishing_hit = any(term in text for term in ("Bolton", "tooth size", "fused tooth", "missing tooth", "midline", "finishing", "牙量", "融合牙", "缺牙", "中线", "精调"))
    plan_hit = any(term in text for term in ("IPR", "restorative", "limited ortho", "final occlusion", "measurement", "片切", "修复", "有限矫治", "咬合"))
    return finishing_hit and plan_hit


def _measurement_closure_gate(
    case_payload: dict[str, Any],
    *,
    close_diagnosis: str,
    close_source: str,
    close_treatment: str,
) -> dict[str, Any]:
    measurements = _measurement_evidence_items(case_payload)
    metric_dependent = [item for item in measurements if _used_for_closure(item)]
    review_flags: list[str] = []
    missing_evidence: list[str] = []
    low_confidence_metrics: list[str] = []
    visual_metrics: list[str] = []
    dental_relation_metrics: list[str] = []
    high_confidence_metrics: list[str] = []
    metrics_used_for_closure: list[dict[str, Any]] = []
    axes_closed = {
        "close_diagnosis": close_diagnosis == "closed",
        "close_source_or_subtype": close_source == "closed",
        "close_treatment_branch": close_treatment == "closed",
    }
    effect_on_closure = {
        "close_diagnosis": "allow" if close_diagnosis == "closed" else "review_required",
        "close_source_or_subtype": "allow" if close_source == "closed" else "review_required",
        "close_treatment_branch": "allow" if close_treatment == "closed" else "review_required",
    }

    for item in metric_dependent:
        metric_name = str(item.get("metric_name") or "unknown_metric")
        metrics_used_for_closure.append(_project_metric_metadata(item))
        source_type = _norm(item.get("source_type"))
        confidence = _norm(item.get("confidence"))
        missing = sorted(key for key in REQUIRED_MEASUREMENT_METADATA if item.get(key) in (None, "", [], {}))
        if missing:
            review_flags.append("measurement_source_missing")
            missing_evidence.append(f"{metric_name}: missing {', '.join(missing)}")
        if source_type in HIGH_CONFIDENCE_SOURCE_TYPES and confidence == "high" and not missing:
            high_confidence_metrics.append(metric_name)
        if source_type in VISUAL_SOURCE_TYPES:
            visual_metrics.append(metric_name)
            review_flags.append("visual_estimate_false_quant")
            if _item_used_for_axis(item, "close_source_or_subtype"):
                review_flags.append("visual_estimate_source_closure")
        if source_type in DENTAL_RELATION_SOURCE_TYPES or item.get("dental_relation_used_as_skeletal_source") is True:
            dental_relation_metrics.append(metric_name)
            review_flags.append("dental_relation_as_skeletal_source")
        if confidence in LOW_CONFIDENCE_VALUES or source_type in LOW_CONFIDENCE_SOURCE_TYPES:
            low_confidence_metrics.append(metric_name)
            review_flags.append("low_confidence_measurement_closure")
        if _item_used_for_axis(item, "close_treatment_branch") and (
            confidence in LOW_CONFIDENCE_VALUES or source_type not in HIGH_CONFIDENCE_SOURCE_TYPES
        ):
            review_flags.append("treatment_from_unverified_measurement")
        if _item_used_for_axis(item, "close_treatment_branch") and not _case_has_treatment_branch_eligibility(case_payload):
            review_flags.append("treatment_from_measurement_gate_alone")
    if case_payload.get("dental_relation_used_as_skeletal_source") is True:
        dental_relation_metrics.append("dental_relation_label")
        review_flags.append("dental_relation_as_skeletal_source")
    if case_payload.get("surface_coben_to_patient") is True:
        review_flags.append("coben_over_surfacing")

    status = "not_applicable"
    if metric_dependent:
        status = "pass" if not review_flags else "fail"
    if review_flags:
        for axis, closed in axes_closed.items():
            if closed:
                effect_on_closure[axis] = "block"
        status = "fail"

    guard_projection = _measurement_guard_projection()
    return {
        "schema_version": "measurement_closure_gate.1",
        "guard_id": MEASUREMENT_GUARD_ID,
        "status": status,
        "review_flags": _dedupe(review_flags),
        "hard_fail_flags": [],
        "failed_metrics": _dedupe(visual_metrics + dental_relation_metrics + low_confidence_metrics),
        "low_confidence_metrics_used_for_closure": _dedupe(low_confidence_metrics),
        "visual_quantification_detected": bool(visual_metrics),
        "dental_relation_used_as_skeletal_source": bool(dental_relation_metrics),
        "required_missing_evidence": _dedupe(missing_evidence),
        "metrics_used_for_closure": metrics_used_for_closure,
        "high_confidence_metrics_used_for_closure": _dedupe(high_confidence_metrics),
        "effect_on_closure": effect_on_closure,
        "source_policy": {
            "high_confidence_source_types": sorted(HIGH_CONFIDENCE_SOURCE_TYPES),
            "required_current_case_metadata": sorted(REQUIRED_MEASUREMENT_METADATA),
            "strict_quant_metrics": sorted(STRICT_QUANT_METRICS),
        },
        "guard_projection": guard_projection,
    }


def _measurement_guard_projection() -> dict[str, Any]:
    try:
        guard = get_reasoning_guard(MEASUREMENT_GUARD_ID)
    except Exception:
        return {
            "guard_id": MEASUREMENT_GUARD_ID,
            "guard_type": "finalization_boundary_guard",
            "may_enter_final_conclusion": False,
        }
    return {
        "guard_id": guard["guard_id"],
        "guard_type": guard["guard_type"],
        "runtime_roles": guard["runtime_roles"],
        "source_sensitivity": guard["source_sensitivity"],
        "may_enter_final_conclusion": guard["may_enter_final_conclusion"],
        "may_support_finalization": guard["may_support_finalization"],
        "required_current_case_metadata": guard["required_current_case_metadata"],
    }


def _measurement_evidence_items(case_payload: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("measurement_evidence", "measurements", "ceph_measurements", "ceph_metrics", "quantitative_measurements")
    items: list[dict[str, Any]] = []
    for key in keys:
        value = case_payload.get(key)
        if isinstance(value, dict):
            value = value.get("items") or value.get("metrics") or value.get("measurements") or [value]
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    items.append(deepcopy(item))
    return items


def _used_for_closure(item: dict[str, Any]) -> bool:
    if item.get("used_for_closure") is True:
        return True
    axes = item.get("used_for_axis") or item.get("used_for_axes") or []
    if isinstance(axes, str):
        axes = [axes]
    return bool(set(axes) & {"close_diagnosis", "close_source_or_subtype", "close_treatment_branch"})


def _item_used_for_axis(item: dict[str, Any], axis: str) -> bool:
    axes = item.get("used_for_axis") or item.get("used_for_axes") or []
    if isinstance(axes, str):
        axes = [axes]
    if axis in axes:
        return True
    return item.get("used_for_closure") is True and axis != "close_treatment_branch"


def _project_metric_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "metric_name": item.get("metric_name"),
        "source_type": item.get("source_type"),
        "confidence": item.get("confidence"),
        "unit": item.get("unit"),
        "used_for_axis": item.get("used_for_axis") or item.get("used_for_axes"),
        "extraction_context_present": bool(item.get("extraction_context")),
        "current_case_link_present": bool(item.get("current_case_link")),
    }


def _case_has_treatment_branch_eligibility(case_payload: dict[str, Any]) -> bool:
    return bool(
        case_payload.get("non_measurement_treatment_branch_anchors")
        and case_payload.get("individualization_branch_eligibility") is True
    )


def _norm(value: Any) -> str:
    return str(value or "").strip().replace("-", "_")


def _basis_observations(
    gate_packet: dict[str, Any],
    source_packet: dict[str, Any],
    subtype_packet: dict[str, Any],
    diagnosis_packet: dict[str, Any],
) -> list[str]:
    values = [
        f"gate_result={gate_packet.get('gate_result')}",
        f"source_candidate={source_packet.get('source_candidate')}",
        f"source_attribution_level={source_packet.get('attribution_level')}",
        f"subtype={subtype_packet.get('upper_lower_subtype')}",
        f"diagnosis_first_status={diagnosis_packet.get('positive_chain_status')}",
    ]
    return [value for value in values if not value.endswith("=None")]


def _missing_evidence(source_packet: dict[str, Any], subtype_packet: dict[str, Any], diagnosis_packet: dict[str, Any]) -> list[str]:
    missing = []
    for item in source_packet.get("missing_required_anchors") or []:
        if isinstance(item, dict):
            missing.append(str(item.get("field") or item.get("anchor") or item.get("reason") or item))
        else:
            missing.append(str(item))
    missing.extend(str(item) for item in subtype_packet.get("missing_evidence") or [])
    missing.extend(str(item) for item in diagnosis_packet.get("required_missing_evidence") or [])
    return _dedupe(missing) or ["records sufficient to resolve current uncertainty"]


def _what_would_change(missing: list[str]) -> list[str]:
    return _dedupe((missing[:4] or ["new contradictory current-case evidence"]) + ["doctor review decision"])


def _supporting_evidence(source_packet: dict[str, Any], subtype_packet: dict[str, Any], diagnosis_packet: dict[str, Any]) -> list[str]:
    support = []
    for field in ("decisive_anchors", "supporting_anchors"):
        for anchor in source_packet.get(field) or []:
            support.append(f"{anchor.get('field') or anchor.get('anchor')}={anchor.get('value')}")
    support.extend(str(item) for item in subtype_packet.get("evidence_for") or [])
    anchors = diagnosis_packet.get("minimum_positive_anchor_set") or {}
    support.extend(key for key, value in anchors.items() if value)
    return _dedupe(support) or ["structured runtime packets reviewed"]


def _refuting_evidence(gate_packet: dict[str, Any], source_packet: dict[str, Any], *, review_required: bool) -> list[str]:
    refute = []
    refute.extend(str(item) for item in gate_packet.get("blocking_reasons") or [])
    for anchor in source_packet.get("conflicting_anchors") or []:
        refute.append(f"{anchor.get('field') or anchor.get('anchor')}={anchor.get('value')}")
    if review_required:
        refute.append("review_required boundary blocks clean closure")
    return _dedupe(refute) or ["no decisive refuting evidence in deterministic first slice"]


def _individualization_state(case_payload: dict[str, Any], *, high_risk: bool) -> dict[str, Any]:
    return {
        "individual_variables_checked": [
            "age_or_growth_stage",
            "TMJ_status",
            "periodontal_dental_root_status",
            "prior_treatment_extraction_missing_teeth",
            "functional_shift_or_asymmetry",
            "patient_goals",
            "patient_compliance",
            "risk_tolerance_or_constraints",
        ],
        "variables_affecting_diagnosis": [
            "age/growth, TMJ, prior treatment, function, periodontal/root status can change source interpretation",
        ],
        "variables_affecting_treatment": [
            "patient goals, compliance, risk tolerance, TMJ/surgical risk can change treatment sequencing",
        ],
        "variables_not_yet_known": [
            "patient compliance",
            "risk tolerance",
            "periodontal/root status",
            "TMJ stability",
        ],
        "patient_goal_observed": bool(case_payload.get("patient_goal") or case_payload.get("chief_complaint")),
        "high_risk_review_required": high_risk,
    }


def _treatment_personalization_state(
    treatment_advisory: dict[str, Any] | None,
    *,
    hypothesis_label: str,
    closure_status: str,
    high_risk: bool,
    sgtb_unit: dict[str, Any],
    transverse_unit: dict[str, Any],
    asymmetry_unit: dict[str, Any],
    jaw_position_unit: dict[str, Any],
    second_batch_units: dict[str, dict[str, Any]],
    third_batch_units: dict[str, dict[str, Any]],
    fourth_batch_units: dict[str, dict[str, Any]],
    fifth_batch_units: dict[str, dict[str, Any]],
    sixth_batch_units: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    options = list((treatment_advisory or {}).get("must_include_options") or [])
    if not options:
        options = ["review-dependent orthodontic options", "follow-up/records before branch closure"]
    return {
        "treatment_options": options,
        "why_fit_this_case": [
            f"treatment discussion follows structured hypothesis: {hypothesis_label}",
            "source/subtype/treatment-boundary anchors are considered",
        ],
        "why_not_other_options": [
            "label-only treatment is blocked",
            "alternatives remain review-dependent when source/risk anchors are incomplete",
        ],
        "feasibility_constraints": ["source measurements", "periodontal/root status", "TMJ stability", "patient compliance"],
        "risk_boundaries": [
            "not a final medical order",
            "high-risk branch remains review_required" if high_risk else "closure only if finalization boundary permits",
            "SGTB/S8 reasoning is eligibility_support_only; extraction/device/GS/treatment branch remains review_required",
            "transverse expansion reasoning is eligibility_support_only; appliance/device/surgery/final-plan branch remains review_required",
            "asymmetry source-family reasoning is eligibility_support_only; S-class/device/splint/extraction/surgery/final-plan branch remains review_required",
            "jaw-position / occlusion-TMJ balance reasoning is eligibility_support_only; GS/S-class/ARS/splint/device/extraction/surgery/final-plan branch remains review_required",
            "second-batch KB reasoning units are support-only; diagnosis/source/subtype/device/extraction/surgery/phase/final-plan closure remains blocked",
            "third-batch KB reasoning units are support-only; extraction/timing/anchorage/subtype/medical/modality/appliance/phase/final-plan closure remains blocked",
            "fourth-batch ML29F reasoning unit is support-only; ordinary protrusion/source/subtype/treatment closure remains blocked",
            "fifth-batch KB reasoning units are support-only; space/biologic-risk/orthognathic/deepbite/openbite source or treatment closure remains blocked",
            "sixth-batch KB reasoning units are support-only; diagnosis/source/subtype/treatment/mechanics/retention/plan-approval closure remains blocked",
        ],
        "follow_up_or_referral": ["doctor review", "missing-record collection", "specialist referral if high-risk/TMJ/surgical"],
        "closure_status": closure_status,
        "sgtb_eligibility_support": {
            "status": sgtb_unit.get("status"),
            "subtype_candidate": sgtb_unit.get("subtype_candidate"),
            "treatment_branch_effect": sgtb_unit.get("treatment_branch_effect"),
        },
        "transverse_expansion_eligibility_support": {
            "status": transverse_unit.get("status"),
            "device_family_candidate": transverse_unit.get("device_family_candidate"),
            "treatment_branch_effect": transverse_unit.get("treatment_branch_effect"),
            "why_fit": transverse_unit.get("why_fit") or [],
            "why_not": transverse_unit.get("why_not") or [],
            "records_needed": transverse_unit.get("records_needed") or [],
            "follow_up_owner": transverse_unit.get("follow_up_owner"),
        },
        "asymmetry_source_family_eligibility_support": {
            "status": asymmetry_unit.get("status"),
            "contributor_family_candidate": asymmetry_unit.get("contributor_family_candidate"),
            "treatment_branch_effect": asymmetry_unit.get("treatment_branch_effect"),
            "why_fit": asymmetry_unit.get("why_fit") or [],
            "why_not": asymmetry_unit.get("why_not") or [],
            "records_needed": asymmetry_unit.get("records_needed") or [],
            "follow_up_owner": asymmetry_unit.get("follow_up_owner"),
        },
        "jaw_position_occlusion_tmj_balance_eligibility_support": {
            "status": jaw_position_unit.get("status"),
            "balance_candidate": jaw_position_unit.get("balance_candidate"),
            "treatment_branch_effect": jaw_position_unit.get("treatment_branch_effect"),
            "why_fit": jaw_position_unit.get("why_fit") or [],
            "why_not": jaw_position_unit.get("why_not") or [],
            "records_needed": jaw_position_unit.get("records_needed") or [],
            "follow_up_owner": jaw_position_unit.get("follow_up_owner"),
        },
        "second_batch_reasoning_support": {
            unit_id: _unit_treatment_support(unit)
            for unit_id, unit in second_batch_units.items()
        },
        "third_batch_reasoning_support": {
            unit_id: _unit_treatment_support(unit)
            for unit_id, unit in third_batch_units.items()
        },
        "fourth_batch_reasoning_support": {
            unit_id: _unit_treatment_support(unit)
            for unit_id, unit in fourth_batch_units.items()
        },
        "fifth_batch_reasoning_support": {
            unit_id: _unit_treatment_support(unit)
            for unit_id, unit in fifth_batch_units.items()
        },
        "sixth_batch_reasoning_support": {
            unit_id: _unit_treatment_support(unit)
            for unit_id, unit in sixth_batch_units.items()
        },
    }


def _finalization_boundary(
    hypothesis_id: str,
    close_diagnosis: str,
    close_source: str,
    close_treatment: str,
    measurement_gate: dict[str, Any],
    sgtb_unit: dict[str, Any],
    transverse_unit: dict[str, Any],
    asymmetry_unit: dict[str, Any],
    jaw_position_unit: dict[str, Any],
    second_batch_units: dict[str, dict[str, Any]],
    third_batch_units: dict[str, dict[str, Any]],
    fourth_batch_units: dict[str, dict[str, Any]],
    fifth_batch_units: dict[str, dict[str, Any]],
    sixth_batch_units: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "allowed_final_statuses": ["closed", "review_required"],
        "closure_axes": {
            "close_diagnosis": close_diagnosis,
            "close_source_or_subtype": close_source,
            "close_treatment_branch": close_treatment,
        },
        "whitelisted_final_inputs": [
            {
                "object_type": "hypothesis_state",
                "object_id": hypothesis_id,
                "status": close_diagnosis,
                "may_enter_final_conclusion": close_diagnosis in {"closed", "review_required"},
            },
            {
                "object_type": "treatment_personalization_state",
                "object_id": "treatment_personalization_state",
                "status": close_treatment,
                "may_enter_final_conclusion": close_treatment in {"closed", "review_required"},
            },
        ],
        "forbidden_inputs": [
            "candidate_only",
            "preliminary",
            "supported_but_unclosed",
            "free_text_reasoning_without_structured_closure",
            "KB context not observed in this case",
            "measurement source/confidence missing or low-confidence visual estimate",
        ],
        "measurement_closure_gate": measurement_gate,
        "sgtb_reasoning_unit": sgtb_unit,
        "transverse_expansion_unit": transverse_unit,
        "asymmetry_functional_joint_bone_unit": asymmetry_unit,
        "jaw_position_reconstruction_occlusion_tmj_balance_unit": jaw_position_unit,
        "second_batch_reasoning_units": second_batch_units,
        CONCAVE_ASYMMETRY_UNIT_ID + "_unit": second_batch_units.get(CONCAVE_ASYMMETRY_UNIT_ID),
        TRUE_CONCAVE_UNIT_ID + "_unit": second_batch_units.get(TRUE_CONCAVE_UNIT_ID),
        VERTICAL_SAGITTAL_UNIT_ID + "_unit": second_batch_units.get(VERTICAL_SAGITTAL_UNIT_ID),
        RETX_SOURCE_SPACE_UNIT_ID + "_unit": second_batch_units.get(RETX_SOURCE_SPACE_UNIT_ID),
        TMD_REVISE_PERSIST_UNIT_ID + "_unit": second_batch_units.get(TMD_REVISE_PERSIST_UNIT_ID),
        "third_batch_reasoning_units": third_batch_units,
        SPACE_BUDGET_UNIT_ID + "_unit": third_batch_units.get(SPACE_BUDGET_UNIT_ID),
        PROTRUSIVE_JAW_POSITION_UNIT_ID + "_unit": third_batch_units.get(PROTRUSIVE_JAW_POSITION_UNIT_ID),
        AIRWAY_TIMING_UNIT_ID + "_unit": third_batch_units.get(AIRWAY_TIMING_UNIT_ID),
        FUNCTIONAL_ADVANCEMENT_UNIT_ID + "_unit": third_batch_units.get(FUNCTIONAL_ADVANCEMENT_UNIT_ID),
        SCENE3_MODALITY_UNIT_ID + "_unit": third_batch_units.get(SCENE3_MODALITY_UNIT_ID),
        "fourth_batch_reasoning_units": fourth_batch_units,
        ML29F_SURFACE_CONVEX_UNIT_ID + "_unit": fourth_batch_units.get(ML29F_SURFACE_CONVEX_UNIT_ID),
        "fifth_batch_reasoning_units": fifth_batch_units,
        EXTRACTION_SPACE_TRADEOFF_UNIT_ID + "_unit": fifth_batch_units.get(EXTRACTION_SPACE_TRADEOFF_UNIT_ID),
        ADULT_RETX_BIOLOGIC_RISK_UNIT_ID + "_unit": fifth_batch_units.get(ADULT_RETX_BIOLOGIC_RISK_UNIT_ID),
        ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID + "_unit": fifth_batch_units.get(ORTHOGNATHIC_CAMOUFLAGE_UNIT_ID),
        DEEPBITE_BITE_OPENING_UNIT_ID + "_unit": fifth_batch_units.get(DEEPBITE_BITE_OPENING_UNIT_ID),
        OPENBITE_VERTICAL_CONTROL_UNIT_ID + "_unit": fifth_batch_units.get(OPENBITE_VERTICAL_CONTROL_UNIT_ID),
        "sixth_batch_reasoning_units": sixth_batch_units,
        CASE_MATURITY_UNIT_ID + "_unit": sixth_batch_units.get(CASE_MATURITY_UNIT_ID),
        ACTIVE_MECHANICS_STALL_UNIT_ID + "_unit": sixth_batch_units.get(ACTIVE_MECHANICS_STALL_UNIT_ID),
        DIGITAL_SETUP_AUDIT_UNIT_ID + "_unit": sixth_batch_units.get(DIGITAL_SETUP_AUDIT_UNIT_ID),
        RETENTION_RELAPSE_UNIT_ID + "_unit": sixth_batch_units.get(RETENTION_RELAPSE_UNIT_ID),
        TOOTH_SIZE_FINISHING_UNIT_ID + "_unit": sixth_batch_units.get(TOOTH_SIZE_FINISHING_UNIT_ID),
    }


def _unit_treatment_support(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": unit.get("status"),
        "support_candidate": unit.get("support_candidate"),
        "treatment_branch_effect": unit.get("treatment_branch_effect"),
        "why_fit": unit.get("why_fit") or [],
        "why_not": unit.get("why_not") or [],
        "records_needed": unit.get("records_needed") or [],
        "follow_up_owner": unit.get("follow_up_owner"),
        "review_required_reason": unit.get("review_required_reason"),
        "may_enter_final_conclusion": False,
    }


def _final_conclusion(
    hypothesis_id: str,
    hypothesis_label: str,
    close_diagnosis: str,
    close_source: str,
    close_treatment: str,
    knowns: list[str],
    unknowns: list[str],
    high_risk: bool,
) -> dict[str, Any]:
    return {
        "current_best_judgment": hypothesis_label,
        "closure_status": close_diagnosis,
        "diagnosis_closed_or_review_required": close_diagnosis in {"closed", "review_required"},
        "source_or_subtype_closed_or_review_required": close_source in {"closed", "review_required"},
        "treatment_branch_closed_or_review_required": close_treatment in {"closed", "review_required"},
        "knowns": knowns,
        "unknowns": unknowns,
        "what_would_change_the_plan": _dedupe(unknowns[:4] + ["clinical review decision"]),
        "review_or_followup_owner": "doctor review" if high_risk or close_diagnosis == "review_required" else "routine output",
        "consumed_hypothesis_ids": [hypothesis_id],
        "consumed_treatment_state": True,
        "has_structured_closure": True,
    }


def _doctor_trace(workspace: dict[str, Any], *, workflow_state_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "reasoning_loop_doctor_trace.1",
        "projection": "doctor_admin_full_trace",
        "hypothesis_state": deepcopy(workspace["hypothesis_state"]),
        "kb_context_used": deepcopy(workspace["kb_context_used"]),
        "evidence_seeking_state": deepcopy(workspace["evidence_seeking_state"]),
        "support_refute_trace": deepcopy(workspace["support_refute_trace"]),
        "hypothesis_transition_log": deepcopy(workspace["hypothesis_transition_log"]),
        "individualization_state": deepcopy(workspace["individualization_state"]),
        "treatment_personalization_state": deepcopy(workspace["treatment_personalization_state"]),
        "finalization_boundary": deepcopy(workspace["finalization_boundary"]),
        "workflow_state_metadata": deepcopy(workflow_state_metadata),
    }


def _workflow_state_metadata(
    case_payload: dict[str, Any],
    *,
    conflicts: bool,
    missing_evidence: list[str],
    why_card_not_applicable: str | None,
) -> dict[str, Any]:
    requested = case_payload.get("workflow_state_metadata")
    requested = requested if isinstance(requested, dict) else {}
    workflow_state = {
        "case_maturity_route_closure": canonical_or_review_required(
            "case_maturity_route_closure",
            requested.get("case_maturity_route_closure") or _case_maturity_route(case_payload),
        ),
        "evidence_sufficiency_state": canonical_or_review_required(
            "evidence_sufficiency_state",
            requested.get("evidence_sufficiency_state") or _evidence_sufficiency_state(
                case_payload,
                conflicts=conflicts,
                missing_evidence=missing_evidence,
            ),
        ),
        "why_card_not_applicable": canonical_or_review_required(
            "why_card_not_applicable",
            requested.get("why_card_not_applicable") or _why_card_not_applicable_state(
                why_card_not_applicable=why_card_not_applicable,
                missing_evidence=missing_evidence,
            ),
        ),
    }
    return {
        "schema_version": "workflow_state_runtime_metadata.1",
        "projection": "internal_doctor_debug_trace_only",
        "no_clinical_closure_disclaimer": WORKFLOW_STATE_NO_CLINICAL_CLOSURE_NOTE,
        "workflow_state": workflow_state,
        "protected_clinical_axes": review_required_clinical_axes(),
        "source_safe_evidence_gap": _workflow_evidence_gap_tags(case_payload, missing_evidence),
        "non_finalization_boundary": {
            "affects_finalization": False,
            "affects_treatment_mapper": False,
            "affects_plan_approval": False,
            "affects_patient_projection": False,
            "default_api_response": False,
        },
    }


def _case_maturity_route(case_payload: dict[str, Any]) -> str:
    text = _case_text(case_payload)
    if any(term in text for term in ("setup", "animation", "staging", " ClinCheck".lower(), "方案动画", "模拟")):
        return "plan_animation_audit_route"
    if any(term in text for term in ("retention", "retainer", "relapse", "保持器", "复发", "维持")):
        return "retention_finished_route"
    if any(term in text for term in ("active treatment", "aligner", "bracket", "appliance", "附件", "牙套", "矫治中")):
        return "active_treatment_route"
    if any(term in text for term in ("mixed maturity", "unclear maturity", "stage unclear", "阶段不清")):
        return "cross_maturity_not_applicable_route"
    return "initial_untreated_route"


def _evidence_sufficiency_state(case_payload: dict[str, Any], *, conflicts: bool, missing_evidence: list[str]) -> str:
    text = _case_text(case_payload)
    if any(term in text for term in ("source unsafe", "private source", "source_unsafe", "private_source")):
        return "source_unsafe_or_private"
    if conflicts:
        return "conflicting_evidence"
    if missing_evidence:
        return "missing_required_evidence"
    return "sufficient_for_workflow_triage"


def _why_card_not_applicable_state(*, why_card_not_applicable: str | None, missing_evidence: list[str]) -> str:
    if why_card_not_applicable:
        return "unit_scope_excluded"
    if missing_evidence:
        return "missing_required_evidence"
    return "review_required"


def _workflow_evidence_gap_tags(case_payload: dict[str, Any], missing_evidence: list[str]) -> list[str]:
    tags = []
    requested_tags = case_payload.get("workflow_evidence_gap_tags")
    if isinstance(requested_tags, list):
        tags.extend(str(tag) for tag in requested_tags if isinstance(tag, str))
    tags.extend(str(item) for item in missing_evidence[:4] if isinstance(item, str))
    return _dedupe(tags + ["clinical_closure_not_authorized"])


def _patient_summary(workspace: dict[str, Any]) -> dict[str, Any]:
    final = workspace["final_conclusion"]
    support = workspace["support_refute_trace"][0].get("supporting_evidence") or []
    missing = final.get("unknowns") or []
    return {
        "schema_version": "reasoning_loop_patient_summary.1",
        "projection": "patient_readable_summary",
        "current_judgment": final.get("current_best_judgment"),
        "status": "需要复核" if final.get("closure_status") == "review_required" else "当前可结构化闭合",
        "core_basis": support[:3],
        "what_is_missing": missing[:4],
        "next_step": final.get("review_or_followup_owner"),
    }


def _negative_controls(gate_packet: dict[str, Any], diagnosis_packet: dict[str, Any]) -> list[str]:
    controls = ["no prose-only closure", "no label-only treatment", "no KB context as case fact"]
    if gate_packet.get("gate_result") != "true_convex_closed":
        controls.append("review gate blocks clean final closure")
    if diagnosis_packet.get("positive_chain_status") != "active":
        controls.append("candidate card not activated")
    return controls


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if not value:
            continue
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
