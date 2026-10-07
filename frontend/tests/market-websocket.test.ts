// C09: cliente y hook reales, con transporte y reloj controlados en este archivo.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { MarketWebSocket, type MarketWSMessage } from "../src/api/market"
import { useMarketData } from "../src/hooks/useMarketData"
import type { CandleHistoryResponse, MarketCandleEvent, MarketStatus } from "../src/types/market"

// Runner mínimo de los tres hooks usados por useMarketData. Conserva estado/refs
// entre renders y ejecuta efectos y sus limpiezas, sin DOM ni helpers externos.
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
      stateIndex = 0
      refIndex = 0
      effectIndex = 0
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
      states.length = 0
      refs.length = 0
      effects.length = 0
      pending.length = 0
      stateIndex = refIndex = effectIndex = 0
    },
  }
})

vi.mock("react", () => ({
  useState: reactHooks.useState,
  useRef: reactHooks.useRef,
  useEffect: reactHooks.useEffect,
}))

const CONNECTING = 0
const OPEN = 1
const CLOSING = 2
const CLOSED = 3
const URL = "ws://localhost/test"
const SECRET = "C09_PRIVATE_TOKEN"
const OPEN_EVENT = { type: "open" }
const CLOSE_EVENT = { code: 1006, reason: "", wasClean: false }

class FakeWebSocket {
  static readonly CONNECTING = CONNECTING
  static readonly OPEN = OPEN
  static readonly CLOSING = CLOSING
  static readonly CLOSED = CLOSED
  static instances: FakeWebSocket[] = []
  static attempts: string[] = []
  static constructorFailures = 0

  readyState = CONNECTING
  onopen: ((event: typeof OPEN_EVENT) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: typeof CLOSE_EVENT) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  sentMessages: string[] = []
  sendError: Error | null = null
  closeCalls = 0

  constructor(readonly url: string) {
    FakeWebSocket.attempts.push(url)
    if (FakeWebSocket.constructorFailures > 0) {
      FakeWebSocket.constructorFailures--
      throw new Error(`constructor failed: ${SECRET} ${url}`)
    }
    FakeWebSocket.instances.push(this)
  }

  send(data: string): void {
    if (this.readyState !== OPEN) throw new Error("socket not open")
    if (this.sendError) throw this.sendError
    this.sentMessages.push(data)
  }

  // Cierre síncrono a propósito: fuerza la ruta reentrante y no crea timers
  // del fake que puedan confundirse con el backoff/heartbeat de producción.
  close(): void {
    this.closeCalls++
    if (this.readyState === CLOSED) return
    this.readyState = CLOSING
    this.emitClose()
  }

  emitOpen(): void {
    this.readyState = OPEN
    this.onopen?.(OPEN_EVENT)
  }

  emitMessage(data: unknown): void {
    this.onmessage?.({ data })
  }

  emitClose(): void {
    this.readyState = CLOSED
    this.onclose?.(CLOSE_EVENT)
  }

  static reset(): void {
    this.instances = []
    this.attempts = []
    this.constructorFailures = 0
  }
}

let clients: MarketWebSocket[] = []

function client(url = URL): MarketWebSocket {
  const ws = new MarketWebSocket(url)
  clients.push(ws)
  return ws
}

function socket(index = FakeWebSocket.instances.length - 1): FakeWebSocket {
  const instance = FakeWebSocket.instances[index]
  expect(instance).toBeDefined()
  return instance!
}

function messages(sock: FakeWebSocket): MarketWSMessage[] {
  return sock.sentMessages.map(data => JSON.parse(data) as MarketWSMessage)
}

function subscriptions(sock: FakeWebSocket): MarketWSMessage[] {
  return messages(sock).filter(message => message.type === "subscribe")
}

function subscribeMessages(...channels: string[]): MarketWSMessage[] {
  return channels.map(channel => ({ type: "subscribe", channel }))
}

function retainedHandlers(sock: FakeWebSocket) {
  expect(sock.onopen).toEqual(expect.any(Function))
  expect(sock.onmessage).toEqual(expect.any(Function))
  expect(sock.onclose).toEqual(expect.any(Function))
  expect(sock.onerror).toEqual(expect.any(Function))
  return {
    open: sock.onopen!,
    message: sock.onmessage!,
    close: sock.onclose!,
    error: sock.onerror!,
  }
}

function expectDetached(sock: FakeWebSocket): void {
  expect(sock.onopen).toBeNull()
  expect(sock.onmessage).toBeNull()
  expect(sock.onclose).toBeNull()
  expect(sock.onerror).toBeNull()
}

function expectGenericLogs(): void {
  const calls = [...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]
  expect(calls.length).toBeGreaterThan(0)
  const logged = calls.flat().map(value => value instanceof Error
    ? `${value.message}\n${value.stack}`
    : typeof value === "string" ? value : JSON.stringify(value)).join(" ")
  expect(logged).not.toContain(SECRET)
  expect(logged).not.toContain("token=")
}

function bar(type: MarketCandleEvent["type"] = "bar_updated"): MarketCandleEvent {
  return {
    type,
    symbol: "BTC/USDT",
    interval: "1m",
    data_source: "market_engine",
    candle: {
      open_time: 1000, close_time: 1500,
      open: 50000, high: 51000, low: 49500, close: 50500,
      volume: 100, is_closed: type === "bar_closed",
    },
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal("WebSocket", FakeWebSocket)
  vi.spyOn(console, "warn").mockImplementation(() => {})
  vi.spyOn(console, "error").mockImplementation(() => {})
  FakeWebSocket.reset()
  reactHooks.reset()
  clients = []
})

afterEach(() => {
  try {
    reactHooks.unmount()
    for (const ws of clients) ws.disconnect()
  } finally {
    // Incluso ante una assertion fallida se liberan transporte, reloj y globals.
    try {
      for (const sock of FakeWebSocket.instances) sock.close()
    } finally {
      vi.clearAllTimers()
      vi.restoreAllMocks()
      vi.useRealTimers()
      vi.unstubAllGlobals()
      vi.unstubAllEnvs()
      FakeWebSocket.reset()
      reactHooks.reset()
      clients = []
    }
  }
})

describe("C09 — MarketWebSocket real", () => {
  // Las primeras doce pruebas preservan las intenciones del test original.
  it("1. apertura lenta conserva suscripciones hasta onopen", () => {
    const ws = client()
    const opened = vi.fn()
    ws.onOpen(opened)
    ws.subscribe("candles")
    ws.subscribe("candles")
    ws.connect()
    const first = socket()

    vi.advanceTimersByTime(60000)
    expect(ws.state).toBe(CONNECTING)
    expect(ws.isOpen).toBe(false)
    expect(opened).not.toHaveBeenCalled()
    expect(first.sentMessages).toEqual([])
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)

    first.emitOpen()
    expect(ws.isOpen).toBe(true)
    expect(opened).toHaveBeenCalledTimes(1)
    expect(messages(first)).toEqual(subscribeMessages("candles"))
  })

  it("2. disconnect es terminal e idempotente, incluso ante connect manual", () => {
    const ws = client()
    ws.connect()
    const first = socket()
    first.emitOpen()
    ws.disconnect()
    ws.disconnect()
    ws.connect()
    first.emitClose()
    vi.advanceTimersByTime(120000)

    expect(ws.state).toBeNull()
    expect(ws.isOpen).toBe(false)
    expect(first.readyState).toBe(CLOSED)
    expect(first.closeCalls).toBe(1)
    expectDetached(first)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("3. desmontaje durante backoff deja cero timers y sockets activos", () => {
    const ws = client()
    const closed = vi.fn()
    ws.onClose(closed)
    ws.connect()
    const first = socket()
    first.emitOpen()
    first.emitClose()
    expect(closed).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)

    ws.disconnect()
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(120000)
    expect(ws.state).toBeNull()
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances.every(sock => sock.readyState === CLOSED)).toBe(true)
    expect(closed).toHaveBeenCalledTimes(1)
    expectDetached(first)
  })

  it("4. isOpen exige onopen confirmado, no basta readyState OPEN", () => {
    const ws = client()
    const opened = vi.fn()
    ws.onOpen(opened)
    ws.connect()
    const first = socket()
    expect(ws.isOpen).toBe(false)

    first.readyState = OPEN
    expect(ws.state).toBe(OPEN)
    expect(ws.isOpen).toBe(false)
    expect(opened).not.toHaveBeenCalled()
    ws.ping()
    ws.subscribe("candles")
    expect(first.sentMessages).toEqual([])

    first.emitOpen()
    expect(ws.isOpen).toBe(true)
    expect(opened).toHaveBeenCalledTimes(1)
    expect(messages(first)).toEqual(subscribeMessages("candles"))
  })

  it("5. reconexión usa un único timeout one-shot, aun con close repetido", () => {
    const timeout = vi.spyOn(globalThis, "setTimeout")
    const interval = vi.spyOn(globalThis, "setInterval")
    const closed = vi.fn()
    const ws = client()
    ws.onClose(closed)
    ws.connect()
    const first = socket()
    first.emitOpen()
    const old = retainedHandlers(first)
    first.emitClose()
    old.close(CLOSE_EVENT)
    old.close(CLOSE_EVENT)

    expect(closed).toHaveBeenCalledTimes(1)
    expect(timeout).toHaveBeenCalledTimes(1)
    expect(timeout).toHaveBeenLastCalledWith(expect.any(Function), 1000)
    expect(interval).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(999)
    expect(FakeWebSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(ws.state).toBe(CONNECTING)
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(60000)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("6. resuscribe todos los canales una vez en la nueva sesión", () => {
    const ws = client()
    const opened = vi.fn()
    ws.onOpen(opened)
    ws.subscribe("candles")
    ws.subscribe("status")
    ws.connect()
    const first = socket()
    first.emitOpen()
    expect(messages(first)).toEqual(subscribeMessages("candles", "status"))
    first.emitClose()

    vi.advanceTimersByTime(1000)
    expect(FakeWebSocket.instances).toHaveLength(2)
    const second = socket()
    expect(second).not.toBe(first)
    expect(second.readyState).toBe(CONNECTING)
    expect(second.sentMessages).toEqual([])
    second.emitOpen()

    expect(opened).toHaveBeenCalledTimes(2)
    expect(messages(second)).toEqual(subscribeMessages("candles", "status"))
    expect(vi.getTimerCount()).toBe(0)
  })

  it("7. entrega bar_updated y bar_closed completos, filtrando pong/subscribed", () => {
    const ws = client()
    const received = vi.fn()
    ws.onMessage(received)
    ws.connect()
    socket().emitOpen()
    socket().emitMessage(JSON.stringify({ type: "pong" }))
    socket().emitMessage(JSON.stringify({ type: "subscribed", channel: "candles" }))
    const update = bar()
    const closed = bar("bar_closed")
    socket().emitMessage(JSON.stringify(update))
    socket().emitMessage(JSON.stringify(closed))

    expect(received.mock.calls).toEqual([[update], [closed]])
    expect(ws.isOpen).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("8. ignora JSON inválido, null, primitivos, arrays y binarios sin romper el stream", () => {
    const ws = client()
    const received = vi.fn()
    ws.onMessage(received)
    ws.connect()
    socket().emitOpen()
    const invalid: unknown[] = [
      "GARBAGE\x00\x01", "{invalid json}", "null", "true", "false", "42", '"text"',
      "[]", '[{"type":"bar_updated"}]', "{}", '{"type":null}', '{"type":42}',
      new ArrayBuffer(8), new Uint8Array([1, 2, 3]),
      { toString: () => JSON.stringify(bar()) },
    ]
    for (const payload of invalid) {
      expect(() => socket().emitMessage(payload)).not.toThrow()
      expect(received).not.toHaveBeenCalled()
    }
    socket().emitMessage(JSON.stringify(bar()))
    expect(received.mock.calls).toEqual([[bar()]])
    expect(ws.isOpen).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("9. onClose notifica una sola vez al desconectar un socket existente", () => {
    const ws = client()
    const closed = vi.fn()
    ws.onClose(closed)
    ws.connect()
    const first = socket()
    first.emitOpen()
    const old = retainedHandlers(first)
    expect(closed).not.toHaveBeenCalled()

    ws.disconnect()
    ws.disconnect()
    old.close(CLOSE_EVENT)
    expect(closed).toHaveBeenCalledTimes(1)
    expect(first.closeCalls).toBe(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("10. connect no duplica sockets CONNECTING, OPEN ni CLOSING", () => {
    const ws = client()
    ws.connect()
    const first = socket()
    for (const state of [CONNECTING, OPEN, CLOSING]) {
      first.readyState = state
      ws.connect()
      ws.connect()
      expect(FakeWebSocket.instances).toEqual([first])
      expect(FakeWebSocket.attempts).toEqual([URL])
      expect(ws.state).toBe(state)
      expect(vi.getTimerCount()).toBe(0)
    }
  })

  it("11. ping envía exactamente el mensaje esperado solo tras apertura", () => {
    const ws = client()
    ws.ping()
    ws.connect()
    const first = socket()
    ws.ping()
    expect(first.sentMessages).toEqual([])
    first.emitOpen()
    ws.ping()
    expect(messages(first)).toEqual([{ type: "ping" }])
    ws.disconnect()
    ws.ping()
    expect(messages(first)).toEqual([{ type: "ping" }])
    expect(vi.getTimerCount()).toBe(0)
  })

  it("12. unsubscribe elimina canales pendientes y de siguientes reconexiones", () => {
    const ws = client()
    ws.subscribe("candles")
    ws.subscribe("status")
    ws.unsubscribe("candles")
    ws.connect()
    const first = socket()
    first.emitOpen()
    expect(messages(first)).toEqual(subscribeMessages("status"))
    ws.subscribe("candles")
    ws.unsubscribe("candles")
    expect(messages(first)).toEqual([
      ...subscribeMessages("status", "candles"), { type: "unsubscribe", channel: "candles" },
    ])
    first.emitClose()
    ws.subscribe("gaps")
    ws.unsubscribe("gaps")
    vi.advanceTimersByTime(1000)
    const second = socket()
    second.emitOpen()
    expect(messages(second)).toEqual(subscribeMessages("status"))
    ws.subscribe("candles")
    ws.subscribe("candles")
    expect(messages(second)).toEqual(subscribeMessages("status", "candles"))
  })

  it("cierre inesperado notifica inmediatamente y reconecta después de 1s", () => {
    const ws = client()
    const closed = vi.fn()
    ws.onClose(closed)
    ws.connect()
    socket().emitOpen()
    socket().emitClose()
    expect(ws.isOpen).toBe(false)
    expect(closed).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(999)
    expect(FakeWebSocket.instances).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.instances).toHaveLength(2)
    expect(ws.isOpen).toBe(false)
    socket().emitOpen()
    expect(ws.isOpen).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("callbacks retenidos del socket anterior no afectan la sesión reconectada", () => {
    const ws = client()
    const opened = vi.fn()
    const closed = vi.fn()
    const received = vi.fn()
    ws.onOpen(opened)
    ws.onClose(closed)
    ws.onMessage(received)
    ws.subscribe("candles")
    ws.connect()
    const first = socket()
    first.emitOpen()
    const old = retainedHandlers(first)
    first.emitClose()
    vi.advanceTimersByTime(1000)
    const second = socket()
    second.emitOpen()

    expect(() => {
      old.open(OPEN_EVENT)
      old.message({ data: JSON.stringify(bar()) })
      old.close(CLOSE_EVENT)
      old.error({ message: SECRET })
    }).not.toThrow()
    expect(opened).toHaveBeenCalledTimes(2)
    expect(closed).toHaveBeenCalledTimes(1)
    expect(received).not.toHaveBeenCalled()
    expect(ws.state).toBe(OPEN)
    expect(ws.isOpen).toBe(true)
    expect(messages(second)).toEqual(subscribeMessages("candles"))
    expect(second.closeCalls).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
    expectDetached(first)
    second.emitMessage(JSON.stringify(bar("bar_closed")))
    expect(received.mock.calls).toEqual([[bar("bar_closed")]])
  })

  it("callbacks retenidos tras disconnect no emiten ni recrean sockets/timers", () => {
    const ws = client()
    const opened = vi.fn()
    const closed = vi.fn()
    const received = vi.fn()
    ws.onOpen(opened)
    ws.onClose(closed)
    ws.onMessage(received)
    ws.connect()
    const first = socket()
    first.emitOpen()
    const old = retainedHandlers(first)
    ws.disconnect()

    expect(() => {
      old.open(OPEN_EVENT)
      old.message({ data: JSON.stringify(bar()) })
      old.close(CLOSE_EVENT)
      old.error(new Error(SECRET))
    }).not.toThrow()
    vi.advanceTimersByTime(120000)
    expect(opened).toHaveBeenCalledTimes(1)
    expect(closed).toHaveBeenCalledTimes(1)
    expect(received).not.toHaveBeenCalled()
    expect(ws.state).toBeNull()
    expect(ws.isOpen).toBe(false)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(first.sentMessages).toEqual([])
    expectDetached(first)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("aperturas duplicadas y subscribe desde onOpen no duplican envíos", () => {
    const ws = client()
    const opened = vi.fn(() => {
      ws.subscribe("candles")
      ws.subscribe("status")
      ws.subscribe("status")
    })
    ws.onOpen(opened)
    ws.subscribe("candles")
    ws.connect()
    const first = socket()
    first.emitOpen()
    first.emitOpen()
    ws.subscribe("candles")
    ws.subscribe("status")
    expect(opened).toHaveBeenCalledTimes(1)
    expect(messages(first)).toEqual(subscribeMessages("candles", "status"))

    first.emitClose()
    vi.advanceTimersByTime(1000)
    const second = socket()
    second.emitOpen()
    second.emitOpen()
    expect(opened).toHaveBeenCalledTimes(2)
    expect(messages(second)).toEqual(subscribeMessages("candles", "status"))
  })

  it("backoff crece 1s, 2s, 4s, 8s, 16s y queda limitado a 30s", () => {
    const ws = client()
    ws.connect()
    const delays = [1000, 2000, 4000, 8000, 16000, 30000, 30000]
    for (const [index, delay] of delays.entries()) {
      socket().emitClose() // Fallo antes de onopen: no debe resetear el delay.
      expect(vi.getTimerCount()).toBe(1)
      vi.advanceTimersByTime(delay - 1)
      expect(FakeWebSocket.instances).toHaveLength(index + 1)
      vi.advanceTimersByTime(1)
      expect(FakeWebSocket.instances).toHaveLength(index + 2)
      expect(ws.state).toBe(CONNECTING)
      expect(vi.getTimerCount()).toBe(0)
    }
    ws.disconnect()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("onopen exitoso reinicia backoff a 1s y deja cero timers de retry", () => {
    const ws = client()
    ws.connect()
    socket().emitClose()
    vi.advanceTimersByTime(1000)
    socket().emitClose()
    vi.advanceTimersByTime(2000)
    socket().emitOpen()
    expect(vi.getTimerCount()).toBe(0)
    socket().emitClose()
    vi.advanceTimersByTime(999)
    expect(FakeWebSocket.instances).toHaveLength(3)
    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.instances).toHaveLength(4)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("constructor que lanza se trata como intento fallido y recupera suscripciones", () => {
    FakeWebSocket.constructorFailures = 2
    const ws = client(`${URL}?token=${SECRET}`)
    ws.subscribe("candles")
    expect(() => ws.connect()).not.toThrow()
    expect(ws.state).toBeNull()
    expect(ws.isOpen).toBe(false)
    expect(FakeWebSocket.instances).toHaveLength(0)
    expect(FakeWebSocket.attempts).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(1)

    expect(() => vi.advanceTimersByTime(1000)).not.toThrow()
    expect(ws.state).toBeNull()
    expect(FakeWebSocket.attempts).toHaveLength(2)
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(1999)
    expect(FakeWebSocket.attempts).toHaveLength(2)
    vi.advanceTimersByTime(1)
    expect(FakeWebSocket.attempts).toHaveLength(3)
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
    socket().emitOpen()
    expect(messages(socket())).toEqual(subscribeMessages("candles"))
    expect(ws.isOpen).toBe(true)
    expectGenericLogs()
  })

  it.each(["ping", "subscribe"] as const)(
    "send que falla durante %s cierra una vez y programa un único retry",
    failure => {
      const ws = client(`${URL}?token=${SECRET}`)
      const closed = vi.fn()
      ws.onClose(closed)
      ws.subscribe("candles")
      ws.subscribe("status")
      ws.connect()
      const first = socket()
      const old = retainedHandlers(first)
      const failSend = () => { first.sendError = new Error(`send failed: ${SECRET}`) }
      const actions = {
        ping: () => { first.emitOpen(); failSend(); ws.ping() },
        subscribe: () => { failSend(); first.emitOpen() },
      }
      expect(actions[failure]).not.toThrow()
      ws.ping()
      old.close(CLOSE_EVENT)
      expect(first.readyState).toBe(CLOSED)
      expect(first.closeCalls).toBe(1)
      expect(ws.isOpen).toBe(false)
      expect(closed).toHaveBeenCalledTimes(1)
      expect(vi.getTimerCount()).toBe(1)
      expectDetached(first)

      vi.advanceTimersByTime(1000)
      expect(FakeWebSocket.instances).toHaveLength(2)
      const second = socket()
      second.emitOpen()
      expect(ws.isOpen).toBe(true)
      expect(messages(second)).toEqual(subscribeMessages("candles", "status"))
      expect(closed).toHaveBeenCalledTimes(1)
      expect(vi.getTimerCount()).toBe(0)
      expectGenericLogs()
    },
  )

  it.each(["onOpen", "onMessage", "onClose"] as const)(
    "excepciones de %s no rompen suscripciones, mensajes ni reconexión",
    failingCallback => {
      const ws = client()
      const failure = () => { throw new Error(`callback failed: ${SECRET}`) }
      const callbacks = {
        onOpen: vi.fn(), onMessage: vi.fn(), onClose: vi.fn(),
      }
      callbacks[failingCallback].mockImplementation(failure)
      ws.onOpen(callbacks.onOpen)
      ws.onMessage(callbacks.onMessage)
      ws.onClose(callbacks.onClose)
      ws.subscribe("candles")
      ws.connect()
      const first = socket()
      expect(() => first.emitOpen()).not.toThrow()
      expect(messages(first)).toEqual(subscribeMessages("candles"))
      expect(() => first.emitMessage(JSON.stringify(bar()))).not.toThrow()
      expect(() => first.emitMessage(JSON.stringify(bar("bar_closed")))).not.toThrow()
      expect(callbacks.onMessage.mock.calls).toEqual([[bar()], [bar("bar_closed")]])
      expect(() => first.emitClose()).not.toThrow()
      expect(callbacks.onClose).toHaveBeenCalledTimes(1)
      expect(vi.getTimerCount()).toBe(1)

      vi.advanceTimersByTime(1000)
      const second = socket()
      expect(() => second.emitOpen()).not.toThrow()
      expect(callbacks.onOpen).toHaveBeenCalledTimes(2)
      expect(messages(second)).toEqual(subscribeMessages("candles"))
      expect(ws.isOpen).toBe(true)
      expect(() => ws.disconnect()).not.toThrow()
      expect(callbacks.onClose).toHaveBeenCalledTimes(2)
      expect(vi.getTimerCount()).toBe(0)
      expectGenericLogs()
    },
  )

  it("connect manual cancela backoff y no deja un retry que duplique el socket", () => {
    const ws = client()
    ws.connect()
    socket().emitOpen()
    socket().emitClose()
    expect(vi.getTimerCount()).toBe(1)
    vi.advanceTimersByTime(500)
    ws.connect()
    expect(FakeWebSocket.instances).toHaveLength(2)
    const second = socket()
    expect(ws.state).toBe(CONNECTING)
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(60000)
    expect(FakeWebSocket.instances).toHaveLength(2)
    second.emitOpen()
    expect(ws.isOpen).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("onerror registra un diagnóstico genérico sin error/evento/URL secretos", () => {
    const ws = client(`${URL}?token=${SECRET}`)
    ws.connect()
    const first = socket()
    const handlers = retainedHandlers(first)
    expect(() => handlers.error({
      message: SECRET, target: first, error: new Error(SECRET),
    })).not.toThrow()
    expectGenericLogs()
    ws.disconnect()
    expect(ws.state).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("disconnect durante CONNECTING cierra y bloquea una apertura tardía", () => {
    const ws = client()
    const opened = vi.fn()
    const closed = vi.fn()
    ws.onOpen(opened)
    ws.onClose(closed)
    ws.subscribe("candles")
    ws.connect()
    const first = socket()
    const old = retainedHandlers(first)
    ws.disconnect()
    old.open(OPEN_EVENT)
    old.close(CLOSE_EVENT)
    expect(opened).not.toHaveBeenCalled()
    expect(closed).toHaveBeenCalledTimes(1)
    expect(first.readyState).toBe(CLOSED)
    expect(first.closeCalls).toBe(1)
    expect(first.sentMessages).toEqual([])
    expect(ws.state).toBeNull()
    expectDetached(first)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("disconnect sin socket es terminal y no inventa eventos de cierre", () => {
    const ws = client()
    const closed = vi.fn()
    ws.onClose(closed)
    ws.disconnect()
    ws.disconnect()
    ws.connect()
    expect(closed).not.toHaveBeenCalled()
    expect(FakeWebSocket.attempts).toHaveLength(0)
    expect(ws.state).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("disconnect reentrante desde onOpen detiene los envíos pendientes", () => {
    const ws = client()
    const closed = vi.fn()
    ws.onClose(closed)
    ws.onOpen(() => ws.disconnect())
    ws.subscribe("candles")
    ws.connect()
    const first = socket()
    expect(() => first.emitOpen()).not.toThrow()
    expect(first.sentMessages).toEqual([])
    expect(first.readyState).toBe(CLOSED)
    expect(closed).toHaveBeenCalledTimes(1)
    expect(ws.state).toBeNull()
    expectDetached(first)
    expect(vi.getTimerCount()).toBe(0)
  })

  it("disconnect reentrante desde onClose no duplica eventos ni deja retry", () => {
    const ws = client()
    const closed = vi.fn(() => ws.disconnect())
    ws.onClose(closed)
    ws.connect()
    socket().emitOpen()
    expect(() => socket().emitClose()).not.toThrow()
    expect(closed).toHaveBeenCalledTimes(1)
    expect(ws.state).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
    vi.advanceTimersByTime(60000)
    expect(FakeWebSocket.instances).toHaveLength(1)
  })
})

describe("C09 — useMarketData real con hooks React controlados", () => {
  const fixture: CandleHistoryResponse = {
    symbol: "BTC/USDT", interval: "1m", data_source: "TEST_ONLY",
    candles: [{ ...bar("bar_closed").candle, open_time: 100, close_time: 200 }],
  }
  const channels = ["candles", "updates", "status", "gaps"]
  const marketStatus: MarketStatus & { type: "status" } = {
    type: "status", symbol: "BTC/USDT", connected: true, reconnecting: false,
    closed_candles_count: 1, last_bar_open_time: 1000,
    interval: "1m", data_source: "market_engine",
  }

  function renderHook() {
    return reactHooks.render(() => useMarketData("BTC/USDT"))
  }

  async function mountHook() {
    renderHook()
    // Vacía las promesas de HTTP sin avanzar el heartbeat ni usar timers reales.
    await vi.advanceTimersByTimeAsync(0)
    return renderHook()
  }

  beforeEach(() => {
    vi.stubGlobal("window", { location: { origin: "http://localhost" } })
    vi.stubEnv("VITE_API_URL", "")
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => fixture,
    })))
  })

  it("suscribe cuatro canales, incluyendo updates, y permanece false antes de onopen", async () => {
    const result = await mountHook()
    const first = socket()
    expect(result.wsConnected).toBe(false)
    expect(first.readyState).toBe(CONNECTING)
    expect(first.sentMessages).toEqual([])
    first.readyState = OPEN
    expect(renderHook().wsConnected).toBe(false)
    first.emitOpen()
    expect(subscriptions(first)).toHaveLength(4)
    expect(subscriptions(first).map(message => message.channel).sort()).toEqual([...channels].sort())
    expect(renderHook().wsConnected).toBe(false)
    first.emitOpen()
    expect(subscriptions(first)).toHaveLength(4)
  })

  it("conserva la fixture TEST_ONLY al abrir/cerrar sin fingir datos live", async () => {
    const initial = await mountHook()
    expect(initial.loading).toBe(false)
    expect(initial.error).toBeNull()
    expect(initial.dataSource).toBe("TEST_ONLY")
    expect(initial.candles).toEqual(fixture.candles)
    expect(initial.interval).toBe("1m")
    socket().emitOpen()
    socket().emitMessage(JSON.stringify(marketStatus))
    expect(renderHook().wsConnected).toBe(false)
    socket().emitClose()
    const closed = renderHook()
    expect(closed.wsConnected).toBe(false)
    expect(closed.status).toBeNull()
    expect(closed.dataSource).toBe("TEST_ONLY")
    expect(closed.candles).toEqual(fixture.candles)
    expect(closed.interval).toBe("1m")
  })

  it("cierre inesperado pone conexión false y resetea status; reconexión resuscribe sin duplicados", async () => {
    await mountHook()
    const first = socket()
    first.emitOpen()
    first.emitMessage(JSON.stringify(bar()))
    first.emitMessage(JSON.stringify(marketStatus))
    const live = renderHook()
    expect(live.dataSource).toBe("market_engine")
    expect(live.candles).toEqual([bar().candle])
    expect(live.wsConnected).toBe(true)
    expect(live.status).toEqual(marketStatus)
    first.emitClose()
    const closed = renderHook()
    expect(closed.wsConnected).toBe(false)
    expect(closed.status).toBeNull()
    expect(vi.getTimerCount()).toBe(2) // Heartbeat + único backoff.

    vi.advanceTimersByTime(1000)
    const second = socket()
    expect(second).not.toBe(first)
    expect(renderHook().wsConnected).toBe(false)
    second.emitOpen()
    expect(renderHook().wsConnected).toBe(false)
    expect(subscriptions(second)).toHaveLength(4)
    expect(subscriptions(second).map(message => message.channel).sort()).toEqual([...channels].sort())
    second.emitMessage(JSON.stringify(marketStatus))
    expect(renderHook().wsConnected).toBe(true)
    expect(vi.getTimerCount()).toBe(1)
  })

  it.each(["abierto", "backoff"] as const)(
    "desmontaje con socket %s limpia heartbeat, callbacks y todos los sockets",
    async phase => {
      await mountHook()
      const first = socket()
      first.emitOpen()
      vi.advanceTimersByTime(30000)
      expect(messages(first).filter(message => message.type === "ping")).toEqual([{ type: "ping" }])
      const old = retainedHandlers(first)
      const prepare = { abierto: () => {}, backoff: () => first.emitClose() }
      prepare[phase]()
      const sentBeforeUnmount = [...first.sentMessages]
      reactHooks.unmount()

      expect(vi.getTimerCount()).toBe(0)
      expect(first.readyState).toBe(CLOSED)
      expectDetached(first)
      expect(() => {
        old.open(OPEN_EVENT)
        old.message({ data: JSON.stringify(marketStatus) })
        old.close(CLOSE_EVENT)
        old.error(new Error(SECRET))
      }).not.toThrow()
      vi.advanceTimersByTime(120000)
      expect(FakeWebSocket.instances).toHaveLength(1)
      expect(FakeWebSocket.instances.every(sock => sock.readyState === CLOSED)).toBe(true)
      expect(first.sentMessages).toEqual(sentBeforeUnmount)
      expect(vi.getTimerCount()).toBe(0)
    },
  )
})
