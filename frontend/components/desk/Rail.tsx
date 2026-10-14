import { Bookmark, ChartPie, Leaf, Plus, X } from "lucide-react";
import { BENCHMARK_FUND, isSupportedStock, NEW_CASH_DESTINATION, type Analysis, type PortfolioSettings, type Position, type SavedDecision, type Snapshot, type ThreadSummary, type UnresolvedHolding } from "../../lib/contracts";
import { decisionLabel, doneLabel, fullDate, money, pct, shortDate } from "./format";

type Props = {
  snapshot: Snapshot | null; unresolved: UnresolvedHolding[]; settings: PortfolioSettings; result: Analysis | null;
  /** The committed portfolio revision, and how many prepared changes wait for a review. */
  revision: number | null; pending: number;
  decisions: SavedDecision[]; currentId: string | null; running: string | null; registerOpen: boolean;
  threads: ThreadSummary[] | null; threadsError: boolean; threadId: string | null; onThread: (id: string) => void;
  /** Questions that were not sent from a new conversation since left: each one reopens that conversation. */
  unsent: { key: string; text: string }[]; onUnsent: (key: string) => void;
  onNew: () => void; onHoldings: () => void; onOpen: (decision: SavedDecision) => void; onAllDecisions: () => void; onStock: (position: Position) => void;
  onDelete: (decision: SavedDecision) => void; onClearAll: () => void; onClose: () => void;
  /** Set while the details cover the page. */
  inert?: boolean;
};

/** Left rail: which portfolio and which decisions am I working with? */
export default function Rail({ snapshot, unresolved, settings, result, revision, pending, decisions, currentId, running, registerOpen, threads, threadsError, threadId, onThread, unsent, onUnsent, onNew, onHoldings, onOpen, onAllDecisions, onStock, onDelete, onClearAll, onClose, inert }: Props) {
  const review = result?.portfolio;
  // A supported stock row offers Stock Analysis; funds and cash are listed only.
  const holding = (row: Position, figure: string) => {
    const name = row.kind === "cash" ? `Cash ${row.currency}` : row.ticker || row.company_name || "Unnamed";
    return isSupportedStock(row) ? <button type="button" className="pv hold" key={row.id} aria-label={`Analyze ${name}`} title={`Stock Analysis for ${name}`} onClick={() => onStock(row)}>
      <span>{name}</span><span className="m">{figure}</span></button> : <span className="pv" key={row.id}><span>{name}</span><span className="m">{figure}</span></span>;
  };
  const rules = [settings.single_company_cap && `cap ${pct(settings.single_company_cap)}`, settings.active_budget && `active ${pct(settings.active_budget)}`].filter(Boolean);
  const waiting = unresolved.length + pending;
  const open = decisions.filter(decision => !decision.confirmed_action).length;
  return <nav className="rail" aria-label="Portfolio, conversations and decisions" id="rail" inert={inert}>
    <div className="rail-head">
      <span className="wordmark"><span className="mark" aria-hidden="true"><Leaf size={14} /></span>Analyst</span>
      <button type="button" className="icon-btn rail-close" aria-label="Close menu" onClick={onClose}><X size={17} aria-hidden="true" /></button>
    </div>

    <button type="button" className="rail-new" onClick={onNew}><Plus size={16} aria-hidden="true" />New conversation</button>

    <div className="rail-nav">
      <button type="button" className="nav-item" onClick={onHoldings}>
        <ChartPie size={17} aria-hidden="true" /><span>Portfolio<span className="sr-only">, open holdings</span></span>
        {waiting > 0 && <span className="count">{waiting}<span className="sr-only"> waiting on you</span></span>}
      </button>
      <button type="button" className="nav-item" onClick={onAllDecisions} aria-current={registerOpen} data-testid="all-decisions-link">
        <Bookmark size={17} aria-hidden="true" /><span><span className="sr-only">All </span>Decisions</span>
        {open > 0 && <span className="count quiet">{open} open</span>}
      </button>
    </div>

    {/* Only threads the backend stores are listed; none is made up from a cached answer or a saved decision. */}
    <div className="rail-group">
      <span className="rail-label">Conversations</span>
      {threadsError ? <span className="cap amber rail-note" role="status">Conversations could not be loaded. Reload the page to try again.</span>
        : !threads ? <span className="cap rail-note" role="status">Loading conversations</span>
        : threads.length === 0 && unsent.length === 0 && <span className="cap rail-note">No conversations yet. Ask a question to start one.</span>}
      {unsent.map(item => <button type="button" className="thread-item" key={item.key} onClick={() => onUnsent(item.key)} title={item.text}>
        <span className="title"><span className="sr-only">Unsent question: </span>{item.text}</span>
        <span className="cap amber">Not sent</span>
      </button>)}
      {threads?.map(thread => <button type="button" className="thread-item" key={thread.id} data-testid="thread-item"
        aria-current={thread.id === threadId} onClick={() => onThread(thread.id)} title={thread.title || undefined}>
        <span className="title"><span className="sr-only">Conversation: </span>{thread.title}</span>
        <span className="cap n">{shortDate(thread.updated_at)}</span>
      </button>)}
    </div>

    <div className="rail-group">
      <span className="rail-label"><span>Saved decisions</span>
        {decisions.length > 0 && <button type="button" className="link cap" onClick={onClearAll}>Clear all</button>}</span>
      {running && <div className="ditem" aria-current="true"><span>{running}</span><span className="cap">Now · analyzing</span></div>}
      {decisions.length === 0 && !running && <span className="cap rail-note">Saved decisions appear here.</span>}
      {decisions.map(decision => <div className="drow" key={decision.id}>
        <button type="button" className="ditem" data-testid="decision-item" aria-label={`${decisionLabel(decision)}: ${decision.question}`} aria-current={decision.id === currentId} onClick={() => onOpen(decision)} title={decision.question}>
          <span className="title"><span>{decisionLabel(decision)}</span>{!decision.confirmed_action && <span className="dot" aria-label="Not confirmed" />}</span>
          <span className="cap">{shortDate(decision.saved_at)} · {decision.confirmed_action ? `you: ${doneLabel(decision.confirmed_action.action)}` : "not confirmed"}</span>
        </button>
        <button type="button" className="ddel" aria-label={`Delete decision: ${decisionLabel(decision)}`} title="Delete" onClick={() => onDelete(decision)}>
          <X size={14} aria-hidden="true" />
        </button>
      </div>)}
    </div>

    <div className="rail-card">
      {snapshot ? <>
        <span className="rail-card-head"><b>{revision != null ? `Portfolio revision ${revision}` : "Portfolio"}</b>
          <span className="cap" data-testid="portfolio-date">As of {fullDate(snapshot.as_of)}</span></span>
        {review && <span className="portfolio-total n">{money(review.total_value, review.reporting_currency)}</span>}
        {review && <span className="cap">Weights · {shortDate(review.reviewed_at)} analysis</span>}
        <span className="holdings-mini n">
          {review ? review.positions.filter(row => row.supplied.id !== NEW_CASH_DESTINATION && !row.supplied.id.startsWith("candidate-") && row.supplied.id !== BENCHMARK_FUND).map(row =>
            holding(snapshot.positions.find(held => held.id === row.supplied.id) ?? row.supplied, pct(row.weight)))
            : snapshot.positions.filter(row => row.id !== BENCHMARK_FUND).map(row => holding(row, row.kind === "cash" ? money(row.cash ?? null, row.currency) : `${row.shares ?? "?"} sh`))}
        </span>
        {unresolved.length > 0 && <span className="holdings-mini n">{unresolved.map(row => <span className="pv" key={`${row.account_id}:${row.ticker}`}>
          <span>{row.ticker}</span><span className="amber">needs {[!row.listing && "exchange", !row.kind && "type"].filter(Boolean).join(" and ")}</span></span>)}</span>}
        {!review && <span className="cap">Values and weights appear after the first analysis.</span>}
        <span className="cap n rules-line">{rules.length ? `Rules · ${rules.join(" · ")}` : "No rules set"}</span>
      </> : <><b>Portfolio</b><span className="cap">No saved portfolio yet. Import a CSV to start.</span></>}
    </div>
  </nav>;
}
