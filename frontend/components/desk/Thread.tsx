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
  type MessageRecord,
  type PortfolioProposal,
  type RunRecord,
  type Snapshot,
  type ThemeInput,
  type ThreadDetail,
} from "../../lib/contracts";
import { fullDate } from "./format";
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

/** A link is drawn only for a web address; anything else a source supplied stays plain text. */
function webAddress(value: unknown): URL | null {
  try {
    const url = new URL(text(value));
    return url.protocol === "https:" || url.protocol === "http:" ? url : null;
  } catch {
    return null;
  }
}

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
  if (reference.kind === "portfolio_snapshot" || !reference.kind) return <Badge size="sm">{reference.label}</Badge>;
  // A financial capability that could not proceed stores what it needs; that is a request, not a result to read.
  const needed = (payload as { clarification_required?: Record<string, unknown> } | null)?.clarification_required;
  if (needed) {
    const form = ["portfolio_required", "unresolved_identity"].includes(text(needed.code)) ? "holdings" : "";
    return <Clarification clarification={{ ...needed, form }} actionable={actionable} shared={shared} />;
  }
  if (reference.kind === "analysis_result")
    return (
      <article className={styles.result} aria-label={`${sentenceCase(reference.label)} result`}>
        <p className={styles.source}>{`${sentenceCase(reference.label)} · ${source}`}</p>
        {(payload as Partial<Analysis> | null)?.portfolio?.positions && (payload as Analysis).recommendation ? (
          shared.renderResult(payload as Analysis, reference, storedSnapshot(payload as Analysis))
        ) : (
          unreadable
        )}
      </article>
    );
  if (reference.kind === "web_research") {
    const research = payload as WebResearch;
    const sources = (research.sources ?? []).flatMap(item => {
      const url = webAddress(item.url);
      return url ? [{ url, title: item.title || url.hostname }] : [];
    });
    return (
      <article className={styles.result} aria-label={reference.label}>
        <p className={styles.source}>{`${reference.label}${research.query ? ` for "${research.query}"` : ""} · stored ${fullDate(reference.created_at)}`}</p>
        {research.status === "unavailable" ? (
          <Alert tone="warning" title="Web search was unavailable">
            {list(research.qualifications).map(sentence).join(" ") || "No current lookup took place."}
          </Alert>
        ) : (
          <>
            {sources.length ? (
              <ul className={styles.sources} aria-label="Sources consulted">
                {sources.map(item => (
                  <li key={item.url.href}>
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
      </article>
    );
  }
  // ponytail: research, evidence and calculation records show their stored values as a tree, never as prose.
  // Give a kind its own view when its values need more than reading.
  return (
    <article className={styles.result} aria-label={reference.label}>
      <p className={styles.source}>{`${reference.label} · ${source}`}</p>
      <JsonViewer data={payload} rootName="result" label={`${reference.label} values`} defaultExpandDepth={2} searchable={false} showPath={false} maxHeight={320} />
    </article>
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

/** The sources a stored answer cited, kept with that answer. */
function Citations({ citations }: { citations: MessageRecord["citations"] }) {
  const seen = new Set<string>();
  const cited = citations.flatMap(citation => {
    const url = webAddress(citation.url);
    const quote = text(citation.cited_text);
    if (!url || seen.has(`${url.href}|${quote}`)) return [];
    seen.add(`${url.href}|${quote}`);
    return [{ url, quote, title: text(citation.title) || url.hostname }];
  });
  if (!cited.length) return null;
  return (
    <ol className={styles.sources} aria-label="Sources for this answer">
      {cited.map(item => (
        <li key={`${item.url.href}|${item.quote}`}>
          <SourceLink url={item.url} title={item.title} />
          {item.quote && <q className={styles.quote}>{item.quote}</q>}
        </li>
      ))}
    </ol>
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
  const ticker = (id: string) => snapshot?.positions.find(row => row.id === id)?.ticker || id;
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
  const outdated = proposal.status === "stale" || (proposal.status === "pending" && committedRevision != null && proposal.base_revision != null && proposal.base_revision !== committedRevision);
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

function Message({ message, actionable, shared }: { message: MessageRecord; actionable: boolean; shared: Shared }) {
  if (message.role === "user")
    return (
      <li className={styles.user}>
        <span className="sr-only">You asked: </span>
        <p className={styles.bubble}>{message.text}</p>
      </li>
    );
  // A tool step is stored as a message; its raw record is never shown as prose.
  if (message.tool_outcomes.length) {
    const clarification = message.clarification_required;
    // A proposal's before and after portfolios are read through its review, not as separate results.
    const results = message.proposal_ids.length ? [] : [...new Set(message.artifact_ids)].filter(id => !revisionOf(id));
    return (
      <li className={styles.stepGroup}>
        <div className={styles.step}>
          <span className="sr-only">Analyst step: </span>
          {message.tool_outcomes.map(outcome => {
            const [done, failed] = STEP[outcome.name] ?? [`Checked ${toolLabel(outcome.name)}`, `Could not check ${toolLabel(outcome.name)}`];
            return (
              <span key={outcome.id}>
                {clarification
                  ? `Paused ${toolLabel(outcome.name)}`
                  : outcome.status !== "error"
                    ? done
                    : // A step that stored a result is shown by that result below; its error is about reading it back.
                      outcome.artifact_ids.length
                      ? outcome.name === "web_search" ? failed : done
                      : `${failed}. ${sentence(outcome.error || "No result is available")}`}
              </span>
            );
          })}
          <Revisions ids={message.artifact_ids} />
        </div>
        {clarification && <Clarification clarification={clarification} actionable={actionable} shared={shared} />}
        {/* Stored records load after the thread opens; they are read on demand, not announced as new messages. */}
        {(results.length > 0 || message.proposal_ids.length > 0) && (
          <div className={styles.stepGroup} aria-live="off">
            {results.map(id => (
              <Artifact key={id} id={id} actionable={actionable} shared={shared} />
            ))}
            {message.proposal_ids.map(id => (
              <Proposal key={id} id={id} shared={shared} />
            ))}
          </div>
        )}
      </li>
    );
  }
  return (
    <li className={styles.assistant}>
      <span className="sr-only">Analyst answered: </span>
      {/* ponytail: plain paragraphs only; add a Markdown renderer when answers need lists or tables. */}
      {message.text.split(/\n{2,}/).map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
      <Citations citations={message.citations} />
      <Revisions ids={message.artifact_ids} />
    </li>
  );
}

/** One durable conversation: persisted messages, results and run states from the backend, plus the one send still in flight. */
export default function Thread({ thread, pending, sendError, loadError, onRetrySend, onDiscardSend, onAskAgain, onReload, ...shared }: Props) {
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
  const question = (runId: string) => messages.find(message => message.run_id === runId && message.role === "user")?.text;
  // Once the backend has stored the message, the thread shows it; until then it is only this browser's unsent text.
  const unsent = pending && !thread?.runs.some(run => run.client_request_id === pending.clientRequestId) ? pending : null;

  if (!messages.length && !unsent) return <p className={styles.empty}>This conversation has no messages yet. Ask a question to start it.</p>;

  return (
    <div role="log" aria-label="Conversation">
    <ol className={styles.thread}>
      {messages.map(message => {
        const run = message.run_id && lastOfRun.get(message.run_id) === message.id ? runs.get(message.run_id) : undefined;
        const notice = run && RUN_NOTICE[run.status];
        const asked = run && run.id === latestRun?.id && !unsent ? question(run.id) : undefined;
        return [
          // Only the latest run's request for inputs can still be answered; earlier ones are history.
          <Message key={message.id} message={message} actionable={message.run_id === latestRun?.id && !unsent} shared={shared} />,
          run?.status === "running" && (
            <li key={`${run.id}-working`} className={styles.working}>
              <TextShimmer>Thinking</TextShimmer>
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
