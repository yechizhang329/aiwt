"""Our-side measurement-layer EXTENSION (task TRACE / #157, PM+DW green-lit).

Extends HRNet (hrnet_poc_inference) from 4 sagittal measurements to DW's 11 PRODUCIBLE items
(9 skeletal + 2 incisor-mm). 5 incisor-ANGLE items = pending (ISBI-19 has incisor TIP not
root-apex → can't define long axis; flagged llm_estimated/pending per DW). Provenance
invariants HELD: calibration_status = hrnet_estimated (NEVER calibrated); reuses the D7.1
sanity floor. MP definition (Go-Me vs Go-Gn) PARAMETERIZED until 正雅 reverse-locks it.

FIRST it writes a numbered landmark OVERLAY for visual verification — landmark indices come
from the script header's documentation; a wrong index = wrong measurement, so verify before
trusting the numbers.

Run: python3 tools/ceph_measure_extend.py --image <ceph.png> [--mp go_gn]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path

import torch
from PIL import Image, ImageDraw

# ISBI-19 indices (from hrnet_poc_inference.py header). Verify via overlay before trusting.
LM_IDX_FULL = {"S": 0, "N": 1, "Or": 2, "Po": 3, "A": 4, "B": 5, "Pog": 6, "Me": 7,
               "Gn": 8, "Go": 9, "Is": 10, "Ii": 11, "ANS": 17, "PNS": 18}

PENDING_INCISOR_ANGLES = ["U1_NA_deg", "L1_NB_deg", "U1_L1", "U1_SN", "IMPA"]  # need root-apex


def _load_hp():
    spec = importlib.util.spec_from_file_location("hp", str(Path(__file__).with_name("hrnet_poc_inference.py")))
    hp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hp)
    hp.LM_IDX = LM_IDX_FULL  # extend to all needed landmarks
    return hp


def _ang(p1, vertex, p2):
    v1 = (p1[0] - vertex[0], p1[1] - vertex[1]); v2 = (p2[0] - vertex[0], p2[1] - vertex[1])
    d = v1[0] * v2[0] + v1[1] * v2[1]; m = math.hypot(*v1) * math.hypot(*v2)
    return math.degrees(math.acos(max(-1, min(1, d / m)))) if m else None


def _line_angle(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def _angle_between_lines(a1, a2, b1, b2):
    d = abs(_line_angle(a1, a2) - _line_angle(b1, b2)) % 180
    return min(d, 180 - d)


def _perp_dist_px(pt, l1, l2):
    """perpendicular distance (px) of pt from line l1-l2."""
    num = abs((l2[0] - l1[0]) * (l1[1] - pt[1]) - (l1[0] - pt[0]) * (l2[1] - l1[1]))
    den = math.hypot(l2[0] - l1[0], l2[1] - l1[1])
    return num / den if den else None


def measure(pts, mp="go_me"):
    g = pts.get
    S, N, A, B, Pog, Or, Po, Me, Gn, Go, Is, Ii, ANS, PNS = (
        g("S"), g("N"), g("A"), g("B"), g("Pog"), g("Or"), g("Po"), g("Me"),
        g("Gn"), g("Go"), g("Is"), g("Ii"), g("ANS"), g("PNS"))
    mp_pt = Me if mp == "go_me" else Gn  # parameterized mandibular plane (Go-Me vs Go-Gn)
    out = {}

    def safe(name, fn, *need):
        out[name] = round(fn(), 2) if all(p is not None for p in need) and fn() is not None else None

    out["SNA"] = round(_ang(S, N, A), 2) if all(p for p in (S, N, A)) else None
    out["SNB"] = round(_ang(S, N, B), 2) if all(p for p in (S, N, B)) else None
    out["ANB"] = round(out["SNA"] - out["SNB"], 2) if out["SNA"] is not None and out["SNB"] is not None else None
    out["FH_NPo"] = round(_angle_between_lines(Po, Or, N, Pog), 2) if all(p for p in (Po, Or, N, Pog)) else None
    out["NA_APo"] = round(180 - _ang(N, A, Pog), 2) if all(p for p in (N, A, Pog)) else None  # convexity
    out["FMA"] = round(_angle_between_lines(Po, Or, Go, mp_pt), 2) if all(p for p in (Po, Or, Go, mp_pt)) else None
    out["SGn_FH"] = round(_angle_between_lines(S, Gn, Po, Or), 2) if all(p for p in (S, Gn, Po, Or)) else None
    out["MP_SN"] = round(_angle_between_lines(Go, mp_pt, S, N), 2) if all(p for p in (Go, mp_pt, S, N)) else None
    # mm measures (perpendicular distance in PX — needs scale calibration for true mm; flagged)
    out["Po_NB_px"] = round(_perp_dist_px(Pog, N, B), 1) if all(p for p in (Pog, N, B)) else None
    out["U1_NA_mm_px"] = round(_perp_dist_px(Is, N, A), 1) if all(p for p in (Is, N, A)) else None
    out["L1_NB_mm_px"] = round(_perp_dist_px(Ii, N, B), 1) if all(p for p in (Ii, N, B)) else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--mp", default="go_me", choices=["go_me", "go_gn"])
    ap.add_argument("--out", default="/tmp/ceph_extend_results.json")
    args = ap.parse_args()
    hp = _load_hp()
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = hp.load_model(device)
    pts, confs = hp.run_inference(model, Path(args.image), device)

    # numbered overlay for VISUAL VERIFICATION of landmark indices
    img = Image.open(args.image).convert("RGB"); dr = ImageDraw.Draw(img)
    for name, p in pts.items():
        if p:
            x, y = p
            dr.ellipse([x - 6, y - 6, x + 6, y + 6], outline=(255, 0, 0), width=3)
            dr.text((x + 8, y - 6), name, fill=(255, 255, 0))
    overlay = "tools/_ceph_extend_overlay.png"; img.save(overlay)

    meas = measure(pts, args.mp)
    sane = hp.sanity_check({"SNA_deg": meas.get("SNA"), "SNB_deg": meas.get("SNB")}, pts)
    result = {
        "calibration_status": "hrnet_estimated",  # INVARIANT — never calibrated
        "provenance": "llm_estimated_hrnet", "weight_modifier": None,
        "resolvable": sane is None, "sanity_note": sane,
        "mp_definition": args.mp,
        "producible_11": meas,
        "pending_5_incisor_angles": {k: "pending_need_incisor_root_apex" for k in PENDING_INCISOR_ANGLES},
        "landmark_confidence": {k: round(v, 2) for k, v in confs.items()},
        "low_confidence_flag": {k: round(v, 2) for k, v in confs.items() if v < 7},
        "overlay_for_verification": overlay,
        "mm_note": "Po_NB/U1_NA/L1_NB in PX — true-mm needs scale calibration (ceph ruler / 正雅 scale).",
    }
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"producible_11": meas, "low_conf": result["low_confidence_flag"],
                      "resolvable": result["resolvable"]}, ensure_ascii=False, indent=2))
    print("overlay (VERIFY landmarks):", overlay)


if __name__ == "__main__":
    main()
