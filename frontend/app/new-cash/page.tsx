"use client";

import { useEffect, useRef, useState } from "react";
import { get, post, put, type Analysis, type DecisionAction, type NewCashInput, type PortfolioSettings, type SavedDecision, type SavedPortfolio, type Snapshot, type UnresolvedHolding } from "../../lib/contracts";
import Rail from "../../components/desk/Rail";
import Composer from "../../components/desk/Composer";
import Panel, { evidenceFor, ImportCsv } from "../../components/desk/Panel";
import { Answer, Echo, Failure, Historical, Waiting, type Tab } from "../../components/desk/Memo";
import { clock, fullDate, newCashLabel, pct, shortDate } from "../../components/desk/format";

type Sent = { question: string; newCash: NewCashInput };
const EMPTY_CASH: NewCashInput = { amount: null, cash_position_id: null, account_id: null, currency: null, confirmed: false, risk_context: null };
const DURATIONS_KEY = "desk.newCashDurations";
const TITLE = "New cash · Analyst";

/** Only rules the user actually set; an all-unset rule set is sent and saved as none. */
function storedSettings(settings: PortfolioSettings): PortfolioSettings | null {
  return Object.values(settings).some(value => value !== undefined && value !== null) ? settings : null;
}

function pastDurations(): number[] {
  try { return JSON.parse(localStorage.getItem(DURATIONS_KEY) || "[]").filter((n: unknown) => typeof n === "number").slice(-3); } catch { return []; }
}

export default function NewCashPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [averageCosts, setAverageCosts] = useState<Record<string, string>>({});
  const [unresolved, setUnresolved] = useState<UnresolvedHolding[]>([]);
  const [restoring, setRestoring] = useState(true);
  const [settings, setSettings] = useState<PortfolioSettings>({});
  // The holdings and rules last read from or written to the saved portfolio; anything else is a user edit to save.
  const persisted = useRef("");
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

  useEffect(() => {
    get<SavedPortfolio | null>("/api/portfolio").then(saved => { if (saved) adopt(saved); })
      .catch(() => setNotice("Your saved portfolio couldn't be loaded.")).finally(() => setRestoring(false));
    get<SavedDecision[]>("/api/decisions").then(setDecisions).catch(() => setNotice("Saved decisions couldn't be loaded.")); setPast(pastDurations());
  }, []);
  useEffect(() => {
    const current = JSON.stringify({ snapshot, settings });
    if (!snapshot || current === persisted.current) return;
    // Rules are typed a character at a time; save once typing pauses.
    const timer = setTimeout(() => {
      persisted.current = current;
      put<SavedPortfolio>("/api/portfolio", { snapshot, average_costs: averageCosts, settings: storedSettings(settings), unresolved })
        .catch(error => setNotice(error instanceof Error ? `Your change wasn't saved: ${error.message}` : "Your change wasn't saved."));
    }, 400);
    return () => clearTimeout(timer);
  }, [snapshot, settings, averageCosts, unresolved]);
  useEffect(() => { if (!run) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [run]);
  useEffect(() => { const reset = () => { if (!document.hidden) document.title = TITLE; }; document.addEventListener("visibilitychange", reset); return () => document.removeEventListener("visibilitychange", reset); }, []);
  // A changed portfolio invalidates the destination and the "this is new money" confirmation, as in the classic view.
  useEffect(() => { setNewCash(current => ({ ...current, confirmed: false, currency: current.currency || snapshot?.reporting_currency || null,
    account_id: snapshot?.accounts.some(account => account.id === current.account_id) ? current.account_id : snapshot?.accounts.length === 1 ? snapshot.accounts[0].id : null })); }, [snapshot]);

  function adopt(saved: SavedPortfolio) {
    const restored = saved.settings || {};
    persisted.current = JSON.stringify({ snapshot: saved.snapshot, settings: restored });
    setSnapshot(saved.snapshot); setAverageCosts(saved.average_costs); setUnresolved(saved.unresolved); setSettings(restored);
  }

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
        settings: storedSettings(settings) ?? undefined });
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

  const sentCash = (run ?? answer ?? failure)?.newCash;
  const sentLabel = sentCash && newCashLabel(sentCash, snapshot);
  const ctxLabel = reopened ? `Decision · ${shortDate(reopened.saved_at)}` : sentLabel ? `New cash · ${sentLabel}` : workflow ? "New cash" : "New analysis";
  const holdingsCount = snapshot?.positions.filter(row => row.kind !== "cash").length ?? 0;
  const hasFund = snapshot?.positions.some(row => row.kind === "etf" && row.etf_role === "diversified");
  const scope = snapshot ? [
    `${holdingsCount} holding${holdingsCount === 1 ? "" : "s"}${snapshot.positions.length > holdingsCount ? ` and ${snapshot.positions.length - holdingsCount} cash balance${snapshot.positions.length - holdingsCount === 1 ? "" : "s"}` : ""} in ${snapshot.accounts.length} account${snapshot.accounts.length === 1 ? "" : "s"}, your portfolio as of ${fullDate(snapshot.as_of)}, plus the new cash`,
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
  else if (restoring) center = <p className="cap" role="status">Loading your saved portfolio…</p>;
  else if (!snapshot) center = <div className="hero">
    <h1>Start with what you own.</h1>
    <p className="body" style={{ maxWidth: 520 }}>Import your holdings once. They&apos;re saved on this computer and loaded every time. Prices, values and weights come from the analysis, not from your cost.</p>
    <ImportCsv snapshot={null} onImported={adopt} onError={setNotice} label="Choose a holdings CSV" />
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
    <Rail snapshot={snapshot} unresolved={unresolved} settings={settings} result={result} decisions={decisions} currentId={reopened?.id ?? saved?.id ?? null} running={!!run}
      onNew={startOver} onHoldings={() => { openTab("holdings"); setRailOpen(false); }} onOpen={decision => { setReopened(decision); setTab("evidence"); setFocus(null); setRailOpen(false); }} />
    <main className="center">
      <header className="ctx">
        <div className="ctx-left">
          <button type="button" className="icon-btn menu-btn" aria-label="Open portfolio and decisions" onClick={() => setRailOpen(true)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg></button>
          <span>{ctxLabel}</span>
        </div>
        <div className="ctx-right">
          <span className="cap hide-sm">{reopened ? "Read-only" : snapshot ? `Portfolio as of ${fullDate(snapshot.as_of)}${result && !run ? ` · answered ${clock(result.portfolio.reviewed_at)}` : ""}` : ""}</span>
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
      {!reopened && <Composer snapshot={snapshot} unresolved={unresolved.length} workflow={workflow} onWorkflow={setWorkflow} question={question} onQuestion={setQuestion}
        newCash={newCash} onNewCash={setNewCash} running={!!run} collapsed={collapsed && !!(answer || failure || run)} onExpand={() => setCollapsed(false)}
        onSubmit={() => void analyze({ question, newCash })} />}
    </main>
    <Panel tab={tab} onTab={openTab} onClose={() => setPanelOpen(false)} focus={focus} snapshot={snapshot} setSnapshot={setSnapshot} averageCosts={averageCosts} unresolved={unresolved} onImported={adopt}
      settings={settings} setSettings={setSettings} result={reopened ? null : result} decision={reopened} running={!!run} onError={setNotice} />
    <div className="scrim" onClick={() => { setPanelOpen(false); setRailOpen(false); }} aria-hidden="true" />
  </div>;
}
