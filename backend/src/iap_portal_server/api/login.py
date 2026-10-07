"""Sign-in, sign-out, and app sign-in endpoints.

Okta and Google are independent OIDC flows. Users are identified by
(issuer, subject); see auth/identity.py for the linking rules.

App sign-in: an app host without a valid app session is redirected by
/auth/verify to /auth/app-login here. With a portal session, the portal issues a
single-use code and redirects to https://<slug>.<domain>/.iap-portal/callback,
where the gateway check exchanges it for a host-only app session cookie.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote

from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server import ratelimit
from iap_portal_server.api.dev_annotations import login_markup
from iap_portal_server.audit import log_event
from iap_portal_server.auth.identity import LoginRejected, resolve_user, verified_claims
from iap_portal_server.auth.oidc import GOOGLE_ISSUER, PROVIDERS, oauth, provider_issuer
from iap_portal_server.auth.principals import optional_user
from iap_portal_server.auth.session import (
    clear_portal_cookie,
    create_portal_session,
    new_token,
    revoke_session_tree,
    set_portal_cookie,
    token_hash,
)
from iap_portal_server.auth.urls import app_origin, parse_redirect, validate_return_to
from iap_portal_server.auth.verify import CALLBACK_PATH
from iap_portal_server.config import get_settings
from iap_portal_server.db.models import App, AppLoginCode
from iap_portal_server.db.session import get_db

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _message_page(title: str, message: str, status: int, action: str = "") -> HTMLResponse:
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8" /><title>{escape(title)} · {escape(get_settings().display_name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0" /></head>
<body style="font-family:system-ui,sans-serif;background:#07070b;color:#e8e8ee;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0;padding:24px">
<main style="max-width:480px"><h1 style="font-weight:600;font-size:22px">{escape(title)}</h1>
<p style="color:#8b8b96;line-height:1.55">{escape(message)}</p>{action}</main></body></html>"""
    return HTMLResponse(body, status_code=status, headers=_NO_STORE)


@router.get("/login")
async def login_page(request: Request, return_to: str = "/") -> HTMLResponse:
    """Landing-page login showing only configured OIDC providers."""
    return_to = validate_return_to(return_to)
    rt = escape(quote(return_to, safe=""), quote=True)
    buttons = ""
    if getattr(oauth(), "okta", None) is not None:
        buttons += f"""    <a href="/login/okta?return_to={rt}" class="btn btn-okta">
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 15a5 5 0 110-10 5 5 0 010 10z"/>
      </svg>
      Continue with Okta
    </a>
"""
    if getattr(oauth(), "google", None) is not None:
        buttons += f"""    <a href="/login/google?return_to={rt}" class="btn btn-google">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
        <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A10.997 10.997 0 0012 23z"/>
        <path fill="#FBBC05" d="M5.84 14.1A6.6 6.6 0 015.48 12c0-.73.13-1.44.36-2.1V7.06H2.18A11.003 11.003 0 001 12c0 1.78.43 3.46 1.18 4.94l3.66-2.84z"/>
        <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84C6.71 7.31 9.14 5.38 12 5.38z"/>
      </svg>
      Continue with Google
    </a>"""
    if not buttons:
        buttons = '<p class="sub">Sign-in is not configured. Contact your administrator.</p>'
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sign in · {escape(get_settings().display_name)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ height: 100%; margin: 0; }}
    body {{
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
      -webkit-font-smoothing: antialiased;
      color: #e8e8ee;
      background-color: #07070b;
      background-image:
        radial-gradient(60rem 40rem at 15% -10%, rgba(168, 85, 247, 0.18), transparent 60%),
        radial-gradient(50rem 30rem at 110% 10%, rgba(236, 72, 153, 0.14), transparent 60%),
        radial-gradient(55rem 35rem at 50% 120%, rgba(6, 182, 212, 0.14), transparent 60%);
      background-attachment: fixed;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.025) 1px, transparent 1px);
      background-size: 56px 56px;
      mask-image: radial-gradient(ellipse at 50% 30%, rgba(0, 0, 0, 0.6), transparent 70%);
      z-index: 0;
    }}
    .card {{
      position: relative;
      z-index: 1;
      width: 100%;
      max-width: 380px;
      padding: 36px 32px;
      background: linear-gradient(180deg, rgba(20, 20, 30, 0.7), rgba(10, 10, 18, 0.55));
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 20px;
      backdrop-filter: blur(18px) saturate(160%);
      -webkit-backdrop-filter: blur(18px) saturate(160%);
      box-shadow: 0 20px 60px -20px rgba(0, 0, 0, 0.5);
    }}
    .brand {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      margin-bottom: 32px;
    }}
    .brand-mark {{
      width: 40px; height: 40px; border-radius: 12px; flex-shrink: 0;
      background-image: linear-gradient(135deg, #a855f7, #ec4899 55%, #06b6d4);
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 18px -4px rgba(168, 85, 247, 0.6);
    }}
    .brand-logo {{
      width: 26px;
      height: 26px;
      background: white;
      -webkit-mask: url('/assets/iap-logo.svg') center / contain no-repeat;
              mask: url('/assets/iap-logo.svg') center / contain no-repeat;
    }}
    .brand-text {{
      min-width: 0; overflow-wrap: anywhere;
      background: linear-gradient(90deg, #c4b5fd 0%, #f0abfc 50%, #67e8f9 100%);
      -webkit-background-clip: text; background-clip: text;
      -webkit-text-fill-color: transparent;
      font-weight: 600; font-size: 22px; letter-spacing: -0.035em;
    }}
    .eyebrow {{
      font-family: 'JetBrains Mono', ui-monospace, monospace;
      font-size: 11px; text-transform: uppercase; letter-spacing: 0.15em;
      color: #8b8b96; margin-bottom: 8px;
    }}
    h1 {{ font-size: 26px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 8px 0; }}
    p.sub {{ color: #8b8b96; font-size: 14px; margin: 0 0 28px 0; line-height: 1.5; }}
    .btn {{
      display: flex; align-items: center; justify-content: center; gap: 10px;
      width: 100%; padding: 12px 16px; margin: 10px 0;
      border-radius: 10px; border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.03);
      color: #e8e8ee; font-size: 14px; font-weight: 500;
      text-decoration: none; transition: all 0.2s ease;
    }}
    .btn:hover {{
      background: rgba(255, 255, 255, 0.06);
      border-color: rgba(255, 255, 255, 0.18);
      transform: translateY(-1px);
    }}
    .btn svg {{ width: 16px; height: 16px; }}
    .btn-okta svg {{ color: #007DC1; }}
    .btn-google svg {{ color: #e8e8ee; }}
    .foot {{
      margin-top: 24px; padding-top: 20px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      font-size: 12px; color: #6b6b76; text-align: center;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <span class="brand-logo"></span>
      </div>
      <div class="brand-text">{escape(get_settings().display_name)}</div>
    </div>
    <div class="eyebrow">Welcome</div>
    <h1>Sign in to continue</h1>
    <p class="sub">Sign in to access your team's applications.</p>

    {buttons}

    <div class="foot">Having trouble? Contact your administrator.</div>
  </div>
</body>
</html>"""
    return HTMLResponse(html.replace("</body>", login_markup() + "</body>"), headers=_NO_STORE)


@router.get("/login/{provider}")
async def login_redirect(provider: str, request: Request, return_to: str = "/"):
    if provider not in PROVIDERS:
        raise HTTPException(404)
    settings = get_settings()
    await ratelimit.enforce(
        f"login:{ratelimit.client_ip(request)}", settings.rate_limit_login_per_minute
    )
    return_to = validate_return_to(return_to)
    client = getattr(oauth(), provider, None)
    if client is None:
        raise HTTPException(404, f"{provider} sign-in is not configured")
    request.session.clear()
    request.session["return_to"] = return_to
    # Build redirect_uri from the configured public URL — request.url_for would
    # reflect the port uvicorn listens on (e.g. :8088), but in dev the browser
    # is on :5173 (Vite), and the prod URL is portal.apps.example.com.
    redirect_uri = f"{settings.portal_origin}/auth/callback/{provider}"
    return await client.authorize_redirect(request, redirect_uri)


def _issuer_matches(claimed: str, expected: str) -> bool:
    claimed = claimed.rstrip("/")
    if expected == GOOGLE_ISSUER and claimed == "accounts.google.com":
        return True
    return bool(expected) and claimed == expected


@router.get("/auth/callback/{provider}", name="auth_callback")
async def auth_callback(
    provider: str, request: Request, db: AsyncSession = Depends(get_db)
):
    if provider not in PROVIDERS:
        raise HTTPException(404)
    settings = get_settings()
    await ratelimit.enforce(
        f"login:{ratelimit.client_ip(request)}", settings.rate_limit_login_per_minute
    )
    client = getattr(oauth(), provider, None)
    if client is None:
        raise HTTPException(404, f"{provider} sign-in is not configured")
    try:
        token = await client.authorize_access_token(request)
    except OAuthError:
        return _message_page(
            "Sign-in failed",
            "The sign-in response could not be validated. Please try again.",
            400,
            '<a href="/login" style="color:#c4b5fd">Back to sign-in</a>',
        )
    # Authlib validates the ID token (signature, issuer, audience, expiry, nonce)
    # and exposes its claims as token["userinfo"]. Never decode it ourselves.
    claims = token.get("userinfo")
    issuer = await provider_issuer(client)
    if not claims or not _issuer_matches(str(claims.get("iss") or ""), issuer):
        return _message_page("Sign-in failed", "The identity provider returned no valid ID token.", 400)

    ip = ratelimit.client_ip(request)
    try:
        identity = verified_claims(provider, issuer, dict(claims), settings)
        user, how = await resolve_user(db, identity, settings)
    except LoginRejected as rejected:
        await db.rollback()
        await log_event(
            db,
            event_type="login_rejected",
            actor_email=(claims.get("email") or "")[:320] or None,
            ip=ip,
            detail={"provider": provider, "reason": rejected.reason},
        )
        return _message_page("Sign-in not allowed", rejected.message, rejected.status)
    if user.disabled_at is not None:
        await db.rollback()
        return _message_page("Account disabled", "Your portal account is disabled.", 403)

    previous = await optional_user(request, db)
    if previous is not None:
        await revoke_session_tree(db, previous[0].id)
    user.is_admin = user.email in settings.admin_email_set
    user.last_login_at = datetime.now(timezone.utc)
    session_token, session = await create_portal_session(db, user, provider)
    await db.commit()

    await log_event(
        db,
        event_type="login",
        actor_email=user.email,
        ip=ip,
        detail={"provider": provider, "identity": how},
    )

    target = parse_redirect(request.session.pop("return_to", "/")) or parse_redirect("/")
    request.session.clear()
    resp = RedirectResponse(target.url(), status_code=302, headers=_NO_STORE)
    set_portal_cookie(resp, session_token, session.expires_at)
    return resp


async def _logout(request: Request, db: AsyncSession) -> RedirectResponse:
    loaded = await optional_user(request, db)
    if loaded is not None:
        await revoke_session_tree(db, loaded[0].id)
        await db.commit()
        await log_event(db, event_type="logout", actor_email=loaded[1].email)
    resp = RedirectResponse("/login", status_code=303, headers=_NO_STORE)
    clear_portal_cookie(resp)
    return resp


@router.get("/logout")
async def logout_page(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """Sign out when navigated to from the portal itself; otherwise ask first.

    A plain GET from another site or a sibling app (Sec-Fetch-Site other than
    same-origin/none) gets a confirmation form instead of a forced sign-out.
    """
    if request.headers.get("sec-fetch-site") in {"same-origin", "none"}:
        return await _logout(request, db)
    return _message_page(
        "Sign out?",
        "Confirm to sign out of the portal and every app.",
        200,
        '<form method="post" action="/logout"><button type="submit" style="padding:10px 18px;'
        'border-radius:10px;border:0;background:#7c3aed;color:white;font-size:14px">Sign out'
        "</button></form>",
    )


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    # OriginCheckMiddleware already rejected cross-origin posts carrying the cookie.
    return await _logout(request, db)


@router.get("/auth/app-login")
async def app_login(request: Request, rd: str = "", db: AsyncSession = Depends(get_db)):
    """Issue a single-use app sign-in code and send the browser to the app's callback."""
    settings = get_settings()
    target = parse_redirect(rd)
    if target is None or target.slug is None:
        return _message_page("Invalid link", "This sign-in link is not for a portal app.", 400)
    loaded = await optional_user(request, db)
    if loaded is None:
        here = f"/auth/app-login?rd={quote(target.url(), safe='')}"
        return RedirectResponse(
            f"/login?return_to={quote(here, safe='')}", status_code=302, headers=_NO_STORE
        )
    session, user = loaded
    await ratelimit.enforce(f"app-login:{user.id}", settings.rate_limit_app_login_per_minute)
    app = (
        await db.execute(select(App).where(App.slug == target.slug, App.is_enabled.is_(True)))
    ).scalar_one_or_none()
    if app is None:
        return _message_page("App not found", "No enabled app is registered at this address.", 404)
    code = new_token()
    db.add(
        AppLoginCode(
            code_hash=token_hash(code),
            session_id=session.id,
            app_slug=target.slug,
            return_path=target.path,
            expires_at=datetime.now(timezone.utc)
            + timedelta(seconds=settings.app_login_code_ttl_seconds),
        )
    )
    await db.commit()
    return RedirectResponse(
        f"{app_origin(target.slug)}{CALLBACK_PATH}?code={quote(code, safe='')}",
        status_code=302,
        headers=_NO_STORE,
    )
