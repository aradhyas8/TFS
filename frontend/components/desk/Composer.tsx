import { useState, type FormEvent, type KeyboardEvent } from "react";
import type { NewCashInput, Snapshot } from "../../lib/contracts";
import { accountName, fullDate, money, newCashLabel } from "./format";

const WORKFLOWS = [
  { cmd: "/new-cash", text: "Decide where new money should go", live: true },
  { cmd: "/stock", text: "Analyze one company you own, e.g. /stock AVGO", live: true },
  { cmd: "/review", text: "Exposure, concentration and fund overlap", live: true },
  { cmd: "/rebalance", text: "Should anything change? Reduce, keep or add, within your rules", live: true },
  { cmd: "/theme", text: "Test an idea against a short list", live: false },
];

type Props = {
  snapshot: Snapshot | null; unresolved: number; workflow: "new-cash" | null; onWorkflow: (workflow: "new-cash" | null) => void;
  question: string; onQuestion: (value: string) => void; newCash: NewCashInput; onNewCash: (value: NewCashInput) => void;
  running: boolean; collapsed: boolean; onExpand: () => void; onSubmit: () => void;
};

export function missingInput(snapshot: Snapshot | null, newCash: NewCashInput | null, question: string, unresolved = 0): string | null {
  if (!snapshot) return "Import a portfolio first";
  if (unresolved) return `Identify ${unresolved} holding${unresolved === 1 ? "" : "s"} first`;
  if (!newCash) return question.trim() ? null : "Ask a question";
  if (!newCash.amount || !(Number(newCash.amount) > 0)) return "Enter an amount";
  if (!newCash.account_id) return "Choose an account";
  if (!newCash.confirmed) return "Confirm the box above";
  if (!question.trim()) return "Ask a question";
  return null;
}

export default function Composer({ snapshot, unresolved, workflow, onWorkflow, question, onQuestion, newCash, onNewCash, running, collapsed, onExpand, onSubmit }: Props) {
  const [active, setActive] = useState(0);
  // The menu is for picking a command; once it has an argument ("/stock AVGO") the line is a question to send.
  const slashOpen = !workflow && /^\/\S*$/.test(question);
  const matches = slashOpen ? WORKFLOWS.filter(item => item.cmd.startsWith(question.trim().split(" ")[0] || "/")) : [];
  const accounts = snapshot?.accounts || [];
  const currencies = [...new Set([snapshot?.reporting_currency, "CAD", "USD", ...(snapshot?.positions.map(row => row.currency) || [])].filter((c): c is string => !!c))];
  const label = newCashLabel(newCash, snapshot);
  // Without a workflow, a question is routed: a company you own becomes Stock Analysis, anything else a portfolio review.
  const blocked = slashOpen ? "Choose a workflow" : missingInput(snapshot, workflow === "new-cash" ? newCash : null, question, unresolved);
  const update = (patch: Partial<NewCashInput>) => onNewCash({ ...newCash, ...patch, confirmed: "confirmed" in patch ? !!patch.confirmed : false });

  function choose(index: number) {
    const item = matches[index];
    if (!item) return;
    if (!item.live) { window.location.href = "/classic"; return; }
    const rest = question.slice(item.cmd.length).trimStart();
    if (item.cmd === "/review") { onWorkflow(null); onQuestion(rest || "Review my portfolio"); return; }
    if (item.cmd === "/stock") { onWorkflow(null); onQuestion(`/stock ${rest}`); return; }
    if (item.cmd === "/rebalance") { onWorkflow(null); onQuestion(`/rebalance ${rest}`); return; }
    onWorkflow("new-cash"); onQuestion(rest);
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
    if (!blocked && !running) onSubmit();
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
            <label className="field"><span className="cap">Currency</span>
              <select className="in" value={newCash.currency || ""} onChange={event => update({ currency: event.target.value })}>
                {currencies.map(currency => <option key={currency} value={currency}>{currency}</option>)}
              </select></label>
            <label className="field"><span className="cap">Into account</span>
              <select className="in" value={newCash.account_id || ""} onChange={event => update({ account_id: event.target.value || null })}>
                <option value="">Choose an account</option>
                {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
              </select></label>
          </div>
          <label className="field"><span className="cap">Loss tolerance or withdrawal plans</span>
            <input className="in" maxLength={1000} placeholder="e.g. could hold through a 30% drop; no withdrawals for 5 years" value={newCash.risk_context || ""}
              onChange={event => onNewCash({ ...newCash, risk_context: event.target.value || null })} />
            <span className="cap">You can leave this out, but the analyst won't give an amount without it.</span></label>
          <label className="check"><input type="checkbox" checked={newCash.confirmed} disabled={!label}
            onChange={event => update({ confirmed: event.target.checked })} />
            <span>{label && newCash.amount ? `This ${money(newCash.amount, newCash.currency || "")} is new money, not already in your portfolio as of ${fullDate(snapshot?.as_of)}, and it will arrive in ${accountName(snapshot, newCash.account_id || undefined)}.`
              : "Confirm this is new money and where it arrives."}</span></label>
        </fieldset>}
        {workflow === "new-cash" && collapsed && <div className="box-line" style={{ minHeight: 44 }}>
          <span className="cap n" style={{ flex: 1 }}>{label || "New cash inputs"}{newCash.risk_context ? " · loss tolerance given" : " · no loss tolerance"}</span>
          <button type="button" className="link cap" onClick={onExpand}>Edit inputs</button>
        </div>}
        <div className="box-line">
          {workflow === "new-cash" && <span className="prefix">New cash</span>}
          <label htmlFor="ask" className="sr-only">{workflow ? "Question" : "Ask about your portfolio or a holding, or type / for workflows"}</label>
          <input id="ask" autoComplete="off" value={question} disabled={running} onKeyDown={keys}
            aria-controls={slashOpen ? "slash-menu" : undefined} aria-expanded={slashOpen}
            placeholder={running ? "You can ask a follow-up once this answer arrives." : workflow ? collapsed ? "Follow up. This runs a new analysis with the same portfolio." : "I have new cash. Where should I allocate it?" : collapsed ? "Ask a follow-up. Each question is a fresh analysis of your saved portfolio." : "Ask about your portfolio or a holding, or type / for a workflow"}
            onChange={event => { onQuestion(event.target.value); setActive(0); }} />
          {(workflow === "new-cash" || question.trim()) && blocked && !running && !slashOpen && <span className="cap amber hint">{blocked}</span>}
          {workflow === "new-cash" ? <button type="submit" className="btn primary" disabled={!!blocked || running}>Analyze</button>
            : question.trim() && !slashOpen ? <button type="submit" className="btn primary" disabled={!!blocked || running}>Ask</button> : <span className="cap">/</span>}
        </div>
      </div>
    </div>
  </form>;
}
