import { valueLabel, type ComparisonResult as Comparison } from "../lib/contracts";

export default function ComparisonResult({ comparison }: { comparison: Comparison | null }) {
  if (!comparison) return null;
  const currency = comparison.reporting_currency;
  return <section className="panel" aria-label="Five-year conditional comparison">
    <h2>Five-year conditional comparison</h2>
    <p>As of {comparison.as_of} / {currency}. Common starting value: {valueLabel(comparison.starting_value, currency)}.</p>
    <ul>{comparison.qualifications.map((item, index) => <li key={index}>{item}</li>)}</ul>
    <details><summary>Dated supplied fund facts and effects</summary>
      {comparison.inputs.fund_facts.length === 0 && <p>Fund exposure, costs and income facts are unknown.</p>}
      {comparison.inputs.fund_facts.map(fact => <div key={fact.position_id}>
        <h3>{fact.position_id}: {fact.exposure}</h3><p>{fact.source} / {fact.as_of} / User supplied</p>
        {fact.source_url && /^https?:\/\//.test(fact.source_url) && <a href={fact.source_url} target="_blank" rel="noreferrer">Fund source</a>}
        <p>Annual cost fraction: {fact.annual_cost ?? "Unknown"}. Income yield fraction: {fact.income_yield ?? "Unknown"}.</p>
      </div>)}
      {comparison.inputs.effects.map(effect => <p key={effect.alternative_id}>{effect.alternative_id}: transaction cost {valueLabel(effect.transaction_cost, currency)};
        terminal tax {valueLabel(effect.terminal_tax, currency)}. {effect.source} / {effect.as_of} / User supplied</p>)}
    </details>
    {comparison.alternatives.map(alt => <section key={alt.selection.id} aria-label={`Comparison ${alt.selection.kind}`}>
      <h3>{alt.selection.kind === "no_action" ? "No action" : alt.selection.kind === "short_bill" ? "Short government bills" : alt.selection.kind === "etf" ? "Diversified ETF" : "Cash"}</h3>
      <p>{alt.selection.kind === "no_action" ? "Retain actual positions" : "Selected destination"}: {alt.position_ids.join(", ")}</p>
      <div className="table-scroll"><table aria-label={`${alt.selection.id} conditional cases`}>
        <thead><tr><th>Conditional case</th><th>Known terminal subtotal ({currency})</th><th>Fully specified conditional value ({currency})</th></tr></thead>
        <tbody>{alt.cases.map(row => <tr key={row.name}><td>{row.name}</td><td>{valueLabel(row.known_terminal_value, currency)}</td><td>{valueLabel(row.terminal_value, currency)}</td></tr>)}</tbody>
      </table></div>
      <p className="muted small">Known subtotals exclude unknown effects. A fully specified case remains a conditional judgment, not a forecast.</p>
      {alt.cases.map(row => <details key={row.name}><summary>{row.name}: drivers, assumptions and uncertainty</summary>
        <p><strong>Downside:</strong> {row.judgment.downside}</p>
        <h4>Pivotal assumptions</h4><ul>{row.judgment.assumptions.map((item, i) => <li key={i}>{item}</li>)}</ul>
        <h4>Uncertainty</h4><ul>{row.judgment.uncertainty.map((item, i) => <li key={i}>{item}</li>)}</ul>
        {row.judgment.drivers.map(driver => <div key={driver.position_id}>
          <h4>{driver.position_id}: future driver judgments</h4>
          <p>Years one through five, annual fractions. Return basis: {driver.return_basis.replaceAll("_", " ")}; cost basis: {driver.cost_basis.replaceAll("_", " ")}; income reinvested: {driver.reinvest ? "Yes" : "No"}.</p>
          <p>Exposure return path: {driver.annual_returns?.join(", ") ?? "Not modeled"}. Rate path: {driver.annual_rates?.join(", ") ?? "Not modeled"}.</p>
          <p>Income multipliers: {driver.income_multipliers?.join(", ") ?? "Not separately modeled"}. FX multipliers relative to starting FX: {driver.fx_multipliers.join(", ")}.</p>
        </div>)}
        {row.components.map(component => <div key={component.position_id}>
          <h4>{component.position_id}: Python calculation</h4>
          <p>Starting local capital: {valueLabel(component.starting_local_value, component.local_currency)}. Terminal invested local capital: {valueLabel(component.terminal_local_value, component.local_currency)}.</p>
          <p>Known terminal value including distributed income: {valueLabel(component.known_terminal_value, currency)}.</p>
          <p>Starting FX: {component.fx_used ? `${component.fx_used.rate} ${component.fx_used.to_currency}/${component.fx_used.from_currency} / ${component.fx_used.as_of} / ${component.fx_used.source} / ${component.fx_used.status}` : component.local_currency === currency ? "Same currency; identity conversion" : "Unknown; foreign-currency conversion unavailable"}.</p>
        </div>)}
        <h4>Calculation qualifications</h4><ul>{row.qualifications.map((item, i) => <li key={i}>{item}</li>)}</ul>
      </details>)}
    </section>)}
    <details><summary>Scenario calculation basis</summary><p>{comparison.calculation_basis}</p></details>
  </section>;
}
