"""Scene 3 — Doctor-to-doctor consultation form (chat-style, Change 34).

Access: role=doctor or role=admin only.
Flow:
  1. Chat-style: doctor_question free textarea (≥50 chars) as primary input.
  2. Required: patient_age + sex + imaging ≥1 + type per image.
  3. Optional expander: ceph / TMD / prior_treatment / patient_prefs / notes.
  4. On submit: upload files → POST /v1/case (scene=3_doctor).
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import api_client
from auth import get_current_role

_IMAGING_TYPES = [
    "ceph_lateral", "panx", "cbct_axial", "cbct_sagittal",
    "mri_t1", "mri_t2", "facial_lateral", "facial_frontal", "clinical_photo", "other",
]


def _form_ui():
    st.title("🦷 医生诊断咨询")
    st.caption("直接描述您的问题，越详细越好。* 为必填项。")

    # ── Primary: doctor question ──────────────────────────────────────────────
    doctor_question = st.text_area(
        "您的提问 *",
        height=160,
        placeholder=(
            "请描述您的问题，例如：\n"
            "「患者女，28岁，凹面型，上颌后缩，下颌前突。全景+侧位已拍。"
            "曾在九院被建议正颌手术，患者拒绝。想请教：非手术方案是否可行？"
            "关节目前稳定，无疼痛史。」\n\n"
            "可以是一句话，也可以是详细描述，取决于您提供的影像资料完整度。"
        ),
        key="s3_question",
    )

    # ── Required: demographics ────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        age = st.number_input(
            "患者年龄（选填）", min_value=1, max_value=100, value=None,
            placeholder="岁", key="s3_age",
        )
    with col2:
        sex = st.selectbox("性别（选填）", ["", "女", "男"], key="s3_sex")

    # ── Required: imaging ─────────────────────────────────────────────────────
    st.markdown("**影像资料 / 真人照片**（必填，至少 1 张，每张 ≤10MB；突/凹面诊断建议上传侧面照 facial_lateral 作为方向参考）")
    uploaded_files = st.file_uploader(
        "上传影像",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tiff", "pdf"],
        accept_multiple_files=True,
        key="s3_files",
    )

    img_types: list[str] = []
    img_views: list[str] = []
    if uploaded_files:
        st.caption("为每张影像指定类型：")
        for i, f in enumerate(uploaded_files):
            ic1, ic2 = st.columns([2, 3])
            with ic1:
                t = st.selectbox(
                    f"类型 ({f.name})", _IMAGING_TYPES, key=f"s3_img_type_{i}"
                )
                img_types.append(t)
            with ic2:
                v = st.text_input(
                    f"描述 ({f.name})",
                    placeholder="可选，如: TMJ 右",
                    key=f"s3_img_view_{i}",
                )
                img_views.append(v)

    # ── Submit ────────────────────────────────────────────────────────────────
    st.divider()
    if st.button("提交案例", type="primary", use_container_width=True):
        _handle_submit(
            doctor_question=doctor_question,
            age=age,
            sex=sex,
            uploaded_files=uploaded_files or [],
            img_types=img_types,
            img_views=img_views,
        )


def _handle_submit(doctor_question, age, sex, uploaded_files, img_types, img_views):
    errors = []
    q = doctor_question.strip()
    if not q:
        errors.append("您的提问")
    if not uploaded_files:
        errors.append("至少 1 张影像资料")

    if errors:
        st.error("以下项目需要补充：**" + "、".join(errors) + "**")
        return

    oversized = [f.name for f in uploaded_files if f.size > 10 * 1024 * 1024]
    if oversized:
        st.error("以下文件超过 10MB 限制：" + ", ".join(oversized))
        return

    username = st.session_state.get("username", "unknown")

    with st.spinner("上传影像并提交案例…"):
        imaging_provided = []
        try:
            for i, f in enumerate(uploaded_files):
                aid = api_client.upload_attachment(
                    f.read(),
                    f.name,
                    f.type or "application/octet-stream",
                )
                imaging_provided.append({
                    "attachment_id": aid,
                    "type": img_types[i] if i < len(img_types) else "other",
                    "view": img_views[i] if i < len(img_views) else "",
                })
        except Exception as e:
            st.error(f"影像上传失败: {e}")
            return

        payload = {
            "scene": "3_doctor",
            "submitter_role": "doctor",
            "patient_sex": sex if sex else None,
            "doctor_specific_question": q,
            "imaging_provided": imaging_provided,
            "submitter": {
                "doctor_id": username,
                "role": "doctor",
            },
        }
        if age:
            payload["patient_age"] = int(age)
            payload["patient_age_confidence"] = "high"
            payload["patient_demographics"] = {
                "age": int(age),
                "age_evidence": [],
                "age_confidence": "high",
                "sex": sex if sex else None,
                "is_minor": int(age) < 18,
            }

        try:
            result = api_client.submit_case(payload)
        except Exception as e:
            st.error(f"案例提交失败: {e}")
            return

        st.session_state["flash"] = f"✓ 已提交！案例编号：{result['case_id']}。AI 正在分析，完成后可在此页查看结果。"
        st.switch_page("pages/2_Case_History.py")


# ── Entry point ────────────────────────────────────────────────────────────────

role = get_current_role()
if role not in ("doctor", "admin"):
    st.error("此页面仅限医生和管理员访问。")
    st.stop()

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

_form_ui()
