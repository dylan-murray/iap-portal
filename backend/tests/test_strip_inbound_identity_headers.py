"""Control #3 from docs/SECURITY.md — the portal MUST strip inbound X-Forwarded-*
identity headers before routing, to prevent a browser from spoofing identity.
"""


async def test_x_forwarded_email_header_is_stripped(client):
    resp = await client.get(
        "/healthz",
        headers={
            "x-forwarded-email": "ceo@example.com",
            "x-forwarded-groups": "admins",
            "x-iap-portal-app": "ceo-tool",
        },
    )
    # /healthz just returns ok — the point is that if middleware works, these
    # headers never reached any handler. We can't easily assert absence from
    # inside, so check the lower-level middleware instance directly too.
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_middleware_actually_removes_headers():
    from starlette.requests import Request

    from iap_portal_server.middleware import StripInboundIdentityMiddleware

    seen = {}

    async def app(scope, receive, send):  # minimal ASGI app capturing scope
        req = Request(scope, receive=receive)
        seen["headers"] = dict(req.headers)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = StripInboundIdentityMiddleware(app)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [
            (b"host", b"portal.localhost"),
            (b"x-forwarded-email", b"ceo@example.com"),
            (b"x-forwarded-groups", b"admins"),
            (b"x-forwarded-user", b"1"),
            (b"x-forwarded-name", b"CEO"),
            (b"x-iap-portal-app", b"ceo-tool"),
            (b"cookie", b"iap_portal_session=abc"),
        ],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(msg):
        sent.append(msg)

    await mw(scope, receive, send)

    headers = seen["headers"]
    for bad in (
        "x-forwarded-email",
        "x-forwarded-groups",
        "x-forwarded-user",
        "x-forwarded-name",
        "x-iap-portal-app",
    ):
        assert bad not in headers, f"{bad} was not stripped"
    # The cookie SHOULD survive — it's how sessions work.
    assert "cookie" in headers
