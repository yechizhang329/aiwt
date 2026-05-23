import streamlit as st
from auth import load_authenticator, require_login, is_admin
from db import init_db
import poller

st.set_page_config(
    page_title="Scene 1 — 在线咨询助理工具",
    page_icon="🦷",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_db()
poller.start()

authenticator = load_authenticator()
if not require_login(authenticator):
    st.stop()

# Define pages via st.navigation() — replaces Streamlit's auto-generated nav
# so only one nav set appears in the sidebar.
pages = st.navigation(
    [
        st.Page("pages/1_New_Case.py", title="新建咨询", icon="➕"),
        st.Page("pages/2_Case_History.py", title="历史记录", icon="📋"),
    ],
    position="hidden",   # we render our own sidebar nav below
)

with st.sidebar:
    name = st.session_state.get("name", "")
    username = st.session_state.get("username", "")
    role_label = "管理员" if is_admin() else "助理"
    st.markdown(f"**{name}**")
    st.caption(f"@{username} · {role_label}")
    authenticator.logout("退出登录", location="sidebar")
    st.divider()
    st.page_link("pages/1_New_Case.py", label="新建咨询", icon="➕")
    st.page_link("pages/2_Case_History.py", label="历史记录", icon="📋")

pages.run()
