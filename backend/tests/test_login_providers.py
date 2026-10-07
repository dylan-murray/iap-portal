from authlib.integrations.starlette_client import OAuth
import pytest

from iap_portal_server.api import login


@pytest.mark.asyncio
@pytest.mark.parametrize('providers', [('okta',), ('google',), ('okta', 'google'), ()])
async def test_login_shows_only_configured_providers(client, monkeypatch, providers):
    configured = OAuth()
    for name in providers:
        configured.register(name, client_id='fixture', server_metadata_url='https://idp.example.test/discovery')
    monkeypatch.setattr(login, 'oauth', lambda: configured)
    response = await client.get('/login?return_to=%2Fadmin%2Fapps')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    for name in ('okta', 'google'):
        assert (f'href="/login/{name}?return_to=%2Fadmin%2Fapps"' in response.text) == (name in providers)
        if name not in providers:
            for path in (f'/login/{name}', f'/auth/callback/{name}'):
                denied = await client.get(path)
                assert denied.status_code == 404
    assert ('Sign-in is not configured.' in response.text) == (not providers)
