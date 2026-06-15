"""Scene 1 — Patient-facing consultation form (FastAPI backend).

Access: role=patient_team, admin, or doctor.
Flow:
  1. Fill form (age, sex, chief complaint, photos).
  2. On submit: upload files via POST /v1/attachment → POST /v1/case (scene=1_patient).
  3. Case tracked in sidebar; result available in 历史记录 when done.
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import api_client
from auth import get_current_role
from components.stage_progress import render_active_sidebar

_ALLOWED_ROLES = ("patient_team", "admin")


# ── Form UI ─────────────────────────────────────────────────────────────────────

def _form_ui():
    st.title("➕ 患者初诊")
    st.caption("填写越详细，AI 答复越准确（打 * 为必填）")

    _photo_notice = st.session_state.pop("_prefill_photo_notice", None)
    if _photo_notice:
        st.info(f"↩️ 已预填原始内容。原案例含 {_photo_notice} 张图片，请重新上传。")

    col1, col2 = st.columns(2)
    with col1:
        age = st.number_input(
            "年龄 *",
            min_value=3,
            max_value=120,
            value=None,
            placeholder="请输入患者年龄",
            help="整数，单位：岁",
            key="s1_age",
        )
    with col2:
        gender = st.selectbox(
            "性别 *",
            options=["", "男", "女"],
            index=0,
            key="s1_gender",
        )

    chief_complaint = st.text_area(
        "患者诉求（可直接粘贴聊天记录）*",
        height=160,
        placeholder=(
            "可直接粘贴您与患者的聊天记录，或描述患者情况，例如：\n"
            "• \"我想矫正牙齿，牙齿有点凌乱，25岁女生\"\n"
            "• \"孩子13岁，下巴有点后缩，想了解是否需要矫正\"\n"
            "• \"已经在别处做过正畸，想换一家诊所\"\n"
            "可以用口语，不需要专业术语。"
        ),
        key="s1_complaint",
    )

    photos = st.file_uploader(
        "照片 / X 光（每张 ≤10MB，最多 20 张）",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tiff", "pdf"],
        accept_multiple_files=True,
        help="建议上传至少 1 张照片或 X 光，帮助 AI 更准确评估形态。可上传：真人侧面照（推荐，用于突/凹面方向判断）/ 真人正面照 / 口内照 / 全景片 / 侧位片（ceph）。",
        key="s1_photos",
    )

    st.divider()
    if st.button("提交咨询", type="primary", use_container_width=True):
        _handle_submit(
            age=age,
            gender=gender,
            chief_complaint=chief_complaint,
            photos=photos or [],
        )


def _handle_submit(age, gender, chief_complaint, photos):
    errors = []
    if not age:
        errors.append("年龄")
    if not gender:
        errors.append("性别")
    if not chief_complaint.strip():
        errors.append("患者诉求")
    if photos and len(photos) > 20:
        errors.append("照片数量超限（最多 20 张）")

    if errors:
        st.error(f"以下必填项未填写：**{'、'.join(errors)}**")
        return

    if not photos:
        st.info("建议上传至少 1 张照片或 X 光，帮助 AI 更准确评估。如无照片可先提交文字主诉。")

    oversized = [f.name for f in photos if f.size > 10 * 1024 * 1024]
    if oversized:
        st.error(f"以下文件超过 10MB 限制：{', '.join(oversized)}")
        return

    username = st.session_state.get("username", "unknown")

    attachment_ids = []
    if photos:
        total = len(photos)
        progress = st.progress(0, text=f"0/{total} 已上传，准备中…")
        try:
            for i, f in enumerate(photos):
                progress.progress(i / total, text=f"{i}/{total} 已上传，正在上传 {f.name}…")
                aid = api_client.upload_attachment(
                    f.read(),
                    f.name,
                    f.type or "application/octet-stream",
                )
                attachment_ids.append(aid)
            progress.progress(1.0, text=f"{total}/{total} 上传完成 ✓")
        except Exception as e:
            st.error(f"文件上传失败: {e}")
            return

    payload = {
        "scene": "1_patient",
        "patient_age": int(age),
        "patient_gender": gender,
        "chief_complaint": chief_complaint.strip(),
        "attachment_ids": attachment_ids,
        "submitter": {"user_id": username, "role": "patient_team"},
    }

    try:
        result = api_client.submit_case(payload)
    except api_client.SufficiencyError as e:
        st.warning(str(e))
        return
    except Exception as e:
        st.error(f"案例提交失败: {e}")
        return

    st.toast(f"✓ 已提交！案例编号：{result['case_id']}", icon="✅")
    st.session_state["flash"] = f"✓ 已提交！案例编号：{result['case_id']}。AI 正在分析，完成后可在此页查看结果。"
    st.switch_page("pages/2_Case_History.py")


# ── Entry point ────────────────────────────────────────────────────────────────

role = get_current_role()
if role not in _ALLOWED_ROLES:
    st.error("此页面仅限助理团队和管理员访问。")
    st.stop()

# Pre-populate form from aborted/cancelled case (Item 3)
if "prefill_form" in st.session_state:
    _pf = st.session_state.pop("prefill_form")
    if _pf.get("patient_age") is not None:
        st.session_state["s1_age"] = _pf["patient_age"]
    if _pf.get("patient_gender"):
        st.session_state["s1_gender"] = _pf["patient_gender"]
    if _pf.get("chief_complaint"):
        st.session_state["s1_complaint"] = _pf["chief_complaint"]
    _photo_n = _pf.get("photo_count", 0)
    if _photo_n > 0:
        st.session_state["_prefill_photo_notice"] = _photo_n

# Sidebar: show active case progress (Item 6)
with st.sidebar:
    _sidebar_cases = st.session_state.get("_sidebar_cases")
    if _sidebar_cases is None:
        try:
            _sidebar_cases = api_client.list_cases(limit=20)
            st.session_state["_sidebar_cases"] = _sidebar_cases
        except Exception:
            _sidebar_cases = []
    render_active_sidebar(_sidebar_cases)

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))

_form_ui()
