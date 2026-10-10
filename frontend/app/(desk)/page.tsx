"use client";

import { useEffect, useRef, useState } from "react";
import { del, get, portfolioReviewComparison, post, put, type Analysis, type DecisionAction, type NewCashInput, type PortfolioSettings, type Position, type PriceRefresh, type SavedDecision, type SavedPortfolio, type Snapshot, type UnresolvedHolding } from "../../lib/contracts";
import Rail from "../../components/desk/Rail";
import Composer from "../../components/desk/Composer";
import Panel, { evidenceFor, ImportCsv } from "../../components/desk/Panel";
import { Answer, Clarify, Echo, Failure, Historical, RebalanceAnswer, ReviewAnswer, StockAnswer, Waiting, type Tab } from "../../components/desk/Memo";
import { route, type Route } from "../../components/desk/route";
import { clock, fullDate, newCashLabel, pct, shortDate } from "../../components/desk/format";

/** A question and its workflow: new cash with its input, Stock Analysis of one holding (position ID), a rebalance, else a portfolio review.
 *  Each one is a fresh analysis of the saved portfolio; nothing from an earlier answer is carried over. */
type Sent = { question: string; newCash: NewCashInput | null; stock?: string | null; rebalance?: boolean };
const EMPTY_CASH: NewCashInput = { amount: null, cash_position_id: null, account_id: null, currency: null, confirmed: false, risk_context: null };
const durationsKey = (sent: Sent | null) => sent?.newCash ? "desk.newCashDurations" : sent?.stock ? "desk.stockDurations" : sent?.rebalance ? "desk.rebalanceDurations" : "desk.reviewDurations";
const TITLE = "Analyst";
// The last answer, so a reload doesn't lose it. Per-browser convenience; Save puts it in Decisions.
const ANSWER_KEY = "desk.lastAnswer";

/** Only rules the user actually set; an all-unset rule set is sent and saved as none. */
function storedSettings(settings: PortfolioSettings): PortfolioSettings | null {
  return Object.values(settings).some(value => value !== undefined && value !== null) ? settings : null;
}

function pastDurations(sent: Sent | null): number[] {
  try { return JSON.parse(localStorage.getItem(durationsKey(sent)) || "[]").filter((n: unknown) => typeof n === "number").slice(-3); } catch { return []; }
}

export default function DeskPage() {
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
  const [clarify, setClarify] = useState<Extract<Route, { kind: "clarify" }> | null>(null);
  const [saved, setSaved] = useState<SavedDecision | null>(null);
  const [saving, setSaving] = useState(false);
  const [decisions, setDecisions] = useState<SavedDecision[]>([]);
  const [reopened, setReopened] = useState<SavedDecision | null>(null);
  const [tab, setTab] = useState<Tab>("holdings");
  const [focus, setFocus] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [prices, setPrices] = useState<PriceRefresh | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const runId = useRef(0);

  useEffect(() => {
    try { const last = localStorage.getItem(ANSWER_KEY); if (last) { setAnswer(JSON.parse(last)); setCollapsed(true); setTab("evidence"); } } catch { /* nothing to restore */ }
    get<SavedPortfolio | null>("/api/portfolio").then(saved => { if (saved) adopt(saved); })
      .catch(() => setNotice("Your saved portfolio couldn't be loaded.")).finally(() => setRestoring(false));
    get<SavedDecision[]>("/api/decisions").then(setDecisions).catch(() => setNotice("Saved decisions couldn't be loaded."));
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
    // Once a day per holding; a same-day call costs no market-data requests.
    if (saved.snapshot.positions.some(row => row.kind !== "cash")) void refreshPrices(false);
  }

  async function refreshPrices(force: boolean) {
    setRefreshing(true);
    try { setPrices(await post<PriceRefresh>("/api/market/refresh", { force })); }
    catch (error) { setNotice(error instanceof Error ? `Prices couldn't be refreshed: ${error.message}` : "Prices couldn't be refreshed."); }
    finally { setRefreshing(false); }
  }

  const result = answer?.analysis ?? null;
  const evidence = evidenceFor(reopened ? null : result, reopened);
  const cite = { numbers: evidence.numbers, onCite: (id: string) => { setTab("evidence"); setFocus(id); setPanelOpen(true); } };
  const openTab = (next: Tab) => { setTab(next); setFocus(null); setPanelOpen(true); };

  async function analyze(sent: Sent) {
    if (!snapshot) return;
    const id = ++runId.current;
    const startedAt = Date.now();
    setRun({ ...sent, id, startedAt }); setNow(startedAt); setPast(pastDurations(sent)); setFailure(null); setClarify(null); setSaved(null); setReopened(null); setCollapsed(true); setNotice("");
    try {
      // Saved holdings are the current portfolio: priced by the provider today, unless the user dated their own marks or FX.
      const dated = snapshot.positions.some(row => row.mark) || snapshot.fx.length > 0;
      const portfolio = dated ? snapshot : { ...snapshot, as_of: new Date().toLocaleDateString("en-CA") };
      // A rebalance is the existing whole-portfolio re-underwriting: no prior theses or risk context unless the user gives them, so no sizing is invented.
      const analysis = await post<Analysis>("/api/analyze", { question: sent.question, portfolio, new_cash: sent.newCash ?? undefined,
        stock: sent.stock ? { position_id: sent.stock } : undefined, settings: storedSettings(settings) ?? undefined,
        ...(sent.rebalance ? { portfolio_review: { prior_theses: [], risk_context: null }, comparison: portfolioReviewComparison(portfolio) } : {}) });
      if (runId.current !== id) return;
      if (analysis.status !== "completed") throw new Error("The analysis was not completed.");
      setAnswer({ ...sent, analysis }); setTab(sent.rebalance ? "proposed" : "evidence"); setFocus(null);
      try { localStorage.setItem(ANSWER_KEY, JSON.stringify({ ...sent, analysis })); } catch { /* too large or blocked: kept in memory only */ }
      const durations = [...pastDurations(sent), Date.now() - startedAt].slice(-3);
      try { localStorage.setItem(durationsKey(sent), JSON.stringify(durations)); } catch { /* per-viewer convenience only */ }
      setPast(durations);
      if (document.hidden) document.title = `Answer ready · ${TITLE}`;
    } catch (error) {
      if (runId.current === id) setFailure({ ...sent, message: error instanceof Error ? error.message : "The analysis could not be completed." });
    } finally { if (runId.current === id) setRun(null); }
  }

  async function deleteDecision(decision: SavedDecision) {
    if (!window.confirm(`Delete "${decision.question}"? This can't be undone.`)) return;
    try {
      await del(`/api/decisions/${encodeURIComponent(decision.id)}`);
      setDecisions(list => list.filter(d => d.id !== decision.id));
      if (reopened?.id === decision.id) setReopened(null);
      if (saved?.id === decision.id) setSaved(null);
    } catch (error) { setNotice(error instanceof Error ? error.message : "The decision couldn't be deleted."); }
  }

  async function clearDecisions() {
    if (!window.confirm(`Delete all ${decisions.length} saved decisions? This can't be undone.`)) return;
    try { await del("/api/decisions"); setDecisions([]); setReopened(null); setSaved(null); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Decisions couldn't be cleared."); }
  }

  /** A typed question becomes Portfolio Review, Stock Analysis of a holding, or one short clarifying question. */
  function ask(text: string) {
    const next = route(text, snapshot);
    if (next.kind === "clarify") { setClarify(next); setReopened(null); setCollapsed(false); return; }
    void analyze({ question: next.question, newCash: null, stock: next.kind === "stock" ? next.position.id : null, rebalance: next.kind === "rebalance" });
  }
  const analyzeStock = (position: Position, question: string) => void analyze({ question, newCash: null, stock: position.id });
  // Clicking a holding offers its Stock Analysis in the composer; sending it is one more click.
  function offerStock(position: Position) {
    setWorkflow(null); setQuestion(`/stock ${position.ticker}`); setRailOpen(false); setReopened(null);
    setCollapsed(false); setTimeout(() => document.getElementById("ask")?.focus(), 0);
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
    runId.current++; setRun(null); setAnswer(null); setFailure(null); setClarify(null); setSaved(null); setReopened(null);
    try { localStorage.removeItem(ANSWER_KEY); } catch { /* nothing stored */ }
    setWorkflow(null); setQuestion(""); setCollapsed(false); setTab("holdings"); setRailOpen(false);
  }

  const sent = run ?? failure ?? answer;
  const tickerOf = (id: string | null | undefined) => id ? snapshot?.positions.find(row => row.id === id)?.ticker || answer?.analysis.portfolio.positions.find(row => row.supplied.id === id)?.supplied.ticker || id : null;
  const sentStock = tickerOf(sent?.stock);
  const sentLabel = sent?.newCash && newCashLabel(sent.newCash, snapshot);
  const ctxLabel = reopened ? `Decision · ${shortDate(reopened.saved_at)}` : sentLabel ? `New cash · ${sentLabel}` : sent ? sent.newCash ? "New cash" : sentStock ? `Stock analysis · ${sentStock}` : sent.rebalance ? "Rebalance" : "Portfolio review"
    : workflow ? "New cash" : "New analysis";
  const holdingsCount = snapshot?.positions.filter(row => row.kind !== "cash").length ?? 0;
  const hasFund = snapshot?.positions.some(row => row.kind === "etf" && row.etf_role === "diversified");
  const portfolioLine = snapshot ? `${holdingsCount} holding${holdingsCount === 1 ? "" : "s"}${snapshot.positions.length > holdingsCount ? ` and ${snapshot.positions.length - holdingsCount} cash balance${snapshot.positions.length - holdingsCount === 1 ? "" : "s"}` : ""} in ${snapshot.accounts.length} account${snapshot.accounts.length === 1 ? "" : "s"}, your portfolio as of ${fullDate(snapshot.as_of)}` : "";
  const rulesLine = settings.single_company_cap || settings.active_budget ? `Your rules: ${settings.single_company_cap ? `${pct(settings.single_company_cap)} per company` : "no company cap"}, ${settings.active_budget ? `${pct(settings.active_budget)} in active picks` : "no active budget"}` : "No rules set, so no limits are checked";
  const scope = !snapshot ? [] : run?.stock ? [
    `${sentStock}'s latest filings and issuer material, with their dates`,
    "Downside, base and upside cases calculated in Python from the reported facts",
    `Its value and weight in your portfolio as of ${fullDate(snapshot.as_of)}, against a fund, cash and doing nothing`, rulesLine,
  ] : run?.rebalance ? [
    `${portfolioLine}, valued with dated prices and exchange rates`,
    "Each company you own re-checked against its filings on hand, in one run rather than one analysis per holding",
    `Reducing, keeping or adding, against ${hasFund ? "a diversified fund, " : ""}cash and changing nothing`, rulesLine,
  ] : run?.newCash ? [
    `${portfolioLine}, plus the new cash`, rulesLine,
    "A bounded screen of what you could add to, then filings for candidates that could change the answer",
    `Against ${hasFund ? "a diversified fund, " : ""}keeping cash and doing nothing`,
  ] : [
    `${portfolioLine}, valued with dated prices and exchange rates`,
    "Direct company exposure across accounts, and what your funds hold where that's known", rulesLine,
  ];
  const review = (question: string) => void analyze({ question, newCash: null, stock: null });

  let center;
  if (reopened) center = <Historical decision={reopened} snapshot={snapshot} cite={cite} onConfirm={(action, notes) => confirm(reopened, action, notes)}
    onRerun={() => { setReopened(null); setWorkflow(reopened.conclusion.preferred_action === "review_only" ? null : "new-cash"); setQuestion(reopened.question); setCollapsed(false); setTab("holdings"); }} />;
  else if (run) center = <><Echo question={run.question} newCash={run.newCash} snapshot={snapshot} stock={sentStock} rebalance={run.rebalance} /><Waiting title={run.newCash ? "Analyzing your new cash." : run.stock ? `Analyzing ${sentStock}.` : run.rebalance ? "Checking whether anything should change." : "Reviewing your portfolio."} startedAt={run.startedAt} now={now} past={past} scope={scope} onStop={stopWaiting} /></>;
  else if (clarify) center = <Clarify {...clarify} onStock={position => analyzeStock(position, clarify.question)} onReview={() => review(clarify.question.startsWith("/") ? "Review my portfolio" : clarify.question)} />;
  else if (failure) center = <><Echo question={failure.question} newCash={failure.newCash} snapshot={snapshot} stock={sentStock} rebalance={failure.rebalance} /><Failure message={failure.message} onRetry={() => analyze(failure)} /></>;
  else if (answer?.analysis.reunderwriting) center = <RebalanceAnswer key={answer.analysis.portfolio.reviewed_at} result={answer.analysis} snapshot={snapshot} cite={cite} saved={saved} onTab={openTab}
    onConfirm={(action, notes) => saved ? confirm(saved, action, notes) : Promise.resolve()} />;
  else if (answer?.analysis.stock && !answer.analysis.allocation) center = <StockAnswer key={answer.analysis.portfolio.reviewed_at} result={answer.analysis} snapshot={snapshot} cite={cite} saved={saved} onTab={openTab}
    onConfirm={(action, notes) => saved ? confirm(saved, action, notes) : Promise.resolve()} />;
  else if (answer && !answer.analysis.allocation) center = <ReviewAnswer key={answer.analysis.portfolio.reviewed_at} result={answer.analysis} snapshot={snapshot} cite={cite} saved={saved} onTab={openTab}
    onConfirm={(action, notes) => saved ? confirm(saved, action, notes) : Promise.resolve()} />;
  else if (answer) {
    // A question that is really about new cash comes back as an allocation; show it with its own inputs.
    const cash = answer.newCash ?? answer.analysis.allocation!.context;
    center = <Answer key={answer.analysis.portfolio.reviewed_at} result={answer.analysis} sent={{ question: answer.question, newCash: cash }} snapshot={snapshot} cite={cite} saved={saved} onTab={openTab}
      onConfirm={(action, notes) => saved ? confirm(saved, action, notes) : Promise.resolve()}
      onRerun={risk => { const next = { ...cash, risk_context: risk }; setNewCash(next); void analyze({ question: answer.question, newCash: next }); }} />;
  }
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
        <button type="button" className="starter" disabled={unresolved.length > 0} onClick={() => { setQuestion("Review my portfolio"); review("Review my portfolio"); }}><span>Review my portfolio</span><span className="m">Exposure, concentration, fund overlap and your rules</span></button>
        <button type="button" className="starter" onClick={() => { setWorkflow("new-cash"); setCollapsed(false); document.getElementById("ask")?.focus(); }}><span>/new-cash</span><span className="m">Decide where new money should go</span></button>
        <button type="button" className="starter" disabled={unresolved.length > 0} onClick={() => void analyze({ question: "Should I rebalance my portfolio?", newCash: null, rebalance: true })}><span>/rebalance</span><span className="m">Should anything change? Reduce, keep or add, within your rules</span></button>
        <button type="button" className="starter" disabled={unresolved.length > 0} onClick={() => { setWorkflow(null); setQuestion("/stock "); document.getElementById("ask")?.focus(); }}><span>/stock</span><span className="m">Analyze one company you own. Or just ask: &ldquo;What do you think about {snapshot.positions.find(row => row.kind === "stock")?.ticker || "AVGO"}?&rdquo;</span></button>
      </div>}
  </div>;

  return <div className={`desk${panelOpen ? " panel-open" : ""}${railOpen ? " rail-open" : ""}`}>
    <a className="skip" href="#details">Skip to details</a>
    <Rail snapshot={snapshot} unresolved={unresolved} settings={settings} result={result} decisions={decisions} currentId={reopened?.id ?? saved?.id ?? null} running={run ? run.newCash ? "New cash" : run.stock ? `Stock analysis · ${sentStock}` : run.rebalance ? "Rebalance" : "Portfolio review" : null}
      onNew={startOver} onHoldings={() => { openTab("holdings"); setRailOpen(false); }} onOpen={decision => { setReopened(decision); setClarify(null); setTab("evidence"); setFocus(null); setRailOpen(false); }} onStock={offerStock}
      onDelete={decision => void deleteDecision(decision)} onClearAll={() => void clearDecisions()} />
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
        {!run && !reopened && unresolved.length > 0 && snapshot && <p className="amber" style={{ marginBottom: 16 }}>
          <button type="button" className="link amber" onClick={() => openTab("holdings")}>Identify {unresolved.length} holding{unresolved.length === 1 ? "" : "s"}</button> before asking. They aren&apos;t in the analysis until then.</p>}
        {center}
        <p className="sr-only" aria-live="polite">{result && !run ? "Answer ready." : ""}</p>
      </div></div>
      {!reopened && <Composer snapshot={snapshot} unresolved={unresolved.length} workflow={workflow} onWorkflow={setWorkflow} question={question} onQuestion={setQuestion}
        newCash={newCash} onNewCash={setNewCash} running={!!run} collapsed={collapsed && !!(answer || failure || run)} onExpand={() => setCollapsed(false)}
        onSubmit={() => { if (workflow === "new-cash") void analyze({ question, newCash, stock: null }); else { ask(question); setQuestion(""); } }} />}
    </main>
    <Panel tab={tab} onTab={openTab} onClose={() => setPanelOpen(false)} focus={focus} snapshot={snapshot} setSnapshot={setSnapshot} averageCosts={averageCosts} unresolved={unresolved} onImported={adopt}
      settings={settings} setSettings={setSettings} result={reopened ? null : result} decision={reopened} running={!!run} onError={setNotice}
      prices={prices} refreshing={refreshing} onRefreshPrices={() => void refreshPrices(true)} />
    <div className="scrim" onClick={() => { setPanelOpen(false); setRailOpen(false); }} aria-hidden="true" />
  </div>;
}
