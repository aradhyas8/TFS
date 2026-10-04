"use client";

import { useState, type FormEvent } from "react";
import PortfolioEditor from "../components/PortfolioEditor";
import ReviewResult from "../components/ReviewResult";
import GuardrailInputs from "../components/GuardrailInputs";
import { post, type Analysis, type Snapshot, type PortfolioSettings, type ProposedChanges } from "../lib/contracts";

export default function Page() {
  const [snapshot, setSnapshot] = useState<Snapshot>({ as_of: "", reporting_currency: "CAD",
    accounts: [{ id: "account-1", name: "" }], positions: [], fx: [] });
  const [question, setQuestion] = useState("");
  const [settings, setSettings] = useState<PortfolioSettings>({});
  const [changes, setChanges] = useState<ProposedChanges>({ new_cash: [], trades: [] });
  const [result, setResult] = useState<Analysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setResult(null);
    try {
      const answer = await post<Analysis>("/api/analyze", { question, portfolio: snapshot,
        settings: Object.values(settings).some(value => value !== undefined) ? settings : undefined,
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
      <PortfolioEditor snapshot={snapshot} setSnapshot={setSnapshot} busy={busy || importing} onError={setError}
        importing={importing} setImporting={setImporting} />
      <GuardrailInputs settings={settings} setSettings={setSettings} changes={changes} setChanges={setChanges}
        snapshot={snapshot} busy={busy || importing} />
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
