"""Single-ceph advisory CLI: input gate → HRNet → bone measurements → sanity gate → overlay.

Usage: python3 tools/run_ceph_advisory_cli.py --image <ceph.png>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ceph_measure_extend import LM_IDX_FULL, _load_hp, measure

_TOOLS_DIR = Path(__file__).resolve().parent
_ROOT = _TOOLS_DIR.parent


def _input_gate(image_path):
    import numpy as np
    from PIL import Image

    img = Image.open(image_path)
    w, h = img.size
    long_edge = max(w, h)
    arr = np.array(img)
    gray = np.mean(arr[:, :, :3], axis=2).astype(np.uint8) if arr.ndim == 3 and arr.shape[2] >= 3 else arr

    issues = []
    if arr.ndim == 3 and arr.shape[2] >= 3:
        r, g, b = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
        spread = np.mean(np.abs(r - g)) + np.mean(np.abs(r - b))
        if spread > 15:
            issues.append(f"color_image: spread={spread:.1f} (likely photo/screenshot, not digital)")

    margin = max(h, w) // 20
    edges = [gray[:margin, :].mean(), gray[-margin:, :].mean(), gray[:, :margin].mean(), gray[:, -margin:].mean()]
    if sum(1 for e in edges if e > 200) >= 2:
        issues.append("lightbox_frame: bright borders (re-photo)")

    res_note = "pass" if long_edge >= 1500 else ("low_confidence" if long_edge >= 1000 else "below_1000")
    aspect = max(w, h) / min(w, h) if min(w, h) > 0 else 999
    if aspect > 3.0:
        issues.append(f"unlikely_lateral: aspect={aspect:.1f}")

    return {"pass": len(issues) == 0, "size": f"{w}x{h}", "long_edge": long_edge,
            "resolution": res_note, "issues": issues}


_PHYSIO_BOUNDS = {
    "SNA": (65, 100), "SNB": (62, 95), "ANB": (-12, 16), "FH_NPo": (75, 105),
    "NA_APo": (-25, 30), "FMA": (5, 55), "SGn_FH": (45, 85), "MP_SN": (10, 58),
}


def _sanity_gate(confs, meas):
    issues = []
    key_lm = ["S", "N", "A", "B", "Or", "Po", "Go", "Me"]
    low = [lm for lm in key_lm if confs.get(lm, 0) < 5]
    if len(low) >= 3:
        issues.append(f"anatomical_incomplete: {low}")
    if confs.get("S", 0) < 5:
        issues.append(f"sella_low_confidence: {confs.get('S', 0):.1f}")
    for key, (lo, hi) in _PHYSIO_BOUNDS.items():
        val = meas.get(key)
        if val is not None and not (lo <= val <= hi):
            issues.append(f"{key}: {val}° outside {lo}-{hi}")
    sna, snb, anb = meas.get("SNA"), meas.get("SNB"), meas.get("ANB")
    if sna is not None and snb is not None and anb is not None:
        exp = sna - snb
        if abs(anb - exp) > 1.5:
            issues.append(f"ANB_inconsistent: ANB={anb}° vs SNA-SNB={exp:.1f}°")
    return {"pass": len(issues) == 0, "issues": issues}


def _make_overlay(image_path, pts, out_path):
    from PIL import Image, ImageDraw
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    bone = {"S", "N", "A", "B", "Or", "Po", "Go", "Me", "Pog", "Gn", "ANS", "PNS"}
    for name, p in pts.items():
        if not p:
            continue
        x, y = p
        c = (255, 50, 50) if name in bone else (50, 255, 50)
        draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=c, outline=(255, 255, 255), width=2)
        draw.text((x + 8, y - 6), name, fill=(255, 255, 0))
    S, N, Or, Po = pts.get("S"), pts.get("N"), pts.get("Or"), pts.get("Po")
    if S and N:
        draw.line([S, N], fill=(255, 255, 0), width=2)
    if Or and Po:
        draw.line([Or, Po], fill=(0, 255, 255), width=2)
    img.save(out_path)


def main():
    ap = argparse.ArgumentParser(description="Single-ceph bone advisory")
    ap.add_argument("--image", required=True, help="Path to lateral ceph image")
    args = ap.parse_args()

    import torch

    image_path = Path(args.image).resolve()
    if not image_path.exists():
        print(f"ERROR: {image_path} not found")
        sys.exit(1)

    gate = _input_gate(image_path)
    print(f"=== Input Gate ===")
    print(f"  Image: {image_path.name} ({gate['size']}, long_edge={gate['long_edge']})")
    print(f"  Resolution: {gate['resolution']}")
    if not gate["pass"]:
        print(f"  ✗ REJECT: {gate['issues']}")
        print(f"  无可靠测量、需数字原片")
        sys.exit(0)
    print(f"  ✓ PASS (clean digital)")

    hp = _load_hp()
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = hp.load_model(device)
    pts, confs = hp.run_inference(model, image_path, device)
    meas = measure(pts)
    gate_s = _sanity_gate(confs, meas)

    overlay_path = image_path.parent / f"{image_path.stem}_advisory_overlay.png"
    _make_overlay(image_path, pts, overlay_path)

    print(f"\n=== Bone Measurements (provenance: hrnet_estimated) ===")
    for key in ["SNA", "SNB", "ANB", "FH_NPo", "NA_APo", "FMA", "SGn_FH", "MP_SN"]:
        val = meas.get(key)
        flag = ""
        if val is not None:
            lo, hi = _PHYSIO_BOUNDS.get(key, (None, None))
            if lo is not None and not (lo <= val <= hi):
                flag = f" ⚠ outside {lo}-{hi}"
        print(f"  {key:8s}: {val}°{flag}" if val is not None else f"  {key:8s}: —")

    print(f"\n=== Sanity Gate ===")
    if gate_s["pass"]:
        print(f"  ✓ SANE — measurements reliable")
    else:
        for issue in gate_s["issues"]:
            print(f"  ⚠ {issue}")

    low_conf = {k: round(v, 2) for k, v in confs.items() if v < 7}
    if low_conf:
        print(f"\n=== Low-confidence landmarks ===")
        for k, v in low_conf.items():
            print(f"  {k}: {v}")

    print(f"\n叠图: {overlay_path}")
    print(json.dumps({"measurements": {k: v for k, v in meas.items() if v is not None},
                       "sanity": gate_s, "provenance": "hrnet_estimated"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
