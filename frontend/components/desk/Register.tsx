import { ArrowLeft, Banknote, Bookmark, ChartPie, Check, Lightbulb, Scale, TrendingUp, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { actionLabel, type DecisionWorkflow, type SavedDecision } from "../../lib/contracts";
import { decisionLabel, doneLabel, fullDate, shortDate } from "./format";
import { Alert } from "@/components/arc/alert/alert";
import { Button } from "@/components/arc/button/button";
import { Skeleton } from "@/components/arc/skeleton/skeleton";

type Props = {
  /** The menu button, so the rail drawer opens from this view too. */
  menu: ReactNode;
  decisions: SavedDecision[];
  state: "loading" | "ready" | "failed";
  onOpen: (decision: SavedDecision) => void;
  onDelete: (decision: SavedDecision) => void;
  onClearAll: () => void;
  onBack: () => void;
};

const ICON: Record<DecisionWorkflow, [ReactNode, string]> = {
  stock: [<TrendingUp key="i" size={16} />, "blue"], new_cash: [<Banknote key="i" size={16} />, "sand"], review: [<ChartPie key="i" size={16} />, ""],
  rebalance: [<Scale key="i" size={16} />, "sand"], theme: [<Lightbulb key="i" size={16} />, "blue"],
};

/** Saved decisions as their own view. Each is kept as it was saved, with that day's portfolio and evidence;
 *  whether you recorded what you did is shown apart from what the analyst concluded. */
export default function Register({ menu, decisions, state, onOpen, onDelete, onClearAll, onBack }: Props) {
  const [workflowFilter, setWorkflowFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "recorded">("all");
  const open = decisions.filter(d => !d.confirmed_action).length;

  const filtered = decisions.filter(d => {
    if (workflowFilter === "unknown" ? !!d.workflow : workflowFilter !== "all" && d.workflow !== workflowFilter) return false;
    if (statusFilter === "open" && d.confirmed_action) return false;
    if (statusFilter === "recorded" && !d.confirmed_action) return false;
    return true;
  });

  return (
    <section className="pview" aria-labelledby="decisions-title">
      <header className="ctx view-head">
        <div className="ctx-left">
          {menu}
          <h1 id="decisions-title" className="view-title" tabIndex={-1}>Decisions</h1>
          {decisions.length > 0 && <span className="chip hide-sm n">{decisions.length} saved{open ? ` · ${open} not confirmed` : ""}</span>}
        </div>
        <div className="ctx-right">
          <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft size={15} aria-hidden="true" />Back to chat</Button>
        </div>
      </header>
      <div className="pview-body dview" role="region" aria-label="Decisions register">
        <p className="cap dintro">Each decision is kept as it was saved, with the portfolio and evidence of that day. It does not change when your holdings do; your holdings today are on Portfolio.</p>
        {state === "loading" ? <Skeleton label="Loading saved decisions" lines={4} />
          : state === "failed" ? <Alert tone="danger" title="Saved decisions couldn't be loaded">Check that the analyst service is running, then reload the page.</Alert>
          : decisions.length === 0 ? <div className="pempty">
            <h2>No saved decisions yet.</h2>
            <p className="cap">Save a decision from any analysis result. It is kept here with its evidence, and you can record what you did later.</p>
            <span><Button variant="secondary" size="sm" onClick={onBack}>Ask a question</Button></span>
          </div>
          : <>
            <div className="dfilters">
              <label className="field dfield">
                <select className="in pill-select" aria-label="Filter by workflow" value={workflowFilter} onChange={e => setWorkflowFilter(e.target.value)}>
                  <option value="all">All workflows</option>
                  <option value="review">Portfolio review</option>
                  <option value="rebalance">Rebalance</option>
                  <option value="stock">Stock analysis</option>
                  <option value="new_cash">New cash</option>
                  <option value="theme">Theme</option>
                  <option value="unknown">Unknown workflow</option>
                </select>
              </label>
              <div className="seg" role="radiogroup" aria-label="What you did">
                {([["all", "All"], ["open", "Not confirmed"], ["recorded", "Recorded"]] as const).map(([value, label]) => <label key={value} className="seg-opt">
                  <input type="radio" name="decision-status" value={value} checked={statusFilter === value} onChange={() => setStatusFilter(value)} />
                  <span>{label}</span></label>)}
              </div>
              <span className="cap n dcount" role="status">Showing {filtered.length} of {decisions.length} decision{decisions.length === 1 ? "" : "s"}</span>
            </div>
            {filtered.length === 0 ? <div className="pempty">
              <p className="cap">No decisions match these filters.</p>
              <span><Button variant="ghost" size="sm" onClick={() => { setWorkflowFilter("all"); setStatusFilter("all"); }}>Clear filters</Button></span>
            </div>
              : <ul className="dlist" aria-label="Saved decisions">
                {filtered.map(decision => {
                  const label = decisionLabel(decision);
                  const [icon, tint] = decision.workflow ? ICON[decision.workflow] : [<Bookmark key="i" size={16} />, "plain"];
                  const sources = decision.evidence_references.length;
                  return <li className="dcard" key={decision.id}>
                    <button type="button" className="dopen" data-testid="decision-row" onClick={() => onOpen(decision)}>
                      <span className={`ico ${tint}`} aria-hidden="true">{icon}</span>
                      <span className="dmain">
                        <b className="dtitle">{label}</b>
                        <span className="dq">{decision.question}</span>
                        <span className="cap n">Saved {fullDate(decision.saved_at)} · portfolio as of {fullDate(decision.as_of)}{sources ? ` · ${sources} source${sources === 1 ? "" : "s"}` : ""}</span>
                      </span>
                      <span className="dside">
                        <span className="dconc">{actionLabel(decision.conclusion.preferred_action)}</span>
                        {decision.confirmed_action
                          ? <span className="chip done"><Check size={13} aria-hidden="true" />You did: {doneLabel(decision.confirmed_action.action)} · {shortDate(decision.confirmed_action.confirmed_at)}</span>
                          : <span className="chip warn">Not confirmed</span>}
                      </span>
                    </button>
                    <button type="button" className="ddel" aria-label={`Delete decision: ${label}`} title="Delete" onClick={() => onDelete(decision)}>
                      <X size={15} aria-hidden="true" />
                    </button>
                  </li>;
                })}
              </ul>}
            <span><button type="button" className="link cap" onClick={onClearAll}>Delete all decisions</button></span>
          </>}
      </div>
    </section>
  );
}
