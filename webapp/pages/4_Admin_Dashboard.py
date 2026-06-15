"""Change 28: KPI dashboard — admin and audit_agent only.

Metrics: case counts by status/scene · latency p50/p90/p99 per scene + per phase ·
7-day trend · escalation breakdown · today's case table.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import api_client
from auth import get_current_role

_ALLOWED_ROLES = ("admin", "audit_agent")
_CST = timezone(timedelta(hours=8))

_STATUS_LABELS = {
    "processing": "处理中",
    "done": "已完成",
    "escalated": "已升级",
    "failed": "失败",
    "pending": "等待中",
}

_SCENE_LABELS = {
    "1_patient": "Scene 1 (患者)",
    "3_doctor": "Scene 3 (医生)",
}

_PHASE_LABELS = {
    "A": "A·KC检索",
    "B": "B·临床分析",
    "C": "C·Critic验证",
    "D": "D·规则审核",
    "E": "E·答复生成",
    "F": "F·VoiceWrapper",
}


def _ms_to_str(ms) -> str:
    if ms is None:
        return "—"
    if ms >= 60000:
        return f"{ms/60000:.1f} min"
    return f"{ms/1000:.1f}s"


def _to_cst(utc_str: str) -> str:
    if not utc_str:
        return "—"
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        return dt.astimezone(_CST).strftime("%m-%d %H:%M")
    except Exception:
        return utc_str[:16].replace("T", " ")


def _render_tab(kpi: dict):
    total = kpi.get("total_cases", 0)
    by_status = kpi.get("by_status") or {}
    by_scene = kpi.get("by_scene") or {}
    lat = kpi.get("latency_ms") or {}
    modes = kpi.get("voice_mode_distribution") or {}
    esc_reasons = kpi.get("escalation_reasons") or {}
    retries = kpi.get("total_retries", 0)
    daily = kpi.get("daily_counts") or {}
    cases = kpi.get("cases") or []

    # ── Overview metrics ─────────────────────────────────────────────────────
    st.markdown("#### 概览")
    cols = st.columns(5)
    cols[0].metric("案例总数", total)
    cols[1].metric("已完成", by_status.get("done", 0))
    cols[2].metric("处理中", by_status.get("processing", 0))
    cols[3].metric("已升级", by_status.get("escalated", 0))
    cols[4].metric("失败", by_status.get("failed", 0))

    # Mode + latency summary
    col_mode, col_lat = st.columns(2)
    with col_mode:
        st.markdown("**模式分布**")
        mode_a = modes.get("A", 0)
        mode_b = modes.get("B", 0)
        mode_unk = modes.get("unknown", 0)
        st.caption(f"Mode A (标准): {mode_a}  ·  Mode B (降级): {mode_b}  ·  未知: {mode_unk}")
        if retries:
            st.caption(f"重试总计: {retries}")

    with col_lat:
        st.markdown("**综合延迟**")
        if lat:
            st.caption(
                f"p50: {_ms_to_str(lat.get('p50'))}  ·  "
                f"p90: {_ms_to_str(lat.get('p90'))}  ·  "
                f"p99: {_ms_to_str(lat.get('p99'))}"
            )
            st.caption(f"avg: {_ms_to_str(lat.get('avg'))}  ·  max: {_ms_to_str(lat.get('max'))}")
        else:
            st.caption("暂无延迟数据")

    # ── Scene breakdown ───────────────────────────────────────────────────────
    if by_scene:
        st.markdown("#### 场景分布")
        scene_data = {_SCENE_LABELS.get(k, k): v for k, v in by_scene.items()}
        st.bar_chart(scene_data)

    # ── Per-phase latency ─────────────────────────────────────────────────────
    lat_by_phase = kpi.get("latency_per_phase_ms") or {}
    if lat_by_phase:
        st.markdown("#### 各 Phase 延迟")
        phase_rows = []
        for ph in ("A", "B", "C", "D", "E", "F"):
            ph_d = lat_by_phase.get(ph)
            if ph_d:
                phase_rows.append({
                    "Phase": _PHASE_LABELS.get(ph, ph),
                    "avg": _ms_to_str(ph_d.get("avg")),
                    "p50": _ms_to_str(ph_d.get("p50")),
                    "p90": _ms_to_str(ph_d.get("p90")),
                })
        if phase_rows:
            st.dataframe(phase_rows, use_container_width=True, hide_index=True)

    # ── Daily trend ───────────────────────────────────────────────────────────
    if daily and len(daily) > 1:
        st.markdown("#### 每日案例数")
        st.bar_chart(daily)

    # ── Escalation breakdown ──────────────────────────────────────────────────
    if esc_reasons:
        st.markdown("#### 升级原因分布")
        for reason, count in sorted(esc_reasons.items(), key=lambda x: -x[1]):
            st.caption(f"• {reason}: {count}")

    # ── Today's cases table ───────────────────────────────────────────────────
    if cases:
        st.markdown("#### 案例明细")
        table_rows = []
        for c in cases:
            table_rows.append({
                "ID": c.get("case_id", ""),
                "场景": _SCENE_LABELS.get(c.get("scene"), c.get("scene", "")),
                "状态": _STATUS_LABELS.get(c.get("status"), c.get("status", "")),
                "提交时间": _to_cst(c.get("submitted_at", "")),
                "延迟": _ms_to_str(c.get("latency_ms")),
                "模式": c.get("voice_mode", "—"),
                "重试": c.get("retry_count", 0),
                "升级原因": c.get("escalation_reason") or "—",
            })
        st.dataframe(table_rows, use_container_width=True, hide_index=True)


# ── Entry point ───────────────────────────────────────────────────────────────

role = get_current_role()
if role not in _ALLOWED_ROLES:
    st.error("此页面仅限管理员和审计账号访问。")
    st.stop()

st.title("📊 运营 KPI 仪表盘")
st.caption("数据来源：cases DB。每次打开页面刷新。")

tab_today, tab_7d = st.tabs(["今日 (24h)", "7 天"])

with tab_today:
    try:
        kpi_today = api_client.get_kpi(range="today")
        _render_tab(kpi_today)
    except Exception as e:
        st.error(f"加载失败: {e}")

with tab_7d:
    try:
        kpi_7d = api_client.get_kpi(range="7d")
        _render_tab(kpi_7d)
    except Exception as e:
        st.error(f"加载失败: {e}")
