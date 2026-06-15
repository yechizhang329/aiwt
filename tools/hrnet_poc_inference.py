"""HRNet-W32 ceph landmark inference — POC validation set.

Provenance: hrnet_estimated — NOT calibrated. weight=NULL.
Model: /tmp/hrnet_model/best_model.pth (HRNet-W32, ISBI 19-landmark)
Output: /tmp/hrnet_poc_results.csv

ISBI 19-landmark indices (0-based):
  0=S(Sella), 1=N(Nasion), 2=Or, 3=Po, 4=A(Subspinale), 5=B(Supramentale),
  6=Pog, 7=Me, 8=Gn, 9=Go, 10=Is, 11=Ii, 12=UL, 13=LL, 14=Sn, 15=Pog',
  16=NasalTip, 17=ANS, 18=PNS

Architecture note:
  checkpoint's final_layer = Conv2d(32, 19, 1x1) applied to stage4[0] (32ch, 192x192).
  timm hrnet_w32 stage4 output[0] matches this exactly for 768x768 input.
"""

from __future__ import annotations  # py3.9 compat for `str | None` hints (no logic change)

import math
from pathlib import Path

import numpy as np
import pandas as pd
import timm
import torch
import torch.nn as nn
from PIL import Image

MODEL_PATH = Path("/tmp/hrnet_model/best_model.pth")
IMAGE_DIR = Path("/tmp/hrnet_poc_images")
OUTPUT_CSV = Path("/tmp/hrnet_poc_results.csv")

LM_IDX = {"S": 0, "N": 1, "A": 4, "B": 5, "Pog": 6}

INPUT_SIZE = 768
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


class HRNetCeph(nn.Module):
    """Wraps timm hrnet_w32 backbone + ceph final_layer head."""

    def __init__(self):
        super().__init__()
        self.backbone = timm.create_model("hrnet_w32", pretrained=False, num_classes=0)
        self.final_layer = nn.Conv2d(32, 19, kernel_size=1)
        self._stage4_feat = None

        self.backbone.stage4.register_forward_hook(self._hook)

    def _hook(self, module, input, output):
        self._stage4_feat = output[0]  # (B, 32, H/4, W/4)

    def forward(self, x):
        self.backbone(x)  # runs backbone, hook captures stage4[0]
        return self.final_layer(self._stage4_feat)  # (B, 19, H/4, W/4)


def load_model(device):
    model = HRNetCeph()
    ckpt = torch.load(MODEL_PATH, map_location=device, weights_only=False)
    state = ckpt["model_state_dict"]

    # Load backbone weights (keys without prefix match timm's naming)
    backbone_state = {k: v for k, v in state.items() if not k.startswith("final_layer")}
    missing, unexpected = model.backbone.load_state_dict(backbone_state, strict=False)
    if missing:
        print(f"  Backbone missing keys: {missing[:3]}")
    if unexpected:
        print(f"  Backbone unexpected keys: {unexpected[:3]}")

    # Load the ceph head
    head_state = {k.replace("final_layer.", ""): v
                  for k, v in state.items() if k.startswith("final_layer")}
    model.final_layer.load_state_dict(head_state)
    print(f"  final_layer loaded: weight{state['final_layer.weight'].shape}")

    model.eval()
    return model.to(device)


def preprocess(img_path: Path):
    img = Image.open(img_path).convert("RGB")
    orig_w, orig_h = img.size
    img_resized = img.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    arr = np.array(img_resized, dtype=np.float32) / 255.0
    arr = (arr - MEAN) / STD
    tensor = torch.from_numpy(arr.transpose(2, 0, 1)).unsqueeze(0).float()
    return tensor, orig_w, orig_h


def heatmap_to_coords(hm: torch.Tensor, temperature: float = 0.05):
    """Soft-argmax with temperature sharpening + per-landmark peak confidence.

    Temperature: lower = closer to hard-argmax (0.05 makes it sharp).
    Returns (x, y, confidence) in heatmap space.
    confidence = max heatmap value after ReLU (proxy for landmark presence strength).
    """
    H, W = hm.shape
    hm_relu = torch.nn.functional.relu(hm)
    confidence = hm_relu.max().item()
    if confidence < 1e-9:
        return None, None, 0.0
    flat = hm_relu.reshape(-1)
    flat = flat / temperature
    flat = flat - flat.max()  # numerical stability
    flat = torch.exp(flat)
    flat = flat / flat.sum()
    ys = torch.arange(H, dtype=torch.float32).unsqueeze(1).expand(H, W).reshape(-1)
    xs = torch.arange(W, dtype=torch.float32).unsqueeze(0).expand(H, W).reshape(-1)
    x = (flat * xs).sum().item()
    y = (flat * ys).sum().item()
    return x, y, confidence


def angle_at_vertex(p1, vertex, p2):
    v1 = (p1[0] - vertex[0], p1[1] - vertex[1])
    v2 = (p2[0] - vertex[0], p2[1] - vertex[1])
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    m1 = math.hypot(*v1)
    m2 = math.hypot(*v2)
    if m1 < 1e-9 or m2 < 1e-9:
        return None
    return round(math.degrees(math.acos(max(-1.0, min(1.0, dot / (m1 * m2))))), 2)


def run_inference(model, img_path: Path, device):
    """Returns (pts dict, confs dict). pts values are (x, y) in orig-image space or None."""
    tensor, orig_w, orig_h = preprocess(img_path)
    with torch.no_grad():
        heatmaps = model(tensor.to(device))  # (1, 19, 192, 192)

    heatmaps = heatmaps[0].cpu()  # (19, 192, 192)
    hm_h, hm_w = heatmaps.shape[1], heatmaps.shape[2]

    pts, confs = {}, {}
    for name, idx in LM_IDX.items():
        hx, hy, conf = heatmap_to_coords(heatmaps[idx])
        confs[name] = conf
        if hx is None:
            pts[name] = None
        else:
            pts[name] = (hx * orig_w / hm_w, hy * orig_h / hm_h)

    return pts, confs


def compute_angles(pts):
    S, N, A, B, Pog = pts.get("S"), pts.get("N"), pts.get("A"), pts.get("B"), pts.get("Pog")
    if any(p is None for p in [S, N, A, B, Pog]):
        return {}
    SNA = angle_at_vertex(S, N, A)
    SNB = angle_at_vertex(S, N, B)
    ANB = round(SNA - SNB, 2) if SNA and SNB else None
    conv = angle_at_vertex(N, A, Pog)
    return {"SNA_deg": SNA, "SNB_deg": SNB, "ANB_deg": ANB, "facial_convexity_deg": conv}


def sanity_check(angles: dict, pts: dict) -> str | None:
    """§9 geometry sanity floor. Returns failure reason string or None if OK.

    GARBAGE-REJECTION ONLY — NOT a clinical validity stamp.
    sanity-pass ≠ direction-validated (DW f9540a3a):
      zhen_10: SNA=85.0, SNB=79.8 → passes all checks below YET is the
      confident-wrong 76db trap. floor correctly does NOT catch it.
    Any hrnet_estimated output that passes must still be cold-read direction
    cross-validated before consumption. sanity-pass confers NO direction confidence.

    SNA∈[65,100] is an untrustworthy lower bound (garbage-rejection), NOT normal range.
    Extreme values within range (65-70 = true severe retrognathia, 95-100) remain
    prior-only / low-conf / pending cross-check — not to be treated as confident.
    """
    SNA = angles.get("SNA_deg")
    SNB = angles.get("SNB_deg")

    if SNA is None or SNB is None:
        return "missing_SNA_or_SNB"

    # §9 range check: SNA outside 65-100° = anatomically implausible
    if not (65 <= SNA <= 100):
        return f"SNA_out_of_range={SNA}"

    # SNB range check
    if not (60 <= SNB <= 100):
        return f"SNB_out_of_range={SNB}"

    # S-N degenerate: if S and N are within 5 pixels in orig space = same point
    S, N = pts.get("S"), pts.get("N")
    if S and N:
        sn_dist = math.hypot(S[0] - N[0], S[1] - N[1])
        if sn_dist < 20:
            return f"SN_degenerate_dist={sn_dist:.1f}px"

    # Geometric impossibility: SNB > SNA + 30 = mandible far ahead of maxilla
    if SNB > SNA + 30:
        return f"SNB_implausibly_exceeds_SNA: SNB={SNB} SNA={SNA}"

    return None


def main():
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"Device: {device}")
    print("Loading HRNet-W32...")
    model = load_model(device)
    print("Model ready.\n")

    images = sorted(f for f in IMAGE_DIR.glob("*")
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png"))
    print(f"POC images: {len(images)}\n")

    records = []
    for i, img_path in enumerate(images, 1):
        stem = img_path.stem
        print(f"[{i}/{len(images)}] {stem} ... ", end="", flush=True)
        try:
            pts, confs = run_inference(model, img_path, device)
            angles = compute_angles(pts)
            if not angles:
                print("NO ANGLES")
                records.append({"case_stem": stem, "resolvable": False,
                                 "calibration_status": "hrnet_estimated"})
                continue

            sanity_fail = sanity_check(angles, pts)
            if sanity_fail:
                print(f"SANITY_FAIL: {sanity_fail}")
                records.append({"case_stem": stem, "resolvable": False,
                                 "calibration_status": "hrnet_estimated",
                                 "notes": f"sanity_fail:{sanity_fail}"})
                continue

            row = {"case_stem": stem, "calibration_status": "hrnet_estimated",
                   "resolvable": True, **angles}
            for name in LM_IDX:
                p = pts.get(name)
                row[f"{name}_x"] = round(p[0], 1) if p else None
                row[f"{name}_y"] = round(p[1], 1) if p else None
                row[f"{name}_conf"] = round(confs.get(name, 0.0), 4)

            print(f"SNA={angles.get('SNA_deg')} SNB={angles.get('SNB_deg')} "
                  f"conv={angles.get('facial_convexity_deg')}")
            records.append(row)

        except Exception as e:
            print(f"ERROR: {e}")
            records.append({"case_stem": stem, "resolvable": False,
                             "calibration_status": "hrnet_estimated", "notes": str(e)})

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nResults → {OUTPUT_CSV}")
    resolvable = df.get("resolvable", pd.Series(dtype=bool))
    print(f"  Resolvable: {resolvable.sum()} / {len(df)}")


if __name__ == "__main__":
    main()
