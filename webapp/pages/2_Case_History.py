"""Case history page — shows new API cases and legacy Slock-CLI jobs.

New API cases: fetched from GET /v1/cases (backend FastAPI).
Legacy jobs: read from webapp local jobs.db (read-only, no requeue).
"""

import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import api_client
import case_display
from auth import is_admin, get_current_role
from components.stage_progress import render_stage_progress, render_active_sidebar
from db import list_jobs

_CST = timezone(timedelta(hours=8))

_ALLOWED_ROLES = ("patient_team", "admin", "doctor", "audit_agent")


def _to_cst(utc_str: str) -> str:
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc)
        return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return utc_str[:16].replace("T", " ")


_API_STATUS_LABELS = {
    "pending":                ("⏸ 等待中",   "gray"),
    "queued":                 ("🕐 排队中",   "gray"),
    "processing":             ("⏳ 处理中",   "blue"),
    "done":                   ("✅ 已完成",   "green"),
    "awaiting_doctor_review": ("✅ 已完成",       "green"),
    "escalated":              ("⬆️ 已升级",  "orange"),
    "failed":                 ("❌ 失败",     "red"),
    "aborted":                ("⛔ 已终止",   "gray"),
}

_LEGACY_STATUS_LABELS = {
    "pending":     ("⏸ 等待提交", "gray"),
    "processing":  ("⏳ 处理中",  "blue"),
    "sufficiency": ("⚠️ 需补充",  "orange"),
    "done":        ("✅ 已完成",  "green"),
    "failed":      ("❌ 失败",    "red"),
    "timeout":     ("⏱ 超时",    "red"),
}


_PHASES = [
    ("A", "🔍 知识检索"),
    ("B", "🧠 临床分析"),
    ("C", "🔎 结论验证"),
    ("D", "📋 规则审核"),
    ("E", "✍️ 答复生成"),
]
_PHASE_KEYS = [p[0] for p in _PHASES]


def _friendly_error(raw: str) -> str:
    """Map raw error_msg to a user-friendly Chinese description."""
    r = raw.lower()
    if "json" in r and ("parse" in r or "decode" in r or "invalid" in r):
        return "系统暂时未能处理 — JSON 解析错误（技术问题，重新提交即可）"
    if "timeout" in r and "kc" in r:
        return "KC 深度检索超时（重新提交）"
    if "超时" in raw or "timeout" in r:
        return "处理超时（可重新提交）"
    if "image" in r and ("access" in r or "not found" in r or "missing" in r):
        return "图片访问错误（检查文件后重新提交）"
    if raw:
        return f"系统错误（{raw[:80]}）"
    return "系统暂时未能处理，请重新提交"


# ── New API cases section ──────────────────────────────────────────────────────

@st.fragment(run_every=30)
def _render_api_cases(role: str, username: str):
    st.subheader("📋 API 案例")

    try:
        cases = api_client.list_cases(limit=50)
    except Exception as e:
        st.error(f"加载案例失败: {e}")
        return
    # Cache for sidebar rendering
    st.session_state["_sidebar_cases"] = cases

    if not cases:
        st.info("暂无 API 案例记录。")
        return

    active = any(c["status"] in ("pending", "queued", "processing") for c in cases)
    if active:
        st.caption("⟳ 手动刷新页面查看最新状态")

    # Metrics
    mc = {s: sum(1 for c in cases if c["status"] == s)
          for s in ("queued", "processing", "done", "awaiting_doctor_review", "escalated", "failed")}
    done_total = mc["done"] + mc["awaiting_doctor_review"]
    col_total, col_queue, col_proc, col_done, col_esc, col_fail = st.columns(6)
    col_total.metric("全部", len(cases))
    col_queue.metric("排队中", mc["queued"])
    col_proc.metric("处理中", mc["processing"])
    col_done.metric("已完成", done_total)
    col_esc.metric("已升级", mc["escalated"])
    col_fail.metric("失败", mc["failed"])

    # Filter
    filter_opt = st.segmented_control(
        "筛选", ["全部", "排队中", "处理中", "已完成", "已升级", "失败"],
        default="全部", label_visibility="collapsed", key="s2_api_filter"
    )
    _filter_map = {
        "全部": None, "排队中": ["queued"], "处理中": ["pending", "processing"],
        "已完成": ["done", "awaiting_doctor_review"], "已升级": ["escalated"], "失败": ["failed"],
    }
    filter_statuses = _filter_map.get(filter_opt or "全部")
    filtered = [c for c in cases if filter_statuses is None or c["status"] in filter_statuses]

    st.divider()

    for case in filtered:
        label, _ = _API_STATUS_LABELS.get(case["status"], ("未知", "gray"))
        submitted_dt = _to_cst(case.get("submitted_at") or "")
        scene = case.get("scene", "")
        scene_label = {"1_patient": "Scene 1", "3_doctor": "Scene 3"}.get(scene, scene)
        submitter = (case.get("submission") or {}).get("submitter", {}).get("user_id") or ""
        submitter_tag = f" @{submitter}" if submitter and submitter not in ("unknown", "") else ""

        exp_col, del_col = st.columns([0.96, 0.04])

        with del_col:
            if is_admin():
                if st.button("🗑", key=f"del_{case['case_id']}", help="删除案例"):
                    try:
                        api_client.delete_case(case["case_id"])
                        st.toast(f"案例 #{case['case_id'][:8]} 已删除")
                        st.rerun()
                    except Exception as e:
                        st.error(f"删除失败: {e}")

        with exp_col:
            with st.expander(
                f"**#{case['case_id']}** — {scene_label}{submitter_tag} — {submitted_dt}  `{label}`",
                expanded=(case["status"] in ("done", "processing", "pending", "queued", "awaiting_doctor_review")),
            ):
                meta_row = st.columns(2)
                meta_row[0].caption(f"场景: {scene_label}")
                completed_at_str = case.get("completed_at") or ""
                if completed_at_str and case["status"] in ("done", "escalated", "failed", "aborted"):
                    completed_dt = _to_cst(completed_at_str)
                    meta_row[1].caption(f"提交: {submitted_dt} · 产出: {completed_dt}")
                else:
                    meta_row[1].caption(f"提交: {submitted_dt}")

                if case["status"] == "queued":
                    st.info("🕐 当前系统并发已满，本案例正在排队等待处理。处理开始后自动刷新。")
                    _abort_key = f"abort_confirm_{case['case_id']}"
                    if not st.session_state.get(_abort_key):
                        if st.button("⛔ 取消排队", key=f"abort_{case['case_id']}", type="secondary"):
                            st.session_state[_abort_key] = True
                            st.rerun()
                    else:
                        st.warning("确认取消此排队？取消后可直接继续编辑并重新提交。")
                        _ac1, _ac2 = st.columns(2)
                        with _ac1:
                            if st.button("✅ 确认取消", key=f"abort_yes_{case['case_id']}", type="primary"):
                                try:
                                    api_client.abort_case(case["case_id"])
                                    st.session_state.pop(_abort_key, None)
                                    _sub = case.get("submission") or {}
                                    st.session_state["prefill_form"] = {
                                        "patient_age": _sub.get("patient_age"),
                                        "patient_gender": _sub.get("patient_gender"),
                                        "chief_complaint": _sub.get("chief_complaint", ""),
                                        "photo_count": len(_sub.get("attachment_ids") or []),
                                    }
                                    st.switch_page("pages/1_New_Case.py")
                                except Exception as _e:
                                    st.error(f"取消失败: {_e}")
                        with _ac2:
                            if st.button("返回", key=f"abort_no_{case['case_id']}"):
                                st.session_state.pop(_abort_key, None)
                                st.rerun()

                elif case["status"] in ("pending", "processing"):
                    stage_info = case.get("stage_info") or {}
                    if stage_info:
                        render_stage_progress(stage_info)
                    else:
                        # v1 fallback: phase-based progress bar
                        phase = case.get("phase") or "A"
                        phase_idx = _PHASE_KEYS.index(phase) if phase in _PHASE_KEYS else 0
                        progress_frac = (phase_idx + 1) / len(_PHASES)
                        st.progress(progress_frac, text=f"Phase {phase} — {_PHASES[phase_idx][1]}")
                    st.caption("AI 正在处理，每 30 秒自动刷新。")
                    _abort_key = f"abort_confirm_{case['case_id']}"
                    if not st.session_state.get(_abort_key):
                        if st.button("⛔ 终止当前诊断", key=f"abort_{case['case_id']}", type="secondary"):
                            st.session_state[_abort_key] = True
                            st.rerun()
                    else:
                        st.warning("确认终止此诊断？终止后将跳转至新建咨询页面，之前填写的内容已预填。")
                        _ac1, _ac2 = st.columns(2)
                        with _ac1:
                            if st.button("✅ 确认终止", key=f"abort_yes_{case['case_id']}", type="primary"):
                                try:
                                    api_client.abort_case(case["case_id"])
                                    st.session_state.pop(_abort_key, None)
                                    _sub = case.get("submission") or {}
                                    st.session_state["prefill_form"] = {
                                        "patient_age": _sub.get("patient_age"),
                                        "patient_gender": _sub.get("patient_gender"),
                                        "chief_complaint": _sub.get("chief_complaint", ""),
                                        "photo_count": len(_sub.get("attachment_ids") or []),
                                    }
                                    st.switch_page("pages/1_New_Case.py")
                                except Exception as _e:
                                    st.error(f"终止失败: {_e}")
                        with _ac2:
                            if st.button("取消", key=f"abort_no_{case['case_id']}"):
                                st.session_state.pop(_abort_key, None)
                                st.rerun()

                elif case["status"] == "aborted":
                    _sub = case.get("submission") or {}
                    if st.button("↩️ 重新提交（预填原始内容）", key=f"reuse_{case['case_id']}"):
                        st.session_state["prefill_form"] = {
                            "patient_age": _sub.get("patient_age"),
                            "patient_gender": _sub.get("patient_gender"),
                            "chief_complaint": _sub.get("chief_complaint", ""),
                            "photo_count": len(_sub.get("attachment_ids") or []),
                        }
                        st.switch_page("pages/1_New_Case.py")

                elif case["status"] in ("failed",):
                    raw_err = case.get("error_msg") or ""
                    friendly = _friendly_error(raw_err)
                    st.error(f"处理失败：{friendly}")
                    if st.button("🔄 重新提交", key=f"retry_{case['case_id']}"):
                        try:
                            api_client.retry_case(case["case_id"])
                            st.toast("已重新提交，AI 正在处理。", icon="✅")
                            st.rerun()
                        except Exception as _e:
                            st.error(f"重新提交失败: {_e}")

                elif case["status"] == "escalated":
                    fo = case.get("final_output") or {}
                    meta = case.get("metadata", {})
                    reason = meta.get("escalation_reason") or ""
                    if fo.get("rendered_markdown"):
                        case_display.render_result(case["case_id"], case)
                    elif "needs_more_info" in reason or "sufficiency" in reason.lower():
                        st.warning("⚠️ 信息不足，AI 无法生成答复。请创建新咨询并补充详情。")
                        if reason:
                            st.caption(f"原因: {reason}")
                    else:
                        st.error("处理失败，无法生成答复。请联系管理员。")
                        if reason:
                            st.caption(f"原因: {reason}")

                elif case["status"] == "awaiting_doctor_review":
                    case_display.render_result(case["case_id"], case)

                elif case["status"] == "done":
                    case_display.render_result(case["case_id"], case)


# ── Legacy Slock-CLI jobs section ──────────────────────────────────────────────

def _render_legacy_jobs(username: str, admin: bool):
    st.subheader("📂 历史记录（旧系统）")
    st.caption("以下为迁移前 Slock-CLI 流程的历史数据，只读展示。")

    jobs = list_jobs(submitted_by=None if admin else username)
    if not jobs:
        st.info("暂无旧系统记录。")
        return

    _TERM_CORRECTED = {"real0001", "60627825", "f6e4b10c"}

    for job in jobs:
        label, _ = _LEGACY_STATUS_LABELS.get(job["status"], ("未知", "gray"))
        submitted_dt = _to_cst(job["submitted_at"])

        with st.expander(
            f"**#{job['job_id']}** — {job['patient_age']}岁/{job['patient_gender']} "
            f"— {submitted_dt}  `{label}`",
            expanded=False,
        ):
            st.markdown(
                f"**主诉：** {job['chief_complaint'][:200]}"
                f"{'…' if len(job['chief_complaint']) > 200 else ''}"
            )

            meta_cols = []
            if job.get("confidence"):
                meta_cols.append(f"置信度: {job['confidence']}")
            if job.get("sufficiency"):
                meta_cols.append(f"信息充分度: {job['sufficiency']}")
            if job.get("emergency_level"):
                urgency_map = {
                    "urgent": "🔴 紧急", "high": "🟠 高", "medium": "🟡 中", "low": "🟢 低",
                }
                level = (job["emergency_level"] or "").lower()
                meta_cols.append(urgency_map.get(level, job["emergency_level"]))
            if meta_cols:
                st.caption(" · ".join(meta_cols))

            if job["status"] == "done":
                if job["job_id"] in _TERM_CORRECTED:
                    st.warning(
                        "⚠️ **术语已校正提示**：此案例产出时使用了「正凹假凸」（现已更正为「**真凹假凸**」）。"
                        "助理转发患者前请手动核对并替换相关术语。"
                    )
                tab1, tab2 = st.tabs(["患者答复", "内部 Playbook"])
                with tab1:
                    layer1 = job.get("layer1_output") or ""
                    st.markdown(layer1)
                with tab2:
                    st.warning("⚠️ 内部使用 — 严禁转发患者")
                    st.markdown(job.get("layer2_output") or "（暂无内容）")

            elif job["status"] == "sufficiency":
                st.warning("⚠️ 此案例当时需要补充信息（旧系统，无法重新提交）。请创建新咨询。")

            elif job["status"] in ("failed", "timeout"):
                st.error(job.get("error_msg") or "处理失败。")


# ── Entry point ────────────────────────────────────────────────────────────────

role = get_current_role()
if role not in _ALLOWED_ROLES:
    st.error("此页面仅限助理团队、医生、管理员和审计账号访问。")
    st.stop()

with st.sidebar:
    render_active_sidebar(st.session_state.get("_sidebar_cases", []))

st.title("📋 历史记录")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

username = st.session_state.get("username", "")
admin = is_admin()

_render_api_cases(role, username)

st.divider()
with st.expander("旧系统记录（Slock-CLI，只读）", expanded=False):
    _render_legacy_jobs(username, admin)
