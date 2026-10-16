import { AlertTriangle, Check, ChartLine, FileText, Globe, Pause } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Alert } from "@/components/arc/alert/alert";
import { Badge } from "@/components/arc/badge/badge";
import { Button } from "@/components/arc/button/button";
import { JsonViewer } from "@/components/arc/json-viewer/json-viewer";
import { Skeleton } from "@/components/arc/skeleton/skeleton";
import { TextShimmer } from "@/components/arc/text-shimmer/text-shimmer";
import {
  getArtifact,
  isSupportedStock,
  type Analysis,
  type ArtifactEnvelope,
  type ArtifactReference,
  type GuardrailReview,
  type MessageRecord,
  type PortfolioProposal,
  type ProposalReview,
  type Review,
  type RunRecord,
  type Snapshot,
  type ThemeInput,
  type ThreadDetail,
} from "../../lib/contracts";
import { linkOnly, placeCitations, webAddress, type Source } from "./citations";
import { fullDate, money, pct } from "./format";
import { blocks, inline } from "./Markdown";
import { Row } from "./Memo";
import { changeRows, isStale } from "./proposal";
import type { PendingSend } from "./useConversation";
import styles from "./Thread.module.css";

type Props = {
  thread: ThreadDetail | null;
  pending: PendingSend | null;
  sendError: string | null;
  loadError: string | null;
  onRetrySend: () => void;
  onDiscardSend: () => void;
  onAskAgain: (text: string) => void;
  onReload: () => void;
  /** Cancels the running run on the backend. */
  onStop: () => void;
  stopping: boolean;
  stopError: string | null;
  /** The saved portfolio: its holdings are the choices a stock clarification offers. */
  snapshot: Snapshot | null;
  committedRevision: number | null;
  /** Proposal records from the backend; null until the first list has arrived. */
  proposals: PortfolioProposal[] | null;
  proposalsFailed: boolean;
  onReloadProposals: () => void;
  /** The theme form as filled in this session, shown again when only the agreement is missing. */
  theme: ThemeInput;
  busy: boolean;
  /** Draws a stored analysis with the desk's existing result memo, against the portfolio it was computed from. */
  renderResult: (result: Analysis, reference: ArtifactReference, source: Snapshot) => ReactNode;
  onReviewProposal: (proposal: PortfolioProposal) => void;
  /** Opens an existing form (a workflow form or the holdings editor) so the missing inputs can be given. */
  onOpenForm: (form: ClarificationForm) => void;
  /** An explicit submission: a new message with typed workflow input, which starts a new run. */
  onSubmitWorkflow: (text: string, workflowInput: Record<string, unknown>) => void;
};

/** The existing desk forms a request for inputs can point at. A stock is chosen in place, so it opens none. */
export type ClarificationForm = "/new-cash" | "/theme" | "holdings";

type Shared = Pick<Props, "snapshot" | "committedRevision" | "proposals" | "proposalsFailed" | "onReloadProposals" | "theme" | "busy" | "renderResult" | "onReviewProposal" | "onOpenForm" | "onSubmitWorkflow">;

const RUN_NOTICE: Partial<Record<RunRecord["status"], string>> = {
  failed: "This answer did not finish",
  cancelled: "This answer was stopped",
  interrupted: "The analyst restarted before answering",
};

const STEP: Record<string, [done: string, failed: string]> = {
  web_search: ["Searched the web", "Could not search the web"],
  prepare_portfolio_proposal: ["Prepared portfolio changes for your review", "Could not prepare portfolio changes"],
};
const toolLabel = (name: string) => name.replace(/^(get|run)_/, "").replace(/_/g, " ");
const revisionOf = (id: string) => id.match(/^art-rev-(\d+)$/)?.[1];
// Stored labels arrive in title case ("Stock Analysis"); the desk writes in sentence case.
const sentenceCase = (label: string) => label.charAt(0) + label.slice(1).toLowerCase();
const sentence = (text: string) => (/[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);
const text = (value: unknown) => (typeof value === "string" ? value : "");
const list = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);

// Artifacts are immutable, so one request per ID serves every message and every reopening in this session.
const envelopes = new Map<string, Promise<ArtifactEnvelope>>();
type Loaded = { state: "loading" } | { state: "ready"; envelope: ArtifactEnvelope } | { state: "missing" } | { state: "failed" };

function useArtifact(id: string): [Loaded, () => void] {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setLoaded({ state: "loading" });
    let request = envelopes.get(id);
    if (!request) envelopes.set(id, (request = getArtifact(id)));
    request.then(
      envelope => current && setLoaded({ state: "ready", envelope }),
      error => {
        envelopes.delete(id);
        if (current) setLoaded({ state: (error as { status?: number }).status === 404 ? "missing" : "failed" });
      }
    );
    return () => {
      current = false;
    };
  }, [id, attempt]);
  return [loaded, () => setAttempt(count => count + 1)];
}

/** What a stored result was computed from. It is fixed when the result is stored and never follows the current portfolio. */
function provenance(reference: ArtifactReference): string {
  const basis = reference.basis === "hypothetical" ? "Hypothetical portfolio" : reference.revision ? `Portfolio revision ${reference.revision}` : "No portfolio used";
  return `${basis} · stored ${fullDate(reference.created_at)}`;
}

const unreadable = (
  <Alert tone="warning" title="This stored result cannot be shown">
    Its stored values are not in a form the desk can read. Ask again to compute a new result.
  </Alert>
);

/** The holdings and accounts a stored analysis was computed from, as the analysis itself recorded them.
 *  A memo drawn from a stored result names these, never today's portfolio. */
function storedSnapshot({ portfolio }: Analysis): Snapshot {
  return {
    as_of: portfolio.holdings_as_of ?? portfolio.as_of,
    reporting_currency: portfolio.reporting_currency,
    accounts: portfolio.accounts.map(({ id, name }) => ({ id, name })),
    positions: portfolio.positions.map(row => row.supplied),
    fx: [],
  };
}

function SourceLink({ url, title }: { url: URL; title: string }) {
  return (
    <>
      <a href={url.href} target="_blank" rel="noopener noreferrer">
        {title}
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
      <span className={styles.host}>{url.hostname}</span>
    </>
  );
}

/** A stored result in the conversation: a tonal card with what it is, where it came from, and its values. */
function Card({ label, icon, tone = "", title, meta, chip, children }: { label: string; icon: ReactNode; tone?: string; title: string; meta: string; chip?: ReactNode; children: ReactNode }) {
  return (
    <article className={styles.card} aria-label={label}>
      <header className={styles.cardHead}>
        <span className={`${styles.tile} ${tone}`} aria-hidden="true">{icon}</span>
        <span className={styles.cardTitle}>
          <b>{title}</b>
          <small className={styles.source}>{meta}</small>
        </span>
        {chip}
      </header>
      <div className={styles.cardBody}>{children}</div>
    </article>
  );
}

/** Web pages are not checked against filings. Said wherever they are shown, so they never read as verified evidence. */
const Unverified = () => <span className={`${styles.chip} ${styles.web}`}>Web · not verified</span>;

type WebResearch = { status?: string; query?: string | null; sources?: { url?: string; title?: string | null }[]; qualifications?: string[] };

function Artifact({ id, actionable, shared }: { id: string; actionable: boolean; shared: Shared }) {
  const [loaded, retry] = useArtifact(id);
  if (loaded.state === "loading") return <Skeleton label="Loading stored result" lines={2} />;
  if (loaded.state === "missing")
    return (
      <Alert tone="warning" title="This stored result is no longer available">
        The conversation still refers to it, but its values could not be found. Ask again to compute a new result.
      </Alert>
    );
  if (loaded.state === "failed")
    return (
      <div className={styles.notice}>
        <Alert tone="danger" title="This stored result could not be loaded">
          Check that the analyst service is running, then load it again.
        </Alert>
        <Button variant="secondary" onClick={retry}>
          Load result again
        </Button>
      </div>
    );
  const { reference, payload } = loaded.envelope;
  const source = provenance(reference);
  if (reference.kind === "portfolio_snapshot" || !reference.kind) return <Badge size="sm">{sentenceCase(reference.label)}</Badge>;
  // A financial capability that could not proceed stores what it needs; that is a request, not a result to read.
  const needed = (payload as { clarification_required?: Record<string, unknown> } | null)?.clarification_required;
  if (needed) {
    const form = ["portfolio_required", "unresolved_identity"].includes(text(needed.code)) ? "holdings" : "";
    return <Clarification clarification={{ ...needed, form }} actionable={actionable} shared={shared} />;
  }
  if (reference.kind === "analysis_result") {
    const readable = !!(payload as Partial<Analysis> | null)?.portfolio?.positions && !!(payload as Analysis).recommendation;
    const analysis = payload as Analysis;
    // The company a stock result is about, as the stored result recorded it.
    const held = readable && analysis.stock ? analysis.portfolio.positions.find(row => row.supplied.id === analysis.stock!.position_id)?.supplied : undefined;
    return (
      <Card
        label={`${sentenceCase(reference.label)} result`}
        icon={<ChartLine size={16} />}
        tone={styles.blue}
        title={`${sentenceCase(reference.label)}${held?.ticker ? ` · ${held.ticker}` : ""}`}
        meta={source}
        chip={held?.listing && <span className={styles.chip}>{`${held.listing} · ${held.currency}`}</span>}
      >
        {readable ? shared.renderResult(analysis, reference, storedSnapshot(analysis)) : unreadable}
      </Card>
    );
  }
  if (reference.kind === "web_research") {
    const research = payload as WebResearch;
    const sources = (research.sources ?? []).flatMap(item => {
      const url = webAddress(item.url);
      return url ? [{ url, title: item.title || url.hostname }] : [];
    });
    return (
      <Card
        label={reference.label}
        icon={<Globe size={16} />}
        title={`${reference.label}${research.query ? ` for "${research.query}"` : ""}`}
        meta={`Stored ${fullDate(reference.created_at)}`}
        chip={research.status !== "unavailable" && <Unverified />}
      >
        {research.status === "unavailable" ? (
          <Alert tone="warning" title="Web search was unavailable">
            {list(research.qualifications).map(sentence).join(" ") || "No current lookup took place."}
          </Alert>
        ) : (
          <>
            {sources.length ? (
              <ul className={styles.sources} aria-label="Sources consulted">
                {sources.map(item => (
                  <li key={item.url.href} className={styles.sourceCard}>
                    <Globe size={14} aria-hidden="true" className={styles.globe} />
                    <SourceLink url={item.url} title={item.title} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.source}>No sources were stored for this search.</p>
            )}
            {list(research.qualifications).map((note, index) => (
              <p key={index} className={styles.source}>
                {sentence(note)}
              </p>
            ))}
          </>
        )}
      </Card>
    );
  }
  const calculation = payload as { capability?: string; result?: Review & ProposalReview } | null;
  return (
    <Card label={reference.label} icon={<FileText size={16} />} title={reference.label} meta={source}>
      {calculation?.capability === "review_portfolio" && calculation.result?.positions ? (
        <ReviewSummary review={calculation.result} />
      ) : calculation?.capability === "check_proposed_changes" && calculation.result ? (
        <ChangeSummary review={calculation.result} />
      ) : (
        <Fields value={payload} />
      )}
      {/* The stored values stay one click away, exactly as the backend recorded them. */}
      <details className={styles.stored}>
        <summary>Show stored values</summary>
        <JsonViewer data={payload} rootName="result" label={`${reference.label} values`} defaultExpandDepth={2} searchable={false} showPath={false} maxHeight={320} />
      </details>
    </Card>
  );
}

const STATUS: Record<string, string> = { breached: "Breached", within_limit: "Within limit", unknown: "Unknown", unset: "No rule set", within_limits: "Within your rules", blocked: "Blocked by your rules" };
const humanize = (key: string) => sentenceCase(key.replace(/_/g, " "));

/** Every stored value is shown as the backend computed it; the desk only formats it. */
function Notes({ items, title = "Notes" }: { items: string[]; title?: string }) {
  return items.length ? (
    <Row label={title} kind="needs">
      <ul className="bullets">{items.map((item, index) => <li key={index}>{sentence(item)}</li>)}</ul>
    </Row>
  ) : null;
}

function Breaches({ guardrails }: { guardrails: GuardrailReview | null }) {
  if (!guardrails) return null;
  const breached = guardrails.companies.filter(row => row.status === "breached");
  const unknown = guardrails.companies.filter(row => row.status === "unknown");
  return (
    <>
      {(breached.length > 0 || guardrails.active.status === "breached") && (
        <Alert tone="danger" title="A saved rule is breached">
          <ul className={styles.mdList}>
            {breached.map(row => <li key={row.company_id}>{`${row.company_name}: ${pct(row.current_weight)} against a ${pct(row.cap)} cap. ${sentence(row.explanation)}`}</li>)}
            {guardrails.active.status === "breached" && <li>{`Active picks: ${pct(guardrails.active.weight)} against a ${pct(guardrails.active.budget)} budget.`}</li>}
          </ul>
        </Alert>
      )}
      {unknown.length > 0 && (
        <Alert tone="warning" title="Some rules could not be checked">
          {unknown.map(row => row.company_name).join(", ")}
        </Alert>
      )}
      {!breached.length && !unknown.length && guardrails.active.status !== "breached" && (
        <p className={styles.source}>{`Company caps: ${guardrails.companies.length ? "within your saved rules" : "no company rule applies"}. Active picks: ${STATUS[guardrails.active.status] ?? guardrails.active.status}.`}</p>
      )}
    </>
  );
}

function ReviewSummary({ review }: { review: Review }) {
  const cur = review.reporting_currency;
  const accounts = new Map(review.accounts.map(account => [account.id, account.name]));
  return (
    <div className="memo" role="region" aria-label="Portfolio summary">
      <Row label="Total value">
        <b>{money(review.total_value, cur)}</b>
        {!review.complete && <span>{` · known ${money(review.known_value, cur)}`}</span>}
        <div className={styles.source}>{`Holdings ${money(review.holdings_value, cur)} · cash ${money(review.cash_value, cur)}`}</div>
      </Row>
      <Row label="Dates">
        {`Valued as of ${fullDate(review.as_of)}`}
        {review.holdings_as_of && <div className={styles.source}>{`Holdings last confirmed ${fullDate(review.holdings_as_of)}`}</div>}
      </Row>
      {!review.complete && (
        <Alert tone="warning" title="Not every holding has a usable value">
          The total is unknown. Known values are shown; the notes below say what is missing.
        </Alert>
      )}
      {!review.source_inputs_usable && (
        <p className={styles.source}>Not every price, identity or FX rate is verified, so these values are not used to size trades.</p>
      )}
      <Breaches guardrails={review.guardrails} />
      <div className={styles.tableWrap}>
        <table className={styles.mdTable} aria-label="Holdings">
          <thead><tr><th scope="col">Holding</th><th scope="col">Account</th><th scope="col">Value</th><th scope="col">Weight</th><th scope="col">Issues</th></tr></thead>
          <tbody>
            {review.positions.map(row => (
              <tr key={row.supplied.id}>
                <td>{row.supplied.ticker || (row.supplied.kind === "cash" ? `Cash ${row.supplied.currency}` : row.supplied.id)}</td>
                <td>{accounts.get(row.supplied.account_id) ?? row.supplied.account_id}</td>
                <td>{money(row.value, cur)}</td>
                <td>{pct(row.weight)}</td>
                <td>{row.issues.map(sentence).join(" ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {review.direct_companies.length > 0 && (
        <Row label="Company exposure">
          {review.direct_companies.map(row => `${row.company_name} ${pct(row.weight)}`).join(" · ")}
        </Row>
      )}
      {(review.currency_exposure ?? []).length > 0 && (
        <Row label="Currency exposure">
          {(review.currency_exposure ?? []).map(row => `${row.currency} ${pct(row.weight)}`).join(" · ")}
        </Row>
      )}
      <Notes items={review.qualifications} />
    </div>
  );
}

function ChangeSummary({ review }: { review: ProposalReview }) {
  return (
    <div className="memo" role="region" aria-label="Checked changes">
      <Row label="Result">{STATUS[review.status] ?? humanize(review.status)}</Row>
      <Row label="After the changes">{`Total ${review.post_total_value ?? "unknown"} · cash ${review.post_cash_value ?? "unknown"}`}</Row>
      <Breaches guardrails={review.guardrails} />
      <Notes items={review.qualifications} />
    </div>
  );
}

/** A readable view for a stored kind without its own view: plain values and notes, nested records counted. */
function Fields({ value }: { value: unknown }) {
  const record = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : { value };
  const rows = Object.entries(record).filter(([, item]) => item !== null && item !== undefined && item !== "");
  return (
    <div className="memo" role="region" aria-label="Stored result">
      {rows.map(([key, item]) =>
        list(item).length && list(item).length === (item as unknown[]).length ? (
          <Notes key={key} title={humanize(key)} items={list(item)} />
        ) : (
          <Row key={key} label={humanize(key)}>
            {typeof item === "object" ? (Array.isArray(item) ? `${item.length} record${item.length === 1 ? "" : "s"}` : `${Object.keys(item as object).length} fields`) : String(item)}
          </Row>
        )
      )}
    </div>
  );
}

function Revisions({ ids }: { ids: string[] }) {
  const revisions = [...new Set(ids.map(revisionOf).filter(Boolean))];
  if (!revisions.length) return null;
  return (
    <ul className={styles.refs} aria-label="Referenced results">
      {revisions.map(revision => (
        <li key={revision}>
          <Badge size="sm">{`Portfolio revision ${revision}`}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** A source number in the text: a small pill. On touch layouts the link around it is a full-size target. */
const Mark = ({ number, anchor }: { number: number; anchor: string }) => (
  <a className={styles.cite} href={`#${anchor}`} aria-label={`Source ${number}`}>
    <span className={styles.ct}>{number}</span>
  </a>
);

function Citations({ sources, anchor }: { sources: Source[]; anchor: (n: number) => string }) {
  if (!sources.length) return null;
  return (
    <div className={styles.cited}>
      <p className={styles.citedHead}>
        <span>Sources</span>
        <Unverified />
      </p>
      <ol className={styles.sources} aria-label="Sources for this answer">
        {sources.map((item, index) => (
          // A page quoted twice is listed twice; the number's anchor is on its first entry.
          <li key={`${item.url.href}|${item.quote}`} id={sources.findIndex(other => other.number === item.number) === index ? anchor(item.number) : undefined} className={styles.sourceCard}>
            <span className={styles.ct} aria-hidden="true">{item.number}</span>
            <SourceLink url={item.url} title={item.title} />
            {item.quote && !linkOnly(item.quote) && <q className={styles.quote}>{item.quote}</q>}
          </li>
        ))}
      </ol>
    </div>
  );
}

const FIELD: Record<string, string> = {
  portfolio: "A saved portfolio",
  "holdings.identity": "Every holding identified",
  "stock.position_id": "The stock to analyze",
  "new_cash.amount": "The amount of new cash",
  "new_cash.account_id": "The account it arrives in",
  "new_cash.risk_context": "Your loss tolerance or withdrawal plans",
  "new_cash.confirmed": "Your confirmation that this is new money",
  "theme.name": "A theme name",
  "theme.mechanism": "The economic mechanism",
  "theme.shortlist": "A shortlist of one to four candidates",
  "theme.confirmed": "Your agreement to the theme and shortlist",
};
const REQUEST: Record<string, string> = {
  missing_inputs: "It cannot run this until you give the inputs below.",
  invalid_inputs: "Some of the inputs cannot be used together. Change them in the form, then submit it again.",
  portfolio_required: "It needs your holdings for this. Save a portfolio first.",
  unresolved_identity: "Some holdings are not identified yet. Identify them in your holdings first.",
};
const fieldLabel = (field: string) => FIELD[field] ?? sentence(field.replace(/[._]/g, " ")).slice(0, -1);

/** What a run could not go on without. The inputs are given through the desk's own forms and sent as a new run;
 *  nothing typed in the conversation counts as an agreement. */
function Clarification({ clarification, actionable, shared }: { clarification: Record<string, unknown>; actionable: boolean; shared: Shared }) {
  const fields = list(clarification.fields);
  const form = text(clarification.form);
  const { theme, snapshot, busy } = shared;
  const stocks = (snapshot?.positions ?? []).filter(isSupportedStock);
  const themeReady = !!theme.name?.trim() && !!theme.mechanism?.trim() && theme.shortlist.length > 0;
  const onlyAgreement = form === "/theme" && fields.length === 1 && fields[0] === "theme.confirmed" && themeReady;
  // An unowned candidate is named by its form ID, "candidate-TICKER-LISTING".
  const ticker = (id: string) =>
    snapshot?.positions.find(row => row.id === id)?.ticker || (id.startsWith("candidate-") ? id.slice(10).split("-")[0] : id);
  return (
    <div className={styles.notice} data-testid="clarification">
      <Alert tone="info" title="The analyst needs more from you">
        {/* A workflow's stored note is written for the model; the desk says the same thing to the investor. */}
        {(clarification.workflow ? REQUEST[text(clarification.code)] : undefined) ?? sentence(text(clarification.message) || "Some inputs are missing")}
      </Alert>
      {fields.length > 0 && (
        <ul className={styles.fields} aria-label="Missing inputs">
          {fields.map(field => (
            <li key={field}>{fieldLabel(field)}</li>
          ))}
        </ul>
      )}
      {list(clarification.choices).length > 0 && <p className={styles.source}>{`Choices: ${list(clarification.choices).join(", ")}`}</p>}
      {actionable && onlyAgreement && (
        <dl className={styles.agreement} aria-label="Theme to agree">
          <dt>Theme</dt>
          <dd>{theme.name}</dd>
          <dt>Economic mechanism</dt>
          <dd>{theme.mechanism}</dd>
          <dt>Shortlist</dt>
          <dd>{theme.shortlist.map(ticker).join(", ")}</dd>
        </dl>
      )}
      {actionable && (
        <div className={styles.actions}>
          {onlyAgreement && (
            <Button
              disabled={busy}
              onClick={() =>
                shared.onSubmitWorkflow(`Explore theme: ${theme.name}`, { workflow: "run_theme_exploration", theme: { ...theme, confirmed: true } })
              }
            >
              Agree and research
            </Button>
          )}
          {form === "/theme" && (
            <Button variant="secondary" disabled={busy} onClick={() => shared.onOpenForm(form)}>
              {onlyAgreement ? "Edit theme form" : "Open theme form"}
            </Button>
          )}
          {form === "/new-cash" && (
            <Button variant="secondary" disabled={busy} onClick={() => shared.onOpenForm(form)}>
              Open new-cash form
            </Button>
          )}
          {form === "holdings" && (
            <Button variant="secondary" onClick={() => shared.onOpenForm(form)}>
              Open holdings
            </Button>
          )}
          {form === "/stock" &&
            stocks.map(position => (
              <Button
                key={position.id}
                variant="secondary"
                disabled={busy}
                onClick={() =>
                  shared.onSubmitWorkflow(`Analyze ${position.ticker}`, { workflow: "run_stock_analysis", stock: { position_id: position.id } })
                }
              >
                {`Analyze ${position.ticker}`}
              </Button>
            ))}
        </div>
      )}
    </div>
  );
}

const PROPOSAL_STATE: Record<PortfolioProposal["status"], [label: string, tone: "warning" | "success" | "neutral"]> = {
  pending: ["Waiting for your review", "warning"],
  applied: ["Applied", "success"],
  cancelled: ["Cancelled", "neutral"],
  stale: ["Out of date", "neutral"],
};

/** A prepared change to the saved portfolio. It only opens the existing review: nothing here confirms or applies it. */
function Proposal({ id, shared }: { id: string; shared: Shared }) {
  const { proposals, committedRevision } = shared;
  if (!proposals)
    return shared.proposalsFailed ? (
      <div className={styles.notice}>
        <Alert tone="warning" title="The proposed changes could not be loaded">
          Check that the analyst service is running, then load them again. Nothing has been changed.
        </Alert>
        <Button variant="secondary" onClick={shared.onReloadProposals}>
          Load proposed changes again
        </Button>
      </div>
    ) : (
      <Skeleton label="Loading proposed changes" lines={2} />
    );
  const proposal = proposals.find(item => item.id === id);
  if (!proposal)
    return (
      <Alert tone="warning" title="These proposed changes are no longer available">
        Ask again to prepare a new preview from your saved portfolio.
      </Alert>
    );
  const outdated = isStale(proposal, committedRevision);
  const [label, tone] = PROPOSAL_STATE[outdated ? "stale" : proposal.status];
  const changes = proposal.differences.length;
  return (
    <article className={styles.proposal} aria-label="Proposed portfolio changes">
      <div className={styles.proposalHead}>
        <span>{`${changes} proposed change${changes === 1 ? "" : "s"}`}</span>
        <Badge size="sm" tone={tone}>
          {label}
        </Badge>
      </div>
      {proposal.status === "pending" && !outdated && (
        <ul className={styles.proposalChanges} aria-label="Changes, before and after">
          {changeRows(proposal, shared.snapshot).slice(0, 3).map(row => (
            <li key={row.key}><span>{row.subject} <small>{row.detail}</small></span><b>{row.before} <span aria-hidden="true">→</span><span className="sr-only">to</span> {row.after}</b></li>
          ))}
        </ul>
      )}
      <p className={styles.source}>
        {proposal.status === "applied" && proposal.receipt
          ? `Confirmed by you. Your portfolio moved from revision ${proposal.receipt.previous_revision ?? "none"} to revision ${proposal.receipt.new_revision}.`
          : proposal.status === "cancelled"
            ? "Cancelled. Your saved portfolio was not changed."
            : outdated
              ? `Prepared from revision ${proposal.base_revision}, which is no longer current. Your saved portfolio was not changed. Ask again for a fresh preview.`
              : `Prepared from revision ${proposal.base_revision ?? "none"}. Your saved portfolio stays as it is unless you confirm the changes in the review.`}
      </p>
      {proposal.status === "pending" && !outdated && (
        <Button variant="secondary" onClick={() => shared.onReviewProposal(proposal)}>
          Review changes
        </Button>
      )}
    </article>
  );
}

// Reads of data the run already has: kept in the activity details, never announced in the conversation.
const ROUTINE = new Set(["get_holdings", "get_artifact", "get_proposal", "get_messages"]);

function stepLine(outcome: MessageRecord["tool_outcomes"][number], paused: boolean): string {
  const [done, failed] = STEP[outcome.name] ?? [`Checked ${toolLabel(outcome.name)}`, `Could not check ${toolLabel(outcome.name)}`];
  if (paused) return `Paused ${toolLabel(outcome.name)}`;
  if (outcome.status !== "error") return done;
  // A step that stored a result is shown by that result below; its error is about reading it back.
  if (outcome.artifact_ids.length) return outcome.name === "web_search" ? failed : done;
  return `${failed}. ${sentence(outcome.error || "No result is available")}`;
}

/** One run's consecutive tool steps as a single status line. Results, requests for inputs and proposals stay in the
 *  conversation; routine reads and the step-by-step list are in the expandable details. */
function Activity({ steps, run, owns, actionable, shared }: { steps: MessageRecord[]; run: RunRecord | undefined; owns: (id: string, step: string) => boolean; actionable: boolean; shared: Shared }) {
  const lines = steps.flatMap(step => step.tool_outcomes.map(outcome => ({ step, outcome, text: stepLine(outcome, !!step.clarification_required) })));
  const failedLine = ({ outcome }: (typeof lines)[number]) => outcome.status === "error" && (!outcome.artifact_ids.length || outcome.name === "web_search");
  const paused = steps.some(step => step.clarification_required);
  const failed = lines.some(failedLine);
  const shown = lines.filter(line => !ROUTINE.has(line.outcome.name) || failedLine(line));
  // The holdings a read used are the run's bound revision, which the run records.
  const revisions = [...steps.flatMap(step => step.artifact_ids),
    ...(run?.bound_revision && !run.hypothetical_artifact_id && lines.some(line => line.outcome.name === "get_holdings") ? [`art-rev-${run.bound_revision}`] : [])];
  return (
    <li className={styles.stepGroup}>
      <div className={styles.step}>
        <span className={`${styles.stepIcon} ${paused ? styles.paused : failed ? styles.failed : ""}`} aria-hidden="true">
          {paused ? <Pause size={10} /> : failed ? <AlertTriangle size={10} /> : <Check size={10} strokeWidth={3} />}
        </span>
        <span className="sr-only">Analyst activity: </span>
        {shown.length ? shown.map(line => <span key={line.outcome.id} className={styles.stepText}>{line.text}</span>) : <span className={styles.stepText}>Read saved data</span>}
        {lines.length > 1 || shown.length < lines.length ? (
          <details className={styles.activity}>
            <summary>{`${lines.length} step${lines.length === 1 ? "" : "s"}`}</summary>
            <ol aria-label="Analyst steps">
              {lines.map(line => <li key={line.outcome.id}>{line.text}</li>)}
            </ol>
          </details>
        ) : null}
        <Revisions ids={revisions} />
      </div>
      {steps.map(step => {
        // A proposal's before and after portfolios are read through its review, not as separate results; a result
        // is drawn once, under the step that created it, never again under a later read of it.
        const results = step.proposal_ids.length ? [] : [...new Set(step.artifact_ids)].filter(id => !revisionOf(id) && owns(id, step.id));
        return [
          step.clarification_required && <Clarification key={`${step.id}-needed`} clarification={step.clarification_required} actionable={actionable} shared={shared} />,
          /* Stored records load after the thread opens; they are read on demand, not announced as new messages. */
          (results.length > 0 || step.proposal_ids.length > 0) && (
            <div key={`${step.id}-results`} className={styles.stepGroup} aria-live="off">
              {results.map(id => (
                <Artifact key={id} id={id} actionable={actionable} shared={shared} />
              ))}
              {step.proposal_ids.map(id => (
                <Proposal key={id} id={id} shared={shared} />
              ))}
            </div>
          ),
        ];
      })}
    </li>
  );
}

/** `hypothetical`: the message belongs to a run that analyzed a stored draft, not the saved portfolio.
 *  `proposing`: that run also prepared portfolio changes, which always start from the saved portfolio. */
function Message({ message, hypothetical, proposing }: { message: MessageRecord; hypothetical: boolean; proposing: boolean }) {
  if (message.role === "user")
    return (
      <li className={styles.user}>
        <span className="sr-only">You asked: </span>
        <p className={styles.bubble}>{message.text}</p>
        {hypothetical && (
          <Badge size="sm" tone="warning">
            Hypothetical draft
          </Badge>
        )}
      </li>
    );
  const { paragraphs, sources } = placeCitations(message.text, message.citations);
  const anchor = (n: number) => `source-${message.id}-${n}`;
  // Citation numbers sit at stored character offsets, so a cited answer keeps its paragraphs and gets inline
  // formatting only; an uncited answer is read as Markdown.
  const cited = paragraphs.some(pieces => pieces.some(piece => typeof piece !== "string"));
  return (
    <li className={`${styles.assistant} ${styles.markdown}`}>
      <span className="sr-only">Analyst answered: </span>
      {cited
        ? paragraphs.map((pieces, index) => (
            <p key={index}>
              {pieces.map((piece, at) => (typeof piece === "string" ? inline(piece) : piece.map(number => <Mark key={`${at}-${number}`} number={number} anchor={anchor(number)} />)))}
            </p>
          ))
        : blocks(message.text)}
      {hypothetical && (
        <p className={styles.source}>
          {proposing
            ? "Asked about your unsaved draft. The proposed changes start from your saved portfolio, and apply only if you confirm them."
            : "Based on your unsaved draft, not your saved portfolio. Nothing was saved."}
        </p>
      )}
      <Citations sources={sources} anchor={anchor} />
      <Revisions ids={message.artifact_ids} />
    </li>
  );
}

/** One durable conversation: persisted messages, results and run states from the backend, plus the one send still in flight. */
export default function Thread({ thread, pending, sendError, loadError, onRetrySend, onDiscardSend, onAskAgain, onReload, onStop, stopping, stopError, ...shared }: Props) {
  if (loadError)
    return (
      <div className={styles.notice}>
        <Alert tone="danger" title="This conversation could not be loaded">
          {sentence(loadError)}
        </Alert>
        <Button variant="secondary" onClick={onReload}>
          Load conversation again
        </Button>
      </div>
    );
  if (!thread && !pending) return <Skeleton label="Loading conversation" lines={4} />;

  const messages = thread?.messages ?? [];
  const runs = new Map((thread?.runs ?? []).map(run => [run.id, run]));
  const lastOfRun = new Map<string, string>();
  for (const message of messages) if (message.run_id) lastOfRun.set(message.run_id, message.id);
  const latestRun = thread?.runs.at(-1);
  // The first ordinary run whose committed revision differs from the ordinary run before it: run ID to that
  // earlier revision. Read from the stored runs, so the notice is the same after a reload.
  const revisionChanged = new Map<string, number>();
  const proposingRuns = new Set(messages.filter(message => message.proposal_ids.length).map(message => message.run_id));
  let lastRevision: number | null = null;
  for (const run of thread?.runs ?? []) {
    if (run.hypothetical_artifact_id || run.bound_revision == null) continue;
    if (lastRevision !== null && run.bound_revision !== lastRevision) revisionChanged.set(run.id, lastRevision);
    lastRevision = run.bound_revision;
  }
  const question = (runId: string) => messages.find(message => message.run_id === runId && message.role === "user")?.text;
  // Once the backend has stored the message, the thread shows it; until then it is only this browser's unsent text.
  const unsent = pending && !thread?.runs.some(run => run.client_request_id === pending.clientRequestId) ? pending : null;

  if (!messages.length && !unsent) return <p className={styles.empty}>This conversation has no messages yet. Ask a question to start it.</p>;

  // Consecutive tool steps of one run are drawn as one activity entry; every other message stands alone.
  const items: MessageRecord[][] = [];
  for (const message of messages) {
    const previous = items.at(-1);
    if (message.tool_outcomes.length && previous?.[0].tool_outcomes.length && previous[0].run_id === message.run_id) previous.push(message);
    else items.push([message]);
  }
  // The step that first stored a result owns its card; an older thread's read of it adds no second card.
  const owner = new Map<string, string>();
  for (const message of messages) if (message.tool_outcomes.length) for (const id of message.artifact_ids) if (!owner.has(id)) owner.set(id, message.id);
  const owns = (id: string, step: string) => owner.get(id) === step;

  return (
    <div role="log" aria-label="Conversation">
    <ol className={styles.thread}>
      {items.map(group => {
        const message = group.at(-1)!;
        const run = message.run_id && lastOfRun.get(message.run_id) === message.id ? runs.get(message.run_id) : undefined;
        const notice = run && RUN_NOTICE[run.status];
        const asked = run && run.id === latestRun?.id && !unsent ? question(run.id) : undefined;
        // Every message knows its run; `run` above is set only on the run's last message, where its outcome is shown.
        const messageRun = message.run_id ? runs.get(message.run_id) : undefined;
        const earlierRevision = messageRun && message.role === "user" ? revisionChanged.get(messageRun.id) : undefined;
        return [
          // Only the latest run's request for inputs can still be answered; earlier ones are history.
          message.tool_outcomes.length ? (
            <Activity key={group[0].id} steps={group} run={messageRun} owns={owns} actionable={message.run_id === latestRun?.id && !unsent} shared={shared} />
          ) : (
            <Message key={message.id} message={message} hypothetical={!!messageRun?.hypothetical_artifact_id} proposing={proposingRuns.has(message.run_id)} />
          ),
          messageRun && earlierRevision !== undefined && (
            <li key={`${messageRun.id}-revision`} className={styles.notice} data-testid="revision-notice">
              <Alert tone="info" title="Your saved portfolio changed">
                {`This question uses revision ${messageRun.bound_revision}. Earlier questions used revision ${earlierRevision}, and their results are unchanged.`}
              </Alert>
            </li>
          ),
          run?.status === "running" && (
            <li key={`${run.id}-working`} className={styles.working}>
              <TextShimmer>Thinking</TextShimmer>
              <Button variant="secondary" disabled={stopping} onClick={onStop}>
                {stopping ? "Stopping answer" : "Stop answer"}
              </Button>
            </li>
          ),
          run?.status === "running" && stopError && (
            <li key={`${run.id}-stop-error`} className={styles.notice}>
              <Alert tone="danger" title="This answer could not be stopped">
                {`${sentence(stopError)} It is still running. Check that the analyst service is running, then stop it again.`}
              </Alert>
            </li>
          ),
          run && notice && (
            <li key={`${run.id}-notice`} className={styles.notice}>
              {/* A stored outcome, not a new emergency: announced politely, also when a thread is reopened. */}
              <Alert tone="warning" title={notice}>
                {`${sentence(run.error || "No answer was saved for this question")} Your question is kept in this conversation.`}
              </Alert>
              {asked && (
                <Button variant="secondary" onClick={() => onAskAgain(asked)}>
                  Ask again
                </Button>
              )}
            </li>
          ),
        ];
      })}
      {unsent && (
        <li className={styles.user}>
          <span className="sr-only">You asked, not sent yet: </span>
          <p className={`${styles.bubble} ${styles.unsent}`}>{unsent.text}</p>
          {!sendError && <span className={styles.status}>Sending</span>}
        </li>
      )}
      {unsent && sendError && (
        <li className={styles.notice}>
          <Alert tone="danger" title="Not sent">
            {`${sentence(sendError)} This message is not part of the conversation yet.`}
          </Alert>
          <div className={styles.actions}>
            <Button variant="secondary" onClick={onRetrySend}>
              Send again
            </Button>
            <Button variant="ghost" onClick={onDiscardSend}>
              Discard message
            </Button>
          </div>
        </li>
      )}
    </ol>
    </div>
  );
}
