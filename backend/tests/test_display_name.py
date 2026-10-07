from html import escape

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from iap_portal_server.config import Settings
from iap_portal_server.main import create_app


@pytest.mark.asyncio
async def test_custom_display_name_is_public_and_html_escaped(settings, tmp_path, monkeypatch):
    name = 'Acme <script>alert("x")</script> & Research'
    settings.set(display_name=name)
    (tmp_path / 'index.html').write_text('<html><head><title>iap-portal</title></head><body>SPA</body></html>')
    monkeypatch.setenv('PORTAL_STATIC_DIR', str(tmp_path))
    async with AsyncClient(transport=ASGITransport(create_app()), base_url='http://portal.iapportal.test:8080') as client:
        config = await client.get('/api/config')
        assert config.status_code == 200
        assert config.json() == {'display_name': name}
        assert config.headers['cache-control'] == 'no-store'
        for path in ('/login', '/', '/admin/groups'):
            response = await client.get(path)
            assert response.status_code == 200
            assert escape(name) in response.text
            assert '<script>alert' not in response.text
        assert f'<div class="brand-text">{escape(name)}</div>' in (await client.get('/login')).text


def test_display_name_defaults_and_validation(monkeypatch):
    monkeypatch.delenv('PORTAL_DISPLAY_NAME', raising=False)
    assert Settings().display_name == 'iap-portal'
    monkeypatch.setenv('PORTAL_DISPLAY_NAME', '  Acme Workspace  ')
    assert Settings().display_name == 'Acme Workspace'
    for value in ('', '   ', 'x' * 101):
        with pytest.raises(ValidationError):
            Settings(display_name=value)
