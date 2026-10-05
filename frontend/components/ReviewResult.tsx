import { useState } from "react";
import { actionLabel, post, valueLabel, weightLabel, type Analysis, type SavedDecision } from "../lib/contracts";
import GuardrailResult from "./GuardrailResult";
import ComparisonResult from "./ComparisonResult";
import AllocationResult from "./AllocationResult";
import ReunderwritingResult from "./ReunderwritingResult";
import ThemeResult from "./ThemeResult";
import StockResult from "./StockResult";

function Points({ title, items }: { title: string; items: string[] }) {
  return <div className="review-points"><h3>{title}</h3><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}

export default function ReviewResult({
  result,
  onSaved,
  onReopen,
}: {
  result: Analysis;
  onSaved?: (saved: SavedDecision) => void;
  onReopen?: (saved: SavedDecision) => void;
}) {
  const [savedDecision, setSavedDecision] = useState<SavedDecision | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  async function handleSave() {
    setSaving(true);
    setSaveError("");
    try {
      const saved = await post<SavedDecision>("/api/decisions", { result });
      setSavedDecision(saved);
      if (onSaved) onSaved(saved);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save decision.");
    } finally {
      setSaving(false);
    }
  }

  const { portfolio: portfolio, recommendation: answer } = result;

  const currency = portfolio.reporting_currency;
  return <section className="review-result" aria-label="Completed portfolio review" aria-live="polite">
    <ThemeResult theme={result.theme} currency={currency} />
    <div className="question-bubble"><span className="eyebrow">YOUR QUESTION</span><p>{result.question}</p></div>
    <div className="panel answer-panel">
      <div className="section-heading"><div><p className="eyebrow">PORTFOLIO REVIEW / {portfolio.as_of}</p><h2>{actionLabel(answer.preferred_action)}</h2></div>
        <span className="badge">{portfolio.complete ? "Snapshot valued" : "Incomplete valuation"}</span></div>
      <p className="answer-reason">{answer.reason}</p>
      <p className="muted small">Reviewed at {portfolio.reviewed_at}. Values describe the snapshot date; historical marks are not current quotes.</p>
      <div className="totals"><div><span>Portfolio total</span><strong data-testid="portfolio-total">{valueLabel(portfolio.total_value, currency)}</strong></div>
        <div><span>Holdings</span><strong>{valueLabel(portfolio.holdings_value, currency)}</strong></div>
        <div><span>Cash</span><strong>{valueLabel(portfolio.cash_value, currency)}</strong></div></div>
      {!portfolio.complete && <p className="qualification">Known subtotal: {valueLabel(portfolio.known_value, currency)}. This excludes unresolved values.</p>}
      <h3>Accounts</h3><div className="account-totals">{portfolio.accounts.map(account => <div key={account.id}>
        <span>{account.name}</span><strong>{valueLabel(account.total_value, currency)}</strong>
        {account.total_value === null && <small>Known subtotal: {valueLabel(account.known_value, currency)}</small>}
      </div>)}</div>
      <h3>Holdings &amp; cash</h3><div className="table-scroll"><table aria-label="Calculated holdings and cash">
        <thead><tr><th>Position / account</th><th>Dated valuation basis</th><th>Value ({currency})</th><th>Weight</th></tr></thead>
        <tbody>{portfolio.positions.map(row => <tr key={row.supplied.id}>
          <td><strong>{row.supplied.kind === "cash" ? "Cash" : row.identity?.ticker || row.supplied.ticker || "Unresolved security"}</strong>
            <small>{portfolio.accounts.find(account => account.id === row.supplied.account_id)?.name} · {row.supplied.listing || row.supplied.currency}</small>
            <small>Identity: {row.identity_status.replaceAll("_", " ")}</small>
            {row.identity && <><small>{row.identity.company_name || row.identity.ticker} | {row.identity.listing} | {row.identity.currency}</small>
              <small>{row.identity.source} | {row.identity.as_of || "Identity date unknown"}</small>
              <small>Identity captured: {row.identity.captured_at || "Unknown"}</small>
              {row.identity.source_url && /^https?:\/\//.test(row.identity.source_url) && <a href={row.identity.source_url} target="_blank" rel="noreferrer">Identity source</a>}
              {row.identity.status !== row.identity_status && <small className="issue">Source identity: {row.identity.status}</small>}</>}</td>
          <td>{row.supplied.kind === "cash" ? <span>{row.supplied.cash} {row.supplied.currency} cash balance</span> : <>
            <span>{row.supplied.shares} shares at {row.quote_used ? `${row.quote_used.value} ${row.quote_used.currency}` : "Mark unknown"}</span>
            {row.quote_used && <><small>{row.quote_used.source} | {row.quote_used.as_of} | {row.quote_used.status}</small>
              <small>Quote captured: {row.quote_used.captured_at || "Unknown"}</small>
              <small>Quote age vs review: {row.quote_age_days ?? "Unknown"} days | age at capture: {row.quote_age_at_capture_days ?? "Unknown"} days</small>
              <small>Quote age at request: {row.quote_age_at_request_days ?? "Unknown"} days</small>
              <small>Price basis: {row.quote_used.basis?.replaceAll("_", " ") || "Unknown"}</small></>}</>}
            {row.fx_used && <><small>FX: {row.fx_used.rate} {row.fx_used.to_currency}/{row.fx_used.from_currency} | {row.fx_used.as_of} | {row.fx_used.source}</small>
              <small>FX status: {row.fx_used.status} | captured: {row.fx_used.captured_at || "Unknown"} | age vs review: {row.fx_age_days ?? "Unknown"} days | indicative only</small></>}
            <small>Local value: {valueLabel(row.local_value, row.supplied.currency)} | reporting currency: {currency}</small>
            {!row.source_inputs_usable && <small className="issue">Source inputs do not support confident sizing.</small>}
            {row.issues.map(issue => <small className="issue" key={issue}>{issue}</small>)}</td>
          <td>{valueLabel(row.value, currency)}</td><td>{weightLabel(row.weight)}</td>
        </tr>)}</tbody>
      </table></div>
      <h3>Direct company exposure</h3>
      <p className="muted small">Aggregated by company identity across all accounts; supplied identities remain provisional.</p>
      {portfolio.direct_companies.length ? <div className="table-scroll"><table aria-label="Direct company exposure">
        <thead><tr><th>Company</th><th>Value ({currency})</th><th>Portfolio weight</th></tr></thead>
        <tbody>{portfolio.direct_companies.map(company => <tr key={company.company_id}>
          <td>{company.company_name}<small>{company.company_id}</small></td><td>{valueLabel(company.value, currency)}
            {company.value === null && <small>Known subtotal: {valueLabel(company.known_value, currency)}</small>}</td><td>{weightLabel(company.weight)}</td>
        </tr>)}</tbody></table></div> : <p className="muted">No resolved direct-company positions supplied.</p>}
      <h3>ETF indirect overlap &amp; look-through</h3>
      <p className="muted small">
        Indirect look-through status: <span className="badge" data-testid="indirect-exposure-badge">{portfolio.indirect_exposure}</span>.
        {portfolio.indirect_exposure === "full" && " Full look-through from dated sponsor holdings."}
        {portfolio.indirect_exposure === "partial" && " Partial look-through; some funds provide top holdings only. Incomplete look-through is not assumed zero."}
        {portfolio.indirect_exposure === "stale" && " ETF sponsor holdings are dated differently from snapshot; coverage is stale."}
        {portfolio.indirect_exposure === "unknown" && " Sponsor holdings unavailable or unverified."}
        {portfolio.indirect_exposure === "none" && " No ETF positions held."}
      </p>
      {portfolio.company_overlap && portfolio.company_overlap.length > 0 ? (
        <div className="table-scroll"><table aria-label="ETF company overlap and look-through">
          <thead><tr><th>Company</th><th>Direct ({currency})</th><th>Indirect ({currency})</th><th>Total ({currency})</th><th>Total weight</th><th>Coverage</th><th>Contributing funds / dates</th></tr></thead>
          <tbody>{portfolio.company_overlap.map(item => <tr key={item.company_id} data-testid={`overlap-row-${item.company_id}`}>
            <td><strong>{item.company_name}</strong><small>{item.company_id}</small></td>
            <td>{valueLabel(item.direct_value, currency)}{item.direct_weight && <small>{weightLabel(item.direct_weight)}</small>}</td>
            <td>{valueLabel(item.indirect_value, currency)}{item.indirect_weight && <small>{weightLabel(item.indirect_weight)}</small>}</td>
            <td>{valueLabel(item.total_value, currency)}</td>
            <td>{weightLabel(item.total_weight)}</td>
            <td><span className="badge">{item.coverage}</span></td>
            <td>{item.contributing_funds.map((fund, idx) => <div key={idx}><small>{fund.fund_name || fund.ticker || fund.position_id} ({fund.position_id}): {weightLabel(fund.weight_in_fund)} · {valueLabel(fund.indirect_value, currency)} ({fund.source} · {fund.as_of})</small></div>)}
              {item.source_dates && item.source_dates.length > 0 && <small className="muted">Source dates: {item.source_dates.join(", ")}</small>}</td>
          </tr>)}</tbody>
        </table></div>
      ) : <p className="muted">No indirect company overlap identified across held funds.</p>}
      <GuardrailResult review={portfolio.guardrails} currency={currency} label="Current" />
      <StockResult stock={result.stock} currency={portfolio.positions.find(row => row.supplied.id === result.stock?.position_id)?.supplied.currency || currency} />
      {answer.evidence_ids && <p>Recommendation evidence: {answer.evidence_ids.map((id, index) => { const doc = [result.stock, ...(result.allocation?.stocks || []), ...(result.reunderwriting?.stocks || [])].flatMap(stock => stock?.research.documents || []).find(row => row.id === id); return <span key={id}>{index > 0 && "; "}{doc ? <a href={doc.url} target="_blank" rel="noreferrer">{doc.title} ({doc.published_on})</a> : id}</span>; })}</p>}
      <ReunderwritingResult review={result.reunderwriting} positions={portfolio.positions.map(row => row.supplied)} />
      <AllocationResult allocation={result.allocation} />
      <ComparisonResult comparison={result.comparison} />
      {[...result.proposals, ...(result.allocation?.previews || []), ...(result.reunderwriting?.previews || []), ...(result.theme?.previews || [])].map((proposal, index) => <section className="panel" aria-label={`Proposed change ${index + 1}`} key={index}>
        <h3>Proposed change {index + 1}: {proposal.status.replaceAll("_", " ")}</h3>
        <p className="muted small">Source: {proposal.source}. Hypothetical preview using the snapshot date; no action has occurred.</p>
        <p>Post-change portfolio total: {valueLabel(proposal.post_total_value, currency)}. Post-change cash: {valueLabel(proposal.post_cash_value, currency)}.</p>
        <details><summary>Submitted preview inputs</summary><ul>
          {proposal.changes.new_cash.map(row => <li key={row.cash_position_id}>New cash in {row.cash_position_id}: {valueLabel(row.amount, portfolio.positions.find(position => position.supplied.id === row.cash_position_id)?.supplied.currency || currency)}</li>)}
          {proposal.changes.trades.map(row => <li key={row.position_id}>{row.position_id}: {row.shares_change} shares; funding/proceeds balance: {row.cash_position_id}</li>)}
        </ul></details>
        {proposal.positions.length > 0 && <div className="table-scroll"><table aria-label={`Proposed change ${index + 1} positions`}>
          <thead><tr><th>Position</th><th>Post-change value ({currency})</th><th>Post-change weight</th></tr></thead>
          <tbody>{proposal.positions.map(row => <tr key={row.supplied.id}><td>{row.supplied.ticker || "Cash"}<small>{row.supplied.id}</small></td>
            <td>{valueLabel(row.value, currency)}</td><td>{weightLabel(row.weight)}</td></tr>)}</tbody>
        </table></div>}
        <GuardrailResult review={proposal.guardrails} currency={currency} label={`Proposed change ${index + 1}`} />
        <Points title="Preview qualifications" items={proposal.qualifications} />
      </section>)}
      <div className="qualification"><strong>{answer.amount ? `Approximate ${result.reunderwriting ? "adjustment" : "allocation"} amount: ${valueLabel(answer.amount.minimum, answer.amount.currency)} to ${valueLabel(answer.amount.maximum, answer.amount.currency)} for ${answer.amount.position_id}` : "Allocation amount: not determined"}</strong><p>{answer.amount ? "The judged range passed deterministic post-allocation checks. Costs and execution remain uncertain; place any orders yourself." : "Exposure checks use only supplied settings. A hypothetical preview does not establish justified sizing."}</p></div>
      <Points title="Qualifications" items={portfolio.qualifications} />
      <Points title="Alternatives" items={answer.alternatives.map(option => `${actionLabel(option.action)}: ${option.reason}`)} />
      <Points title="Downside" items={[answer.downside]} />
      <Points title="Assumptions" items={answer.assumptions} />
      <Points title="Uncertainty" items={answer.uncertainty} />
      <Points title="What could change the view" items={answer.what_could_change} />
      <details><summary>Calculation basis</summary><p className="muted small">{portfolio.calculation_basis}</p></details>
      <section className="panel save-decision-panel" aria-label="Save decision panel">
        <h3>Save decision for honest revisit</h3>
        <p className="muted small">
          Store the dated question, original evidence references, reasoning, and conclusion to plain local files. Saving is optional and does not affect your analysis.
        </p>
        {savedDecision ? (
          <div className="saved-confirmation" data-testid="save-success-message">
            <p><strong>Decision saved successfully.</strong> Reference ID: {savedDecision.id}</p>
            <p className="muted small">As-of: {savedDecision.as_of}. Stored to plain local file without database or external dependencies.</p>
            {onReopen && (
              <button
                type="button"
                className="button"
                onClick={() => onReopen(savedDecision)}
                aria-label="Reopen saved decision view"
              >
                Reopen saved decision view →
              </button>
            )}
          </div>
        ) : (
          <div>
            <button
              type="button"
              className="button primary"
              disabled={saving}
              onClick={handleSave}
              aria-label="Save decision"
              data-testid="save-decision-button"
            >
              {saving ? "Saving decision…" : "Save decision"}
            </button>
            {saveError && <p className="error">{saveError}</p>}
          </div>
        )}
      </section>
    </div>
  </section>;
}

