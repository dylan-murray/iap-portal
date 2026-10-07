"""Local mock-provider boundaries: registered redirects and escaped HTML inputs."""

import importlib.util
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

SPEC = importlib.util.spec_from_file_location('mock_idp_test', Path(__file__).resolve().parents[1] / 'dev/mock-idp/main.py')
IDP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IDP
SPEC.loader.exec_module(IDP)
CALLBACK = 'http://localhost:8088/auth/callback/oidc'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(IDP, 'REDIRECT_URIS', (CALLBACK,))
    IDP._CODES.clear()
    with TestClient(IDP.app) as client:
        yield client
    IDP._CODES.clear()


@pytest.mark.parametrize('redirect', [
    'https://evil.example/steal', '//evil.example/steal',
    CALLBACK + '.evil.example', CALLBACK + '?next=https://evil.example',
    'http://localhost:8088@evil.example/auth/callback/oidc',
])
def test_unregistered_redirects_rejected_before_issuing_code(client, redirect):
    assert client.get('/authorize', params={'client_id':IDP.CLIENT_ID,'redirect_uri':redirect}).status_code == 400
    assert client.post('/authorize/choose', data={'username':'alice','redirect_uri':redirect}, follow_redirects=False).status_code == 400
    assert not IDP._CODES


def test_authorization_form_escapes_untrusted_fields(client):
    attack = '\"><script>alert(1)</script>'
    response = client.get('/authorize', params={
        'client_id':IDP.CLIENT_ID,'redirect_uri':CALLBACK,
        'state':attack,'nonce':attack,'code_challenge':attack,
    })
    assert response.status_code == 200
    assert '<script>' not in response.text
    assert '&quot;&gt;&lt;script&gt;' in response.text


def test_registered_callback_preserves_encoded_state_and_issues_code(client):
    response=client.post('/authorize/choose', data={
        'username':'alice','redirect_uri':CALLBACK,'state':'a&b=<value>',
    },follow_redirects=False)
    assert response.status_code == 302
    destination=urlsplit(response.headers['location'])
    assert destination._replace(query='').geturl() == CALLBACK
    query=parse_qs(destination.query)
    assert query['state']==['a&b=<value>']
    assert query['code'][0] in IDP._CODES
