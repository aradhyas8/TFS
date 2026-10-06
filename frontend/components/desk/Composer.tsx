import { useState, type FormEvent, type KeyboardEvent } from "react";
import type { NewCashInput, Snapshot } from "../../lib/contracts";
import { accountName, money, shortDate } from "./format";

const WORKFLOWS = [
  { cmd: "/new-cash", text: "Decide where new money should go", live: true },
  { cmd: "/stock", text: "Value one company with its filings", live: false },
  { cmd: "/review", text: "Exposure, concentration and fund overlap", live: false },
  { cmd: "/rebalance", text: "Recheck each thesis against your rules", live: false },
  { cmd: "/theme", text: "Test an idea against a short list", live: false },
];

type Props = {
  snapshot: Snapshot | null; workflow: "new-cash" | null; onWorkflow: (workflow: "new-cash" | null) => void;
  question: string; onQuestion: (value: string) => void; newCash: NewCashInput; onNewCash: (value: NewCashInput) => void;
  running: boolean; collapsed: boolean; onExpand: () => void; onSubmit: () => void;
};

export function missingInput(snapshot: Snapshot | null, newCash: NewCashInput, question: string): string | null {
  if (!snapshot) return "Import a portfolio first";
  if (!newCash.amount || !(Number(newCash.amount) > 0)) return "Enter an amount";
  if (!newCash.cash_position_id) return "Choose where it arrives";
  if (!newCash.confirmed) return "Confirm the box above";
  if (!question.trim()) return "Ask a question";
  return null;
}

export default function Composer({ snapshot, workflow, onWorkflow, question, onQuestion, newCash, onNewCash, running, collapsed, onExpand, onSubmit }: Props) {
  const [active, setActive] = useState(0);
  const slashOpen = !workflow && question.startsWith("/");
  const matches = slashOpen ? WORKFLOWS.filter(item => item.cmd.startsWith(question.trim().split(" ")[0] || "/")) : [];
  const cashRows = snapshot?.positions.filter(row => row.kind === "cash") || [];
  const destination = cashRows.find(row => row.id === newCash.cash_position_id);
  const blocked = workflow === "new-cash" ? missingInput(snapshot, newCash, question) : null;
  const update = (patch: Partial<NewCashInput>) => onNewCash({ ...newCash, ...patch, confirmed: "confirmed" in patch ? !!patch.confirmed : false });

  function choose(index: number) {
    const item = matches[index];
    if (!item) return;
    if (!item.live) { window.location.href = "/"; return; }
    onWorkflow("new-cash"); onQuestion(question.slice(item.cmd.length).trimStart());
  }
  function keys(event: KeyboardEvent<HTMLInputElement>) {
    if (slashOpen && matches.length) {
      if (event.key === "ArrowDown") { event.preventDefault(); setActive(i => (i + 1) % matches.length); }
      if (event.key === "ArrowUp") { event.preventDefault(); setActive(i => (i - 1 + matches.length) % matches.length); }
      if (event.key === "Enter") { event.preventDefault(); choose(active); }
      if (event.key === "Escape") { event.preventDefault(); onQuestion(""); }
    } else if (event.key === "Backspace" && !question && workflow) onWorkflow(null);
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!blocked && workflow && !running) onSubmit();
  }

  return <form className="composer" onSubmit={submit} aria-label="Ask the analyst">
    <div className="col">
      {slashOpen && <div className="slash" role="listbox" aria-label="Workflows" id="slash-menu">
        <span className="cap" style={{ padding: "8px 12px 6px" }}>Workflows</span>
        {matches.map((item, index) => <button type="button" role="option" aria-selected={index === active} className="opt" key={item.cmd}
          onMouseEnter={() => setActive(index)} onClick={() => choose(index)}>
          <span className="cmd">{item.cmd}</span><span className={item.live ? "" : "m"}>{item.text}</span>
          <span className="cap">{item.live ? (index === active ? "↵" : "") : "classic view"}</span>
        </button>)}
        {matches.length === 0 && <span className="cap" style={{ padding: "10px 12px" }}>No workflow matches.</span>}
      </div>}

      <div className={`box${slashOpen ? " focus" : ""}`}>
        {workflow === "new-cash" && !collapsed && <fieldset className="nc-form" disabled={running}>
          <legend className="sr-only">New cash inputs</legend>
          <div className="nc-grid">
            <label className="field"><span className="cap">Amount</span>
              <input className="in n" inputMode="decimal" placeholder={snapshot ? snapshot.reporting_currency : ""} value={newCash.amount || ""}
                onChange={event => update({ amount: event.target.value.replace(/[^0-9.]/g, "") || null })} /></label>
            <label className="field"><span className="cap">Arrives in</span>
              <select className="in" value={newCash.cash_position_id || ""} onChange={event => update({ cash_position_id: event.target.value || null })}>
                <option value="">Choose a cash balance</option>
                {cashRows.map(row => <option key={row.id} value={row.id}>{accountName(snapshot, row.account_id)} · {row.currency} cash (now {money(row.cash ?? null, row.currency)})</option>)}
              </select></label>
          </div>
          <label className="field"><span className="cap">Loss tolerance or withdrawal plans</span>
            <input className="in" maxLength={1000} placeholder="e.g. could hold through a 30% drop; no withdrawals for 5 years" value={newCash.risk_context || ""}
              onChange={event => onNewCash({ ...newCash, risk_context: event.target.value || null })} />
            <span className="cap">You can leave this out, but the analyst won't give an amount without it.</span></label>
          <label className="check"><input type="checkbox" checked={newCash.confirmed} disabled={!newCash.amount || !destination}
            onChange={event => update({ confirmed: event.target.checked })} />
            <span>{newCash.amount && destination ? `This ${money(newCash.amount, destination.currency)} is new money, not already in the ${shortDate(snapshot?.as_of)} snapshot, and it will arrive in ${accountName(snapshot, destination.account_id)}.`
              : "Confirm this is new money and where it arrives."}</span></label>
        </fieldset>}
        {workflow === "new-cash" && collapsed && <div className="box-line" style={{ minHeight: 44 }}>
          <span className="cap n" style={{ flex: 1 }}>{newCash.amount && destination ? `${money(newCash.amount, destination.currency)} into ${accountName(snapshot, destination.account_id)} · ${destination.currency} cash` : "New cash inputs"}{newCash.risk_context ? " · loss tolerance given" : " · no loss tolerance"}</span>
          <button type="button" className="link cap" onClick={onExpand}>Edit inputs</button>
        </div>}
        <div className="box-line">
          {workflow === "new-cash" && <span className="prefix">New cash</span>}
          <label htmlFor="ask" className="sr-only">{workflow ? "Question" : "Ask about your portfolio, or type / for workflows"}</label>
          <input id="ask" autoComplete="off" value={question} disabled={running} onKeyDown={keys}
            aria-controls={slashOpen ? "slash-menu" : undefined} aria-expanded={slashOpen}
            placeholder={running ? "You can ask a follow-up once this answer arrives." : workflow ? collapsed ? "Follow up. This runs a new analysis with the same portfolio." : "I have new cash. Where should I allocate it?" : "Type / to choose a workflow"}
            onChange={event => { onQuestion(event.target.value); setActive(0); }} />
          {workflow === "new-cash" && blocked && !running && <span className="cap amber hint">{blocked}</span>}
          {workflow === "new-cash" && <button type="submit" className="btn primary" disabled={!!blocked || running}>Analyze</button>}
          {!workflow && <span className="cap">/</span>}
        </div>
      </div>
    </div>
  </form>;
}
