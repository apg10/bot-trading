# API endpoints para análisis técnico (indicadores FYL, Keltner, MACD).

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from src.indicators.keltner import calculate_keltner, KeltnerPoint
from src.indicators.macd import calculate_macd, MACDPoint
from src.indicators.fyl import calculate_fyl, PivotPoint, ConsolidationZone

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class AnalysisRequest(BaseModel):
    """Solicitud de análisis técnico."""
    symbol: str = Field(..., description="Símbolo a analizar (ej: BTC/USDT)")
    timeframe: str = Field("15m", description="Temporalidad de las velas")
    candles_count: int = Field(200, ge=50, le=500, description="Cantidad de velas a usar")


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
    keltner: KeltnerResponse
    macd: MACDResponse
    fyl: FYLResponse


def _keltner_to_dict(kp: KeltnerPoint) -> dict:
    return {
        "time_ms": kp.time_ms,
        "ema": round(kp.ema, 2),
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


@router.post("", response_model=AnalysisResponse)
async def analyze(request: AnalysisRequest):
    """Calcula todos los indicadores técnicos para las velas dadas.

    Args:
        request: Solicitud con symbol, timeframe y candles_count

    Returns:
        Análisis completo con Keltner, MACD y FYL
    """
    # Obtener velas del mercado (simulado por ahora)
    # En producción, esto vendría de MarketEngine o base de datos
    import random
    from datetime import datetime, timedelta

    base_time = int(datetime.now().timestamp() * 1000) - request.candles_count * 60_000
    candles = []
    price = 65000.0  # Precio base simulado

    for i in range(request.candles_count):
        time_ms = base_time + i * 60_000
        change = random.uniform(-200, 200)
        open_price = price
        close_price = price + change
        high_price = max(open_price, close_price) + random.uniform(0, 50)
        low_price = min(open_price, close_price) - random.uniform(0, 50)

        candles.append({
            "open_time": time_ms,
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(close_price, 2),
        })
        price = close_price

    # Calcular indicadores
    keltner_points = calculate_keltner(candles)
    closes = [c["close"] for c in candles]
    macd_points = calculate_macd(closes)
    fyl_result = calculate_fyl(candles)

    return AnalysisResponse(
        symbol=request.symbol,
        timeframe=request.timeframe,
        keltner=KeltnerResponse(points=[_keltner_to_dict(p) for p in keltner_points]),
        macd=MACDResponse(points=[_macd_to_dict(p) for p in macd_points]),
        fyl=FYLResponse(
            pivots=[_pivot_to_dict(p) for p in fyl_result.pivots],
            zones=[_zone_to_dict(z) for z in fyl_result.zones],
            annotations=fyl_result.annotations,
        ),
    )
