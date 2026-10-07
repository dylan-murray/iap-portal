from fastapi import FastAPI

from iap_portal.auth import current_user
from iap_portal.auth.fastapi import protect

app = FastAPI(title="{{SLUG}}")
protect(app, public_paths=["/healthz"])


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    user = current_user()
    return {"hello": user.email, "groups": user.groups}
