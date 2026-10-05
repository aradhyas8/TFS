"use client";

import { useState, type FormEvent, type SetStateAction } from "react";
import PortfolioEditor from "../components/PortfolioEditor";
import ReviewResult from "../components/ReviewResult";
import GuardrailInputs from "../components/GuardrailInputs";
import ComparisonInputs from "../components/ComparisonInputs";
import { post, type Analysis, type Snapshot, type PortfolioSettings, type ProposedChanges, type ComparisonInput } from "../lib/contracts";

export default function Page() {
  const [snapshot, setSnapshot] = useState<Snapshot>({ as_of: "", reporting_currency: "CAD",
    accounts: [{ id: "account-1", name: "" }], positions: [], fx: [] });
  const [stockId, setStockId] = useState("");
  const [question, setQuestion] = useState("");
  const [settings, setSettings] = useState<PortfolioSettings>({});
  const [changes, setChanges] = useState<ProposedChanges>({ new_cash: [], trades: [] });
  const [comparison, setComparison] = useState<ComparisonInput | null>(null);
  const [result, setResult] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");

  function updateSnapshot(update: SetStateAction<Snapshot>) {
    const next = typeof update === "function" ? update(snapshot) : update;
    setSnapshot(next);
    const nextStockId = next.positions.some(row => row.id === stockId && row.kind === "stock" && row.currency === "USD" && ["XNAS", "XNYS", "XASE"].includes(row.listing || "")) ? stockId : "";
    setStockId(nextStockId);
    setComparison(current => {
      if (!current) return null;
      const positions = new Map(next.positions.map(row => [row.id, row]));
      const scope = current.scope_position_ids.filter(id => positions.has(id));
      const alternatives = current.alternatives.filter(alt => {
        if (alt.kind === "no_action") return true;
        const row = positions.get(alt.position_id || "");
        return alt.kind === "stock" ? row?.kind === "stock" && row.id === nextStockId : alt.kind === "etf" ? row?.kind === "etf" && row.etf_role === "diversified" : row?.kind === "cash";
      });
      const relevant = new Set([...scope, ...alternatives.map(alt => alt.position_id)]);
      const facts = current.fund_facts.filter(fact => {
        const row = positions.get(fact.position_id);
        const previous = snapshot.positions.find(old => old.id === fact.position_id);
        return relevant.has(fact.position_id) && row?.kind === "etf" && previous?.ticker === row.ticker && previous?.listing === row.listing && previous?.currency === row.currency;
      });
      return { ...current, scope_position_ids: scope, alternatives, fund_facts: facts,
        effects: next.reporting_currency !== snapshot.reporting_currency ? [] : current.effects.filter(effect => alternatives.some(alt => alt.id === effect.alternative_id)) };
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setResult(null);
    try {
      const answer = await post<Analysis>("/api/analyze", { question, portfolio: snapshot,
        stock: stockId ? { position_id: stockId } : undefined,
        settings: Object.values(settings).some(value => value !== undefined) ? settings : undefined,
        comparison: comparison ? { ...comparison,
          fund_facts: comparison.fund_facts.filter(row => comparison.scope_position_ids.includes(row.position_id) || comparison.alternatives.some(alt => alt.position_id === row.position_id)),
          effects: comparison.effects.filter(row => comparison.alternatives.some(alt => alt.id === row.alternative_id)) } : undefined,
        proposed_changes: changes.new_cash.length || changes.trades.length ? changes : undefined });
      if (answer.status !== "completed") throw new Error("The review was not completed.");
      setResult(answer);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The review could not be completed."); }
    finally { setBusy(false); }
  }

  return <main>
    <header className="masthead"><a href="/" className="brand"><span className="brand-mark">P</span>Personal Investment Analyst</a><span className="header-note">A whole-portfolio perspective</span></header>
    <div className="intro"><p className="eyebrow">YOUR PORTFOLIO, IN CONTEXT</p><h1>Understand what you own.<br /><span>Then ask what matters.</span></h1>
      <p>A dated view of your accounts, cash and company exposure, with the unknowns kept visible.</p></div>
    <form onSubmit={submit}>
      <PortfolioEditor snapshot={snapshot} setSnapshot={updateSnapshot} busy={busy || importing} onError={setError}
        importing={importing} setImporting={setImporting} />
      <GuardrailInputs settings={settings} setSettings={setSettings} changes={changes} setChanges={setChanges}
        snapshot={snapshot} busy={busy || importing} />
      <section className="panel" aria-label="Stock question inputs"><h2>Analyze a US stock</h2>
        <label>US stock to analyze<select value={stockId} disabled={busy || importing} onChange={event => { const id = event.target.value; setStockId(id); setComparison(current => current ? { ...current, alternatives: current.alternatives.flatMap(alt => alt.kind === "stock" ? id ? [{ ...alt, position_id: id }] : [] : [alt]), effects: current.effects.filter(effect => id || effect.alternative_id !== "company") } : null); }}>
          <option value="">Portfolio review only</option>{snapshot.positions.filter(row => row.kind === "stock" && row.currency === "USD" && ["XNAS", "XNYS", "XASE"].includes(row.listing || "")).map(row => <option key={row.id} value={row.id}>{row.ticker} / {row.listing} / {row.id}</option>)}
        </select></label><p className="muted small">Choose the listing and ask your stock question below. Add a candidate with zero shares if needed. Primary research uses available issuer and SEC evidence. Without a custom comparison, the analysis compares the company with available diversified fund and cash rows and retaining the selected scope.</p></section>
      <ComparisonInputs value={comparison} onChange={setComparison} snapshot={snapshot} stockId={stockId} busy={busy || importing} />
      <section className="panel question-panel" aria-labelledby="question-title"><p className="eyebrow">02 / ASK YOUR ANALYST</p><h2 id="question-title">What would you like to understand?</h2>
        <label className="sr-only" htmlFor="question">Investment question</label><textarea id="question" required maxLength={1000} value={question}
          disabled={busy || importing} placeholder="How concentrated is my portfolio across all accounts?" onChange={event => setQuestion(event.target.value)} />
        <div className="question-footer"><p className="muted small">Basic exposure review using your supplied snapshot.</p><button className="button primary" type="submit"
          disabled={busy || importing || !question.trim() || !snapshot.as_of}>{busy ? "Reviewing portfolio…" : "Review portfolio →"}</button></div>
      </section>
    </form>
    {error && <div className="error" role="alert">{error}</div>}
    {busy && <p className="loading" role="status">Calculating your portfolio and preparing the review…</p>}
    {result ? <ReviewResult result={result} /> : !busy && <div className="empty-state"><span>↗</span><p>Your portfolio review will appear here.</p><small>Supplied values, transparent calculations, conditional direction.</small></div>}
    <footer>Personal Investment Analyst · Dated snapshot review</footer>
  </main>;
}
