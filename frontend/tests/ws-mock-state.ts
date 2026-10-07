// Estado singleton compartido entre el mock WebSocket y los tests.

export interface MockWSInstance {
  url: string
  readyState: number
  onmessage: ((event: { data: string }) => void) | null
  onopen: (() => void) | null
  onclose: (() => void) | null
  onerror: ((err: unknown) => void) | null
  sentMessages: string[]
  emitMessage(data: string): void
  emitOpen(): void
  emitClose(): void
}

export const mockState = {
  current: null as MockWSInstance | null,
  reset() { this.current = null },
}
