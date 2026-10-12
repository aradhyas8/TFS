import { Alert } from "@/components/arc/alert/alert";
import { Badge } from "@/components/arc/badge/badge";
import { Button } from "@/components/arc/button/button";
import { Skeleton } from "@/components/arc/skeleton/skeleton";
import { TextShimmer } from "@/components/arc/text-shimmer/text-shimmer";
import type { MessageRecord, RunRecord, ThreadDetail } from "../../lib/contracts";
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
};

const RUN_NOTICE: Partial<Record<RunRecord["status"], string>> = {
  failed: "This answer did not finish",
  cancelled: "This answer was stopped",
  interrupted: "The analyst restarted before answering",
};

const toolLabel = (name: string) => name.replace(/^(get|run)_/, "").replace(/_/g, " ");
// A stored result the answer drew on. Its contents open with the result views that follow this ticket.
const artifactLabel = (id: string) => {
  const revision = id.match(/^art-rev-(\d+)$/);
  return revision ? `Portfolio revision ${revision[1]}` : `Saved result ${id}`;
};
const sentence = (text: string) => (/[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);
const webLink = (value: unknown) => (typeof value === "string" && /^https?:\/\//i.test(value) ? value : null);

function References({ ids }: { ids: string[] }) {
  if (!ids.length) return null;
  return (
    <ul className={styles.refs} aria-label="Referenced results">
      {[...new Set(ids)].map(id => (
        <li key={id}>
          <Badge size="sm">{artifactLabel(id)}</Badge>
        </li>
      ))}
    </ul>
  );
}

function Message({ message }: { message: MessageRecord }) {
  if (message.role === "user")
    return (
      <li className={styles.user}>
        <span className="sr-only">You asked: </span>
        <p className={styles.bubble}>{message.text}</p>
      </li>
    );
  // A tool step is stored as a message; its raw record is never shown as prose.
  if (message.tool_outcomes.length)
    return (
      <li className={styles.step}>
        {message.tool_outcomes.map(outcome => (
          <span key={outcome.id}>
            {outcome.status === "error" ? `Could not check ${toolLabel(outcome.name)}` : `Checked ${toolLabel(outcome.name)}`}
          </span>
        ))}
        <References ids={message.artifact_ids} />
      </li>
    );
  const sources = message.citations
    .map(citation => ({ url: webLink(citation.url), title: typeof citation.title === "string" ? citation.title : null }))
    .filter((source): source is { url: string; title: string | null } => !!source.url);
  return (
    <li className={styles.assistant}>
      <span className="sr-only">Analyst answered: </span>
      {/* ponytail: plain paragraphs only; add a Markdown renderer when answers need lists or tables. */}
      {message.text.split(/\n{2,}/).map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
      {sources.length > 0 && (
        <ul className={styles.sources} aria-label="Sources">
          {sources.map((source, index) => (
            <li key={index}>
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                {source.title || source.url}
              </a>
            </li>
          ))}
        </ul>
      )}
      <References ids={message.artifact_ids} />
    </li>
  );
}

/** One durable conversation: persisted messages and run states from the backend, plus the one send still in flight. */
export default function Thread({ thread, pending, sendError, loadError, onRetrySend, onDiscardSend, onAskAgain, onReload }: Props) {
  if (loadError)
    return (
      <div className={styles.notice}>
        <Alert tone="danger" title="This conversation could not be loaded">
          {sentence(loadError)}
        </Alert>
        <Button variant="secondary" size="sm" onClick={onReload}>
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

  return (
    <ol className={styles.thread} role="log" aria-label="Conversation" aria-live="polite">
      {messages.map(message => {
        const run = message.run_id && lastOfRun.get(message.run_id) === message.id ? runs.get(message.run_id) : undefined;
        const notice = run && RUN_NOTICE[run.status];
        const asked = run && run.id === latestRun?.id && !unsent ? question(run.id) : undefined;
        return [
          <Message key={message.id} message={message} />,
          run?.status === "running" && (
            <li key={`${run.id}-working`} className={styles.working}>
              <TextShimmer>Thinking</TextShimmer>
            </li>
          ),
          run && notice && (
            <li key={`${run.id}-notice`} className={styles.notice}>
              <Alert tone={run.status === "failed" ? "danger" : "warning"} title={notice}>
                {`${sentence(run.error || "No answer was saved for this question")} Your question is kept in this conversation.`}
              </Alert>
              {asked && (
                <Button variant="secondary" size="sm" onClick={() => onAskAgain(asked)}>
                  Ask again
                </Button>
              )}
            </li>
          ),
        ];
      })}
      {unsent && (
        <li className={styles.user}>
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
            <Button variant="secondary" size="sm" onClick={onRetrySend}>
              Send again
            </Button>
            <Button variant="ghost" size="sm" onClick={onDiscardSend}>
              Discard message
            </Button>
          </div>
        </li>
      )}
    </ol>
  );
}
