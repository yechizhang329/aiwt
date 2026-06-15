import streamlit as st
from auth import load_authenticator, require_login, is_admin, get_current_role
from db import init_db
import poller
import api_client
from components.stage_progress import render_stage_progress

st.set_page_config(
    page_title="WangSpecial 咨询工具",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
poller.start()

authenticator = load_authenticator()
if not require_login(authenticator):
    st.stop()

role = get_current_role()

# Build page list: Scene 3 visible to doctor + admin
_all_pages = [
    st.Page("pages/1_New_Case.py", title="患者初诊", icon="➕"),
    st.Page("pages/2_Case_History.py", title="历史记录", icon="📋"),
]
if role in ("doctor", "admin"):
    _all_pages.append(
        st.Page("pages/3_Scene3_Doctor.py", title="医生诊断咨询", icon="🩺")
    )

pages = st.navigation(_all_pages, position="hidden")

_SCENE_LABELS = {"1_patient": "初诊", "3_doctor": "医生"}


@st.fragment(run_every=30)
def _inflight_sidebar():
    if not st.session_state.get("authentication_status"):
        return
    admin_user = is_admin()
    username = st.session_state.get("username", "")
    try:
        all_cases = api_client.list_cases(limit=100)
    except Exception:
        return
    inflight = [
        c for c in all_cases
        if c["status"] in ("pending", "processing")
        and (admin_user or c.get("submitted_by") == username)
    ]
    if not inflight:
        return
    st.divider()
    st.caption("⟳ 进行中案例")
    for c in inflight[:5]:
        scene_label = _SCENE_LABELS.get(c.get("scene", ""), "")
        cid_short = c["case_id"][:8]
        st.caption(f"**{cid_short}** {scene_label}")
        stage_info = c.get("stage_info") or {}
        if stage_info:
            render_stage_progress(stage_info, simple=True)
        else:
            st.progress(0.1, text="等待开始…")
    if len(inflight) > 5:
        st.caption(f"…另有 {len(inflight) - 5} 个进行中")
    if st.button("查看历史记录 →", key="_sb_hist", use_container_width=True):
        st.switch_page("pages/2_Case_History.py")


_ROLE_LABELS = {
    "admin": "管理员",
    "doctor": "医生",
    "audit_agent": "审计",
    "patient_team": "助理",
}

with st.sidebar:
    name = st.session_state.get("name", "")
    username = st.session_state.get("username", "")
    role_label = _ROLE_LABELS.get(role, "助理")
    st.markdown(f"**{name}**")
    st.caption(f"@{username} · {role_label}")
    authenticator.logout("退出登录", location="sidebar")
    st.divider()
    st.page_link("pages/1_New_Case.py", label="患者初诊", icon="➕")
    if role in ("doctor", "admin"):
        st.page_link("pages/3_Scene3_Doctor.py", label="医生诊断咨询", icon="🩺")
    st.divider()
    st.page_link("pages/2_Case_History.py", label="历史记录", icon="📋")
    _inflight_sidebar()

pages.run()
