import { useEffect, useRef, useState } from "react";
import { cancelThreadRun, createThread, getThread, getThreadRun, listThreads, submitThreadMessage, type ThreadDetail, type ThreadSummary } from "../../lib/contracts";

// Which thread is open, so a reload returns to it. The messages themselves always come from the backend.
const THREAD_KEY = "desk.thread";
// A send the backend has not yet acknowledged. Resending it with the same client request ID is safe:
// the backend returns the run it already accepted instead of starting a second one. Once acknowledged,
// the run is found again through the thread itself, which lists every run with its client request ID.
const PENDING_KEY = "desk.pendingSend";
const POLL_MS = 1000;

/** A message this browser has sent but the backend has not acknowledged. No thread ID yet means a new conversation. */
/** `view`: the view it was sent from, and the only one it is shown in. Thread IDs cannot say this: every new conversation has none. */
export type PendingSend = { threadId: string | null; view: string; clientRequestId: string; text: string; workflowInput?: Record<string, unknown>; hypotheticalArtifactId?: string };

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
  const [stopping, setStopping] = useState(false);
  // Kept with the run it was about, so it never shows on a later run.
  const [stopError, setStopError] = useState<{ runId: string; message: string } | null>(null);
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

  // Which view is on screen: a thread, or one particular new conversation. A thread ID cannot tell two new
  // conversations apart, so every new conversation that is started gets its own number.
  const newViews = useRef(0);
  const viewKeyNow = useRef("new:0");
  const [viewKey, setViewKey] = useState("new:0");
  // A new conversation that received its thread is the same view under a new key.
  const [rekeyed, setRekeyed] = useState<{ from: string; to: string; threadId: string } | null>(null);

  /** `newKey` returns to one particular new conversation instead of starting another. */
  function view(id: string | null, newKey?: string) {
    if (id === null && !newKey) newViews.current++;
    viewing.current = id;
    viewKeyNow.current = id ? `thread:${id}` : (newKey ?? `new:${newViews.current}`);
    setViewKey(viewKeyNow.current);
    setOpenId(id);
    setStopError(null);
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

  /** Leaves the open thread for a new conversation. `fresh: false` stays in the new conversation already on screen. */
  function close(fresh = true) {
    if (fresh || viewing.current !== null) view(null);
    setThread(null);
    setLoadError(null);
  }

  /** A thread created for the message is opened only while the view it was sent from is still on screen.
   *  Resolves false when another delivery is under way and this one was not started. */
  async function deliver(send: PendingSend): Promise<boolean> {
    if (delivering.current) return false;
    delivering.current = true;
    try {
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
            if (!threadId || !send.view.startsWith("thread:")) {
              if (!threadId) threadId = `th-${send.clientRequestId.replace(/-/g, "").slice(0, 12)}`;
              await createThread(threadId);
              const asked = send.view;
              // From here on the message belongs to its thread, wherever that is opened.
              hold({ ...send, threadId, view: `thread:${threadId}` });
              // Follow the new thread only if the user is still where they sent from.
              if (viewKeyNow.current === asked) {
                view(threadId);
                setRekeyed({ from: asked, to: viewKeyNow.current, threadId });
              }
            }
            await submitThreadMessage(threadId, {
              client_request_id: send.clientRequestId, text: send.text, workflow_input: send.workflowInput,
              hypothetical_artifact_id: send.hypotheticalArtifactId,
            });
            break;
          } catch (error) {
            if (attempt > 0 || !unacknowledged(error)) throw error;
          }
        }
      } catch (error) {
        setSendError(message(error));
        return true;
      }
      await load(threadId);
      remember(PENDING_KEY, null);
      setPending(null);
      void refreshThreads();
      return true;
    } finally {
      delivering.current = false;
    }
  }

  /** Sends one message into the open thread, or into a new thread when none is open. Explicit form inputs
   *  (a chosen stock, a confirmed new-cash or theme form) travel with it and start a new run; they are
   *  never inferred from the text. A stored hypothetical portfolio is analyzed only when its ID is given here.
   *  ponytail: one unacknowledged send at a time; a new send replaces an earlier one that failed and was left behind.
   *  A client-supplied thread ID makes thread creation and multi-tab reload recovery idempotent. */
  const send = (
    text: string,
    workflowInput?: Record<string, unknown>,
    hypotheticalArtifactId?: string,
    // Where the question was asked. Given when work came between the asking and the sending, so a
    // conversation opened in the meantime never receives it and is never replaced by it.
    asked: { threadId: string | null; view: string } = { threadId: viewing.current, view: viewKeyNow.current }
  ) =>
    deliver({ threadId: asked.threadId, view: asked.view, clientRequestId: crypto.randomUUID(), text, workflowInput, hypotheticalArtifactId });

  const retry = () => (pending ? deliver(pending) : Promise.resolve(false));

  function discardPending() {
    remember(PENDING_KEY, null);
    setPending(null);
    setSendError(null);
    // A conversation whose only message was never accepted has nothing left to show.
    // Stays in the same new conversation when it never got a thread.
    if (!thread?.messages.length) close(false);
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
      // View keys do not outlive the page: the recovered message belongs to the view restored here.
      void deliver({ ...unsent, view: viewKeyNow.current });
    } else if (last) open(last);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const running = thread?.runs.find(run => run.status === "running") ?? null;
  const runningThread = running?.thread_id;
  const runningId = running?.id;
  const seen = running?.message_ids.length ?? 0;

  /** Asks the backend to cancel the open thread's running run. The thread is then read again, so what is
   *  shown is the stored outcome; only that one run is named, never another run or a proposal. */
  async function stop() {
    if (!running || stopping) return;
    setStopping(true);
    setStopError(null);
    try {
      await cancelThreadRun(running.thread_id, running.id);
      await load(running.thread_id, true);
      void refreshThreads();
    } catch (error) {
      if (viewing.current === running.thread_id) setStopError({ runId: running.id, message: message(error) });
    } finally {
      setStopping(false);
    }
  }

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
  const pendingHere = pending && pending.view === viewKey ? pending : null;
  return {
    threads,
    threadsError,
    thread,
    openId,
    pending: pendingHere,
    sendError: pendingHere ? sendError : null,
    loadError,
    // ponytail: a delivery under way for another view also holds the composer here for its short duration,
    // because only one delivery runs at a time. Queue sends per view if that wait is ever felt.
    busy: !!running || (!!pending && !sendError),
    running: !!running,
    stopping,
    stopError: stopError && stopError.runId === running?.id ? stopError.message : null,
    stop,
    /** The thread a question asked now belongs to; null starts a new conversation. */
    destination: () => viewing.current,
    /** Identity of the view on screen, for work that must report back only where it was started. */
    viewKey,
    /** The same identity read at call time, for use right after a navigation in the same handler. */
    currentView: () => viewKeyNow.current,
    rekeyed,
    /** Returns to a new conversation that was left before it had a thread. */
    reopenNew: (key: string) => {
      view(null, key);
      setThread(null);
      setLoadError(null);
    },
    open,
    close,
    send,
    retry,
    discardPending,
    reload: () => (openId ? load(openId) : Promise.resolve()),
  };
}
