"""
Basic API tests. Only cover endpoints/paths that don't require a live
MongoDB connection, so these run in CI without any external services.
"""
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


def test_root_returns_api_info():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert "message" in body
    assert "endpoints" in body
    assert "/sources" in body["endpoints"]


def test_sources_endpoint_handles_no_data_gracefully():
    """Without a MongoDB instance running, /sources should return a
    clean 404 with a helpful message — not crash with a 500."""
    response = client.get("/sources")
    assert response.status_code in (200, 404)
