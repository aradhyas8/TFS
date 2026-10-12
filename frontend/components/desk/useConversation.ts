import { useEffect, useRef, useState } from "react";
import { createThread, getThread, getThreadRun, listThreads, submitThreadMessage, type ThreadDetail, type ThreadSummary } from "../../lib/contracts";

// Which thread is open, so a reload returns to it. The messages themselves always come from the backend.
const THREAD_KEY = "desk.thread";
// A send the backend has not yet acknowledged. Resending it with the same client request ID is safe:
// the backend returns the run it already accepted instead of starting a second one.
const PENDING_KEY = "desk.pendingSend";
const POLL_MS = 1000;

export type PendingSend = { threadId: string; clientRequestId: string; text: string };

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
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingSend | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The thread the user is looking at now; late responses for another thread are dropped.
  const current = useRef<string | null>(null);

  const refreshThreads = () =>
    listThreads()
      .then(list => setThreads(list.filter(item => item.title)))
      .catch(() => {
        /* the list is retried on the next change; the open thread reports its own errors */
      });

  async function load(id: string) {
    try {
      const detail = await getThread(id);
      if (current.current !== id) return;
      setThread(detail);
      setLoadError(null);
    } catch (error) {
      if (current.current === id) setLoadError(message(error));
    }
  }

  function open(id: string) {
    current.current = id;
    setOpenId(id);
    setThread(null);
    setLoadError(null);
    remember(THREAD_KEY, id);
    void load(id);
  }

  function close() {
    current.current = null;
    setOpenId(null);
    setThread(null);
    setLoadError(null);
    remember(THREAD_KEY, null);
  }

  async function deliver(send: PendingSend) {
    setPending(send);
    setSendError(null);
    try {
      if (!send.threadId) {
        send = { ...send, threadId: (await createThread()).id };
        current.current = send.threadId;
        setOpenId(send.threadId);
        setPending(send);
        remember(THREAD_KEY, send.threadId);
      }
      remember(PENDING_KEY, JSON.stringify(send));
      for (let attempt = 0; ; attempt++) {
        try {
          await submitThreadMessage(send.threadId, { client_request_id: send.clientRequestId, text: send.text });
          break;
        } catch (error) {
          if (attempt > 0 || !unacknowledged(error)) throw error;
        }
      }
    } catch (error) {
      setSendError(message(error));
      return;
    }
    await load(send.threadId);
    remember(PENDING_KEY, null);
    setPending(null);
    void refreshThreads();
  }

  /** Sends one message into the open thread, or into a new thread when none is open. */
  const send = (text: string) => deliver({ threadId: current.current ?? "", clientRequestId: crypto.randomUUID(), text });

  const retry = () => (pending ? deliver(pending) : Promise.resolve());

  function discardPending() {
    remember(PENDING_KEY, null);
    setPending(null);
    setSendError(null);
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
    if (unsent?.threadId && unsent.clientRequestId && unsent.text) {
      open(unsent.threadId);
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
          await load(runningThread);
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

  const unsent = pending && pending.threadId === (openId ?? "") ? pending : null;
  return {
    threads,
    thread,
    openId,
    pending: unsent,
    sendError: unsent ? sendError : null,
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
