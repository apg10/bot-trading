"""Pruebas de la aplicación FastAPI."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["environment"] == "paper"

    def test_health_not_live(self, client: TestClient):
        resp = client.get("/health")
        assert resp.json()["is_live"] is False


class TestEngineState:
    def test_engine_state(self, client: TestClient):
        resp = client.get("/engine/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "stopped"
        assert data["environment"] == "paper"


class TestFixtureData:
    def test_fixture_data(self, client: TestClient):
        resp = client.get("/fixture/data")
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshot" in data
        assert "candles" in data


class TestEngineAction:
    def test_engine_action_accepted(self, client: TestClient):
        # Desde estado STOPPED (placeholder), solo "resume" es legal
        resp = client.post("/engine/action", json={"action": "resume"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"

    def test_engine_action_rejected_invalid_state(self, client: TestClient):
        # Desde estado STOPPED (placeholder), "pause" no es legal
        resp = client.post("/engine/action", json={"action": "pause"})
        assert resp.status_code == 409

    def test_engine_action_rejected_unknown(self, client: TestClient):
        # Acción no en el enum debe ser rechazada por Pydantic (422)
        resp = client.post("/engine/action", json={"action": "unknown_action"})
        assert resp.status_code == 422
