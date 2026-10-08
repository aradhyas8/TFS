import { useState, type ReactNode } from "react";
import { actionLabel, NEW_CASH_DESTINATION, type Analysis, type DecisionAction, type NewCashInput, type SavedDecision, type Snapshot } from "../../lib/contracts";
import { answerSentence, clock, doneLabel, durationLabel, fullDate, money, newCashLabel, pct, positionName, range, shortDate } from "./format";

export type Tab = "evidence" | "scenarios" | "holdings" | "guardrails";
type Cite = { numbers: Map<string, number>; onCite: (id: string) => void };

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

export function Echo({ question, newCash, snapshot }: { question: string; newCash: NewCashInput | null; snapshot: Snapshot | null }) {
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
  const currency = review.reporting_currency;
  const rows = review.positions.filter(row => row.supplied.id !== NEW_CASH_DESTINATION);
  const ranked = [...rows].sort((a, b) => (b.weight === null ? -1 : Number(b.weight)) - (a.weight === null ? -1 : Number(a.weight)));
  const scale = Math.max(...rows.map(row => Number(row.weight ?? 0)), 0.01);
  const guardrails = review.guardrails;
  const over = guardrails ? guardrails.companies.filter(company => company.status === "breached").length + (guardrails.active.status === "breached" ? 1 : 0) : 0;
  const rules = !guardrails ? <span className="m">No rules set</span> : over ? <span><span className="amber">●</span> {over === 1 ? "Breaks one of your rules" : `Breaks ${over} of your rules`}</span>
    : <span><span className="sage">●</span> Within your rules</span>;
  const capOf = (companyId: string) => guardrails?.companies.find(company => company.company_id === companyId);
  // The backend already groups missing values by cause; per-holding detail stays in Holdings.
  const unknowns = review.qualifications;
  const label = (row: (typeof rows)[number]) => row.supplied.kind === "cash" ? `Cash ${row.supplied.currency} · ${snapshot?.accounts.find(a => a.id === row.supplied.account_id)?.name ?? ""}`
    : `${row.supplied.ticker || positionName(row.supplied, row.supplied.id)} · ${snapshot?.accounts.find(a => a.id === row.supplied.account_id)?.name ?? ""}`;
  return <div>
    <Echo question={result.question} newCash={null} snapshot={snapshot} />
    <div className="memo" aria-label="Analyst memo" role="region">
      <Row label="Recommendation" kind="rec">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <h2 className="display">{rec.preferred_action === "review_only" && over ? `Your portfolio breaks ${over === 1 ? "one" : over} of your rules.` : answerSentence(rec.preferred_action, rec.amount, "", null)}</h2>
          <span className="cap n meta"><span>{review.complete ? `Valued ${fullDate(review.as_of)}` : "Some values are unknown"}</span>{rules}</span>
        </div>
      </Row>
      <Row label="Why"><p className="body">{rec.reason}<Cites ids={rec.evidence_ids || []} cite={cite} /></p></Row>
      <Row label="Portfolio today">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <span className="n" data-testid="review-total">{review.total_value === null ? <>Total unknown <span className="m">· known {money(review.known_value, currency)}</span></> : money(review.total_value, currency)}
            <span className="m"> · holdings {money(review.holdings_value, currency)} · cash {money(review.cash_value, currency)} · {review.accounts.length} account{review.accounts.length === 1 ? "" : "s"}</span></span>
          <div className="weights" role="list" aria-label="Weights">
            {ranked.slice(0, 8).map(row => {
              const company = row.identity?.company_id || row.supplied.company_id;
              const breached = company ? capOf(company)?.status === "breached" : false;
              return <div className="wrow n" role="listitem" key={row.supplied.id}>
                <span className="wname">{label(row)}</span>
                {row.weight === null ? <div className="bar unknown" aria-hidden="true" /> : <div className="bar" aria-hidden="true"><div className={`chg${breached ? " over" : ""}`} style={{ left: 0, width: `${(Number(row.weight) / scale) * 100}%` }} /></div>}
                <span className={breached ? "amber" : ""}>{row.weight === null ? "Unknown" : pct(row.weight)}</span>
              </div>;
            })}
          </div>
          {ranked.length > 8 && <button type="button" className="link cap" style={{ alignSelf: "flex-start" }} onClick={() => onTab("holdings")}>All {ranked.length} positions in Holdings →</button>}
        </div>
      </Row>
      <Row label="Exposure">
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {review.direct_companies.length ? <span>Largest companies you own directly: {[...review.direct_companies].sort((a, b) => Number(b.weight ?? -1) - Number(a.weight ?? -1)).slice(0, 3)
            .map(company => `${company.company_name} ${company.weight === null ? "Unknown" : pct(company.weight)}`).join(" · ")}.</span>
            : <span className="m">No directly held companies are identified yet.</span>}
          {!!review.currency_exposure?.length && <span>By currency: {review.currency_exposure.map(row => `${row.currency} ${row.weight === null ? "Unknown" : pct(row.weight)}`).join(" · ")}.</span>}
          <span>Inside your funds: <span className={review.indirect_exposure === "full" || review.indirect_exposure === "none" ? "" : "amber"}>{{ full: "fully looked through", partial: "partly looked through", stale: "looked through with stale holdings", unknown: "unknown", none: "you hold no funds" }[review.indirect_exposure] || review.indirect_exposure}</span>
            {["partial", "stale", "unknown"].includes(review.indirect_exposure) && <span className="m">. Indirect exposure is not assumed to be zero.</span>}</span>
          {review.company_overlap.filter(row => row.indirect_value !== "0").slice(0, 3).map(row => <span className="cap n" key={row.company_id}>{row.company_name}: {row.total_weight === null ? "Unknown" : pct(row.total_weight)} in total, including {row.indirect_weight === null ? "an unknown share" : pct(row.indirect_weight)} through funds</span>)}
        </div>
      </Row>
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
