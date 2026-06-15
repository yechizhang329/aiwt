"""IX-4 compliance surfaces for case display (RD-1a 档1 scaffolding).

W8: diagnose-or-explain — every result either carries a direction or a reason (no silent fallback).
W9: measurement-serves-diagnosis — measurements shown as diagnostic evidence, not raw numbers.

Track 1 (LLM-Wiki) first; Track 2 deferred. R-1 gbrain isolation maintained (no gbrain import).
"""

import streamlit as st


# ── W8: diagnose-or-explain surface ─────────────────────────────────────────────

_W8_ESCALATION_LABELS = {
    "ix_cross_track_escalate": "多轨标签不一致，需人工确认",
    "ix_non_convergence": "IX 分析未收敛，需人工确认",
}

_STATUS_DISPLAY = {
    "done": ("✅ 已生成初步诊断讨论", "success"),
    "awaiting_doctor_review": ("⚠️ 已升级，待医生确认", "warning"),
    "escalated": ("⚠️ 已升级，待医生确认", "warning"),
    "failed": ("❌ 处理失败", "error"),
    "pending": ("⏳ 分析中", "info"),
    "processing": ("⏳ 分析中", "info"),
    "queued": ("⏳ 排队中", "info"),
}


def render_w8_surface(case: dict):
    """W8 — diagnose-or-explain: show direction or reason, never blank.

    For diagnosed cases: highlight that a direction was reached.
    For escalated cases: show the reason so the human understands why (W8 forbids silent fallback).
    """
    status = case.get("status", "")
    meta = case.get("metadata") or {}

    label, kind = _STATUS_DISPLAY.get(status, (f"状态：{status}", "info"))
    getattr(st, kind)(label)

    # Escalation reason surface (W8: explain why, never blank)
    if status in ("awaiting_doctor_review", "escalated"):
        reasons = []

        # IX-specific escalation flags
        for flag_key, flag_label in _W8_ESCALATION_LABELS.items():
            if meta.get(flag_key):
                reasons.append(flag_label)

        # General error message
        err = case.get("error_msg") or ""
        if err and not any(r in err for r in reasons):
            reasons.append(err)

        # HRW clinical flags
        final_output = case.get("final_output") or {}
        hrw_flag = final_output.get("hrw_clinical_flag")
        if hrw_flag:
            reasons.append(f"临床规则提示：{hrw_flag}")

        if reasons:
            with st.expander("升级原因（W8 诊断说明）", expanded=True):
                for r in reasons:
                    st.markdown(f"- {r}")
        else:
            st.error("⚠️ W8 违约：升级但未附原因 — 系统缺陷，请上报")


# ── W9: measurement-serves-diagnosis surface ────────────────────────────────────

_MEASUREMENT_KEYS_ZH = {
    "SNA": "SNA（颌骨前后位置）",
    "SNB": "SNB（下颌前后位置）",
    "ANB": "ANB（骨性凸凹度）",
    "anb": "ANB（骨性凸凹度）",
    "snb": "SNB（下颌前后位置）",
    "sna": "SNA（颌骨前后位置）",
    "wits": "Wits 值",
    "u1_l1": "上下切牙角",
}


def render_w9_surface(case: dict):
    """W9 — measurement-serves-diagnosis: show measurements as diagnostic evidence.

    Pulls cephalometric estimates from submission and shows them as "what this tells us"
    (measurement → diagnostic contribution), not raw number lists.

    If no measurements available, shows the W9 implication: diagnosis was reached via
    gestalt multi-indicator convergence without precise numeric anchors.
    """
    submission = case.get("submission") or {}
    cf = submission.get("clinical_findings") or {}
    ceph = cf.get("cephalometric_estimates") or {}

    available = {k: v for k, v in ceph.items()
                 if k in _MEASUREMENT_KEYS_ZH and v is not None}

    final_output = case.get("final_output") or {}
    meta = case.get("metadata") or {}

    with st.expander("📐 测量值诊断贡献（W9）", expanded=False):
        if available:
            st.caption("以下测量值参与辅助诊断判断（W9：测量服务诊断）")
            for k, v in available.items():
                label = _MEASUREMENT_KEYS_ZH.get(k, k.upper())
                conf = ceph.get("estimate_confidence", "unknown")
                conf_tag = f"置信度={conf}" if conf != "unknown" else ""
                st.markdown(f"- **{label}**: {v}°{f'（{conf_tag}）' if conf_tag else ''}")
        else:
            st.caption(
                "本例无头影精确度数输入 — 诊断依据多指标 gestalt 汇聚（AP magnitude / "
                "骨性代偿性 / 垂直不调 / 唇 strain），符合 W9 原则：够诊断即不要求精确度数。"
            )

        # Show IX evidence state if available (Track 1 KB falsification)
        ix_evidence = meta.get("ix_evidence_snapshot") or {}
        if ix_evidence:
            st.caption("IX 证伪路径结果：")
            for eid, state in list(ix_evidence.items())[:6]:
                st.markdown(f"  - `{eid}` → `{state}`")
