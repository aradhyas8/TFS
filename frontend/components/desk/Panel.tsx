import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { NEW_CASH_DESTINATION, post, type Analysis, type GuardrailReview, type PortfolioSettings, type PriceRefresh, type SavedDecision, type SavedPortfolio, type Snapshot, type UnresolvedHolding } from "../../lib/contracts";
import type { Tab } from "./Memo";
import { caseCurrency, compact, fullDate, money, pct, positionName, shortDate } from "./format";

export type Evidence = { id: string; title: string; source: string; date: string | null; url: string | null; excerpt: string | null; facts: string[]; available: boolean };

/** Sources in citation order: documents the answer cites first, then the rest it researched; unavailable ones separately. */
export function evidenceFor(result: Analysis | null, decision: SavedDecision | null): { items: Evidence[]; missing: Evidence[]; numbers: Map<string, number> } {
  let all: Evidence[] = [];
  if (decision) all = decision.evidence_references.map(ref => ({ id: ref.id, title: ref.title, source: ref.source, date: ref.as_of, url: ref.url, excerpt: ref.excerpt, facts: [], available: true }));
  else if (result) {
    const stocks = [result.stock, ...(result.allocation?.stocks || [])].filter((s): s is NonNullable<typeof s> => !!s);
    all = stocks.flatMap(stock => stock.research.documents.map(doc => ({
      id: doc.id, title: doc.title, source: doc.authority, date: doc.published_on, url: doc.url, excerpt: doc.excerpt, available: doc.available,
      facts: stock.research.facts.filter(fact => fact.document_ids.includes(doc.id)).map(fact => `${fact.metric}: ${fact.value ?? "Unknown"}${fact.unit && fact.unit !== "ratio" ? ` ${fact.unit}` : ""}${fact.period_end ? ` (period ending ${fact.period_end})` : ""}`),
    })));
    const cited = result.recommendation.evidence_ids || [];
    all.sort((a, b) => (cited.includes(a.id) ? cited.indexOf(a.id) : 1e6) - (cited.includes(b.id) ? cited.indexOf(b.id) : 1e6));
  }
  const items = all.filter(item => item.available), missing = all.filter(item => !item.available);
  return { items, missing, numbers: new Map(items.map((item, index) => [item.id, index + 1])) };
}

type Props = {
  tab: Tab; onTab: (tab: Tab) => void; onClose: () => void; focus: string | null;
  snapshot: Snapshot | null; setSnapshot: Dispatch<SetStateAction<Snapshot | null>>; averageCosts: Record<string, string>; unresolved: UnresolvedHolding[];
  onImported: (saved: SavedPortfolio) => void; settings: PortfolioSettings; setSettings: Dispatch<SetStateAction<PortfolioSettings>>;
  result: Analysis | null; decision: SavedDecision | null; running: boolean; onError: (message: string) => void;
  prices: PriceRefresh | null; refreshing: boolean; onRefreshPrices: () => void;
};

const TABS: { id: Tab; label: string }[] = [{ id: "evidence", label: "Evidence" }, { id: "scenarios", label: "Scenarios" }, { id: "holdings", label: "Holdings" }, { id: "guardrails", label: "Guardrails" }];

export default function Panel(props: Props) {
  const { tab, onTab, onClose, result, decision, running } = props;
  const evidence = evidenceFor(result, decision);
  const waiting = running && !result;
  return <aside className="panel" aria-label="Details" id="details">
    <div className="tabs" role="tablist" aria-label="Detail views">
      {TABS.map(item => <button key={item.id} type="button" role="tab" id={`tab-${item.id}`} aria-controls="tabpanel" className="tab" aria-selected={tab === item.id}
        onClick={() => onTab(item.id)}>{item.label}{item.id === "evidence" && evidence.items.length > 0 && <span className="m n">{evidence.items.length}</span>}</button>)}
      <button type="button" className="icon-btn panel-close" aria-label="Close details" onClick={onClose}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button>
    </div>
    <div className="pbody" role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab}`}>
      {tab === "evidence" && (waiting ? <Pending what="Evidence" /> : <EvidenceTab {...evidence} focus={props.focus} result={result} decision={decision} snapshot={props.snapshot} />)}
      {tab === "scenarios" && (decision ? <NotSaved what="Scenarios" /> : waiting || !result ? <Pending what="Scenarios" /> : <ScenariosTab result={result} snapshot={props.snapshot} />)}
      {tab === "holdings" && <HoldingsTab {...props} />}
      {tab === "guardrails" && (decision ? <NotSaved what="Guardrail checks" /> : waiting || !result ? <RulesSummary settings={props.settings} /> : <GuardrailsTab result={result} snapshot={props.snapshot} />)}
    </div>
  </aside>;
}

const Pending = ({ what }: { what: string }) => <p className="cap">{what} open when the answer arrives.</p>;
const NotSaved = ({ what }: { what: string }) => <p className="cap">{what} weren&apos;t saved with this decision. Re-run with today&apos;s portfolio to see them.</p>;

function EvidenceTab({ items, missing, numbers, focus, result, decision, snapshot }: ReturnType<typeof evidenceFor> & { focus: string | null; result: Analysis | null; decision: SavedDecision | null; snapshot: Snapshot | null }) {
  const focused = useRef<HTMLDivElement>(null);
  useEffect(() => { focused.current?.scrollIntoView({ block: "nearest" }); }, [focus]);
  const scan = !decision ? result?.allocation?.scan : null;
  const targetId = result?.recommendation.amount?.position_id;
  const target = snapshot?.positions.find(row => row.id === targetId);
  return <>
    {decision && <p className="cap">As saved on {shortDate(decision.saved_at)}. Sources may have been updated since.</p>}
    {items.length === 0 && <p className="cap">{decision ? "No sources were saved with this decision." : result?.allocation ? "No filings were researched for this answer. The analyst used your snapshot and the screen below."
      : "No filings were researched for this answer. The values rest on the prices and rates below."}</p>}
    {items.length > 0 && <div className="ev-list" style={{ margin: "0 -8px" }}>{items.map(item => <div ref={item.id === focus ? focused : undefined} key={item.id} className={`ev${item.id === focus ? " focus" : ""}`}>
      <span className="num">{numbers.get(item.id)}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div><div>{item.title}</div><div className="cap">{item.source} · {shortDate(item.date)}</div></div>
        {item.excerpt && (item.id === focus || items.length <= 2) && <blockquote>&ldquo;{item.excerpt}&rdquo;</blockquote>}
        {item.facts.length > 0 && item.id === focus && <ul className="cap" style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>{item.facts.map(fact => <li key={fact}>{fact}</li>)}</ul>}
        {item.url && /^https?:\/\//.test(item.url) && <a className="cap" href={item.url} target="_blank" rel="noreferrer">Open source ↗ <span className="m">(new tab)</span></a>}
      </div>
    </div>)}</div>}
    {missing.length > 0 && <div className="pgroup"><span className="lbl">Looked for, not found</span>
      {missing.map(item => <div key={item.id} className="ev"><span className="m">—</span><div><div className="m">{item.title}</div><div className="cap">{item.source} · {shortDate(item.date)}</div></div></div>)}
      <p className="cap">Missing sources are listed so you can judge what the answer couldn&apos;t see.</p></div>}
    {result && !decision && <Valuation result={result} />}
    {target?.mark && <div className="pgroup pnote"><span className="lbl">Your price</span>
      <span className="n">{target.ticker || positionName(target, target.id)} {money(target.mark.value, target.currency)}</span>
      <span className="cap">{target.mark.source} · {shortDate(target.mark.as_of)}</span></div>}
    {scan && <div className="pgroup pnote"><span className="lbl">Screen</span>
      <span className="cap">{scan.source} Scanned {shortDate(scan.scanned_at)}, evidence as of {shortDate(scan.as_of)}.</span>
      <ul className="plist" style={{ listStyle: "none" }}>{scan.candidates.map(candidate => <li key={candidate.position.id}>
        <span>{candidate.position.ticker || candidate.position.id}</span> <span className="m">· {candidate.signal}</span>
        {candidate.source_url && /^https?:\/\//.test(candidate.source_url) && <> · <a href={candidate.source_url} target="_blank" rel="noreferrer">source ↗</a></>}</li>)}</ul>
      {scan.issues.map(issue => <span className="cap amber" key={issue}>{issue}</span>)}</div>}
  </>;
}

/** Each position's dated price and the FX used, as returned with the review. Unknown stays unknown. */
function Valuation({ result }: { result: Analysis }) {
  const rows = result.portfolio.positions.filter(row => row.supplied.kind !== "cash" && row.supplied.id !== NEW_CASH_DESTINATION);
  const rates = [...new Map(result.portfolio.positions.filter(row => row.fx_used).map(row => [`${row.fx_used!.from_currency}${row.fx_used!.to_currency}`, row.fx_used!])).values()];
  if (!rows.length && !rates.length) return null;
  return <div className="pgroup pnote"><span className="lbl">Prices and rates used</span>
    <ul className="plist n" style={{ listStyle: "none" }} aria-label="Prices and rates used">
      {rows.map(row => <li key={row.supplied.id} className="pv"><span>{row.supplied.ticker || row.supplied.id}</span>
        <span className={row.quote_used ? "" : "amber"}>{row.quote_used ? <>{money(row.quote_used.value, row.quote_used.currency)} <span className="m">· {row.quote_used.source} · {shortDate(row.quote_used.as_of)} · {row.quote_used.status}</span></> : "No price"}</span></li>)}
      {rates.map(fx => <li key={`${fx.from_currency}${fx.to_currency}`} className="pv"><span>{fx.from_currency}/{fx.to_currency}</span><span>{fx.rate} <span className="m">· {fx.source} · {shortDate(fx.as_of)}</span></span></li>)}
    </ul>
    {result.portfolio.company_overlap.some(row => row.contributing_funds.length) && <span className="cap">Fund holdings: {[...new Set(result.portfolio.company_overlap.flatMap(row => row.contributing_funds.map(fund => `${fund.ticker || fund.position_id} (${fund.source}, ${shortDate(fund.as_of)})`)))].join(" · ")}</span>}
  </div>;
}

function ScenariosTab({ result, snapshot }: { result: Analysis; snapshot: Snapshot | null }) {
  const stocks = [result.stock, ...(result.allocation?.stocks || [])].filter((s): s is NonNullable<typeof s> => !!s);
  const comparison = result.comparison;
  const currency = result.portfolio.reporting_currency;
  const altName = (alt: NonNullable<typeof comparison>["alternatives"][number]["selection"]) => alt.kind === "no_action" ? "No action" : alt.kind === "cash" ? "Keep as cash" :
    alt.kind === "short_bill" ? "Short-term bills" : snapshot?.positions.find(row => row.id === alt.position_id)?.ticker || alt.position_id || alt.id;
  if (!stocks.length && !comparison) return <p className="cap">{result.allocation ? "No scenarios were produced for this answer." : "A portfolio review describes where you stand today; it doesn't project scenarios. /new-cash compares futures for new money."}</p>;
  return <>
    {stocks.map(stock => {
      const row = snapshot?.positions.find(position => position.id === stock.position_id);
      const quote = result.portfolio.positions.find(position => position.supplied.id === stock.position_id)?.quote_used;
      const local = caseCurrency(stock, row?.currency || quote?.currency || stock.reporting_currency);
      return <div className="pgroup" key={stock.position_id}>
        <div><div>{positionName(row, stock.position_id)} · per share, discounted to today</div>
          <div className="cap n">{quote ? `Price ${money(quote.value, quote.currency)} on ${shortDate(quote.as_of)} · ` : row?.mark ? `Price ${money(row.mark.value, row.currency)} on ${shortDate(row.mark.as_of)} · ` : ""}{stock.judgments.method.replaceAll("_", " ")}</div></div>
        {(() => {
          const base = stock.cases.find(c => c.name === "base");
          if (!base?.path?.length) return null;
          return <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <table className="matrix" aria-label="Base case, year by year">
              <thead><tr><th scope="col">Year</th><th scope="col">Revenue</th><th scope="col">{stock.judgments.method === "fcf_exit" ? "FCF" : "Earnings"}</th><th scope="col">Shares</th><th scope="col">Per share</th></tr></thead>
              <tbody>{base.path.map(year => <tr key={year.year}><th scope="row">{year.year}</th><td>{compact(year.revenue, local)}</td><td>{compact(year.metric, local)}</td>
                <td>{compact(year.diluted_shares, null).replace(" shares", "")}</td><td>{money(year.metric_per_share, local)}</td></tr>)}</tbody>
            </table>
            <span className="cap n">Start {compact(base.starting_metric, local)} revenue, {compact(base.starting_shares, null)} · year 5 {compact(base.terminal_metric, local)} × {base.judgment.exit_multiple} = equity {compact(base.equity_value, local)} ÷ {compact(base.terminal_shares, null)} = {money(base.terminal_price, local)} · ÷ {base.discount_factor} = {money(base.present_value_of_exit, local)} + payouts {money(base.present_value_of_distributions, local)} = {money(base.present_value_per_share, local)}</span>
          </div>;
        })()}
        {stock.cases.map(c => <div className="case" key={c.name}>
          <div className="pv" style={{ alignItems: "baseline" }}><span className="lbl" style={{ color: c.name === "base" ? "var(--brass)" : undefined }}>{c.name}</span>
            <span className="fig" style={{ color: c.name === "base" ? "var(--brass)" : undefined }}>{money(c.present_value_per_share, local)}</span></div>
          <div className="drv n"><span>Exit price</span><span>{money(c.terminal_price, local)}</span></div>
          {c.required_exit_multiple && <div className="drv n"><span>Multiple needed</span><span>{c.required_exit_multiple}</span></div>}
          <div className="drv n"><span>Exit multiple</span><span>{c.judgment.exit_multiple}</span></div>
          <div className="drv n"><span>Discount rate</span><span>{pct(c.judgment.discount_rate)}</span></div>
          {c.judgment.growth.length > 0 && <div className="drv n"><span>Growth path</span><span>{c.judgment.growth.map(pct).join(" → ")}</span></div>}
        </div>)}
        <details><summary className="cap" style={{ cursor: "pointer" }}>Calculation basis</summary><p className="cap" style={{ paddingTop: 6 }}>{stock.calculation_basis}</p></details>
      </div>;
    })}
    {comparison && <div className="pgroup">
      <div><div>Where the money could go · {comparison.horizon_years} years</div>
        <div className="cap n">Starting value {money(comparison.starting_value, comparison.reporting_currency || currency)} · value at the end of each case</div></div>
      <table className="matrix" aria-label="Five-year comparison">
        <thead><tr><th scope="col">Option</th>{comparison.alternatives[0]?.cases.map(c => <th scope="col" key={c.name}>{c.name}</th>)}</tr></thead>
        <tbody>{comparison.alternatives.map(alt => <tr key={alt.selection.id}><th scope="row">{altName(alt.selection)}</th>
          {alt.cases.map(c => <td key={c.name} className={(c.terminal_value ?? c.comparison_value) == null ? "m" : ""}>{money(c.terminal_value ?? c.comparison_value, comparison.reporting_currency || currency)}</td>)}</tr>)}</tbody>
      </table>
      {comparison.alternatives.some(alt => alt.cases.some(c => !c.terminal_value && c.comparison_value)) && <p className="cap">Values are before {[...new Set(comparison.alternatives.flatMap(alt => alt.cases.flatMap(c => c.terminal_value ? [] : c.unmodeled || [])))].join(" and ")}, which are unknown and not assumed to be zero.</p>}
      {comparison.alternatives.some(alt => alt.cases.some(c => c.terminal_value === null)) && <p className="cap">Unknown where costs, fund facts or drivers weren&apos;t supplied. Unknown is never treated as zero.</p>}
      {comparison.qualifications.length > 0 && <details><summary className="cap" style={{ cursor: "pointer" }}>Comparison notes · {comparison.qualifications.length}</summary>
        <ul className="cap" style={{ paddingLeft: 16, paddingTop: 6 }}>{comparison.qualifications.map(q => <li key={q}>{q}</li>)}</ul></details>}
    </div>}
    <p className="cap pnote">Each case is a set of stated assumptions, not a forecast.</p>
  </>;
}

function statusWord(status: string) {
  if (status === "within_limit") return <span className="sage">Within</span>;
  if (status === "breached") return <span className="amber">Over</span>;
  if (status === "unset") return <span className="m">No limit set</span>;
  return <span className="m">Unknown</span>;
}

function GuardrailsTab({ result, snapshot }: { result: Analysis; snapshot: Snapshot | null }) {
  const previews = result.allocation?.previews || [];
  const top = previews.at(-1);
  const current = result.portfolio.guardrails;
  const review: GuardrailReview | null = top?.guardrails ?? current;
  const currency = result.portfolio.reporting_currency;
  const added = top?.changes.new_cash[0];
  const addedCurrency = result.portfolio.positions.find(row => row.supplied.id === added?.cash_position_id)?.supplied.currency || currency;
  if (!review) return <p className="cap">You haven&apos;t set any rules, so nothing was checked. Set a company cap or active budget under Holdings.</p>;
  return <>
    <div><div>{top && added ? `If ${money(added.amount, addedCurrency)} arrives and is invested at the top of the range` : "Your portfolio today (no amount was tested)"}</div>
      <div className="cap">Checked against the rules you set · {review.settings.indirect_cap_policy === "include_known_indirect" ? "includes known fund overlap" : "direct holdings only"}</div></div>
    <div>
      {review.companies.map(company => {
        const before = current?.companies.find(c => c.company_id === company.company_id);
        // "Over before this cash" only means something when an amount was tested against today.
        const wasOver = !!top && before?.status === "breached" && company.status === "breached";
        return <div className="g" key={company.company_id}>
          <div className="pv n"><span>Single company · {company.company_name}</span>
            <span>{wasOver ? <span className="amber">Over, before this cash</span> : statusWord(company.status)} · {pct(company.current_weight)} / {pct(company.cap)}</span></div>
          {company.status === "breached" && <span className="cap">{wasOver ? "Already over before this cash. This trade doesn't fix it." : company.explanation}
            {" "}<a href="/classic">/rebalance</a></span>}
        </div>;
      })}
      <div className="g"><div className="pv n"><span>Active picks</span><span>{statusWord(review.active.status)} · {pct(review.active.weight)} / {pct(review.active.budget)}</span></div>
        {review.active.qualifications.map(q => <span className="cap" key={q}>{q}</span>)}</div>
      <div className="g"><div className="pv"><span>Inside your funds</span><span className="m">{result.portfolio.indirect_exposure === "full" ? "Counted" : result.portfolio.indirect_exposure === "none" ? "No funds held" : "Unknown"}</span></div>
        {["unknown", "partial", "stale"].includes(result.portfolio.indirect_exposure) && <span className="cap">Fund look-through is {result.portfolio.indirect_exposure}; indirect exposure is not assumed to be zero.</span>}</div>
      <div className="g"><div className="pv"><span>Target mix</span><span className="m">{review.baseline_comparison.length ? "Set" : "Not set"}</span></div>
        {review.baseline_comparison.map(row => <div className="drv n" key={row.category}><span>{row.category.replaceAll("_", " ")}</span><span>{pct(row.current_weight)} vs {pct(row.baseline_weight)}</span></div>)}</div>
    </div>
    {previews.length > 1 && <p className="cap pnote">Both ends of the range were checked: {previews.map(p => p.status.replaceAll("_", " ")).join(" and ")}.</p>}
  </>;
}

function RulesSummary({ settings }: { settings: PortfolioSettings }) {
  return <div className="pgroup"><span>Your rules</span>
    <span className="cap n">Single company {settings.single_company_cap ? pct(settings.single_company_cap) : "not set"} · active picks {settings.active_budget ? pct(settings.active_budget) : "not set"}</span>
    <span className="cap">They&apos;re checked against the portfolio after the new cash when the answer arrives. Edit them under Holdings.</span></div>;
}

const EXCHANGES = [["XNAS", "Nasdaq"], ["XNYS", "NYSE"], ["XASE", "NYSE American"], ["XTSE", "TSX"], ["XTSX", "TSX Venture"], ["NEOE", "Cboe Canada"], ["XCNQ", "CSE"]];

/** Where analysis prices come from. Delayed or cached quotes are never called live. */
function PriceStatus({ prices, refreshing, disabled, onRefresh }: { prices: PriceRefresh | null; refreshing: boolean; disabled: boolean; onRefresh: () => void }) {
  const quotes = Object.values(prices?.quotes ?? {});
  const latest = quotes.map(q => q.captured_at).filter((t): t is string => !!t).sort().at(-1);
  const cached = quotes.some(q => q.status === "cached");
  const sources = [...new Set(quotes.map(q => q.source))].join(" and ");
  return <div className="pgroup pnote" aria-label="Prices" role="group">
    <span className="lbl">Prices</span>
    <span className="cap">{quotes.length ? `${quotes.length} delayed quote${quotes.length === 1 ? "" : "s"} from ${sources}, fetched ${new Date(latest ?? "").toLocaleString()}${cached ? "; some are from an earlier day" : ""}. Not live.` : "No cached prices yet."}
      {prices?.message ? ` ${prices.message}` : ""}{prices?.remaining != null ? ` ${prices.remaining} EODHD requests left today.` : ""}</span>
    <span><button type="button" className="btn secondary small" disabled={refreshing || disabled} onClick={onRefresh}>{refreshing ? "Refreshing…" : "Refresh prices"}</button></span>
  </div>;
}

/** Asks only for what is missing: the exchange, the security type, or both. */
function Identify({ row, snapshot, disabled, onImported, onError }: { row: UnresolvedHolding; snapshot: Snapshot; disabled: boolean;
  onImported: (saved: SavedPortfolio) => void; onError: (message: string) => void }) {
  const [listing, setListing] = useState("");
  const [kind, setKind] = useState("");
  const [busy, setBusy] = useState(false);
  // When the lookup found several listings, only the exchange is asked; the type comes from the lookup.
  const choices = row.candidates?.length ? EXCHANGES.filter(([code]) => row.candidates.includes(code)) : EXCHANGES;
  const ready = (row.listing || listing) && (row.kind || kind || row.candidates?.length);
  async function save() {
    setBusy(true); onError("");
    try { onImported(await post<SavedPortfolio>("/api/portfolio/identify", { account_id: row.account_id, ticker: row.ticker, listing: listing || null, kind: kind || null })); }
    catch (failure) { onError(failure instanceof Error ? failure.message : "The holding couldn't be identified."); }
    finally { setBusy(false); }
  }
  return <div className="identify">
    <span className="n">{row.ticker} <span className="m">· {row.shares} sh · {snapshot.accounts.find(account => account.id === row.account_id)?.name}</span></span>
    <div className="rules">
      {!row.listing && <label className="field"><span className="cap">Exchange</span>
        <select className="in" aria-label={`Exchange for ${row.ticker}`} value={listing} disabled={disabled || busy} onChange={event => setListing(event.target.value)}>
          <option value="">Choose</option>{choices.map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select></label>}
      {!row.kind && !row.candidates?.length && <label className="field"><span className="cap">Type</span>
        <select className="in" aria-label={`Type for ${row.ticker}`} value={kind} disabled={disabled || busy} onChange={event => setKind(event.target.value)}>
          <option value="">Choose</option><option value="stock">Stock</option><option value="etf">ETF</option></select></label>}
    </div>
    <span><button type="button" className="btn secondary small" disabled={!ready || disabled || busy} onClick={save}>{busy ? "Saving…" : `Save ${row.ticker}`}</button></span>
  </div>;
}

/** Percent in the UI, fraction for the backend. Local text keeps partial input like "12." editable. */
function PercentInput({ label, value, onChange }: { label: string; value: string | null | undefined; onChange: (fraction: string | null) => void }) {
  const [text, setText] = useState(value ? String(Number((Number(value) * 100).toFixed(4))) : "");
  // A restored rule replaces the field unless it already says the same thing.
  useEffect(() => { setText(current => (current === "" ? null : Number(current) / 100) === (value ? Number(value) : null) ? current : value ? String(Number((Number(value) * 100).toFixed(4))) : ""); }, [value]);
  return <label className="field"><span className="cap">{label}</span><input className="in n" inputMode="decimal" value={text}
    onChange={event => { const next = event.target.value.replace(/[^0-9.]/g, ""); setText(next); onChange(next === "" || Number.isNaN(Number(next)) ? null : String(Number(next) / 100)); }} /></label>;
}

function HoldingsTab({ snapshot, setSnapshot, averageCosts, unresolved, onImported, settings, setSettings, result, decision, running, onError, prices, refreshing, onRefreshPrices }: Props) {
  if (!snapshot) return <p className="cap">Import a portfolio to see holdings.</p>;
  const review = result?.portfolio;
  const editable = !running;
  return <>
    {decision && <p className="cap">Today&apos;s portfolio. Historical weights weren&apos;t saved with this decision.</p>}
    <PriceStatus prices={prices} refreshing={refreshing} disabled={running} onRefresh={onRefreshPrices} />
    {unresolved.length > 0 && <div className="pgroup needs-id" aria-label="Holdings to identify" role="group">
      <span className="lbl amber">Needs you · {unresolved.length} holding{unresolved.length === 1 ? "" : "s"} to identify</span>
      <span className="cap">These couldn&apos;t be matched to a listing or type, so they aren&apos;t in the analysis yet. Nothing is assumed.</span>
      {unresolved.map(row => <Identify key={`${row.account_id}:${row.ticker}`} row={row} snapshot={snapshot} disabled={!editable} onImported={onImported} onError={onError} />)}
    </div>}
    {snapshot.accounts.map(account => {
      const rows = snapshot.positions.filter(row => row.account_id === account.id);
      return <div className="pgroup" key={account.id}>
        <div className="pv"><span className="lbl">{account.name || "Account"}</span>
          {review && <span className="cap n">{money(review.accounts.find(a => a.id === account.id)?.total_value ?? null, review.reporting_currency)}</span>}</div>
        <div className="plist n">{rows.map(row => {
          const valued = review?.positions.find(p => p.supplied.id === row.id);
          return <div key={row.id} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="pv"><span>{row.kind === "cash" ? `Cash ${row.currency}` : row.ticker || row.company_name || row.id} <span className="m">{row.kind === "cash" ? "" : `· ${row.shares} sh${row.currency !== snapshot.reporting_currency ? ` · ${row.currency}` : ""}`}</span></span>
              <span>{valued ? <>{money(valued.value, review!.reporting_currency)} <span className="m">{pct(valued.weight)}</span></> : row.kind === "cash" ? money(row.cash ?? null, row.currency) : row.mark ? `@ ${money(row.mark.value, row.currency)}` : <span className="m">no price</span>}</span></div>
            {row.kind === "etf" && <label className="pv cap" style={{ alignItems: "center" }}><span>Fund type</span>
              <select className="in" style={{ width: 180, minHeight: 32 }} disabled={!editable} aria-label={`Fund type for ${row.ticker || row.id}`} value={row.etf_role ?? ""}
                onChange={event => setSnapshot(current => current && ({ ...current, positions: current.positions.map(p => p.id === row.id ? { ...p, etf_role: (event.target.value || null) as typeof p.etf_role } : p) }))}>
                <option value="">Unknown</option><option value="diversified">Diversified</option><option value="sector_theme">Sector or theme</option></select></label>}
            {averageCosts[row.id] && <span className="cap">Average cost {money(averageCosts[row.id], row.currency)} · yours, not used for value</span>}
            {valued?.issues.map(issue => <span className="cap amber" key={issue}>{issue}</span>)}
          </div>;
        })}</div>
      </div>;
    })}
    <p className="cap pnote">{review ? `Valued ${shortDate(review.reviewed_at)} from your portfolio as of ${fullDate(review.as_of)}.` : `Portfolio as of ${fullDate(snapshot.as_of)}. Prices, values and weights come back with the analysis.`}
      {snapshot.fx.map(fx => ` ${fx.from_currency}/${fx.to_currency} ${fx.rate}.`)}</p>

    <fieldset className="pgroup pnote" disabled={!editable} style={{ border: 0, padding: "12px 0 0" }}>
      <legend className="lbl" style={{ padding: 0, marginBottom: 12 }}>Your rules</legend>
      <div className="rules">
        <PercentInput label="Single-company cap (%)" value={settings.single_company_cap} onChange={single_company_cap => setSettings(s => ({ ...s, single_company_cap }))} />
        <PercentInput label="Active picks budget (%)" value={settings.active_budget} onChange={active_budget => setSettings(s => ({ ...s, active_budget }))} />
      </div>
      <label className="field"><span className="cap">Count fund holdings toward the cap</span>
        <select className="in" value={settings.indirect_cap_policy ?? ""} onChange={event => setSettings(s => ({ ...s, indirect_cap_policy: (event.target.value || null) as PortfolioSettings["indirect_cap_policy"] }))}>
          <option value="">Not set</option><option value="direct_only">No, direct holdings only</option><option value="include_known_indirect">Yes, where fund holdings are known</option></select></label>
      <label className="field"><span className="cap">Cash above target is a deliberate choice</span>
        <select className="in" value={settings.cash_is_deliberate_tilt === undefined || settings.cash_is_deliberate_tilt === null ? "" : String(settings.cash_is_deliberate_tilt)}
          onChange={event => setSettings(s => ({ ...s, cash_is_deliberate_tilt: event.target.value === "" ? null : event.target.value === "true" }))}>
          <option value="">Not set</option><option value="true">Yes</option><option value="false">No</option></select></label>
    </fieldset>
    <ImportCsv snapshot={snapshot} onImported={onImported} onError={onError} disabled={!editable} label="Replace with a new CSV" />
    <p className="cap">Need to edit individual positions or set a target mix? Use the <a href="/classic">classic view</a>.</p>
  </>;
}

/** Imports and saves the current portfolio. The holdings date is the user's; it is shown wherever the portfolio is. */
export function ImportCsv({ snapshot, onImported, onError, disabled, label }: {
  snapshot: Snapshot | null; onImported: (saved: SavedPortfolio) => void; onError: (message: string) => void; disabled?: boolean; label: string;
}) {
  const [asOf, setAsOf] = useState(() => new Date().toLocaleDateString("en-CA"));
  const [currency, setCurrency] = useState(snapshot?.reporting_currency || "CAD");
  const [busy, setBusy] = useState(false);
  async function load(file: File) {
    if (file.size > 1_000_000) { onError("CSV must be no larger than 1 MB."); return; }
    onError(""); setBusy(true);
    try { onImported(await post<SavedPortfolio>("/api/portfolio/import", { csv: await file.text(), as_of: asOf || undefined, reporting_currency: currency })); }
    catch (failure) { onError(failure instanceof Error ? failure.message : "CSV import failed."); }
    finally { setBusy(false); }
  }
  return <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
    <div className="setup">
      <label className="field"><span className="cap">Holdings as of</span><input className="in" type="date" aria-label="Holdings as of" value={asOf} disabled={disabled} onChange={event => setAsOf(event.target.value)} /></label>
      <label className="field"><span className="cap">Reporting currency</span><input className="in" aria-label="Reporting currency" value={currency} maxLength={3} disabled={disabled} onChange={event => setCurrency(event.target.value.toUpperCase())} /></label>
    </div>
    <label className="drop" style={{ position: "relative" }}>
      <span>{busy ? "Importing…" : label}</span>
      <input type="file" accept=".csv,text/csv" aria-label="Load portfolio CSV" disabled={disabled || busy}
        onChange={event => { const file = event.target.files?.[0]; if (file) void load(file); event.target.value = ""; }} />
    </label>
    <span className="cap">Columns: account, ticker, shares, and optionally average_cost, currency (USD or CAD) and type. Exchange, name and current price are looked up; currency avoids a question when a ticker trades in both countries. Cash rows are optional. <a href="/api/portfolio/holdings-template" download>Template</a> · the <a href="/api/portfolio/template" download>full template</a> also works.</span>
  </div>;
}
