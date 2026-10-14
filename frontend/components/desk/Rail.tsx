import { Bookmark, ChartPie, Leaf, Plus, X } from "lucide-react";
import type { Snapshot, ThreadSummary } from "../../lib/contracts";
import { fullDate, shortDate } from "./format";

type Props = {
  snapshot: Snapshot | null;
  /** The committed portfolio revision, and how much waits on the investor: holdings to identify and prepared changes. */
  revision: number | null; waiting: number;
  /** Saved decisions with no recorded action. */
  open: number;
  view: "chat" | "portfolio" | "decisions"; running: string | null;
  threads: ThreadSummary[] | null; threadsError: boolean; threadId: string | null; onThread: (id: string) => void;
  /** Questions that were not sent from a new conversation since left: each one reopens that conversation. */
  unsent: { key: string; text: string }[]; onUnsent: (key: string) => void;
  onNew: () => void; onPortfolio: () => void; onDecisions: () => void; onClose: () => void;
  /** Set while the details cover the page. */
  inert?: boolean;
};

/** Left rail: start a conversation, open the portfolio or the decisions, or go back to a conversation.
 *  Holdings and saved decisions have their own views, so they are not listed here again. */
export default function Rail({ snapshot, revision, waiting, open, view, running, threads, threadsError, threadId, onThread, unsent, onUnsent, onNew, onPortfolio, onDecisions, onClose, inert }: Props) {
  return <nav className="rail" aria-label="Portfolio, conversations and decisions" id="rail" inert={inert}>
    <div className="rail-head">
      <span className="wordmark"><span className="mark" aria-hidden="true"><Leaf size={14} /></span>Analyst</span>
      <button type="button" className="icon-btn rail-close" aria-label="Close menu" onClick={onClose}><X size={17} aria-hidden="true" /></button>
    </div>

    <button type="button" className="rail-new" onClick={onNew}><Plus size={16} aria-hidden="true" />New conversation</button>

    <div className="rail-nav">
      <button type="button" className="nav-item" onClick={onPortfolio} aria-current={view === "portfolio" ? "page" : undefined}>
        <ChartPie size={17} aria-hidden="true" /><span>Portfolio</span>
        {waiting > 0 && <span className="count">{waiting}<span className="sr-only"> waiting on you</span></span>}
      </button>
      <button type="button" className="nav-item" onClick={onDecisions} aria-current={view === "decisions" ? "page" : undefined} data-testid="all-decisions-link">
        <Bookmark size={17} aria-hidden="true" /><span>Decisions</span>
        {open > 0 && <span className="count quiet">{open} open<span className="sr-only">, not confirmed</span></span>}
      </button>
    </div>

    {/* Only threads the backend stores are listed; none is made up from a cached answer or a saved decision. */}
    <div className="rail-group">
      <span className="rail-label">Conversations</span>
      {running && <div className="thread-item" aria-current="true"><span className="title">{running}</span><span className="cap">Analyzing</span></div>}
      {threadsError ? <span className="cap amber rail-note" role="status">Conversations could not be loaded. Reload the page to try again.</span>
        : !threads ? <span className="cap rail-note" role="status">Loading conversations</span>
        : threads.length === 0 && unsent.length === 0 && !running && <span className="cap rail-note">No conversations yet. Ask a question to start one.</span>}
      {unsent.map(item => <button type="button" className="thread-item" key={item.key} onClick={() => onUnsent(item.key)} title={item.text}>
        <span className="title"><span className="sr-only">Unsent question: </span>{item.text}</span>
        <span className="cap amber">Not sent</span>
      </button>)}
      {threads?.map(thread => <button type="button" className="thread-item" key={thread.id} data-testid="thread-item"
        aria-current={thread.id === threadId && view === "chat"} onClick={() => onThread(thread.id)} title={thread.title || undefined}>
        <span className="title"><span className="sr-only">Conversation: </span>{thread.title}</span>
        <span className="cap n">{shortDate(thread.updated_at)}</span>
      </button>)}
    </div>

    <div className="rail-card">
      {snapshot ? <span className="rail-card-head"><b>{revision != null ? `Portfolio revision ${revision}` : "Portfolio"}</b>
        <span className="cap" data-testid="portfolio-date">Holdings as of {fullDate(snapshot.as_of)}</span></span>
        : <span className="rail-card-head"><b>Portfolio</b><span className="cap">No saved portfolio yet. Import a CSV to start.</span></span>}
    </div>
  </nav>;
}
