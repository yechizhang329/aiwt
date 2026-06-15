"""Read-tool advisory stage: HRNet ceph landmark detection + measurement.

Pure computation (no LLM). Runs as shadow-mode when READ_TOOL_ADVISORY_ENABLED=1:
writes to stage_info for observability, does NOT inject into downstream payloads.
Live-enable gated on Walter +5 digital-fix confirm + David wiring approval.

Input-gate criteria (DW input_gate_clinical_criteria_BUILD.md):
  #1 [decisive] Clean digital vs re-photo/screenshot (domain discriminator)
  #2 Resolution: long-edge >= 1500px (PASS), <1000px (REJECT), 1000-1500 (low-conf)
  #3 Anatomical completeness: all key landmarks in frame (post-HRNet confidence check)
  #4 True lateral: not panoramic/AP/CBCT/intraoral/face photo
  #5 Key region no occlusion: post-HRNet landmark confidence in A/B/incisor region

Pipeline: input-gate → HRNet trace (19 landmarks) → 11-field measurement → sanity-gate → advisory dict.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import numpy as np
from PIL import Image

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
_MODEL_PATH = _TOOLS_DIR / "hrnet_model" / "best_model.pth"

_model_cache = {"model": None, "hp": None, "ce": None, "device": None}


def _ensure_model():
    if _model_cache["model"] is not None:
        return
    import torch
    spec_ce = importlib.util.spec_from_file_location("ce", str(_TOOLS_DIR / "ceph_measure_extend.py"))
    ce = importlib.util.module_from_spec(spec_ce)
    spec_ce.loader.exec_module(ce)
    hp = ce._load_hp()
    hp.MODEL_PATH = _MODEL_PATH

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = hp.load_model(device)
    _model_cache.update(model=model, hp=hp, ce=ce, device=device)


def _detect_lightbox_frame(arr_gray: np.ndarray) -> bool:
    """Detect bright border bands typical of lightbox re-photography."""
    h, w = arr_gray.shape
    margin = max(h, w) // 20
    edges = [
        arr_gray[:margin, :].mean(),
        arr_gray[-margin:, :].mean(),
        arr_gray[:, :margin].mean(),
        arr_gray[:, -margin:].mean(),
    ]
    bright_edges = sum(1 for e in edges if e > 200)
    return bright_edges >= 2


def _input_gate(image_path: Path) -> dict:
    """DW 5-criteria input gate. Returns {pass, confidence, criteria, reasons}."""
    reasons = []
    criteria = {}
    try:
        img = Image.open(image_path)
    except Exception as e:
        return {"pass": False, "confidence": "reject", "criteria": {},
                "reasons": [f"unreadable: {e}"]}

    w, h = img.size
    long_edge = max(w, h)
    arr = np.array(img)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        gray = np.mean(arr[:, :, :3], axis=2).astype(np.uint8)
    else:
        gray = arr

    # #1 Digital vs re-photo (decisive)
    is_color = False
    if arr.ndim == 3 and arr.shape[2] >= 3:
        r, g, b = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
        color_spread = np.mean(np.abs(r - g)) + np.mean(np.abs(r - b))
        is_color = color_spread > 15
    has_frame = _detect_lightbox_frame(gray)
    if is_color:
        reasons.append(f"color_image: spread={color_spread:.1f} (likely photo/screenshot, not digital export)")
        criteria["digital_vs_rephoto"] = "reject_color"
    elif has_frame:
        reasons.append("lightbox_frame: bright borders detected (re-photo)")
        criteria["digital_vs_rephoto"] = "reject_frame"
    else:
        criteria["digital_vs_rephoto"] = "pass"

    # #2 Resolution
    if long_edge < 1000:
        reasons.append(f"resolution_reject: {w}x{h} (long_edge={long_edge} < 1000)")
        criteria["resolution"] = "reject"
    elif long_edge < 1500:
        criteria["resolution"] = "low_confidence"
    else:
        criteria["resolution"] = "pass"

    # #3 Anatomical completeness — checked post-HRNet (stub pass here)
    criteria["anatomical_completeness"] = "deferred_to_post_hrnet"

    # #4 True lateral — basic heuristic (aspect ratio + grayscale)
    aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 999
    if aspect > 3.0:
        reasons.append(f"unlikely_lateral: aspect={aspect:.1f} (panoramic?)")
        criteria["true_lateral"] = "reject_aspect"
    else:
        criteria["true_lateral"] = "pass"

    # #5 Key region occlusion — checked post-HRNet (stub pass here)
    criteria["no_occlusion"] = "deferred_to_post_hrnet"

    gate_pass = len(reasons) == 0
    confidence = "pass" if gate_pass else ("low_confidence" if criteria.get("resolution") == "low_confidence" and len(reasons) == 0 else "reject")

    return {"pass": gate_pass, "confidence": confidence, "criteria": criteria,
            "reasons": reasons, "image_size": [w, h], "long_edge": long_edge}


_PHYSIO_BOUNDS = {
    "SNA":    (65, 100),
    "SNB":    (62, 95),
    "ANB":    (-12, 16),
    "FH_NPo": (75, 105),
    "NA_APo": (-25, 30),
    "FMA":    (5, 55),
    "SGn_FH": (45, 85),
    "MP_SN":  (10, 58),
}


def _post_hrnet_gate(confs: dict, meas: dict) -> dict:
    """DW sanity gate (post-HRNet): anatomical sanity + completeness + occlusion.

    Thresholds = DW sanity_gate_clinical_thresholds_BUILD.md "hard impossible"
    bounds (physiologically possible, NOT normal). Catches trace-drift artifacts;
    severe pathology values that fall inside bounds are NOT flagged.
    """
    issues = []

    # Anatomical completeness (#3): key landmark confidence
    key_landmarks = ["S", "N", "A", "B", "Or", "Po", "Go", "Me"]
    missing_or_low = [lm for lm in key_landmarks if confs.get(lm, 0) < 5]
    if len(missing_or_low) >= 3:
        issues.append(f"anatomical_incomplete: {len(missing_or_low)} key landmarks low-conf ({missing_or_low})")

    # Sella confidence: S drifts → SNA/SNB both wrong
    sella_conf = confs.get("S", 0)
    if sella_conf < 5:
        issues.append(f"sella_low_confidence: {sella_conf:.1f} (S drift → SNA/SNB unreliable)")

    # Occlusion (#5): A/B/incisor region low confidence
    ab_landmarks = ["A", "B", "Is", "Ii"]
    ab_low = [lm for lm in ab_landmarks if confs.get(lm, 0) < 5]
    if len(ab_low) >= 2:
        issues.append(f"possible_occlusion: A/B/incisor region low-conf ({ab_low})")

    # Physiological-impossible bounds (DW spec §A)
    for key, (lo, hi) in _PHYSIO_BOUNDS.items():
        val = meas.get(key)
        if val is not None and not (lo <= val <= hi):
            issues.append(f"{key}_unreliable: {val}° (outside {lo}-{hi})")

    # ANB ≈ SNA − SNB consistency (DW spec §B, tol 1.5°)
    sna = meas.get("SNA")
    snb = meas.get("SNB")
    anb = meas.get("ANB")
    if sna is not None and snb is not None and anb is not None:
        expected_anb = sna - snb
        if abs(anb - expected_anb) > 1.5:
            issues.append(
                f"ANB_inconsistent: ANB={anb}° but SNA−SNB={expected_anb:.1f}° "
                f"(diff={abs(anb - expected_anb):.1f}° > 1.5°)"
            )

    return {"pass": len(issues) == 0, "issues": issues}


def run_advisory(image_path: str | Path) -> dict:
    """Run the full read-tool advisory pipeline on a single ceph image.

    Returns advisory dict with input_gate, measurements, sanity, and provenance.
    FAIL output includes which criteria failed + "无可靠测量、需数字原片" message.
    """
    image_path = Path(image_path)
    t0 = time.monotonic()

    gate = _input_gate(image_path)
    if not gate["pass"]:
        return {
            "status": "input_gate_fail",
            "input_gate": gate,
            "measurements": None,
            "sanity": None,
            "message": "无可靠测量、需数字原片",
            "fail_criteria": gate["reasons"],
            "provenance": "read_tool_advisory_v1",
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }

    _ensure_model()
    hp = _model_cache["hp"]
    ce = _model_cache["ce"]
    model = _model_cache["model"]
    device = _model_cache["device"]

    pts, confs = hp.run_inference(model, image_path, device)
    meas = ce.measure(pts)

    post_gate = _post_hrnet_gate(confs, meas)
    if not post_gate["pass"]:
        return {
            "status": "sanity_gate_fail",
            "input_gate": gate,
            "measurements": meas,
            "sanity": post_gate,
            "message": "测量值不可靠(描点漂移)",
            "fail_criteria": post_gate["issues"],
            "provenance": "read_tool_advisory_v1",
            "calibration_status": "hrnet_estimated",
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }

    return {
        "status": "sane",
        "input_gate": gate,
        "measurements": meas,
        "sanity": post_gate,
        "low_confidence_landmarks": {k: round(v, 2) for k, v in confs.items() if v < 7},
        "provenance": "read_tool_advisory_v1",
        "calibration_status": "hrnet_estimated",
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }
