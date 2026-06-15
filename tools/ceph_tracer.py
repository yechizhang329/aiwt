"""Ceph tracing tool — Path① landmark placement + angle extraction.

Protocol: DW v3_ceph_tracing_protocol.md
Landmarks: S (Sella), N (Nasion), A (Subspinale), B (Supramentale), Pog (Pogonion)
Measures: SNA, SNB, ANB (derived), facial_convexity (∠N-A-Pog)
Output: /tmp/ceph_trace_results.csv
"""

import math
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit_image_coordinates import streamlit_image_coordinates

MANIFEST_PATH = Path("/tmp/convex_att_manifest.tsv")
EXTRACT_BASE = Path("/tmp/convex_extracts")
OUTPUT_CSV = Path("/tmp/ceph_trace_results.csv")

LANDMARK_SEQ = ["S", "N", "A", "B", "Pog"]
LANDMARK_DESC = {
    "S": "Sella (蝶鞍中心)",
    "N": "Nasion (额鼻缝最前点)",
    "A": "A点 Subspinale (上颌牙槽弓最凹, 硬组织)",
    "B": "B点 Supramentale (下颌牙槽弓最凹, 硬组织)",
    "Pog": "Pogonion (颏部骨最前点)",
}
LANDMARK_COLOR = {
    "S":   (255, 80, 80),
    "N":   (80, 220, 80),
    "A":   (80, 120, 255),
    "B":   (255, 200, 40),
    "Pog": (240, 80, 240),
}

DOT_RADIUS = 8
FONT_SIZE = 22


def angle_at_vertex(p1, vertex, p2):
    """Interior angle at vertex between rays vertex→p1 and vertex→p2 (degrees)."""
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
    """pts: dict {S,N,A,B,Pog} → (x,y). Returns dict of angles."""
    S, N, A, B, Pog = pts["S"], pts["N"], pts["A"], pts["B"], pts["Pog"]
    SNA = angle_at_vertex(S, N, A)
    SNB = angle_at_vertex(S, N, B)
    ANB = round(SNA - SNB, 2) if SNA is not None and SNB is not None else None
    convexity = angle_at_vertex(N, A, Pog)
    return {"SNA_deg": SNA, "SNB_deg": SNB, "ANB_deg": ANB, "facial_convexity_deg": convexity}


def draw_landmarks(img: Image.Image, pts: dict) -> Image.Image:
    """Draw placed landmarks on a copy of the image."""
    img = img.copy()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", FONT_SIZE)
    except Exception:
        font = ImageFont.load_default()
    for name, (x, y) in pts.items():
        color = LANDMARK_COLOR[name]
        r = DOT_RADIUS
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(255, 255, 255), width=2)
        draw.text((x + r + 3, y - r), name, fill=color, font=font)
    # Draw SN line if both placed
    if "S" in pts and "N" in pts:
        draw.line([pts["S"], pts["N"]], fill=(200, 200, 200), width=2)
    return img


def load_manifest():
    df = pd.read_csv(MANIFEST_PATH, sep="\t")
    return df


def load_results():
    if OUTPUT_CSV.exists():
        return pd.read_csv(OUTPUT_CSV).to_dict("records")
    return []


def save_result(record: dict):
    records = load_results()
    # Replace existing entry for same stem
    records = [r for r in records if r.get("case_stem") != record["case_stem"]]
    records.append(record)
    pd.DataFrame(records).to_csv(OUTPUT_CSV, index=False)


def get_case_image_path(stem: str, filename: str) -> Path:
    return EXTRACT_BASE / stem / filename


# ── Streamlit app ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Ceph Tracer", layout="wide")
st.title("头影测量描迹工具 (Path① tracing-tool)")
st.caption("Protocol: DW v3_ceph_tracing_protocol.md | Landmarks: S / N / A / B / Pog | Hard tissue only")

# Load manifest + existing results
manifest = load_manifest()
existing = {r["case_stem"]: r for r in load_results()}
cases = manifest["stem"].tolist()
total = len(cases)

# Sidebar — case selector + progress
with st.sidebar:
    st.markdown("### Progress")
    done_count = sum(1 for s in cases if s in existing and existing[s].get("resolvable") is not None)
    st.progress(done_count / total, text=f"{done_count}/{total} cases")

    st.markdown("### Case navigator")
    if "case_idx" not in st.session_state:
        # Default to first unfinished case
        unfinished = [i for i, s in enumerate(cases) if s not in existing]
        st.session_state.case_idx = unfinished[0] if unfinished else 0

    st.session_state.case_idx = st.selectbox(
        "Select case",
        range(total),
        index=st.session_state.case_idx,
        format_func=lambda i: f"{cases[i]} {'✓' if cases[i] in existing else ''}",
    )

    st.markdown("---")
    if st.button("⬅ Prev", disabled=st.session_state.case_idx == 0):
        st.session_state.case_idx -= 1
        st.session_state.pop("pts", None)
        st.rerun()
    if st.button("Next ➡", disabled=st.session_state.case_idx == total - 1):
        st.session_state.case_idx += 1
        st.session_state.pop("pts", None)
        st.rerun()

    st.markdown("---")
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, "rb") as f:
            st.download_button("⬇ Download CSV", f, file_name="ceph_trace_results.csv", mime="text/csv")

# Current case
idx = st.session_state.case_idx
stem = cases[idx]
row = manifest[manifest["stem"] == stem].iloc[0]
img_path = get_case_image_path(stem, row["file"])

st.markdown(f"## Case {idx + 1}/{total}: `{stem}`")
st.markdown(f"Image: `{row['file']}` | Att: `{row['att_id'][:8]}…`")

if not img_path.exists():
    st.error(f"Image not found: {img_path}")
    st.stop()

# Init per-case landmark state
state_key = f"pts_{stem}"
if state_key not in st.session_state:
    # Pre-fill from existing result if available
    existing_rec = existing.get(stem, {})
    loaded_pts = {}
    for lm in LANDMARK_SEQ:
        xk, yk = f"{lm}_x", f"{lm}_y"
        if xk in existing_rec and yk in existing_rec and existing_rec[xk] is not None:
            try:
                loaded_pts[lm] = (int(existing_rec[xk]), int(existing_rec[yk]))
            except (ValueError, TypeError):
                pass
    st.session_state[state_key] = loaded_pts

pts = st.session_state[state_key]
next_lm = LANDMARK_SEQ[len(pts)] if len(pts) < len(LANDMARK_SEQ) else None

# Load + annotate image
orig_img = Image.open(img_path).convert("RGB")
display_img = draw_landmarks(orig_img, pts)

# Layout: image (left) + controls (right)
col_img, col_ctrl = st.columns([3, 1])

with col_img:
    if next_lm:
        color_hex = "#{:02X}{:02X}{:02X}".format(*LANDMARK_COLOR[next_lm])
        st.markdown(
            f"**Click to place: <span style='color:{color_hex}; font-size:1.2em'>● {next_lm}</span> "
            f"— {LANDMARK_DESC[next_lm]}**",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("**All 5 landmarks placed. Review angles → Save.**")

    coords = streamlit_image_coordinates(display_img, key=f"img_{stem}_{len(pts)}")

    if coords and next_lm:
        x, y = int(coords["x"]), int(coords["y"])
        pts[next_lm] = (x, y)
        st.session_state[state_key] = pts
        st.rerun()

with col_ctrl:
    st.markdown("### Placed landmarks")
    for lm in LANDMARK_SEQ:
        if lm in pts:
            color_hex = "#{:02X}{:02X}{:02X}".format(*LANDMARK_COLOR[lm])
            st.markdown(
                f"<span style='color:{color_hex}'>●</span> **{lm}**: ({pts[lm][0]}, {pts[lm][1]})",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f"○ **{lm}**: —")

    if len(pts) == 5:
        angles = compute_angles(pts)
        st.markdown("### Measured angles")
        st.metric("SNA", f"{angles['SNA_deg']}°")
        st.metric("SNB", f"{angles['SNB_deg']}°")
        st.metric("facial_convexity", f"{angles['facial_convexity_deg']}°")
        st.caption(f"ANB (derived): {angles['ANB_deg']}°")

    st.markdown("---")

    # Undo last point
    if pts and st.button("↩ Undo last"):
        last = LANDMARK_SEQ[len(pts) - 1]
        pts.pop(last, None)
        st.session_state[state_key] = pts
        st.rerun()

    # Clear all
    if st.button("🗑 Clear all"):
        st.session_state[state_key] = {}
        st.rerun()

    st.markdown("---")
    st.markdown("### Quality + save")

    # Pre-fill from existing
    ex = existing.get(stem, {})
    conf_default = ex.get("landmark_confidence", "high")
    conf_idx = ["high", "mod", "low"].index(conf_default) if conf_default in ["high", "mod", "low"] else 0
    resolvable_default = bool(ex.get("resolvable", True))
    notes_default = ex.get("notes", "")

    confidence = st.selectbox("landmark_confidence", ["high", "mod", "low"], index=conf_idx)
    resolvable = st.checkbox("resolvable", value=resolvable_default)
    notes = st.text_area("notes", value=notes_default, height=80,
                         placeholder="偏-dominant / overlay遮挡 / 描迹质量 等")

    can_save = len(pts) == 5 or not resolvable
    if st.button("💾 Save & Next", disabled=not can_save, type="primary"):
        if len(pts) == 5:
            angles = compute_angles(pts)
        else:
            angles = {"SNA_deg": None, "SNB_deg": None, "ANB_deg": None, "facial_convexity_deg": None}

        record = {
            "case_stem": stem,
            "SNA_deg": angles["SNA_deg"],
            "SNB_deg": angles["SNB_deg"],
            "ANB_deg": angles["ANB_deg"],
            "facial_convexity_deg": angles["facial_convexity_deg"],
            "convexity_mm": None,
            "landmark_confidence": confidence,
            "resolvable": resolvable,
            "notes": notes,
            **{f"{lm}_x": pts[lm][0] if lm in pts else None for lm in LANDMARK_SEQ},
            **{f"{lm}_y": pts[lm][1] if lm in pts else None for lm in LANDMARK_SEQ},
        }
        save_result(record)

        # Advance to next unfinished
        updated_existing = {r["case_stem"]: r for r in load_results()}
        next_unfinished = next(
            (i for i, s in enumerate(cases) if s not in updated_existing and i > idx),
            idx + 1 if idx + 1 < total else idx,
        )
        st.session_state.case_idx = next_unfinished
        st.session_state.pop(state_key, None)
        st.rerun()

# Show burn-in advisory
with st.expander("⚠️ Burn-in守线 reminder", expanded=False):
    st.markdown("""
**NewTom burn-in values** (if visible on image):
- Do NOT use as `calibrated` — landmark protocol + calibration unknown
- These are `external_burnin_protocol_unknown` + weight NULL
- AUTHORITATIVE = Step-2 self-tracing (this tool, DW protocol)
- Record as cross-check/prior only; never short-circuit self-tracing

**Hard-tissue rule**: Click A/B on **hard tissue** (bone outline), never on soft-tissue lip profile.
    """)
