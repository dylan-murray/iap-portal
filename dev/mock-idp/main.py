"""Minimal OIDC provider for local dev. Runs on any arch natively.

Implements just enough of OpenID Connect to satisfy Authlib's PKCE flow:
  GET  /.well-known/openid-configuration
  GET  /.well-known/jwks.json
  GET  /authorize      → shows tiny user-picker, redirects back with code
  POST /token          → issues a signed id_token
  GET  /userinfo       → returns the user claims

Users are hardcoded below. Edit USERS to add more.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

ISSUER = os.environ.get("MOCK_IDP_ISSUER", "http://localhost:8081")
CLIENT_ID = "portal-local"

USERS = {
    "alice": {"sub": "u-alice", "email": "alice@example.com", "name": "Alice"},
    "bob":   {"sub": "u-bob",   "email": "bob@example.com",   "name": "Bob"},
    "intern": {"sub": "u-int",  "email": "intern@example.com", "name": "Intern"},
}

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "mock-idp-1"


@dataclass
class PendingAuth:
    username: str
    redirect_uri: str
    code_challenge: str | None
    nonce: str | None


# Short-lived auth codes: code → PendingAuth
_CODES: dict[str, PendingAuth] = {}

app = FastAPI(title="mock-idp")


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile", "groups"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/.well-known/jwks.json")
def jwks():
    jwk = RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
    jwk.update({"kid": _KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


@app.get("/authorize")
def authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: str = "",
    nonce: str = "",
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    scope: str = "",
):
    if client_id != CLIENT_ID:
        raise HTTPException(400, f"unknown client_id {client_id}")
    buttons = "".join(
        f"""<form method="POST" action="/authorize/choose" style="display:inline">
            <input type="hidden" name="username" value="{u}">
            <input type="hidden" name="redirect_uri" value="{redirect_uri}">
            <input type="hidden" name="state" value="{state}">
            <input type="hidden" name="nonce" value="{nonce}">
            <input type="hidden" name="code_challenge" value="{code_challenge or ''}">
            <button type="submit"
                    style="padding:10px 20px;margin:6px;background:#0a7;color:#fff;
                           border:0;border-radius:6px;cursor:pointer;font-size:16px">
                Sign in as {u}
            </button>
        </form>"""
        for u in USERS
    )
    return HTMLResponse(
        f"""<!doctype html><html><body style="font-family:system-ui;padding:60px">
            <h1>mock-idp</h1>
            <p>Pick a user to sign in as:</p>
            {buttons}
        </body></html>"""
    )


@app.post("/authorize/choose")
def authorize_choose(
    username: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    nonce: str = Form(""),
    code_challenge: str = Form(""),
):
    if username not in USERS:
        raise HTTPException(400, "unknown user")
    code = secrets.token_urlsafe(32)
    _CODES[code] = PendingAuth(
        username=username,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge or None,
        nonce=nonce or None,
    )
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}",
        status_code=302,
    )


@app.post("/token")
async def token(request: Request):
    form = dict(await request.form())
    code = form.get("code", "")
    entry = _CODES.pop(code, None)
    if entry is None:
        raise HTTPException(400, "invalid_grant")
    user = USERS[entry.username]

    now = int(time.time())
    id_token_claims: dict = {
        "iss": ISSUER,
        "sub": user["sub"],
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 600,
        "email": user["email"],
        "email_verified": True,
        "name": user["name"],
    }
    if entry.nonce:
        id_token_claims["nonce"] = entry.nonce

    id_token = jwt.encode(
        id_token_claims,
        _KEY.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        algorithm="RS256",
        headers={"kid": _KID},
    )
    access_token = secrets.token_urlsafe(32)

    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 600,
            "id_token": id_token,
            "scope": "openid email profile",
        }
    )


@app.get("/userinfo")
def userinfo():
    return {"sub": "u-alice", "email": "alice@example.com", "name": "Alice"}
