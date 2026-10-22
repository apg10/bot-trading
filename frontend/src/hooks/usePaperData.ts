import { useCallback, useEffect, useState } from "react"
import {
  getActivity,
  getBalances,
  getFills,
  getLifecycle,
  getOrders,
  getPositions,
} from "../api/paper"
import type {
  PaperActivityResponse,
  PaperBalance,
  PaperBalancesResponse,
  PaperFillsResponse,
  PaperLifecycleResponse,
  PaperOrdersResponse,
  PaperPositionsResponse,
  PaperReadItem,
} from "../api/paper"

export interface PaperDataState {
  balances: PaperBalance[]
  positions: PaperReadItem[]
  orders: PaperReadItem[]
  fills: PaperReadItem[]
  lifecycle: PaperLifecycleResponse | null
  activity: PaperReadItem[]
  loading: boolean
  error: string | null
  refetch: () => void
}

const emptyData = (): Omit<PaperDataState, "loading" | "error" | "refetch"> => ({
  balances: [],
  positions: [],
  orders: [],
  fills: [],
  lifecycle: null,
  activity: [],
})

function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : "PAPER data unavailable"
}

/**
 * Reads the PAPER projections only. An account is deliberately not inferred
 * from a URL, local storage, or a financial default.
 */
export function usePaperData(
  accountId?: string | null,
  sessionId?: string | null,
): PaperDataState {
  const [data, setData] = useState(() => emptyData())
  const [loading, setLoading] = useState(Boolean(accountId))
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)

  const refetch = useCallback(() => setRevision(value => value + 1), [])

  useEffect(() => {
    let cancelled = false

    if (!accountId) {
      setData(emptyData())
      setLoading(false)
      setError(null)
      return () => { cancelled = true }
    }

    setData(emptyData())
    setLoading(true)
    setError(null)

    // The lifecycle endpoint is intentionally represented by a resolved
    // promise when there is no session. This keeps the other five reads
    // independent while guaranteeing no lifecycle request is made.
    const lifecycleRequest = sessionId
      ? getLifecycle(accountId, sessionId)
      : Promise.resolve<PaperLifecycleResponse | null>(null)
    const ordersRequest = sessionId ? getOrders(accountId, sessionId) : getOrders(accountId)
    const fillsRequest = sessionId ? getFills(accountId, sessionId) : getFills(accountId)
    const activityRequest = sessionId ? getActivity(accountId, sessionId) : getActivity(accountId)

    Promise.allSettled([
      getBalances(accountId),
      getPositions(accountId),
      ordersRequest,
      fillsRequest,
      lifecycleRequest,
      activityRequest,
    ]).then(results => {
      if (cancelled) return

      const next = emptyData()
      const errors: unknown[] = []
      const [balances, positions, orders, fills, lifecycle, activity] = results

      const read = <T,>(
        result: PromiseSettledResult<T>,
        onValue: (value: T) => void,
      ) => {
        if (result.status === "fulfilled") onValue(result.value)
        else errors.push(result.reason)
      }

      read(balances, value => {
        next.balances = Array.from((value as PaperBalancesResponse).balances)
      })
      read(positions, value => {
        next.positions = Array.from((value as PaperPositionsResponse).positions)
      })
      read(orders, value => {
        next.orders = Array.from((value as PaperOrdersResponse).orders)
      })
      read(fills, value => {
        next.fills = Array.from((value as PaperFillsResponse).fills)
      })
      read(lifecycle, value => {
        next.lifecycle = value as PaperLifecycleResponse | null
      })
      read(activity, value => {
        next.activity = Array.from((value as PaperActivityResponse).activity)
      })

      setData(next)
      setError(errors.length > 0 ? errorMessage(errors[0]) : null)
      setLoading(false)
    })

    return () => { cancelled = true }
  }, [accountId, sessionId, revision])

  return { ...data, loading, error, refetch }
}
