"""Streamlit helper. Usage:

    import streamlit as st
    from iap_portal.auth.streamlit import require_user

    user = require_user()  # renders a 401 card + st.stop()s if missing identity

    st.title(f"Hello, {user.email}")

On missing or invalid identity this module renders a dark 'Sign-in required'
card and halts the script via st.stop() — no stack trace leaks to the page.

Streamlit reruns reuse the identity of the WebSocket handshake. That token is
accepted for IAP_PORTAL_MAX_CONNECTION_AGE_SECONDS (default one hour) after it
was issued; after that the user is asked to reload, which re-runs gateway
authorization with the current session and grants.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from iap_portal.auth.core import AuthError, TokenExpired, User, current_user as _current_user


_CARD = """
<div style="
    max-width: 520px;
    margin: 80px auto;
    padding: 32px;
    border-radius: 18px;
    background: linear-gradient(180deg, rgba(20, 20, 30, 0.7), rgba(10, 10, 18, 0.55));
    border: 1px solid rgba(255, 255, 255, 0.08);
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', system-ui, sans-serif;
    color: #e8e8ee;
    box-shadow: 0 20px 60px -20px rgba(0, 0, 0, 0.5);
">
  <div style="
      display: inline-block;
      padding: 4px 10px;
      border-radius: 6px;
      background: rgba(239, 68, 68, 0.12);
      color: #fca5a5;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 14px;
      font-family: 'JetBrains Mono', ui-monospace, monospace;
  ">{eyebrow}</div>
  <h2 style="margin: 0 0 8px 0; font-weight: 600; font-size: 24px; letter-spacing: -0.01em;">
    {title}
  </h2>
  <p style="color: #8b8b96; margin: 0 0 20px 0; line-height: 1.55; font-size: 14px;">
    {message}
  </p>
  <div style="
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 12px;
      color: #6b6b76;
      background: rgba(255, 255, 255, 0.03);
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid rgba(255, 255, 255, 0.05);
      margin-bottom: 20px;
      word-break: break-word;
  ">{detail}</div>
  <a href="{href}" target="_top" style="
      display: inline-block;
      padding: 10px 18px;
      background-image: linear-gradient(135deg, #7c3aed, #db2777);
      color: white;
      text-decoration: none;
      border-radius: 10px;
      font-weight: 500;
      font-size: 14px;
      box-shadow: 0 6px 24px -6px rgba(168, 85, 247, 0.55);
  ">{action} &rarr;</a>
</div>
"""


def _render(**parts: str) -> None:
    st.markdown(_CARD.format(**parts), unsafe_allow_html=True)


def require_user() -> User:
    """Return the authenticated user, or render a card and stop the script.

    Call this at the top of every Streamlit app that needs an authenticated user.
    On success, returns the User. Otherwise renders a dark card and calls
    st.stop() — nothing below this line runs:
      - connection older than the allowed age: 'Reload to continue'
      - missing or invalid identity: 'Sign-in required'
    """
    try:
        return _current_user()
    except TokenExpired:
        _render(
            eyebrow="Session refresh",
            title="Reload to continue",
            message="This page has been open for a while. Reload it to confirm you still have access.",
            detail="connection age limit reached",
            href="",
            action="Reload",
        )
        st.stop()
        raise  # unreachable, satisfies type checkers
    except AuthError as e:
        _render(
            eyebrow="401 · Not authorized",
            title="Sign-in required",
            message="This app is gated by the IAP Portal. You need an active session before you can access it.",
            detail=escape(str(e)),
            href="/",
            action="Go back",
        )
        st.stop()
        raise  # unreachable, satisfies type checkers


__all__ = ["require_user"]
