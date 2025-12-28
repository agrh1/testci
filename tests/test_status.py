from __future__ import annotations

from app import app


def test_status_has_environment_and_git_sha() -> None:
    client = app.test_client()
    resp = client.get("/status")
    assert resp.status_code == 200

    data = resp.get_json()
    assert data["status"] == "ok"
    assert "environment" in data
    assert "git_sha" in data
