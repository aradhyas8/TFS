"use client";

import { useEffect, useState, type FormEvent, type SetStateAction } from "react";
import ThemeInputs from "../components/ThemeInputs";
import type { ThemeInput } from "../lib/contracts";
import PortfolioEditor from "../components/PortfolioEditor";
import PortfolioReviewInputs from "../components/PortfolioReviewInputs";
import NewCashInputs from "../components/NewCashInputs";
import ReviewResult from "../components/ReviewResult";
import GuardrailInputs from "../components/GuardrailInputs";
import ComparisonInputs from "../components/ComparisonInputs";
import SavedDecisionView from "../components/SavedDecisionView";
import SavedDecisionsList from "../components/SavedDecisionsList";
import {
  get,
  post,
  isThemeCandidate,
  isSupportedStock,
  themeComparison,
  portfolioReviewComparison,
  type PortfolioReviewInput,
  type NewCashInput,
  type Analysis,
  type Snapshot,
  type PortfolioSettings,
  type ProposedChanges,
  type ComparisonInput,
  type SavedDecision,
} from "../lib/contracts";

export default function Page() {
  const [snapshot, setSnapshot] = useState<Snapshot>({ as_of: "", reporting_currency: "CAD",
    accounts: [{ id: "account-1", name: "" }], positions: [], fx: [] });
  const [themeEnabled, setThemeEnabled] = useState(false);
  const [theme, setTheme] = useState<ThemeInput>({ name: null, mechanism: null, shortlist: [], max_candidates: 2, max_tool_calls: 16, confirmed: false });
  const [newCashEnabled, setNewCashEnabled] = useState(false);
  const [newCash, setNewCash] = useState<NewCashInput>({ amount: null, cash_position_id: null, confirmed: false, risk_context: null });
  const [reviewEnabled, setReviewEnabled] = useState(false);
  const [reviewContext, setReviewContext] = useState<PortfolioReviewInput>({ prior_theses: [], risk_context: null });
  const [stockId, setStockId] = useState("");
  const [question, setQuestion] = useState("");
  const [settings, setSettings] = useState<PortfolioSettings>({});
  const [changes, setChanges] = useState<ProposedChanges>({ new_cash: [], trades: [] });
  const [comparison, setComparison] = useState<ComparisonInput | null>(null);
  const [result, setResult] = useState<Analysis | null>(null);
  const [decisions, setDecisions] = useState<SavedDecision[]>([]);
  const [reopenedDecision, setReopenedDecision] = useState<SavedDecision | null>(null);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<SavedDecision[]>("/api/decisions")
      .then(setDecisions)
      .catch(() => {});
  }, []);


  function updateSnapshot(update: SetStateAction<Snapshot>) {
    const next = typeof update === "function" ? update(snapshot) : update;
    setSnapshot(next);
    setTheme(current => ({ ...current, confirmed: false, shortlist: current.shortlist.filter(id => next.positions.some(row => row.id === id && isThemeCandidate(row))) }));
    setReviewContext(current => ({ ...current, prior_theses: current.prior_theses.filter(prior => next.positions.some(row => row.company_id === prior.company_id)) }));
    if (next !== snapshot) setNewCash(current => ({ ...current, confirmed: false,
      cash_position_id: next.positions.some(row => row.kind === "cash" && row.id === current.cash_position_id) ? current.cash_position_id : null }));
    const nextStockId = next.positions.some(row => row.id === stockId && isSupportedStock(row)) ? stockId : "";
    setStockId(nextStockId);
    setComparison(current => {
      if (!current) return null;
      const positions = new Map(next.positions.map(row => [row.id, row]));
      const scope = current.scope_position_ids.filter(id => positions.has(id));
      const alternatives = reviewEnabled ? portfolioReviewComparison(next).alternatives : current.alternatives.filter(alt => {
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
      return { ...current, scope_position_ids: reviewEnabled ? next.positions.map(row => row.id) : scope, alternatives, fund_facts: facts,
        effects: next.reporting_currency !== snapshot.reporting_currency ? [] : current.effects.filter(effect => alternatives.some(alt => alt.id === effect.alternative_id)) };
    });
  }

  const boundThemeComparison = themeEnabled && theme.shortlist.length ? { ...themeComparison(snapshot, theme), fund_facts: comparison?.fund_facts || [], effects: comparison?.effects || [] } : null;

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setResult(null);
    try {
      const answer = await post<Analysis>("/api/analyze", { question, portfolio: snapshot,
        theme: themeEnabled ? theme : undefined,
        new_cash: newCashEnabled ? newCash : undefined,
        portfolio_review: reviewEnabled ? reviewContext : undefined,
        stock: !themeEnabled && !newCashEnabled && !reviewEnabled && stockId ? { position_id: stockId } : undefined,
        settings: Object.values(settings).some(value => value !== undefined) ? settings : undefined,
        comparison: themeEnabled ? boundThemeComparison : !newCashEnabled && comparison ? { ...comparison,
          fund_facts: comparison.fund_facts.filter(row => comparison.scope_position_ids.includes(row.position_id) || comparison.alternatives.some(alt => alt.position_id === row.position_id)),
          effects: comparison.effects.filter(row => comparison.alternatives.some(alt => alt.id === row.alternative_id)) } : undefined,
        proposed_changes: !newCashEnabled && (changes.new_cash.length || changes.trades.length) ? changes : undefined });
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
      <ThemeInputs enabled={themeEnabled} onEnabled={enabled => { setThemeEnabled(enabled); setTheme(current => ({ ...current, confirmed: false })); if (enabled) { setReviewEnabled(false); setNewCashEnabled(false); setStockId(""); setComparison(null); } }} value={theme} onChange={setTheme} snapshot={snapshot} busy={busy || importing} />
      {themeEnabled && boundThemeComparison && <ComparisonInputs value={boundThemeComparison} onChange={next => { setComparison(next); setTheme(current => ({ ...current, confirmed: false })); }} snapshot={snapshot} themeDiscovery busy={busy || importing} />}
      <PortfolioReviewInputs enabled={reviewEnabled} onEnabled={enabled => { setReviewEnabled(enabled); if (enabled) { setThemeEnabled(false); setNewCashEnabled(false); setStockId(""); setComparison(null); } }} value={reviewContext} onChange={setReviewContext} snapshot={snapshot} busy={busy || importing} />
      <NewCashInputs enabled={newCashEnabled} onEnabled={enabled => { setNewCashEnabled(enabled); if (enabled) { setThemeEnabled(false); setReviewEnabled(false); } }} value={newCash} onChange={setNewCash}
        snapshot={snapshot} busy={busy || importing} />
      {!newCashEnabled && !themeEnabled && <>{!reviewEnabled && <section className="panel" aria-label="Stock question inputs"><h2>Analyze a stock</h2>
        <label>US or Canadian stock to analyze<select aria-label="US stock to analyze" value={stockId} disabled={busy || importing} onChange={event => { const id = event.target.value; setStockId(id); setComparison(current => current ? { ...current, alternatives: current.alternatives.flatMap(alt => alt.kind === "stock" ? id ? [{ ...alt, position_id: id }] : [] : [alt]), effects: current.effects.filter(effect => id || effect.alternative_id !== "company") } : null); }}>
          <option value="">Portfolio review only</option>{snapshot.positions.filter(isSupportedStock).map(row => <option key={row.id} value={row.id}>{row.ticker} / {row.listing} / {row.id}</option>)}
        </select></label><p className="muted small">Choose the listing and ask your stock question below. Add a candidate with zero shares if needed. Primary research uses available issuer and SEC or SEDAR+ evidence. Without a custom comparison, the analysis compares the company with available diversified fund and cash rows and retaining the selected scope.</p></section>}
      <ComparisonInputs value={comparison} onChange={setComparison} snapshot={snapshot} stockId={stockId} portfolioReview={reviewEnabled} busy={busy || importing} /></>}
      <section className="panel question-panel" aria-labelledby="question-title"><p className="eyebrow">02 / ASK YOUR ANALYST</p><h2 id="question-title">What would you like to understand?</h2>
        <label className="sr-only" htmlFor="question">Investment question</label><textarea id="question" required maxLength={1000} value={question}
          disabled={busy || importing} placeholder="How concentrated is my portfolio across all accounts?" onChange={event => setQuestion(event.target.value)} />
        <div className="question-footer"><p className="muted small">Basic exposure review using your supplied snapshot.</p><button className="button primary" type="submit"
          disabled={busy || importing || !question.trim() || !snapshot.as_of}>{busy ? "Reviewing portfolio…" : "Review portfolio →"}</button></div>
      </section>
    </form>
    {error && <div className="error" role="alert">{error}</div>}
    {busy && <p className="loading" role="status">Calculating your portfolio and preparing the review…</p>}
    {reopenedDecision ? (
      <SavedDecisionView
        decision={reopenedDecision}
        onClose={() => setReopenedDecision(null)}
        onUpdated={(updated) => {
          setReopenedDecision(updated);
          setDecisions((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
        }}
      />
    ) : (
      <>
        {result ? (
          <ReviewResult
            result={result}
            onSaved={(saved) => setDecisions((prev) => [saved, ...prev.filter((d) => d.id !== saved.id)])}
            onReopen={(saved) => setReopenedDecision(saved)}
          />
        ) : (
          !busy && (
            <div className="empty-state">
              <span>↗</span>
              <p>Your portfolio review will appear here.</p>
              <small>Supplied values, transparent calculations, conditional direction.</small>
            </div>
          )
        )}
      </>
    )}
    <SavedDecisionsList
      decisions={decisions}
      onReopen={(decision) => setReopenedDecision(decision)}
      busy={busy}
    />
    <footer>Personal Investment Analyst · Dated snapshot review</footer>

  </main>;
}
