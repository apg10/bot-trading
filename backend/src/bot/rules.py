# Motor de evaluación de reglas para el bot de trading.

from __future__ import annotations

import json
from typing import Optional

from .models import (
    TradeRule,
    TradingStrategy,
    TradingSignal,
    AnalysisResult,
    SignalType,
    SignalStrength,
)


class RulesEngine:
    """Motor que evalúa reglas de trading basándose en análisis técnico."""

    def __init__(self, strategy: TradingStrategy):
        self.strategy = strategy
        self.active_rules = [r for r in strategy.rules if r.enabled]

    def evaluate(self, analysis: AnalysisResult) -> TradingSignal:
        """Evalúa las reglas activas y genera una señal de trading.

        Args:
            analysis: Resultado del análisis técnico

        Returns:
            TradingSignal con la decisión de trading
        """
        if not self.active_rules:
            return TradingSignal(
                timestamp_ms=self._now_ms(),
                symbol=analysis.symbol,
                signal_type=SignalType.HOLD,
                strength=SignalStrength.WEAK,
                confidence=0.1,
                reason="No hay reglas activas en la estrategia",
                rules_triggered=[],
            )

        total_weight = 0.0
        weighted_score = 0.0
        triggered_rules: list[str] = []

        for rule in self.active_rules:
            score = self._evaluate_rule(rule, analysis)
            if score != 0:
                weighted_score += score * rule.weight
                total_weight += rule.weight
                triggered_rules.append(rule.name)

        # Calcular fuerza y confianza de la señal
        if total_weight == 0:
            return TradingSignal(
                timestamp_ms=self._now_ms(),
                symbol=analysis.symbol,
                signal_type=SignalType.HOLD,
                strength=SignalStrength.WEAK,
                confidence=0.1,
                reason="Ninguna regla activada",
                rules_triggered=[],
            )

        normalized_score = weighted_score / total_weight

        # Determinar tipo de señal basado en el score normalizado
        if normalized_score > 0.3:
            signal_type = SignalType.BUY
        elif normalized_score < -0.3:
            signal_type = SignalType.SELL
        else:
            signal_type = SignalType.HOLD

        # Determinar fuerza basado en la magnitud del score
        abs_score = abs(normalized_score)
        if abs_score > 0.7:
            strength = SignalStrength.VERY_STRONG
        elif abs_score > 0.5:
            strength = SignalStrength.STRONG
        elif abs_score > 0.3:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK

        # Calcular confianza basada en el número de reglas activadas
        confidence = min(0.9, len(triggered_rules) / max(len(self.active_rules), 1))

        # Generar razón de la señal
        reason = self._generate_reason(signal_type, triggered_rules, analysis)

        # Calcular precios sugeridos
        entry_price = self._calculate_entry_price(analysis)
        stop_loss, take_profit = self._calculate_sl_tp(entry_price, signal_type)

        return TradingSignal(
            timestamp_ms=self._now_ms(),
            symbol=analysis.symbol,
            signal_type=signal_type,
            strength=strength,
            confidence=confidence,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            reason=reason,
            rules_triggered=triggered_rules,
        )

    def _evaluate_rule(self, rule: TradeRule, analysis: AnalysisResult) -> float:
        """Evalúa una regla individual y retorna un score (-1 a 1)."""
        # Evaluar reglas basadas en condiciones específicas
        if "macd" in rule.condition.lower():
            return self._evaluate_macd_rule(rule, analysis)
        elif "keltner" in rule.condition.lower():
            return self._evaluate_keltner_rule(rule, analysis)
        elif "pivot" in rule.condition.lower():
            return self._evaluate_pivot_rule(rule, analysis)
        elif "consolidation" in rule.condition.lower():
            return self._evaluate_consolidation_rule(rule, analysis)
        else:
            # Regla genérica: evaluar por nombre
            return self._evaluate_generic_rule(rule, analysis)

    def _evaluate_macd_rule(self, rule: TradeRule, analysis: AnalysisResult) -> float:
        """Evalúa reglas basadas en MACD."""
        if analysis.macd_histogram is None or analysis.macd_signal is None:
            return 0

        histogram = analysis.macd_histogram
        macd_line = analysis.macd_line or 0
        signal_line = analysis.macd_signal

        # Detectar cruces MACD
        if rule.condition == "macd_crossover_bullish":
            # MACD cruza por encima de la señal
            return 1.0 if histogram > 0 and macd_line > signal_line else -1.0
        elif rule.condition == "macd_crossover_bearish":
            # MACD cruza por debajo de la señal
            return -1.0 if histogram < 0 and macd_line < signal_line else 1.0
        elif rule.condition == "macd_histogram_increasing":
            # Histograma aumentando (momentum positivo)
            return 1.0 if histogram > 0 else -1.0
        else:
            return 0

    def _evaluate_keltner_rule(self, rule: TradeRule, analysis: AnalysisResult) -> float:
        """Evalúa reglas basadas en Keltner Channels."""
        if analysis.keltner_ema is None or analysis.keltner_upper is None or analysis.keltner_lower is None:
            return 0

        ema = analysis.keltner_ema
        upper = analysis.keltner_upper
        lower = analysis.keltner_lower

        # Nota: En una implementación real, necesitaríamos el precio actual del mercado
        # Aquí usamos un valor simulado basado en el EMA
        current_price = ema  # Simulación

        if rule.condition == "keltner_bounce_upper":
            # Rebote desde la banda superior (señal de venta)
            return -1.0 if abs(current_price - upper) < (upper - lower) * 0.1 else 0
        elif rule.condition == "keltner_bounce_lower":
            # Rebote desde la banda inferior (señal de compra)
            return 1.0 if abs(current_price - lower) < (upper - lower) * 0.1 else 0
        elif rule.condition == "keltner_breakout_upper":
            # Rompimiento hacia arriba (señal de compra)
            return 1.0 if current_price > upper else -1.0
        elif rule.condition == "keltner_breakout_lower":
            # Rompimiento hacia abajo (señal de venta)
            return -1.0 if current_price < lower else 1.0
        else:
            return 0

    def _evaluate_pivot_rule(self, rule: TradeRule, analysis: AnalysisResult) -> float:
        """Evalúa reglas basadas en pivotes."""
        highs = analysis.pivot_highs or []
        lows = analysis.pivot_lows or []

        if not highs and not lows:
            return 0

        # Evaluar basado en el último pivote
        if highs:
            last_high = highs[-1].get("price", 0)
            if rule.condition == "pivot_high_resistance":
                return -1.0  # Resistencia detectada
        if lows:
            last_low = lows[-1].get("price", 0)
            if rule.condition == "pivot_low_support":
                return 1.0  # Soporte detectado

        return 0

    def _evaluate_consolidation_rule(self, rule: TradeRule, analysis: AnalysisResult) -> float:
        """Evalúa reglas basadas en zonas de consolidación."""
        zones = analysis.consolidation_zones or []

        if not zones:
            return 0

        # Evaluar basado en la zona más reciente
        last_zone = zones[-1]
        contacts = last_zone.get("contacts", 0)

        if rule.condition == "consolidation_breakout":
            # Consolidación con muchos contactos sugiere breakout inminente
            return 1.0 if contacts >= 3 else -1.0
        elif rule.condition == "consolidation_range_bound":
            # Mantenerse dentro del rango de consolidación
            return 0.5  # Neutral, esperar dirección

        return 0

    def _evaluate_generic_rule(self, rule: TradeRule, analysis: AnalysisResult) -> float:
        """Evalúa reglas genéricas basadas en su nombre."""
        condition = rule.condition.lower()

        if "bullish" in condition or "buy" in condition:
            return 1.0
        elif "bearish" in condition or "sell" in condition:
            return -1.0
        elif "neutral" in condition or "hold" in condition:
            return 0

        return 0

    def _generate_reason(self, signal_type: SignalType, triggered_rules: list[str], analysis: AnalysisResult) -> str:
        """Genera una razón descriptiva para la señal."""
        rules_text = ", ".join(triggered_rules) if triggered_rules else "Ninguna"

        reason_parts = [
            f"Señal {signal_type.value.upper()}",
            f"Reglas activadas: [{rules_text}]",
        ]

        if analysis.macd_histogram is not None:
            direction = "positivo" if analysis.macd_histogram > 0 else "negativo"
            reason_parts.append(f"Histograma MACD {direction}")

        if analysis.keltner_ema is not None:
            reason_parts.append(f"EMA Keltner en ${analysis.keltner_ema:,.2f}")

        return " · ".join(reason_parts)

    def _calculate_entry_price(self, analysis: AnalysisResult) -> Optional[float]:
        """Calcula el precio de entrada sugerido."""
        # Usar el EMA de Keltner como precio de entrada base
        if analysis.keltner_ema is not None:
            return analysis.keltner_ema
        return None

    def _calculate_sl_tp(self, entry_price: Optional[float], signal_type: SignalType) -> tuple[Optional[float], Optional[float]]:
        """Calcula precios de stop loss y take profit."""
        if entry_price is None:
            return None, None

        stop_loss_pct = self.strategy.stop_loss_pct
        take_profit_pct = self.strategy.take_profit_pct

        if signal_type == SignalType.BUY:
            stop_loss = entry_price * (1 - stop_loss_pct)
            take_profit = entry_price * (1 + take_profit_pct)
        else:  # SELL
            stop_loss = entry_price * (1 + stop_loss_pct)
            take_profit = entry_price * (1 - take_profit_pct)

        return round(stop_loss, 2), round(take_profit, 2)

    @staticmethod
    def _now_ms() -> int:
        """Retorna el timestamp actual en milisegundos."""
        from datetime import datetime
        return int(datetime.now().timestamp() * 1000)


def create_default_strategy() -> TradingStrategy:
    """Crea una estrategia de trading por defecto con reglas comunes."""
    rules = [
        TradeRule(
            id="macd_crossover",
            name="MACD Crossover",
            condition="macd_crossover_bullish",
            weight=0.3,
        ),
        TradeRule(
            id="keltner_bounce",
            name="Keltner Bounce",
            condition="keltner_bounce_lower",
            weight=0.25,
        ),
        TradeRule(
            id="pivot_support",
            name="Pivot Support",
            condition="pivot_low_support",
            weight=0.2,
        ),
        TradeRule(
            id="consolidation_breakout",
            name="Consolidation Breakout",
            condition="consolidation_breakout",
            weight=0.15,
        ),
        TradeRule(
            id="macd_histogram",
            name="MACD Histogram Momentum",
            condition="macd_histogram_increasing",
            weight=0.1,
        ),
    ]

    return TradingStrategy(
        name="Default Strategy",
        description="Estrategia por defecto con reglas MACD, Keltner y pivotes",
        rules=rules,
        max_position_size=1.0,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
    )
