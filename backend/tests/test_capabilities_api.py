"""Pruebas aisladas del contrato de GET /api/capabilities.

No abren TestClient(main.app) ni inician el motor de mercado.
Montan un FastAPI minimo con solo el router de capacidades.
"""

from __future__ import annotations

import httpx
import importlib
import socket
import sys
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Autouse fixture: bloqueo de red para toda la suite
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real network is forbidden in these tests")

    async def blocked_async(*args, **kwargs):
        blocked()

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(sock, address):
        if sock.family != socket.AF_UNIX:
            blocked()
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            blocked()
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", blocked_async
    )


# ---------------------------------------------------------------------------
# Montaje: app minima con solo el router de capacidades
# ---------------------------------------------------------------------------


def _build_app(env: str) -> FastAPI:
    """Crea una app FastAPI con el router de capacidades y config mock."""
    # Mock config antes de importar el modulo de capacidades
    mock_config = mock.MagicMock()
    mock_config.env = env

    # Reemplazar temporalmente la instancia global de config
    capabilities_mod = importlib.import_module("src.api.capabilities")
    original_config = capabilities_mod.app_config
    capabilities_mod.app_config = mock_config

    app = FastAPI()
    app.include_router(capabilities_mod.router)
    client = TestClient(app)

    # Restaurar config despues de cada uso
    def _restore():
        capabilities_mod.app_config = original_config

    return client, _restore


# ---------------------------------------------------------------------------
# Test 1: Contrato JSON exacto en PAPER
# ---------------------------------------------------------------------------


def test_capabilities_paper_contract():
    """Verifica que la respuesta en PAPER cumple el contrato exacto."""
    client, restore = _build_app("paper")
    try:
        resp = client.get("/api/capabilities")
        assert resp.status_code == 200
        data = resp.json()

        # Keys exactas
        expected_keys = {
            "schema_version",
            "environment",
            "runtime_health_checked",
            "market_data_supported",
            "technical_analysis_supported",
            "ai_scenarios_supported",
            "original_method_supported",
            "execution_available",
            "portfolio_available",
            "paper_simulation_available",
            "exchange_demo_execution_available",
            "live_execution_available",
        }
        assert set(data.keys()) == expected_keys, f"Keys inesperadas: {set(data.keys()) - expected_keys}"

        # Valores
        assert data["schema_version"] == "backend_capabilities.v1"
        assert data["environment"] == "paper"
        assert data["runtime_health_checked"] is False
        assert data["market_data_supported"] is True
        assert data["technical_analysis_supported"] is True
        assert data["ai_scenarios_supported"] is True
        assert data["original_method_supported"] is False
        assert data["execution_available"] is False
        assert data["portfolio_available"] is False
        assert data["paper_simulation_available"] is False
        assert data["exchange_demo_execution_available"] is False
        assert data["live_execution_available"] is False

        # Tipos
        assert isinstance(data["schema_version"], str)
        assert isinstance(data["environment"], str)
        assert isinstance(data["runtime_health_checked"], bool)
        assert isinstance(data["market_data_supported"], bool)
        assert isinstance(data["execution_available"], bool)
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2: Config EXCHANGE_DEMO — solo cambia environment
# ---------------------------------------------------------------------------


def test_capabilities_exchange_demo_environment():
    """En EXCHANGE_DEMO solo cambia environment; todos los flags permanecen false."""
    client, restore = _build_app("exchange_demo")
    try:
        resp = client.get("/api/capabilities")
        assert resp.status_code == 200
        data = resp.json()

        assert data["environment"] == "exchange_demo"
        assert data["runtime_health_checked"] is False
        assert data["market_data_supported"] is True
        assert data["technical_analysis_supported"] is True
        assert data["ai_scenarios_supported"] is True
        assert data["original_method_supported"] is False
        assert data["execution_available"] is False
        assert data["portfolio_available"] is False
        assert data["paper_simulation_available"] is False
        assert data["exchange_demo_execution_available"] is False
        assert data["live_execution_available"] is False
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3: GET repetido / query params no cambian flags; POST no habilita nada
# ---------------------------------------------------------------------------


def test_repeated_get_unchanged():
    """Dos GET consecutivos devuelven el mismo resultado."""
    client, restore = _build_app("paper")
    try:
        r1 = client.get("/api/capabilities")
        r2 = client.get("/api/capabilities")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
    finally:
        restore()


def test_query_params_ignored():
    """Query params no modifican los flags ni la configuracion."""
    client, restore = _build_app("paper")
    try:
        resp = client.get("/api/capabilities?enable_execution=true&force_live=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_available"] is False
        assert data["live_execution_available"] is False
        assert data["environment"] == "paper"
    finally:
        restore()


def test_post_not_allowed():
    """POST a /api/capabilities debe rechazar (method not allowed)."""
    client, restore = _build_app("paper")
    try:
        resp = client.post("/api/capabilities")
        assert resp.status_code == 405
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 4: Ruta registrada en main.app (sin lifespan)
# ---------------------------------------------------------------------------


def test_route_registered_in_main_app():
    """Verifica que la ruta existe en main.app sin iniciar lifespan.

    Los routers incluidos se almacenan como _IncludedRouter; sus rutas
    originales se pierden, pero el openapi del app las reconstruye.
    """
    from src.api import main

    # Verificar via openapi que la ruta esta registrada
    openapi = main.app.openapi()
    paths = openapi.get("paths", {})
    assert "/api/capabilities" in paths, (
        f"/api/capabilities no registrado en openapi. Paths: {list(paths.keys())}"
    )
    # Debe ser GET
    assert "get" in paths["/api/capabilities"], (
        "El endpoint /api/capabilities debe exponer GET"
    )
