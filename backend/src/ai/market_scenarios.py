"""Orquestación de análisis de escenarios desde snapshot preparado.

Conecta preparación propia con cliente N02 inyectado y propaga su resultado.
"""

from __future__ import annotations

from src.ai.scenario_adapters import prepare_market_scenario_snapshot
from src.ai.scenario_models import ScenarioAnalysisResult, ScenarioSnapshot


async def analyze_market_scenarios(
    client,
    engine,
    *,
    symbol: str,
    candles_count: int = 200,
    snapshot_id: str,
    close_ref: str,
    captured_at_ms: int,
    valid_until_ms: int,
    missing_data: list[str],
) -> ScenarioAnalysisResult:
    """Orquestar motor → snapshot propio → cliente N02.

    Importa prepare_market_scenario_snapshot a nivel del módulo nuevo.
    Prepara snapshot con argumentos exactos; luego:
        return await client.analyze_snapshot(snapshot).
    Retorna ScenarioAnalysisResult, sin reconstruir, copiar o cambiar flags/source.
    Propaga excepciones intactas; no HTTPException ni recodificación.
    """
    snapshot: ScenarioSnapshot = prepare_market_scenario_snapshot(
        engine,
        symbol=symbol,
        candles_count=candles_count,
        snapshot_id=snapshot_id,
        close_ref=close_ref,
        captured_at_ms=captured_at_ms,
        valid_until_ms=valid_until_ms,
        missing_data=missing_data,
    )

    return await client.analyze_snapshot(snapshot)
