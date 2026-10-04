import { actionLabel, valueLabel, weightLabel, type Analysis } from "../lib/contracts";

function Points({ title, items }: { title: string; items: string[] }) {
  return <div className="review-points"><h3>{title}</h3><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}

export default function ReviewResult({ result }: { result: Analysis }) {
  const { portfolio: portfolio, recommendation: answer } = result;
  const currency = portfolio.reporting_currency;
  return <section className="review-result" aria-label="Completed portfolio review" aria-live="polite">
    <div className="question-bubble"><span className="eyebrow">YOUR QUESTION</span><p>{result.question}</p></div>
    <div className="panel answer-panel">
      <div className="section-heading"><div><p className="eyebrow">PORTFOLIO REVIEW / {portfolio.as_of}</p><h2>{actionLabel(answer.preferred_action)}</h2></div>
        <span className="badge">{portfolio.complete ? "Snapshot valued" : "Incomplete valuation"}</span></div>
      <p className="answer-reason">{answer.reason}</p>
      <div className="totals"><div><span>Portfolio total</span><strong data-testid="portfolio-total">{valueLabel(portfolio.total_value, currency)}</strong></div>
        <div><span>Holdings</span><strong>{valueLabel(portfolio.holdings_value, currency)}</strong></div>
        <div><span>Cash</span><strong>{valueLabel(portfolio.cash_value, currency)}</strong></div></div>
      {!portfolio.complete && <p className="qualification">Known subtotal: {valueLabel(portfolio.known_value, currency)}. This excludes unresolved values.</p>}
      <h3>Accounts</h3><div className="account-totals">{portfolio.accounts.map(account => <div key={account.id}>
        <span>{account.name}</span><strong>{valueLabel(account.total_value, currency)}</strong>
        {account.total_value === null && <small>Known subtotal: {valueLabel(account.known_value, currency)}</small>}
      </div>)}</div>
      <h3>Holdings &amp; cash</h3><div className="table-scroll"><table aria-label="Calculated holdings and cash">
        <thead><tr><th>Position / account</th><th>Supplied basis</th><th>Value ({currency})</th><th>Weight</th></tr></thead>
        <tbody>{portfolio.positions.map(row => <tr key={row.supplied.id}>
          <td><strong>{row.supplied.kind === "cash" ? "Cash" : row.supplied.ticker || "Unresolved security"}</strong>
            <small>{portfolio.accounts.find(account => account.id === row.supplied.account_id)?.name} · {row.supplied.listing || row.supplied.currency}</small>
            <small>Identity: {row.identity_status.replaceAll("_", " ")}</small></td>
          <td>{row.supplied.kind === "cash" ? <span>{row.supplied.cash} {row.supplied.currency} cash balance</span> : <>
            <span>{row.supplied.shares} shares · {row.supplied.mark ? `${row.supplied.mark.value} ${row.supplied.currency}` : "Mark unknown"}</span>
            {row.supplied.mark && <small>{row.supplied.mark.source} · {row.supplied.mark.as_of} · user supplied</small>}</>}
            {row.fx_used && <small>FX: {row.fx_used.rate} {row.fx_used.to_currency}/{row.fx_used.from_currency} · {row.fx_used.as_of} · {row.fx_used.source}</small>}
            {row.issues.map(issue => <small className="issue" key={issue}>{issue}</small>)}</td>
          <td>{valueLabel(row.value, currency)}</td><td>{weightLabel(row.weight)}</td>
        </tr>)}</tbody>
      </table></div>
      <h3>Direct company exposure</h3>
      <p className="muted small">Aggregated by the supplied company identity across all accounts. ETF look-through is unknown.</p>
      {portfolio.direct_companies.length ? <div className="table-scroll"><table aria-label="Direct company exposure">
        <thead><tr><th>Company</th><th>Value ({currency})</th><th>Portfolio weight</th></tr></thead>
        <tbody>{portfolio.direct_companies.map(company => <tr key={company.company_id}>
          <td>{company.company_name}<small>{company.company_id}</small></td><td>{valueLabel(company.value, currency)}
            {company.value === null && <small>Known subtotal: {valueLabel(company.known_value, currency)}</small>}</td><td>{weightLabel(company.weight)}</td>
        </tr>)}</tbody></table></div> : <p className="muted">No resolved direct-company positions supplied.</p>}
      <div className="qualification"><strong>Allocation amount: not determined</strong><p>Baseline and personal guardrails are unknown.</p></div>
      <Points title="Qualifications" items={portfolio.qualifications} />
      <Points title="Alternatives" items={answer.alternatives.map(option => `${actionLabel(option.action)}: ${option.reason}`)} />
      <Points title="Downside" items={[answer.downside]} />
      <Points title="Assumptions" items={answer.assumptions} />
      <Points title="Uncertainty" items={answer.uncertainty} />
      <Points title="What could change the view" items={answer.what_could_change} />
      <details><summary>Calculation basis</summary><p className="muted small">{portfolio.calculation_basis}</p></details>
    </div>
  </section>;
}
