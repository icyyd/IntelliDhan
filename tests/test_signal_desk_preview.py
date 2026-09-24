"""Synthetic browser QA must not become a second production gateway."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def preview():
    path = Path(__file__).resolve().parents[1] / "scripts/preview_signal_desk.py"
    spec = importlib.util.spec_from_file_location("signal_desk_preview_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_app()


def test_preview_is_labeled_and_reads_only_synthetic_data(preview):
    client = TestClient(preview, base_url="http://127.0.0.1:8322", client=("127.0.0.1", 50000))
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="demoPreviewBanner"' in response.text
    assert "DEMO —" in response.text
    assert response.headers["X-IntelliDhan-Preview"] == "SYNTHETIC-UI-FIXTURES-ONLY"
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/auth/session").json()["fixture_only"] is True
    for path in ("state", "playbooks", "dossier/AAPL", "news", "daily-brief", "trade-log"):
        result = client.get("/api/" + path)
        assert result.status_code == 200
        assert result.json()["fixture_only"] is True
    assert client.get("/api/autotrade/status").status_code == 404


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/auth/session"),
    ("DELETE", "/api/auth/session"),
    ("POST", "/api/watchlists"),
    ("POST", "/api/autotrade/activate"),
])
def test_preview_rejects_every_mutation(preview, method, path):
    client = TestClient(preview, base_url="http://127.0.0.1:8322", client=("127.0.0.1", 50000))
    response = client.request(method, path, json={})
    assert response.status_code == 405


@pytest.mark.parametrize("host,peer", [
    ("http://example.com", "127.0.0.1"),
    ("http://127.0.0.1", "203.0.113.10"),
])
def test_preview_rejects_foreign_host_or_peer(preview, host, peer):
    client = TestClient(preview, base_url=host, client=(peer, 50000))
    assert client.get("/").status_code == 403
    assert client.get("/api/state").status_code == 403
