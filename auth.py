import yaml
import streamlit as st
import streamlit_authenticator as stauth
import config


def load_authenticator() -> stauth.Authenticate:
    with open(config.USERS_YAML) as f:
        cfg = yaml.safe_load(f)
    return stauth.Authenticate(
        cfg["credentials"],
        config.COOKIE_NAME,
        config.COOKIE_KEY,
        config.COOKIE_EXPIRY_DAYS,
    )


def get_current_role() -> str:
    username = st.session_state.get("username", "")
    if not username:
        return ""
    with open(config.USERS_YAML) as f:
        cfg = yaml.safe_load(f)
    return cfg["credentials"]["usernames"].get(username, {}).get("role", "user")


def is_admin() -> bool:
    return get_current_role() == "admin"


def require_login(authenticator: stauth.Authenticate) -> bool:
    # streamlit-authenticator >=0.3 returns None; reads from session_state instead
    authenticator.login(
        location="main",
        fields={
            "Form name": "Scene 1 — 在线咨询助理工具",
            "Username": "用户名",
            "Password": "密码",
            "Login": "登录",
        },
    )
    auth_status = st.session_state.get("authentication_status")
    if auth_status is False:
        st.error("用户名或密码错误")
        return False
    if auth_status is None:
        st.info("请输入用户名和密码")
        return False
    return True
