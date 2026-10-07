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
        assert data["implementation_status"] == "not_implemented"
        assert data["execution_available"] is False
        assert data["portfolio_available"] is False


class TestFixtureData:
    def test_fixture_data(self, client: TestClient):
        resp = client.get("/fixture/data")
        assert resp.status_code == 200
        data = resp.json()
        assert "snapshot" in data
        assert "candles" in data
        assert data["data_source"] == "TEST_ONLY"
        assert data["interval"] == "1m"


class TestEngineAction:
    @pytest.mark.parametrize("action", ["pause", "resume", "stop"])
    def test_engine_action_not_implemented(self, client: TestClient, action: str):
        before = client.get("/engine/state").json()
        resp = client.post("/engine/action", json={"action": action})
        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert detail["code"] == "ENGINE_NOT_IMPLEMENTED"
        assert detail["action"] == action
        assert detail["executed"] is False
        assert client.get("/engine/state").json() == before

    def test_engine_action_rejected_unknown(self, client: TestClient):
        # Acción no en el enum debe ser rechazada por Pydantic (422)
        resp = client.post("/engine/action", json={"action": "unknown_action"})
        assert resp.status_code == 422
