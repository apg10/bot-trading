import { useState, type ChangeEvent, type FormEvent } from "react"
import {
  getComparison,
  type ExperimentComparisonReport,
  type NormalizedExperimentResult,
  type NormalizedMetric,
  type NormalizedMetricAggregate,
} from "../api/laboratory"

type FormValues = {
  experimentId: string
  experimentVersion: string
  comparisonId: string
  comparisonVersion: string
}

const initialForm: FormValues = {
  experimentId: "",
  experimentVersion: "",
  comparisonId: "",
  comparisonVersion: "",
}

export function metricState(metric: NormalizedMetric): string {
  if (metric.status === "available" && typeof metric.value === "string") return metric.value
  return metric.status
}

export function aggregateValue(value: string | null, availableCount: number): string {
  return availableCount === 0 || value === null ? "—" : value
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
            <span aria-label={`${metric.metric_id} ${metric.status}`}>
              {metric.status === "available" ? metricState(metric) : metric.status}
            </span>
          </div>
        ))}
      </div>
    </article>
  )
}

function AggregateRow({ aggregate }: { aggregate: NormalizedMetricAggregate }) {
  return (
    <tr>
      <th scope="row">{aggregate.metric_id}{aggregate.unit ? ` (${aggregate.unit})` : ""}</th>
      <td>{aggregateValue(aggregate.mean, aggregate.available_count)}</td>
      <td>{aggregateValue(aggregate.minimum, aggregate.available_count)}</td>
      <td>{aggregateValue(aggregate.maximum, aggregate.available_count)}</td>
      <td>{aggregate.available_count}</td>
      <td>{aggregate.undefined_count}</td>
      <td>{aggregate.insufficient_data_count}</td>
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
        <p role="status">No hay agregados disponibles.</p>
      ) : (
        <div className="card" style={{ overflowX: "auto" }}>
          <table>
            <thead><tr>
              <th>Métrica</th><th>Media</th><th>Mínimo</th><th>Máximo</th>
              <th>Disponibles</th><th>undefined</th><th>insufficient_data</th>
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

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (Object.values(form).some(value => value.trim() === "")) {
      setReport(null)
      setError("Completa los cuatro identificadores.")
      return
    }
    setLoading(true)
    setError(null)
    try {
      const next = await getComparison(
        form.experimentId, form.experimentVersion, form.comparisonId, form.comparisonVersion,
      )
      setReport(next)
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
    <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, overflow: "hidden" }}>
      <form onSubmit={submit} style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-sm)", alignItems: "end", padding: "var(--space-lg)", borderBottom: "1px solid var(--color-border)" }}>
        {([
          ["experimentId", "Experiment ID"],
          ["experimentVersion", "Experiment version"],
          ["comparisonId", "Comparison ID"],
          ["comparisonVersion", "Comparison version"],
        ] as const).map(([key, label]) => (
          <label key={key} style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)", flex: "1 1 180px" }}>
            <span>{label}</span>
            <input id={key} name={key} aria-label={label} value={form[key]} onChange={update(key)} disabled={loading} />
          </label>
        ))}
        <button type="submit" disabled={loading}>
          {loading ? "Cargando…" : "Consultar comparación"}
        </button>
      </form>
      <div aria-live="polite" style={{ padding: "0 var(--space-lg)" }}>
        {loading && <p role="status">Cargando comparación…</p>}
        {error && <p role="alert" style={{ color: "var(--color-negative)" }}>{error}</p>}
      </div>
      {report ? <LaboratoryComparisonResult report={report} /> : !loading && !error ? (
        <p role="status" style={{ padding: "var(--space-lg)", color: "var(--color-text-muted)" }}>
          Sin resultados. Introduce los identificadores para consultar una comparación.
        </p>
      ) : null}
    </div>
  )
}

export default LaboratoryView
