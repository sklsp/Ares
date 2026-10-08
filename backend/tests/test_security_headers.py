"""The API sets its security headers on every response, errors included."""

import pytest


@pytest.mark.parametrize("path", ["/health", "/auth/me", "/definitely-not-a-route"])
def test_security_headers(client, path):
    response = client.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
