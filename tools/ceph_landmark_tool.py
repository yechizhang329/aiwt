"""Offline ceph landmark placement tool — Option A workstation.

Usage (per case):
    python3 ceph_landmark_tool.py \\
        --image /tmp/clean_ceph_boundary9/bonepath_03_clean.jpg \\
        --S 540,312 --N 612,278 --A 698,430 --B 702,510 --Pog 715,540 \\
        [--output /tmp/ceph_landmark_results.csv]

Provenance invariants (太上老君 4f7241ee + DW 77a704b1):
    calibration_status = pending_calibration  (ALWAYS — never calibrated*)
    weight_modifier    = NULL                  (ALWAYS)
    provenance         = llm_estimated         (DW point = LLM pixel estimate, NOT instrument-grade)

    Upgrade to `calibrated` requires ALL THREE gates (IX-4, 太上老君 owns):
      1. DW clinician sign (gate#1 = "放行先验消费", NOT "升 calibrated")
      2. STRATIFIED trap-gate (must include 76db trap subset, non-clean-only)
      3. D7.1-consume cold-read direction cross-validation
    This tool NEVER writes calibrated* literals.

§9 sanity floor (D7.1 HEURISTIC-THRESHOLD FIREWALL — same as hrnet_poc_inference.py):
    SNA ∉ [65,100] → resolvable=False  (garbage-rejection, NOT normal-range)
    SNB ∉ [60,100] → resolvable=False
    S-N dist < 20px → resolvable=False  (degenerate placement)
    SNB > SNA + 30  → resolvable=False

    sanity-pass ≠ direction-validated (DW f9540a3a).
    Threshold numbers FIREWALLED from calibration namespace (never ② cutoff, never C-c).
"""

import argparse
import csv
import math
from pathlib import Path

from PIL import Image

OUTPUT_CSV = Path("/tmp/ceph_landmark_results.csv")


def angle_at_vertex(p1, vertex, p2):
    v1 = (p1[0] - vertex[0], p1[1] - vertex[1])
    v2 = (p2[0] - vertex[0], p2[1] - vertex[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    m1 = math.hypot(*v1)
    m2 = math.hypot(*v2)
    if m1 < 1e-9 or m2 < 1e-9:
        return None
    return round(math.degrees(math.acos(max(-1.0, min(1.0, dot / (m1 * m2))))), 2)


def sanity_check(angles, pts):
    """§9 HEURISTIC floor — garbage-rejection only, NOT direction validation."""
    SNA = angles.get("SNA_deg")
    SNB = angles.get("SNB_deg")
    if SNA is None or SNB is None:
        return "missing_SNA_or_SNB"
    if not (65 <= SNA <= 100):
        return f"SNA_out_of_range={SNA}"
    if not (60 <= SNB <= 100):
        return f"SNB_out_of_range={SNB}"
    S, N = pts.get("S"), pts.get("N")
    if S and N:
        sn_dist = math.hypot(S[0] - N[0], S[1] - N[1])
        if sn_dist < 20:
            return f"SN_degenerate_dist={sn_dist:.1f}px"
    if SNB > SNA + 30:
        return f"SNB_implausibly_exceeds_SNA: SNB={SNB} SNA={SNA}"
    return None


def parse_coord(s):
    """Parse 'x,y' string to (float, float)."""
    parts = s.strip().split(",")
    if len(parts) != 2:
        raise ValueError(f"Expected 'x,y', got {s!r}")
    return float(parts[0]), float(parts[1])


def main():
    parser = argparse.ArgumentParser(description="Ceph landmark placement → SNA/SNB CSV")
    parser.add_argument("--image", required=True, help="Path to clean lateral ceph image")
    parser.add_argument("--S",   required=True, help="Sella pixel coords 'x,y'")
    parser.add_argument("--N",   required=True, help="Nasion pixel coords 'x,y'")
    parser.add_argument("--A",   required=True, help="A-point (Subspinale) pixel coords 'x,y'")
    parser.add_argument("--B",   required=True, help="B-point (Supramentale) pixel coords 'x,y'")
    parser.add_argument("--Pog", required=True, help="Pogonion pixel coords 'x,y'")
    parser.add_argument("--output", default=str(OUTPUT_CSV),
                        help=f"Output CSV path (default: {OUTPUT_CSV})")
    args = parser.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        raise FileNotFoundError(f"Image not found: {img_path}")

    stem = img_path.stem

    pts = {
        "S":   parse_coord(args.S),
        "N":   parse_coord(args.N),
        "A":   parse_coord(args.A),
        "B":   parse_coord(args.B),
        "Pog": parse_coord(args.Pog),
    }

    S, N, A, B, Pog = pts["S"], pts["N"], pts["A"], pts["B"], pts["Pog"]
    SNA = angle_at_vertex(S, N, A)
    SNB = angle_at_vertex(S, N, B)
    ANB = round(SNA - SNB, 2) if SNA and SNB else None
    conv = angle_at_vertex(N, A, Pog)
    angles = {"SNA_deg": SNA, "SNB_deg": SNB, "ANB_deg": ANB, "facial_convexity_deg": conv}

    sanity_fail = sanity_check(angles, pts)

    out_path = Path(args.output)
    write_header = not out_path.exists()

    fieldnames = [
        "case_stem", "calibration_status", "weight_modifier", "provenance",
        "resolvable", "SNA_deg", "SNB_deg", "ANB_deg", "facial_convexity_deg",
        "S_x", "S_y", "N_x", "N_y", "A_x", "A_y", "B_x", "B_y", "Pog_x", "Pog_y",
        "notes",
    ]

    with out_path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()

        if sanity_fail:
            row = {
                "case_stem": stem,
                "calibration_status": "pending_calibration",
                "weight_modifier": "",  # NULL
                "provenance": "llm_estimated",
                "resolvable": False,
                "notes": f"sanity_fail:{sanity_fail}",
            }
            print(f"SANITY_FAIL [{stem}]: {sanity_fail}")
        else:
            row = {
                "case_stem": stem,
                "calibration_status": "pending_calibration",
                "weight_modifier": "",  # NULL
                "provenance": "llm_estimated",
                "resolvable": True,
                "SNA_deg": SNA, "SNB_deg": SNB, "ANB_deg": ANB,
                "facial_convexity_deg": conv,
                "S_x": round(S[0], 1), "S_y": round(S[1], 1),
                "N_x": round(N[0], 1), "N_y": round(N[1], 1),
                "A_x": round(A[0], 1), "A_y": round(A[1], 1),
                "B_x": round(B[0], 1), "B_y": round(B[1], 1),
                "Pog_x": round(Pog[0], 1), "Pog_y": round(Pog[1], 1),
            }
            print(f"OK [{stem}]: SNA={SNA}° SNB={SNB}° ANB={ANB}° conv={conv}°")

        w.writerow(row)

    print(f"→ {out_path}")


if __name__ == "__main__":
    main()
