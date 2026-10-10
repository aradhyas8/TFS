import { useState, type ReactNode } from "react";
import { actionLabel, BENCHMARK_FUND, NEW_CASH_DESTINATION, type Analysis, type CandidateListing, type DecisionAction, type NewCashInput, type Position, type SavedDecision, type Snapshot, type ThemeInput } from "../../lib/contracts";
import { accountName, answerSentence, caseCurrency, clock, compact, POSITION_LABEL, doneLabel, durationLabel, fullDate, money, newCashLabel, pct, positionName, range, shortDate } from "./format";

export type Tab = "evidence" | "scenarios" | "proposed" | "holdings" | "guardrails";
type Cite = { numbers: Map<string, number>; onCite: (id: string) => void };

// Holdings keep the date they were last confirmed; prices carry their own (newer) date.
function valuationDates(review: Analysis["portfolio"]): string {
  const prices = review.positions.map(row => row.quote_used?.as_of).filter((day): day is string => !!day).sort().at(-1);
  return `Holdings last confirmed ${fullDate(review.holdings_as_of || review.as_of)}${prices ? ` · Prices ${fullDate(prices)}` : ""}`;
}

export function Row({ label, kind = "", children }: { label: string; kind?: string; children: ReactNode }) {
  return <div className={`row ${kind}`}><span className="lbl">{label}</span><div>{children}</div></div>;
}

function Cites({ ids, cite }: { ids: string[]; cite: Cite }) {
  const numbered = ids.filter(id => cite.numbers.has(id));
  return <>{numbered.map(id => <sup key={id}><button type="button" aria-label={`Source ${cite.numbers.get(id)}`} onClick={() => cite.onCite(id)}>{cite.numbers.get(id)}</button></sup>)}</>;
}

function Expandable({ title, count, children }: { title: string; count: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" className="more" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span>{title} <span className="m">· {count}</span></span><span className="arrow" aria-hidden="true">{open ? "−" : "+"}</span>
    </button>
    {open && <div className="expand">{children}</div>}
  </>;
}

const Bullets = ({ items }: { items: string[] }) => <ul className="bullets">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>;

export function Echo({ question, newCash, snapshot, stock, rebalance, theme }: { question: string; newCash: NewCashInput | null; snapshot: Snapshot | null; stock?: string | null; rebalance?: boolean; theme?: ThemeInput | null }) {
  if (rebalance) return <div className="echo"><span className="q">{question}</span>
    <span className="cap n">Rebalance · your whole saved portfolio as of {fullDate(snapshot?.as_of)}. Nothing in it is changed.</span></div>;
  if (stock) return <div className="echo"><span className="q">{question}</span>
    <span className="cap n">Stock analysis · {stock} against your saved portfolio as of {fullDate(snapshot?.as_of)}</span></div>;
  if (theme) return <div className="echo"><span className="q">{question}</span>
    <span className="cap n">Theme discovery · {theme.name || "Theme"} against your saved portfolio as of {fullDate(snapshot?.as_of)}
      {theme.confirmed ? " · confirmed agreement" : " · awaiting agreement"}</span></div>;
  if (!newCash) return <div className="echo"><span className="q">{question}</span>
    <span className="cap n">Portfolio review · your whole portfolio as of {fullDate(snapshot?.as_of)}</span></div>;
  return <div className="echo"><span className="q">{question}</span>
    <span className="cap n">{newCashLabel(newCash, snapshot) || "New cash"}
      {newCash.confirmed ? " · confirmed new money" : " · not confirmed"}{newCash.risk_context ? " · loss tolerance given" : " · no loss tolerance given"}</span></div>;
}

export function Waiting({ title, startedAt, now, past, scope, onStop }: { title: string; startedAt: number; now: number; past: number[]; scope: string[]; onStop: () => void }) {
  const elapsed = now - startedAt;
  const history = past.length ? `Your last ${past.length === 1 ? "run" : `${past.length} runs`} took ${durationLabel(Math.min(...past))}${past.length > 1 ? ` to ${durationLabel(Math.max(...past))}` : ""}.` : "Most runs finish in 1–3 minutes.";
  return <div className="memo">
    <Row label="Working" kind="working">
      <div role="status" aria-live="polite" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span className="serif" style={{ fontSize: 26, lineHeight: "34px" }}>{title}</span>
        <span className="n" style={{ color: "var(--text-2)" }}>Started {clock(new Date(startedAt).toISOString())} · <span style={{ color: "var(--text)" }}>{durationLabel(elapsed)}</span> so far. {history}</span>
        {elapsed > 180_000 && <span className="amber">Taking longer than usual. It will keep going for up to 10 minutes.</span>}
      </div>
    </Row>
    <Row label="This run checks"><Bullets items={scope} /></Row>
    <Row label="Meanwhile">
      <div style={{ display: "flex", flexDirection: "column", gap: 12, color: "var(--text-2)" }}>
        <span>The answer arrives all at once. You can browse holdings or past decisions; this tab&apos;s title changes when it&apos;s ready.</span>
        <span><button type="button" className="link" style={{ color: "var(--text)", textDecoration: "underline", textUnderlineOffset: 4 }} onClick={onStop}>Stop waiting</button>
          <span className="cap"> · the server may still finish the run; nothing is saved.</span></span>
      </div>
    </Row>
  </div>;
}

export function Failure({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="memo"><Row label="Not finished" kind="err">
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span>The analysis didn&apos;t return an answer. Your question and inputs are kept.</span>
      <details><summary className="cap" style={{ cursor: "pointer" }}>Details</summary><p className="cap" style={{ paddingTop: 6 }}>{message}</p></details>
      <span><button type="button" className="link" onClick={onRetry}>Try again</button></span>
    </div>
  </Row></div>;
}

function Impact({ result, snapshot }: { result: Analysis; snapshot: Snapshot | null }) {
  const allocation = result.allocation!;
  const targetId = result.recommendation.amount?.position_id || allocation.judgment?.position_id;
  if (!targetId) return null;
  const target = snapshot?.positions.find(row => row.id === targetId);
  const name = target?.ticker || positionName(target, targetId);
  const now = result.portfolio.positions.find(row => row.supplied.id === targetId)?.weight ?? null;
  const after = allocation.previews.map(preview => preview.positions.find(row => row.supplied.id === targetId)?.weight).filter((w): w is string => !!w).map(Number);
  const cap = target?.kind === "stock" ? result.portfolio.guardrails?.settings.single_company_cap ?? null : null;
  const judged = allocation.judgment && <span className="cap">Judged range {pct(allocation.judgment.min_weight)}–{pct(allocation.judgment.max_weight)}: {allocation.judgment.reason}</span>;
  if (now === null || !after.length) return <Row label="Portfolio impact">
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}><div className="bar unknown" aria-hidden="true" />
      <span className="cap n">{name} weight after this cash is Unknown: no amount was tested.</span>{judged}</div></Row>;
  const lo = Math.min(...after), hi = Math.max(...after), nowN = Number(now), capN = cap === null ? null : Number(cap);
  const scale = Math.max(nowN, hi, capN ?? 0) * 1.1 || 1;
  return <Row label="Portfolio impact">
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="bar" role="img" aria-label={`${name} ${pct(now)} now, ${pct(String(lo))} to ${pct(String(hi))} after${cap ? `, cap ${pct(cap)}` : ""}`}>
        <div className="now" style={{ width: `${(nowN / scale) * 100}%` }} />
        <div className={`chg${capN !== null && hi > capN ? " over" : ""}`} style={{ left: `${(lo / scale) * 100}%`, width: `${Math.max(0.6, ((hi - lo) / scale) * 100)}%` }} />
        {capN !== null && <div className="tick" style={{ left: `${(capN / scale) * 100}%` }} />}
      </div>
      <div className="bar-legend n"><span className="m">{name} {pct(now)} now</span><span>{lo === hi ? pct(String(lo)) : `${pct(String(lo))}–${pct(String(hi))}`} after</span>
        <span className="m">{cap ? `cap ${pct(cap)}` : "no company cap applies"}</span></div>
      {judged}
    </div>
  </Row>;
}

const CASH_CHOICES = (target: string): [DecisionAction, string][] => [["add", `Added to ${target}`], ["no_action", "Kept it as cash"]];
const REVIEW_CHOICES: [DecisionAction, string][] = [["no_action", "Left the portfolio as it is"], ["reduce", "Trimmed a holding"], ["add", "Added to a holding"]];

function ConfirmForm({ choices, onConfirm }: { choices: [DecisionAction, string][]; onConfirm: (action: DecisionAction, notes: string | undefined) => Promise<void> }) {
  const [action, setAction] = useState<DecisionAction>(choices[0][0]);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [later, setLater] = useState(false);
  const [error, setError] = useState("");
  if (later) return <p className="body">Saved. Record what you did from Decisions whenever you&apos;re ready.</p>;
  return <form style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={async event => {
    event.preventDefault(); setBusy(true); setError("");
    const notes = [action === "add" && amount ? `Amount: ${amount}.` : "", note.trim()].filter(Boolean).join(" ");
    try { await onConfirm(action, notes || undefined); } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not record the action."); } finally { setBusy(false); }
  }}>
    <fieldset style={{ border: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }} disabled={busy}>
      <legend className="cap" style={{ paddingBottom: 6 }}>What did you do?</legend>
      {choices.map(([value, label]) => <label key={value} className="check" style={{ alignItems: "center", minHeight: 36 }}>
        <input type="radio" name="did" checked={action === value} onChange={() => setAction(value)} /><span>{label}</span>
        {value === "add" && action === "add" && <input className="in n" aria-label="Amount added (optional)" placeholder="Amount" value={amount} onChange={event => setAmount(event.target.value)} style={{ width: 120, minHeight: 36, marginLeft: 8 }} />}</label>)}
    </fieldset>
    <label className="field"><span className="cap">Note for future you · optional</span>
      <input className="in" maxLength={1000} value={note} onChange={event => setNote(event.target.value)} placeholder="e.g. split into two buys around the next results" /></label>
    <div className="actions"><button type="submit" className="btn primary" disabled={busy}>{busy ? "Recording…" : "Record action"}</button>
      <button type="button" className="link m" onClick={() => setLater(true)}>Later</button></div>
    {error && <p className="amber" role="alert">{error}</p>}
  </form>;
}

function DidRow({ decision }: { decision: SavedDecision }) {
  const done = decision.confirmed_action!;
  return <Row label="You did">
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span style={{ fontSize: 15, lineHeight: "24px" }}>{doneLabel(done.action).replace(/^./, c => c.toUpperCase())} <span className="m">· recorded {shortDate(done.confirmed_at)}</span></span>
      {done.notes && <span className="body" style={{ fontStyle: "italic" }}>&ldquo;{done.notes}&rdquo;</span>}
    </div>
  </Row>;
}

function Reasoning({ assumptions, uncertainty, change, extra }: { assumptions: string[]; uncertainty: string[]; change: string[]; extra?: ReactNode }) {
  return <Row label="Detail"><div>
    {extra}
    <Expandable title="Assumptions and uncertainty" count={`${assumptions.length} and ${uncertainty.length}`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}><span className="lbl">Assumes</span><Bullets items={assumptions} />
        <span className="lbl">Uncertain</span><Bullets items={uncertainty} /></div></Expandable>
    <Expandable title="What would change this" count={`${change.length}`}><Bullets items={change} /></Expandable>
  </div></Row>;
}

type AnswerProps = {
  result: Analysis; sent: { question: string; newCash: NewCashInput }; snapshot: Snapshot | null; cite: Cite;
  saved: SavedDecision | null; onTab: (tab: Tab) => void; onRerun: (riskContext: string) => void;
  onConfirm: (action: DecisionAction, notes: string | undefined) => Promise<void>;
};

export function Answer({ result, sent, snapshot, cite, saved, onTab, onRerun, onConfirm }: AnswerProps) {
  const { recommendation: rec, allocation } = result;
  const [risk, setRisk] = useState(sent.newCash.risk_context || "");
  const targetId = rec.amount?.position_id || allocation?.judgment?.position_id;
  const target = snapshot?.positions.find(row => row.id === targetId);
  const targetName = positionName(target, targetId || "the candidate");
  const missing = allocation?.missing_inputs || [];
  const preview = allocation?.previews.at(-1);
  const rules = preview ? preview.status === "within_limits" ? <span><span className="sage">●</span> Within your rules</span>
    : preview.status === "blocked" ? <span><span className="amber">●</span> Breaks one of your rules</span> : <span>Rules check unknown</span> : null;
  const stock = allocation?.stocks[0];
  const scenarioSummary = stock ? `${positionName(snapshot?.positions.find(row => row.id === stock.position_id), stock.position_id)} · ${stock.cases.map(c => `${c.name} ${money(c.present_value_per_share, stock.reporting_currency)}`).join(" · ")}`
    : result.comparison ? `${result.comparison.horizon_years}-year comparison of ${result.comparison.alternatives.length} options` : "Not available for this answer";
  const notes = [...(allocation?.qualifications || []), ...result.portfolio.qualifications];
  const evidenceIds = rec.evidence_ids || [];
  const sentence = answerSentence(rec.preferred_action, rec.amount, targetName, sent.newCash.amount);
  const amountText = rec.amount ? range(rec.amount.minimum, rec.amount.maximum, rec.amount.currency) : null;

  return <div>
    <Echo question={result.question} newCash={sent.newCash} snapshot={snapshot} />
    <div className="memo" aria-label="Analyst memo" role="region">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{amountText && sentence.includes(amountText) ? <>{sentence.split(amountText)[0]}<span className="amt">{amountText}</span>{sentence.split(amountText)[1]}</> : sentence}</h2>
          <span className="cap n meta"><span>{actionLabel(rec.preferred_action)}{missing.length ? ` · ${missing.length} input${missing.length > 1 ? "s" : ""} would firm this up` : ""}</span>{rules}</span>
        </div>
      </Row>
      {(missing.length > 0 || rec.preferred_action === "wait_for_inputs") && <Row label="Needs you" kind="needs">
        <form style={{ display: "flex", flexDirection: "column", gap: 14 }} onSubmit={event => { event.preventDefault(); onRerun(risk); }}>
          {missing.length > 0 ? <Bullets items={missing} /> : <p className="body">The analyst needs more before it can size this.</p>}
          <label className="field"><span>How large a drop could you hold through on this money, and do you plan any withdrawals?</span>
            <textarea className="in" maxLength={1000} value={risk} onChange={event => setRisk(event.target.value)} placeholder="e.g. could hold through a 30% drop; no withdrawals for 5 years" /></label>
          <div className="actions"><button type="submit" className="btn primary" disabled={!risk.trim()}>Re-run with these</button><span className="cap">Usually 1–3 minutes</span></div>
        </form>
      </Row>}
      <Row label="Why"><p className="body">{rec.reason}<Cites ids={evidenceIds} cite={cite} /></p></Row>
      {allocation && <Impact result={result} snapshot={snapshot} />}
      {rec.alternatives.length > 0 && <Row label="Alternatives"><div className="alts">
        {rec.alternatives.map((alt, index) => <div className="alt" key={index}><span>{actionLabel(alt.action)}</span><span className="m">{alt.reason}</span></div>)}
      </div></Row>}
      <Row label="Downside"><p className="body">{rec.downside}</p></Row>
      <Reasoning assumptions={rec.assumptions} uncertainty={rec.uncertainty} change={rec.what_could_change} extra={<>
        <button type="button" className="more" onClick={() => onTab("scenarios")}><span>Scenarios <span className="m">· {scenarioSummary}</span></span><span className="arrow" aria-hidden="true">→</span></button>
        <button type="button" className="more" onClick={() => onTab("guardrails")}><span>Guardrails <span className="m">· {preview ? preview.status.replaceAll("_", " ") : "checked against today's portfolio"}</span></span><span className="arrow" aria-hidden="true">→</span></button>
      </>} />
      {notes.length > 0 && <Row label="Notes"><Expandable title="Qualifications from the analyst" count={`${notes.length}`}><Bullets items={notes} /></Expandable>
        <Expandable title="Calculation basis" count="how values were computed"><p className="cap">{result.portfolio.calculation_basis}</p></Expandable></Row>}
      {saved && <Row label="Saved">
        {saved.confirmed_action ? <span className="body">Saved and recorded. <span className="m">You: {doneLabel(saved.confirmed_action.action)}.</span></span> : <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="body" style={{ color: "var(--text)" }} data-testid="saved-message">Saved to Decisions with its {saved.evidence_references.length} source{saved.evidence_references.length === 1 ? "" : "s"} and the {shortDate(saved.as_of)} snapshot. When you&apos;ve acted, record what you did.</p>
          <ConfirmForm choices={CASH_CHOICES(targetName)} onConfirm={onConfirm} />
        </div>}
      </Row>}
    </div>
  </div>;
}

type ReviewProps = {
  result: Analysis; snapshot: Snapshot | null; cite: Cite; saved: SavedDecision | null; onTab: (tab: Tab) => void;
  onConfirm: (action: DecisionAction, notes: string | undefined) => Promise<void>;
};

/** A whole-portfolio review. Every value and weight is the backend's; this only orders and formats them. */
export function ReviewAnswer({ result, snapshot, cite, saved, onTab, onConfirm }: ReviewProps) {
  const { recommendation: rec, portfolio: review } = result;
  const guardrails = review.guardrails;
  const over = guardrails ? guardrails.companies.filter(company => company.status === "breached").length + (guardrails.active.status === "breached" ? 1 : 0) : 0;
  const rules = !guardrails ? <span className="m">No rules set</span> : over ? <span><span className="amber">●</span> {over === 1 ? "Breaks one of your rules" : `Breaks ${over} of your rules`}</span>
    : <span><span className="sage">●</span> Within your rules</span>;
  // The backend already groups missing values by cause; per-holding detail stays in Holdings.
  const unknowns = review.qualifications;
  return <div>
    <Echo question={result.question} newCash={null} snapshot={snapshot} />
    <div className="memo" aria-label="Analyst memo" role="region">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{rec.preferred_action === "review_only" && over ? `Your portfolio breaks ${over === 1 ? "one" : over} of your rules.` : answerSentence(rec.preferred_action, rec.amount, "", null)}</h2>
          <span className="cap n meta"><span>{valuationDates(review)}{review.complete ? "" : " · Some values are unknown"}</span>{rules}</span>
        </div>
      </Row>
      <Row label="Why"><p className="body">{rec.reason}<Cites ids={rec.evidence_ids || []} cite={cite} /></p></Row>
      <Row label="Portfolio today"><Weights result={result} snapshot={snapshot} onTab={onTab} /></Row>
      <Row label="Exposure"><Exposure review={review} /></Row>
      <Row label="Risks">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <p className="body">{rec.downside}</p>
          {unknowns.length > 0 && <Expandable title="What's unknown" count={`${unknowns.length}`}><Bullets items={unknowns} /></Expandable>}
        </div>
      </Row>
      {rec.alternatives.length > 0 && <Row label="Alternatives"><div className="alts">
        {rec.alternatives.map((alt, index) => <div className="alt" key={index}><span>{actionLabel(alt.action)}</span><span className="m">{alt.reason}</span></div>)}
      </div></Row>}
      <Reasoning assumptions={rec.assumptions} uncertainty={rec.uncertainty} change={rec.what_could_change} extra={<>
        <button type="button" className="more" onClick={() => onTab("guardrails")}><span>Guardrails <span className="m">· {guardrails ? `${guardrails.companies.length} compan${guardrails.companies.length === 1 ? "y" : "ies"} checked` : "no rules set"}</span></span><span className="arrow" aria-hidden="true">→</span></button>
        <button type="button" className="more" onClick={() => onTab("evidence")}><span>Evidence <span className="m">· prices, rates and fund holdings used</span></span><span className="arrow" aria-hidden="true">→</span></button>
        <button type="button" className="more" onClick={() => onTab("scenarios")}><span>Scenarios <span className="m">· {result.comparison ? `${result.comparison.horizon_years}-year comparison` : "none for a review"}</span></span><span className="arrow" aria-hidden="true">→</span></button>
      </>} />
      <Row label="Notes"><Expandable title="Calculation basis" count="how values were computed"><p className="cap">{review.calculation_basis}</p></Expandable></Row>
      {saved && <Row label="Saved">
        {saved.confirmed_action ? <span className="body">Saved and recorded. <span className="m">You: {doneLabel(saved.confirmed_action.action)}.</span></span> : <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="body" style={{ color: "var(--text)" }} data-testid="saved-message">Saved to Decisions with the {shortDate(saved.as_of)} portfolio. When you&apos;ve acted, record what you did.</p>
          <ConfirmForm choices={REVIEW_CHOICES} onConfirm={onConfirm} />
        </div>}
      </Row>}
    </div>
  </div>;
}

type Review = Analysis["portfolio"];
type Valued = Review["positions"][number];
const holdingLabel = (row: Valued, snapshot: Snapshot | null) => row.supplied.kind === "cash" ? `Cash ${row.supplied.currency} · ${accountName(snapshot, row.supplied.account_id)}`
  : `${row.supplied.ticker || positionName(row.supplied, row.supplied.id)} · ${accountName(snapshot, row.supplied.account_id)}`;
const byWeight = (rows: Valued[]) => [...rows].sort((a, b) => (b.weight === null ? -1 : Number(b.weight)) - (a.weight === null ? -1 : Number(a.weight)));

/** Total, then up to eight position weight bars; a position over its cap is amber and an unknown weight is a dashed bar. */
function Weights({ result, snapshot, onTab }: { result: Analysis; snapshot: Snapshot | null; onTab: (tab: Tab) => void }) {
  const review = result.portfolio;
  const currency = review.reporting_currency;
  const rows = review.positions.filter(row => row.supplied.id !== NEW_CASH_DESTINATION && !row.supplied.id.startsWith("candidate-") && row.supplied.id !== BENCHMARK_FUND);
  const ranked = byWeight(rows);
  const scale = Math.max(...rows.map(row => Number(row.weight ?? 0)), 0.01);
  const capOf = (companyId: string) => review.guardrails?.companies.find(company => company.company_id === companyId);
  return <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
    <span className="n" data-testid="review-total">{review.total_value === null ? <>Total unknown <span className="m">· known {money(review.known_value, currency)}</span></> : money(review.total_value, currency)}
      <span className="m"> · holdings {money(review.holdings_value, currency)} · cash {money(review.cash_value, currency)} · {review.accounts.length} account{review.accounts.length === 1 ? "" : "s"}</span></span>
    <div className="weights" role="list" aria-label="Weights">
      {ranked.slice(0, 8).map(row => {
        const company = row.identity?.company_id || row.supplied.company_id;
        const breached = company ? capOf(company)?.status === "breached" : false;
        return <div className="wrow n" role="listitem" key={row.supplied.id}>
          <span className="wname">{holdingLabel(row, snapshot)}</span>
          {row.weight === null ? <div className="bar unknown" aria-hidden="true" /> : <div className="bar" aria-hidden="true"><div className={`chg${breached ? " over" : ""}`} style={{ left: 0, width: `${(Number(row.weight) / scale) * 100}%` }} /></div>}
          <span className={breached ? "amber" : ""}>{row.weight === null ? "Unknown" : pct(row.weight)}</span>
        </div>;
      })}
    </div>
    {ranked.length > 8 && <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("holdings")}>All {ranked.length} positions in Holdings →</button>}
  </div>;
}

/** Largest direct companies, currency mix and what's known inside funds. Unknown is never shown as zero. */
function Exposure({ review }: { review: Review }) {
  const direct = review.direct_companies.filter(c => !c.position_ids.some(id => id.startsWith("candidate-") || id === BENCHMARK_FUND));
  return <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
    {direct.length ? <span>Largest companies you own directly: {[...direct].sort((a, b) => Number(b.weight ?? -1) - Number(a.weight ?? -1)).slice(0, 3)
      .map(company => `${company.company_name} ${company.weight === null ? "Unknown" : pct(company.weight)}`).join(" · ")}.</span>
      : <span className="m">No directly held companies are identified yet.</span>}
    {!!review.currency_exposure?.length && <span>By currency: {review.currency_exposure.map(row => `${row.currency} ${row.weight === null ? "Unknown" : pct(row.weight)}`).join(" · ")}.</span>}
    <span>Inside your funds: <span className={review.indirect_exposure === "full" || review.indirect_exposure === "none" ? "" : "amber"}>{{ full: "fully looked through", partial: "partly looked through", stale: "looked through with stale holdings", unknown: "unknown", none: "you hold no funds" }[review.indirect_exposure] || review.indirect_exposure}</span>
      {["partial", "stale", "unknown"].includes(review.indirect_exposure) && <span className="m">. Indirect exposure is not assumed to be zero.</span>}</span>
    {review.company_overlap.filter(row => row.indirect_value !== "0").slice(0, 3).map(row => <span className="cap n" key={row.company_id}>{row.company_name}: {row.total_weight === null ? "Unknown" : pct(row.total_weight)} in total, including {row.indirect_weight === null ? "an unknown share" : pct(row.indirect_weight)} through funds</span>)}
  </div>;
}

type Move = "lower" | "exit" | "higher" | "same" | "undecided";
export type PlanRow = { row: Valued; label: string; ticker: string; move: Move; after: string[]; why: string | null; toCap: string | null };
const MOVE: Record<Move, string> = { lower: "Lower", exit: "Sell", higher: "Higher", same: "Unchanged", undecided: "Undecided" };
const CHANGES = ["add", "reduce", "exit"];

/** Which way each position would move, from the backend only: the holding assessments when the answer is a change,
 *  the Python previews when sizing passed, and the deterministic reduction path when a configured cap is breached.
 *  With no change recommended, nothing moves. */
export function rebalancePlan(result: Analysis, snapshot: Snapshot | null): PlanRow[] {
  const rw = result.reunderwriting!;
  const action = result.recommendation.preferred_action;
  const changing = CHANGES.includes(action);
  const companyOf = (row: Valued) => row.identity?.company_id || row.supplied.company_id || null;
  const assessed = new Map<string, (typeof rw.assessments)[number]>();
  for (const a of rw.assessments) assessed.set(rw.research[a.position_id]?.company_id || result.portfolio.positions.find(p => p.supplied.id === a.position_id)?.supplied.company_id || a.position_id, a);
  const breached = new Map((result.portfolio.guardrails?.companies || []).filter(c => c.status === "breached").map(c => [c.company_id, c]));
  const moved = new Set<string>();
  const plan = byWeight(result.portfolio.positions.filter(row => row.supplied.id !== NEW_CASH_DESTINATION && !row.supplied.id.startsWith("candidate-") && row.supplied.id !== BENCHMARK_FUND)).map(row => {
    const company = companyOf(row);
    const a = company ? assessed.get(company) : undefined;
    let move: Move = "same";
    let toCap: string | null = null;
    if (changing && a && CHANGES.includes(a.action)) move = a.action === "add" ? "higher" : a.action === "exit" ? "exit" : "lower";
    else if (action === "review_only" && company && breached.has(company)) {
      move = "lower";
      // The company's reduction is shown once, on its largest lot.
      if (!moved.has(company)) toCap = breached.get(company)!.reduction_to_cash;
    } else if (a?.action === "wait_for_inputs" || (action === "wait_for_inputs" && a)) move = "undecided";
    if (company && move !== "same") moved.add(company);
    const after = rw.previews.map(preview => preview.positions.find(p => p.supplied.id === row.supplied.id)?.weight).filter((w): w is string => !!w);
    return { row, label: holdingLabel(row, snapshot), ticker: row.supplied.ticker || positionName(row.supplied, row.supplied.id), move, after, why: a ? a.current_thesis : null, toCap };
  });
  // Sale proceeds land in, and purchases come from, cash in the same account; its weight moves the other way.
  for (const item of plan) {
    if (item.row.supplied.kind !== "cash" || item.move !== "same") continue;
    const same = plan.filter(other => other.row.supplied.account_id === item.row.supplied.account_id && other.row.supplied.kind !== "cash");
    if (same.some(other => other.move === "lower" || other.move === "exit")) item.move = "higher";
    else if (same.some(other => other.move === "higher")) item.move = "lower";
  }
  return plan;
}

const joinNames = (names: string[]) => names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;

/** Current weight, proposed weight (or direction) and approximate change for every position. */
export function BeforeAfter({ result, plan }: { result: Analysis; plan: PlanRow[] }) {
  const currency = result.portfolio.reporting_currency;
  return <table className="matrix" aria-label="Before and after">
    <thead><tr><th scope="col">Position</th><th scope="col">Now</th><th scope="col">Proposed</th><th scope="col">Change</th></tr></thead>
    <tbody>{plan.map(item => {
      const after = item.after.map(Number);
      const lo = after.length ? Math.min(...after) : null, hi = after.length ? Math.max(...after) : null;
      const now = item.row.weight === null ? null : Number(item.row.weight);
      const delta = lo !== null && hi !== null && now !== null ? [lo - now, hi - now].map(d => `${d > 0 ? "+" : d < 0 ? "−" : ""}${pct(String(Math.abs(d)))}`) : null;
      return <tr key={item.row.supplied.id} className={item.move === "same" ? "m" : ""}>
        <th scope="row">{item.label}</th>
        <td>{pct(item.row.weight)}</td>
        <td>{lo !== null && hi !== null ? lo === hi ? pct(String(lo)) : `${pct(String(lo))}–${pct(String(hi))}` : MOVE[item.move]}</td>
        <td>{delta ? delta[0] === delta[1] ? delta[0] : `${delta[0]} to ${delta[1]}` : item.toCap ? `about ${money(item.toCap, currency)} to cash` : item.move === "same" || item.move === "undecided" ? "—" : "direction only"}</td>
      </tr>;
    })}</tbody>
  </table>;
}

function rebalanceSentence(action: string, plan: PlanRow[], amount: Analysis["recommendation"]["amount"], over: number): string {
  const names = (moves: Move[]) => [...new Set(plan.filter(item => item.row.supplied.kind !== "cash" && moves.includes(item.move)).map(item => item.ticker))];
  if (action === "review_only" && over) return `Your portfolio breaks ${over === 1 ? "one" : over} of your rules: bring ${joinNames(names(["lower"])) || "it"} back within ${over === 1 ? "it" : "them"}.`;
  if (CHANGES.includes(action)) {
    const sized = amount ? plan.find(item => item.row.supplied.id === amount.position_id) : undefined;
    const by = (move: Move, word: string) => amount && sized?.move === move ? ` ${word} ${range(amount.minimum, amount.maximum, amount.currency)}` : "";
    const parts = [
      names(["exit"]).length ? `sell ${joinNames(names(["exit"]))}` : "",
      names(["lower"]).length ? `reduce ${joinNames(names(["lower"]))}${by("lower", "by")}` : "",
      names(["higher"]).length ? `add to ${joinNames(names(["higher"]))}${by("higher", "with")}` : "",
    ].filter(Boolean).join("; ");
    return parts ? `${parts[0].toUpperCase()}${parts.slice(1)}. Keep the rest.` : `${actionLabel(action)}.`;
  }
  if (action === "wait_for_inputs") return "Not enough to decide on a rebalance yet.";
  return "No change. Keep your portfolio as it is.";
}

/** Rebalance in Direction D: whether anything should change, then what, by how much where Python could check it, and why.
 *  Nothing here changes the saved holdings. */
export function RebalanceAnswer({ result, snapshot, cite, saved, onTab, onConfirm }: ReviewProps) {
  const { recommendation: rec, portfolio: review } = result;
  const rw = result.reunderwriting!;
  const plan = rebalancePlan(result, snapshot);
  const guardrails = review.guardrails;
  const breaches = guardrails ? guardrails.companies.filter(c => c.status === "breached") : [];
  const over = breaches.length + (guardrails?.active.status === "breached" ? 1 : 0);
  const changing = CHANGES.includes(rec.preferred_action) || (rec.preferred_action === "review_only" && over > 0);
  const verdict = changing ? "Change recommended" : rec.preferred_action === "wait_for_inputs" ? "Undecided" : "No change recommended";
  const rules = !guardrails ? <span className="m">No rules set</span> : over ? <span><span className="amber">●</span> {over === 1 ? "Breaks one of your rules" : `Breaks ${over} of your rules`}</span>
    : <span><span className="sage">●</span> Within your rules</span>;
  const tickerOf = (id: string) => plan.find(item => item.row.supplied.id === id)?.ticker || review.positions.find(item => item.supplied.id === id)?.supplied.ticker || id;
  const stocks = rw.stocks.filter((stock, index) => rw.stocks.findIndex(other => other.research.company_id === stock.research.company_id) === index);
  const sized = plan.some(item => item.after.length);
  const fund = result.comparison?.alternatives.find(alt => alt.selection.kind === "etf");
  const reduced = plan.filter(item => item.row.supplied.kind !== "cash" && (item.move === "lower" || item.move === "exit"));
  const added = plan.filter(item => item.row.supplied.kind !== "cash" && item.move === "higher");
  const kept = plan.filter(item => item.row.supplied.kind !== "cash" && item.move === "same");
  const undecided = plan.filter(item => item.move === "undecided");
  const moving = [...reduced, ...added].map(item => rw.assessments.find(a => a.current_thesis === item.why)).filter((a, i, all): a is NonNullable<typeof a> => !!a && all.indexOf(a) === i);
  const notes = [...rw.qualifications, ...review.qualifications];
  const group = (title: string, items: PlanRow[], testid: string) => items.length > 0 && <div style={{ display: "flex", flexDirection: "column", gap: 4 }} data-testid={testid}>
    <span className="lbl">{title}</span>
    {items.map(item => <span key={item.row.supplied.id}>{item.label}{item.why && <span className="m"> · {item.why}</span>}</span>)}</div>;

  return <div>
    <Echo question={result.question} newCash={null} snapshot={snapshot} rebalance />
    <div className="memo" aria-label="Rebalance" role="region">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{rebalanceSentence(rec.preferred_action, plan, rec.amount, over)}</h2>
          <span className="cap n meta"><span data-testid="rebalance-verdict">{verdict}</span><span>{actionLabel(rec.preferred_action)}</span>{rules}</span>
          <p className="body">{rec.reason}<Cites ids={rec.evidence_ids || []} cite={cite} /></p>
        </div>
      </Row>
      <Row label="Portfolio today"><div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <Weights result={result} snapshot={snapshot} onTab={onTab} /><Exposure review={review} /></div></Row>
      <Row label="What I would change">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {!changing && <span>{rec.preferred_action === "wait_for_inputs" ? "Nothing yet. The open questions below have to be settled first." : "Nothing. No trade is proposed just because you asked."}</span>}
          {group(rec.preferred_action === "review_only" ? "Reduce to meet your rules" : "Reduce", reduced, "plan-reduce")}
          {group("Add", added, "plan-add")}
          {group("Keep", kept, "plan-keep")}
          {group("Undecided", undecided, "plan-undecided")}
          {reduced.length > 0 && <span className="cap">Sale proceeds stay as cash in the same account unless you choose otherwise{fund ? `; ${tickerOf(fund.selection.position_id || "")} was compared as a diversified home for them` : ""}.</span>}
        </div>
      </Row>
      <Row label="Before → after">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <BeforeAfter result={result} plan={plan} />
          {sized ? <span className="cap">Proposed weights are Python previews of both ends of the range, checked against your rules. They are hypothetical, not orders.</span>
            : changing ? <div data-testid="sizing-withheld" style={{ display: "flex", flexDirection: "column", gap: 4 }}><span>Direction only: no exact size could be justified, so none is invented.</span>
              {rw.missing_inputs.length > 0 && <Bullets items={[...new Set(rw.missing_inputs)]} />}</div>
            : <span className="cap">No trades proposed, so every weight stays where it is.</span>}
          <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("proposed")}>Inspect in Proposed portfolio →</button>
        </div>
      </Row>
      <Row label="Why">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }} data-testid="rebalance-why">
          <div><span className="lbl">Concentration</span><p className="body">{review.direct_companies.length ? [...review.direct_companies].sort((a, b) => Number(b.weight ?? -1) - Number(a.weight ?? -1)).slice(0, 3)
            .map(c => `${c.company_name} ${pct(c.weight)}`).join(" · ") : "No directly held companies"}{breaches.length ? `. Over your cap: ${breaches.map(c => `${c.company_name} ${pct(c.current_weight)} vs ${pct(c.cap)}`).join(" · ")}.` : "."}</p></div>
          <div><span className="lbl">Valuation</span>{stocks.length ? stocks.map(stock => <p className="body" key={stock.position_id}>{tickerOf(stock.position_id)}: {stock.valuation?.price
            ? `price ${money(stock.valuation.price, stock.valuation.currency)} is ${POSITION_LABEL[stock.valuation.position]}` : `base case ${money(stock.cases.find(c => c.name === "base")?.present_value_per_share, caseCurrency(stock, stock.reporting_currency))} a share; no usable price to compare`}.</p>)
            : <p className="body m">No company was valued in this run.</p>}</div>
          <div><span className="lbl">Thesis quality</span>{rw.assessments.length ? rw.assessments.map(a => <p className="body" key={a.position_id}>{tickerOf(a.position_id)} · {actionLabel(a.action).toLowerCase()} · {a.status === "unknown" ? "no prior thesis to compare" : `thesis ${a.status}`}: {a.current_thesis} {a.change_reason}<Cites ids={a.evidence_ids} cite={cite} /></p>)
            : <p className="body m">No held company had research to re-check; their outcomes stay unknown.</p>}</div>
          <div><span className="lbl">Portfolio fit and diversification</span><p className="body">{result.comparison ? `Compared ${result.comparison.alternatives.length} options over ${result.comparison.horizon_years} years: ${result.comparison.alternatives.map(alt => alt.selection.kind === "no_action" ? "changing nothing" : alt.selection.kind === "cash" ? "cash" : tickerOf(alt.selection.position_id || "")).join(", ")}. ` : ""}
            Fund look-through is {review.indirect_exposure === "none" ? "not needed (no funds)" : review.indirect_exposure}.</p></div>
          <div><span className="lbl">Your guardrails</span><p className="body">{!guardrails ? "None set. This view doesn't depend on them; no limit was assumed or checked."
            : `${guardrails.settings.single_company_cap ? `Company cap ${pct(guardrails.settings.single_company_cap)}` : "No company cap"} · ${guardrails.settings.active_budget ? `active picks ${pct(guardrails.settings.active_budget)} (now ${pct(guardrails.active.weight)}, ${guardrails.active.status.replaceAll("_", " ")})` : "no active budget"}. ${over ? "Any change has to bring the breach back within your limits." : "Every proposed change has to stay within them."}`}</p></div>
        </div>
      </Row>
      <Row label="Trade-offs and risks"><div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <p className="body">{rec.downside}</p>
        {moving.map(a => <p className="body" key={a.position_id}>{tickerOf(a.position_id)}: {a.downside}</p>)}
        <Bullets items={rec.uncertainty} />
      </div></Row>
      {rec.alternatives.length > 0 && <Row label="Alternatives"><div className="alts">
        {rec.alternatives.map((alt, index) => <div className="alt" key={index}><span>{actionLabel(alt.action)}</span><span className="m">{alt.reason}</span></div>)}
      </div></Row>}
      <Row label="Would change this"><Bullets items={rec.what_could_change} /></Row>
      <Row label="Notes">
        <Expandable title="Assumptions" count={`${rec.assumptions.length}`}><Bullets items={rec.assumptions} /></Expandable>
        {notes.length > 0 && <Expandable title="Qualifications from the analyst" count={`${notes.length}`}><Bullets items={notes} /></Expandable>}
        <Expandable title="Calculation basis" count="how values were computed"><p className="cap">{review.calculation_basis} {result.comparison?.calculation_basis}</p></Expandable>
      </Row>
      {saved && <Row label="Saved">
        {saved.confirmed_action ? <span className="body">Saved and recorded. <span className="m">You: {doneLabel(saved.confirmed_action.action)}.</span></span> : <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="body" style={{ color: "var(--text)" }} data-testid="saved-message">Saved to Decisions with its {saved.evidence_references.length} source{saved.evidence_references.length === 1 ? "" : "s"} and the {shortDate(saved.as_of)} portfolio. When you&apos;ve acted, record what you did.</p>
          <ConfirmForm choices={REVIEW_CHOICES} onConfirm={onConfirm} />
        </div>}
      </Row>}
    </div>
  </div>;
}

/** One short question when a company reference can't be settled from the saved holdings. Nothing is guessed. */
export function Clarify({ question, message, options, review, onStock, onReview, candidateListings, onSelectListing }: {
  question: string;
  message: string;
  options: Position[];
  review: boolean;
  onStock: (position: Position) => void;
  onReview: () => void;
  candidateListings?: CandidateListing[];
  onSelectListing?: (listing: CandidateListing) => void;
}) {
  return <div>
    <div className="echo"><span className="q">{question}</span><span className="cap">Not sent yet · needs one answer</span></div>
    <div className="memo" aria-label="Clarification" role="region">
      <Row label="Which one?" kind="needs">
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="body" style={{ color: "var(--text)" }}>{message}</p>
          {options.length > 0 && <div className="choices">{options.map(row => <button type="button" className="btn secondary small" key={row.id} onClick={() => onStock(row)}>
            {row.ticker || row.id}<span className="m"> · {positionName(row, row.id)}</span></button>)}</div>}
          {candidateListings && candidateListings.length > 0 && <div className="choices">{candidateListings.map(listing => <button type="button" className="btn secondary small" key={`${listing.ticker}:${listing.listing}`} onClick={() => onSelectListing?.(listing)}>
            {listing.ticker} / {listing.listing}<span className="m"> · {listing.currency} · {listing.company_name || listing.ticker}</span></button>)}</div>}
          {review && <span><button type="button" className="link" onClick={onReview}>Review my whole portfolio instead</button></span>}
        </div>
      </Row>
    </div>
  </div>;
}

const METRIC = { revenue: "Revenue", operating_income: "Operating income", net_income: "Net income", free_cash_flow: "Free cash flow",
  operating_cash_flow: "Operating cash flow", capex: "Capital expenditure", cash: "Cash", total_debt: "Total debt", shares: "Diluted shares", book_value: "Book value", ffo: "FFO" } as Record<string, string>;
const metricLabel = (fact: { metric: string; period_start: string | null; period_end: string }) => METRIC[fact.metric] || fact.metric;

const STOCK_CHOICES: [DecisionAction, string][] = [["hold", "Kept it as it is"], ["add", "Added to it"], ["reduce", "Trimmed it"], ["exit", "Sold it"]];

function stockSentence(action: string, amount: Analysis["recommendation"]["amount"], ticker: string): string {
  if (action === "add" && amount) return `Add ${range(amount.minimum, amount.maximum, amount.currency)} to ${ticker}.`;
  if (action === "wait_for_inputs" || action === "clarify_inputs") return `Not enough to decide on ${ticker} yet.`;
  if (action === "review_only") return `Here is where ${ticker} stands.`;
  if (action === "no_action") return `Leave ${ticker} as it is for now.`;
  const verb = ({ hold: "Hold", add: "Add to", reduce: "Trim", exit: "Sell" } as Record<string, string>)[action];
  return verb ? `${verb} ${ticker}.` : `${actionLabel(action)}: ${ticker}.`;
}

/** Direction D stock analysis: conclusion first, then position, thesis, evidence, quality, valuation, cases and portfolio fit.
 *  Every number is the backend's (Python cases, dated quotes, portfolio review); this only orders and formats them. */
export function StockAnswer({ result, snapshot, cite, saved, onTab, onConfirm }: ReviewProps) {
  const { recommendation: rec, portfolio: review, comparison } = result;
  const stock = result.stock!;
  const target = snapshot?.positions.find(row => row.id === stock.position_id) ?? review.positions.find(row => row.supplied.id === stock.position_id)?.supplied;
  const ticker = target?.ticker || positionName(target, stock.position_id);
  const companyId = target?.company_id || stock.research.company_id;
  const lots = review.positions.filter(row => row.supplied.kind === "stock" && (row.supplied.id === stock.position_id || !!target?.company_id && row.supplied.company_id === target.company_id));
  const owned = lots.filter(row => Number(row.supplied.shares || 0) > 0);
  const company = review.direct_companies.find(row => row.company_id === companyId || row.position_ids.includes(stock.position_id));
  const quote = lots.find(row => row.quote_used)?.quote_used ?? null;
  // Per-share case values are in the reported metric's currency; portfolio values in the reporting currency.
  const local = caseCurrency(stock, target?.currency || quote?.currency || stock.reporting_currency);
  const currency = review.reporting_currency;
  const cap = review.guardrails?.companies.find(row => row.company_id === companyId);
  // Broken rules make the backend's checks, not the model, set the answer to review only.
  const over = review.guardrails ? review.guardrails.companies.filter(row => row.status === "breached").length + (review.guardrails.active.status === "breached" ? 1 : 0) : 0;
  const cases = ["downside", "base", "upside"].map(name => stock.cases.find(c => c.name === name)).filter((c): c is NonNullable<typeof c> => !!c);
  const base = cases.find(c => c.name === "base");
  const documents = stock.research.documents.filter(doc => doc.available).sort((a, b) => b.published_on.localeCompare(a.published_on));
  const missing = stock.research.documents.filter(doc => !doc.available);
  const facts = stock.research.facts;
  const altName = (alt: NonNullable<typeof comparison>["alternatives"][number]["selection"]) => alt.kind === "no_action" ? "Keep things as they are" : alt.kind === "cash" ? "Hold cash" :
    alt.kind === "short_bill" ? "Short-term bills" : alt.position_id === stock.position_id ? ticker : snapshot?.positions.find(row => row.id === alt.position_id)?.ticker || review.positions.find(row => row.supplied.id === alt.position_id)?.supplied.ticker || alt.position_id || alt.id;
  const accounts = [...new Set(owned.map(row => snapshot?.accounts.find(account => account.id === row.supplied.account_id)?.name || row.supplied.account_id))];
  const shares = owned.reduce((sum, row) => sum + Number(row.supplied.shares || 0), 0);
  const notes = [...stock.qualifications, ...stock.research.issues];
  const valuation = stock.valuation;
  // The figures a reader wants first: the latest fiscal year's revenue, profit, cash flow and balance sheet.
  const latestYear = facts.filter(fact => fact.metric === "revenue" && fact.period_start).map(fact => fact.period_end).sort().at(-1);
  const order = ["revenue", "operating_income", "net_income", "free_cash_flow", "cash", "total_debt", "shares"];
  const headline = facts.filter(fact => fact.period_end === latestYear && order.includes(fact.metric) && (fact.period_start || ["cash", "total_debt"].includes(fact.metric)))
    .sort((a, b) => order.indexOf(a.metric) - order.indexOf(b.metric)).slice(0, 7);
  const unmodeled = [...new Set(comparison?.alternatives.flatMap(alt => alt.cases.flatMap(c => c.terminal_value ? [] : c.unmodeled || [])) || [])];

  return <div>
    <Echo question={result.question} newCash={null} snapshot={snapshot} stock={ticker} />
    <div className="memo" aria-label="Stock analysis" role="region">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{rec.preferred_action === "review_only" && cap?.status === "breached" ? `${ticker} is over your ${pct(cap.cap)} company cap.`
            : rec.preferred_action === "review_only" && over ? `Your portfolio breaks ${over === 1 ? "one" : over} of your rules, so there is no call on ${ticker} yet.`
            : stockSentence(rec.preferred_action, rec.amount, ticker)}</h2>
          <span className="cap n meta"><span>{actionLabel(rec.preferred_action)} · {positionName(target, ticker)}</span>
            {cap && <span>{cap.status === "breached" ? <><span className="amber">●</span> Over your company cap</> : cap.status === "within_limit" ? <><span className="sage">●</span> Within your company cap</> : "Company cap not checked"}</span>}</span>
        </div>
      </Row>
      <Row label="Your position">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="stock-position">
          {owned.length ? <span className="n">{shares.toLocaleString("en-CA", { maximumFractionDigits: 4 })} shares in {accounts.join(" and ")} · {money(company?.value ?? owned[0].value, currency)} · <strong>{pct(company?.weight ?? owned[0].weight)}</strong> of your portfolio</span>
            : <span>You don&apos;t own {ticker} today.</span>}
          {quote ? <span className="cap n">Price {money(quote.value, quote.currency)} · {quote.source} · {shortDate(quote.as_of)} · {quote.status}</span> : <span className="cap amber">No usable price; value and weight are unknown.</span>}
          {cap?.cap && <span className="cap n">Your cap {pct(cap.cap)} per company · {cap.explanation}</span>}
        </div>
      </Row>
      <Row label="Thesis"><div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <p className="body">{rec.reason}<Cites ids={rec.evidence_ids || []} cite={cite} /></p>
        {stock.judgments.mid_cycle_context && <p className="cap">{stock.judgments.mid_cycle_context}</p>}
      </div></Row>
      <Row label="What changed">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {documents.length ? documents.slice(0, 3).map(doc => <div key={doc.id} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span>{doc.title}<Cites ids={[doc.id]} cite={cite} /></span>
            <span className="cap n">{["sedar", "sedar_plus"].includes(doc.authority) ? "SEDAR+" : doc.authority.toUpperCase()} · published {shortDate(doc.published_on)} · as of {shortDate(doc.as_of)}</span></div>)
            : <span className="amber">No primary filings or issuer material were available, so nothing here rests on current evidence.</span>}
          {documents.length > 3 && <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("evidence")}>All {documents.length} sources in Evidence →</button>}
          {missing.length > 0 && <span className="cap">Looked for, not found: {missing.map(doc => doc.title).join(" · ")}</span>}
        </div>
      </Row>
      <Row label="Business quality">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <span className="cap">Sector {stock.research.sector}{stock.research.cyclical === null ? "" : stock.research.cyclical ? " · cyclical" : " · not cyclical"} · valued on {stock.judgments.method.replaceAll("_", " ")}</span>
          {facts.length ? <ul className="plist n" style={{ listStyle: "none" }} aria-label="Reported facts">{headline.map(fact => <li key={fact.id} className="pv">
            <span>{metricLabel(fact)}</span><span>{compact(fact.value, fact.currency)} <span className="m">· {fact.period_start ? `${shortDate(fact.period_start)}–` : "at "}{shortDate(fact.period_end)}</span></span></li>)}</ul>
            : <span className="m">No reported facts were available.</span>}
          {facts.length > headline.length && <Expandable title="All reported facts" count={`${facts.length}`}><ul className="plist n" style={{ listStyle: "none" }}>{facts.map(fact => <li key={fact.id} className="pv">
            <span>{metricLabel(fact)}</span><span>{compact(fact.value, fact.currency)} <span className="m">· {fact.period_start ? `${shortDate(fact.period_start)}–` : "at "}{shortDate(fact.period_end)}</span></span></li>)}</ul></Expandable>}
        </div>
      </Row>
      <Row label="Valuation">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="stock-valuation">
          {valuation?.price && <span className="n">Today&apos;s price {money(valuation.price, valuation.currency)} is <strong>{POSITION_LABEL[valuation.position]}</strong>
            {valuation.price_to_base && <> · {Number(valuation.price_to_base) >= 0 ? `${pct(valuation.price_to_base)} above` : `${pct(String(-Number(valuation.price_to_base)))} below`} the base value</>}</span>}
          <span className="n">Base case {money(base?.present_value_per_share, local)} per share, discounted to today{quote && !valuation?.price ? <> · price {money(quote.value, quote.currency)}</> : ""}</span>
          <span className="cap n">Base exit price {money(base?.terminal_price, local)} · exit multiple {base?.judgment.exit_multiple ?? "Unknown"} · discount rate {pct(base?.judgment.discount_rate)} · multiple needed {base?.required_exit_multiple ?? "Unknown"}</span>
          {valuation?.reported_margin && <span className="cap n">First-year modeled margin {pct(valuation.modeled_first_year_margin)} · reported {pct(valuation.reported_margin)}</span>}
          {(valuation?.cash || valuation?.total_debt) && <span className="cap n">Cash {compact(valuation.cash, local)} · debt {compact(valuation.total_debt, local)} at {shortDate(valuation.balance_date)}</span>}
          {valuation?.notes.map(note => <span className="cap" key={note}>{note}</span>)}
        </div>
      </Row>
      <Row label="Cases">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="cases n" role="list" aria-label="Downside, base and upside">{cases.map(c => <div role="listitem" className="pv" key={c.name}>
            <span className={c.name === "base" ? "" : "m"}>{c.name[0].toUpperCase() + c.name.slice(1)}{valuation?.price && c.present_value_per_share && <span className="m"> · {Number(valuation.price) > Number(c.present_value_per_share) ? "price above" : "price below"}</span>}</span>
            <span>{money(c.present_value_per_share, local)} <span className="m">· exit {money(c.terminal_price, local)}</span></span></div>)}</div>
          <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("scenarios")}>Drivers and sensitivity in Scenarios →</button>
        </div>
      </Row>
      <Row label="Portfolio impact">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {comparison ? (
            Number(comparison.starting_value || 0) === 0 ? (
              <span>No cash to compare on. Use /new-cash with an amount to compare against the fund and cash.</span>
            ) : (
              <>
                <span className="cap n">Value after {comparison.horizon_years} years in the base case, from {money(comparison.starting_value, comparison.reporting_currency || currency)}</span>
                <ul className="plist n" style={{ listStyle: "none" }} aria-label="Options compared">{comparison.alternatives.map(alt => {
                  const c = alt.cases.find(item => item.name === "base");
                  return <li key={alt.selection.id} className="pv"><span>{altName(alt.selection)}</span>
                    <span>{money(c?.terminal_value ?? c?.comparison_value, comparison.reporting_currency || currency)}{!c?.terminal_value && c?.comparison_value && c.unmodeled?.length ? <span className="m"> · before {c.unmodeled.join(" and ")}</span> : ""}</span></li>;
                })}</ul>
                {unmodeled.length > 0 && <span className="cap">Not modeled, not assumed zero: {unmodeled.join(" and ")}. They come off these values when known.</span>}
              </>
            )
          ) : <span className="m">No comparison was produced for this answer.</span>}
          {rec.amount && <span className="n">Sized at {range(rec.amount.minimum, rec.amount.maximum, rec.amount.currency)}.</span>}
          <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("guardrails")}>Your rules in Guardrails →</button>
        </div>
      </Row>
      {!!stock.sizing_withheld?.length && <Row label="Sizing"><div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="sizing-withheld">
        <span>No exact position size: the view above stands on its own.</span><Bullets items={stock.sizing_withheld} /></div></Row>}
      <Row label="Risks"><div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <p className="body">{rec.downside}</p><Bullets items={rec.uncertainty} />
      </div></Row>
      {rec.alternatives.length > 0 && <Row label="Alternatives"><div className="alts">
        {rec.alternatives.map((alt, index) => <div className="alt" key={index}><span>{actionLabel(alt.action)}</span><span className="m">{alt.reason}</span></div>)}
      </div></Row>}
      <Row label="Would change this"><Bullets items={rec.what_could_change} /></Row>
      <Row label="Notes">
        <Expandable title="Assumptions" count={`${rec.assumptions.length}`}><Bullets items={rec.assumptions} /></Expandable>
        {notes.length > 0 && <Expandable title="Qualifications from the analyst" count={`${notes.length}`}><Bullets items={notes} /></Expandable>}
        <Expandable title="Calculation basis" count="how values were computed"><p className="cap">{stock.calculation_basis}</p></Expandable>
      </Row>
      {saved && <Row label="Saved">
        {saved.confirmed_action ? <span className="body">Saved and recorded. <span className="m">You: {doneLabel(saved.confirmed_action.action)}.</span></span> : <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="body" style={{ color: "var(--text)" }} data-testid="saved-message">Saved to Decisions with its {saved.evidence_references.length} source{saved.evidence_references.length === 1 ? "" : "s"} and the {shortDate(saved.as_of)} snapshot. When you&apos;ve acted, record what you did.</p>
          <ConfirmForm choices={STOCK_CHOICES} onConfirm={onConfirm} />
        </div>}
      </Row>}
    </div>
  </div>;
}

const THEME_CHOICES: [DecisionAction, string][] = [
  ["no_action", "Took no action"],
  ["add", "Added to candidate"],
  ["hold", "Kept current holdings"],
];

export function ThemeAnswer({ result, snapshot, cite, saved, onTab, onAgree, onConfirm }: {
  result: Analysis; snapshot: Snapshot | null; cite: Cite; saved: SavedDecision | null; onTab: (tab: Tab) => void;
  onAgree?: () => void; onConfirm: (action: DecisionAction, notes: string | undefined) => Promise<void>;
}) {
  const { recommendation: rec, portfolio: review, theme } = result;
  if (!theme) return null;

  const tickerOf = (id: string) => {
    const s = snapshot?.positions.find(p => p.id === id);
    if (s?.ticker) return s.ticker;
    const r = review.positions.find(p => p.supplied.id === id);
    if (r?.supplied.ticker) return r.supplied.ticker;
    if (id.startsWith("candidate-")) {
      const parts = id.replace("candidate-", "").split("-");
      return parts[0];
    }
    return id;
  };

  const shortlistTickers = theme.context.shortlist.map(tickerOf).join(", ");

  if (theme.status === "awaiting_agreement") {
    return <div>
      <Echo question={result.question} newCash={null} snapshot={snapshot} theme={theme.context} />
      <div className="memo" aria-label="Analyst memo" role="region">
        <Row label="Recommendation" kind="rec">
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <h2 className="display">Agree the theme mechanism and shortlist before researching.</h2>
            <span className="cap n meta">
              <span>Awaiting agreement · {theme.context.shortlist.length} candidate{theme.context.shortlist.length === 1 ? "" : "s"}</span>
              <span className="amber">● Needs your agreement</span>
            </span>
          </div>
        </Row>
        <Row label="Needs you" kind="needs">
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <p className="body">Review the economic mechanism and candidate shortlist before live research begins.</p>
            <div className="pgroup">
              <span className="lbl">Theme</span>
              <span className="body serif" style={{ fontSize: 18 }}>{theme.context.name || "Untitled theme"}</span>
            </div>
            <div className="pgroup">
              <span className="lbl">Economic mechanism</span>
              <p className="body">{theme.context.mechanism}</p>
            </div>
            <div className="pgroup">
              <span className="lbl">Agreed shortlist</span>
              <p className="body">{shortlistTickers || "None"}</p>
              <span className="cap n">Maximum candidates: {theme.context.max_candidates} · Research calls bound: {theme.context.max_tool_calls}</span>
            </div>
            <div className="actions">
              <button type="button" className="btn primary" onClick={onAgree}>Agree and research</button>
              <span className="cap">Runs live research against primary SEC EDGAR filings</span>
            </div>
          </div>
        </Row>
      </div>
    </div>;
  }

  const amountText = rec.amount ? range(rec.amount.minimum, rec.amount.maximum, rec.amount.currency) : null;
  const sentence = rec.preferred_action === "no_action"
    ? "Take no action on the theme candidates for now."
    : answerSentence(rec.preferred_action, rec.amount, shortlistTickers, null);

  const guardrails = review.guardrails;

  const shortlistedEtfs = theme.context.shortlist.map(id => {
    const p = snapshot?.positions.find(pos => pos.id === id) || review.positions.find(pos => pos.supplied.id === id)?.supplied;
    return p?.kind === "etf" ? p : null;
  }).filter((p): p is Position => !!p);

  return <div>
    <Echo question={result.question} newCash={null} snapshot={snapshot} theme={theme.context} />
    <div className="memo" aria-label="Analyst memo" role="region">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{amountText && sentence.includes(amountText) ? <>{sentence.split(amountText)[0]}<span className="amt">{amountText}</span>{sentence.split(amountText)[1]}</> : sentence}</h2>
          <span className="cap n meta">
            <span>{valuationDates(review)} · Completed theme research · {theme.tool_calls_used} of {theme.context.max_tool_calls} calls used</span>
          </span>
        </div>
      </Row>
      <Row label="Economic mechanism">
        <p className="body">{theme.context.mechanism}</p>
      </Row>
      <Row label="Candidate verdicts">
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {theme.tests.map(test => {
            const ticker = tickerOf(test.position_id);
            const isEtf = shortlistedEtfs.some(e => e.id === test.position_id);
            const etfMissingFacts = isEtf && test.conclusion === "unknown";
            return <div key={test.position_id} className="pgroup" data-testid={`test-${test.position_id}`}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="serif" style={{ fontSize: 18 }}>{ticker}</span>
                <span className={`verdict ${test.conclusion}`}>{test.conclusion}</span>
              </div>
              <p className="body">{test.explanation}<Cites ids={test.evidence_ids} cite={cite} /></p>
              {etfMissingFacts && <span className="cap amber">Fund facts and dated sponsor holdings are missing; verdict and overlap remain unknown.</span>}
            </div>;
          })}
          {shortlistedEtfs.filter(e => !theme.tests.some(t => t.position_id === e.id)).map(etf => (
            <div key={etf.id} className="pgroup" data-testid={`test-${etf.id}`}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="serif" style={{ fontSize: 18 }}>{etf.ticker}</span>
                <span className="verdict unknown">unknown</span>
              </div>
              <p className="body">No dated fund facts or sponsor holdings were available for this ETF.</p>
              <span className="cap amber">Fund facts and dated sponsor holdings are missing; verdict and overlap remain unknown.</span>
            </div>
          ))}
        </div>
      </Row>
      <Row label="Exposure">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <Exposure review={review} />
          {shortlistedEtfs.filter(etf => {
            const test = theme.tests.find(t => t.position_id === etf.id);
            return !test || test.conclusion === "unknown";
          }).map(etf => <span className="cap n" key={etf.id}>
            {etf.ticker}: look-through and overlap are unknown (no dated fund facts).
          </span>)}
        </div>
      </Row>
      <Row label="Guardrails">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <p className="body">{!guardrails ? "None set. Sizing was checked without user-defined portfolio limits."
            : `${guardrails.settings.single_company_cap ? `Company cap ${pct(guardrails.settings.single_company_cap)}` : "No company cap"} · ${guardrails.settings.active_budget ? `active picks budget ${pct(guardrails.settings.active_budget)} (now ${pct(guardrails.active.weight)}, ${guardrails.active.status.replaceAll("_", " ")})` : "no active budget"}. Checked against limits.`}</p>
          {theme.sizing && <p className="cap">Judged exposure range: {pct(theme.sizing.min_weight)} to {pct(theme.sizing.max_weight)}. {theme.sizing.reason}</p>}
          <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("guardrails")}>Inspect in Guardrails →</button>
        </div>
      </Row>
      {rec.alternatives.length > 0 && <Row label="Alternatives"><div className="alts">
        {rec.alternatives.map((alt, index) => <div className="alt" key={index}><span>{actionLabel(alt.action)}</span><span className="m">{alt.reason}</span></div>)}
      </div></Row>}
      <Row label="Risks"><p className="body">{rec.downside}</p></Row>
      <Reasoning assumptions={rec.assumptions} uncertainty={rec.uncertainty} change={rec.what_could_change} extra={<>
        <button type="button" className="more" onClick={() => onTab("scenarios")}><span>Scenarios <span className="m">· {theme.stocks.length} company case{theme.stocks.length === 1 ? "" : "s"}</span></span><span className="arrow" aria-hidden="true">→</span></button>
        <button type="button" className="more" onClick={() => onTab("evidence")}><span>Evidence <span className="m">· {cite.numbers.size} source{cite.numbers.size === 1 ? "" : "s"}</span></span><span className="arrow" aria-hidden="true">→</span></button>
        <button type="button" className="more" onClick={() => onTab("guardrails")}><span>Guardrails <span className="m">· checked against limits</span></span><span className="arrow" aria-hidden="true">→</span></button>
      </>} />
      <Row label="Notes">
        <span className="cap">Research calls used: {theme.tool_calls_used} of {theme.context.max_tool_calls} maximum.</span>
        {theme.missing_inputs.length > 0 && <Expandable title="Missing inputs" count={`${theme.missing_inputs.length}`}><Bullets items={theme.missing_inputs} /></Expandable>}
        {theme.qualifications.length > 0 && <Expandable title="Qualifications" count={`${theme.qualifications.length}`}><Bullets items={theme.qualifications} /></Expandable>}
        <Expandable title="Calculation basis" count="how values were computed"><p className="cap">{review.calculation_basis}</p></Expandable>
      </Row>
      {saved && <Row label="Saved">
        {saved.confirmed_action ? <span className="body">Saved and recorded. <span className="m">You: {doneLabel(saved.confirmed_action.action)}.</span></span> : <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="body" style={{ color: "var(--text)" }} data-testid="saved-message">Saved to Decisions with its sources and the {shortDate(saved.as_of)} snapshot. When you&apos;ve acted, record what you did.</p>
          <ConfirmForm choices={THEME_CHOICES} onConfirm={onConfirm} />
        </div>}
      </Row>}
    </div>
  </div>;
}

type HistoricalProps = { decision: SavedDecision; snapshot: Snapshot | null; cite: Cite; onRerun: () => void; onConfirm: (action: DecisionAction, notes: string | undefined) => Promise<void> };

export function Historical({ decision, snapshot, cite, onRerun, onConfirm }: HistoricalProps) {
  const { conclusion, reasoning } = decision;
  const targetId = conclusion.amount?.position_id;
  const target = targetId ? positionName(snapshot?.positions.find(row => row.id === targetId), targetId) : "the candidate";
  const sentence = answerSentence(conclusion.preferred_action, conclusion.amount, target, null);
  const amountText = conclusion.amount ? range(conclusion.amount.minimum, conclusion.amount.maximum, conclusion.amount.currency) : null;
  return <div aria-label="Reopened decision" role="region">
    <div className="historical-band" data-testid="historical-band" style={{ background: "var(--raised)", border: "1px solid var(--rule)", borderRadius: 8, padding: "10px 16px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap", marginBottom: 24 }}>
      <span style={{ color: "var(--text-2)" }}>Historical. Prices, filings and your rules are as of the {shortDate(decision.as_of)} snapshot and are not updated.</span>
      <button type="button" className="btn secondary small" onClick={onRerun}>Re-run with today&apos;s portfolio</button>
    </div>
    <div className="echo"><span className="q">{decision.question}</span><span className="cap">Saved {shortDate(decision.saved_at)} · snapshot {shortDate(decision.as_of)}</span></div>
    <div className="memo">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{amountText && sentence.includes(amountText) ? <>{sentence.split(amountText)[0]}<span className="amt">{amountText}</span>{sentence.split(amountText)[1]}</> : sentence}</h2>
          <span className="cap">{actionLabel(conclusion.preferred_action)} · at the time</span>
        </div>
      </Row>
      <Row label="Why"><p className="body">{reasoning.reason}<Cites ids={decision.evidence_references.map(ref => ref.id)} cite={cite} /></p></Row>
      {reasoning.alternatives.length > 0 && <Row label="Alternatives"><div className="alts">
        {reasoning.alternatives.map((alt, index) => <div className="alt" key={index}><span>{actionLabel(alt.action)}</span><span className="m">{alt.reason}</span></div>)}
      </div></Row>}
      <Row label="Downside"><p className="body">{reasoning.downside}</p></Row>
      <Reasoning assumptions={reasoning.assumptions} uncertainty={reasoning.uncertainty} change={reasoning.what_could_change} />
      {decision.confirmed_action ? <DidRow decision={decision} /> : <Row label="You did"><ConfirmForm choices={conclusion.preferred_action === "review_only" ? REVIEW_CHOICES : CASH_CHOICES(target)} onConfirm={onConfirm} /></Row>}
    </div>
  </div>;
}
