"""Dev feedback must stay authenticated, origin checked, and absent by default."""

import pytest
from httpx import ASGITransport, AsyncClient

from iap_portal_server.auth.principals import Principal
from iap_portal_server.api.dev_annotations import annotation_principal, login_token
from iap_portal_server.auth.session import portal_cookie_name
from iap_portal_server.config import get_settings
from iap_portal_server.main import create_app, create_authorization_app
from iap_portal_server.security import check_settings

PAYLOAD = {
    "kind": "element", "note": "Make this quieter", "path": "/apps",
    "selector": "h1", "text": "Apps", "scroll_x": 0, "scroll_y": 20,
    "bounds": {"x": 10, "y": 20, "width": 100, "height": 30},
    "anchor": {"x": 10, "y": 20, "width": 100, "height": 30},
    "viewport": {"x": 0, "y": 0, "width": 1200, "height": 800},
}
ORIGIN = "http://portal.iapportal.test:8080"


@pytest.mark.asyncio
async def test_feedback_persists_exact_context_and_archives_deletion(settings, tmp_path):
    settings.set(env="dev", enable_dev_annotations=True, dev_annotations_dir=tmp_path)
    app = create_app()
    app.dependency_overrides[annotation_principal] = lambda: Principal(kind="operator")
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        saved = await client.post("/dev/annotations", json=PAYLOAD, headers={"Origin": ORIGIN})
        assert saved.status_code == 201
        note = saved.json()
        assert {k: note[k] for k in PAYLOAD} == PAYLOAD
        assert len(list(tmp_path.glob("*.json"))) == 1
        response = await client.get("/dev/annotations")
        assert response.json() == [note]
        assert response.headers["cache-control"] == "no-store"
        assert (await client.post("/dev/annotations", json=PAYLOAD, headers={"Origin": "https://evil.test", "Cookie": f"{portal_cookie_name()}=fixture"})).status_code == 403
        assert (await client.post("/dev/annotations", json={**PAYLOAD, "note": " "}, headers={"Origin": ORIGIN})).status_code == 422
        assert (await client.delete(f"/dev/annotations/{note['id']}", headers={"Origin": ORIGIN})).status_code == 204
        assert (await client.get("/dev/annotations")).json() == []
        assert len(list(tmp_path.glob("*.deleted"))) == 1


@pytest.mark.asyncio
async def test_feedback_requires_admin(settings, tmp_path):
    settings.set(env="dev", enable_dev_annotations=True, dev_annotations_dir=tmp_path)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get("/dev/annotations")).status_code == 401
        app.dependency_overrides[annotation_principal] = lambda: Principal(kind="registration", app_slug="test")
        assert (await client.get("/dev/annotations")).status_code == 403
        assert (await client.post("/dev/annotations", json=PAYLOAD, headers={"Origin": ORIGIN})).status_code == 403


@pytest.mark.asyncio
async def test_feedback_absent_by_default_and_on_private_listener(settings):
    settings.set(env="dev", enable_dev_annotations=False)
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        assert (await client.get("/dev/annotations")).status_code == 404
    settings.set(enable_dev_annotations=True)
    assert not any(r.path.startswith("/dev/annotations") for r in create_authorization_app().routes)


def test_feedback_flag_rejected_outside_dev(settings):
    settings.set(env="production", enable_dev_annotations=True)
    assert "PORTAL_ENABLE_DEV_ANNOTATIONS requires PORTAL_ENV=dev" in check_settings(get_settings()).fatal


@pytest.mark.asyncio
async def test_login_capability_is_limited_to_login_feedback(settings, tmp_path):
    settings.set(env="dev", enable_dev_annotations=True, dev_annotations_dir=tmp_path)
    app = create_app()
    token = login_token()
    headers = {"X-Dev-Annotation-Token": token, "Origin": ORIGIN}
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN) as client:
        saved = await client.post("/dev/annotations", json={**PAYLOAD, "path": "/login"}, headers=headers)
        assert saved.status_code == 201
        assert saved.json()["author"] == "local-login-reviewer"
        assert (await client.get("/dev/annotations", headers=headers)).json() == [saved.json()]
        assert (await client.post("/dev/annotations", json=PAYLOAD, headers=headers)).status_code == 403
        assert (await client.post("/dev/annotations", json={**PAYLOAD, "path": "/login"}, headers={**headers, "Origin": "https://evil.test"})).status_code == 403
        assert (await client.get("/dev/annotations", headers={"X-Dev-Annotation-Token": token + "invalid"})).status_code == 403
        assert (await client.get("/dev/annotations")).status_code == 401
        # Other portal annotations cannot be read or deleted by a login capability.
        other_id = "00000000-0000-0000-0000-000000000001"
        (tmp_path / f"{other_id}.json").write_text('{"path":"/apps","note":"private fixture"}')
        assert len((await client.get("/dev/annotations", headers=headers)).json()) == 1
        assert (await client.delete(f"/dev/annotations/{other_id}", headers=headers)).status_code == 403
        assert (await client.delete(f"/dev/annotations/{saved.json()['id']}", headers=headers)).status_code == 204


@pytest.mark.asyncio
async def test_login_markup_only_when_enabled(settings, tmp_path, monkeypatch):
    manifest = tmp_path / ".vite"
    manifest.mkdir()
    (manifest / "manifest.json").write_text('{"src/dev-annotator.ts":{"file":"assets/annotations.js","css":["assets/annotations.css"]}}')
    monkeypatch.setenv("PORTAL_STATIC_DIR", str(tmp_path))
    settings.set(env="dev", enable_dev_annotations=True)
    async with AsyncClient(transport=ASGITransport(create_app()), base_url=ORIGIN) as client:
        response = await client.get("/login")
        assert 'id="dev-annotation-root"' in response.text
        assert '/assets/annotations.js' in response.text
        assert '/assets/annotations.css' in response.text
        assert response.headers['cache-control'] == 'no-store'
    settings.set(enable_dev_annotations=False)
    async with AsyncClient(transport=ASGITransport(create_app()), base_url=ORIGIN) as client:
        assert 'dev-annotation-root' not in (await client.get("/login")).text
