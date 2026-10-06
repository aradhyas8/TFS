"use client";

import { useEffect, useRef, useState } from "react";
import { get, post, type Analysis, type DecisionAction, type NewCashInput, type PortfolioSettings, type SavedDecision, type Snapshot } from "../../lib/contracts";
import Rail from "../../components/desk/Rail";
import Composer from "../../components/desk/Composer";
import Panel, { evidenceFor, ImportCsv } from "../../components/desk/Panel";
import { Answer, Echo, Failure, Historical, Waiting, type Tab } from "../../components/desk/Memo";
import { accountName, clock, money, pct, shortDate } from "../../components/desk/format";

type Sent = { question: string; newCash: NewCashInput };
const EMPTY_CASH: NewCashInput = { amount: null, cash_position_id: null, confirmed: false, risk_context: null };
const DURATIONS_KEY = "desk.newCashDurations";
const TITLE = "New cash · Analyst";

function pastDurations(): number[] {
  try { return JSON.parse(localStorage.getItem(DURATIONS_KEY) || "[]").filter((n: unknown) => typeof n === "number").slice(-3); } catch { return []; }
}

export default function NewCashPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [setupAsOf, setSetupAsOf] = useState("");
  const [setupCurrency, setSetupCurrency] = useState("CAD");
  const [settings, setSettings] = useState<PortfolioSettings>({});
  const [workflow, setWorkflow] = useState<"new-cash" | null>(null);
  const [question, setQuestion] = useState("");
  const [newCash, setNewCash] = useState<NewCashInput>(EMPTY_CASH);
  const [collapsed, setCollapsed] = useState(false);
  const [run, setRun] = useState<(Sent & { id: number; startedAt: number }) | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [past, setPast] = useState<number[]>([]);
  const [answer, setAnswer] = useState<(Sent & { analysis: Analysis }) | null>(null);
  const [failure, setFailure] = useState<(Sent & { message: string }) | null>(null);
  const [saved, setSaved] = useState<SavedDecision | null>(null);
  const [saving, setSaving] = useState(false);
  const [decisions, setDecisions] = useState<SavedDecision[]>([]);
  const [reopened, setReopened] = useState<SavedDecision | null>(null);
  const [tab, setTab] = useState<Tab>("holdings");
  const [focus, setFocus] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const runId = useRef(0);

  useEffect(() => { get<SavedDecision[]>("/api/decisions").then(setDecisions).catch(() => setNotice("Saved decisions couldn't be loaded.")); setPast(pastDurations()); }, []);
  useEffect(() => { if (!run) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [run]);
  useEffect(() => { const reset = () => { if (!document.hidden) document.title = TITLE; }; document.addEventListener("visibilitychange", reset); return () => document.removeEventListener("visibilitychange", reset); }, []);
  // A changed snapshot invalidates the destination and the "this is new money" confirmation, as in the classic view.
  useEffect(() => { setNewCash(current => ({ ...current, confirmed: false,
    cash_position_id: snapshot?.positions.some(row => row.kind === "cash" && row.id === current.cash_position_id) ? current.cash_position_id : null })); }, [snapshot]);

  const result = answer?.analysis ?? null;
  const evidence = evidenceFor(reopened ? null : result, reopened);
  const cite = { numbers: evidence.numbers, onCite: (id: string) => { setTab("evidence"); setFocus(id); setPanelOpen(true); } };
  const openTab = (next: Tab) => { setTab(next); setFocus(null); setPanelOpen(true); };

  async function analyze(sent: Sent) {
    if (!snapshot) return;
    const id = ++runId.current;
    const startedAt = Date.now();
    setRun({ ...sent, id, startedAt }); setNow(startedAt); setFailure(null); setSaved(null); setReopened(null); setCollapsed(true); setNotice("");
    try {
      const analysis = await post<Analysis>("/api/analyze", { question: sent.question, portfolio: snapshot, new_cash: sent.newCash,
        settings: Object.values(settings).some(value => value !== undefined && value !== null) ? settings : undefined });
      if (runId.current !== id) return;
      if (analysis.status !== "completed") throw new Error("The analysis was not completed.");
      setAnswer({ ...sent, analysis }); setTab("evidence"); setFocus(null);
      const durations = [...pastDurations(), Date.now() - startedAt].slice(-3);
      try { localStorage.setItem(DURATIONS_KEY, JSON.stringify(durations)); } catch { /* per-viewer convenience only */ }
      setPast(durations);
      if (document.hidden) document.title = `Answer ready · ${TITLE}`;
    } catch (error) {
      if (runId.current === id) setFailure({ ...sent, message: error instanceof Error ? error.message : "The analysis could not be completed." });
    } finally { if (runId.current === id) setRun(null); }
  }

  function stopWaiting() { runId.current++; setRun(null); setCollapsed(false); }

  async function save() {
    if (!result) return;
    setSaving(true); setNotice("");
    try {
      const record = await post<SavedDecision>("/api/decisions", { result });
      setSaved(record); setDecisions(list => [record, ...list.filter(d => d.id !== record.id)]);
    } catch (error) { setNotice(error instanceof Error ? error.message : "The decision couldn't be saved."); }
    finally { setSaving(false); }
  }

  async function confirm(decision: SavedDecision, action: DecisionAction, notes: string | undefined) {
    const updated = await post<SavedDecision>(`/api/decisions/${decision.id}/confirm`, { action, notes });
    setDecisions(list => list.map(d => d.id === updated.id ? updated : d));
    if (saved?.id === updated.id) setSaved(updated);
    if (reopened?.id === updated.id) setReopened(updated);
  }

  function startOver() {
    runId.current++; setRun(null); setAnswer(null); setFailure(null); setSaved(null); setReopened(null);
    setWorkflow(null); setQuestion(""); setCollapsed(false); setTab("holdings"); setRailOpen(false);
  }

  const cashRow = snapshot?.positions.find(row => row.id === (run ?? answer ?? failure)?.newCash.cash_position_id);
  const sentCash = (run ?? answer ?? failure)?.newCash;
  const ctxLabel = reopened ? `Decision · ${shortDate(reopened.saved_at)}` : sentCash?.amount && cashRow
    ? `New cash · ${money(sentCash.amount, cashRow.currency)} → ${accountName(snapshot, cashRow.account_id)}` : workflow ? "New cash" : "New analysis";
  const holdingsCount = snapshot?.positions.filter(row => row.kind !== "cash").length ?? 0;
  const hasFund = snapshot?.positions.some(row => row.kind === "etf" && row.etf_role === "diversified");
  const scope = snapshot ? [
    `${holdingsCount} holding${holdingsCount === 1 ? "" : "s"} and ${snapshot.positions.length - holdingsCount} cash balance${snapshot.positions.length - holdingsCount === 1 ? "" : "s"} in ${snapshot.accounts.length} account${snapshot.accounts.length === 1 ? "" : "s"}, snapshot ${shortDate(snapshot.as_of)}`,
    settings.single_company_cap || settings.active_budget ? `Your rules: ${settings.single_company_cap ? `${pct(settings.single_company_cap)} per company` : "no company cap"}, ${settings.active_budget ? `${pct(settings.active_budget)} in active picks` : "no active budget"}` : "No rules set, so no limits are checked",
    "A bounded screen of what you could add to, then filings for candidates that could change the answer",
    `Against ${hasFund ? "a diversified fund, " : ""}keeping cash and doing nothing`,
  ] : [];

  let center;
  if (reopened) center = <Historical decision={reopened} snapshot={snapshot} cite={cite} onConfirm={(action, notes) => confirm(reopened, action, notes)}
    onRerun={() => { setReopened(null); setWorkflow("new-cash"); setQuestion(reopened.question); setCollapsed(false); setTab("holdings"); }} />;
  else if (run) center = <><Echo question={run.question} newCash={run.newCash} snapshot={snapshot} /><Waiting startedAt={run.startedAt} now={now} past={past} scope={scope} onStop={stopWaiting} /></>;
  else if (failure) center = <><Echo question={failure.question} newCash={failure.newCash} snapshot={snapshot} /><Failure message={failure.message} onRetry={() => analyze(failure)} /></>;
  else if (answer) center = <Answer key={answer.analysis.portfolio.reviewed_at} result={answer.analysis} sent={answer} snapshot={snapshot} cite={cite} saved={saved} onTab={openTab}
    onConfirm={(action, notes) => saved ? confirm(saved, action, notes) : Promise.resolve()}
    onRerun={risk => { const next = { ...answer.newCash, risk_context: risk }; setNewCash(next); void analyze({ question: answer.question, newCash: next }); }} />;
  else if (!snapshot) center = <div className="hero">
    <h1>Start with what you own.</h1>
    <p className="body" style={{ maxWidth: 520 }}>Import your portfolio CSV. Values, weights and rules are checked by the analysis; nothing is invented here.</p>
    <div className="setup">
      <label className="field"><span className="cap">Snapshot date</span><input className="in" type="date" aria-label="As-of date" value={setupAsOf} onChange={event => setSetupAsOf(event.target.value)} /></label>
      <label className="field"><span className="cap">Reporting currency</span><input className="in" aria-label="Reporting currency" value={setupCurrency} maxLength={3} onChange={event => setSetupCurrency(event.target.value.toUpperCase())} /></label>
    </div>
    <ImportCsv snapshot={null} setSnapshot={setSnapshot} onError={setNotice} label="Choose a portfolio CSV" asOf={setupAsOf} currency={setupCurrency} />
    <p className="cap"><a href="/api/portfolio/template" download>Download the CSV template</a></p>
  </div>;
  else center = <div className="hero">
    <h1>{workflow ? "Where should new money go?" : "What should we look at?"}</h1>
    {workflow ? <p className="body" style={{ maxWidth: 560 }}>The analyst screens what you could add to, researches candidates that could change the answer, and compares them with keeping cash and doing nothing. You place any order yourself.</p>
      : <div className="starters">
        <button type="button" className="starter" onClick={() => { setWorkflow("new-cash"); setCollapsed(false); document.getElementById("ask")?.focus(); }}><span>/new-cash</span><span className="m">Decide where new money should go</span></button>
        <a className="starter" href="/"><span>/rebalance</span><span className="m">Recheck holdings against your rules · classic view</span></a>
        <a className="starter" href="/"><span>/stock</span><span className="m">Value one company with its filings · classic view</span></a>
      </div>}
  </div>;

  return <div className={`desk${panelOpen ? " panel-open" : ""}${railOpen ? " rail-open" : ""}`}>
    <a className="skip" href="#details">Skip to details</a>
    <Rail snapshot={snapshot} settings={settings} result={result} decisions={decisions} currentId={reopened?.id ?? saved?.id ?? null} running={!!run}
      onNew={startOver} onHoldings={() => { openTab("holdings"); setRailOpen(false); }} onOpen={decision => { setReopened(decision); setTab("evidence"); setFocus(null); setRailOpen(false); }} />
    <main className="center">
      <header className="ctx">
        <div className="ctx-left">
          <button type="button" className="icon-btn menu-btn" aria-label="Open portfolio and decisions" onClick={() => setRailOpen(true)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg></button>
          <span>{ctxLabel}</span>
        </div>
        <div className="ctx-right">
          <span className="cap hide-sm">{reopened ? "Read-only" : snapshot ? `Snapshot ${shortDate(snapshot.as_of)}${result && !run ? ` · answered ${clock(result.portfolio.reviewed_at)}` : ""}` : ""}</span>
          {result && !run && !reopened && !failure && (saved ? <span className="cap" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--sage)" strokeWidth="2" aria-hidden="true"><path d="m5 12 5 5 9-10" /></svg>Saved {clock(saved.saved_at)}</span>
            : <button type="button" className="btn primary small" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save decision"}</button>)}
          <button type="button" className="btn secondary small details-btn" onClick={() => setPanelOpen(true)}>Details</button>
        </div>
        {run && <div className="indet" aria-hidden="true"><span /></div>}
      </header>
      <div className="scroll"><div className="col">
        {notice && <p className="amber" role="alert" style={{ marginBottom: 16 }}>{notice}</p>}
        {center}
        <p className="sr-only" aria-live="polite">{result && !run ? "Answer ready." : ""}</p>
      </div></div>
      {!reopened && <Composer snapshot={snapshot} workflow={workflow} onWorkflow={setWorkflow} question={question} onQuestion={setQuestion}
        newCash={newCash} onNewCash={setNewCash} running={!!run} collapsed={collapsed && !!(answer || failure || run)} onExpand={() => setCollapsed(false)}
        onSubmit={() => void analyze({ question, newCash })} />}
    </main>
    <Panel tab={tab} onTab={openTab} onClose={() => setPanelOpen(false)} focus={focus} snapshot={snapshot} setSnapshot={setSnapshot}
      settings={settings} setSettings={setSettings} result={reopened ? null : result} decision={reopened} running={!!run} onError={setNotice} />
    <div className="scrim" onClick={() => { setPanelOpen(false); setRailOpen(false); }} aria-hidden="true" />
  </div>;
}
