// Transiciones puras del buffer. No calcula indicadores, fills ni precios faltantes.

import type { CandleData } from "../types/market"

export const MAX_CANDLES = 200

function validateLimit(limit: number): void {
  if (!Number.isSafeInteger(limit) || limit <= 0) {
    throw new Error("El límite de velas debe ser un entero positivo")
  }
}

function checkedCandle(value: unknown): CandleData {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Vela inválida: se requiere un snapshot OHLCV")
  }
  const candle = value as CandleData
  if (
    !Number.isSafeInteger(candle.open_time) || candle.open_time < 0
    || !Number.isSafeInteger(candle.close_time) || candle.close_time < candle.open_time
    || typeof candle.is_closed !== "boolean"
  ) {
    throw new Error("Vela inválida: timestamps o confirmación de cierre incorrectos")
  }
  const prices = [candle.open, candle.high, candle.low, candle.close]
  if (
    prices.some(price => typeof price !== "number" || !Number.isFinite(price) || price <= 0)
    || typeof candle.volume !== "number" || !Number.isFinite(candle.volume) || candle.volume < 0
    || candle.high < Math.max(candle.open, candle.close, candle.low)
    || candle.low > Math.min(candle.open, candle.close, candle.high)
  ) {
    throw new Error("Vela inválida: valores OHLCV no finitos o incoherentes")
  }
  // Copiar solamente campos del contrato; no compartir referencias de entrada.
  return {
    open_time: candle.open_time, close_time: candle.close_time,
    open: candle.open, high: candle.high, low: candle.low, close: candle.close,
    volume: candle.volume, is_closed: candle.is_closed,
  }
}

function sameCandle(a: CandleData, b: CandleData): boolean {
  return a.open_time === b.open_time && a.close_time === b.close_time
    && a.open === b.open && a.high === b.high && a.low === b.low
    && a.close === b.close && a.volume === b.volume && a.is_closed === b.is_closed
}

function insert(candles: Map<number, CandleData>, incoming: CandleData): void {
  const existing = candles.get(incoming.open_time)
  if (existing?.is_closed) {
    if (incoming.is_closed && !sameCandle(existing, incoming)) {
      // Sin revisión/event_time no elegir silenciosamente entre cierres distintos.
      throw new Error("Conflicto entre cierres confirmados de la misma vela")
    }
    return // Un cierre nunca vuelve a provisional; duplicados son idempotentes.
  }
  // Un snapshot sustituye OHLCV completo, incluido el volumen acumulado.
  candles.set(incoming.open_time, incoming)
}

function normalize(values: readonly CandleData[]): Map<number, CandleData> {
  if (!Array.isArray(values)) throw new Error("El histórico de velas debe ser una lista")
  const result = new Map<number, CandleData>()
  for (const value of values) insert(result, checkedCandle(value))
  return result
}

function latest(candles: Map<number, CandleData>, limit: number): CandleData[] {
  return [...candles.values()].sort((a, b) => a.open_time - b.open_time).slice(-limit)
}

/** Snapshot en orden de llegada: una fila por open_time, sin reabrir cierres.
 * No se infiere orden intrabar por una secuencia inexistente en el protocolo.
 */
export function upsertCandle(
  current: readonly CandleData[], incoming: CandleData, limit: number = MAX_CANDLES,
): CandleData[] {
  validateLimit(limit)
  const result = normalize(current)
  insert(result, checkedCandle(incoming))
  return latest(result, limit)
}

/** HTTP rellena claves ausentes; snapshots ya recibidos por WS tienen prioridad.
 * Incluso un cierre HTTP coincidente no reemplaza una revisión WS en formación:
 * no hay revisión de snapshot para demostrar que esa respuesta sea más reciente.
 * No fabricar un cierre combinando flags HTTP con precios provisionales WS.
 */
export function mergeHistoricalCandles(
  current: readonly CandleData[], historical: readonly CandleData[], limit: number = MAX_CANDLES,
): CandleData[] {
  validateLimit(limit)
  const result = normalize(historical)
  for (const [time, candle] of normalize(current)) result.set(time, candle)
  return latest(result, limit)
}
