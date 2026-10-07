// C10: contrato del almacén puro y del hook real; todo el harness es local.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MAX_CANDLES, mergeHistoricalCandles, upsertCandle } from "../src/market/candle_store"
import { useMarketData } from "../src/hooks/useMarketData"
import type { CandleData, CandleHistoryResponse, MarketCandleEvent, MarketStatus } from "../src/types/market"

function candle(open_time = 1000, patch: Partial<CandleData> = {}): CandleData {
  return {
    open_time, close_time: open_time + 500,
    open: 100, high: 120, low: 90, close: 110,
    volume: 10, is_closed: false,
    ...patch,
  }
}

function frozenCandles(...rows: CandleData[]): readonly CandleData[] {
  return Object.freeze(rows.map(row => Object.freeze({ ...row })))
}

// Referencia independiente SOLO para claves/cardinalidad/orden. No reproduce
// reglas OHLCV, resolución de cierres ni invoca los helpers de producción.
function latestKeys(rows: readonly CandleData[], limit = 200): number[] {
  const keys = new Map<number, true>()
  for (const row of rows) keys.set(row.open_time, true)
  return [...keys.keys()].sort((a, b) => a - b).slice(-limit)
}

function expectKeys(actual: readonly CandleData[], input: readonly CandleData[], limit = 200): void {
  const expected = latestKeys(input, limit)
  expect(actual.map(row => row.open_time)).toEqual(expected)
  expect(actual).toHaveLength(expected.length)
  expect(new Set(actual.map(row => row.open_time)).size).toBe(actual.length)
  expect(actual.length).toBeLessThanOrEqual(limit)
}

describe("C10 — upsertCandle", () => {
  it("expone MAX_CANDLES=200 e inserta una vela sin alterar timestamps ni rellenar huecos", () => {
    expect(MAX_CANDLES).toBe(200)
    const first = candle(123, { close_time: 456 })
    const distant = candle(987654321, { close_time: 987654999 })
    const current = frozenCandles(first)
    const result = upsertCandle(current, Object.freeze(distant))
    expect(result).toEqual([first, distant])
    expect(result).not.toBe(current)
    expect(current).toEqual([first])
    expect(upsertCandle([], first)).toEqual([first])
  })

  it("reemplaza TODO el snapshot provisional: OHLCV acumulado 10 -> 15 -> 20, sin sumar", () => {
    const first = candle()
    const update = candle(1000, {
      open: 101, high: 125, low: 85, close: 115, close_time: 1600, volume: 15,
    })
    const closed = candle(1000, {
      open: 102, high: 130, low: 80, close: 118, close_time: 1700,
      volume: 20, is_closed: true,
    })
    const initial = upsertCandle([], first)
    const intermediate = upsertCandle(initial, update)
    const final = upsertCandle(intermediate, closed)
    expect(initial).toEqual([first])
    expect(intermediate).toEqual([update])
    expect(final).toEqual([closed])
    expect([initial[0].volume, intermediate[0].volume, final[0].volume]).toEqual([10, 15, 20])
  })

  it("usa el último snapshot recibido, incluso si su volumen es menor", () => {
    const older = candle(1000, { high: 140, low: 75, close: 130, volume: 25 })
    const latest = candle(1000, { high: 115, low: 95, close: 105, volume: 15 })
    expect(upsertCandle(frozenCandles(older), latest)).toEqual([latest])
  })

  it("updated -> closed -> new preserva el cierre al insertar otra clave", () => {
    const update = candle(1000, { volume: 15 })
    const closed = candle(1000, { close: 115, volume: 20, is_closed: true })
    const next = candle(2000, { volume: 3 })
    const result = upsertCandle(upsertCandle(upsertCandle([], update), closed), next)
    expect(result).toEqual([closed, next])
  })

  it("duplicados exactos provisionales y cerrados son idempotentes, sin nuevas filas", () => {
    for (const is_closed of [false, true]) {
      const row = candle(1000, { is_closed })
      let result = upsertCandle([], row)
      for (let i = 0; i < 20; i++) result = upsertCandle(result, { ...row })
      expect(result).toEqual([row])
    }
  })

  it("un cierre no retrocede a provisional aunque llegue OHLCV diferente", () => {
    const closed = candle(1000, { close: 118, volume: 20, is_closed: true })
    const stale = candle(1000, { high: 115, low: 95, close: 105, volume: 15 })
    const current = frozenCandles(closed, candle(2000))
    expect(() => upsertCandle(current, stale)).not.toThrow()
    expect(upsertCandle(current, stale)).toEqual(current)
    expect(current[0]).toEqual(closed)
  })

  it.each([
    ["open", { open: 101 }],
    ["high", { high: 121 }],
    ["low", { low: 89 }],
    ["close", { close: 111 }],
    ["volume", { volume: 11 }],
    ["close_time", { close_time: 1600 }],
  ] satisfies [string, Partial<CandleData>][])(
    "dos cierres confirmados que difieren en %s lanzan Error sin elegir una versión",
    (_field, patch) => {
      const closed = candle(1000, { is_closed: true })
      const current = frozenCandles(candle(500), closed, candle(2000))
      const before = current.map(row => ({ ...row }))
      const conflict = Object.freeze({ ...closed, ...patch })
      expect(() => upsertCandle(current, conflict)).toThrow(Error)
      expect(current).toEqual(before)
      expect(conflict).toEqual({ ...closed, ...patch })
      expect(upsertCandle(current, { ...closed })).toEqual(before)
    },
  )

  it("inserta eventos antiguos fuera de orden y actualiza una clave interior", () => {
    const rows = [candle(5000), candle(1000), candle(3000), candle(2000), candle(4000)]
    const seen: CandleData[] = []
    let result: CandleData[] = []
    for (const row of rows) {
      seen.push(row)
      result = upsertCandle(result, row)
      expectKeys(result, seen)
    }
    const interior = candle(2000, { close: 117, volume: 19, is_closed: true })
    result = upsertCandle(result, interior)
    expectKeys(result, rows)
    expect(result).toEqual([rows[1], interior, rows[2], rows[4], rows[0]])
  })

  it("normaliza current desordenado y elimina duplicados exactos", () => {
    const first = candle(1000)
    const last = candle(3000)
    const current = frozenCandles(last, first, { ...first })
    const incoming = candle(2000)
    expect(upsertCandle(current, incoming)).toEqual([first, incoming, last])
    expect(current).toEqual([last, first, first])
  })

  it("eventos dentro de últimas N se insertan; eventos demasiado antiguos no desplazan las últimas", () => {
    const current = frozenCandles(candle(2000), candle(4000), candle(5000))
    const inside = candle(3000)
    expect(upsertCandle(current, inside, 3)).toEqual([inside, current[1], current[2]])
    expect(upsertCandle(current, candle(1000), 3)).toEqual(current)
    expect(upsertCandle(current, candle(6000), 3)).toEqual([current[1], current[2], candle(6000)])
  })

  it("miles de eventos deterministas mantienen max200, orden y cardinalidad según Map+sort", () => {
    const seen: CandleData[] = []
    let current: CandleData[] = []
    // Permutación determinista de 1201 claves, con más de una pasada y sin cierres conflictivos.
    for (let i = 0; i < 3603; i++) {
      const row = candle(((i * 337) % 1201) * 1000, { volume: i })
      seen.push(row)
      const previous = current
      const snapshot = previous.map(value => ({ ...value }))
      current = upsertCandle(previous, row)
      expectKeys(current, seen)
      expect(previous).toEqual(snapshot)
    }
    expect(current).toHaveLength(200)
    expect(current[0].open_time).toBe(1001000)
    expect(current[199].open_time).toBe(1200000)
  })

  it("no muta arrays ni objetos congelados al reemplazar, insertar o cerrar", () => {
    const original = frozenCandles(candle(1000), candle(3000))
    const update = Object.freeze(candle(1000, { close: 119, volume: 15 }))
    const replaced = upsertCandle(original, update)
    const closed = Object.freeze(candle(1000, { close: 119, volume: 20, is_closed: true }))
    const finalized = upsertCandle(Object.freeze(replaced), closed)
    const inserted = upsertCandle(Object.freeze(finalized), Object.freeze(candle(2000)))
    expect(original).toEqual([candle(1000), candle(3000)])
    expect(replaced).toEqual([update, candle(3000)])
    expect(finalized).toEqual([closed, candle(3000)])
    expect(inserted).toEqual([closed, candle(2000), candle(3000)])
    expect(update.volume).toBe(15)
    expect(closed.volume).toBe(20)
  })
})

describe("C10 — mergeHistoricalCandles", () => {
  it("normaliza/sort/dedup histórico, conservando cerrado frente a provisional en ambos órdenes", () => {
    const provisional = candle(1000, { volume: 15 })
    const closed = candle(1000, { close: 118, volume: 20, is_closed: true })
    const earlier = candle(100, { close_time: 200, is_closed: true })
    const later = candle(2000, { is_closed: true })
    for (const duplicates of [[provisional, closed], [closed, provisional]]) {
      const historical = frozenCandles(later, ...duplicates, earlier, { ...closed })
      expect(mergeHistoricalCandles([], historical)).toEqual([earlier, closed, later])
      expect(historical).toEqual([later, ...duplicates, earlier, closed])
    }
  })

  it.each([
    ["open", { open: 101 }],
    ["high", { high: 121 }],
    ["low", { low: 89 }],
    ["close", { close: 111 }],
    ["volume", { volume: 11 }],
    ["close_time", { close_time: 1600 }],
  ] satisfies [string, Partial<CandleData>][])(
    "cierres conflictivos en un lote histórico (%s) fallan atómicamente en ambos órdenes",
    (_field, patch) => {
      const closed = candle(1000, { is_closed: true })
      const conflict = { ...closed, ...patch }
      const current = frozenCandles(candle(3000))
      for (const duplicates of [[closed, conflict], [conflict, closed]]) {
        const historical = frozenCandles(candle(500), ...duplicates, candle(2000))
        const before = historical.map(row => ({ ...row }))
        expect(() => mergeHistoricalCandles(current, historical)).toThrow(Error)
        expect(current).toEqual([candle(3000)])
        expect(historical).toEqual(before)
      }
    },
  )

  it.each([false, true])(
    "HTTP tardío nunca sustituye OHLCV actual, con current.is_closed=%s y HTTP cerrado",
    is_closed => {
      const live = candle(1000, {
        open: 103, high: 140, low: 80, close: 130, volume: 25, is_closed,
      })
      const stale = candle(1000, { close: 105, volume: 10, is_closed: true })
      const missing = candle(500, { close_time: 777, is_closed: true })
      const current = frozenCandles(live, candle(2000))
      const historical = frozenCandles(stale, missing)
      const result = mergeHistoricalCandles(current, historical)
      expect(result).toEqual([missing, live, current[1]])
      expect(current).toEqual([live, candle(2000)])
      expect(historical).toEqual([stale, missing])
    },
  )

  it("current cerrado prevalece también sobre provisional histórico y merge repetido es idempotente", () => {
    const live = candle(1000, { volume: 20, is_closed: true })
    const historical = frozenCandles(candle(1000, { volume: 15 }), candle(500))
    const result = mergeHistoricalCandles(frozenCandles(live), historical)
    expect(result).toEqual([candle(500), live])
    expect(mergeHistoricalCandles(Object.freeze(result), historical)).toEqual(result)
  })

  it("valida conflictos internos del lote incluso si current tiene la clave o el recorte la excluiría", () => {
    const first = candle(1000, { is_closed: true })
    const conflict = candle(1000, { close: 115, is_closed: true })
    const current = frozenCandles(first, candle(5000))
    expect(() => mergeHistoricalCandles(current, frozenCandles(first, conflict), 1)).toThrow(Error)
    expect(current).toEqual([first, candle(5000)])
  })

  it("sort y recorte son globales: history puede aportar claves anteriores, interiores y posteriores", () => {
    const current = frozenCandles(candle(4000), candle(2000))
    const historical = frozenCandles(candle(5000), candle(1000), candle(3000), candle(6000))
    const result = mergeHistoricalCandles(current, historical, 4)
    expectKeys(result, [...current, ...historical], 4)
    expect(result).toEqual([candle(3000), candle(4000), candle(5000), candle(6000)])
  })

  it("miles de filas históricas y duplicados no superan200 ni inventan timestamps", () => {
    const current = frozenCandles(candle(2500500), candle(1000, { volume: 99 }))
    const historical: CandleData[] = []
    for (let i = 2500; i >= 0; i--) {
      const row = candle(i * 1000, { close_time: i * 1000 + 123, is_closed: true })
      historical.push(row, { ...row })
    }
    const frozen = frozenCandles(...historical)
    const result = mergeHistoricalCandles(current, frozen)
    expectKeys(result, [...current, ...historical])
    expect(result).toHaveLength(200)
    expect(result[result.length - 1]).toEqual(current[0])
    expect(result[0].open_time).toBe(2302000)
    expect(result[0].close_time).toBe(2302123)
    expect(frozen).toEqual(historical)
  })

  it("entradas vacías son válidas y current desordenado se normaliza sin mutarlo", () => {
    const current = frozenCandles(candle(2000), candle(1000), candle(1000))
    expect(mergeHistoricalCandles([], [])).toEqual([])
    expect(mergeHistoricalCandles(current, Object.freeze([]))).toEqual([candle(1000), candle(2000)])
    expect(current).toEqual([candle(2000), candle(1000), candle(1000)])
  })
})

const invalidCandles: [string, unknown][] = [
  ["null", null], ["undefined", undefined], ["array", []], ["string", "candle"],
  ["number", 1], ["boolean", true], ["objeto vacío", {}],
  ...(["open_time", "close_time", "open", "high", "low", "close", "volume", "is_closed"] as const)
    .map(field => {
      const value: Partial<CandleData> = candle()
      delete value[field]
      return [`falta ${field}`, value] as [string, unknown]
    }),
  ["open_time negativo", candle(-1)],
  ["open_time fraccionario", candle(1.5)],
  ["open_time unsafe", candle(Number.MAX_SAFE_INTEGER + 1)],
  ["open_time NaN", candle(NaN)],
  ["open_time infinito", candle(Infinity)],
  ["open_time string", { ...candle(), open_time: "1000" }],
  ["open_time null", { ...candle(), open_time: null }],
  ["open_time boolean", { ...candle(), open_time: false }],
  ["close_time anterior", candle(1000, { close_time: 999 })],
  ["close_time fraccionario", candle(1000, { close_time: 1500.5 })],
  ["close_time unsafe", candle(1000, { close_time: Number.MAX_SAFE_INTEGER + 1 })],
  ["close_time NaN", candle(1000, { close_time: NaN })],
  ["close_time infinito", candle(1000, { close_time: Infinity })],
  ["close_time string", { ...candle(), close_time: "1500" }],
  ["close_time null", { ...candle(0), close_time: null }],
  ["close_time boolean", { ...candle(0), close_time: true }],
  ...(["open", "high", "low", "close"] as const).flatMap(field =>
    [0, -1, NaN, Infinity, -Infinity, "100", null, true].map(value =>
      [`${field}=${String(value)}`, { ...candle(), [field]: value }] as [string, unknown])),
  ["volume negativo", candle(1000, { volume: -1 })],
  ["volume NaN", candle(1000, { volume: NaN })],
  ["volume infinito", candle(1000, { volume: Infinity })],
  ["volume -infinito", candle(1000, { volume: -Infinity })],
  ["volume string", { ...candle(), volume: "10" }],
  ["volume null", { ...candle(), volume: null }],
  ["volume boolean", { ...candle(), volume: true }],
  ["is_closed numérico", { ...candle(), is_closed: 1 }],
  ["is_closed string", { ...candle(), is_closed: "false" }],
  ["is_closed null", { ...candle(), is_closed: null }],
  ["open por debajo de low", candle(1000, { open: 89 })],
  ["open por encima de high", candle(1000, { open: 121 })],
  ["close por debajo de low", candle(1000, { close: 89 })],
  ["close por encima de high", candle(1000, { close: 121 })],
  ["high menor que low", candle(1000, { high: 80 })],
]

describe("C10 — límites y validación compartida", () => {
  it.each([1, 2, 7, 200, 250])("límite entero positivo %i aplica a ambos helpers", limit => {
    const rows = Array.from({ length: 300 }, (_, index) => candle(index * 1000))
    const current = frozenCandles(...rows.slice(0, 299).reverse())
    expectKeys(upsertCandle(current, rows[299], limit), rows, limit)
    expectKeys(mergeHistoricalCandles(current, frozenCandles(rows[299]), limit), rows, limit)
  })

  it("omitir limit o pasar undefined usa200, también con current ya sobredimensionado", () => {
    const rows = Array.from({ length: 250 }, (_, index) => candle(index * 1000))
    const current = frozenCandles(...rows)
    expectKeys(upsertCandle(current, candle(300000)), [...rows, candle(300000)])
    expectKeys(upsertCandle(current, candle(300000), undefined), [...rows, candle(300000)])
    expectKeys(mergeHistoricalCandles(current, []), rows)
    expectKeys(mergeHistoricalCandles(current, [], undefined), rows)
  })

  it.each([0, -1, 1.5, NaN, Infinity, -Infinity, null, "2", true])(
    "límite inválido %s lanza Error en ambos helpers sin mutar entradas",
    invalid => {
      const current = frozenCandles(candle(1000))
      const historical = frozenCandles(candle(500))
      const incoming = Object.freeze(candle(2000))
      const limit = invalid as number
      expect(() => upsertCandle(current, incoming, limit)).toThrow(Error)
      expect(() => mergeHistoricalCandles(current, historical, limit)).toThrow(Error)
      expect(current).toEqual([candle(1000)])
      expect(historical).toEqual([candle(500)])
      expect(incoming).toEqual(candle(2000))
    },
  )

  it.each(invalidCandles)("CandleData inválido: %s se rechaza en incoming/current/historical", (_label, invalid) => {
    const current = frozenCandles(candle(2000))
    const before = current.map(row => ({ ...row }))
    const bad = invalid as CandleData
    const badRows = Object.freeze([bad])
    expect(() => upsertCandle(current, bad)).toThrow(Error)
    expect(() => upsertCandle(badRows, candle(3000))).toThrow(Error)
    expect(() => mergeHistoricalCandles(current, badRows)).toThrow(Error)
    expect(() => mergeHistoricalCandles(badRows, [])).toThrow(Error)
    expect(current).toEqual(before)
  })

  it.each([null, undefined, {}, "rows", 1, true])("colecciones no-array (%s) lanzan Error", invalid => {
    const rows = invalid as unknown as readonly CandleData[]
    expect(() => upsertCandle(rows, candle())).toThrow(Error)
    expect(() => mergeHistoricalCandles(rows, [])).toThrow(Error)
    expect(() => mergeHistoricalCandles([], rows)).toThrow(Error)
  })

  it("rechaza filas inválidas aunque sean antiguas, se recorten o coincidan con current", () => {
    const current = frozenCandles(candle(2000), candle(3000))
    const invalid = Object.freeze(candle(1000, { volume: -1 }))
    expect(() => upsertCandle(current, invalid, 1)).toThrow(Error)
    expect(() => upsertCandle(frozenCandles(invalid, ...current), candle(4000), 1)).toThrow(Error)
    expect(() => mergeHistoricalCandles(current, frozenCandles(invalid), 1)).toThrow(Error)
    expect(() => mergeHistoricalCandles(current, frozenCandles(candle(2000, { open: 0 })), 1)).toThrow(Error)
    expect(current).toEqual([candle(2000), candle(3000)])
  })

  it("acepta extremos válidos: tiempos safe, duración arbitraria/cero, volumen cero y OHLC iguales", () => {
    const rows = [
      candle(0, { close_time: 0, open: 1, high: 1, low: 1, close: 1, volume: 0 }),
      candle(100, { close_time: 200, open: 90, close: 120, volume: 0.125 }),
      candle(1000, { close_time: 1001 }),
      candle(Number.MAX_SAFE_INTEGER, { close_time: Number.MAX_SAFE_INTEGER, is_closed: true }),
    ]
    let result: CandleData[] = []
    for (const row of rows) result = upsertCandle(result, Object.freeze(row))
    expect(result).toEqual(rows)
    expect(mergeHistoricalCandles([], frozenCandles(...rows.slice().reverse()))).toEqual(rows)
  })
})

// Runner local de useState/useRef/useEffect, como C09: persiste refs y estado,
// respeta dependencias y ejecuta limpiezas; no requiere DOM ni otro archivo de tests.
const reactHooks = vi.hoisted(() => {
  const states: unknown[] = []
  const refs: { current: unknown }[] = []
  const effects: { deps?: readonly unknown[]; cleanup?: () => void }[] = []
  const pending: (() => void)[] = []
  let stateIndex = 0
  let refIndex = 0
  let effectIndex = 0
  return {
    useState<T>(initial: T | (() => T)) {
      const index = stateIndex++
      if (index >= states.length) {
        states[index] = typeof initial === "function" ? (initial as () => T)() : initial
      }
      const setState = (next: T | ((previous: T) => T)) => {
        states[index] = typeof next === "function"
          ? (next as (previous: T) => T)(states[index] as T)
          : next
      }
      return [states[index] as T, setState] as const
    },
    useRef<T>(initial: T) {
      const index = refIndex++
      if (index >= refs.length) refs[index] = { current: initial }
      return refs[index] as { current: T }
    },
    useEffect(effect: () => void | (() => void), deps?: readonly unknown[]) {
      const index = effectIndex++
      const previous = effects[index]
      const unchanged = previous && deps && previous.deps
        && deps.length === previous.deps.length
        && deps.every((value, i) => Object.is(value, previous.deps![i]))
      if (unchanged) return
      pending.push(() => {
        previous?.cleanup?.()
        const cleanup = effect()
        effects[index] = { deps, cleanup: typeof cleanup === "function" ? cleanup : undefined }
      })
    },
    render<T>(hook: () => T): T {
      stateIndex = refIndex = effectIndex = 0
      const result = hook()
      for (const run of pending.splice(0)) run()
      return result
    },
    unmount() {
      for (const effect of [...effects].reverse()) effect.cleanup?.()
      effects.length = 0
      pending.length = 0
    },
    reset() {
      states.length = refs.length = effects.length = pending.length = 0
      stateIndex = refIndex = effectIndex = 0
    },
  }
})

vi.mock("react", () => ({
  useState: reactHooks.useState,
  useRef: reactHooks.useRef,
  useEffect: reactHooks.useEffect,
}))

// Transporte falso; MarketWebSocket y sus callbacks onOpen/onClose/isOpen son reales.
class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []
  readyState = FakeWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  sentMessages: string[] = []

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    if (this.readyState !== FakeWebSocket.OPEN) throw new Error("socket not open")
    this.sentMessages.push(data)
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  message(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }

  close(): void {
    if (this.readyState === FakeWebSocket.CLOSED) return
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

function deferredPromise<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((fulfill, fail) => { resolve = fulfill; reject = fail })
  return { promise, resolve, reject }
}

function history(rows: CandleData[] = [], patch: Partial<CandleHistoryResponse> = {}): CandleHistoryResponse {
  return { symbol: "BTC/USDT", interval: "1m", data_source: "market_engine", candles: rows, ...patch }
}

function bar(row: CandleData, patch: Partial<MarketCandleEvent> = {}): MarketCandleEvent {
  return {
    type: row.is_closed ? "bar_closed" : "bar_updated",
    symbol: "BTC/USDT", interval: "1m", data_source: "market_engine", candle: row,
    ...patch,
  }
}

function status(patch: Partial<MarketStatus> = {}) {
  return {
    type: "status", symbol: "BTC/USDT", connected: true, reconnecting: false,
    closed_candles_count: 1, last_bar_open_time: 1000, interval: "1m",
    data_source: "market_engine", ...patch,
  }
}

function gap(patch: Record<string, unknown> = {}) {
  return {
    type: "gap", symbol: "BTC/USDT", interval: "1m", data_source: "market_engine",
    gap_ms: 60000, previous_close_time: 1500, ...patch,
  }
}

describe("C10 — useMarketData real con React, HTTP y transporte controlados", () => {
  let http: ReturnType<typeof deferredPromise<CandleHistoryResponse>>

  function renderHook(symbol = "BTC/USDT") {
    reactHooks.render(() => useMarketData(symbol))
    // El segundo render observa también setters ejecutados por los efectos.
    return reactHooks.render(() => useMarketData(symbol))
  }

  function socket(): FakeWebSocket {
    const current = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    expect(current).toBeDefined()
    return current!
  }

  async function resolveHistory(response = history(), symbol = "BTC/USDT") {
    http.resolve(response)
    await vi.advanceTimersByTimeAsync(0)
    return renderHook(symbol)
  }

  async function mountLive() {
    renderHook()
    await resolveHistory()
    socket().open()
    socket().message(bar(candle()))
    socket().message(status())
    const result = renderHook()
    expect(result.degraded).toBe(false)
    expect(result.error).toBeNull()
    expect(result.wsConnected).toBe(true)
    return result
  }

  function expectDegraded(result: ReturnType<typeof useMarketData>): void {
    expect(result.degraded).toBe(true)
    expect(typeof result.error).toBe("string")
    expect(result.error?.length).toBeGreaterThan(0)
    expect(result.wsConnected).toBe(false)
  }

  beforeEach(() => {
    vi.useFakeTimers()
    reactHooks.reset()
    FakeWebSocket.instances = []
    http = deferredPromise<CandleHistoryResponse>()
    vi.stubGlobal("WebSocket", FakeWebSocket)
    vi.stubGlobal("window", { location: { origin: "http://localhost" } })
    vi.stubEnv("VITE_API_URL", "")
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: () => http.promise })))
    vi.spyOn(console, "warn").mockImplementation(() => {})
    vi.spyOn(console, "error").mockImplementation(() => {})
    vi.spyOn(console, "log").mockImplementation(() => {})
  })

  afterEach(() => {
    try {
      reactHooks.unmount()
    } finally {
      try {
        for (const instance of FakeWebSocket.instances) instance.close()
      } finally {
        vi.clearAllTimers()
        vi.restoreAllMocks()
        vi.useRealTimers()
        vi.unstubAllGlobals()
        vi.unstubAllEnvs()
        reactHooks.reset()
        FakeWebSocket.instances = []
      }
    }
  })

  it("updated -> closed -> new conserva OHLCV/volumen y sincroniza ref para eventos posteriores sin render intermedio", async () => {
    renderHook()
    await resolveHistory()
    const ws = socket()
    ws.open()
    const first = candle(1000, { volume: 10 })
    const update = candle(1000, { high: 125, close: 115, volume: 15 })
    const closed = candle(1000, { high: 130, close: 118, volume: 20, is_closed: true })
    const next = candle(2000, { volume: 3 })
    ws.message(bar(first))
    expect(renderHook().candles).toEqual([first])
    ws.message(bar(update))
    expect(renderHook().candles).toEqual([update])
    ws.message(bar(closed))
    ws.message(bar(next))
    expect(renderHook().candles).toEqual([closed, next])
    ws.message(bar(closed))
    ws.message(bar(update))
    ws.message(bar(candle(3000)))
    const final = renderHook()
    expect(final.candles).toEqual([closed, next, candle(3000)])
    expect(final.degraded).toBe(false)
    expect(final.error).toBeNull()
  })

  it.each([false, true])(
    "HTTP engine tardío no revierte WS (cerrado=%s), fusiona claves faltantes y persiste en la ref",
    async is_closed => {
      renderHook()
      socket().open()
      const live = candle(1000, { high: 140, close: 130, volume: 20, is_closed })
      const next = candle(3000, { volume: 5 })
      socket().message(bar(live))
      socket().message(bar(next))
      socket().message(status())
      expect(renderHook().loading).toBe(true)
      const stale = candle(1000, { close: 105, volume: 10, is_closed: true })
      const missing = candle(500, { close_time: 777, is_closed: true })
      const inside = candle(2000, { is_closed: true })
      const merged = await resolveHistory(history([inside, stale, missing]))
      expect(merged.candles).toEqual([missing, live, inside, next])
      expect(merged.loading).toBe(false)
      expect(merged.dataSource).toBe("market_engine")
      expect(merged.interval).toBe("1m")
      expect(merged.error).toBeNull()
      expect(merged.degraded).toBe(false)
      expect(merged.wsConnected).toBe(true)
      socket().message(bar(candle(4000)))
      expect(renderHook().candles).toEqual([missing, live, inside, next, candle(4000)])
    },
  )

  it("cierre WS conflictivo conserva la versión previa y marca degraded/error sin romper barras posteriores", async () => {
    await mountLive()
    const closed = candle(1000, { close: 115, volume: 20, is_closed: true })
    socket().message(bar(closed))
    expect(renderHook().candles).toEqual([closed])
    socket().message(bar(candle(1000, { close: 118, volume: 21, is_closed: true })))
    const failed = renderHook()
    expectDegraded(failed)
    expect(failed.candles).toEqual([closed])
    socket().message(bar(candle(2000)))
    socket().message(status())
    const later = renderHook()
    expectDegraded(later)
    expect(later.candles).toEqual([closed, candle(2000)])
  })

  it.each([
    ["OHLC inválido", candle(1000, { high: 105 })],
    ["timestamp fraccionario", candle(1000.5)],
    ["volumen negativo", candle(1000, { volume: -1 })],
    ["candle ausente", undefined],
  ] satisfies [string, CandleData | undefined][])(
    "validación WS (%s) falla atómicamente y queda degradado con error",
    async (_label, invalid) => {
      const before = await mountLive()
      socket().message({ ...bar(candle()), candle: invalid })
      const result = renderHook()
      expectDegraded(result)
      expect(result.candles).toEqual(before.candles)
      socket().message(bar(candle(2000)))
      socket().message(status())
      expectDegraded(renderHook())
      expect(renderHook().candles).toEqual([candle(), candle(2000)])
    },
  )

  it.each(["validación", "conflicto"] as const)(
    "histórico HTTP con fallo de %s no aplica parcialmente y marca degraded/error",
    async failure => {
      renderHook()
      socket().open()
      const live = candle(3000)
      socket().message(bar(live))
      socket().message(status())
      const closed = candle(1000, { is_closed: true })
      const bad = failure === "validación"
        ? candle(2000, { volume: -1 })
        : candle(1000, { volume: 15, is_closed: true })
      const result = await resolveHistory(history([candle(500), closed, bad]))
      expectDegraded(result)
      expect(result.loading).toBe(false)
      expect(result.candles).toEqual([live])
      socket().message(bar(candle(4000)))
      socket().message(status())
      expect(renderHook().candles).toEqual([live, candle(4000)])
      expectDegraded(renderHook())
    },
  )

  it("gap válido deja degraded/error y wsConnected=false pese a barras, status y reconexión", async () => {
    await mountLive()
    const first = socket()
    first.message(gap())
    const degraded = renderHook()
    expectDegraded(degraded)
    expect(degraded.candles).toEqual([candle()])
    first.message(bar(candle(2000)))
    first.message(status())
    expectDegraded(renderHook())
    expect(renderHook().candles).toEqual([candle(), candle(2000)])
    first.close()
    expectDegraded(renderHook())
    vi.advanceTimersByTime(1000)
    const second = socket()
    expect(second).not.toBe(first)
    second.open()
    second.message(status())
    second.message(bar(candle(3000)))
    const recoveredTransport = renderHook()
    expectDegraded(recoveredTransport)
    expect(recoveredTransport.candles).toEqual([candle(), candle(2000), candle(3000)])
    expect(recoveredTransport.dataSource).toBe("market_engine")
    expect(recoveredTransport.interval).toBe("1m")
  })

  it("HTTP pendiente que acaba después del gap tampoco libera el latch", async () => {
    renderHook()
    socket().open()
    const live = candle(2000)
    socket().message(bar(live))
    socket().message(status())
    socket().message(gap())
    expectDegraded(renderHook())
    const result = await resolveHistory(history([candle(1000, { is_closed: true })]))
    expectDegraded(result)
    expect(result.candles).toEqual([candle(1000, { is_closed: true }), live])
    expect(result.loading).toBe(false)
  })

  it("otro símbolo se ignora para barras, gaps y status, incluso con CandleData inválido", async () => {
    const before = await mountLive()
    socket().message(bar(candle(2000), { symbol: "ETH/USDT" }))
    socket().message(bar(candle(1000, { volume: -1 }), { symbol: "ETH/USDT" }))
    socket().message(gap({ symbol: "ETH/USDT" }))
    socket().message(status({ symbol: "ETH/USDT", connected: false }))
    const result = renderHook()
    expect(result.candles).toEqual(before.candles)
    expect(result.status).toEqual(before.status)
    expect(result.interval).toBe("1m")
    expect(result.degraded).toBe(false)
    expect(result.error).toBeNull()
    expect(result.wsConnected).toBe(true)
  })

  it("gap de otro intervalo se ignora y no degrada un engine 1m establecido", async () => {
    const before = await mountLive()
    socket().message(gap({ interval: "5m" }))
    const result = renderHook()
    expect(result.candles).toEqual(before.candles)
    expect(result.degraded).toBe(false)
    expect(result.error).toBeNull()
    expect(result.wsConnected).toBe(true)
    expect(result.interval).toBe("1m")
  })

  it("bar de otro intervalo del mismo engine se rechaza sin mezclar y marca degraded/error", async () => {
    const before = await mountLive()
    socket().message(bar(candle(2000, { volume: 30 }), { interval: "5m" }))
    const rejected = renderHook()
    expectDegraded(rejected)
    expect(rejected.candles).toEqual(before.candles)
    expect(rejected.interval).toBe("1m")
    expect(rejected.dataSource).toBe("market_engine")
    socket().message(bar(candle(2000)))
    socket().message(status())
    expect(renderHook().candles).toEqual([candle(), candle(2000)])
    expectDegraded(renderHook())
  })

  it("HTTP engine tardío de otro intervalo no reemplaza ni mezcla el engine establecido", async () => {
    renderHook()
    socket().open()
    const live = candle(1000)
    socket().message(bar(live))
    socket().message(status())
    const result = await resolveHistory(history([candle(2000)], { interval: "5m" }))
    expectDegraded(result)
    expect(result.candles).toEqual([live])
    expect(result.interval).toBe("1m")
    socket().message(bar(candle(3000)))
    expect(renderHook().candles).toEqual([live, candle(3000)])
  })

  it("C08 permite TEST_ONLY -> market_engine y cambio de intervalo al cambiar el source", async () => {
    renderHook()
    const fixture = candle(100, { close_time: 200, is_closed: true })
    const initial = await resolveHistory(history([fixture], { data_source: "TEST_ONLY", interval: "5m" }))
    expect(initial.dataSource).toBe("TEST_ONLY")
    expect(initial.interval).toBe("5m")
    expect(initial.wsConnected).toBe(false)
    expect(initial.degraded).toBe(false)
    socket().open()
    socket().message(bar(candle(1000)))
    socket().message(status())
    const live = renderHook()
    expect(live.candles).toEqual([candle(1000)])
    expect(live.dataSource).toBe("market_engine")
    expect(live.interval).toBe("1m")
    expect(live.wsConnected).toBe(true)
    expect(live.degraded).toBe(false)
    expect(live.error).toBeNull()
  })

  it("TEST_ONLY HTTP tardío no contamina ni revierte velas WS del engine", async () => {
    renderHook()
    socket().open()
    const live = candle(1000, { close: 118, volume: 20, is_closed: true })
    socket().message(bar(live))
    socket().message(status())
    const result = await resolveHistory(history([candle(500), candle(1000)], {
      data_source: "TEST_ONLY", interval: "5m",
    }))
    expect(result.candles).toEqual([live])
    expect(result.dataSource).toBe("market_engine")
    expect(result.interval).toBe("1m")
    expect(result.degraded).toBe(false)
    expect(result.error).toBeNull()
    expect(result.wsConnected).toBe(true)
    socket().message(bar(candle(2000)))
    expect(renderHook().candles).toEqual([live, candle(2000)])
  })

  it("solo cambiar símbolo libera degraded/error; no arrastra velas ni callbacks del símbolo anterior", async () => {
    await mountLive()
    const old = socket()
    const staleMessage = old.onmessage!
    old.message(gap())
    expectDegraded(renderHook())
    http = deferredPromise<CandleHistoryResponse>()
    const changed = renderHook("ETH/USDT")
    expect(changed.degraded).toBe(false)
    expect(changed.error).toBeNull()
    expect(changed.candles).toEqual([])
    expect(changed.wsConnected).toBe(false)
    expect(old.readyState).toBe(FakeWebSocket.CLOSED)
    staleMessage({ data: JSON.stringify(bar(candle(9000))) })
    const missing = candle(500, { is_closed: true })
    await resolveHistory(history([missing], { symbol: "ETH/USDT" }), "ETH/USDT")
    const fresh = socket()
    expect(fresh).not.toBe(old)
    fresh.open()
    fresh.message(bar(candle(2000), { symbol: "ETH/USDT" }))
    fresh.message(status({ symbol: "ETH/USDT" }))
    const result = renderHook("ETH/USDT")
    expect(result.candles).toEqual([missing, candle(2000)])
    expect(result.degraded).toBe(false)
    expect(result.error).toBeNull()
    expect(result.wsConnected).toBe(true)
  })

  it("eventos fuera de orden y más de2000 barras mantienen max200 también en la ref del hook", async () => {
    renderHook()
    await resolveHistory()
    socket().open()
    const rows: CandleData[] = []
    for (let i = 0; i < 2101; i++) {
      const row = candle(((i * 337) % 2101) * 1000, { volume: i })
      rows.push(row)
      socket().message(bar(row))
      if (i % 137 === 0) expectKeys(renderHook().candles, rows)
    }
    expectKeys(renderHook().candles, rows)
    const retained = candle(2050000, { close: 115, volume: 20, is_closed: true })
    socket().message(bar(retained))
    socket().message(bar(candle(0)))
    const next = candle(2200000)
    socket().message(bar(next))
    const result = renderHook()
    expectKeys(result.candles, [...rows, retained, next])
    expect(result.candles.find(row => row.open_time === retained.open_time)).toEqual(retained)
    expect(result.candles[result.candles.length - 1]).toEqual(next)
    expect(result.degraded).toBe(false)
    expect(result.error).toBeNull()
  })
})
