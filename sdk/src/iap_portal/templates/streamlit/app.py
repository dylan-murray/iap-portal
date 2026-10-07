import streamlit as st

from iap_portal.auth.streamlit import require_user

st.set_page_config(page_title="{{SLUG}}", layout="wide")

user = require_user()

st.sidebar.write(f"Signed in as **{user.email}**")
st.sidebar.write(f"Groups: {', '.join(user.groups) or '—'}")

st.title("{{SLUG}}")
st.write("Edit `app.py` to build your Streamlit app.")
