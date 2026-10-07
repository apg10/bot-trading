# API endpoints para análisis técnico (indicadores FYL, Keltner, MACD).
"""
IMPORTANTE: Este endpoint NO genera datos de mercado. Recibe velas como input
desde el cliente. Si no se proporcionan al menos 50 velas, se rechaza con 422.

NUNCA usar para decisiones operacionales sin verificar procedencia de datos.
"""

import math

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from src.indicators.keltner import calculate_keltner, KeltnerPoint
from src.indicators.macd import calculate_macd, MACDPoint
from src.indicators.fyl import calculate_fyl, PivotPoint, ConsolidationZone

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class CandleInput(BaseModel):
    """Una vela para análisis técnico."""
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class AnalysisRequest(BaseModel):
    """Solicitud de análisis técnico."""
    symbol: str = Field(..., min_length=1, max_length=20, description="Símbolo a analizar (ej: BTC/USDT)")
    timeframe: str = Field("15m", description="Temporalidad de las velas")
    candles_count: int = Field(200, ge=50, le=500, description="Cantidad de velas a usar")
    # Velas proporcionadas por el cliente (reales o simuladas).
    # Se requieren al menos 50 velas; de lo contrario se rechaza con 422.
    candles: Optional[List[CandleInput]] = None


class KeltnerResponse(BaseModel):
    """Respuesta del indicador Keltner."""
    points: List[dict]


class MACDResponse(BaseModel):
    """Respuesta del indicador MACD."""
    points: List[dict]


class FYLResponse(BaseModel):
    """Respuesta del indicador FYL."""
    pivots: List[dict]
    zones: List[dict]
    annotations: List[dict]


class AnalysisResponse(BaseModel):
    """Respuesta completa de análisis técnico."""
    symbol: str
    timeframe: str
    data_source: str  # "provided"
    keltner: KeltnerResponse
    macd: MACDResponse
    fyl: FYLResponse


def _keltner_to_dict(kp: KeltnerPoint) -> dict:
    return {
        "time_ms": kp.time_ms,
        "ema": round(kp.ema_val, 2),
        "upper": round(kp.upper, 2),
        "lower": round(kp.lower, 2),
        "atr": round(kp.atr, 2),
    }


def _macd_to_dict(mp: MACDPoint) -> dict:
    return {
        "time_ms": mp.time_ms,
        "macd_line": round(mp.macd_line, 4),
        "signal_line": round(mp.signal_line, 4),
        "histogram": round(mp.histogram, 4),
    }


def _pivot_to_dict(p: PivotPoint) -> dict:
    return {
        "id": f"pivot_{p.time_ms}",
        "time_ms": p.time_ms,
        "price": round(p.price, 2),
        "pivot_type": p.pivot_type.value,
        "strength": round(p.strength, 4),
    }


def _zone_to_dict(z: ConsolidationZone) -> dict:
    return {
        "id": z.id,
        "start_time_ms": z.start_time_ms,
        "end_time_ms": z.end_time_ms,
        "origin": z.origin.value,
        "low": round(z.low, 2),
        "high": round(z.high, 2),
        "contacts": z.contacts,
        "status": z.status.value,
    }


def _resolve_candles(request: AnalysisRequest) -> tuple[List[dict], str]:
    """Resuelve las velas a usar para análisis.

    Raises:
        HTTPException 422 si se reciben menos de 50 velas.

    Returns:
        Tupla (candles_dict, source_label) donde source_label es 'provided'.
    """
    if request.candles is None or len(request.candles) < 50:
        received = 0 if request.candles is None else len(request.candles)
        raise HTTPException(
            status_code=422,
            detail={"code": "INSUFFICIENT_CANDLES", "received": received, "required": 50},
        )

    candles = [
        {
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in request.candles[:request.candles_count]
    ]
    return candles, "provided"


@router.post("", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
    """Calcula todos los indicadores técnicos para las velas dadas.

    Args:
        request: Solicitud con symbol, timeframe, candles_count y opcionalmente candles.

    Returns:
        Análisis completo con Keltner, MACD y FYL.
        data_source indica que se usaron velas proporcionadas por el cliente.
    """
    # Validar que todos los valores OHLCV son finitos
    for i, candle in enumerate(request.candles or []):
        for field_name in ("open", "high", "low", "close", "volume"):
            if not math.isfinite(getattr(candle, field_name)):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "NON_FINITE_CANDLE_VALUE", "index": i, "field": field_name},
                )

    # Validar que high >= low en todas las velas y open/close dentro de [low,high]
    for i, candle in enumerate(request.candles or []):
        if candle.high < candle.low:
            raise HTTPException(
                status_code=422,
                detail=f"Candle {i}: high ({candle.high}) debe ser >= low ({candle.low})",
            )
        # open y close deben estar dentro del intervalo inclusivo [low,high]
        if not (candle.low <= candle.open <= candle.high):
            raise HTTPException(
                status_code=422,
                detail={"code": "CANDLE_PRICE_OUT_OF_RANGE", "index": i, "field": "open"},
            )
        if not (candle.low <= candle.close <= candle.high):
            raise HTTPException(
                status_code=422,
                detail={"code": "CANDLE_PRICE_OUT_OF_RANGE", "index": i, "field": "close"},
            )

    # Validar open_time estrictamente creciente en el orden proporcionado
    for i in range(1, len(request.candles or [])):
        if request.candles[i].open_time <= request.candles[i - 1].open_time:
            raise HTTPException(
                status_code=422,
                detail={"code": "NON_INCREASING_CANDLE_TIME", "index": i},
            )

    candles, data_source = _resolve_candles(request)

    # Extraer series con timestamps reales de las velas
    closes = [c["close"] for c in candles]
    timestamps = [c["open_time"] for c in candles]

    # Calcular indicadores
    keltner_points = calculate_keltner(candles)
    macd_points = calculate_macd(closes, timestamps=timestamps)
    fyl_result = calculate_fyl(candles)

    return AnalysisResponse(
        symbol=request.symbol,
        timeframe=request.timeframe,
        data_source=data_source,
        keltner=KeltnerResponse(points=[_keltner_to_dict(p) for p in keltner_points]),
        macd=MACDResponse(points=[_macd_to_dict(p) for p in macd_points]),
        fyl=FYLResponse(
            pivots=[_pivot_to_dict(p) for p in fyl_result.pivots],
            zones=[_zone_to_dict(z) for z in fyl_result.zones],
            annotations=fyl_result.annotations,
        ),
    )
