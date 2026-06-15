"""Batch ceph landmark estimation via Claude vision (Path① b).

Provenance: llm_estimated — NOT calibrated. weight=NULL. C-c excluded.
Protocol: DW v3_ceph_tracing_protocol.md §9
Output: /tmp/ceph_vision_results.csv
"""

import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd

MANIFEST_PATH = Path("/tmp/convex_att_manifest.tsv")
EXTRACT_BASE = Path("/tmp/convex_extracts")
OUTPUT_CSV = Path("/tmp/ceph_vision_results.csv")
CALIBRATION_STATUS = "llm_estimated"

LANDMARK_PROMPT = """\
Read the image at {img_path}

GATE 0 — VERIFY LATERAL CEPH FIRST:
Before doing anything else, confirm this is a true lateral cephalometric radiograph (侧位片):
- Must show a skull in true lateral view (profile)
- Must have cranial base structures (sella, cranial vault) AND facial skeleton AND mandible visible
- NOT: a title card, photograph, panoramic, PA/frontal view, occlusal view, intraoral photo, or other non-lateral-ceph image
If it is NOT a true lateral ceph → immediately return:
{{"resolvable": false, "S": null, "N": null, "A": null, "B": null, "Pog": null, "landmark_confidence": "low", "notes": "not_lateral_ceph"}}

If GATE 0 passes, proceed:
Your task: Identify the pixel coordinates of 5 hard-tissue cephalometric landmarks.

LANDMARK DEFINITIONS (hard tissue only — DW protocol §1):
- S = Sella: geometric center of the sella turcica outline (NOT the clinoid processes)
- N = Nasion: most anterior point of the fronto-nasal suture (BONE SUTURE, NOT soft-tissue nasal tip)
- A = Subspinale (A-point): deepest concavity on the anterior surface of the maxillary alveolar bone, between ANS and prosthion (🔴 NOT upper lip / NOT incisor roots — hard bone edge only)
- B = Supramentale (B-point): deepest concavity on the anterior surface of the mandibular symphysis, between infradentale and pogonion (🔴 NOT lower lip / NOT lower incisor roots — hard bone edge only)
- Pog = Pogonion: most anterior point of the chin bone (NOT soft-tissue chin)

SELF-CHECKS (mandatory, Gate 1-5 in order):
Gate 1: SNA range check — if SNA < 70° or SNA > 95°, re-examine N and A placement. Re-attempt once. If SNA still outside range, mark resolvable=false with notes="SNA_out_of_range=<value>".
Gate 2 (strictest — overlay/visibility): If the A or B point location is UNCERTAIN due to EITHER: (a) dense ceph tracing overlay lines covering the bone outline in that region, OR (b) double image/blur at the landmark site — mark resolvable=false. Do NOT place A/B and then flag low confidence — the rule is: uncertain visibility → resolvable=false, no coordinates. This prevents the 76db failure pattern where overlay-obscured A gets placed incorrectly, causing false-low SNA and false-straight convexity (direction reversal error).
Gate 3: Directional consistency — if ALL of these are true simultaneously: (a) SNA < 79° AND (b) facial convexity < 165° (quite straight/concave) AND (c) the facial profile appears convex (prominent upper jaw, receded chin), this is a directional conflict suggesting A was placed incorrectly (76db pattern). Mark resolvable=false with notes="direction_conflict: low_SNA+straight_conv inconsistent with convex facial profile".
Gate 4: B point — if B concavity is unclear or obstructed, mark resolvable=false.
Gate 5: Asymmetry — if the patient has significant facial asymmetry with mid-face rotated out of the sagittal plane, mark resolvable=false with notes="asymmetry-rotated".
NEVER fabricate or guess coordinates if unsure. Return resolvable=false instead.

Output ONLY valid JSON, nothing else. Format:
{{
  "resolvable": true or false,
  "S": [x, y],
  "N": [x, y],
  "A": [x, y],
  "B": [x, y],
  "Pog": [x, y],
  "landmark_confidence": "high" or "mod" or "low",
  "notes": "any flags or empty string"
}}

If resolvable=false, set all coordinate arrays to null:
{{
  "resolvable": false,
  "S": null, "N": null, "A": null, "B": null, "Pog": null,
  "landmark_confidence": "low",
  "notes": "reason for non-resolvable"
}}
"""


def angle_at_vertex(p1, vertex, p2):
    v1 = (p1[0] - vertex[0], p1[1] - vertex[1])
    v2 = (p2[0] - vertex[0], p2[1] - vertex[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    mag1 = math.hypot(*v1)
    mag2 = math.hypot(*v2)
    if mag1 < 1e-9 or mag2 < 1e-9:
        return None
    cos_a = max(-1.0, min(1.0, dot / (mag1 * mag2)))
    return round(math.degrees(math.acos(cos_a)), 2)


def compute_angles(pts):
    S, N, A, B, Pog = pts["S"], pts["N"], pts["A"], pts["B"], pts["Pog"]
    SNA = angle_at_vertex(S, N, A)
    SNB = angle_at_vertex(S, N, B)
    ANB = round(SNA - SNB, 2) if SNA is not None and SNB is not None else None
    convexity = angle_at_vertex(N, A, Pog)
    return {"SNA_deg": SNA, "SNB_deg": SNB, "ANB_deg": ANB, "facial_convexity_deg": convexity}


def run_claude_vision(img_path: Path) -> dict:
    """Call claude CLI to estimate landmarks. Returns parsed dict."""
    prompt = LANDMARK_PROMPT.format(img_path=str(img_path))
    proc = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = proc.stdout.strip()

    # Extract JSON from output (might have surrounding text)
    start = output.find("{")
    end = output.rfind("}") + 1
    if start == -1 or end == 0:
        return {"resolvable": False, "notes": f"parse_error: no json found", "raw": output[:200]}

    try:
        return json.loads(output[start:end])
    except json.JSONDecodeError as e:
        return {"resolvable": False, "notes": f"parse_error: {e}", "raw": output[:200]}


def load_existing() -> dict:
    if OUTPUT_CSV.exists():
        df = pd.read_csv(OUTPUT_CSV)
        return {r["case_stem"]: r for r in df.to_dict("records")}
    return {}


def save_record(records: list):
    pd.DataFrame(records).to_csv(OUTPUT_CSV, index=False)


def process_case(stem: str, img_path: Path, idx: int, total: int) -> dict:
    print(f"[{idx}/{total}] {stem} ... ", end="", flush=True)

    result = run_claude_vision(img_path)

    resolvable = result.get("resolvable", False)
    notes = result.get("notes", "")
    confidence = result.get("landmark_confidence", "low")

    if not resolvable or result.get("S") is None:
        record = {
            "case_stem": stem,
            "calibration_status": CALIBRATION_STATUS,
            "resolvable": False,
            "SNA_deg": None, "SNB_deg": None, "ANB_deg": None, "facial_convexity_deg": None,
            "landmark_confidence": confidence,
            "notes": notes or "non-resolvable",
            "S_x": None, "S_y": None, "N_x": None, "N_y": None,
            "A_x": None, "A_y": None, "B_x": None, "B_y": None,
            "Pog_x": None, "Pog_y": None,
        }
        print(f"NON-RESOLVABLE — {notes[:60]}")
        return record

    try:
        pts = {lm: tuple(result[lm]) for lm in ["S", "N", "A", "B", "Pog"]}
        angles = compute_angles(pts)

        # DW §9 self-check: SNA range 70-95
        sna = angles.get("SNA_deg")
        if sna is not None and not (70 <= sna <= 95):
            notes = f"SNA_out_of_range={sna}; " + notes
            confidence = "low"
            if not (65 <= sna <= 100):
                # Really extreme — escalate
                record = {
                    "case_stem": stem,
                    "calibration_status": CALIBRATION_STATUS,
                    "resolvable": False,
                    "SNA_deg": sna, "SNB_deg": angles.get("SNB_deg"),
                    "ANB_deg": None, "facial_convexity_deg": None,
                    "landmark_confidence": "low",
                    "notes": notes,
                    "S_x": pts["S"][0], "S_y": pts["S"][1],
                    "N_x": pts["N"][0], "N_y": pts["N"][1],
                    "A_x": pts["A"][0], "A_y": pts["A"][1],
                    "B_x": pts["B"][0], "B_y": pts["B"][1],
                    "Pog_x": pts["Pog"][0], "Pog_y": pts["Pog"][1],
                }
                print(f"ESCALATE (SNA={sna}) — {notes[:60]}")
                return record

        record = {
            "case_stem": stem,
            "calibration_status": CALIBRATION_STATUS,
            "resolvable": True,
            **angles,
            "landmark_confidence": confidence,
            "notes": notes,
            **{f"S_x": pts["S"][0], "S_y": pts["S"][1]},
            **{f"N_x": pts["N"][0], "N_y": pts["N"][1]},
            **{f"A_x": pts["A"][0], "A_y": pts["A"][1]},
            **{f"B_x": pts["B"][0], "B_y": pts["B"][1]},
            **{f"Pog_x": pts["Pog"][0], "Pog_y": pts["Pog"][1]},
        }
        print(f"OK — SNA={angles.get('SNA_deg')} SNB={angles.get('SNB_deg')} conv={angles.get('facial_convexity_deg')} [{confidence}]")
        return record

    except Exception as e:
        record = {
            "case_stem": stem,
            "calibration_status": CALIBRATION_STATUS,
            "resolvable": False,
            "SNA_deg": None, "SNB_deg": None, "ANB_deg": None, "facial_convexity_deg": None,
            "landmark_confidence": "low",
            "notes": f"processing_error: {e}",
            "S_x": None, "S_y": None, "N_x": None, "N_y": None,
            "A_x": None, "A_y": None, "B_x": None, "B_y": None,
            "Pog_x": None, "Pog_y": None,
        }
        print(f"ERROR — {e}")
        return record


def main(dry_run: bool = False, start_from: int = 0):
    manifest = pd.read_csv(MANIFEST_PATH, sep="\t")
    existing = load_existing()
    records = list(existing.values())
    total = len(manifest)

    print(f"Batch ceph vision — {total} cases, {len(existing)} already done")
    print(f"Output: {OUTPUT_CSV}")
    print(f"Provenance: {CALIBRATION_STATUS} / weight=NULL / C-c EXCLUDED\n")

    for i, row in enumerate(manifest.itertuples(), 1):
        if i < start_from:
            continue
        stem = row.stem
        if stem in existing:
            print(f"[{i}/{total}] {stem} — SKIP (already done)")
            continue

        img_path = EXTRACT_BASE / stem / row.file
        if not img_path.exists():
            print(f"[{i}/{total}] {stem} — SKIP (image not found: {img_path})")
            continue

        if dry_run:
            print(f"[{i}/{total}] {stem} — DRY RUN (would process {row.file})")
            continue

        record = process_case(stem, img_path, i, total)
        records.append(record)
        save_record(records)

    print(f"\nDone. Results at {OUTPUT_CSV}")
    df = pd.read_csv(OUTPUT_CSV)
    print(f"  Total: {len(df)}")
    print(f"  Resolvable: {df['resolvable'].sum()}")
    print(f"  Non-resolvable: {(~df['resolvable']).sum()}")
    if "SNA_deg" in df.columns:
        valid = df[df["resolvable"] == True]["SNA_deg"].dropna()
        if len(valid):
            print(f"  SNA range: {valid.min():.1f}°–{valid.max():.1f}° (mean={valid.mean():.1f}°)")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    start_from = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--from=")), 1))
    main(dry_run=dry_run, start_from=start_from)
