"""return_to / rd validation: portal paths and exact app hosts only."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from iap_portal_server.auth.urls import parse_redirect, validate_return_to

PORTAL = "http://portal.iapportal.test:8080"
APP = "http://annotation.iapportal.test:8080"


@pytest.mark.parametrize("url", ["/", "/dashboard", "/apps/x?tab=1&y=%2F"])
def test_portal_paths_are_allowed(url):
    assert validate_return_to(url) == url


def test_app_and_portal_urls_are_canonicalized():
    assert validate_return_to(APP + "/label/42?x=1") == APP + "/label/42?x=1"
    assert validate_return_to("HTTP://ANNOTATION.iapportal.test:8080") == APP + "/"
    assert validate_return_to(PORTAL + "/admin") == "/admin"
    assert parse_redirect(APP + "/x").slug == "annotation"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.com/steal",
        "//evil.com/x",
        "/\\evil.com",
        "/\\/evil.com",
        "\\\\evil.com",
        "https://evil.com\\@annotation.iapportal.test:8080/",
        "http://evil.com\\@annotation.iapportal.test:8080/",
        "http://user:pw@annotation.iapportal.test:8080/",
        "http://annotation.iapportal.test:8080@evil.com/",
        "https://annotation.iapportal.test:8080/",  # scheme differs from the portal
        "http://annotation.iapportal.test/",  # port differs from the portal
        "http://annotation.iapportal.test.evil.com:8080/",
        "http://a.b.iapportal.test:8080/",
        "http://iapportal.test:8080/",
        "javascript:alert(1)",
        "data:text/html,x",
        "/x\r\nSet-Cookie: a=b",
        "/x\ty",
        "http://annotation.iapportal.test:8080/a\\b",
        "",
    ],
)
def test_ambiguous_or_foreign_targets_are_rejected(url):
    with pytest.raises(HTTPException) as ei:
        validate_return_to(url)
    assert ei.value.status_code == 400
