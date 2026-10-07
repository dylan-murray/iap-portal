"""Example Streamlit app — validates subdomain routing + WebSocket + auth headers."""

import streamlit as st

from iap_portal.auth.streamlit import require_user

st.set_page_config(page_title="Streamlit Example", layout="wide")

user = require_user()

st.title("Streamlit Example")
st.write("If you can see this page **and** your email below, auth works end-to-end.")

st.subheader("Identity")
st.json({"email": user.email, "name": user.name, "groups": user.groups})

st.subheader("Interactive bit (validates WebSocket)")
count = st.slider("Count", 0, 100, 42)
st.write(f"Selected: {count}")
