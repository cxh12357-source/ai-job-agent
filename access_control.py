"""Minimal local access gate for LAN-hosted Streamlit sessions."""

from __future__ import annotations

import hmac

import streamlit as st


SESSION_KEY = "app_access_granted"
MIN_PASSWORD_LENGTH = 12


def password_matches(expected: str, candidate: str) -> bool:
    """Compare access passwords without leaking prefix timing information."""

    expected_value = str(expected or "")
    candidate_value = str(candidate or "")
    return bool(expected_value) and hmac.compare_digest(expected_value, candidate_value)


def require_access(expected_password: str, *, required: bool = False) -> None:
    """Stop rendering private app data until this browser session is unlocked."""

    expected = str(expected_password or "").strip()
    if not expected:
        if required:
            st.error(
                "云端部署必须配置 APP_ACCESS_PASSWORD。请在 Streamlit "
                "Community Cloud 的 Secrets 中设置至少 12 位的随机密码。"
            )
            st.stop()
        return
    if len(expected) < MIN_PASSWORD_LENGTH:
        st.error(
            f"APP_ACCESS_PASSWORD 至少需要 {MIN_PASSWORD_LENGTH} 个字符；"
            "为保护简历和投递记录，当前页面没有继续加载。"
        )
        st.stop()
    if st.session_state.get(SESSION_KEY) is True:
        return

    st.title("AI Job Agent")
    st.caption("此页面包含私人简历和投递记录")
    with st.form("app-access-form", clear_on_submit=True):
        candidate = st.text_input(
            "访问密码",
            type="password",
            autocomplete="current-password",
            placeholder="输入本机设置的访问密码",
        )
        submitted = st.form_submit_button("进入应用", type="primary")
    if submitted:
        if password_matches(expected, candidate):
            st.session_state[SESSION_KEY] = True
            st.rerun()
        st.error("访问密码不正确")
    st.info("只在你信任的设备和家庭网络中输入此密码。")
    st.stop()


def lock_current_session() -> None:
    st.session_state.pop(SESSION_KEY, None)


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "SESSION_KEY",
    "lock_current_session",
    "password_matches",
    "require_access",
]
