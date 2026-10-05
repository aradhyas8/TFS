import { valueLabel, weightLabel, type GuardrailReview } from "../lib/contracts";

const categoryLabel = (category: string) => ({ stocks: "Individual stocks", diversified_etfs: "Diversified ETFs",
  sector_theme_etfs: "Sector/theme ETFs", excess_cash: "Deliberate excess cash", cash: "Cash" }[category] || category);

export default function GuardrailResult({ review, currency, label }: { review: GuardrailReview | null; currency: string; label: string }) {
  if (!review) return <p className="qualification">Personal guardrails and baseline are unknown; no limits or target mix were inferred.</p>;
  const { active, settings } = review;
  return <div>
    <h3>{label} portfolio guardrails</h3>
    <p className="muted small">Company cap: {weightLabel(settings.single_company_cap ?? null)}. Active budget: {weightLabel(settings.active_budget ?? null)}.
      Indirect cap policy: {settings.indirect_cap_policy === "include_known_indirect" ? "Include known indirect ETF overlap" : settings.indirect_cap_policy === "direct_only" ? "Direct stock only (indirect overlap noted)" : "Unknown"}.</p>
    {review.companies.length > 0 && <div className="table-scroll"><table aria-label={`${label} company cap checks`}>
      <thead><tr><th>Company</th><th>Weight / cap</th><th>Check</th><th>Excess / reduction to retained cash ({currency})</th></tr></thead>
      <tbody>{review.companies.map(company => <tr key={company.company_id}>
        <td>{company.company_name}<small>{company.explanation}</small></td>
        <td>{weightLabel(company.current_weight)} / {weightLabel(company.cap)}
          {company.indirect_weight && company.indirect_weight !== "0.00000000" && (
            <small>Direct: {weightLabel(company.direct_weight ?? null)} | Indirect: {weightLabel(company.indirect_weight ?? null)}</small>
          )}</td><td>{company.status.replaceAll("_", " ")}</td>
        <td>{valueLabel(company.excess_value, currency)}<small>Reduction to retained cash: {valueLabel(company.reduction_to_cash, currency)}</small></td>
      </tr>)}</tbody>
    </table></div>}
    <div className="qualification"><strong>Active-budget check: {active.status.replaceAll("_", " ")}</strong>
      <p>Active exposure: {valueLabel(active.value, currency)} / {weightLabel(active.weight)}. Budget: {weightLabel(active.budget)}.</p>
      {active.value === null && <p>Known active subtotal: {valueLabel(active.known_value, currency)}; missing contributions remain unknown.</p>}
      <ul>{Object.entries(active.contributions).map(([category, value]) => <li key={category}>{categoryLabel(category)}: {valueLabel(value, currency)}</li>)}</ul>
      {active.qualifications.map(note => <p key={note}>{note}</p>)}
    </div>
    {review.baseline_comparison.length > 0 ? <div className="table-scroll"><table aria-label={`${label} baseline comparison`}>
      <thead><tr><th>Category</th><th>Current weight</th><th>Supplied baseline</th><th>Difference</th></tr></thead>
      <tbody>{review.baseline_comparison.map(row => <tr key={row.category}><td>{categoryLabel(row.category)}</td>
        <td>{weightLabel(row.current_weight)}</td><td>{weightLabel(row.baseline_weight)}</td><td>{weightLabel(row.difference)}</td></tr>)}</tbody>
    </table></div> : <p className="muted">Baseline is unknown; no target allocation mix is inferred.</p>}
    {review.qualifications.map(note => <p className="muted small" key={note}>{note}</p>)}
  </div>;
}
