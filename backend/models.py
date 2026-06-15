"""Pydantic models for all API request/response shapes."""

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    name: str
    role: str


# ── Case submission ───────────────────────────────────────────────────────────

class Scene1CaseSubmit(BaseModel):
    """Scene 1 (patient-facing / sales team) submission."""
    model_config = ConfigDict(extra="allow")

    scene: str = "1_patient"
    patient_age: Optional[int] = Field(None, ge=1, le=100)
    patient_gender: Optional[str] = None
    chief_complaint: Optional[str] = None
    attachment_ids: list[str] = Field(default_factory=list)


class CephEstimates(BaseModel):
    anb: Optional[float] = None
    snb: Optional[float] = None
    wits: Optional[float] = None
    coben_ptm_a_pct: Optional[float] = None
    coben_go_pog_pct: Optional[float] = None
    u1_l1: Optional[float] = None
    estimate_confidence: str = "low (待面诊)"


class TmdSignals(BaseModel):
    active_symptoms: list[str] = Field(default_factory=list)
    symptom_progression: str = "不明"
    cbct_findings: Optional[str] = None
    mri_findings: Optional[str] = None


class ClinicalFindings(BaseModel):
    face: Optional[str] = None
    intraoral: Optional[str] = None
    tmd_signals: Optional[TmdSignals] = None
    cephalometric_estimates: Optional[CephEstimates] = None
    dental_findings: Optional[str] = None


class ImagingItem(BaseModel):
    attachment_id: str
    type: str  # ceph_lateral | panx | cbct_axial | ...
    view: Optional[str] = None


class Scene3CaseSubmit(BaseModel):
    """Scene 3 (doctor-to-doctor) submission."""
    model_config = ConfigDict(extra="allow")

    scene: str = "3_doctor"
    submitter_role: str = "doctor"
    institution: Optional[str] = None
    patient_age: Optional[int] = Field(None, ge=1, le=100)
    patient_age_confidence: str = "high"
    patient_sex: Optional[str] = None
    chief_complaint_patient: Optional[str] = None
    chief_complaint_doctor: Optional[str] = None
    chief_complaint_main_concern: str = "多项"
    clinical_findings: Optional[ClinicalFindings] = None
    imaging_provided: list[ImagingItem] = Field(default_factory=list)
    prior_treatment_history: Optional[str] = None
    prior_school_framework: Optional[str] = None
    patient_pref_no_surgery: bool = False
    patient_pref_time_priority: bool = False
    doctor_specific_question: Optional[str] = None
    context_notes: Optional[str] = None


# ── Case response ─────────────────────────────────────────────────────────────

class CaseSubmitResponse(BaseModel):
    case_id: str
    trace_id: str
    status: str = "pending"


class FinalOutput(BaseModel):
    format: str
    sections: dict[str, Any] = Field(default_factory=dict)
    rendered_markdown: str = ""
    word_count: int = 0
    layer_2: Optional[Any] = None
    image_anchors: list[Any] = Field(default_factory=list)
    confidence: Optional[float] = None
    学派_attribution_used: Optional[list[Any]] = None
    v4_review_prompt: Optional[dict[str, Any]] = None
    v4_phase2: Optional[dict[str, Any]] = None
    v4_source_explanation: Optional[dict[str, Any]] = None
    v4_shengang_subtype: Optional[dict[str, Any]] = None
    v4_treatment_advisory: Optional[dict[str, Any]] = None
    v4_diagnosis_first: Optional[dict[str, Any]] = None
    v4_diagnosis_first_training: Optional[dict[str, Any]] = None
    v4_reasoning_output_projection: Optional[dict[str, Any]] = None
    v4_reasoning_workspace: Optional[dict[str, Any]] = None
    v4_reasoning_doctor_trace: Optional[dict[str, Any]] = None
    v4_reasoning_patient_summary: Optional[dict[str, Any]] = None
    v4_reasoning_training: Optional[dict[str, Any]] = None
    v4_final_report_compliance: Optional[dict[str, Any]] = None


class CaseStatusResponse(BaseModel):
    case_id: str
    trace_id: str
    status: str  # pending | processing | done | escalated | failed
    phase: Optional[str] = None
    scene: str
    final_output: Optional[FinalOutput] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    submission: dict[str, Any] = Field(default_factory=dict)
    submitted_at: Optional[str] = None
    completed_at: Optional[str] = None
    stage_info: dict[str, Any] = Field(default_factory=dict)
    error_msg: Optional[str] = None


# ── Attachment ────────────────────────────────────────────────────────────────

class AttachmentUploadResponse(BaseModel):
    attachment_id: str
    url: str
    filename: str
    mime_type: str
    size_bytes: int


# ── Audit ─────────────────────────────────────────────────────────────────────

class AnomalyMetrics(BaseModel):
    total_entries: int
    critic_disagreement_rate: float
    wrapper_violation_rate: float
    total_retries: int
    total_escalations: int
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None


# ── Health + meta ─────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
    sprint: str = "1"
