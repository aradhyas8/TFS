import { useState, type FormEvent, type KeyboardEvent } from "react";
import { isThemeCandidate, type NewCashInput, type Position, type Snapshot, type ThemeInput } from "../../lib/contracts";
import { accountName, fullDate, money, newCashLabel } from "./format";

const WORKFLOWS = [
  { cmd: "/new-cash", text: "Decide where new money should go", live: true },
  { cmd: "/stock", text: "Analyze one company you own, e.g. /stock AVGO", live: true },
  { cmd: "/review", text: "Exposure, concentration and fund overlap", live: true },
  { cmd: "/rebalance", text: "Should anything change? Reduce, keep or add, within your rules", live: true },
  { cmd: "/theme", text: "Test an idea against a short list", live: true },
];

type Props = {
  snapshot: Snapshot | null;
  unresolved: number;
  workflow: "new-cash" | "theme" | null;
  onWorkflow: (workflow: "new-cash" | "theme" | null) => void;
  question: string;
  onQuestion: (value: string) => void;
  newCash: NewCashInput;
  onNewCash: (value: NewCashInput) => void;
  theme: ThemeInput;
  onTheme: (value: ThemeInput) => void;
  themeCandidates: Position[];
  onAddCandidate: (ticker: string) => Promise<boolean>;
  onRemoveCandidate: (id: string) => void;
  running: boolean;
  collapsed: boolean;
  onExpand: () => void;
  onSubmit: () => void;
};

export function missingInput(
  snapshot: Snapshot | null,
  newCash: NewCashInput | null,
  theme: ThemeInput | null,
  question: string,
  unresolved = 0
): string | null {
  if (!snapshot) return "Import a portfolio first";
  if (unresolved) return `Identify ${unresolved} holding${unresolved === 1 ? "" : "s"} first`;
  if (newCash) {
    if (!newCash.amount || !(Number(newCash.amount) > 0)) return "Enter an amount";
    if (!newCash.account_id) return "Choose an account";
    if (!newCash.confirmed) return "Confirm the box above";
    if (!question.trim()) return "Ask a question";
    return null;
  }
  if (theme) {
    if (!theme.name?.trim()) return "Enter a theme name";
    if (!theme.mechanism?.trim()) return "Describe the economic mechanism";
    if (!theme.shortlist.length) return "Add 1–4 candidate ticker chips";
    if (theme.shortlist.length > theme.max_candidates) return "Shortlist exceeds max candidates";
    return null;
  }
  if (!question.trim()) return "Ask a question";
  return null;
}

export default function Composer({
  snapshot,
  unresolved,
  workflow,
  onWorkflow,
  question,
  onQuestion,
  newCash,
  onNewCash,
  theme,
  onTheme,
  themeCandidates,
  onAddCandidate,
  onRemoveCandidate,
  running,
  collapsed,
  onExpand,
  onSubmit,
}: Props) {
  const [active, setActive] = useState(0);
  const [tickerInput, setTickerInput] = useState("");
  const [tickerBusy, setTickerBusy] = useState(false);
  const [tickerError, setTickerError] = useState("");

  const slashOpen = !workflow && /^\/\S*$/.test(question);
  const matches = slashOpen ? WORKFLOWS.filter(item => item.cmd.startsWith(question.trim().split(" ")[0] || "/")) : [];
  const accounts = snapshot?.accounts || [];
  const currencies = [...new Set([snapshot?.reporting_currency, "CAD", "USD", ...(snapshot?.positions.map(row => row.currency) || [])].filter((c): c is string => !!c))];
  const label = newCashLabel(newCash, snapshot);
  const blocked = slashOpen
    ? "Choose a workflow"
    : missingInput(
        snapshot,
        workflow === "new-cash" ? newCash : null,
        workflow === "theme" ? theme : null,
        question,
        unresolved
      );

  const updateCash = (patch: Partial<NewCashInput>) =>
    onNewCash({ ...newCash, ...patch, confirmed: "confirmed" in patch ? !!patch.confirmed : false });

  const heldCandidates = (snapshot?.positions || []).filter(isThemeCandidate);

  async function handleAddTicker() {
    const raw = tickerInput.trim();
    if (!raw) return;
    if (theme.shortlist.length >= 4) {
      setTickerError("Maximum 4 candidates.");
      return;
    }
    setTickerError("");
    const held = heldCandidates.find(p => p.ticker?.toUpperCase() === raw.toUpperCase());
    if (held) {
      if (theme.shortlist.includes(held.id)) {
        setTickerError(`${held.ticker} is already in the shortlist.`);
        return;
      }
      const nextShortlist = [...theme.shortlist, held.id];
      onTheme({
        ...theme,
        shortlist: nextShortlist,
        max_candidates: Math.max(1, nextShortlist.length),
        confirmed: false,
      });
      setTickerInput("");
      return;
    }
    setTickerBusy(true);
    try {
      const ok = await onAddCandidate(raw);
      if (ok) {
        setTickerInput("");
      } else {
        setTickerError(`Could not find "${raw}" as a US or Canadian stock or ETF.`);
      }
    } catch {
      setTickerError(`Lookup failed for "${raw}".`);
    } finally {
      setTickerBusy(false);
    }
  }

  function handleAddHeld(posId: string) {
    if (!posId) return;
    if (theme.shortlist.includes(posId)) return;
    if (theme.shortlist.length >= 4) {
      setTickerError("Maximum 4 candidates.");
      return;
    }
    setTickerError("");
    const nextShortlist = [...theme.shortlist, posId];
    onTheme({
      ...theme,
      shortlist: nextShortlist,
      max_candidates: Math.max(1, nextShortlist.length),
      confirmed: false,
    });
  }

  function handleRemoveCandidate(id: string) {
    onRemoveCandidate(id);
    const nextShortlist = theme.shortlist.filter(item => item !== id);
    onTheme({
      ...theme,
      shortlist: nextShortlist,
      max_candidates: Math.max(1, nextShortlist.length),
      confirmed: false,
    });
  }

  function choose(index: number) {
    const item = matches[index];
    if (!item) return;
    const rest = question.slice(item.cmd.length).trimStart();
    if (item.cmd === "/review") { onWorkflow(null); onQuestion(rest || "Review my portfolio"); return; }
    if (item.cmd === "/stock") { onWorkflow(null); onQuestion(`/stock ${rest}`); return; }
    if (item.cmd === "/rebalance") { onWorkflow(null); onQuestion(`/rebalance ${rest}`); return; }
    if (item.cmd === "/theme") {
      onWorkflow("theme");
      if (rest) onTheme({ ...theme, name: rest, confirmed: false });
      onQuestion("");
      return;
    }
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
                onChange={event => updateCash({ amount: event.target.value.replace(/[^0-9.]/g, "") || null })} /></label>
            <label className="field"><span className="cap">Currency</span>
              <select className="in" value={newCash.currency || ""} onChange={event => updateCash({ currency: event.target.value })}>
                {currencies.map(currency => <option key={currency} value={currency}>{currency}</option>)}
              </select></label>
            <label className="field"><span className="cap">Into account</span>
              <select className="in" value={newCash.account_id || ""} onChange={event => updateCash({ account_id: event.target.value || null })}>
                <option value="">Choose an account</option>
                {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
              </select></label>
          </div>
          <label className="field"><span className="cap">Loss tolerance or withdrawal plans</span>
            <input className="in" maxLength={1000} placeholder="e.g. could hold through a 30% drop; no withdrawals for 5 years" value={newCash.risk_context || ""}
              onChange={event => onNewCash({ ...newCash, risk_context: event.target.value || null })} />
            <span className="cap">You can leave this out, but the analyst won't give an amount without it.</span></label>
          <label className="check"><input type="checkbox" checked={newCash.confirmed} disabled={!label}
            onChange={event => updateCash({ confirmed: event.target.checked })} />
            <span>{label && newCash.amount ? `This ${money(newCash.amount, newCash.currency || "")} is new money, not already in your portfolio as of ${fullDate(snapshot?.as_of)}, and it will arrive in ${accountName(snapshot, newCash.account_id || undefined)}.`
              : "Confirm this is new money and where it arrives."}</span></label>
        </fieldset>}

        {workflow === "theme" && !collapsed && <fieldset className="theme-form" disabled={running}>
          <legend className="sr-only">Theme inputs</legend>
          <label className="field"><span className="cap">Theme name</span>
            <input className="in" aria-label="Theme name" placeholder="e.g. Industrial automation" value={theme.name || ""}
              onChange={event => onTheme({ ...theme, name: event.target.value, confirmed: false })} /></label>
          <label className="field"><span className="cap">Economic mechanism</span>
            <textarea className="in" aria-label="Economic mechanism" rows={2} placeholder="Name how the theme changes revenue, margins, reinvestment or fund exposure, and what evidence could disprove it."
              value={theme.mechanism || ""} onChange={event => onTheme({ ...theme, mechanism: event.target.value, confirmed: false })} />
            <span className="cap">Name how the theme changes revenue, margins, reinvestment or fund exposure.</span></label>
          <div className="field">
            <span className="cap">Agreed shortlist (1–4 candidates)</span>
            <div className="chips" role="list" aria-label="Candidate chips">
              {theme.shortlist.map(id => {
                const pos = snapshot?.positions.find(p => p.id === id) || themeCandidates.find(p => p.id === id);
                const chipLabel = pos?.ticker || pos?.company_name || id;
                return <span className="chip" role="listitem" key={id}>
                  <span>{chipLabel}</span>
                  <button type="button" aria-label={`Remove ${chipLabel}`} onClick={() => handleRemoveCandidate(id)}>×</button>
                </span>;
              })}
              {!theme.shortlist.length && <span className="cap m">No candidates selected yet. Add 1–4 candidates below.</span>}
            </div>
            {theme.shortlist.length < 4 && <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
              <input className="in" style={{ maxWidth: 160 }} placeholder="Ticker (e.g. NVDA)" value={tickerInput} disabled={tickerBusy}
                aria-label="Add candidate ticker" onChange={event => setTickerInput(event.target.value.toUpperCase())}
                onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); void handleAddTicker(); } }} />
              <button type="button" className="btn small" disabled={!tickerInput.trim() || tickerBusy} onClick={() => void handleAddTicker()}>
                {tickerBusy ? "Looking up…" : "+ Add"}
              </button>
              {heldCandidates.length > 0 && <select className="in" style={{ maxWidth: 220 }} value="" aria-label="Add held position to shortlist"
                onChange={event => handleAddHeld(event.target.value)}>
                <option value="">Or pick held stock/ETF…</option>
                {heldCandidates.filter(p => !theme.shortlist.includes(p.id)).map(p => <option key={p.id} value={p.id}>
                  {p.ticker || p.company_name} ({p.id})
                </option>)}
              </select>}
            </div>}
            {tickerError && <span className="cap amber">{tickerError}</span>}
          </div>
          <div className="nc-grid">
            <label className="field"><span className="cap">Max candidates (1–4)</span>
              <input className="in n" type="number" min={1} max={4} aria-label="Maximum candidates" value={theme.max_candidates}
                onChange={event => { const bound = Math.max(1, Math.min(4, Number(event.target.value) || 1)); onTheme({ ...theme, max_candidates: bound, confirmed: false }); }} /></label>
            <label className="field"><span className="cap">Max tool calls (1–24)</span>
              <input className="in n" type="number" min={1} max={24} aria-label="Maximum research calls" value={theme.max_tool_calls}
                onChange={event => { const bound = Math.max(1, Math.min(24, Number(event.target.value) || 1)); onTheme({ ...theme, max_tool_calls: bound, confirmed: false }); }} /></label>
          </div>
        </fieldset>}

        {workflow === "new-cash" && collapsed && <div className="box-line" style={{ minHeight: 44 }}>
          <span className="cap n" style={{ flex: 1 }}>{label || "New cash inputs"}{newCash.risk_context ? " · loss tolerance given" : " · no loss tolerance"}</span>
          <button type="button" className="link cap" onClick={onExpand}>Edit inputs</button>
        </div>}

        {workflow === "theme" && collapsed && <div className="box-line" style={{ minHeight: 44 }}>
          <span className="cap n" style={{ flex: 1 }}>{theme.name || "Theme inputs"} · {theme.shortlist.length} candidate{theme.shortlist.length === 1 ? "" : "s"} · bound {theme.max_tool_calls} calls</span>
          <button type="button" className="link cap" onClick={onExpand}>Edit inputs</button>
        </div>}

        <div className="box-line">
          {workflow === "new-cash" && <span className="prefix">New cash</span>}
          {workflow === "theme" && <span className="prefix">Theme</span>}
          <label htmlFor="ask" className="sr-only">{workflow ? "Question" : "Ask about your portfolio or a holding, or type / for workflows"}</label>
          <input id="ask" autoComplete="off" value={question} disabled={running} onKeyDown={keys}
            aria-controls={slashOpen ? "slash-menu" : undefined} aria-expanded={slashOpen}
            placeholder={running ? "You can ask a follow-up once this answer arrives." : workflow === "new-cash" ? (collapsed ? "Follow up. This runs a new analysis with the same portfolio." : "I have new cash. Where should I allocate it?") : workflow === "theme" ? (collapsed ? "Follow up. This runs a new analysis with the same portfolio." : "Explore a theme against your shortlist.") : collapsed ? "Ask a follow-up. Each question is a fresh analysis of your saved portfolio." : "Ask about your portfolio or a holding, or type / for a workflow"}
            onChange={event => { onQuestion(event.target.value); setActive(0); }} />
          {(workflow || question.trim()) && blocked && !running && !slashOpen && <span className="cap amber hint">{blocked}</span>}
          {workflow ? <button type="submit" className="btn primary" disabled={!!blocked || running}>Analyze</button>
            : question.trim() && !slashOpen ? <button type="submit" className="btn primary" disabled={!!blocked || running}>Ask</button> : <span className="cap">/</span>}
        </div>
      </div>
    </div>
  </form>;
}
