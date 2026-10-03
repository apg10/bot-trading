# Models para el bot de trading — reglas, estrategias y estados.

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


class SignalStrength(float, Enum):
    WEAK = 0.25
    MODERATE = 0.50
    STRONG = 0.75
    VERY_STRONG = 0.90


class TradeRule(BaseModel):
    """Regla de trading individual."""
    id: str = Field(..., description="ID único de la regla")
    name: str = Field(..., description="Nombre descriptivo")
    condition: str = Field(..., description="Condición a evaluar (ej: 'MACD_crossover_bullish')")
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="Peso de la regla (0-1)")
    enabled: bool = Field(default=True, description="Si la regla está activa")


class TradingStrategy(BaseModel):
    """Estrategia de trading compuesta por reglas."""
    name: str = Field(..., description="Nombre de la estrategia")
    description: str = Field(..., description="Descripción detallada")
    rules: list[TradeRule] = Field(default_factory=list)
    max_position_size: float = Field(default=1.0, ge=0.01, le=100.0, description="Tamaño máximo de posición")
    stop_loss_pct: float = Field(default=0.02, ge=0.001, le=0.50, description="Stop loss porcentaje")
    take_profit_pct: float = Field(default=0.04, ge=0.001, le=1.0, description="Take profit porcentaje")


class TradingSignal(BaseModel):
    """Señal generada por el motor de reglas."""
    timestamp_ms: int = Field(..., description="Timestamp en milisegundos")
    symbol: str = Field(..., description="Símbolo del activo")
    signal_type: SignalType = Field(..., description="Tipo de señal")
    strength: SignalStrength = Field(..., description="Fuerza de la señal")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confianza (0-1)")
    entry_price: Optional[float] = Field(None, description="Precio de entrada sugerido")
    stop_loss_price: Optional[float] = Field(None, description="Precio de stop loss")
    take_profit_price: Optional[float] = Field(None, description="Precio de take profit")
    reason: str = Field(..., description="Motivo de la señal")
    rules_triggered: list[str] = Field(default_factory=list, description="Reglas que activaron la señal")


class Position(BaseModel):
    """Posición abierta."""
    id: str = Field(..., description="ID único de la posición")
    symbol: str = Field(..., description="Símbolo del activo")
    side: Side = Field(..., description="Dirección (buy/sell)")
    quantity: float = Field(..., gt=0.0, description="Cantidad de la posición")
    entry_price: float = Field(..., gt=0.0, description="Precio de entrada")
    current_price: Optional[float] = Field(None, description="Precio actual del mercado")
    unrealized_pnl: float = Field(default=0.0, description="P&L no realizado")
    timestamp_ms: int = Field(..., description="Timestamp de apertura")
    stop_loss_price: Optional[float] = Field(None, description="Stop loss de la posición")
    take_profit_price: Optional[float] = Field(None, description="Take profit de la posición")


class Order(BaseModel):
    """Orden de trading."""
    id: str = Field(..., description="ID único de la orden")
    symbol: str = Field(..., description="Símbolo del activo")
    side: Side = Field(..., description="Dirección (buy/sell)")
    order_type: OrderType = Field(..., description="Tipo de orden")
    quantity: float = Field(..., gt=0.0, description="Cantidad")
    price: Optional[float] = Field(None, description="Precio de la orden")
    status: str = Field(default="pending", description="Estado de la orden")
    timestamp_ms: int = Field(..., description="Timestamp de creación")


class AnalysisResult(BaseModel):
    """Resultado del análisis técnico."""
    symbol: str = Field(..., description="Símbolo analizado")
    timeframe: str = Field(..., description="Temporalidad")
    keltner_ema: Optional[float] = Field(None, description="Valor actual de la EMA de Keltner")
    keltner_upper: Optional[float] = Field(None, description="Banda superior de Keltner")
    keltner_lower: Optional[float] = Field(None, description="Banda inferior de Keltner")
    macd_line: Optional[float] = Field(None, description="Valor actual de la línea MACD")
    macd_signal: Optional[float] = Field(None, description="Valor actual de la signal line")
    macd_histogram: Optional[float] = Field(None, description="Valor actual del histograma MACD")
    pivot_highs: list[dict] = Field(default_factory=list, description="Pivotes altos recientes")
    pivot_lows: list[dict] = Field(default_factory=list, description="Pivotes bajos recientes")
    consolidation_zones: list[dict] = Field(default_factory=list, description="Zonas de consolidación")
