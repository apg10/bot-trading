"""Proyección segura de dict de estado a SnapshotMarketState y conversión
del último close preparado a SnapshotEvidence numérica.

Solo proyecta connected/history_loaded/complete/recovering/stale/pending_gaps;
ignora extras; conserva False (base Contract). Faltantes/tipos inválidos →
ValidationError. Sin defaults, conversión de truthiness, mutación ni permiso
de operación.
"""

from __future__ import annotations

from pydantic import ValidationError

from .scenario_models import SnapshotEvidence, SnapshotMarketState, ScenarioSnapshot

_ALLOWED = frozenset(("connected", "history_loaded", "complete", "recovering", "stale", "pending_gaps"))


def project_market_state(state: dict) -> SnapshotMarketState:
    """Proyectar *state* a ``SnapshotMarketState`` sin inventar valores.

    - Solo se consideran las claves de ``_ALLOWED``; el resto se ignora.
    - Si falta alguna clave o su tipo es inválido, se propaga
      ``pydantic.ValidationError``.
    - No se realizan conversiones de truthiness ni defaults implícitos:
      ``strict=True`` de la base ``Contract`` garantiza tipos exactos.
    """
    projected: dict = {}
    for key in _ALLOWED:
        if key not in state:
            raise ValidationError.from_exception_data(
                title="SnapshotMarketState",
                line_errors=[{"type": "missing", "loc": (key,)}],
            )
        projected[key] = state[key]
    return SnapshotMarketState(**projected)


# ---------------------------------------------------------------------------
# last_close_to_evidence
# ---------------------------------------------------------------------------

_CODE_EMPTY_PREPARED_CANDLES = "EMPTY_PREPARED_CANDLES"


def last_close_to_evidence(prepared, *, quote_unit: str) -> SnapshotEvidence:
    """Convertir el último close preparado en una SnapshotEvidence numérica.

    Lee ``prepared.request.candles[-1].close`` sin calcular ni redondear.
    Retorna ``SnapshotEvidence(value=close, dimension="price", unit=quote_unit,
    observed_at_ms=prepared.as_of_close_time_ms)``.

    - Candles None/vacías → ``ValueError("EMPTY_PREPARED_CANDLES")``.
    - Delega tipos/finitud/unidad sintáctica al modelo existente; no muta entrada.
    - No reconstruye close_time desde open_time ni emite volumen o indicadores.
    """
    candles = getattr(prepared, "request", None)
    if candles is None:
        raise ValueError("EMPTY_PREPARED_CANDLES")

    candle_list = getattr(candles, "candles", None)
    if not candle_list:
        raise ValueError("EMPTY_PREPARED_CANDLES")

    last_candle = candle_list[-1]
    close_value = last_candle.close

    return SnapshotEvidence(
        value=close_value,
        dimension="price",
        unit=quote_unit,
        observed_at_ms=prepared.as_of_close_time_ms,
    )


# ---------------------------------------------------------------------------
# build_market_scenario_snapshot
# ---------------------------------------------------------------------------

_CODE_INVALID_DATA_SOURCE = "INVALID_DATA_SOURCE"
_CODE_EXECUTION_AVAILABLE = "EXECUTION_AVAILABLE"


def build_market_scenario_snapshot(
    prepared,
    *,
    snapshot_id: str,
    close_ref: str,
    captured_at_ms: int,
    valid_until_ms: int,
    missing_data: list[str],
) -> ScenarioSnapshot:
    """Componer un ScenarioSnapshot mínimo desde PreparedMarketAnalysis.

    Rechaza data_source distinto de "market_engine" o execution_available no False
    mediante ValueError; no recibe ni transforma AnalysisResponse.

    Crea ScenarioSnapshot con campos del prepared y del caller; usa project_market_state
    y una evidencia {close_ref: last_close_to_evidence(...)}. Deriva quote_unit de
    symbol.rsplit("/",1)[-1]. Copia missing_data. Delega refs, unidades y límites
    temporales al modelo. Llama snapshot.assert_current(captured_at_ms) antes de retornar.
    """
    # Validaciones del prepared.
    data_source = getattr(prepared, "data_source", None)
    if data_source != "market_engine":
        raise ValueError(_CODE_INVALID_DATA_SOURCE)

    execution_available = getattr(prepared, "execution_available", True)
    if execution_available is not False:
        raise ValueError(_CODE_EXECUTION_AVAILABLE)

    # Derivar quote_unit del símbolo.
    symbol = getattr(prepared, "request", None)
    if symbol is None:
        raise ValueError("EMPTY_PREPARED_CANDLES")
    raw_symbol = getattr(symbol, "symbol", "")
    quote_unit = raw_symbol.rsplit("/", 1)[-1]

    # Construir SnapshotMarketState desde el estado del prepared.
    market_state_dict = getattr(prepared, "market_state", {})
    ms = project_market_state(market_state_dict)

    # Construir evidencia con last_close_to_evidence.
    evidence = {close_ref: last_close_to_evidence(prepared, quote_unit=quote_unit)}

    # Construir ScenarioSnapshot.
    snapshot = ScenarioSnapshot(
        schema_version="market_snapshot.v1",
        snapshot_id=snapshot_id,
        symbol=raw_symbol,
        timeframe=getattr(symbol, "timeframe", "1m"),
        environment="paper",
        data_source=data_source,
        captured_at_ms=captured_at_ms,
        as_of_close_time_ms=prepared.as_of_close_time_ms,
        valid_until_ms=valid_until_ms,
        market_state=ms,
        evidence=evidence,
        missing_data=list(missing_data),
    )

    # Validar vigencia al instante declarado.
    snapshot.assert_current(captured_at_ms)

    return snapshot


# ---------------------------------------------------------------------------
# prepare_market_scenario_snapshot
# ---------------------------------------------------------------------------


def prepare_market_scenario_snapshot(
    engine,
    *,
    symbol: str,
    candles_count: int = 200,
    snapshot_id: str,
    close_ref: str,
    captured_at_ms: int,
    valid_until_ms: int,
    missing_data: list[str],
) -> ScenarioSnapshot:
    """Orquestar motor → preparación aceptada → snapshot mínimo.

    Llama prepare_market_analysis(engine, symbol=symbol, candles_count=candles_count),
    pasa su resultado y los cinco kwargs restantes a build_market_scenario_snapshot,
    retorna ScenarioSnapshot. Propaga errores sin capturarlos ni recodificarlos.
    Sin validaciones duplicadas, analyze/calculate, reloj, IDs o TTL generados.
    """
    from src.api.analysis_market import prepare_market_analysis

    prepared = prepare_market_analysis(engine, symbol=symbol, candles_count=candles_count)

    return build_market_scenario_snapshot(
        prepared,
        snapshot_id=snapshot_id,
        close_ref=close_ref,
        captured_at_ms=captured_at_ms,
        valid_until_ms=valid_until_ms,
        missing_data=missing_data,
    )


# ---------------------------------------------------------------------------
