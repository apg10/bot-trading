"""Helper interno: prepara entrada de análisis desde velas CERRADAS del engine."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Literal

from .analysis import AnalysisRequest, CandleInput


# ---------------------------------------------------------------------------
# Error y resultado
# ---------------------------------------------------------------------------


class MarketAnalysisInputError(ValueError):
    """Error de preparación de entrada desde mercado."""

    code: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedMarketAnalysis:
    """Entrada preparada para análisis técnico desde el motor de mercado."""

    request: AnalysisRequest
    data_source: str
    as_of_close_time_ms: int
    market_state: dict = field(repr=False)
    execution_available: Literal[False] = False


# ---------------------------------------------------------------------------
# Constantes de código
# ---------------------------------------------------------------------------

_CODE_INVALID_CANDLES_COUNT = "INVALID_CANDLES_COUNT"
_MSG_INVALID_CANDLES_COUNT = "candles_count debe ser un entero entre 50 y 500"

_CODE_MARKET_NOT_READY = "MARKET_NOT_READY"
_MSG_MARKET_NOT_READY = "El mercado no está listo para análisis"

_CODE_MARKET_SYMBOL_MISMATCH = "MARKET_SYMBOL_MISMATCH"
_MSG_MARKET_SYMBOL_MISMATCH = "El símbolo del motor no coincide con el solicitado"

_CODE_MARKET_INTERVAL_UNSUPPORTED = "MARKET_INTERVAL_UNSUPPORTED"
_MSG_MARKET_INTERVAL_UNSUPPORTED = "El intervalo del motor no es 1m; se requiere 1m para análisis"

_CODE_INSUFFICIENT_CANDLES = "INSUFFICIENT_CANDLES"
_MSG_INSUFFICIENT_CANDLES = "Veladas cerradas insuficientes: se requieren al menos 50"

_CODE_MARKET_CONTEXT_CHANGED = "MARKET_CONTEXT_CHANGED"
_MSG_MARKET_CONTEXT_CHANGED = "El contexto del mercado cambió durante la preparación"


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------


def prepare_market_analysis(
    engine: object,
    *,
    symbol: str,
    candles_count: int = 200,
) -> PreparedMarketAnalysis:
    """Prepara entrada de análisis desde velas CERRADAS del motor.

    Parámetros
    ----------
    engine : MarketEngine o None
        Motor suministrado por el backend; nunca se crea ni se busca por HTTP.
    symbol : str
        Símbolo a analizar (ej: BTC/USDT). No se normaliza ni infiere.
    candles_count : int
        Cantidad de velas solicitada (50-500). Nunca bool.

    Retorna
    -------
    PreparedMarketAnalysis con request, data_source, as_of exacto y market_state copia.

    Lanza
    -----
    MarketAnalysisInputError con code string cuando la preparación falla.
    """

    # 1. Validar candles_count: debe ser int NO bool, entre 50 y 500.
    if isinstance(candles_count, bool) or not isinstance(candles_count, int):
        raise MarketAnalysisInputError(_CODE_INVALID_CANDLES_COUNT, _MSG_INVALID_CANDLES_COUNT)
    if candles_count < 50 or candles_count > 500:
        raise MarketAnalysisInputError(_CODE_INVALID_CANDLES_COUNT, _MSG_INVALID_CANDLES_COUNT)

    # engine None → MARKET_NOT_READY.
    if engine is None:
        raise MarketAnalysisInputError(_CODE_MARKET_NOT_READY, _MSG_MARKET_NOT_READY)

    # 2. Primera lectura de snapshot.
    snap1 = engine.snapshot()
    if snap1.get("entries_allowed") is not True:
        raise MarketAnalysisInputError(_CODE_MARKET_NOT_READY, _MSG_MARKET_NOT_READY)

    # 3. Validar símbolo e intervalo del snapshot/config.
    if snap1.get("symbol") != symbol:
        raise MarketAnalysisInputError(
            _CODE_MARKET_SYMBOL_MISMATCH, _MSG_MARKET_SYMBOL_MISMATCH
        )

    kline_interval = getattr(getattr(engine, "config", None), "kline_interval", None)
    if kline_interval != "1m":
        raise MarketAnalysisInputError(
            _CODE_MARKET_INTERVAL_UNSUPPORTED, _MSG_MARKET_INTERVAL_UNSUPPORTED
        )

    # 4. Leer velas cerradas y tomar ÚLTIMAS N.
    closed = engine.closed_candles  # type: ignore[attr-defined]
    window = closed[-candles_count:] if len(closed) >= candles_count else closed[:]

    if len(window) < 50:
        raise MarketAnalysisInputError(_CODE_INSUFFICIENT_CANDLES, _MSG_INSUFFICIENT_CANDLES)

    # 5. Segunda lectura de snapshot y config: debe seguir apto, mismo símbolo/intervalo.
    snap2 = engine.snapshot()
    if snap2.get("entries_allowed") is not True:
        raise MarketAnalysisInputError(_CODE_MARKET_NOT_READY, _MSG_MARKET_NOT_READY)

    if snap2.get("symbol") != symbol:
        raise MarketAnalysisInputError(
            _CODE_MARKET_CONTEXT_CHANGED, _MSG_MARKET_CONTEXT_CHANGED
        )

    kline_interval2 = getattr(getattr(engine, "config", None), "kline_interval", None)
    if kline_interval2 != "1m":
        raise MarketAnalysisInputError(
            _CODE_MARKET_CONTEXT_CHANGED, _MSG_MARKET_CONTEXT_CHANGED
        )

    # last_closed_close_time de snap2 debe coincidir con close_time de la última cerrada.
    expected_close = window[-1].get("close_time") if window else None
    actual_close = snap2.get("last_closed_close_time")
    if expected_close is None or actual_close is None or expected_close != actual_close:
        raise MarketAnalysisInputError(
            _CODE_MARKET_CONTEXT_CHANGED, _MSG_MARKET_CONTEXT_CHANGED
        )

    # 6. Construir CandleInput y AnalysisRequest directamente desde la ventana.
    candles_input = [
        CandleInput(
            open_time=c["open_time"],
            open=c["open"],
            high=c["high"],
            low=c["low"],
            close=c["close"],
            volume=c.get("volume", 0.0),
        )
        for c in window
    ]

    request = AnalysisRequest(
        symbol=symbol,
        timeframe="1m",
        candles_count=candles_count,
        candles=candles_input,
    )

    # 7. Retornar con market_state copia independiente profunda.
    return PreparedMarketAnalysis(
        request=request,
        data_source="market_engine",
        as_of_close_time_ms=expected_close,
        market_state=copy.deepcopy(snap2),
        execution_available=False,
    )
