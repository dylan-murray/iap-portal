"""Flask helpers. Usage:

    from flask import Flask
    from iap_portal.auth.flask import require_user, register_auth

    app = Flask(__name__)
    register_auth(app)  # populates g.user automatically

    @app.route("/label/<id>")
    @require_user
    def label(id, user):
        return {"user": user.email}
"""

from __future__ import annotations

from functools import wraps

from flask import abort, g, request

from iap_portal.auth.core import (
    AuthError,
    User,
    reset_current,
    set_current,
    user_from_request,
)


def register_auth(app, enforce: bool = False) -> None:
    """Populate `g.user` and the `current_user()` context on every request."""

    @app.before_request
    def _before():
        try:
            user = user_from_request(request.headers)
        except AuthError:  # missing, invalid, or expired token
            if enforce:
                abort(401)
            g.user = None
            g._iap_portal_token = None
            return
        g.user = user
        g._iap_portal_token = set_current(user)

    @app.teardown_request
    def _after(_exc):
        token = getattr(g, "_iap_portal_token", None)
        if token is not None:
            reset_current(token)


def require_user(fn):
    """Decorator that injects the current User as a kw-arg. 401s if unauthenticated."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = getattr(g, "user", None)
        if user is None:
            try:
                user = user_from_request(request.headers)
            except AuthError:
                abort(401)
        return fn(*args, user=user, **kwargs)

    return wrapper


__all__ = ["register_auth", "require_user", "User"]
