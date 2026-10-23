import { useState, type ChangeEvent, type FormEvent } from "react"
import {
  getCapitalCurve,
  getComparison,
  getCostBreakdown,
  getInspector,
  type CapitalCurveReport,
  type CostValue,
  type CostBreakdownReport,
  type ExperimentComparisonReport,
  type NormalizedExperimentResult,
  type NormalizedMetric,
  type NormalizedMetricAggregate,
  type OperationInspectorReport,
} from "../api/laboratory"

type FormValues = {
  experimentId: string
  experimentVersion: string
  comparisonId: string
  comparisonVersion: string
  accountId: string
  sessionId: string
  quoteAsset: string
}

const initialForm: FormValues = {
  experimentId: "",
  experimentVersion: "",
  comparisonId: "",
  comparisonVersion: "",
  accountId: "",
  sessionId: "",
  quoteAsset: "USDT",
}

export function metricState(metric: NormalizedMetric): string {
  if (metric.status === "available" && typeof metric.value === "string") return metric.value
  return metric.status
}

export function aggregateValue(value: string | null, availableCount: number): string {
  return availableCount === 0 || value === null ? "—" : value
}

function statusMessage(status: string, reason: string | null): string {
  return `${status}${reason ? `: ${reason}` : ""}`
}

function costValueDisplay(value: CostValue | null): string {
  if (!value) return "unknown"
  return value.amount ?? value.value ?? statusMessage(value.status, value.reason)
}

export function LaboratoryCapitalCurve({ report }: { report: CapitalCurveReport }) {
  const hasObservations = report.points.length > 0
  return (
    <section className="card" aria-label="Curva de capital y drawdown">
      <div className="card-title">Capital cash · {report.quote_asset}</div>
      {report.status !== "available" || !hasObservations ? (
        <>
          <p role="status">{report.status !== "available" ? statusMessage(report.status, report.reason) : "insufficient_data: No hay observaciones de capital."}</p>
          <p aria-label="Curva de capital sin observaciones">—</p>
        </>
      ) : (
        <>
          <svg aria-label="Capital curve" role="img" viewBox="0 0 320 70" width="100%" height="70">
            <title>{`Curva de capital cash en ${report.quote_asset}`}</title>
            <polyline
              fill="none" stroke="currentColor" strokeWidth="2"
              points={report.points.map((_point, index) => `${index * (report.points.length > 1 ? 300 / (report.points.length - 1) : 0) + 10},25`).join(" ")}
            />
            {report.points.map((point, index) => (
              <circle key={point.fill_id} cx={index * (report.points.length > 1 ? 300 / (report.points.length - 1) : 0) + 10} cy="25" r="3"
                data-balance={point.balance} data-drawdown={point.drawdown} aria-label={`${point.fill_id} ${point.balance}`} />
            ))}
          </svg>
          <div className="card-detail">Saldo inicial: {report.initial_balance}</div>
          <div style={{ overflowX: "auto" }}>
            <table>
              <caption>Observaciones de la curva de capital</caption>
              <thead><tr><th scope="col">Fill</th><th scope="col">Saldo cash</th><th scope="col">Peak</th><th scope="col">Drawdown</th></tr></thead>
              <tbody>{report.points.map(point => (
                <tr key={point.fill_id}><th scope="row">{point.fill_id}</th><td>{point.balance}</td><td>{point.peak_balance}</td><td>{point.drawdown}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

export function LaboratoryCostBreakdown({ report }: { report: CostBreakdownReport }) {
  return (
      <section className="card" aria-label="Cost breakdown">
      <div className="card-title">Costes por fill · {report.quote_asset}</div>
      {report.status !== "available" && <p role="status">{statusMessage(report.status, report.reason)}</p>}
      {report.fills.length === 0 && (
        <p aria-label="Costes sin observaciones">—</p>
      )}
      {report.fills.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table>
            <caption>Desglose de costes por fill</caption>
            <thead><tr><th scope="col">Fill</th><th scope="col">Side</th><th scope="col">Qty</th><th scope="col">Price</th><th scope="col">Commission</th><th scope="col">Slippage</th><th scope="col">Total</th></tr></thead>
            <tbody>{report.fills.map(fill => (
              <tr key={fill.fill_id}>
                <th scope="row">{fill.fill_id}</th><td>{fill.side}</td><td>{fill.qty}</td><td>{fill.price}</td>
                <td>{costValueDisplay(fill.commission)}</td>
                <td>{costValueDisplay(fill.slippage)}</td>
                <td>{costValueDisplay(fill.total_cost)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function LaboratoryInspector({ report }: { report: OperationInspectorReport }) {
  const hasAuditData = report.decisions.length > 0
  return (
    <section className="card" aria-label="Operation inspector">
      <div className="card-title">Inspector de operaciones · {report.account_id}/{report.session_id}</div>
      {report.status !== "available" || !hasAuditData ? (
        <>
          <p role="status">{report.status !== "available" ? statusMessage(report.status, report.reason) : "insufficient_data: No hay decisiones auditadas."}</p>
          <p aria-label="Cadena causal sin datos de auditoría">—</p>
        </>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table>
            <caption>Cadena causal de decisiones, órdenes y fills</caption>
            <thead><tr><th scope="col">Decision</th><th scope="col">Timestamp</th><th scope="col">Candle</th><th scope="col">OHLCV</th><th scope="col">Reason</th><th scope="col">Order</th><th scope="col">Fill</th></tr></thead>
            <tbody>{report.decisions.map(decision => (
              <tr key={decision.decision_id}>
                <th scope="row">{decision.side} · {decision.decision_id}</th>
                <td>{decision.timestamp}</td>
                <td>{decision.candle_index}</td>
                <td>{decision.candle.open} / {decision.candle.high} / {decision.candle.low} / {decision.candle.close} / {decision.candle.volume}</td>
                <td>{decision.reason}</td>
                <td>{decision.orders.length === 0 ? "insufficient_data" : decision.orders.map(order => String(order.order_id ?? "unknown")).join(", ")}</td>
                <td>{decision.fills.length === 0 ? "insufficient_data" : decision.fills.map(fill => String(fill.fill_id ?? "unknown")).join(", ")}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function VariantCard({ result }: { result: NormalizedExperimentResult }) {
  return (
    <article className="card" aria-label={`Variante ${result.variant_id}`}>
      <div className="card-title">Variante {result.variant_id}</div>
      <div className="card-detail">Resultado {result.result_id}</div>
      <div style={{ marginTop: "var(--space-sm)" }}>
        {result.metrics.map(metric => (
          <div key={metric.metric_id} style={{ display: "flex", justifyContent: "space-between", gap: "var(--space-sm)", padding: "3px 0" }}>
            <span>{metric.metric_id}{metric.unit ? ` (${metric.unit})` : ""}</span>
            <span aria-label={`${metric.metric_id} ${metric.status}${metric.reason ? `: ${metric.reason}` : ""}`}>
              {metric.status === "available" ? metricState(metric) : metric.status}
              {metric.status !== "available" && metric.reason ? `: ${metric.reason}` : ""}
            </span>
          </div>
        ))}
      </div>
    </article>
  )
}

function AggregateRow({ aggregate }: { aggregate: NormalizedMetricAggregate }) {
  const status = aggregate.available_count === 0
    ? (aggregate.insufficient_data_count > 0 ? "insufficient_data" : "undefined")
    : "available"
  return (
    <tr>
      <th scope="row">{aggregate.metric_id}{aggregate.unit ? ` (${aggregate.unit})` : ""}</th>
      <td>{aggregateValue(aggregate.mean, aggregate.available_count)}</td>
      <td>{aggregateValue(aggregate.minimum, aggregate.available_count)}</td>
      <td>{aggregateValue(aggregate.maximum, aggregate.available_count)}</td>
      <td>{aggregate.available_count}</td>
      <td>{aggregate.undefined_count}</td>
      <td>{aggregate.insufficient_data_count}</td>
      <td>{status}</td>
    </tr>
  )
}

export function LaboratoryComparisonResult({ report }: { report: ExperimentComparisonReport }) {
  const results = report.comparison.normalized_results
  const first = results[0]
  if (!first) return <p role="status">La comparación no contiene resultados.</p>

  return (
    <div aria-label="Resultado de comparación" style={{ padding: "var(--space-lg)", overflowY: "auto", flex: 1 }}>
      <section className="card">
        <div className="card-title">Hipótesis</div>
        <p>{report.comparison.hypothesis}</p>
        <div className="card-detail" style={{ marginTop: "var(--space-sm)" }}>
          Experimento: {first.experiment_ref.experiment_id} · versión {first.experiment_ref.experiment_version}
        </div>
        <div className="card-detail">
          Comparación: {report.comparison.comparison_id} · versión {report.comparison.comparison_version}
        </div>
        <div className="card-detail">
          Periodos efectivos: {first.effective_periods.map(period =>
            `${period.purpose} ${period.start_ms}–${period.end_ms}`).join(" · ")}
        </div>
      </section>

      <h2 style={{ fontSize: "var(--font-size-lg)", margin: "var(--space-lg) 0 var(--space-sm)" }}>Variantes</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: "var(--space-sm)" }}>
        {results.map(result => <VariantCard key={result.variant_id} result={result} />)}
      </div>

      <h2 style={{ fontSize: "var(--font-size-lg)", margin: "var(--space-lg) 0 var(--space-sm)" }}>Agregados deterministas</h2>
      {report.aggregates.length === 0 ? (
        <>
          <p role="status">insufficient_data: No hay agregados disponibles.</p>
          <p aria-label="Agregados sin observaciones">—</p>
        </>
      ) : (
        <div className="card" style={{ overflowX: "auto" }}>
          <table>
            <thead><tr>
              <th scope="col">Métrica</th><th scope="col">Media</th><th scope="col">Mínimo</th><th scope="col">Máximo</th>
              <th scope="col">Disponibles</th><th scope="col">undefined</th><th scope="col">insufficient_data</th><th scope="col">Estado</th>
            </tr></thead>
            <tbody>{report.aggregates.map(aggregate => <AggregateRow key={aggregate.metric_id} aggregate={aggregate} />)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function LaboratoryView() {
  const [form, setForm] = useState<FormValues>(initialForm)
  const [report, setReport] = useState<ExperimentComparisonReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [capitalCurve, setCapitalCurve] = useState<CapitalCurveReport | null>(null)
  const [costBreakdown, setCostBreakdown] = useState<CostBreakdownReport | null>(null)
  const [inspector, setInspector] = useState<OperationInspectorReport | null>(null)

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if ([form.experimentId, form.experimentVersion, form.comparisonId, form.comparisonVersion].some(value => value.trim() === "")) {
      setReport(null)
      setError("Completa los cuatro identificadores.")
      return
    }
    setLoading(true)
    setError(null)
    setCapitalCurve(null)
    setCostBreakdown(null)
    setInspector(null)
    try {
      const comparison = getComparison(
        form.experimentId, form.experimentVersion, form.comparisonId, form.comparisonVersion,
      )
      const hasMetricsContext = form.accountId.trim() !== "" && form.sessionId.trim() !== ""
      if (hasMetricsContext) {
        const [next, curve, costs, nextInspector] = await Promise.all([
          comparison,
          getCapitalCurve(form.accountId, form.sessionId, form.quoteAsset || "USDT"),
          getCostBreakdown(form.accountId, form.sessionId, form.quoteAsset || "USDT"),
          getInspector(form.accountId, form.sessionId),
        ])
        setReport(next)
        setCapitalCurve(curve)
        setCostBreakdown(costs)
        setInspector(nextInspector)
      } else {
        setReport(await comparison)
      }
    } catch (reason: unknown) {
      setReport(null)
      setError(reason instanceof Error ? reason.message : "No se pudo cargar la comparación")
    } finally {
      setLoading(false)
    }
  }

  const update = (key: keyof FormValues) => (event: ChangeEvent<HTMLInputElement>) => {
    setForm(current => ({ ...current, [key]: event.target.value }))
  }

  return (
    <div className="laboratory-view" style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, overflow: "hidden" }}>
      <form className="laboratory-query-form" aria-label="Consulta de comparación del laboratorio" onSubmit={submit} style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)", alignItems: "end", padding: "var(--space-lg)", borderBottom: "1px solid var(--color-border)" }}>
        {([
          ["experimentId", "Experiment ID"],
          ["experimentVersion", "Experiment version"],
          ["comparisonId", "Comparison ID"],
          ["comparisonVersion", "Comparison version"],
          ["accountId", "Account (optional)"],
          ["sessionId", "Session (optional)"],
          ["quoteAsset", "Quote asset (optional)"],
        ] as const).map(([key, label]) => (
          <label key={key} style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", flex: "1 1 180px" }}>
            <span>{label}</span>
            <input id={key} name={key} aria-label={label} value={form[key]} onChange={update(key)} disabled={loading} />
          </label>
        ))}
        <button type="submit" aria-label="Consultar comparación" disabled={loading}>
          {loading ? "Cargando…" : "Consultar comparación"}
        </button>
      </form>
      <div aria-live="polite" style={{ padding: "0 var(--space-lg)" }}>
        {loading && <p role="status">Cargando comparación…</p>}
        {error && <p role="alert" style={{ color: "var(--color-negative)" }}>{error}</p>}
      </div>
      {report ? <>
        <div className="laboratory-results">
          <LaboratoryComparisonResult report={report} />
        </div>
        {(capitalCurve || costBreakdown || inspector) && <div className="laboratory-projection-grid" style={{ display: "grid", gap: "var(--space-sm)", padding: "0 var(--space-lg) var(--space-lg)" }}>
           {capitalCurve && <LaboratoryCapitalCurve report={capitalCurve} />}
           {costBreakdown && <LaboratoryCostBreakdown report={costBreakdown} />}
           {inspector && <LaboratoryInspector report={inspector} />}
         </div>}
      </> : !loading && !error ? (
        <p role="status" style={{ padding: "var(--space-lg)", color: "var(--color-text-muted)" }}>
          Sin resultados. Introduce los identificadores para consultar una comparación.
        </p>
      ) : null}
    </div>
  )
}

export default LaboratoryView
