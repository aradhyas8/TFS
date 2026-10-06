import { NEW_CASH_DESTINATION, type Analysis, type PortfolioSettings, type SavedDecision, type Snapshot, type UnresolvedHolding } from "../../lib/contracts";
import { doneLabel, fullDate, money, pct, shortDate } from "./format";

type Props = {
  snapshot: Snapshot | null; unresolved: UnresolvedHolding[]; settings: PortfolioSettings; result: Analysis | null;
  decisions: SavedDecision[]; currentId: string | null; running: boolean;
  onNew: () => void; onHoldings: () => void; onOpen: (decision: SavedDecision) => void;
};

/** Left rail: which portfolio and which decisions am I working with? */
export default function Rail({ snapshot, unresolved, settings, result, decisions, currentId, running, onNew, onHoldings, onOpen }: Props) {
  const review = result?.portfolio;
  const rules = [settings.single_company_cap && `cap ${pct(settings.single_company_cap)}`, settings.active_budget && `active ${pct(settings.active_budget)}`].filter(Boolean);
  return <nav className="rail" aria-label="Portfolio and decisions" id="rail">
    <div className="rail-head">
      <span className="wordmark">Analyst</span>
      <button type="button" className="icon-btn" aria-label="New analysis" onClick={onNew}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      </button>
    </div>

    {snapshot ? <button type="button" className="portfolio" onClick={onHoldings} aria-label="Portfolio, open holdings">
      <span className="pv"><span className="lbl">Portfolio</span>
        <span className="cap" data-testid="portfolio-date">As of {fullDate(snapshot.as_of)}</span></span>
      {review && <span className="cap">Weights · {shortDate(review.reviewed_at)} analysis</span>}
      {review && <span className="portfolio-total n">{money(review.total_value, review.reporting_currency)}</span>}
      <span className="holdings-mini n">
        {review ? review.positions.filter(row => row.supplied.id !== NEW_CASH_DESTINATION).map(row => <span className="pv" key={row.supplied.id}>
          <span>{row.supplied.kind === "cash" ? `Cash ${row.supplied.currency}` : row.supplied.ticker || row.supplied.company_name || "Unnamed"}</span>
          <span className="m">{pct(row.weight)}</span></span>)
          : snapshot.positions.map(row => <span className="pv" key={row.id}>
            <span>{row.kind === "cash" ? `Cash ${row.currency}` : row.ticker || row.company_name || "Unnamed"}</span>
            <span className="m">{row.kind === "cash" ? money(row.cash ?? null, row.currency) : `${row.shares ?? "?"} sh`}</span></span>)}
      </span>
      {unresolved.length > 0 && <span className="holdings-mini n">{unresolved.map(row => <span className="pv" key={`${row.account_id}:${row.ticker}`}>
        <span>{row.ticker}</span><span className="amber">needs {[!row.listing && "exchange", !row.kind && "type"].filter(Boolean).join(" and ")}</span></span>)}</span>}
      {!review && <span className="cap">Values and weights appear after the first analysis.</span>}
      <span className="cap n rules-line">{rules.length ? `Rules · ${rules.join(" · ")}` : "No rules set"}</span>
    </button> : <div className="portfolio"><span className="lbl">Portfolio</span><span className="cap">No saved portfolio yet. Import a CSV to start.</span></div>}

    <div className="decisions">
      <span className="lbl">Decisions</span>
      {running && <div className="ditem" aria-current="true"><span>New cash</span><span className="cap">Now · analyzing</span></div>}
      {decisions.length === 0 && !running && <span className="cap" style={{ padding: "0 12px" }}>Saved decisions appear here.</span>}
      {decisions.slice(0, 8).map(decision => <button type="button" className="ditem" key={decision.id} aria-current={decision.id === currentId}
        onClick={() => onOpen(decision)}>
        <span className="title"><span>{decision.question}</span>{!decision.confirmed_action && <span className="dot" aria-label="Not confirmed" />}</span>
        <span className="cap">{shortDate(decision.saved_at)} · {decision.confirmed_action ? `you: ${doneLabel(decision.confirmed_action.action)}` : "not confirmed"}</span>
      </button>)}
      <a href="/" className="ditem">Classic view</a>
    </div>
  </nav>;
}
