from flask import Flask

from iap_portal.auth.flask import register_auth, require_user

app = Flask(__name__)
register_auth(app)


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/")
@require_user
def index(user):
    return {"hello": user.email, "groups": user.groups}
