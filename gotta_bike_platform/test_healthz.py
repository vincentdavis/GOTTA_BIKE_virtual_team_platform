"""Tests for the /healthz version/health endpoint."""

import pytest


@pytest.mark.django_db
def test_healthz_returns_ok_json(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response["content-type"] == "application/json"
    data = response.json()
    assert data["status"] == "ok"
    assert "deployed_at" in data
    assert "version" in data  # None locally, short SHA in deploys
