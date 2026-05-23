import streamlit as st
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import is_admin
from db import list_jobs, requeue_job
import slock_client

_STATUS_META = {
    "pending":     ("⏸ 等待提交", "gray"),
    "processing":  ("⏳ AI 处理中", "blue"),
    "sufficiency": ("⚠️ 需补充信息", "orange"),
    "done":        ("✅ 已完成", "green"),
    "failed":      ("❌ 提交失败", "red"),
    "timeout":     ("⏱ 超时未响应", "red"),
}

st.title("📋 历史记录")

username = st.session_state.get("username", "")
admin = is_admin()
jobs = list_jobs(submitted_by=None if admin else username)

if not jobs:
    st.info("暂无咨询记录。点击左侧 **新建咨询** 提交第一个 case。")
    st.stop()

# Auto-refresh if any job is active
active = any(j["status"] in ("pending", "processing", "sufficiency") for j in jobs)
if active:
    st.caption("⟳ 每 20 秒自动刷新")
    st.markdown('<meta http-equiv="refresh" content="20">', unsafe_allow_html=True)

# Summary metrics
col_total, col_active, col_done, col_need = st.columns(4)
col_total.metric("总计", len(jobs))
col_active.metric("处理中", sum(1 for j in jobs if j["status"] == "processing"))
col_need.metric("待补充", sum(1 for j in jobs if j["status"] == "sufficiency"))
col_done.metric("已完成", sum(1 for j in jobs if j["status"] == "done"))

st.divider()

for job in jobs:
    label, _ = _STATUS_META.get(job["status"], ("未知", "gray"))
    submitted_dt = job["submitted_at"][:16].replace("T", " ")

    with st.expander(
        f"**#{job['job_id']}** — {job['patient_age']}岁/{job['patient_gender']} "
        f"— {submitted_dt}  `{label}`",
        expanded=(job["status"] in ("done", "sufficiency")),
    ):
        m1, m2, m3 = st.columns(3)
        m1.caption(f"提交人：{job['submitted_by']}")
        m2.caption(f"提交时间：{submitted_dt}")
        if job.get("completed_at"):
            m3.caption(f"完成时间：{job['completed_at'][:16].replace('T', ' ')}")

        st.markdown(
            f"**主诉：** {job['chief_complaint'][:200]}"
            f"{'…' if len(job['chief_complaint']) > 200 else ''}"
        )

        attachments = json.loads(job.get("attachment_paths") or "[]")
        if attachments:
            st.caption(f"附件：{len(attachments)} 张")
            # Show thumbnails for image attachments (P0 C8)
            img_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
            img_paths = [p for p in attachments
                         if Path(p).suffix.lower() in img_exts and Path(p).exists()]
            if img_paths:
                thumb_cols = st.columns(min(len(img_paths), 5))
                for col, img_path in zip(thumb_cols, img_paths):
                    col.image(img_path, caption=Path(img_path).name, use_container_width=True)

        # Metadata badges
        meta_cols = []
        if job.get("confidence"):
            meta_cols.append(f"置信度: {job['confidence']}")
        if job.get("sufficiency"):
            meta_cols.append(f"信息充分度: {job['sufficiency']}")
        if job.get("emergency_level"):
            urgency_map = {
                "urgent": "🔴 紧急（24h）",
                "high": "🟠 高（1-2周）",
                "medium": "🟡 中（1-3月）",
                "low": "🟢 低（弹性）",
            }
            level = job["emergency_level"].lower()
            meta_cols.append(urgency_map.get(level, job["emergency_level"]))
        if meta_cols:
            st.caption(" · ".join(meta_cols))

        # --- Status-specific UI ---

        if job["status"] in ("failed", "timeout"):
            st.error(job.get("error_msg") or "处理失败，请联系管理员。")

        elif job["status"] in ("pending", "processing"):
            st.info("AI 正在处理中，请稍候…（预计 7-13 分钟）")

        elif job["status"] == "sufficiency":
            st.warning("**AI 需要更多信息才能生成答复。** 请补充以下信息后重新提交。")

            suf_data = {}
            if job.get("sufficiency_msg"):
                try:
                    suf_data = json.loads(job["sufficiency_msg"])
                except Exception:
                    pass

            missing = suf_data.get("missing_fields", [])
            priority_order = suf_data.get("priority_order", [])

            def _priority_key(f):
                name = f.get("field", "")
                try:
                    return priority_order.index(name)
                except ValueError:
                    return 999

            missing_sorted = sorted(missing, key=_priority_key)

            if missing_sorted:
                with st.container(border=True):
                    st.markdown("**AI 需要补充的信息：**")
                    for i, item in enumerate(missing_sorted):
                        field = item.get("field", f"字段{i+1}")
                        why = item.get("why", "")
                        phrasing = item.get("suggested_phrasing", "")
                        st.markdown(f"**{i+1}. {field}**")
                        if why:
                            st.caption(f"原因：{why}")
                        if phrasing:
                            st.caption(f"建议问法：_{phrasing}_")
                        st.divider() if i < len(missing_sorted) - 1 else None

            supplement_parts = {}
            with st.form(f"supplement_{job['job_id']}"):
                if missing_sorted:
                    for item in missing_sorted:
                        field = item.get("field", "")
                        phrasing = item.get("suggested_phrasing", "")
                        supplement_parts[field] = st.text_area(
                            f"{field}",
                            height=80,
                            placeholder=phrasing or f"请补充：{field}",
                            key=f"sup_{job['job_id']}_{field}",
                        )
                else:
                    supplement_parts["general"] = st.text_area(
                        "补充信息",
                        height=120,
                        placeholder="请根据 AI 提示补充相关信息",
                    )
                submit_sup = st.form_submit_button(
                    "提交补充信息", type="primary", use_container_width=True
                )

            supplement_text = "\n".join(
                f"{k}: {v}" for k, v in supplement_parts.items() if v.strip()
            )

            if submit_sup:
                if not supplement_text.strip():
                    st.error("请至少填写一项补充信息")
                else:
                    dm_short_id = job.get("dm_msg_id")
                    if not dm_short_id:
                        st.error("无法找到对话线程，请联系管理员")
                    else:
                        try:
                            slock_client.submit_supplement(
                                job_id=job["job_id"],
                                dm_short_id=dm_short_id,
                                supplement_text=supplement_text.strip(),
                            )
                            requeue_job(job["job_id"])
                            st.success("补充信息已提交，AI 将继续处理。")
                            st.rerun()
                        except Exception as e:
                            st.error(f"提交失败：{e}")

        elif job["status"] == "done":
            st.divider()
            tab1, tab2 = st.tabs(["Layer 1 — 患者答复", "Layer 2 — 内部 Playbook"])

            with tab1:
                layer1 = job.get("layer1_output") or ""
                st.markdown(layer1)
                if st.button("📋 复制答复", key=f"copy_{job['job_id']}"):
                    import json as _json
                    safe_json = _json.dumps(layer1)
                    st.components.v1.html(
                        f"<script>navigator.clipboard.writeText({safe_json})"
                        f".then(()=>{{}})</script>",
                        height=0,
                    )
                    st.toast("已复制到剪贴板！")

            with tab2:
                st.warning("⚠️ 内部使用 — 严禁转发患者")
                st.markdown(job.get("layer2_output") or "（暂无内容）")
