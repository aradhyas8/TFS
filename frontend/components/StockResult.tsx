import { valueLabel, type StockResult as Stock } from "../lib/contracts";

export default function StockResult({ stock, currency }: { stock: Stock | null; currency: string }) {
  if (!stock) return null;
  return <section className="panel" aria-label="Stock analysis">
    <h2>Stock analysis: {stock.research.company_id}</h2>
    <p>Decision date: {stock.as_of}. Sector: {stock.research.sector}. Company method: {stock.judgments.method.replaceAll("_", " ")}.</p>
    <p>{stock.judgments.mid_cycle_context || "Mid-cycle context is unknown."}</p>
    <h3>Primary evidence</h3>
    {stock.research.documents.length === 0 && <p>Primary evidence is unavailable.</p>}
    {stock.research.documents.map(doc => <article key={doc.id}>
      <h4><a href={doc.url} target="_blank" rel="noreferrer">{doc.title}</a></h4>
      <p>{doc.authority} | Published {doc.published_on} | As of {doc.as_of} | {doc.available ? "Reviewed extract available" : "Unavailable"}</p>
      <p>{doc.available ? doc.excerpt : "This source was not reviewed."}</p>
      <p className="muted small">Transcript Q&amp;A: {doc.available && doc.qa_available ? "Available in this evidence" : "Unavailable; not reviewed"}</p>
    </article>)}
    <h3>Reported facts and provenance</h3>
    <p className="muted small">Reported records below remain subject to the checks and conflicts disclosed in each calculated case. An unavailable or conflicting input does not become a known calculation.</p>
    <div className="table-scroll"><table aria-label="Company reported facts"><thead><tr><th>Metric</th><th>Reported record</th><th>Period and definition</th><th>Primary checks</th></tr></thead>
      <tbody>{stock.research.facts.map(fact => <tr key={fact.id}><td>{fact.metric}</td>
        <td>{fact.value ?? "Unknown"} {fact.currency || fact.unit}<small>{fact.document_ids.join(", ")}</small></td>
        <td>{fact.period_start || "Point in time / start unknown"} to {fact.period_end}<small>{fact.definition}</small></td>
        <td>Filing: {fact.filing_checked ? "checked" : "unknown"}; notes: {fact.notes_checked ? "checked" : "unknown"}; custom tags: {fact.custom_tags_checked ? "checked" : "unknown"}; segments: {fact.segments_checked ? "checked" : "unknown"}</td>
      </tr>)}</tbody></table></div>
    <h3>Python calculations: conditional company cases</h3>
    <p>Prices and discounted per-share values are in {currency}; position outcomes are in {stock.reporting_currency}. Unknown costs and personal tax effects remain unquantified.</p>
    <div className="table-scroll"><table aria-label="Company conditional cases"><thead><tr><th>Case</th><th>Exit price</th><th>Position outcome before unknown effects</th><th>Present value per share</th><th>Required exit multiple</th></tr></thead>
      <tbody>{stock.cases.map(row => <tr key={row.name}><td>{row.name}</td><td>{valueLabel(row.terminal_price, currency)}</td>
        <td>{valueLabel(row.known_terminal_value, stock.reporting_currency)}</td><td>{valueLabel(row.present_value_per_share, currency)}</td><td>{row.required_exit_multiple || "Unknown"}</td></tr>)}</tbody></table></div>
    <p className="muted small">Required exit multiples describe performance under each named operating path; they do not uniquely describe market beliefs. Compare alternatives below using their common starting capital.</p>
    {stock.cases.map(row => <details key={row.name}><summary>{row.name}: driver judgments, sensitivity and uncertainty</summary>
      <p>Growth: {row.judgment.growth.join(", ")}. Net margins: {row.judgment.margins.join(", ")}. Cash conversion: {row.judgment.cash_conversion.join(", ")}. Reinvestment: {row.judgment.reinvestment.join(", ")}.</p>
      <p>Dilution: {row.judgment.dilution.join(", ")}. Payout: {row.judgment.payout.join(", ")}. FX multipliers: {row.judgment.fx_multipliers.join(", ")}. Discount rate: {row.judgment.discount_rate}. Exit multiple: {row.judgment.exit_multiple}.</p>
      <p>Exit sensitivity: {row.judgment.exit_sensitivity.map((multiple, index) => `${multiple}: ${valueLabel(row.sensitivity_prices[index], currency)}`).join("; ")}</p>
      <ul>{[...row.judgment.assumptions, ...row.judgment.uncertainty, ...row.qualifications].map((item, index) => <li key={index}>{item}</li>)}</ul>
    </details>)}
    <ul>{stock.qualifications.map((item, index) => <li key={index}>{item}</li>)}</ul>
    <details><summary>Company calculation basis</summary><p>{stock.calculation_basis}</p></details>
  </section>;
}
