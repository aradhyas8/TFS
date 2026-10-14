import { ArrowLeft, RefreshCw, TriangleAlert, Upload } from "lucide-react";
import { useState, type Dispatch, type KeyboardEvent, type ReactNode, type SetStateAction } from "react";
import { isSupportedStock, type BenchmarkSetting, type PortfolioProposal, type PortfolioSettings, type Position, type PriceRefresh, type Review, type SavedPortfolio, type Snapshot, type UnresolvedHolding } from "../../lib/contracts";
import { baselineOver, Identify, ImportCsv, RulesEditor } from "./Panel";
import { Alert } from "@/components/arc/alert/alert";
import { Button } from "@/components/arc/button/button";
import { Skeleton } from "@/components/arc/skeleton/skeleton";
import { accountName, fullDate, money, pct, shortDate } from "./format";
import { allocation, holdingStatus, isSynthetic, type HoldingStatus } from "./portfolio";

type Props = {
  /** The menu button, so the rail drawer opens from this view too. */
  menu: ReactNode;
  restoring: boolean; notice: string;
  revision: number | null; committed: SavedPortfolio | null; unresolved: UnresolvedHolding[];
  /** The draft: the committed holdings with any unsaved fund types, rules and benchmark. */
  draft: Snapshot | null; setDraft: Dispatch<SetStateAction<Snapshot | null>>;
  settings: PortfolioSettings; setSettings: Dispatch<SetStateAction<PortfolioSettings>>;
  benchmark: BenchmarkSetting | null; setBenchmark: Dispatch<SetStateAction<BenchmarkSetting | null>>;
  /** An analysis of exactly this revision, or null. Values and weights come only from it. */
  valuation: Review | null;
  prices: PriceRefresh | null; refreshing: boolean; onRefreshPrices: () => void;
  isDirty: boolean; edits: string[]; reviewing: boolean; running: boolean;
  onReviewChanges: () => void; onDiscardDraft: () => void; onAnalyzeDraft: () => void;
  pending: PortfolioProposal[]; onReopenProposal: (proposal: PortfolioProposal) => void; onCancelProposal: (id: string) => void;
  onProposal: (proposal: PortfolioProposal) => void; onError: (message: string) => void;
  onStock: (position: Position) => void; onReview: () => void; onBack: () => void;
};

type PortfolioTab = "holdings" | "rules" | "changes";
const ORIGIN: Record<string, string> = { identify: "Identity correction", import: "Holdings import", chat: "Change from a conversation", manual: "Manual edit" };
const STATUS_LABEL: Record<HoldingStatus, string> = { over: "Over", within: "Within", fund: "Fund", cash: "Cash", not_valued: "Not valued", unchecked: "Not checked" };
const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
const initials = (row: Position) => (row.kind === "cash" ? "$" : (row.ticker || row.company_name || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase());

/** What an unresolved holding still needs before it can be analyzed. */
const needs = (row: UnresolvedHolding) => [!row.listing && "an exchange", !row.kind && "a type"].filter(Boolean).join(" and ");

/** The committed portfolio as its own view: what it is, what it is worth when an analysis of this revision says so,
 *  the holdings, the rules, and what is waiting. Edits stay in the draft until reviewed and confirmed. */
export default function PortfolioView(props: Props) {
  const { committed, draft, valuation, revision } = props;
  const [tab, setTab] = useState<PortfolioTab>("holdings");
  const [account, setAccount] = useState("all");
  const [importing, setImporting] = useState(false);
  const [identifying, setIdentifying] = useState(false);
  const snapshot = committed?.snapshot ?? null;
  const tabs: { id: PortfolioTab; label: string }[] = [
    { id: "holdings", label: "Holdings" },
    { id: "rules", label: "Rules and benchmark" },
    { id: "changes", label: props.pending.length ? `Changes · ${props.pending.length} pending` : "Changes" },
  ];
  // Arrow keys, Home and End move between the tabs, as in any tab list.
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const at = tabs.findIndex(item => item.id === tab);
    const to = ({ ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabs.length - 1 } as Record<string, number>)[event.key];
    if (to === undefined) return;
    event.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length].id;
    setTab(next);
    requestAnimationFrame(() => document.getElementById(`ptab-${next}`)?.focus());
  };

  const header = <header className="ctx view-head">
    <div className="ctx-left">
      {props.menu}
      <h1 id="portfolio-title" className="view-title" tabIndex={-1}>Portfolio</h1>
      {snapshot && <span className="chip live-chip hide-sm"><span className="live" aria-hidden="true" />
        {revision != null ? `Revision ${revision} · holdings` : "Holdings"} as of {fullDate(snapshot.as_of)}</span>}
    </div>
    <div className="ctx-right">
      <Button variant="ghost" size="sm" onClick={props.onBack}><ArrowLeft size={15} aria-hidden="true" />Back to chat</Button>
      {snapshot && <Button variant="ghost" size="sm" className="hide-sm" disabled={props.refreshing || props.running} onClick={props.onRefreshPrices}>
        <RefreshCw size={15} aria-hidden="true" />{props.refreshing ? "Refreshing…" : "Refresh prices"}</Button>}
      {snapshot && <Button variant="secondary" size="sm" aria-expanded={importing} aria-controls="portfolio-import"
        onClick={() => { setTab("holdings"); setImporting(open => !open); }}><Upload size={15} aria-hidden="true" />Import CSV</Button>}
    </div>
  </header>;

  if (props.restoring || !snapshot || !draft)
    return <section className="pview" aria-labelledby="portfolio-title">
      {header}
      <div className="pview-body">
        {props.notice && <Alert tone="warning" title={props.notice} />}
        {props.restoring ? <Skeleton label="Loading your saved portfolio" lines={4} /> : <div className="pempty">
          <h2>No portfolio saved yet.</h2>
          <p className="cap">Import your holdings once. They are saved on this computer, and nothing changes until you confirm the import.</p>
          <ImportCsv snapshot={null} baseRevision={revision} onProposal={props.onProposal} onError={props.onError} label="Choose a holdings CSV" />
        </div>}
      </div>
    </section>;

  const held = draft.positions.filter(row => !isSynthetic(row.id));
  const rows = held.filter(row => account === "all" || row.account_id === account);
  const holdings = held.filter(row => row.kind !== "cash").length;
  const cashCount = held.length - holdings;
  const valued = (row: Position) => valuation?.positions.find(item => item.supplied.id === row.id);
  const mix = valuation && allocation(valuation);
  const maxWeight = Math.max(...held.map(row => Number(valued(row)?.weight ?? 0)), Number(committed?.settings?.single_company_cap ?? 0), 0.0001);
  const cap = committed?.settings?.single_company_cap ?? null;
  const active = committed?.settings?.active_budget ?? null;
  const guard = valuation?.guardrails ?? null;
  const overCap = guard?.companies.filter(company => company.status === "breached").length ?? 0;
  const quoteDates = valuation?.positions.map(row => row.quote_used?.as_of).filter((date): date is string => !!date).sort() ?? [];
  const fx = valuation ? [...new Map(valuation.positions.flatMap(row => (row.fx_used ? [[`${row.fx_used.from_currency}${row.fx_used.to_currency}`, row.fx_used] as const] : []))).values()] : snapshot.fx;
  const committedRole = (id: string) => snapshot.positions.find(row => row.id === id)?.etf_role ?? null;
  const quotes = Object.values(props.prices?.quotes ?? {});
  const fetched = quotes.map(quote => quote.captured_at).filter((at): at is string => !!at).sort().at(-1);

  const statusCell = (row: Position) => {
    const status = holdingStatus(row, valuation);
    if (row.kind === "etf")
      return <select className="in pill-select" aria-label={`Fund type for ${row.ticker || row.id}`} disabled={props.running} value={row.etf_role ?? ""}
        onChange={event => props.setDraft(current => current && ({ ...current, positions: current.positions.map(p => p.id === row.id ? { ...p, etf_role: (event.target.value || null) as Position["etf_role"] } : p) }))}>
        <option value="">Type unknown</option><option value="diversified">Diversified</option><option value="sector_theme">Sector or theme</option></select>;
    if (status === "over") return <span className="chip warn">Over</span>;
    if (status === "within") return <span className="chip good">Within</span>;
    return <span className="m">{STATUS_LABEL[status]}</span>;
  };

  return <section className="pview" aria-labelledby="portfolio-title">
    {header}
    <div className="tabs pview-tabs" role="tablist" aria-label="Portfolio views">
      {tabs.map(item => <button key={item.id} type="button" role="tab" id={`ptab-${item.id}`} aria-controls="ptabpanel" className="tab"
        aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} onKeyDown={onTabKey} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </div>
    <div className="pview-body" role="tabpanel" id="ptabpanel" aria-labelledby={`ptab-${tab}`}>
      {props.notice && <Alert tone="warning" title={props.notice} />}
      {/* The header chip names the revision; on a phone it has no room, so it is said here. */}
      <p className="cap n show-sm">{revision != null ? `Revision ${revision} · holdings` : "Holdings"} as of {fullDate(snapshot.as_of)}</p>

      {tab === "holdings" && <>
        {importing && <div className="pcard" id="portfolio-import" role="region" aria-label="Import holdings">
          <b>Replace holdings from a CSV</b>
          <span className="cap">The import is prepared as a change for you to review. Nothing is saved until you confirm it.</span>
          <ImportCsv snapshot={snapshot} baseRevision={revision} onProposal={props.onProposal} onError={props.onError} disabled={props.running} label="Choose a holdings CSV" />
        </div>}

        <div className="psum">
          <div className="pcard" role="group" aria-label="Total value">
            <span className="cap">{valuation ? `Total value · valued ${shortDate(valuation.reviewed_at)}` : "Total value"}</span>
            {valuation?.total_value != null ? <span className="ptotal n">{money(valuation.total_value, valuation.reporting_currency)}</span>
              : valuation ? <><span className="ptotal n">{money(valuation.known_value, valuation.reporting_currency)}</span><span className="cap amber">Known value only. Some holdings could not be valued.</span></>
              : <><span className="ptotal muted">Not valued yet</span>
                <span className="cap">Values and weights come from an analysis of this revision.</span>
                <span><Button variant="secondary" size="sm" disabled={props.running || props.unresolved.length > 0} onClick={props.onReview}>Review my portfolio</Button></span>
                {props.unresolved.length > 0 && <span className="cap">Identify the holdings below first.</span>}</>}
            <span className="cap psum-foot n">{plural(holdings, "holding")} · {plural(cashCount, "cash balance")} · {plural(snapshot.accounts.length, "account")}</span>
          </div>
          <div className="pcard" role="group" aria-label="Allocation">
            <span className="cap">Allocation</span>
            {mix ? <>
              <div className="alloc" aria-hidden="true">
                {mix.stocks > 0 && <i className="stocks" style={{ flexGrow: mix.stocks }} />}{mix.etfs > 0 && <i className="etfs" style={{ flexGrow: mix.etfs }} />}{mix.cash > 0 && <i className="cash" style={{ flexGrow: mix.cash }} />}
              </div>
              <div className="legend n"><span><i className="stocks" />Stocks {pct(String(mix.stocks))}</span><span><i className="etfs" />ETFs {pct(String(mix.etfs))}</span><span><i className="cash" />Cash {pct(String(mix.cash))}</span></div>
            </> : <>
              <span className="pfig n">{[plural(held.filter(row => row.kind === "stock").length, "stock"), plural(held.filter(row => row.kind === "etf").length, "ETF"), plural(cashCount, "cash balance")].join(" · ")}</span>
              <span className="cap">{valuation ? "Shares appear when every holding has a value." : "Shares of the portfolio appear with a valuation."}</span>
            </>}
          </div>
          <div className="pcard" role="group" aria-label="Rules">
            <div className="kv"><span>{cap ? `Company cap ${pct(cap)}` : "Company cap"}</span>
              <b className={!cap ? "m" : guard ? (overCap ? "amber" : "sage") : "m"}>{!cap ? "Not set" : guard ? (overCap ? `${overCap} over` : "All within") : "Not checked yet"}</b></div>
            <div className="kv"><span>{active ? `Active budget ${pct(active)}` : "Active budget"}</span>
              <b className={!active ? "m" : guard?.active.status === "breached" ? "amber" : guard?.active.status === "within_limit" ? "sage" : "m"}>
                {!active ? "Not set" : guard?.active.status === "breached" ? `Over${guard.active.weight ? ` · ${pct(guard.active.weight)}` : ""}` : guard?.active.status === "within_limit" ? `Within${guard.active.weight ? ` · ${pct(guard.active.weight)}` : ""}` : "Not checked yet"}</b></div>
            <div className="kv"><span>Benchmark</span><b className={committed?.benchmark ? undefined : "m"}>{committed?.benchmark?.ticker ?? "None"}</b></div>
          </div>
        </div>

        {props.unresolved.length > 0 && <div className="pnotice" role="note">
          <TriangleAlert size={17} aria-hidden="true" />
          <span><b>{props.unresolved.length === 1 ? "1 holding needs you." : `${props.unresolved.length} holdings need you.`}</b>{" "}
            {props.unresolved.map(row => `${row.ticker} needs ${needs(row) || "identifying"}`).join("; ")} before it can be analyzed.</span>
          <Button variant="secondary" size="sm" aria-expanded={identifying} aria-controls="identify-holdings" onClick={() => setIdentifying(open => !open)}>Identify</Button>
        </div>}
        {identifying && props.unresolved.length > 0 && <div className="pcard" id="identify-holdings" role="group" aria-label="Holdings to identify">
          <span className="cap">Nothing is assumed. Each answer is prepared as a change for you to review.</span>
          {props.unresolved.map(row => <Identify key={`${row.account_id}:${row.ticker}`} row={row} snapshot={snapshot} disabled={props.running} baseRevision={revision} onPreviewProposal={props.onReopenProposal} onError={props.onError} />)}
        </div>}
        {props.pending.length > 0 && <div className="pnotice" role="note">
          <TriangleAlert size={17} aria-hidden="true" />
          <span><b>{props.pending.length === 1 ? "1 change is waiting for your review." : `${props.pending.length} changes are waiting for your review.`}</b> Your holdings stay as they are until you confirm.</span>
          <Button variant="secondary" size="sm" onClick={() => setTab("changes")}>See changes</Button>
        </div>}

        {snapshot.accounts.length > 1 && <div className="seg" role="radiogroup" aria-label="Account">
          {[{ id: "all", name: "All accounts" }, ...snapshot.accounts].map(item => <label key={item.id} className="seg-opt">
            <input type="radio" name="portfolio-account" value={item.id} checked={account === item.id} onChange={() => setAccount(item.id)} />
            <span>{item.name || "Account"}</span></label>)}
        </div>}

        <div className="ht" role="table" aria-label="Holdings">
          <div role="rowgroup"><div className="hr hh" role="row">
            <span role="columnheader">Holding</span><span role="columnheader">Account</span><span role="columnheader" className="r">Shares</span>
            <span role="columnheader" className="r c-avg">Average cost</span><span role="columnheader" className="r">Value</span><span role="columnheader">Weight</span>
            <span role="columnheader">Status</span><span role="columnheader"><span className="sr-only">Actions</span></span>
          </div></div>
          <div role="rowgroup">
            {rows.map(row => {
              const v = valued(row);
              const name = row.kind === "cash" ? `Cash ${row.currency}` : row.ticker || row.company_name || "Unnamed";
              const status = holdingStatus(row, valuation);
              const weight = v?.weight ?? null;
              const isDraft = row.kind === "etf" && (row.etf_role ?? null) !== committedRole(row.id);
              const avg = committed?.average_costs[row.id];
              return <div className="hr" role="row" key={row.id} data-draft={isDraft || undefined}>
                <span role="cell" className="c-hold">
                  <span className={`av ${row.kind}`} aria-hidden="true">{initials(row)}</span>
                  <span className="hname"><b>{name}</b> <small>{row.kind === "cash" ? `${accountName(snapshot, row.account_id)} cash` : [row.company_name, row.listing, row.currency].filter(Boolean).join(" · ")}</small></span>
                </span>
                <span role="cell" className="c-acct" data-label="Account">{accountName(snapshot, row.account_id)}</span>
                <span role="cell" className="c-shares r n" data-label="Shares">{row.kind === "cash" ? "" : row.shares ?? <span className="m">Unknown</span>}</span>
                <span role="cell" className="c-avg r n" data-label="Average cost">{avg ? money(avg, row.currency) : row.kind === "cash" ? "" : <span className="m">Not given</span>}</span>
                <span role="cell" className="c-val r n">{v ? (v.value === null ? <span className="m">Not valued{v.issues.length > 0 && <span className="sr-only">: {v.issues.join(" ")}</span>}</span> : money(v.value, valuation!.reporting_currency))
                  : row.kind === "cash" ? money(row.cash ?? null, row.currency) : <span className="m">Not valued</span>}</span>
                <span role="cell" className="c-weight n" data-label="Weight">{weight !== null ? <>
                  <span className={`wbar ${status}`} aria-hidden="true"><i style={{ width: `${Math.min(100, (Number(weight) / maxWeight) * 100)}%` }} />
                    {cap && row.kind === "stock" && <u style={{ left: `${Math.min(100, (Number(cap) / maxWeight) * 100)}%` }} />}</span>{pct(weight)}</>
                  : <span className="sr-only">No weight yet</span>}</span>
                <span role="cell" className="c-status">{statusCell(row)}</span>
                <span role="cell" className="c-act">{isSupportedStock(row) && <Button variant="ghost" size="sm" aria-label={`Analyze ${name}`} title={`Stock Analysis for ${name}`}
                  disabled={props.running || props.unresolved.length > 0} onClick={() => props.onStock(row)}>Analyze</Button>}</span>
                <span className="br" role="presentation" />
              </div>;
            })}
          </div>
        </div>

        <p className="cap n pfresh">
          {valuation ? `Valued ${shortDate(valuation.reviewed_at)} from holdings as of ${fullDate(valuation.holdings_as_of || valuation.as_of)}.${quoteDates.length ? ` Prices from ${shortDate(quoteDates[0])}${quoteDates.at(-1) !== quoteDates[0] ? ` to ${shortDate(quoteDates.at(-1))}` : ""}.` : ""}`
            : `Holdings as of ${fullDate(snapshot.as_of)}. No analysis of this revision yet, so nothing is valued.`}
          {fx.map(rate => ` ${rate.from_currency} to ${rate.to_currency} ${rate.rate} (${shortDate(rate.as_of)}).`).join("")}
          {quotes.length ? ` ${plural(quotes.length, "delayed quote")} cached, fetched ${shortDate(fetched)}. Not live.` : ""}
        </p>
      </>}

      {tab === "rules" && <div className="pcard prules">
        <span className="cap">Rules and the benchmark are checked by every analysis. Changes stay in the draft until you review and confirm them.</span>
        <RulesEditor settings={props.settings} setSettings={props.setSettings} benchmark={props.benchmark} setBenchmark={props.setBenchmark} editable={!props.running} onError={props.onError} />
      </div>}

      {tab === "changes" && <div className="pchanges">
        {props.pending.length === 0 ? <p className="cap">No changes are waiting. Imports, identity corrections and edits from a conversation appear here until you confirm or discard them.</p>
          : props.pending.map(proposal => {
            const stale = proposal.status === "stale" || (revision != null && proposal.base_revision != null && proposal.base_revision !== revision);
            return <div className="pcard pchange" key={proposal.id} data-testid={`pending-proposal-item-${proposal.id}`}>
              <div className="pchange-text">
                <b>{ORIGIN[proposal.origin] || "Change"}</b>
                <span className="cap n">{proposal.base_revision != null ? `Prepared from revision ${proposal.base_revision}` : "First portfolio"} · {plural(proposal.differences.length, "change")}
                  {proposal.warnings.length ? ` · ${plural(proposal.warnings.length, "warning")}` : ""} · {shortDate(proposal.created_at)}</span>
                {stale && <span className="cap amber">Prepared from an older revision. Review it again before confirming.</span>}
              </div>
              <div className="pchange-act">
                <Button variant="ghost" size="sm" onClick={() => props.onCancelProposal(proposal.id)}>Cancel proposal</Button>
                <Button variant="secondary" size="sm" onClick={() => props.onReopenProposal(proposal)}>Review</Button>
              </div>
            </div>;
          })}
      </div>}

      {props.isDirty && <div className="draft-bar" role="region" aria-label="Unsaved draft">
        <span className="chip good">Draft</span>
        <span className="draft-text"><b>{props.edits.length ? `${plural(props.edits.length, "unsaved edit")}.` : "Unsaved edits."}</b> {props.edits.join("; ")}{props.edits.length ? ". " : ""}Nothing is saved yet.</span>
        <span className="draft-act">
          <Button variant="ghost" size="sm" disabled={props.running || props.reviewing} onClick={props.onDiscardDraft}>Discard</Button>
          <Button variant="secondary" size="sm" disabled={props.running || props.reviewing} onClick={props.onAnalyzeDraft}>Analyze this draft</Button>
          <Button variant="primary" size="sm" loading={props.reviewing} disabled={props.running || baselineOver(props.settings)} onClick={props.onReviewChanges}>Review changes</Button>
        </span>
      </div>}
    </div>
  </section>;
}
