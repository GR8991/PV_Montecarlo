"""Login page backed by Streamlit secrets.

Users live in `.streamlit/secrets.toml` locally, or in the app's **Settings → Secrets**
on Streamlit Community Cloud. Nothing about users is stored in the repository.

    [auth]
    enabled = true                  # optional, default true
    session_timeout_minutes = 480   # optional, default 480 (8 h)
    max_attempts = 5                # optional, failed logins before a pause
    lockout_seconds = 60            # optional

    [passwords]
    # username = "plain password"           (simplest)
    # username = "pbkdf2_sha256$..."        (hash from: python tools/hash_password.py)
    gangaraju = "change-me"

    [names]                          # optional display names
    gangaraju = "Gangaraju P"

If no users are configured the app stays locked and shows setup steps (it fails closed).
"""
from __future__ import annotations

import time

import streamlit as st

from .ui import full

from .passwords import hash_password, verify_password  # noqa: E402,F401


# --------------------------------------------------------------------------
# secrets
# --------------------------------------------------------------------------
def _secrets_section(name: str) -> dict:
    try:
        sec = st.secrets.get(name, {})
        return dict(sec) if sec else {}
    except Exception:  # no secrets file at all
        return {}


def _settings() -> dict:
    a = _secrets_section("auth")
    return {
        "enabled": bool(a.get("enabled", True)),
        "timeout": float(a.get("session_timeout_minutes", 480)) * 60,
        "max_attempts": int(a.get("max_attempts", 5)),
        "lockout": int(a.get("lockout_seconds", 60)),
    }


def _users() -> dict:
    return {str(k).strip().lower(): str(v) for k, v in _secrets_section("passwords").items()}


def _display_name(user: str) -> str:
    return str(_secrets_section("names").get(user, user))


# --------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------
def logout():
    for k in ("auth_user", "auth_time"):
        st.session_state.pop(k, None)


def _setup_help():
    st.title("PV Monte Carlo Scaler")
    st.error("Sign-in is not set up yet, so the app is locked.")
    st.markdown(
        "Add at least one user to the app's secrets:\n\n"
        "- **Streamlit Community Cloud:** open the app's **Settings → Secrets** and paste the block below.\n"
        "- **Running locally:** create `.streamlit/secrets.toml` next to `app.py` with the same block "
        "(see `.streamlit/secrets.toml.example`).\n"
    )
    st.code('[passwords]\nyour_username = "your-password"\n\n[names]\nyour_username = "Your Name"', language="toml")
    st.caption("To store a hash instead of the plain password, run `python tools/hash_password.py` and paste its output as the value.")


def require_login() -> str:
    """Show the sign-in page until a valid user signs in. Returns the username."""
    cfg = _settings()
    if not cfg["enabled"]:
        return "local"
    users = _users()
    if not users:
        _setup_help()
        st.stop()

    user = st.session_state.get("auth_user")
    if user and user in users and time.time() - st.session_state.get("auth_time", 0) < cfg["timeout"]:
        st.session_state["auth_time"] = time.time()  # sliding timeout
        with st.sidebar:
            st.markdown(f"Signed in as **{_display_name(user)}**")
            if st.button("Sign out", **full(st.button)):
                logout()
                st.rerun()
        return user
    if user:
        logout()
        st.session_state["auth_msg"] = "Your session expired. Please sign in again."

    # ---------------- sign-in form ----------------
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)
        st.title("PV Monte Carlo Scaler")
        st.caption("Sign in to continue.")
        if st.session_state.get("auth_msg"):
            st.info(st.session_state.pop("auth_msg"))
        locked_until = st.session_state.get("auth_locked_until", 0)
        wait = int(locked_until - time.time())
        with st.form("login", clear_on_submit=False):
            username = st.text_input("Username", key="login_user", autocomplete="username")
            password = st.text_input("Password", type="password", key="login_pw", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", **full(st.form_submit_button))
        if wait > 0:
            st.warning(f"Too many failed attempts. Try again in {wait} s.")
        elif submitted:
            u = (username or "").strip().lower()
            ok = u in users and verify_password(password or "", users[u])
            if not ok and u not in users:
                verify_password(password or "", hash_password("x", 1000))  # similar timing for unknown users
            if ok:
                st.session_state.update(auth_user=u, auth_time=time.time(), auth_fails=0)
                st.session_state.pop("login_pw", None)
                st.rerun()
            else:
                fails = st.session_state.get("auth_fails", 0) + 1
                st.session_state["auth_fails"] = fails
                if fails >= cfg["max_attempts"]:
                    st.session_state["auth_locked_until"] = time.time() + cfg["lockout"]
                    st.session_state["auth_fails"] = 0
                    st.warning(f"Too many failed attempts. Try again in {cfg['lockout']} s.")
                else:
                    st.error("Username or password is incorrect.")
        st.caption("Access is managed by the app owner. Ask them to add you if you need an account.")
    st.stop()
