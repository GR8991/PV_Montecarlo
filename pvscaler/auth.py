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
# look and feel of the sign-in page
# --------------------------------------------------------------------------
_GRAIN = ("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'>"
          "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter>"
          "<rect width='100%25' height='100%25' filter='url(%23n)' opacity='.55'/></svg>")

# Look: black canvas, diagonal red-orange light, wide red wordmark, square outlined buttons
_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Krona+One&family=Inter+Tight:wght@300;400;500;600;700;800&display=swap');
:root {--ss-red:#E5341F; --ss-ember:#C2340E; --ss-ink:#000; --ss-panel:#0B0B0B; --ss-line:#2A2A2A; --ss-text:#F4F4F4; --ss-mute:#A9A9A9; --ss-soft:#C9C9C9;}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer {display:none !important;}
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display:none !important;}
.stApp {background-color:#000 !important;
  background-image: url("GRAIN"),
    radial-gradient(ellipse 70% 60% at 0% 100%, rgba(0,0,0,.92) 0%, rgba(0,0,0,0) 70%),
    linear-gradient(122deg, rgba(110,24,6,.9) 0%, rgba(190,50,14,.95) 24%, rgba(214,66,24,.9) 36%, rgba(120,30,8,.55) 47%, rgba(0,0,0,0) 58%),
    radial-gradient(ellipse 45% 35% at 100% 100%, rgba(90,20,6,.35) 0%, rgba(0,0,0,0) 70%) !important;
  background-blend-mode: soft-light, normal, normal, normal; color:var(--ss-text);}
.stApp, .stApp p, .stApp label, .stApp input, .stApp button {font-family:'Inter Tight', 'Helvetica Neue', Arial, sans-serif !important;}
.block-container {max-width:1180px; padding-top:0 !important; padding-bottom:3rem;}
.ss-bar {display:flex; align-items:center; justify-content:space-between; gap:24px; padding:30px 0; margin:0 0 7vh; position:relative; z-index:2;
  background:#000; box-shadow:0 0 0 100vmax #000; clip-path:inset(0 -100vmax); border-bottom:1px solid #000;}
.ss-word {font-family:'Krona One', 'Arial Black', sans-serif; color:var(--ss-red); font-size:18px; font-weight:700; letter-spacing:.06em; line-height:1;}
.ss-tag {color:#fff; font-size:12.5px; font-weight:700; letter-spacing:.08em; text-transform:uppercase;}
.ss-tag span {color:var(--ss-mute); font-weight:500; margin:0 10px;}
.ss-eyebrow {color:rgba(255,255,255,.82); font-size:12.5px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; margin:0 0 18px;}
.ss-hero h1 {font-family:'Inter Tight', sans-serif !important; color:#fff; font-size:58px; line-height:1.02; font-weight:800; letter-spacing:-.045em; margin:0 0 26px; padding:0;}
.ss-lead {color:var(--ss-soft); font-size:22px; line-height:1.32; font-weight:700; letter-spacing:-.02em; margin:0 0 20px; max-width:560px;}
.ss-body {color:#EDEDED; font-size:17px; line-height:1.55; font-weight:300; letter-spacing:-.01em; margin:0 0 34px; max-width:560px;}
.ss-cards {display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; max-width:600px;}
.ss-cards div {border:1px solid var(--ss-line); background:rgba(0,0,0,.55); padding:16px 16px 14px;}
.ss-cards b {display:block; color:#fff; font-size:16px; font-weight:700; letter-spacing:-.02em; margin-bottom:4px;}
.ss-cards span {color:var(--ss-mute); font-size:12.5px; line-height:1.4;}
.ss-form-head {margin:6px 0 18px;}
.ss-form-head p.ss-eyebrow {margin:0 0 10px; color:var(--ss-red);}
.ss-form-head h2 {font-family:'Inter Tight', sans-serif !important; color:#fff; font-size:30px; font-weight:800; letter-spacing:-.035em; margin:0; padding:0;}
.ss-form-head p {color:var(--ss-mute); font-size:14.5px; margin:8px 0 0;}
[data-testid="stForm"] {background:rgba(8,8,8,.92) !important; border:1px solid var(--ss-line) !important; border-radius:0 !important; padding:28px 28px 22px !important;}
[data-testid="stForm"] label p {color:#fff !important; font-size:12px !important; font-weight:700 !important; letter-spacing:.1em; text-transform:uppercase;}
[data-testid="stForm"] div[data-baseweb="input"], [data-testid="stForm"] div[data-baseweb="base-input"] {background:#000 !important; border-radius:0 !important; border-color:#3A3A3A !important;}
[data-testid="stForm"] div[data-baseweb="input"]:focus-within {border-color:var(--ss-red) !important;}
[data-testid="stForm"] input {background:#000 !important; color:#fff !important; -webkit-text-fill-color:#fff; caret-color:var(--ss-red); font-size:15px !important;}
[data-testid="stForm"] input::placeholder {color:#6E6E6E !important; -webkit-text-fill-color:#6E6E6E;}
[data-testid="stForm"] [data-testid="stTextInput"] button {background:#000 !important; color:#A9A9A9 !important; border:none !important;}
[data-testid="stFormSubmitButton"] button {height:48px; margin-top:8px; border-radius:0 !important; background:transparent !important; color:#fff !important;
  border:1px solid #fff !important; font-weight:700 !important; letter-spacing:.08em; text-transform:uppercase; transition:background .15s, border-color .15s;}
[data-testid="stFormSubmitButton"] button:hover, [data-testid="stFormSubmitButton"] button:focus-visible {background:var(--ss-red) !important; border-color:var(--ss-red) !important; color:#fff !important;}
[data-testid="stFormSubmitButton"] button p {font-weight:700 !important;}
.ss-note {display:flex; gap:10px; align-items:flex-start; color:var(--ss-mute); font-size:12.5px; line-height:1.55; margin-top:16px;}
.ss-note svg {flex:none; margin-top:2px;}
.ss-foot {display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; color:#7C7C7C; font-size:12px; margin-top:8vh; padding-top:16px; border-top:1px solid rgba(255,255,255,.08);}
.stApp [data-testid="stAlert"] {border-radius:0;}
@media (max-width: 900px) {.ss-hero h1 {font-size:40px;} .ss-lead {font-size:18px;} .ss-body {font-size:15.5px; margin-bottom:24px;} .ss-bar {margin-bottom:4vh;}}
@media (max-width: 640px) {.ss-cards, .ss-body {display:none;} .ss-tag {display:none;} .ss-lead {margin-bottom:6px;} .ss-form-head p.ss-eyebrow {color:rgba(255,255,255,.82);} .ss-tag span, .ss-tag em {display:none;} .ss-hero h1 {font-size:34px;} .ss-word {font-size:15px;}}
</style>""".replace("GRAIN", _GRAIN)

_LOCK = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#A9A9A9" stroke-width="2" stroke-linecap="round" '
         'stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>')


def _html(body: str):
    """Render trusted static HTML (st.html on newer Streamlit, markdown otherwise)."""
    if hasattr(st, "html"):
        st.html(body)
    else:
        st.markdown(body, unsafe_allow_html=True)


def _top_bar():
    _html('<div class="ss-bar"><div class="ss-word">PV MONTE CARLO SCALER</div>'
          '<div class="ss-tag">Engineering Tools<span>|</span><em style="font-style:normal">Solar Yield Analysis</em></div></div>')


def _hero():
    _html("""<div class="ss-hero">
<p class="ss-eyebrow">Solar yield intelligence</p>
<h1>PV Yield &amp;<br>Plant Size Engineering</h1>
<p class="ss-lead">Long-term energy decisions need more than one simulation year. They need the full range of outcomes.</p>
<p class="ss-body">Scale a PVsyst design across the project life with module degradation and Monte Carlo weather, resize the plant without rerunning PVsyst, and issue PVsyst-style reports.</p>
<div class="ss-cards">
<div><b>P50 / P90</b><span>By year and across the full range</span></div>
<div><b>Plant size</b><span>MWp, DC/AC ratio and grid limit</span></div>
<div><b>Reports</b><span>PVsyst-style PDF and 8760 exports</span></div>
</div></div>""")


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


def _form_border_ok() -> bool:
    import inspect
    try:
        return "border" in inspect.signature(st.form).parameters
    except (TypeError, ValueError):
        return False


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

    # ---------------- sign-in page ----------------
    st.markdown(_CSS, unsafe_allow_html=True)
    _top_bar()
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        _hero()
    with right:
        _html("""<div class="ss-form-head"><p class="ss-eyebrow">Secure access</p><h2>Sign in</h2>
<p>Enter your credentials to open the workspace.</p></div>""")
        if st.session_state.get("auth_msg"):
            st.info(st.session_state.pop("auth_msg"))
        locked_until = st.session_state.get("auth_locked_until", 0)
        wait = int(locked_until - time.time())
        with st.form("login", clear_on_submit=False, border=False) if _form_border_ok() else st.form("login", clear_on_submit=False):
            username = st.text_input("Username", key="login_user", autocomplete="username", placeholder="your.username")
            password = st.text_input("Password", type="password", key="login_pw", autocomplete="current-password", placeholder="••••••••")
            submitted = st.form_submit_button("Sign in", type="secondary", **full(st.form_submit_button))
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
        _html(f"""<div class="ss-note">{_LOCK}<span>Access is managed by the app owner. Ask them to add you if you need an account. Uploaded files are processed in memory and are not stored.</span></div>""")
    _html("""<div class="ss-foot"><span>Technical analysis only. No financial analysis.</span><span>Developed by GANGARAJU PILLY</span></div>""")
    st.stop()
