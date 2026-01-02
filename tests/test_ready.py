from __future__ import annotations

from app import app


def test_ready_endpoint_has_checks() -> None:
    client = app.test_client()
    resp = client.get("/ready")
    assert resp.status_code in (200, 503)

    data = resp.get_json()
    assert data is not None
    assert "ready" in data
    assert "checks" in data
    assert isinstance(data["checks"], list)

    names = {c.get("name") for c in data["checks"] if isinstance(c, dict)}
    assert "env.environment" in names
    assert "config.required_env" in names
