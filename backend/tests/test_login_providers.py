from authlib.integrations.starlette_client import OAuth
import pytest
from pydantic import ValidationError

from iap_portal_server.config import Settings

from iap_portal_server.api import login


@pytest.mark.asyncio
@pytest.mark.parametrize('providers', [('oidc',), ('google',), ('oidc', 'google'), ()])
async def test_login_shows_only_configured_providers(client, monkeypatch, providers):
    configured = OAuth()
    for name in providers:
        configured.register(name, client_id='fixture', server_metadata_url='https://idp.example.test/discovery')
    monkeypatch.setattr(login, 'oauth', lambda: configured)
    response = await client.get('/login?return_to=%2Fadmin%2Fapps')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    for name in ('oidc', 'google'):
        assert (f'href="/login/{name}?return_to=%2Fadmin%2Fapps"' in response.text) == (name in providers)
        if name not in providers:
            for path in (f'/login/{name}', f'/auth/callback/{name}'):
                denied = await client.get(path)
                assert denied.status_code == 404
    assert ('Sign-in is not configured.' in response.text) == (not providers)


@pytest.mark.asyncio
async def test_oidc_label_is_configurable_and_html_escaped(client, monkeypatch, settings):
    configured = OAuth()
    configured.register('oidc', client_id='fixture')
    monkeypatch.setattr(login, 'oauth', lambda: configured)
    settings.set(oidc_display_name='Company <SSO> & team')
    response = await client.get('/login')
    assert 'Continue with Company &lt;SSO&gt; &amp; team' in response.text
    assert 'Continue with Okta' not in response.text
    assert (await client.get('/login/okta')).status_code == 404
    assert (await client.get('/auth/callback/okta')).status_code == 404


def test_oidc_settings_require_openid_and_valid_label():
    assert Settings().oidc_scopes == ['openid', 'email', 'profile']
    for overrides in (
        {'oidc_scopes': ['email']},
        {'oidc_scopes': ['openid', 'email profile']},
        {'oidc_display_name': '   '},
        {'oidc_token_endpoint_auth_method': 'none'},
    ):
        with pytest.raises(ValidationError):
            Settings(**overrides)
