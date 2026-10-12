import { useEffect, useRef, useState } from "react";
import { createThread, getThread, getThreadRun, listThreads, submitThreadMessage, type ThreadDetail, type ThreadSummary } from "../../lib/contracts";

// Which thread is open, so a reload returns to it. The messages themselves always come from the backend.
const THREAD_KEY = "desk.thread";
// A send the backend has not yet acknowledged. Resending it with the same client request ID is safe:
// the backend returns the run it already accepted instead of starting a second one. Once acknowledged,
// the run is found again through the thread itself, which lists every run with its client request ID.
const PENDING_KEY = "desk.pendingSend";
const POLL_MS = 1000;

/** A message this browser has sent but the backend has not acknowledged. No thread ID yet means a new conversation. */
export type PendingSend = { threadId: string | null; clientRequestId: string; text: string; workflowInput?: Record<string, unknown> };

const remember = (key: string, value: string | null) => {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* recovery after a reload is a convenience; the thread itself is stored by the backend */
  }
};
const message = (error: unknown) => (error instanceof Error ? error.message : "The request could not be completed.");
// A lost connection or a timed-out request says nothing about whether the backend accepted the message.
const unacknowledged = (error: unknown) => error instanceof TypeError || (error instanceof Error && error.name === "TimeoutError");

/** Durable conversation state for the desk: thread list, the open thread, one idempotent send, and run polling. */
export function useConversation() {
  // null until the first list arrives, so "none yet" is never claimed before it is known.
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [threadsError, setThreadsError] = useState(false);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingSend | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The open thread as asynchronous work sees it: a late response for a thread the user has left is dropped.
  const viewing = useRef<string | null>(null);
  // One delivery at a time. A second delivery of a message that has no thread yet would create a second
  // thread, and the same message would then be accepted in both.
  const delivering = useRef(false);

  const refreshThreads = () =>
    listThreads()
      .then(list => {
        // A thread with no accepted question (its first send failed) is not a conversation yet.
        setThreads(list.filter(item => item.title));
        setThreadsError(false);
      })
      .catch(() => setThreadsError(true));

  function view(id: string | null) {
    viewing.current = id;
    setOpenId(id);
    remember(THREAD_KEY, id);
  }

  /** Background refreshes stay quiet: a failed poll keeps the messages already shown. */
  async function load(id: string, quiet = false) {
    try {
      const detail = await getThread(id);
      if (viewing.current !== id) return;
      setThread(detail);
      setLoadError(null);
    } catch (error) {
      if (viewing.current === id && !quiet) setLoadError(message(error));
    }
  }

  function open(id: string) {
    view(id);
    setThread(null);
    setLoadError(null);
    void load(id);
  }

  function close() {
    view(null);
    setThread(null);
    setLoadError(null);
  }

  async function deliver(send: PendingSend) {
    if (delivering.current) return;
    delivering.current = true;
    try {
      const origin = viewing.current;
      // Stored before any request, so a reload while the thread is still being created keeps the message.
      const hold = (next: PendingSend) => {
        send = next;
        setPending(next);
        remember(PENDING_KEY, JSON.stringify(next));
      };
      hold(send);
      setSendError(null);
      let threadId = send.threadId;
      try {
        for (let attempt = 0; ; attempt++) {
          try {
            if (!threadId) {
              // A creation whose response was lost leaves an empty thread behind. It never receives the
              // message, so the message is accepted only in the thread recorded here.
              threadId = (await createThread()).id;
              hold({ ...send, threadId });
              // Follow the new thread only if the user is still where they sent from.
              if (viewing.current === origin) view(threadId);
            }
            await submitThreadMessage(threadId, { client_request_id: send.clientRequestId, text: send.text, workflow_input: send.workflowInput });
            break;
          } catch (error) {
            if (attempt > 0 || !unacknowledged(error)) throw error;
          }
        }
      } catch (error) {
        setSendError(message(error));
        return;
      }
      await load(threadId);
      remember(PENDING_KEY, null);
      setPending(null);
      void refreshThreads();
    } finally {
      delivering.current = false;
    }
  }

  /** Sends one message into the open thread, or into a new thread when none is open. Explicit form inputs
   *  (a chosen stock, a confirmed new-cash or theme form) travel with it and start a new run; they are
   *  never inferred from the text.
   *  ponytail: one unacknowledged send at a time; a new send replaces an earlier one that failed and was left behind.
   *  ponytail: two tabs reloaded with the same thread-less send each create a thread; a client-supplied thread ID
   *  on POST /api/threads would make creation idempotent too. */
  const send = (text: string, workflowInput?: Record<string, unknown>) =>
    deliver({ threadId: viewing.current, clientRequestId: crypto.randomUUID(), text, workflowInput });

  const retry = () => (pending ? deliver(pending) : Promise.resolve());

  function discardPending() {
    remember(PENDING_KEY, null);
    setPending(null);
    setSendError(null);
    // A conversation whose only message was never accepted has nothing left to show.
    if (!thread?.messages.length) close();
  }

  useEffect(() => {
    void refreshThreads();
    let unsent: PendingSend | null = null;
    let last: string | null = null;
    try {
      unsent = JSON.parse(localStorage.getItem(PENDING_KEY) || "null");
      last = localStorage.getItem(THREAD_KEY);
    } catch {
      /* nothing to recover */
    }
    if (unsent?.clientRequestId && unsent.text) {
      // No thread ID means the reload came before the thread existed: delivery resumes by creating it.
      if (unsent.threadId) open(unsent.threadId);
      void deliver(unsent);
    } else if (last) open(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const running = thread?.runs.find(run => run.status === "running") ?? null;
  const runningThread = running?.thread_id;
  const runningId = running?.id;
  const seen = running?.message_ids.length ?? 0;

  // Short status requests until the run ends. Reopening a thread with a running run resumes here.
  useEffect(() => {
    if (!runningThread || !runningId) return;
    let stopped = false;
    let known = seen;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const run = await getThreadRun(runningThread, runningId);
        if (stopped) return;
        if (run.status !== "running" || run.message_ids.length !== known) {
          known = run.message_ids.length;
          await load(runningThread, true);
          if (run.status !== "running") void refreshThreads();
        }
      } catch {
        /* a failed status request is retried on the next tick */
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS);
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningThread, runningId]);

  // The unacknowledged send belongs to one thread and shows only there.
  const pendingHere = pending && pending.threadId === openId ? pending : null;
  return {
    threads,
    threadsError,
    thread,
    openId,
    pending: pendingHere,
    sendError: pendingHere ? sendError : null,
    loadError,
    busy: !!running || (!!pending && !sendError),
    running: !!running,
    open,
    close,
    send,
    retry,
    discardPending,
    reload: () => (openId ? load(openId) : Promise.resolve()),
  };
}
