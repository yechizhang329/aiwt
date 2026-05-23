import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import create_job, set_dm_msg_id, update_job_failed
import slock_client

st.title("➕ 新建咨询")
st.caption("以下 4 项为必填，缺一项 AI 无法生成答复")

with st.form("new_case_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        age = st.number_input(
            "年龄 *",
            min_value=3,
            max_value=120,
            value=None,
            placeholder="请输入患者年龄",
            help="整数，单位：岁",
        )
    with col2:
        gender = st.selectbox(
            "性别 *",
            options=["", "男", "女"],
            index=0,
        )

    chief_complaint = st.text_area(
        "文字主诉 *",
        height=160,
        placeholder=(
            "请填写患者主诉，可包含：\n"
            "• 主要诉求（如牙齿不齐、龅牙、反颌等）\n"
            "• 已有治疗方案 / 病史 / 过敏史\n"
            "• 拔牙史 / 外科接受度\n"
            "• 监护人参与情况（未成年）"
        ),
    )

    photos = st.file_uploader(
        "照片 / X 光 *（至少 1 张，每张 ≤10MB，共 ≤20 张）",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tiff", "pdf"],
        accept_multiple_files=True,
        help="可上传：面型照 / 口内照 / 全景片 / 侧位片（ceph）。图片会自动压缩到 2048px。",
    )

    extra_notes = st.text_area(
        "额外说明（可选）",
        height=80,
        placeholder="额外诊断报告说明、特殊情况备注等",
    )

    submitted = st.form_submit_button("提交咨询", type="primary", use_container_width=True)

if submitted:
    errors = []
    if not age:
        errors.append("年龄")
    if not gender:
        errors.append("性别")
    if not chief_complaint.strip():
        errors.append("文字主诉")
    if not photos:
        errors.append("至少 1 张照片或 X 光")
    if photos and len(photos) > slock_client.MAX_ATTACHMENTS:
        errors.append(f"照片数量超限（最多 {slock_client.MAX_ATTACHMENTS} 张）")

    if errors:
        st.error(f"以下必填项未填写：**{'、'.join(errors)}**")
        st.stop()

    # Check individual file sizes before saving
    oversized = [f.name for f in photos
                 if f.size > slock_client.MAX_FILE_SIZE_BYTES]
    if oversized:
        st.error(f"以下文件超过 10MB 限制：{', '.join(oversized)}")
        st.stop()

    with st.spinner("正在上传资料并提交…"):
        upload_dir = Path(__file__).parent.parent / "uploads"
        upload_dir.mkdir(exist_ok=True)
        attachment_paths = []
        for f in photos:
            dest = upload_dir / f.name
            dest.write_bytes(f.read())
            attachment_paths.append(str(dest))

        full_complaint = chief_complaint.strip()
        if extra_notes.strip():
            full_complaint += f"\n\n[补充说明] {extra_notes.strip()}"

        job_id = create_job(
            submitted_by=st.session_state.get("username", "unknown"),
            age=int(age),
            gender=gender,
            chief_complaint=full_complaint,
            attachment_paths=attachment_paths,
        )

        try:
            dm_short_id = slock_client.submit_cowork(
                job_id=job_id,
                age=int(age),
                gender=gender,
                chief_complaint=full_complaint,
                attachment_paths=attachment_paths,
            )
            set_dm_msg_id(job_id, dm_short_id)
            st.success(f"咨询已提交！任务编号：**#{job_id}**")
            st.info("AI 正在处理（预计 7-13 分钟），请前往 **历史记录** 查看进度。")
        except Exception as e:
            update_job_failed(job_id, str(e))
            st.error(f"提交失败：{e}\n\n任务 #{job_id} 已记录，请联系管理员。")

    st.page_link("pages/2_Case_History.py", label="查看历史记录 →", icon="📋")
