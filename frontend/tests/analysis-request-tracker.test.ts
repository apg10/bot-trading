// C11: helpers y paneles reales. React/JSX, HTTP, elementos y chart son fakes
// locales; no importa otros tests ni necesita DOM, red o dependencias nuevas.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  AnalysisRequestTracker,
  createAnalysisSnapshotKey,
  type AnalysisSnapshotContext,
} from "../src/analysis/request_tracker"
import { AnalysisPanel } from "../src/components/AnalysisPanel"
import { ChartPanel } from "../src/components/ChartPanel"
import type { CandleData } from "../src/types/market"

type TestNode = {
  type: unknown
  props: Record<string, unknown> & { children?: unknown }
}

// Un solo componente raíz por caso. Los hijos funcionales se ejecutan al
// expandir JSX; un hook en ellos lanza, en vez de contaminar los slots del padre.
// Las refs se asignan en commit, ANTES de ejecutar los efectos, igual que React.
const reactHarness = vi.hoisted(() => {
  type Node = { type: unknown; props: Record<string, unknown> }
  type Element = {
    clientWidth: number
    clientHeight: number
    getBoundingClientRect: () => { width: number; height: number }
  }
  type Ref = { current: unknown } | ((element: unknown) => void)
  type Effect = { deps?: readonly unknown[]; cleanup?: () => void }
  const Fragment = Symbol("C11.Fragment")
  const states: { value: unknown; set: (next: unknown) => void }[] = []
  const refs: { current: unknown }[] = []
  const memos: { value: unknown; deps?: readonly unknown[] }[] = []
  const effects: Effect[] = []
  const pending = new Map<number, { effect: () => void | (() => void); deps?: readonly unknown[] }>()
  const elements = new Map<string, Element>()
  let attached = new Map<Ref, Element>()
  let stateIndex = 0
  let refIndex = 0
  let memoIndex = 0
  let effectIndex = 0
  let inChild = false
  let mounted = false
  let dirty = false
  let updatesAfterUnmount = 0
  let stateWrites = 0

  const sameDeps = (a?: readonly unknown[], b?: readonly unknown[]) =>
    !!a && !!b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]))
  const assertRoot = () => {
    if (inChild) throw new Error("El colector JSX solo admite hijos sin hooks")
  }
  const assignRef = (ref: Ref, element: Element | null) => {
    if (typeof ref === "function") ref(element)
    else ref.current = element
  }

  function expand(value: unknown, path: string, nextRefs: Map<Ref, Element>): unknown {
    if (Array.isArray(value)) return value.map((child, i) => expand(child, `${path}.${i}`, nextRefs))
    if (!value || typeof value !== "object" || !("type" in value)) return value
    const node = value as Node
    if (node.type === Fragment) return expand(node.props.children, `${path}.fragment`, nextRefs)
    if (typeof node.type === "function") {
      const previous = inChild
      inChild = true
      try {
        return expand(node.type(node.props), `${path}.child`, nextRefs)
      } finally {
        inChild = previous
      }
    }
    const ref = node.props.ref as Ref | undefined
    if (ref) {
      let element = elements.get(path)
      if (!element) {
        const created: Element = {
          clientWidth: 800,
          clientHeight: 400,
          getBoundingClientRect: () => ({ width: created.clientWidth, height: created.clientHeight }),
        }
        element = created
        elements.set(path, element)
      }
      nextRefs.set(ref, element)
    }
    return { ...node, props: { ...node.props, children: expand(node.props.children, `${path}.children`, nextRefs) } }
  }

  const api = {
    Fragment,
    jsx(type: unknown, props: Record<string, unknown> | null): Node {
      return { type, props: props ?? {} }
    },
    createElement(type: unknown, props: Record<string, unknown> | null, ...children: unknown[]): Node {
      return { type, props: { ...props, ...(children.length ? { children } : {}) } }
    },
    useState<T>(initial: T | (() => T)) {
      assertRoot()
      const index = stateIndex++
      if (index >= states.length) {
        states[index] = {
          value: typeof initial === "function" ? (initial as () => T)() : initial,
          set(next: unknown) {
            stateWrites++
            if (!mounted) updatesAfterUnmount++
            const previous = states[index].value
            const value = typeof next === "function" ? next(previous) : next
            if (!Object.is(previous, value)) {
              states[index].value = value
              dirty = true
            }
          },
        }
      }
      return [states[index].value as T, states[index].set as (next: T | ((previous: T) => T)) => void] as const
    },
    useRef<T>(initial: T) {
      assertRoot()
      const index = refIndex++
      if (index >= refs.length) refs[index] = { current: initial }
      return refs[index] as { current: T }
    },
    useMemo<T>(factory: () => T, deps?: readonly unknown[]): T {
      assertRoot()
      const index = memoIndex++
      if (!memos[index] || !sameDeps(memos[index].deps, deps)) memos[index] = { value: factory(), deps }
      return memos[index].value as T
    },
    useCallback<T extends (...args: never[]) => unknown>(callback: T, deps?: readonly unknown[]): T {
      return api.useMemo(() => callback, deps)
    },
    useEffect(effect: () => void | (() => void), deps?: readonly unknown[]) {
      assertRoot()
      const index = effectIndex++
      if (!effects[index] || !sameDeps(effects[index].deps, deps)) pending.set(index, { effect, deps })
      else pending.delete(index)
    },
    renderOnly(component: () => unknown): unknown {
      mounted = true
      dirty = false
      stateIndex = refIndex = memoIndex = effectIndex = 0
      const nextRefs = new Map<Ref, Element>()
      const result = expand(component(), "root", nextRefs)
      for (const [ref, element] of attached) if (nextRefs.get(ref) !== element) assignRef(ref, null)
      for (const [ref, element] of nextRefs) if (attached.get(ref) !== element) assignRef(ref, element)
      attached = nextRefs
      return result
    },
    flushEffects() {
      const work = [...pending.entries()]
      pending.clear()
      // React hace todos los cleanups antes de montar los nuevos efectos.
      for (const [index] of work) effects[index]?.cleanup?.()
      for (const [index, { effect, deps }] of work) {
        const cleanup = effect()
        effects[index] = { deps, cleanup: typeof cleanup === "function" ? cleanup : undefined }
      }
    },
    render(component: () => unknown): unknown {
      let tree: unknown
      for (let pass = 0; pass < 30; pass++) {
        tree = api.renderOnly(component)
        api.flushEffects()
        if (!dirty) return tree
      }
      throw new Error("Más de 30 renders: posible bucle de efectos")
    },
    get attachedElements() { return [...attached.values()] },
    get updatesAfterUnmount() { return updatesAfterUnmount },
    get stateWrites() { return stateWrites },
    unmount() {
      mounted = false
      pending.clear()
      for (const effect of [...effects].reverse()) effect?.cleanup?.()
      effects.length = 0
      for (const ref of attached.keys()) assignRef(ref, null)
      attached.clear()
    },
    reset() {
      states.length = refs.length = memos.length = effects.length = 0
      pending.clear()
      elements.clear()
      attached.clear()
      stateIndex = refIndex = memoIndex = effectIndex = 0
      mounted = dirty = inChild = false
      updatesAfterUnmount = 0
      stateWrites = 0
    },
  }
  return api
})

vi.mock("react", () => ({
  default: reactHarness,
  Fragment: reactHarness.Fragment,
  createElement: reactHarness.createElement,
  useState: reactHarness.useState,
  useRef: reactHarness.useRef,
  useMemo: reactHarness.useMemo,
  useCallback: reactHarness.useCallback,
  useEffect: reactHarness.useEffect,
  useLayoutEffect: reactHarness.useEffect,
}))
vi.mock("react/jsx-runtime", () => ({ jsx: reactHarness.jsx, jsxs: reactHarness.jsx, Fragment: reactHarness.Fragment }))
vi.mock("react/jsx-dev-runtime", () => ({ jsxDEV: reactHarness.jsx, Fragment: reactHarness.Fragment }))

const charts = vi.hoisted(() => {
  type Bar = { time: number; open: number; high: number; low: number; close: number }
  const instances: ReturnType<typeof makeChart>[] = []
  const CandlestickSeries = Symbol("C11.CandlestickSeries")
  function makeChart(container: unknown) {
    let removed = false
    let rows: Bar[] = []
    let range: { from: number; to: number } | null = null
    const operations: { kind: "setData" | "update"; data: Bar[] }[] = []
    const live = () => { if (removed) throw new Error("Trabajo sobre un gráfico eliminado") }
    const validate = (data: Bar[]) => {
      if (data.length > 200) throw new Error("La serie retiene más de 200 velas")
      for (let i = 0; i < data.length; i++) {
        if (i && data[i - 1].time >= data[i].time) throw new Error("Tiempos duplicados o fuera de orden")
      }
    }
    const series = {
      setData: vi.fn((data: Bar[]) => {
        live()
        validate(data)
        rows = data.map(row => ({ ...row }))
        operations.push({ kind: "setData", data: rows.map(row => ({ ...row })) })
      }),
      update: vi.fn((bar: Bar) => {
        live()
        const last = rows[rows.length - 1]
        if (last && bar.time < last.time) throw new Error("update no puede corregir una vela histórica")
        const next = rows.map(row => ({ ...row }))
        if (last?.time === bar.time) next[next.length - 1] = { ...bar }
        else next.push({ ...bar })
        validate(next)
        rows = next
        operations.push({ kind: "update", data: [{ ...bar }] })
      }),
    }
    const scale = {
      getVisibleLogicalRange: vi.fn(() => { live(); return range }),
      fitContent: vi.fn(() => { live(); range = { from: 0, to: Math.max(0, rows.length - 1) } }),
    }
    return {
      container,
      series,
      operations,
      get rows() { return rows },
      addSeries: vi.fn(() => { live(); return series }),
      timeScale: vi.fn(() => { live(); return scale }),
      applyOptions: vi.fn((_options: unknown) => { live() }),
      remove: vi.fn(() => {
        if (removed) throw new Error("remove duplicado")
        removed = true
      }),
    }
  }
  return {
    instances,
    CandlestickSeries,
    createChart: vi.fn((container: unknown, _options: unknown) => {
      const chart = makeChart(container)
      instances.push(chart)
      return chart
    }),
    reset() {
      instances.length = 0
      this.createChart.mockClear()
    },
  }
})

vi.mock("lightweight-charts", () => ({
  createChart: charts.createChart,
  CandlestickSeries: charts.CandlestickSeries,
  ColorType: { Solid: "solid" },
}))

class FakeResizeObserver {
  static instances: FakeResizeObserver[] = []
  observe = vi.fn((_element: unknown) => {})
  disconnect = vi.fn(() => {})

  constructor(private readonly callback: (entries: unknown[]) => void) {
    FakeResizeObserver.instances.push(this)
  }

  resize() {
    const target = this.observe.mock.calls[0]?.[0] as { clientWidth: number; clientHeight: number }
    this.callback([{ target, contentRect: { width: target.clientWidth, height: target.clientHeight } }])
  }
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((fulfill, fail) => { resolve = fulfill; reject = fail })
  return { promise, resolve, reject }
}

type HttpReply = { ok: boolean; status: number; json: () => Promise<unknown> }
type Request = {
  url: string
  init: RequestInit
  response: ReturnType<typeof deferred<HttpReply>>
  body: ReturnType<typeof deferred<unknown>>
}
let requests: Request[] = []

function candle(index = 0, patch: Partial<CandleData> = {}): CandleData {
  const open_time = 1_700_000_000_000 + index * 60_000
  return {
    open_time, close_time: open_time + 59_999,
    open: 100, high: 120, low: 90, close: 110, volume: 10, is_closed: true,
    ...patch,
  }
}

function candles(count = 50): CandleData[] {
  return Array.from({ length: count }, (_, i) => candle(i, { is_closed: i !== count - 1 }))
}

function changed(rows: readonly CandleData[], index: number, patch: Partial<CandleData>): CandleData[] {
  return rows.map((row, i) => ({ ...row, ...(i === index ? patch : {}) }))
}

const fields: [string, Partial<CandleData>][] = [
  ["open_time", { open_time: candle(49).open_time + 1000 }],
  ["close_time", { close_time: candle(49).close_time + 1000 }],
  ["open", { open: 101 }], ["high", { high: 121 }], ["low", { low: 89 }],
  ["close", { close: 111 }], ["volume", { volume: 11 }], ["is_closed", { is_closed: true }],
]
const context: AnalysisSnapshotContext = { symbol: "BTC/USDT", interval: "1m", dataSource: "market_engine" }

function interiorPatch(field: string, patch: Partial<CandleData>): Partial<CandleData> {
  if (field === "is_closed") return { is_closed: false }
  if (field === "open_time") return { open_time: candle(20).open_time + 1000 }
  if (field === "close_time") return { close_time: candle(20).close_time + 1000 }
  return patch
}

function text(value: unknown): string {
  if (value === null || value === undefined || typeof value === "boolean") return ""
  if (typeof value === "string" || typeof value === "number") return String(value)
  if (Array.isArray(value)) return value.map(text).filter(Boolean).join(" ")
  return typeof value === "object" && "props" in value ? text((value as TestNode).props.children) : ""
}

function nodes(value: unknown): TestNode[] {
  if (Array.isArray(value)) return value.flatMap(nodes)
  if (!value || typeof value !== "object" || !("props" in value)) return []
  const node = value as TestNode
  return [node, ...nodes(node.props.children)]
}

function expectError(tree: unknown): void {
  const alerts = nodes(tree).filter(node => node.props.role === "alert")
  const diagnostic = /error|fall[oó]|inv[aá]lid|incompatib|rechaz|HTTP|failed|mismatch|malformed/i
  // Un role vacío no basta. Sin role se exige un diagnóstico inequívoco.
  if (alerts.length) expect(alerts.map(text).join(" ").trim().length).toBeGreaterThan(0)
  else expect(text(tree)).toMatch(diagnostic)
}

function expectPending(tree: unknown): void {
  // No coincide con el banner estático «Reglas ... pendientes».
  expect(text(tree)).toMatch(/calculando|actualizando|esperando|cargando|caducad|obsolet|stale|calculating|updating|waiting/i)
}

function expectNotCurrent(tree: unknown, marker: string): void {
  const visible = text(tree)
  if (visible.includes(marker)) expect(visible).toMatch(/caducad|obsolet|stale|desactualiz/i)
  else expect(visible).not.toContain(marker)
}

type AnalysisProps = Parameters<typeof AnalysisPanel>[0]
type ChartProps = Parameters<typeof ChartPanel>[0]
let analysisProps: AnalysisProps
let chartProps: ChartProps

function renderAnalysis(patch: Partial<AnalysisProps> = {}) {
  analysisProps = { ...analysisProps, ...patch }
  return reactHarness.render(() => AnalysisPanel(analysisProps))
}

function renderChart(patch: Partial<ChartProps> = {}) {
  chartProps = { ...chartProps, ...patch }
  return reactHarness.render(() => ChartPanel(chartProps))
}

function response(marker = "C11_CURRENT", patch: Record<string, unknown> = {}) {
  return {
    symbol: analysisProps.symbol,
    timeframe: analysisProps.interval,
    data_source: "provided",
    keltner: { points: [{ time_ms: candle(49).open_time, ema: 105, upper: 120, lower: 90, atr: 5 }] },
    macd: { points: [
      { time_ms: candle(48).open_time, macd_line: -1, signal_line: 0, histogram: -1 },
      { time_ms: candle(49).open_time, macd_line: 1, signal_line: 0, histogram: 1 },
    ] },
    fyl: {
      pivots: [{ id: "p1", time_ms: candle(48).open_time, price: 120, pivot_type: "high", strength: 0.8 }],
      zones: [{ id: "z1", start_time_ms: candle(40).open_time, end_time_ms: candle(48).open_time,
        origin: "high", low: 90, high: 120, contacts: 3, status: "forming" }],
      annotations: [{ id: "a1", annotation_type: "test_marker", content: marker }],
    },
    ...patch,
  }
}

function request(index = requests.length - 1): Request {
  expect(requests[index]).toBeDefined()
  return requests[index]!
}

function signal(index = requests.length - 1): AbortSignal {
  const value = request(index).init.signal
  expect(value).toBeInstanceOf(AbortSignal)
  return value!
}

async function flushPromises() {
  await vi.advanceTimersByTimeAsync(0)
}

function deliver(index: number, data: unknown, status = 200): void {
  const current = request(index)
  // Ignora abort deliberadamente: la implementación debe además guardar tickets.
  current.response.resolve({ ok: status >= 200 && status < 300, status, json: () => current.body.promise })
  current.body.resolve(data)
}

async function complete(index: number, data: unknown = response(), status = 200) {
  deliver(index, data, status)
  await flushPromises()
  return renderAnalysis()
}

function chart() {
  expect(charts.instances).toHaveLength(1)
  return charts.instances[0]!
}

function bars(rows: readonly CandleData[]) {
  return rows.slice(-200).map(row => ({ time: row.open_time / 1000, open: row.open, high: row.high, low: row.low, close: row.close }))
}

function clearChartWork() {
  chart().series.setData.mockClear()
  chart().series.update.mockClear()
  chart().operations.length = 0
}

function expectNoChartWork() {
  expect(chart().series.setData).not.toHaveBeenCalled()
  expect(chart().series.update).not.toHaveBeenCalled()
}

beforeEach(() => {
  vi.useFakeTimers()
  reactHarness.reset()
  charts.reset()
  FakeResizeObserver.instances = []
  requests = []
  analysisProps = { ...context, candles: candles(), loading: false }
  chartProps = { ...analysisProps, wsConnected: false }
  vi.stubEnv("VITE_API_URL", "")
  vi.stubGlobal("window", { location: { origin: "http://localhost" } })
  vi.stubGlobal("ResizeObserver", FakeResizeObserver)
  vi.stubGlobal("fetch", vi.fn((url: string, init: RequestInit) => {
    const current: Request = { url, init, response: deferred<HttpReply>(), body: deferred<unknown>() }
    requests.push(current)
    return current.response.promise
  }))
})

afterEach(() => {
  try {
    reactHarness.unmount()
  } finally {
    vi.clearAllTimers()
    vi.restoreAllMocks()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    reactHarness.reset()
    charts.reset()
    FakeResizeObserver.instances = []
    requests = []
  }
})

describe("C11 — createAnalysisSnapshotKey puro", () => {
  it("JSON determinista, contexto y tuplas completas de las últimas200; no muta entradas", () => {
    const rows = Object.freeze(candles(250).map(row => Object.freeze({ ...row })))
    const input = Object.freeze({ ...context })
    const key = createAnalysisSnapshotKey(input, rows)
    const decoded: unknown = JSON.parse(key)
    const tuples: unknown[][] = []
    const leaves: unknown[] = []
    function visit(value: unknown): void {
      if (Array.isArray(value)) {
        if (value.length === 8 && value.every(item => typeof item !== "object")) tuples.push(value)
        else value.forEach(visit)
      } else if (value && typeof value === "object") Object.values(value).forEach(visit)
      else leaves.push(value)
    }
    visit(decoded)
    expect(tuples).toEqual(rows.slice(-200).map(row => [
      row.open_time, row.close_time, row.open, row.high, row.low, row.close, row.volume, row.is_closed,
    ]))
    expect(leaves).toEqual(expect.arrayContaining([context.symbol, context.interval, context.dataSource]))
    const clone = rows.map(row => ({ ...row }))
    expect(createAnalysisSnapshotKey({ ...context }, clone)).toBe(key)
    // Orden de propiedades ajeno al contrato: el mismo contexto y las mismas velas.
    expect(createAnalysisSnapshotKey({ dataSource: context.dataSource, interval: context.interval, symbol: context.symbol },
      clone.map(row => ({ is_closed: row.is_closed, volume: row.volume, close: row.close, low: row.low,
        high: row.high, open: row.open, close_time: row.close_time, open_time: row.open_time })))).toBe(key)
    expect(rows).toEqual(candles(250))
    expect(input).toEqual(context)
  })

  it.each(fields)("cambiar %s en la última vela cambia la clave aun con longitud constante", (_field, patch) => {
    const rows = candles()
    expect(createAnalysisSnapshotKey(context, changed(rows, 49, patch))).not.toBe(createAnalysisSnapshotKey(context, rows))
  })

  it.each(fields)("cambiar %s en una vela interior retenida cambia la clave", (field, patch) => {
    const rows = candles()
    expect(createAnalysisSnapshotKey(context, changed(rows, 20, interiorPatch(field, patch)))).not.toBe(createAnalysisSnapshotKey(context, rows))
  })

  it.each([
    ["symbol", { symbol: "ETH/USDT" }], ["interval", { interval: "5m" }],
    ["interval null", { interval: null }], ["source TEST_ONLY", { dataSource: "TEST_ONLY" }],
    ["source unknown", { dataSource: "unknown" }],
  ] satisfies [string, Partial<AnalysisSnapshotContext>][])('cambiar contexto (%s) cambia la clave', (_label, patch) => {
    const rows = candles()
    expect(createAnalysisSnapshotKey({ ...context, ...patch }, rows)).not.toBe(createAnalysisSnapshotKey(context, rows))
  })

  it("el recorte incluye la frontera200 y excluye TODOS los campos anteriores", () => {
    const rows = candles(250)
    const key = createAnalysisSnapshotKey(context, rows)
    const outside = changed(rows, 49, { open_time: 123, close_time: 456, open: 10, high: 30, low: 5, close: 20,
      volume: 999, is_closed: false })
    expect(createAnalysisSnapshotKey(context, outside)).toBe(key)
    expect(createAnalysisSnapshotKey(context, rows.slice(50))).toBe(key)
    expect(createAnalysisSnapshotKey(context, changed(rows, 50, { volume: 999 }))).not.toBe(key)
  })

  it("vacío es estable, pero añadir o reducir el snapshot modifica la clave", () => {
    expect(createAnalysisSnapshotKey(context, [])).toBe(createAnalysisSnapshotKey({ ...context }, []))
    const rows = candles()
    expect(createAnalysisSnapshotKey(context, [])).not.toBe(createAnalysisSnapshotKey(context, rows))
    expect(createAnalysisSnapshotKey(context, rows.slice(1))).not.toBe(createAnalysisSnapshotKey(context, rows))
    expect(createAnalysisSnapshotKey(context, [...rows, candle(50)])).not.toBe(createAnalysisSnapshotKey(context, rows))
  })
})

describe("C11 — AnalysisRequestTracker puro", () => {
  it("begin entrega ticket con clave/señal, aborta anterior e incrementa id incluso con misma clave", () => {
    const tracker = new AnalysisRequestTracker()
    const first = tracker.begin("A")
    const onAbort = vi.fn()
    first.signal.addEventListener("abort", onAbort)
    expect(Number.isInteger(first.id)).toBe(true)
    expect(first.snapshotKey).toBe("A")
    expect(first.signal).toBeInstanceOf(AbortSignal)
    expect(first.signal.aborted).toBe(false)
    expect(tracker.isCurrent(first)).toBe(true)
    const second = tracker.begin("A")
    expect(second.id).toBeGreaterThan(first.id)
    expect(second.signal).not.toBe(first.signal)
    expect(first.signal.aborted).toBe(true)
    expect(onAbort).toHaveBeenCalledTimes(1)
    expect(tracker.isCurrent(first, "A")).toBe(false)
    expect(tracker.isCurrent(second, "A")).toBe(true)
  })

  it("isCurrent exige id, snapshotKey, clave opcional vigente y señal no abortada", () => {
    const tracker = new AnalysisRequestTracker()
    const ticket = tracker.begin("A")
    expect(tracker.isCurrent(ticket)).toBe(true)
    expect(tracker.isCurrent(ticket, "A")).toBe(true)
    expect(tracker.isCurrent(ticket, "B")).toBe(false)
    expect(tracker.isCurrent({ ...ticket, id: ticket.id - 1 })).toBe(false)
    expect(tracker.isCurrent({ ...ticket, snapshotKey: "B" })).toBe(false)
    const aborted = new AbortController()
    aborted.abort()
    expect(tracker.isCurrent({ ...ticket, signal: aborted.signal }, "A")).toBe(false)
  })

  it("invalidate es idempotente, aborta una sola vez y admite un nuevo begin monotónico", () => {
    const tracker = new AnalysisRequestTracker()
    tracker.invalidate()
    tracker.invalidate()
    const ticket = tracker.begin("A")
    const onAbort = vi.fn()
    ticket.signal.addEventListener("abort", onAbort)
    tracker.invalidate()
    tracker.invalidate()
    expect(ticket.signal.aborted).toBe(true)
    expect(onAbort).toHaveBeenCalledTimes(1)
    expect(tracker.isCurrent(ticket)).toBe(false)
    const next = tracker.begin("A")
    expect(next.id).toBeGreaterThan(ticket.id)
    expect(next.signal.aborted).toBe(false)
    expect(tracker.isCurrent(next, "A")).toBe(true)
    expect(tracker.isCurrent(ticket, "A")).toBe(false)
  })

  it("A1 -> B -> A2 nunca resucita A1, aunque la clave vuelva a ser A", () => {
    const tracker = new AnalysisRequestTracker()
    const a1 = tracker.begin("A")
    const b = tracker.begin("B")
    const a2 = tracker.begin("A")
    expect(a1.id).toBeLessThan(b.id)
    expect(b.id).toBeLessThan(a2.id)
    expect(a1.signal.aborted).toBe(true)
    expect(b.signal.aborted).toBe(true)
    expect(tracker.isCurrent(a1, "A")).toBe(false)
    expect(tracker.isCurrent(b, "B")).toBe(false)
    expect(tracker.isCurrent(a2, "A")).toBe(true)
  })
})

describe("C11 — AnalysisPanel real, JSX/hooks y fetch deferred locales", () => {
  it.each([
    ["loading", { loading: true }], ["49 velas", { candles: candles(49) }],
    ["vacío", { candles: [] }], ["source unknown", { dataSource: "unknown" }],
    ["interval null", { interval: null }], ["interval unknown", { interval: "unknown" }],
    ["interval vacío", { interval: "" }],
  ] satisfies [string, Partial<AnalysisProps>][])('no solicita análisis con input no elegible: %s', (_label, patch) => {
    const tree = renderAnalysis(patch)
    expect(fetch).not.toHaveBeenCalled()
    expect(text(tree)).not.toContain("C11_CURRENT")
    expect(text(tree)).toMatch(/sin|esper|carg|calcul|insuficiente|verificar/i)
  })

  it.each(["market_engine", "TEST_ONLY"] as const)("50 velas %s hacen solo POST /api/analysis con timeframe actual", source => {
    renderAnalysis({ dataSource: source, interval: "5m" })
    expect(requests).toHaveLength(1)
    const sent = request()
    expect(new URL(sent.url, "http://localhost").pathname).toBe("/api/analysis")
    expect(sent.init.method).toBe("POST")
    expect(new Headers(sent.init.headers).get("Content-Type")).toBe("application/json")
    const payload = JSON.parse(sent.init.body as string)
    expect(payload).toMatchObject({ symbol: "BTC/USDT", timeframe: "5m", candles_count: 50 })
    expect(payload.candles).toHaveLength(50)
    expect(payload.candles).toEqual(analysisProps.candles.map(row => expect.objectContaining({
      open_time: row.open_time, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume,
    })))
    expect(signal().aborted).toBe(false)
    expectPending(renderAnalysis())
  })

  it("envía últimas200 incluyendo vela en formación y etiqueta el análisis provisional", async () => {
    const rows = candles(250)
    renderAnalysis({ candles: rows })
    const payload = JSON.parse(request().init.body as string)
    expect(payload.candles_count).toBe(200)
    expect(payload.candles.map((row: { open_time: number }) => row.open_time)).toEqual(rows.slice(-200).map(row => row.open_time))
    const tree = await complete(0)
    expect(text(tree)).toContain("C11_CURRENT")
    expect(text(tree)).toMatch(/provisional|en formaci[oó]n|no confirmad/i)
  })

  it("clones equivalentes y cambios fuera de últimas200 deduplican sin abortar ni ocultar resultado", async () => {
    const rows = candles(250)
    renderAnalysis({ candles: rows })
    const pendingSignal = signal()
    renderAnalysis({ candles: rows.map(row => ({ ...row })) })
    expect(requests).toHaveLength(1)
    expect(pendingSignal.aborted).toBe(false)
    await complete(0)
    const tree = renderAnalysis({ candles: changed(rows, 49, { volume: 99, is_closed: false }) })
    expect(requests).toHaveLength(1)
    expect(pendingSignal.aborted).toBe(false)
    expect(text(tree)).toContain("C11_CURRENT")
  })

  it.each(fields)("refresca por %s en última vela, sin cambiar longitud; invalida resultado anterior", async (_field, patch) => {
    renderAnalysis()
    await complete(0, response("C11_OLD"))
    const oldSignal = signal(0)
    const tree = renderAnalysis({ candles: changed(analysisProps.candles, 49, patch) })
    expect(requests).toHaveLength(2)
    expect(oldSignal.aborted).toBe(true)
    expectNotCurrent(tree, "C11_OLD")
    expectPending(tree)
    const final = await complete(1, response("C11_NEW"))
    expect(text(final)).toContain("C11_NEW")
    expect(text(final)).not.toContain("C11_OLD")
  })

  it.each(fields)("refresca por %s interior con longitud constante", async (field, patch) => {
    renderAnalysis()
    await complete(0)
    const tree = renderAnalysis({ candles: changed(analysisProps.candles, 20, interiorPatch(field, patch)) })
    expect(requests).toHaveLength(2)
    expect(signal(0).aborted).toBe(true)
    expectNotCurrent(tree, "C11_CURRENT")
    expectPending(tree)
  })

  it.each([
    ["symbol", { symbol: "ETH/USDT" }], ["interval", { interval: "5m" }],
    ["source", { dataSource: "TEST_ONLY" }],
  ] satisfies [string, Partial<AnalysisProps>][])('cambio de %s renueva request y contexto sin reciclar resultado', async (_label, patch) => {
    renderAnalysis()
    await complete(0, response("C11_OLD_CONTEXT"))
    const tree = renderAnalysis(patch)
    expect(requests).toHaveLength(2)
    expect(signal(0).aborted).toBe(true)
    expectNotCurrent(tree, "C11_OLD_CONTEXT")
    expect(JSON.parse(request(1).init.body as string)).toMatchObject({ symbol: analysisProps.symbol, timeframe: analysisProps.interval })
    const final = await complete(1, response("C11_NEW_CONTEXT"))
    expect(text(final)).toContain("C11_NEW_CONTEXT")
    expect(text(final)).not.toContain("C11_OLD_CONTEXT")
  })

  it.each([
    ["loading", { loading: true }], ["unknown source", { dataSource: "unknown" }],
    ["unknown interval", { interval: null }], ["insuficiente", { candles: candles(49) }],
  ] satisfies [string, Partial<AnalysisProps>][])('perder elegibilidad (%s) aborta pendiente y no acepta su respuesta tardía', async (_label, patch) => {
    renderAnalysis()
    const staleData = response("C11_INELIGIBLE_STALE")
    renderAnalysis(patch)
    expect(signal(0).aborted).toBe(true)
    expect(requests).toHaveLength(1)
    const tree = await complete(0, staleData)
    expect(text(tree)).not.toContain("C11_INELIGIBLE_STALE")
    expect(requests).toHaveLength(1)
  })

  it("loading aborta también resultado completado; al volver a input válido pide snapshot fresco", async () => {
    renderAnalysis()
    await complete(0, response("C11_PRE_LOADING"))
    const tree = renderAnalysis({ loading: true })
    expectNotCurrent(tree, "C11_PRE_LOADING")
    expectPending(tree)
    expect(signal(0).aborted).toBe(true)
    renderAnalysis({ loading: false })
    expect(requests).toHaveLength(2)
    expect(text(await complete(1, response("C11_POST_LOADING")))).toContain("C11_POST_LOADING")
  })

  it.each(["fetch", "json"] as const)("B termina antes de A (%s deferred): A no sustituye resultado nuevo", async stage => {
    renderAnalysis()
    const stale = response("C11_STALE_A")
    if (stage === "json") {
      request(0).response.resolve({ ok: true, status: 200, json: () => request(0).body.promise })
      await flushPromises()
    }
    renderAnalysis({ candles: changed(analysisProps.candles, 49, { close: 115 }) })
    expect(signal(0).aborted).toBe(true)
    expect(text(await complete(1, response("C11_CURRENT_B")))).toContain("C11_CURRENT_B")
    const final = await complete(0, stale)
    expect(text(final)).toContain("C11_CURRENT_B")
    expect(text(final)).not.toContain("C11_STALE_A")
    expect(requests).toHaveLength(2)
  })

  it.each(["reject", "HTTP", "invalid"] as const)("fallo antiguo %s no baja loading de B ni publica error antiguo", async failure => {
    renderAnalysis()
    renderAnalysis({ candles: changed(analysisProps.candles, 49, { volume: 20 }) })
    const writesBeforeFailure = reactHarness.stateWrites
    if (failure === "reject") request(0).response.reject(new Error("C11_STALE_ERROR"))
    else deliver(0, failure === "invalid" ? { broken: "C11_STALE_ERROR" } : response(), failure === "HTTP" ? 503 : 200)
    await flushPromises()
    expect(reactHarness.stateWrites).toBe(writesBeforeFailure)
    const pending = renderAnalysis()
    expectPending(pending)
    expect(text(pending)).not.toContain("C11_STALE_ERROR")
    const final = await complete(1, response("C11_GOOD_B"))
    expect(text(final)).toContain("C11_GOOD_B")
    expect(text(final)).not.toContain("C11_STALE_ERROR")
  })

  it("rechazo de A después del éxito de B no borra B ni convierte su éxito en error", async () => {
    renderAnalysis()
    renderAnalysis({ candles: changed(analysisProps.candles, 49, { volume: 20 }) })
    await complete(1, response("C11_SURVIVES_ERROR"))
    request(0).response.reject(new Error("C11_STALE_REJECTION"))
    await flushPromises()
    const tree = renderAnalysis()
    expect(text(tree)).toContain("C11_SURVIVES_ERROR")
    expect(text(tree)).not.toContain("C11_STALE_REJECTION")
    expect(nodes(tree).filter(node => node.props.role === "alert")).toHaveLength(0)
  })

  it("A1 -> B -> A2: ni A1 ni B prevalecen sobre A2 con la misma clave que A1", async () => {
    const a = analysisProps.candles
    renderAnalysis()
    const a1 = response("C11_A1")
    renderAnalysis({ candles: changed(a, 49, { volume: 20 }) })
    const b = response("C11_B")
    renderAnalysis({ candles: a.map(row => ({ ...row })) })
    expect(requests).toHaveLength(3)
    expect(signal(0).aborted).toBe(true)
    expect(signal(1).aborted).toBe(true)
    await complete(2, response("C11_A2"))
    await complete(1, b)
    const final = await complete(0, a1)
    expect(text(final)).toContain("C11_A2")
    expect(text(final)).not.toContain("C11_A1")
    expect(text(final)).not.toContain("C11_B")
  })

  it("latestKey bloquea respuesta entre render del nuevo snapshot y cleanup del efecto anterior", async () => {
    renderAnalysis()
    const oldData = response("C11_BETWEEN_RENDER_AND_CLEANUP")
    analysisProps = { ...analysisProps, candles: changed(analysisProps.candles, 49, { volume: 20 }) }
    const beforeEffects = reactHarness.renderOnly(() => AnalysisPanel(analysisProps))
    expectNotCurrent(beforeEffects, "C11_BETWEEN_RENDER_AND_CLEANUP")
    expect(requests).toHaveLength(1)
    expect(signal(0).aborted).toBe(false)
    const writesBeforeReply = reactHarness.stateWrites
    deliver(0, oldData)
    await flushPromises()
    expect(reactHarness.stateWrites).toBe(writesBeforeReply)
    // Otro render SIN efectos observa cualquier setter ilícito de la respuesta.
    const guarded = reactHarness.renderOnly(() => AnalysisPanel(analysisProps))
    expect(text(guarded)).not.toContain("C11_BETWEEN_RENDER_AND_CLEANUP")
    reactHarness.flushEffects()
    expect(signal(0).aborted).toBe(true)
    expect(requests).toHaveLength(2)
    expect(text(await complete(1, response("C11_LATEST_KEY")))).toContain("C11_LATEST_KEY")
  })

  it("resultado previo ya resuelto no aparece vigente ni durante el render anterior a efectos", async () => {
    renderAnalysis()
    await complete(0, response("C11_PREVIOUS_VISIBLE"))
    analysisProps = { ...analysisProps, candles: changed(analysisProps.candles, 49, { is_closed: true }) }
    const tree = reactHarness.renderOnly(() => AnalysisPanel(analysisProps))
    expectNotCurrent(tree, "C11_PREVIOUS_VISIBLE")
    expectPending(tree)
    reactHarness.flushEffects()
  })

  it.each(["resolve", "reject"] as const)("unmount aborta una vez y %s tardío no ejecuta setters", async ending => {
    renderAnalysis()
    const data = response("C11_UNMOUNTED")
    const abort = vi.fn()
    signal().addEventListener("abort", abort)
    reactHarness.unmount()
    reactHarness.unmount()
    expect(signal(0).aborted).toBe(true)
    expect(abort).toHaveBeenCalledTimes(1)
    if (ending === "resolve") deliver(0, data)
    else request(0).response.reject(new Error("C11_AFTER_UNMOUNT"))
    await flushPromises()
    expect(reactHarness.updatesAfterUnmount).toBe(0)
    expect(requests).toHaveLength(1)
  })

  it.each(["network", "HTTP", "json"] as const)("error actual %s es visible, oculta resultado viejo y permite recuperar", async failure => {
    renderAnalysis()
    await complete(0, response("C11_BEFORE_FAILURE"))
    renderAnalysis({ candles: changed(analysisProps.candles, 49, { volume: 20 }) })
    if (failure === "network") request(1).response.reject(new Error("C11_CURRENT_NETWORK_ERROR"))
    else if (failure === "HTTP") deliver(1, response(), 503)
    else {
      request(1).response.resolve({ ok: true, status: 200, json: () => request(1).body.promise })
      request(1).body.reject(new Error("C11_BAD_JSON"))
    }
    await flushPromises()
    const tree = renderAnalysis()
    expectError(tree)
    expectNotCurrent(tree, "C11_BEFORE_FAILURE")
    renderAnalysis({ candles: changed(analysisProps.candles, 49, { volume: 21 }) })
    expect(requests).toHaveLength(3)
    expect(text(await complete(2, response("C11_RECOVERED")))).toContain("C11_RECOVERED")
  })

  it.each([
    ["symbol", { symbol: "ETH/USDT" }], ["timeframe", { timeframe: "5m" }],
    ["source desconocido", { data_source: "market_engine" }], ["source ausente", { data_source: undefined }],
  ] satisfies [string, Record<string, unknown>][])('respuesta con %s incompatible genera error visible sin renderizar indicadores', async (_label, patch) => {
    renderAnalysis()
    const tree = await complete(0, response("C11_MISMATCH", patch))
    expectError(tree)
    expect(text(tree)).not.toContain("C11_MISMATCH")
  })

  const malformed: [string, () => unknown][] = [
    ["null", () => null], ["array raíz", () => []], ["primitivo", () => "analysis"],
    ["keltner ausente", () => response("C11_INVALID", { keltner: undefined })],
    ["keltner points no array", () => response("C11_INVALID", { keltner: { points: {} } })],
    ["keltner punto null", () => response("C11_INVALID", { keltner: { points: [null] } })],
    ["keltner punto incompleto", () => response("C11_INVALID", { keltner: { points: [{ time_ms: 1 }] } })],
    ["keltner ema string", () => { const data = response("C11_INVALID"); return { ...data,
      keltner: { points: [{ ...data.keltner.points[0], ema: "105" }] } } }],
    ["macd ausente", () => response("C11_INVALID", { macd: undefined })],
    ["macd points null", () => response("C11_INVALID", { macd: { points: null } })],
    ["macd punto null", () => response("C11_INVALID", { macd: { points: [null] } })],
    ["macd punto incompleto", () => response("C11_INVALID", { macd: { points: [{}] } })],
    ["fyl ausente", () => response("C11_INVALID", { fyl: undefined })],
    ["pivots no array", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, pivots: {} } } }],
    ["pivot null", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, pivots: [null] } } }],
    ["pivot incompleto", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, pivots: [{}] } } }],
    ["zones no array", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, zones: "zones" } } }],
    ["zone null", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, zones: [null] } } }],
    ["zone incompleta", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, zones: [{}] } } }],
    ["annotations no array", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, annotations: {} } } }],
    ["annotation null", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, annotations: [null] } } }],
    ["annotation incompleta", () => { const data = response("C11_INVALID"); return { ...data, fyl: { ...data.fyl, annotations: [{}] } } }],
    ["annotation_type numérico", () => { const data = response("C11_INVALID"); return { ...data,
      fyl: { ...data.fyl, annotations: [{ ...data.fyl.annotations[0], annotation_type: 123 }] } } }],
  ]

  it.each(malformed)("valida estructura (%s): error visible, nunca crash", async (_label, invalid) => {
    renderAnalysis()
    deliver(0, invalid())
    await flushPromises()
    let tree: unknown
    expect(() => { tree = renderAnalysis() }).not.toThrow()
    expectError(tree)
    expect(text(tree)).not.toContain("C11_INVALID")
  })

  it("arrays de indicadores vacíos son respuesta válida y no se confunden con error", async () => {
    renderAnalysis()
    const tree = await complete(0, response("unused", { keltner: { points: [] }, macd: { points: [] },
      fyl: { pivots: [], zones: [], annotations: [] } }))
    expect(text(tree)).toMatch(/Keltner/i)
    expect(text(tree)).toMatch(/MACD/i)
    expect(nodes(tree).filter(node => node.props.role === "alert")).toHaveLength(0)
  })

  it.each([
    ["TEST_ONLY", "provided"], ["market_engine", "synthetic_test"], ["TEST_ONLY", "synthetic_test"],
  ] as const)("input %s + respuesta %s conservan etiqueta TEST_ONLY no operativa", async (source, responseSource) => {
    renderAnalysis({ dataSource: source })
    const tree = await complete(0, response("C11_SOURCE", { data_source: responseSource }))
    expect(text(tree)).toContain("C11_SOURCE")
    expect(text(tree)).toContain("TEST_ONLY")
    expect(text(tree)).toMatch(/sint[eé]tic/i)
    expect(text(tree)).toMatch(/no operativ/i)
  })

  it("mantiene reglas pendientes, T1/V1/V2 deshabilitados, MACD no BB y cruce no ejecutable; sin Ollama", async () => {
    renderAnalysis()
    const tree = await complete(0)
    const visible = text(tree)
    expect(visible).toMatch(/reglas.*pendientes/i)
    expect(visible).toMatch(/T1.*V1.*V2.*deshabilitad/i)
    expect(visible).toMatch(/no (?:es )?MACD BB/i)
    expect(visible).toMatch(/no ejecutable/i)
    expect(visible).toMatch(/descriptiv/i)
    expect(visible).toMatch(/no (?:es )?(?:una )?orden.*fill/i)
    expect(requests).toHaveLength(1)
    expect(new URL(request().url, "http://localhost").pathname).toBe("/api/analysis")
    expect(request().url).not.toMatch(/ollama|11434|generate|chat/i)
  })
})

describe("C11 — ChartPanel real con Lightweight Charts, ResizeObserver y elemento falsos", () => {
  it("container estable desde loading/vacío hasta datos; único chart/serie y cleanup exactamente una vez", () => {
    renderChart({ candles: [], loading: true })
    expect(charts.createChart).toHaveBeenCalledTimes(1)
    const current = chart()
    const container = reactHarness.attachedElements[0]
    expect(container).toBeDefined()
    expect(current.container).toBe(container)
    expect(current.addSeries).toHaveBeenCalledTimes(1)
    expect(current.addSeries).toHaveBeenCalledWith(charts.CandlestickSeries, expect.any(Object))
    expect(FakeResizeObserver.instances).toHaveLength(1)
    const observer = FakeResizeObserver.instances[0]!
    expect(observer.observe).toHaveBeenCalledTimes(1)
    expect(observer.observe).toHaveBeenCalledWith(container)
    renderChart({ loading: false })
    renderChart({ candles: candles() })
    renderChart({ candles: [], loading: true })
    renderChart({ candles: candles(), loading: false, wsConnected: true })
    expect(reactHarness.attachedElements).toHaveLength(1)
    expect(reactHarness.attachedElements[0]).toBe(container)
    expect(charts.createChart).toHaveBeenCalledTimes(1)
    expect(current.addSeries).toHaveBeenCalledTimes(1)
    expect(observer.disconnect).not.toHaveBeenCalled()
    expect(current.remove).not.toHaveBeenCalled()
    reactHarness.unmount()
    reactHarness.unmount()
    expect(observer.disconnect).toHaveBeenCalledTimes(1)
    expect(current.remove).toHaveBeenCalledTimes(1)
    expect(reactHarness.attachedElements).toHaveLength(0)
    expect(fetch).not.toHaveBeenCalled()
  })

  it("resize aplica dimensiones actuales exactamente una vez por evento, sin trabajo en velas ni recreación", () => {
    renderChart()
    const container = reactHarness.attachedElements[0]!
    const current = chart()
    current.applyOptions.mockClear()
    clearChartWork()
    container.clientWidth = 1024
    container.clientHeight = 512
    FakeResizeObserver.instances[0]!.resize()
    expect(current.applyOptions.mock.calls).toEqual([[{ width: 1024, height: 512 }]])
    container.clientWidth = 640
    container.clientHeight = 360
    FakeResizeObserver.instances[0]!.resize()
    expect(current.applyOptions.mock.calls).toEqual([[{ width: 1024, height: 512 }], [{ width: 640, height: 360 }]])
    expectNoChartWork()
    expect(charts.createChart).toHaveBeenCalledTimes(1)
    expect(current.remove).not.toHaveBeenCalled()
  })

  it("carga inicial setData, vaciar setData([]) y repoblar reconstruye usando segundos/OHLC exactos", () => {
    const rows = candles()
    renderChart({ candles: rows })
    expect(chart().series.setData.mock.calls).toEqual([[bars(rows)]])
    expect(chart().series.update).not.toHaveBeenCalled()
    clearChartWork()
    renderChart({ candles: [] })
    expect(chart().series.setData.mock.calls).toEqual([[[]]])
    expect(chart().series.update).not.toHaveBeenCalled()
    expect(chart().rows).toEqual([])
    clearChartWork()
    renderChart({ candles: rows })
    expect(chart().series.setData.mock.calls).toEqual([[bars(rows)]])
    expect(chart().rows).toEqual(bars(rows))
    expect(charts.createChart).toHaveBeenCalledTimes(1)
  })

  it.each(fields.slice(2, 6))("OHLC último (%s) con mismo tiempo/prefijo usa solo update", (_field, patch) => {
    const rows = candles()
    renderChart({ candles: rows })
    clearChartWork()
    const next = changed(rows, 49, patch)
    renderChart({ candles: next })
    expect(chart().series.setData).not.toHaveBeenCalled()
    expect(chart().series.update.mock.calls).toEqual([[bars(next)[49]]])
    expect(chart().rows).toEqual(bars(next))
    expect(charts.createChart).toHaveBeenCalledTimes(1)
  })

  it.each([1, 3])("append compatible de %i velas usa update ordenados sin setData", count => {
    const rows = candles()
    renderChart({ candles: rows })
    clearChartWork()
    const appended = Array.from({ length: count }, (_, i) => candle(50 + i))
    renderChart({ candles: [...rows, ...appended] })
    expect(chart().series.setData).not.toHaveBeenCalled()
    expect(chart().series.update.mock.calls).toEqual(bars(appended).map(row => [row]))
    expect(chart().rows).toEqual(bars([...rows, ...appended]))
    expect(charts.createChart).toHaveBeenCalledTimes(1)
  })

  it("corrige OHLC de previoúltimo antes de append múltiple, sin update histórico ni reconstrucción", () => {
    const rows = candles()
    renderChart({ candles: rows })
    clearChartWork()
    const corrected = changed(rows, 49, { high: 125, close: 115 })
    const next = [...corrected, candle(50), candle(51)]
    renderChart({ candles: next })
    expect(chart().series.setData).not.toHaveBeenCalled()
    expect(chart().series.update.mock.calls).toEqual(bars(next).slice(49).map(row => [row]))
    expect(chart().rows).toEqual(bars(next))
  })

  it("clones, volumen/cierre/close_time solos no hacen trabajo; preservan comparación para el siguiente OHLC", () => {
    const rows = candles()
    renderChart({ candles: rows })
    clearChartWork()
    renderChart({ candles: rows.map(row => ({ ...row })) })
    expectNoChartWork()
    const metadata = changed(changed(rows, 20, { volume: 99, is_closed: false }), 49,
      { volume: 20, is_closed: true, close_time: candle(49).close_time + 1000 })
    renderChart({ candles: metadata })
    expectNoChartWork()
    expect(chart().rows).toEqual(bars(rows))
    const next = changed(metadata, 49, { close: 115 })
    renderChart({ candles: next })
    expect(chart().series.update.mock.calls).toEqual([[bars(next)[49]]])
    expect(chart().series.setData).not.toHaveBeenCalled()
    expect(chart().rows).toEqual(bars(next))
  })

  it.each([
    ["OHLC interior", (rows: CandleData[]) => changed(rows, 20, { close: 115 })],
    ["OHLC primero", (rows: CandleData[]) => changed(rows, 0, { high: 125 })],
    ["tiempo interior", (rows: CandleData[]) => changed(rows, 20, { open_time: rows[20].open_time + 1000 })],
    ["tiempo último", (rows: CandleData[]) => changed(rows, 49, { open_time: rows[49].open_time + 1000 })],
    ["reducción", (rows: CandleData[]) => rows.slice(0, 40)],
    ["corrección interior + append", (rows: CandleData[]) => [...changed(rows, 20, { close: 115 }), candle(50)]],
  ] satisfies [string, (rows: CandleData[]) => CandleData[]][])('%s reconstruye exactamente con setData, nunca update', (_label, transform) => {
    const rows = candles()
    renderChart({ candles: rows })
    clearChartWork()
    const next = transform(rows)
    renderChart({ candles: next })
    expect(chart().series.setData.mock.calls).toEqual([[bars(next)]])
    expect(chart().series.update).not.toHaveBeenCalled()
    expect(chart().rows).toEqual(bars(next))
    expect(charts.createChart).toHaveBeenCalledTimes(1)
  })

  it.each([
    ["symbol", { symbol: "ETH/USDT" }], ["interval", { interval: "5m" }],
    ["source", { dataSource: "TEST_ONLY" }],
  ] satisfies [string, Partial<ChartProps>][])('contextchange %s reconstruye incluso con precios/array iguales y conserva labels', (_label, patch) => {
    renderChart()
    clearChartWork()
    const tree = renderChart(patch)
    expect(chart().series.setData.mock.calls).toEqual([[bars(chartProps.candles)]])
    expect(chart().series.update).not.toHaveBeenCalled()
    expect(text(tree)).toContain(chartProps.symbol)
    expect(text(tree)).toContain(chartProps.interval!)
    if (chartProps.dataSource === "TEST_ONLY") expect(text(tree)).toMatch(/TEST_ONLY.*sint[eé]tic.*no operativ/i)
    else expect(text(tree)).toMatch(/motor/i)
    expect(charts.createChart).toHaveBeenCalledTimes(1)
    expect(chart().remove).not.toHaveBeenCalled()
  })

  it("flags loading/conexión no cambian datos ni container; labels de source e intervalo siguen honestos", () => {
    renderChart()
    const container = reactHarness.attachedElements[0]
    clearChartWork()
    let tree = renderChart({ loading: true, wsConnected: true })
    expectNoChartWork()
    expect(text(tree)).toMatch(/motor conectado/i)
    expect(text(tree)).toMatch(/frescura no validada/i)
    tree = renderChart({ loading: false, wsConnected: false })
    expectNoChartWork()
    expect(text(tree)).toMatch(/desconectado/i)
    tree = renderChart({ dataSource: "TEST_ONLY", interval: "5m", wsConnected: true })
    expect(text(tree)).toMatch(/TEST_ONLY.*no operativ/i)
    expect(text(tree)).not.toMatch(/motor conectado/i)
    expect(text(tree)).toContain("5m")
    tree = renderChart({ dataSource: "unknown", interval: null })
    expect(text(tree)).toMatch(/procedencia.*sin verificar/i)
    expect(text(tree)).toMatch(/intervalo.*sin verificar/i)
    expect(text(tree)).toContain(chartProps.symbol)
    expect(reactHarness.attachedElements).toHaveLength(1)
    expect(reactHarness.attachedElements[0]).toBe(container)
    expect(charts.createChart).toHaveBeenCalledTimes(1)
  })

  it("rollingwindow200 elimina oldest con setData y no retiene más200 tras muchos eventos", () => {
    let rows = candles(200)
    renderChart({ candles: rows })
    for (let index = 200; index < 440; index++) {
      clearChartWork()
      rows = [...rows.slice(1), candle(index, { is_closed: false })]
      renderChart({ candles: rows })
      expect(chart().series.setData.mock.calls).toEqual([[bars(rows)]])
      expect(chart().series.update).not.toHaveBeenCalled()
      expect(chart().rows).toHaveLength(200)
      expect(chart().rows).toEqual(bars(rows))
    }
    clearChartWork()
    const next = changed(rows, 199, { close: 115 })
    renderChart({ candles: next })
    expect(chart().series.update.mock.calls).toEqual([[bars(next)[199]]])
    expect(chart().series.setData).not.toHaveBeenCalled()
    expect(chart().rows).toEqual(bars(next))
    expect(charts.createChart).toHaveBeenCalledTimes(1)
    expect(FakeResizeObserver.instances).toHaveLength(1)
  })

  it("input sobredimensionado se recorta200; cambios descartados no trabajan y append reconstruye el límite", () => {
    const rows = candles(250)
    renderChart({ candles: rows })
    expect(chart().series.setData.mock.calls).toEqual([[bars(rows)]])
    expect(chart().rows).toHaveLength(200)
    clearChartWork()
    const outside = changed(rows, 0, { close: 115 })
    renderChart({ candles: outside })
    expectNoChartWork()
    const next = [...outside, candle(250)]
    renderChart({ candles: next })
    expect(chart().series.setData.mock.calls).toEqual([[bars(next)]])
    expect(chart().series.update).not.toHaveBeenCalled()
    expect(chart().rows).toEqual(bars(next))
    expect(chart().rows).toHaveLength(200)
    expect(charts.createChart).toHaveBeenCalledTimes(1)
  })

  it("199 -> 200 permite update; siguiente append requiere setData para eliminar oldest", () => {
    const rows = candles(199)
    renderChart({ candles: rows })
    clearChartWork()
    const full = [...rows, candle(199)]
    renderChart({ candles: full })
    expect(chart().series.update.mock.calls).toEqual([[bars(full)[199]]])
    expect(chart().series.setData).not.toHaveBeenCalled()
    clearChartWork()
    const rolled = [...full.slice(1), candle(200)]
    renderChart({ candles: rolled })
    expect(chart().series.setData.mock.calls).toEqual([[bars(rolled)]])
    expect(chart().series.update).not.toHaveBeenCalled()
    expect(chart().rows).toEqual(bars(rolled))
  })
})
