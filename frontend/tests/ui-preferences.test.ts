import { describe, expect, expectTypeOf, it } from "vitest"
import {
  DEFAULT_ACTIVE_VIEW,
  DEFAULT_BOTTOM_TAB,
  DEFAULT_INTERVAL,
  DEFAULT_PREFERENCES,
  normalizePreferences,
  type ActiveView,
  type BottomTab,
  type Interval,
  type UiPreferences,
} from "../src/preferences/uiPreferences"

describe("UI preferences contract", () => {
  it("normalizes valid preferences", () => {
    const result = normalizePreferences({
      schemaVersion: "1",
      view: {
        activeView: "laboratorio",
        bottomTab: "orders",
        interval: "15m",
      },
      metadata: { lastSaved: "2026-01-01T00:00:00.000Z" },
    })

    expect(result).toEqual({
      schemaVersion: "1",
      view: { activeView: "laboratorio", bottomTab: "orders", interval: "15m" },
      metadata: { lastSaved: "2026-01-01T00:00:00.000Z", schemaVersion: "1" },
    })
  })

  it.each([null, undefined])("returns defaults for %s", (raw) => {
    const result = normalizePreferences(raw)
    expect(result).toMatchObject({
      schemaVersion: "1",
      view: { activeView: DEFAULT_ACTIVE_VIEW, bottomTab: DEFAULT_BOTTOM_TAB, interval: DEFAULT_INTERVAL },
      metadata: { lastSaved: expect.any(String), schemaVersion: "1" },
    })
    expect(result).not.toBe(DEFAULT_PREFERENCES)
  })

  it("returns defaults for an invalid schema version", () => {
    const result = normalizePreferences({ schemaVersion: "2", view: {} })
    expect(result).toMatchObject({ view: { activeView: DEFAULT_ACTIVE_VIEW, bottomTab: DEFAULT_BOTTOM_TAB, interval: DEFAULT_INTERVAL } })
    expect(result).not.toBe(DEFAULT_PREFERENCES)
  })

  it("falls back independently for invalid enum values", () => {
    expect(normalizePreferences({
      schemaVersion: "1",
      view: { activeView: "invalid", bottomTab: "invalid", interval: "invalid" },
    }).view).toEqual({
      activeView: DEFAULT_ACTIVE_VIEW,
      bottomTab: DEFAULT_BOTTOM_TAB,
      interval: DEFAULT_INTERVAL,
    })

    expect(normalizePreferences({
      schemaVersion: "1",
      view: { activeView: "bot", bottomTab: "invalid", interval: "1h" },
    }).view).toEqual({ activeView: "bot", bottomTab: DEFAULT_BOTTOM_TAB, interval: "1h" })
  })

  it("fills defaults for missing view fields", () => {
    expect(normalizePreferences({ schemaVersion: "1", view: {} }).view).toEqual({
      activeView: DEFAULT_ACTIVE_VIEW,
      bottomTab: DEFAULT_BOTTOM_TAB,
      interval: DEFAULT_INTERVAL,
    })
  })

  it("ignores unknown fields", () => {
    expect(normalizePreferences({
      schemaVersion: "1",
      unknown: "ignored",
      view: {
        activeView: "terminal",
        bottomTab: "fills",
        interval: "1m",
        unknown: true,
      },
    })).toEqual({
      schemaVersion: "1",
      view: { activeView: "terminal", bottomTab: "fills", interval: "1m" },
      metadata: { lastSaved: expect.any(String), schemaVersion: "1" },
    })
  })

  it("rejects invalid ISO-8601 metadata timestamps", () => {
    const result = normalizePreferences({
      schemaVersion: "1",
      view: {},
      metadata: { lastSaved: "not-a-timestamp" },
    })

    expect(result.metadata.lastSaved).not.toBe("not-a-timestamp")
    expect(result.metadata.lastSaved).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/)
    expect(new Date(result.metadata.lastSaved).getTime()).not.toBeNaN()
  })

  it("returns defaults for array input", () => {
    const result = normalizePreferences([])
    expect(result).toMatchObject({ view: { activeView: DEFAULT_ACTIVE_VIEW, bottomTab: DEFAULT_BOTTOM_TAB, interval: DEFAULT_INTERVAL } })
    expect(result).not.toBe(DEFAULT_PREFERENCES)
  })

  it("returns defaults for string input", () => {
    const result = normalizePreferences("preferences")
    expect(result).toMatchObject({ view: { activeView: DEFAULT_ACTIVE_VIEW, bottomTab: DEFAULT_BOTTOM_TAB, interval: DEFAULT_INTERVAL } })
    expect(result).not.toBe(DEFAULT_PREFERENCES)
  })

  it("exposes the expected default values", () => {
    expect(DEFAULT_ACTIVE_VIEW).toBe("terminal")
    expect(DEFAULT_BOTTOM_TAB).toBe("balances")
    expect(DEFAULT_INTERVAL).toBe("1m")
    expect(DEFAULT_PREFERENCES).toMatchObject({
      schemaVersion: "1",
      view: { activeView: "terminal", bottomTab: "balances", interval: "1m" },
      metadata: { schemaVersion: "1" },
    })
    expect(DEFAULT_PREFERENCES.metadata.lastSaved).toEqual(expect.any(String))
  })

  it("returns defaults when schemaVersion is missing or mismatched", () => {
    const missing = normalizePreferences({ view: {} })
    expect(missing).toMatchObject({ view: { activeView: DEFAULT_ACTIVE_VIEW, bottomTab: DEFAULT_BOTTOM_TAB, interval: DEFAULT_INTERVAL } })
    expect(missing).not.toBe(DEFAULT_PREFERENCES)

    const mismatched = normalizePreferences({ schemaVersion: "0", view: {} })
    expect(mismatched).toMatchObject({ view: { activeView: DEFAULT_ACTIVE_VIEW, bottomTab: DEFAULT_BOTTOM_TAB, interval: DEFAULT_INTERVAL } })
    expect(mismatched).not.toBe(DEFAULT_PREFERENCES)
  })

  it("does not share mutable defaults between calls", () => {
    const first = normalizePreferences(null)
    first.view.activeView = "bot"

    const second = normalizePreferences(null)
    expect(second.view.activeView).toBe(DEFAULT_ACTIVE_VIEW)
    expect(second).not.toBe(first)
  })

  it("preserves the declared preference types", () => {
    expectTypeOf<ActiveView>().toEqualTypeOf<"terminal" | "laboratorio" | "bot">()
    expectTypeOf<BottomTab>().toEqualTypeOf<"balances" | "positions" | "orders" | "fills" | "activity">()
    expectTypeOf<Interval>().toEqualTypeOf<"1s" | "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "2h" | "4h" | "6h" | "8h" | "12h" | "1d" | "3d" | "1w">()
    expectTypeOf(DEFAULT_PREFERENCES).toEqualTypeOf<UiPreferences>()
  })
})
