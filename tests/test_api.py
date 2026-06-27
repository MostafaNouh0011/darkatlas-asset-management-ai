"""
API-level tests exercising the actual FastAPI routes (not just the service
functions). These confirm routing, auth, and request/response shapes work
end to end. LangChain-backed /analyze endpoints are intentionally NOT tested
here since they require a real API key and network access — see the README
for how those were manually verified with example prompts/outputs instead.
"""
import os

SAMPLE = [
    {"id": "a1", "type": "domain", "value": "example.com",
     "status": "active", "source": "scan", "tags": ["root"], "metadata": {}},
]


def test_import_requires_api_key(client):
    response = client.post("/import", json=SAMPLE)
    assert response.status_code == 422  # missing required header entirely


def test_import_rejects_wrong_api_key(client):
    response = client.post("/import", json=SAMPLE, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_import_succeeds_with_correct_key(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    import app.auth
    app.auth.API_KEY = "test-key"  # auth.py reads the env var once at import time

    response = client.post("/import", json=SAMPLE, headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 0


def test_list_assets_after_import(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-key")
    import app.auth
    app.auth.API_KEY = "test-key"

    client.post("/import", json=SAMPLE, headers={"X-API-Key": "test-key"})
    response = client.get("/assets")
    assert response.status_code == 200
    assets = response.json()
    assert len(assets) == 1
    assert assets[0]["id"] == "a1"


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
